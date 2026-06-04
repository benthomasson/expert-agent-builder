"""Tests for expert_build.exam — answer/verdict parsing and retry."""

from unittest.mock import patch

import pytest

from expert_build.exam import extract_answer, judge_answer, _parse_answer, _parse_verdict


# --- _parse_answer ---

def test_parse_answer_with_answer_line():
    assert _parse_answer("ANSWER: b\nEXPLANATION: because") == "b"


def test_parse_answer_with_letter_format():
    assert _parse_answer("ANSWER: b) Option B\nEXPLANATION: reason") == "b"


def test_parse_answer_bare_letter():
    assert _parse_answer("Some reasoning\nc\n") == "c"


def test_parse_answer_no_match():
    assert _parse_answer("Here is my long explanation without a format") is None


def test_parse_answer_case_insensitive():
    assert _parse_answer("answer: D\nExplanation: stuff") == "D"


# --- extract_answer with retry ---

def test_extract_answer_succeeds_without_retry():
    result = extract_answer("ANSWER: a\nEXPLANATION: yes")
    assert result == "a"


def test_extract_answer_retries_on_bad_format():
    bad_response = "I think the answer is probably B because of reasons"

    with patch("expert_build.exam.invoke_sync",
               return_value="ANSWER: b\nEXPLANATION: reasons") as mock_llm:
        result = extract_answer(bad_response, model="test", prompt="original prompt")

    assert result == "b"
    assert mock_llm.called


def test_extract_answer_fallback_after_failed_retry():
    bad_response = "No format at all"

    with patch("expert_build.exam.invoke_sync",
               return_value="Still no format"):
        result = extract_answer(bad_response, model="test", prompt="original prompt")

    assert result == "No format at all"


def test_extract_answer_no_retry_without_model():
    result = extract_answer("No format here")
    assert result == "No format here"


# --- _parse_verdict ---

def test_parse_verdict_correct():
    result = _parse_verdict("VERDICT: CORRECT\nEXPLANATION: good answer")
    assert result == (True, "good answer")


def test_parse_verdict_wrong():
    result = _parse_verdict("VERDICT: WRONG\nEXPLANATION: missed the point")
    assert result == (False, "missed the point")


def test_parse_verdict_no_match():
    assert _parse_verdict("The answer seems right to me") is None


def test_parse_verdict_no_explanation():
    result = _parse_verdict("VERDICT: CORRECT")
    assert result == (True, "")


# --- judge_answer with retry ---

def test_judge_answer_succeeds_without_retry():
    with patch("expert_build.exam.invoke_sync",
               return_value="VERDICT: CORRECT\nEXPLANATION: matches"):
        is_correct, explanation = judge_answer("q", "expected", "got", "test")

    assert is_correct is True
    assert explanation == "matches"


def test_judge_answer_retries_on_bad_format():
    call_count = 0
    def side_effect(prompt, model=None, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "I think this is correct because it matches"
        return "VERDICT: CORRECT\nEXPLANATION: matches expected"

    with patch("expert_build.exam.invoke_sync", side_effect=side_effect):
        is_correct, explanation = judge_answer("q", "expected", "got", "test")

    assert is_correct is True
    assert call_count == 2


def test_judge_answer_fallback_after_failed_retry():
    with patch("expert_build.exam.invoke_sync",
               return_value="No format at all"):
        is_correct, explanation = judge_answer("q", "expected", "got", "test")

    assert is_correct is False
    assert explanation == "no verdict"


def test_judge_answer_handles_llm_error():
    with patch("expert_build.exam.invoke_sync",
               side_effect=RuntimeError("timeout")):
        is_correct, explanation = judge_answer("q", "expected", "got", "test")

    assert is_correct is False
    assert explanation == "judge error"
