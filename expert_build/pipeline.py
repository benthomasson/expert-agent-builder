"""End-to-end EEM construction pipeline."""

import re
import sys
from pathlib import Path
from types import SimpleNamespace

from .llm import check_model_available, invoke_sync
from .propose import REASONS_DB


def _banner(stage_num, total, name):
    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  Stage {stage_num}/{total}: {name}", file=sys.stderr)
    print(f"{'=' * 50}\n", file=sys.stderr)


def _stage_ingest(args):
    """Stage 1: Fetch docs or chunk PDFs into sources/."""
    if args.no_fetch:
        print("Skipping fetch (--no-fetch)", file=sys.stderr)
        return

    if args.url:
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
    """Stage 9: Export network and README card."""
    from reasons_lib.api import export_network, export_card

    data = export_network(db_path=REASONS_DB)

    network_path = Path("network.json")
    import json
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

    total_stages = 9
    has_sources = args.url or args.pdf

    # Stage 1: Ingest
    if has_sources:
        _banner(1, total_stages, "INGEST")
        _stage_ingest(args)
    else:
        print("No --url or --pdf provided, skipping ingest", file=sys.stderr)

    # Stage 2: Summarize
    _banner(2, total_stages, "SUMMARIZE")
    _stage_summarize(args)

    # Stage 3: Extract
    _banner(3, total_stages, "EXTRACT")
    should_continue = _stage_extract(args)
    if not should_continue:
        return

    # Stages 4-7: Derive → Review → Repair → Deduplicate (convergence loop)
    for cycle in range(1, args.rounds + 1):
        label = f"cycle {cycle}/{args.rounds}"

        # Stage 4: Derive
        _banner(4, total_stages, f"DERIVE ({label})")
        added = _stage_derive(args, round_label=label)

        # Stage 5: Review
        _banner(5, total_stages, f"REVIEW ({label})")
        review_result = _stage_review(args, round_label=label)

        invalid_count = review_result.get("invalid", 0)

        # Stage 6: Repair
        if invalid_count > 0:
            _banner(6, total_stages, f"REPAIR ({label})")
            _stage_repair(args, review_result, round_label=label)

        # Stage 7: Deduplicate
        _banner(7, total_stages, f"DEDUPLICATE ({label})")
        _stage_deduplicate(args, round_label=label)

        # Check convergence
        if invalid_count == 0 and added == 0:
            print(f"\nConverged after {cycle} cycles "
                  f"(0 invalids, 0 new derivations)", file=sys.stderr)
            break

    # Stage 9: Export
    _banner(9, total_stages, "EXPORT")
    _stage_export(args)

    print("\nPipeline complete.", file=sys.stderr)
