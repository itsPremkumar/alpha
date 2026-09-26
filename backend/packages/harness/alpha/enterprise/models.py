"""Data models, contracts, and enums for the Autonomous AI Software Enterprise."""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

#: Neutral, disclosed baseline used when no real evidence backs a score.
#: Mirrors the deliberation-side neutral 0.5 baseline (commit b7e8f3e): an
#: honest "unknown", never a measured confidence.
NEUTRAL_CONFIDENCE_BASELINE = 0.5

#: How a stored confidence/weight was produced. Always serialized next to the
#: value so consumers can tell an honest baseline from a caller-supplied
#: rating from illustrative bootstrap demo data.
CONFIDENCE_BASIS_NEUTRAL = "neutral_baseline_0.5"  # field omitted; honest unknown
CONFIDENCE_BASIS_CALLER_SUPPLIED = "caller_supplied"  # explicit rating supplied by the caller
CONFIDENCE_BASIS_SEED_DEMO = "seed_demo_data"  # bootstrap demo record, not a live review


class CSuiteRole(StrEnum):
    CEO = "ceo"      # Executive Director
    CTO = "cto"      # Lead Architect
    CPO = "cpo"      # Product & Market Strategist
    CISO = "ciso"    # Quality & Security Director


class EnterpriseDepartment(StrEnum):
    ENGINEERING = "engineering"
    ARCHITECTURE = "architecture"
    SECURITY = "security"
    PERFORMANCE = "performance"
    DOCUMENTATION = "documentation"


class ClearanceLevel(StrEnum):
    L1_WORKER = "L1_WORKER"
    L2_SPECIALIST = "L2_SPECIALIST"
    L3_LEAD = "L3_LEAD"
    L4_DIRECTOR = "L4_DIRECTOR"
    L5_CSUITE = "L5_CSUITE"


class CapabilityContract(BaseModel):
    contract_id: str = Field(default_factory=lambda: f"cap-{uuid.uuid4().hex[:8]}")
    role_id: str
    title: str
    department: str
    clearance: ClearanceLevel = ClearanceLevel.L2_SPECIALIST
    capabilities: list[str] = Field(default_factory=list)
    max_concurrency: int = 4
    decision_authority: list[str] = Field(default_factory=list)
    active: bool = True
    created_at: float = Field(default_factory=time.time)


class OrgNode(BaseModel):
    node_id: str = Field(default_factory=lambda: f"node-{uuid.uuid4().hex[:8]}")
    bot_name: str
    title: str
    role_type: str  # c_suite, lead, worker
    department: str
    reports_to: str | None = None
    subordinates: list[str] = Field(default_factory=list)
    contract: CapabilityContract
    status: str = "active"  # active, busy, idle, recovering


class DepartmentHierarchy(BaseModel):
    dept_id: str
    name: str
    department_type: EnterpriseDepartment
    lead_bot_name: str
    lead_title: str
    workers: list[OrgNode] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    token_budget: int = 500000
    token_spent: int = 0
    burn_rate_tpm: float = 0.0
    circuit_breaker_active: bool = False


class MissionEpic(BaseModel):
    epic_id: str = Field(default_factory=lambda: f"epic-{uuid.uuid4().hex[:8]}")
    mission_id: str
    title: str
    description: str
    target_department: str
    priority: int = 1
    status: str = "open"  # open, in_progress, completed
    created_at: float = Field(default_factory=time.time)


class TechnicalSpec(BaseModel):
    spec_id: str = Field(default_factory=lambda: f"spec-{uuid.uuid4().hex[:8]}")
    epic_id: str
    title: str
    architect_bot: str
    requirements: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    status: str = "approved"
    created_at: float = Field(default_factory=time.time)


class DAGTask(BaseModel):
    task_id: str = Field(default_factory=lambda: f"dag-task-{uuid.uuid4().hex[:8]}")
    title: str
    assigned_bot: str
    department: str
    dependencies: list[str] = Field(default_factory=list)
    status: str = "pending"  # pending, ready, in_progress, completed, failed
    definition_of_done: list[str] = Field(default_factory=list)
    dod_verified: bool = False
    execution_output: str = ""
    estimated_tokens: int = 5000
    actual_tokens: int = 0


class DAGSprint(BaseModel):
    sprint_id: str = Field(default_factory=lambda: f"sprint-{uuid.uuid4().hex[:8]}")
    spec_id: str
    title: str
    tasks: list[DAGTask] = Field(default_factory=list)
    topology_layers: list[list[str]] = Field(default_factory=list)
    status: str = "active"  # planned, active, completed, blocked
    progress_percent: float = 0.0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class RFCStatus(StrEnum):
    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    DEBATING = "debating"
    APPROVED = "approved"
    REJECTED = "rejected"
    IMPLEMENTED = "implemented"


class RFCReview(BaseModel):
    review_id: str = Field(default_factory=lambda: f"rvw-{uuid.uuid4().hex[:8]}")
    reviewer_bot: str
    department: str
    verdict: str  # approve, reject, amend
    epistemic_confidence: float = NEUTRAL_CONFIDENCE_BASELINE  # score in [0.0, 1.0]; NOT a measured probability — see confidence_basis
    #: "neutral_baseline_0.5" | "caller_supplied" | "seed_demo_data" — how the score was produced.
    confidence_basis: str = CONFIDENCE_BASIS_NEUTRAL
    argument: str
    created_at: float = Field(default_factory=time.time)


class DebateArgument(BaseModel):
    argument_id: str = Field(default_factory=lambda: f"arg-{uuid.uuid4().hex[:8]}")
    speaker_bot: str
    department: str
    stance: str  # pro, con, amend, synthesis
    claim: str
    evidence: str
    counter_to_id: str | None = None
    epistemic_weight: float = NEUTRAL_CONFIDENCE_BASELINE  # score in [0.0, 1.0]; see confidence_basis
    #: "neutral_baseline_0.5" | "caller_supplied" | "seed_demo_data" — how the score was produced.
    confidence_basis: str = CONFIDENCE_BASIS_NEUTRAL
    created_at: float = Field(default_factory=time.time)


class EnterpriseRFC(BaseModel):
    rfc_id: str = Field(default_factory=lambda: f"rfc-{uuid.uuid4().hex[:8]}")
    title: str
    author_bot: str
    department: str
    status: RFCStatus = RFCStatus.DRAFT
    summary: str
    proposal_content: str
    affected_departments: list[str] = Field(default_factory=list)
    reviews: list[RFCReview] = Field(default_factory=list)
    debate_thread: list[DebateArgument] = Field(default_factory=list)
    consensus_score: float = 0.0
    gating_passed: bool = False
    gating_reason: str = "Pending multi-agent review and epistemic debate"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class TreasuryAllocation(BaseModel):
    dept_id: str
    department_name: str
    allocated_tokens: int = 500000
    spent_tokens: int = 0
    balance_tokens: int = 500000
    burn_rate_tpm: float = 0.0
    burn_history: list[dict[str, Any]] = Field(default_factory=list)
    roi_velocity: float = 1.0  # completed items per 10k tokens
    circuit_breaker_active: bool = False
    circuit_breaker_threshold_tpm: float = 80000.0
    last_updated: float = Field(default_factory=time.time)


class CryptographicSignature(BaseModel):
    signature_id: str = Field(default_factory=lambda: f"sig-{uuid.uuid4().hex[:8]}")
    signatory_role: str  # CTO_ARCH, SWE_BENCHMARK, CISO_ASTRA
    signatory_bot: str
    signature_hash: str
    payload_digest: str
    timestamp: float = Field(default_factory=time.time)
    verified: bool = True


class ReleaseCandidate(BaseModel):
    release_id: str = Field(default_factory=lambda: f"rel-{uuid.uuid4().hex[:8]}")
    version: str
    component: str
    description: str
    diff_hash: str
    holdout_benchmark_score: float = 0.0
    holdout_passed: bool = False
    security_scan_passed: bool = False
    architecture_approved: bool = False
    signatures: list[CryptographicSignature] = Field(default_factory=list)
    status: str = "staged"  # staged, multi_sig_verified, promoted_active, rejected
    promoted_at: float | None = None
    created_at: float = Field(default_factory=time.time)


class FeatureGap(BaseModel):
    gap_id: str = Field(default_factory=lambda: f"gap-{uuid.uuid4().hex[:8]}")
    area: str
    severity: str  # low, medium, high, critical
    description: str
    remediation_proposal: str
    status: str = "discovered"  # discovered, converted_to_epic, resolved
    discovered_at: float = Field(default_factory=time.time)


class LatencyProfile(BaseModel):
    component: str
    p50_ms: float
    p95_ms: float
    p99_ms: float
    is_bottleneck: bool = False
    optimization_suggestion: str = ""
    last_profiled: float = Field(default_factory=time.time)


class SecurityScanReport(BaseModel):
    scan_id: str = Field(default_factory=lambda: f"sec-{uuid.uuid4().hex[:8]}")
    files_scanned: int = 0
    ast_violations: list[dict[str, Any]] = Field(default_factory=list)
    security_score: float | None = None
    ast_boundary_passed: bool = True
    cve_alerts: list[str] = Field(default_factory=list)
    timestamp: float = Field(default_factory=time.time)


class EnterpriseTelemetry(BaseModel):
    heartbeat_cycle: int = 0
    uptime_seconds: float = 0.0
    csuite_status: dict[str, str] = Field(default_factory=dict)
    #: Counts are 0 until get_telemetry() overwrites them with real len() of
    #: the live hierarchy — a fresh construction has no measured org size.
    departments_count: int = 0
    active_workers_count: int = 0
    active_rfcs_count: int = 0
    approved_rfcs_count: int = 0
    active_sprints_count: int = 0
    tasks_completed_count: int = 0
    treasury_overall_burn_rate_tpm: float = 0.0
    treasury_circuit_breakers_tripped: int = 0
    #: None = no latency profile has been recorded yet (honest "no data"),
    #: never a fabricated millisecond figure.
    system_latency_p95_ms: float | None = None
    security_posture_score: float | None = None
    holdout_pass_rate_percent: float | None = None
    latest_release_version: str | None = None
    stagnation_recovery_status: str = "nominal"
    last_heartbeat_timestamp: str = ""
