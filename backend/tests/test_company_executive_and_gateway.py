"""Comprehensive tests for Company Executive Layer, Strategy Engine, Agent Tool, and REST Endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.company.executive import ExecutiveDigest, ExecutiveIntelligenceLayer
from alpha.company.organization import AutonomousCompanyEngine
from alpha.tools.builtins.company_tool import company_tool
from app.gateway.routers.company import router as company_router


def test_executive_intelligence_digest_generation():
    engine = AutonomousCompanyEngine()
    state = engine.bootstrap_company(prompt="Autonomous AI Cloud Platform")

    digest = ExecutiveIntelligenceLayer.generate_digest(state)
    assert digest.org_id == state.org_id
    assert digest.company_name == state.name
    assert digest.overall_health_percent >= 90.0
    assert digest.active_bots_count > 0
    assert digest.active_projects_count >= 2
    assert len(digest.kpi_summary) >= 1
    assert "why_idle_agents" in digest.explainability
    assert "Executive Digest" in digest.summary_markdown


def test_executive_digest_default_health_is_unmeasured():
    """Honesty pin: a directly-constructed digest must not invent a health score.

    generate_digest() computes health from real incident counts; the old 95.0
    class default was a fabricated value on any other construction path.
    """
    digest = ExecutiveDigest(org_id="org-pin-test", company_name="Pin Test Co")
    assert digest.overall_health_percent is None


def test_digest_kpi_summary_discloses_seed_basis():
    """Honesty pin: seed KPI rows are disclosed, never claimed healthy/unhealthy."""
    engine = AutonomousCompanyEngine()
    state = engine.bootstrap_company(prompt="Autonomous AI Cloud Platform")
    digest = ExecutiveIntelligenceLayer.generate_digest(state)

    assert len(digest.kpi_summary) >= 1
    for entry in digest.kpi_summary:
        assert entry["basis"] == "seed_demo_data"

    # The markdown KPI rows must carry the seed disclosure text.
    kpi_lines = [ln for ln in digest.summary_markdown.splitlines() if "(Target:" in ln]
    assert kpi_lines, "expected KPI lines in the summary markdown"
    for line in kpi_lines:
        assert "(seed example — not a live measurement)" in line
        assert "🟢" not in line
        assert "🔴" not in line


def test_strategy_engine_pivot_and_replanning():
    engine = AutonomousCompanyEngine()
    state = engine.bootstrap_company(prompt="Autonomous FinTech platform")
    initial_project_count = len(state.projects)

    directive = "Pivot immediately to European compliance and GDPR data sovereign infrastructure"
    report = engine.replan_strategy(state.org_id, directive)

    assert report.directive == directive
    assert len(report.new_projects) >= 1
    assert len(report.new_objectives) >= 1
    assert "security" in report.realigned_departments
    # Verify state was updated
    updated_state = engine.get_company(state.org_id)
    assert len(updated_state.projects) >= initial_project_count


def test_company_tool_execution():
    # 1. Bootstrap via tool
    res_boot = company_tool.invoke(
        {
            "action": "bootstrap",
            "prompt": "Autonomous enterprise analytics and telemetry company",
        }
    )
    data_boot = json.loads(res_boot)
    assert data_boot["status"] == "company_bootstrapped"
    org_id = data_boot["org_id"]

    # 2. Status via tool
    res_status = company_tool.invoke({"action": "status", "org_id": org_id})
    data_status = json.loads(res_status)
    assert data_status["org_id"] == org_id
    assert data_status["state"] == "active"

    # 3. Discover work via tool
    res_disc = company_tool.invoke({"action": "discover_work", "org_id": org_id})
    data_disc = json.loads(res_disc)
    assert "discovered_items" in data_disc

    # 4. Executive digest via tool
    res_digest = company_tool.invoke({"action": "executive_digest", "org_id": org_id})
    data_digest = json.loads(res_digest)
    assert data_digest["org_id"] == org_id
    assert data_digest["overall_health_percent"] > 0

    # 5. Pause & Resume via tool
    res_pause = company_tool.invoke({"action": "pause", "org_id": org_id})
    assert "company_paused" in res_pause
    res_resume = company_tool.invoke({"action": "resume", "org_id": org_id})
    assert "company_resumed" in res_resume


def test_company_gateway_rest_api():
    app = FastAPI()
    app.include_router(company_router)
    client = TestClient(app)

    # 1. Bootstrap
    res_boot = client.post(
        "/api/company/bootstrap",
        json={
            "prompt": "Build and operate an autonomous logistics AI agent network for 5 years",
            "owner": "logistics-operator",
            "duration_years": 5.0,
        },
    )
    assert res_boot.status_code == 200
    boot_data = res_boot.json()
    org_id = boot_data["org_id"]
    assert boot_data["name"] == "Apex Autonomous Systems Inc."

    # 2. Get status
    res_status = client.get(f"/api/company/status?org_id={org_id}")
    assert res_status.status_code == 200
    assert res_status.json()["org_id"] == org_id

    # 3. Get executive digest
    res_digest = client.get(f"/api/company/executive-digest?org_id={org_id}")
    assert res_digest.status_code == 200
    assert res_digest.json()["overall_health_percent"] >= 90.0

    # 4. Discover work
    res_disc = client.post(
        "/api/company/discover-work",
        json={"org_id": org_id, "signals": []},
    )
    assert res_disc.status_code == 200
    assert res_disc.json()["workers_should_sleep"] is True

    # 5. KPIs
    res_kpis = client.get(f"/api/company/kpis?org_id={org_id}")
    assert res_kpis.status_code == 200
    assert len(res_kpis.json()["kpis"]) >= 4

    # 6. Strategic replanning
    res_replan = client.post(
        "/api/company/strategy/replan",
        json={
            "org_id": org_id,
            "directive": "Expand to real-time multimodal fleet tracking",
        },
    )
    assert res_replan.status_code == 200
    assert len(res_replan.json()["new_projects"]) >= 1

    # 7. Responsibility transfer
    resp_id = boot_data["responsibilities"][0]["responsibility_id"]
    res_transfer = client.post(
        "/api/company/responsibilities/transfer",
        json={"org_id": org_id, "responsibility_id": resp_id, "reason": "Testing gateway failover"},
    )
    assert res_transfer.status_code == 200
    assert res_transfer.json()["status"] == "failed_over"

    # 8. Pause & Resume
    res_pause = client.post(f"/api/company/{org_id}/pause")
    assert res_pause.status_code == 200
    assert res_pause.json()["state"] == "paused"

    res_resume = client.post(f"/api/company/{org_id}/resume")
    assert res_resume.status_code == 200
    assert res_resume.json()["state"] == "active"


def test_company_boundary_firewall_integrity():
    """Verify that packages/harness/alpha/company/ strictly avoids importing app.*."""
    company_dir = Path(__file__).resolve().parent.parent / "packages" / "harness" / "alpha" / "company"
    assert company_dir.exists(), f"Directory not found: {company_dir}"

    for py_file in company_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "from app" not in content, f"Boundary violation: {py_file.name} imports from 'app'"
        assert "import app" not in content, f"Boundary violation: {py_file.name} imports 'app'"
