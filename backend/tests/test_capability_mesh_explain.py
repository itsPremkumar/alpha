"""Tests for the bounded explainability projection (plan §24, Feature W).

The load-bearing rules pinned here: every panel is bounded and truncation is
reported as a visible "…+N more" marker rather than a silent drop, a caption is
DERIVED from the evidence level plus sample count (so an unmeasured capability
can only read "unmeasured"), disagreements keep "left as alternatives" or
"unresolved", the resource meter carries ``None`` for unknown rather than 0.0,
the learning controls are advertised-not-implemented and default to False, and
the bundle never renders an overall agent ranking.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from alpha.capability_mesh.assignment import NO_ELIGIBLE_ENTITY, MicrotaskAssignment, Role, RoutingPlan, route
from alpha.capability_mesh.decomposition import Decomposition, Microtask
from alpha.capability_mesh.explain import (
    CAPTION_LIMITED_HISTORY,
    CAPTION_UNMEASURED,
    CAPTION_WELL_MEASURED,
    MAX_VISIBLE_CAPABILITIES,
    MAX_VISIBLE_CONFIDENCE,
    MAX_VISIBLE_STEPS,
    RESOLUTION_LEFT_AS_ALTERNATIVES,
    RESOLUTION_UNRESOLVED,
    TASK_STATUS_ROUTED,
    TRUNCATION_MARKER,
    ConfidenceView,
    ContributionMapView,
    DisagreementView,
    ExplanationBundle,
    FallbackNotice,
    LearningControls,
    ResourceView,
    SpecialistAssignmentView,
    TaskMapItem,
    explain_plan,
)
from alpha.capability_mesh.fingerprint import Pressure, Risk, SituationFingerprint
from alpha.capability_mesh.fit import HardConstraintRefusal
from alpha.capability_mesh.memory import CapabilityMemory, CapabilityRecord, OutcomeKind
from alpha.capability_mesh.models import CapabilityEstimate, CapabilityPassport, EntityKind, EvidenceLevel
from alpha.capability_mesh.policy import DecisionPolicy
from alpha.capability_mesh.synthesis import CompositionResult, ContributionSlot, SlotStatus

PV = "p1"


def _fingerprint(*, capabilities: tuple[str, ...] = ("code",)) -> SituationFingerprint:
    return SituationFingerprint(task_type="code_generation", risk_band=Risk.LOW, context_pressure=Pressure.LOW, required_capabilities=capabilities)


def _assignment(microtask_id: str = "m1", *, passport_id: str = "agent:alpha") -> MicrotaskAssignment:
    return MicrotaskAssignment(
        microtask_id=microtask_id,
        passport_id=passport_id,
        role=Role.PRIMARY,
        fit_score=0.8,
        score_explanation=("coverage 1.000 (1/1 required capabilities: code)", "quality 0.900 measured"),
        expected_outputs=(f"completed deliverable for '{microtask_id}'",),
        acceptance_checks=("the deliverable compiles",),
    )


def _plan(*, assignments: tuple[MicrotaskAssignment, ...] | None = None, refusals: tuple[HardConstraintRefusal, ...] = (), notes: tuple[str, ...] = (), capabilities: tuple[str, ...] = ("code",)) -> RoutingPlan:
    return RoutingPlan(fingerprint=_fingerprint(capabilities=capabilities), assignments=(_assignment(),) if assignments is None else assignments, refusals=refusals, notes=notes)


def _long_reason(length: int) -> str:
    return " ".join("coverage 1.000 (1/1 required capabilities: code) quality 0.900 measured" for _ in range(length // 4 + 1))[:length]


# --------------------------------------------------------------------- refusals


def test_explain_plan_requires_a_routing_plan_and_a_profile_version():
    with pytest.raises(ValueError, match="routing_plan must be a RoutingPlan"):
        explain_plan({}, profile_version=PV)
    with pytest.raises(ValueError, match="profile_version"):
        explain_plan(_plan(), profile_version="")


def test_explain_plan_refuses_bad_resource_inputs():
    with pytest.raises(ValueError, match="calls must be an int >= 0"):
        explain_plan(_plan(), calls=-1, profile_version=PV)
    with pytest.raises(ValueError, match="estimated_cost_usd must be a finite number >= 0 or None"):
        explain_plan(_plan(), calls=1, estimated_cost_usd=-0.5, profile_version=PV)
    with pytest.raises(ValueError, match="remaining_budget must be a finite number >= 0 or None"):
        explain_plan(_plan(), calls=1, remaining_budget=float("nan"), profile_version=PV)


def test_explain_plan_refuses_bad_confidence_rows():
    with pytest.raises(ValueError, match="confidence row must be a 4-tuple"):
        explain_plan(_plan(), confidence_rows=(("code", "agent:alpha"),), profile_version=PV)
    with pytest.raises(ValueError, match="confidence row .* must carry"):
        explain_plan(_plan(), confidence_rows=({"capability": "code"},), profile_version=PV)
    with pytest.raises(ValueError, match="evidence_level"):
        explain_plan(_plan(), confidence_rows=(("code", "agent:a", "vibes", 3),), profile_version=PV)


# ------------------------------------------------------------------ the panels


def test_the_bundle_carries_one_panel_each_and_bounds_them(tmp_path):
    plan = _plan()
    bundle = explain_plan(plan, calls=7, estimated_cost_usd=0.25, remaining_budget=1.75, profile_version=PV)

    assert len(bundle.task_map) == 1
    item = bundle.task_map[0]
    assert (item.microtask_id, item.title, item.status, item.capabilities) == ("m1", "m1", TASK_STATUS_ROUTED, ("code",))
    assert len(bundle.specialist_assignments) == 1
    view = bundle.specialist_assignments[0]
    assert (view.microtask_id, view.passport_id, view.role) == ("m1", "agent:alpha", "primary")
    assert "coverage 1.000" in view.reason
    assert bundle.resource_meter.calls == 7
    assert bundle.resource_meter.estimated_cost_usd == pytest.approx(0.25)
    assert bundle.resource_meter.remaining_budget == pytest.approx(1.75)
    assert bundle.honest_notes


def test_an_unassigned_step_is_a_task_map_status_not_a_fallback():
    plan = _plan(
        assignments=(),
        refusals=(
            HardConstraintRefusal(entity_id=NO_ELIGIBLE_ENTITY, unmet=("no eligible entity for microtask 'm2'",), microtask_id="m2"),
            HardConstraintRefusal(entity_id="agent:beta", unmet=("disabled",), microtask_id="m2"),
        ),
        notes=("microtask 'm2' left unassigned: no candidate passed the hard gates",),
    )

    bundle = explain_plan(plan, profile_version=PV)

    assert [item.status for item in bundle.task_map] == ["unassigned"]
    assert [item.microtask_id for item in bundle.task_map] == ["m2"]
    # The NO_ELIGIBLE_ENTITY sentinel is a task-map status; each real
    # candidate's refusal still becomes a fallback notice.
    assert sorted(notice.what_changed for notice in bundle.fallback_notices) == ["candidate refused: agent:beta on microtask 'm2'", "step left unassigned"]
    assert any(notice.why == "disabled" for notice in bundle.fallback_notices)


def test_a_per_candidate_refusal_and_a_missing_role_become_fallback_notices():
    plan = _plan(
        assignments=(_assignment(),),
        refusals=(HardConstraintRefusal(entity_id="agent:beta", unmet=("disabled",), microtask_id=None),),
        notes=("microtask 'm1': no challenger — only one entity passed the hard gates",),
    )

    bundle = explain_plan(plan, profile_version=PV)

    changed = {notice.what_changed for notice in bundle.fallback_notices}
    assert changed == {"candidate refused: agent:beta", "role not filled"}
    assert any(notice.why == "disabled" for notice in bundle.fallback_notices)


def test_truncation_is_marked_never_silent():
    plan = _plan(assignments=tuple(_assignment(f"m{index}") for index in range(MAX_VISIBLE_STEPS + 3)), capabilities=("code", "docs", "tests"))

    bundle = explain_plan(plan, profile_version=PV)

    assert len(bundle.task_map) == MAX_VISIBLE_STEPS
    markers = [note for note in bundle.honest_notes if TRUNCATION_MARKER in note]
    assert any(f"{TRUNCATION_MARKER}+3 more" in note for note in markers)
    assert any("task map" in note for note in markers)


def test_capabilities_are_bounded_per_item_with_a_visible_marker():
    """The per-item capability cap marks what it drops, exactly like the panels.

    Panel truncation writes "…+N more" into ``honest_notes``; a capability list
    truncated inside one item has nowhere else to say it, so the marker travels
    in the list itself. A silent cut would read as "these are all of them".
    """
    plan = _plan(capabilities=tuple(f"cap{index}" for index in range(20)))

    bundle = explain_plan(plan, profile_version=PV)

    capabilities = bundle.task_map[0].capabilities
    assert len(capabilities) == MAX_VISIBLE_CAPABILITIES
    assert len(capabilities) <= MAX_VISIBLE_CAPABILITIES
    assert capabilities[-1] == f"{TRUNCATION_MARKER}+13 more"
    # Sorted lexicographically, so the cut is the first 7 of 20 sorted ids.
    everything = sorted(f"cap{index}" for index in range(20))
    assert capabilities[:-1] == tuple(everything[: MAX_VISIBLE_CAPABILITIES - 1])


def test_capabilities_within_the_cap_carry_no_marker():
    plan = _plan(capabilities=("code", "docs"))

    bundle = explain_plan(plan, profile_version=PV)

    assert bundle.task_map[0].capabilities == ("code", "docs")
    assert not any(TRUNCATION_MARKER in capability for capability in bundle.task_map[0].capabilities)


def test_a_long_reason_is_bounded_with_a_visible_marker():
    assignment = MicrotaskAssignment(
        microtask_id="m1",
        passport_id="agent:alpha",
        role=Role.PRIMARY,
        fit_score=0.8,
        score_explanation=(_long_reason(4000),),
        expected_outputs=("completed deliverable for 'm1'",),
        acceptance_checks=("the deliverable compiles",),
    )
    plan = _plan(assignments=(assignment,))

    bundle = explain_plan(plan, profile_version=PV)

    reason = bundle.specialist_assignments[0].reason
    assert len(reason) <= 241
    assert reason.endswith(TRUNCATION_MARKER)


# --------------------------------------------------------- capability confidence


def test_confidence_captions_follow_the_evidence_level_and_the_sample_count():
    bundle = explain_plan(
        _plan(),
        confidence_rows=(
            ("code", "agent:alpha", EvidenceLevel.BENCHMARKED, 42),
            ("code", "agent:beta", EvidenceLevel.OBSERVED, 3),
            ("code", "agent:gamma", EvidenceLevel.UNMEASURED, 0),
            ("code", "agent:delta", EvidenceLevel.OUTCOME_VERIFIED, 2),
            ("code", "agent:eps", EvidenceLevel.BENCHMARKED, 4),
            ("code", "agent:zeta", EvidenceLevel.OBSERVED, 1),
        ),
        profile_version=PV,
    )

    captions = {row.entity_id: row.caption for row in bundle.capability_confidence}
    assert captions["agent:alpha"] == CAPTION_WELL_MEASURED + " (42 samples)"
    assert captions["agent:beta"] == CAPTION_LIMITED_HISTORY + " (3 samples)"
    assert captions["agent:gamma"] == CAPTION_UNMEASURED + " (0 samples)"
    assert captions["agent:delta"] == "well measured (2 samples)"
    # A benchmarked claim on 4 samples cannot buy "well measured".
    assert captions["agent:eps"] == "limited history (4 samples)"
    assert captions["agent:zeta"] == "limited history (1 sample)"


def test_the_caption_is_derived_and_cannot_be_hand_set():
    row = ConfidenceView(capability="code", entity_id="agent:alpha", evidence_level=EvidenceLevel.UNMEASURED, sample_count=0)

    assert row.caption == CAPTION_UNMEASURED + " (0 samples)"
    assert "well measured" not in row.caption
    # Not an input at all: constructing one is a TypeError, not a caption lie.
    with pytest.raises(TypeError):
        ConfidenceView(capability="code", entity_id="agent:alpha", evidence_level=EvidenceLevel.UNMEASURED, sample_count=0, caption="well measured")


def test_confidence_is_derived_from_memory_when_no_rows_are_supplied(tmp_path):
    memory = CapabilityMemory(tmp_path)
    for index in range(3):
        memory.record(
            CapabilityRecord(
                record_id=f"r{index}",
                task_fingerprint="fp_0011223344556677",
                task_category="*",
                entity_id="agent:alpha",
                capability="code",
                outcome=OutcomeKind.SELECTED,
                quality=0.9,
                profile_version=PV,
                recorded_at=f"2025-06-0{index + 1}T00:00:00+00:00",
            )
        )

    bundle = explain_plan(_plan(), memory=memory, profile_version=PV)

    assert len(bundle.capability_confidence) == 1
    row = bundle.capability_confidence[0]
    assert (row.capability, row.entity_id, row.sample_count) == ("code", "agent:alpha", 3)
    assert row.caption == "well measured (3 samples)"
    assert any("derived from capability memory" in note for note in bundle.honest_notes)


def test_confidence_is_disclosed_as_unknown_when_nothing_can_answer_it():
    bundle = explain_plan(_plan(), profile_version=PV)

    assert bundle.capability_confidence == ()
    assert any("capability confidence is empty" in note for note in bundle.honest_notes)


def test_the_confidence_panel_is_bounded():
    rows = tuple(("code", f"agent:{index}", EvidenceLevel.OBSERVED, index + 1) for index in range(MAX_VISIBLE_CONFIDENCE + 4))

    bundle = explain_plan(_plan(), confidence_rows=rows, profile_version=PV)

    assert len(bundle.capability_confidence) == MAX_VISIBLE_CONFIDENCE
    assert any(f"{TRUNCATION_MARKER}+4 more" in note for note in bundle.honest_notes)


# ---------------------------------------------------------------- disagreements


def test_conflicts_become_disagreement_rows_with_alternatives():
    composition = _composition(conflicts=("Slot m1: Contradiction: not X vs X",), alternatives=("Slot m1: Option (rejected) — a2",))

    bundle = explain_plan(_plan(), composition=composition, profile_version=PV)

    assert [(row.subject, row.resolution) for row in bundle.disagreements] == [
        ("Slot m1: Contradiction: not X vs X", RESOLUTION_LEFT_AS_ALTERNATIVES),
    ]


def test_an_unresolved_conflict_says_unresolved_and_so_does_a_missing_capability():
    composition = _composition(conflicts=("Slot m1: Contradiction: not X vs X",), alternatives=(), unresolved=("docs",))

    bundle = explain_plan(_plan(), composition=composition, profile_version=PV)

    assert composition.composed_artifact is None
    assert [row.resolution for row in bundle.disagreements] == [RESOLUTION_UNRESOLVED, RESOLUTION_UNRESOLVED]
    assert bundle.disagreements[1].subject == "capability 'docs' was not covered by any selected contribution"


def test_a_compositionless_plan_discloses_an_empty_contribution_map():
    bundle = explain_plan(_plan(), profile_version=PV)

    assert bundle.contribution_map == ()
    assert any("no composition was supplied" in note for note in bundle.honest_notes)


def test_the_contribution_map_projects_the_slot_selection_and_its_verifier():
    composition = _composition(conflicts=(), alternatives=(), unresolved=())

    bundle = explain_plan(_plan(), composition=composition, profile_version=PV)

    view = bundle.contribution_map[0]
    assert (view.slot_id, view.selected_artifact_id, view.entity_id) == ("m1", "artifact-1", "agent:alpha")
    assert view.verification_status == SlotStatus.PASSED.value
    assert view.capabilities == ("code",)


# --------------------------------------------------------------- resource meter


def test_the_resource_meter_reports_none_rather_than_zero():
    bundle = explain_plan(_plan(), calls=0, profile_version=PV)

    assert bundle.resource_meter == ResourceView(calls=0, estimated_cost_usd=None, remaining_budget=None)
    assert any("estimated cost is unknown" in note for note in bundle.honest_notes)


# -------------------------------------------------------------- learning controls


def test_learning_controls_are_advertised_not_implemented():
    controls = LearningControls()

    assert (controls.rate_result, controls.opt_out_routing_experiments, controls.disable_cross_run_memory, controls.clear_learned_profiles) == (False, False, False, False)
    assert "ADVERTISED, not implemented" in LearningControls.__doc__
    assert any("advertised, not implemented" in note for note in explain_plan(_plan(), profile_version=PV).honest_notes)


def test_the_learning_controls_docstring_names_the_four_controls():
    document = LearningControls.__doc__ or ""
    for phrase in ("rate a result", "opt out of routing experiments", "disable cross-run performance memory", "clear learned profile updates"):
        assert phrase in document


# ------------------------------------------------------------------ the honesty


def test_the_bundle_states_that_no_leaderboard_is_rendered():
    bundle = explain_plan(_plan(), profile_version=PV)

    assert any("no leaderboard is rendered" in note for note in bundle.honest_notes)
    assert not hasattr(bundle, "leaderboard")
    assert not hasattr(bundle, "ranking")


def test_the_task_map_discloses_that_titles_are_microtask_ids():
    bundle = explain_plan(_plan(), profile_version=PV)

    assert any("titles are microtask ids" in note for note in bundle.honest_notes)


# ------------------------------------------------------------ the value types


def test_task_map_item_refuses_an_unknown_status():
    with pytest.raises(ValueError, match="status must be one of"):
        TaskMapItem(microtask_id="m1", title="m1", status="done-ish")
    with pytest.raises(ValueError, match="title must be non-empty"):
        TaskMapItem(microtask_id="m1", title="", status=TASK_STATUS_ROUTED)


def test_assignment_view_refuses_an_empty_reason():
    with pytest.raises(ValueError, match="reason must be non-empty"):
        SpecialistAssignmentView(microtask_id="m1", passport_id="agent:a", role="primary", reason="")


def test_contribution_map_view_refuses_an_unknown_verification_status():
    with pytest.raises(ValueError, match="verification_status must be one of"):
        ContributionMapView(slot_id="s1", selected_artifact_id=None, entity_id=None, verification_status="probably fine")
    with pytest.raises(ValueError, match="slot_id must be non-empty"):
        ContributionMapView(slot_id="", selected_artifact_id=None, entity_id=None, verification_status=SlotStatus.PENDING.value)


def test_disagreement_view_refuses_an_unknown_resolution():
    with pytest.raises(ValueError, match="resolution must be one of"):
        DisagreementView(subject="x", resolution="ignored")
    with pytest.raises(ValueError, match="subject must be non-empty"):
        DisagreementView(subject="", resolution=RESOLUTION_UNRESOLVED)


def test_confidence_view_refuses_a_bad_sample_count():
    with pytest.raises(ValueError, match="sample_count must be an int >= 0"):
        ConfidenceView(capability="code", entity_id="agent:a", evidence_level=EvidenceLevel.UNMEASURED, sample_count=-1)
    with pytest.raises(ValueError, match="capability and entity_id must be non-empty"):
        ConfidenceView(capability="", entity_id="agent:a", evidence_level=EvidenceLevel.UNMEASURED, sample_count=0)
    with pytest.raises(ValueError, match="unknown evidence_level"):
        ConfidenceView(capability="code", entity_id="agent:a", evidence_level="vibes", sample_count=3)


def test_fallback_notice_refuses_an_empty_half():
    with pytest.raises(ValueError, match="what_changed must be non-empty"):
        FallbackNotice(what_changed="", why="because")
    with pytest.raises(ValueError, match="why must be non-empty"):
        FallbackNotice(what_changed="switch", why="")


def test_resource_view_refuses_a_negative_call_count():
    with pytest.raises(ValueError, match="calls must be an int >= 0"):
        ResourceView(calls=-3)


def test_the_bundle_refuses_a_wrongly_typed_panel():
    bundle = explain_plan(_plan(), profile_version=PV)
    with pytest.raises(ValueError, match="task_map must contain TaskMapItem rows"):
        ExplanationBundle(
            task_map=("m1",),
            specialist_assignments=bundle.specialist_assignments,
            contribution_map=bundle.contribution_map,
            capability_confidence=bundle.capability_confidence,
            disagreements=bundle.disagreements,
            resource_meter=bundle.resource_meter,
            fallback_notices=bundle.fallback_notices,
            learning_controls=bundle.learning_controls,
            honest_notes=bundle.honest_notes,
        )
    with pytest.raises(ValueError, match="resource_meter must be a ResourceView"):
        ExplanationBundle(
            task_map=bundle.task_map,
            specialist_assignments=bundle.specialist_assignments,
            contribution_map=bundle.contribution_map,
            capability_confidence=bundle.capability_confidence,
            disagreements=bundle.disagreements,
            resource_meter={"calls": 0},
            fallback_notices=bundle.fallback_notices,
            learning_controls=bundle.learning_controls,
            honest_notes=bundle.honest_notes,
        )
    with pytest.raises(ValueError, match="honest_notes entries must be non-empty"):
        ExplanationBundle(
            task_map=bundle.task_map,
            specialist_assignments=bundle.specialist_assignments,
            contribution_map=bundle.contribution_map,
            capability_confidence=bundle.capability_confidence,
            disagreements=bundle.disagreements,
            resource_meter=bundle.resource_meter,
            fallback_notices=bundle.fallback_notices,
            learning_controls=bundle.learning_controls,
            honest_notes=("",),
        )


def test_the_bundle_round_trips_through_dict():
    bundle = explain_plan(_plan(), calls=3, estimated_cost_usd=0.1, profile_version=PV)

    reloaded = ExplanationBundle.from_dict(bundle.to_dict())

    assert reloaded.task_map == bundle.task_map
    assert reloaded.specialist_assignments == bundle.specialist_assignments
    assert reloaded.capability_confidence == bundle.capability_confidence
    assert reloaded.resource_meter == bundle.resource_meter
    assert reloaded.learning_controls == bundle.learning_controls
    assert reloaded.honest_notes == bundle.honest_notes


# ------------------------------------------------------------ a realistic route


def test_the_projection_works_over_a_real_routed_plan():
    passport = CapabilityPassport(
        entity_id="agent:alpha",
        entity_type=EntityKind.AGENT,
        version="v1",
        capabilities=(CapabilityEstimate(capability="code", task_category="*", quality_mean=0.9, sample_count=20, evidence_level=EvidenceLevel.BENCHMARKED, profile_version=PV),),
    )
    microtask = Microtask(
        microtask_id="m1",
        title="write the parser",
        summary="a parser",
        required_capabilities=("code",),
        acceptance_checks=("the parser compiles",),
    )
    plan = route(
        _fingerprint(),
        Decomposition(fingerprint=_fingerprint(), microtasks=(microtask,), decomposer="heuristic_baseline"),
        [passport],
        policy=DecisionPolicy.BALANCED,
        profile_version=PV,
        now=datetime(2025, 6, 15, tzinfo=UTC),
    )

    bundle = explain_plan(plan, calls=2, profile_version=PV)

    assert bundle.task_map[0].status == TASK_STATUS_ROUTED
    assert bundle.specialist_assignments[0].passport_id == "agent:alpha"
    assert bundle.specialist_assignments[0].role == "primary"
    assert bundle.resource_meter.calls == 2
    assert bundle.honest_notes


def _composition(*, conflicts: tuple[str, ...], alternatives: tuple[str, ...], unresolved: tuple[str, ...] = ()) -> CompositionResult:
    """One composition result carrying the conflict/alternatives shape given."""
    slots = (
        ContributionSlot(
            slot_id="m1",
            requirement_id="m1",
            capability_target=("code",),
            selected_artifact_id="artifact-1",
            selected_reason="best score 0.500",
            verification_status=SlotStatus.PASSED,
            provenance=(("agent:alpha", "primary"),),
        ),
    )
    return CompositionResult(
        slots=slots,
        composed_artifact=None if conflicts else "deliverable text",
        alternatives=alternatives,
        conflicts=conflicts,
        provenance_map={"artifact-1": "agent:alpha"},
        unresolved_capabilities=unresolved,
        profile_version=PV,
    )
