"""Comprehensive tests for local Hermes bot integration, 8-stage production line, and kanban sync."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.company import router as company_router
from agent_workspace.company import (
    AutonomousCompanyEngine,
    HermesKanbanAdapter,
    HermesLocalBridge,
    ProductionLineEngine,
    ProductionStage,
)
from agent_workspace.tools.builtins.company_tool import company_tool


def test_hermes_local_bridge_bot_discovery():
    bridge = HermesLocalBridge()
    # If .hermes exists on user's machine
    if bridge.is_hermes_installed:
        bots = bridge.discover_local_bots()
        assert len(bots) >= 10
        assert "ceo" in bots
        assert "cto" in bots
        assert "qa-lead" in bots
        assert "devops-engineer" in bots

        # Test CEO parsing
        ceo_meta = bridge.get_bot_metadata("ceo")
        assert "Chief Executive Officer" in ceo_meta.role or "CEO" in ceo_meta.role
        assert ceo_meta.symbol == "👔"
        assert len(ceo_meta.responsibilities) >= 1
        assert "WORK ASSIGNMENT PROTOCOL" in ceo_meta.work_protocol or len(ceo_meta.work_protocol) > 0

        # Test CTO parsing
        cto_meta = bridge.get_bot_metadata("cto")
        assert "Chief Technology Officer" in cto_meta.role or "CTO" in cto_meta.role
        assert cto_meta.symbol == "🧠"
    else:
        # Synthetic fallback test
        meta = bridge.get_bot_metadata("synthetic-bot")
        assert meta.name == "synthetic-bot"


def test_eight_stage_production_line_pipeline():
    engine = ProductionLineEngine()
    run = engine.submit_feature(
        title="Automated Vector Semantic Search",
        feature_spec="Requirements: Sub-10ms similarity queries across 1M vectors",
    )
    assert run.current_stage == ProductionStage.REQUIREMENTS
    assert run.status == "in_progress"

    # Stage 1: Requirements
    run, a1 = engine.advance_stage(run.run_id, content="# PRD\nDetailed requirements...")
    assert a1.stage == ProductionStage.REQUIREMENTS
    assert a1.assigned_bot == "product-owner"
    assert a1.filename == "01-requirements.md"
    assert run.current_stage == ProductionStage.ARCHITECTURE

    # Stage 2: Architecture
    run, a2 = engine.advance_stage(run.run_id, content="# Architecture\nDetailed diagrams...")
    assert a2.stage == ProductionStage.ARCHITECTURE
    assert a2.assigned_bot == "architect"
    assert run.current_stage == ProductionStage.API_CONTRACT

    # Advance remaining 6 stages
    for _ in range(6):
        run, _ = engine.advance_stage(run.run_id)

    assert run.status == "completed"
    assert len(run.artifacts) == 8
    stage_names = [a.stage.value for a in run.artifacts]
    assert "01_requirements" in stage_names
    assert "04_backend" in stage_names
    assert "07_test_plan" in stage_names
    assert "08_documentation_release" in stage_names


def test_hermes_kanban_adapter_sync():
    adapter = HermesKanbanAdapter()
    tasks = adapter.list_tasks(limit=10)
    # If the local board exists, it should have tasks
    if adapter.is_available:
        assert isinstance(tasks, list)

    company_engine = AutonomousCompanyEngine()
    state = company_engine.bootstrap_company(prompt="IT Services and Autonomous AI software platform")
    sync_res = adapter.sync_projects_to_kanban(state.projects)
    assert "total_company_projects" in sync_res
    assert sync_res["total_company_projects"] >= 2


def test_company_tool_hermes_and_production_actions():
    # 1. Bootstrap company
    res_boot = company_tool.invoke(
        {
            "action": "bootstrap",
            "prompt": "Apex Autonomous Cloud IT company",
        }
    )
    boot_data = json.loads(res_boot)
    org_id = boot_data["org_id"]

    # 2. hermes_bots action
    res_bots = company_tool.invoke({"action": "hermes_bots", "org_id": org_id})
    bots_data = json.loads(res_bots)
    assert "discovered_bots_count" in bots_data

    # 3. production_submit action
    res_submit = company_tool.invoke(
        {
            "action": "production_submit",
            "title": "High-Throughput Ingestion Queue",
            "spec": "Requirements: Handle 50k events per second with Kafka/Redis",
        }
    )
    submit_data = json.loads(res_submit)
    assert submit_data["current_stage"] == "01_requirements"
    run_id = submit_data["run_id"]

    # 4. production_advance action
    res_advance = company_tool.invoke(
        {
            "action": "production_advance",
            "run_id": run_id,
            "stage_content": "Approved PRD for ingestion queue",
        }
    )
    advance_data = json.loads(res_advance)
    assert advance_data["created_artifact"]["stage"] == "01_requirements"
    assert advance_data["run"]["current_stage"] == "02_architecture"

    # 5. kanban_sync action
    res_sync = company_tool.invoke({"action": "kanban_sync", "org_id": org_id})
    sync_data = json.loads(res_sync)
    assert "total_company_projects" in sync_data


def test_gateway_rest_hermes_and_production_endpoints():
    app = FastAPI()
    app.include_router(company_router)
    client = TestClient(app)

    # 1. GET /api/company/hermes/bots
    res_bots = client.get("/api/company/hermes/bots")
    assert res_bots.status_code == 200
    assert "discovered_bots_count" in res_bots.json()

    # 2. POST /api/company/production-line/submit
    res_sub = client.post(
        "/api/company/production-line/submit",
        json={"title": "Cloud Disaster Recovery Failover", "feature_spec": "RTO < 60s"},
    )
    assert res_sub.status_code == 200
    run_data = res_sub.json()
    run_id = run_data["run_id"]
    assert run_data["current_stage"] == "01_requirements"

    # 3. POST /api/company/production-line/advance
    res_adv = client.post(
        "/api/company/production-line/advance",
        json={"run_id": run_id, "stage_content": "Disaster recovery specs validated"},
    )
    assert res_adv.status_code == 200
    assert res_adv.json()["run"]["current_stage"] == "02_architecture"
