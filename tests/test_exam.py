"""Tests for expert_build.exam — JSON answer/verdict parsing and retry."""

from unittest.mock import patch

from expert_build.exam import extract_answer, judge_answer, _extract_json


# --- _extract_json ---

def test_extract_json_plain():
    assert _extract_json('{"answer": "b", "explanation": "because"}') == {
        "answer": "b", "explanation": "because"
    }


def test_extract_json_with_code_fence():
    response = '```json\n{"answer": "c", "explanation": "reason"}\n```'
    assert _extract_json(response) == {"answer": "c", "explanation": "reason"}


def test_extract_json_embedded_in_text():
    response = 'Here is my answer:\n{"answer": "a", "explanation": "yes"}\nDone.'
    result = _extract_json(response)
    assert result["answer"] == "a"


def test_extract_json_braces_in_value():
    response = 'Sure: {"answer": "b", "explanation": "use {braces} here"}'
    result = _extract_json(response)
    assert result["answer"] == "b"
    assert "{braces}" in result["explanation"]


def test_extract_json_invalid():
    assert _extract_json("No JSON here at all") is None


def test_extract_json_truncated():
    assert _extract_json('{"answer": "b", "explan') is None


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
