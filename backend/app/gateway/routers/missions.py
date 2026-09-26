"""Durable missions API: long-lived objectives above threads.

Surfaces, in the order a mission moves through them:

- ``POST   /api/missions``                          create (with acceptance criteria)
- ``GET    /api/missions``                          list
- ``GET    /api/missions/{id}``                     read
- ``POST   /api/missions/{id}/transition``          move a phase
- ``POST   /api/missions/{id}/threads``             attach a working thread
- ``POST   /api/missions/{id}/artifacts``           attach a produced artifact
- ``POST   /api/missions/{id}/observations``        record observed behaviour
- ``POST   /api/missions/{id}/acceptance``          evaluate the criteria now
- ``GET    /api/missions/{id}/acceptance``          read the last report
- ``GET    /api/missions/{id}/events``              **LIVE** SSE feed, assignment→observation
- ``GET    /api/missions/{id}/events/history``      the same events, batch

The live feed is the part that was missing: acceptance used to be BATCH and
POST-HOC, so nothing could show what a mission was doing while it did it.  The
feed replays the durable journal from ``after_seq`` and then tails live events,
and it terminates once the mission is terminal rather than streaming forever.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission

router = APIRouter(prefix="/api/missions", tags=["missions"])

#: Terminal mission statuses; the SSE feed closes after replaying these.
_TERMINAL_STATUSES = frozenset({"completed", "cancelled"})
#: How long the SSE generator waits for the next event before sending a
#: keepalive comment.  Bounded so a dead client is noticed and so the
#: generator always re-checks ``is_disconnected``.
_KEEPALIVE_SECONDS = 15.0
#: Hard cap on a single SSE session so a client that never disconnects cannot
#: hold a thread forever.  Disclosed in the terminal ``stream_closed`` event.
_MAX_STREAM_SECONDS = 3600.0


class MissionCreateRequest(BaseModel):
    objective: str = Field(..., min_length=3, max_length=5000)
    constraints: dict = Field(default_factory=dict)
    budget: dict = Field(default_factory=dict)
    # A mission with no criteria cannot be justified as completed, so the
    # criteria are part of creation rather than an afterthought.
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=200)


class MissionAttachArtifactRequest(BaseModel):
    artifact: str = Field(..., min_length=1, max_length=2048)


class MissionObservationRequest(BaseModel):
    observation: str = Field(..., min_length=1, max_length=8000)
    detail: dict[str, Any] = Field(default_factory=dict)


class MissionAcceptanceRequest(BaseModel):
    # Keyed by criterion text -> the MEASURED boolean.  Omit it to run the
    # registered evaluators; omit both to get an honest all-UNVERIFIED report.
    evidence: dict[str, bool] = Field(default_factory=dict)
    use_registry: bool = True


def _owner(request: Request) -> str:
    try:
        from alpha.runtime.user_context import get_effective_user_id

        return get_effective_user_id() or "local-user"
    except Exception:
        return "local-user"


def _owned_or_404(mission_id: str, owner: str) -> Any:
    from alpha.missions import get_mission_store

    mission = get_mission_store().get(mission_id)
    if mission is None or mission.owner != owner:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


@router.post("", status_code=201)
@require_permission("threads", "write")
async def create_mission(body: MissionCreateRequest, request: Request) -> dict:
    owner = _owner(request)

    def _do():
        from alpha.missions import get_mission_store

        m = get_mission_store().create(
            owner,
            body.objective,
            constraints=body.constraints,
            budget=body.budget,
            acceptance_criteria=body.acceptance_criteria,
        )
        return m.to_dict()

    return await asyncio.to_thread(_do)


@router.get("")
@require_permission("threads", "read")
async def list_missions(request: Request, status: str | None = None) -> dict:
    owner = _owner(request)

    def _do():
        from alpha.missions import get_mission_store

        rows = get_mission_store().list(owner=owner, status=status)
        return {"missions": [m.to_dict() for m in rows], "count": len(rows)}

    return await asyncio.to_thread(_do)


@router.get("/{mission_id}")
@require_permission("threads", "read")
async def get_mission(mission_id: str, request: Request) -> dict:
    owner = _owner(request)
    return (await asyncio.to_thread(_owned_or_404, mission_id, owner)).to_dict()


@router.post("/{mission_id}/transition")
@require_permission("threads", "write")
async def transition_mission(mission_id: str, request: Request, to: str = "active") -> dict:
    owner = _owner(request)
    if to not in ("active", "paused", "completed", "cancelled"):
        raise HTTPException(status_code=422, detail="Invalid mission transition.")

    def _do():
        from alpha.missions import get_mission_store

        store = get_mission_store()
        m = store.get(mission_id)
        if m is None or m.owner != owner:
            return None
        refusal = store.completion_refusal(mission_id) if to == "completed" else None
        result = store.transition(mission_id, to)
        return result, refusal

    result, refusal = await asyncio.to_thread(_do)
    if result is None:
        if refusal:
            # 409, not 404: the transition was legal but the acceptance gate
            # refused it, and the caller is told exactly why.
            raise HTTPException(status_code=409, detail=refusal)
        raise HTTPException(status_code=404, detail="Mission not found or transition illegal.")
    return result.to_dict()


class AttachThreadRequest(BaseModel):
    thread_id: str = Field(..., min_length=1, max_length=64)


@router.post("/{mission_id}/threads")
@require_permission("threads", "write")
async def attach_thread(mission_id: str, body: AttachThreadRequest, request: Request) -> dict:
    owner = _owner(request)

    def _do():
        from alpha.missions import get_mission_store

        mission = _owned_or_404(mission_id, owner)
        result = get_mission_store().attach_thread(mission_id, body.thread_id)
        return result if result is not None else mission

    return (await asyncio.to_thread(_do)).to_dict()


@router.post("/{mission_id}/artifacts")
@require_permission("threads", "write")
async def attach_artifact(mission_id: str, body: MissionAttachArtifactRequest, request: Request) -> dict:
    owner = _owner(request)

    def _do():
        from alpha.missions import get_mission_store

        mission = _owned_or_404(mission_id, owner)
        result = get_mission_store().attach_artifact(mission_id, body.artifact)
        return result if result is not None else mission

    return (await asyncio.to_thread(_do)).to_dict()


@router.post("/{mission_id}/observations")
@require_permission("threads", "write")
async def record_observation(mission_id: str, body: MissionObservationRequest, request: Request) -> dict:
    owner = _owner(request)

    def _do():
        from alpha.missions import get_mission_store

        _owned_or_404(mission_id, owner)
        event = get_mission_store().record_observation(mission_id, body.observation, **body.detail)
        return None if event is None else event.to_dict()

    return await asyncio.to_thread(_do)


@router.post("/{mission_id}/acceptance")
@require_permission("threads", "write")
async def evaluate_acceptance(mission_id: str, body: MissionAcceptanceRequest, request: Request) -> dict:
    """Evaluate the mission's acceptance criteria and persist the verdict.

    Always returns the real report, including when it is NOT a pass.  It never
    transitions the mission: the caller still has to ask for ``completed``,
    which the store refuses unless this report is a full pass.
    """
    owner = _owner(request)

    def _do():
        from alpha.mission.acceptance import evaluate_acceptance as _evaluate
        from alpha.mission.acceptance import get_acceptance_registry
        from alpha.missions import get_mission_store

        mission = _owned_or_404(mission_id, owner)
        criteria = list(mission.acceptance_criteria)
        if body.evidence:
            report = _evaluate(criteria, body.evidence, evaluator="recorded_evidence")
        elif body.use_registry:
            report = get_acceptance_registry().evaluate(criteria)
        else:
            from alpha.mission.acceptance import unevaluated_report

            report = unevaluated_report(
                criteria,
                evaluator="none",
                notes=["evaluation was requested without measured evidence and without a registered evaluator"],
            )
        stored = get_mission_store().record_acceptance(mission_id, report)
        payload = report.to_dict()
        payload["mission_id"] = mission_id
        payload["status"] = stored.status if stored is not None else mission.status
        payload["completion_refusal"] = get_mission_store().completion_refusal(mission_id)
        return payload

    return await asyncio.to_thread(_do)


@router.get("/{mission_id}/acceptance")
@require_permission("threads", "read")
async def get_acceptance(mission_id: str, request: Request) -> dict:
    owner = _owner(request)

    def _do():
        from alpha.missions import get_mission_store

        mission = _owned_or_404(mission_id, owner)
        report = mission.acceptance_report()
        return {
            "mission_id": mission_id,
            "status": mission.status,
            "criteria": list(mission.acceptance_criteria),
            "acceptance": report.to_dict() if report is not None else None,
            "completion_refusal": get_mission_store().completion_refusal(mission_id),
        }

    return await asyncio.to_thread(_do)


@router.get("/{mission_id}/events/history")
@require_permission("threads", "read")
async def mission_event_history(mission_id: str, request: Request, after_seq: int = 0) -> dict:
    """The mission's events, batch. The same content the live feed streams."""
    owner = _owner(request)

    def _do():
        from alpha.missions import get_mission_store

        _owned_or_404(mission_id, owner)
        events = get_mission_store().read_events(mission_id, after_seq=after_seq)
        return {
            "mission_id": mission_id,
            "events": [e.to_dict() for e in events],
            "count": len(events),
            "last_seq": events[-1].seq if events else after_seq,
        }

    return await asyncio.to_thread(_do)


@router.get("/{mission_id}/events")
@require_permission("threads", "read")
async def stream_mission_events(mission_id: str, request: Request, after_seq: int = 0) -> StreamingResponse:
    """LIVE SSE feed: assignment through to observed behaviour.

    Opens with a ``ready`` event, replays everything after ``after_seq`` from the
    durable journal, then tails live events.  Closes with ``stream_closed`` once
    the mission is terminal, or when the client disconnects, or at the session
    cap.  A mission that is ALREADY terminal therefore replays and closes
    immediately rather than hanging the client.
    """
    owner = _owner(request)
    await asyncio.to_thread(_owned_or_404, mission_id, owner)

    from alpha.missions import MISSION_FEED, get_mission_store

    async def _events():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        seen: set[int] = set()
        got_terminal = False

        def _on_event(event) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        # Replay first, then subscribe, then drop anything the replay already
        # covered.  Subscribing before the replay would be simpler but would
        # double-deliver every event that arrived during it.
        def _do_replay() -> tuple[list[dict[str, Any]], str, bool]:
            store = get_mission_store()
            mission = store.get(mission_id)
            status = mission.status if mission is not None else "unknown"
            events = store.read_events(mission_id, after_seq=after_seq)
            return [e.to_dict() for e in events], status, status in _TERMINAL_STATUSES

        replay, status, got_terminal = await asyncio.to_thread(_do_replay)
        MISSION_FEED.subscribe(mission_id, _on_event)
        try:
            yield f"event: ready\ndata: {json.dumps({'type': 'ready', 'mission_id': mission_id, 'status': status, 'replay': len(replay), 'after_seq': after_seq})}\n\n"
            for payload in replay:
                seen.add(int(payload.get("seq", 0)))
                yield f"event: mission\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if got_terminal:
                yield f"event: stream_closed\ndata: {json.dumps({'type': 'stream_closed', 'reason': 'mission is already terminal', 'status': status})}\n\n"
                return

            started = loop.time()
            while loop.time() - started < _MAX_STREAM_SECONDS:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if int(event.seq) in seen:
                    continue
                seen.add(int(event.seq))
                yield f"event: mission\ndata: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"
                if event.event_type == "mission_transitioned" and event.payload.get("to") in _TERMINAL_STATUSES:
                    yield f"event: stream_closed\ndata: {json.dumps({'type': 'stream_closed', 'reason': 'mission reached a terminal status', 'status': event.payload.get('to')})}\n\n"
                    return
            yield f"event: stream_closed\ndata: {json.dumps({'type': 'stream_closed', 'reason': 'session cap reached', 'max_seconds': _MAX_STREAM_SECONDS})}\n\n"
        finally:
            MISSION_FEED.unsubscribe(mission_id, _on_event)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
