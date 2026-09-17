"""Tests for Recursive Self-Improvement (RSI) Engine."""

import json
from copy import deepcopy
from dataclasses import asdict

import pytest

from agent_workspace.rsi.engine import RSIEngine
from agent_workspace.rsi.models import ABTestResult, HoldoutResult, RSIStage
from agent_workspace.tools.builtins.rsi_engine_tool import run_rsi_cycle


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("component", ["compaction", "tool_router", "context_pruner", "unknown"])
def test_rsi_engine_preview_cycle(force, component):
    engine = RSIEngine()
    original = deepcopy(engine.active_configurations)
    result = engine.run_rsi_cycle(
        bottleneck="Excessive token bloat due to uncompacted directory listings",
        target_component=component,
        force_promote=force,
    )
    assert engine.active_configurations == original
    assert result.promoted is False
    assert result.stage == RSIStage.PREVIEW
    assert result.evidence_kind == "simulated"
    assert result.ab_test is not None
    assert result.ab_test.improved is True
    assert result.holdout is not None
    assert result.holdout.regressed is False
    assert result.ab_test.evidence_kind == "simulated"
    assert result.holdout.evidence_kind == "simulated"
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["evidence_kind"] == "simulated"
    assert payload["ab_test"] == asdict(result.ab_test)
    assert payload["holdout"] == asdict(result.holdout)
    assert not any("45/45" in item for item in result.holdout.evidence)
    assert any("Promotion blocked" in item for item in result.evidence)
    assert engine.get_status()["stage"] == "preview"


def test_rsi_engine_does_not_claim_rollback_for_preview():
    engine = RSIEngine()
    result = engine.run_rsi_cycle(
        bottleneck="Minor timeout fluctuation",
        target_component="tool_router",
        force_promote=False,
    )
    assert result.stage == RSIStage.PREVIEW
    assert result.promoted is False
    assert not any("Rollback executed" in item for item in result.evidence)


def test_run_rsi_cycle_tool():
    res_str = run_rsi_cycle.invoke(
        {
            "bottleneck": "Tool invocation retry rate exceeded 15% on file writes",
            "target_component": "tool_router",
        }
    )
    data = json.loads(res_str)
    assert "promoted" in data
    assert "stage" in data
    assert "hypothesis" in data
    assert "candidate" in data
    assert "ab_test" in data
    assert data["promoted"] is False
    assert data["stage"] == "preview"
    assert data["evidence_kind"] == "simulated"


def test_legacy_rsi_results_default_to_unknown_evidence():
    ab_test = ABTestResult("candidate", 0.7, 0.9, True, 0.9)
    holdout = HoldoutResult("candidate", True, False, 0.9, 0.8)
    assert ab_test.evidence_kind == "unknown"
    assert holdout.evidence_kind == "unknown"
    assert ABTestResult(**asdict(ab_test)) == ab_test
    assert HoldoutResult(**asdict(holdout)) == holdout
