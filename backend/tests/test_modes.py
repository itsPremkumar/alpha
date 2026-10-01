"""Tests for working modes (alpha.modes)."""

from __future__ import annotations

import sys
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness"
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

import pytest  # noqa: E402

from alpha.modes import (  # noqa: E402
    ModeViolation,
    UnknownModeError,
    WorkMode,
    assert_allowed,
    can_escalate,
    capabilities_for,
    is_allowed,
    parse_mode,
)


def test_parse_mode_aliases():
    assert parse_mode("ask") == WorkMode.ASK
    assert parse_mode("Chat") == WorkMode.ASK
    assert parse_mode("PLAN") == WorkMode.PLAN
    assert parse_mode("agent") == WorkMode.CRAFT
    assert parse_mode("coding") == WorkMode.CODING
    assert parse_mode(WorkMode.PLAN) == WorkMode.PLAN


def test_unknown_mode_raises():
    with pytest.raises(UnknownModeError):
        parse_mode("turbo")


def test_ask_is_read_only():
    caps = capabilities_for("ask")
    assert caps.can_read_files is True
    assert caps.can_write_files is False
    assert caps.can_run_commands is False
    assert caps.produces_artifacts is False


def test_craft_can_execute():
    caps = capabilities_for("craft")
    assert caps.can_write_files and caps.can_run_commands and caps.produces_artifacts


def test_assert_allowed_enforces_mode():
    assert_allowed("craft", "write_file")  # no raise
    with pytest.raises(ModeViolation):
        assert_allowed("ask", "write_file")
    with pytest.raises(ModeViolation):
        assert_allowed("plan", "run_command")
    assert is_allowed("plan", "delegate") is True
    assert is_allowed("ask", "delegate") is False


def test_can_escalate():
    assert can_escalate("ask", "plan") is True
    assert can_escalate("craft", "ask") is False
    assert can_escalate("plan", "plan") is False


def test_unknown_action_raises():
    with pytest.raises(Exception):
        assert_allowed("craft", "fly_to_moon")
