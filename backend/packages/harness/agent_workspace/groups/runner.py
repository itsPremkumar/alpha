"""Autonomous team execution: one objective in, coordinated multi-agent work out.

``GroupChatService.post_message`` only routes conversation. This runner closes
the automation loop: ``start_run`` fans the objective out to one subagent per
room member (each carrying its BotProfile role + SOUL, running the same
``SubagentExecutor`` engine as ordinary delegation — including its
no-clarification rule, so members never block waiting for a human), then a
moderator synthesis pass merges the member outputs into one deliverable. Every
phase is posted back into the room log, so the run is observable from the
Team page without polling a second surface.

Durability model: run records persist to ``runs.json`` next to ``rooms.json``
(status transitions only); member executions ride the process-wide isolated
subagent loop with the shared admission controller, so a Gateway restart
recovers the record as ``interrupted`` while in-flight model work follows the
standard subagent lifecycle. This mirrors the durable batch runtime's
assembly/execution pattern without requiring a SQL backend.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

_MAX_PARALLEL_MEMBERS = 3
_POLL_SECONDS = 2.0
_MAX_RUNS_KEPT = 100
_MAX_OBJECTIVE_CHARS = 20000


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sanitize_thread_suffix(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", name.strip().lower())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return (cleaned or "room")[:48]


@dataclass
class GroupRun:
    """One autonomous team execution."""

    run_id: str
    room_name: str
    objective: str
    members: list[str] = field(default_factory=list)
    moderator: str | None = None
    status: str = "running"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    member_results: dict[str, dict] = field(default_factory=dict)
    synthesis: str | None = None
    error: str | None = None
    # New fields for retry, critic, risk, adaptive parallelism
    retry_counts: dict[str, int] = field(default_factory=dict)
    max_retries: int = 2
    critic_output: str | None = None
    risk_note: str | None = None
    adaptive_parallelism: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


class GroupRunService:
    """Starts, tracks, and cancels autonomous team runs."""

    def __init__(self, storage_path: str | Path | None = None):
        if storage_path is not None:
            self.storage_path = Path(storage_path).resolve()
        else:
            try:
                from agent_workspace.config.runtime_paths import runtime_home

                self.storage_path = runtime_home() / "groups" / "runs.json"
            except Exception:
                self.storage_path = Path.cwd() / ".agent_workspace" / "groups" / "runs.json"
        self._runs: dict[str, GroupRun] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._member_executions: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._load()

    # -- persistence -----------------------------------------------------

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("runs", [])[-_MAX_RUNS_KEPT:]:
                run = GroupRun(**{k: v for k, v in item.items() if k in GroupRun.__dataclass_fields__})
                if run.status == "running":
                    run.status = "interrupted"
                    run.error = "Gateway restarted while this run was in flight."
                self._runs[run.run_id] = run
        except Exception:
            logger.warning("Group runs load failed; starting empty", exc_info=True)

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            runs = list(self._runs.values())[-_MAX_RUNS_KEPT:]
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "runs": [r.to_dict() for r in runs]}, f, indent=2)
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Group runs save failed", exc_info=True)

    def _update(self, run_id: str, **changes) -> GroupRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            for key, value in changes.items():
                setattr(run, key, value)
            run.updated_at = _now()
            self._save()
            return run

    # -- public API ------------------------------------------------------

    def get_run(self, run_id: str) -> GroupRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(self, room_name: str | None = None) -> list[GroupRun]:
        with self._lock:
            runs = list(self._runs.values())
        if room_name is not None:
            runs = [r for r in runs if r.room_name.lower() == room_name.lower()]
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def start_run(
        self,
        room_name: str,
        objective: str,
        *,
        members: list[str] | None = None,
        moderator: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        max_parallel: int = _MAX_PARALLEL_MEMBERS,
    ) -> GroupRun:
        """Create the run record, announce it in the room, and execute async."""
        from agent_workspace.groups.service import get_group_chat_service

        objective = objective.strip()
        if not objective:
            raise ValueError("objective must not be empty")
        if len(objective) > _MAX_OBJECTIVE_CHARS:
            raise ValueError(f"objective exceeds {_MAX_OBJECTIVE_CHARS} characters")

        room = get_group_chat_service().get_or_create_room(room_name)
        resolved_members = [m.lower().strip() for m in (members or room.members)]
        resolved_members = [m for m in dict.fromkeys(resolved_members) if m]
        if not resolved_members:
            raise ValueError(f"Room '{room.name}' has no members to run.")

        from agent_workspace.bots.kill_switch import is_bot_paused, is_kill_switch_active
        from agent_workspace.bots.registry import get_bot_registry
        from agent_workspace.bots.templates import INACTIVE_STATUSES

        killed, kill_reason = is_kill_switch_active()
        if killed:
            raise ValueError(f"Global kill switch is active: {kill_reason}")

        bot_registry = get_bot_registry()
        skipped: dict[str, dict] = {}
        active_members: list[str] = []
        for member in resolved_members:
            profile = bot_registry.get_or_create(member)
            paused, pause_reason = is_bot_paused(member)
            if paused:
                skipped[member] = {"status": "skipped", "output": f"@{member} is paused ({pause_reason}); skipped."}
            elif profile.status in INACTIVE_STATUSES:
                skipped[member] = {"status": "skipped", "output": f"@{member} is {profile.status}; skipped."}
            else:
                active_members.append(member)
        if not active_members:
            raise ValueError(f"Room '{room.name}' has no active members to run (all suspended or archived).")
        resolved_moderator = (moderator or room.moderator or active_members[0]).lower().strip()
        max_parallel = max(1, min(max_parallel, _MAX_PARALLEL_MEMBERS))

        run = GroupRun(
            run_id=f"grun_{uuid4().hex[:12]}",
            room_name=room.name,
            objective=objective,
            members=active_members,
            moderator=resolved_moderator,
            member_results=dict(skipped),
        )
        cancel_event = threading.Event()
        with self._lock:
            self._runs[run.run_id] = run
            self._cancel_events[run.run_id] = cancel_event
            self._save()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            with self._lock:
                del self._runs[run.run_id]
                del self._cancel_events[run.run_id]
            raise RuntimeError("Group runs must be started from a running event loop.") from exc

        announcement = f"Team run started ({run.run_id}): {objective}"
        if skipped:
            announcement += f" Skipped inactive: {', '.join(sorted(skipped))}."
        get_group_chat_service().post_message(
            room.name,
            sender="coordinator",
            content=announcement,
            intent="action",
            metadata={"group_run_id": run.run_id, "phase": "started"},
        )
        task = loop.create_task(
            self._execute(run.run_id, user_id=user_id, user_role=user_role, max_parallel=max_parallel),
            name=f"group-run-{run.run_id}",
        )
        with self._lock:
            self._tasks[run.run_id] = task
        task.add_done_callback(lambda t, rid=run.run_id: self._forget_task(rid, t))
        return run

    def _forget_task(self, run_id: str, task: asyncio.Task) -> None:
        with self._lock:
            self._tasks.pop(run_id, None)
            self._cancel_events.pop(run_id, None)
        if task.cancelled():
            self._update(run_id, status="cancelled", error="Run cancelled.")
        elif exc := task.exception():
            logger.warning("Group run %s task failed: %s", run_id, exc)
            current = self.get_run(run_id)
            if current is not None and current.status == "running":
                self._update(run_id, status="failed", error=str(exc))

    def cancel_run(self, run_id: str) -> bool:
        """Signal cancellation; member executions are asked to stop as well."""
        from agent_workspace.subagents.executor import request_cancel_background_task

        with self._lock:
            run = self._runs.get(run_id)
            event = self._cancel_events.get(run_id)
            executions = list(self._member_executions.get(run_id, []))
            task = self._tasks.get(run_id)
        if run is None or run.status != "running":
            return False
        if event is not None:
            event.set()
        for execution_id in executions:
            try:
                request_cancel_background_task(execution_id)
            except Exception:
                pass
        if task is not None:
            task.cancel()
        return True

    # -- execution -------------------------------------------------------

    async def _execute(
        self,
        run_id: str,
        *,
        user_id: str | None,
        user_role: str | None,
        max_parallel: int,
    ) -> None:
        from agent_workspace.bots.registry import get_bot_registry
        from agent_workspace.config import get_app_config
        from agent_workspace.groups.service import get_group_chat_service
        from agent_workspace.subagents.builtins import BUILTIN_SUBAGENTS
        from agent_workspace.subagents.config import SubagentConfig, resolve_subagent_model_name
        from agent_workspace.subagents.executor import (
            SubagentExecutor,
            SubagentStatus,
            get_background_task_result,
        )
        from agent_workspace.tools import get_available_tools
        from agent_workspace.utils.assembly_io import run_assembly

        run = self.get_run(run_id)
        if run is None:
            return
        rooms = get_group_chat_service()
        bots = get_bot_registry()
        app_config = get_app_config()
        if not app_config.models:
            message = "No chat models are configured. Run setup first (make setup)."
            rooms.post_message(run.room_name, sender="coordinator", content=message, intent="action", metadata={"group_run_id": run_id, "phase": "failed"})
            self._update(run_id, status="failed", error=message)
            return

        thread_id = f"group-{_sanitize_thread_suffix(run.room_name)}"
        base = BUILTIN_SUBAGENTS["general-purpose"]
        semaphore = asyncio.Semaphore(max_parallel)
        member_outputs: dict[str, str] = {}

        # One shared tool assembly for every member + the synthesis pass
        # (off-loop: assembly may block on MCP cache init). Synthesis reuses
        # the same pool with side-effect tools denied — it only merges text.
        _probe_model = resolve_subagent_model_name(base, None, app_config=app_config)
        tool_pool = await run_assembly(
            get_available_tools,
            groups=None,
            model_name=_probe_model,
            subagent_enabled=False,
            include_upload_tool=False,
            app_config=app_config,
        )

        async def run_member(member: str) -> None:
            event = self._cancel_events.get(run_id)
            if event is not None and event.is_set():
                return
            profile = bots.get_or_create(member)
            system_prompt = (
                f"{profile.soul}\n\n<team_context>\n"
                f"You are @{profile.name} ({profile.role}) in the '{run.room_name}' team room. "
                f"Deliver your specialist slice of the team objective below. "
                f"Work autonomously — never ask for clarification. "
                f"End with a concise deliverable the moderator can merge.\n"
                f"Teammates: {', '.join(run.members)}\n"
                f"Moderator: {run.moderator}\n</team_context>"
            )
            config = SubagentConfig(
                name=f"group-{profile.name}",
                description=profile.role,
                system_prompt=system_prompt,
                tools=base.tools,
                disallowed_tools=list(base.disallowed_tools or []),
                skills=list(profile.skills) if profile.skills else None,
                model=profile.model or "inherit",
                max_turns=base.max_turns,
                timeout_seconds=base.timeout_seconds,
            )
            prompt = (
                f"Team objective: {run.objective}\n\nYour slice as @{profile.name} ({profile.role}): contribute your specialist expertise toward the objective. Produce concrete output (analysis, code, findings), not a plan to do the work."
            )
            max_retries = run.max_retries
            attempt = run.retry_counts.get(member, 0)
            while attempt <= max_retries:
                event = self._cancel_events.get(run_id)
                if event is not None and event.is_set():
                    return
                try:
                    executor = SubagentExecutor(
                        config=config,
                        tools=tool_pool,
                        app_config=app_config,
                        thread_id=thread_id,
                        user_id=user_id,
                        user_role=user_role,
                        run_id=run_id,
                    )
                    async with semaphore:
                        event = self._cancel_events.get(run_id)
                        if event is not None and event.is_set():
                            return
                        execution_id = executor.execute_async(prompt, task_id=f"{run_id}:{member}:attempt{attempt + 1}")
                        with self._lock:
                            self._member_executions.setdefault(run_id, []).append(execution_id)
                        while True:
                            await asyncio.sleep(_POLL_SECONDS)
                            result = get_background_task_result(execution_id)
                            if result is None:
                                raise RuntimeError(f"Member execution for @{member} disappeared.")
                            if result.status.is_terminal:
                                if result.status is SubagentStatus.COMPLETED and result.result:
                                    member_outputs[member] = result.result
                                else:
                                    member_outputs[member] = f"@{member} did not complete ({result.status.value}): {result.error or 'no output'}"
                                break
                            event = self._cancel_events.get(run_id)
                            if event is not None and event.is_set():
                                from agent_workspace.subagents.executor import request_cancel_background_task

                                request_cancel_background_task(execution_id)
                                member_outputs[member] = f"@{member} was cancelled."
                                break
                        break  # Success, exit retry loop
                except Exception as exc:
                    logger.warning("Group run %s member @%s attempt %d failed: %s", run_id, member, attempt + 1, exc)
                    attempt += 1
                    run.retry_counts[member] = attempt
                    self._update(run_id, retry_counts=run.retry_counts)
                    if attempt > max_retries:
                        member_outputs[member] = f"@{member} failed after {max_retries + 1} attempts: {exc}"
                        break
                    else:
                        # Retry after a short backoff
                        await asyncio.sleep(min(2**attempt, 10))
                        continue

            self._update(
                run_id,
                member_results={
                    **(self.get_run(run_id).member_results if self.get_run(run_id) else {}),
                    member: {"status": "done", "output": member_outputs.get(member, "")},
                },
            )
            rooms.post_message(
                run.room_name,
                sender=member,
                content=member_outputs.get(member, ""),
                intent="discussion",
                metadata={"group_run_id": run_id, "phase": "member_result"},
            )

        await asyncio.gather(*(run_member(m) for m in run.members))

        event = self._cancel_events.get(run_id)
        if event is not None and event.is_set():
            self._update(run_id, status="cancelled", error="Run cancelled.")
            return

        # Moderator synthesis pass over the collected member outputs.
        synthesis = ""
        try:
            moderator_profile = bots.get_or_create(run.moderator or run.members[0])
            combined = "\n\n".join(f"--- @{m} ---\n{member_outputs.get(m, '(no output)')}" for m in run.members)
            synthesis_config = SubagentConfig(
                name=f"group-{moderator_profile.name}-synthesis",
                description="Team moderator synthesis",
                system_prompt=(
                    f"{moderator_profile.soul}\n\n<team_context>\n"
                    f"You are moderating the '{run.room_name}' team room. Merge the "
                    f"member outputs below into ONE final deliverable for the team "
                    f"objective. Resolve conflicts, drop duplication, keep every "
                    f"verifiable fact. Never ask for clarification.\n</team_context>"
                ),
                tools=None,
                disallowed_tools=[
                    "task",
                    "ralph_loop",
                    "session_search",
                    "ask_clarification",
                    "bash",
                    "write_file",
                    "str_replace",
                    "present_files",
                ],
                model=moderator_profile.model or "inherit",
                max_turns=60,
                timeout_seconds=900,
            )
            synthesis_executor = SubagentExecutor(
                config=synthesis_config,
                tools=tool_pool,
                app_config=app_config,
                thread_id=thread_id,
                user_id=user_id,
                user_role=user_role,
                run_id=run_id,
            )
            synthesis_execution_id = synthesis_executor.execute_async(
                f"Team objective: {run.objective}\n\nMember outputs:\n{combined}",
                task_id=f"{run_id}:synthesis",
            )
            with self._lock:
                self._member_executions.setdefault(run_id, []).append(synthesis_execution_id)
            while True:
                await asyncio.sleep(_POLL_SECONDS)
                result = get_background_task_result(synthesis_execution_id)
                if result is None:
                    raise RuntimeError("Synthesis execution disappeared.")
                if result.status.is_terminal:
                    synthesis = result.result or result.error or ""
                    break
        except Exception as exc:
            logger.warning("Group run %s synthesis failed: %s", run_id, exc)
            synthesis = f"Synthesis failed ({exc}). Member outputs are posted individually above."

        rooms.post_message(
            run.room_name,
            sender=run.moderator or "coordinator",
            content=synthesis,
            intent="action",
            metadata={"group_run_id": run_id, "phase": "synthesis"},
        )
        self._update(run_id, status="succeeded", synthesis=synthesis)


_global_runner: GroupRunService | None = None
_global_runner_path: str | None = None


def get_group_run_service(storage_path: str | Path | None = None) -> GroupRunService:
    """Return the process-wide group run service (AGENT_WORKSPACE_HOME-aware)."""
    global _global_runner, _global_runner_path
    if storage_path is not None:
        resolved = str(Path(storage_path).resolve())
        if _global_runner is None or _global_runner_path != resolved:
            _global_runner = GroupRunService(storage_path=resolved)
            _global_runner_path = resolved
        return _global_runner
    if _global_runner is None:
        _global_runner = GroupRunService()
        try:
            _global_runner_path = str(_global_runner.storage_path.resolve())
        except Exception:
            _global_runner_path = None
        return _global_runner
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        live = str((runtime_home() / "groups" / "runs.json").resolve())
    except Exception:
        return _global_runner
    if _global_runner_path != live:
        _global_runner = GroupRunService()
        _global_runner_path = live
    return _global_runner
