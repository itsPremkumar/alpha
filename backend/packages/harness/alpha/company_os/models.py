"""Data models for the Company OS: durable, multi-tenant, evidence-honest organizations.

Design commitments shared with the rest of the harness:

* **Index, never duplicate.** Every field that names a real subsystem entity
  carries that entity's own id: ``project_id`` is an ``alpha.projects`` project,
  ``board_id`` is an ``alpha.kanban`` board, ``agent_handle`` is a
  ``alpha.bots`` profile name. The Company OS owns the *organisation*; the
  subsystems own the *work*. A second project or board model here would be the
  duplication this package exists to remove.
* **Measured or ``None``.** A number that was not measured is ``None``, never a
  flattering zero and never an invented percentage. Fields whose value depends
  on provenance carry a ``basis`` discriminator so a reader can tell a real
  reading from a declared default. See ``MeasurementBasis``.
* **Owner-scoped.** ``owner_id`` is server-assigned. Nothing in a request body
  may assert it.
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

#: Bump when an on-disk shape changes incompatibly. A store written by a newer
#: version is REFUSED by the loader, never guessed at.
COMPANY_STORE_SCHEMA_VERSION = 1


def new_id(prefix: str) -> str:
    """A short, collision-resistant, prefixed identifier."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def now_ts() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


class MeasurementBasis(StrEnum):
    """How a value was produced. Serialized beside every number that needs it.

    This is the mechanism that makes an "honest company" checkable: a value
    carrying ``SEED`` is an operator-declared starting target, and a reader can
    refuse to present it as an observation.
    """

    #: A real reading taken from a subsystem (a Kanban card, a run's token usage).
    MEASURED = "measured"
    #: An operator-declared starting value shipped with a blueprint.
    SEED = "seed"
    #: No measurement exists yet. The paired value must be ``None``.
    UNMEASURED = "unmeasured"
    #: Derived by arithmetic over measured inputs.
    DERIVED = "derived"


class CompanyState(StrEnum):
    """Lifecycle of a company. Mirrors a real org's operational status."""

    DRAFT = "draft"
    HIRING = "hiring"
    ACTIVE = "active"
    DEGRADED = "degraded"
    PAUSED = "paused"
    STOPPED = "stopped"
    ARCHIVED = "archived"


class OrgUnitKind(StrEnum):
    """Structural layers of an organization, coarsest first."""

    DIVISION = "division"
    DEPARTMENT = "department"
    TEAM = "team"
    SQUAD = "squad"


class EmploymentType(StrEnum):
    EXECUTIVE = "executive"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    CONTRACTOR = "contractor"


class EmploymentStatus(StrEnum):
    ACTIVE = "active"
    PROBATION = "probation"
    SUSPENDED = "suspended"
    NOTICE = "notice"
    TERMINATED = "terminated"


class ClearanceLevel(StrEnum):
    """Ordered authority tiers. A clearance is the ceiling on what a role may do."""

    L1_WORKER = "L1_WORKER"
    L2_SPECIALIST = "L2_SPECIALIST"
    L3_LEAD = "L3_LEAD"
    L4_DIRECTOR = "L4_DIRECTOR"
    L5_EXECUTIVE = "L5_EXECUTIVE"

    @property
    def rank(self) -> int:
        return int(self.value[1])


class AutonomyTier(StrEnum):
    """How much the loop may do without a human.

    Ordered: each tier's authority is a superset of the one below it. The loop
    reads this on every tick and refuses any action above it, so lowering the
    tier takes effect on the next tick rather than at the next restart.
    """

    #: Read and report. No execution.
    T0_OBSERVE = "T0_observe"
    #: Plan and stage work for approval. No execution.
    T1_ADVISE = "T1_advise"
    #: Execute cards that are already assigned to an agent.
    T2_EXECUTE_ROUTINE = "T2_execute_routine"
    #: Also create projects and cards, reassign agents, replan.
    T3_SELF_DIRECT = "T3_self_direct"
    #: Also restructure the org and evolve prompts without per-item approval.
    T4_AUTONOMOUS = "T4_autonomous"

    @property
    def rank(self) -> int:
        return int(self.value[1])


class ObjectiveStatus(StrEnum):
    ACTIVE = "active"
    ACHIEVED = "achieved"
    AT_RISK = "at_risk"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class ApprovalKind(StrEnum):
    """Everything the loop may propose but must not apply unilaterally."""

    HIRE = "hire"
    TERMINATE = "terminate"
    PROMOTE = "promote"
    RESTRUCTURE = "restructure"
    BUDGET_INCREASE = "budget_increase"
    CREATE_PROJECT = "create_project"
    PROMPT_EVOLUTION = "prompt_evolution"
    STRATEGY_REPLAN = "strategy_replan"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TickOutcome(StrEnum):
    """Why a loop tick stopped. Never a success the loop did not measure."""

    #: Work was found and dispatched.
    DISPATCHED = "dispatched"
    #: Nothing actionable. Zero model tokens spent. This is a healthy state.
    IDLE = "idle"
    #: A budget ceiling refused the tick before it ran.
    BUDGET_EXHAUSTED = "budget_exhausted"
    #: Consecutive ticks produced no verified outcome. Parked.
    NO_PROGRESS = "no_progress"
    #: The company is paused or archived.
    PAUSED = "paused"
    #: Too many actions in one window; the remainder was deferred.
    DEFERRED = "deferred"
    #: An action failed. The tick continues; the failure is recorded per action.
    PARTIAL = "partial"
    #: An unexpected error aborted the tick. Never reported as success.
    ERROR = "error"


# --------------------------------------------------------------------------- #
# Governance
# --------------------------------------------------------------------------- #


class Charter(BaseModel):
    """The company's founding document. Its mission is the alignment input the
    loop scores discovered work against, so it is required rather than default."""

    mission: str
    vision: str = ""
    values: list[str] = Field(default_factory=list)
    #: Free-form operating constraints an operator wants enforced by humans.
    constraints: list[str] = Field(default_factory=list)
    autonomy_tier: AutonomyTier = AutonomyTier.T1_ADVISE
    #: Hard spend ceilings. ``None`` means "not yet set" and the loop then
    #: refuses to spend rather than defaulting to an invented allowance.
    budget_total_usd: float | None = None
    budget_daily_usd: float | None = None
    currency: str = "USD"


class KeyResult(BaseModel):
    """One measurable outcome attached to an objective.

    ``baseline`` and ``target`` are declared by the operator at authoring time,
    so they carry ``SEED`` basis. ``current`` is what the company actually
    measured, and stays ``None`` until a real reading exists.
    """

    key_result_id: str = Field(default_factory=lambda: new_id("kr"))
    title: str
    unit: str = "%"
    baseline: float | None = None
    target: float | None = None
    current: float | None = None
    #: ``None`` when nothing has been measured; a number here is always real.
    current_basis: MeasurementBasis = MeasurementBasis.UNMEASURED
    higher_is_better: bool = True
    measured_at: float | None = None
    #: Which KPI or board projection feeds this key result. Empty means nobody
    #: wired it to a real signal yet, and a reader must treat it as untracked.
    source_kpi_id: str | None = None
    source_board_id: str | None = None
    source_column: str | None = None


class Objective(BaseModel):
    """An OKR objective: a qualitative goal with measurable key results.

    Progress is ``None`` while any key result is unmeasured. A progress figure
    over a partially-measured objective would be arithmetic over nothing.
    """

    objective_id: str = Field(default_factory=lambda: new_id("obj"))
    title: str
    rationale: str = ""
    key_results: list[KeyResult] = Field(default_factory=list)
    status: ObjectiveStatus = ObjectiveStatus.ACTIVE
    #: The org unit accountable for moving this objective.
    owner_unit_id: str | None = None
    #: Real project ids that serve this objective.
    project_ids: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=now_ts)

    @property
    def progress_percent(self) -> float | None:
        """Measured completion, or ``None`` when nothing is measurable yet."""
        measurable = [kr for kr in self.key_results if kr.current is not None and kr.target is not None]
        if not measurable:
            return None
        total = 0.0
        for kr in measurable:
            start = kr.baseline if kr.baseline is not None else kr.target
            if kr.target == start:
                continue
            frac = (kr.current - start) / (kr.target - start)
            total += frac if kr.higher_is_better else -frac
        return round(max(0.0, min(1.0, total / len(measurable))) * 100.0, 1)


class Role(BaseModel):
    """A role in the org chart: clearance plus explicit decision authority.

    ``decision_authority`` is the RACI "Accountable" set — what this role may
    sign off on without escalating. The company orchestrator checks it before
    approving an action, so authority is data rather than prose in a prompt.
    """

    role_id: str = Field(default_factory=lambda: new_id("role"))
    title: str
    clearance: ClearanceLevel = ClearanceLevel.L1_WORKER
    responsibilities: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    decision_authority: list[str] = Field(default_factory=list)
    #: A hard cap on simultaneous assignments, so a "team of one" cannot be
    #: handed ten cards and silently do none of them.
    max_concurrent_assignments: int = 3
    #: Optional real bot template slug to hire against.
    bot_template: str | None = None


class OrgUnit(BaseModel):
    """A structural node: division → department → team → squad."""

    unit_id: str = Field(default_factory=lambda: new_id("unit"))
    name: str
    kind: OrgUnitKind = OrgUnitKind.DEPARTMENT
    parent_unit_id: str | None = None
    #: The agent handle accountable for this unit. Must name a real bot.
    lead_agent_handle: str | None = None
    purpose: str = ""
    #: The real group room this unit coordinates in, when one exists.
    room_id: str | None = None
    #: The real Kanban board backing this unit's work, when one exists.
    board_id: str | None = None
    role_id: str | None = None


class Employment(BaseModel):
    """The join record that makes a bot an *employee of this specific company*.

    The bot itself lives in ``alpha.bots``; this record says where it sits in
    *this* org, who it reports to, and what it is accountable for. One agent can
    hold several employments (a fractional contractor across two companies), and
    a company can be created without inventing any bot.
    """

    employment_id: str = Field(default_factory=lambda: new_id("emp"))
    #: The real ``alpha.bots`` profile name. Not a display string.
    agent_handle: str
    employment_type: EmploymentType = EmploymentType.EMPLOYEE
    status: EmploymentStatus = EmploymentStatus.ACTIVE
    role_id: str | None = None
    unit_id: str | None = None
    reports_to_handle: str | None = None
    hired_at: float = Field(default_factory=now_ts)
    #: Backfill cover for this person, mirroring a real org's redundancy policy.
    backup_agent_handle: str | None = None
    #: Operator cadence at which this employment gets a performance review.
    review_cadence_days: int = 30
    last_review_at: float | None = None
    #: Measured, copied from ``alpha.bots.performance``. ``None`` until read.
    performance_score: float | None = None
    performance_basis: MeasurementBasis = MeasurementBasis.UNMEASURED
    notes: str = ""

    def is_active(self) -> bool:
        return self.status in (EmploymentStatus.ACTIVE, EmploymentStatus.PROBATION)


class Accountability(BaseModel):
    """A duty that outlives any individual holder.

    This is the Company OS's version of a runbook. The primary holder may fail
    at any moment; the duty moves to the backup, and if that also fails, to the
    escalation contact. The chain is exercised by the loop's health phase.
    """

    accountability_id: str = Field(default_factory=lambda: new_id("acct"))
    title: str
    unit_id: str | None = None
    primary_agent_handle: str
    backup_agent_handle: str | None = None
    escalation_agent_handle: str | None = None
    #: ``active`` | ``failed_over`` | ``degraded`` | ``critical`` | ``vacant``
    status: str = "active"
    active_agent_handle: str | None = None
    #: What the duty covers. Free text is fine; a checklist is better.
    scope: list[str] = Field(default_factory=list)
    last_reviewed_at: float | None = None
    #: Why the duty last moved, with a real timestamp. Empty until it has.
    failover_reason: str = ""
    failover_at: float | None = None

    def active_or_primary(self) -> str:
        return self.active_agent_handle or self.primary_agent_handle


class ApprovalRequest(BaseModel):
    """A proposal the loop may not apply on its own.

    Nothing in this record is applied. Approving it is a separate, explicit,
    owner-scoped mutation, and rejecting it is recorded rather than deleted so a
    later reviewer can see what was proposed and why it was declined.
    """

    approval_id: str = Field(default_factory=lambda: new_id("appr"))
    kind: ApprovalKind
    title: str
    rationale: str = ""
    #: What would change if approved, as a structured payload.
    payload: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_by: str = "company_loop"
    requested_at: float = Field(default_factory=now_ts)
    decided_at: float | None = None
    decided_by: str | None = None
    decision_note: str = ""


# --------------------------------------------------------------------------- #
# Portfolio links — ids into real subsystems, never copies
# --------------------------------------------------------------------------- #


class ProjectLink(BaseModel):
    """A real ``alpha.projects`` project that belongs to this company."""

    project_id: str
    title: str = ""
    lead_agent_handle: str | None = None
    objective_ids: list[str] = Field(default_factory=list)
    #: ``todo`` | ``active`` | ``blocked`` | ``done`` | ``paused`` | ``unknown``
    #: ``unknown`` is a real state: the project row was not readable, and it is
    #: deliberately not coerced to ``todo``.
    status: str = "unknown"
    status_basis: MeasurementBasis = MeasurementBasis.UNMEASURED
    last_synced_at: float | None = None


class WorkItemLink(BaseModel):
    """A real ``alpha.kanban`` card, plus the company's own acceptance terms."""

    task_id: str
    board_id: str
    title: str = ""
    assignee_agent_handle: str | None = None
    #: The Kanban column verbatim. Never remapped to a company vocabulary.
    column: str = "todo"
    priority: str = "medium"
    objective_id: str | None = None
    #: Definition of Done. The loop refuses to mark this card done without it
    #: being non-empty and every criterion evidenced.
    definition_of_done: list[str] = Field(default_factory=list)
    #: What this work was measured to have cost. ``None`` until a real reading.
    cost_usd: float | None = None
    cost_basis: MeasurementBasis = MeasurementBasis.UNMEASURED
    #: Real run ids that produced evidence for this card.
    evidence_run_ids: list[str] = Field(default_factory=list)


class GroupLink(BaseModel):
    """A real ``alpha.groups`` room used by this company."""

    room_id: str
    name: str = ""
    purpose: str = ""
    unit_id: str | None = None
    member_agent_handles: list[str] = Field(default_factory=list)


class ScheduleLink(BaseModel):
    """A real schedule. ``alpha.scheduler`` decides *when*; the loop decides *what*.

    Recording both owners explicitly is what stops a second cron owner from
    appearing: a schedule is never fired by the company loop itself.
    """

    schedule_id: str
    #: ``alpha.scheduler`` task id, or an ``alpha.automations`` automation id.
    backend: Literal["scheduler", "automation"] = "scheduler"
    backend_task_id: str = ""
    title: str = ""
    #: RRULE-ish description carried verbatim from the backend.
    recurrence: str = ""
    owner_agent_handle: str | None = None
    enabled: bool = True
    #: When the company believes the next occurrence is due. ``None`` when the
    #: backend has not computed one.
    next_run_at: float | None = None


# --------------------------------------------------------------------------- #
# Loop state
# --------------------------------------------------------------------------- #


class LoopPolicy(BaseModel):
    """The company's operating rules for its perpetual loop.

    Every field here is a *bound*, not a suggestion. ``interval_seconds`` is the
    supervisor's cadence; ``max_actions_per_tick`` caps one tick's fan-out; the
    budgets are hard ceilings evaluated before any model call.
    """

    enabled: bool = False
    interval_seconds: int = 60
    #: Hard cap on actions in a single tick. Exceeding it defers, never truncates.
    max_actions_per_tick: int = 8
    #: Hard USD ceiling per day across this company. ``None`` = refuse to spend.
    daily_cost_ceiling_usd: float | None = None
    #: Consecutive ticks with zero verified outcomes before the loop parks.
    no_progress_tolerance: int = 3
    #: Fraction of recent failed actions above which autonomy is frozen.
    failure_storm_threshold: float = 0.4
    failure_storm_window: int = 20
    #: The cadence at which each ritual is eligible to run.
    standup_cadence: Literal["hourly", "daily", "weekly", "manual"] = "daily"
    review_cadence: Literal["weekly", "biweekly", "monthly", "manual"] = "weekly"
    okr_cadence: Literal["monthly", "quarterly", "manual"] = "monthly"
    board_cadence: Literal["quarterly", "manual"] = "quarterly"


class CostLedger(BaseModel):
    """Real spend, attributed to the company.

    ``spent_today_usd`` is only ever advanced by a measured reading from a run's
    token usage. It is not estimated from task counts, because an estimate fed
    into a ceiling is a ceiling that trips on fiction.
    """

    spent_today_usd: float | None = None
    spent_total_usd: float | None = None
    budget_daily_usd: float | None = None
    budget_total_usd: float | None = None
    #: Real run ids that produced the measurement.
    measured_run_ids: list[str] = Field(default_factory=list)
    last_measured_at: float | None = None
    measured_run_count: int = 0


class TickRecord(BaseModel):
    """One loop tick, appended to the ledger and never rewritten.

    This is the audit trail that answers "what did the company actually do while
    nobody was watching", and it distinguishes a tick that did nothing from a
    tick that was never run.
    """

    tick_id: str = Field(default_factory=lambda: new_id("tick"))
    company_id: str
    started_at: float = Field(default_factory=now_ts)
    finished_at: float | None = None
    duration_ms: float | None = None
    outcome: TickOutcome = TickOutcome.IDLE
    #: The single-sentence reason, including for a healthy no-op. Empty only on
    #: an aborted tick, where the reason is the abort itself.
    reason: str = ""
    #: What was actually dispatched, with the real ids it will produce.
    dispatched: list[dict[str, Any]] = Field(default_factory=list)
    #: What was proposed but not applied, because policy or the tier forbade it.
    deferred: list[dict[str, Any]] = Field(default_factory=list)
    #: Actions that failed. A failure is recorded as a failure.
    failures: list[dict[str, Any]] = Field(default_factory=list)
    #: Real USD this tick cost. ``None`` means nothing was measured, which for a
    #: tick that dispatched no work is the expected answer.
    cost_usd: float | None = None
    cost_basis: MeasurementBasis = MeasurementBasis.UNMEASURED
    #: Counters this tick observed, for trend lines.
    observed: dict[str, int] = Field(default_factory=dict)

    def summary(self) -> str:
        return f"{self.outcome.value}: {self.reason}"


class CompanyMetrics(BaseModel):
    """A measured snapshot for the dashboard.

    Every field is ``None`` when it was not measured. ``health_percent`` is the
    one number most likely to be fabricated by an earlier implementation, so it
    carries a basis and is only ever a derivation over measured inputs.
    """

    headcount: int = 0
    active_headcount: int = 0
    project_count: int = 0
    active_project_count: int = 0
    open_work_items: int = 0
    blocked_work_items: int = 0
    in_review_work_items: int = 0
    completed_work_items: int = 0
    room_count: int = 0
    schedule_count: int = 0
    objective_count: int = 0
    pending_approvals: int = 0
    #: Derived only from measured inputs. ``None`` until then.
    health_percent: float | None = None
    health_basis: MeasurementBasis = MeasurementBasis.UNMEASURED
    measured_at: float | None = None


# --------------------------------------------------------------------------- #
# The aggregate root
# --------------------------------------------------------------------------- #


class Company(BaseModel):
    """A durable, multi-tenant autonomous organization.

    One operator may own many companies of any kind — a startup, a research lab,
    a security SOC, a game studio, an NGO. ``owner_id`` is server-assigned from
    request context and is the only tenant boundary.
    """

    company_id: str = Field(default_factory=lambda: new_id("co"))
    owner_id: str = "default"
    name: str
    #: Free-form classification. The bundled archetypes are a starting set, not
    #: a closed enum, so an operator can define their own kind of company.
    archetype: str = "company"
    description: str = ""
    state: CompanyState = CompanyState.DRAFT

    charter: Charter
    roles: list[Role] = Field(default_factory=list)
    units: list[OrgUnit] = Field(default_factory=list)
    #: The join records. See :class:`Employment` for why this is not the bot.
    employments: list[Employment] = Field(default_factory=list)
    accountabilities: list[Accountability] = Field(default_factory=list)
    objectives: list[Objective] = Field(default_factory=list)

    # Portfolio links into real subsystems.
    projects: list[ProjectLink] = Field(default_factory=list)
    #: Real ``alpha.kanban`` cards on this company's own board.
    work_items: list[WorkItemLink] = Field(default_factory=list)
    groups: list[GroupLink] = Field(default_factory=list)
    schedules: list[ScheduleLink] = Field(default_factory=list)

    loop_policy: LoopPolicy = Field(default_factory=LoopPolicy)
    cost: CostLedger = Field(default_factory=CostLedger)
    approvals: list[ApprovalRequest] = Field(default_factory=list)

    #: Consecutive ticks with zero verified outcomes. Reset by any real outcome.
    consecutive_no_progress_ticks: int = 0
    #: Rolling window of recent action outcomes, newest last: ``"ok"`` /
    #: ``"failed"``. Bounded by ``LoopPolicy.failure_storm_window``.
    recent_outcomes: list[str] = Field(default_factory=list)
    #: When each ritual last actually ran, as a Unix timestamp.
    last_ritual_at: dict[str, float] = Field(default_factory=dict)

    created_at: float = Field(default_factory=now_ts)
    updated_at: float = Field(default_factory=now_ts)
    archived_at: float | None = None
    #: Schema version of the record itself, so a per-company migration is
    #: possible without bumping the whole store.
    revision: int = COMPANY_STORE_SCHEMA_VERSION

    # -- derived views ----------------------------------------------------- #

    def unit(self, unit_id: str | None) -> OrgUnit | None:
        if not unit_id:
            return None
        return next((u for u in self.units if u.unit_id == unit_id), None)

    def role(self, role_id: str | None) -> Role | None:
        if not role_id:
            return None
        return next((r for r in self.roles if r.role_id == role_id), None)

    def employment(self, agent_handle: str) -> Employment | None:
        key = (agent_handle or "").strip().lower()
        return next((e for e in self.employments if e.agent_handle.strip().lower() == key), None)

    def active_employments(self) -> list[Employment]:
        return [e for e in self.employments if e.is_active()]

    def active_agent_handles(self) -> list[str]:
        return [e.agent_handle for e in self.active_employments()]

    def objective(self, objective_id: str | None) -> Objective | None:
        if not objective_id:
            return None
        return next((o for o in self.objectives if o.objective_id == objective_id), None)

    def pending_approvals(self) -> list[ApprovalRequest]:
        return [a for a in self.approvals if a.status is ApprovalStatus.PENDING]

    def touch(self) -> None:
        self.updated_at = now_ts()

    def check_invariants(self) -> list[str]:
        """Return a list of structural problems. Empty means the record is sound.

        This is a self-check, not a validator: it reports rather than raises so a
        partially-repaired company can still be read and fixed through the API.
        """
        problems: list[str] = []
        seen_units: set[str] = set()
        for unit in self.units:
            if unit.unit_id in seen_units:
                problems.append(f"Duplicate unit id: {unit.unit_id}")
            seen_units.add(unit.unit_id)
            if unit.parent_unit_id and unit.parent_unit_id not in seen_units:
                problems.append(f"Unit '{unit.unit_id}' has unknown parent '{unit.parent_unit_id}'")
        seen_roles = {r.role_id for r in self.roles}
        for emp in self.employments:
            if emp.role_id and emp.role_id not in seen_roles:
                problems.append(f"Employment '{emp.employment_id}' references unknown role '{emp.role_id}'")
            if emp.unit_id and emp.unit_id not in seen_units:
                problems.append(f"Employment '{emp.employment_id}' references unknown unit '{emp.unit_id}'")
            if emp.reports_to_handle and self.employment(emp.reports_to_handle) is None:
                problems.append(f"Employment '{emp.employment_id}' reports to unknown agent '{emp.reports_to_handle}'")
        seen_obj = {o.objective_id for o in self.objectives}
        for proj in self.projects:
            for oid in proj.objective_ids:
                if oid not in seen_obj:
                    problems.append(f"Project '{proj.project_id}' references unknown objective '{oid}'")
        for acct in self.accountabilities:
            if acct.unit_id and acct.unit_id not in seen_units:
                problems.append(f"Accountability '{acct.accountability_id}' references unknown unit '{acct.unit_id}'")
        return problems
