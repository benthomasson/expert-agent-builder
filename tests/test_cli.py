"""Tests for expert_build.cli — registration validation."""

from unittest.mock import patch

import pytest

from expert_build.cli import main


def test_cli_registration_is_consistent():
    """Current subparsers and dispatch dict are in sync."""
    with patch("sys.argv", ["expert-build", "status"]), \
         patch("expert_build.cli._lazy") as mock_lazy:
        mock_lazy.return_value = lambda a: None
        main()
