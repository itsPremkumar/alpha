"""War Room REST API: read the room, judge it, and stop it.

This is the surface the frontend renders. It is deliberately **read-mostly**: the
model-facing ``war_room`` tool is what opens a room, because opening one spends
tokens and a human should usually be the one to decide that. These routes exist
so a person can *see* what happened and *judge* it afterwards.

Routes:

- ``GET  /api/war-rooms``                  list persisted runs, newest first
- ``GET  /api/war-rooms/{run_id}``         one run with its full stage evidence
- ``GET  /api/war-rooms/{run_id}/transcript`` the gap-checked transcript
- ``GET  /api/war-rooms/analytics``        cross-run totals a dashboard needs
- ``POST /api/war-rooms/evaluate``         would a room open on this prompt?
- ``GET  /api/war-rooms/trigger-policy``   the current auto-trigger switches

Two honesty rules this router exists to enforce:

1. **A run is never reported as verified.** ``verification`` is a
   ``consensus_supported``/``tainted``/``unverified`` label derived from the
   recorded evidence, and a completed run is not a verified one.
2. **Unreadable state is disclosed, not hidden.** A corrupt ``run.json`` comes
   back as ``status="unreadable"`` with the parse error rather than vanishing
   from the list.

Every filesystem read runs through ``asyncio.to_thread`` so the Gateway's
non-blocking concurrency invariants hold.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/war-rooms", tags=["war-rooms"])


class WarRoomEvaluateRequest(BaseModel):
    """Pre-flight: is this prompt worth a panel?"""

    topic: str = Field(..., min_length=2, description="The topic a room would deliberate.")
    simulate_enabled: bool = Field(
        default=True,
        description="Evaluate as if auto-triggering were ON, so an operator can see what it would decide.",
    )


def _root():
    from alpha.commands.channel_ops import runtime_root

    return runtime_root()


def _verification(record: dict[str, Any]) -> str:
    """A label describing what the run actually established.

    Deliberately NOT "verified": a run that reached a synthesis established that
    a quorum of agents agreed on a claim, which is a different and much weaker
    statement than the decision being correct.
    """
    if record.get("status") in {"failed", "timeout", "cancelled", "unreadable"}:
        return "unverified"
    if record.get("tainted"):
        return "tainted"
    if record.get("status") == "partial":
        return "consensus_degraded"
    return "consensus_supported"


@router.get("")
@require_permission("threads", "read")
async def list_war_rooms(room: str | None = None, limit: int = 50) -> dict:
    """Persisted runs, newest first, each carrying its verification label."""
    limit = max(1, min(int(limit), 500))

    def _do() -> list[dict[str, Any]]:
        from alpha.groups.war_room import list_persisted_runs

        records = list_persisted_runs(_root(), room or None)
        for record in records:
            record["verification"] = _verification(record)
        return records

    records = await asyncio.to_thread(_do)
    return {"ok": True, "count": len(records), "runs": records[:limit]}


@router.get("/analytics")
@require_permission("threads", "read")
async def war_room_analytics(room: str | None = None) -> dict:
    """Cross-run totals: statuses, strategies, taint, dissent, duration."""

    def _do() -> dict[str, Any]:
        from alpha.groups.war_room import list_persisted_runs

        records = [r for r in list_persisted_runs(_root(), room or None) if r.get("status") != "unreadable"]
        statuses = Counter(str(r.get("status", "unknown")) for r in records)
        strategies = Counter(str(r.get("strategy", "legacy_fixed")) for r in records)
        tainted = sum(1 for r in records if r.get("tainted"))
        unreadable = sum(1 for r in list_persisted_runs(_root(), room or None) if r.get("status") == "unreadable")

        durations: list[float] = []
        dissent_total = 0
        agree_total = 0
        for record in records:
            started, finished = record.get("started_at"), record.get("finished_at")
            if started and finished:
                try:
                    from alpha.channels.transcript import parse_iso

                    durations.append((parse_iso(finished) - parse_iso(started)).total_seconds())
                except Exception:  # noqa: BLE001 - a bad timestamp is not a route failure
                    pass
            dissent_total += len(record.get("minority_dissent") or {})
            quorum = record.get("final_quorum") or {}
            if isinstance(quorum, dict):
                agree_total += int(quorum.get("agree", 0) or 0)

        durations.sort()

        def _pct(value: float) -> float:
            if not durations:
                return 0.0
            index = min(len(durations) - 1, int(round(value * (len(durations) - 1))))
            return round(durations[index], 3)

        return {
            "runs": len(records),
            "unreadable": unreadable,
            "by_status": dict(statuses),
            "by_strategy": dict(strategies),
            "tainted_runs": tainted,
            "stages_with_dissent": dissent_total,
            "total_agreeing_members": agree_total,
            "duration_seconds": {
                "count": len(durations),
                "p50": _pct(0.50),
                "p95": _pct(0.95),
                "max": round(durations[-1], 3) if durations else 0.0,
            },
        }

    return {"ok": True, **(await asyncio.to_thread(_do))}


@router.get("/trigger-policy")
@require_permission("threads", "read")
async def get_trigger_policy() -> dict:
    """The auto-trigger switches, so a UI can show what would and would not fire."""
    from alpha.groups.trigger import TriggerPolicy

    return {"ok": True, "policy": TriggerPolicy().to_dict()}


@router.post("/evaluate")
@require_permission("threads", "read")
async def evaluate_war_room(payload: WarRoomEvaluateRequest) -> dict:
    """Would a room open on this topic? Read-only; changes no stored policy."""
    from alpha.groups.trigger import TriggerPolicy, decide

    def _do() -> dict[str, Any]:
        return decide(
            payload.topic,
            policy=TriggerPolicy(enabled=payload.simulate_enabled),
            interactive=True,
            now=time.time(),
        ).to_dict()

    return {"ok": True, "decision": await asyncio.to_thread(_do)}


@router.get("/{run_id}")
@require_permission("threads", "read")
async def get_war_room(run_id: str, room: str = "war-room") -> dict:
    """One run with every piece of evidence it recorded."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise HTTPException(status_code=400, detail="invalid run id")

    def _do() -> dict[str, Any] | None:
        from alpha.groups.war_room import load_run_record

        return load_run_record(_root(), room, run_id)

    record = await asyncio.to_thread(_do)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no persisted run {run_id!r} in room {room!r}")
    record["verification"] = _verification(record)
    return {"ok": True, "run": record}


@router.get("/{run_id}/transcript")
@require_permission("threads", "read")
async def get_war_room_transcript(run_id: str, room: str = "war-room", limit: int = 500) -> dict:
    """The durable transcript, plus whether it is actually gap-free."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise HTTPException(status_code=400, detail="invalid run id")
    limit = max(1, min(int(limit), 5000))

    def _do() -> dict[str, Any]:
        from alpha.channels.transcript import TranscriptStore
        from alpha.groups.war_room import run_dir

        path = run_dir(_root(), room, run_id) / "transcript.jsonl"
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"no transcript for run {run_id!r}")
        messages: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    messages.append(__import__("json").loads(stripped))
                except ValueError:
                    messages.append({"unparsed": stripped[:2000]})
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"transcript unreadable: {exc}") from exc

        gap_free = True
        try:
            TranscriptStore(path).verify_ordered_and_gap_free()
        except Exception:  # noqa: BLE001 - report the fault, do not hide it
            gap_free = False
        return {
            "run_id": run_id,
            "room": room,
            "count": len(messages),
            "gap_free": gap_free,
            "messages": messages[:limit],
        }

    return {"ok": True, **(await asyncio.to_thread(_do))}
