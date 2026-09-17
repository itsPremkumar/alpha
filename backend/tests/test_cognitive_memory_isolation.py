import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import memory
from agent_workspace.config.paths import Paths, make_safe_user_id
from agent_workspace.memory.cognitive import engine
from agent_workspace.runtime.user_context import reset_current_user, set_current_user
from agent_workspace.tools.builtins.cognitive_memory_tool import cognitive_memory_tool


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "get_paths", lambda: Paths(tmp_path))
    monkeypatch.setattr(engine, "_owner_systems", {})


@contextmanager
def owner(user_id):
    token = set_current_user(SimpleNamespace(id=user_id))
    try:
        yield
    finally:
        reset_current_user(token)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(memory.router)
    with TestClient(app, raise_server_exceptions=False) as value:
        yield value


def test_owner_cache_restart_and_explicit_path_compatibility(tmp_path):
    with owner("alice"):
        alice = engine.get_cognitive_memory_system()
        assert alice is engine.get_cognitive_memory_system()
        with alice.operation():
            node = alice.semantic_graph.add_belief("PrivateAlice", "likes", "tea")
            alice.working_mem.add("ephemeral")
            alice.save_to_disk()
        explicit = engine.get_cognitive_memory_system(storage_dir=tmp_path / "explicit")
        assert explicit is not alice
        assert engine.get_cognitive_memory_system() is alice
    with owner("bob"):
        bob = engine.get_cognitive_memory_system()
        assert bob is not alice
        assert bob.semantic_graph.get_node(node.node_id) is None
        bob.save_to_disk()
    assert alice.storage_dir == tmp_path / "users" / "alice" / "cognitive_memory"
    assert bob.storage_dir == tmp_path / "users" / "bob" / "cognitive_memory"
    engine._owner_systems.clear()
    with owner("alice"):
        restarted = engine.get_cognitive_memory_system()
        assert restarted is not alice
        assert restarted.semantic_graph.get_node(node.node_id) is not None
        assert restarted.working_mem.list_active() == []
    with owner("bob"):
        assert engine.get_cognitive_memory_system().semantic_graph.get_node(node.node_id) is None
    with pytest.raises(ValueError):
        engine.get_cognitive_memory_system(storage_dir=tmp_path, user_id="alice")


def test_snapshot_preserves_events_beyond_display_limit(tmp_path):
    system = engine.CognitiveMemorySystem(storage_dir=tmp_path / "snapshot")
    expected = {}
    for index in range(750):
        event = system.spatio_temporal.record_event(
            title=f"Event {index}",
            description=f"Observation {index}",
            timestamp=float(index + 1),
            valid_to=float(index + 1000),
            entities=[f"entity-{index}"],
        )
        expected[event.event_id] = event.to_dict()
    system.save_to_disk()
    restarted = engine.CognitiveMemorySystem(storage_dir=system.storage_dir)
    actual = {event.event_id: event.to_dict() for event in restarted.spatio_temporal.list_events(limit=1000)}
    for event_id, value in expected.items():
        assert actual.get(event_id) == value
    assert len(actual) == 751


@pytest.mark.no_auto_user
def test_no_owner_service_and_tool_fail_closed(tmp_path):
    for factory in (engine.CognitiveMemorySystem, engine.get_cognitive_memory_system):
        with pytest.raises(RuntimeError):
            factory()
    for action in ("recall", "store_belief", "lookup_skill", "record_step", "overview"):
        with pytest.raises(RuntimeError, match="requires an owner"):
            cognitive_memory_tool.func(action=action, runtime=SimpleNamespace(context={}))
    assert not engine._owner_systems
    assert not (tmp_path / "users").exists()


@pytest.mark.parametrize("user_id", ["", " ", "../alice", "alice/bob", "alice\\bob"])
def test_invalid_owner_never_selects_storage(user_id):
    with pytest.raises(ValueError):
        engine.get_cognitive_memory_system(user_id=user_id)
    assert not engine._owner_systems


ROUTES = [
    ("GET", "overview", None),
    ("POST", "recall", {"query": "private"}),
    ("GET", "working", None),
    ("POST", "working", {"content": "private"}),
    ("DELETE", "working", None),
    ("GET", "episodic", None),
    ("POST", "episodic", {"action": "test", "observation": "ok"}),
    ("DELETE", "episodic/missing", None),
    ("GET", "semantic", None),
    ("POST", "semantic", {"subject": "test", "predicate": "is", "object_val": "private"}),
    ("DELETE", "semantic/missing", None),
    ("GET", "procedural", None),
    ("POST", "procedural", {"name": "test", "description": "test", "trigger_pattern": "test"}),
    ("DELETE", "procedural/missing", None),
    ("POST", "consolidate", None),
    ("POST", "reconcile", None),
]


@pytest.mark.no_auto_user
@pytest.mark.parametrize("method,path,payload", ROUTES)
def test_all_routes_require_owner(client, method, path, payload):
    response = client.request(method, f"/api/memory/cognitive/{path}", json=payload, headers={"X-DeerFlow-Owner-User-Id": "alice"}, params={"user_id": "alice"})
    assert response.status_code == 401
    assert not engine._owner_systems


def test_api_owners_cannot_read_mutate_or_delete_each_other(client):
    with owner("alice"):
        belief = client.post("/api/memory/cognitive/semantic", json={"subject": "AliceUnique", "predicate": "likes", "object_val": "tea", "user_id": "bob"}).json()
        trace = client.post("/api/memory/cognitive/episodic", json={"action": "AliceUnique", "observation": "ok"}).json()
        skill = client.post("/api/memory/cognitive/procedural", json={"name": "AliceUnique", "description": "private", "trigger_pattern": "AliceUnique"}).json()
        client.post("/api/memory/cognitive/working", json={"content": "AliceUnique"})
    with owner("bob"):
        assert client.get("/api/memory/cognitive/working").json() == []
        assert client.get("/api/memory/cognitive/episodic").json() == []
        assert client.get("/api/memory/cognitive/episodic?mode=episode").json() == []
        for path in ("semantic", "procedural", "overview"):
            result = client.get(f"/api/memory/cognitive/{path}", headers={"X-DeerFlow-Owner-User-Id": "alice"}, params={"user_id": "alice"})
            assert result.status_code == 200
            assert "AliceUnique" not in result.text
        recalled = client.post("/api/memory/cognitive/recall", json={"query": "AliceUnique"})
        assert recalled.status_code == 200
        assert "AliceUnique" not in json.dumps(recalled.json())
        for path, item_id in (("semantic", belief["node_id"]), ("episodic", trace["trace_id"]), ("procedural", skill["skill_id"])):
            assert client.delete(f"/api/memory/cognitive/{path}/{item_id}").status_code == 404
        assert client.delete("/api/memory/cognitive/working").json()["count"] == 0
        assert client.post("/api/memory/cognitive/reconcile").status_code == 200
        assert client.post("/api/memory/cognitive/consolidate").status_code == 200
    with owner("alice"):
        assert len(client.get("/api/memory/cognitive/working").json()) == 1
        assert any(n["node_id"] == belief["node_id"] for n in client.get("/api/memory/cognitive/semantic").json()["nodes"])
    engine._owner_systems.clear()
    with owner("alice"):
        assert any(t["trace_id"] == trace["trace_id"] for t in client.get("/api/memory/cognitive/episodic").json())


@pytest.mark.no_auto_user
def test_internal_owner_requires_server_stamped_internal_principal(tmp_path):
    app = FastAPI()

    @app.middleware("http")
    async def trusted_internal(request, call_next):
        request.state.user = SimpleNamespace(id="default", system_role="internal")
        return await call_next(request)

    app.include_router(memory.router)
    with TestClient(app) as client:
        assert client.get("/api/memory/cognitive/overview").status_code == 401
        response = client.post("/api/memory/cognitive/episodic", headers={"X-DeerFlow-Owner-User-Id": "owner@example.test"}, json={"action": "test", "observation": "ok"})
        assert response.status_code == 200
    assert (tmp_path / "users" / make_safe_user_id("owner@example.test") / "cognitive_memory" / "cognitive_state.json").exists()


@pytest.mark.no_auto_user
def test_tool_runtime_scope_schema_and_precedence():
    runtime = SimpleNamespace(context={"user_id": "alice"})
    stored = json.loads(cognitive_memory_tool.func(action="store_belief", runtime=runtime, subject="PrivateAlice", predicate="likes", object_val="tea"))
    assert stored["status"] == "stored"
    runtime.context = {"user_id": "bob"}
    result = json.loads(cognitive_memory_tool.func(action="recall", runtime=runtime, query="PrivateAlice"))
    assert "PrivateAlice" not in json.dumps(result["results"])
    runtime.server_info = SimpleNamespace(user=SimpleNamespace(identity="alice"))
    result = cognitive_memory_tool.func(action="recall", runtime=runtime, query="PrivateAlice")
    assert "PrivateAlice" in result
    assert not {"runtime", "user_id", "owner", "storage_dir"} & cognitive_memory_tool.tool_call_schema.model_fields.keys()
    from agent_workspace.tools.tools import BUILTIN_TOOLS

    assert cognitive_memory_tool in BUILTIN_TOOLS


@pytest.mark.parametrize("failure", ["replace", "fsync", "serialize"])
def test_atomic_failure_preserves_snapshot_and_rolls_back(tmp_path, monkeypatch, failure):
    system = engine.CognitiveMemorySystem(storage_dir=tmp_path)
    system.save_to_disk()
    target = tmp_path / "cognitive_state.json"
    before = target.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("injected write failure")

    if failure == "serialize":
        monkeypatch.setattr(engine.json, "dumps", fail)
    else:
        monkeypatch.setattr(engine.os, failure, fail)
    with pytest.raises(OSError, match="injected"):
        with system.operation():
            system.semantic_graph.add_belief("Uncommitted", "is", "private")
            system.save_to_disk()
    assert target.read_bytes() == before
    assert not system.semantic_graph.list_nodes(subject="Uncommitted")
    assert not list(tmp_path.glob(".cognitive-*.tmp"))


def test_api_and_tool_do_not_report_failed_writes_as_success(client, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("injected write failure")

    monkeypatch.setattr(engine.os, "replace", fail)
    with owner("alice"):
        response = client.post("/api/memory/cognitive/semantic", json={"subject": "Uncommitted", "predicate": "is", "object_val": "private"})
        assert response.status_code == 500
        assert engine.get_cognitive_memory_system().semantic_graph.list_nodes(subject="Uncommitted") == []
    with pytest.raises(OSError):
        cognitive_memory_tool.func(action="record_step", runtime=SimpleNamespace(context={"user_id": "bob"}), query="Uncommitted", observation="ok")
    assert engine.get_cognitive_memory_system(user_id="bob").episodic_mem.list_traces() == []


def test_corrupt_snapshot_fails_closed(tmp_path):
    directory = tmp_path / "users" / "alice" / "cognitive_memory"
    directory.mkdir(parents=True)
    target = directory / "cognitive_state.json"
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        engine.get_cognitive_memory_system(user_id="alice")
    assert not engine._owner_systems
    assert target.read_text(encoding="utf-8") == "{broken"


def test_concurrent_owner_cache_and_mutations():
    def write(index):
        system = engine.get_cognitive_memory_system(user_id="alice")
        with system.operation():
            system.episodic_mem.record_trace(action=f"step-{index}", observation="ok")
            system.save_to_disk()
        return system

    with ThreadPoolExecutor(max_workers=4) as pool:
        systems = list(pool.map(write, range(16)))
    assert all(system is systems[0] for system in systems)
    assert len(systems[0].episodic_mem.list_traces()) == 16
    engine._owner_systems.clear()
    assert len(engine.get_cognitive_memory_system(user_id="alice").episodic_mem.list_traces()) == 16


def test_owner_cache_evicts_least_recently_used(monkeypatch):
    monkeypatch.setattr(engine, "_MAX_OWNER_SYSTEMS", 2)
    alice = engine.get_cognitive_memory_system(user_id="alice")
    engine.get_cognitive_memory_system(user_id="bob")
    assert engine.get_cognitive_memory_system(user_id="alice") is alice
    charlie = engine.get_cognitive_memory_system(user_id="charlie")
    assert list(key[0] for key in engine._owner_systems) == ["alice", "charlie"]
    assert engine.get_cognitive_memory_system(user_id="charlie") is charlie
    for index in range(8):
        engine.get_cognitive_memory_system(user_id=f"owner-{index}")
        assert len(engine._owner_systems) == 2


def test_evicted_owner_reloads_persisted_data(monkeypatch):
    monkeypatch.setattr(engine, "_MAX_OWNER_SYSTEMS", 1)
    alice = engine.get_cognitive_memory_system(user_id="alice")
    with alice.operation():
        node = alice.semantic_graph.add_belief("PrivateAlice", "likes", "tea")
        trace = alice.episodic_mem.record_trace(action="PrivateAlice", observation="saved")
        alice.save_to_disk()
    bob = engine.get_cognitive_memory_system(user_id="bob")
    assert alice not in engine._owner_systems.values()
    assert bob.semantic_graph.get_node(node.node_id) is None
    reloaded = engine.get_cognitive_memory_system(user_id="alice")
    assert reloaded is not alice
    assert [saved.to_dict() for saved in reloaded.semantic_graph.list_nodes(subject="PrivateAlice")] == [node.to_dict()]
    assert reloaded.episodic_mem._traces[trace.trace_id].to_dict() == trace.to_dict()
    assert len(engine._owner_systems) == 1


def test_owner_cache_skips_busy_system(monkeypatch):
    from threading import Event

    monkeypatch.setattr(engine, "_MAX_OWNER_SYSTEMS", 2)
    alice = engine.get_cognitive_memory_system(user_id="alice")
    engine.get_cognitive_memory_system(user_id="bob")
    started = Event()
    release = Event()

    def operate():
        with alice.operation():
            started.set()
            assert release.wait(timeout=10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        busy = pool.submit(operate)
        try:
            assert started.wait(timeout=10)
            charlie = pool.submit(engine.get_cognitive_memory_system, user_id="charlie").result(timeout=5)
            assert list(key[0] for key in engine._owner_systems) == ["alice", "charlie"]
            assert alice in engine._owner_systems.values()
            assert charlie in engine._owner_systems.values()
        finally:
            release.set()
        busy.result(timeout=5)
    engine.get_cognitive_memory_system(user_id="dave")
    assert alice not in engine._owner_systems.values()
    assert len(engine._owner_systems) == 2


def test_owner_cache_skips_reentrant_busy_system(monkeypatch):
    monkeypatch.setattr(engine, "_MAX_OWNER_SYSTEMS", 2)
    alice = engine.get_cognitive_memory_system(user_id="alice")
    engine.get_cognitive_memory_system(user_id="bob")
    with alice.operation():
        engine.get_cognitive_memory_system(user_id="charlie")
        assert list(key[0] for key in engine._owner_systems) == ["alice", "charlie"]


def test_owner_cache_rejects_new_owner_when_all_systems_busy(monkeypatch):
    monkeypatch.setattr(engine, "_MAX_OWNER_SYSTEMS", 1)
    alice = engine.get_cognitive_memory_system(user_id="alice")
    with alice.operation():
        with pytest.raises(RuntimeError, match="cache is busy"):
            engine.get_cognitive_memory_system(user_id="bob")
        assert engine.get_cognitive_memory_system(user_id="alice") is alice
        assert len(engine._owner_systems) == 1
    engine.get_cognitive_memory_system(user_id="bob")
    assert alice not in engine._owner_systems.values()
