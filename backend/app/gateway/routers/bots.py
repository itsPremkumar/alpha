"""First-class Bot profiles API.

Bots are autonomous specialist teammates (architect/coder/reviewer/tester/
researcher by default, auto-provisioned on demand). This router makes the
harness-layer BotRegistry a real system part: list/get/ensure/update with
capability-epoch fingerprints, AGENT_WORKSPACE_HOME-aware persistence, and
blocking-IO offload so Gateway event-loop rules hold.
"""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.deps import require_admin_user
from alpha.bots.profile import _now

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bots", tags=["bots"])
_ADMIN_REQUIRED_DETAIL = "Admin privileges are required to manage bots."

_BOT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validate_bot_name(name: str) -> str:
    cleaned = name.lower().strip()
    if not _BOT_NAME_RE.match(cleaned):
        raise HTTPException(
            status_code=422,
            detail="Bot name must be 1-64 chars: letters, digits, '_' or '-'.",
        )
    return cleaned


def _validation_error(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _bot_to_response(profile) -> dict:
    data = profile.to_dict()
    return {
        "name": data.get("name"),
        "display_name": data.get("display_name"),
        "role": data.get("role"),
        "soul": data.get("soul"),
        "model": data.get("model"),
        "toolsets": data.get("toolsets", []),
        "skills": data.get("skills", []),
        "avatar": data.get("avatar", ""),
        "status": data.get("status", "active"),
        "last_active": data.get("last_active"),
        "version": data.get("version", 1),
        "epoch": data.get("epoch"),
        "department": data.get("department", "engineering"),
        "reports_to": data.get("reports_to"),
        "responsibilities": data.get("responsibilities", []),
        "capabilities": data.get("capabilities", []),
        "heartbeat": data.get("heartbeat"),
        "succession_fallback": data.get("succession_fallback"),
        "reputation_score": data.get("reputation_score", 1.0),
        "task_stats": data.get("task_stats", {}),
        "routines": data.get("routines", []),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def _registry():
    from alpha.bots.registry import get_bot_registry

    return get_bot_registry()


class BotEnsureRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    role: str | None = Field(default=None, max_length=200)
    soul: str | None = Field(default=None, max_length=20000)
    template: str | None = Field(default=None, max_length=64)
    avatar: str | None = Field(default=None, max_length=16)
    department: str | None = Field(default=None, max_length=64)
    reports_to: str | None = Field(default=None, max_length=64)
    responsibilities: list[str] | None = None
    capabilities: list[str] | None = None
    succession_fallback: str | None = Field(default=None, max_length=64)


class BotUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    role: str | None = Field(default=None, max_length=200)
    soul: str | None = Field(default=None, max_length=20000)
    model: str | None = Field(default=None, max_length=200)
    toolsets: list[str] | None = Field(default=None, max_length=50)
    skills: list[str] | None = Field(default=None, max_length=100)
    avatar: str | None = Field(default=None, max_length=16)
    status: str | None = Field(default=None, max_length=16)
    last_active: str | None = Field(default=None, max_length=32)
    department: str | None = Field(default=None, max_length=64)
    reports_to: str | None = Field(default=None, max_length=64)
    responsibilities: list[str] | None = None
    capabilities: list[str] | None = None
    heartbeat: str | None = Field(default=None, max_length=32)
    succession_fallback: str | None = Field(default=None, max_length=64)
    reputation_score: float | None = Field(default=None, ge=0.0, le=1.0)
    task_stats: dict | None = None
    routines: list[dict] | None = None


class BotCloneRequest(BaseModel):
    source: str = Field(max_length=64)
    display_name: str | None = Field(default=None, max_length=100)
    role: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    department: str | None = Field(default=None, max_length=64)
    reports_to: str | None = Field(default=None, max_length=64)


class HeartbeatRequest(BaseModel):
    task_id: str | None = Field(default=None, max_length=128)
    lease_seconds: int | None = Field(default=None, ge=10, le=86400)
    details: dict | None = None


class GenerateOrgRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    auto_provision: bool = Field(default=False)


class TaskHandoffRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    from_bot: str = Field(min_length=1, max_length=64)
    to_bot: str = Field(min_length=1, max_length=64)
    objective: str = Field(min_length=1, max_length=2000)
    context_summary: str = Field(default="", max_length=20000)
    artifacts: list[str] | None = None
    acceptance_criteria: list[str] | None = None
    handoff_notes: str = Field(default="", max_length=5000)


class EscalateTaskRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)


class WorkDiscoveryMatchRequest(BaseModel):
    task_description: str = Field(min_length=3, max_length=5000)
    required_skills: list[str] | None = None
    required_department: str | None = None
    limit: int = Field(default=5, ge=1, le=20)


class TaskClaimRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    lease_seconds: int = Field(default=300, ge=30, le=86400)


class RecordTaskOutcomeRequest(BaseModel):
    success: bool
    duration_sec: float = Field(default=0.0, ge=0.0)
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    task_id: str | None = Field(default=None, max_length=128)


class QualityGateVerifyRequest(BaseModel):
    deliverable: str = Field(max_length=100000)
    acceptance_criteria: list[str] = Field(default_factory=list)
    required_sections: list[str] | None = None
    min_length: int = Field(default=40, ge=0)


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = Field(default="Operator emergency stop", max_length=500)


class BotPauseRequest(BaseModel):
    reason: str = Field(default="Operator paused", max_length=500)


@router.get("/templates", summary="List bot role templates")
async def list_bot_templates() -> dict:
    def _list():
        from alpha.bots.templates import list_templates

        return list_templates()

    templates = await asyncio.to_thread(_list)
    return {"templates": templates, "count": len(templates)}


@router.get("/departments", summary="List standard organization departments")
async def list_departments() -> dict:
    def _depts():
        from alpha.bots.templates import DEPARTMENTS

        return list(DEPARTMENTS)

    departments = await asyncio.to_thread(_depts)
    return {"departments": departments, "count": len(departments)}


@router.get("/health/overview", summary="Fleet-wide bot health and liveness overview")
async def get_fleet_health_overview() -> dict:
    def _health():
        from alpha.bots.health import get_health_monitor

        monitor = get_health_monitor()
        bots = _registry().list_bots()
        return monitor.get_fleet_health(bots)

    return await asyncio.to_thread(_health)


@router.get("/organization-chart", summary="Get organization hierarchy tree and graph")
async def get_org_chart() -> dict:
    def _chart():
        from alpha.bots.organization import get_organization_chart

        return get_organization_chart(_registry())

    return await asyncio.to_thread(_chart)


@router.post("/generate-org", summary="Dynamically generate team organization from goal")
async def generate_org(request: Request, body: GenerateOrgRequest) -> dict:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)

    def _gen():
        from alpha.bots.organization import generate_organization_for_goal

        return generate_organization_for_goal(body.goal, registry=_registry(), auto_provision=body.auto_provision)

    return await asyncio.to_thread(_gen)


@router.get("/kill-switch", summary="Check global kill switch and paused bots")
async def get_kill_switch() -> dict:
    def _status():
        from alpha.bots.kill_switch import get_kill_switch_status

        return get_kill_switch_status()

    return await asyncio.to_thread(_status)


@router.post("/kill-switch", summary="Engage or disengage global kill switch")
async def set_kill_switch(request: Request, body: KillSwitchRequest) -> dict:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)

    def _set():
        from alpha.bots.kill_switch import set_global_kill_switch

        return set_global_kill_switch(body.active, reason=body.reason)

    return await asyncio.to_thread(_set)


@router.post("/handoff", summary="Execute structured task handoff between bots")
async def handle_task_handoff(request: Request, body: TaskHandoffRequest) -> dict:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)

    def _handoff():
        from alpha.bots.handoff import execute_handoff

        try:
            pkg = execute_handoff(
                task_id=body.task_id,
                from_bot=body.from_bot,
                to_bot=body.to_bot,
                objective=body.objective,
                context_summary=body.context_summary,
                artifacts=body.artifacts,
                acceptance_criteria=body.acceptance_criteria,
                handoff_notes=body.handoff_notes,
                registry=_registry(),
            )
            return pkg.to_dict()
        except ValueError as exc:
            raise _validation_error(str(exc)) from exc

    return await asyncio.to_thread(_handoff)


@router.post("/work-discovery/match", summary="Rank candidate bots for a task by capability")
async def match_bots_for_task(body: WorkDiscoveryMatchRequest) -> dict:
    def _match():
        from alpha.bots.work_discovery import match_bot_for_task

        return match_bot_for_task(
            body.task_description,
            required_skills=body.required_skills,
            required_department=body.required_department,
            limit=body.limit,
            registry=_registry(),
        )

    candidates = await asyncio.to_thread(_match)
    return {"matches": candidates, "count": len(candidates)}


@router.post("/quality-gate/verify", summary="Evaluate deliverable against acceptance quality gate")
async def verify_quality_gate(body: QualityGateVerifyRequest) -> dict:
    def _verify():
        from alpha.bots.quality_gate import evaluate_quality_gate

        return evaluate_quality_gate(
            body.deliverable,
            body.acceptance_criteria,
            required_sections=body.required_sections,
            min_length=body.min_length,
        )

    return await asyncio.to_thread(_verify)


@router.get("/events", summary="Query organizational event audit trail")
async def get_org_events(
    limit: int = 50,
    event_type: str | None = None,
    actor: str | None = None,
    target: str | None = None,
) -> dict:
    def _events():
        from alpha.bots.events import query_org_events

        return query_org_events(limit=limit, event_type=event_type, actor=actor, target=target)

    events = await asyncio.to_thread(_events)
    return {"events": events, "count": len(events)}


@router.get("", summary="List bots")
async def list_bots(status: str | None = None, department: str | None = None) -> dict:
    if status is not None:
        from alpha.bots.templates import BOT_STATUSES

        if status not in BOT_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {list(BOT_STATUSES)}")

    def _list():
        return [_bot_to_response(b) for b in _registry().list_bots(status=status, department=department)]

    bots = await asyncio.to_thread(_list)
    return {"bots": bots, "count": len(bots)}


@router.get("/{name}", summary="Get bot profile")
async def get_bot(name: str) -> dict:
    key = _validate_bot_name(name)

    def _get():
        return _registry().get_bot(key)

    bot = await asyncio.to_thread(_get)
    if bot is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")
    return _bot_to_response(bot)


@router.post("/{name}/ensure", status_code=200, summary="Get or auto-provision bot")
async def ensure_bot(name: str, request: Request, body: BotEnsureRequest | None = None) -> dict:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)
    payload = body or BotEnsureRequest()

    def _ensure():
        try:
            bot = _registry().get_or_create(
                key,
                display_name=payload.display_name,
                role=payload.role,
                soul=payload.soul,
                template=payload.template,
                avatar=payload.avatar,
                department=payload.department,
                reports_to=payload.reports_to,
                responsibilities=payload.responsibilities,
                capabilities=payload.capabilities,
                succession_fallback=payload.succession_fallback,
            )
        except ValueError as exc:
            raise _validation_error(str(exc)) from exc
        # SOUL guarantee: custom SOULs keep the DM protocol section.
        from alpha.bots.dm import ensure_messaging_section

        guarded = ensure_messaging_section(bot.soul, key)
        if guarded != bot.soul:
            bot = _registry().update_bot(key, soul=guarded, bump_version=False) or bot
        return bot

    bot = await asyncio.to_thread(_ensure)
    return _bot_to_response(bot)


@router.post("/{name}/clone", status_code=201, summary="Clone bot from another profile")
async def clone_bot(name: str, request: Request, body: BotCloneRequest) -> dict:
    """Create a bot from another profile (config, skills, SOUL, avatar).

    Memory is never cloned: the copy starts with a fresh identity.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)
    source = _validate_bot_name(body.source)

    def _clone():
        try:
            return _registry().clone_bot(
                source,
                key,
                display_name=body.display_name,
                role=body.role,
                model=body.model,
                department=body.department,
                reports_to=body.reports_to,
            )
        except KeyError:
            return None
        except ValueError as exc:
            raise _validation_error(str(exc)) from exc

    bot = await asyncio.to_thread(_clone)
    if bot is None:
        raise HTTPException(status_code=404, detail=f"Bot '{source}' not found")
    return _bot_to_response(bot)


@router.patch("/{name}", summary="Update bot profile")
async def update_bot(name: str, request: Request, body: BotUpdateRequest) -> dict:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)

    def _update():
        try:
            return _registry().update_bot(
                key,
                display_name=body.display_name,
                role=body.role,
                soul=body.soul,
                model=body.model,
                toolsets=body.toolsets,
                skills=body.skills,
                avatar=body.avatar,
                status=body.status,
                last_active=body.last_active,
                department=body.department,
                reports_to=body.reports_to,
                responsibilities=body.responsibilities,
                capabilities=body.capabilities,
                heartbeat=body.heartbeat,
                succession_fallback=body.succession_fallback,
                reputation_score=body.reputation_score,
                task_stats=body.task_stats,
                routines=body.routines,
            )
        except ValueError as exc:
            raise _validation_error(str(exc)) from exc

    bot = await asyncio.to_thread(_update)
    if bot is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")
    return _bot_to_response(bot)


@router.post("/{name}/match", summary="Update match-time tracking (last_active, version bump)")
async def match_bot(name: str, request: Request) -> dict:
    """Update last_active to now and bump version — call when bot is invoked in a run."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)

    def _match():
        return _registry().update_bot(key, last_active=_now(), bump_version=True)

    bot = await asyncio.to_thread(_match)
    if bot is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")
    return _bot_to_response(bot)


@router.post("/{name}/heartbeat", summary="Record bot heartbeat and task lease")
async def record_heartbeat(name: str, body: HeartbeatRequest | None = None) -> dict:
    key = _validate_bot_name(name)
    payload = body or HeartbeatRequest()

    def _hb():
        from alpha.bots.health import get_health_monitor

        bot = _registry().get_bot(key)
        if not bot:
            return None
        monitor = get_health_monitor()
        rec = monitor.record_heartbeat(
            key,
            task_id=payload.task_id,
            lease_seconds=payload.lease_seconds,
            details=payload.details,
        )
        return rec.to_dict()

    res = await asyncio.to_thread(_hb)
    if res is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")
    return res


@router.post("/{name}/pause", summary="Pause execution for a specific bot")
async def pause_specific_bot(name: str, request: Request, body: BotPauseRequest | None = None) -> dict:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)
    payload = body or BotPauseRequest()

    def _pause():
        from alpha.bots.kill_switch import pause_bot

        return pause_bot(key, reason=payload.reason)

    return await asyncio.to_thread(_pause)


@router.post("/{name}/resume", summary="Resume execution for a paused bot")
async def resume_specific_bot(name: str, request: Request) -> dict:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)

    def _resume():
        from alpha.bots.kill_switch import resume_bot

        return resume_bot(key)

    return await asyncio.to_thread(_resume)


@router.get("/{name}/performance", summary="Get bot execution statistics and reputation score")
async def get_performance(name: str) -> dict:
    key = _validate_bot_name(name)

    def _perf():
        from alpha.bots.performance import get_bot_performance

        try:
            return get_bot_performance(key, registry=_registry())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return await asyncio.to_thread(_perf)


@router.post("/{name}/record-task", summary="Record task outcome and update reputation score")
async def record_task(name: str, request: Request, body: RecordTaskOutcomeRequest) -> dict:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)

    def _record():
        from alpha.bots.performance import record_task_outcome

        bot = record_task_outcome(
            key,
            success=body.success,
            duration_sec=body.duration_sec,
            quality_score=body.quality_score,
            task_id=body.task_id,
            registry=_registry(),
        )
        if not bot:
            return None
        return _bot_to_response(bot)

    res = await asyncio.to_thread(_record)
    if res is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")
    return res


@router.post("/{name}/claim", summary="Claim a task with a time-bound lease")
async def claim_task_endpoint(name: str, request: Request, body: TaskClaimRequest) -> dict:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)

    def _claim():
        from alpha.bots.work_discovery import claim_task

        try:
            return claim_task(
                body.task_id,
                key,
                lease_seconds=body.lease_seconds,
                registry=_registry(),
            )
        except ValueError as exc:
            raise _validation_error(str(exc)) from exc

    return await asyncio.to_thread(_claim)


@router.post("/{name}/escalate", summary="Escalate task to bot's reporting manager")
async def escalate_task_endpoint(name: str, request: Request, body: EscalateTaskRequest) -> dict:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)

    def _esc():
        from alpha.bots.handoff import escalate_task

        try:
            return escalate_task(
                body.task_id,
                key,
                body.reason,
                registry=_registry(),
            )
        except ValueError as exc:
            raise _validation_error(str(exc)) from exc

    return await asyncio.to_thread(_esc)


class SelectAgentRequest(BaseModel):
    required_capabilities: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=20)
    project_id: str | None = Field(default=None, max_length=64)


@router.post("/select", summary="Workload-aware agent selection")
async def select_agent_endpoint(body: SelectAgentRequest) -> dict:
    """Rank bots by capability match, availability, load, and reputation."""

    def _select():
        from alpha.projects.membership import get_membership_store
        from alpha.projects.routing import rank_candidates, select_agent

        memberships = get_membership_store().presence(body.project_id) if body.project_id else None
        ranked = rank_candidates(body.required_capabilities, exclude=set(body.exclude), limit=body.limit, memberships=memberships)
        picked = select_agent(body.required_capabilities, exclude=set(body.exclude), memberships=memberships)
        return {"candidates": [c.to_dict() for c in ranked], "selected": picked.to_dict() if picked else None}

    return await asyncio.to_thread(_select)


class RouteTaskRequest(BaseModel):
    task_type: str = Field(..., min_length=1, max_length=64)


@router.post("/route-task", summary="Route a task type to a model chain")
async def route_task_endpoint(body: RouteTaskRequest) -> dict:
    """Map task type to category chain with measured re-ranking (local-first)."""

    def _route():
        from alpha.models.task_router import route_task

        return route_task(body.task_type).to_dict()

    return await asyncio.to_thread(_route)


class DMSendRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=128)
    message: str = Field(..., min_length=1, max_length=16000)
    thread_metadata: dict | None = None
    thread_id: str | None = Field(default=None, max_length=64)


@router.post("/{name}/dm", summary="Send a fire-and-forget DM to a teammate bot")
async def send_dm_endpoint(name: str, request: Request, body: DMSendRequest) -> dict:
    """Bot Mode DM: roster-validated, server-side attribution, inbox delivery, no reply."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)

    metadata = dict(body.thread_metadata or {})
    if body.thread_id:
        # Server-resolved identity beats caller-supplied metadata: a thread
        # carrying a botName proves a bot-chat context.
        from app.gateway.deps import get_thread_store

        try:
            row = await get_thread_store(request).get(body.thread_id)
        except Exception:
            row = None
        thread_meta = None
        if row is not None:
            get_row = getattr(row, "get", None)
            thread_meta = get_row("metadata") if callable(get_row) else getattr(row, "metadata", None)
        if isinstance(thread_meta, dict) and thread_meta.get("botName"):
            metadata = {**metadata, "botName": thread_meta["botName"]}

    def _send():
        from alpha.bots.dm import send_dm

        return send_dm(key, body.target, body.message, thread_metadata=metadata).to_dict()

    return await asyncio.to_thread(_send)


@router.get("/{name}/inbox", summary="List a bot's DM inbox")
async def list_inbox(name: str, unread_only: bool = False, limit: int = 50) -> dict:
    key = _validate_bot_name(name)
    limit = max(1, min(limit, 200))

    def _list():
        from alpha.bots.inbox import get_bot_inbox

        box = get_bot_inbox(key)
        return {"messages": [m.to_dict() for m in box.list(unread_only=unread_only, limit=limit)], "unread_count": box.unread_count()}

    return await asyncio.to_thread(_list)


@router.post("/{name}/inbox/{delivery_id}/ack", summary="Ack a DM as handled")
async def ack_inbox_message(name: str, delivery_id: str, request: Request) -> dict:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)

    def _ack():
        from alpha.bots.inbox import get_bot_inbox

        msg = get_bot_inbox(key).ack(delivery_id)
        return msg.to_dict() if msg else None

    result = await asyncio.to_thread(_ack)
    if result is None:
        raise HTTPException(status_code=404, detail=f"DM '{delivery_id}' not found for bot '{key}'.")
    return result


@router.get("/{name}/dm-schema", summary="message_agent tool schema for bot chats")
async def dm_schema(name: str) -> dict:
    key = _validate_bot_name(name)

    def _schema():
        from alpha.bots.dm import build_roster_snippet, message_agent_tool_schema
        from alpha.bots.registry import get_bot_registry

        profiles = [{"name": b.name, "role": b.role} for b in get_bot_registry().list_bots() if b.name != key]
        return {"schema": message_agent_tool_schema(), "roster_snippet": build_roster_snippet(profiles)}

    return await asyncio.to_thread(_schema)


@router.get("/{name}/chat", summary="Canonical Bot Chat thread plus inbox summary")
async def bot_chat(name: str, limit: int = 20) -> dict:
    """One canonical DM-visible conversation per bot (idempotent thread id)."""
    key = _validate_bot_name(name)
    limit = max(1, min(limit, 100))

    def _chat():
        from alpha.bots.dm import canonical_bot_chat_id
        from alpha.bots.inbox import get_bot_inbox

        bot = _registry().get_bot(key)
        if bot is None:
            return None
        box = get_bot_inbox(key)
        return {
            "bot_name": key,
            "canonical_thread_id": canonical_bot_chat_id(key),
            "unread_count": box.unread_count(),
            "recent": [m.to_dict() for m in box.list(limit=limit)],
        }

    result = await asyncio.to_thread(_chat)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")
    return result


@router.post("/{name}/soul/backfill", summary="Backfill DM protocol into roster SOULs")
async def backfill_soul_protocol(name: str, request: Request) -> dict:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)

    def _backfill():
        from alpha.bots.dm import backfill_roster_profiles, ensure_messaging_section

        if key == "all":
            return {"updated": backfill_roster_profiles(registry=_registry())}
        bot = _registry().get_bot(key)
        if bot is None:
            return None
        updated = ensure_messaging_section(bot.soul, key)
        if updated != bot.soul:
            _registry().update_bot(key, soul=updated, bump_version=False)
            return {"updated": [key]}
        return {"updated": []}

    result = await asyncio.to_thread(_backfill)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")
    return result
