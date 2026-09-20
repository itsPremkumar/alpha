"""Gateway REST Router for Autonomous AI Company & Perpetual Organization OS."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from alpha.company.organization import get_autonomous_company_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/company", tags=["company-os"])


class CompanyBootstrapRequest(BaseModel):
    prompt: str = Field(..., min_length=5, description="High-level goal or vision for the organization.")
    archetype: str | None = Field(default=None, description="Archetype: company, open_source, security_soc, research_lab, custom")
    owner: str = Field(default="human-owner", description="Owner authority identifier")
    duration_years: float = Field(default=5.0, ge=0.1, description="Target operational lifespan in years")


class StrategyReplanRequest(BaseModel):
    org_id: str | None = Field(default=None, description="Organization ID")
    directive: str = Field(..., min_length=3, description="Strategic pivot directive from owner")


class TransferResponsibilityRequest(BaseModel):
    org_id: str | None = Field(default=None, description="Organization ID")
    responsibility_id: str = Field(..., description="Target responsibility ID")
    reason: str = Field(default="Manual transfer initiated via gateway", description="Failover rationale")


class WorkDiscoveryRequest(BaseModel):
    org_id: str | None = Field(default=None, description="Organization ID")
    signals: list[dict[str, Any]] | None = Field(default=None, description="Telemetry, issue, or market signals")


class RetrospectiveRequest(BaseModel):
    org_id: str | None = Field(default=None, description="Organization ID")


@router.get("/archetypes")
async def list_archetypes():
    """Lists supported perpetual organization archetypes and their descriptions."""
    engine = get_autonomous_company_engine()
    return engine.list_archetypes()


@router.post("/bootstrap")
async def bootstrap_company(payload: CompanyBootstrapRequest):
    """Instantiates a complete autonomous organization with departments, Hermes bots, and responsibilities."""
    engine = get_autonomous_company_engine()
    state = engine.bootstrap_company(
        prompt=payload.prompt,
        archetype=payload.archetype,
        owner=payload.owner,
        duration_years=payload.duration_years,
    )
    return state.model_dump()


@router.get("/status")
async def get_company_status(org_id: str | None = None):
    """Retrieves organizational state, department hierarchy, and active workforce."""
    engine = get_autonomous_company_engine()
    if org_id:
        state = engine.get_company(org_id)
        if not state:
            raise HTTPException(status_code=404, detail=f"Organization '{org_id}' not found.")
        return state.model_dump()

    companies = engine.list_companies()
    if not companies:
        raise HTTPException(status_code=404, detail="No active organizations found. Bootstrap a company first.")
    return companies[0].model_dump()


@router.get("/executive-digest")
async def get_executive_digest(org_id: str | None = None):
    """Generates a high-level executive digest with health score, KPI progress, and explainability."""
    engine = get_autonomous_company_engine()
    target_id = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    digest = engine.get_executive_digest(target_id)
    return digest.model_dump()


@router.post("/discover-work")
async def discover_work(payload: WorkDiscoveryRequest):
    """Scans signals for new bugs, security items, and opportunities without inventing pointless work."""
    engine = get_autonomous_company_engine()
    target_id = payload.org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    disc_engine = engine.get_discovery_engine(target_id)
    if payload.signals is not None:
        signals = payload.signals
    else:
        signals = [
            {"title": "Automated security patch available for urllib3", "category": "security", "impact": 0.8, "urgency": 0.8},
            {"title": "Memory leak reported in async task queue worker", "category": "bug", "impact": 0.9, "urgency": 0.9},
        ]
    items, should_sleep = disc_engine.discover_from_sources(signals)
    return {
        "org_id": target_id,
        "discovered_items": [i.model_dump() for i in items],
        "workers_should_sleep": should_sleep,
    }


@router.get("/kpis")
async def list_kpis(org_id: str | None = None):
    """Lists organization KPIs, targets, and autonomous corrective tasks."""
    engine = get_autonomous_company_engine()
    target_id = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    kpi_engine = engine.get_kpi_engine(target_id)
    return {
        "org_id": target_id,
        "kpis": [k.model_dump() for k in kpi_engine.list_kpis()],
        "triggered_corrective_tasks": kpi_engine.get_triggered_tasks(),
    }


@router.post("/strategy/replan")
async def replan_strategy(payload: StrategyReplanRequest):
    """Conducts strategic impact analysis and realigns initiatives based on owner directive."""
    engine = get_autonomous_company_engine()
    target_id = payload.org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    report = engine.replan_strategy(target_id, payload.directive)
    return report.model_dump()


@router.post("/responsibilities/transfer")
async def transfer_responsibility(payload: TransferResponsibilityRequest):
    """Fails over or reassigns an organizational responsibility to backup worker."""
    engine = get_autonomous_company_engine()
    target_id = payload.org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    resp_engine = engine.get_responsibility_engine(target_id)
    binding = resp_engine.trigger_failover(payload.responsibility_id, payload.reason)
    return binding.model_dump()


@router.post("/retrospective")
async def run_retrospective(payload: RetrospectiveRequest):
    """Executes an autonomous retrospective and logs to the evolution journal."""
    engine = get_autonomous_company_engine()
    target_id = payload.org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    record = engine.run_retrospective(target_id)
    return record.model_dump()


@router.get("/evolution-journal")
async def get_evolution_journal(org_id: str | None = None):
    """Retrieves the self-improvement evolution journal."""
    engine = get_autonomous_company_engine()
    target_id = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    state = engine.get_company(target_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Organization '{target_id}' not found.")
    return [e.model_dump() for e in state.evolution_journal]


class ProductionLineSubmitRequest(BaseModel):
    title: str = Field(..., min_length=2, description="Feature title")
    feature_spec: str = Field(default="", description="Requirements or architecture specification")


class ProductionLineAdvanceRequest(BaseModel):
    run_id: str = Field(..., description="Production line run ID")
    stage_content: str = Field(default="", description="Generated artifact content for the stage")


class KanbanSyncRequest(BaseModel):
    org_id: str | None = Field(default=None, description="Organization ID")


@router.get("/swarm/bots")
@router.get("/hermes/bots")
async def get_hermes_bots(org_id: str | None = None):
    """Discovers and imports local specialist bot profiles."""
    engine = get_autonomous_company_engine()
    target_id = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if target_id:
        return engine.sync_hermes_bots(target_id)

    bridge = engine.get_hermes_bridge()
    local_bots = bridge.discover_local_bots()
    return {
        "hermes_installed": bridge.is_hermes_installed,
        "discovered_bots_count": len(local_bots),
        "bot_names": local_bots,
    }


@router.post("/production-line/submit")
async def submit_production_line_feature(payload: ProductionLineSubmitRequest):
    """Submits a new feature into the 8-stage enterprise production line."""
    engine = get_autonomous_company_engine()
    prod_line = engine.get_production_line()
    run = prod_line.submit_feature(title=payload.title, feature_spec=payload.feature_spec)
    return run.model_dump()


@router.post("/production-line/advance")
async def advance_production_line_stage(payload: ProductionLineAdvanceRequest):
    """Advances a feature through the 8-stage production pipeline."""
    engine = get_autonomous_company_engine()
    prod_line = engine.get_production_line()
    run, artifact = prod_line.advance_stage(run_id=payload.run_id, content=payload.stage_content)
    return {
        "run": run.model_dump(),
        "created_artifact": artifact.model_dump(),
    }


@router.post("/kanban/sync")
async def sync_kanban(payload: KanbanSyncRequest):
    """Synchronizes active company projects into enterprise SQLite kanban board."""
    engine = get_autonomous_company_engine()
    target_id = payload.org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    res = engine.sync_company_to_hermes_kanban(target_id)
    return res


@router.post("/{org_id}/pause")
async def pause_company(org_id: str):
    engine = get_autonomous_company_engine()
    state = engine.pause_company(org_id)
    return {"status": "paused", "org_id": org_id, "state": state.state.value}


@router.post("/{org_id}/resume")
async def resume_company(org_id: str):
    engine = get_autonomous_company_engine()
    state = engine.resume_company(org_id)
    return {"status": "resumed", "org_id": org_id, "state": state.state.value}


class CreateGroupChannelRequest(BaseModel):
    org_id: str | None = Field(default=None, description="Organization ID")
    channel_id: str = Field(..., min_length=2, description="Unique channel identifier (e.g. 'eng-squad')")
    name: str = Field(..., min_length=2, description="Channel display name (e.g. '#Engineering-Squad')")
    member_bot_names: list[str] = Field(default_factory=list, description="Initial list of bot member identities")
    description: str = Field(default="", description="Scope and purpose of channel")


class PostGroupMessageRequest(BaseModel):
    org_id: str | None = Field(default=None, description="Organization ID")
    channel_id: str = Field(default="all-hands", description="Target channel ID")
    sender_bot: str = Field(..., description="Sender bot identity")
    content: str = Field(..., min_length=1, description="Message text with optional @mentions")


class RecordPulseRequest(BaseModel):
    org_id: str | None = Field(default=None, description="Organization ID")
    bot_name: str = Field(..., description="Target bot name")
    status: str = Field(default="present", description="Liveness state: present, busy, idle")
    active_task_id: str | None = Field(default=None, description="Active task ID if currently working")


class CheckAttendanceRequest(BaseModel):
    org_id: str | None = Field(default=None, description="Organization ID")
    timeout_seconds: float = Field(default=120.0, description="Max seconds before absent")
    stuck_task_seconds: float = Field(default=300.0, description="Max seconds on task before stuck")


@router.get("/groups")
async def list_group_channels(org_id: str | None = None):
    """Lists all company communication channels, default rooms, and member bots without 7-bot limits."""
    engine = get_autonomous_company_engine()
    target_id = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    chat = engine.get_chat_engine(target_id)
    return [c.model_dump() for c in chat.list_channels()]


@router.post("/groups/create")
async def create_group_channel(payload: CreateGroupChannelRequest):
    """Dynamically creates a sub-team communication channel or squad war-room."""
    engine = get_autonomous_company_engine()
    target_id = payload.org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    chan = engine.create_subgroup(
        org_id=target_id,
        channel_id=payload.channel_id,
        name=payload.name,
        member_bot_names=payload.member_bot_names,
        description=payload.description,
    )
    return chan.model_dump()


@router.post("/groups/message")
async def post_group_message(payload: PostGroupMessageRequest):
    """Posts a message to a group room, automatically extracting @mentions and notifying target bots."""
    engine = get_autonomous_company_engine()
    target_id = payload.org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    msg = engine.post_group_message(
        org_id=target_id,
        channel_id=payload.channel_id,
        sender_bot=payload.sender_bot,
        content=payload.content,
    )
    return msg.model_dump()


@router.get("/groups/{channel_id}/messages")
async def get_channel_messages(channel_id: str, org_id: str | None = None, limit: int = 50):
    """Retrieves recent message history and mentions for a specific channel."""
    engine = get_autonomous_company_engine()
    target_id = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    chat = engine.get_chat_engine(target_id)
    return [m.model_dump() for m in chat.get_channel_history(channel_id, limit=limit)]


@router.post("/attendance/pulse")
async def record_attendance_pulse(payload: RecordPulseRequest):
    """Records a silent, non-conversational protocol heartbeat tick for an agent."""
    engine = get_autonomous_company_engine()
    target_id = payload.org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    hb = engine.record_bot_pulse(
        org_id=target_id,
        bot_name=payload.bot_name,
        status=payload.status,
        active_task_id=payload.active_task_id,
    )
    return hb.model_dump()


@router.post("/attendance/check-and-heal")
async def check_attendance_and_heal(payload: CheckAttendanceRequest):
    """Evaluates the silent attendance ledger and automatically dispatches Bot Medic to heal absent or stuck agents."""
    engine = get_autonomous_company_engine()
    target_id = payload.org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    res = engine.check_attendance_and_heal(
        org_id=target_id,
        timeout_seconds=payload.timeout_seconds,
        stuck_task_seconds=payload.stuck_task_seconds,
    )
    return res


@router.get("/attendance/roll-call")
async def get_roll_call_digest(org_id: str | None = None):
    """Returns a clean executive markdown standup digest of workforce attendance without spamming chat."""
    engine = get_autonomous_company_engine()
    target_id = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    digest = engine.get_roll_call_digest(target_id)
    return {"org_id": target_id, "roll_call_digest": digest}


@router.get("/attendance/status")
async def get_attendance_status(org_id: str | None = None):
    """Retrieves full liveness ledger status for all enrolled bots."""
    engine = get_autonomous_company_engine()
    target_id = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    att = engine.get_attendance_engine(target_id)
    return {
        "org_id": target_id,
        "bots_count": len(att.list_heartbeats()),
        "heartbeats": [h.model_dump() for h in att.list_heartbeats()],
    }


class UpdateKanbanTaskRequest(BaseModel):
    org_id: str | None = Field(default=None, description="Organization ID")
    new_status: str = Field(..., description="New task status: ready, in_progress, review, done")
    bot_name: str = Field(default="system", description="Bot updating the task")
    log_message: str = Field(default="", description="Audit log progress comment")
    result: str = Field(default="", description="Optional deliverable or outcome text")


class AddKanbanLogRequest(BaseModel):
    org_id: str | None = Field(default=None, description="Organization ID")
    task_id: str = Field(..., description="Target task ID")
    bot_name: str = Field(..., description="Bot recording the log")
    message: str = Field(..., min_length=1, description="Progress or event description")
    kind: str = Field(default="progress_log", description="Event category: progress_log, sign_off, blocker")


class AgentCheckInRequest(BaseModel):
    org_id: str | None = Field(default=None, description="Organization ID")
    bot_name: str = Field(..., description="Agent checking in")
    current_task_id: str | None = Field(default=None, description="Current task if actively working")
    progress_notes: str = Field(default="", description="Progress update notes")
    new_status: str | None = Field(default=None, description="Optional updated task status")


@router.get("/kanban/tasks")
async def list_kanban_tasks(
    org_id: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    limit: int = 50,
):
    """Retrieves tasks from the SQLite Kanban board, filterable by bot assignee and status."""
    engine = get_autonomous_company_engine()
    target_id = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    tasks = engine.list_kanban_tasks(org_id=target_id, status=status, assignee=assignee, limit=limit)
    return tasks


@router.post("/kanban/tasks/{task_id}/update")
async def update_kanban_task(task_id: str, payload: UpdateKanbanTaskRequest):
    """Updates a Kanban task status and writes an audit event to the activity log."""
    engine = get_autonomous_company_engine()
    target_id = payload.org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    res = engine.update_kanban_task(
        org_id=target_id,
        task_id=task_id,
        new_status=payload.new_status,
        bot_name=payload.bot_name,
        log_message=payload.log_message,
        result=payload.result,
    )
    return res


@router.post("/kanban/events/log")
async def add_kanban_log(payload: AddKanbanLogRequest):
    """Records an activity log event for a task."""
    engine = get_autonomous_company_engine()
    target_id = payload.org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    event = engine.add_kanban_log(
        org_id=target_id,
        task_id=payload.task_id,
        bot_name=payload.bot_name,
        message=payload.message,
        kind=payload.kind,
    )
    return event


@router.get("/kanban/events")
async def list_kanban_events(org_id: str | None = None, task_id: str | None = None, limit: int = 50):
    """Retrieves operational audit logs and task progress history."""
    engine = get_autonomous_company_engine()
    target_id = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    events = engine.list_kanban_logs(org_id=target_id, task_id=task_id, limit=limit)
    return events


@router.post("/kanban/agent-check-in")
async def agent_kanban_check_in(payload: AgentCheckInRequest):
    """Allows an agent to regularly check in on its queue, log progress, claim tasks, and update status."""
    engine = get_autonomous_company_engine()
    target_id = payload.org_id or (engine.list_companies()[0].org_id if engine.list_companies() else None)
    if not target_id:
        raise HTTPException(status_code=404, detail="No active organizations found.")

    res = engine.agent_kanban_check_in(
        org_id=target_id,
        bot_name=payload.bot_name,
        current_task_id=payload.current_task_id,
        progress_notes=payload.progress_notes,
        new_status=payload.new_status,
    )
    return res
