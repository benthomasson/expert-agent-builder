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
    init_p.add_argument("--no-git", action="store_true", help="Skip git init (for subdirectories of existing repos)")

    # -- chunk-pdf --
    chunk_p = sub.add_parser("chunk-pdf", help="Chunk a PDF paper into section entries")
    chunk_p.add_argument("pdf", help="Path to PDF file")
    chunk_p.add_argument("--prefix", help="Entry filename prefix (e.g., 'doyle-1979')")
    chunk_p.add_argument("--source-label", help="Citation label for Source line")
    chunk_p.add_argument("--dry-run", action="store_true", help="Show sections without creating entries")

    # -- chunk-docs --
    chunkd_p = sub.add_parser("chunk-docs", help="Chunk large documents into entry-sized pieces")
    chunkd_p.add_argument("--input-dir", default="sources", help="Source directory (default: sources)")
    chunkd_p.add_argument("--threshold", type=int, default=30000,
                          help="Only chunk files larger than this (default: 30000)")
    chunkd_p.add_argument("--recursive", "-r", action="store_true",
                          help="Recursively search subdirectories")
    chunkd_p.add_argument("--dry-run", action="store_true", help="Show chunks without creating entries")

    # -- summarize --
    sum_p = sub.add_parser("summarize", help="Generate entries from source documents")
    sum_p.add_argument("--input-dir", default="sources", help="Source directory (default: sources)")
    sum_p.add_argument("--recursive", "-r", action="store_true",
                       help="Recursively search subdirectories")
    sum_p.add_argument("--parallel", type=int, default=1,
                       help="Number of parallel LLM calls (default: 1)")
    sum_p.add_argument("--limit", type=int, help="Max files to process")
    sum_p.add_argument("--model", default="claude", help="Model to use (default: claude)")

    # -- propose-beliefs --
    prop_p = sub.add_parser("propose-beliefs", help="Extract candidate beliefs from entries")
    prop_p.add_argument("--input-dir", default="entries", help="Entries directory (default: entries)")
    prop_p.add_argument("--output", default="proposed-beliefs.md",
                        help="Output file (default: proposed-beliefs.md)")
    prop_p.add_argument("--model", default="claude", help="Model to use (default: claude)")
    prop_p.add_argument("--parallel", type=int, default=1,
                        help="Number of parallel LLM calls (default: 1)")
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
    cert_p.add_argument("--beliefs-file", type=Path, default=Path("reasons.db"))
    cert_p.add_argument("--model", default=None, help="Use LLM for semantic matching")

    # -- exam --
    exam_p = sub.add_parser("exam", help="Run practice questions, discover gaps")
    exam_p.add_argument("questions_file", help="Path to practice questions")
    exam_p.add_argument("--model", default="claude", help="Model to use (default: claude)")
    exam_p.add_argument("--beliefs-file", type=Path, default=Path("reasons.db"))
    exam_p.add_argument("--limit", type=int, help="Max questions to process")
    exam_p.add_argument("--output", "-o", type=Path, default=None,
                        help="Save results to file (markdown)")
    exam_p.add_argument("--no-judge", action="store_true",
                        help="Disable LLM judge for open-ended questions (use string matching)")
    exam_p.add_argument("--no-beliefs", action="store_true",
                        help="Run without belief context (control condition)")
    exam_p.add_argument("--agentic", action="store_true",
                        help="Use tool-calling mode (search beliefs per question)")
    exam_p.add_argument("--max-turns", type=int, default=5,
                        help="Max tool-calling turns per question in agentic mode (default: 5)")
    exam_p.add_argument("--max-depth", type=int, default=None,
                        help="Only include beliefs up to this derivation depth (e.g. 0=premises, 3=depth 0-3)")

    # -- exam-matrix --
    em_p = sub.add_parser("exam-matrix",
                          help="Run exam across models × beliefs/control")
    em_p.add_argument("questions_file", help="Path to practice questions")
    em_p.add_argument("--models", default="claude:opus,claude:sonnet,claude:haiku",
                      help="Comma-separated models (default: claude:opus,claude:sonnet,claude:haiku)")
    em_p.add_argument("--output-dir", default="results",
                      help="Output directory (default: results)")
    em_p.add_argument("--beliefs-file", type=Path, default=Path("reasons.db"))
    em_p.add_argument("--limit", type=int, help="Max questions to process")
    em_p.add_argument("--no-judge", action="store_true",
                      help="Disable LLM judge for open-ended questions")
    em_p.add_argument("--timeout", type=int, default=120,
                      help="LLM timeout per question in seconds (default: 120)")
    em_p.add_argument("--agentic", action="store_true",
                      help="Include agentic mode (tool-calling) as third condition")
    em_p.add_argument("--max-turns", type=int, default=5,
                      help="Max tool-calling turns per question in agentic mode (default: 5)")
    em_p.add_argument("--max-depth", type=int, default=None,
                      help="Only include beliefs up to this derivation depth (e.g. 0=premises, 3=depth 0-3)")

    # -- pipeline --
    pipe_p = sub.add_parser("pipeline", help="Run end-to-end EEM construction pipeline")
    pipe_p.add_argument("--pdf", action="append", help="PDF files to chunk (repeatable)")
    pipe_p.add_argument("--sources-dir", default="sources", help="Source directory (default: sources)")
    pipe_p.add_argument("--model", default="claude", help="Model for LLM calls (default: claude)")
    pipe_p.add_argument("--rounds", type=int, default=3,
                        help="Max derive/review/repair cycles (default: 3)")
    pipe_p.add_argument("--max-derive-rounds", type=int, default=10,
                        help="Max derive exhaust rounds per cycle (default: 10)")
    pipe_p.add_argument("--no-auto-accept", action="store_true",
                        help="Stop after propose-beliefs for human review")
    pipe_p.add_argument("--timeout", type=int, default=600,
                        help="LLM timeout in seconds (default: 600)")
    pipe_p.add_argument("--domain", help="Domain description for derive context")
    pipe_p.add_argument("--parallel", type=int, default=1,
                        help="Number of parallel LLM calls for summarize/propose (default: 1)")
    pipe_p.add_argument("--recursive", "-r", action="store_true",
                        help="Recursively search source subdirectories")
    pipe_p.add_argument("--resume", action="store_true",
                        help="Resume from last saved pipeline state")
    pipe_p.add_argument("--namespace", default=None,
                        help="Filter derive/review to belief namespace (use '' for non-namespaced)")

    # -- derive-review-repair --
    drr_p = sub.add_parser("derive-review-repair",
                           help="Run derive/review/repair loop on existing belief network")
    drr_p.add_argument("--model", default="claude", help="Model to use (default: claude)")
    drr_p.add_argument("--rounds", type=int, default=3,
                       help="Max derive/review/repair cycles (default: 3)")
    drr_p.add_argument("--max-derive-rounds", type=int, default=10,
                       help="Max derive exhaust rounds per cycle (default: 10)")
    drr_p.add_argument("--timeout", type=int, default=600,
                       help="LLM timeout in seconds (default: 600)")
    drr_p.add_argument("--domain", help="Domain description for derive context")
    drr_p.add_argument("--namespace", default=None,
                       help="Filter to belief namespace (use '' for non-namespaced)")

    # -- index-sources --
    idx_p = sub.add_parser("index-sources", help="Build FTS5 chunks database from source documents")
    idx_p.add_argument("--input-dir", default="sources", help="Source directory (default: sources)")
    idx_p.add_argument("--recursive", "-r", action="store_true",
                       help="Recursively search subdirectories")
    idx_p.add_argument("--db", default="rag_fts.db", help="Output database path (default: rag_fts.db)")
    idx_p.add_argument("--type", default="source", choices=["source", "summary", "chunked-summary"],
                       help="Chunk type metadata (default: source)")
    idx_p.add_argument("--chunk-size", type=int, default=2000,
                       help="Target chunk size in chars (default: 2000)")
    idx_p.add_argument("--rebuild", action="store_true",
                       help="Drop and rebuild the index from scratch")

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
        "chunk-pdf": lambda a: _lazy("chunk_pdf", "cmd_chunk_pdf")(a),
        "chunk-docs": lambda a: _lazy("chunk_docs", "cmd_chunk_docs")(a),
        "summarize": lambda a: _lazy("summarize", "cmd_summarize")(a),
        "propose-beliefs": lambda a: _lazy("propose", "cmd_propose_beliefs")(a),
        "accept-beliefs": lambda a: _lazy("propose", "cmd_accept_beliefs")(a),
        "cert-coverage": lambda a: _lazy("coverage", "cmd_cert_coverage")(a),
        "exam": lambda a: _lazy("exam", "cmd_exam")(a),
        "exam-matrix": lambda a: _lazy("exam_matrix", "cmd_exam_matrix")(a),
        "index-sources": lambda a: _lazy("index_sources", "cmd_index_sources")(a),
        "pipeline": lambda a: _lazy("pipeline", "cmd_pipeline")(a),
        "derive-review-repair": lambda a: _lazy("pipeline", "cmd_derive_review_repair")(a),
        "status": lambda a: _lazy("init_cmd", "cmd_status")(a),
        "install-skill": lambda a: _lazy("init_cmd", "cmd_install_skill")(a),
    }

    subparser_names = set(sub.choices.keys())
    command_names = set(commands.keys())
    if subparser_names != command_names:
        missing_dispatch = subparser_names - command_names
        missing_parser = command_names - subparser_names
        parts = []
        if missing_dispatch:
            parts.append(f"subcommands without dispatch: {sorted(missing_dispatch)}")
        if missing_parser:
            parts.append(f"dispatch keys without subcommand: {sorted(missing_parser)}")
        print(f"CLI registration error: {'; '.join(parts)}", file=sys.stderr)
        sys.exit(1)

    try:
        commands[args.command](args)
    except KeyboardInterrupt:
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        from .llm import get_cost_summary, format_cost_summary
        local = get_cost_summary()
        try:
            from reasons_lib.llm import get_cost_summary as reasons_cost
            remote = reasons_cost()
        except ImportError:
            remote = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_cost_usd": 0.0}
        total_calls = local["calls"] + remote["calls"]
        if total_calls > 0:
            total_input = local["input_tokens"] + remote["input_tokens"]
            total_output = local["output_tokens"] + remote["output_tokens"]
            total_cost = local["total_cost_usd"] + remote["total_cost_usd"]
            parts = []
            if total_cost > 0:
                parts.append(f"${total_cost:.4f}")
            parts.append(f"{total_input:,} input + {total_output:,} output tokens")
            parts.append(f"{total_calls} call(s)")
            print(f"\nCost: {' | '.join(parts)}", file=sys.stderr)


if __name__ == "__main__":
    main()
