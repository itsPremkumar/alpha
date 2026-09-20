"""Comprehensive tests for Autonomous AI Company Bootstrapper & Lifecycle."""

from __future__ import annotations

from alpha.company import (
    AutonomousCompanyEngine,
    DepartmentType,
    OrgState,
)


def test_company_single_prompt_bootstrapper():
    engine = AutonomousCompanyEngine()
    prompt = "Create an autonomous AI software company and operate it for the next five years. Build products, manage engineering, research markets, acquire users, maintain infrastructure, and recover failed agents automatically."

    state = engine.bootstrap_company(prompt=prompt, owner="owner-alice", duration_years=5.0)

    # 1. Verify Charter & State
    assert state.name == "Apex Autonomous Systems Inc."
    assert state.state == OrgState.ACTIVE
    assert state.charter.duration_years == 5.0
    assert state.charter.owner == "owner-alice"

    # 2. Verify Department Breakdown
    dept_types = {d.department_type for d in state.departments}
    assert DepartmentType.EXECUTIVE in dept_types
    assert DepartmentType.ENGINEERING in dept_types
    assert DepartmentType.SECURITY in dept_types
    assert DepartmentType.RESEARCH in dept_types

    # 3. Verify Permanent Workforce Roster
    all_members = []
    for d in state.departments:
        all_members.extend(d.member_bot_names)
    assert "bot-ceo" in all_members
    assert "bot-cto" in all_members
    assert "bot-backend-lead" in all_members
    assert "bot-security-lead" in all_members
    assert "bot-sre-lead" in all_members
    assert state.active_bots_count > 0

    # 4. Verify Responsibilities & Projects
    assert len(state.responsibilities) >= 4
    assert len(state.projects) >= 2
    assert len(state.objectives) >= 2
    assert len(state.kpis) >= 4
    assert state.overall_health_percent >= 90.0


def test_company_lifecycle_pause_and_resume():
    engine = AutonomousCompanyEngine()
    state = engine.bootstrap_company(prompt="Run an autonomous AI lab")
    org_id = state.org_id

    # Pause
    paused = engine.pause_company(org_id)
    assert paused.state == OrgState.PAUSED

    # Resume
    resumed = engine.resume_company(org_id)
    assert resumed.state == OrgState.ACTIVE
