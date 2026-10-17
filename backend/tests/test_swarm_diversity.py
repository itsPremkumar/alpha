"""Panel decorrelation (S4): composition refusal and the measured copy-cat guard.

Anthropic's red-team measurements document swarms where 18/30 agents chose the
same branch name, several agents independently titled a story with the same
sentence, and over half picked the same two projects.  Low-variance voters turn
an isolated error into a systemic one: a panel of clones agrees with itself for
reasons that have nothing to do with being right.  This suite pins the response:

- a panel whose voters share one declared ``(model, framing)`` tuple is refused
  **before registration**, with the colliding axes named, and nothing is
  persisted;
- a *partial* collision (same model, distinct framings) is disclosed as
  advisory, never refused — the shipped decomposer always varies framings, and
  refusing the partial case would refuse every healthy ensemble;
- measurement is honest: fewer than two outputs is ``None`` (neither
  "identical" nor "perfectly diverse"), and the suspicious-unanimity guard can
  only ever downgrade an approval to ``manual_review`` — never raise a refusal
  to an approval, never fire on an unmeasured panel.
"""

from __future__ import annotations

from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.decomposer import SwarmTaskDecomposer
from alpha.swarm.diversity import (
    COMPOSITION_BASIS,
    DECORRELATION_BASIS,
    HIGH_SIMILARITY_THRESHOLD,
    UNSUPPORTED_AXES,
    composition_audit,
    panel_decorrelation,
    panel_voters,
    suspicious_unanimity,
)
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.swarm.scheduler import SwarmPlanValidationError

COPY_CAT_TEXT = "The migration is safe to land and I recommend proceeding now"
VARIED_TEXT = "Rebuild the ingestion path around a bounded event journal first"
APPROVE_EVIDENCE = [
    {"kind": "consensus_vote", "voter": "v1", "stance": "approve", "evidence": ["receipt:1"], "reason": "checked"},
    {"kind": "consensus_vote", "voter": "v2", "stance": "approve", "evidence": ["receipt:2"], "reason": "checked"},
]


def _vote(index: int) -> list[dict[str, object]]:
    """One approval from a distinct voter; duplicate voters are made unusable."""

    return [{"kind": "consensus_vote", "voter": f"v{index}", "stance": "approve", "evidence": [f"receipt:{index}"], "reason": "checked"}]


def _task(task_id: str, objective: str, *, deps: tuple[str, ...] = (), model: str | None = None, tags: tuple[str, ...] = ()) -> SwarmTaskNode:
    return SwarmTaskNode(task_id=task_id, objective=objective, dependencies=list(deps), model_override=model, capability_tags=list(tags))


def _panel_plan(*, identical: bool, plan_mode: SwarmMode = SwarmMode.ENSEMBLE) -> SwarmPlan:
    def objective(index: int) -> str:
        return COPY_CAT_TEXT if identical else f"{COPY_CAT_TEXT} (approach {index}: distinct trade-offs)"

    tasks = {f"sol-{index}": _task(f"sol-{index}", objective(index)) for index in range(3)}
    tasks["synth"] = _task("synth", "Weigh the proposals", deps=("sol-0", "sol-1", "sol-2"))
    return SwarmPlan(swarm_id="panel", goal="panel goal", mode=plan_mode, tasks=tasks)


def _stub_panel_decomposer(*, identical: bool):
    """A deterministic ENSEMBLE plan: no model calls, no real decomposition."""

    def decompose(**_kwargs: object) -> SwarmPlan:
        return _panel_plan(identical=identical)

    return decompose


def _completed(task: SwarmTaskNode, *, summary: str, evidence: list[dict[str, object]] | None = None) -> SwarmTaskNode:
    task.state = TaskNodeState.COMPLETED
    task.result_summary = summary
    task.evidence = list(evidence or [])
    return task


# ---------------------------------------------------------------------------
# panel_decorrelation: honest measurement
# ---------------------------------------------------------------------------


def test_identical_outputs_score_zero_decorrelation() -> None:
    measured = panel_decorrelation([COPY_CAT_TEXT, COPY_CAT_TEXT])
    assert measured is not None
    assert measured["basis"] == DECORRELATION_BASIS
    assert measured["voters"] == 2
    assert measured["pairs"] == 1
    assert measured["similarity"] == pytest.approx(1.0)
    assert measured["decorrelation"] == pytest.approx(0.0)
    assert measured["identical_pairs"] == 1


def test_distinct_outputs_score_positive_decorrelation() -> None:
    measured = panel_decorrelation([COPY_CAT_TEXT, VARIED_TEXT])
    assert measured is not None
    assert measured["similarity"] < HIGH_SIMILARITY_THRESHOLD
    assert measured["decorrelation"] > 0.5
    assert measured["identical_pairs"] == 0


def test_fewer_than_two_outputs_is_unmeasurable_not_a_pass() -> None:
    assert panel_decorrelation([COPY_CAT_TEXT]) is None
    assert panel_decorrelation([]) is None
    assert panel_decorrelation([None, "", "   "]) is None
    assert panel_decorrelation([COPY_CAT_TEXT, None, ""]) is None, "blank/None voters are ignored, and one usable output is still unmeasurable"


def test_three_voters_average_every_pair() -> None:
    measured = panel_decorrelation([COPY_CAT_TEXT, COPY_CAT_TEXT, VARIED_TEXT])
    assert measured is not None
    assert measured["voters"] == 3
    assert measured["pairs"] == 3
    # pair(0,1) = 1.0, pair(0,2) = 0.0, pair(1,2) = 0.0 -> mean ~0.333
    assert measured["similarity"] == pytest.approx(1 / 3, abs=1e-6)
    assert measured["identical_pairs"] == 1


# ---------------------------------------------------------------------------
# composition_audit: declared axes, total collision, honest disclosure
# ---------------------------------------------------------------------------


def test_audit_flags_a_clone_panel_and_excludes_the_synthesizer() -> None:
    audit = composition_audit(_panel_plan(identical=True))
    assert audit is not None
    assert audit["basis"] == COMPOSITION_BASIS
    assert audit["panel_size"] == 3
    assert audit["voters"] == ["sol-0", "sol-1", "sol-2"]
    assert "synth" not in audit["voters"], "a synthesizer depends on the panel and is not a voter"
    assert audit["identical_tuples"] is True
    assert audit["colliding_axes"] == ["model_family", "framing"]
    assert set(UNSUPPORTED_AXES) <= set(audit["unsupported_axes"])
    assert "temperature" in audit["unsupported_axes"] and "seed" in audit["unsupported_axes"]


def test_audit_discloses_a_partial_collision_without_claiming_identity() -> None:
    audit = composition_audit(_panel_plan(identical=False))
    assert audit is not None
    assert audit["identical_tuples"] is False
    assert audit["colliding_axes"] == ["model_family"], "framings differ, so only the shared default model collides"
    assert "framing" in audit["note"]


def test_non_panel_modes_and_single_voters_have_no_panel_to_audit() -> None:
    parallel = _panel_plan(identical=True, plan_mode=SwarmMode.PARALLEL)
    assert composition_audit(parallel) is None
    assert panel_voters(parallel) == [], "a non-panel mode has no voters"
    single = SwarmPlan(swarm_id="single", goal="g", mode=SwarmMode.ENSEMBLE, tasks={"only": _task("only", COPY_CAT_TEXT)})
    assert composition_audit(single) is None, "one voter is not a panel to audit"
    assert [task.task_id for task in panel_voters(single)] == ["only"]


def test_shipped_decomposer_ensemble_is_never_a_total_collision() -> None:
    tasks = SwarmTaskDecomposer._decompose_ensemble("ship the bounded migration safely")
    plan = SwarmPlan(swarm_id="live-ensemble", goal="ship the bounded migration safely", mode=SwarmMode.ENSEMBLE, tasks=tasks)
    audit = composition_audit(plan)
    assert audit is not None
    assert audit["identical_tuples"] is False, "the decomposer's solver framings vary by construction; refusing them would refuse every healthy ensemble"
    assert "framing" not in audit["colliding_axes"]


# ---------------------------------------------------------------------------
# suspicious_unanimity: pure measurement, only the copy-cat signature fires
# ---------------------------------------------------------------------------


def _consensus(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"status": "approved", "approved": True, "agreement": 1.0, "counted_voters": 3}
    return {**base, **overrides}


def test_suspicious_unanimity_fires_only_on_unanimous_near_identical_panels() -> None:
    similar = panel_decorrelation([COPY_CAT_TEXT, COPY_CAT_TEXT])
    diverse = panel_decorrelation([COPY_CAT_TEXT, VARIED_TEXT])
    assert suspicious_unanimity(_consensus(), similar) is True
    assert suspicious_unanimity(_consensus(), diverse) is False
    assert suspicious_unanimity(_consensus(agreement=0.66), similar) is False, "a split vote is not copy-cat unanimity"
    assert suspicious_unanimity(_consensus(counted_voters=1), similar) is False, "one voter cannot form a panel"
    assert suspicious_unanimity(_consensus(), None) is False, "unmeasurable never fires a guard"
    assert suspicious_unanimity(None, similar) is False


# ---------------------------------------------------------------------------
# _apply_deliberation: the guard can only block
# ---------------------------------------------------------------------------


def test_apply_deliberation_downgrades_a_unanimous_copy_cat_panel() -> None:
    result = SwarmAggregator._apply_deliberation(_consensus(), APPROVE_EVIDENCE, decorrelation=panel_decorrelation([COPY_CAT_TEXT, COPY_CAT_TEXT]))
    assert result["approved"] is False
    assert result["status"] == "manual_review"
    assert "suspicious_unanimity" in result["blocked_by"]
    assert result["panel_decorrelation"]["similarity"] == pytest.approx(1.0)


def test_apply_deliberation_leaves_a_diverse_panel_approved() -> None:
    result = SwarmAggregator._apply_deliberation(_consensus(), APPROVE_EVIDENCE, decorrelation=panel_decorrelation([COPY_CAT_TEXT, VARIED_TEXT]))
    assert result["approved"] is True
    assert result["status"] == "approved"
    assert "blocked_by" not in result
    assert result["panel_decorrelation"]["decorrelation"] > 0.5


def test_the_guard_never_raises_a_refusal_to_an_approval() -> None:
    refused = _consensus(status="rejected", approved=False, agreement=0.2)
    result = SwarmAggregator._apply_deliberation(refused, APPROVE_EVIDENCE, decorrelation=panel_decorrelation([COPY_CAT_TEXT, COPY_CAT_TEXT]))
    assert result["approved"] is False
    assert result["status"] == "rejected", "blocking guards downgrade; nothing upgrades a refusal"


def test_unmeasured_panel_does_not_fire_the_guard_during_aggregation() -> None:
    plan = _panel_plan(identical=False)
    summaries = [COPY_CAT_TEXT, VARIED_TEXT, "A third, entirely different reading of the migration plan emerges"]
    for index in range(3):
        _completed(plan.tasks[f"sol-{index}"], summary=summaries[index], evidence=_vote(index))
    plan.requires_consensus = True
    # The outputs are genuinely varied, so the measured decorrelation is not
    # suspicious and the explicit approval must survive deliberation's guards.
    _completed(plan.tasks["synth"], summary="Synthesized the proposals")
    SwarmAggregator.aggregate(plan)
    assert plan.consensus is not None
    assert "suspicious_unanimity" not in (plan.consensus.get("blocked_by") or [])


def test_aggregation_downgrades_a_unanimous_copy_cat_panel_end_to_end() -> None:
    plan = _panel_plan(identical=True)
    plan.requires_consensus = True
    for index in range(3):
        _completed(plan.tasks[f"sol-{index}"], summary=COPY_CAT_TEXT, evidence=_vote(index))
    _completed(plan.tasks["synth"], summary="Weighed the proposals")

    result = SwarmAggregator.aggregate(plan)

    assert plan.metrics["panel_decorrelation"]["similarity"] == pytest.approx(1.0)
    assert plan.consensus is not None
    assert plan.consensus["approved"] is False
    assert plan.consensus["status"] == "manual_review"
    assert "suspicious_unanimity" in plan.consensus["blocked_by"]
    assert plan.status == "partial_success", "a downgraded approval must not read as a clean success"
    assert "Panel Decorrelation" in plan.final_result
    assert result["consensus"]["status"] == "manual_review"


# ---------------------------------------------------------------------------
# coordinator: refusal before registration, advisory otherwise
# ---------------------------------------------------------------------------


def test_coordinator_refuses_a_clone_panel_before_registration() -> None:
    with TemporaryDirectory() as storage, patch.object(SwarmTaskDecomposer, "decompose", side_effect=_stub_panel_decomposer(identical=True)):
        coordinator = SwarmCoordinator(storage_dir=storage)
        with pytest.raises(SwarmPlanValidationError) as excinfo:
            coordinator.create_swarm(goal="panel goal")
        assert coordinator.list_swarms() == [], "a refused plan must not remain visible as a running swarm"
    message = str(excinfo.value)
    assert "refused before registration" in message
    assert "panel composition is homogeneous" in message
    assert "model_family" in message and "framing" in message


def test_coordinator_records_a_partial_collision_as_advisory() -> None:
    with TemporaryDirectory() as storage, patch.object(SwarmTaskDecomposer, "decompose", side_effect=_stub_panel_decomposer(identical=False)):
        coordinator = SwarmCoordinator(storage_dir=storage)
        plan = coordinator.create_swarm(goal="panel goal")
    audit = plan.metrics["diversity"]
    assert audit["basis"] == COMPOSITION_BASIS
    assert audit["identical_tuples"] is False
    assert audit["panel_size"] == 3
