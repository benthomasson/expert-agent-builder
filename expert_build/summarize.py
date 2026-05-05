"""Summarize source documents into entries using an LLM."""

import re
import subprocess
import sys
from pathlib import Path

from .llm import check_model_available, invoke_sync
from .prompts import SUMMARIZE


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

    sources = sorted(input_dir.glob("*.md"))
    if not sources:
        print(f"No .md files in {input_dir}")
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
                    elif line.startswith("source_id:"):
                        source_id = line.split(":", 1)[1].strip()
                content = content[end + 3:].strip()

        if not content.strip():
            print(f"  SKIP (empty)")
            continue

        # Truncate very long documents
        if len(content) > 30000:
            content = content[:30000] + "\n\n[Truncated — original was longer]"

        prompt = SUMMARIZE.format(content=content)

        try:
            summary = invoke_sync(prompt, model=args.model)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        # Extract a title from the summary or source filename
        title_match = re.search(r"^#+ (.+)$", summary, re.MULTILINE)
        title = title_match.group(1) if title_match else source_path.stem.replace("-", " ").title()
        topic = source_path.stem

        # Create entry via entry CLI
        entry_path = None
        try:
            result = subprocess.run(
                ["entry", "create", topic, title, "--content", summary],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                entry_path = result.stdout.strip().replace("Created ", "")
                print(f"  -> {result.stdout.strip()}")
            else:
                # Try alternative invocation
                result = subprocess.run(
                    ["entry", "create", topic, title],
                    input=summary,
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    entry_path = result.stdout.strip().replace("Created ", "")
                    print(f"  -> {result.stdout.strip()}")
                else:
                    print(f"  WARN: entry create failed: {result.stderr.strip()}")
        except FileNotFoundError:
            print("  ERROR: entry CLI not found. Install with: uv tool install entry")
            sys.exit(1)

        # Prepend source provenance frontmatter to the entry file
        if entry_path and source_url:
            ep = Path(entry_path)
            if ep.exists():
                fm = f"---\nsource_url: {source_url}\n"
                if source_id:
                    fm += f"source_id: {source_id}\n"
                fm += "---\n\n"
                ep.write_text(fm + ep.read_text())

        # Record as done
        with manifest.open("a") as f:
            f.write(f"{source_path}\n")
        done.add(str(source_path))

        processed += 1

    print(f"\nSummarized {processed} sources ({skipped} already done)")
