"""Tests for expert_build.exam — JSON answer/verdict parsing and retry."""

from unittest.mock import patch

from expert_build.exam import extract_answer, judge_answer
from expert_build.llm import extract_json


# --- extract_json ---

def test_extract_json_plain():
    assert extract_json('{"answer": "b", "explanation": "because"}') == {
        "answer": "b", "explanation": "because"
    }


def test_extract_json_with_code_fence():
    response = '```json\n{"answer": "c", "explanation": "reason"}\n```'
    assert extract_json(response) == {"answer": "c", "explanation": "reason"}


def test_extract_json_embedded_in_text():
    response = 'Here is my answer:\n{"answer": "a", "explanation": "yes"}\nDone.'
    result = extract_json(response)
    assert result["answer"] == "a"


def test_extract_json_braces_in_value():
    response = 'Sure: {"answer": "b", "explanation": "use {braces} here"}'
    result = extract_json(response)
    assert result["answer"] == "b"
    assert "{braces}" in result["explanation"]


def test_extract_json_invalid():
    assert extract_json("No JSON here at all") is None


def test_extract_json_truncated():
    assert extract_json('{"answer": "b", "explan') is None


def test_extract_json_array():
    response = '[{"id": "a"}, {"id": "b"}]'
    result = extract_json(response)
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["id"] == "a"


def test_extract_json_array_in_text():
    response = 'Here are the beliefs:\n[{"id": "a", "claim": "test"}]\nDone.'
    result = extract_json(response)
    assert isinstance(result, list)
    assert result[0]["id"] == "a"


def test_extract_json_array_with_code_fence():
    response = '```json\n[{"id": "a"}]\n```'
    result = extract_json(response)
    assert isinstance(result, list)
    assert result[0]["id"] == "a"


# --- extract_answer ---

def test_extract_answer_from_json():
    response = '{"answer": "b", "explanation": "because"}'
    assert extract_answer(response) == "b"


def test_extract_answer_strips_whitespace():
    response = '{"answer": "  d  ", "explanation": "reason"}'
    assert extract_answer(response) == "d"


def test_extract_answer_retries_on_bad_json():
    bad_response = "I think the answer is B because of reasons"

    with patch("expert_build.exam.invoke_sync",
               return_value='{"answer": "b", "explanation": "reasons"}') as mock_llm:
        result = extract_answer(bad_response, model="test", prompt="original prompt")

    assert result == "b"
    assert mock_llm.called


def test_extract_answer_fallback_after_failed_retry():
    bad_response = "No format at all"

    with patch("expert_build.exam.invoke_sync", return_value="Still no format"):
        result = extract_answer(bad_response, model="test", prompt="original prompt")

    assert result == "No format at all"


def test_extract_answer_no_retry_without_model():
    result = extract_answer("No JSON here")
    assert result == "No JSON here"


def test_extract_answer_code_fence():
    response = '```json\n{"answer": "a", "explanation": "yes"}\n```'
    assert extract_answer(response) == "a"


# --- judge_answer ---

def test_judge_correct():
    with patch("expert_build.exam.invoke_sync",
               return_value='{"verdict": "CORRECT", "explanation": "matches"}'):
        is_correct, explanation = judge_answer("q", "expected", "got", "test")

    assert is_correct is True
    assert explanation == "matches"


def test_judge_wrong():
    with patch("expert_build.exam.invoke_sync",
               return_value='{"verdict": "WRONG", "explanation": "missed key point"}'):
        is_correct, explanation = judge_answer("q", "expected", "got", "test")

    assert is_correct is False
    assert explanation == "missed key point"


def test_judge_retries_on_bad_json():
    call_count = 0
    def side_effect(prompt, model=None, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "I think this is correct because it matches"
        return '{"verdict": "CORRECT", "explanation": "matches expected"}'

    with patch("expert_build.exam.invoke_sync", side_effect=side_effect):
        is_correct, explanation = judge_answer("q", "expected", "got", "test")

    assert is_correct is True
    assert call_count == 2


def test_judge_fallback_after_failed_retry():
    with patch("expert_build.exam.invoke_sync", return_value="No JSON at all"):
        is_correct, explanation = judge_answer("q", "expected", "got", "test")

    assert is_correct is False
    assert explanation == "no verdict"


def test_judge_handles_llm_error():
    with patch("expert_build.exam.invoke_sync",
               side_effect=RuntimeError("timeout")):
        is_correct, explanation = judge_answer("q", "expected", "got", "test")

    assert is_correct is False
    assert explanation == "judge error"


def test_judge_retry_itself_raises():
    call_count = 0
    def side_effect(prompt, model=None, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "Not JSON"
        raise RuntimeError("retry timeout")

    with patch("expert_build.exam.invoke_sync", side_effect=side_effect):
        is_correct, explanation = judge_answer("q", "expected", "got", "test")

    assert is_correct is False
    assert explanation == "no verdict"
    assert call_count == 2


def test_judge_case_insensitive_verdict():
    with patch("expert_build.exam.invoke_sync",
               return_value='{"verdict": "correct", "explanation": "ok"}'):
        is_correct, _ = judge_answer("q", "expected", "got", "test")

    assert is_correct is True


# --- MC answer normalization ---

from expert_build.exam import _normalize_mc_answer


def test_normalize_just_letter():
    assert _normalize_mc_answer("c") == "c"


def test_normalize_letter_paren():
    assert _normalize_mc_answer("c)") == "c"


def test_normalize_letter_with_text():
    assert _normalize_mc_answer("c) AlexNet") == "c"


def test_normalize_bold_letter():
    assert _normalize_mc_answer("**b**") == "b"


def test_normalize_answer_prefix():
    assert _normalize_mc_answer("Answer: b) something") == "b"


def test_normalize_parens():
    assert _normalize_mc_answer("(a)") == "a"


def test_normalize_non_mc_passthrough():
    assert _normalize_mc_answer("some long answer") == "some long answer"


# --- agentic mode ---

from expert_build.exam import _run_agentic_question, _execute_tool


def test_agentic_search_then_answer():
    """Model searches, gets results, then answers."""
    call_count = [0]

    def mock_invoke(prompt, model="claude", timeout=120):
        call_count[0] += 1
        if call_count[0] == 1:
            return '{"tool": "search_beliefs", "query": "sky color"}'
        return '{"answer": "b", "explanation": "found it"}'

    with patch("expert_build.exam.invoke_sync", side_effect=mock_invoke), \
         patch("expert_build.exam._execute_tool", return_value="- sky-is-blue: The sky is blue"):
        result = _run_agentic_question("What color is the sky?", "", "test", "reasons.db")

    assert result["answer"] == "b"
    assert result["tool_calls"] == 1


def test_agentic_max_turns_exceeded():
    """Stops after max_turns with empty answer."""
    def mock_invoke(prompt, model="claude", timeout=120):
        return '{"tool": "search_beliefs", "query": "more info"}'

    with patch("expert_build.exam.invoke_sync", side_effect=mock_invoke), \
         patch("expert_build.exam._execute_tool", return_value="some results"):
        result = _run_agentic_question("Q?", "", "test", "reasons.db", max_turns=3)

    assert result["answer"] == ""
    assert result["tool_calls"] == 3


def test_agentic_direct_answer():
    """Model answers without using tools."""
    with patch("expert_build.exam.invoke_sync",
               return_value='{"answer": "c", "explanation": "I know this"}'):
        result = _run_agentic_question("Q?", "", "test", "reasons.db")

    assert result["answer"] == "c"
    assert result["tool_calls"] == 0


def test_execute_tool_unknown():
    result = _execute_tool({"tool": "nonexistent"}, "reasons.db")
    assert "Unknown tool" in result


def test_execute_tool_search():
    with patch("reasons_lib.api.search", return_value="- belief-1: A fact"):
        result = _execute_tool({"tool": "search_beliefs", "query": "test"}, "reasons.db")
    assert "belief-1" in result


def test_execute_tool_show():
    node = {"id": "b1", "truth_value": "IN", "text": "A fact"}
    with patch("reasons_lib.api.show_node", return_value=node):
        result = _execute_tool({"tool": "show_belief", "id": "b1"}, "reasons.db")
    assert "[IN] b1" in result


def test_execute_tool_show_not_found():
    with patch("reasons_lib.api.show_node", side_effect=KeyError("not found")):
        result = _execute_tool({"tool": "show_belief", "id": "missing"}, "reasons.db")
    assert "not found" in result
