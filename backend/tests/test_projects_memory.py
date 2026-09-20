"""Constitution, events, state folding, decisions, goals."""

from __future__ import annotations

import pytest

from alpha.projects import constitution as const_mod
from alpha.projects.decisions import DecisionLog
from alpha.projects.events import ProjectEventBus
from alpha.projects.goals import GoalTree
from alpha.projects.state import get_state, refresh_state, set_phase


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    yield


def test_constitution_rejects_missing_sections():
    missing = const_mod.validate_constitution("# hello\n\n## Objective\nDone.\n")
    assert "security rules" in missing
    with pytest.raises(ValueError):
        const_mod.put_constitution("proj-a", "# hello")


def test_constitution_roundtrip_and_hash_pin():
    text = const_mod.DEFAULT_CONSTITUTION_TEMPLATE
    assert const_mod.validate_constitution(text) == []
    const = const_mod.put_constitution("proj-a", text)
    assert len(const.sha16) == 16
    again = const_mod.get_constitution("proj-a")
    assert again is not None and again.sha16 == const.sha16


def test_event_bus_sequence_and_search(tmp_path):
    bus = ProjectEventBus("proj-a")
    bus._path = tmp_path / "events.jsonl"
    bus._seq = 0
    bus.emit("task_created", "architect", {"task_id": "t-1"})
    bus.emit("task_completed", "coder", {"task_id": "t-1", "risk": "needs redis backup"})
    rows = bus.read()
    assert [e.seq for e in rows] == [1, 2]
    assert bus.read(after_seq=1)[0].type == "task_completed"
    assert len(bus.search("redis")) == 1
    with pytest.raises(ValueError):
        bus.emit("bogus_type", "coder", {})


def test_state_folds_events():
    from alpha.projects.events import get_event_bus

    bus = get_event_bus("proj-state")
    bus.emit("agent_joined", "coder", {})
    bus.emit("agent_joined", "reviewer", {})
    bus.emit("task_created", "architect", {})
    bus.emit("task_blocked", "coder", {"risk": "flaky CI"})
    bus.emit("task_completed", "coder", {"verified_at": "2026-01-01T00:00:00+00:00"})
    bus.emit("decision_made", "architect", {"decision_id": "ADR-001", "arch_version": "v2.0"})
    state = refresh_state("proj-state")
    assert state.active_agents == 2
    assert state.active_tasks == 0
    assert state.completed_tasks == 1
    assert state.blocked_tasks == 1
    assert state.latest_decision == "ADR-001"
    assert state.arch_version == "v2.0"
    assert state.open_risks == ["flaky CI"]


def test_lifecycle_phase_guard():
    with pytest.raises(ValueError):
        set_phase("proj-phase", "warp")
    state = set_phase("proj-phase", "plan")
    assert state.phase == "plan"
    assert get_state("proj-phase").phase == "plan"


def test_decision_log_records_and_searches_adr(tmp_path):
    log = DecisionLog("proj-a")
    log._path = tmp_path / "decisions.json"
    first = log.record("Use PostgreSQL", "Relational fit", reason="JSONB + pgvector", made_by="architect", approved_by="supervisor")
    assert first.decision_id == "ADR-001"
    log.record("Use Redis", "Cache sessions", made_by="architect")
    assert len(log.search("postgres")) == 1
    assert len(log.list()) == 2


def test_goal_tree_progress_and_attempts(tmp_path):
    tree = GoalTree("proj-a", "goal-x")
    tree._path = tmp_path / "goal.json"
    root = tree.add_root("Ship auth", acceptance=["tests green"])
    sub = tree.add_subgoal(root.node_id, "JWT service")
    assert tree.progress()["total"] == 2
    tree.set_status(sub.node_id, "blocked", blocked_reason="no creds", attempt={"strategy": "mock"})
    assert tree.progress()["blocked"] == 1
    tree.set_status(sub.node_id, "satisfied", evidence={"kind": "tests_passed", "reference": "r1"})
    assert tree.progress()["percent"] == 50.0
    with pytest.raises(ValueError):
        tree.add_subgoal("missing", "nope")
