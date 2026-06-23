"""Run exam across a matrix of models and belief conditions."""

import sys
from datetime import datetime
from pathlib import Path

from .exam import (
    load_beliefs_for_context,
    parse_questions,
    run_exam,
    write_exam_results,
)
from .llm import check_model_available

REASONS_DB = "reasons.db"
DEFAULT_MODELS = ["claude:opus", "claude:sonnet", "claude:haiku"]


def write_matrix_summary(
    all_results, models, output_path, questions_file, belief_count=None,
):
    """Write comparison table across all model × condition runs."""
    total = next(iter(all_results.values()))["total"]

    lines = [
        f"# Exam Matrix: {questions_file.name}",
        "",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Questions:** {total}",
    ]
    if belief_count is not None:
        lines.append(f"**Beliefs:** {belief_count} IN")
    lines.extend(["", "## Score Matrix", ""])

    has_agentic = any(k[1] == "agentic" for k in all_results)

    header = "| Model | With Beliefs | Control | Delta |"
    sep = "|-------|-------------|---------|-------|"
    if has_agentic:
        header = "| Model | With Beliefs | Control | Agentic | Delta |"
        sep = "|-------|-------------|---------|---------|-------|"
    lines.append(header)
    lines.append(sep)

    for model in models:
        b = all_results.get((model, "beliefs"))
        c = all_results.get((model, "control"))
        if not b or not c:
            continue
        b_pct = 100 * b["correct"] // b["total"] if b["total"] else 0
        c_pct = 100 * c["correct"] // c["total"] if c["total"] else 0
        delta = b_pct - c_pct
        sign = "+" if delta >= 0 else ""
        row = (
            f"| {model} "
            f"| {b['correct']}/{b['total']} ({b_pct}%) "
            f"| {c['correct']}/{c['total']} ({c_pct}%) "
        )
        if has_agentic:
            a = all_results.get((model, "agentic"))
            if a:
                a_pct = 100 * a["correct"] // a["total"] if a["total"] else 0
                row += f"| {a['correct']}/{a['total']} ({a_pct}%) "
            else:
                row += "| — "
        row += f"| {sign}{delta}% |"
        lines.append(row)

    all_objectives = {}
    for result in all_results.values():
        for obj, scores in result["obj_scores"].items():
            if obj not in all_objectives:
                all_objectives[obj] = scores["total"]

    if all_objectives:
        lines.extend(["", "## By Objective", ""])

        headers = ["Objective"]
        obj_conditions = ["beliefs", "control"]
        suffixes = ["+b", "-c"]
        if has_agentic:
            obj_conditions.append("agentic")
            suffixes.append("+a")
        for model in models:
            short = model.split(":")[-1] if ":" in model else model
            headers.extend([f"{short}{s}" for s in suffixes])
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

        for obj in sorted(all_objectives.keys()):
            row = [obj]
            for model in models:
                for condition in obj_conditions:
                    r = all_results.get((model, condition))
                    if r and obj in r["obj_scores"]:
                        s = r["obj_scores"][obj]
                        row.append(f"{s['correct']}/{s['total']}")
                    else:
                        row.append("—")
            lines.append("| " + " | ".join(row) + " |")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n")


def cmd_exam_matrix(args):
    """Run exam across models × beliefs/control and produce comparison."""
    q_path = Path(args.questions_file)
    if not q_path.exists():
        print(f"Questions file not found: {q_path}")
        sys.exit(1)

    models = [m.strip() for m in args.models.split(",")]
    for model in models:
        if not check_model_available(model):
            print(f"Model not available: {model}")
            sys.exit(1)

    questions = parse_questions(q_path)
    if not questions:
        print(f"No questions found in {q_path}")
        sys.exit(1)

    if args.limit:
        questions = questions[:args.limit]

    db_path = str(args.beliefs_file)
    max_depth = getattr(args, "max_depth", None)
    beliefs_context = load_beliefs_for_context(db_path=db_path, max_depth=max_depth)

    belief_count = None
    try:
        from reasons_lib.api import list_nodes
        result = list_nodes(status="IN", max_depth=max_depth, db_path=db_path)
        belief_count = len(result.get("nodes", []))
    except Exception:
        pass

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    no_judge = getattr(args, "no_judge", False)
    timeout = getattr(args, "timeout", 120)
    include_agentic = getattr(args, "agentic", False)
    max_turns = getattr(args, "max_turns", 5)

    # (name, beliefs_context, is_agentic, is_control)
    conditions = [
        ("beliefs", beliefs_context, False, False),
        ("control", "", False, True),
    ]
    if include_agentic:
        conditions.append(("agentic", "", True, False))

    all_results = {}
    total_runs = len(models) * len(conditions)

    print(f"=== Exam Matrix: {q_path.name} ===")
    print(f"Models: {', '.join(models)}")
    print(f"Conditions: {', '.join(c[0] for c in conditions)}")
    print(f"Questions: {len(questions)}")
    print(f"Runs: {total_runs}\n")

    run_num = 0
    for model in models:
        for condition, context, is_agentic, is_control in conditions:
            run_num += 1
            label = f"[{run_num}/{total_runs}] {model} ({condition})"
            print(f"\n{'=' * 50}")
            print(f"  {label}")
            print(f"{'=' * 50}\n")

            result = run_exam(
                questions, context, model,
                no_judge=no_judge, timeout=timeout,
                agentic=is_agentic, control=is_control,
                db_path=db_path, max_turns=max_turns,
            )
            all_results[(model, condition)] = result

            slug = model.replace(":", "-")
            run_path = output_dir / f"{slug}-{condition}.md"
            write_exam_results(result, run_path, model, q_path)

            pct = 100 * result["correct"] // result["total"] if result["total"] else 0
            print(f"\n  Score: {result['correct']}/{result['total']} ({pct}%)")
            print(f"  Saved: {run_path}")

    summary_path = output_dir / "matrix-summary.md"
    write_matrix_summary(all_results, models, summary_path, q_path, belief_count)

    print(f"\n{'=' * 50}")
    print(f"  MATRIX SUMMARY")
    print(f"{'=' * 50}\n")

    for model in models:
        b = all_results.get((model, "beliefs"))
        c = all_results.get((model, "control"))
        if not b or not c:
            continue
        b_pct = 100 * b["correct"] // b["total"] if b["total"] else 0
        c_pct = 100 * c["correct"] // c["total"] if c["total"] else 0
        delta = b_pct - c_pct
        sign = "+" if delta >= 0 else ""
        line = f"  {model:20s}  beliefs: {b_pct:3d}%  control: {c_pct:3d}%  delta: {sign}{delta}%"
        a = all_results.get((model, "agentic"))
        if a:
            a_pct = 100 * a["correct"] // a["total"] if a["total"] else 0
            line += f"  agentic: {a_pct:3d}%"
        print(line)

    print(f"\nSummary saved to {summary_path}")
