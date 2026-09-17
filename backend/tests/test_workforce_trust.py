"""Policy engine, missions, council, recovery."""

from __future__ import annotations

import pytest

from agent_workspace.council import CouncilEngine
from agent_workspace.missions import MissionStore
from agent_workspace.policy import PolicyEngine
from agent_workspace.recovery import classify_failure, decide


@pytest.fixture()
def policy(tmp_path):
    return PolicyEngine(storage_path=tmp_path / "policies.json")


@pytest.fixture()
def missions(tmp_path):
    return MissionStore(storage_path=tmp_path / "missions.json")


def test_base_rules_allow_reads_require_approval_for_shell(policy):
    assert policy.evaluate("read:docs").verdict == "allow"
    assert policy.evaluate("shell:pytest -q").verdict == "approval"
    assert policy.evaluate("secret:export").verdict == "deny"
    assert policy.evaluate("payment:refund").verdict == "deny"
    assert policy.evaluate("something:entirely-new").verdict == "approval"


def test_operator_policy_overrides_and_removes(policy):
    policy.add_policy("deploy:staging", auto="allow", note="staging is safe")
    assert policy.evaluate("deploy:staging").verdict == "allow"
    assert policy.evaluate("deploy:prod").verdict == "approval"
    pid = policy.list_policies()[0].policy_id
    assert policy.remove_policy(pid) is True
    assert policy.remove_policy(pid) is False
    assert policy.evaluate("deploy:staging").verdict == "approval"


def test_mission_lifecycle_guards(missions):
    m = missions.create("owner-1", "Ship auth")
    assert m.status == "draft"
    assert missions.transition(m.mission_id, "completed") is None
    assert missions.transition(m.mission_id, "active") is not None
    assert missions.transition(m.mission_id, "paused") is not None
    assert missions.transition(m.mission_id, "active") is not None
    missions.attach_thread(m.mission_id, "thread-1")
    missions.attach_artifact(m.mission_id, "auth.py")
    row = missions.get(m.mission_id)
    assert row is not None and row.thread_ids == ["thread-1"] and row.artifacts == ["auth.py"]
    assert missions.transition(m.mission_id, "completed") is not None
    assert missions.list(owner="owner-1", status="completed")


def test_council_quorum_ship_and_hold():
    engine = CouncilEngine()
    ship = engine.open_case("a-1", "auth.py", min_reviews=2)
    assert engine.submit_review(ship.case_id, "reviewer", "approve", evidence="tests green")[1] == "inconclusive"
    assert engine.submit_review(ship.case_id, "architect", "approve")[1] == "ship"
    assert engine.get_case(ship.case_id).status == "ship"
    assert engine.submit_review(ship.case_id, "tester", "approve") is None

    hold = engine.open_case("a-2", "pay.py", min_reviews=2)
    assert engine.submit_review(hold.case_id, "security", "block", evidence="secret leak")[1] == "hold"
    assert engine.get_case(hold.case_id).status == "hold"


def test_failure_classification_and_budgets():
    assert classify_failure("429 Too Many Requests") == "model_rate_limit"
    assert classify_failure("tool timed out after 30s") == "tool_timeout"
    assert classify_failure("model timed out") == "model_timeout"
    assert classify_failure("context length exceeded max tokens") == "context_overflow"
    assert classify_failure("container died") == "container_crash"
    assert classify_failure("weird mystery") == "unknown"

    first = decide("model timed out", attempt=1)
    assert first.action == "retry" and first.wait_seconds > 0
    exhausted = decide("model timed out", attempt=99)
    assert exhausted.action == "fallback_model"
    assert decide("429 slow down", attempt=99).action == "fallback_model"
    assert decide("context length exceeded", attempt=5).action == "restore_checkpoint"
