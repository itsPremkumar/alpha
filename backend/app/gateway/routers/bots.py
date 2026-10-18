"""First-class Bot profiles API.

Bots are autonomous specialist teammates (architect/coder/reviewer/tester/
researcher by default, auto-provisioned on demand). This router makes the
harness-layer BotRegistry a real system part: list/get/ensure/update with
capability-epoch fingerprints, ALPHA_HOME-aware persistence, and
blocking-IO offload so Gateway event-loop rules hold.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from alpha.bots.inbox import roster_activity
from alpha.bots.profile import _now
from alpha.persistence.run.model import RunRow
from alpha.persistence.thread_meta.model import ThreadMetaRow
from app.gateway.authz import require_permission
from app.gateway.deps import require_admin_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bots", tags=["bots"])
_ADMIN_REQUIRED_DETAIL = "Admin privileges are required to manage bots."

_BOT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: One real bot-scoped run, no tools, one short answer: the cheapest probe that
#: still exercises the whole loop (assembly → model → reply) for this bot.
_SMOKE_PROMPT = "Smoke test: reply with one short sentence confirming you are operational."

#: The smoke route waits for the run to reach a terminal state, bounded so a
#: stalled model call cannot hold the request forever. On the deadline the run
#: is interrupted and the smoke reports ``timeout`` — never a fabricated verdict.
_SMOKE_TIMEOUT_SECONDS = 180.0
_SMOKE_POLL_SECONDS = 0.5


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
    stats = data.get("task_stats") or {}
    recorded_runs = int(stats.get("total_runs", 0) or 0)
    # Zero recorded runs => reputation is UNVERIFIED: report None with an
    # explicit disclosure instead of a fabricated perfect 1.0 default.
    # Never coerce None to 0 or 1.0 at render time.
    if recorded_runs > 0:
        reputation_score = data.get("reputation_score")
        reputation_basis = f"based on {recorded_runs} recorded run(s)"
    else:
        reputation_score = None
        reputation_basis = "no recorded runs — unverified"
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    smoke = metadata.get("smoke") if isinstance(metadata.get("smoke"), dict) else None
    # Only a passing smoke makes a bot "alive": a profile with no recorded
    # smoke is "created · unverified", never alive by default.
    alive = bool(smoke and smoke.get("status") == "passed")
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
        "smoke": smoke,
        "alive": alive,
        "department": data.get("department", "engineering"),
        "reports_to": data.get("reports_to"),
        "responsibilities": data.get("responsibilities", []),
        "capabilities": data.get("capabilities", []),
        "heartbeat": data.get("heartbeat"),
        "succession_fallback": data.get("succession_fallback"),
        "reputation_score": reputation_score,
        "reputation_basis": reputation_basis,
        "task_stats": data.get("task_stats", {}),
        "routines": data.get("routines", []),
        "model_config": data.get("model_config") or {},
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def _registry():
    from alpha.bots.registry import get_bot_registry

    return get_bot_registry()


def _forge_result_to_response(result) -> dict:
    """Project a harness-layer ``ForgeResult`` onto the wire.

    ``alive`` is the load-bearing field: it is true ONLY when the forge ran a
    smoke probe and that probe answered. A clean build with no probe supplied
    is ``alive: false`` with ``smoke_tested: false`` — "created · unverified",
    never "alive". ``steps``/``warnings``/``rollback_report`` travel with the
    result so a refusal that rolled back is readable without a log dive.
    """
    return {
        "ok": bool(result.ok),
        "name": result.name,
        "reason": result.reason,
        "alive": bool(result.ok and result.smoke_tested),
        "smoke_tested": bool(result.smoke_tested),
        "rolled_back": bool(result.rolled_back),
        "steps": result.step_names,
        "warnings": list(result.warnings),
        "rollback_report": list(result.rollback_report),
        "elapsed_seconds": round(float(result.elapsed_seconds), 3),
        "reply": result.reply(),
    }


def _persist_smoke_outcome(bot_name: str, smoke: dict) -> None:
    """Record the smoke outcome on the profile so the badge survives a reload.

    ``metadata`` is the profile's own free-form slot (round-tripped by
    ``BotProfile.to_dict``/``from_dict``), so "created · unverified" becomes
    "smoke passed" on the next GET without a schema change. Best-effort: a
    roster write failure must not turn an observed smoke into a 500.
    """
    registry = _registry()
    bot = registry.get_bot(bot_name)
    if bot is None:
        return
    bot.metadata = {**(bot.metadata or {}), "smoke": smoke}
    registry.register(bot)


async def _await_run_terminal(run_mgr, run_id: str, *, timeout_seconds: float):
    """Poll a run record until it leaves pending/running — or the deadline passes.

    Returns the last observed record (including one that is still running at
    the deadline, so the caller can report ``timeout`` honestly) or ``None``
    when the record cannot be observed at all.
    """
    from alpha.runtime.runs.schemas import RunStatus

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        record = await run_mgr.get(run_id)
        if record is None:
            return None
        if record.status not in (RunStatus.pending, RunStatus.running):
            return record
        if time.monotonic() >= deadline:
            return record
        await asyncio.sleep(_SMOKE_POLL_SECONDS)


async def _run_bot_smoke(bot_name: str, request: Request) -> dict:
    """Run ONE real bot-scoped run and return the observed terminal result.

    The run is started through the scheduler's internal launcher — the same
    admission path every scheduled run uses — with ``assistant_id`` bound to
    the bot under test, so the probe exercises the bot's real assembly, model
    and tool path rather than a simulation. It lands on
    ``canonical_bot_chat_id`` — the bot's own chat — so the evidence sits
    where the bot's other work lives.
    """
    from alpha.bots.dm import canonical_bot_chat_id
    from alpha.runtime.runs.schemas import RunStatus
    from app.gateway.deps import get_run_manager
    from app.gateway.services import launch_scheduled_thread_run

    thread_id = canonical_bot_chat_id(bot_name)
    owner = getattr(getattr(request, "state", None), "user", None)
    owner_user_id = str(owner.id) if getattr(owner, "id", None) is not None else None
    launch = await launch_scheduled_thread_run(
        thread_id=thread_id,
        assistant_id=bot_name,
        prompt=_SMOKE_PROMPT,
        app=request.app,
        owner_user_id=owner_user_id,
        metadata={"bot_mode_smoke": True},
    )
    run_id = str(launch.get("run_id") or "")
    run_mgr = get_run_manager(request)
    record = await _await_run_terminal(run_mgr, run_id, timeout_seconds=_SMOKE_TIMEOUT_SECONDS)

    if record is None:
        return {
            "bot_name": bot_name,
            "run_id": run_id,
            "thread_id": thread_id,
            "status": "failed",
            "observed_status": "absent",
            "detail": "the launched run could not be observed in the run store",
            "answered": False,
            "reply": None,
            "message_count": 0,
        }

    observed_status = str(record.status)
    if record.status == RunStatus.success:
        verdict = "passed"
        detail = None
    elif record.status in (RunStatus.pending, RunStatus.running):
        verdict = "timeout"
        detail = f"still {observed_status} after {_SMOKE_TIMEOUT_SECONDS:.0f}s — the run was interrupted"
        try:
            await run_mgr.cancel(run_id, action="interrupt")
        except Exception:  # noqa: BLE001 — the deadline verdict stands regardless
            logger.warning("Smoke run %s could not be interrupted after the deadline.", run_id, exc_info=True)
    else:
        verdict = "failed"
        detail = record.error or record.stop_reason or f"the run ended as {observed_status}"

    reply = (record.last_ai_message or "").strip()
    return {
        "bot_name": bot_name,
        "run_id": run_id,
        "thread_id": thread_id,
        "status": verdict,
        "observed_status": observed_status,
        "detail": detail,
        "answered": bool(reply),
        "reply": reply[:1000] or None,
        "message_count": int(record.message_count or 0),
    }


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


class BotCreateRequest(BaseModel):
    """The forge's create contract, mirroring ``forge_bot``'s own parameters.

    ``role`` and ``template`` may be combined: the template supplies the
    display name / role / avatar / department / responsibilities it owns, and
    the explicit fields override. Unknown templates and models that are not
    declared in ``config.yaml models[]`` are refused by the forge with a
    reason (``ok: false``), never silently ignored.
    """

    name: str = Field(min_length=1, max_length=64, description="Bot handle: letters, digits, '_' or '-'")
    role: str = Field(default="", max_length=200, description="One-line specialty")
    template: str | None = Field(default=None, max_length=64)
    display_name: str | None = Field(default=None, max_length=100)
    department: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=200, description="Must be declared in config.yaml models[]")
    skills: list[str] | None = Field(default=None, max_length=100)
    capabilities: list[str] | None = Field(default=None, max_length=100)
    allow_overlap: bool = Field(default=False, description="Operator override for the duplicate-role refusal")
    workspace_survey: bool = Field(default=True, description="Survey the workspace at birth and write it into the bot's memory")


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
    # NOTE: ``model_config`` is deliberately NOT writable through this generic
    # PATCH. It has its own validated route (``PUT /{name}/model-config``) so a
    # fail-closed validation result (422 + ``detail.issues``) is the only write
    # path; a name that is not in ``config.yaml models[]`` must never land here.


class BotModelConfigRequest(BaseModel):
    """Raw ``model_config`` payload. Validated server-side against ``models[]``."""

    config: dict = Field(..., description="The model_config block to validate/store")


class BotModelConfigPreviewRequest(BaseModel):
    """Validate + resolve a candidate block **without** saving it."""

    config: dict = Field(..., description="The candidate model_config block")
    request_model: str | None = Field(default=None, max_length=200, description="Simulate a request-level model selection")
    bot_model: str | None = Field(default=None, max_length=200, description="Simulate the bot's plain `model` field")
    custom_agent_model: str | None = Field(default=None, max_length=200, description="Simulate a bound custom agent's model")


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


@router.post("", status_code=200, summary="Forge a new bot (transactional)")
async def create_bot(request: Request, body: BotCreateRequest) -> dict:
    """Create one bot through the transactional forge.

    The forge never raises — a refusal is a *result*, and its ``steps`` and
    ``rollback_report`` are part of what the caller must see, so this route
    answers 200 with ``ok: false`` and the reason instead of translating the
    refusal into an error that would drop them. Callers read ``ok``.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(body.name)
    if not body.role.strip() and not body.template:
        raise _validation_error("Provide a role, a template, or both.")

    def _forge():
        from alpha.bots.forge import forge_bot

        return forge_bot(
            name=key,
            role=body.role,
            registry=_registry(),
            template=body.template,
            display_name=body.display_name,
            department=body.department,
            model=body.model,
            skills=body.skills,
            capabilities=body.capabilities,
            allow_overlap=body.allow_overlap,
            workspace_survey=body.workspace_survey,
        )

    result = await asyncio.to_thread(_forge)
    return _forge_result_to_response(result)


@router.post("/{name}/smoke", summary="Run a real bot-scoped smoke run and record the observed result")
async def smoke_bot(name: str, request: Request) -> dict:
    """Prove a bot works end to end — or say plainly that it did not.

    One REAL bot-scoped run on the bot's canonical chat thread; ``alive`` is
    true only when that run reached ``success``, the run system's own terminal
    verdict. The outcome (including the run id) is persisted on the profile,
    so the UI badge progresses from ``created · unverified`` to
    ``smoke passed`` without re-running — and a bot is never reported alive
    without one.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)

    def _get():
        return _registry().get_bot(key)

    bot = await asyncio.to_thread(_get)
    if bot is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")

    observed = await _run_bot_smoke(key, request)
    smoke = {
        "status": observed["status"],
        "run_id": observed["run_id"],
        "thread_id": observed["thread_id"],
        "observed_status": observed["observed_status"],
        "answered": observed["answered"],
        "detail": observed["detail"],
        "at": datetime.now(UTC).isoformat(),
    }
    await asyncio.to_thread(_persist_smoke_outcome, key, smoke)
    return {
        "ok": observed["observed_status"] not in ("absent", "pending", "running"),
        "name": key,
        "alive": observed["status"] == "passed",
        "smoke": smoke,
        "run_id": observed["run_id"],
        "thread_id": observed["thread_id"],
        "reply": observed["reply"],
        "message_count": observed["message_count"],
    }


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


@router.get("/working", summary="Which bots are working right now, and on what")
async def get_bots_working() -> dict:
    """Live work attributed to each bot: the answer `health/overview` cannot give.

    Why this is a separate route
    ----------------------------
    `alpha.bots.health` answers liveness from heartbeats, and a heartbeat is
    only written by `POST /{name}/heartbeat` — which nothing in a stock
    deployment calls. Measured on a live install: 96 bots, 73 "healthy", and
    every single row reporting "no heartbeat recorded". A fleet view that stops
    there says "responsive, no task reported" about a bot that is mid-run.

    The run store is the source that does know: a run row records which
    assistant the Gateway was asked to run, and `ChatView` sends
    `assistant_id = activeBot.name` for a bot chat, so a non-terminal run whose
    `assistant_id` names a bot *is* that bot working on that thread. This route
    reads it, so the UI can name the run, the thread and the elapsed time
    instead of asserting a presence it cannot see.

    Attribution rules, and the one this route refuses
    ------------------------------------------------
    - `RunRow.assistant_id` is the only binding used. It is the value the caller
      asked the Gateway to run, and the frontend sends the roster bot's `name`
      there for a bot chat — the same convention `scripts/fleet_task_assign.py`
      follows.
    - Keys are folded to `name.strip().lower()` because
      `alpha.bots.health.evaluate_liveness` keys its rows the same way, so the
      two payloads can be joined on one key without a second mapping.
    - A run with no `assistant_id` is **not** attributed to anybody. Guessing
      from the thread's own `assistant_id` would put a run on a bot that was
      never asked to do it, which is the failure `deps._activity_bot_name`
      documents for the room ledger; an unattributed run is counted instead.
    - Only `operation_kind == "run"` rows count. Checkpoint writes, artifact
      writes and branches are internal bookkeeping, not an agent working.
    - The report is **fleet-wide, not owner-scoped**, matching the rest of this
      router (the registry reads and `/health/overview` are fleet-wide). A
      caller that needs an owner-scoped view owns the filtering itself.

    Failure shapes
    --------------
    No SQL backend (``database.backend: memory``) is a **503 naming the
    backend**, the same refusal `routers/console` gives: there is no run
    history to report on, and answering "0 bots working" would claim the store
    measured an idle fleet. A store that exists but fails the read answers 200
    with ``reported: false`` plus its reason, so a surface can keep rendering
    the liveness it does have instead of blanking.
    """
    from alpha.persistence.engine import get_session_factory

    # Imported rather than re-typed so this route and the console reporting layer
    # cannot disagree about which statuses are still live work — the same rule
    # `PublicBodyLimitMiddleware` follows for routers.peer_network's
    # `_PUBLIC_BODY_LIMITED_PATHS`.
    from app.gateway.routers.console import _ACTIVE_STATUSES, _as_utc

    sf = get_session_factory()
    if sf is None:
        raise HTTPException(
            status_code=503,
            detail=("the run store is unavailable: database.backend is memory, which persists no run history, so which bots are working right now cannot be measured"),
        )

    try:
        async with sf() as session:
            result = await session.execute(
                select(
                    RunRow.run_id,
                    RunRow.thread_id,
                    RunRow.assistant_id,
                    RunRow.status,
                    RunRow.model_name,
                    RunRow.created_at,
                    ThreadMetaRow.display_name,
                )
                .join(ThreadMetaRow, ThreadMetaRow.thread_id == RunRow.thread_id, isouter=True)
                .where(RunRow.operation_kind == "run", RunRow.status.in_(_ACTIVE_STATUSES))
                .order_by(RunRow.created_at.desc(), RunRow.run_id.desc())
            )
            rows = list(result.all())
    except Exception as exc:
        # The store exists; this particular read failed. That is not evidence of
        # an idle fleet, so it is a 200 carrying the reason rather than a zero.
        logger.warning("bots working: run store read failed", exc_info=True)
        return {
            "reported": False,
            "reason": f"the run store could not be read: {type(exc).__name__}",
            "generated_at": None,
            "active_runs_by_bot": {},
            "counts": {
                "active_runs": None,
                "attributed_runs": None,
                "unattributed_runs": None,
                "bots_working": None,
            },
        }

    now = datetime.now(UTC)
    by_bot: dict[str, list[dict]] = {}
    attributed = 0
    unattributed = 0
    for run_id, thread_id, assistant_id, status, model_name, created_at, display_name in rows:
        binding = str(assistant_id).strip() if assistant_id else ""
        if not binding:
            unattributed += 1
            continue
        attributed += 1
        # SQLite round-trips these naive and Postgres returns them aware, so the
        # timestamp is normalised before it is compared to anything — the same
        # `_as_utc` rule the console reporting layer applies. Skipping it makes
        # every elapsed time wrong by the host's UTC offset.
        started = _as_utc(created_at)
        by_bot.setdefault(binding.lower(), []).append(
            {
                "bot_name": binding,
                "run_id": str(run_id),
                "thread_id": str(thread_id) if thread_id else None,
                # Null when the thread carries no display name: the UI says
                # "untitled thread", never invents one.
                "thread_title": display_name,
                # The store's own word, verbatim: a status this build does not
                # know is shown rather than snapped to "running".
                "status": str(status),
                "model_name": model_name,
                "started_at": started.isoformat() if started is not None else None,
                # Measured here because a live run has no duration yet; null
                # only when the row carries no start time at all.
                "elapsed_seconds": round((now - started).total_seconds(), 1) if started is not None else None,
            }
        )

    return {
        "reported": True,
        "reason": None,
        "generated_at": now.isoformat(),
        "active_runs_by_bot": by_bot,
        "counts": {
            "active_runs": len(rows),
            "attributed_runs": attributed,
            "unattributed_runs": unattributed,
            "bots_working": len(by_bot),
        },
    }


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
async def list_bots(status: str | None = None, department: str | None = None, activity: bool = False) -> dict:
    if status is not None:
        from alpha.bots.templates import BOT_STATUSES

        if status not in BOT_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {list(BOT_STATUSES)}")

    def _list():
        rows = [_bot_to_response(b) for b in _registry().list_bots(status=status, department=department)]
        # Opt-in: the projection adds a per-Bot inbox read and a secret scan of
        # the newest body, which a polled roster read should not pay for by
        # default. `roster_activity` never raises, so one broken inbox degrades
        # its own row instead of the response.
        if activity:
            for row in rows:
                row.update(roster_activity(row["name"]))
        return rows

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


@router.get("/{name}/mode", summary="The effective bot-mode contract (what this bot may do right now)")
async def get_bot_mode(name: str) -> dict:
    """One answer for "what may this bot do right now".

    Server-side resolution only: capabilities come from the roster profile
    intersected with the server-owned ceiling — never from a request payload.
    The response carries per-field provenance plus an ``enforcement``
    disclosure, so a declared-but-unwired scope never reads as a restriction
    that exists.
    """
    key = _validate_bot_name(name)

    def _mode():
        from alpha.bots.mode import resolve_bot_mode

        return resolve_bot_mode(bot_name=key, registry=_registry())

    mode = await asyncio.to_thread(_mode)
    if mode is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")
    return mode.to_dict()


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


# ── Per-bot model configuration ────────────────────────────────────────────
#
# Declared models live in ``config.yaml`` ``models[]`` and nowhere else: these
# routes only ever *name* them. Every mutation validates against that set and
# fails with the full issue list, so the detail panel can render every problem
# in one save instead of one round trip each.


def _known_model_names() -> set[str]:
    from alpha.config import get_app_config

    return {m.name for m in get_app_config().models}


def _model_config_issues(exc) -> list[dict]:
    return [issue.to_dict() for issue in getattr(exc, "issues", [])]


def _plan_view(cfg, *, bot_name: str, request_model=None, bot_model=None, custom_agent_model=None) -> dict:
    """Resolve + describe a plan for preview/PUT responses.

    ``resolve_model_plan`` reads ``model``/``model_config`` off a bot object;
    preview inputs are plain strings, so a trivial namespace stands in for the
    profile. ``bot_model_config`` is passed explicitly because a preview may
    show a *candidate* block that is not stored yet.
    """
    from types import SimpleNamespace

    from alpha.bots.model_config import describe_model_plan, resolve_model_plan

    plan = resolve_model_plan(
        request_model=request_model,
        bot=SimpleNamespace(model=bot_model, model_config=None),
        custom_agent_model=custom_agent_model,
        bot_model_config=cfg,
    )
    return describe_model_plan(plan, bot_name=bot_name)


@router.get("/{name}/model-config", summary="Full-detail per-bot model configuration")
async def get_bot_model_config(name: str) -> dict:
    """The stored block plus the resolved plan (with per-value provenance).

    The response carries the precedence ladder and every limit so the UI can
    explain *why* a value won, not only what won.
    """
    from alpha.bots.model_config import BotModelConfigError, validate_bot_model_config

    key = _validate_bot_name(name)

    def _get():
        bot = _registry().get_bot(key)
        if bot is None:
            return None
        raw = dict(getattr(bot, "model_config", None) or {})
        issues: list[dict] = []
        cfg = None
        try:
            cfg = validate_bot_model_config(raw, known_models=_known_model_names(), field_prefix="model_config")
        except BotModelConfigError as exc:
            issues = _model_config_issues(exc)
        return {
            "name": bot.name,
            "model": bot.model,
            "config": raw,
            "resolved": _plan_view(cfg, bot_name=bot.name, bot_model=bot.model),
            "issues": issues,
            "valid": not issues,
            # The picker may only offer names ``models[]`` declares, so the
            # validated set travels with the read instead of the UI re-deriving
            # it from ``GET /api/models`` (a different, wider surface).
            "known_models": sorted(_known_model_names()),
        }

    result = await asyncio.to_thread(_get)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")
    return result


@router.put("/{name}/model-config", summary="Set the per-bot model configuration")
async def put_bot_model_config(name: str, request: Request, body: BotModelConfigRequest) -> dict:
    """Validate against ``models[]`` and store; every issue is reported at once.

    An empty ``config`` clears the block (inherit-everything), which is the
    same "empty means unset" convention the rest of the profile uses.
    """
    from alpha.bots.model_config import BotModelConfigError, validate_bot_model_config

    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)

    def _put():
        bot = _registry().get_bot(key)
        if bot is None:
            return None, None
        raw = dict(body.config or {})
        cfg = validate_bot_model_config(raw, known_models=_known_model_names(), field_prefix="model_config")
        # Store the canonical form (trimmed names, normalised effort rungs),
        # not the caller's spelling, so the epoch hashes what will be executed.
        _registry().update_bot(key, model_config=cfg.to_dict())
        return _registry().get_bot(key), cfg

    try:
        bot, cfg = await asyncio.to_thread(_put)
    except BotModelConfigError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "issues": _model_config_issues(exc)}) from exc
    if bot is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")
    return {
        "name": bot.name,
        "model": bot.model,
        "config": cfg.to_dict() if cfg else {},
        "resolved": _plan_view(cfg, bot_name=bot.name, bot_model=bot.model),
        "issues": [],
        "valid": True,
    }


@router.delete("/{name}/model-config", summary="Clear the per-bot model configuration")
async def delete_bot_model_config(name: str, request: Request) -> dict:
    """Remove the block: the bot inherits primary/chain/sampling from global config."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)

    def _clear():
        return _registry().update_bot(key, model_config={})

    bot = await asyncio.to_thread(_clear)
    if bot is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")
    return {"name": bot.name, "config": {}, "cleared": True}


@router.post("/{name}/model-config/preview", summary="Preview a candidate model config without saving")
async def preview_bot_model_config(name: str, body: BotModelConfigPreviewRequest) -> dict:
    """Validate a candidate block and show the plan it would resolve to.

    Read-only by contract: nothing is stored, so the UI can resolve errors and
    see the winning chain before the operator commits the edit.
    """
    from alpha.bots.model_config import BotModelConfigError, validate_bot_model_config

    key = _validate_bot_name(name)
    raw = dict(body.config or {})
    try:
        cfg = validate_bot_model_config(raw, known_models=_known_model_names(), field_prefix="model_config")
        issues: list[dict] = []
    except BotModelConfigError as exc:
        cfg = None
        issues = _model_config_issues(exc)

    return {
        "name": key,
        "config": raw,
        "resolved": _plan_view(
            cfg,
            bot_name=key,
            request_model=body.request_model,
            bot_model=body.bot_model,
            custom_agent_model=body.custom_agent_model,
        ),
        "issues": issues,
        "valid": not issues,
    }


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
    prompt: str = Field(default="", max_length=8000)


@router.post("/route-task", summary="Route a task type to a model chain")
async def route_task_endpoint(body: RouteTaskRequest) -> dict:
    """Map task type to category chain with measured re-ranking (local-first).

    When ``prompt`` is supplied, System One additionally decides whether the
    request needs the flagship model; escalation only ever moves up the ladder,
    so a bad signal costs money and never quality. Omitting ``prompt`` (or
    System One being unavailable) reproduces the previous behaviour exactly.
    """
    from alpha.models.task_router import aroute_task

    return (await aroute_task(body.task_type, body.prompt)).to_dict()


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

    from alpha.runtime.estop import get_estop_manager

    if get_estop_manager().is_engaged():
        raise HTTPException(status_code=409, detail="Fleet ESTOP active: Bot DM refused")

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


class BotCloneApiRequest(BaseModel):
    target_name: str | None = None
    mode: str = "specialist_fork"
    specialist_directive: str | None = None
    skills_to_add: list[str] = Field(default_factory=list)
    tools_to_add: list[str] = Field(default_factory=list)
    model_override: str | None = None
    department: str | None = None
    ttl_seconds: int = 3600


class BotEvolveApiRequest(BaseModel):
    improvement_directive: str = Field(..., min_length=3)
    performance_delta: dict = Field(default_factory=dict)
    promoted_skills: list[str] = Field(default_factory=list)


@router.post("/{name}/clone-engine", summary="Clone or fork a bot via the clone engine")
async def clone_bot_endpoint(name: str, body: BotCloneApiRequest, request: Request) -> dict:
    if isinstance(request, Request):
        await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)
    from alpha.bots.cloning import CloneMode, get_bot_clone_engine

    def _clone():
        engine = get_bot_clone_engine()
        mode_val = CloneMode(body.mode) if body.mode in [m.value for m in CloneMode] else CloneMode.SPECIALIST_FORK
        cloned = engine.clone_bot(
            source_name=key,
            target_name=body.target_name,
            mode=mode_val,
            specialist_directive=body.specialist_directive,
            skills_to_add=body.skills_to_add,
            tools_to_add=body.tools_to_add,
            model_override=body.model_override,
            department=body.department,
            ttl_seconds=body.ttl_seconds,
        )
        return _bot_to_response(cloned)

    try:
        return await asyncio.to_thread(_clone)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{name}/evolve", summary="Evolve a bot to a new generation")
async def evolve_bot_endpoint(name: str, body: BotEvolveApiRequest, request: Request) -> dict:
    if isinstance(request, Request):
        await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)
    from alpha.bots.cloning import get_bot_clone_engine

    def _evolve():
        engine = get_bot_clone_engine()
        evolved = engine.evolve_bot(
            source_name=key,
            performance_delta=body.performance_delta,
            improvement_directive=body.improvement_directive,
            promoted_skills=body.promoted_skills,
        )
        return _bot_to_response(evolved)

    try:
        return await asyncio.to_thread(_evolve)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class BotWorkflowRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=100_000)
    context: dict = Field(default_factory=dict)
    initial_state: dict = Field(default_factory=dict)
    max_steps: int = Field(default=40, ge=1, le=200)
    auto_execute: bool = True


@router.post("/{name}/workflow", summary="Compile or execute a bot-mode dynamic workflow")
@require_permission("runs", "create")
async def run_bot_workflow_endpoint(
    name: str,
    body: BotWorkflowRequest,
    request: Request,
) -> dict:
    """Run the shared DWE in bot mode without pretending a claim is execution.

    The bot profile is server-validated before planning. A real bot executor
    must be installed in the process-owned executor registry before
    ``auto_execute`` can complete; otherwise the service returns the exact
    missing-executor failure instead of treating inbox acceptance as work.
    """
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    key = _validate_bot_name(name)
    profile = _registry().get_bot(key)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Bot '{key}' not found")
    if getattr(profile, "is_archived", False) or getattr(profile, "status", "active") == "archived":
        raise HTTPException(status_code=409, detail=f"Bot '{key}' is archived and cannot run workflows")

    # ── Fleet ESTOP & Alpha Mod Kernel Admission Gate ──
    from alpha.runtime.estop import get_estop_manager

    if get_estop_manager().is_engaged():
        raise HTTPException(status_code=409, detail=f"Fleet ESTOP active: Bot '{key}' cannot run workflows")

    from alpha.orchestrator.dynamic_service import DynamicRequest, DynamicWorkflowService
    from alpha.runtime.user_context import get_effective_user_id
    from app.gateway.routers.workflows import get_workflow_kernel

    owner = get_effective_user_id() if isinstance(request, Request) else None

    from alpha.mods.kernel import ModAdmissionError, get_mod_kernel, require_mod_admission
    from alpha.mods.types import AlphaEvent, CorrelationContext

    try:
        await require_mod_admission(
            get_mod_kernel(),
            AlphaEvent(
                name="bot.workflow_admit",
                payload={"bot_name": key, "prompt": body.prompt, "max_steps": body.max_steps},
                correlation=CorrelationContext.create(agent_id=key, metadata={"owner_id": owner}),
                source="gateway.routers.bots",
            ),
        )
    except ModAdmissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Mod Kernel evaluation error during bot workflow admission: %s", exc)

    # Use the Gateway-owned kernel/engine so the returned run is immediately
    # visible to the ordinary workflow read/step/cancel/approval/replay APIs.
    service = DynamicWorkflowService(kernel=get_workflow_kernel())

    def _run() -> dict:
        result = service.execute(
            DynamicRequest(
                prompt=body.prompt,
                mode="bot",
                context={**body.context, "bot_name": key, "owner_id": owner},
                initial_state={**body.initial_state, "bot_name": key},
                max_steps=body.max_steps,
                # A bot profile is not a digest projection.  ``alpha.local.bot``
                # is registered by ``alpha.orchestrator.executors`` and executes
                # each node as this bot (real model call, bot SOUL as the system
                # prompt).  It still refuses with a named reason on an
                # unknown/archived bot, an engaged ESTOP, or no configured model.
                default_executor="alpha.local.bot",
            ),
            auto_execute=body.auto_execute,
            provision=body.auto_execute,
        )
        return result.to_dict()

    try:
        return await asyncio.to_thread(_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Per-bot project membership
#
# The bot detail page needs "which projects is this bot on?". There was no way
# to ask that: `GET /projects` returns project rows without their crew, and
# `GET /projects/{id}/crew` requires an id you do not have yet. So the page
# would have had to fan out over every project and N+1 its crew -- or worse,
# guess.
#
# This resolves membership server-side by reading each project's crew, and it
# deliberately does NOT invent a per-bot memory store: cognitive memory in Alpha
# is project-scoped and owner-resolved, so the page aggregates the existing
# `GET /projects/{id}/memory` across these ids instead. Putting a model-supplied
# owner inside the cognitive-memory boundary is exactly what that boundary
# exists to prevent.
#
# Honesty rules for this payload:
#   - `projects` lists only projects where the bot is actually a member.
#   - A project whose crew cannot be read is reported in `unreadable` with its
#     reason rather than silently dropped, so "not on any project" stays
#     distinguishable from "we could not tell".
#   - `memory_scoped: "project"` is stated, not implied, because a caller
#     looking for per-bot memory would otherwise conclude none exists.
# ---------------------------------------------------------------------------


@router.get("/{name}/projects", summary="Projects this bot belongs to")
@require_permission("projects", "read")
async def bot_projects(name: str, request: Request) -> dict:
    from alpha.projects.crew import get_crew_service
    from app.gateway.deps import get_project_repo

    key = _validate_bot_name(name)

    # The repository read is async, so it stays on the event loop. Only the
    # blocking crew reconciliation goes to a thread - `crew_view` reconciles
    # against the group room as a side effect and touches the filesystem, so it
    # must not run on the loop. `_collect` is deliberately a *sync* function:
    # handing an `async def` to `asyncio.to_thread` returns an un-awaited
    # coroutine instead of a payload.
    rows = await get_project_repo(request).list()

    def _collect() -> dict:
        crew = get_crew_service()
        projects: list[dict] = []
        unreadable: list[dict] = []
        for row in rows:
            project_id = row["id"]
            try:
                view = crew.crew_view(project_id)
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                unreadable.append({"id": project_id, "name": row.get("name", ""), "reason": str(exc)})
                continue
            # `MemberBrief` exposes `bot_name` / `role_in_project` (not
            # `name` / `role`), and its membership key is the lowercased bot name
            # that `attach()` stored. Reading the wrong attribute here would make
            # every project look empty, so the field names are load-bearing.
            members = list(getattr(view, "members", None) or [])
            mine = [m for m in members if str(getattr(m, "bot_name", "")).lower() == key]
            if not mine:
                continue
            projects.append(
                {
                    "id": project_id,
                    "name": row.get("name", ""),
                    "status": row.get("status", ""),
                    "role": str(getattr(mine[0], "role_in_project", "") or ""),
                    "member_status": str(getattr(mine[0], "status", "") or ""),
                    "updated_at": row.get("updated_at") or None,
                }
            )
        return {
            "bot": key,
            "projects": projects,
            "unreadable": unreadable,
            "memory_scoped": "project",
            "memory_note": ("Alpha stores cognitive memory per project, not per bot. Read GET /api/projects/{id}/memory for each project listed here."),
        }

    return await asyncio.to_thread(_collect)


# ---------------------------------------------------------------------------
# Per-bot group membership
#
# The bot detail page needs "which rooms is this bot in, and why". `GET
# /api/groups/rooms` lists rooms without rosters and `GET /api/groups/rooms/
# {name}` needs a name you do not have yet, so membership was only answerable
# by fanning out over every room from the client.
#
# Membership is resolved **live per room** through `resolved_roster()` - the
# same projection the runner fans out to - because inheritance is a projection
# and never a stored copy: an inherited member has to be reported as inherited,
# not as somebody who was added to this room. Hence `origins`, which lists
# every roster bucket the bot appears in, verbatim, and `effective`, which says
# whether it can actually take part (an excluded or expired member is reported,
# not hidden).
#
# Honesty rules for this payload:
#   - only rooms where the bot appears in some bucket are listed.
#   - a room whose roster cannot be read goes in `unreadable` with its reason
#     rather than being dropped, so "in no rooms" stays distinguishable from
#     "we could not tell".
#   - the room list itself failing is 503, never an empty 200.
#   - `path` is one visibility-parent chain root-first with the room appended
#     (`breadcrumbs` deliberately excludes the room itself), so a root room
#     renders as `[own name]` rather than an empty path.
# ---------------------------------------------------------------------------


@router.get("/{name}/groups", summary="Groups this bot belongs to")
@require_permission("threads", "read")
async def bot_groups(name: str, request: Request) -> dict:
    from alpha.groups.service import get_group_chat_service

    key = _validate_bot_name(name)

    def _collect() -> dict:
        svc = get_group_chat_service()
        try:
            all_rooms = svc.list_rooms()
        except Exception as exc:  # noqa: BLE001 - an unreadable store is 503, not "no groups"
            raise HTTPException(status_code=503, detail=f"Group store unreadable: {exc}") from exc

        rooms: list[dict] = []
        unreadable: list[dict] = []
        for room in all_rooms:
            try:
                roster = svc.resolved_roster(room.name)
                crumbs = svc.breadcrumbs(room.name)
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                unreadable.append({"id": room.room_id, "name": room.name, "reason": str(exc)})
                continue
            buckets = (
                ("direct", roster.direct),
                ("rule_matched", roster.rule_matched),
                ("inherited", roster.inherited),
                ("excluded", roster.excluded),
                ("expired", roster.expired),
            )
            origins = [label for label, members in buckets if key in members]
            if not origins:
                continue
            path = [str(c.get("name") or c.get("room_id") or "") for c in crumbs]
            path.append(room.name)
            rooms.append(
                {
                    "name": room.name,
                    "room_id": room.room_id,
                    "project_id": room.project_id or None,
                    "mode": str(room.mode),
                    "lifecycle": str(room.lifecycle),
                    "origins": origins,
                    "effective": key in roster.effective,
                    "direct_count": roster.direct_count,
                    "effective_count": roster.effective_count,
                    "parent_count": len(room.parent_ids or []),
                    "path": path,
                }
            )
        return {
            "bot": key,
            "rooms": rooms,
            "unreadable": unreadable,
            "rooms_checked": len(all_rooms),
            "note": ("Membership is resolved live per room: direct, rule-matched and inherited, minus excluded and expired. `effective` reports whether this bot can actually take part in the room."),
        }

    return await asyncio.to_thread(_collect)


# ---------------------------------------------------------------------------
# Per-bot company employment
#
# `GET /api/companies` lists organizations without their employments and
# `GET /api/companies/{id}` needs an id you do not have yet, so "which
# organizations employ this bot" was unanswerable without fanning out over
# every company. Employment is indexed on `Company.employments[].agent_handle`,
# which makes this the one read that does not need a company id first.
#
# There is deliberately no `require_permission` layer here: the Company OS
# router carries none either. Tenancy is the boundary, and it is resolved
# through the very same `_owner()` so this route cannot become a second
# owner-derivation path.
#
# Honesty rules for this payload:
#   - a terminated employment keeps its own `status`; it is never dropped, so
#     "never worked here" stays distinguishable from "no longer works here".
#   - an unreadable company store is 503 carrying the store's own reason, never
#     an empty list - a corrupt store and an owner with no companies drive
#     opposite decisions.
#   - `role` / `unit` are `null` when the ids dangle, not fabricated stubs.
# ---------------------------------------------------------------------------


@router.get("/{name}/companies", summary="Companies that employ this bot")
async def bot_companies(name: str, request: Request) -> dict:
    from alpha.company_os.service import get_company_service
    from alpha.company_os.store import CompanyStoreUnreadable
    from app.gateway.routers.companies import _owner

    key = _validate_bot_name(name)
    owner = _owner(request)

    def _collect() -> dict:
        service = get_company_service()
        try:
            companies = service.list_companies(owner)
        except CompanyStoreUnreadable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        employments: list[dict] = []
        for company in companies:
            hit = next(
                (e for e in company.employments if str(e.agent_handle or "").strip().lower() == key),
                None,
            )
            if hit is None:
                continue
            role = company.role(hit.role_id)
            unit = company.unit(hit.unit_id)
            employments.append(
                {
                    "company_id": company.company_id,
                    "name": company.name,
                    "archetype": company.archetype,
                    "state": str(company.state),
                    "created_at": company.created_at,
                    "employment": hit.model_dump(mode="json"),
                    "role": role.model_dump(mode="json") if role is not None else None,
                    "unit": unit.model_dump(mode="json") if unit is not None else None,
                }
            )
        return {
            "bot": key,
            "companies": employments,
            "companies_checked": len(companies),
            "note": ("Employment is indexed on `Company.employments[].agent_handle`; a past employment keeps its own `status` instead of being dropped, and `role` / `unit` are null when their ids dangle."),
        }

    return await asyncio.to_thread(_collect)
