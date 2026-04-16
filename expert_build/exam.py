"""Practice exam runner for nogood discovery."""

import re
import sys
from pathlib import Path

from reasons_lib.api import add_node, add_nogood, list_nodes

from .llm import check_model_available, invoke_sync
from .prompts import EXAM_ANSWER, EXAM_JUDGE

REASONS_DB = "reasons.db"


def parse_questions(filepath: Path) -> list[dict]:
    """Parse practice questions from markdown.

    Expected format:
        ## Q1: Question text here
        - a) Choice A
        - b) Choice B
        - c) Choice C
        - d) Choice D
        Answer: b
        Objective: Domain Name

    Or simple format:
        ## Q1: Question text here
        Answer: The correct answer text
    """
    text = filepath.read_text()
    questions = []

    # Split by ## headings
    sections = re.split(r"^## ", text, flags=re.MULTILINE)

    for section in sections:
        section = section.strip()
        if not section:
            continue

        lines = section.split("\n")
        first_line = lines[0].strip()

        # Parse Q-number and question text
        q_match = re.match(r"Q?(\d+):?\s*(.+)", first_line)
        if not q_match:
            continue

        q_num = q_match.group(1)
        q_text = q_match.group(2).strip()

        question = {
            "id": f"Q{q_num}",
            "text": q_text,
            "choices": {},
            "correct": None,
            "objective": None,
        }

        for line in lines[1:]:
            line = line.strip()

            # Parse choices: - a) text or - a. text
            choice_match = re.match(r"[-*]\s*([a-d])[.)]\s*(.+)", line)
            if choice_match:
                question["choices"][choice_match.group(1)] = choice_match.group(2).strip()
                continue

            # Parse answer
            ans_match = re.match(r"Answer:\s*(.+)", line, re.IGNORECASE)
            if ans_match:
                question["correct"] = ans_match.group(1).strip()
                continue

            # Parse objective
            obj_match = re.match(r"Objective:\s*(.+)", line, re.IGNORECASE)
            if obj_match:
                question["objective"] = obj_match.group(1).strip()
                continue

        if question["correct"]:
            questions.append(question)

    return questions


def load_beliefs_for_context(db_path: str = REASONS_DB) -> str:
    """Load IN beliefs from reasons database and format as context string."""
    if not Path(db_path).exists():
        return "(No reasons database found)"

    try:
        result = list_nodes(status="IN", db_path=db_path)
        beliefs = [f"- {n['id']}: {n['text']}" for n in result["nodes"]]
    except Exception:
        return "(Error reading reasons database)"

    if not beliefs:
        return "(No IN beliefs found)"

    return "\n".join(beliefs)


def extract_answer(response: str) -> str:
    """Extract the answer from LLM response."""
    # Look for ANSWER: line
    match = re.search(r"ANSWER:\s*(.+)", response, re.IGNORECASE)
    if match:
        ans = match.group(1).strip()
        # If it's a letter choice, extract just the letter
        letter_match = re.match(r"([a-d])[.):\s]", ans, re.IGNORECASE)
        if letter_match:
            return letter_match.group(1).lower()
        return ans

    # Fallback: look for a single letter on its own
    lines = response.strip().split("\n")
    for line in lines:
        line = line.strip()
        if re.match(r"^[a-d]$", line, re.IGNORECASE):
            return line.lower()

    return response.strip()[:100]


def judge_answer(question: str, expected: str, got: str, model: str) -> tuple[bool, str]:
    """Use LLM to judge if an open-ended answer is semantically correct."""
    prompt = EXAM_JUDGE.format(question=question, expected=expected, got=got)
    try:
        response = invoke_sync(prompt, model=model, timeout=60)
    except Exception:
        return False, "judge error"

    verdict_match = re.search(r"VERDICT:\s*(CORRECT|WRONG)", response, re.IGNORECASE)
    if not verdict_match:
        return False, "no verdict"

    is_correct = verdict_match.group(1).upper() == "CORRECT"
    explanation = ""
    exp_match = re.search(r"EXPLANATION:\s*(.+)", response, re.IGNORECASE)
    if exp_match:
        explanation = exp_match.group(1).strip()
    return is_correct, explanation


def cmd_exam(args):
    """Run practice questions through LLM, discover nogoods."""
    q_path = Path(args.questions_file)
    if not q_path.exists():
        print(f"Questions file not found: {q_path}")
        sys.exit(1)

    if not check_model_available(args.model):
        print(f"Model not available: {args.model}")
        sys.exit(1)

    questions = parse_questions(q_path)
    if not questions:
        print(f"No questions found in {q_path}")
        print("Expected format: ## Q1: Question text\\nAnswer: correct answer")
        return

    if args.limit:
        questions = questions[:args.limit]

    db_path = str(args.beliefs_file)
    beliefs_context = load_beliefs_for_context(db_path=db_path)

    print(f"=== Exam: {q_path.name} ===")
    print(f"Questions: {len(questions)}")
    print(f"Model: {args.model}\n")

    correct = 0
    wrong = []
    results = []  # Per-question results for output file

    for q in questions:
        # Format choices
        choices_text = ""
        if q["choices"]:
            choices_text = "\n".join(f"  {k}) {v}" for k, v in sorted(q["choices"].items()))

        prompt = EXAM_ANSWER.format(
            beliefs=beliefs_context,
            question=q["text"],
            choices=choices_text,
        )

        try:
            response = invoke_sync(prompt, model=args.model, timeout=120)
        except Exception as e:
            print(f"  {q['id']}: ERROR - {e}")
            results.append({"question": q, "status": "ERROR", "error": str(e)})
            continue

        answer = extract_answer(response)
        expected = q["correct"].strip().lower()
        use_judge = not getattr(args, 'no_judge', False)

        # Score: MC uses exact match, open-ended uses LLM judge
        if q["choices"] or len(expected) == 1:
            is_correct = answer.lower() == expected
            judge_note = ""
        elif use_judge:
            is_correct, judge_note = judge_answer(
                q["text"], q["correct"], response, args.model,
            )
        else:
            is_correct = expected in answer.lower() or answer.lower() in expected
            judge_note = ""

        if is_correct:
            correct += 1
            print(f"  {q['id']}: CORRECT")
            results.append({"question": q, "status": "CORRECT", "got": answer, "response": response, "judge": judge_note})
        else:
            wrong.append({
                "question": q,
                "got": answer,
                "expected": q["correct"],
                "response": response,
                "judge": judge_note,
            })
            results.append({"question": q, "status": "WRONG", "got": answer, "expected": q["correct"], "response": response, "judge": judge_note})
            print(f"  {q['id']}: WRONG (expected: {q['correct']}, got: {answer})")
            if judge_note:
                print(f"    Judge: {judge_note}")

    # Summary
    total = len(questions)
    pct = 100 * correct // total if total else 0
    print(f"\n=== Results ===")
    print(f"Score: {correct}/{total} ({pct}%)")

    # Gaps by objective
    obj_scores: dict[str, dict] = {}
    for q in questions:
        obj = q.get("objective", "general")
        if obj not in obj_scores:
            obj_scores[obj] = {"correct": 0, "total": 0}
        obj_scores[obj]["total"] += 1

    for q in questions:
        obj = q.get("objective", "general")
        if not any(w["question"]["id"] == q["id"] for w in wrong):
            obj_scores[obj]["correct"] += 1

    if wrong:
        print(f"\nWRONG ANSWERS ({len(wrong)}):\n")
        for w in wrong:
            q = w["question"]
            print(f"  {q['id']}: {q['text']}")
            print(f"    Expected: {w['expected']}")
            print(f"    Got: {w['got']}")
            if q["objective"]:
                print(f"    Objective: {q['objective']}")

            # Record exam failure as a node for tracking
            nogood_id = f"exam-fail-{q['id'].lower()}"
            description = f"Exam {q['id']}: expected '{w['expected']}' but agent answered '{w['got']}' for: {q['text']}"
            resolution = f"Review and update beliefs about: {q['objective'] or q['text']}"
            try:
                add_node(
                    node_id=nogood_id,
                    text=f"{description} — {resolution}",
                    source=str(q_path),
                    db_path=db_path,
                )
                print(f"    -> Recorded as nogood")
            except Exception:
                print(f"    -> WARN: could not record nogood")

        print(f"\nBY OBJECTIVE:")
        for obj, scores in sorted(obj_scores.items(), key=lambda x: x[1]["correct"] / max(x[1]["total"], 1)):
            pct = 100 * scores["correct"] // scores["total"] if scores["total"] else 0
            weak = " *** WEAK AREA" if pct < 50 else ""
            print(f"  {obj}: {scores['correct']}/{scores['total']} ({pct}%){weak}")

    # Write output file if requested
    output_path = getattr(args, "output", None)
    if output_path:
        _write_results(output_path, q_path, args.model, questions, results, wrong, obj_scores, correct, total)
        print(f"\nResults saved to {output_path}")


def _write_results(
    output_path: Path,
    q_path: Path,
    model: str,
    questions: list[dict],
    results: list[dict],
    wrong: list[dict],
    obj_scores: dict[str, dict],
    correct: int,
    total: int,
) -> None:
    """Write exam results to a markdown file."""
    from datetime import datetime

    pct = 100 * correct // total if total else 0
    lines = [
        f"# Exam Results: {q_path.name}",
        "",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Model:** {model}",
        f"**Score:** {correct}/{total} ({pct}%)",
        "",
        "## Per-Question Results",
        "",
    ]

    for r in results:
        q = r["question"]
        status = r["status"]
        if status == "CORRECT":
            lines.append(f"- **{q['id']}**: CORRECT — {q['text']}")
        elif status == "WRONG":
            lines.append(f"- **{q['id']}**: WRONG — {q['text']}")
            lines.append(f"  - Expected: {r['expected']}")
            lines.append(f"  - Got: {r['got']}")
        else:
            lines.append(f"- **{q['id']}**: ERROR — {q['text']}")
            lines.append(f"  - {r.get('error', 'unknown error')}")

    if wrong:
        lines.extend(["", "## Wrong Answers (Detail)", ""])
        for w in wrong:
            q = w["question"]
            lines.append(f"### {q['id']}: {q['text']}")
            if q["choices"]:
                for k, v in sorted(q["choices"].items()):
                    marker = " **<--**" if k == q["correct"].strip().lower() else ""
                    lines.append(f"- {k}) {v}{marker}")
            lines.append(f"- **Expected:** {w['expected']}")
            lines.append(f"- **Got:** {w['got']}")
            if q["objective"]:
                lines.append(f"- **Objective:** {q['objective']}")
            lines.extend(["", "**Model response:**", "", "```", w["response"].strip(), "```", ""])

    lines.extend(["", "## By Objective", ""])
    for obj, scores in sorted(obj_scores.items(), key=lambda x: x[1]["correct"] / max(x[1]["total"], 1)):
        obj_pct = 100 * scores["correct"] // scores["total"] if scores["total"] else 0
        weak = " **WEAK AREA**" if obj_pct < 50 else ""
        lines.append(f"- **{obj}**: {scores['correct']}/{scores['total']} ({obj_pct}%){weak}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n")
