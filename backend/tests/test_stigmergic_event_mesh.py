"""Tests for the Autonomous Stigmergic Event Mesh and Swarm Pheromone Memory."""

from __future__ import annotations

import time

import pytest

from alpha.blackboard.stigmergic_event_mesh import (
    DEFAULT_HALF_LIFE_SEC,
    EventAction,
    EventStream,
    PheromoneType,
    StigmergicEvent,
    StigmergicEventMesh,
    StigmergicEventStore,
    default_mesh_path,
    exponential_decay,
    get_stigmergic_mesh,
)
from alpha.tools.builtins.stigmergic_mesh_tool import (
    emit_stigmergic_event,
    query_stigmergic_traces,
)


@pytest.fixture()
def mesh() -> StigmergicEventMesh:
    """Return a fresh in-memory mesh."""
    return StigmergicEventMesh()


# ---------------------------------------------------------------------------
# Decay mathematics
# ---------------------------------------------------------------------------


def test_exponential_decay_halves_at_half_life():
    assert exponential_decay(1.0, 300.0, 300.0) == pytest.approx(0.5)


def test_exponential_decay_quarter_after_two_half_lives():
    assert exponential_decay(8.0, 200.0, 100.0) == pytest.approx(2.0)


def test_exponential_decay_zero_half_life_disables_decay():
    assert exponential_decay(3.0, 10_000.0, 0.0) == 3.0


def test_exponential_decay_identity_at_zero_age():
    assert exponential_decay(2.5, 0.0, 100.0) == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def test_deposit_records_event(mesh):
    result = mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", strength=2.0)
    assert result["success"] is True
    assert result["action"] == "deposit"
    assert result["pheromone"] == "DEFECT_SUSPECT"
    assert result["strength"] == pytest.approx(2.0)


def test_pheromone_alias_is_normalized(mesh):
    result = mesh.emit("agent-a", "svc/pay.py", "refactor", strength=1.0)
    assert result["success"] is True
    assert result["pheromone"] == PheromoneType.UNDER_SURGICAL_REFACTOR.value


def test_unknown_pheromone_is_rejected(mesh):
    result = mesh.emit("agent-a", "svc/pay.py", "NOT_A_PHEROMONE")
    assert result["success"] is False
    assert "unknown pheromone" in result["error"]


def test_missing_agent_or_entity_is_rejected(mesh):
    assert mesh.emit("", "svc/pay.py", "DEFECT_SUSPECT")["success"] is False
    assert mesh.emit("agent-a", "", "DEFECT_SUSPECT")["success"] is False


def test_unknown_action_is_rejected(mesh):
    result = mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", action="explode")
    assert result["success"] is False
    assert "unknown action" in result["error"]


def test_negative_half_life_is_rejected(mesh):
    result = mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", half_life_sec=-1)
    assert result["success"] is False


def test_reinforce_adds_to_decayed_value():
    mesh = StigmergicEventMesh(default_half_life_sec=100.0)
    now = time.time()
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", strength=2.0, timestamp=now)
    result = mesh.emit(
        "agent-a",
        "svc/pay.py",
        "DEFECT_SUSPECT",
        action="reinforce",
        strength=1.0,
        timestamp=now + 100.0,
    )
    # 2.0 decays to 1.0 after one half-life, then 1.0 is added.
    assert result["strength"] == pytest.approx(2.0)


def test_release_removes_deposits(mesh):
    mesh.emit("agent-b", "svc/pay.py", "UNDER_SURGICAL_REFACTOR", strength=1.0)
    result = mesh.emit("agent-b", "svc/pay.py", "UNDER_SURGICAL_REFACTOR", action="release")
    assert result["success"] is True
    assert mesh.field(pheromone="UNDER_SURGICAL_REFACTOR") == []


def test_purge_removes_only_matching_pheromone(mesh):
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT")
    mesh.emit("agent-a", "svc/pay.py", "COVERAGE_GAP")
    mesh.emit("agent-a", "svc/pay.py", "COVERAGE_GAP", action="purge")
    remaining = {item.pheromone for item in mesh.field()}
    assert PheromoneType.COVERAGE_GAP not in remaining
    assert PheromoneType.DEFECT_SUSPECT in remaining


# ---------------------------------------------------------------------------
# Field aggregation
# ---------------------------------------------------------------------------


def test_field_aggregates_by_entity_and_pheromone(mesh):
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", strength=2.0)
    mesh.emit("agent-b", "svc/pay.py", "DEFECT_SUSPECT", strength=3.0)
    mesh.emit("agent-c", "svc/order.py", "CANARY_VERIFIED_STABLE", strength=1.0)
    field = mesh.field()
    assert len(field) == 2
    pay = next(item for item in field if item.entity == "svc/pay.py")
    # Deposits evaporate continuously, so allow for the few microseconds of
    # wall-clock time that elapse between emitting and reading the field.
    assert pay.effective_strength == pytest.approx(5.0, abs=1e-3)
    assert pay.contributors == 2


def test_field_applies_decay():
    mesh = StigmergicEventMesh(default_half_life_sec=60.0)
    now = time.time()
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", strength=4.0, timestamp=now - 120.0)
    field = mesh.field(now=now)
    assert field[0].effective_strength == pytest.approx(1.0)


def test_field_drops_evaporated_signals():
    mesh = StigmergicEventMesh(default_half_life_sec=1.0)
    now = time.time()
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", strength=1.0, timestamp=now - 60.0)
    assert mesh.field(now=now) == []


def test_field_filters_by_entity_and_pheromone(mesh):
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT")
    mesh.emit("agent-a", "svc/order.py", "DEFECT_SUSPECT")
    assert len(mesh.field(entity="svc/pay.py")) == 1
    assert len(mesh.field(pheromone="DEFECT_SUSPECT")) == 2
    assert mesh.field(pheromone="NOT_A_PHEROMONE") == []


# ---------------------------------------------------------------------------
# Coordination semantics
# ---------------------------------------------------------------------------


def test_locked_entities_report_owners(mesh):
    mesh.emit("agent-b", "svc/pay.py", "UNDER_SURGICAL_REFACTOR", strength=1.0)
    locks = mesh.locked_entities()
    assert len(locks) == 1
    assert locks[0]["entity"] == "svc/pay.py"
    assert locks[0]["owners"] == ["agent-b"]


def test_prioritize_ranks_defect_pressure_first(mesh):
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", strength=3.0)
    mesh.emit("agent-a", "svc/order.py", "DEFECT_SUSPECT", strength=1.0)
    ranked = mesh.prioritize()
    assert ranked[0]["entity"] == "svc/pay.py"
    assert ranked[0]["score"] > ranked[1]["score"]


def test_prioritize_penalises_refactor_locks(mesh):
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", strength=4.0)
    mesh.emit("agent-b", "svc/pay.py", "UNDER_SURGICAL_REFACTOR", strength=2.0)
    mesh.emit("agent-c", "svc/order.py", "DEFECT_SUSPECT", strength=4.0)
    ranked = mesh.prioritize()
    assert ranked[0]["entity"] == "svc/order.py"
    locked_entry = next(item for item in ranked if item["entity"] == "svc/pay.py")
    assert locked_entry["locked"] is True


def test_prioritize_respects_limit(mesh):
    for index in range(5):
        mesh.emit("agent-a", f"svc/file{index}.py", "DEFECT_SUSPECT", strength=float(index))
    assert len(mesh.prioritize(limit=2)) <= 2


def test_convergence_lock_reduces_priority(mesh):
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", strength=1.0)
    mesh.emit("agent-a", "svc/pay.py", "CONVERGENCE_LOCKED", strength=5.0)
    ranked = mesh.prioritize()
    assert ranked[0]["score"] < 0


# ---------------------------------------------------------------------------
# Traces, store and stream
# ---------------------------------------------------------------------------


def test_traces_are_filterable(mesh):
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT")
    mesh.emit("agent-b", "svc/order.py", "COVERAGE_GAP")
    assert len(mesh.traces()) == 2
    assert len(mesh.traces(agent_id="agent-a")) == 1
    assert len(mesh.traces(entity="svc/order.py")) == 1


def test_event_stream_publishes_to_subscribers(mesh):
    seen: list[StigmergicEvent] = []
    mesh.stream.subscribe(seen.append)
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT")
    assert len(seen) == 1
    assert seen[0].entity == "svc/pay.py"


def test_event_stream_is_bounded():
    stream = EventStream(capacity=5)
    for index in range(20):
        stream.publish(
            StigmergicEvent(
                event_id=f"e{index}",
                agent_id="a",
                entity="f",
                pheromone=PheromoneType.DEFECT_SUSPECT,
                action=EventAction.DEPOSIT,
                strength=1.0,
                half_life_sec=10.0,
                timestamp=float(index),
            )
        )
    assert len(stream.recent()) == 5
    assert stream.recent(limit=2)[-1].event_id == "e19"


def test_store_persists_to_disk(tmp_path):
    db_path = tmp_path / "mesh.db"
    first = StigmergicEventMesh(db_path=db_path)
    first.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", strength=2.0)
    first.close()

    second = StigmergicEventMesh(db_path=db_path)
    assert len(second.traces()) == 1
    assert second.traces()[0].strength == pytest.approx(2.0)
    second.close()


def test_store_prunes_expired_events(tmp_path):
    mesh = StigmergicEventMesh(db_path=tmp_path / "mesh.db", default_half_life_sec=1.0)
    now = time.time()
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", strength=1.0, timestamp=now - 30)
    mesh.emit("agent-a", "svc/fresh.py", "DEFECT_SUSPECT", strength=1.0, timestamp=now)
    removed = mesh.evaporate(now=now)
    assert removed == 1
    assert [item.entity for item in mesh.traces()] == ["svc/fresh.py"]
    mesh.close()


def test_store_purge_filters(tmp_path):
    store = StigmergicEventStore(db_path=tmp_path / "store.db")
    mesh = StigmergicEventMesh(db_path=tmp_path / "store.db")
    mesh.emit("agent-a", "a.py", "DEFECT_SUSPECT")
    mesh.emit("agent-a", "b.py", "DEFECT_SUSPECT")
    assert store.purge(entity="a.py") == 1
    assert len(mesh.traces()) == 1
    mesh.close()


def test_default_mesh_path_convention(tmp_path):
    assert default_mesh_path(tmp_path).endswith("stigmergic_event_mesh.db")
    assert ".agent-workspace" in default_mesh_path(tmp_path)


def test_shared_mesh_registry_is_keyed_by_path(tmp_path):
    first = get_stigmergic_mesh(tmp_path / "shared.db")
    second = get_stigmergic_mesh(tmp_path / "shared.db")
    assert first is second
    first.emit("agent-a", "x.py", "DEFECT_SUSPECT")
    assert len(second.traces()) == 1


# ---------------------------------------------------------------------------
# Query surface and tools
# ---------------------------------------------------------------------------


def test_query_modes(mesh):
    mesh.emit("agent-a", "svc/pay.py", "DEFECT_SUSPECT", strength=2.0)
    assert mesh.query(mode="field")["count"] == 1
    assert mesh.query(mode="traces")["count"] == 1
    assert mesh.query(mode="locks")["success"] is True
    assert mesh.query(mode="priorities")["count"] >= 1
    stats = mesh.query(mode="stats")["results"][0]
    assert stats["event_count"] == 1
    assert stats["distinct_agents"] == 1
    assert stats["default_half_life_sec"] == DEFAULT_HALF_LIFE_SEC


def test_query_rejects_unknown_mode(mesh):
    result = mesh.query(mode="nonsense")
    assert result["success"] is False


def test_tool_emit_and_query_roundtrip():
    emitted = emit_stigmergic_event.invoke(
        {
            "agent_id": "agent-a",
            "entity": "svc/pay.py",
            "pheromone": "DEFECT_SUSPECT",
            "strength": 2.5,
            "in_memory": True,
        }
    )
    assert emitted["success"] is True

    field = query_stigmergic_traces.invoke({"mode": "field", "entity": "svc/pay.py", "in_memory": True})
    assert field["success"] is True
    assert field["count"] >= 1
    assert field["results"][0]["entity"] == "svc/pay.py"


def test_tool_emit_parses_metadata_json():
    emitted = emit_stigmergic_event.invoke(
        {
            "agent_id": "agent-a",
            "entity": "svc/pay.py",
            "pheromone": "COVERAGE_GAP",
            "metadata_json": '{"line": 42}',
            "in_memory": True,
        }
    )
    assert emitted["success"] is True
    assert emitted["event"]["metadata"] == {"line": 42}


def test_tool_emit_tolerates_invalid_metadata_json():
    emitted = emit_stigmergic_event.invoke(
        {
            "agent_id": "agent-a",
            "entity": "svc/pay.py",
            "pheromone": "COVERAGE_GAP",
            "metadata_json": "{not json",
            "in_memory": True,
        }
    )
    assert emitted["success"] is True
    assert emitted["event"]["metadata"] == {}


def test_tool_query_rejects_unknown_mode():
    result = query_stigmergic_traces.invoke({"mode": "nonsense", "in_memory": True})
    assert result["success"] is False
