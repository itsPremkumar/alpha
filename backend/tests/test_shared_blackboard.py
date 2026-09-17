import os
import tempfile

from agent_workspace.blackboard import (
    ALL_20_PLANES,
    BlackboardEngine,
    BlackboardPersistence,
    CognitivePhase,
    GoalClassification,
    PlaneStatus,
)


def test_blackboard_initialization_all_20_planes():
    bb = BlackboardEngine(goal="Optimize query compiler", goal_classification=GoalClassification.COMPLEX)
    assert len(bb.plane_states) == 20
    for plane_id in ALL_20_PLANES:
        assert plane_id in bb.plane_states
        assert bb.get_plane_state(plane_id).status == PlaneStatus.IDLE


def test_blackboard_plane_state_transitions():
    bb = BlackboardEngine(goal="Refactor AST parser")
    p1 = bb.set_plane_state("plane_1_self_evolution", PlaneStatus.RUNNING)
    assert p1.status == PlaneStatus.RUNNING
    assert p1.started_at is not None

    p1_done = bb.set_plane_state(
        "plane_1_self_evolution",
        PlaneStatus.COMPLETED,
        output={"skill": "ast_visitor_evolution"},
    )
    assert p1_done.status == PlaneStatus.COMPLETED
    assert p1_done.completed_at is not None
    assert p1_done.output["skill"] == "ast_visitor_evolution"


def test_blackboard_evidence_recording_and_query():
    bb = BlackboardEngine(goal="Verify memory integrity")
    item1 = bb.record_evidence(
        plane_id="plane_4_deep_research",
        evidence_type="source_verification",
        content={"doc": "RFC-7540", "status": "verified"},
        confidence=0.95,
    )
    item2 = bb.record_evidence(
        plane_id="plane_13_multi_round_verification",
        evidence_type="test_pass",
        content={"tests": 45, "passed": 45},
        confidence=1.0,
    )
    item3 = bb.record_evidence(
        plane_id="plane_13_multi_round_verification",
        evidence_type="lint_check",
        content={"warnings": 2},
        confidence=0.4,
    )

    all_evidence = bb.query_evidence()
    assert len(all_evidence) == 3

    p13_evidence = bb.query_evidence(plane_id="plane_13_multi_round_verification")
    assert len(p13_evidence) == 2

    high_conf = bb.query_evidence(min_confidence=0.9)
    assert len(high_conf) == 2

    filtered = bb.query_evidence(
        plane_id="plane_13_multi_round_verification",
        evidence_type="test_pass",
        min_confidence=0.8,
    )
    assert len(filtered) == 1
    assert filtered[0].content["passed"] == 45


def test_blackboard_snapshot_and_persistence():
    bb = BlackboardEngine(
        session_id="test_sess_123",
        goal="Run AVO search",
        goal_classification=GoalClassification.NOVEL,
    )
    bb.set_phase(CognitivePhase.PLAN)
    bb.set_shared_context("token_budget", 50000)
    bb.record_evidence("plane_2_self_awareness", "capability_check", {"can_proceed": True}, 0.88)

    snapshot = bb.create_snapshot()
    assert snapshot.session_id == "test_sess_123"
    assert snapshot.goal_classification == "novel"
    assert snapshot.current_phase == "plan"

    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = os.path.join(tmpdir, "blackboard_snap.json")
        saved_file = BlackboardPersistence.save_json(snapshot, json_path)
        assert os.path.exists(saved_file)

        loaded = BlackboardPersistence.load_json(saved_file)
        assert loaded.session_id == "test_sess_123"
        assert loaded.shared_context["token_budget"] == 50000
        assert len(loaded.evidence_trail) == 1
        assert loaded.evidence_trail[0]["plane_id"] == "plane_2_self_awareness"
