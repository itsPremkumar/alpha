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
    usage = store.get(session.session_id).usage
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.llm_calls) == (80, 20, 100, 1)
    assert usage.event_cursors == {"run-events": 8}


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
