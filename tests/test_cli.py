"""Tests for expert_build.cli — registration validation."""

from unittest.mock import patch

import pytest

from expert_build.cli import main


def test_cli_registration_is_consistent():
    """Current subparsers and dispatch dict are in sync (no startup error)."""
    with patch("sys.argv", ["expert-build", "--help"]), \
         pytest.raises(SystemExit, match="0"):
        main()
