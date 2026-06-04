"""Summarize source documents into entries using an LLM."""

import sys
from datetime import date
from pathlib import Path

from .llm import check_model_available, invoke_sync
from .prompts import SUMMARIZE, SUMMARIZE_CODE


def cmd_summarize(args):
    """Generate entries from source documents."""
    from .caffeinate import hold as _caffeinate
    _caffeinate()
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"Source directory not found: {input_dir}")
        print("Run: expert-build fetch-docs <url>")
        sys.exit(1)

    if not check_model_available(args.model):
        print(f"Model not available: {args.model}")
        print("Install claude CLI or specify --model")
        sys.exit(1)

    sources = sorted(
        [*input_dir.glob("*.md"), *input_dir.glob("*.py")],
        key=lambda p: p.name,
    )
    if not sources:
        print(f"No .md or .py files in {input_dir}")
        return

    if args.limit:
        sources = sources[:args.limit]

    # Track what's been summarized
    manifest = Path(".summarized")
    done = set()
    if manifest.exists():
        done = set(manifest.read_text().strip().split("\n"))

    processed = 0
    skipped = 0

    for source_path in sources:
        if str(source_path) in done:
            skipped += 1
            continue

        print(f"Summarizing: {source_path.name}")

        content = source_path.read_text()

        # Extract and strip frontmatter
        source_url = None
        source_id = None
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                frontmatter = content[3:end]
                for line in frontmatter.splitlines():
                    if line.startswith("source_url:"):
                        source_url = line.split(":", 1)[1].strip()
                    elif line.startswith("source:"):
                        val = line.split(":", 1)[1].strip()
                        if not source_url and val.startswith(("http://", "https://")):
                            source_url = val
                    elif line.startswith("source_id:"):
                        source_id = line.split(":", 1)[1].strip()
                content = content[end + 3:].strip()

        if not content.strip():
            print(f"  SKIP (empty)")
            continue

        # Truncate very long documents
        if len(content) > 30000:
            original_len = len(content)
            content = content[:30000] + "\n\n[Truncated — original was longer]"
            if source_path.suffix == ".pdf":
                print(f"  WARN: truncated from {original_len} to 30000 chars. "
                      f"Consider: expert-build chunk-pdf {source_path}")
            else:
                print(f"  WARN: truncated from {original_len} to 30000 chars. "
                      f"Consider: expert-build chunk-docs")

        template = SUMMARIZE_CODE if source_path.suffix == ".py" else SUMMARIZE
        prompt = template.format(content=content)

        try:
            summary = invoke_sync(prompt, model=args.model)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        topic = source_path.stem

        # Write entry directly with provenance frontmatter
        today = date.today()
        entry_dir = Path("entries") / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}"
        entry_dir.mkdir(parents=True, exist_ok=True)
        entry_path = entry_dir / f"{topic}.md"

        fm_lines = [f"source: {source_path}"]
        if source_url:
            fm_lines.append(f"source_url: {source_url}")
        if source_id:
            fm_lines.append(f"source_id: {source_id}")
        frontmatter = "---\n" + "\n".join(fm_lines) + "\n---\n\n"

        entry_path.write_text(frontmatter + summary + "\n")
        print(f"  -> Created {entry_path}")

        # Record as done
        with manifest.open("a") as f:
            f.write(f"{source_path}\n")
        done.add(str(source_path))

        processed += 1

    print(f"\nSummarized {processed} sources ({skipped} already done)")
