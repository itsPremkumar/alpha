"""Read-only team composition surface for the Alpha swarm runtime.

A *team* is a :class:`alpha.swarm.models.SwarmPlan` whose tasks are assigned to
declared specialists. There is no team table, no team lifecycle, and no second
run owner: every route here is a projection of swarm state that
:class:`alpha.swarm.coordinator.SwarmCoordinator` already owns, and every
mutation a team needs is a mutation the swarm routes already expose
(``POST /api/swarms``, ``POST /api/swarms/{id}/run-async``). This router adds
readability, not authority.

**Mounted.** Router registration in this Gateway is explicit
(``app.include_router(...)`` in ``app/gateway/app.py``); the ``teams`` import and
one ``app.include_router(teams.router)`` beside the swarms mount are both
present, so every route below answers over HTTP. The mount is pinned positively
by ``tests/test_team_routes.py::test_the_team_routes_are_mounted`` so it cannot
rot back into dead code that merely looks like a working feature. See
``docs/TEAM_RUNTIME.md`` for the mount history - this notice previously said the
router was *not* mounted, which was true when written and stopped being true when
those two lines landed. Keep the claim in step with ``app.py``.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from alpha.runtime.user_context import get_effective_user_id
from alpha.swarm.coordinator import get_swarm_coordinator
from alpha.swarm.team import render_team_report_markdown, resolve_roster

router = APIRouter(prefix="/api/teams", tags=["teams"])


def _request_owner(request: Request | None) -> str | None:
    """Resolve the server-owned owner for a route without trusting body fields."""

    if request is None:
        return None
    user = getattr(getattr(request, "state", None), "user", None)
    user_id = getattr(user, "id", None)
    return str(user_id) if user_id else get_effective_user_id()


def _owner_scope(request: Request | None) -> str | None:
    """Admins may inspect every team; ordinary callers are owner-scoped."""

    if request is None:
        return None
    user = getattr(getattr(request, "state", None), "user", None)
    if getattr(user, "system_role", None) == "admin":
        return None
    return _request_owner(request)


@router.get("/roster")
async def get_specialist_roster(request: Request = None):
    """Report the specialist roster this process would compose a team from.

    Answers "is a specialist team even possible here?" before a plan exists.
    With no roster provider registered it returns an empty roster carrying the
    reason, which is the honest answer rather than a 404 or a silent default.
    """

    roster, provenance = await asyncio.to_thread(resolve_roster)
    return {
        "registered": bool(provenance.get("registered")),
        "source": provenance.get("source", ""),
        "reason": provenance.get("reason", ""),
        "declared": provenance.get("declared", 0),
        "accepted": provenance.get("accepted", 0),
        "rejected": provenance.get("rejected", []),
        "error": provenance.get("error"),
        "members": [member.to_dict() for member in roster],
    }


@router.get("/{swarm_id}")
async def get_team_report(swarm_id: str, request: Request = None):
    """Who was on the team, what each was asked, what each returned."""

    coordinator = get_swarm_coordinator()
    report = await asyncio.to_thread(coordinator.team_report, swarm_id, owner_id=_owner_scope(request))
    if report.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"Team '{swarm_id}' not found.")
    return report


@router.get("/{swarm_id}/report.md", response_class=None)
async def get_team_report_markdown(swarm_id: str, request: Request = None):
    """The same report rendered for a human reading a terminal."""

    coordinator = get_swarm_coordinator()
    report = await asyncio.to_thread(coordinator.team_report, swarm_id, owner_id=_owner_scope(request))
    if report.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"Team '{swarm_id}' not found.")
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(render_team_report_markdown(report), media_type="text/markdown; charset=utf-8")


@router.get("/{swarm_id}/roster")
async def get_team_roster(swarm_id: str, request: Request = None):
    """The specialists this plan actually assigned work to, with their load.

    Reads ``metrics["team"]``, which the coordinator recorded at composition
    time. It is a snapshot of who was on the team, not the current process
    roster: a plan composed before a catalogue reload keeps reporting the team
    it actually ran, which is the only useful answer after the fact.
    """

    coordinator = get_swarm_coordinator()
    plan = coordinator.get_swarm(swarm_id, owner_id=_owner_scope(request))
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Team '{swarm_id}' not found.")
    team = plan.metrics.get("team") if isinstance(plan.metrics, dict) else None
    return {
        "swarm_id": swarm_id,
        "roster_registered": bool((team or {}).get("roster_registered")),
        "roster_source": (team or {}).get("roster_source", ""),
        "roster_provenance": (team or {}).get("roster_provenance", {}),
        "assigned": (team or {}).get("assigned", 0),
        "unassigned": (team or {}).get("unassigned", 0),
        "load": (team or {}).get("load", {}),
        "unassigned_detail": (team or {}).get("unassigned_detail", []),
    }
