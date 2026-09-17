"""Comprehensive tests for Perpetual Organization Archetypes & Self-Improvement Loops."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.company import router as company_router
from agent_workspace.company import (
    AutonomousCompanyEngine,
    OrgArchetype,
    OrgState,
)
from agent_workspace.tools.builtins.company_tool import company_tool


def test_archetype_listing():
    engine = AutonomousCompanyEngine()
    archetypes = engine.list_archetypes()
    assert len(archetypes) == 5
    types = [a["archetype"] for a in archetypes]
    assert "company" in types
    assert "open_source" in types
    assert "security_soc" in types
    assert "research_lab" in types
    assert "custom" in types


def test_open_source_collective_archetype():
    engine = AutonomousCompanyEngine()
    state = engine.bootstrap_company(
        prompt="Continuously maintain, review, and release open-source distributed graph database",
        archetype=OrgArchetype.OPEN_SOURCE,
        owner="foss-lead",
    )

    assert state.archetype == OrgArchetype.OPEN_SOURCE
    assert state.name == "Open Forge Maintainer Collective"
    assert state.state == OrgState.ACTIVE

    # Departments
    dept_names = [d.name for d in state.departments]
    assert any("Triage" in n for n in dept_names)
    assert any("Core Maintenance" in n for n in dept_names)
    assert any("Review" in n for n in dept_names)
    assert any("Release" in n for n in dept_names)

    # Permanent Bot Profiles
    all_members = [m for d in state.departments for m in d.member_bot_names]
    assert "bot-issue-triage" in all_members
    assert "bot-core-maintainer" in all_members
    assert "bot-pr-reviewer" in all_members
    assert "bot-release-manager" in all_members

    # Specific KPIs
    kpi_ids = [k.kpi_id for k in state.kpis]
    assert "kpi-pr-review-time" in kpi_ids
    assert "kpi-test-coverage" in kpi_ids


def test_security_soc_archetype():
    engine = AutonomousCompanyEngine()
    state = engine.bootstrap_company(
        prompt="Autonomous 24/7 Red and Blue team cyber defense fleet",
        archetype=OrgArchetype.SECURITY_SOC,
        owner="ciso-alice",
    )

    assert state.archetype == OrgArchetype.SECURITY_SOC
    assert state.name == "Sentinel Autonomous Cyber Defense Fleet"

    # Specialist Bots
    all_members = [m for d in state.departments for m in d.member_bot_names]
    assert "bot-soc-lead" in all_members
    assert "bot-red-lead" in all_members
    assert "bot-cve-hunter" in all_members
    assert "bot-incident-commander" in all_members

    # KPIs
    kpi_ids = [k.kpi_id for k in state.kpis]
    assert "kpi-mttd" in kpi_ids
    assert "kpi-mttr" in kpi_ids
    assert "kpi-patch-pass-rate" in kpi_ids


def test_research_lab_archetype():
    engine = AutonomousCompanyEngine()
    state = engine.bootstrap_company(
        prompt="Autonomous scientific AI lab discovering neural architectures",
        archetype=OrgArchetype.RESEARCH_LAB,
        owner="prof-bob",
    )

    assert state.archetype == OrgArchetype.RESEARCH_LAB
    assert state.name == "Nova Autonomous Discovery Lab"

    all_members = [m for d in state.departments for m in d.member_bot_names]
    assert "bot-arxiv-crawler" in all_members
    assert "bot-chief-scientist" in all_members
    assert "bot-sim-runner" in all_members
    assert "bot-paper-writer" in all_members

    kpi_ids = [k.kpi_id for k in state.kpis]
    assert "kpi-hypotheses-tested" in kpi_ids
    assert "kpi-reproducibility" in kpi_ids


def test_dynamic_custom_archetype_synthesis():
    engine = AutonomousCompanyEngine()
    # Prompt for game development collective
    state = engine.bootstrap_company(
        prompt="Build and continuously balance a procedural cyberpunk RPG game",
        archetype=OrgArchetype.CUSTOM,
    )

    assert state.archetype == OrgArchetype.CUSTOM
    all_members = [m for d in state.departments for m in d.member_bot_names]
    assert "bot-gameplay-lead" in all_members
    assert "bot-world-builder" in all_members
    assert "bot-qa-tester" in all_members

    kpi_names = [k.name for k in state.kpis]
    assert any("Playtest" in name or "Stability" in name for name in kpi_names)


def test_continuous_self_improvement_and_evolution_journal():
    engine = AutonomousCompanyEngine()
    state = engine.bootstrap_company(prompt="Autonomous AI collective", archetype=OrgArchetype.OPEN_SOURCE)
    org_id = state.org_id

    # Cycle 1
    rec1 = engine.run_retrospective(org_id)
    assert rec1.cycle_number == 1
    assert len(rec1.insights) >= 2
    assert len(rec1.improved_playbooks) >= 2
    assert len(rec1.calibrated_bots) >= 2

    # Cycle 2
    rec2 = engine.run_retrospective(org_id)
    assert rec2.cycle_number == 2

    # Verify Journal persistence
    updated = engine.get_company(org_id)
    assert len(updated.evolution_journal) == 2
    assert updated.evolution_journal[0].cycle_number == 1
    assert updated.evolution_journal[1].cycle_number == 2


def test_archetypes_and_retrospective_via_tool_and_gateway():
    # 1. Tool Archetypes
    res_arch = company_tool.invoke({"action": "archetypes"})
    arch_data = json.loads(res_arch)
    assert len(arch_data) == 5

    # 2. Tool Bootstrap with Archetype
    res_boot = company_tool.invoke(
        {
            "action": "bootstrap",
            "prompt": "Autonomous 24/7 Security SOC",
            "archetype": "security_soc",
        }
    )
    boot_data = json.loads(res_boot)
    assert boot_data["archetype"] == "security_soc"
    org_id = boot_data["org_id"]

    # 3. Tool Retrospective
    res_retro = company_tool.invoke({"action": "retrospective", "org_id": org_id})
    retro_data = json.loads(res_retro)
    assert retro_data["cycle_number"] == 1

    # 4. Gateway REST API
    app = FastAPI()
    app.include_router(company_router)
    client = TestClient(app)

    # GET /api/company/archetypes
    res_api_arch = client.get("/api/company/archetypes")
    assert res_api_arch.status_code == 200
    assert len(res_api_arch.json()) == 5

    # POST /api/company/retrospective
    res_api_retro = client.post("/api/company/retrospective", json={"org_id": org_id})
    assert res_api_retro.status_code == 200
    assert res_api_retro.json()["cycle_number"] == 2

    # GET /api/company/evolution-journal
    res_api_journal = client.get(f"/api/company/evolution-journal?org_id={org_id}")
    assert res_api_journal.status_code == 200
    assert len(res_api_journal.json()) == 2
