"""Propose and accept beliefs from entries."""

import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from .llm import check_model_available, invoke_sync
from .prompts import PROPOSE_BELIEFS

PROJECT_DIR = ".expert-build"


def _has_embeddings() -> bool:
    """Check if fastembed is available."""
    try:
        import numpy  # noqa: F401
        from fastembed import TextEmbedding  # noqa: F401
        return True
    except ImportError:
        return False


_embed_model = None


def _get_embed_model():
    """Lazy-load the fastembed model."""
    global _embed_model
    if _embed_model is None:
        from fastembed import TextEmbedding
        _embed_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    return _embed_model


def _load_existing_beliefs(beliefs_path: Path) -> list[dict]:
    """Parse beliefs.md into list of {id, text, source} dicts."""
    if not beliefs_path.exists():
        return []
    text = beliefs_path.read_text()
    beliefs = []
    sections = re.split(r'^(?=### )', text, flags=re.MULTILINE)
    for section in sections:
        m = re.match(r'^### ([\w-]+) \[(IN|OUT|STALE)\]', section)
        if not m:
            continue
        lines = section.strip().splitlines()
        claim_text = lines[1].strip() if len(lines) > 1 else ""
        source = ""
        for line in lines:
            if line.startswith("- Source:"):
                source = line.replace("- Source:", "").strip()
        beliefs.append({"id": m.group(1), "text": claim_text, "source": source})
    return beliefs


def _load_processed(path: Path) -> dict[str, str]:
    """Load processed entries tracking {path: content_hash}."""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _save_processed(path: Path, entries: list[Path], existing: dict[str, str]):
    """Record entries as processed by content hash."""
    updated = dict(existing)
    for entry_path in entries:
        content = entry_path.read_text()
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        updated[str(entry_path)] = content_hash
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated, indent=2) + "\n")


def _filter_unprocessed(entries: list[Path], processed: dict[str, str]) -> list[Path]:
    """Return entries that are new or modified since last propose."""
    unprocessed = []
    for entry_path in entries:
        key = str(entry_path)
        if key not in processed:
            unprocessed.append(entry_path)
            continue
        content = entry_path.read_text()
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        if content_hash != processed[key]:
            unprocessed.append(entry_path)
    return unprocessed


# --- Embedding support ---


def _load_belief_vectors(cache_path: Path) -> dict[str, list[float]]:
    """Load cached belief vectors from JSON."""
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _save_belief_vectors(cache_path: Path, vectors: dict[str, list[float]]):
    """Save belief vectors to JSON cache."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(vectors))


def _get_belief_embeddings(
    beliefs: list[dict], cache_path: Path,
) -> dict[str, list[float]]:
    """Get embeddings for all beliefs, using cache for known ones."""
    model = _get_embed_model()
    cached = _load_belief_vectors(cache_path)

    def _cache_key(belief):
        text_hash = hashlib.sha256(belief["text"].encode()).hexdigest()[:8]
        return f"{belief['id']}:{text_hash}"

    needed = []
    needed_keys = []
    result = {}
    for belief in beliefs:
        key = _cache_key(belief)
        if key in cached:
            result[belief["id"]] = cached[key]
        else:
            needed.append(belief)
            needed_keys.append(key)

    if needed:
        texts = [b["text"] for b in needed]
        vectors = list(model.embed(texts))
        for belief, key, vec in zip(needed, needed_keys, vectors):
            vec_list = vec.tolist()
            cached[key] = vec_list
            result[belief["id"]] = vec_list

        current_keys = {_cache_key(b) for b in beliefs}
        cached = {k: v for k, v in cached.items() if k in current_keys}
        _save_belief_vectors(cache_path, cached)

    return result


def _score_by_embedding(
    beliefs: list[dict],
    belief_vectors: dict[str, list[float]],
    batch_text: str,
    batch_entry_paths: list[str],
) -> list[tuple[float, dict]]:
    """Score beliefs by embedding similarity to batch content."""
    import numpy as np

    model = _get_embed_model()
    batch_summary = batch_text[:4000]
    query_vec = np.array(list(model.embed([batch_summary]))[0], dtype=np.float32)

    scored = []
    for belief in beliefs:
        vec = belief_vectors.get(belief["id"])
        if vec is None:
            scored.append((0.0, belief))
            continue
        belief_vec = np.array(vec, dtype=np.float32)
        dot = np.dot(query_vec, belief_vec)
        norm = np.linalg.norm(query_vec) * np.linalg.norm(belief_vec)
        similarity = float(dot / norm) if norm > 0 else 0.0
        if belief["source"] and any(belief["source"] in p or p in belief["source"]
                                     for p in batch_entry_paths):
            similarity += 1.0
        scored.append((similarity, belief))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _score_by_keywords(
    beliefs: list[dict],
    batch_text: str,
    batch_entry_paths: list[str],
) -> list[tuple[float, dict]]:
    """Score beliefs by keyword overlap (fallback when embeddings unavailable)."""
    batch_words = set(re.findall(r'[a-z]{3,}', batch_text.lower()))

    scored = []
    for belief in beliefs:
        score = 0.0
        if belief["source"] and any(belief["source"] in p or p in belief["source"]
                                     for p in batch_entry_paths):
            score += 1000
        belief_words = set(re.findall(r'[a-z]{3,}', belief["text"].lower()))
        belief_words |= set(belief["id"].replace("-", " ").lower().split())
        overlap = len(batch_words & belief_words)
        score += overlap
        scored.append((score, belief))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _build_dedup_context(
    existing_beliefs: list[dict],
    batch_entry_paths: list[str],
    batch_text: str,
    max_detailed: int = 50,
    max_compact: int = 200,
    belief_vectors: dict[str, list[float]] | None = None,
) -> str:
    """Build per-batch dedup context: relevant beliefs with text, rest as compact IDs."""
    if not existing_beliefs:
        return ""

    if belief_vectors:
        scored = _score_by_embedding(
            existing_beliefs, belief_vectors, batch_text, batch_entry_paths,
        )
    else:
        scored = _score_by_keywords(
            existing_beliefs, batch_text, batch_entry_paths,
        )

    detailed = scored[:max_detailed]
    compact = scored[max_detailed:max_detailed + max_compact]

    parts = [
        "\n\n## Already Accepted Beliefs\n\n"
        "The following beliefs already exist. Do NOT propose beliefs with these IDs "
        "or that duplicate their meaning under different names.\n"
    ]

    if detailed:
        parts.append("\nRelevant existing beliefs:")
        for _, belief in detailed:
            parts.append(f"- `{belief['id']}`: {belief['text']}")

    if compact:
        compact_ids = ", ".join(b["id"] for _, b in compact)
        parts.append(f"\nOther existing IDs: {compact_ids}")

    return "\n".join(parts) + "\n"


# --- Commands ---


def cmd_propose_beliefs(args):
    """Extract candidate beliefs from entries for human review."""
    from .caffeinate import hold as _caffeinate
    _caffeinate()
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"Entries directory not found: {input_dir}")
        sys.exit(1)

    if not check_model_available(args.model):
        print(f"Model not available: {args.model}")
        sys.exit(1)

    # Collect entries
    if hasattr(args, 'entry') and args.entry:
        entries = [Path(p) for p in args.entry]
    else:
        entries = sorted(input_dir.rglob("*.md"))

    if not entries:
        print(f"No .md files found.")
        return

    # Filter out already-processed entries (unless --all or --entry)
    processed_path = Path(PROJECT_DIR) / "proposed-entries.json"
    processed = _load_processed(processed_path)
    process_all = getattr(args, 'all', False)
    has_entry_flag = hasattr(args, 'entry') and args.entry

    if not process_all and not has_entry_flag:
        total = len(entries)
        entries = _filter_unprocessed(entries, processed)
        skipped = total - len(entries)
        if skipped:
            print(f"Skipping {skipped} already-processed entries (use --all to reprocess)")
        if not entries:
            print("No new entries to process.")
            return

    # Load existing beliefs for dedup context
    existing_beliefs = _load_existing_beliefs(Path("beliefs.md"))
    existing_ids = {b["id"] for b in existing_beliefs}

    if existing_ids:
        print(f"Found {len(existing_ids)} existing beliefs (will skip duplicates)")

    # Compute belief embeddings once (if fastembed available)
    belief_vectors = None
    if existing_beliefs and _has_embeddings():
        print("Computing belief embeddings for semantic dedup...")
        cache_path = Path(PROJECT_DIR) / "belief-vectors.json"
        belief_vectors = _get_belief_embeddings(existing_beliefs, cache_path)
        print(f"  {len(belief_vectors)} belief vectors ready")
    elif existing_beliefs:
        print("(install fastembed for semantic dedup: uv pip install 'expert-agent-builder[embeddings]')")

    print(f"Reading {len(entries)} entries...")

    # Batch entries — track paths per batch for relevance scoring
    batches = []
    batch_paths = []
    current_batch = []
    current_paths = []
    for entry_path in entries:
        content = entry_path.read_text()
        if len(content) > 10000:
            content = content[:10000] + "\n[Truncated]"
        current_batch.append(f"--- FILE: {entry_path} ---\n{content}")
        current_paths.append(str(entry_path))
        if len(current_batch) >= args.batch_size:
            batches.append("\n\n".join(current_batch))
            batch_paths.append(current_paths)
            current_batch = []
            current_paths = []
    if current_batch:
        batches.append("\n\n".join(current_batch))
        batch_paths.append(current_paths)

    print(f"Processing {len(batches)} batches (batch size: {args.batch_size})...")

    all_proposals = []
    for i, batch_text in enumerate(batches):
        print(f"  Batch {i + 1}/{len(batches)}...")
        existing_context = _build_dedup_context(
            existing_beliefs, batch_paths[i], batch_text,
            belief_vectors=belief_vectors,
        )
        prompt = PROPOSE_BELIEFS.format(entries=batch_text) + existing_context
        try:
            result = invoke_sync(prompt, model=args.model, timeout=600)
            all_proposals.append(result)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

    # Filter out proposals whose IDs already exist
    filtered_proposals = []
    skipped = 0
    for proposal in all_proposals:
        lines = proposal.split("\n")
        filtered_lines = []
        skip_until_next = False
        for line in lines:
            m = re.match(r"^### \[?(?:ACCEPT|REJECT)\]? (\S+)", line)
            if m:
                belief_id = m.group(1)
                if belief_id in existing_ids:
                    skip_until_next = True
                    skipped += 1
                    continue
                else:
                    skip_until_next = False
            if skip_until_next:
                if line.startswith("### "):
                    skip_until_next = False
                    filtered_lines.append(line)
                continue
            filtered_lines.append(line)
        filtered_proposals.append("\n".join(filtered_lines))

    if skipped:
        print(f"  Filtered {skipped} already-accepted beliefs")

    # Record processed entries
    _save_processed(processed_path, entries, processed)

    # Write proposals file (append if it already exists)
    source_desc = (", ".join(str(e) for e in entries)
                   if has_entry_flag
                   else f"{len(entries)} entries from {input_dir}/")
    output = Path(args.output)
    if output.exists() and output.stat().st_size > 0:
        with output.open("a") as f:
            f.write(f"\n---\n\n")
            f.write(f"**Generated:** {date.today().isoformat()}\n")
            f.write(f"**Source:** {source_desc}\n")
            f.write(f"**Model:** {args.model}\n\n")
            for proposal in filtered_proposals:
                f.write(proposal)
                f.write("\n\n")
        print(f"\nAppended to {output}")
    else:
        with output.open("w") as f:
            f.write("# Proposed Beliefs\n\n")
            f.write("Edit each entry: change `[ACCEPT/REJECT]` to `[ACCEPT]` or `[REJECT]`.\n")
            f.write("Then run: `expert-build accept-beliefs`\n\n")
            f.write("---\n\n")
            f.write(f"**Generated:** {date.today().isoformat()}\n")
            f.write(f"**Source:** {source_desc}\n")
            f.write(f"**Model:** {args.model}\n\n")
            for proposal in filtered_proposals:
                f.write(proposal)
                f.write("\n\n")
        print(f"\nWrote {output}")

    print("Review the file, mark entries as [ACCEPT] or [REJECT], then run:")
    print("  expert-build accept-beliefs")


def _accept_batch(matches: list[tuple[str, str, str]]) -> bool:
    """Try to add all beliefs in one subprocess via 'beliefs add-batch'.

    Returns True if batch mode succeeded, False to fall back to per-belief.
    """
    lines = []
    for belief_id, claim_text, source in matches:
        lines.append(json.dumps({
            "id": belief_id,
            "text": claim_text.strip(),
            "source": source.strip(),
        }))
    json_input = "\n".join(lines)

    try:
        result = subprocess.run(
            ["beliefs", "add-batch"],
            input=json_input,
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("ERROR: beliefs CLI not found. Install with: uv tool install beliefs")
        sys.exit(1)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "invalid choice" in stderr or "unrecognized arguments" in stderr:
            return False
        print(f"Batch failed: {stderr}")
        return False

    if result.stdout.strip():
        print(result.stdout.strip())

    return True


def cmd_accept_beliefs(args):
    """Import accepted beliefs from proposals file."""
    proposals_file = Path(args.file)
    if not proposals_file.exists():
        print(f"Proposals file not found: {proposals_file}")
        print("Run: expert-build propose-beliefs")
        sys.exit(1)

    text = proposals_file.read_text()

    # Parse accepted beliefs — tolerate both ### [ACCEPT] and ### ACCEPT
    pattern = re.compile(
        r"### \[?ACCEPT\]? (\S+)\n"
        r"(.+?)\n"
        r"- Source: (.+?)(?:\n|$)"
    )
    matches = pattern.findall(text)

    if not matches:
        print("No [ACCEPT] entries found in proposals file.")
        print("Edit the file and change [ACCEPT/REJECT] to [ACCEPT] for beliefs to keep.")
        return

    print(f"Found {len(matches)} accepted beliefs")

    # Try batch mode first (single subprocess, single parse of beliefs.md)
    if _accept_batch(matches):
        return

    # Fall back to per-belief add
    print("Falling back to per-belief add...")
    added = 0
    failed = 0
    for belief_id, claim_text, source in matches:
        try:
            result = subprocess.run(
                ["beliefs", "add",
                 "--id", belief_id,
                 "--text", claim_text.strip(),
                 "--source", source.strip()],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print(f"  Added: {belief_id}")
                added += 1
            else:
                stderr = result.stderr.strip()
                if "already exists" in stderr or "already exists" in result.stdout:
                    print(f"  EXISTS: {belief_id}")
                else:
                    print(f"  FAIL: {belief_id}: {stderr or result.stdout.strip()}")
                    failed += 1
        except FileNotFoundError:
            print("ERROR: beliefs CLI not found. Install with: uv tool install beliefs")
            sys.exit(1)

    print(f"\nAccepted {added} beliefs ({failed} failed)")
    if added:
        print("Run: shared-enterprise sync")
