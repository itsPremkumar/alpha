"""GRO runner: end-to-end phases with honest outcomes.

The runner is where every honesty rule has to survive contact with a real
group: a winner exists only when a candidate passes every *required* gate;
no declared criteria means an UNVERIFIED winner; an all-fail group ends as a
bounded repair round and then an explicit UNVERIFIED partial result — never
"the best of a bad lot, verified"; a verifier outage is UNKNOWN, never a pass;
final verification demotes, never promotes; every candidate gets an outcome;
and a run that could not be persisted says so.

Tests inject verifiers (or craft the deterministic checker's evidence shape)
so no model or network is ever touched.
"""

from __future__ import annotations

import threading
import time

import pytest

from alpha.gro.adapters import SyncCallableAdapter
from alpha.gro.config import GroConfig
from alpha.gro.models import (
    CandidateOutcome,
    CandidateStatus,
    GroBudget,
    GroRun,
    GroRunState,
    RiskTier,
    RunMode,
    TaskContract,
    TaskType,
)
from alpha.gro.runner import GroDisabledError, default_acceptance_verifier, report_for_run, run_with_gro
from alpha.gro.store import GroStore

GOAL = "Fix the parser bug in the tokenizer module"
CRITERIA = ("tests_passed: pytest",)


def _cfg(**overrides) -> GroConfig:
    base: dict = {
        "enabled": True,
        "default_mode": "group",
        "max_candidates_per_task": 3,
        "max_active_candidates_global": 1,
        "max_repair_rounds": 1,
        "early_stop_on_verified_success": True,
        "candidate_timeout_seconds": 30.0,
        "run_timeout_seconds": 120.0,
        "require_final_verification": True,
    }
    base.update(overrides)
    return GroConfig(**base)


def _completed(spec) -> CandidateOutcome:
    """A completed candidate carrying harness-measured evidence."""

    return CandidateOutcome(
        candidate_id=spec.candidate_id,
        status=CandidateStatus.COMPLETED,
        output_text=f"result from {spec.candidate_id}",
        duration_ms=40.0,
        bash_executions=[{"command": "pytest", "status": "success", "shell_persistent": False, "output_tail": "12 passed in 0.42s"}],
        artifacts=["report.md"],
    )


def _verdict_only(pass_ids: set[str]):
    """Injected verifier: criteria hold exactly for ``pass_ids`` candidates."""

    def verifier(contract, outcome):
        if not contract.acceptance_criteria:
            return None
        holds = outcome.candidate_id in pass_ids
        return {"leaves": [{"criterion": c, "checked": True, "holds": holds} for c in contract.acceptance_criteria]}

    return verifier


def _fresh_run(run_id: str, owner_id: str = "u") -> GroRun:
    contract = TaskContract(run_id=run_id, goal=GOAL, task_type=TaskType.CODING, risk_tier=RiskTier.ORDINARY)
    return GroRun.new(run_id=run_id, owner_id=owner_id, thread_id="", contract=contract, budget=GroBudget(), mode=RunMode.CANDIDATE_GROUP)


class TestVerifiedWinner:
    def test_winner_passes_every_required_gate_and_is_verified(self, tmp_path):
        store = GroStore(tmp_path)
        calls: list[str] = []

        def fn(spec):
            calls.append(spec.candidate_id)
            return _completed(spec)

        report = run_with_gro(
            GOAL,
            adapter=SyncCallableAdapter(fn),
            acceptance_criteria=CRITERIA,
            config=_cfg(),
            owner_id="user-1",
            store=store,
            verifier=_verdict_only({"cand_01"}),
        )

        assert report.outcome == "selected"
        assert report.state is GroRunState.PROMOTED
        assert report.winner_candidate_id == "cand_01"
        assert report.verified is True
        assert report.verified_criteria_count == 1
        assert report.persisted is True
        # Early stop: only the winner was ever dispatched.
        assert calls == ["cand_01"]
        markdown = report.markdown()
        assert "Verification: **passed**" in markdown
        assert "never started" in markdown
        # The persisted document reloads with the same terminal state.
        reloaded = store.load(report.run_id, owner_id="user-1")
        assert reloaded is not None and reloaded.state is GroRunState.PROMOTED

    def test_report_for_run_repeats_only_what_the_stored_run_proved(self, tmp_path):
        store = GroStore(tmp_path)
        report = run_with_gro(
            GOAL,
            adapter=SyncCallableAdapter(_completed),
            acceptance_criteria=CRITERIA,
            config=_cfg(),
            owner_id="user-1",
            store=store,
            verifier=_verdict_only({"cand_01"}),
        )
        reloaded = store.load(report.run_id, owner_id="user-1")
        rebuilt = report_for_run(reloaded)
        assert rebuilt.outcome == "selected"
        assert rebuilt.winner_candidate_id == "cand_01"
        assert rebuilt.verified is True
        assert rebuilt.persisted is True
        assert "GRO run" in rebuilt.markdown()

    def test_report_for_run_on_a_non_terminal_run_claims_no_winner(self):
        run = _fresh_run("gro_wip")
        for state in (GroRunState.NORMALIZED, GroRunState.POLICY_DECIDED, GroRunState.GROUP_CREATED, GroRunState.READY, GroRunState.RUNNING):
            run.transition(state, reason="still working")
        report = report_for_run(run)
        assert report.outcome == "in_progress"
        assert report.verified is False
        assert report.winner_candidate_id is None
        assert "no winner exists yet" in report.markdown()

    def test_no_criteria_winner_is_labelled_unverified(self):
        report = run_with_gro(
            "Say hello to the moon politely",
            adapter=SyncCallableAdapter(_completed),
            config=_cfg(default_mode="single"),
            owner_id="u",
        )
        assert report.outcome == "selected"
        assert report.state is GroRunState.PROMOTED
        assert report.verified is False
        assert "UNVERIFIED" in report.markdown()

    def test_candidate_count_is_bounded_by_the_operator_ceiling(self):
        report = run_with_gro(
            GOAL,
            adapter=SyncCallableAdapter(_completed),
            acceptance_criteria=CRITERIA,
            config=_cfg(),
            candidate_count=10,
            owner_id="u",
            verifier=_verdict_only({"cand_01"}),
        )
        assert len(report.run.candidate_specs) == 3
        assert "candidate_count_bounded_by_ceiling:3" in report.run.decision["reasons"]


class TestAllFail:
    def test_bounded_repair_round_then_unverified_partial_result(self, tmp_path):
        calls: list[str] = []

        def fn(spec):
            calls.append(spec.candidate_id)
            return _completed(spec)

        report = run_with_gro(
            GOAL,
            adapter=SyncCallableAdapter(fn),
            acceptance_criteria=CRITERIA,
            config=_cfg(),
            owner_id="u",
            store=GroStore(tmp_path),
            verifier=_verdict_only(set()),  # nobody passes
        )
        assert calls == ["cand_01", "cand_02", "cand_03", "cand_r1"]
        assert report.outcome == "partial_result"
        assert report.state is GroRunState.PARTIAL_RESULT
        assert report.run.repair_round == 1
        assert report.verified is False
        assert report.partial_candidate_id is not None
        assert "fabricate" in report.run.selection.reason
        markdown = report.markdown()
        assert "UNVERIFIED partial result" in markdown
        assert "gate_acceptance_criteria_hold_fail" in markdown
        event_types = [e["type"] for e in report.events]
        assert "repair.started" in event_types

    def test_verifier_outage_is_unknown_never_a_pass(self):
        def broken(contract, outcome):
            raise RuntimeError("checker down")

        report = run_with_gro(
            GOAL,
            adapter=SyncCallableAdapter(_completed),
            acceptance_criteria=CRITERIA,
            config=_cfg(),
            owner_id="u",
            verifier=broken,
        )
        assert report.outcome == "partial_result"
        assert report.verified is False
        evaluation = report.run.evaluations["cand_01"]
        assert "gate_acceptance_criteria_hold_unknown" in evaluation.reason_codes
        assert "gate_acceptance_criteria_hold_unknown" in report.markdown()

    def test_final_verification_demotes_the_provisional_winner(self):
        calls_per: dict[str, int] = {}

        def flaky(contract, outcome):
            count = calls_per.get(outcome.candidate_id, 0) + 1
            calls_per[outcome.candidate_id] = count
            holds = count == 1  # passes in the group wave, fails the final re-check
            return {"leaves": [{"criterion": c, "checked": True, "holds": holds} for c in contract.acceptance_criteria]}

        report = run_with_gro(
            GOAL,
            adapter=SyncCallableAdapter(_completed),
            acceptance_criteria=CRITERIA,
            config=_cfg(),
            owner_id="u",
            verifier=flaky,
        )
        assert calls_per["cand_01"] == 2  # group wave + final re-verification
        assert report.run.selection.outcome == "no_acceptable_candidate"
        assert report.run.selection.reason.startswith("final re-verification failed for cand_01")
        assert report.outcome == "partial_result"  # the completed attempt is returned, unverified
        assert report.state is GroRunState.PARTIAL_RESULT
        assert report.verified is False
        assert any(e["type"] == "selection.demoted" for e in report.events)

    def test_all_candidates_failing_execution_is_an_honest_failure(self):
        def boom(spec):
            raise RuntimeError("lane exploded")

        report = run_with_gro(
            GOAL,
            adapter=SyncCallableAdapter(boom),
            acceptance_criteria=CRITERIA,
            config=_cfg(),
            owner_id="u",
        )
        # Nothing completed, so nothing is repairable and no partial result exists.
        assert report.outcome == "failed"
        assert report.state is GroRunState.FAILED
        assert "fabricate" in report.error
        assert all(outcome.status is CandidateStatus.FAILED for outcome in report.run.outcomes.values())


class TestEveryCandidateGetsAnOutcome:
    def test_early_stop_records_never_started_candidates(self):
        report = run_with_gro(
            GOAL,
            adapter=SyncCallableAdapter(_completed),
            acceptance_criteria=CRITERIA,
            config=_cfg(),
            owner_id="u",
            verifier=_verdict_only({"cand_02"}),
        )
        assert report.run.outcomes["cand_02"].status is CandidateStatus.COMPLETED
        skipped = report.run.outcomes["cand_03"]
        assert skipped.status is CandidateStatus.NOT_STARTED
        assert "early stop" in skipped.error
        assert report.not_started_count == 1
        assert "never started" in report.markdown()

    def test_timeout_is_a_fence_and_a_late_result_is_discarded(self):
        finished = threading.Event()

        def slow(spec):
            time.sleep(1.0)
            finished.set()
            return "late result that must be discarded"

        report = run_with_gro(
            "Say hello to the moon politely",
            adapter=SyncCallableAdapter(slow),
            config=_cfg(default_mode="single", candidate_timeout_seconds=0.3),
            owner_id="u",
        )
        outcome = report.run.outcomes["cand_01"]
        assert outcome.status is CandidateStatus.TIMEOUT
        # The run reported the timeout honestly instead of adopting the late text.
        assert report.outcome == "failed"
        assert "fabricate" in report.error
        assert finished.wait(5.0)
        time.sleep(0.1)  # give a hypothetical (wrong) adoption a chance to land
        assert report.run.outcomes["cand_01"].status is CandidateStatus.TIMEOUT
        assert report.run.outcomes["cand_01"].output_text == ""


class TestApprovalParking:
    def test_external_side_effects_park_for_approval_and_run_nothing(self, tmp_path):
        calls: list[str] = []

        def fn(spec):
            calls.append(spec.candidate_id)
            return _completed(spec)

        report = run_with_gro(
            "deploy the service to production",
            adapter=SyncCallableAdapter(fn),
            config=_cfg(require_approval_for_external_side_effects=True),
            owner_id="u",
            store=GroStore(tmp_path),
        )
        assert report.outcome == "needs_human"
        assert report.state is GroRunState.NEEDS_HUMAN
        assert calls == []  # nothing executed
        assert report.verified is False
        assert report.persisted is True
        assert "Blocked on human approval" in report.markdown()
        assert any(e["type"] == "run.needs_human" for e in report.events)


class TestRefusals:
    def test_disabled_engine_refuses(self):
        with pytest.raises(GroDisabledError, match="disabled"):
            run_with_gro(GOAL, adapter=SyncCallableAdapter(_completed), config=GroConfig(enabled=False))

    def test_oversized_goal_is_refused_with_the_bound_named(self):
        with pytest.raises(ValueError, match="ceiling is 8000"):
            run_with_gro("x" * 9000, adapter=SyncCallableAdapter(_completed), config=_cfg())

    def test_too_many_criteria_are_refused_not_clamped(self):
        with pytest.raises(ValueError, match="refusing 51 acceptance criteria"):
            run_with_gro(GOAL, adapter=SyncCallableAdapter(_completed), acceptance_criteria=tuple(f"c{i}" for i in range(51)), config=_cfg())

    def test_oversized_criterion_is_refused(self):
        with pytest.raises(ValueError, match="ceiling is 2000"):
            run_with_gro(GOAL, adapter=SyncCallableAdapter(_completed), acceptance_criteria=("x" * 2500,), config=_cfg())

    def test_explicit_zero_candidates_is_refused(self):
        with pytest.raises(ValueError, match="candidate_count must be >= 1"):
            run_with_gro(GOAL, adapter=SyncCallableAdapter(_completed), config=_cfg(), candidate_count=0)

    def test_duplicate_strategy_ids_fail_the_run_loudly(self):
        report = run_with_gro(GOAL, adapter=SyncCallableAdapter(_completed), config=_cfg(), strategy_ids=("coding.test_first", "coding.test_first"), owner_id="u")
        assert report.outcome == "failed"
        assert "twice" in report.error
        assert report.state is GroRunState.FAILED

    def test_unknown_strategy_id_fails_the_run_loudly(self):
        report = run_with_gro(GOAL, adapter=SyncCallableAdapter(_completed), config=_cfg(), strategy_ids=("coding.nonexistent",), owner_id="u")
        assert report.outcome == "failed"
        assert "coding.nonexistent" in report.error

    def test_more_strategies_than_the_ceiling_fail_the_run_loudly(self):
        four = ("coding.minimal_patch", "coding.test_first", "coding.root_cause_first", "coding.alternate_design")
        report = run_with_gro(GOAL, adapter=SyncCallableAdapter(_completed), config=_cfg(), strategy_ids=four, owner_id="u")
        assert report.outcome == "failed"
        assert "ceiling is 3" in report.error


class TestEventJournal:
    def test_events_are_journalled_beside_the_document(self, tmp_path):
        store = GroStore(tmp_path)
        report = run_with_gro(
            GOAL,
            adapter=SyncCallableAdapter(_completed),
            acceptance_criteria=CRITERIA,
            config=_cfg(),
            owner_id="u",
            store=store,
            verifier=_verdict_only({"cand_01"}),
        )
        events = store.read_events(owner_id="u", run_id=report.run_id, limit=50)
        types = [e["type"] for e in events]
        assert types[0] == "run.started"
        assert types[-1] == "run.completed"
        assert store.is_degraded is False


class TestStoreHonesty:
    def test_absent_is_not_corrupt_and_owners_are_scoped(self, tmp_path):
        store = GroStore(tmp_path)
        assert store.load("gro_missing", owner_id="u") is None
        assert store.is_degraded is False

        run = _fresh_run("gro_owner")
        assert store.save(run) is True
        assert store.load("gro_owner", owner_id="u") is not None
        # A different owner's namespace does not contain this run.
        assert store.load("gro_owner", owner_id="someone-else") is None
        assert store.is_degraded is False

    def test_corrupt_document_is_disclosed_never_read_as_empty(self, tmp_path):
        store = GroStore(tmp_path)
        assert store.save(_fresh_run("gro_bad")) is True
        store._path("u", "gro_bad").write_text("{not json", encoding="utf-8")
        fresh = GroStore(tmp_path)
        assert fresh.load("gro_bad", owner_id="u") is None
        assert fresh.is_degraded is True
        assert fresh.load_error

    def test_unwritable_store_is_reported_not_claimed(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        store = GroStore(root=blocker)
        run = _fresh_run("gro_blocked")
        assert store.save(run) is False
        assert run.save_error

        report = run_with_gro(
            GOAL,
            adapter=SyncCallableAdapter(_completed),
            config=_cfg(default_mode="single"),
            owner_id="u",
            store=GroStore(root=blocker),
        )
        assert report.outcome == "selected"  # the work happened...
        assert report.persisted is False  # ...but durability is never claimed
        assert "could not be persisted" in report.markdown()


class TestDefaultVerifier:
    """The real deterministic acceptance checker (no model, no network)."""

    def test_no_criteria_short_circuits_without_importing_the_checker(self):
        contract = TaskContract(run_id="gro_dv", goal="say hello", task_type=TaskType.GENERAL, risk_tier=RiskTier.TRIVIAL)
        outcome = CandidateOutcome(candidate_id="c", status=CandidateStatus.COMPLETED, output_text="hello")
        assert default_acceptance_verifier(contract, outcome) is None

    def test_checker_demands_proven_evidence(self, tmp_path):
        contract = TaskContract(run_id="gro_dv", goal=GOAL, task_type=TaskType.CODING, risk_tier=RiskTier.ORDINARY, acceptance_criteria=CRITERIA)
        proven = CandidateOutcome(
            candidate_id="c1",
            status=CandidateStatus.COMPLETED,
            output_text="fixed",
            bash_executions=[{"command": "pytest", "status": "success", "shell_persistent": False, "output_tail": "12 passed in 0.42s"}],
        )
        bare = CandidateOutcome(candidate_id="c2", status=CandidateStatus.COMPLETED, output_text="I fixed it, tests pass")

        verdict = default_acceptance_verifier(contract, proven)
        assert verdict["leaves"][0]["checked"] is True and verdict["leaves"][0]["holds"] is True
        bare_verdict = default_acceptance_verifier(contract, bare)
        assert bare_verdict["leaves"][0]["checked"] is False  # a bare claim is never a check

        def fn(spec):
            return CandidateOutcome(
                candidate_id=spec.candidate_id,
                status=CandidateStatus.COMPLETED,
                output_text="fixed and proven",
                duration_ms=30.0,
                bash_executions=[{"command": "pytest", "status": "success", "shell_persistent": False, "output_tail": "12 passed in 0.42s"}],
            )

        report = run_with_gro(
            GOAL,
            adapter=SyncCallableAdapter(fn),
            acceptance_criteria=CRITERIA,
            config=_cfg(default_mode="single"),
            owner_id="u",
            store=GroStore(tmp_path),
        )
        assert report.outcome == "selected"
        assert report.verified is True  # the default verifier proved the criterion
        assert "Verification: **passed**" in report.markdown()
