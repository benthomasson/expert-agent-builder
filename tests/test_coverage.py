"""Tests for expert_build.coverage — JSON cert-match parsing."""

import json
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from expert_build.coverage import cmd_cert_coverage


@pytest.fixture
def objectives_file(tmp_path):
    p = tmp_path / "objectives.md"
    p.write_text("# Storage\n- Configure local storage\n- Manage LVM volumes\n")
    return p


@pytest.fixture
def beliefs_db(tmp_path):
    db = tmp_path / "reasons.db"
    db.write_text("")
    return db


def make_args(objectives_file, beliefs_file, model="test"):
    return types.SimpleNamespace(
        objectives_file=str(objectives_file),
        beliefs_file=beliefs_file,
        model=model,
    )


FAKE_BELIEFS = [
    {"id": "local-storage-config", "text": "Local storage is configured via /etc/fstab"},
    {"id": "lvm-basics", "text": "LVM uses PVs, VGs, and LVs for volume management"},
    {"id": "unrelated-belief", "text": "SELinux enforces mandatory access control"},
]


def test_json_matching(objectives_file, beliefs_db, capsys):
    """LLM returns valid JSON with matching belief IDs."""
    args = make_args(objectives_file, beliefs_db)

    call_count = 0
    def invoke_side_effect(prompt, model=None, timeout=None):
        nonlocal call_count
        call_count += 1
        if "Configure local storage" in prompt:
            return json.dumps({"matching_ids": ["local-storage-config"]})
        return json.dumps({"matching_ids": ["lvm-basics"]})

    with patch("expert_build.coverage.check_model_available", return_value=True), \
         patch("expert_build.coverage.invoke_sync", side_effect=invoke_side_effect), \
         patch("expert_build.coverage.load_beliefs", return_value=FAKE_BELIEFS):
        cmd_cert_coverage(args)

    output = capsys.readouterr().out
    assert "local-storage-config" in output
    assert "lvm-basics" in output


def test_json_empty_matches(objectives_file, beliefs_db, capsys):
    """LLM returns empty matching_ids array — falls back to keyword."""
    args = make_args(objectives_file, beliefs_db)

    def invoke_side_effect(prompt, model=None, timeout=None):
        return json.dumps({"matching_ids": []})

    with patch("expert_build.coverage.check_model_available", return_value=True), \
         patch("expert_build.coverage.invoke_sync", side_effect=invoke_side_effect), \
         patch("expert_build.coverage.load_beliefs", return_value=FAKE_BELIEFS):
        cmd_cert_coverage(args)

    output = capsys.readouterr().out
    assert "GAPS" in output or "COVERED" in output


def test_json_retry_on_bad_response(objectives_file, beliefs_db, capsys):
    """When LLM returns non-JSON, retry and parse the retry response."""
    args = make_args(objectives_file, beliefs_db)

    call_count = 0
    def invoke_side_effect(prompt, model=None, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return "I think the matching beliefs are local-storage-config and lvm-basics"
        return json.dumps({"matching_ids": ["local-storage-config"]})

    with patch("expert_build.coverage.check_model_available", return_value=True), \
         patch("expert_build.coverage.invoke_sync", side_effect=invoke_side_effect), \
         patch("expert_build.coverage.load_beliefs", return_value=FAKE_BELIEFS):
        cmd_cert_coverage(args)

    output = capsys.readouterr().out
    assert call_count >= 3


def test_json_with_code_fence(objectives_file, beliefs_db, capsys):
    """LLM response wrapped in code fences is parsed correctly."""
    args = make_args(objectives_file, beliefs_db)

    def invoke_side_effect(prompt, model=None, timeout=None):
        return '```json\n{"matching_ids": ["local-storage-config"]}\n```'

    with patch("expert_build.coverage.check_model_available", return_value=True), \
         patch("expert_build.coverage.invoke_sync", side_effect=invoke_side_effect), \
         patch("expert_build.coverage.load_beliefs", return_value=FAKE_BELIEFS):
        cmd_cert_coverage(args)

    output = capsys.readouterr().out
    assert "local-storage-config" in output


def test_invalid_belief_ids_ignored(objectives_file, beliefs_db, capsys):
    """Belief IDs not in the known beliefs list are silently ignored."""
    args = make_args(objectives_file, beliefs_db)

    def invoke_side_effect(prompt, model=None, timeout=None):
        return json.dumps({"matching_ids": ["nonexistent-belief", "local-storage-config"]})

    with patch("expert_build.coverage.check_model_available", return_value=True), \
         patch("expert_build.coverage.invoke_sync", side_effect=invoke_side_effect), \
         patch("expert_build.coverage.load_beliefs", return_value=FAKE_BELIEFS):
        cmd_cert_coverage(args)

    output = capsys.readouterr().out
    assert "local-storage-config" in output
    assert "nonexistent-belief" not in output
