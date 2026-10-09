"""APEX — the executive cycle, the invariant set, and the status projection.

The properties these pin are the ones that make APEX a control plane rather
than a claim:

1. **A cycle decides, it does not execute.** Every test here drives the cycle
   with no host adapter and asserts the *decision*, never a side effect.
2. **No false completion.** A session reaches ``COMPLETED`` only via a passing
   acceptance report — the negative cases are the point.
3. **Fleet control outranks the cycle.** An engaged ESTOP or an unreadable
   control state stops the cycle, and "could not read" is a stop, not a go.
4. **A missing site is disclosed, never a pass.** Both the invariant set and the
   store report unavailability explicitly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from alpha.apex.contract import narrow_contract, profile_for
from alpha.apex.executive import (
    REASON_ACCEPTANCE_FAILED,
    REASON_ACCEPTANCE_PENDING,
    REASON_ACCEPTED,
    REASON_BUDGET_EXHAUSTED,
    REASON_FLEET_STOPPED,
    REASON_POLICY_DRIFT,
    REASON_PROFILE_OFF,
    REASON_REPLAN_LIMIT,
    NextAction,
    run_cycle,
    select_next_action,
)
from alpha.apex.invariants import (
    INVARIANTS,
    InvariantCheck,
    InvariantStatus,
    check_invariants,
    enforcement_site_of,
    invariant_summary,
)
from alpha.apex.status import apex_status, contract_status, fleet_status
from alpha.apex.store import ApexSession, ApexSessionState, ApexStore
from alpha.mission.acceptance import REASON_NO_CRITERIA, REASON_NOT_EVALUATED


@pytest.fixture()
def store(tmp_path: Path) -> ApexStore:
    return ApexStore(tmp_path / "sessions.json")


def _session(store: ApexStore, **kwargs) -> ApexSession:
    defaults = {
        "owner": "tester",
        "objective": "do the thing",
        "profile": "autonomous",
        "contract_digest": profile_for("autonomous").digest(),
    }
    return store.create(**{**defaults, **kwargs})


def _set_acceptance(session: ApexSession, verdict: str) -> None:
    session.acceptance = {
        "criteria": [{"criterion": criterion, "verdict": verdict} for criterion in session.acceptance_criteria],
        "evaluator": "probe",
        "report_id": "acc-test",
    }


# --------------------------------------------------------------------------- #
# The cycle decides; it does not execute
# --------------------------------------------------------------------------- #


class TestCycleIsDecideOnly:
    def test_off_profile_never_decides(self, store: ApexStore) -> None:
        session = _session(store)
        decision = select_next_action(contract=profile_for("off"), session=session, store=store)
        assert decision.action is NextAction.NONE
        assert decision.reason == REASON_PROFILE_OFF
        assert decision.confidence == 1.0

    def test_no_session_blocks_rather_than_guessing(self, store: ApexStore) -> None:
        # A cycle that cannot resolve its subject must refuse to decide: guessing
        # here would be how a cycle starts work against the wrong owner.
        decision = select_next_action(contract=profile_for("autonomous"), session=None, store=store)
        assert decision.blocked is True
        assert decision.reason == "no_session"

    def test_first_cycle_asks_for_a_mission(self, store: ApexStore) -> None:
        session = _session(store)
        decision = select_next_action(contract=profile_for("autonomous"), session=session, store=store)
        assert decision.action is NextAction.CREATE_MISSION

    def test_cycle_records_every_step(self, store: ApexStore) -> None:
        session = _session(store)
        result = run_cycle(store, session.session_id, profile_for("autonomous"))
        names = [s.name for s in result.steps]
        assert names == ["load_session", "check_policy", "apply_decision", "checkpoint"]
        assert all(s.outcome != "error" for s in result.steps)

    def test_cycle_journals_a_decision_without_claiming_dispatch(self, store: ApexStore) -> None:
        session = _session(store)

        result = run_cycle(store, session.session_id, profile_for("autonomous"))

        assert result.decision.action is NextAction.CREATE_MISSION
        events = store.read_events(session.session_id)
        decision_events = [event for event in events if event.event_type.startswith("cycle.")]
        assert [event.event_type for event in decision_events] == ["cycle.decision_recorded"]
        assert decision_events[0].payload["decision"]["action"] == "create_mission"

    def test_policy_drift_blocks_before_selecting_or_dispatching_work(self, store: ApexStore) -> None:
        # The stored session was admitted under an unscoped contract; the
        # current mission-scoped contract has different authority semantics.
        session = _session(store)
        active_contract = narrow_contract(profile_for("autonomous"), authority={"browser": False})

        result = run_cycle(store, session.session_id, active_contract)

        assert result.decision.action is NextAction.NONE
        assert result.decision.reason == REASON_POLICY_DRIFT
        assert result.decision.blocked is True
        assert "session policy" in result.decision.detail["refusal"]
        assert next(step for step in result.steps if step.name == "check_policy").outcome == "drift"
        assert store.get(session.session_id).state is ApexSessionState.BLOCKED
        assert store.pending_approval(session.session_id) is None

    def test_unreadable_policy_blocks_instead_of_using_the_current_contract(self, store: ApexStore, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _session(store)

        def _policy_unreadable(*_args, **_kwargs):
            raise OSError("policy storage unavailable")

        import alpha.apex.contract as contract_module  # noqa: PLC0415

        monkeypatch.setattr(contract_module, "contract_digest_matches", _policy_unreadable)
        result = run_cycle(store, session.session_id, profile_for("autonomous"))

        assert result.decision.action is NextAction.NONE
        assert result.decision.reason == REASON_POLICY_DRIFT
        assert result.decision.blocked is True
        assert "could not be checked" in result.decision.detail["refusal"]
        assert next(step for step in result.steps if step.name == "check_policy").outcome == "error"
        assert store.get(session.session_id).state is ApexSessionState.BLOCKED
        assert store.pending_approval(session.session_id) is None

    def test_decision_input_failure_is_reported_as_a_block(self, store: ApexStore) -> None:
        session = _session(store)

        def _usage_unreadable(_session):
            raise OSError("usage store unavailable")

        result = run_cycle(
            store,
            session.session_id,
            profile_for("autonomous"),
            usage_provider=_usage_unreadable,
        )

        assert result.decision.action is NextAction.NONE
        assert result.decision.blocked is True
        assert "decision inputs could not be read" in result.decision.detail["refusal"]
        assert next(step for step in result.steps if step.name == "select_decision").outcome == "error"
        assert store.get(session.session_id).state is ApexSessionState.BLOCKED

    def test_the_cycle_counts_itself_and_the_checkpoint_names_that_count(self, store: ApexStore) -> None:
        """Both numbers the checkpoint reports are measurements, not decorations.

        ``cycle_count`` used to be a field nothing ever incremented, so the API
        answered ``cycle_count: 0`` after any number of cycles — a measured
        zero. The step detail was ``f"cycle {len(steps)}"``, which is how many
        probes ran *in this pass*: a session on its very first cycle reported
        ``cycle 3`` (three steps recorded before the lambda's own append), so a
        reader saw a cycle ordinal that named the step count instead.
        """
        session = _session(store)
        assert store.get(session.session_id).cycle_count == 0

        first = run_cycle(store, session.session_id, profile_for("autonomous"))
        assert store.get(session.session_id).cycle_count == 1
        first_checkpoint = next(s for s in first.steps if s.name == "checkpoint")
        assert first_checkpoint.outcome == "recorded"
        assert first_checkpoint.detail == "cycle 1"

        second = run_cycle(store, session.session_id, profile_for("autonomous"))
        assert store.get(session.session_id).cycle_count == 2
        second_checkpoint = next(s for s in second.steps if s.name == "checkpoint")
        assert second_checkpoint.detail == "cycle 2"

    def test_a_cycle_for_a_session_that_is_gone_records_no_count(self, store: ApexStore) -> None:
        # Counting a cycle against no row would be inventing a subject: the
        # checkpoint has to say the row vanished rather than report a total
        # for a session the store no longer holds.
        result = run_cycle(store, "apx-does-not-exist", profile_for("autonomous"))
        checkpoint = next(s for s in result.steps if s.name == "checkpoint")
        assert checkpoint.outcome == "absent"

    def test_cycle_does_not_create_a_run_or_touch_a_sandbox(self, store: ApexStore, monkeypatch: pytest.MonkeyPatch) -> None:
        """The cycle must not reach execution. Trip-wires prove it did not."""

        def _tripwire(*_args, **_kwargs):  # pragma: no cover - only runs on a regression
            raise AssertionError("the executive cycle attempted to perform work itself")

        import alpha.runtime.runs.manager as rm  # noqa: PLC0415

        for attr in ("create_or_reject", "create", "try_start", "cancel"):
            monkeypatch.setattr(rm.RunManager, attr, _tripwire)

        session = _session(store)
        result = run_cycle(store, session.session_id, profile_for("autonomous"))
        assert result.decision.action is NextAction.CREATE_MISSION

    def test_a_session_stays_idle_until_a_host_reports_work(self, store: ApexStore) -> None:
        """The cycle asks for a plan; it does not promote itself to active.

        ``ACTIVE`` means work is in flight, and only a host adapter knows that.
        A cycle that flipped the state itself would be claiming progress it has
        not observed — the same overreach as reporting completion from model
        text rather than evidence.
        """
        session = _session(store, mission_id="msn-1")
        for _ in range(3):
            result = run_cycle(store, session.session_id, profile_for("autonomous"))
            assert result.decision.action is NextAction.PLAN
            assert store.get(session.session_id).state is ApexSessionState.IDLE

    def test_an_active_session_is_dispatched_rather_than_replanned(self, store: ApexStore) -> None:
        session = _session(store, mission_id="msn-1")
        store.set_state(session.session_id, ApexSessionState.ACTIVE)
        result = run_cycle(store, session.session_id, profile_for("autonomous"))
        assert result.decision.action is NextAction.DISPATCH
        assert result.decision.detail["mission_id"] == "msn-1"


# --------------------------------------------------------------------------- #
# Invariant I6 — no false completion
# --------------------------------------------------------------------------- #


class TestCompletionRequiresAcceptance:
    def test_declared_criteria_do_not_block_work_before_any_evidence_exists(self, store: ApexStore) -> None:
        session = _session(store, acceptance_criteria=["tests pass"])
        result = run_cycle(store, session.session_id, profile_for("autonomous"))
        assert result.decision.action is NextAction.CREATE_MISSION
        assert result.decision.blocked is False
        assert store.get(session.session_id).state is not ApexSessionState.COMPLETED

    def test_declared_criteria_are_reported_unverified_never_absent(self, store: ApexStore) -> None:
        # The session declares its criteria, so an un-evaluated one is "nobody
        # has measured it yet" — a third fact, distinct from both "they failed"
        # and "there are none". The refusal used to be REASON_NO_CRITERIA,
        # because the cycle handed `assert_acceptance_passed` a report that did
        # not exist yet: a refusal denying three criteria sitting in the very
        # record it was read from, sending the operator to declare a second set.
        session = _session(store, acceptance_criteria=["tests pass", "docs pass"])
        _set_acceptance(session, "unverified")
        result = run_cycle(store, session.session_id, profile_for("autonomous"))

        refusal = str(result.decision.detail["refusal"])
        assert REASON_NO_CRITERIA not in refusal, "declared criteria must never be reported as absent"
        assert REASON_NOT_EVALUATED in refusal
        assert "2 of 2" in refusal, "the refusal names how many are outstanding"
        # The verdict itself is unchanged: unevaluated is a block, not a failure.
        assert result.decision.action is NextAction.AWAIT_VERIFICATION
        assert result.decision.reason == REASON_ACCEPTANCE_PENDING
        assert store.get(session.session_id).state is not ApexSessionState.COMPLETED

    def test_partial_acceptance_report_cannot_complete_all_declared_criteria(self, store: ApexStore) -> None:
        session = _session(store, acceptance_criteria=["tests pass", "docs pass"])
        session.acceptance = {
            "criteria": [{"criterion": "tests pass", "verdict": "met"}],
            "evaluator": "probe",
        }

        result = run_cycle(store, session.session_id, profile_for("autonomous"))

        assert result.decision.action is NextAction.AWAIT_VERIFICATION
        assert result.decision.reason == REASON_ACCEPTANCE_PENDING
        assert "docs pass" in result.decision.detail["refusal"]
        assert store.get(session.session_id).state is not ApexSessionState.COMPLETED

    def test_failed_criteria_recover_rather_than_complete(self, store: ApexStore) -> None:
        session = _session(store, acceptance_criteria=["tests pass"])
        _set_acceptance(session, "not_met")
        result = run_cycle(store, session.session_id, profile_for("autonomous"))
        assert result.decision.reason == REASON_ACCEPTANCE_FAILED
        assert result.decision.action is NextAction.RECOVER
        assert store.get(session.session_id).state is ApexSessionState.ACTIVE
        assert store.get(session.session_id).usage.replans == 1

    def test_failed_criteria_park_after_contract_replan_limit(self, store: ApexStore) -> None:
        contract = narrow_contract(profile_for("autonomous"), budget={"max_replans": 2})
        session = _session(store, acceptance_criteria=["tests pass"], contract_digest=contract.digest())
        session.usage.replans = 2
        _set_acceptance(session, "not_met")

        result = run_cycle(store, session.session_id, contract)

        assert result.decision.reason == REASON_REPLAN_LIMIT
        assert result.decision.blocked is True
        assert result.decision.detail["replans_used"] == 2
        assert store.get(session.session_id).state is ApexSessionState.BLOCKED
        assert store.get(session.session_id).blocked_reason == REASON_REPLAN_LIMIT
        assert store.pending_approval(session.session_id) is None

    def test_legacy_failed_acceptance_history_seeds_replan_counter(self) -> None:
        restored = ApexSession.from_dict(
            {
                "session_id": "apx-legacy-replans",
                "owner": "tester",
                "objective": "continue work",
                "acceptance_history": [{"report_id": "old-1"}, {"report_id": "old-2"}],
                "usage": {"replans": None},
            }
        )
        assert restored.usage.replans == 2

    def test_passed_criteria_complete_the_session(self, store: ApexStore) -> None:
        session = _session(store, acceptance_criteria=["tests pass"])
        _set_acceptance(session, "met")
        result = run_cycle(store, session.session_id, profile_for("autonomous"))
        assert result.decision.reason == REASON_ACCEPTED
        assert store.get(session.session_id).state is ApexSessionState.COMPLETED

    def test_a_terminal_session_is_not_reopened_by_a_later_cycle(self, store: ApexStore) -> None:
        session = _session(store, acceptance_criteria=["tests pass"])
        _set_acceptance(session, "met")
        run_cycle(store, session.session_id, profile_for("autonomous"))
        result = run_cycle(store, session.session_id, profile_for("autonomous"))
        assert store.get(session.session_id).state is ApexSessionState.COMPLETED
        assert result.decision.action is NextAction.NONE

    def test_set_state_refuses_a_terminal_transition(self, store: ApexStore) -> None:
        session = _session(store)
        store.set_state(session.session_id, ApexSessionState.CANCELLED, reason="operator")
        assert store.set_state(session.session_id, ApexSessionState.ACTIVE, reason="late") is None
        assert store.get(session.session_id).state is ApexSessionState.CANCELLED


# --------------------------------------------------------------------------- #
# Invariant I9 — fleet control outranks the cycle
# --------------------------------------------------------------------------- #


class TestFleetControlStopsTheCycle:
    @pytest.fixture()
    def engaged_stop(self, monkeypatch: pytest.MonkeyPatch):
        import alpha.runtime.estop as estop

        monkeypatch.setattr(estop.EmergencyStopManager, "is_engaged", lambda self: True)

    @pytest.fixture()
    def paused_fleet(self, monkeypatch: pytest.MonkeyPatch):
        import alpha.runtime.control as control

        state = control.ControlState(mode=control.ControlMode.PAUSE, reason="test")
        monkeypatch.setattr(control, "read_state", lambda *_a, **_k: state)

    @pytest.fixture()
    def unreadable_fleet(self, monkeypatch: pytest.MonkeyPatch):
        import alpha.runtime.control as control

        def _boom(*_a, **_k):
            raise RuntimeError("control file is unreadable")

        monkeypatch.setattr(control, "read_state", _boom)

    def test_engaged_estop_stops_the_cycle(self, store: ApexStore, engaged_stop) -> None:
        session = _session(store)
        decision = select_next_action(contract=profile_for("autonomous"), session=session, store=store)
        assert decision.blocked is True
        assert decision.reason == REASON_FLEET_STOPPED
        assert decision.detail["source"] == "estop_sentinel"

    def test_paused_fleet_stops_the_cycle(self, store: ApexStore, paused_fleet) -> None:
        session = _session(store)
        decision = select_next_action(contract=profile_for("autonomous"), session=session, store=store)
        assert decision.reason == REASON_FLEET_STOPPED
        assert decision.detail["source"] == "fleet_control:pause"

    def test_unreadable_control_state_fails_closed(self, store: ApexStore, unreadable_fleet) -> None:
        # A stop mechanism that cannot read its state must stop. Proceeding
        # would be the exact failure this test exists to prevent.
        session = _session(store)
        decision = select_next_action(contract=profile_for("autonomous"), session=session, store=store)
        assert decision.blocked is True
        assert "control_unreadable" in decision.detail["source"]


# --------------------------------------------------------------------------- #
# Budgets are ceilings
# --------------------------------------------------------------------------- #


class TestBudgetCeilings:
    def test_exhausted_tool_budget_blocks(self, store: ApexStore) -> None:
        contract = narrow_contract(profile_for("apex_max"), budget={"max_tool_calls": 3})
        session = _session(store, profile="apex_max", contract_digest=contract.digest())
        decision = select_next_action(
            contract=contract,
            session=session,
            store=store,
            usage_provider=lambda _s: {"tool_calls": 10},
        )
        assert decision.reason == REASON_BUDGET_EXHAUSTED
        assert decision.detail == {"tool_calls": 10, "max_tool_calls": 3}

    def test_unmeasured_usage_does_not_exhaust_a_budget(self, store: ApexStore) -> None:
        # `None` means "not counted". Reading it as zero spent would let an
        # unmeasured mission look indefinitely fresh.
        contract = narrow_contract(profile_for("apex_max"), budget={"max_tool_calls": 3})
        session = _session(store, profile="apex_max", contract_digest=contract.digest())
        decision = select_next_action(
            contract=contract,
            session=session,
            store=store,
            usage_provider=lambda _s: {"tool_calls": None},
        )
        assert decision.reason != REASON_BUDGET_EXHAUSTED

    def test_zero_tool_budget_blocks_even_when_no_calls_have_run(self, store: ApexStore) -> None:
        contract = narrow_contract(profile_for("apex_max"), budget={"max_tool_calls": 0})
        session = _session(store, profile="apex_max", contract_digest=contract.digest())
        decision = select_next_action(
            contract=contract,
            session=session,
            store=store,
            usage_provider=lambda _s: {"tool_calls": 0},
        )
        assert decision.reason == REASON_BUDGET_EXHAUSTED
        assert decision.detail == {"tool_calls": 0, "max_tool_calls": 0}

    def test_unlimited_default_never_stops_on_measured_tool_usage(self, store: ApexStore) -> None:
        contract = profile_for("assist")
        session = _session(store, profile="assist", contract_digest=contract.digest())
        decision = select_next_action(
            contract=contract,
            session=session,
            store=store,
            usage_provider=lambda _s: {"tool_calls": 10_000_000},
        )
        assert decision.reason != REASON_BUDGET_EXHAUSTED


# --------------------------------------------------------------------------- #
# Steering (spec §57)
# --------------------------------------------------------------------------- #


class TestSteering:
    def test_constraint_is_recorded_with_its_source(self, store: ApexStore) -> None:
        session = _session(store)
        constraint = store.record_constraint(session.session_id, "use local models only")
        assert constraint is not None
        assert constraint.source == "user"
        assert constraint.instruction == "use local models only"

    def test_constraint_on_a_terminal_session_is_refused(self, store: ApexStore) -> None:
        session = _session(store)
        store.set_state(session.session_id, ApexSessionState.CANCELLED, reason="operator")
        assert store.record_constraint(session.session_id, "too late") is None

    def test_constraint_does_not_rewrite_a_prompt(self, store: ApexStore) -> None:
        # Spec §57 requires a mission constraint, not a system-prompt overwrite.
        session = _session(store)
        store.record_constraint(session.session_id, "prioritise reliability")
        assert not hasattr(store.get(session.session_id), "system_prompt")


# --------------------------------------------------------------------------- #
# Store durability and honest degradation
# --------------------------------------------------------------------------- #


class TestStoreDurability:
    def test_sessions_survive_a_restart(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        first = ApexStore(path)
        session = first.create(owner="o", objective="obj", profile="autonomous", contract_digest="d")
        first.set_state(session.session_id, ApexSessionState.ACTIVE)

        second = ApexStore(path)
        restored = second.get(session.session_id)
        assert restored is not None
        assert restored.state is ApexSessionState.ACTIVE
        assert restored.objective == "obj"

    def test_acceptance_report_survives_a_restart(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        first = ApexStore(path)
        session = first.create(
            owner="o",
            objective="obj",
            profile="autonomous",
            contract_digest="d",
            acceptance_criteria=["tests pass"],
        )
        report = {"criteria": [{"criterion": "tests pass", "verdict": "met"}], "evaluator": "verified-run"}
        first.update(session.session_id, acceptance=report)

        restored = ApexStore(path).get(session.session_id)
        assert restored is not None
        assert restored.acceptance == report

    def test_events_replay_after_a_restart(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        first = ApexStore(path)
        session = first.create(owner="o", objective="obj", profile="autonomous", contract_digest="d")
        assert first.read_events(session.session_id)

        second = ApexStore(path)
        assert len(second.read_events(session.session_id)) == len(first.read_events(session.session_id))

    def test_an_unreadable_store_reports_rather_than_reads_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        path.write_text("{ this is not json", encoding="utf-8")
        broken = ApexStore(path)
        assert broken.is_degraded is True
        assert "JSONDecodeError" in (broken.load_error or "")
        # "Could not look" and "looked and found nothing" lead to opposite
        # decisions, so the difference has to survive to the caller.
        assert broken.list(limit=10) == []

    def test_unknown_field_update_is_refused_named(self, store: ApexStore) -> None:
        session = _session(store)
        with pytest.raises(ValueError) as exc:
            store.update(session.session_id, not_a_field=1)
        assert "not_a_field" in str(exc.value)

    def test_usage_reports_none_until_measured(self, store: ApexStore) -> None:
        session = _session(store)
        usage = store.get(session.session_id).usage
        assert usage.tool_calls is None
        usage.measure(tool_calls=3, llm_calls=1)
        assert usage.tool_calls == 3
        assert usage.llm_calls == 1


# --------------------------------------------------------------------------- #
# The invariant set is checkable
# --------------------------------------------------------------------------- #


class TestInvariantSet:
    def test_twelve_invariants_are_declared(self) -> None:
        # Spec §188 lists exactly I1..I12.
        assert len(INVARIANTS) == 12
        assert [c.id for c in INVARIANTS] == [f"I{n}" for n in range(1, 13)]

    def test_every_invariant_names_an_enforcement_site_and_a_symbol(self) -> None:
        for check in INVARIANTS:
            assert check.module, check.id
            assert check.symbol, check.id

    def test_all_declared_sites_are_live_in_this_tree(self) -> None:
        reports = check_invariants()
        missing = [(r.id, r.reason) for r in reports if not r.live]
        assert not missing, missing

    def test_a_missing_site_is_reported_not_scored_as_live(self) -> None:
        # A row whose site cannot be imported must say so. Reporting it as a
        # pass is the failure mode this module exists to prevent.
        bogus = InvariantCheck(
            id="IX",
            statement="test",
            module="alpha.does_not_exist",
            symbol="nothing",
            spec_section="test",
        )
        report = check_invariants((bogus,))[0]
        assert report.live is False
        assert report.status is InvariantStatus.MISSING
        assert "ModuleNotFoundError" in report.reason

    def test_a_missing_symbol_on_a_real_module_is_reported(self) -> None:
        # Several real sites are packages whose __init__ imports nothing, so a
        # module-only probe would report healthy for an empty namespace.
        bogus = InvariantCheck(
            id="IY",
            statement="test",
            module="alpha.mission.acceptance",
            symbol="definitely_not_defined",
            spec_section="test",
        )
        report = check_invariants((bogus,))[0]
        assert report.live is False
        assert "does not expose" in report.reason

    def test_summary_does_not_collapse_declared_into_live(self) -> None:
        summary = invariant_summary()
        assert summary["declared"] == 12
        assert summary["live"] == 12
        assert summary["live_ids"] and summary["missing_ids"] == []
        assert summary["probe_scope"] == "module_symbol_presence"
        assert summary["runtime_enforcement_verified"] is False

    def test_summary_of_a_partial_set_reports_both_counts(self) -> None:
        bogus = InvariantCheck(id="IX", statement="t", module="alpha.nope", symbol="x", spec_section="t")
        summary = invariant_summary(check_invariants((bogus,)))
        assert summary["declared"] == 1
        assert summary["live"] == 0
        assert summary["all_live"] is False

    def test_enforcement_site_lookup(self) -> None:
        assert enforcement_site_of("I9").module == "alpha.runtime.control"
        assert enforcement_site_of("i9").module == "alpha.runtime.control"
        assert enforcement_site_of("nope") is None

    @pytest.mark.parametrize("invariant_id", [c.id for c in INVARIANTS])
    def test_each_invariant_documents_its_spec_section(self, invariant_id: str) -> None:
        check = enforcement_site_of(invariant_id)
        assert check.spec_section.startswith("§188-")


# --------------------------------------------------------------------------- #
# The status projection discloses what it cannot read
# --------------------------------------------------------------------------- #


class TestHarnessBoundaryOfTheProjection:
    """The supervisor is injected by the app layer; the harness never imports it.

    Asserted at the source level because the real enforcement is
    ``tests/test_harness_boundary.py`` (a static scan of every ``alpha.*`` file).
    A dynamic import that slipped past it would fail that gate, not this one.
    """

    def test_status_module_declares_the_supervisor_path_rather_than_importing_it(self) -> None:
        from alpha.apex import status as status_module

        assert status_module.SUPERVISOR_STATUS_PATH == "app.gateway.autonomy.supervisor:get_autonomy_supervisor"
        source = Path(status_module.__file__).read_text(encoding="utf-8")
        assert "import app." not in source
        assert "from app." not in source

    def test_contract_status_names_live_and_missing_policy_sites(self) -> None:
        payload = contract_status(profile_for("apex_max"))
        assert payload["policy_sites"]
        assert set(payload["policy_sites_live"]) <= set(payload["policy_sites"])
        # Declared and live travel together so a reader can tell the difference.
        assert "policy_sites_missing" in payload

    def test_fleet_status_reads_through_the_stop_home(self) -> None:
        payload = fleet_status()
        assert payload["available"] is True
        assert "admits_work" in payload
        assert "estop_sentinel" in payload

    def test_full_status_includes_contract_fleet_and_sessions(self) -> None:
        store = ApexStore(Path(tempfile_dir()))
        payload = apex_status(store, profile_for("autonomous"))
        for key in ("contract", "fleet", "sessions", "supervisor", "invariants"):
            assert key in payload

    def test_an_unbound_supervisor_is_disclosed_not_faked(self) -> None:
        # The harness may not import the app layer, so the supervisor arrives
        # by injection. With none bound the block must say so — an operator has
        # to be able to tell "no supervisor ran" from "the supervisor is fine".
        payload = apex_status(ApexStore(Path(tempfile_dir())), profile_for("autonomous"))
        assert payload["supervisor"]["available"] is False
        assert "SUPERVISOR_STATUS_PATH" in payload["supervisor"]["reason"] or payload["supervisor"]["reason"]

    def test_a_bound_supervisor_is_passed_through(self) -> None:
        payload = apex_status(
            ApexStore(Path(tempfile_dir())),
            profile_for("autonomous"),
            supervisor_provider=lambda: {"loops": {"apex": {"enabled": True}}},
        )
        assert payload["supervisor"]["available"] is True
        assert payload["supervisor"]["status"]["loops"]["apex"]["enabled"] is True

    def test_a_failing_supervisor_degrades_only_that_block(self) -> None:
        def _boom() -> dict[str, Any]:
            raise RuntimeError("supervisor unavailable")

        payload = apex_status(
            ApexStore(Path(tempfile_dir())),
            profile_for("autonomous"),
            supervisor_provider=_boom,
        )
        assert payload["supervisor"]["available"] is False
        assert payload["contract"]["available"] is True  # one broken block, not the whole projection

    def test_status_for_an_unknown_session_discloses_rather_than_500s(self) -> None:
        store = ApexStore(Path(tempfile_dir()))
        payload = apex_status(store, profile_for("autonomous"), session_id="apx-nope")
        assert payload["session"]["available"] is False
        assert payload["session"]["session_id"] == "apx-nope"

    def test_status_reports_a_degraded_store_as_unavailable(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        path.write_text("{ broken", encoding="utf-8")
        payload = apex_status(ApexStore(path), profile_for("autonomous"))
        assert payload["sessions"]["available"] is False
        assert payload["sessions"]["count"] is None

    def test_status_does_not_mutate(self, store: ApexStore) -> None:
        session = _session(store)
        before = store.get(session.session_id).to_dict()
        apex_status(store, profile_for("autonomous"), session_id=session.session_id)
        assert store.get(session.session_id).to_dict() == before

    def test_invariants_can_be_skipped_for_a_poll_loop(self, store: ApexStore) -> None:
        payload = apex_status(store, profile_for("autonomous"), include_invariants=False)
        assert "invariants" not in payload


def tempfile_dir() -> str:
    import tempfile

    return tempfile.mkdtemp()
