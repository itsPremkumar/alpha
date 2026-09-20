"""Comprehensive tests for Goal Integrity and Scope Creep Auditing."""

from __future__ import annotations

import pytest

from alpha.planning.integrity import GoalIntegrityEngine
from alpha.tools.builtins.goal_integrity_tool import goal_integrity_tool


def test_goal_integrity_aligned_plan():
    goal = "Refactor user authentication service to use JWT tokens"
    subtasks = [
        "Implement JWT token generator and validator module",
        "Update login endpoint to return signed JWT token",
        "Add unit tests for expired and malformed JWT tokens",
    ]

    report = GoalIntegrityEngine.audit_plan(goal, subtasks)
    assert report.is_aligned is True
    assert report.scope_creep_detected is False
    assert report.overengineering_detected is False
    assert report.drift_score < 0.65


def test_goal_integrity_scope_creep_detection():
    goal = "Sort list of customer names alphabetically"
    subtasks = [
        "Read customer names",
        "Deploy Kubernetes cluster and setup Kafka event streaming pipeline",
        "Sort the names",
    ]

    report = GoalIntegrityEngine.audit_plan(goal, subtasks)
    assert report.scope_creep_detected is True
    assert report.is_aligned is False
    assert any("kubernetes" in f.lower() or "kafka" in f.lower() for f in report.findings)


def test_goal_integrity_overengineering_detection():
    # Simple 3-word goal split into 5 complex subtasks
    goal = "Fix typo in README"
    subtasks = [
        "Initialize distributed git review committee",
        "Draft architectural decision record ADR-001",
        "Deploy multi-region staging environment",
        "Execute penetration testing suite",
        "Commit spelling correction",
    ]

    report = GoalIntegrityEngine.audit_plan(goal, subtasks)
    assert report.overengineering_detected is True


def test_goal_integrity_tool_and_gateway_router():
    out = goal_integrity_tool.invoke(
        {
            "action": "audit_plan",
            "mission_goal": "Optimize postgres query for orders table",
            "subtasks_json": '["Add composite index on customer_id and created_at", "Analyze EXPLAIN ANALYZE query plan"]',
        }
    )
    assert "is_aligned" in out
    assert "true" in out.lower()


@pytest.mark.asyncio
async def test_gateway_goal_integrity_router():
    from app.gateway.routers import goal_integrity as gi_router

    req = gi_router.GoalAuditRequest(
        mission_goal="Build responsive pricing table in React",
        subtasks=[
            {"task_id": "t1", "description": "Create PricingCard component with monthly/yearly toggle"},
            {"task_id": "t2", "description": "Add Tailwind CSS responsive styles"},
        ],
    )
    resp = await gi_router.audit_goal_integrity(req)
    assert resp["is_aligned"] is True
    assert resp["audited_subtasks_count"] == 2
