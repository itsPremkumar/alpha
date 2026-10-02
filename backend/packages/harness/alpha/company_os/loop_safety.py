"""Loop safety: the breakers that keep a perpetual loop from becoming a money cannon.

An autonomous loop that never stops is only safe if it can say "no" for the right
reasons. This module is the set of reasons. Every check is a hard gate evaluated
*before* any model call, because a budget that is enforced after the spend is not
a budget.

The five breakers, and the real-world failure each one prevents:

1. :func:`check_budget` — an unbounded loop bills you while you sleep. The
   "$30K agent loop" failure mode: 15M tokens in six hours.
2. :func:`check_no_progress` — a loop that retries the same failing thing forever.
   Detected by consecutive ticks with zero verified outcomes.
3. :func:`check_tick_budget` — one tick fan-outs to a hundred agents. Bounded
   fan-out; the remainder is deferred to the next tick, never dropped.
4. :func:`check_failure_storm` — a systemic regression looks like "lots of
   activity". Above a failure ratio, autonomy freezes.
5. :func:`check_stagnation` — all work finished and nothing new was found.

None of these *fix* anything. They stop the loop and say why, which is the only
honest response available without a human.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from alpha.company_os.models import (
    AutonomyTier,
    Company,
    CompanyState,
    MeasurementBasis,
    now_ts,
)

logger = logging.getLogger(__name__)


class Breaker(StrEnum):
    """Which gate refused the tick, or ``NONE`` when nothing did."""

    NONE = "none"
    DISABLED = "disabled"
    NOT_ACTIVE = "not_active"
    BUDGET = "budget"
    NO_PROGRESS = "no_progress"
    TICK_BUDGET = "tick_budget"
    FAILURE_STORM = "failure_storm"
    KILL_SWITCH = "kill_switch"


@dataclass
class BreakerVerdict:
    """A gate's decision. ``ok=True`` means the tick may proceed."""

    breaker: Breaker = Breaker.NONE
    ok: bool = True
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"breaker": self.breaker.value, "ok": self.ok, "reason": self.reason}


ALLOW = BreakerVerdict()


def _block(breaker: Breaker, reason: str) -> BreakerVerdict:
    logger.info("Company loop blocked by %s: %s", breaker.value, reason)
    return BreakerVerdict(breaker=breaker, ok=False, reason=reason)


def global_kill_switch_engaged() -> bool:
    """Whether the process-wide kill switch is engaged.

    Read through the bot kill-switch module rather than re-implemented, so the
    company loop and the bot fleet can never disagree about whether the system is
    paused. An unreadable switch is treated as *engaged*, which fails closed.
    """
    try:
        from alpha.bots.kill_switch import is_kill_switch_active

        active, _reason = is_kill_switch_active()
        return bool(active)
    except Exception as exc:
        logger.warning("Kill switch state unreadable; treating as engaged: %s", exc)
        return True


def check_enabled(company: Company) -> BreakerVerdict:
    """The loop must be explicitly enabled by its operator."""
    if not company.loop_policy.enabled:
        return _block(Breaker.DISABLED, "The perpetual loop is not enabled for this company.")
    return ALLOW


def check_state(company: Company) -> BreakerVerdict:
    """A paused, stopped or archived company does not run."""
    if company.state in (CompanyState.PAUSED, CompanyState.STOPPED, CompanyState.ARCHIVED):
        return _block(Breaker.NOT_ACTIVE, f"The company is {company.state.value}.")
    if company.state is CompanyState.DRAFT:
        return _block(Breaker.NOT_ACTIVE, "The company is still a draft; finish setup or set it active.")
    return ALLOW


def check_kill_switch() -> BreakerVerdict:
    if global_kill_switch_engaged():
        return _block(Breaker.KILL_SWITCH, "The global kill switch is engaged.")
    return ALLOW


def check_budget(company: Company) -> BreakerVerdict:
    """Refuse when today's measured spend has reached the daily ceiling.

    ``None`` for either the ceiling or the measured spend means the company has
    not declared a budget or nothing has been measured. An undeclared budget
    **refuses** rather than defaulting to an allowance, because an invented
    ceiling is a ceiling the operator never agreed to.
    """
    policy = company.loop_policy
    ceiling = policy.daily_cost_ceiling_usd
    if ceiling is None:
        ceiling = company.cost.budget_daily_usd
    if ceiling is None:
        return _block(
            Breaker.BUDGET,
            "No daily spend ceiling is set, so the loop refuses to spend. Declare one to allow autonomous operation.",
        )

    spent = company.cost.spent_today_usd
    if spent is None:
        # Nothing measured yet is not permission to spend without a number; but
        # with a ceiling declared, an unmeasured day is simply a day so far. The
        # loop proceeds and the first measured reading arms the breaker.
        return ALLOW

    if spent >= ceiling:
        return _block(
            Breaker.BUDGET,
            f"Today's measured spend ${spent:.4f} has reached the ${ceiling:.4f} daily ceiling.",
        )
    return ALLOW


def check_no_progress(company: Company) -> BreakerVerdict:
    """Park after consecutive ticks that produced no verified outcome."""
    tolerance = max(1, company.loop_policy.no_progress_tolerance)
    if company.consecutive_no_progress_ticks >= tolerance:
        return _block(
            Breaker.NO_PROGRESS,
            f"{company.consecutive_no_progress_ticks} consecutive ticks produced no verified outcome (tolerance {tolerance}). The loop is parked pending a human.",
        )
    return ALLOW


def check_failure_storm(company: Company) -> BreakerVerdict:
    """Freeze autonomy when too much recent work has failed."""
    window = max(1, company.loop_policy.failure_storm_window)
    threshold = company.loop_policy.failure_storm_threshold
    recent = company.recent_outcomes[-window:]
    if len(recent) < 3:
        return ALLOW
    failures = sum(1 for o in recent if o == "failed")
    ratio = failures / len(recent)
    if ratio >= threshold:
        return _block(
            Breaker.FAILURE_STORM,
            f"{failures}/{len(recent)} recent actions failed ({ratio:.0%} >= {threshold:.0%}). Autonomy is frozen pending a human.",
        )
    return ALLOW


def bounded_actions(company: Company, actions: list[Any]) -> tuple[list[Any], list[Any]]:
    """Split actions into what runs now and what is deferred to the next tick.

    Deferred work is returned, never discarded: a dropped action is a silently
    lost task, which is the worst possible outcome for a work queue.
    """
    limit = max(0, company.loop_policy.max_actions_per_tick)
    return actions[:limit], actions[limit:]


def check_stagnation(company: Company, *, open_work_items: int, discovered_new: bool) -> BreakerVerdict | None:
    """A stagnation signal, or ``None`` when the company is simply idle.

    Idle with no backlog and no new work is a *healthy* state that costs nothing.
    Only "everything finished and nothing was found" is worth reporting, because
    that is the point where a real company would invent a new goal.
    """
    if open_work_items == 0 and not discovered_new:
        return _block(Breaker.NO_PROGRESS, "No open work and nothing new was discovered.")
    return None


def pre_tick_gate(company: Company) -> BreakerVerdict:
    """Every breaker, in the cheapest order.

    Order matters: a disabled loop should not report a budget breach, because the
    operator's first question would be "why is my paused company complaining
    about money".
    """
    for check in (
        check_enabled,
        check_state,
        lambda c: check_kill_switch(),
        check_budget,
        check_no_progress,
        check_failure_storm,
    ):
        verdict = check(company)
        if not verdict.ok:
            return verdict
    return ALLOW


def record_outcome(company: Company, outcome: str) -> None:
    """Append one action outcome to the bounded rolling window."""
    window = max(1, company.loop_policy.failure_storm_window)
    company.recent_outcomes.append(outcome)
    if len(company.recent_outcomes) > window:
        del company.recent_outcomes[:-window]


def record_tick_result(company: Company, *, verified_outcomes: int) -> None:
    """Advance or reset the no-progress counter.

    Any verified outcome resets it. A tick that merely *ran* does not — the
    counter exists to catch loops that are busy and achieving nothing.
    """
    if verified_outcomes > 0:
        company.consecutive_no_progress_ticks = 0
    else:
        company.consecutive_no_progress_ticks += 1


def autonomy_allows(company: Company, required_tier: AutonomyTier) -> tuple[bool, str]:
    """Whether the company's configured tier permits an action.

    ``required_tier`` comes from the action's own definition, so a self-direct
    action is refused under T2 without any per-action special case.
    """
    current = company.charter.autonomy_tier
    if not isinstance(current, AutonomyTier):
        current = AutonomyTier(current)
    if current.rank < required_tier.rank:
        return False, f"This action needs autonomy {required_tier.value} but the company is set to {current.value}."
    return True, ""


def record_ritual(company: Company, name: str) -> float:
    """Stamp when a ritual last ran and return the timestamp."""
    stamp = now_ts()
    company.last_ritual_at[name] = stamp
    return stamp


def ritual_due(company: Company, name: str, cadence_days: float) -> bool:
    """Whether a ritual is due, using the company's own last-run stamp.

    ``cadence_days <= 0`` disables the ritual entirely, which is how an operator
    turns one off without deleting the policy field.
    """
    if cadence_days <= 0:
        return False
    last = company.last_ritual_at.get(name)
    if last is None:
        return True
    return (now_ts() - last) >= cadence_days * 86400.0


def cost_status(company: Company) -> dict[str, Any]:
    """The company's spend, honestly.

    ``spent_usd`` is ``None`` until a real reading exists and ``basis`` says so.
    ``utilization_percent`` is likewise ``None`` rather than a division by an
    undeclared ceiling.
    """
    spent = company.cost.spent_today_usd
    ceiling = company.loop_policy.daily_cost_ceiling_usd or company.cost.budget_daily_usd
    utilization: float | None = None
    if spent is not None and ceiling:
        utilization = round(spent / ceiling * 100.0, 2)
    return {
        "spent_today_usd": spent,
        "budget_daily_usd": ceiling,
        "budget_total_usd": company.cost.budget_total_usd or company.charter.budget_total_usd,
        "utilization_percent": utilization,
        "basis": MeasurementBasis.MEASURED if spent is not None else MeasurementBasis.UNMEASURED,
        "measured_run_count": company.cost.measured_run_count,
        "last_measured_at": company.cost.last_measured_at,
    }
