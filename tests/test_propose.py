"""Tests for expert_build.propose — incremental batch writing."""

import json
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from expert_build.propose import cmd_propose_beliefs


@pytest.fixture
def entries_dir(tmp_path):
    d = tmp_path / "entries"
    d.mkdir()
    return d


@pytest.fixture
def work_dir(tmp_path, monkeypatch):
    wd = tmp_path / "work"
    wd.mkdir()
    (wd / ".expert-build").mkdir()
    monkeypatch.chdir(wd)
    return wd


def make_args(input_dir, output="proposed-beliefs.md", batch_size=2, model="test"):
    return types.SimpleNamespace(
        input_dir=str(input_dir),
        output=output,
        batch_size=batch_size,
        model=model,
        all=False,
    )


def test_proposals_written_after_each_batch(entries_dir, work_dir):
    """Proposals from completed batches survive a crash in a later batch."""
    for i in range(4):
        (entries_dir / f"entry{i}.md").write_text(f"# Entry {i}\nContent {i}")

    output = work_dir / "proposed-beliefs.md"
    args = make_args(entries_dir, output=str(output), batch_size=2)

    call_count = 0
    def invoke_side_effect(prompt, model=None, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated crash")
        return f"### [ACCEPT/REJECT] belief-from-batch-{call_count}\nA belief.\n"

    with patch("expert_build.propose.check_model_available", return_value=True), \
         patch("expert_build.propose.invoke_sync", side_effect=invoke_side_effect), \
         patch("expert_build.propose._load_existing_beliefs", return_value=[]), \
         patch("expert_build.propose._has_embeddings", return_value=False):
        cmd_propose_beliefs(args)

    content = output.read_text()
    assert "belief-from-batch-1" in content
    assert "belief-from-batch-2" not in content


def test_all_batches_written_on_success(entries_dir, work_dir):
    for i in range(4):
        (entries_dir / f"entry{i}.md").write_text(f"# Entry {i}\nContent {i}")

    output = work_dir / "proposed-beliefs.md"
    args = make_args(entries_dir, output=str(output), batch_size=2)

    call_count = 0
    def invoke_side_effect(prompt, model=None, timeout=None):
        nonlocal call_count
        call_count += 1
        return f"### [ACCEPT/REJECT] belief-{call_count}\nA belief.\n"

    with patch("expert_build.propose.check_model_available", return_value=True), \
         patch("expert_build.propose.invoke_sync", side_effect=invoke_side_effect), \
         patch("expert_build.propose._load_existing_beliefs", return_value=[]), \
         patch("expert_build.propose._has_embeddings", return_value=False):
        cmd_propose_beliefs(args)

    content = output.read_text()
    assert "belief-1" in content
    assert "belief-2" in content


def test_existing_beliefs_filtered_per_batch(entries_dir, work_dir):
    (entries_dir / "entry0.md").write_text("# Entry\nContent")

    output = work_dir / "proposed-beliefs.md"
    args = make_args(entries_dir, output=str(output), batch_size=5)

    existing = [{"id": "already-exists", "text": "old belief", "source": ""}]

    def invoke_side_effect(prompt, model=None, timeout=None):
        return (
            "### [ACCEPT] already-exists\nDuplicate.\n\n"
            "### [ACCEPT] new-belief\nFresh.\n"
        )

    with patch("expert_build.propose.check_model_available", return_value=True), \
         patch("expert_build.propose.invoke_sync", side_effect=invoke_side_effect), \
         patch("expert_build.propose._load_existing_beliefs", return_value=existing), \
         patch("expert_build.propose._has_embeddings", return_value=False):
        cmd_propose_beliefs(args)

    content = output.read_text()
    assert "new-belief" in content
    assert "already-exists" not in content


def test_failed_batch_entries_not_marked_processed(entries_dir, work_dir):
    """Entries from failed batches are not marked as processed."""
    for i in range(4):
        (entries_dir / f"entry{i}.md").write_text(f"# Entry {i}\nContent {i}")

    output = work_dir / "proposed-beliefs.md"
    args = make_args(entries_dir, output=str(output), batch_size=2)

    call_count = 0
    def invoke_side_effect(prompt, model=None, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated crash")
        return f"### [ACCEPT/REJECT] belief-{call_count}\nA belief.\n"

    with patch("expert_build.propose.check_model_available", return_value=True), \
         patch("expert_build.propose.invoke_sync", side_effect=invoke_side_effect), \
         patch("expert_build.propose._load_existing_beliefs", return_value=[]), \
         patch("expert_build.propose._has_embeddings", return_value=False):
        cmd_propose_beliefs(args)

    processed_path = work_dir / ".expert-build" / "proposed-entries.json"
    processed = json.loads(processed_path.read_text())
    # Batch 1 (entry0, entry1) succeeded — should be marked processed
    assert any("entry0" in k for k in processed)
    assert any("entry1" in k for k in processed)
    # Batch 2 (entry2, entry3) failed — should NOT be marked processed
    assert not any("entry2" in k for k in processed)
    assert not any("entry3" in k for k in processed)


def test_filter_regex_matches_accept_reject_placeholder(entries_dir, work_dir):
    """The dedup filter handles [ACCEPT/REJECT] placeholder from LLM output."""
    (entries_dir / "entry0.md").write_text("# Entry\nContent")

    output = work_dir / "proposed-beliefs.md"
    args = make_args(entries_dir, output=str(output), batch_size=5)

    existing = [{"id": "old-belief", "text": "existing", "source": ""}]

    def invoke_side_effect(prompt, model=None, timeout=None):
        return (
            "### [ACCEPT/REJECT] old-belief\nDuplicate.\n\n"
            "### [ACCEPT/REJECT] new-belief\nFresh.\n"
        )

    with patch("expert_build.propose.check_model_available", return_value=True), \
         patch("expert_build.propose.invoke_sync", side_effect=invoke_side_effect), \
         patch("expert_build.propose._load_existing_beliefs", return_value=existing), \
         patch("expert_build.propose._has_embeddings", return_value=False):
        cmd_propose_beliefs(args)

    content = output.read_text()
    assert "new-belief" in content
    assert "old-belief" not in content


def test_appends_to_existing_output_file(entries_dir, work_dir):
    """When output file already exists, new proposals are appended."""
    (entries_dir / "entry0.md").write_text("# Entry\nContent")

    output = work_dir / "proposed-beliefs.md"
    output.write_text("# Proposed Beliefs\n\n### [ACCEPT] prior-belief\nOld.\n\n")
    args = make_args(entries_dir, output=str(output), batch_size=5)

    def invoke_side_effect(prompt, model=None, timeout=None):
        return "### [ACCEPT/REJECT] new-belief\nFresh.\n"

    with patch("expert_build.propose.check_model_available", return_value=True), \
         patch("expert_build.propose.invoke_sync", side_effect=invoke_side_effect), \
         patch("expert_build.propose._load_existing_beliefs", return_value=[]), \
         patch("expert_build.propose._has_embeddings", return_value=False):
        cmd_propose_beliefs(args)

    content = output.read_text()
    assert "prior-belief" in content
    assert "new-belief" in content
