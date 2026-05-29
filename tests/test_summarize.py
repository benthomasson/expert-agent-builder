"""Tests for expert_build.summarize."""

import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from expert_build.summarize import cmd_summarize
from expert_build.prompts import SUMMARIZE, SUMMARIZE_CODE


# --- Fixtures ---

@pytest.fixture
def source_dir(tmp_path):
    """Create a temp directory with sample source files."""
    src = tmp_path / "sources"
    src.mkdir()
    return src


@pytest.fixture
def work_dir(tmp_path, monkeypatch):
    """Set working directory to tmp_path so .summarized manifest is isolated."""
    wd = tmp_path / "work"
    wd.mkdir()
    monkeypatch.chdir(wd)
    return wd


def make_args(input_dir, model="test-model", limit=None):
    return types.SimpleNamespace(input_dir=str(input_dir), model=model, limit=limit)


# --- File discovery tests ---

def test_discovers_md_files(source_dir, work_dir):
    (source_dir / "doc.md").write_text("# Hello\nSome content")
    args = make_args(source_dir)

    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync", return_value="## Topic Title\nSummary"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Created entries/doc.md", stderr="")
        cmd_summarize(args)

    assert mock_run.called


def test_discovers_py_files(source_dir, work_dir):
    (source_dir / "module.py").write_text("def hello(): pass")
    args = make_args(source_dir)

    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync", return_value="## Module\nSummary") as mock_llm, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Created entries/module.md", stderr="")
        cmd_summarize(args)

    assert mock_llm.called


def test_discovers_both_md_and_py(source_dir, work_dir):
    (source_dir / "alpha.md").write_text("# Alpha\nContent")
    (source_dir / "beta.py").write_text("x = 1")
    args = make_args(source_dir)

    calls = []
    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync", return_value="## Title\nSummary") as mock_llm, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Created entries/x.md", stderr="")
        cmd_summarize(args)

    assert mock_llm.call_count == 2


def test_ignores_other_extensions(source_dir, work_dir):
    (source_dir / "data.json").write_text("{}")
    (source_dir / "notes.txt").write_text("hello")
    args = make_args(source_dir)

    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync") as mock_llm:
        cmd_summarize(args)

    assert not mock_llm.called


# --- Template selection tests ---

def test_uses_summarize_code_for_py(source_dir, work_dir):
    (source_dir / "module.py").write_text("def hello(): pass")
    args = make_args(source_dir)

    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync", return_value="## Module\nSummary") as mock_llm, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Created entries/module.md", stderr="")
        cmd_summarize(args)

    prompt = mock_llm.call_args[0][0]
    assert "source code" in prompt.lower()


def test_uses_summarize_for_md(source_dir, work_dir):
    (source_dir / "doc.md").write_text("# Hello\nSome content")
    args = make_args(source_dir)

    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync", return_value="## Doc Title\nSummary") as mock_llm, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Created entries/doc.md", stderr="")
        cmd_summarize(args)

    prompt = mock_llm.call_args[0][0]
    assert "documentation page" in prompt.lower()


# --- Truncation tests ---

def test_truncation_warning_for_large_file(source_dir, work_dir, capsys):
    (source_dir / "big.md").write_text("x" * 50000)
    args = make_args(source_dir)

    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync", return_value="## Big Doc\nSummary") as mock_llm, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Created entries/big.md", stderr="")
        cmd_summarize(args)

    captured = capsys.readouterr()
    assert "WARN: truncated from 50000 to 30000 chars" in captured.out
    assert "Large documents may lose tail content" in captured.out


def test_truncation_content_is_capped(source_dir, work_dir):
    (source_dir / "big.md").write_text("x" * 50000)
    args = make_args(source_dir)

    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync", return_value="## Big\nSummary") as mock_llm, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Created entries/big.md", stderr="")
        cmd_summarize(args)

    prompt = mock_llm.call_args[0][0]
    assert "[Truncated" in prompt
    assert len(prompt) < 50000


def test_no_truncation_warning_for_small_file(source_dir, work_dir, capsys):
    (source_dir / "small.md").write_text("Short content")
    args = make_args(source_dir)

    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync", return_value="## Small\nSummary") as mock_llm, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Created entries/small.md", stderr="")
        cmd_summarize(args)

    captured = capsys.readouterr()
    assert "WARN" not in captured.out


# --- Manifest / idempotency tests ---

def test_skips_already_summarized(source_dir, work_dir):
    (source_dir / "doc.md").write_text("# Hello\nContent")
    manifest = work_dir / ".summarized"
    manifest.write_text(f"{source_dir / 'doc.md'}\n")
    args = make_args(source_dir)

    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync") as mock_llm:
        cmd_summarize(args)

    assert not mock_llm.called


def test_manifest_records_processed_file(source_dir, work_dir):
    (source_dir / "doc.md").write_text("# Hello\nContent")
    args = make_args(source_dir)

    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync", return_value="## Title\nSummary"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Created entries/doc.md", stderr="")
        cmd_summarize(args)

    manifest = work_dir / ".summarized"
    assert manifest.exists()
    assert str(source_dir / "doc.md") in manifest.read_text()


# --- Frontmatter stripping tests ---

def test_strips_frontmatter_before_summarizing(source_dir, work_dir):
    content = "---\nsource_url: https://example.com\n---\n\nActual content here"
    (source_dir / "doc.md").write_text(content)
    args = make_args(source_dir)

    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync", return_value="## Title\nSummary") as mock_llm, \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Created entries/doc.md", stderr="")
        cmd_summarize(args)

    prompt = mock_llm.call_args[0][0]
    assert "source_url" not in prompt
    assert "Actual content here" in prompt


def test_skips_empty_content_after_frontmatter(source_dir, work_dir, capsys):
    (source_dir / "empty.md").write_text("---\nsource_url: https://example.com\n---\n\n")
    args = make_args(source_dir)

    with patch("expert_build.summarize.check_model_available", return_value=True), \
         patch("expert_build.summarize.invoke_sync") as mock_llm:
        cmd_summarize(args)

    assert not mock_llm.called
    captured = capsys.readouterr()
    assert "SKIP" in captured.out


# --- Prompt template tests ---

def test_summarize_template_requests_descriptive_title():
    assert "<Descriptive Title>" in SUMMARIZE


def test_summarize_code_template_requests_descriptive_title():
    assert "<Descriptive Title>" in SUMMARIZE_CODE


def test_summarize_template_has_content_placeholder():
    assert "{content}" in SUMMARIZE


def test_summarize_code_template_has_content_placeholder():
    assert "{content}" in SUMMARIZE_CODE
