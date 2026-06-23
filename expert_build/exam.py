"""Practice exam runner for nogood discovery."""

import re
import sys
from pathlib import Path

from reasons_lib.api import add_node, add_nogood, list_nodes

from .llm import check_model_available, extract_json, invoke_sync, RETRY_JSON
from .prompts import EXAM_ANSWER, EXAM_ANSWER_AGENTIC, EXAM_ANSWER_CONTROL, EXAM_JUDGE

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


def load_beliefs_for_context(db_path: str = REASONS_DB,
                             max_depth: int | None = None) -> str:
    """Load IN beliefs from reasons database and format as context string."""
    if not Path(db_path).exists():
        return "(No reasons database found)"

    try:
        result = list_nodes(status="IN", max_depth=max_depth, db_path=db_path)
        beliefs = [f"- {n['id']}: {n['text']}" for n in result["nodes"]]
    except Exception:
        return "(Error reading reasons database)"

    if not beliefs:
        return "(No IN beliefs found)"

    depth_note = f" (depth 0-{max_depth})" if max_depth is not None else ""
    return f"({len(beliefs)} beliefs{depth_note})\n" + "\n".join(beliefs)


def _normalize_mc_answer(answer: str) -> str:
    """Normalize a multiple-choice answer to just the letter.

    Handles: "c", "c)", "c) AlexNet", "**c**", "Answer: b) text", "(a)", etc.
    """
    text = answer.strip().lower()
    text = text.lstrip("*").rstrip("*").strip()
    if text.startswith("answer:"):
        text = text[len("answer:"):].strip()
    text = text.lstrip("*").rstrip("*").strip()
    text = text.lstrip("(").rstrip(")")
    if text and text[0] in "abcd" and (len(text) == 1 or text[1] in ").) "):
        return text[0]
    return answer.strip()


def extract_answer(response: str, model: str = None, prompt: str = None) -> str:
    """Extract answer from JSON LLM response, retrying on parse failure."""
    data = extract_json(response)
    if data and "answer" in data:
        return _normalize_mc_answer(str(data["answer"]))

    if model and prompt:
        print("    WARN: response not valid JSON, retrying...", file=sys.stderr)
        try:
            retry_response = invoke_sync(
                prompt + "\n\n" + response + "\n\n" + RETRY_JSON,
                model=model, timeout=60,
            )
            data = extract_json(retry_response)
            if data and "answer" in data:
                return str(data["answer"]).strip()
        except Exception:
            pass

    print("    WARN: could not parse answer JSON", file=sys.stderr)
    return response.strip()[:100]


def judge_answer(question: str, expected: str, got: str, model: str) -> tuple[bool, str]:
    """Use LLM to judge if an open-ended answer is semantically correct."""
    prompt = EXAM_JUDGE.format(question=question, expected=expected, got=got)
    try:
        response = invoke_sync(prompt, model=model, timeout=60)
    except Exception:
        return False, "judge error"

    data = extract_json(response)
    if data and "verdict" in data:
        is_correct = str(data["verdict"]).strip().upper() == "CORRECT"
        return is_correct, str(data.get("explanation", "")).strip()

    print("    WARN: verdict not valid JSON, retrying...", file=sys.stderr)
    try:
        retry_response = invoke_sync(
            prompt + "\n\n" + response + "\n\n" + RETRY_JSON,
            model=model, timeout=60,
        )
        data = extract_json(retry_response)
        if data and "verdict" in data:
            is_correct = data["verdict"].strip().upper() == "CORRECT"
            return is_correct, data.get("explanation", "")
    except Exception:
        pass

    print("    WARN: could not parse verdict JSON", file=sys.stderr)
    return False, "no verdict"


def _execute_tool(call, db_path):
    """Execute a pseudo-tool call against the reasons API."""
    from reasons_lib.api import search, show_node

    tool = call.get("tool")
    if tool == "search_beliefs":
        query = call.get("query", "")
        if not query:
            return "Error: query is required"
        return search(query=query, db_path=db_path, format="minimal")
    elif tool == "show_belief":
        node_id = call.get("id", "")
        if not node_id:
            return "Error: id is required"
        try:
            node = show_node(node_id, db_path=db_path)
            return f"[{node['truth_value']}] {node['id']}: {node['text']}"
        except KeyError:
            return f"Belief '{node_id}' not found"
    return f"Unknown tool: {tool}"


def _run_agentic_question(question, choices_text, model, db_path,
                          max_turns=5, timeout=120):
    """Run one question with pseudo-tool-calling loop.

    Returns: {"answer": str, "explanation": str, "tool_calls": int}
    """
    prompt = EXAM_ANSWER_AGENTIC.format(question=question, choices=choices_text)
    tool_calls = 0

    for turn in range(max_turns):
        response = invoke_sync(prompt, model=model, timeout=timeout)
        parsed = extract_json(response)

        if parsed and "answer" in parsed:
            parsed["tool_calls"] = tool_calls
            return parsed

        if parsed and "tool" in parsed:
            tool_calls += 1
            tool_result = _execute_tool(parsed, db_path)
            print(f"    tool: {parsed['tool']}({parsed.get('query', parsed.get('id', ''))})",
                  file=sys.stderr)
            prompt = (
                f"{prompt}\n\n"
                f"Assistant: {response}\n\n"
                f"Tool result:\n{tool_result}\n\n"
                f"Continue. Search more or provide your final answer as JSON."
            )
        else:
            return {"answer": response.strip()[:100], "explanation": "",
                    "tool_calls": tool_calls}

    return {"answer": "", "explanation": "max turns reached",
            "tool_calls": tool_calls}


def run_exam(questions, beliefs_context, model, no_judge=False, timeout=120,
             agentic=False, control=False, db_path="reasons.db", max_turns=5):
    """Run exam questions and return results.

    Returns: {"correct": int, "total": int, "results": [...],
              "wrong": [...], "obj_scores": {...}}
    """
    correct = 0
    wrong = []
    results = []

    for q in questions:
        choices_text = ""
        if q["choices"]:
            choices_text = "\n".join(f"  {k}) {v}" for k, v in sorted(q["choices"].items()))

        if agentic:
            try:
                agentic_result = _run_agentic_question(
                    q["text"], choices_text, model, db_path,
                    max_turns=max_turns, timeout=timeout,
                )
                answer = _normalize_mc_answer(str(agentic_result.get("answer", "")))
                response = agentic_result.get("explanation", "")
            except Exception as e:
                print(f"  {q['id']}: ERROR - {e}")
                results.append({"question": q, "status": "ERROR", "error": str(e)})
                continue
        else:
            if control:
                prompt = EXAM_ANSWER_CONTROL.format(
                    question=q["text"],
                    choices=choices_text,
                )
            else:
                prompt = EXAM_ANSWER.format(
                    beliefs=beliefs_context,
                    question=q["text"],
                    choices=choices_text,
                )

            try:
                response = invoke_sync(prompt, model=model, timeout=timeout)
            except Exception as e:
                print(f"  {q['id']}: ERROR - {e}")
                results.append({"question": q, "status": "ERROR", "error": str(e)})
                continue

            answer = extract_answer(response, model=model, prompt=prompt)
        expected = q["correct"].strip().lower()

        if q["choices"] or len(expected) == 1:
            is_correct = answer.lower() == expected
            judge_note = ""
        elif not no_judge:
            is_correct, judge_note = judge_answer(
                q["text"], q["correct"], response, model,
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

    total = len(questions)
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

    return {
        "correct": correct,
        "total": total,
        "results": results,
        "wrong": wrong,
        "obj_scores": obj_scores,
    }


def write_exam_results(run_result, output_path, model, questions_file):
    """Write per-run exam results to a markdown file."""
    _write_results(
        output_path, questions_file, model,
        [r["question"] for r in run_result["results"]],
        run_result["results"], run_result["wrong"],
        run_result["obj_scores"], run_result["correct"], run_result["total"],
    )


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

    is_agentic = getattr(args, "agentic", False)
    is_control = getattr(args, "no_beliefs", False)
    db_path = str(args.beliefs_file)

    max_depth = getattr(args, "max_depth", None)

    if is_agentic:
        beliefs_context = ""
    elif is_control:
        beliefs_context = ""
    else:
        beliefs_context = load_beliefs_for_context(db_path=db_path, max_depth=max_depth)

    mode = "agentic" if is_agentic else ("control" if is_control else "one-shot")
    print(f"=== Exam: {q_path.name} ===")
    print(f"Questions: {len(questions)}")
    print(f"Model: {args.model}")
    print(f"Mode: {mode}\n")

    result = run_exam(
        questions, beliefs_context, args.model,
        no_judge=getattr(args, "no_judge", False),
        agentic=is_agentic, control=is_control, db_path=db_path,
        max_turns=getattr(args, "max_turns", 5),
    )

    correct = result["correct"]
    total = result["total"]
    wrong = result["wrong"]
    obj_scores = result["obj_scores"]

    pct = 100 * correct // total if total else 0
    print(f"\n=== Results ===")
    print(f"Score: {correct}/{total} ({pct}%)")

    if wrong:
        db_path = str(args.beliefs_file) if not getattr(args, "no_beliefs", False) else REASONS_DB
        print(f"\nWRONG ANSWERS ({len(wrong)}):\n")
        for w in wrong:
            q = w["question"]
            print(f"  {q['id']}: {q['text']}")
            print(f"    Expected: {w['expected']}")
            print(f"    Got: {w['got']}")
            if q["objective"]:
                print(f"    Objective: {q['objective']}")

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

    output_path = getattr(args, "output", None)
    if output_path:
        write_exam_results(result, output_path, args.model, q_path)
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
