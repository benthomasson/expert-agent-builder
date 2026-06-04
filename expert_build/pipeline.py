"""End-to-end EEM construction pipeline."""

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .llm import check_model_available, invoke_sync
from .propose import PROJECT_DIR, REASONS_DB

STATE_FILE = Path(PROJECT_DIR) / "pipeline-state.json"

STAGE_NAMES = {
    1: "ingest",
    2: "summarize",
    3: "extract",
    4: "derive",
    5: "review",
    6: "repair",
    7: "deduplicate",
    8: "export",
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _init_state(args):
    state = {
        "started_at": _now(),
        "updated_at": _now(),
        "status": "running",
        "current_stage": None,
        "current_cycle": None,
        "args": {
            "url": getattr(args, "url", None),
            "model": args.model,
            "rounds": args.rounds,
            "domain": getattr(args, "domain", None),
        },
        "stages": {
            f"{n}_{name}": {"status": "pending"}
            for n, name in STAGE_NAMES.items()
        },
    }
    _save_state(state)
    return state


def _load_state():
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, ValueError):
        print(f"WARNING: corrupt state file {STATE_FILE}, ignoring",
              file=sys.stderr)
        return None


def _save_state(state):
    state["updated_at"] = _now()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def _mark_stage(state, stage_num, status, **meta):
    key = f"{stage_num}_{STAGE_NAMES[stage_num]}"
    state["stages"][key]["status"] = status
    if status == "running":
        state["current_stage"] = stage_num
    elif status == "completed":
        state["stages"][key]["completed_at"] = _now()
    state["stages"][key].update(meta)
    _save_state(state)


def _stage_completed(state, stage_num):
    key = f"{stage_num}_{STAGE_NAMES[stage_num]}"
    return state["stages"][key]["status"] == "completed"


def _banner(stage_num, total, name):
    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  Stage {stage_num}/{total}: {name}", file=sys.stderr)
    print(f"{'=' * 50}\n", file=sys.stderr)


def _stage_ingest(args):
    """Stage 1: Fetch docs or chunk PDFs into sources/."""
    if not args.no_fetch and args.url:
        from .fetch import cmd_fetch_docs
        fetch_args = SimpleNamespace(
            url=args.url,
            depth=args.depth,
            output_dir=args.sources_dir,
            selector="main,article,.content,body",
            sitemap=False,
            include=None,
            exclude=None,
            delay=1.0,
        )
        cmd_fetch_docs(fetch_args)
    elif args.no_fetch:
        print("Skipping fetch (--no-fetch)", file=sys.stderr)

    if args.pdf:
        from .chunk_pdf import cmd_chunk_pdf
        for pdf_path in args.pdf:
            chunk_args = SimpleNamespace(
                pdf=pdf_path,
                prefix=None,
                source_label=None,
                dry_run=False,
            )
            cmd_chunk_pdf(chunk_args)


def _stage_summarize(args):
    """Stage 2: Generate entries from source documents."""
    from .summarize import cmd_summarize
    sum_args = SimpleNamespace(
        input_dir=args.sources_dir,
        limit=None,
        model=args.model,
    )
    cmd_summarize(sum_args)


def _stage_extract(args):
    """Stage 3: Extract beliefs from entries and optionally auto-accept."""
    from .propose import cmd_propose_beliefs, cmd_accept_beliefs

    prop_args = SimpleNamespace(
        input_dir="entries",
        output="proposed-beliefs.md",
        model=args.model,
        batch_size=5,
        entry=None,
    )
    setattr(prop_args, "all", False)

    cmd_propose_beliefs(prop_args)

    if args.no_auto_accept:
        print("\nStopping after propose-beliefs (--no-auto-accept)", file=sys.stderr)
        print("Review proposed-beliefs.md, mark entries as [ACCEPT], then run:", file=sys.stderr)
        print("  expert-build accept-beliefs", file=sys.stderr)
        return False

    proposals_path = Path("proposed-beliefs.md")
    if proposals_path.exists():
        from .propose import auto_accept_proposals
        auto_accept_proposals(str(proposals_path))
        print("Auto-accepted all proposed beliefs", file=sys.stderr)

        accept_args = SimpleNamespace(file="proposed-beliefs.md")
        cmd_accept_beliefs(accept_args)

    return True


def _stage_derive(args, round_label=""):
    """Stage 4: Derive new beliefs until saturated or max rounds hit.

    Returns total number of beliefs added.
    """
    from reasons_lib.api import export_network
    from reasons_lib.derive import build_prompt, parse_proposals, validate_proposals, apply_proposals

    total_added = 0
    prefix = f"[{round_label}] " if round_label else ""

    for derive_round in range(1, args.max_derive_rounds + 1):
        print(f"{prefix}Derive round {derive_round}/{args.max_derive_rounds}...",
              file=sys.stderr)

        data = export_network(db_path=REASONS_DB)
        nodes = data.get("nodes", {})
        if not nodes:
            print(f"{prefix}No nodes in network", file=sys.stderr)
            break

        prompt, stats = build_prompt(nodes, domain=args.domain)
        print(f"{prefix}  Network: {stats['total_in']} IN, "
              f"{stats['total_derived']} derived, depth {stats['max_depth']}",
              file=sys.stderr)

        try:
            response = invoke_sync(prompt, model=args.model, timeout=args.timeout)
        except Exception as e:
            print(f"{prefix}  Derive error: {e}", file=sys.stderr)
            break

        proposals = parse_proposals(response)
        if not proposals:
            print(f"{prefix}  Saturated (no proposals)", file=sys.stderr)
            break

        valid, skipped = validate_proposals(proposals, nodes)
        for p, reason in skipped:
            print(f"{prefix}  SKIP {p['id']}: {reason}", file=sys.stderr)

        if not valid:
            print(f"{prefix}  Saturated (no valid proposals)", file=sys.stderr)
            break

        results = apply_proposals(valid, db_path=REASONS_DB)
        added = sum(1 for _, r in results if isinstance(r, dict))
        total_added += added
        print(f"{prefix}  Added {added} beliefs", file=sys.stderr)

    return total_added


def _stage_review(args, round_label=""):
    """Stage 5: Review derived beliefs for validity.

    Returns the review results dict.
    """
    from reasons_lib.api import review_beliefs

    prefix = f"[{round_label}] " if round_label else ""
    print(f"{prefix}Reviewing beliefs...", file=sys.stderr)

    result = review_beliefs(
        model=args.model,
        timeout=args.timeout,
        db_path=REASONS_DB,
    )

    reviewed = result.get("reviewed", 0)
    invalid = result.get("invalid", 0)
    print(f"{prefix}  Reviewed {reviewed}, invalid {invalid}", file=sys.stderr)

    return result


def _stage_repair(args, review_result, round_label=""):
    """Stage 6: Research and repair invalid beliefs.

    Returns the research results dict.
    """
    from reasons_lib.api import research

    prefix = f"[{round_label}] " if round_label else ""

    invalid_ids = [
        r["belief_id"] for r in review_result.get("results", [])
        if not r.get("valid", True)
    ]

    if not invalid_ids:
        print(f"{prefix}No invalid beliefs to repair", file=sys.stderr)
        return {"total_invalid": 0}

    print(f"{prefix}Researching {len(invalid_ids)} invalid beliefs...", file=sys.stderr)

    result = research(
        belief_ids=invalid_ids,
        model=args.model,
        timeout=args.timeout,
        db_path=REASONS_DB,
    )

    print(f"{prefix}  Linked: {result.get('linked', 0)}, "
          f"Softened: {result.get('softened', 0)}, "
          f"Abandoned: {result.get('abandoned', 0)}", file=sys.stderr)

    return result


def _stage_deduplicate(args, round_label=""):
    """Stage 7: Remove duplicate beliefs."""
    from reasons_lib.api import deduplicate

    prefix = f"[{round_label}] " if round_label else ""
    print(f"{prefix}Deduplicating...", file=sys.stderr)

    result = deduplicate(auto=True, db_path=REASONS_DB)
    retracted = result.get("retracted", [])
    clusters = result.get("clusters", [])

    if retracted:
        print(f"{prefix}  {len(clusters)} clusters, retracted {len(retracted)}",
              file=sys.stderr)
    else:
        print(f"{prefix}  No duplicates found", file=sys.stderr)

    return result


def _stage_export(args):
    """Export network and README card."""
    from reasons_lib.api import export_network, export_card

    data = export_network(db_path=REASONS_DB)

    network_path = Path("network.json")
    network_path.write_text(json.dumps(data, indent=2))
    print(f"Exported {network_path}", file=sys.stderr)

    card = export_card(db_path=REASONS_DB, domain=args.domain)
    readme_path = Path("README.md")
    readme_path.write_text(card)
    print(f"Exported {readme_path}", file=sys.stderr)

    in_count = sum(1 for n in data.get("nodes", {}).values()
                   if n.get("truth_value") == "IN")
    total = len(data.get("nodes", {}))
    print(f"\nFinal: {in_count} IN / {total} total beliefs", file=sys.stderr)


def cmd_pipeline(args):
    """Run end-to-end EEM construction pipeline."""
    from .caffeinate import hold as _caffeinate
    _caffeinate()

    if not check_model_available(args.model):
        print(f"Model not available: {args.model}", file=sys.stderr)
        sys.exit(1)

    resume = getattr(args, "resume", False)
    if resume:
        state = _load_state()
        if not state:
            print("No pipeline state to resume. Run without --resume first.",
                  file=sys.stderr)
            sys.exit(1)
        if state["status"] == "completed":
            print("Pipeline already completed. Run without --resume to start fresh.",
                  file=sys.stderr)
            return
        print(f"Resuming pipeline from state file", file=sys.stderr)
        state["status"] = "running"
        _save_state(state)
    else:
        state = _init_state(args)

    total_stages = 8
    has_sources = args.url or args.pdf

    try:
        # Stage 1: Ingest
        if not _stage_completed(state, 1):
            if has_sources:
                _banner(1, total_stages, "INGEST")
                _mark_stage(state, 1, "running")
                _stage_ingest(args)
                _mark_stage(state, 1, "completed")
            else:
                print("No --url or --pdf provided, skipping ingest", file=sys.stderr)
                _mark_stage(state, 1, "completed", skipped=True)
        else:
            print("Stage 1 (INGEST) already completed, skipping", file=sys.stderr)

        # Stage 2: Summarize
        if not _stage_completed(state, 2):
            _banner(2, total_stages, "SUMMARIZE")
            _mark_stage(state, 2, "running")
            _stage_summarize(args)
            _mark_stage(state, 2, "completed")
        else:
            print("Stage 2 (SUMMARIZE) already completed, skipping", file=sys.stderr)

        # Stage 3: Extract
        if not _stage_completed(state, 3):
            _banner(3, total_stages, "EXTRACT")
            _mark_stage(state, 3, "running")
            should_continue = _stage_extract(args)
            _mark_stage(state, 3, "completed")
            if not should_continue:
                state["status"] = "paused"
                _save_state(state)
                return
        else:
            print("Stage 3 (EXTRACT) already completed, skipping", file=sys.stderr)

        # Stages 4-7: Derive → Review → Repair → Deduplicate (convergence loop)
        start_cycle = state.get("current_cycle") or 1
        for cycle in range(start_cycle, args.rounds + 1):
            label = f"cycle {cycle}/{args.rounds}"
            state["current_cycle"] = cycle
            _save_state(state)

            _banner(4, total_stages, f"DERIVE ({label})")
            _mark_stage(state, 4, "running", cycle=cycle)
            added = _stage_derive(args, round_label=label)
            _mark_stage(state, 4, "completed", cycle=cycle, added=added)

            _banner(5, total_stages, f"REVIEW ({label})")
            _mark_stage(state, 5, "running", cycle=cycle)
            review_result = _stage_review(args, round_label=label)
            invalid_count = review_result.get("invalid", 0)
            _mark_stage(state, 5, "completed", cycle=cycle,
                        reviewed=review_result.get("reviewed", 0),
                        invalid=invalid_count)

            if invalid_count > 0:
                _banner(6, total_stages, f"REPAIR ({label})")
                _mark_stage(state, 6, "running", cycle=cycle)
                _stage_repair(args, review_result, round_label=label)
                _mark_stage(state, 6, "completed", cycle=cycle)
            else:
                _mark_stage(state, 6, "completed", cycle=cycle, skipped=True)

            _banner(7, total_stages, f"DEDUPLICATE ({label})")
            _mark_stage(state, 7, "running", cycle=cycle)
            _stage_deduplicate(args, round_label=label)
            _mark_stage(state, 7, "completed", cycle=cycle)

            if invalid_count == 0 and added == 0:
                print(f"\nConverged after {cycle} cycles "
                      f"(0 invalids, 0 new derivations)", file=sys.stderr)
                break

        # Stage 8: Export
        _banner(8, total_stages, "EXPORT")
        _mark_stage(state, 8, "running")
        _stage_export(args)
        _mark_stage(state, 8, "completed")

        state["status"] = "completed"
        _save_state(state)
        print("\nPipeline complete.", file=sys.stderr)

    except Exception as e:
        state["status"] = "failed"
        state["error"] = str(e)
        state["error_traceback"] = traceback.format_exc()
        _save_state(state)
        raise
