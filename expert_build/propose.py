"""Propose and accept beliefs from entries."""

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from .llm import check_model_available, invoke_sync
from .prompts import PROPOSE_BELIEFS


def cmd_propose_beliefs(args):
    """Extract candidate beliefs from entries for human review."""
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"Entries directory not found: {input_dir}")
        sys.exit(1)

    if not check_model_available(args.model):
        print(f"Model not available: {args.model}")
        sys.exit(1)

    # Collect all entry files
    entries = sorted(input_dir.rglob("*.md"))
    if not entries:
        print(f"No .md files in {input_dir}")
        return

    print(f"Reading {len(entries)} entries...")

    # Batch entries
    batches = []
    current_batch = []
    for entry_path in entries:
        content = entry_path.read_text()
        if len(content) > 10000:
            content = content[:10000] + "\n[Truncated]"
        current_batch.append(f"--- FILE: {entry_path} ---\n{content}")
        if len(current_batch) >= args.batch_size:
            batches.append("\n\n".join(current_batch))
            current_batch = []
    if current_batch:
        batches.append("\n\n".join(current_batch))

    print(f"Processing {len(batches)} batches (batch size: {args.batch_size})...")

    all_proposals = []
    for i, batch_text in enumerate(batches):
        print(f"  Batch {i + 1}/{len(batches)}...")
        prompt = PROPOSE_BELIEFS.format(entries=batch_text)
        try:
            result = invoke_sync(prompt, model=args.model, timeout=600)
            all_proposals.append(result)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

    # Write proposals file
    output = Path(args.output)
    with output.open("w") as f:
        f.write(f"# Proposed Beliefs\n\n")
        f.write(f"**Generated:** {date.today().isoformat()}\n")
        f.write(f"**Source:** {len(entries)} entries from {input_dir}/\n")
        f.write(f"**Model:** {args.model}\n\n")
        f.write("Edit each entry: change `[ACCEPT/REJECT]` to `[ACCEPT]` or `[REJECT]`.\n")
        f.write("Then run: `expert-build accept-beliefs`\n\n")
        f.write("---\n\n")
        for proposal in all_proposals:
            f.write(proposal)
            f.write("\n\n")

    print(f"\nWrote {output}")
    print(f"Review the file, mark entries as [ACCEPT] or [REJECT], then run:")
    print(f"  expert-build accept-beliefs")


def cmd_accept_beliefs(args):
    """Import accepted beliefs from proposals file."""
    proposals_file = Path(args.file)
    if not proposals_file.exists():
        print(f"Proposals file not found: {proposals_file}")
        print("Run: expert-build propose-beliefs")
        sys.exit(1)

    text = proposals_file.read_text()

    # Parse accepted beliefs
    # Format: ### [ACCEPT] belief-id\nclaim text\n- Source: path
    pattern = re.compile(
        r"### \[ACCEPT\] (\S+)\n"
        r"(.+?)\n"
        r"- Source: (.+?)(?:\n|$)"
    )
    matches = pattern.findall(text)

    if not matches:
        print("No [ACCEPT] entries found in proposals file.")
        print("Edit the file and change [ACCEPT/REJECT] to [ACCEPT] for beliefs you want to keep.")
        return

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
                # Might already exist
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
