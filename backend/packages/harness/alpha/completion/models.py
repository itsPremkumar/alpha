"""Canonical domain objects for the Alpha Completion Engine.

One persisted representation of the work contract: a :class:`CompletionScope`
owns its :class:`Contract`, its :class:`TaskNode` DAG, its :class:`Gate`s, the
:class:`Evidence` those gates produced, and the :class:`DispatchRecord`s that
launched the workers. Markdown (or a chat message) may *render* any of this,
but it is never the source of truth for concurrent execution — the plan is
explicit that a human-readable export must not be what a scheduler reads.

Three vocabulary rules are load-bearing and are the reason the enums look the
way they do:

* **Returned is not verified.** A worker's prose lands in
  :class:`DispatchRecord.state` ``returned``; a task only becomes
  ``verified`` after its own gates pass against current evidence.
* **Not run is not passed.** :class:`CheckOutcome.NOT_RUN` and
  :class:`GateStatus.PENDING` can never satisfy a required gate, and no
  default in this module converts them to a pass.
* **Waived is not passed.** A :class:`GateStatus.WAIVED` gate is a visible
  owner decision, which is why finalization treats it as a blocker rather than
  as satisfaction (``finalization.py``).

Every model is ``extra="forbid"``: a field added to a wire shape must be
chosen deliberately rather than leaking into stored JSON and API responses by
default.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScopeStatus(StrEnum):
    """Lifecycle of one user request under completion discipline.

    ``COMPLETE`` is deliberately reachable only from ``VERIFYING`` and only
    through ``finalization.evaluate`` — see ``state_machine.py``.
    """

    PLANNING = "planning"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    HANDOFF_REQUIRED = "handoff_required"


class TaskState(StrEnum):
    """Worker/task state; transitions are centrally validated."""

    WAITING = "waiting"  # dependencies incomplete
    READY = "ready"  # dependencies verified, may be dispatched
    IN_FLIGHT = "in_flight"  # a host-confirmed worker handle exists
    RETURNED = "returned"  # a worker sent a result — not yet trusted
    VERIFYING = "verifying"  # gates/evidence being evaluated
    VERIFIED = "verified"  # own required gates pass at the current revision
    BLOCKED = "blocked"  # needs a dependency, approval, credential or human
    ABANDONED = "abandoned"  # explicitly stopped without success, with a reason
    FAILED = "failed"  # failed with no remaining permitted retry


class GateStatus(StrEnum):
    """Gate state. ``WAIVED``/``ABANDONED`` are visible decisions, never passes."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    STALE = "stale"
    BLOCKED = "blocked"
    WAIVED = "waived"
    ABANDONED = "abandoned"


class GateKind(StrEnum):
    """Coarse gate category used by policy (the exact check lives in ``check_ref``)."""

    COMMAND = "command"
    PYTEST = "pytest"
    FILE = "file"
    ARTIFACT = "artifact"
    API = "api"
    DATABASE_READONLY = "database_readonly"
    MANUAL_REVIEW = "manual_review"
    REGISTERED = "registered"


class CheckOutcome(StrEnum):
    """What one check execution actually did.

    ``PASSED`` is the only member that can satisfy a gate. ``BLOCKED`` (policy
    or missing approval), ``TIMED_OUT``, ``NOT_RUN`` and
    ``INVALID_CONFIGURATION`` are all distinct from ``FAILED``: "the check
    said no" and "the check never ran" lead to opposite decisions.
    """

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    TIMED_OUT = "timed_out"
    NOT_RUN = "not_run"
    INVALID_CONFIGURATION = "invalid_configuration"


class Freshness(StrEnum):
    """Whether recorded evidence still speaks for the current world."""

    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalPolicy(StrEnum):
    """How a gate obtains permission to execute."""

    AUTOMATIC = "automatic"
    ASK_USER = "ask_user"
    DENIED = "denied"


class DispatchState(StrEnum):
    """Launch-wave bookkeeping. ``requested`` is not ``started``."""

    REQUESTED = "requested"  # launch asked for; host has not confirmed
    STARTED = "started"  # host confirmed a handle/session id
    RETURNED = "returned"  # worker reported a result
    LAUNCH_FAILED = "launch_failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"  # handle exists but its state could not be read


class RequirementStatus(StrEnum):
    """Verdict for one requirement of the original request.

    Same three-valued vocabulary as ``alpha.mission.acceptance`` on purpose:
    an unreviewed requirement is ``unverified``, never ``met``.
    """

    UNVERIFIED = "unverified"
    MET = "met"
    NOT_MET = "not_met"


class Expectation(BaseModel):
    """Structured expected result for one check.

    Every field is optional *individually* but the model must carry at least
    one meaningful assertion — ``contract.validate_contract`` rejects a gate
    whose expectation is empty ("success" with nothing measured), and each
    check implementation decides which fields are meaningful for it.
    """

    model_config = ConfigDict(extra="forbid")

    exit_code: int | None = Field(default=0, description="Expected process exit code; null ignores exit status.")
    required_markers: list[str] = Field(default_factory=list, description="Substrings that must appear in the full output.")
    forbidden_markers: list[str] = Field(default_factory=list, description="Substrings that must not appear anywhere in the full output.")
    min_bytes: int | None = Field(default=None, description="Minimum size for a file/artifact check.")
    max_bytes: int | None = Field(default=None, description="Maximum size for a file/artifact check.")
    artifact_exists: str | None = Field(default=None, description="Workspace-relative path that must exist and be non-empty.")
    status_code: int | None = Field(default=None, description="Expected HTTP status for an api gate.")
    notes: str = Field(default="", max_length=2000, description="Why this expectation proves the outcome it claims.")

    def is_empty(self) -> bool:
        """True when nothing at all is measured — a gate that proves nothing."""
        return self.exit_code is None and not self.required_markers and not self.forbidden_markers and self.min_bytes is None and self.max_bytes is None and not self.artifact_exists and self.status_code is None


class ApprovalRecord(BaseModel):
    """A permission bound to exactly one gate definition.

    The digest is the point: re-parameterising the gate changes
    ``Gate.definition_digest`` and silently invalidates this approval, so an
    approval for ``pytest tests/x.py`` never covers ``rm -rf /``.
    """

    model_config = ConfigDict(extra="forbid")

    approved_by: str
    approved_at: float
    definition_digest: str
    reason: str = ""


class Gate(BaseModel):
    """One independently meaningful proof obligation."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str
    task_id: str
    title: str = Field(min_length=1, max_length=300)
    required: bool = True
    kind: GateKind
    check_ref: str = Field(min_length=1, description="Registered check implementation name, e.g. file.exists.")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Validated check payload; never free-form executable text.")
    expectation: Expectation
    owner: str = ""
    status: GateStatus = GateStatus.PENDING
    evidence_ids: list[str] = Field(default_factory=list)
    risk_tier: RiskTier = RiskTier.LOW
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTOMATIC
    approval: ApprovalRecord | None = None
    integration: bool = Field(default=False, description="Parent/root integration gate rather than a leaf deliverable check.")
    created_from_request_revision: int = 1
    definition_digest: str = ""
    attempts: int = 0
    last_failure: str = ""
    waiver_reason: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


class TaskNode(BaseModel):
    """A scoped unit of work with an output contract and an owner."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    title: str = Field(min_length=1, max_length=300)
    objective: str = ""
    parent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    gate_ids: list[str] = Field(default_factory=list)
    state: TaskState = TaskState.WAITING
    owner: str = ""
    owns: list[str] = Field(default_factory=list, description="Declared repository-relative write scope (OWNS).")
    expected_outputs: list[str] = Field(default_factory=list, description="Concrete artifacts this task must produce.")
    integration: bool = Field(default=False, description="Parent task that integrates its children behind its own gate.")
    dispatch_ids: list[str] = Field(default_factory=list)
    blocked_reason: str = ""
    attempts: int = 0
    max_attempts: int = 3
    depth: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0


class Evidence(BaseModel):
    """One recorded check execution — first class, never just a chat line."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    scope_id: str
    gate_id: str
    task_id: str
    check_ref: str
    check_version: str
    outcome: CheckOutcome
    reason: str = ""
    summary: str = Field(default="", max_length=4000, description="Short human-readable result; never a claim beyond the check.")
    exit_code: int | None = None
    output_digest: str = Field(default="", description="SHA-256 over the full captured output (the bytes that were matched).")
    output_preview: str = Field(default="", description="Bounded, control-character-stripped tail of the output.")
    output_truncated: bool = Field(default=False, description="True when the output exceeded the capture limit; such evidence can never pass.")
    artifacts: list[dict[str, Any]] = Field(default_factory=list, description="Produced/verified artifacts: {path, sha256, bytes}.")
    input_fingerprints: dict[str, str] = Field(default_factory=dict, description="Declared input path -> sha256 at run time.")
    freshness_basis: str = Field(default="definition_only", description="'definition+inputs' when inputs were declared, else 'definition_only'.")
    definition_digest: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    duration_seconds: float = 0.0
    context: dict[str, Any] = Field(default_factory=dict, description="Safe execution context: cwd, argv, policy id, host tool identity. Never secrets.")
    worker: str = ""
    freshness: Freshness = Freshness.CURRENT
    invalidation_reason: str = ""
    created_at: float = 0.0


class DispatchRecord(BaseModel):
    """One launch attempt in a dispatch wave, with its honest terminal state."""

    model_config = ConfigDict(extra="forbid")

    dispatch_id: str
    wave_id: str
    scope_id: str
    task_id: str
    agent: str = ""
    attempt: int = 1
    state: DispatchState = DispatchState.REQUESTED
    handle: str = Field(default="", description="Host session/job id once the host confirms the launch.")
    requested_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    launch_error: str = ""
    terminal_reason: str = ""
    artifact_refs: list[str] = Field(default_factory=list)
    verified: bool = False
    input_contract_revision: int = 1


class Requirement(BaseModel):
    """One explicit outcome of the original request, tracked to evidence."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    intent: str = Field(min_length=1, max_length=1000, description="What the user asked for, in the user's own terms.")
    mandatory: bool = True
    observable: str = Field(default="", description="How this will be proven; empty means it has not been bound to a check yet.")
    task_id: str = ""
    gate_ids: list[str] = Field(default_factory=list)
    status: RequirementStatus = RequirementStatus.UNVERIFIED
    evidence_ids: list[str] = Field(default_factory=list)
    request_revision: int = 1
    notes: str = Field(default="", description="Accepted amendments, or an explicit non-completion reason.")


class ContractFinding(BaseModel):
    """One deterministic validation finding against a contract."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: str = Field(description="'error' blocks dispatch/finalization; 'warning' is advisory.")
    message: str
    subject_id: str = ""


class Contract(BaseModel):
    """The acceptance contract: what "done" means, fixed before the work."""

    model_config = ConfigDict(extra="forbid")

    contract_id: str
    version: int = 1
    digest: str = Field(default="", description="SHA-256 over the canonical contract payload; changes when obligations change.")
    mode: str = Field(default="normal", description="normal | thorough | autopilot | coding | research.")
    requirements: list[Requirement] = Field(default_factory=list)
    findings: list[ContractFinding] = Field(default_factory=list)
    valid: bool = False
    validated_at: float | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def blocking_findings(self) -> list[ContractFinding]:
        return [f for f in self.findings if f.severity == "error"]


class ScopeBudget(BaseModel):
    """Scope-level hard budget. Children share it; none inherits a fresh copy."""

    model_config = ConfigDict(extra="forbid")

    max_tasks: int = 40
    max_depth: int = 4
    max_concurrency: int = 2
    max_gate_runs: int = 200
    wall_clock_seconds: float = 3600.0
    deadline_at: float | None = None
    gate_runs_used: int = 0
    exhausted: bool = False
    exhaustion_reason: str = ""


class ReconciliationReport(BaseModel):
    """Original-request-to-contract audit performed before finalization."""

    model_config = ConfigDict(extra="forbid")

    basis: str = Field(default="heuristic_clause_match", description="How clauses were matched; this is a heuristic, and says so.")
    clauses_seen: int = 0
    unrepresented: list[str] = Field(default_factory=list, description="Request clauses with no requirement covering them.")
    unmet: list[str] = Field(default_factory=list, description="Mandatory requirements without current passing evidence.")
    unverifiable: list[str] = Field(default_factory=list, description="Requirements bound to no observable at all.")
    clean: bool = False
    notes: str = ""


class Blocker(BaseModel):
    """One reason finalization cannot claim completion."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    subject_id: str = ""
    kind: str = Field(default="unverified", description="unverified | failed | blocked | abandoned | omitted | no_progress")


class FinalReport(BaseModel):
    """The honest outcome of a finalization attempt, and its evidence summary."""

    model_config = ConfigDict(extra="forbid")

    status: ScopeStatus
    enforced: bool = Field(default=True, description="False when the engine is in observe mode and did not move the scope.")
    complete: bool = False
    blockers: list[Blocker] = Field(default_factory=list)
    checks_ran: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    checks_stale: int = 0
    checks_not_run: int = 0
    requirements_met: int = 0
    requirements_total: int = 0
    not_rerun: list[str] = Field(default_factory=list, description="Required checks that were not re-executed in this attempt.")
    reconciliation: ReconciliationReport | None = None
    summary: str = ""
    generated_at: float = 0.0
    attempt: int = 0


class OwnershipClaim(BaseModel):
    """A declared write scope with an expiry, so a crashed worker's claim dies."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    owner: str
    paths: list[str] = Field(default_factory=list)
    claimed_at: float = 0.0
    expires_at: float = 0.0

    def expired(self, now: float) -> bool:
        return now >= self.expires_at


class CompletionScope(BaseModel):
    """One user request and everything required to prove it was carried out."""

    model_config = ConfigDict(extra="forbid")

    scope_id: str
    owner_id: str = ""
    thread_id: str = ""
    request_snapshot: str = Field(max_length=200_000, description="The original request, preserved so reconciliation has something to read.")
    request_revision: int = 1
    mode: str = "normal"
    status: ScopeStatus = ScopeStatus.PLANNING
    status_reason: str = ""
    contract: Contract
    tasks: dict[str, TaskNode] = Field(default_factory=dict)
    gates: dict[str, Gate] = Field(default_factory=dict)
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    dispatches: dict[str, DispatchRecord] = Field(default_factory=dict)
    ownership: list[OwnershipClaim] = Field(default_factory=list)
    budget: ScopeBudget = Field(default_factory=ScopeBudget)
    final_report: FinalReport | None = None
    last_verified_revision: int | None = None
    finalize_attempts: int = 0
    #: Consecutive finalization attempts whose progress fingerprint did not
    #: move. Bounded by ``completion.no_progress_limit``; exhaustion emits a
    #: handoff instead of trapping the caller in an endless repair loop.
    no_progress_count: int = 0
    progress_fingerprint: str = ""
    #: Monotonic id counters for records created inside this scope. Never
    #: derived from ``len(...)``: ids must not be reused after a deletion.
    counters: dict[str, int] = Field(default_factory=lambda: {"evidence": 0, "dispatch": 0, "wave": 0, "attempt": 0})
    created_at: float = 0.0
    updated_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None

    def task(self, task_id: str) -> TaskNode:
        from .errors import NotFound

        try:
            return self.tasks[task_id]
        except KeyError:
            raise NotFound(f"task {task_id!r} does not exist in scope {self.scope_id!r}", subject=task_id) from None

    def gate(self, gate_id: str) -> Gate:
        from .errors import NotFound

        try:
            return self.gates[gate_id]
        except KeyError:
            raise NotFound(f"gate {gate_id!r} does not exist in scope {self.scope_id!r}", subject=gate_id) from None

    def gates_of(self, task_id: str) -> list[Gate]:
        return [gate for gate in self.gates.values() if gate.task_id == task_id]

    def children_of(self, task_id: str) -> list[TaskNode]:
        return [task for task in self.tasks.values() if task.parent_id == task_id]

    def required_gates(self) -> list[Gate]:
        return [gate for gate in self.gates.values() if gate.required]

    def mandatory_requirements(self) -> list[Requirement]:
        return [req for req in self.contract.requirements if req.mandatory]


__all__ = [
    "ApprovalPolicy",
    "ApprovalRecord",
    "Blocker",
    "CheckOutcome",
    "CompletionScope",
    "Contract",
    "ContractFinding",
    "DispatchRecord",
    "DispatchState",
    "Evidence",
    "Expectation",
    "FinalReport",
    "Freshness",
    "Gate",
    "GateKind",
    "GateStatus",
    "OwnershipClaim",
    "ReconciliationReport",
    "Requirement",
    "RequirementStatus",
    "RiskTier",
    "ScopeBudget",
    "ScopeStatus",
    "TaskNode",
    "TaskState",
]
