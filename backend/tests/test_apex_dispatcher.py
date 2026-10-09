"""APEX host dispatch reuses one RunManager admission and observes usage."""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from alpha.apex.contract import narrow_contract, profile_for
from alpha.apex.mode import ApexModeStore
from alpha.apex.store import ApexSession, ApexSessionState, ApexStore


@pytest.mark.asyncio
async def test_apex_execution_tick_dispatches_once_and_projects_terminal_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import alpha.mission.acceptance as acceptance_module
    import app.gateway.autonomy.supervisor as supervisor_module
    import app.gateway.services as gateway_services
    from alpha.mission.acceptance import AcceptanceRegistry
    from app.gateway.autonomy.loops import apex_execution_tick

    owner = "operator"
    thread_id = "thread-apex-dispatch"
    contract = profile_for("apex_max")
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "apex_max", owner=owner)
    session = store.create(
        owner=owner,
        objective="inspect the local service health",
        profile="apex_max",
        contract_digest=contract.digest(),
        contract_snapshot=contract.to_dict(),
        thread_id=thread_id,
        acceptance_criteria=["the service is ready"],
    )
    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)
    monkeypatch.setattr(acceptance_module, "get_acceptance_registry", lambda: AcceptanceRegistry())

    admitted: list[tuple[str, int]] = []

    async def launch(*, app, session, generation):
        admitted.append((session.session_id, generation))
        return SimpleNamespace(run_id="run-apex-1", status=SimpleNamespace(value="running"))

    monkeypatch.setattr(gateway_services, "launch_apex_session_run", launch)

    class FakeRunManager:
        record = SimpleNamespace(
            run_id="run-apex-1",
            status=SimpleNamespace(value="running"),
            total_input_tokens=125,
            total_output_tokens=25,
            llm_call_count=3,
        )

        async def get(self, run_id, *, user_id=None, raise_on_store_error=False):
            assert run_id == self.record.run_id
            assert user_id == owner
            return self.record

    class EmptyEventStore:
        async def list_events(self, thread, run, *, event_types, limit, after_seq):
            return []

    app = SimpleNamespace(state=SimpleNamespace(run_manager=FakeRunManager(), run_event_store=EmptyEventStore()))

    dispatched = await apex_execution_tick(app, session_id=session.session_id)
    assert dispatched["dispatched"] == 1
    assert admitted == [(session.session_id, 1)]
    linked = store.get(session.session_id)
    assert linked.run_id == "run-apex-1"
    assert linked.dispatch_state == "running"
    assert linked.state.value == "active"

    live = await apex_execution_tick(app, session_id=session.session_id)
    assert live["running"] == 1
    usage = store.get(session.session_id).usage
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.llm_calls) == (125, 25, 150, 3)

    app.state.run_manager.record.status = SimpleNamespace(value="completed")
    app.state.run_manager.record.total_input_tokens = 500
    app.state.run_manager.record.total_output_tokens = 100
    app.state.run_manager.record.llm_call_count = 5
    observed = await apex_execution_tick(app, session_id=session.session_id)
    assert observed["awaiting_verification"] == 1
    usage = store.get(session.session_id).usage
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.llm_calls) == (500, 100, 600, 5)

    repeated = await apex_execution_tick(app, session_id=session.session_id)
    assert repeated["awaiting_verification"] == 1
    assert admitted == [(session.session_id, 1)]
    assert store.get(session.session_id).usage.total_tokens == 600


@pytest.mark.asyncio
async def test_late_tool_approval_requeues_a_finished_run_for_a_fresh_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module
    import app.gateway.services as gateway_services
    from app.gateway.autonomy.loops import apex_execution_tick

    owner, thread_id = "operator", "thread-apex-late-approval"
    contract = profile_for("apex_max")
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "apex_max", owner=owner)
    session = store.create(
        owner=owner,
        objective="complete the approved operation",
        profile="apex_max",
        contract_digest=contract.digest(),
        contract_snapshot=contract.to_dict(),
        thread_id=thread_id,
    )
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    first_generation = store.claim_dispatch(session.session_id)
    assert first_generation == 1
    assert store.record_dispatch_run(session.session_id, generation=first_generation, run_id="run-before-approval", status="running")
    action = {
        "tool_name": "python_repl",
        "action_class": "tool_governance",
        "contract_digest": contract.digest(),
        "arguments_digest": "sha256-exact-request",
    }
    store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="operator approval required")
    approval = store.request_approval(
        session.session_id,
        note="operator approval required",
        requester="apex.tool_policy",
        action=action,
    )
    assert approval is not None
    store.decide_approval(approval.approval_id, verdict="approved", operator="admin")

    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)

    class CompletedRunManager:
        async def get(self, run_id, *, user_id=None, raise_on_store_error=False):
            assert run_id in {"run-before-approval", "run-after-approval"}
            assert user_id == owner
            return SimpleNamespace(
                run_id=run_id,
                status=SimpleNamespace(value="success"),
                total_input_tokens=0,
                total_output_tokens=0,
                llm_call_count=0,
            )

    admitted: list[tuple[str, int]] = []

    async def launch(*, app, session, generation):
        admitted.append((session.session_id, generation))
        return SimpleNamespace(run_id="run-after-approval", status=SimpleNamespace(value="running"))

    monkeypatch.setattr(gateway_services, "launch_apex_session_run", launch)
    app = SimpleNamespace(state=SimpleNamespace(run_manager=CompletedRunManager()))

    observed = await apex_execution_tick(app, session_id=session.session_id)
    assert observed["approval_requeued"] == 1
    requeued = store.get(session.session_id)
    assert requeued.run_id == ""
    assert requeued.dispatch_state == "idle"
    assert requeued.state is ApexSessionState.ACTIVE

    dispatched = await apex_execution_tick(app, session_id=session.session_id)
    assert dispatched["dispatched"] == 1
    assert admitted == [(session.session_id, 2)]
    linked = store.get(session.session_id)
    assert linked.run_id == "run-after-approval"
    assert linked.dispatch_generation == 2

    finished_again = await apex_execution_tick(app, session_id=session.session_id)
    assert finished_again["approval_requeued"] == 0
    assert finished_again["awaiting_verification"] == 1
    assert store.get(session.session_id).run_id == "run-after-approval"
    assert admitted == [(session.session_id, 2)]


@pytest.mark.asyncio
async def test_apex_execution_tick_reaches_sessions_after_the_first_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module
    import app.gateway.services as gateway_services
    from app.gateway.autonomy.loops import apex_execution_tick

    owner = "operator"
    thread_id = "old-apex-session"
    contract = profile_for("autonomous")
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "autonomous", owner=owner)
    session = store.create(
        owner=owner,
        objective="older goal remains dispatchable",
        profile="autonomous",
        contract_digest=contract.digest(),
        contract_snapshot=contract.to_dict(),
        thread_id=thread_id,
    )
    store.update(session.session_id, created_at=1.0)

    # Seed a full first page of newer, disabled sessions without turning this
    # regression into 200 unrelated persistence writes.
    for index in range(200):
        placeholder = ApexSession(
            session_id=f"placeholder-{index:03d}",
            owner="disabled-owner",
            objective="not enabled",
            created_at=1000.0 + index,
        )
        store._rows[placeholder.session_id] = placeholder

    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)
    admitted: list[str] = []

    async def launch(*, app, session, generation):
        admitted.append(session.session_id)
        return SimpleNamespace(run_id="run-apex-old", status=SimpleNamespace(value="running"))

    monkeypatch.setattr(gateway_services, "launch_apex_session_run", launch)
    app = SimpleNamespace(state=SimpleNamespace(run_manager=SimpleNamespace()))

    result = await apex_execution_tick(app)

    assert result["sessions"] == 201
    assert result["dispatched"] == 1
    assert admitted == [session.session_id]


@pytest.mark.asyncio
async def test_apex_execution_tick_can_dispatch_a_session_outside_the_default_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module
    import app.gateway.services as gateway_services
    from app.gateway.autonomy.loops import apex_execution_tick

    owner = "operator"
    thread_id = "direct-old-apex-session"
    contract = profile_for("autonomous")
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "autonomous", owner=owner)
    session = store.create(
        owner=owner,
        objective="dispatch by direct lookup",
        profile="autonomous",
        contract_digest=contract.digest(),
        contract_snapshot=contract.to_dict(),
        thread_id=thread_id,
    )
    store.update(session.session_id, created_at=1.0)
    for index in range(200):
        placeholder = ApexSession(
            session_id=f"newer-placeholder-{index:03d}",
            owner="disabled-owner",
            objective="not enabled",
            created_at=1000.0 + index,
        )
        store._rows[placeholder.session_id] = placeholder

    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)
    admitted: list[str] = []

    async def launch(*, app, session, generation):
        admitted.append(session.session_id)
        return SimpleNamespace(run_id="run-apex-direct-old", status=SimpleNamespace(value="running"))

    monkeypatch.setattr(gateway_services, "launch_apex_session_run", launch)
    app = SimpleNamespace(state=SimpleNamespace(run_manager=SimpleNamespace()))

    result = await apex_execution_tick(app, session_id=session.session_id)

    assert result["sessions"] == 1
    assert result["dispatched"] == 1
    assert admitted == [session.session_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("measured", [True, False, None])
async def test_completed_apex_run_uses_registered_acceptance_probes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, measured: bool | None) -> None:
    import alpha.apex.executive as executive_module
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import alpha.mission.acceptance as acceptance_module
    import app.gateway.autonomy.supervisor as supervisor_module
    from alpha.mission.acceptance import AcceptanceRegistry
    from app.gateway.autonomy.loops import apex_execution_tick

    owner = "operator"
    thread_id = f"apex-acceptance-{measured}"
    contract = profile_for("autonomous")
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "autonomous", owner=owner)
    session = store.create(
        owner=owner,
        objective="finish work and evaluate it",
        profile="autonomous",
        contract_digest=contract.digest(),
        contract_snapshot=contract.to_dict(),
        thread_id=thread_id,
        acceptance_criteria=["the result is correct"],
    )
    store.update(
        session.session_id,
        state=ApexSessionState.ACTIVE,
        dispatch_state="running",
        run_id="run-apex-acceptance",
        run_status="running",
    )
    registry = AcceptanceRegistry()
    registry.register("test_probe", lambda _criterion: measured)
    monkeypatch.setattr(acceptance_module, "get_acceptance_registry", lambda: registry)
    monkeypatch.setattr(executive_module, "_fleet_stopped", lambda: (False, ""))
    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)

    class CompletedRunManager:
        async def get(self, run_id, *, user_id=None, raise_on_store_error=False):
            assert run_id == "run-apex-acceptance"
            assert user_id == owner
            return SimpleNamespace(
                run_id=run_id,
                status=SimpleNamespace(value="completed"),
                total_input_tokens=10,
                total_output_tokens=5,
                llm_call_count=1,
            )

    result = await apex_execution_tick(SimpleNamespace(state=SimpleNamespace(run_manager=CompletedRunManager())), session_id=session.session_id)

    updated = store.get(session.session_id)
    assert updated is not None
    if measured:
        assert updated.acceptance["evaluator"] == "test_probe"
        assert updated.acceptance["passed"] is True
        assert updated.state is ApexSessionState.COMPLETED
        assert result["completed"] == 1
        assert result["awaiting_verification"] == 0
    elif measured is False:
        assert updated.acceptance is None
        assert updated.acceptance_history[-1]["evaluator"] == "test_probe"
        assert updated.acceptance_history[-1]["passed"] is False
        assert updated.state is ApexSessionState.ACTIVE
        assert updated.run_id == ""
        assert updated.dispatch_state == "idle"
        assert updated.usage.replans == 1
        assert result["replanned"] == 1
    else:
        assert updated.acceptance is None
        assert updated.state is ApexSessionState.ACTIVE
        assert updated.run_id == "run-apex-acceptance"
        assert updated.dispatch_state == "awaiting_verification"
        assert result["awaiting_verification"] == 1
        assert result["completed"] == 0
    acceptance_events = [event for event in store.read_events(session.session_id) if event.event_type == "acceptance.registry_report_submitted"]
    assert bool(acceptance_events) is (measured is not None)


@pytest.mark.asyncio
async def test_apex_dispatch_failure_is_parked_instead_of_left_starting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module
    import app.gateway.services as gateway_services
    from app.gateway.autonomy.loops import apex_execution_tick

    owner = "operator"
    thread_id = "thread-apex-failure"
    contract = profile_for("apex_max")
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "apex_max", owner=owner)
    session = store.create(
        owner=owner,
        objective="inspect local health",
        profile="apex_max",
        contract_digest=contract.digest(),
        contract_snapshot=contract.to_dict(),
        thread_id=thread_id,
    )
    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)

    async def fail_launch(*, app, session, generation):
        raise ValueError("missing thread")

    monkeypatch.setattr(gateway_services, "launch_apex_session_run", fail_launch)
    app = SimpleNamespace(state=SimpleNamespace(run_manager=SimpleNamespace()))

    result = await apex_execution_tick(app, session_id=session.session_id)
    failed = store.get(session.session_id)
    assert result["failed"] == 1
    assert result["errors"]
    assert failed.dispatch_state == "failed"
    assert failed.run_status == "dispatch_error"
    assert failed.blocked_reason == "ValueError: missing thread"


@pytest.mark.asyncio
async def test_apex_counts_persisted_usage_events_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module
    from app.gateway.autonomy.loops import apex_execution_tick

    owner, thread_id = "operator", "thread-apex-events"
    contract = profile_for("apex_max")
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "apex_max", owner=owner)
    session = store.create(owner=owner, objective="inspect", profile="apex_max", contract_digest=contract.digest(), contract_snapshot=contract.to_dict(), thread_id=thread_id)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    generation = store.claim_dispatch(session.session_id)
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-events", status="running")
    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)

    class FakeEventStore:
        async def list_events(self, thread, run, *, event_types, limit, after_seq):
            assert (thread, run) == (thread_id, "run-events")
            assert event_types == ["llm.ai.response", "subagent.end"]
            return [{"seq": 8, "event_type": "llm.ai.response", "metadata": {"usage": {"input_tokens": 80, "output_tokens": 20}}, "content": {}}] if after_seq < 8 else []

    app = SimpleNamespace(state=SimpleNamespace(run_manager=SimpleNamespace(get=lambda *a, **kw: None), run_event_store=FakeEventStore()))

    class FakeRunManager:
        async def get(self, run_id, *, user_id=None, raise_on_store_error=False):
            return SimpleNamespace(run_id=run_id, status=SimpleNamespace(value="running"), total_input_tokens=80, total_output_tokens=20, llm_call_count=1)

    app.state.run_manager = FakeRunManager()
    await apex_execution_tick(app, session_id=session.session_id)
    await apex_execution_tick(app, session_id=session.session_id)
    usage = ApexStore(store.storage_path).get(session.session_id).usage
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.llm_calls) == (80, 20, 100, 1)
    assert usage.event_cursors == {"run-events": 8}
    assert usage.event_usage_runs == ["run-events"]
    assert usage.event_usage_totals == {"run-events": {"input_tokens": 80, "output_tokens": 20, "llm_calls": 1}}


@pytest.mark.asyncio
async def test_usage_less_events_do_not_suppress_cumulative_run_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module
    from app.gateway.autonomy.loops import apex_execution_tick

    owner, thread_id = "operator", "thread-apex-usage-less-events"
    contract = profile_for("apex_max")
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "apex_max", owner=owner)
    session = store.create(owner=owner, objective="inspect", profile="apex_max", contract_digest=contract.digest(), contract_snapshot=contract.to_dict(), thread_id=thread_id)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    generation = store.claim_dispatch(session.session_id)
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-usage-less", status="running")
    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)

    class FakeEventStore:
        async def list_events(self, thread, run, *, event_types, limit, after_seq):
            assert (thread, run) == (thread_id, "run-usage-less")
            return [{"seq": 3, "event_type": "llm.ai.response", "metadata": {}, "content": {}}] if after_seq < 3 else []

    class FakeRunManager:
        async def get(self, run_id, *, user_id=None, raise_on_store_error=False):
            return SimpleNamespace(run_id=run_id, status=SimpleNamespace(value="running"), total_input_tokens=91, total_output_tokens=9, llm_call_count=1)

    app = SimpleNamespace(state=SimpleNamespace(run_manager=FakeRunManager(), run_event_store=FakeEventStore()))
    await apex_execution_tick(app, session_id=session.session_id)
    await apex_execution_tick(app, session_id=session.session_id)

    usage = ApexStore(store.storage_path).get(session.session_id).usage
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.llm_calls) == (91, 9, 100, 1)
    assert usage.event_cursors == {"run-usage-less": 3}
    assert usage.event_usage_runs == []


@pytest.mark.asyncio
async def test_later_usage_less_event_reconciles_to_cumulative_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module
    from app.gateway.autonomy.loops import apex_execution_tick

    owner, thread_id = "operator", "thread-apex-mixed-usage-events"
    contract = profile_for("apex_max")
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "apex_max", owner=owner)
    session = store.create(owner=owner, objective="inspect", profile="apex_max", contract_digest=contract.digest(), contract_snapshot=contract.to_dict(), thread_id=thread_id)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    generation = store.claim_dispatch(session.session_id)
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-mixed-usage", status="running")
    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)

    class FakeEventStore:
        async def list_events(self, thread, run, *, event_types, limit, after_seq):
            assert (thread, run) == (thread_id, "run-mixed-usage")
            return (
                [
                    {"seq": 1, "event_type": "llm.ai.response", "metadata": {"usage": {"input_tokens": 40, "output_tokens": 4}}, "content": {}},
                    {"seq": 2, "event_type": "llm.ai.response", "metadata": {}, "content": {}},
                ]
                if after_seq < 2
                else []
            )

    class FakeRunManager:
        async def get(self, run_id, *, user_id=None, raise_on_store_error=False):
            return SimpleNamespace(run_id=run_id, status=SimpleNamespace(value="running"), total_input_tokens=91, total_output_tokens=9, llm_call_count=2)

    app = SimpleNamespace(state=SimpleNamespace(run_manager=FakeRunManager(), run_event_store=FakeEventStore()))
    await apex_execution_tick(app, session_id=session.session_id)
    await apex_execution_tick(app, session_id=session.session_id)

    usage = ApexStore(store.storage_path).get(session.session_id).usage
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.llm_calls) == (91, 9, 100, 2)
    assert usage.event_cursors == {"run-mixed-usage": 2}
    assert usage.event_usage_runs == []
    assert usage.event_usage_totals == {}


@pytest.mark.parametrize("runtime_limit", [0, 1])
@pytest.mark.asyncio
async def test_apex_runtime_budget_interrupts_through_run_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_limit: int) -> None:
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module
    from alpha.apex.store import ApexSessionState
    from app.gateway.autonomy.loops import apex_execution_tick

    owner, thread_id = "operator", "thread-apex-budget"
    contract = narrow_contract(profile_for("apex_max"), budget={"max_runtime_minutes": runtime_limit})
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "apex_max", owner=owner)
    session = store.create(owner=owner, objective="bounded inspect", profile="apex_max", contract_digest=contract.digest(), contract_snapshot=contract.to_dict(), thread_id=thread_id)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    generation = store.claim_dispatch(session.session_id)
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-budget", status="running")
    store.update(session.session_id, dispatch_started_at=time.time() - (runtime_limit * 60 + 1))
    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)

    class FakeRunManager:
        def __init__(self):
            self.cancelled = []
            self.record = SimpleNamespace(run_id="run-budget", status=SimpleNamespace(value="running"), total_input_tokens=0, total_output_tokens=0, llm_call_count=0)

        async def get(self, run_id, *, user_id=None, raise_on_store_error=False):
            return self.record

        async def cancel(self, run_id, *, action):
            self.cancelled.append((run_id, action))
            self.record.status = SimpleNamespace(value="interrupted")

    manager = FakeRunManager()
    await apex_execution_tick(SimpleNamespace(state=SimpleNamespace(run_manager=manager)), session_id=session.session_id)
    assert manager.cancelled == [("run-budget", "interrupt")]
    assert store.get(session.session_id).run_status == "interrupted"


@pytest.mark.asyncio
async def test_apex_token_budget_interrupts_run_manager_at_live_observation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module
    from app.gateway.autonomy.loops import apex_execution_tick

    owner, thread_id = "operator", "thread-apex-token-budget"
    contract = narrow_contract(profile_for("apex_max"), budget={"max_total_tokens": 100})
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "apex_max", owner=owner)
    session = store.create(owner=owner, objective="bounded token use", profile="apex_max", contract_digest=contract.digest(), contract_snapshot=contract.to_dict(), thread_id=thread_id)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    generation = store.claim_dispatch(session.session_id)
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-token-budget", status="running")
    assert store.record_run_usage(session.session_id, run_id="run-token-budget", input_tokens=100, output_tokens=0, llm_calls=1)
    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)

    class FakeRunManager:
        def __init__(self):
            self.cancelled = []
            self.record = SimpleNamespace(run_id="run-token-budget", status=SimpleNamespace(value="running"), total_input_tokens=100, total_output_tokens=0, llm_call_count=1)

        async def get(self, run_id, *, user_id=None, raise_on_store_error=False):
            return self.record

        async def cancel(self, run_id, *, action):
            self.cancelled.append((run_id, action))
            self.record.status = SimpleNamespace(value="interrupted")

    manager = FakeRunManager()
    result = await apex_execution_tick(SimpleNamespace(state=SimpleNamespace(run_manager=manager)), session_id=session.session_id)
    assert manager.cancelled == [("run-token-budget", "interrupt")]
    assert result["budget_exhausted"] == 1
    assert store.get(session.session_id).run_status == "interrupted"


@pytest.mark.asyncio
async def test_zero_apex_token_budget_never_admits_a_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module
    import app.gateway.services as gateway_services
    from app.gateway.autonomy.loops import apex_execution_tick

    owner, thread_id = "operator", "thread-apex-zero-token-budget"
    contract = narrow_contract(profile_for("apex_max"), budget={"max_total_tokens": 0})
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "apex_max", owner=owner)
    session = store.create(owner=owner, objective="zero token budget", profile="apex_max", contract_digest=contract.digest(), contract_snapshot=contract.to_dict(), thread_id=thread_id)
    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)
    admitted = []

    async def launch(*, app, session, generation):
        admitted.append(session.session_id)
        return SimpleNamespace(run_id="unexpected", status=SimpleNamespace(value="running"))

    monkeypatch.setattr(gateway_services, "launch_apex_session_run", launch)
    result = await apex_execution_tick(SimpleNamespace(state=SimpleNamespace(run_manager=SimpleNamespace())), session_id=session.session_id)

    assert admitted == []
    assert result["budget_exhausted"] == 1
    assert store.get(session.session_id).dispatch_state == "failed"


def _linked_apex_session(tmp_path: Path, *, thread_id: str, profile: str, run_id: str) -> tuple[ApexStore, ApexModeStore, ApexSession]:
    """One ACTIVE session already linked to a dispatched run, plus its stores."""
    contract = profile_for(profile)
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, profile, owner="operator")
    session = store.create(
        owner="operator",
        objective="observe the linked run",
        profile=profile,
        contract_digest=contract.digest(),
        contract_snapshot=contract.to_dict(),
        thread_id=thread_id,
    )
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    generation = store.claim_dispatch(session.session_id)
    assert generation is not None
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id=run_id, status="running")
    return store, modes, session


def _patch_apex_environment(monkeypatch: pytest.MonkeyPatch, store: ApexStore, modes: ApexModeStore) -> None:
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module

    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)


@pytest.mark.asyncio
async def test_timed_out_apex_run_is_projected_terminal_not_stuck_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A RunManager ``timeout`` is terminal; the dispatch projection said otherwise.

    ``RunStatus.timeout`` is terminal in ``services._TERMINAL_RUN_STATUSES``, in
    the durable ``SessionState`` machine, and in edit-replay visibility, but the
    dispatcher's failure set omitted it. Recording it verbatim made
    ``record_run_status`` fall into its ``else`` branch, so a timed-out run was
    stored as ``running`` -- which ``claim_dispatch`` refuses, which
    ``requeue_approved_tool_action`` refuses, which ``replan_failed_run``
    refuses, and which ``record_run_status`` then re-derives on every later
    tick. The session could never move again while the summary claimed live
    work RunManager had already terminalized.
    """
    from app.gateway.autonomy.loops import apex_execution_tick

    thread_id = "thread-apex-timeout"
    store, modes, session = _linked_apex_session(tmp_path, thread_id=thread_id, profile="apex_max", run_id="run-timeout")
    _patch_apex_environment(monkeypatch, store, modes)

    class TimedOutRunManager:
        async def get(self, run_id, *, user_id=None, raise_on_store_error=False):
            return SimpleNamespace(
                run_id=run_id,
                status=SimpleNamespace(value="timeout"),
                total_input_tokens=12,
                total_output_tokens=3,
                llm_call_count=2,
            )

    result = await apex_execution_tick(SimpleNamespace(state=SimpleNamespace(run_manager=TimedOutRunManager())), session_id=session.session_id)

    assert result["failed"] == 1
    assert result["running"] == 0
    stored = store.get(session.session_id)
    assert stored is not None
    assert stored.dispatch_state == "failed"
    # The dispatch projection has no "timeout" vocabulary, so the session row
    # carries the failure class and the observed name is journalled.
    assert stored.run_status == "error"
    assert [event.event_type for event in store.read_events(session.session_id) if event.event_type == "run.status_projected"] == ["run.status_projected"]
    projected = [event for event in store.read_events(session.session_id) if event.event_type == "run.status_projected"][0]
    assert projected.payload["observed_status"] == "timeout"
    assert projected.payload["run_id"] == "run-timeout"

    # The projection is the recovery boundary: a session parked in "running"
    # is one nothing can move, so the terminal projection must be a durable
    # state a later operator replan can act on.
    assert store.claim_dispatch(session.session_id) is None

    repeated = await apex_execution_tick(SimpleNamespace(state=SimpleNamespace(run_manager=TimedOutRunManager())), session_id=session.session_id)
    assert repeated["failed"] == 1
    assert repeated["running"] == 0


@pytest.mark.asyncio
async def test_runtime_budget_interrupt_is_journaled_and_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime ceiling interrupt must leave the same evidence as the token one.

    The token branch emits ``budget.tokens_exhausted`` and counts
    ``budget_exhausted``; the runtime branch cancelled the run and said nothing,
    so a stop caused by elapsed time appeared as a bare failure with no cause
    anywhere in the journal or in the status projection.
    """
    from app.gateway.autonomy.loops import apex_execution_tick

    owner, thread_id = "operator", "thread-apex-runtime-journal"
    runtime_limit = 1
    contract = narrow_contract(profile_for("apex_max"), budget={"max_runtime_minutes": runtime_limit})
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "apex_max", owner=owner)
    session = store.create(owner=owner, objective="bounded inspect", profile="apex_max", contract_digest=contract.digest(), contract_snapshot=contract.to_dict(), thread_id=thread_id)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    generation = store.claim_dispatch(session.session_id)
    assert store.record_dispatch_run(session.session_id, generation=generation, run_id="run-runtime-budget", status="running")
    store.update(session.session_id, dispatch_started_at=time.time() - (runtime_limit * 60 + 1))
    _patch_apex_environment(monkeypatch, store, modes)

    class FakeRunManager:
        def __init__(self):
            self.cancelled = []
            self.record = SimpleNamespace(run_id="run-runtime-budget", status=SimpleNamespace(value="running"), total_input_tokens=0, total_output_tokens=0, llm_call_count=0)

        async def get(self, run_id, *, user_id=None, raise_on_store_error=False):
            return self.record

        async def cancel(self, run_id, *, action):
            self.cancelled.append((run_id, action))
            self.record.status = SimpleNamespace(value="interrupted")

    manager = FakeRunManager()
    result = await apex_execution_tick(SimpleNamespace(state=SimpleNamespace(run_manager=manager)), session_id=session.session_id)

    assert manager.cancelled == [("run-runtime-budget", "interrupt")]
    assert result["budget_exhausted"] == 1
    assert store.get(session.session_id).run_status == "interrupted"
    exhausted = [event for event in store.read_events(session.session_id) if event.event_type == "budget.runtime_exhausted"]
    assert len(exhausted) == 1
    assert exhausted[0].payload["run_id"] == "run-runtime-budget"
    assert exhausted[0].payload["limit_minutes"] == runtime_limit
    assert exhausted[0].payload["elapsed_minutes"] > runtime_limit


@pytest.mark.asyncio
async def test_unavailable_run_record_is_disclosed_not_counted_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A durable link with no readable record must not read as live work.

    The record is gone, not the work. Parking it here would invent a terminal
    state nobody measured, and counting it as ``running`` claims execution that
    cannot be observed, so it is journalled and counted in its own bucket.
    """
    from app.gateway.autonomy.loops import apex_execution_tick

    thread_id = "thread-apex-missing-record"
    store, modes, session = _linked_apex_session(tmp_path, thread_id=thread_id, profile="autonomous", run_id="run-vanished")
    _patch_apex_environment(monkeypatch, store, modes)

    class MissingRunManager:
        async def get(self, run_id, *, user_id=None, raise_on_store_error=False):
            return None

    result = await apex_execution_tick(SimpleNamespace(state=SimpleNamespace(run_manager=MissingRunManager())), session_id=session.session_id)

    assert result["unobserved"] == 1
    assert result["running"] == 0
    assert any(error.get("run_id") == "run-vanished" for error in result["errors"])
    unavailable = [event for event in store.read_events(session.session_id) if event.event_type == "run.record_unavailable"]
    assert len(unavailable) == 1
    assert unavailable[0].payload["run_id"] == "run-vanished"
    stored = store.get(session.session_id)
    assert stored is not None
    assert stored.dispatch_state == "running"
    # Nothing was measured, so no terminal state is fabricated either.
    assert store.claim_dispatch(session.session_id) is None


@pytest.mark.asyncio
async def test_unreadable_run_status_is_not_recorded_as_a_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``status=None`` used to be flattened into the status name ``"none"``.

    The nested ``getattr`` fell through to ``str(None)``, so a record whose
    status could not be read was written to the session row as the literal
    string ``"none"`` and counted as a live run on every later tick.
    """
    from app.gateway.autonomy.loops import apex_execution_tick

    thread_id = "thread-apex-unreadable-status"
    store, modes, session = _linked_apex_session(tmp_path, thread_id=thread_id, profile="autonomous", run_id="run-stateless")
    _patch_apex_environment(monkeypatch, store, modes)

    class StatelessRunManager:
        async def get(self, run_id, *, user_id=None, raise_on_store_error=False):
            return SimpleNamespace(run_id=run_id, status=None, total_input_tokens=0, total_output_tokens=0, llm_call_count=0)

    result = await apex_execution_tick(SimpleNamespace(state=SimpleNamespace(run_manager=StatelessRunManager())), session_id=session.session_id)

    assert result["unobserved"] == 1
    assert result["running"] == 0
    stored = store.get(session.session_id)
    assert stored is not None
    assert stored.run_status != "none"
    assert [event.event_type for event in store.read_events(session.session_id) if event.event_type == "run.status_unreadable"] == ["run.status_unreadable"]


@pytest.mark.asyncio
async def test_refused_dispatch_link_parks_instead_of_redispatching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run the session will not link must not be re-admitted every tick.

    ``record_dispatch_run`` refuses when the session already carries another
    run, when it moved past ``starting``, or when its generation no longer
    matches. Leaving the row in ``starting`` meant the *same* generation was
    re-claimed on every pass, so the dispatcher re-entered run admission
    forever for a session that could never link.
    """
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module
    import app.gateway.services as gateway_services
    from app.gateway.autonomy.loops import apex_execution_tick

    owner, thread_id = "operator", "thread-apex-link-refused"
    contract = profile_for("apex_max")
    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    modes.enable(thread_id, "apex_max", owner=owner)
    session = store.create(owner=owner, objective="link is refused", profile="apex_max", contract_digest=contract.digest(), contract_snapshot=contract.to_dict(), thread_id=thread_id)
    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)

    admitted: list[tuple[str, int]] = []

    async def launch(*, app, session, generation):
        admitted.append((session.session_id, generation))
        return SimpleNamespace(run_id="run-refused-link", status=SimpleNamespace(value="running"))

    monkeypatch.setattr(gateway_services, "launch_apex_session_run", launch)
    # The row refuses the link: another observer already owns it.
    monkeypatch.setattr(store, "record_dispatch_run", lambda *args, **kwargs: False)

    result = await apex_execution_tick(SimpleNamespace(state=SimpleNamespace(run_manager=SimpleNamespace())), session_id=session.session_id)

    assert admitted == [(session.session_id, 1)]
    assert result["dispatched"] == 0
    assert result["failed"] == 1
    refusal = [error for error in result["errors"] if error.get("parked") is True]
    assert refusal and refusal[0]["run_id"] == "run-refused-link"
    stored = store.get(session.session_id)
    assert stored is not None
    assert stored.dispatch_state == "failed"
    assert stored.run_status == "dispatch_error"
    assert stored.blocked_reason.startswith("run was admitted but the session dispatch link changed")

    # The parked row is a terminal dispatch state, so the next pass reports it
    # rather than claiming the same generation and re-entering admission.
    repeat = await apex_execution_tick(SimpleNamespace(state=SimpleNamespace(run_manager=SimpleNamespace())), session_id=session.session_id)
    assert admitted == [(session.session_id, 1)]
    assert repeat["failed"] == 1
    assert repeat["dispatched"] == 0


@pytest.mark.asyncio
async def test_unparkable_dispatch_failure_does_not_abort_the_whole_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One session whose failure cannot be journalled must not strand the rest.

    Parking a dispatch writes through the same store transaction that just
    failed, so it can fail too. An unguarded raise escapes the adapter: the
    remaining sessions of the pass are never examined, and a raising adapter is
    counted against the supervisor's restart budget until it parks the loop for
    every session.
    """
    import alpha.apex.mode as mode_module
    import alpha.apex.store as store_module
    import app.gateway.autonomy.supervisor as supervisor_module
    import app.gateway.services as gateway_services
    from alpha.apex.store import ApexPersistenceError
    from app.gateway.autonomy.loops import apex_execution_tick

    store = ApexStore(tmp_path / "sessions.json")
    modes = ApexModeStore(tmp_path / "mode.json")
    contract = profile_for("apex_max")

    # The older, healthy session is dispatched; the newer one cannot park.
    healthy = store.create(
        owner="operator",
        objective="healthy neighbour",
        profile="apex_max",
        contract_digest=contract.digest(),
        contract_snapshot=contract.to_dict(),
        thread_id="thread-apex-neighbour-ok",
    )
    store.update(healthy.session_id, created_at=1.0)
    modes.enable("thread-apex-neighbour-ok", "apex_max", owner="operator")
    broken = store.create(
        owner="operator",
        objective="cannot be parked",
        profile="apex_max",
        contract_digest=contract.digest(),
        contract_snapshot=contract.to_dict(),
        thread_id="thread-apex-neighbour-broken",
    )
    modes.enable("thread-apex-neighbour-broken", "apex_max", owner="operator")

    monkeypatch.setattr(store_module, "get_apex_store", lambda: store)
    monkeypatch.setattr(mode_module, "get_apex_mode_store", lambda: modes)
    monkeypatch.setattr(supervisor_module, "_fleet_admits_tick", lambda _loop_id: True)

    def raising_park(*_args, **_kwargs) -> bool:
        raise ApexPersistenceError("Could not persist APEX session changes")

    monkeypatch.setattr(store, "record_dispatch_failure", raising_park)

    admitted: list[str] = []

    async def launch(*, app, session, generation):
        if session.session_id == broken.session_id:
            raise ValueError("missing thread")
        admitted.append(session.session_id)
        return SimpleNamespace(run_id="run-neighbour", status=SimpleNamespace(value="running"))

    monkeypatch.setattr(gateway_services, "launch_apex_session_run", launch)

    result = await apex_execution_tick(SimpleNamespace(state=SimpleNamespace(run_manager=SimpleNamespace())))

    assert admitted == [healthy.session_id]
    assert result["sessions"] == 2
    assert result["dispatched"] == 1
    unparsed = [error for error in result["errors"] if "park_error" in error]
    assert len(unparsed) == 1
    assert unparsed[0]["session_id"] == broken.session_id
    assert unparsed[0]["park_error"] == "dispatch failure could not be persisted; inspect the session row"
    # The row keeps the honest state rather than being reported as parked.
    stored = store.get(broken.session_id)
    assert stored is not None
    assert stored.dispatch_state == "starting"
    assert stored.blocked_reason == ""


@pytest.mark.asyncio
async def test_launch_apex_session_run_refuses_a_stale_dispatch_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Admission and the durable claim must sit behind one generation.

    ``start_run`` re-reads the session and stamps whatever generation it finds
    into run metadata, while the idempotency key is built from the caller's
    parameter. A superseded generation therefore admitted a durable run under an
    obsolete key, which ``record_dispatch_run`` then refused -- an unobserved
    worker plus a session parked in ``starting``.
    """
    import app.gateway.services as gateway_services

    owner, thread_id = "operator", "thread-apex-stale-generation"
    contract = profile_for("apex_max")
    store = ApexStore(tmp_path / "sessions.json")
    session = store.create(owner=owner, objective="stale generation", profile="apex_max", contract_digest=contract.digest(), contract_snapshot=contract.to_dict(), thread_id=thread_id)
    store.set_state(session.session_id, ApexSessionState.ACTIVE)
    assert store.claim_dispatch(session.session_id) == 1

    started: list[str] = []

    async def unreachable_start_run(*_args, **_kwargs):
        started.append("called")
        raise AssertionError("stale generation must not reach run admission")

    monkeypatch.setattr(gateway_services, "start_run", unreachable_start_run)

    with pytest.raises(ValueError, match="dispatch generation 7 is not the durable generation 1"):
        await gateway_services.launch_apex_session_run(app=SimpleNamespace(), session=store.get(session.session_id), generation=7)

    assert started == []
