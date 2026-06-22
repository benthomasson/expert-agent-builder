"""Tests for expert_build.exam_matrix — exam matrix command."""

import types
from pathlib import Path
from unittest.mock import patch

import pytest

from expert_build.exam_matrix import cmd_exam_matrix, write_matrix_summary


@pytest.fixture
def work_dir(tmp_path, monkeypatch):
    wd = tmp_path / "work"
    wd.mkdir()
    monkeypatch.chdir(wd)
    return wd


@pytest.fixture
def questions_file(work_dir):
    q = work_dir / "questions.md"
    q.write_text(
        "## Q1: What color is the sky?\n"
        "- a) Red\n"
        "- b) Blue\n"
        "- c) Green\n"
        "Answer: b\n"
        "Objective: Colors\n"
        "\n"
        "## Q2: What is 2+2?\n"
        "Answer: 4\n"
        "Objective: Math\n"
    )
    return q


def make_args(questions_file, **overrides):
    defaults = dict(
        questions_file=str(questions_file),
        models="model-a,model-b",
        output_dir="results",
        beliefs_file=Path("reasons.db"),
        limit=None,
        no_judge=True,
        timeout=120,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def mock_invoke(prompt, model="claude", timeout=120):
    """Return correct answers for both questions."""
    if "What color" in prompt:
        return '{"answer": "b", "explanation": "sky is blue"}'
    if "2+2" in prompt:
        return '{"answer": "4", "explanation": "basic math"}'
    return '{"answer": "unknown"}'


def mock_invoke_wrong(prompt, model="claude", timeout=120):
    """Return wrong answers for both questions."""
    if "What color" in prompt:
        return '{"answer": "a", "explanation": "wrong"}'
    if "2+2" in prompt:
        return '{"answer": "5", "explanation": "wrong"}'
    return '{"answer": "unknown"}'


class TestExamMatrix:
    def test_runs_all_combinations(self, work_dir, questions_file):
        args = make_args(questions_file)
        call_models = []

        def tracking_invoke(prompt, model="claude", timeout=120):
            call_models.append(model)
            return mock_invoke(prompt, model, timeout)

        with patch("expert_build.exam.invoke_sync", side_effect=tracking_invoke), \
             patch("expert_build.exam_matrix.check_model_available", return_value=True), \
             patch("expert_build.exam_matrix.load_beliefs_for_context", return_value="- b1: test"):
            cmd_exam_matrix(args)

        # 2 models × 2 conditions × 2 questions = 8 calls
        assert len(call_models) == 8
        assert call_models.count("model-a") == 4
        assert call_models.count("model-b") == 4

    def test_writes_per_run_files(self, work_dir, questions_file):
        args = make_args(questions_file)

        with patch("expert_build.exam.invoke_sync", side_effect=mock_invoke), \
             patch("expert_build.exam_matrix.check_model_available", return_value=True), \
             patch("expert_build.exam_matrix.load_beliefs_for_context", return_value="- b1: test"):
            cmd_exam_matrix(args)

        results_dir = work_dir / "results"
        assert (results_dir / "model-a-beliefs.md").exists()
        assert (results_dir / "model-a-control.md").exists()
        assert (results_dir / "model-b-beliefs.md").exists()
        assert (results_dir / "model-b-control.md").exists()

    def test_writes_summary(self, work_dir, questions_file):
        args = make_args(questions_file)

        with patch("expert_build.exam.invoke_sync", side_effect=mock_invoke), \
             patch("expert_build.exam_matrix.check_model_available", return_value=True), \
             patch("expert_build.exam_matrix.load_beliefs_for_context", return_value="- b1: test"):
            cmd_exam_matrix(args)

        summary = (work_dir / "results" / "matrix-summary.md").read_text()
        assert "# Exam Matrix" in summary
        assert "Score Matrix" in summary
        assert "model-a" in summary
        assert "model-b" in summary

    def test_custom_models(self, work_dir, questions_file):
        args = make_args(questions_file, models="custom-model")
        call_models = []

        def tracking_invoke(prompt, model="claude", timeout=120):
            call_models.append(model)
            return mock_invoke(prompt, model, timeout)

        with patch("expert_build.exam.invoke_sync", side_effect=tracking_invoke), \
             patch("expert_build.exam_matrix.check_model_available", return_value=True), \
             patch("expert_build.exam_matrix.load_beliefs_for_context", return_value="- b1: test"):
            cmd_exam_matrix(args)

        assert all(m == "custom-model" for m in call_models)

    def test_delta_computation(self, work_dir, questions_file):
        """Beliefs run gets all correct, control gets all wrong -> positive delta."""
        args = make_args(questions_file, models="test-model")
        call_count = [0]

        def alternating_invoke(prompt, model="claude", timeout=120):
            call_count[0] += 1
            # First 2 calls = beliefs (correct), next 2 = control (wrong)
            if call_count[0] <= 2:
                return mock_invoke(prompt, model, timeout)
            return mock_invoke_wrong(prompt, model, timeout)

        with patch("expert_build.exam.invoke_sync", side_effect=alternating_invoke), \
             patch("expert_build.exam_matrix.check_model_available", return_value=True), \
             patch("expert_build.exam_matrix.load_beliefs_for_context", return_value="- b1: test"):
            cmd_exam_matrix(args)

        summary = (work_dir / "results" / "matrix-summary.md").read_text()
        assert "+100%" in summary


class TestWriteMatrixSummary:
    def test_includes_belief_count(self, tmp_path):
        all_results = {
            ("m1", "beliefs"): {"correct": 1, "total": 2, "obj_scores": {}},
            ("m1", "control"): {"correct": 0, "total": 2, "obj_scores": {}},
        }
        out = tmp_path / "summary.md"
        write_matrix_summary(all_results, ["m1"], out, Path("q.md"), belief_count=42)
        text = out.read_text()
        assert "42 IN" in text

    def test_objective_table(self, tmp_path):
        obj = {"Topic A": {"correct": 1, "total": 1}}
        all_results = {
            ("m1", "beliefs"): {"correct": 1, "total": 1, "obj_scores": dict(obj)},
            ("m1", "control"): {"correct": 0, "total": 1, "obj_scores": {"Topic A": {"correct": 0, "total": 1}}},
        }
        out = tmp_path / "summary.md"
        write_matrix_summary(all_results, ["m1"], out, Path("q.md"))
        text = out.read_text()
        assert "Topic A" in text
        assert "By Objective" in text
