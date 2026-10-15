"""The one predicate every stop gate asks: is the fleet stopped?

## Why this exists

Three stop mechanisms existed and none of them was fleet-wide:

* ``alpha.bots.kill_switch`` — an in-memory boolean. Its only consumers were
  self-modification and the war-room watcher. A tool call, a subagent dispatch,
  or an autonomy loop never asked.
* ``alpha.runtime.estop`` — a sentinel *file*, whose only consumer was
  ``alpha.rsi.switchboard``. It gated the recursive-self-improvement cycle and
  nothing else.
* ``alpha.runtime.control`` — a generation-fenced state file. Its guards existed
  and were correct, but the call sites were few: the run worker and the
  supervisor, and the supervisor's own gate consulted the sentinel and control
  state but not the in-memory switch.

So an operator who engaged any of the three saw "Fleet execution paused." while
the autonomy loops kept running, goals kept stepping, subagents kept spawning,
and every tool call still executed. The decision was recorded; the enforcement
was not.

This module is the single answer. Every gate consults it rather than
re-deriving the check, so "is the fleet stopped?" has exactly one definition.
A gate added tomorrow that forgets to ask is then a missing call to a known
function, which is reviewable, rather than a subtle second implementation.

## Why it consults all three

They are not redundant and they are not being unified away. The in-memory switch
is the fastest and vanishes on restart; the sentinel is durable but coarse; the
control state is durable *and* fenced by generation, which is what lets
already-admitted work be stopped mid-flight. Each answers a question the others
cannot, so all three are consulted and none is removed. An operator who engaged
any one of them gets the same fleet-wide effect.

## Failure direction

Every failure mode here fails **closed**. An unreadable control state, an
unparseable one, an unknown mode, or an exception escaping any of the three
reads all report "stopped". A stop mechanism that fails open is not a stop
mechanism, and the cost asymmetry is the whole argument: a spurious stop costs
an operator one ``clear()``, while a missed stop costs whatever the runaway
process did in the meantime.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Reasons, as they appear in ``fleet_stop_reason()``. Kept as module constants
#: so a caller can match on a stable string rather than a prose sentence.
REASON_KILL_SWITCH = "kill_switch"
REASON_ESTOP_SENTINEL = "estop_sentinel"
REASON_FLEET_CONTROL = "fleet_control"
REASON_UNREADABLE = "fleet_stop_unreadable"


def _evaluate() -> tuple[bool, str]:
    """Return ``(stopped, reason)``. Never raises; every failure stops.

    The reason is the *first* mechanism found engaged, in descending order of
    how specific it is. Ordering matters only for the message an operator reads,
    not for the verdict: any one engaged mechanism stops the fleet.
    """
    # 1. The in-memory kill switch. Cheapest first, and the one whose absence
    #    was the original finding: nothing between the model and the tools asked.
    try:
        from alpha.bots.kill_switch import is_kill_switch_active

        active, reason = is_kill_switch_active()
        if active:
            return True, f"{REASON_KILL_SWITCH}: {reason or 'operator kill switch'}"
    except Exception as exc:  # noqa: BLE001 - a stop mechanism must never raise
        logger.error("Kill switch state unreadable; failing closed: %s", exc, exc_info=True)
        return True, f"{REASON_UNREADABLE}: kill_switch ({type(exc).__name__})"

    # 2. The durable sentinel file. Honoured alongside control rather than
    #    subsumed by it: an operator who engaged either gets the same effect.
    try:
        from alpha.runtime.estop import get_estop_manager

        if get_estop_manager().is_engaged():
            return True, REASON_ESTOP_SENTINEL
    except Exception as exc:  # noqa: BLE001
        logger.error("ESTOP sentinel unreadable; failing closed: %s", exc, exc_info=True)
        return True, f"{REASON_UNREADABLE}: estop_sentinel ({type(exc).__name__})"

    # 3. The generation-fenced control state. `read_state` already fails closed
    #    internally, so a corrupt file surfaces as ESTOP with `error` set; the
    #    error is surfaced here rather than swallowed, because an operator
    #    reading "stopped" with no reason cannot tell a real stop from a broken
    #    state file.
    try:
        from alpha.runtime.control import read_state

        state = read_state()
        if state.error:
            return True, f"fail_closed: {state.error}"
        if state.mode.value != "run":
            return True, f"{REASON_FLEET_CONTROL}:{state.mode.value}"
        return False, ""
    except Exception as exc:  # noqa: BLE001
        logger.error("Fleet control state unreadable; failing closed: %s", exc, exc_info=True)
        return True, f"{REASON_UNREADABLE}: fleet_control ({type(exc).__name__})"


def is_fleet_stopped() -> bool:
    """True when any stop mechanism refuses work right now.

    This is the predicate every gate consults. It never raises: a gate that
    raised would either kill a supervisor loop or surface as an internal error
    to the model instead of a refusal, and both are worse than a spurious stop.
    """
    try:
        return _evaluate()[0]
    except Exception as exc:  # noqa: BLE001 - preserve the fail-closed contract
        logger.error("Fleet stop evaluation failed; failing closed: %s", exc, exc_info=True)
        return True


def fleet_stop_reason() -> str:
    """Why the fleet is stopped, or ``""`` when it is not.

    Non-empty exactly when ``is_fleet_stopped()`` is true, so a caller can put
    the real reason in front of an operator or the model instead of a generic
    "the fleet is stopped" that explains nothing.
    """
    try:
        stopped, reason = _evaluate()
    except Exception as exc:  # noqa: BLE001 - preserve the fail-closed contract
        logger.error("Fleet stop reason evaluation failed; failing closed: %s", exc, exc_info=True)
        return f"{REASON_UNREADABLE}: evaluator ({type(exc).__name__})"
    return reason or (f"{REASON_UNREADABLE}: empty stop reason" if stopped else "")


__all__ = [
    "REASON_ESTOP_SENTINEL",
    "REASON_FLEET_CONTROL",
    "REASON_KILL_SWITCH",
    "REASON_UNREADABLE",
    "fleet_stop_reason",
    "is_fleet_stopped",
]
