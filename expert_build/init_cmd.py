"""Init, status, and install-skill commands."""

import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from reasons_lib.api import init_db, get_status as reasons_status

REASONS_DB = "reasons.db"


def cmd_init(args):
    """Bootstrap a new expert agent repo."""
    name = args.name
    domain = args.domain or name
    cwd = Path.cwd()

    # Check prerequisites
    if not shutil.which("git"):
        print("Error: git not found")
        sys.exit(1)

    # git init
    if not (cwd / ".git").exists():
        subprocess.run(["git", "init"], check=True)
        print("Initialized git repo")

    # Create directories
    for d in ["entries", "sources", "objectives", "questions"]:
        (cwd / d).mkdir(exist_ok=True)

    # reasons init
    if not (cwd / REASONS_DB).exists():
        init_db(db_path=REASONS_DB)
        print("Initialized reasons database")
    else:
        print(f"{REASONS_DB} already exists, skipping reasons init")

    # Generate CLAUDE.md from template
    claude_md = cwd / "CLAUDE.md"
    if not claude_md.exists():
        template_path = Path(__file__).parent / "data" / "CLAUDE.md.template"
        template = template_path.read_text()
        content = template.replace("{{NAME}}", name).replace("{{DOMAIN}}", domain)
        claude_md.write_text(content)
        print(f"Created CLAUDE.md for {name}")
    else:
        print("CLAUDE.md already exists, skipping")

    # Create expert-build.md config
    config = cwd / "expert-build.md"
    if not config.exists():
        config.write_text(
            f"# Expert Build: {name}\n"
            f"\n"
            f"**Domain:** {domain}\n"
            f"**Created:** {date.today().isoformat()}\n"
            f"\n"
            f"## Sources\n"
            f"\n"
            f"_No sources fetched yet. Run `expert-build fetch-docs <url>` to start._\n"
            f"\n"
            f"## Certification\n"
            f"\n"
            f"- Objectives: objectives/\n"
            f"- Questions: questions/\n"
            f"\n"
            f"## Progress\n"
            f"\n"
            f"- Sources fetched: 0\n"
            f"- Entries generated: 0\n"
            f"- Beliefs proposed: 0\n"
            f"- Beliefs accepted: 0\n"
            f"- Last exam score: --\n"
        )
        print(f"Created expert-build.md")
    else:
        print("expert-build.md already exists, skipping")

    print(f"\nExpert agent repo initialized: {name}")
    print(f"Next: expert-build fetch-docs <documentation-url>")


def cmd_status(args):
    """Show pipeline progress."""
    cwd = Path.cwd()

    # Count sources
    sources_dir = cwd / "sources"
    source_count = len(list(sources_dir.glob("*.md"))) if sources_dir.exists() else 0

    # Count entries
    entries_dir = cwd / "entries"
    entry_count = 0
    if entries_dir.exists():
        entry_count = len(list(entries_dir.rglob("*.md")))

    # Count beliefs and nogoods from reasons database
    belief_count = 0
    nogood_count = 0
    reasons_db = cwd / REASONS_DB
    if reasons_db.exists():
        try:
            from reasons_lib.api import export_network
            status = reasons_status(db_path=REASONS_DB)
            belief_count = status["in_count"]
            network = export_network(db_path=REASONS_DB)
            nogood_count = len(network.get("nogoods", []))
        except Exception:
            pass

    # Count proposed beliefs
    proposed_file = cwd / "proposed-beliefs.md"
    proposed = 0
    accepted = 0
    if proposed_file.exists():
        text = proposed_file.read_text()
        import re
        proposed = len(re.findall(r"^### \[", text, re.MULTILINE))
        accepted = len(re.findall(r"^### \[ACCEPT\]", text, re.MULTILINE))

    # Read config for domain name
    config = cwd / "expert-build.md"
    domain = "unknown"
    if config.exists():
        first_line = config.read_text().split("\n")[0]
        if first_line.startswith("# Expert Build: "):
            domain = first_line[len("# Expert Build: "):]

    print(f"=== Expert Agent Status: {domain} ===")
    print(f"Sources:     {source_count} documents fetched")
    print(f"Entries:     {entry_count} entries")
    print(f"Beliefs:     {belief_count} IN")
    print(f"Nogoods:     {nogood_count} recorded")
    if proposed:
        print(f"Proposed:    {proposed} candidates ({accepted} accepted)")


def cmd_install_skill(args):
    """Install Claude Code skill file."""
    skill_source = Path(__file__).parent / "data" / "SKILL.md"
    target_dir = Path(args.skill_dir) / "expert-build"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "SKILL.md"
    shutil.copy2(skill_source, target)
    print(f"Installed {target}")
