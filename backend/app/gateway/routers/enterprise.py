"""Gateway REST API Router for Autonomous AI Software Enterprise."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from alpha.enterprise import (
    get_council_quorum_engine,
    get_department_treasury,
    get_discovery_and_optimization_engine,
    get_enterprise_heartbeat_coordinator,
    get_enterprise_hierarchy,
    get_mission_pipeline,
    get_rfc_protocol,
)

logger = logging.getLogger(__name__)
_inner_router = APIRouter()
router = _inner_router


# --- Request Payloads ---

class SynthesizeDepartmentRequest(BaseModel):
    name: str = Field(..., min_length=2, description="Department display name")
    department_key: str = Field(..., min_length=2, description="Department key / slug")
    lead_bot_name: str = Field(..., description="Lead bot identifier")
    lead_title: str = Field(..., description="Lead bot title")
    capabilities: list[str] = Field(default_factory=list, description="Department capabilities")
    initial_budget: int = Field(default=250000, ge=1000, description="Initial token budget")


class CreateRFCRequest(BaseModel):
    title: str = Field(..., min_length=3, description="RFC title")
    author_bot: str = Field(..., description="Author bot name")
    department: str = Field(..., description="Author department")
    summary: str = Field(..., min_length=5, description="Executive summary")
    proposal_content: str = Field(..., min_length=5, description="Full technical proposal")
    affected_departments: list[str] = Field(default_factory=list, description="Impacted departments")


class SubmitRFCReviewRequest(BaseModel):
    reviewer_bot: str = Field(..., description="Reviewer bot name")
    department: str = Field(..., description="Reviewer department")
    verdict: str = Field(..., description="Verdict: approve, reject, amend")
    argument: str = Field(..., min_length=2, description="Review justification")
    epistemic_confidence: float = Field(default=0.85, ge=0.0, le=1.0, description="Confidence rating")


class SubmitDebateArgumentRequest(BaseModel):
    speaker_bot: str = Field(..., description="Debater bot name")
    department: str = Field(..., description="Debater department")
    stance: str = Field(..., description="Stance: pro, con, amend, synthesis")
    claim: str = Field(..., min_length=2, description="Core argument claim")
    evidence: str = Field(default="", description="Supporting technical evidence")
    counter_to_id: str | None = Field(default=None, description="Countered argument ID if applicable")
    epistemic_weight: float = Field(default=0.8, ge=0.0, le=1.0, description="Epistemic weight")


class DecomposeMissionRequest(BaseModel):
    mission_id: str = Field(..., description="Mission identifier")
    objective: str = Field(..., min_length=5, description="Strategic objective statement")


class CompileSprintRequest(BaseModel):
    spec_id: str = Field(..., description="Technical specification ID")


class TreasuryAllocateRequest(BaseModel):
    dept_id: str = Field(..., description="Target department ID")
    tokens: int = Field(..., ge=1, description="Number of tokens to allocate")


class ResetCircuitBreakerRequest(BaseModel):
    dept_id: str = Field(..., description="Target department ID")
    new_threshold_tpm: float | None = Field(default=None, description="Optional new threshold TPM")


class StageReleaseRequest(BaseModel):
    version: str = Field(..., min_length=2, description="Release version string (e.g. v2.2.0)")
    component: str = Field(default="enterprise-core", description="Component name")
    description: str = Field(default="", description="Release description")
    diff_content: str = Field(..., min_length=1, description="Diff or source hash payload")


class SignReleaseRequest(BaseModel):
    role: str = Field(..., description="Role: CTO_ARCH, SWE_BENCHMARK, CISO_ASTRA")
    bot_name: str = Field(..., description="Signatory bot name")


# --- Hierarchy Endpoints ---

@router.get("/hierarchy")
async def get_enterprise_hierarchy_chart() -> dict[str, Any]:
    """Retrieves full interactive organization chart tree for the War Room UI."""
    engine = get_enterprise_hierarchy()
    return await asyncio.to_thread(engine.get_full_org_chart)


@router.get("/csuite")
async def get_csuite_swarm() -> dict[str, Any]:
    """Retrieves all 4 C-Suite leadership roles."""
    engine = get_enterprise_hierarchy()
    csuite = await asyncio.to_thread(engine.get_csuite)
    return {"csuite": {k: v.model_dump() for k, v in csuite.items()}}


@router.post("/departments", status_code=201)
async def synthesize_department(payload: SynthesizeDepartmentRequest) -> dict[str, Any]:
    """Dynamically synthesizes a new sub-department with capability contracts."""
    engine = get_enterprise_hierarchy()
    dept = await asyncio.to_thread(
        engine.synthesize_custom_subdepartment,
        name=payload.name,
        department_key=payload.department_key,
        lead_bot_name=payload.lead_bot_name,
        lead_title=payload.lead_title,
        capabilities=payload.capabilities,
        initial_budget=payload.initial_budget,
    )
    return dept.model_dump()


# --- Cross-Department RFCs & Epistemic Debate Endpoints ---

@router.get("/rfcs")
async def list_rfcs(status: str | None = None) -> list[dict[str, Any]]:
    """Lists cross-department RFCs, filterable by status."""
    protocol = get_rfc_protocol()
    rfcs = await asyncio.to_thread(protocol.list_rfcs, status=status)
    return [r.model_dump() for r in rfcs]


@router.post("/rfcs", status_code=201)
async def create_rfc(payload: CreateRFCRequest) -> dict[str, Any]:
    """Proposes a new formal RFC on the Enterprise Blackboard."""
    protocol = get_rfc_protocol()
    rfc = await asyncio.to_thread(
        protocol.create_rfc,
        title=payload.title,
        author_bot=payload.author_bot,
        department=payload.department,
        summary=payload.summary,
        proposal_content=payload.proposal_content,
        affected_departments=payload.affected_departments,
    )
    return rfc.model_dump()


@router.get("/rfcs/{rfc_id}")
async def get_rfc(rfc_id: str) -> dict[str, Any]:
    """Retrieves RFC details including reviews and debate thread."""
    protocol = get_rfc_protocol()
    rfc = await asyncio.to_thread(protocol.get_rfc, rfc_id)
    if not rfc:
        raise HTTPException(status_code=404, detail=f"RFC '{rfc_id}' not found.")
    return rfc.model_dump()


@router.post("/rfcs/{rfc_id}/review", status_code=201)
async def submit_rfc_review(rfc_id: str, payload: SubmitRFCReviewRequest) -> dict[str, Any]:
    """Submits a multi-agent review on an active RFC."""
    protocol = get_rfc_protocol()
    try:
        review = await asyncio.to_thread(
            protocol.submit_review,
            rfc_id=rfc_id,
            reviewer_bot=payload.reviewer_bot,
            department=payload.department,
            verdict=payload.verdict,
            argument=payload.argument,
            epistemic_confidence=payload.epistemic_confidence,
        )
        return review.model_dump()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"RFC '{rfc_id}' not found.")


@router.post("/rfcs/{rfc_id}/debate", status_code=201)
async def submit_debate_argument(rfc_id: str, payload: SubmitDebateArgumentRequest) -> dict[str, Any]:
    """Submits an argument into the RFC epistemic debate thread."""
    protocol = get_rfc_protocol()
    try:
        arg = await asyncio.to_thread(
            protocol.submit_debate_argument,
            rfc_id=rfc_id,
            speaker_bot=payload.speaker_bot,
            department=payload.department,
            stance=payload.stance,
            claim=payload.claim,
            evidence=payload.evidence,
            counter_to_id=payload.counter_to_id,
            epistemic_weight=payload.epistemic_weight,
        )
        return arg.model_dump()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"RFC '{rfc_id}' not found.")


@router.post("/rfcs/{rfc_id}/gate")
async def evaluate_rfc_gating(rfc_id: str) -> dict[str, Any]:
    """Evaluates consensus gating for an RFC."""
    protocol = get_rfc_protocol()
    try:
        passed, score, reason = await asyncio.to_thread(protocol.evaluate_consensus_gating, rfc_id)
        return {
            "rfc_id": rfc_id,
            "gating_passed": passed,
            "consensus_score": score,
            "reason": reason,
        }
    except KeyError:
        raise HTTPException(status_code=404, detail=f"RFC '{rfc_id}' not found.")


# --- Mission-to-Sprint Pipeline Endpoints ---

@router.get("/missions/pipeline")
async def get_missions_pipeline() -> dict[str, Any]:
    """Retrieves all missions, epics, specs, and dynamic DAG sprints."""
    pipeline = get_mission_pipeline()
    epics = await asyncio.to_thread(pipeline.list_epics)
    sprints = await asyncio.to_thread(pipeline.list_sprints)
    return {
        "epics": [e.model_dump() for e in epics],
        "sprints": [s.model_dump() for s in sprints],
    }


@router.post("/missions/decompose", status_code=201)
async def decompose_mission(payload: DecomposeMissionRequest) -> dict[str, Any]:
    """Decomposes a strategic enterprise mission into cross-department epics and technical specs."""
    pipeline = get_mission_pipeline()
    epics = await asyncio.to_thread(
        pipeline.decompose_strategic_mission,
        mission_id=payload.mission_id,
        objective=payload.objective,
    )
    specs = []
    for e in epics:
        spec = await asyncio.to_thread(pipeline.synthesize_technical_spec, e.epic_id)
        specs.append(spec)
    return {
        "mission_id": payload.mission_id,
        "epics": [e.model_dump() for e in epics],
        "specs": [s.model_dump() for s in specs],
    }


@router.post("/sprints/compile", status_code=201)
async def compile_sprint(payload: CompileSprintRequest) -> dict[str, Any]:
    """Compiles a technical specification into an executable Dynamic DAG Sprint."""
    pipeline = get_mission_pipeline()
    try:
        sprint = await asyncio.to_thread(pipeline.compile_dynamic_dag_sprint, payload.spec_id)
        return sprint.model_dump()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Spec '{payload.spec_id}' not found.")


@router.post("/sprints/{sprint_id}/step")
async def step_sprint(sprint_id: str) -> dict[str, Any]:
    """Advances task execution across topological layers in the DAG sprint."""
    pipeline = get_mission_pipeline()
    try:
        res = await asyncio.to_thread(pipeline.step_sprint_dag, sprint_id)
        return res
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Sprint '{sprint_id}' not found.")


# --- Department Token Treasury Endpoints ---

@router.get("/treasury")
async def get_treasury_status() -> dict[str, Any]:
    """Retrieves department budget allocations, burn rate monitoring, and circuit breaker states."""
    treasury = get_department_treasury()
    return await asyncio.to_thread(treasury.get_overall_telemetry)


@router.post("/treasury/allocate")
async def allocate_treasury_tokens(payload: TreasuryAllocateRequest) -> dict[str, Any]:
    """Allocates or replenishes token budget for a department."""
    treasury = get_department_treasury()
    alloc = await asyncio.to_thread(treasury.allocate_tokens, payload.dept_id, payload.tokens)
    return alloc.model_dump()


@router.post("/treasury/reset-breaker")
async def reset_circuit_breaker(payload: ResetCircuitBreakerRequest) -> dict[str, Any]:
    """Authoritatively resets a tripped circuit breaker for a department."""
    treasury = get_department_treasury()
    try:
        alloc = await asyncio.to_thread(treasury.reset_circuit_breaker, payload.dept_id, payload.new_threshold_tpm)
        return alloc.model_dump()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Department '{payload.dept_id}' not found in treasury.")


# --- Quality Council Quorum Endpoints ---

@router.get("/council/releases")
async def list_releases() -> list[dict[str, Any]]:
    """Lists release candidates and active production releases with cryptographic signatures."""
    council = get_council_quorum_engine()
    releases = await asyncio.to_thread(council.list_releases)
    return [r.model_dump() for r in releases]


@router.post("/council/stage", status_code=201)
async def stage_release(payload: StageReleaseRequest) -> dict[str, Any]:
    """Stages a new candidate release for holdout benchmarking and multi-sig verification."""
    council = get_council_quorum_engine()
    candidate = await asyncio.to_thread(
        council.stage_candidate_release,
        version=payload.version,
        component=payload.component,
        description=payload.description,
        diff_content=payload.diff_content,
    )
    return candidate.model_dump()


@router.post("/council/releases/{release_id}/benchmark")
async def run_release_benchmark(release_id: str) -> dict[str, Any]:
    """Runs candidate through the SWE holdout test suite and attaches benchmark signature."""
    council = get_council_quorum_engine()
    try:
        score = await asyncio.to_thread(council.run_holdout_benchmark, release_id)
        release = await asyncio.to_thread(council.get_release, release_id)
        return {
            "release_id": release_id,
            "holdout_benchmark_score": score,
            "holdout_passed": score >= 90.0,
            "release": release.model_dump() if release else None,
        }
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Release '{release_id}' not found.")


@router.post("/council/releases/{release_id}/sign")
async def sign_release(release_id: str, payload: SignReleaseRequest) -> dict[str, Any]:
    """Applies a cryptographic signature from CTO_ARCH, SWE_BENCHMARK, or CISO_ASTRA."""
    council = get_council_quorum_engine()
    try:
        sig = await asyncio.to_thread(council.sign_release, release_id, payload.role, payload.bot_name)
        release = await asyncio.to_thread(council.get_release, release_id)
        return {
            "signature": sig.model_dump(),
            "release_status": release.status if release else "unknown",
            "release": release.model_dump() if release else None,
        }
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Release '{release_id}' not found.")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/council/releases/{release_id}/promote")
async def promote_release(release_id: str) -> dict[str, Any]:
    """Promotes a multi-sig verified release to active production with zero-downtime hot-swap."""
    council = get_council_quorum_engine()
    try:
        promoted = await asyncio.to_thread(council.promote_release_zero_downtime, release_id)
        return promoted.model_dump()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Release '{release_id}' not found.")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


# --- Discovery, Latency, Security & Heartbeat Endpoints ---

@router.get("/discovery")
async def get_discovery_overview() -> dict[str, Any]:
    """Returns discovered feature gaps, latency profiles, and AST security scan reports."""
    disc = get_discovery_and_optimization_engine()
    gaps = await asyncio.to_thread(disc.discover_feature_gaps)
    latencies = await asyncio.to_thread(disc.profile_latencies)
    latest_scan = await asyncio.to_thread(disc.get_latest_scan)
    return {
        "feature_gaps": [g.model_dump() for g in gaps],
        "latency_profiles": [l.model_dump() for l in latencies],
        "latest_security_scan": latest_scan.model_dump() if latest_scan else None,
    }


@router.post("/heartbeat")
async def trigger_heartbeat() -> dict[str, Any]:
    """Executes an atomic enterprise heartbeat cycle."""
    coordinator = get_enterprise_heartbeat_coordinator()
    result = await asyncio.to_thread(coordinator.step_heartbeat_cycle)
    return result


@router.get("/telemetry")
async def get_telemetry() -> dict[str, Any]:
    """Retrieves real-time telemetry for the live War Room dashboard."""
    coordinator = get_enterprise_heartbeat_coordinator()
    telemetry = await asyncio.to_thread(coordinator.get_telemetry)
    return telemetry.model_dump()


# Multi-mount routers for Gateway API and frontend proxy compatibility
api_enterprise_router = APIRouter(prefix="/api/enterprise", tags=["enterprise"])
api_enterprise_router.include_router(_inner_router)

gateway_enterprise_router = APIRouter(prefix="/api/gateway/enterprise", tags=["enterprise"])
gateway_enterprise_router.include_router(_inner_router)

# Export standard router (for /api/enterprise) and gateway_router (for /api/gateway/enterprise)
router = api_enterprise_router
gateway_router = gateway_enterprise_router

__all__ = ["router", "gateway_router", "api_enterprise_router", "gateway_enterprise_router"]
