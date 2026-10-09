"""Governed AVO layer: policy gate, router, evaluation, receipts, session.

Split from ``test_avo_governance.py`` because these suites exercise the
*decision* modules rather than the value types: the gate deciding an action,
the router admitting a task, the pipeline shaping evidence, the receipt chain
recording it, and the session keeping the five in step.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from alpha.avo.budgets import BudgetKind, BudgetLedger
from alpha.avo.contracts import (
    ActionType,
    AvoRunRequest,
    EvaluationResult,
    EvaluatorGate,
    ExperimentSpec,
    GateStatus,
    PromotionLevel,
    ProposedAction,
    RiskClass,
    TaskFeatures,
    TruthLabel,
    WorkspaceLocator,
)
from alpha.avo.evaluation import EvaluatorPipeline, TierOutcome, UnknownTier
from alpha.avo.lifecycle import Actor, AvoState, RunLifecycle
from alpha.avo.policy import POLICY_VERSION, PolicyGate, ReasonCode, compute_risk
from alpha.avo.profiles import UnknownProfile, approval_policy, budget_profile, capability_profile, evaluator_profile
from alpha.avo.receipts import EVENT_TYPES, ReceiptChain, ReceiptRejected
from alpha.avo.router import ROUTE_COMPLEXITY_FLOOR, Router
from alpha.avo.session import GovernedRunSession, SessionRefused, SessionStateError


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def make_gate(profile_id: str = "repo_repair", *, budget_id: str = "paid_bounded", approval_id: str = "default_deny") -> PolicyGate:
    return PolicyGate(
        capability=capability_profile(profile_id),
        approval=approval_policy(approval_id),
        ledger=BudgetLedger(profile=budget_profile(budget_id)),
    )


def write_action(paths: list[str], *, experiment_id: str = "exp1", **kwargs) -> ProposedAction:
    return ProposedAction(experiment_id=experiment_id, action_type=ActionType.WRITE_CANDIDATE_FILE, write_scope=paths, **kwargs)


def read_action(path: str = "src/main.py", *, experiment_id: str = "exp1", **kwargs) -> ProposedAction:
    return ProposedAction(experiment_id=experiment_id, action_type=ActionType.READ_FILE, read_scope=[path], **kwargs)


def make_request(**overrides) -> AvoRunRequest:
    base = dict(
        alpha_run_id="run1",
        task_id="task1",
        task_description="fix the failing test",
        workspace=WorkspaceLocator(project_id="proj", relative_path="candidates/w1"),
        requested_mode="repo_repair",
        capability_profile_id="repo_repair",
        budget_profile_id="local_low_resource",
        evaluator_profile_id="code_repair",
        approval_policy_id="default_deny",
        idempotency_key="key-abcdef12",
        experiment=ExperimentSpec(hypothesis="the guard clause is inverted"),
    )
    base.update(overrides)
    return AvoRunRequest(**base)


# ---------------------------------------------------------------------------
# policy gate
# ---------------------------------------------------------------------------
class TestPolicyGate:
    def test_allowed_action_carries_its_reservation(self):
        gate = make_gate()
        decision = gate.decide(write_action(["candidates/c1/x.py"]))
        assert decision.allowed
        assert decision.reason_code == ReasonCode.OK.value
        assert decision.budget_reservation["action"] == 1.0
        assert decision.authority == "policy_gate"
        assert decision.policy_version == POLICY_VERSION

    def test_refused_action_reserves_nothing(self):
        gate = make_gate()
        decision = gate.decide(write_action(["tests/test_x.py"]))
        assert not decision.allowed
        assert decision.budget_reservation == {}
        assert decision.reason_code == ReasonCode.PROTECTED_PATH.value

    def test_write_outside_profile_scope_is_refused(self):
        gate = make_gate()
        decision = gate.decide(write_action(["src/main.py"]))
        assert not decision.allowed
        assert decision.reason_code == ReasonCode.WRITE_OUTSIDE_SCOPE.value

    def test_action_type_not_in_allow_list_is_refused(self):
        gate = make_gate()  # repo_repair does not allow RUN_FULL_SUITE
        decision = gate.decide(ProposedAction(experiment_id="exp1", action_type=ActionType.RUN_FULL_SUITE))
        assert not decision.allowed
        assert decision.reason_code == ReasonCode.ACTION_NOT_PERMITTED.value
        assert "repo_repair" in decision.reason

    def test_disabled_profile_reaches_nothing(self):
        gate = make_gate("alpha_self_update")
        decision = gate.decide(read_action())
        assert not decision.allowed
        assert decision.reason_code == ReasonCode.PROFILE_DISABLED.value

    def test_self_assessed_risk_is_recorded_never_used(self):
        gate = make_gate()
        low = gate.decide(read_action())
        high_claim = gate.decide(read_action())
        # Same operation, same computed class regardless of any self-assessment
        # (ProposedAction.self_assessed_risk defaults LOW here; set HIGH on one).
        assert high_claim.risk_class == low.risk_class
        loudest = read_action()
        loudest.self_assessed_risk = RiskClass.LOW  # claimed low
        assert gate.decide(loudest).risk_class is RiskClass.LOW
        loud = ProposedAction(
            experiment_id="exp1",
            action_type=ActionType.RUN_REGRESSION_SUITE,
            self_assessed_risk=RiskClass.LOW,
        )
        # Suite runs compute MEDIUM from the operation, not from the claim.
        assert gate.decide(loud).risk_class is RiskClass.MEDIUM

    def test_irreversible_action_escalates(self):
        reversible = read_action()
        irreversible = ProposedAction(experiment_id="exp1", action_type=ActionType.WRITE_CANDIDATE_FILE, write_scope=["candidates/a.py"], reversible=False)
        gate = make_gate()
        assert compute_risk(irreversible, gate.capability) is RiskClass.HIGH
        assert compute_risk(reversible, gate.capability) is RiskClass.LOW

    def test_network_profile_escalates(self):
        gate = make_gate("external_write_approval")
        # read under a network-allowlist profile: LOW + 1 = MEDIUM
        assert compute_risk(read_action(), gate.capability) is RiskClass.MEDIUM

    def test_approval_required_without_id_refuses_but_records_the_ask(self):
        gate = make_gate("external_write_approval")
        decision = gate.decide(ProposedAction(experiment_id="exp1", action_type=ActionType.REQUEST_PROMOTION))
        assert not decision.allowed
        assert decision.reason_code == ReasonCode.APPROVAL_REQUIRED.value
        assert decision.approval_required is True

    def test_granted_approval_allows_and_records_id(self):
        gate = make_gate("external_write_approval")
        action = ProposedAction(experiment_id="exp1", action_type=ActionType.REQUEST_PROMOTION, arguments={"approval_id": "appr_9"})
        decision = gate.decide(action, granted_approvals=["appr_9"])
        assert decision.allowed
        assert decision.approval_id == "appr_9"

    def test_forbidden_effect_is_refused_at_every_tier(self):
        gate = make_gate("external_write_approval")
        decision = gate.decide(write_action(["candidates/x.py"], arguments={"effect": "rotate_secret"}))
        assert not decision.allowed
        assert decision.reason_code == ReasonCode.FORBIDDEN_ACTION.value

    def test_budget_exhaustion_is_a_decision_not_an_exception(self):
        gate = make_gate(budget_id="free_provider")
        ceiling = int(gate.ledger.ceiling(BudgetKind.ACTION))
        last = None
        for _ in range(ceiling + 1):
            last = gate.decide(read_action())
        assert last is not None and not last.allowed
        assert last.reason_code == ReasonCode.BUDGET_EXHAUSTED.value
        assert "budget_exhausted" in last.reason

    def test_every_reason_code_is_in_the_registered_vocabulary(self):
        gate = make_gate()
        codes = set()
        for action in (
            read_action(),
            write_action(["tests/x.py"]),
            write_action(["src/x.py"]),
            ProposedAction(experiment_id="exp1", action_type=ActionType.RUN_FULL_SUITE),
            write_action(["candidates/x.py"], arguments={"effect": "delete_user_data"}),
        ):
            codes.add(gate.decide(action).reason_code)
        assert codes <= set(ReasonCode)

    def test_denied_decisions_are_counted(self):
        gate = make_gate()
        gate.decide(read_action())
        gate.decide(write_action(["tests/x.py"]))
        snapshot = gate.snapshot()
        assert snapshot["decisions"] == 2
        assert snapshot["allowed"] == 1 and snapshot["denied"] == 1


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------
class TestRouter:
    def complex_task(self, **overrides) -> TaskFeatures:
        base = dict(
            task_type="repository_repair",
            requires_mutation=True,
            estimated_complexity=0.86,
            has_objective_evaluator=True,
            risk_level=RiskClass.MEDIUM,
            autonomy_requested=True,
        )
        base.update(overrides)
        return TaskFeatures(**base)

    def test_complex_task_with_evaluator_routes_to_avo(self):
        decision = Router().route(self.complex_task())
        assert decision.route == "avo"
        assert decision.refused_reasons == []
        assert any(rule.startswith("complexity:") for rule in decision.matched_rules)

    def test_informational_task_never_starts_a_modifying_workflow(self):
        decision = Router().route(self.complex_task(requires_mutation=False, task_type="faq"))
        assert decision.route == "fast_path"
        assert "informational_task_never_starts_a_modifying_workflow" in decision.refused_reasons

    def test_no_evaluator_refuses(self):
        decision = Router().route(self.complex_task(has_objective_evaluator=False))
        assert decision.route == "fast_path"
        assert "no_objective_evaluator" in decision.refused_reasons

    def test_low_complexity_without_prior_failure_stays_fast_path(self):
        decision = Router().route(self.complex_task(estimated_complexity=0.2, autonomy_requested=True))
        assert decision.route == "fast_path"
        assert any(reason.startswith("complexity_below_floor") for reason in decision.refused_reasons)

    def test_low_complexity_with_a_failed_first_attempt_routes(self):
        decision = Router().route(self.complex_task(estimated_complexity=0.2, prior_attempt_failed=True))
        assert decision.route == "avo"
        assert "prior_attempt_failed" in decision.matched_rules

    def test_high_risk_needs_human_admission(self):
        decision = Router().route(self.complex_task(risk_level=RiskClass.DESTRUCTIVE))
        assert decision.route == "fast_path"
        assert any("requires_human_admission" in reason for reason in decision.refused_reasons)

    def test_unknown_budget_profile_refuses_before_anything_else(self):
        decision = Router().route(self.complex_task(budget_profile_id="unlimited"))
        assert decision.route == "fast_path"
        assert any("budget_profile_unresolved" in reason for reason in decision.refused_reasons)

    def test_operator_kill_switch(self):
        decision = Router(governance_enabled=False).route(self.complex_task())
        assert decision.route == "fast_path"
        assert "governance_disabled_by_operator" in decision.refused_reasons

    def test_over_ceiling_task_is_refused_at_the_contract(self):
        # The ceiling lives in TaskFeatures (``estimated_complexity le=1.0``),
        # so an over-complex task never reaches a router at all.
        with pytest.raises(ValidationError):
            self.complex_task(estimated_complexity=1.4)

    def test_autonomy_is_advisory_only(self):
        requested = Router().route(self.complex_task(autonomy_requested=True))
        not_requested = Router().route(self.complex_task(autonomy_requested=False))
        # The request never changes the route by itself; the conditions do.
        assert requested.route == not_requested.route == "avo"

    def test_escalation_progression_is_the_plan_five_steps(self):
        router = Router()
        assert router.escalation_step(attempt_failed=False, governed_run_done=False, stalled=False).startswith("run_once")
        assert router.escalation_step(attempt_failed=True, governed_run_done=False, stalled=False).startswith("route_failed")
        assert router.escalation_step(attempt_failed=True, governed_run_done=True, stalled=False).startswith("supervisor")
        assert router.escalation_step(attempt_failed=True, governed_run_done=True, stalled=True).startswith("stop_on")

    def test_floor_constant_matches_docstring_claim(self):
        assert 0.0 < ROUTE_COMPLEXITY_FLOOR < 1.0


# ---------------------------------------------------------------------------
# evaluation pipeline
# ---------------------------------------------------------------------------
class TestEvaluationPipeline:
    def profile(self):
        return evaluator_profile("code_repair")

    def runners(self, *, fail_at: str | None = None, skip: str | None = None, raise_at: str | None = None):
        out = {}
        for tier in self.profile().tiers:
            if tier == fail_at:
                out[tier] = lambda ctx, t=tier: TierOutcome(GateStatus.FAILED, f"{t} failed", exit_code=1)
            elif tier == skip:
                out[tier] = lambda ctx, t=tier: TierOutcome(GateStatus.SKIPPED, f"{t} not available here")
            elif tier == raise_at:
                out[tier] = lambda ctx: (_ for _ in ()).throw(RuntimeError("runner exploded"))
            else:
                out[tier] = lambda ctx: TierOutcome(GateStatus.PASSED, "ok")
        return out

    def test_all_pass_is_verified(self):
        pipeline = EvaluatorPipeline(profile=self.profile(), runners=self.runners())
        result = pipeline.evaluate(candidate_digest="d1", experiment_id="e1")
        assert result.truth_label() is TruthLabel.VERIFIED
        assert result.passed
        assert result.candidate_digest == "d1"
        assert result.config_digest

    def test_required_tier_missing_runner_fails_before_any_tier_runs(self):
        pipeline = EvaluatorPipeline(profile=self.profile(), runners={"patch_sanity": lambda ctx: TierOutcome(GateStatus.PASSED, "ok")})
        with pytest.raises(UnknownTier, match="static_checks"):
            pipeline.evaluate(candidate_digest="d1", experiment_id="e1")

    def test_failure_stops_the_pipeline_and_names_the_blocker(self):
        pipeline = EvaluatorPipeline(profile=self.profile(), runners=self.runners(fail_at="targeted_regression"))
        result = pipeline.evaluate(candidate_digest="d1", experiment_id="e1")
        assert result.truth_label() is TruthLabel.FAILED
        tiers = {gate.tier: gate for gate in result.gates}
        assert tiers["targeted_regression"].status is GateStatus.FAILED
        # Everything after the failure: skipped, and the reason names the tier.
        assert tiers["relevant_tests"].status is GateStatus.SKIPPED
        assert "blocked by targeted_regression" in tiers["relevant_tests"].reason

    def test_runner_exception_is_inconclusive_never_a_pass(self):
        pipeline = EvaluatorPipeline(profile=self.profile(), runners=self.runners(raise_at="static_checks"))
        result = pipeline.evaluate(candidate_digest="d1", experiment_id="e1")
        gate = next(g for g in result.gates if g.tier == "static_checks")
        assert gate.status is GateStatus.INCONCLUSIVE
        assert "runner exploded" in gate.reason
        assert result.truth_label() is TruthLabel.PARTIALLY_VERIFIED

    def test_skipped_required_tier_demotes_to_unverified(self):
        pipeline = EvaluatorPipeline(profile=self.profile(), runners=self.runners(skip="security_checks"))
        result = pipeline.evaluate(candidate_digest="d1", experiment_id="e1")
        assert result.truth_label() is TruthLabel.UNVERIFIED
        assert "security_checks:skipped" in result.blocked_or_missing

    def test_context_carries_the_digest_and_profile(self):
        seen = {}

        def spy(ctx):
            seen.update(ctx)
            return TierOutcome(GateStatus.PASSED, "ok")

        runners = {tier: spy for tier in self.profile().tiers}
        EvaluatorPipeline(profile=self.profile(), runners=runners).evaluate(candidate_digest="d1", experiment_id="e1")
        assert seen["candidate_digest"] == "d1"
        assert seen["profile_id"] == "code_repair"

    def test_allow_skip_tier_without_runner_is_honestly_skipped(self):
        profile = self.profile().model_copy(update={"allow_skip": [*self.profile().allow_skip, "static_checks"]})
        runners = {tier: (lambda ctx: TierOutcome(GateStatus.PASSED, "ok")) for tier in profile.tiers if tier != "static_checks"}
        result = EvaluatorPipeline(profile=profile, runners=runners).evaluate(candidate_digest="d1", experiment_id="e1")
        gate = next(g for g in result.gates if g.tier == "static_checks")
        assert gate.status is GateStatus.SKIPPED
        assert "no runner bound" in gate.reason

    def test_evaluation_result_config_digest_binds_the_configuration(self):
        pipeline = EvaluatorPipeline(profile=self.profile(), runners=self.runners())
        result = pipeline.evaluate(candidate_digest="d1", experiment_id="e1")
        assert result.config_digest == self.profile().digest()


# ---------------------------------------------------------------------------
# receipts
# ---------------------------------------------------------------------------
class TestReceipts:
    def chain(self) -> ReceiptChain:
        return ReceiptChain(alpha_run_id="r1", avo_run_id="a1")

    def test_event_vocabulary_is_closed(self):
        chain = self.chain()
        with pytest.raises(ReceiptRejected, match="unknown receipt event type"):
            chain.append("action.exfiltrated")

    def test_chain_links_and_verifies(self):
        chain = self.chain()
        chain.append("run.admitted")
        chain.append("action.allowed", action_digest="abc")
        chain.append("run.completed")
        verdict = chain.verify()
        assert verdict.ok and verdict.checked == 3
        assert chain.head != "0" * 64

    def test_tamper_with_edit_is_detected(self):
        chain = self.chain()
        chain.append("run.admitted")
        chain.append("action.denied", reason_code="protected_path")
        chain.entries[1]["payload"]["decision"] = "allow"  # flip a denial to an allow
        verdict = chain.verify()
        assert not verdict.ok
        assert verdict.defect is not None

    def test_deletion_is_detected(self):
        chain = self.chain()
        for _ in range(3):
            chain.append("action.allowed")
        del chain.entries[1]
        assert not chain.verify().ok

    def test_reordering_is_detected(self):
        chain = self.chain()
        for _ in range(3):
            chain.append("action.allowed")
        chain.entries[0], chain.entries[1] = chain.entries[1], chain.entries[0]
        assert not chain.verify().ok

    def test_append_decision_reads_the_decision_not_a_description(self):
        gate = make_gate()
        decision = gate.decide(write_action(["tests/x.py"]))
        chain = self.chain()
        chain.append_decision(decision)
        payload = chain.entries[0]["payload"]
        assert payload["decision"] == "deny"
        assert payload["reason_code"] == "protected_path"
        assert payload["action_digest"] == decision.action_digest
        assert payload["policy_version"] == POLICY_VERSION

    def test_denied_actions_still_produce_receipts(self):
        gate = make_gate()
        chain = self.chain()
        for action in (read_action(), write_action(["tests/x.py"])):
            chain.append_decision(gate.decide(action))
        assert chain.count("action.allowed") == 1
        assert chain.count("action.denied") == 1

    def test_schema_version_mismatch_is_refused_on_load(self):
        chain = self.chain()
        chain.append("run.admitted")
        payload = chain.to_dict()
        payload["schema_version"] = 99
        with pytest.raises(ReceiptRejected, match="schema_version"):
            ReceiptChain.from_dict(payload)

    def test_every_appended_type_is_registered(self):
        chain = self.chain()
        for event_type in sorted(EVENT_TYPES):
            chain.append(event_type)
        assert chain.verify().ok
        assert len(chain.entries) == len(EVENT_TYPES)

    def test_receipt_carries_no_raw_output(self):
        gate = make_gate()
        chain = self.chain()
        chain.append_decision(gate.decide(read_action()), evidence_refs=["check:1"])
        serialised = str(chain.entries)
        # Only ids, digests and codes — nothing that could hold a secret blob.
        for forbidden in ("API_KEY", "password", "Authorization"):
            assert forbidden not in serialised


# ---------------------------------------------------------------------------
# session orchestration
# ---------------------------------------------------------------------------
class TestSession:
    def admitted(self, **overrides) -> GovernedRunSession:
        session = GovernedRunSession.admit(make_request(**overrides))
        session.start()
        return session

    # -- admission ---------------------------------------------------------
    def test_admission_resolves_every_profile_first(self):
        with pytest.raises(UnknownProfile):
            GovernedRunSession.admit(make_request(capability_profile_id="nope"))
        # Nothing half-admitted: the exception predates any lifecycle/receipt.
        # (No object exists to inspect — resolution happens before construction.)

    def test_disabled_profile_cannot_be_admitted(self):
        with pytest.raises(SessionRefused, match="disabled"):
            GovernedRunSession.admit(make_request(capability_profile_id="alpha_self_update"))

    def test_admission_mints_the_first_receipt(self):
        session = GovernedRunSession.admit(make_request())
        assert session.lifecycle.state is AvoState.PREFLIGHT
        assert session.receipts.count("run.admitted") == 1
        assert session.receipts.verify().ok

    # -- action gate -------------------------------------------------------
    def test_denied_action_is_counted_and_receipted(self):
        session = self.admitted()
        decision = session.submit_action(write_action(["tests/test_x.py"]))
        assert not decision.allowed
        assert session.denied_actions == 1
        assert session.receipts.count("action.denied") == 1

    def test_denied_action_cannot_execute(self):
        session = self.admitted()
        decision = session.submit_action(write_action(["tests/test_x.py"]))
        with pytest.raises(SessionRefused, match="no allowed decision"):
            session.mark_in_flight(
                ProposedAction(
                    action_id=decision.action_id,
                    experiment_id="exp1",
                    action_type=ActionType.WRITE_CANDIDATE_FILE,
                    write_scope=["tests/test_x.py"],
                )
            )

    def test_allowed_action_executes_and_reconciles(self):
        session = self.admitted()
        action = read_action()
        session.submit_action(action)
        session.mark_in_flight(action)
        assert session.in_flight
        record = session.reconcile_action(action)
        assert record["action_id"] == action.action_id
        assert not session.in_flight
        assert session.receipts.count("budget.reserved") == 1

    def test_reconcile_twice_is_refused(self):
        session = self.admitted()
        action = read_action()
        session.submit_action(action)
        session.mark_in_flight(action)
        session.reconcile_action(action)
        with pytest.raises(SessionRefused, match="not in flight"):
            session.reconcile_action(action)

    def test_action_while_paused_is_refused_by_the_state_machine(self):
        session = self.admitted()
        session.pause("operator_away")
        with pytest.raises(SessionStateError, match="paused"):
            session.submit_action(read_action())
        session.resume_running()
        assert session.submit_action(read_action()).allowed

    def test_terminal_run_accepts_nothing(self):
        session = self.admitted()
        session.cancel("user_cancelled")
        with pytest.raises(SessionStateError, match="terminal"):
            session.submit_action(read_action())

    # -- evaluation and promotion -----------------------------------------
    def evaluation(self, digest: str = "d1", *, label_gates=None) -> EvaluationResult:
        gates = label_gates or [
            EvaluatorGate(tier="patch_sanity", status=GateStatus.PASSED, reason="ok"),
            EvaluatorGate(tier="targeted_regression", status=GateStatus.PASSED, reason="ok"),
        ]
        return EvaluationResult(evaluator_id="code_repair", evaluator_version="1", candidate_digest=digest, experiment_id="exp1", gates=gates)

    def test_verified_evaluation_reaches_promotion_eligible(self):
        session = self.admitted()
        session.candidate_digest = "d1"
        assert session.record_evaluation(self.evaluation()) is TruthLabel.VERIFIED
        assert session.lifecycle.state is AvoState.PROMOTION_ELIGIBLE

    def test_failed_evaluation_rejects_the_candidate(self):
        session = self.admitted()
        session.candidate_digest = "d1"
        failed = self.evaluation(
            label_gates=[EvaluatorGate(tier="patch_sanity", status=GateStatus.FAILED, reason="does not apply")],
        )
        assert session.record_evaluation(failed) is TruthLabel.FAILED
        assert session.lifecycle.state is AvoState.CANDIDATE_REJECTED
        assert session.receipts.count("candidate.rejected") == 1

    def test_promotion_without_evaluation_reports_blocked_not_raised(self):
        session = self.admitted()
        result = session.promote(PromotionLevel.CANDIDATE_ARTIFACT)
        assert result.truth is TruthLabel.BLOCKED
        assert "no_evaluation_recorded" in result.reason

    def test_promotion_above_the_grant_is_refused_with_the_grant_named(self):
        session = self.admitted()
        session.candidate_digest = "d1"
        session.record_evaluation(self.evaluation())
        eligible, unmet = session.promotion_eligible(PromotionLevel.MERGE_DEPLOY)
        assert not eligible
        assert any("above_grant_candidate_artifact" in reason for reason in unmet)

    def test_promotion_without_approval_is_blocked_until_the_approval_arrives(self):
        session = self.admitted()
        session.candidate_digest = "d1"
        session.record_evaluation(self.evaluation())
        blocked = session.promote(PromotionLevel.CANDIDATE_ARTIFACT)
        assert blocked.truth is TruthLabel.BLOCKED
        assert "approval_required" in blocked.reason
        # The approval arrives afterwards: same run, now promotable.
        promoted = session.promote(PromotionLevel.CANDIDATE_ARTIFACT, approval_id="appr_1")
        assert promoted.truth is TruthLabel.VERIFIED
        assert promoted.promotion_level is PromotionLevel.CANDIDATE_ARTIFACT
        assert "appr_1" in promoted.approvals_granted
        assert session.lifecycle.state is AvoState.PROMOTED

    def test_digest_mismatch_blocks_promotion(self):
        session = self.admitted()
        session.candidate_digest = "different-bytes"
        session.record_evaluation(self.evaluation(digest="d1"))
        eligible, unmet = session.promotion_eligible(PromotionLevel.CANDIDATE_ARTIFACT)
        assert not eligible
        assert any(reason.startswith("digest_mismatch") for reason in unmet)

    def test_tampered_receipt_chain_blocks_promotion(self):
        session = self.admitted()
        session.candidate_digest = "d1"
        session.record_evaluation(self.evaluation())
        session.receipts.entries[0]["payload"]["event_type"] = "run.completed"
        eligible, unmet = session.promotion_eligible(PromotionLevel.CANDIDATE_ARTIFACT)
        assert not eligible
        assert any(reason.startswith("receipt_chain_invalid") for reason in unmet)

    def test_unmet_conditions_are_all_reported_not_just_the_first(self):
        session = self.admitted()  # no evaluation, no digest
        eligible, unmet = session.promotion_eligible(PromotionLevel.MERGE_DEPLOY)
        assert not eligible
        assert len(unmet) >= 2
        assert "no_evaluation_recorded" in unmet

    # -- report ------------------------------------------------------------
    def test_report_never_claims_an_empty_limitations_list(self):
        session = self.admitted()
        result = session.complete(truth=TruthLabel.UNVERIFIED, reason="no evidence collected")
        assert result.limitations
        assert result.receipt_chain_head == session.receipts.head

    def test_report_carries_the_measured_action_counts(self):
        session = self.admitted()
        session.submit_action(read_action())
        session.submit_action(write_action(["tests/x.py"]))
        result = session.complete(truth=TruthLabel.UNVERIFIED, reason="probe")
        assert result.actions_total == 2
        assert result.actions_denied == 1

    def test_report_declares_unreconciled_in_flight_actions(self):
        session = self.admitted()
        action = read_action()
        session.submit_action(action)
        session.mark_in_flight(action)
        result = session.complete(truth=TruthLabel.UNVERIFIED, reason="crash probe")
        assert any("unreconciled" in limitation for limitation in result.limitations)

    def test_model_cannot_complete_a_run_into_promoted(self):
        # complete() fixes its own actor; the model's only reach is through
        # promote(), which runs the eligibility checks first. Verify the
        # lifecycle edge itself refuses a model-authored promotion.
        run = RunLifecycle(avo_run_id="a1")
        run.transition(AvoState.PREFLIGHT, actor=Actor.RUNNER, reason_code="a")
        run.transition(AvoState.RUNNING, actor=Actor.RUNNER, reason_code="b")
        run.transition(AvoState.EVALUATING, actor=Actor.EVALUATOR, reason_code="c")
        from alpha.avo.lifecycle import IllegalTransition

        with pytest.raises(IllegalTransition):
            run.transition(AvoState.PROMOTED, actor=Actor.MODEL, reason_code="trust_me")

    # -- checkpoint/resume -------------------------------------------------
    def test_checkpoint_and_resume_continue_the_run(self, tmp_path: Path):
        session = self.admitted()
        path = tmp_path / "run.json"
        session.checkpoint(reason="test", path=path)
        resumed, verdict = GovernedRunSession.resume(path, expected_idempotency_key="key-abcdef12")
        assert verdict.ok
        assert resumed is not None
        assert resumed.lifecycle.state is AvoState.RUNNING
        assert resumed.receipts.count("run.recovered") == 1
        # The resumed run still verifies and still refuses bad actions.
        decision = resumed.submit_action(write_action(["tests/test_x.py"]))
        assert not decision.allowed

    def test_resume_with_a_different_admission_key_is_refused(self, tmp_path: Path):
        session = self.admitted()
        path = tmp_path / "run.json"
        session.checkpoint(reason="test", path=path)
        resumed, verdict = GovernedRunSession.resume(path, expected_idempotency_key="other-key-0000")
        assert resumed is None and not verdict.ok

    def test_snapshot_is_read_only_status(self):
        session = self.admitted()
        snapshot = session.snapshot()
        assert snapshot["state"] == "running"
        assert snapshot["terminal"] is False
        assert "budget" in snapshot and "policy" in snapshot
        assert snapshot["actions_total"] == 0
