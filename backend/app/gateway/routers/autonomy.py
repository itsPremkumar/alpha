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

Mount seam (app.py, applied by the lead):
``app.include_router(autonomy.router)`` after importing ``autonomy`` from
``app.gateway.routers``. Auth is untouched: like ``/api/ops`` and
``/api/supervision``, these routes sit behind the gateway's default
``AuthMiddleware`` and carry no route-level decorators of their own.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query
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
        "note": (
            "request-time config.yaml view (get_app_config re-reads on file change); "
            "the supervisor snapshots its config at startup, so a startup/config mismatch is flagged in notes"
        ),
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
