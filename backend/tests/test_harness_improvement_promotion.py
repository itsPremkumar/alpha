"""The promotion ladder, atomic activation and rollback (guide §11.3).

The gate is the only place a candidate can become the active harness, so these
tests pin the properties that keep it honest:

1. **A gate that did not run is not a gate that passed.** ``NOT_RUN`` regression
   suites, unmeasured validation and absent probe/contrast evidence each refuse
   promotion by name — and ``automatic_global_promotion`` is off, so even a
   fully measured candidate parks for an operator.
2. **Privileged kinds need human approval even when automation is on**, and
   ``force`` never substitutes for a recorded approval.
3. **The ladder refuses jumps.** ``promote_state`` only moves forward one rung or
   to an explicit off-ramp, and provenance never changes the bundle id.
4. **Activation is an atomic pointer swap with rollback as a one-step restore**,
   and a rollback that cannot be performed says why instead of clearing the
   pointer.
"""

from __future__ import annotations

from typing import Any

import pytest

from alpha.harness_improvement import (
    ComponentKind,
    ContrastReport,
    ContrastVerdict,
    HarnessBundle,
    HarnessImprovementStore,
    PromotionState,
    build_bundle,
)
from alpha.harness_improvement.promotion import (
    ActivePointer,
    PromotionEvidence,
    PromotionPolicy,
    RegressionSuiteResult,
    activate,
    active_bundle,
    evaluate_promotion,
    promote_state,
    promotion_history,
    reject_candidate,
    rollback,
)
from alpha.harness_improvement.store import StoreCorrupt

# ---------------------------------------------------------------- fixtures


def _bundle(component_id: str = "system_prompt/base", *, state: PromotionState = PromotionState.DRAFT) -> HarnessBundle:
    bundle, _ = build_bundle(
        components={component_id: (ComponentKind.SYSTEM_PROMPT, f"content of {component_id}")},
        parent_bundle_id=None,
        created_by="test",
        promotion_state=state,
    )
    return bundle


def _contrast(**overrides: Any) -> ContrastReport:
    fields: dict[str, Any] = {
        "candidate_id": "p-1",
        "scope": "invariant",
        "verdict": ContrastVerdict.POSITIVE,
        "targeted_failure_resolved": True,
        "improvements": ("report.parses: failed -> passed",),
        "regressions": (),
        "deltas": {},
        "uncertainty": (),
        "evidence_refs": ("probe:run-a:baseline", "probe:run-a:p-1"),
    }
    fields.update(overrides)
    return ContrastReport(**fields)


def _evidence(**overrides: Any) -> PromotionEvidence:
    fields: dict[str, Any] = {
        "validation_clean": True,
        "probe_outcomes": 1,
        "contrasts": (_contrast(),),
        "regression_suites": (RegressionSuiteResult(name="suite", passed=True),),
        "approvals": (),
        "component_kinds": (ComponentKind.SYSTEM_PROMPT,),
        "deliverable_verified": True,
    }
    fields.update(overrides)
    return PromotionEvidence(**fields)


def _store(tmp_path: Any) -> HarnessImprovementStore:
    return HarnessImprovementStore(tmp_path / "hi")


# ---------------------------------------------------------------- the gate


class TestPromotionGate:
    def test_a_fully_measured_candidate_is_allowed_only_with_automation_on(self) -> None:
        bundle = _bundle()
        automated = evaluate_promotion(bundle=bundle, evidence=_evidence(), policy=PromotionPolicy(automatic_global_promotion=True))
        assert automated.allowed is True
        assert automated.required_approvals == ()

        default = evaluate_promotion(bundle=bundle, evidence=_evidence())
        assert default.allowed is False
        assert any("automatic_global_promotion is disabled" in reason for reason in default.reasons)

    def test_unmeasured_gates_refuse_by_name(self) -> None:
        bundle = _bundle()
        policy = PromotionPolicy(automatic_global_promotion=True)

        unvalidated = evaluate_promotion(bundle=bundle, evidence=_evidence(validation_clean=None), policy=policy)
        assert any("static validation has not been run" in reason for reason in unvalidated.reasons)
        failed_validation = evaluate_promotion(bundle=bundle, evidence=_evidence(validation_clean=False), policy=policy)
        assert any("static validation is not clean" in reason for reason in failed_validation.reasons)

        no_probe = evaluate_promotion(bundle=bundle, evidence=_evidence(probe_outcomes=0), policy=policy)
        assert any("no probe outcome was recorded" in reason for reason in no_probe.reasons)

        no_contrast = evaluate_promotion(bundle=bundle, evidence=_evidence(contrasts=()), policy=policy)
        assert any("no contrast report was recorded" in reason for reason in no_contrast.reasons)

    def test_a_regression_suite_that_did_not_run_is_not_a_passing_suite(self) -> None:
        bundle = _bundle()
        policy = PromotionPolicy(automatic_global_promotion=True)
        missing = evaluate_promotion(bundle=bundle, evidence=_evidence(regression_suites=()), policy=policy)
        assert any("the regression suite has not been run" in reason for reason in missing.reasons)
        not_run = evaluate_promotion(bundle=bundle, evidence=_evidence(regression_suites=(RegressionSuiteResult(name="suite", passed=None),)), policy=policy)
        assert any("did not run" in reason for reason in not_run.reasons)
        failed_suite = evaluate_promotion(bundle=bundle, evidence=_evidence(regression_suites=(RegressionSuiteResult(name="nightly", passed=False),)), policy=policy)
        assert any(reason == "regression suite failed: nightly" for reason in failed_suite.reasons)

    def test_the_regression_requirement_can_only_be_lifted_by_the_policy(self) -> None:
        bundle = _bundle()
        decision = evaluate_promotion(bundle=bundle, evidence=_evidence(regression_suites=()), policy=PromotionPolicy(automatic_global_promotion=True, require_regression_suite=False))
        assert decision.allowed is True

    def test_confounded_and_negative_contrasts_never_promote(self) -> None:
        bundle = _bundle()
        policy = PromotionPolicy(automatic_global_promotion=True)
        confounded = evaluate_promotion(bundle=bundle, evidence=_evidence(contrasts=(_contrast(verdict=ContrastVerdict.CONFOUNDED),)), policy=policy)
        assert any("at least one contrast is confounded" in reason for reason in confounded.reasons)
        negative = evaluate_promotion(bundle=bundle, evidence=_evidence(contrasts=(_contrast(verdict=ContrastVerdict.NEGATIVE),)), policy=policy)
        assert any("at least one contrast is negative" in reason for reason in negative.reasons)

    def test_a_positive_that_did_not_resolve_its_targeted_failure_is_refused(self) -> None:
        bundle = _bundle()
        policy = PromotionPolicy(automatic_global_promotion=True)
        unresolved = evaluate_promotion(bundle=bundle, evidence=_evidence(contrasts=(_contrast(targeted_failure_resolved=None),)), policy=policy)
        assert any("no positive contrast resolved its targeted failure" in reason for reason in unresolved.reasons)

    def test_a_positive_deliverable_failure_refuses(self) -> None:
        bundle = _bundle()
        decision = evaluate_promotion(bundle=bundle, evidence=_evidence(deliverable_verified=False), policy=PromotionPolicy(automatic_global_promotion=True))
        assert any("failed deterministic verification" in reason for reason in decision.reasons)

    @pytest.mark.parametrize(("kind", "approval"), [(ComponentKind.SCRIPT, "scripts"), (ComponentKind.MIDDLEWARE, "middleware")])
    def test_privileged_kinds_need_a_recorded_approval_even_with_automation_on(self, kind: ComponentKind, approval: str) -> None:
        bundle = _bundle()
        policy = PromotionPolicy(automatic_global_promotion=True)
        pending = evaluate_promotion(bundle=bundle, evidence=_evidence(component_kinds=(kind,)), policy=policy)
        assert pending.allowed is False
        assert pending.required_approvals == (approval,)
        assert any("human approval is required" in reason for reason in pending.reasons)

        approved = evaluate_promotion(bundle=bundle, evidence=_evidence(component_kinds=(kind,), approvals=(approval,)), policy=policy)
        assert approved.allowed is True
        assert approved.required_approvals == ()

    def test_no_other_kind_ever_demands_a_permissions_approval(self) -> None:
        """The permissions flag stays in the schema but can never fire: every
        promotable component kind is outside the editable policy surface."""
        bundle = _bundle()
        for kind in ComponentKind:
            if kind in {ComponentKind.SCRIPT, ComponentKind.MIDDLEWARE}:
                continue
            decision = evaluate_promotion(bundle=bundle, evidence=_evidence(component_kinds=(kind,)), policy=PromotionPolicy(automatic_global_promotion=True))
            assert decision.required_approvals == (), f"{kind} must not require a permissions approval"

    @pytest.mark.parametrize("state", [PromotionState.REJECTED, PromotionState.QUARANTINED, PromotionState.ROLLED_BACK])
    def test_off_ramp_states_cannot_be_promoted_from(self, state: PromotionState) -> None:
        bundle = promote_state(_bundle(), state)
        decision = evaluate_promotion(bundle=bundle, evidence=_evidence(), policy=PromotionPolicy(automatic_global_promotion=True))
        assert decision.allowed is False
        assert decision.reasons == (f"candidate is {state} and cannot be promoted from that state",)


# ---------------------------------------------------------------- the ladder


class TestPromotionLadder:
    def test_every_forward_rung_moves_and_the_bundle_id_never_changes(self) -> None:
        bundle = _bundle()
        bundle_id = bundle.bundle_id
        ladder = [PromotionState.STATICALLY_VALIDATED, PromotionState.PROBED, PromotionState.REGRESSION_TESTED, PromotionState.SHADOW, PromotionState.CANARY, PromotionState.ACTIVE]
        current = bundle
        for target in ladder:
            current = promote_state(current, target)
            assert current.promotion_state is target
            assert current.bundle_id == bundle_id, "promotion state is provenance, not identity"

    def test_a_skipped_rung_is_refused_with_both_states_named(self) -> None:
        with pytest.raises(ValueError, match="draft"):
            promote_state(_bundle(), PromotionState.PROBED)
        with pytest.raises(ValueError, match="active"):
            promote_state(_bundle(state=PromotionState.ACTIVE), PromotionState.STATICALLY_VALIDATED)

    @pytest.mark.parametrize("off_ramp", [PromotionState.REJECTED, PromotionState.QUARANTINED, PromotionState.ROLLED_BACK])
    def test_off_ramps_are_reachable_from_any_state(self, off_ramp: PromotionState) -> None:
        assert promote_state(_bundle(), off_ramp).promotion_state is off_ramp
        assert promote_state(_bundle(state=PromotionState.PROBED), off_ramp).promotion_state is off_ramp


# ---------------------------------------------------------------- activation


class TestActivation:
    def test_a_fully_gated_activation_records_pointer_bundle_state_and_event(self, tmp_path: Any) -> None:
        store = _store(tmp_path)
        bundle = _bundle()
        store.save_bundle(bundle, [])
        decision = evaluate_promotion(bundle=bundle, evidence=_evidence(), policy=PromotionPolicy(automatic_global_promotion=True))
        result = activate(store, bundle_id=bundle.bundle_id, actor="operator", reason="promote after probe", decision=decision)

        assert result["activated"] is True
        pointer = store.load_active()
        assert pointer is not None
        assert pointer.bundle_id == bundle.bundle_id
        assert pointer.previous_bundle_id is None
        assert pointer.actor == "operator"
        assert store.load_bundle(bundle.bundle_id).promotion_state is PromotionState.ACTIVE

        history = promotion_history(store)
        assert [row["event"] for row in history["events"]] == ["activated"]
        assert history["pointer_error"] is None

    def test_an_activation_requires_an_actor(self, tmp_path: Any) -> None:
        store = _store(tmp_path)
        bundle = _bundle()
        store.save_bundle(bundle, [])
        result = activate(store, bundle_id=bundle.bundle_id, actor="   ", reason="r")
        assert result["activated"] is False
        assert "explicit actor" in result["reason"]

    def test_an_unknown_bundle_is_refused(self, tmp_path: Any) -> None:
        result = activate(_store(tmp_path), bundle_id="sha256:missing", actor="operator", reason="r")
        assert result["activated"] is False
        assert "is not stored" in result["reason"]

    def test_unmet_gates_refuse_and_force_never_bypasses_a_pending_approval(self, tmp_path: Any) -> None:
        store = _store(tmp_path)
        bundle = _bundle()
        store.save_bundle(bundle, [])
        evidence = _evidence(component_kinds=(ComponentKind.SCRIPT,), approvals=())
        decision = evaluate_promotion(bundle=bundle, evidence=evidence, policy=PromotionPolicy(automatic_global_promotion=True))
        assert decision.allowed is False

        refused = activate(store, bundle_id=bundle.bundle_id, actor="operator", reason="r", decision=decision)
        assert refused["activated"] is False
        assert "approval is still required" in refused["reason"]
        assert "scripts" in refused["reason"]

        forced = activate(store, bundle_id=bundle.bundle_id, actor="operator", reason="r", decision=decision, force=True)
        assert forced["activated"] is False, "force must not substitute for a recorded approval"

        approved = activate(store, bundle_id=bundle.bundle_id, actor="operator", reason="r", decision=decision, approvals=("scripts",), force=True)
        assert approved["activated"] is True

    def test_an_operator_override_needs_force_and_names_the_reasons(self, tmp_path: Any) -> None:
        store = _store(tmp_path)
        bundle = _bundle()
        store.save_bundle(bundle, [])
        decision = evaluate_promotion(bundle=bundle, evidence=_evidence(), policy=PromotionPolicy())  # automatic off
        refused = activate(store, bundle_id=bundle.bundle_id, actor="operator", reason="r", decision=decision)
        assert refused["activated"] is False
        assert "automatic_global_promotion is disabled" in refused["reason"]
        forced = activate(store, bundle_id=bundle.bundle_id, actor="operator", reason="r", decision=decision, force=True)
        assert forced["activated"] is True
        event = promotion_history(store)["events"][0]
        assert event["gates"]["allowed"] is False, "the override still records the decision it overrode"

    def test_a_library_activation_without_a_decision_skips_gate_evaluation(self, tmp_path: Any) -> None:
        """The library contract: a caller that supplies no decision is trusted to
        have evaluated gates itself; the router always supplies one."""
        store = _store(tmp_path)
        bundle = _bundle()
        store.save_bundle(bundle, [])
        result = activate(store, bundle_id=bundle.bundle_id, actor="operator", reason="seed pointer")
        assert result["activated"] is True

    def test_activating_the_pointer_bundle_again_is_refused(self, tmp_path: Any) -> None:
        store = _store(tmp_path)
        bundle = _bundle()
        store.save_bundle(bundle, [])
        assert activate(store, bundle_id=bundle.bundle_id, actor="a", reason="r")["activated"] is True
        again = activate(store, bundle_id=bundle.bundle_id, actor="a", reason="r")
        assert again["activated"] is False
        assert again["reason"] == "bundle is already active"

    def test_activation_remembers_the_previous_bundle(self, tmp_path: Any) -> None:
        store = _store(tmp_path)
        first = _bundle("system_prompt/base")
        second = _bundle("system_prompt/other")
        store.save_bundle(first, [])
        store.save_bundle(second, [])
        activate(store, bundle_id=first.bundle_id, actor="a", reason="first")
        result = activate(store, bundle_id=second.bundle_id, actor="a", reason="second")
        assert result["previous_bundle_id"] == first.bundle_id
        pointer = store.load_active()
        assert pointer is not None and pointer.previous_bundle_id == first.bundle_id


# ---------------------------------------------------------------- rollback


class TestRollback:
    def test_rollback_restores_the_previous_bundle_in_one_step(self, tmp_path: Any) -> None:
        store = _store(tmp_path)
        first = _bundle("system_prompt/base")
        second = _bundle("system_prompt/other")
        store.save_bundle(first, [])
        store.save_bundle(second, [])
        activate(store, bundle_id=first.bundle_id, actor="a", reason="first")
        activate(store, bundle_id=second.bundle_id, actor="a", reason="second")

        result = rollback(store, actor="operator", reason="regression spotted")
        assert result["rolled_back"] is True
        assert result["restored_bundle_id"] == first.bundle_id
        assert result["from_bundle_id"] == second.bundle_id
        pointer = store.load_active()
        assert pointer is not None and pointer.bundle_id == first.bundle_id and pointer.previous_bundle_id is None
        assert store.load_bundle(first.bundle_id).promotion_state is PromotionState.ACTIVE
        assert [row["event"] for row in promotion_history(store)["events"]] == ["activated", "activated", "rolled_back"]

    def test_a_rollback_without_a_previous_bundle_says_so(self, tmp_path: Any) -> None:
        store = _store(tmp_path)
        bundle = _bundle()
        store.save_bundle(bundle, [])
        activate(store, bundle_id=bundle.bundle_id, actor="a", reason="first")
        result = rollback(store, actor="operator", reason="r")
        assert result["rolled_back"] is False
        assert "no previous known-good bundle" in result["reason"]
        assert store.load_active().bundle_id == bundle.bundle_id, "a refused rollback must not clear the pointer"

    def test_a_rollback_without_a_pointer_refuses(self, tmp_path: Any) -> None:
        result = rollback(_store(tmp_path), actor="operator", reason="r")
        assert result["rolled_back"] is False
        assert "nothing to roll back" in result["reason"]

    def test_a_rollback_to_a_missing_previous_bundle_refuses_instead_of_clearing(self, tmp_path: Any) -> None:
        store = _store(tmp_path)
        bundle = _bundle()
        store.save_bundle(bundle, [])
        store.save_active(ActivePointer(bundle_id=bundle.bundle_id, previous_bundle_id="sha256:vanished", activated_at="2026-01-01T00:00:00Z", actor="a", reason="seed"))
        result = rollback(store, actor="operator", reason="r")
        assert result["rolled_back"] is False
        assert "refusing to clear the active pointer" in result["reason"]
        assert store.load_active().bundle_id == bundle.bundle_id

    def test_a_rollback_requires_an_actor(self, tmp_path: Any) -> None:
        result = rollback(_store(tmp_path), actor=" ", reason="r")
        assert result["rolled_back"] is False
        assert "explicit actor" in result["reason"]


# ---------------------------------------------------------------- rejection


class TestRejection:
    def test_rejecting_a_candidate_marks_it_and_leaves_the_pointer_alone(self, tmp_path: Any) -> None:
        store = _store(tmp_path)
        base = _bundle("system_prompt/base")
        candidate = _bundle("system_prompt/other")
        store.save_bundle(base, [])
        store.save_bundle(candidate, [])
        activate(store, bundle_id=base.bundle_id, actor="a", reason="baseline")

        result = reject_candidate(store, bundle_id=candidate.bundle_id, actor="operator", reason="no evidence")
        assert result["rejected"] is True
        assert store.load_bundle(candidate.bundle_id).promotion_state is PromotionState.REJECTED
        assert store.load_active().bundle_id == base.bundle_id
        assert [row["event"] for row in promotion_history(store)["events"]] == ["activated", "rejected"]

    def test_rejecting_an_unknown_bundle_is_refused(self, tmp_path: Any) -> None:
        result = reject_candidate(_store(tmp_path), bundle_id="sha256:missing", actor="operator", reason="r")
        assert result["rejected"] is False
        assert "is not stored" in result["reason"]


# ---------------------------------------------------------------- readers


def test_active_bundle_raises_on_a_dangling_pointer(tmp_path: Any) -> None:
    store = _store(tmp_path)
    store.save_active(ActivePointer(bundle_id="sha256:vanished", previous_bundle_id=None, activated_at="2026-01-01T00:00:00Z", actor="a", reason="seed"))
    with pytest.raises(StoreCorrupt, match="sha256:vanished"):
        active_bundle(store)
    history = promotion_history(store)
    assert history["pointer"] is not None and history["pointer"]["bundle_id"] == "sha256:vanished"


def test_active_bundle_is_none_without_a_pointer(tmp_path: Any) -> None:
    assert active_bundle(_store(tmp_path)) is None


def test_a_corrupt_pointer_reads_as_pointer_error_not_as_no_pointer(tmp_path: Any) -> None:
    store = _store(tmp_path)
    store.active_path.parent.mkdir(parents=True, exist_ok=True)
    store.active_path.write_text("{not json", encoding="utf-8")
    history = promotion_history(store)
    assert history["pointer"] is None
    assert history["pointer_error"] and "malformed" in history["pointer_error"]


def test_a_torn_promotion_line_is_disclosed_not_silently_dropped(tmp_path: Any) -> None:
    store = _store(tmp_path)
    bundle = _bundle()
    store.save_bundle(bundle, [])
    activate(store, bundle_id=bundle.bundle_id, actor="a", reason="r")
    with store.promotions_path.open("a", encoding="utf-8") as handle:
        handle.write('{"event": "truncated"\n')

    history = promotion_history(store)
    assert [row["event"] for row in history["events"]] == ["activated"]
    assert history["unreadable"] == ["line 2"]
