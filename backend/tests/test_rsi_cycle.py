"""Tests for Recursive Self-Improvement (RSI) Engine."""

import json
from copy import deepcopy
from dataclasses import asdict

import pytest

from alpha.rsi.engine import RSIEngine
from alpha.rsi.models import ABTestResult, HoldoutResult, RSIStage
from alpha.tools.builtins.rsi_engine_tool import run_rsi_cycle


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
    # The preview cannot measure, so it must report nulls. These previously asserted
    # `improved is True` / `regressed is False` against hardcoded 0.72/0.88 and 0.80/0.89
    # scores, which made a fabricated improvement the pinned contract.
    assert result.ab_test.improved is None
    assert result.ab_test.baseline_score is None
    assert result.ab_test.candidate_score is None
    assert result.ab_test.confidence is None
    assert result.ab_test.latency_delta_ms is None
    assert result.ab_test.not_measured_reason
    assert result.holdout is not None
    # "not run" must never read as "did not regress".
    assert result.holdout.regressed is None
    assert result.holdout.improved is None
    assert result.holdout.score is None
    assert result.holdout.baseline_score is None
    assert result.holdout.not_measured_reason
    assert result.ab_test.evidence_kind == "simulated"
    assert result.holdout.evidence_kind == "simulated"
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["evidence_kind"] == "simulated"
    assert payload["ab_test"] == asdict(result.ab_test)
    assert payload["holdout"] == asdict(result.holdout)
    assert not any("45/45" in item for item in result.holdout.evidence)
    assert any("Promotion blocked" in item for item in result.evidence)
    # No fabricated score may survive anywhere in the human/model-readable evidence.
    evidence_text = " ".join(result.evidence + result.holdout.evidence)
    for fabricated in ("0.88", "0.89", "0.72", "0.80", "0.92"):
        assert fabricated not in evidence_text
    assert any("NOT MEASURED" in item for item in result.evidence)
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


def test_rsi_tool_leads_with_the_measured_absence():
    """The model-facing payload must announce the absence of measurement first.

    The tool output is the only thing the model reliably reads. If `candidate`
    appeared before any disclosure, a model could quote the proposal as a result.
    """
    res_str = run_rsi_cycle.invoke({"bottleneck": "Context bloat", "target_component": "compaction"})
    data = json.loads(res_str)
    assert data["outcome"] == "PREVIEW_ONLY_NOT_MEASURED"
    assert "never" in data["what_this_is"].lower() or "not" in data["what_this_is"].lower()
    assert data["ab_test"]["candidate_score"] is None
    assert data["ab_test"]["improved"] is None
    assert data["holdout"]["regressed"] is None


def test_rsi_tool_docstring_makes_no_promotion_claim():
    """The tool schema is a model-facing surface: it must not promise promotion.

    It previously read 'benchmarks them in an A/B sandbox, and promotes them if
    holdout tests improve', describing a pipeline the tool has never run.
    """
    doc = (run_rsi_cycle.description or "").lower()
    assert "promotes them if" not in doc
    assert "promote." not in doc
    assert "never" in doc
    assert "preview" in doc


def test_unmeasured_evidence_cannot_satisfy_the_real_holdout_gate():
    """The preview's evidence must be rejected by the real gate, not merely labelled.

    Label honesty is not enough: the real `holdout_gate` is the enforcement point,
    so prove a preview result actually fails it instead of trusting the label.
    """
    from alpha.rsi.holdout import holdout_gate

    result = RSIEngine().run_rsi_cycle(bottleneck="Latency", target_component="tool_router")
    payload = result.to_dict()
    passed, reason = holdout_gate(payload)
    assert passed is False
    # Compare the prefix, not a transcribed literal: the reason text contains an
    # em dash that a hand-typed copy of it gets wrong.
    assert reason.startswith("holdout unverified")


def test_legacy_rsi_results_default_to_unknown_evidence():
    ab_test = ABTestResult("candidate", 0.7, 0.9, True, 0.9)
    holdout = HoldoutResult("candidate", True, False, 0.9, 0.8)
    assert ab_test.evidence_kind == "unknown"
    assert holdout.evidence_kind == "unknown"
    assert ABTestResult(**asdict(ab_test)) == ab_test
    assert HoldoutResult(**asdict(holdout)) == holdout


def test_measured_evidence_is_still_representable():
    """The honest nulls must not make a real measurement unrepresentable.

    Fixing the lie must not close the door on the measured path: a caller that
    genuinely benchmarked can still pass numbers, and those are what the gate wants.
    """
    from alpha.rsi.holdout import holdout_gate

    measured = {
        "evidence_kind": "measured",
        "passed": 12,
        "failed": 0,
        "regressed": [],
        "score": 0.91,
        "baseline_score": 0.74,
    }
    assert holdout_gate(measured) == (True, "holdout passed")
    # A measured run that failed anything is still a refusal.
    assert holdout_gate({**measured, "failed": 2}) == (False, "holdout regressions")
    # The dataclass still accepts a genuine measurement unchanged.
    ab_test = ABTestResult("candidate", 0.74, 0.91, True, 0.95, -12.0, "measured")
    assert ab_test.candidate_score == 0.91
    assert ab_test.improved is True
