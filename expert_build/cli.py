"""Expert agent builder CLI."""

import argparse
import importlib
import sys
from pathlib import Path

from . import __version__


def _lazy(module_name, func_name):
    """Lazy import to keep startup fast."""
    mod = importlib.import_module(f".{module_name}", package="expert_build")
    return getattr(mod, func_name)


def main():
    parser = argparse.ArgumentParser(
        prog="expert-build",
        description="Build expert agents from documented domains",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command")

    # -- init --
    init_p = sub.add_parser("init", help="Bootstrap a new expert agent repo")
    init_p.add_argument("name", help="Domain name (e.g., rhcsa, kubernetes)")
    init_p.add_argument("--domain", help="One-line domain description")

    # -- fetch-docs --
    fetch_p = sub.add_parser("fetch-docs", help="Fetch documentation from URLs")
    fetch_p.add_argument("url", help="Starting URL to fetch")
    fetch_p.add_argument("--depth", type=int, default=1, help="Crawl depth (default: 1)")
    fetch_p.add_argument("--output-dir", default="sources", help="Output directory (default: sources)")
    fetch_p.add_argument("--selector", default="main,article,.content,body",
                         help="CSS selectors for content (comma-separated, default: main,article,.content,body)")
    fetch_p.add_argument("--sitemap", action="store_true", help="Use sitemap.xml for URL discovery")
    fetch_p.add_argument("--include", help="URL pattern to include (glob)")
    fetch_p.add_argument("--exclude", help="URL pattern to exclude (glob)")
    fetch_p.add_argument("--delay", type=float, default=1.0, help="Delay between requests in seconds (default: 1.0)")

    # -- summarize --
    sum_p = sub.add_parser("summarize", help="Generate entries from source documents")
    sum_p.add_argument("--input-dir", default="sources", help="Source directory (default: sources)")
    sum_p.add_argument("--limit", type=int, help="Max files to process")
    sum_p.add_argument("--model", default="claude", help="Model to use (default: claude)")

    # -- propose-beliefs --
    prop_p = sub.add_parser("propose-beliefs", help="Extract candidate beliefs from entries")
    prop_p.add_argument("--input-dir", default="entries", help="Entries directory (default: entries)")
    prop_p.add_argument("--output", default="proposed-beliefs.md",
                        help="Output file (default: proposed-beliefs.md)")
    prop_p.add_argument("--model", default="claude", help="Model to use (default: claude)")
    prop_p.add_argument("--batch-size", type=int, default=5,
                        help="Entries per LLM batch (default: 5)")
    prop_p.add_argument("--entry", action="append",
                        help="Process specific entry file(s) instead of all entries")
    prop_p.add_argument("--all", action="store_true",
                        help="Re-process all entries (ignore processed tracking)")

    # -- accept-beliefs --
    accept_p = sub.add_parser("accept-beliefs", help="Import accepted beliefs from proposals")
    accept_p.add_argument("--file", default="proposed-beliefs.md",
                          help="Proposals file (default: proposed-beliefs.md)")

    # -- cert-coverage --
    cert_p = sub.add_parser("cert-coverage", help="Map cert objectives to beliefs")
    cert_p.add_argument("objectives_file", help="Path to certification objectives")
    cert_p.add_argument("--beliefs-file", type=Path, default=Path("beliefs.md"))
    cert_p.add_argument("--model", default=None, help="Use LLM for semantic matching")

    # -- exam --
    exam_p = sub.add_parser("exam", help="Run practice questions, discover gaps")
    exam_p.add_argument("questions_file", help="Path to practice questions")
    exam_p.add_argument("--model", default="claude", help="Model to use (default: claude)")
    exam_p.add_argument("--beliefs-file", type=Path, default=Path("beliefs.md"))
    exam_p.add_argument("--limit", type=int, help="Max questions to process")
    exam_p.add_argument("--output", "-o", type=Path, default=None,
                        help="Save results to file (markdown)")
    exam_p.add_argument("--no-judge", action="store_true",
                        help="Disable LLM judge for open-ended questions (use string matching)")

    # -- status --
    sub.add_parser("status", help="Show pipeline progress")

    # -- install-skill --
    skill_p = sub.add_parser("install-skill", help="Install Claude Code skill file")
    skill_p.add_argument("--skill-dir", type=Path, default=Path(".claude/skills"),
                         help="Target skills directory")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": lambda a: _lazy("init_cmd", "cmd_init")(a),
        "fetch-docs": lambda a: _lazy("fetch", "cmd_fetch_docs")(a),
        "summarize": lambda a: _lazy("summarize", "cmd_summarize")(a),
        "propose-beliefs": lambda a: _lazy("propose", "cmd_propose_beliefs")(a),
        "accept-beliefs": lambda a: _lazy("propose", "cmd_accept_beliefs")(a),
        "cert-coverage": lambda a: _lazy("coverage", "cmd_cert_coverage")(a),
        "exam": lambda a: _lazy("exam", "cmd_exam")(a),
        "status": lambda a: _lazy("init_cmd", "cmd_status")(a),
        "install-skill": lambda a: _lazy("init_cmd", "cmd_install_skill")(a),
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
