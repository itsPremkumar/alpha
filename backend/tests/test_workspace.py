"""Tests for the unified workspace facade (alpha.workspace)."""

from __future__ import annotations

import sys
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness"
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

import pytest  # noqa: E402

from alpha.automations import Automation, AutomationSchedule, Frequency  # noqa: E402
from alpha.modes import ModeViolation  # noqa: E402
from alpha.workspace import AgentWorkspace  # noqa: E402


def test_workspace_composes_every_module(tmp_path):
    ws = AgentWorkspace(tmp_path / "ws")
    # Catalogs are wired.
    assert ws.connectors.get("gmail") is not None
    assert ws.experts.get_expert("researcher") is not None
    assert ws.skills_market.get("web-search") is not None
    # Stores are wired and empty.
    assert ws.status()["connectors_enabled"] == 0


def test_mode_enforcement(tmp_path):
    ws = AgentWorkspace(tmp_path / "ws", mode="ask")
    assert ws.mode.name == "ASK"
    assert ws.can("write_file") is False
    with pytest.raises(ModeViolation):
        ws.assert_action("write_file")
    ws.set_mode("craft")
    assert ws.can("write_file") is True
    ws.assert_action("write_file")  # no raise


def test_install_across_modules_updates_status(tmp_path):
    ws = AgentWorkspace(tmp_path / "ws")
    ws.install_connector("gmail")
    ws.install_expert("researcher")
    ws.install_expert_group("content-creation")
    ws.install_skill("excel-processing")
    ws.add_automation(Automation("daily", "Daily", "brief", AutomationSchedule(Frequency.DAILY, at="08:00")))
    st = ws.status()
    assert st["connectors_enabled"] == 1
    assert st["experts_enabled"] == 2
    assert st["skills_enabled"] == 1
    assert st["automations"] == 1


def test_snapshot_shape(tmp_path):
    ws = AgentWorkspace(tmp_path / "ws")
    ws.install_connector("gmail")
    snap = ws.snapshot()
    assert set(snap) >= {"status", "mode_capabilities", "connectors", "experts", "skills", "automations", "routines", "egress"}
    assert snap["status"]["connectors_enabled"] == 1


def test_persistence_across_workspace_reopen(tmp_path):
    root = tmp_path / "ws"
    ws = AgentWorkspace(root)
    ws.install_connector("gmail")
    ws.install_skill("web-search")
    ws.set_mode("plan")

    ws2 = AgentWorkspace(root)  # reopen
    assert ws2.connector_store.get("gmail") is not None
    assert ws2.skill_registry.get("web-search") is not None
    # mode is a session property, not persisted
    assert ws2.status()["skills_enabled"] == 1


def test_capability_report_runs(tmp_path):
    ws = AgentWorkspace(tmp_path / "ws")
    report = ws.capability_report()
    assert report.total > 50
    assert 0.0 <= report.coverage <= 1.0
