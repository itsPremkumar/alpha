"""Tests for Mission Compiler and Risk Governance (R0-R6)."""

import json

from alpha.mission.compiler import MissionCompiler
from alpha.mission.models import Mission, RiskTier
from alpha.tools.builtins.compile_mission_tool import compile_mission


def test_mission_compiler_intent_and_latent_needs():
    compiler = MissionCompiler()
    raw = "Please fix the authentication token expiration bug asap"
    mission = compiler.compile(raw)

    assert isinstance(mission, Mission)
    assert "Authentication token expiration bug" in mission.interpreted_intent
    assert "Regression test" in mission.latent_needs


def test_mission_compiler_constraints():
    compiler = MissionCompiler()
    raw = "Implement new billing worker. You must use stripe API. Never log raw credit cards. Ensure GDPR compliance."
    mission = compiler.compile(raw)

    assert len(mission.constraints["hard"]) > 0
    assert any("must use stripe" in c for c in mission.constraints["hard"])
    assert len(mission.constraints["forbidden"]) > 0
    assert any("never log" in c for c in mission.constraints["forbidden"])
    assert len(mission.constraints["legal"]) > 0
    assert any("gdpr" in c for c in mission.constraints["legal"])


def test_mission_compiler_risk_tiers():
    compiler = MissionCompiler()

    # R0 - Pure reasoning
    m_r0 = compiler.compile("Why does Python GIL impact CPU-bound multithreading?")
    assert m_r0.risk_tier == RiskTier.R0

    # R1 - Read-only
    m_r1 = compiler.compile("Search where the database pool is initialized in src/")
    assert m_r1.risk_tier == RiskTier.R1

    # R2 - Standard local modification
    m_r2 = compiler.compile("Add a helper function to format currency strings")
    assert m_r2.risk_tier == RiskTier.R2

    # R4 - Significant side-effects
    m_r4 = compiler.compile("Run database schema migration to add customer_uuid column")
    assert m_r4.risk_tier == RiskTier.R4

    # R5 - Destructive irreversible operation
    m_r5 = compiler.compile("Drop database production_backup and purge all records")
    assert m_r5.risk_tier == RiskTier.R5
    # R5 must have manual approval proof obligation
    assert any(p.verification_type == "manual_approval" for p in m_r5.proof_obligations)


def test_compile_mission_tool():
    res_str = compile_mission.invoke({"raw_request": "Refactor router endpoints to use async FastAPI"})
    data = json.loads(res_str)

    assert "mission_id" in data
    assert "interpreted_intent" in data
    assert "risk_tier" in data
    assert "markdown_contract" in data
    assert "Mission Contract" in data["markdown_contract"]
