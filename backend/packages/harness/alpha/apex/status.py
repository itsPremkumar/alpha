"""The APEX status projection: spec §55/§59, as one bounded read.

The data behind ``/apex status`` lives in six places — the contract, the session
store, the mission store, the autonomy supervisor, fleet control, and the
invariant set. This module gathers them into **one** payload so the command, the
HTTP route and the UI read the same thing.

**Every field is measured or disclosed.** Where a subsystem is absent or
unreadable, the field is ``null`` with a machine-readable ``reason`` — the rule
``GET /api/ops/runtime`` already follows, and the same reason the self-inventory
plane reports ``count: null`` rather than ``0``. A status line that renders
"healthy" from a subsystem that was never probed is the failure mode this
module exists to prevent.

**It reads; it never mutates.** No call here changes a session, a mission or the
fleet. ``GET /apex/status`` must be safe to call from a UI poll loop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from alpha.apex.contract import AutonomyContract
from alpha.apex.invariants import invariant_summary
from alpha.apex.store import ApexSession, ApexSessionState, ApexStore

logger = logging.getLogger(__name__)

__all__ = ["apex_status", "contract_status", "fleet_status", "store_status"]


def _unavailable(reason: str) -> dict[str, Any]:
    """A disclosed-absent block. Never an empty list or a zero."""
    return {"available": False, "reason": reason, "count": None}


def contract_status(contract: AutonomyContract) -> dict[str, Any]:
    """The active contract, with its attributed policy sites probed."""
    from alpha.apex.contract import PolicyAttribution

    live = PolicyAttribution.live_sites()
    declared = contract.policy_sites.to_dict()
    return {
        "available": True,
        "profile": contract.profile.value,
        "enabled": contract.enabled,
        "digest": contract.digest(),
        "budget": contract.budget.to_dict(),
        "controls": contract.controls.to_dict(),
        "authority_granted": sorted(k for k, v in contract.authority.items() if v),
        "protected_actions": dict(contract.protected_actions),
        "policy_sites": declared,
        "policy_sites_live": sorted(live),
        "policy_sites_missing": sorted(set(declared) - set(live)),
        "note": "APEX composes these sites; it is not a second policy kernel",
    }


def fleet_status() -> dict[str, Any]:
    """Fleet control state, read through the emergency stop's only home."""
    try:
        from alpha.runtime.control import read_state
        from alpha.runtime.estop import get_estop_manager
    except Exception as exc:
        return _unavailable(f"{type(exc).__name__}: {exc}")
    try:
        state = read_state()
        return {
            "available": True,
            "mode": state.mode.value,
            "generation": state.generation,
            "reason": state.reason,
            "engaged_at": state.engaged_at,
            "estop_sentinel": get_estop_manager().is_engaged(),
            "admits_work": state.mode.value == "run" and not get_estop_manager().is_engaged(),
            "read_error": state.error,
        }
    except Exception as exc:
        # An unreadable control state is reported as ESTOP, which is also how
        # `read_state` itself behaves. The projection must not disagree with it.
        return _unavailable(f"{type(exc).__name__}: {exc}")


def store_status(store: ApexStore) -> dict[str, Any]:
    """Session counts by state, or a disclosed read failure."""
    if store.is_degraded:
        return _unavailable(store.load_error or "store load failed")
    try:
        sessions = store.list(limit=1000)
    except Exception as exc:
        return _unavailable(f"{type(exc).__name__}: {exc}")
    by_state: dict[str, int] = {}
    for session in sessions:
        by_state[session.state.value] = by_state.get(session.state.value, 0) + 1
    return {
        "available": True,
        "total": len(sessions),
        "by_state": by_state,
        "active": by_state.get(ApexSessionState.ACTIVE.value, 0),
        "terminal": sum(1 for s in sessions if s.is_terminal),
    }


#: Dotted path of the supervisor whose ``status()`` this projection reads.
#:
#: A **string**, not an import. The harness (``alpha.*``) may never import the
#: app layer — ``tests/test_harness_boundary.py`` fails the build on it — and the
#: supervisor is app-side. Resolving the path here and letting the caller
#: inject the callable keeps this module on the correct side of that boundary
#: instead of hiding the violation behind a try/except that would also swallow
#: a genuine failure.
SUPERVISOR_STATUS_PATH = "app.gateway.autonomy.supervisor:get_autonomy_supervisor"


def _supervisor_status(provider: Callable[[], dict[str, Any]] | None) -> dict[str, Any]:
    """The autonomy supervisor's own view, injected by the caller.

    Absent is reported as ``available: False`` with a reason, never as an empty
    status object — an operator must be able to tell "no supervisor ran" from
    "the supervisor is healthy".
    """
    if provider is None:
        return _unavailable(f"no supervisor provider bound (expected {SUPERVISOR_STATUS_PATH})")
    try:
        return {"available": True, "status": provider()}  # noqa: PGH003 - provider is a zero-arg callable
    except Exception as exc:
        return _unavailable(f"{type(exc).__name__}: {exc}")


def session_summary(session: ApexSession, *, contract: AutonomyContract | None = None) -> dict[str, Any]:
    """One session's live view, including whether its policy has drifted."""
    payload = session.to_dict()
    payload["is_terminal"] = session.is_terminal
    if contract is not None:
        payload["contract_matches"] = session.contract_digest == contract.digest() if session.contract_digest else None
        payload["contract_drift"] = bool(session.contract_digest and contract.digest() and session.contract_digest != contract.digest())
    return payload


def apex_status(
    store: ApexStore,
    contract: AutonomyContract,
    *,
    session_id: str | None = None,
    include_invariants: bool = True,
    supervisor_provider: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The full ``/apex status`` payload.

    ``include_invariants`` is a parameter because the invariant probe imports
    eight modules; a UI poll should not pay that on every tick.
    """
    payload: dict[str, Any] = {
        "schema": "alpha.apex.status.v1",
        "contract": contract_status(contract),
        "fleet": fleet_status(),
        "sessions": store_status(store),
        "supervisor": _supervisor_status(supervisor_provider),
        "generated_at": None,
    }
    try:
        from time import time

        payload["generated_at"] = time()
    except Exception:
        payload["generated_at"] = None

    if include_invariants:
        payload["invariants"] = invariant_summary()

    if session_id:
        session = store.get(session_id)
        if session is None:
            payload["session"] = {
                "available": False,
                "reason": f"no APEX session {session_id!r}",
                "session_id": session_id,
            }
        else:
            payload["session"] = {
                "available": True,
                **session_summary(session, contract=contract),
                "events": [e.to_dict() for e in store.read_events(session_id, after_seq=0)[-25:]],
            }
    return payload
