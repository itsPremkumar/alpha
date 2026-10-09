"""Autonomy REST surface: supervisor status + Sentinel report history & triggers.

Makes the two backend-only subsystems (``AutonomySupervisor`` and the Sentinel
repair loop) visible and operable from the frontend, with real data only:

* ``GET  /api/autonomy/status`` — the live ``get_autonomy_supervisor().status()``
  payload plus the request-time ``AutonomyConfig`` flags (per-loop enabled /
  interval), with disclosure notes about which source is which.
* ``GET  /api/autonomy/sentinel/reports`` — capped read of the durable report
  journal (``alpha.runtime.sentinel.report_store``). An unreadable journal is
  a fail-closed 500 naming the file and line — never a silent empty list.
* ``POST /api/autonomy/sentinel/run`` — run one real pass via
  ``sentinel_tick`` on a worker thread (``asyncio.to_thread``), journaled at
  the same choke point the supervisor loop uses, and return the real report.
* ``GET  /api/autonomy/sentinel/signals`` — observe-only ``SentinelRunner.
  collect()``: no diagnosis, no fixes, no commits.
* ``GET  /api/autonomy/sentinel/analytics`` — the same journal folded into one
  reading (totals, per-kind verdicts, recurring fingerprints, measured
  durations) with the window it was measured over. Reads fail closed like
  ``/reports``; the fold is I/O-free and cannot invent a measurement.
* ``GET  /api/autonomy/sentinel/kinds`` — the declared fault-kind registry and
  which kinds currently carry a repair strategy. A declaration, not a proof.
* ``GET  /api/autonomy/sentinel/escalations`` — the human handoffs the Sentinel
  published to ``alpha.runtime.escalation``. An unreadable store is 503 with
  the reason, never an empty queue.
* ``POST /api/autonomy/sentinel/escalations/{id}/acknowledge`` and
  ``.../resolve`` — admin-only human decisions. Both re-read the record and
  say in their own note that recording a decision changes no engine state.

Mount seam (app.py, applied by the lead):
``app.include_router(autonomy.router)`` after importing ``autonomy`` from
``app.gateway.routers``. Auth is untouched: like ``/api/ops`` and
``/api/supervision``, these routes sit behind the gateway's default
``AuthMiddleware`` and carry no route-level decorators of their own; the two
escalation writes add an explicit ``is_admin_user`` gate on top of it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/autonomy", tags=["autonomy"])

#: Upper bound on one history read, enforced by request validation (422 above it).
MAX_REPORT_LIMIT = 200
DEFAULT_REPORT_LIMIT = 50


def _report_store():
    """Store seam (monkeypatchable in tests); resolves the runtime-home journal."""
    from alpha.runtime.sentinel.report_store import default_sentinel_report_store

    return default_sentinel_report_store()


def _config_block(loop_ids: list[str]) -> dict:
    """Request-time AutonomyConfig flags relevant to the supervisor loops."""
    try:
        from alpha.config.app_config import get_app_config

        autonomy = get_app_config().autonomy
    except Exception as exc:  # honest unavailability, never guessed defaults
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    loops = {}
    for loop_id in loop_ids:
        cfg = autonomy.loop_config(loop_id)
        loops[loop_id] = {
            "enabled": cfg.enabled,
            "interval_seconds": cfg.interval_seconds,
            "jitter_seconds": cfg.jitter_seconds,
        }
    return {
        "available": True,
        "autonomy_enabled": autonomy.enabled,
        "bus_enabled": autonomy.bus.enabled,
        "loops": loops,
        "note": ("request-time config.yaml view (get_app_config re-reads on file change); the supervisor snapshots its config at startup, so a startup/config mismatch is flagged in notes"),
    }


@router.get(
    "/status",
    summary="Autonomy supervisor status",
    description="Live AutonomySupervisor counters for every registered loop plus the request-time AutonomyConfig flags.",
)
async def autonomy_status() -> dict:
    """Return the real supervisor status plus config flags and disclosure notes."""
    try:
        from app.gateway.autonomy.supervisor import get_autonomy_supervisor

        status = get_autonomy_supervisor().status()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    config_block = _config_block(sorted(status.get("loops", {})))
    notes = [
        "'supervisor' is the live AutonomySupervisor singleton: runs/failures/parked are counters this process measured.",
        "'config' is the request-time view of config.yaml; a loop absent from config: loops is disabled by default.",
    ]
    if config_block.get("available") and bool(config_block.get("autonomy_enabled")) != bool(status.get("enabled")):
        notes.append(
            "master-switch mismatch: the supervisor's autonomy.enabled differs from the current config.yaml value "
            f"({bool(status.get('enabled'))} at runtime vs {bool(config_block.get('autonomy_enabled'))} in config) — "
            "config.yaml changed after startup; restart to apply."
        )
    return {"supervisor": status, "config": config_block, "notes": notes}


@router.get(
    "/sentinel/reports",
    summary="Sentinel report history",
    description="Capped read of the durable JSONL report journal, oldest first, with verbatim integrity disclosures.",
)
async def sentinel_reports(limit: int = Query(DEFAULT_REPORT_LIMIT, ge=1, le=MAX_REPORT_LIMIT)) -> dict:
    """Return the newest ``limit`` journal entries. Fails closed on a corrupt journal."""
    from alpha.runtime.sentinel.report_store import ReportStoreReadError

    try:
        history = _report_store().history(limit=limit)
    except ReportStoreReadError as exc:
        # Real reason (file + line) reaches the client; an unreadable history
        # is never rendered as an empty one.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {
        "reports": list(history.entries),
        "order": "oldest_first",
        "total": history.total,
        "cap": history.cap,
        "source": history.source,
        "disclosures": history.disclosures(),
    }


class SentinelRunRequest(BaseModel):
    """Trigger body for one Sentinel pass."""

    auto_heal: bool = Field(
        default=False,
        description="Observe-only pass (false, default) or verified repair pass (true).",
    )


@router.post(
    "/sentinel/run",
    summary="Run one Sentinel pass",
    description="Run sentinel_tick on a worker thread (non-blocking), journal the report, and return the real report dict.",
)
async def run_sentinel(payload: SentinelRunRequest | None = None) -> dict:
    """Run one real Sentinel pass and return its report (plus persistence disclosure)."""
    from app.gateway.autonomy.loops import sentinel_tick

    auto_heal = bool(payload.auto_heal) if payload is not None else False
    try:
        report = await asyncio.to_thread(sentinel_tick, auto_heal=auto_heal, trigger="api")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return report


@router.get(
    "/sentinel/signals",
    summary="Sentinel observe-only signal collection",
    description="SentinelRunner.collect() with no fix strategies wired: nothing is diagnosed, fixed, or committed.",
)
async def sentinel_signals() -> dict:
    """Collect the currently observable signals without acting on them."""

    def observe() -> list[dict]:
        from alpha.runtime.sentinel.runner import SentinelRunner
        from app.gateway.autonomy.loops import _resolve_project_root

        runner = SentinelRunner(repo_root=_resolve_project_root())
        return [signal.to_dict() for signal in runner.collect()]

    try:
        signals = await asyncio.to_thread(observe)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {
        "observe_only": True,
        "fixes_applied": False,
        "count": len(signals),
        "signals": signals,
        "note": "observe-only collection (SentinelRunner.collect): no diagnosis, no fixes, no commits.",
    }


# ---------------------------------------------------------------------------
# Aggregate readings, the kind registry, and the human-handoff queue.
# ---------------------------------------------------------------------------


def _analytics_entries(store: Any, limit: int) -> tuple[list[dict], int]:
    """Newest ``limit`` journal entries plus the on-disk total.

    Raises the store's own typed read error — a corrupt journal fails the whole
    read with the file and line, and is never folded into a smaller number.
    """
    history = store.history(limit=limit)
    return list(history.entries), history.total


@router.get(
    "/sentinel/analytics",
    summary="Sentinel aggregate readings over the report journal",
    description=("Folds the newest N journal passes into one reading: totals, per-kind roll-ups with a verdict, recurring fingerprints, measured durations, and verbatim disclosures about the window."),
)
async def sentinel_analytics(limit: int = Query(DEFAULT_REPORT_LIMIT, ge=1, le=MAX_REPORT_LIMIT)) -> dict:
    """Fold the durable journal; fail closed on an unreadable one."""
    from alpha.runtime.sentinel.analytics import aggregate

    try:
        entries, total = _analytics_entries(_report_store(), limit)
    except Exception as exc:  # typed store errors name the file and line
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    reading = aggregate(entries, limit=limit, total_on_disk=total)
    return {
        "analytics": reading.to_dict(),
        "limit": limit,
        "total_on_disk": total,
        "note": ("folded in-process from the append-only journal; the journal is never rewritten, and every total carries the window it was measured over"),
    }


@router.get(
    "/sentinel/kinds",
    summary="Sentinel fault-kind registry",
    description=("Which fault kinds the loop recognises and which have a repair strategy registered today — a declaration, never a proof that a repair works."),
)
async def sentinel_kinds() -> dict:
    """Report the declared repair posture of every known fault kind."""

    def read_posture() -> dict:
        from alpha.runtime.sentinel.loop import KNOWN_KINDS
        from alpha.runtime.sentinel.runner import (
            make_default_fix_fns,
            make_default_verification_commands,
        )
        from app.gateway.autonomy.loops import _resolve_project_root

        root = _resolve_project_root()
        repair_fns = make_default_fix_fns(root)
        return {
            "known_kinds": sorted(KNOWN_KINDS),
            "repair_kinds": sorted(repair_fns),
            "verification_commands": {k: list(v) for k, v in make_default_verification_commands().items()},
            "source_root": str(root),
        }

    try:
        posture = await asyncio.to_thread(read_posture)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    known = set(posture["known_kinds"])
    repair = set(posture["repair_kinds"])
    posture["kinds"] = [
        {
            "kind": kind,
            "recognised": True,
            "repair_registered": kind in repair,
            # Wired only when a pass asks for repairs: an observe pass runs with
            # `fix_fns=None`, so every signal it meets escalates. Stating it here
            # keeps that from being read as "the repair strategy is broken".
            "repair_note": ("a repair function is registered by default" if kind in repair else "no repair strategy is registered — this kind always escalates"),
        }
        for kind in sorted(known)
    ] + [
        # A repair strategy for a kind the loop does not recognise would be
        # unreachable, but the reverse asymmetry is the dangerous one: a kind the
        # log source emits that the loop has never heard of. Reported, not dropped.
        {
            "kind": kind,
            "recognised": False,
            "repair_registered": False,
            "repair_note": "this repair target is not in the loop's recognised kinds",
        }
        for kind in sorted(repair - known)
    ]
    posture["unrecognised_repair_kinds"] = sorted(repair - known)
    posture["disclosures"] = [
        "recognised: the fault kinds alpha.runtime.sentinel.loop.diagnose can route to a strategy",
        "repair_registered: a repair callable exists in the default set — a declaration, not a successful repair",
        "a repair function is wired only when a pass requests repairs (auto_heal=true); an observe pass registers none and therefore escalates every signal",
        "verification_commands: the checks a repair pass must pass before it may commit",
        "this list is the registry, not a scan of the repository",
    ]
    return posture


@router.get(
    "/sentinel/escalations",
    summary="Sentinel human handoffs",
    description=("Read-only view of the escalations the Sentinel published to a human, from alpha.runtime.escalation. An unreadable store is 503 with the reason, never an empty queue."),
)
async def sentinel_escalations(status: str | None = None, limit: int = Query(DEFAULT_REPORT_LIMIT, ge=1, le=MAX_REPORT_LIMIT)) -> dict:
    """List the Sentinel's human escalations, oldest first."""

    def read_escalations() -> dict:
        from alpha.runtime.escalation import DOMAIN_SENTINEL, get_escalation_store

        store = get_escalation_store()
        rows = store.list(status=status, domain=DOMAIN_SENTINEL)
        return {"rows": rows, "path": str(store.path)}

    try:
        payload = await asyncio.to_thread(read_escalations)
    except Exception as exc:
        # A ledger read failure is "could not look", not "nothing is waiting on
        # a human" — those lead to opposite actions, so it must not be 200 [].
        raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}") from exc

    rows = payload["rows"]
    total = len(rows)
    shown = rows[-limit:]
    return {
        "escalations": [r.to_dict() for r in shown],
        "total": total,
        "returned": len(shown),
        "truncated": total > len(shown),
        "status_filter": status,
        "source": payload["path"],
        "disclosures": [
            "source: the durable human-escalation store (alpha.runtime.escalation)",
            f"history: newest {len(shown)} of {total} record(s) shown, oldest first" + (f"; {total - len(shown)} older record(s) are outside the requested limit" if total > len(shown) else ""),
            "an escalation is a handoff, not a fault count: one record covers one (fingerprint, reason) while it stays open",
            "this store is a local JSON file shared by one host; it is not a cross-process exactly-once queue",
        ],
    }


class SentinelEscalationDecision(BaseModel):
    """Body for one human decision on a Sentinel escalation."""

    by: str = Field(description="Who is recording the decision.", max_length=200)
    note: str = Field(default="", description="Optional resolution note.", max_length=2000)


def _escalation_actor(request: Request) -> str:
    """The caller's recorded identity, or a refusal naming what is missing."""
    user = getattr(getattr(request, "state", None), "user", None)
    for attribute in ("username", "user_id", "email"):
        value = getattr(user, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    logger.warning("Sentinel escalation decision refused: no caller identity on request.state")
    raise HTTPException(status_code=409, detail="caller identity could not be resolved from the request")


@router.post(
    "/sentinel/escalations/{escalation_id}/acknowledge",
    summary="Acknowledge one Sentinel escalation",
    description="Admin-only. Marks a human handoff as seen; it does not resolve it and repairs nothing.",
)
async def acknowledge_sentinel_escalation(escalation_id: str, payload: SentinelEscalationDecision, request: Request) -> dict:
    """Acknowledge one escalation, then re-read it — never paint the click."""
    from alpha.runtime.escalation import DOMAIN_SENTINEL, get_escalation_store
    from app.gateway.deps import is_admin_user

    if not await is_admin_user(request):
        # A PAT never qualifies, matching every other admin-only write.
        raise HTTPException(status_code=403, detail="admin session required")

    actor = _escalation_actor(request)
    declared_by = payload.by.strip()
    if declared_by and declared_by != actor:
        # The body may describe who is acting, but the server's own identity is
        # the authority: a caller-sent name is a claim, not an attestation.
        raise HTTPException(
            status_code=409,
            detail=f"declared actor {declared_by!r} does not match the authenticated caller",
        )

    store = get_escalation_store()
    try:
        record = await asyncio.to_thread(store.acknowledge, escalation_id, by=actor)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}") from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"escalation {escalation_id!r} not found")
    if record.domain != DOMAIN_SENTINEL:
        raise HTTPException(status_code=404, detail=f"escalation {escalation_id!r} is not a Sentinel handoff")
    return {
        "escalation": record.to_dict(),
        "applied": True,
        "note": "acknowledged by the caller; the underlying fault is unchanged and no repair was performed",
    }


@router.post(
    "/sentinel/escalations/{escalation_id}/resolve",
    summary="Resolve one Sentinel escalation",
    description="Admin-only. Records the human decision with its note; it repairs nothing and starts no run.",
)
async def resolve_sentinel_escalation(escalation_id: str, payload: SentinelEscalationDecision, request: Request) -> dict:
    """Resolve one escalation with a note, then re-read it."""
    from alpha.runtime.escalation import DOMAIN_SENTINEL, get_escalation_store
    from app.gateway.deps import is_admin_user

    if not await is_admin_user(request):
        raise HTTPException(status_code=403, detail="admin session required")

    actor = _escalation_actor(request)
    declared_by = payload.by.strip()
    if declared_by and declared_by != actor:
        raise HTTPException(
            status_code=409,
            detail=f"declared actor {declared_by!r} does not match the authenticated caller",
        )

    store = get_escalation_store()
    try:
        record = await asyncio.to_thread(store.resolve, escalation_id, by=actor, note=payload.note)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}") from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"escalation {escalation_id!r} not found")
    if record.domain != DOMAIN_SENTINEL:
        raise HTTPException(status_code=404, detail=f"escalation {escalation_id!r} is not a Sentinel handoff")
    return {
        "escalation": record.to_dict(),
        "applied": True,
        "note": "resolved by the caller; recording a decision changes no engine state and starts no repair",
    }
