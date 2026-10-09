"""Governed AVO layer: contracts, lifecycle, budgets, profiles, paths.

The governance layer added on top of the existing ``alpha.avo`` decision
machinery (see the plan in
``ALPHA_GOVERNED_AUTONOMOUS_VARIATION_ENGINE_PLAN.md``). These tests pin the
*refusals*: every rule here exists to stop something, so a passing suite is the
proof that the stops are where they were put.

Deliberately offline and dependency-free: the governance layer is pure control
state, and a test that needs a model or a filesystem beyond ``tmp_path`` is
testing something else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from alpha.avo.budgets import (
    BudgetExceeded,
    BudgetKind,
    BudgetLedger,
    exhaustion_reason,
    reservations_for_action,
)
from alpha.avo.checkpointing import (
    CHECKPOINT_SCHEMA_VERSION,
    Checkpoint,
    load_checkpoint,
    write_checkpoint,
)
from alpha.avo.contracts import (
    SCHEMA_VERSION,
    ActionType,
    AvoRunRequest,
    EvaluationResult,
    EvaluatorGate,
    ExperimentSpec,
    GateStatus,
    PromotionLevel,
    ProposedAction,
    TruthLabel,
    WorkspaceLocator,
    action_digest,
    workspace_digest,
)
from alpha.avo.lifecycle import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    Actor,
    AvoState,
    IllegalTransition,
    RunLifecycle,
)
from alpha.avo.paths import PathRefused, assert_within_root, is_protected, matches_any, normalize_relative
from alpha.avo.profiles import (
    BUDGET_PROFILES,
    CAPABILITY_PROFILES,
    EVALUATOR_PROFILES,
    UnknownProfile,
    approval_policy,
    budget_profile,
    capability_profile,
    evaluator_profile,
)
from alpha.avo.receipts import ReceiptChain


# ---------------------------------------------------------------------------
# contracts
# ---------------------------------------------------------------------------
class TestContracts:
    def test_every_model_is_extra_forbid(self):
        """An unknown field is rejected, not ignored — on every boundary type."""
        with pytest.raises(ValidationError):
            AvoRunRequest(
                alpha_run_id="r",
                task_id="t",
                task_description="d",
                workspace=WorkspaceLocator(project_id="p", relative_path="w"),
                idempotency_key="0123456789",
                experiment=ExperimentSpec(hypothesis="h"),
                surprise_field="x",  # type: ignore[call-arg]
            )
        with pytest.raises(ValidationError):
            ProposedAction(experiment_id="e", action_type=ActionType.READ_FILE, surprise="x")  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            ExperimentSpec(hypothesis="h", surprise="x")  # type: ignore[call-arg]

    def test_schema_version_mismatch_is_refused_by_name(self):
        with pytest.raises(ValidationError, match="schema_version"):
            AvoRunRequest(
                alpha_run_id="r",
                task_id="t",
                task_description="d",
                workspace=WorkspaceLocator(project_id="p", relative_path="w"),
                idempotency_key="0123456789",
                experiment=ExperimentSpec(hypothesis="h"),
                schema_version=SCHEMA_VERSION + 99,
            )

    def test_scope_entries_may_not_traverse(self):
        with pytest.raises(ValidationError, match="traversal"):
            ProposedAction(experiment_id="e", action_type=ActionType.READ_FILE, read_scope=["../etc/passwd"])
        with pytest.raises(ValidationError, match="traversal"):
            ProposedAction(experiment_id="e", action_type=ActionType.READ_FILE, read_scope=["/etc/passwd"])

    def test_workspace_must_be_relative(self):
        with pytest.raises(ValidationError):
            WorkspaceLocator(project_id="p", relative_path="/absolute/path")
        with pytest.raises(ValidationError):
            WorkspaceLocator(project_id="p", relative_path="../up")

    def test_action_digest_is_stable_and_content_bound(self):
        # Same action id + same content = same digest. The id is part of the
        # action's identity (it is a UUID per instance), so two *separate*
        # actions with identical fields are two actions and digest differently.
        action = ProposedAction(action_id="act_fixed", experiment_id="e", action_type=ActionType.READ_FILE, read_scope=["a.py"])
        same = ProposedAction(action_id="act_fixed", experiment_id="e", action_type=ActionType.READ_FILE, read_scope=["a.py"])
        other = ProposedAction(action_id="act_fixed", experiment_id="e", action_type=ActionType.READ_FILE, read_scope=["b.py"])
        assert action.digest == same.digest
        assert action.digest != other.digest
        # Field order must not matter: the digest is over canonical JSON.
        assert action_digest(action.model_dump(mode="json")) == action_digest(same.model_dump(mode="json"))

    def test_workspace_digest_covers_paths_and_bytes(self):
        base = {"a.py": b"one", "b.py": b"two"}
        assert workspace_digest(base) == workspace_digest({"b.py": b"two", "a.py": b"one"})
        assert workspace_digest(base) != workspace_digest({"a.py": b"ONE", "b.py": b"two"})
        assert workspace_digest(base) != workspace_digest({"a.py": b"one", "b.py": b"two", "c.py": b""})

    def test_promotion_levels_are_ordered(self):
        assert PromotionLevel.REPORT_ONLY.at_most(PromotionLevel.MERGE_DEPLOY)
        assert not PromotionLevel.MERGE_DEPLOY.at_most(PromotionLevel.DRAFT_PR)
        assert PromotionLevel.CANDIDATE_ARTIFACT.rank < PromotionLevel.APPROVED_COMMIT.rank

    def test_truth_label_never_conflates_skipped_with_passed(self):
        def result(gates: list[EvaluatorGate]) -> EvaluationResult:
            return EvaluationResult(evaluator_id="e", evaluator_version="1", gates=gates)

        required_pass = EvaluatorGate(tier="targeted_regression", status=GateStatus.PASSED)
        required_fail = EvaluatorGate(tier="targeted_regression", status=GateStatus.FAILED)
        required_skip = EvaluatorGate(tier="targeted_regression", status=GateStatus.SKIPPED)
        optional_pass = EvaluatorGate(tier="full_suite", status=GateStatus.PASSED)
        optional_skip = EvaluatorGate(tier="full_suite", status=GateStatus.SKIPPED)

        assert result([required_pass, optional_pass]).truth_label() is TruthLabel.VERIFIED
        assert result([required_fail]).truth_label() is TruthLabel.FAILED
        # A required skip demotes to UNVERIFIED, not VERIFIED.
        assert result([required_skip]).truth_label() is TruthLabel.UNVERIFIED
        # An optional skip is PARTIALLY_VERIFIED: we know what we did not check.
        assert result([required_pass, optional_skip]).truth_label() is TruthLabel.PARTIALLY_VERIFIED
        # No gates at all is UNVERIFIED, never VERIFIED.
        assert result([]).truth_label() is TruthLabel.UNVERIFIED

    def test_blocked_or_missing_names_every_non_pass(self):
        ev = EvaluationResult(
            evaluator_id="e",
            evaluator_version="1",
            gates=[
                EvaluatorGate(tier="a", status=GateStatus.PASSED),
                EvaluatorGate(tier="b", status=GateStatus.SKIPPED),
                EvaluatorGate(tier="c", status=GateStatus.INCONCLUSIVE),
            ],
        )
        assert ev.blocked_or_missing == ["b:skipped", "c:inconclusive"]

    def test_evaluation_result_carries_truth_in_serialised_form(self):
        ev = EvaluationResult(evaluator_id="e", evaluator_version="1", gates=[])
        assert ev.to_dict()["truth_label"] == TruthLabel.UNVERIFIED.value


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------
class TestLifecycle:
    def test_legal_path_admission_to_running(self):
        run = RunLifecycle(avo_run_id="a1")
        run.transition(AvoState.PREFLIGHT, actor=Actor.RUNNER, reason_code="admitted")
        run.transition(AvoState.RUNNING, actor=Actor.RUNNER, reason_code="started")
        assert run.active and run.can_execute_actions()

    def test_illegal_edge_is_refused_not_coerced(self):
        run = RunLifecycle()
        with pytest.raises(IllegalTransition, match="lifecycle graph does not contain that edge"):
            run.transition(AvoState.PROMOTED, actor=Actor.OPERATOR, reason_code="nope")

    def test_model_may_never_enter_a_terminal_state(self):
        run = RunLifecycle()
        run.transition(AvoState.PREFLIGHT, actor=Actor.RUNNER, reason_code="admitted")
        run.transition(AvoState.RUNNING, actor=Actor.RUNNER, reason_code="started")
        with pytest.raises(IllegalTransition):
            run.transition(AvoState.FAILED, actor=Actor.MODEL, reason_code="model_says_so")
        with pytest.raises(IllegalTransition):
            run.transition(AvoState.CANCELLED, actor=Actor.MODEL, reason_code="model_says_so")
        # ... and equally may not *leave* one: terminal states have no edges.
        run2 = RunLifecycle(state=AvoState.FAILED)
        for actor in Actor:
            with pytest.raises(IllegalTransition):
                run2.transition(AvoState.RUNNING, actor=actor, reason_code="retry")

    def test_terminal_states_have_no_outgoing_edges(self):
        for state in TERMINAL_STATES:
            run = RunLifecycle(state=state)
            assert run.allowed_targets() == ()
            assert run.terminal

    def test_active_states_are_exactly_where_actions_may_run(self):
        run = RunLifecycle()
        run.transition(AvoState.PREFLIGHT, actor=Actor.RUNNER, reason_code="a")
        run.transition(AvoState.RUNNING, actor=Actor.RUNNER, reason_code="b")
        assert run.state in ACTIVE_STATES
        run.transition(AvoState.PAUSED, actor=Actor.OPERATOR, reason_code="pause")
        assert run.state not in ACTIVE_STATES and not run.active

    def test_from_dict_refuses_unknown_state(self):
        with pytest.raises(ValueError, match="unknown lifecycle state"):
            RunLifecycle.from_dict({"state": "teleported", "history": []})

    def test_from_dict_refuses_state_that_disagrees_with_history(self):
        run = RunLifecycle(avo_run_id="a1")
        run.transition(AvoState.PREFLIGHT, actor=Actor.RUNNER, reason_code="admitted")
        payload = run.to_dict()
        payload["state"] = "running"  # not where the history ends
        with pytest.raises(ValueError, match="disagrees with its last transition"):
            RunLifecycle.from_dict(payload)

    def test_history_records_actor_and_correlation(self):
        run = RunLifecycle()
        entry = run.transition(AvoState.PREFLIGHT, actor=Actor.RUNNER, reason_code="admitted", trace="t-1")
        assert entry.prior_state is AvoState.QUEUED
        assert entry.actor is Actor.RUNNER
        assert entry.correlation_ids["trace"] == "t-1"
        assert run.reason_codes() and "admitted" in list(run.reason_codes())


# ---------------------------------------------------------------------------
# budgets
# ---------------------------------------------------------------------------
class TestBudgets:
    def ledger(self) -> BudgetLedger:
        return BudgetLedger(profile=budget_profile("paid_bounded"))

    def test_reservations_for_action_bound_both_scopes(self):
        res = reservations_for_action(BudgetKind.ACTION)
        assert BudgetKind.ACTION in res and BudgetKind.ACTION_PER_EXPERIMENT in res

    def test_reserve_refuses_over_ceiling_with_true_figures(self):
        ledger = self.ledger()
        ceiling = ledger.ceiling(BudgetKind.ACTION)
        assert ceiling is not None
        with pytest.raises(BudgetExceeded) as exc:
            for _ in range(int(ceiling) + 1):
                ledger.reserve(BudgetKind.ACTION)
        # The ledger reports prospective float figures; assert the true numbers
        # rather than an integer-shaped string they would never format as.
        assert exc.value.reason == exhaustion_reason("action", float(int(ceiling) + 1), ceiling)

    def test_release_returns_budget_and_is_not_a_charge(self):
        ledger = self.ledger()
        ledger.reserve(BudgetKind.ACTION, 2)
        ledger.release(BudgetKind.ACTION, 2)
        assert ledger.dimension(BudgetKind.ACTION).used == 0.0
        assert ledger.remaining(BudgetKind.ACTION) == ledger.ceiling(BudgetKind.ACTION)

    def test_reconcile_releases_before_charging(self):
        ledger = self.ledger()
        ledger.reserve(BudgetKind.ACTION, 3)
        ledger.reconcile(BudgetKind.ACTION, reserved=3, measured=1)
        dim = ledger.dimension(BudgetKind.ACTION)
        assert dim.reserved == 0.0 and dim.used == 1.0

    def test_reconcile_overrun_charges_measurement_not_reservation(self):
        ledger = self.ledger()
        ledger.reserve(BudgetKind.COST_USD, 0.10)
        ledger.reconcile(BudgetKind.COST_USD, reserved=0.10, measured=0.45)
        assert ledger.dimension(BudgetKind.COST_USD).used == pytest.approx(0.45)

    def test_per_experiment_budget_is_separate_per_experiment(self):
        ledger = self.ledger()
        per = ledger.ceiling(BudgetKind.ACTION_PER_EXPERIMENT)
        assert per is not None
        ledger.reserve(BudgetKind.ACTION_PER_EXPERIMENT, per, experiment_id="exp1")
        # exp1 is full ...
        with pytest.raises(BudgetExceeded):
            ledger.reserve(BudgetKind.ACTION_PER_EXPERIMENT, 1, experiment_id="exp1")
        # ... exp2 is untouched.
        ledger.reserve(BudgetKind.ACTION_PER_EXPERIMENT, 1, experiment_id="exp2")

    def test_retry_budget_bites_per_action_not_just_run_wide(self):
        res = reservations_for_action(BudgetKind.ACTION, 2.0)
        assert BudgetKind.ACTION_RETRY in res
        # A unit reservation (first attempt) draws no retry budget.
        assert BudgetKind.ACTION_RETRY not in reservations_for_action(BudgetKind.ACTION)

    def test_failure_signature_exhaustion(self):
        ledger = self.ledger()
        ceiling = ledger.ceiling(BudgetKind.FAILURE_SIGNATURE)
        assert ceiling is not None
        for _ in range(int(ceiling)):
            ledger.record_failure_signature("timeout-x")
        assert ledger.signature_exhausted("timeout-x")
        assert not ledger.signature_exhausted("different-failure")

    def test_unmeasurable_cost_ceiling_is_none_not_infinity(self):
        ledger = BudgetLedger(profile=budget_profile("local_low_resource"))
        assert ledger.ceiling(BudgetKind.COST_USD) is None
        snapshot = ledger.snapshot()
        assert snapshot["dimensions"]["cost_usd"]["ceiling"] is None
        assert snapshot["dimensions"]["cost_usd"]["ceiling_enforceable"] is False
        assert "cost_usd" in snapshot["unbounded_dimensions"]
        # None is a disclosed unmeasurable ceiling; it must never read as a
        # number a caller could compare against.
        assert snapshot["dimensions"]["cost_usd"]["remaining"] is None

    def test_exhausted_reason_is_the_first_ceiling_reached(self):
        ledger = self.ledger()
        ceiling = int(ledger.ceiling(BudgetKind.ACTION))  # type: ignore[arg-type]
        for _ in range(ceiling):
            ledger.reserve(BudgetKind.ACTION)
        assert ledger.is_exhausted(BudgetKind.ACTION)
        assert ledger.exhausted_reason is not None
        assert ledger.exhausted_reason.startswith("budget_exhausted: action")

    def test_unknown_budget_kind_is_refused(self):
        ledger = self.ledger()
        with pytest.raises(ValueError, match="unknown budget kind"):
            ledger.reserve("nonsense")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="unknown budget kind"):
            ledger.dimension("nonsense")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------
class TestProfiles:
    def test_the_five_plan_profiles_exist(self):
        for name in ("research_readonly", "repo_inspect", "repo_repair", "external_write_approval", "alpha_self_update"):
            assert name in CAPABILITY_PROFILES

    def test_self_update_is_present_and_disabled(self):
        profile = capability_profile("alpha_self_update")
        assert profile.enabled is False
        assert not profile.permits_action(ActionType.WRITE_CANDIDATE_FILE)

    def test_unknown_profile_never_defaults_silently(self):
        with pytest.raises(UnknownProfile, match="never default silently"):
            capability_profile("repo_repair_typo")
        with pytest.raises(UnknownProfile):
            budget_profile("unlimited")
        with pytest.raises(UnknownProfile):
            evaluator_profile("whatever")
        with pytest.raises(UnknownProfile):
            approval_policy("whatever")

    def test_default_deny_shape(self):
        readonly = capability_profile("research_readonly")
        assert readonly.write_paths == []
        assert not readonly.permits_action(ActionType.WRITE_CANDIDATE_FILE)
        assert readonly.permits_action(ActionType.READ_FILE)

    def test_repair_profile_cannot_write_outside_candidates(self):
        repair = capability_profile("repo_repair")
        assert repair.write_paths == ["candidates/**"]
        assert not repair.allow_auto_merge and not repair.allow_auto_deploy
        assert not repair.allow_policy_mutation and not repair.allow_evaluator_mutation
        assert repair.max_promotion_level is PromotionLevel.CANDIDATE_ARTIFACT

    def test_every_repair_protected_prefix_is_actually_protected(self):
        repair = capability_profile("repo_repair")
        for path in ("alpha/avo/policy.py", "tests/test_x.py", "config.yaml", ".github/workflows/ci.yml"):
            assert is_protected(path, repair.protected_paths), path

    def test_approval_policies_name_their_forbidden_floor(self):
        for policy_id in ("default_deny", "strict"):
            policy = approval_policy(policy_id)
            assert policy.default_deny
            assert "delete_user_data" in policy.forbidden_actions
            assert "rotate_secret" in policy.forbidden_actions

    def test_default_deny_requirement_table_covers_every_risk_class(self):
        policy = approval_policy("default_deny")
        assert policy.requirement_for("low") == "none"
        assert policy.requirement_for("medium") == "notify"
        assert policy.requirement_for("high") == "require"
        assert policy.requirement_for("destructive") == "require"

    def test_budget_profiles_have_cost_disclosure(self):
        for profile_id, profile in BUDGET_PROFILES.items():
            if profile.max_cost_usd is None or not profile.cost_is_enforceable:
                assert profile.cost_is_enforceable is False, profile_id

    def test_evaluator_profiles_declare_ordered_tiers(self):
        for profile_id, profile in EVALUATOR_PROFILES.items():
            assert profile.tiers, profile_id
            # Cheapest and most decisive first, per the plan's §10.1 order.
            assert profile.tiers[0] == "patch_sanity", profile_id
            assert "targeted_regression" in profile.tiers or profile_id == "research_only"

    def test_config_digest_is_a_function_of_config(self):
        original = evaluator_profile("code_repair")
        tampered = original.model_copy(update={"tiers": ["patch_sanity"]})
        assert original.digest() != tampered.digest()


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------
class TestPaths:
    def test_normalise_strips_dots_and_unifies_separators(self):
        assert normalize_relative("./a/b.py") == "a/b.py"
        assert normalize_relative("a\\b\\c.py") == "a/b/c.py"
        assert normalize_relative("a//b.py") == "a/b.py"

    @pytest.mark.parametrize(
        "bad",
        ["../x", "a/../../x", "/abs", "C:/abs", "\\\\server\\share", "~home", "http://evil/x", "", "   ", "a/\x00b"],
    )
    def test_normalise_refuses_every_escape_shape(self, bad: str):
        with pytest.raises(PathRefused):
            normalize_relative(bad)

    def test_directory_patterns_cover_their_subtree(self):
        # The regression the whole matcher exists for: a grant written as
        # ``candidates/**`` must match *under* candidates, not only the
        # literal directory name.
        assert matches_any("candidates/c1/x.py", ["candidates/**"]) == "candidates/**"
        assert matches_any("candidates/c1/x.py", ["candidates"]) == "candidates"
        assert matches_any("src/main.py", ["candidates/**"]) is None

    def test_glob_segments(self):
        assert matches_any("src/lib/a.py", ["src/**/*.py"]) is not None
        assert matches_any("src/a/b.py", ["src/*.py"]) is None  # * does not cross /

    def test_protected_returns_the_matching_pattern(self):
        matched = is_protected("alpha/avo/session.py", ["alpha/avo/**"])
        assert matched == "alpha/avo/**"
        assert is_protected("src/x.py", ["alpha/avo/**"]) is None

    def test_assert_within_root_refuses_escape(self, tmp_path: Path):
        inside = assert_within_root("sub/file.py", tmp_path)
        assert str(inside).replace("\\", "/").endswith("sub/file.py")
        with pytest.raises(PathRefused, match="traverses"):
            assert_within_root("../outside.py", tmp_path)
        # The root itself is not a valid write target.
        with pytest.raises(PathRefused, match="root itself"):
            assert_within_root(".", tmp_path)


# ---------------------------------------------------------------------------
# checkpoint integrity (write/load happy path and refusals)
# ---------------------------------------------------------------------------
class TestCheckpointing:
    def make(self) -> Checkpoint:
        lifecycle = RunLifecycle(avo_run_id="a1")
        lifecycle.transition(AvoState.PREFLIGHT, actor=Actor.RUNNER, reason_code="admitted")
        return Checkpoint(
            alpha_run_id="r1",
            avo_run_id="a1",
            task_id="t1",
            capability_profile_id="repo_repair",
            budget_profile_id="local_low_resource",
            evaluator_profile_id="code_repair",
            approval_policy_id="default_deny",
            lifecycle=lifecycle,
            ledger=BudgetLedger(profile=budget_profile("local_low_resource")),
            receipts=ReceiptChain(alpha_run_id="r1", avo_run_id="a1"),
            idempotency_key="key-0123456789",
        )

    def test_roundtrip(self, tmp_path: Path):
        checkpoint = self.make()
        target = write_checkpoint(checkpoint, tmp_path / "c.json")
        verdict = load_checkpoint(target, expected_idempotency_key="key-0123456789")
        assert verdict.ok, verdict.reasons
        assert verdict.checkpoint is not None
        assert verdict.checkpoint.lifecycle.state is AvoState.PREFLIGHT
        assert verdict.checkpoint.avo_run_id == "a1"

    def test_tampered_file_fails_integrity_and_stops_there(self, tmp_path: Path):
        checkpoint = self.make()
        target = write_checkpoint(checkpoint, tmp_path / "c.json")
        raw = target.read_text(encoding="utf-8")
        target.write_text(raw.replace('"preflight"', '"promoted"'), encoding="utf-8")
        verdict = load_checkpoint(target)
        assert not verdict.ok
        assert any("integrity" in reason for reason in verdict.reasons)
        assert verdict.checkpoint is None

    def test_wrong_idempotency_key_is_a_new_run_not_a_resume(self, tmp_path: Path):
        target = write_checkpoint(self.make(), tmp_path / "c.json")
        verdict = load_checkpoint(target, expected_idempotency_key="different-key-000")
        assert not verdict.ok
        assert any("idempotency" in reason for reason in verdict.reasons)

    def test_unknown_schema_version_is_refused_by_name(self, tmp_path: Path):
        target = write_checkpoint(self.make(), tmp_path / "c.json")
        raw = json.loads(target.read_text(encoding="utf-8"))
        raw["schema_version"] = CHECKPOINT_SCHEMA_VERSION + 1
        raw.pop("integrity", None)
        import hashlib

        from alpha.avo.contracts import canonical_json

        raw["integrity"] = hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()
        target.write_text(json.dumps(raw), encoding="utf-8")
        verdict = load_checkpoint(target)
        assert not verdict.ok
        assert any("schema_version" in reason for reason in verdict.reasons)

    def test_missing_file_is_a_refusal_not_an_exception(self, tmp_path: Path):
        verdict = load_checkpoint(tmp_path / "absent.json")
        assert not verdict.ok and verdict.checkpoint is None

    def test_disabled_capability_profile_cannot_be_resumed_into(self, tmp_path: Path):
        checkpoint = self.make()
        checkpoint.capability_profile_id = "alpha_self_update"
        target = write_checkpoint(checkpoint, tmp_path / "c.json")
        verdict = load_checkpoint(target)
        assert not verdict.ok
        assert any("disabled" in reason for reason in verdict.reasons)

    def test_unknown_profile_at_resume_is_a_refusal(self, tmp_path: Path):
        checkpoint = self.make()
        checkpoint.capability_profile_id = "vanished_profile"
        target = write_checkpoint(checkpoint, tmp_path / "c.json")
        verdict = load_checkpoint(target)
        assert not verdict.ok
        assert any("no longer resolves" in reason for reason in verdict.reasons)

    def test_write_is_atomic_no_staging_left_behind(self, tmp_path: Path):
        target = write_checkpoint(self.make(), tmp_path / "c.json")
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "c.json"]
        assert leftovers == []
        assert target.exists()
