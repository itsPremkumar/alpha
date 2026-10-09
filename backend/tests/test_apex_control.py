"""APEX — the session control surface: scope join, approval gate,
goal OS exposure, and the nine control verbs (spec §3, §7, §8, §24,
§27, §30, §33).

Five groups, each pinning a property that makes the control surface
real rather than asserted:

1. **The scope join.** A control command names a conversation, not a
   row. ``active_for_scope`` is the join, and a scope with no live
   session is ``None`` — never a fabricated row.
2. **The approval gate.** A blocked cycle parks the session and asks;
   only an operator's verdict un-parks it. Autonomy cannot grant its
   own approval, and a second verdict cannot overwrite a fresh one.
3. **The goal OS.** Goals carry their session link, record the agents
   asked for them, and close only through the acceptance gate — the
   verify flow refuses with the criteria that failed, by name.
4. **The commands.** Every verb resolves the active session for the
   conversation and reports the real outcome, including "there is
   nothing to act on".
5. **The routes.** The HTTP surface answers with real state, refuses
   a non-admin control action, and distinguishes a missing resource
   from a decided one.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.apex.contract import profile_for
from alpha.apex.executive import (
    REASON_AWAITING_APPROVAL,
    REASON_SESSION_PAUSED,
    NextAction,
    run_cycle,
    select_next_action,
)
from alpha.apex.goals import (
    ApexGoalStore,
    GoalEvidence,
    GoalPersistenceError,
    GoalState,
    IllegalGoalTransition,
    get_goal_store,
)
from alpha.apex.store import ApexSessionState, ApexStore

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApexStore:
    """The process-wide session store, pointed at a temp file."""
    import alpha.apex.store as store_module

    instance = ApexStore(tmp_path / "sessions.json")
    monkeypatch.setattr(store_module, "_store", instance)
    monkeypatch.setattr(store_module, "_default_storage_path", lambda: instance.storage_path)
    return instance


@pytest.fixture()
def goal_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ApexGoalStore, list[dict[str, Any]]]:
    """The process-wide goal store, with a recording event sink.

    The sink records what the journal writer was asked to append, so
    the tests assert on real events without touching the session
    store's disk journal.
    """
    import alpha.apex.goals as goals_module

    recorded: list[dict[str, Any]] = []

    def _sink(mission_id: str, event_type: str, payload: dict[str, Any]) -> None:
        recorded.append({"mission_id": mission_id, "event_type": event_type, **payload})

    instance = ApexGoalStore(tmp_path / "goals.json", event_sink=_sink)
    monkeypatch.setattr(goals_module, "_store", instance)
    monkeypatch.setattr(goals_module, "_default_storage_path", lambda: instance.storage_path)
    return instance, recorded


@pytest.fixture()
def session(store: ApexStore) -> Any:
    """One active session, bound to a conversation thread."""
    return store.create(
        owner="tester",
        objective="do the thing",
        profile="autonomous",
        contract_digest=profile_for("autonomous").digest(),
        thread_id="thread-1",
    )


def _client(is_admin: bool = True) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request, call_next):
        # ``system_role``, never ``is_admin``: the real ``User`` model and the
        # auth-disabled principal carry the former and have no such attribute
        # as the latter, so stamping it here would let the router keep reading
        # a field production never sets and still pass.
        request.state.user = SimpleNamespace(id="admin-1" if is_admin else "user-2", system_role="admin" if is_admin else "user")
        return await call_next(request)

    from app.gateway.routers import apex

    app.include_router(apex.router)
    return TestClient(app)


@pytest.fixture()
def client() -> TestClient:
    return _client()


# --------------------------------------------------------------------------- #
# 1. The scope join
# --------------------------------------------------------------------------- #


class TestScopeJoin:
    def test_finds_the_session_by_thread_id(self, store: ApexStore, session: Any) -> None:
        assert store.active_for_scope("thread-1") is session

    def test_finds_the_session_by_mission_id(self, store: ApexStore) -> None:
        session = store.create(
            owner="tester",
            objective="mission-bound",
            profile="autonomous",
            contract_digest=profile_for("autonomous").digest(),
            mission_id="m-42",
        )
        assert store.active_for_scope("m-42") is session

    def test_an_unknown_scope_is_none_not_a_row(self, store: ApexStore, session: Any) -> None:
        assert store.active_for_scope("no-such-conversation") is None

    def test_an_empty_scope_is_none(self, store: ApexStore, session: Any) -> None:
        assert store.active_for_scope("") is None

    def test_terminal_sessions_do_not_match(self, store: ApexStore, session: Any) -> None:
        store.set_state(session.session_id, ApexSessionState.CANCELLED, reason="done")
        assert store.active_for_scope("thread-1") is None

    def test_the_newest_session_wins(self, store: ApexStore) -> None:
        first = store.create(
            owner="tester",
            objective="first",
            profile="autonomous",
            contract_digest=profile_for("autonomous").digest(),
            thread_id="thread-1",
        )
        second = store.create(
            owner="tester",
            objective="second",
            profile="autonomous",
            contract_digest=profile_for("autonomous").digest(),
            thread_id="thread-1",
        )
        assert first.created_at <= second.created_at
        assert store.active_for_scope("thread-1") is second


# --------------------------------------------------------------------------- #
# 2. The approval gate
# --------------------------------------------------------------------------- #


class TestApprovalGate:
    def _park(self, store: ApexStore, session: Any) -> Any:
        store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="acceptance pending")
        return store.request_approval(session.session_id, note="acceptance pending", requester="apex.executive")

    def test_a_blocked_cycle_parks_and_asks(self, store: ApexStore, session: Any) -> None:
        """The load-bearing path: a blocked decision parks the session
        and creates the ask, so an operator has something real to decide."""
        session.acceptance_criteria = ["tests pass"]
        store.update(
            session.session_id,
            acceptance_criteria=["tests pass"],
            acceptance={"criteria": [{"criterion": "tests pass", "verdict": "unverified"}], "evaluator": "test"},
        )
        from alpha.apex.executive import run_cycle as _run

        result = _run(store, session.session_id, profile_for("autonomous"))
        assert result.decision.action is NextAction.AWAIT_VERIFICATION
        assert result.decision.blocked is True
        parked = store.get(session.session_id)
        assert parked.state is ApexSessionState.BLOCKED
        pending = store.pending_approval(session.session_id)
        assert pending is not None
        assert pending.note == result.decision.reason
        assert pending.requester == "apex.executive"

    def test_one_pending_approval_per_parked_episode(self, store: ApexStore, session: Any) -> None:
        first = self._park(store, session)
        assert first is not None
        second = store.request_approval(session.session_id, note="again")
        assert second is None
        assert store.pending_approval(session.session_id).approval_id == first.approval_id

    def test_approving_resumes_a_parked_session(self, store: ApexStore, session: Any) -> None:
        record = self._park(store, session)
        outcome = store.decide_approval(record.approval_id, verdict="approved", operator="admin-1", note="looks fine")
        assert outcome is not None
        decided, resumed = outcome
        assert decided.status == "approved"
        assert decided.operator == "admin-1"
        assert decided.decided_at is not None
        assert resumed is not None
        assert resumed.state is ApexSessionState.ACTIVE
        assert store.pending_approval(session.session_id) is None

    def test_rejecting_leaves_the_park_in_place(self, store: ApexStore, session: Any) -> None:
        record = self._park(store, session)
        outcome = store.decide_approval(record.approval_id, verdict="rejected", operator="admin-1")
        assert outcome is not None
        decided, resumed = outcome
        assert decided.status == "rejected"
        assert resumed is None
        still = store.get(session.session_id)
        assert still.state is ApexSessionState.BLOCKED

    def test_a_second_verdict_cannot_overwrite_a_fresh_one(self, store: ApexStore, session: Any) -> None:
        record = self._park(store, session)
        assert store.decide_approval(record.approval_id, verdict="approved", operator="admin-1") is not None
        assert store.decide_approval(record.approval_id, verdict="rejected", operator="admin-2") is None
        kept = [a for a in store.get(session.session_id).approvals if a["approval_id"] == record.approval_id]
        assert len(kept) == 1
        assert kept[0]["status"] == "approved"

    def test_an_unknown_verdict_is_refused_named(self, store: ApexStore, session: Any) -> None:
        record = self._park(store, session)
        with pytest.raises(ValueError, match="unknown verdict"):
            store.decide_approval(record.approval_id, verdict="maybe", operator="admin-1")

    def test_a_terminal_session_never_asks(self, store: ApexStore, session: Any) -> None:
        store.set_state(session.session_id, ApexSessionState.CANCELLED, reason="done")
        assert store.request_approval(session.session_id, note="late") is None

    def test_approvals_list_is_flattened_and_owner_scoped(self, store: ApexStore, session: Any) -> None:
        self._park(store, session)
        other = store.create(
            owner="someone-else",
            objective="not yours",
            profile="autonomous",
            contract_digest=profile_for("autonomous").digest(),
        )
        store.request_approval(other.session_id, note="theirs")
        mine = store.approvals(owner="tester")
        assert len(mine) == 1
        assert mine[0]["owner"] == "tester"
        assert mine[0]["objective"] == "do the thing"
        assert len(store.approvals()) == 2


# --------------------------------------------------------------------------- #
# 3. Pause and the parked cycle
# --------------------------------------------------------------------------- #


class TestParkedCycle:
    def test_a_paused_session_decides_nothing(self, store: ApexStore, session: Any) -> None:
        store.set_state(session.session_id, ApexSessionState.PAUSED, reason="operator pause")
        decision = select_next_action(contract=profile_for("autonomous"), session=session, store=store)
        assert decision.action is NextAction.NONE
        assert decision.reason == REASON_SESSION_PAUSED
        assert decision.blocked is True

    def test_a_blocked_session_waits_for_an_operator(self, store: ApexStore, session: Any) -> None:
        store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="needs a human")
        decision = select_next_action(contract=profile_for("autonomous"), session=session, store=store)
        assert decision.action is NextAction.NONE
        assert decision.reason == REASON_AWAITING_APPROVAL
        assert decision.blocked is True

    def test_a_parked_session_cycle_is_a_quiet_noop(self, store: ApexStore, session: Any) -> None:
        """A parked session's repeated cycles change nothing and ask twice."""
        store.set_state(session.session_id, ApexSessionState.PAUSED, reason="operator pause")
        before = store.get(session.session_id).updated_at
        before_count = store.get(session.session_id).cycle_count
        result = run_cycle(store, session.session_id, profile_for("autonomous"))
        assert result.decision.action is NextAction.NONE
        assert result.decision.reason == REASON_SESSION_PAUSED
        assert store.pending_approval(session.session_id) is None
        assert store.get(session.session_id).updated_at == before
        # …and that includes the cycle counter: a pass that decided to do
        # nothing must not accrue a cycle, or the row changes on every tick
        # while the answer stays the same.
        assert store.get(session.session_id).cycle_count == before_count
        checkpoint = next(s for s in result.steps if s.name == "checkpoint")
        assert checkpoint.outcome == "skipped"
        assert checkpoint.detail == "no decision to record"

    def test_an_approved_session_cycles_again(self, store: ApexStore, session: Any) -> None:
        session.acceptance_criteria = ["tests pass"]
        store.update(
            session.session_id,
            acceptance_criteria=["tests pass"],
            acceptance={"criteria": [{"criterion": "tests pass", "verdict": "unverified"}], "evaluator": "test"},
        )
        run_cycle(store, session.session_id, profile_for("autonomous"))
        assert store.get(session.session_id).state is ApexSessionState.BLOCKED
        pending = store.pending_approval(session.session_id)
        assert pending is not None
        store.decide_approval(pending.approval_id, verdict="approved", operator="admin-1")
        assert store.get(session.session_id).state is ApexSessionState.ACTIVE
        # The next cycle re-evaluates rather than resuming blindly.
        again = run_cycle(store, session.session_id, profile_for("autonomous"))
        assert again.decision.action is NextAction.AWAIT_VERIFICATION


# --------------------------------------------------------------------------- #
# 4. The goal OS
# --------------------------------------------------------------------------- #


class TestGoalOperatingSystem:
    def test_a_goal_carries_its_session_link(self, goal_store: Any, session: Any) -> None:
        store, _events = goal_store
        goal = store.create(objective="fix the browser", owner="tester", session_id=session.session_id)
        assert goal.session_id == session.session_id
        assert store.list(session_id=session.session_id) == [goal]
        assert store.list(session_id="other") == []

    def test_a_subgoal_inherits_the_session(self, goal_store: Any, session: Any) -> None:
        store, _events = goal_store
        parent = store.create(objective="repair alpha", owner="tester", session_id=session.session_id)
        child = store.create_child(parent.goal_id, objective="repair the frontend")
        assert child.parent_goal_id == parent.goal_id
        assert child.session_id == session.session_id
        assert child.priority <= parent.priority

    def test_subgoal_and_parent_link_commit_in_one_snapshot(self, goal_store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        store, events = goal_store
        parent = store.create(objective="repair alpha", owner="tester")
        snapshots: list[dict[str, Any]] = []
        save = store._save

        def capture_save() -> bool:
            persisted = save()
            if persisted:
                import json

                snapshots.append(json.loads(store.storage_path.read_text(encoding="utf-8")))
            return persisted

        monkeypatch.setattr(store, "_save", capture_save)
        child = store.create_child(parent.goal_id, objective="repair the frontend")

        assert len(snapshots) == 1
        rows = {row["goal_id"]: row for row in snapshots[0]["goals"]}
        assert rows[child.goal_id]["parent_goal_id"] == parent.goal_id
        assert child.goal_id in rows[parent.goal_id]["child_ids"]
        assert [event["event_type"] for event in events[-2:]] == ["goal.created", "goal.decomposed"]
        assert events[-2]["parent_goal_id"] == parent.goal_id

    def test_failed_subgoal_snapshot_leaves_no_orphan_or_event(self, goal_store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        store, events = goal_store
        parent = store.create(objective="repair alpha", owner="tester")
        events_before = list(events)
        monkeypatch.setattr(store, "_save", lambda: False)

        with pytest.raises(GoalPersistenceError, match="persist APEX goal"):
            store.create_child(parent.goal_id, objective="repair the frontend")

        assert store.list() == [parent]
        assert parent.child_ids == []
        assert events == events_before
        assert ApexGoalStore(store.storage_path).list() == [parent]

    def test_separate_goal_store_instances_do_not_overwrite_each_other(self, tmp_path: Path) -> None:
        path = tmp_path / "goals.json"
        first = ApexGoalStore(path)
        second = ApexGoalStore(path)

        first.create(objective="repair alpha")
        second.create(objective="repair the frontend")

        restarted = ApexGoalStore(path)
        assert {goal.objective for goal in restarted.list()} == {"repair alpha", "repair the frontend"}

    def test_a_subgoal_may_not_outrank_its_ancestor(self, goal_store: Any, session: Any) -> None:
        store, _events = goal_store
        parent = store.create(objective="repair alpha", owner="tester", priority=40)
        with pytest.raises(ValueError, match="ancestor ceiling"):
            store.create_child(parent.goal_id, objective="outrank", priority=90)

    def test_a_subgoal_must_preserve_its_parents_owner(self, goal_store: Any) -> None:
        store, _events = goal_store
        parent = store.create(objective="private parent", owner="owner-a")

        with pytest.raises(ValueError, match="same owner"):
            store.create_child(parent.goal_id, objective="foreign child", owner="owner-b")

        assert store.children(parent.goal_id) == []

    def test_agents_are_recorded_as_asks(self, goal_store: Any, session: Any) -> None:
        store, events = goal_store
        goal = store.create(objective="fix the browser", owner="tester")
        updated = store.add_agent(goal.goal_id, "sub-123", role="browser_operator")
        assert updated is not None
        assert updated.agent_records == [{"agent_id": "sub-123", "role": "browser_operator", "recorded_at": updated.agent_records[0]["recorded_at"]}]
        assert any(e["event_type"] == "goal.agent_recorded" and e["agent_id"] == "sub-123" for e in events)

    def test_a_terminal_goal_records_no_agent(self, goal_store: Any) -> None:
        store, events = goal_store
        goal = store.create(objective="done", owner="tester")
        for target in (
            GoalState.ANALYZING,
            GoalState.PLANNING,
            GoalState.EXECUTING,
            GoalState.VERIFYING,
            GoalState.CANCELLED,
        ):
            store.transition(goal.goal_id, target)
        assert store.add_agent(goal.goal_id, "sub-1") is None
        assert not any(e["event_type"] == "goal.agent_recorded" for e in events)

    def test_constraints_carry_their_source_in_the_journal(self, goal_store: Any) -> None:
        store, events = goal_store
        goal = store.create(objective="research", owner="tester")
        updated = store.add_constraint(goal.goal_id, "prefer primary sources", source="operator")
        assert updated is not None
        assert "prefer primary sources" in updated.constraints
        recorded = [e for e in events if e["event_type"] == "goal.constraint_recorded"]
        assert recorded and recorded[0]["source"] == "operator"

    def test_an_empty_constraint_is_refused(self, goal_store: Any) -> None:
        store, _events = goal_store
        goal = store.create(objective="research", owner="tester")
        assert store.add_constraint(goal.goal_id, "   ") is None

    def test_verify_enters_verifying_while_criteria_are_unmeasured(self, goal_store: Any) -> None:
        store, _events = goal_store
        goal = store.create(objective="fix it", owner="tester", success_criteria=["tests pass"])
        for target in (GoalState.ANALYZING, GoalState.PLANNING, GoalState.EXECUTING):
            store.transition(goal.goal_id, target)
        updated = store.verify(goal.goal_id)
        assert updated.state is GoalState.VERIFYING

    def test_verify_completes_through_the_acceptance_gate(self, goal_store: Any) -> None:
        store, events = goal_store
        goal = store.create(objective="fix it", owner="tester", success_criteria=["tests pass"])
        for target in (GoalState.ANALYZING, GoalState.PLANNING, GoalState.EXECUTING, GoalState.VERIFYING):
            store.transition(goal.goal_id, target)
        store.add_evidence(goal.goal_id, GoalEvidence.measured("tests pass", True, source="pytest"))
        updated = store.verify(goal.goal_id)
        assert updated.state is GoalState.COMPLETED
        assert any(e["event_type"] == "goal.completed" for e in events)

    def test_verify_refuses_with_the_failed_criterion_named(self, goal_store: Any) -> None:
        store, _events = goal_store
        goal = store.create(objective="fix it", owner="tester", success_criteria=["tests pass"])
        for target in (GoalState.ANALYZING, GoalState.PLANNING, GoalState.EXECUTING, GoalState.VERIFYING):
            store.transition(goal.goal_id, target)
        store.add_evidence(goal.goal_id, GoalEvidence.measured("tests pass", False, source="pytest"))
        with pytest.raises(IllegalGoalTransition, match="tests pass"):
            store.verify(goal.goal_id)

    def test_verify_on_a_terminal_goal_is_refused(self, goal_store: Any) -> None:
        store, _events = goal_store
        goal = store.create(objective="done", owner="tester")
        store.transition(goal.goal_id, GoalState.CANCELLED)
        with pytest.raises(IllegalGoalTransition, match="terminal"):
            store.verify(goal.goal_id)

    def test_transitions_are_journalled(self, goal_store: Any) -> None:
        store, events = goal_store
        goal = store.create(objective="plan", owner="tester")
        store.transition(goal.goal_id, GoalState.ANALYZING, reason="operator")
        transitioned = [e for e in events if e["event_type"] == "goal.transitioned"]
        assert transitioned
        assert transitioned[0]["from"] == "idle"
        assert transitioned[0]["to"] == "analyzing"
        assert transitioned[0]["reason"] == "operator"

    def test_the_singleton_sink_forwards_into_the_apex_journal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The production wiring: goal events reach the APEX journal.

        ``_session_event_sink`` resolves ``get_apex_store`` lazily, so
        the sink it hands the goal store forwards to whatever the
        session-store singleton holds. Point that singleton at a temp
        journal and prove a goal event lands in it — that is the whole
        reason ``GET /api/apex/goals/{id}/events`` can answer.
        """
        import alpha.apex.goals as goals_module
        import alpha.apex.store as store_module

        sessions = store_module.ApexStore(tmp_path / "sessions.json")
        monkeypatch.setattr(store_module, "_store", sessions)
        monkeypatch.setattr(store_module, "_default_storage_path", lambda: sessions.storage_path)

        goals = goals_module.ApexGoalStore(tmp_path / "goals.json", event_sink=goals_module._session_event_sink())
        goal = goals.create(objective="wiring", owner="tester")
        goals.transition(goal.goal_id, GoalState.ANALYZING, reason="operator")

        replayed = sessions.read_events(goal.goal_id)
        types = [e.event_type for e in replayed]
        assert "goal.created" in types
        assert "goal.transitioned" in types
        assert all(e.mission_id == goal.goal_id for e in replayed)


# --------------------------------------------------------------------------- #
# 5. The commands
# --------------------------------------------------------------------------- #


class TestControlCommands:
    @pytest.fixture()
    def registry(
        self,
        store: ApexStore,
        goal_store: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> Any:
        """The real process-wide command registry, with temp stores
        behind the mode, session and goal singletons."""
        import alpha.apex.mode as mode_module

        mode_instance = mode_module.ApexModeStore(tmp_path / "mode.json")
        monkeypatch.setattr(mode_module, "_store", mode_instance)
        monkeypatch.setattr(mode_module, "_default_storage_path", lambda: mode_instance.storage_path)

        import alpha.apex.commands as apex_commands
        import alpha.commands  # noqa: F401  (binds the handlers)

        apex_commands.register_apex_commands()
        from alpha.commands.registry import command_registry

        return command_registry

    def test_pause_parks_the_conversation_session(self, registry: Any, session: Any) -> None:
        result = registry.execute(command_line="/apex pause", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert result.data["applied"] is True
        assert result.data["state"] == "paused"
        assert result.data["session_id"] == session.session_id

    def test_pause_without_a_session_reports_rather_than_guesses(self, registry: Any) -> None:
        result = registry.execute(command_line="/apex pause", context={"thread_id": "unknown-thread"})
        assert result.status == "error"
        assert "No active APEX session" in result.output

    def test_pause_is_idempotent(self, registry: Any, store: ApexStore, session: Any) -> None:
        store.set_state(session.session_id, ApexSessionState.PAUSED, reason="operator pause")
        result = registry.execute(command_line="/apex pause", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert result.data["applied"] is False

    def test_pause_on_a_terminal_session_is_refused(self, registry: Any, store: ApexStore, session: Any) -> None:
        store.set_state(session.session_id, ApexSessionState.COMPLETED, reason="done")
        result = registry.execute(command_line="/apex pause", context={"thread_id": "thread-1"})
        assert result.status == "error"
        assert "terminal" in result.output

    def test_no_control_verb_can_unpark_a_gated_session(self, registry: Any, store: ApexStore, session: Any) -> None:
        """The two-command side door around the approval gate.

        ``pause`` would move BLOCKED to PAUSED (clearing the blocker) and
        ``resume`` would then release it — the gate with a bypass. Every
        verb that moves state therefore refuses the park by name, and the
        refusal still leaves the real ways out (verdict / replan) open.
        """
        store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="acceptance pending")
        store.request_approval(session.session_id, note="acceptance pending", requester="apex.executive")
        for verb in ("pause", "resume", "stop", "take-over"):
            result = registry.execute(command_line=f"/apex {verb}", context={"thread_id": "thread-1"})
            assert result.status == "error", verb
            assert "parked awaiting approval" in result.output, verb
            assert result.data["state"] == "blocked", verb
        assert store.get(session.session_id).state is ApexSessionState.BLOCKED
        assert store.pending_approval(session.session_id) is not None

    def test_steer_is_not_a_state_move_and_works_while_parked(self, registry: Any, store: ApexStore, session: Any) -> None:
        """Constraints are records, not control: steering a parked session
        must work (the next cycle reads it after the verdict) without
        touching the gate."""
        store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="acceptance pending")
        store.request_approval(session.session_id, note="acceptance pending", requester="apex.executive")
        result = registry.execute(command_line="/apex steer keep the tests green", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert store.get(session.session_id).state is ApexSessionState.BLOCKED

    def test_resume_releases_a_pause(self, registry: Any, store: ApexStore, session: Any) -> None:
        store.set_state(session.session_id, ApexSessionState.PAUSED, reason="operator pause")
        result = registry.execute(command_line="/apex resume", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert result.data["applied"] is True
        assert result.data["state"] == "active"

    def test_resume_on_a_non_paused_session_is_a_noop(self, registry: Any, session: Any) -> None:
        result = registry.execute(command_line="/apex resume", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert result.data["applied"] is False

    def test_stop_states_the_runmanager_and_estop_boundaries(self, registry: Any, session: Any) -> None:
        result = registry.execute(command_line="/apex stop", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert result.data["applied"] is True
        assert "RunManager" in result.output
        assert "ESTOP" in result.output

    def test_steer_records_a_constraint(self, registry: Any, session: Any) -> None:
        result = registry.execute(
            command_line="/apex steer prefer readability over cleverness",
            context={"thread_id": "thread-1"},
        )
        assert result.status == "success"
        assert result.data["constraint"]["instruction"] == "prefer readability over cleverness"
        assert result.data["constraint"]["source"] == "user"

    def test_steer_requires_an_instruction(self, registry: Any) -> None:
        result = registry.execute(command_line="/apex steer", context={"thread_id": "thread-1"})
        assert result.status == "error"
        assert "Usage" in result.output

    def test_take_over_parks_and_records_the_handover(self, registry: Any, store: ApexStore, session: Any) -> None:
        result = registry.execute(command_line="/apex take-over", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert result.data["applied"] is True
        constraints = store.get(session.session_id).constraints
        assert any(c.instruction.startswith("Operator took over") and c.priority == "high" for c in constraints)

    def test_approve_resumes_a_parked_session(self, registry: Any, store: ApexStore, session: Any) -> None:
        store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="acceptance pending")
        record = store.request_approval(session.session_id, note="acceptance pending", requester="apex.executive")
        result = registry.execute(command_line="/apex approve", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert result.data["approval"]["approval_id"] == record.approval_id
        assert result.data["resumed"] is True
        assert store.get(session.session_id).state is ApexSessionState.ACTIVE

    def test_approve_without_a_pending_ask_reports_it(self, registry: Any, session: Any) -> None:
        result = registry.execute(command_line="/apex approve", context={"thread_id": "thread-1"})
        assert result.status == "error"
        assert "Nothing is pending approval" in result.output

    def test_reject_leaves_the_session_parked(self, registry: Any, store: ApexStore, session: Any) -> None:
        store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="acceptance pending")
        store.request_approval(session.session_id, note="acceptance pending")
        result = registry.execute(command_line="/apex reject", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert result.data["resumed"] is False
        assert store.get(session.session_id).state is ApexSessionState.BLOCKED

    def test_replan_moves_the_session_goals(self, registry: Any, goal_store: Any, session: Any) -> None:
        store, _events = goal_store
        goal = store.create(objective="subtask", owner="tester", session_id=session.session_id)
        for target in (GoalState.ANALYZING, GoalState.PLANNING, GoalState.EXECUTING):
            store.transition(goal.goal_id, target)
        result = registry.execute(command_line="/apex replan", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert result.data["replanned"] == [goal.goal_id]
        assert store.get(goal.goal_id).state is GoalState.REPLANNING

    def test_replan_names_what_it_could_not_move(self, registry: Any, goal_store: Any, session: Any) -> None:
        store, _events = goal_store
        goal = store.create(objective="stuck", owner="tester", session_id=session.session_id)
        # IDLE can only reach ANALYZING or CANCELLED — REPLANNING is
        # refused by the transition table and must be named, not
        # silently skipped.
        result = registry.execute(command_line="/apex replan", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert result.data["replanned"] == []
        assert len(result.data["refused"]) == 1
        refused = result.data["refused"][0]
        assert refused.startswith(f"{goal.goal_id} (goal '{goal.goal_id}' cannot move from 'idle' to 'replanning'")
        assert "['analyzing', 'cancelled']" in refused

    def test_verify_reports_per_goal_outcomes(self, registry: Any, goal_store: Any, session: Any) -> None:
        store, _events = goal_store
        goal = store.create(objective="measure me", owner="tester", success_criteria=["tests pass"], session_id=session.session_id)
        for target in (GoalState.ANALYZING, GoalState.PLANNING, GoalState.EXECUTING):
            store.transition(goal.goal_id, target)
        result = registry.execute(command_line="/apex verify", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert result.data["verifying"] == [f"{goal.goal_id} (verifying)"]
        assert store.get(goal.goal_id).state is GoalState.VERIFYING

    def test_verify_without_goals_reports_rather_than_guesses(self, registry: Any, session: Any) -> None:
        result = registry.execute(command_line="/apex verify", context={"thread_id": "thread-1"})
        assert result.status == "success"
        assert "No goals exist" in result.output


# --------------------------------------------------------------------------- #
# 6. The routes
# --------------------------------------------------------------------------- #


class TestControlRoutes:
    def test_pause_applies_to_the_active_session(self, client: TestClient, session: Any) -> None:
        response = client.post("/api/apex/pause", json={"scope_key": "thread-1"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["applied"] is True
        assert body["session"]["state"] == "paused"

    def test_pause_without_a_session_is_404_with_the_remedy(self, client: TestClient) -> None:
        response = client.post("/api/apex/pause", json={})
        assert response.status_code == 404
        assert "POST /api/apex/sessions" in response.json()["detail"]

    def test_resume_releases_the_pause(self, client: TestClient, store: ApexStore, session: Any) -> None:
        client.post("/api/apex/pause", json={"scope_key": "thread-1"})
        response = client.post("/api/apex/resume", json={"scope_key": "thread-1"})
        assert response.status_code == 200
        assert response.json()["session"]["state"] == "active"

    def test_stop_carries_the_boundary_note(self, client: TestClient, session: Any) -> None:
        response = client.post("/api/apex/stop", json={"scope_key": "thread-1"})
        assert response.status_code == 200
        assert "RunManager" in response.json()["note"]

    def test_a_parked_session_is_409_for_every_control_verb(self, client: TestClient, store: ApexStore, session: Any) -> None:
        """HTTP mirrors the command-side gate: pause-then-resume over the
        wire is the same side door, and it is closed the same way — with
        the approval route named rather than a bare refusal."""
        store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="acceptance pending")
        store.request_approval(session.session_id, note="acceptance pending", requester="apex.executive")
        for verb in ("pause", "resume", "stop"):
            response = client.post(f"/api/apex/{verb}", json={"scope_key": "thread-1"})
            assert response.status_code == 409, verb
            assert "approval" in response.json()["detail"], verb
        assert store.get(session.session_id).state is ApexSessionState.BLOCKED

    def test_a_control_action_requires_admin(self, store: ApexStore, session: Any) -> None:
        response = _client(is_admin=False).post("/api/apex/pause", json={"scope_key": "thread-1"})
        assert response.status_code == 403

    def test_the_mode_read_carries_the_active_session(self, client: TestClient, session: Any) -> None:
        body = client.get("/api/apex/mode", params={"scope_key": "thread-1"}).json()
        assert body["active_session"]["session_id"] == session.session_id
        assert body["active_session"]["thread_id"] == "thread-1"

    def test_the_mode_read_reports_null_when_no_session_exists(self, client: TestClient) -> None:
        body = client.get("/api/apex/mode?scope_key=nobody").json()
        assert body["active_session"] is None


class TestGoalRoutes:
    def _advance(self, goal_id: str, *states: str) -> None:
        """Move a goal through states via the store the router shares.

        There is deliberately no HTTP state route for goals: a
        goal's state is advanced by the work that measures it,
        not by a client asserting it.
        """
        store = get_goal_store()
        for state in states:
            store.transition(goal_id, GoalState(state))

    def test_create_returns_the_goal_with_the_caller_as_owner(self, client: TestClient) -> None:
        response = client.post(
            "/api/apex/goals",
            json={"objective": "fix the browser", "success_criteria": ["suite passes"]},
        )
        assert response.status_code == 200, response.text
        goal = response.json()["goal"]
        assert goal["objective"] == "fix the browser"
        assert goal["owner"] == "admin-1"
        assert goal["state"] == "idle"

    def test_create_as_a_child_inherits_the_session(self, client: TestClient, store: ApexStore) -> None:
        # The session link must match the goal owner, so the caller's own
        # session is the one a created goal may carry — a foreign owner's
        # session is refused at creation (see ``test_apex_authz.py``).
        session = store.create(
            owner="admin-1",
            objective="do the thing",
            profile="autonomous",
            contract_digest=profile_for("autonomous").digest(),
            thread_id="thread-1",
        )
        parent = client.post(
            "/api/apex/goals",
            json={"objective": "repair alpha", "session_id": session.session_id},
        ).json()["goal"]
        child = client.post(
            "/api/apex/goals",
            json={"objective": "repair the frontend", "parent_goal_id": parent["goal_id"]},
        ).json()["goal"]
        assert child["parent_goal_id"] == parent["goal_id"]
        assert child["session_id"] == session.session_id

    def test_a_child_that_would_outrank_is_422(self, client: TestClient) -> None:
        parent = client.post("/api/apex/goals", json={"objective": "parent", "priority": 40}).json()["goal"]
        response = client.post("/api/apex/goals", json={"objective": "child", "parent_goal_id": parent["goal_id"], "priority": 90})
        assert response.status_code == 422
        assert "ancestor ceiling" in response.json()["detail"]

    def test_list_is_owner_scoped_for_a_non_admin(self, client: TestClient, store: ApexStore) -> None:
        client.post("/api/apex/goals", json={"objective": "mine"})
        response = _client(is_admin=False).get("/api/apex/goals")
        assert response.status_code == 200
        assert [g["objective"] for g in response.json()["goals"]] == []

    def test_one_goal_carries_its_tree(self, client: TestClient, session: Any) -> None:
        parent = client.post("/api/apex/goals", json={"objective": "parent"}, params={}).json()["goal"]
        client.post("/api/apex/goals", json={"objective": "child", "parent_goal_id": parent["goal_id"]})
        body = client.get(f"/api/apex/goals/{parent['goal_id']}").json()
        assert body["tree"]["children"][0]["objective"] == "child"

    def test_steer_records_a_constraint(self, client: TestClient) -> None:
        goal = client.post("/api/apex/goals", json={"objective": "research"}).json()["goal"]
        response = client.post(
            f"/api/apex/goals/{goal['goal_id']}/steer",
            json={"instruction": "prefer primary sources", "source": "operator"},
        )
        assert response.status_code == 200
        assert "prefer primary sources" in response.json()["goal"]["constraints"]

    def test_replan_from_executing_is_legal(self, client: TestClient) -> None:
        goal = client.post("/api/apex/goals", json={"objective": "plan"}).json()["goal"]
        self._advance(goal["goal_id"], "analyzing", "planning", "executing")
        response = client.post(f"/api/apex/goals/{goal['goal_id']}/replan")
        assert response.status_code == 200, response.text
        assert response.json()["goal"]["state"] == "replanning"
        assert response.json()["goal"]["plan_version"] == 1

    def test_replan_from_idle_is_409_with_the_allowed_set(self, client: TestClient) -> None:
        goal = client.post("/api/apex/goals", json={"objective": "plan"}).json()["goal"]
        response = client.post(f"/api/apex/goals/{goal['goal_id']}/replan")
        assert response.status_code == 409
        assert "cannot move from 'idle'" in response.json()["detail"]

    def test_verify_enters_verifying_while_unmeasured(self, client: TestClient) -> None:
        goal = client.post("/api/apex/goals", json={"objective": "fix", "success_criteria": ["tests pass"]}).json()["goal"]
        self._advance(goal["goal_id"], "analyzing", "planning", "executing")
        response = client.post(f"/api/apex/goals/{goal['goal_id']}/verify")
        assert response.status_code == 200, response.text
        assert response.json()["goal"]["state"] == "verifying"

    def test_verify_completes_on_measured_evidence(self, client: TestClient) -> None:
        goal = client.post("/api/apex/goals", json={"objective": "fix", "success_criteria": ["tests pass"]}).json()["goal"]
        self._advance(goal["goal_id"], "analyzing", "planning", "executing", "verifying")
        # Evidence is attached through the store the router shares.
        get_goal_store().add_evidence(goal["goal_id"], GoalEvidence.measured("tests pass", True, source="pytest"))
        response = client.post(f"/api/apex/goals/{goal['goal_id']}/verify")
        assert response.status_code == 200, response.text
        assert response.json()["goal"]["state"] == "completed"

    def test_verify_refuses_with_the_failed_criterion(self, client: TestClient) -> None:
        goal = client.post("/api/apex/goals", json={"objective": "fix", "success_criteria": ["tests pass"]}).json()["goal"]
        self._advance(goal["goal_id"], "analyzing", "planning", "executing", "verifying")
        get_goal_store().add_evidence(goal["goal_id"], GoalEvidence.measured("tests pass", False, source="pytest"))
        response = client.post(f"/api/apex/goals/{goal['goal_id']}/verify")
        assert response.status_code == 409
        assert "tests pass" in response.json()["detail"]

    def test_tasks_are_the_decomposition(self, client: TestClient) -> None:
        parent = client.post("/api/apex/goals", json={"objective": "parent"}).json()["goal"]
        client.post("/api/apex/goals", json={"objective": "child", "parent_goal_id": parent["goal_id"]})
        body = client.get(f"/api/apex/goals/{parent['goal_id']}/tasks").json()
        assert body["count"] == 1
        assert body["tasks"][0]["objective"] == "child"

    def test_agents_discloses_what_it_records(self, client: TestClient) -> None:
        goal = client.post("/api/apex/goals", json={"objective": "fix"}).json()["goal"]
        body = client.get(f"/api/apex/goals/{goal['goal_id']}/agents").json()
        assert body["count"] == 0
        assert body["agents"] == []
        assert "alpha.subagents.lifecycle" in body["note"]

    def test_workflow_is_honestly_unavailable(self, client: TestClient) -> None:
        goal = client.post("/api/apex/goals", json={"objective": "fix"}).json()["goal"]
        body = client.get(f"/api/apex/goals/{goal['goal_id']}/workflow").json()
        assert body["available"] is False
        assert "dynamic workflow engine" in body["reason"]

    def test_events_replay_the_goal_journal(self, client: TestClient) -> None:
        goal = client.post("/api/apex/goals", json={"objective": "fix"}).json()["goal"]
        body = client.get(f"/api/apex/goals/{goal['goal_id']}/events").json()
        assert body["count"] >= 1
        assert body["events"][0]["event_type"] == "goal.created"

    def test_decisions_discloses_a_missing_session_link(self, client: TestClient) -> None:
        goal = client.post("/api/apex/goals", json={"objective": "fix"}).json()["goal"]
        body = client.get(f"/api/apex/goals/{goal['goal_id']}/decisions").json()
        assert body["decisions"] == []
        assert "not linked to a session" in body["note"]

    def test_evidence_reports_what_decides_nothing(self, client: TestClient) -> None:
        goal = client.post("/api/apex/goals", json={"objective": "fix", "success_criteria": ["tests pass"]}).json()["goal"]
        body = client.get(f"/api/apex/goals/{goal['goal_id']}/evidence").json()
        assert body["criteria"] == ["tests pass"]
        assert body["criteria_without_evidence"] == ["tests pass"]
        assert body["evidence"] == []

    def test_failures_derive_from_the_record(self, client: TestClient) -> None:
        goal = client.post("/api/apex/goals", json={"objective": "fix", "success_criteria": ["tests pass"]}).json()["goal"]
        self._advance(goal["goal_id"], "analyzing", "planning", "executing", "verifying")
        get_goal_store().add_evidence(goal["goal_id"], GoalEvidence.measured("tests pass", False, source="pytest"))
        body = client.get(f"/api/apex/goals/{goal['goal_id']}/failures").json()
        assert body["count"] == 1
        assert body["failures"][0]["kind"] == "criterion_failed"
        assert body["failures"][0]["criterion"] == "tests pass"
        assert body["failures"][0]["source"] == "pytest"

    def test_an_unknown_goal_is_404(self, client: TestClient) -> None:
        response = client.get("/api/apex/goals/agl-does-not-exist")
        assert response.status_code == 404
        assert "no APEX goal 'agl-does-not-exist'" in response.json()["detail"]


class TestApprovalRoutes:
    def _parked(self, client: TestClient, store: ApexStore, session: Any) -> dict[str, Any]:
        store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="acceptance pending")
        record = store.request_approval(session.session_id, note="acceptance pending", requester="apex.executive")
        return record.to_dict()

    def test_approve_unparks_the_session(self, client: TestClient, store: ApexStore, session: Any) -> None:
        record = self._parked(client, store, session)
        response = client.post(f"/api/apex/approvals/{record['approval_id']}/approve", json={"note": "verified by hand"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["approval"]["status"] == "approved"
        assert body["resumed"] is True
        assert body["session"]["state"] == "active"

    def test_reject_keeps_the_park(self, client: TestClient, store: ApexStore, session: Any) -> None:
        record = self._parked(client, store, session)
        response = client.post(f"/api/apex/approvals/{record['approval_id']}/reject", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["approval"]["status"] == "rejected"
        assert body["resumed"] is False
        assert store.get(session.session_id).state is ApexSessionState.BLOCKED

    def test_a_decided_approval_is_409_not_404(self, client: TestClient, store: ApexStore, session: Any) -> None:
        record = self._parked(client, store, session)
        client.post(f"/api/apex/approvals/{record['approval_id']}/approve", json={})
        response = client.post(f"/api/apex/approvals/{record['approval_id']}/approve", json={})
        assert response.status_code == 409
        assert "already decided" in response.json()["detail"]

    def test_an_unknown_approval_is_404(self, client: TestClient) -> None:
        response = client.post("/api/apex/approvals/app-does-not-exist/approve", json={})
        assert response.status_code == 404

    def test_an_approval_requires_admin(self, client: TestClient, store: ApexStore, session: Any) -> None:
        record = self._parked(client, store, session)
        response = _client(is_admin=False).post(f"/api/apex/approvals/{record['approval_id']}/approve", json={})
        assert response.status_code == 403

    def test_the_list_reports_pending_separately(self, client: TestClient, store: ApexStore, session: Any) -> None:
        self._parked(client, store, session)
        body = client.get("/api/apex/approvals").json()
        assert body["count"] == 1
        assert body["pending"] == 1
        assert body["approvals"][0]["status"] == "pending"
        assert body["approvals"][0]["objective"] == "do the thing"
