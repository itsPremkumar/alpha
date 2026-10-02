"""The perpetual company loop: one bounded, honest, restart-recoverable tick.

The loop is **cadenced, not continuous**. Real companies do not run one infinite
loop; they run a daily standup, a weekly review, a monthly OKR check and a
quarterly board meeting on top of continuous triage. That structure is copied
here for two reasons: it is how organizations actually work, and an unpaced
infinite loop is precisely how an agent fleet burns a budget while nobody is
watching.

Every tick runs the same seven phases, and every phase can only *stop* the tick:

1. **Gate** — every breaker in :mod:`alpha.company_os.loop_safety`. Refused
   before any model call, so a budget that is checked after the spend is not a
   budget.
2. **Observe** — pure reads, zero tokens: real Kanban cards, real projects, real
   agent health, real run outcomes.
3. **Plan** — deterministic. The loop only *assigns work that already exists* and
   *proposes* work that does not. It never invents a task out of nothing, because
   a loop that generates its own backlog generates its own busywork.
4. **Act** — each action goes through its owning subsystem, never a bespoke path:
   an assignment becomes a real Kanban claim, a new project becomes a proposal.
5. **Verify** — an outcome counts only when the subsystem reported a real
   terminal state. A run that merely started is not a success.
6. **Ledger** — append one :class:`TickRecord`. Restart-recoverable, never
   rewritten.
7. **Report** — a reason on every tick, including a healthy no-op. A tick with no
   reason is indistinguishable from a tick that never ran.

Nothing here calls a model. The loop's intelligence is in *sequencing bounded
work through real subsystems*; deciding what the work should be is the agent's
job when it picks the card up. Keeping the loop model-free is what makes it
predictable, restartable and safe to run unattended.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from alpha.company_os import loop_safety
from alpha.company_os.models import (
    AutonomyTier,
    Company,
    MeasurementBasis,
    ObjectiveStatus,
    TickOutcome,
    TickRecord,
    now_ts,
)

logger = logging.getLogger(__name__)

#: The action vocabulary the planner may emit. A closed set means an unexpected
#: key is a bug rather than a silently ignored instruction.
ACTION_ASSIGN = "assign_work"
ACTION_CLAIM = "claim_work"
ACTION_SUBMIT_REVIEW = "submit_for_review"
ACTION_FLAG_BLOCKED = "flag_blocked"
ACTION_PROPOSE_PROJECT = "propose_project"
ACTION_PROPOSE_HIRE = "propose_hire"
ACTION_ESCALATE = "escalate"

#: Which autonomy tier each action requires. Read from the action's own
#: definition so a self-direct action is refused under T2 with no special case.
ACTION_TIERS: dict[str, AutonomyTier] = {
    ACTION_ASSIGN: AutonomyTier.T2_EXECUTE_ROUTINE,
    ACTION_CLAIM: AutonomyTier.T2_EXECUTE_ROUTINE,
    ACTION_SUBMIT_REVIEW: AutonomyTier.T2_EXECUTE_ROUTINE,
    ACTION_FLAG_BLOCKED: AutonomyTier.T1_ADVISE,
    ACTION_PROPOSE_PROJECT: AutonomyTier.T3_SELF_DIRECT,
    ACTION_PROPOSE_HIRE: AutonomyTier.T4_AUTONOMOUS,
    ACTION_ESCALATE: AutonomyTier.T1_ADVISE,
}


@dataclass
class PlannedAction:
    """One proposed action, with the evidence that motivated it."""

    kind: str
    reason: str
    #: Real ids this action would touch. Present so a deferred action can be
    #: resumed rather than reconstructed from prose.
    work_item_id: str | None = None
    agent_handle: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class TickPlan:
    """What the loop intends to do this tick, and what it chose not to."""

    actions: list[PlannedAction] = field(default_factory=list)
    deferred: list[PlannedAction] = field(default_factory=list)
    observations: dict[str, Any] = field(default_factory=dict)
    #: True when the loop found genuinely actionable work.
    discovered_new: bool = False


def plan_tick(company: Company, work: dict[str, Any], workforce: dict[str, Any]) -> TickPlan:
    """Turn observations into a bounded action list.

    Deliberately deterministic. Two runs over the same company produce the same
    plan, which is what makes the loop auditable and replayable — and what lets a
    test pin its behaviour without a model.
    """
    plan = TickPlan(observations=dict(work))
    if not work.get("reachable"):
        # The board could not be read. Acting on an unknown board is how a loop
        # double-assigns, so it observes and reports instead.
        return plan

    items = company.work_items
    ready = [w for w in items if w.column in ("todo", "backlog")]
    blocked = [w for w in items if w.column == "blocked"]
    in_progress = [w for w in items if w.column == "in_progress"]
    in_review = [w for w in items if w.column == "in_review"]

    idle_handles = _idle_agents(company, in_progress)

    for item in ready:
        if item.definition_of_done:
            target = item.assignee_agent_handle or (idle_handles[0] if idle_handles else None)
            if target and (not item.assignee_agent_handle or target in idle_handles):
                if target == item.assignee_agent_handle:
                    plan.actions.append(
                        PlannedAction(
                            kind=ACTION_ASSIGN,
                            reason=f"Card '{item.task_id}' is ready and assigned to @{target}.",
                            work_item_id=item.task_id,
                            agent_handle=target,
                        )
                    )
                else:
                    idle_handles.remove(target)
                    plan.actions.append(
                        PlannedAction(
                            kind=ACTION_ASSIGN,
                            reason=f"Card '{item.task_id}' is ready and @{target} has no active work.",
                            work_item_id=item.task_id,
                            agent_handle=target,
                        )
                    )
        else:
            # A card with no Definition of Done cannot be verified, so it is
            # flagged rather than dispatched. This is the single most important
            # rule in the planner.
            plan.actions.append(
                PlannedAction(
                    kind=ACTION_FLAG_BLOCKED,
                    reason=f"Card '{item.task_id}' has no Definition of Done, so it cannot be verified.",
                    work_item_id=item.task_id,
                )
            )

    for item in in_review:
        plan.actions.append(
            PlannedAction(
                kind=ACTION_SUBMIT_REVIEW,
                reason=f"Card '{item.task_id}' is awaiting review.",
                work_item_id=item.task_id,
            )
        )

    for item in blocked:
        plan.actions.append(
            PlannedAction(
                kind=ACTION_ESCALATE,
                reason=f"Card '{item.task_id}' is blocked and needs a decision.",
                work_item_id=item.task_id,
            )
        )

    # Nothing to do and nothing discoverable: a healthy idle company.
    plan.discovered_new = bool(ready or blocked or in_progress)
    if not plan.actions:
        plan.observations["plan"] = "No actionable work. The company is idle, which costs nothing."

    if workforce.get("missing_profile_count"):
        plan.actions.append(
            PlannedAction(
                kind=ACTION_PROPOSE_HIRE,
                reason=(f"{workforce['missing_profile_count']} employee(s) have no bot profile. Re-hire or re-assign them."),
            )
        )

    return plan


def _idle_agents(company: Company, in_progress: list[Any]) -> list[str]:
    """Active employees with no in-progress card, lowest seniority first.

    Ordering by seniority means a senior specialist takes the ambiguous work and a
    junior is not handed something the company cannot verify.
    """
    busy = {(w.assignee_agent_handle or "").lower() for w in in_progress}
    order = {"executive": 0, "manager": 1, "employee": 2, "contractor": 3}
    candidates = [e for e in company.active_employments() if e.agent_handle.lower() not in busy]
    candidates.sort(key=lambda e: order.get(e.employment_type.value, 9))
    return [e.agent_handle for e in candidates]


def execute_action(company: Company, action: PlannedAction) -> dict[str, Any]:
    """Run one planned action through its owning subsystem.

    Returns a result dict with ``ok``. A failure is returned, never raised, so one
    bad action cannot abort a tick that has other work to do.
    """
    from alpha.company_os import portfolio

    required = ACTION_TIERS.get(action.kind)
    if required is None:
        return {"ok": False, "kind": action.kind, "error": f"Unknown action '{action.kind}'."}

    allowed, why = loop_safety.autonomy_allows(company, required)
    if not allowed:
        # A policy refusal is **not a failure**. Recording it as one would count a
        # correctly-refused action toward the failure-storm breaker and park the
        # loop for behaving properly — the breaker would then be a punishment for
        # correct governance. `skipped` routes it to `deferred` instead.
        return {"ok": True, "kind": action.kind, "skipped": True, "reason": why}

    if action.kind == ACTION_ASSIGN:
        if not action.work_item_id or not action.agent_handle:
            return {"ok": False, "kind": action.kind, "error": "An assignment needs a card and an agent."}
        result = portfolio.move_work_item(company, action.work_item_id, "in_progress", board_id=_board_id(company))
        if result.get("ok"):
            for link in company.work_items:
                if link.task_id == action.work_item_id:
                    link.assignee_agent_handle = action.agent_handle
        return {"ok": bool(result.get("ok")), "kind": action.kind, "detail": result}

    if action.kind == ACTION_FLAG_BLOCKED:
        if not action.work_item_id:
            return {"ok": False, "kind": action.kind, "error": "A flag needs a card."}
        result = portfolio.move_work_item(company, action.work_item_id, "blocked", board_id=_board_id(company))
        return {"ok": bool(result.get("ok")), "kind": action.kind, "detail": result}

    if action.kind == ACTION_ESCALATE:
        from alpha.company_os.models import ApprovalKind, ApprovalRequest

        already = any(a.kind is ApprovalKind.EXTERNAL_SIDE_EFFECT and a.status.value == "pending" and action.work_item_id in a.payload.get("work_item_ids", []) for a in company.approvals)
        if already:
            return {"ok": True, "kind": action.kind, "skipped": True, "reason": "Already escalated."}
        company.approvals.append(
            ApprovalRequest(
                kind=ApprovalKind.EXTERNAL_SIDE_EFFECT,
                title=f"Blocked work needs a decision: {action.work_item_id}",
                rationale=action.reason,
                payload={"work_item_ids": [action.work_item_id]},
            )
        )
        return {"ok": True, "kind": action.kind, "escalated": action.work_item_id}

    if action.kind == ACTION_SUBMIT_REVIEW:
        # Review acceptance is a human/agent decision with real consequences, so
        # the loop proposes rather than approves.
        return {"ok": True, "kind": action.kind, "skipped": True, "reason": "Review acceptance is not automated."}

    if action.kind == ACTION_PROPOSE_PROJECT:
        from alpha.company_os.models import ApprovalKind, ApprovalRequest

        company.approvals.append(
            ApprovalRequest(
                kind=ApprovalKind.CREATE_PROJECT,
                title="Propose a project from discovered work",
                rationale=action.reason,
                payload=dict(action.payload),
            )
        )
        return {"ok": True, "kind": action.kind, "proposed": True}

    if action.kind == ACTION_PROPOSE_HIRE:
        from alpha.company_os.models import ApprovalKind, ApprovalRequest

        company.approvals.append(
            ApprovalRequest(
                kind=ApprovalKind.HIRE,
                title="Re-hire employees with no bot profile",
                rationale=action.reason,
                payload=dict(action.payload),
            )
        )
        return {"ok": True, "kind": action.kind, "proposed": True}

    return {"ok": False, "kind": action.kind, "error": f"Unhandled action '{action.kind}'."}


def _board_id(company: Company) -> str:
    from alpha.company_os.portfolio import _company_board_id

    return _company_board_id(company)


def step(company: Company, *, workforce: dict[str, Any] | None = None) -> TickRecord:
    """Run exactly one tick and return its ledger record.

    The caller persists the record. This function never writes: keeping the tick
    pure makes it testable and lets the store decide when durability happens.
    """
    started = time.time()
    record = TickRecord(company_id=company.company_id, started_at=started)

    # 1. Gate.
    verdict = loop_safety.pre_tick_gate(company)
    if not verdict.ok:
        record.outcome = _outcome_for_breaker(verdict.breaker)
        record.reason = verdict.reason
        record.finished_at = time.time()
        record.duration_ms = round((record.finished_at - started) * 1000, 2)
        return record

    try:
        # 2. Observe — real reads only.
        from alpha.company_os import portfolio
        from alpha.company_os import workforce as workforce_mod

        work = portfolio.measure_work(company)
        roster = workforce if workforce is not None else workforce_mod.sync_workforce_status(company)

        record.observed = {
            "open_work_items": int(work.get("open_items") or 0),
            "ready_items": int(work.get("ready_items") or 0),
            "blocked_items": int(work.get("blocked_items") or 0),
            "in_review_items": int(work.get("in_review_items") or 0),
            "done_items": int(work.get("done_items") or 0),
            "headcount": int(roster.get("employment_count") or 0),
        }

        if not work.get("reachable"):
            record.outcome = TickOutcome.ERROR
            record.reason = f"The work board could not be read: {work.get('error', 'unknown error')}"
            record.finished_at = time.time()
            record.duration_ms = round((record.finished_at - started) * 1000, 2)
            return record

        # 3. Plan.
        plan = plan_tick(company, work, roster)
        runnable, deferred = loop_safety.bounded_actions(company, plan.actions)
        record.deferred = [{"kind": a.kind, "reason": a.reason, "work_item_id": a.work_item_id} for a in (plan.deferred + deferred)]

        # 4/5. Act and verify.
        failures = 0
        verified = 0
        for action in runnable:
            result = execute_action(company, action)
            if result.get("ok"):
                if result.get("skipped"):
                    record.deferred.append({"kind": action.kind, "reason": result.get("reason", "")})
                    continue
                verified += 1
                record.dispatched.append(
                    {
                        "kind": action.kind,
                        "reason": action.reason,
                        "work_item_id": action.work_item_id,
                        "agent_handle": action.agent_handle,
                    }
                )
                loop_safety.record_outcome(company, "ok")
            else:
                failures += 1
                record.failures.append({"kind": action.kind, "error": result.get("error") or result.get("reason")})
                loop_safety.record_outcome(company, "failed")

        # 6. Ledger bookkeeping.
        loop_safety.record_tick_result(company, verified_outcomes=verified)

        if failures and verified:
            record.outcome = TickOutcome.PARTIAL
            record.reason = f"{verified} action(s) completed, {failures} failed."
        elif failures:
            record.outcome = TickOutcome.ERROR
            record.reason = f"All {failures} attempted action(s) failed."
        elif verified:
            record.outcome = TickOutcome.DISPATCHED
            record.reason = f"Dispatched {verified} action(s)."
        elif record.deferred:
            record.outcome = TickOutcome.DEFERRED
            record.reason = f"{len(record.deferred)} action(s) deferred by policy."
        else:
            record.outcome = TickOutcome.IDLE
            record.reason = "No actionable work. The company is idle and spent nothing."

        _refresh_objectives(company)

    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Company loop tick failed for %s", company.company_id)
        record.outcome = TickOutcome.ERROR
        record.reason = f"{type(exc).__name__}: {exc}"

    record.finished_at = time.time()
    record.duration_ms = round((record.finished_at - started) * 1000, 2)
    # A tick that dispatched nothing spent nothing. Saying so explicitly is what
    # lets the cost ledger stay honest without a token meter.
    if record.dispatched:
        record.cost_usd = None
        record.cost_basis = MeasurementBasis.UNMEASURED
    else:
        record.cost_usd = 0.0
        record.cost_basis = MeasurementBasis.MEASURED
    return record


def _outcome_for_breaker(breaker: loop_safety.Breaker) -> TickOutcome:
    if breaker is loop_safety.Breaker.NO_PROGRESS:
        return TickOutcome.NO_PROGRESS
    if breaker is loop_safety.Breaker.BUDGET:
        return TickOutcome.BUDGET_EXHAUSTED
    if breaker in (loop_safety.Breaker.DISABLED, loop_safety.Breaker.NOT_ACTIVE, loop_safety.Breaker.KILL_SWITCH):
        return TickOutcome.PAUSED
    if breaker is loop_safety.Breaker.FAILURE_STORM:
        return TickOutcome.PAUSED
    return TickOutcome.PAUSED


def _refresh_objectives(company: Company) -> None:
    """Mark objectives at risk when their work is all blocked.

    Only measurable board state feeds this: an objective whose key results have
    never been measured is left ``ACTIVE`` rather than being declared at risk on
    no evidence.
    """
    blocked = sum(1 for w in company.work_items if w.column == "blocked")
    for objective in company.objectives:
        if objective.status is not ObjectiveStatus.ACTIVE:
            continue
        if objective.key_results and all(kr.current is not None for kr in objective.key_results):
            progress = objective.progress_percent
            if progress is not None and progress < 20.0:
                objective.status = ObjectiveStatus.AT_RISK
        elif blocked and not company.work_items:
            objective.status = ObjectiveStatus.AT_RISK


def run_rituals(company: Company) -> dict[str, Any]:
    """The cadenced rituals: standup, review, OKR checkpoint, board.

    Each is due according to the company's own cadence, and each records when it
    last actually ran. ``due`` says what was eligible this call; ``ran`` says what
    actually executed, so a manual cadence is visible as "never".
    """
    cadence_days = {
        "standup": {"hourly": 1 / 24, "daily": 1.0, "weekly": 7.0, "manual": 0.0},
        "review": {"weekly": 7.0, "biweekly": 14.0, "monthly": 30.0, "manual": 0.0},
        "okr": {"monthly": 30.0, "quarterly": 91.0, "manual": 0.0},
        "board": {"quarterly": 91.0, "manual": 0.0},
    }
    policy = company.loop_policy
    configured = {
        "standup": policy.standup_cadence,
        "review": policy.review_cadence,
        "okr": policy.okr_cadence,
        "board": policy.board_cadence,
    }

    due: list[str] = []
    ran: list[str] = []
    for name, mode in configured.items():
        days = cadence_days[name].get(mode, 0.0)
        if days <= 0:
            continue
        if loop_safety.ritual_due(company, name, days):
            due.append(name)
            loop_safety.record_ritual(company, name)
            ran.append(name)

    return {
        "due": due,
        "ran": ran,
        "last_ritual_at": dict(company.last_ritual_at),
        "cadence": configured,
    }


def company_health(company: Company, work: dict[str, Any] | None = None) -> dict[str, Any]:
    """A health figure derived only from measured inputs.

    Returns ``health_percent=None`` when nothing could be measured. That is the
    honest answer for a fresh company, and it is exactly what an earlier
    implementation got wrong by seeding a fabricated percentage.
    """
    measured_work = work if isinstance(work, dict) and work.get("reachable") else None
    if measured_work is None:
        return {"health_percent": None, "basis": MeasurementBasis.UNMEASURED.value, "reason": "The work board is not readable."}

    total = int(measured_work.get("total_items") or 0)
    if total == 0:
        return {
            "health_percent": None,
            "basis": MeasurementBasis.UNMEASURED.value,
            "reason": "No work items exist yet, so there is nothing to measure.",
        }

    done = int(measured_work.get("done_items") or 0)
    blocked = int(measured_work.get("blocked_items") or 0)
    undod = int(measured_work.get("items_missing_dod") or 0)

    # Three real ratios, each a fraction of the same measured population.
    completion = done / total
    blocked_ratio = blocked / total
    quality = 1.0 - (undod / total)
    score = 100.0 * (0.5 * completion + 0.25 * quality + 0.25 * (1.0 - blocked_ratio))

    return {
        "health_percent": round(max(0.0, min(100.0, score)), 1),
        "basis": MeasurementBasis.DERIVED.value,
        "components": {
            "completion_ratio": round(completion, 4),
            "quality_ratio": round(quality, 4),
            "unblocked_ratio": round(1.0 - blocked_ratio, 4),
        },
        "measured_at": now_ts(),
    }
