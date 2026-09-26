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
import errno
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
#: Upper bound on the exponential backoff between member retries.
_RETRY_BACKOFF_CAP = 10.0
#: Grace added to a component's own execution-time cap before the runner
#: declares it unrecoverable (so a subagent that is about to report
#: ``TIMED_OUT`` on its own is still given a chance to be recorded as such).
_DEADLINE_GRACE_SECONDS = 30.0
#: Fallback wait budget for a component whose own cap is missing or unusable.
_DEFAULT_COMPONENT_TIMEOUT_SECONDS = 1800.0
#: Absolute ceiling on how long the runner waits for ONE component (a member
#: execution, or the moderator pass) to reach a terminal status. ``None`` means
#: "derive it from the component's own execution cap plus
#: ``_DEADLINE_GRACE_SECONDS``".
#:
#: It exists because the poll loops below are the only thing standing between a
#: wedged execution and a run that stays ``running`` forever. A member that
#: never reports a terminal status holds the whole fan-in open: no sibling's
#: result is surfaced, the moderator pass never runs, and every member's record
#: is left with no output at all -- so one stuck component silently swallows
#: the entire run's accounting. Bounding the wait turns that into a typed
#: ``timeout`` on that member alone, which the ordinary failure path already
#: knows how to report.
_COMPONENT_WAIT_CAP_SECONDS: float | None = None

#: Closed set of typed failure kinds recorded on a member result (``error_type``)
#: and mirrored in the run-level ``error`` as ``[kind]``. Keeping it closed means
#: callers can branch on a stable tag instead of parsing prose.
MEMBER_ERROR_TYPES = frozenset({"dependency_error", "timeout", "cancelled", "resource_exhausted"})

#: ``errno`` values that mean "the host ran out of something", not "the call failed".
_RESOURCE_ERRNOS = frozenset({errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.ENOSPC})


class RunComponentError(RuntimeError):
    """A typed failure of one group-run component (a member or the synthesis pass).

    Carries the component that failed and a stable ``error_type`` drawn from
    :data:`MEMBER_ERROR_TYPES`, so a fault is reported as a typed error rather
    than an opaque string.
    """

    def __init__(self, component: str, error_type: str, message: str) -> None:
        super().__init__(message)
        self.component = component
        self.error_type = error_type if error_type in MEMBER_ERROR_TYPES else "dependency_error"


def classify_component_failure(exc: BaseException) -> str:
    """Map an escaping exception onto the stable ``MEMBER_ERROR_TYPES`` tag."""
    if isinstance(exc, RunComponentError):
        return exc.error_type
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, MemoryError):
        return "resource_exhausted"
    if isinstance(exc, OSError) and exc.errno in _RESOURCE_ERRNOS:
        return "resource_exhausted"
    return "dependency_error"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _positive_number(value: object) -> float | None:
    """Return ``value`` as a positive float, or ``None`` if it is not one.

    ``bool`` is excluded on purpose: ``timeout_seconds=True`` is a config bug,
    not a one-second budget.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number > 0 else None


def _component_wait_budget(timeout_seconds: object) -> float:
    """Seconds to wait for one component before declaring it unrecoverable."""
    if _COMPONENT_WAIT_CAP_SECONDS is not None:
        return float(_COMPONENT_WAIT_CAP_SECONDS)
    return _positive_number(timeout_seconds) or _DEFAULT_COMPONENT_TIMEOUT_SECONDS


def _component_wait_deadline(timeout_seconds: object) -> tuple[float, float]:
    """``(deadline, budget)`` on the running loop's clock for one component."""
    budget = _component_wait_budget(timeout_seconds) + _DEADLINE_GRACE_SECONDS
    return asyncio.get_running_loop().time() + budget, budget


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
                from alpha.config.runtime_paths import runtime_home

                self.storage_path = runtime_home() / "groups" / "runs.json"
            except Exception:
                self.storage_path = Path.cwd() / ".alpha" / "groups" / "runs.json"
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

    # -- typed failure surfacing -----------------------------------------
    #
    # A member failure must land on *that member's own result*, never on its
    # siblings and never in the void: the record keeps the human message, the
    # stable ``error_type`` tag, and (when known) how many attempts were spent.

    def _surface_member_failure(
        self,
        run_id: str,
        member: str,
        exc: BaseException,
        *,
        member_outputs: dict[str, str] | None = None,
    ) -> dict:
        """Record ``exc`` as a typed failure on ``member``'s own result.

        Called from two places: the retry loop when a member exhausts its
        attempts, and the ``gather(return_exceptions=True)`` boundary for
        exceptions that escape ``run_member`` entirely. Both paths post a room
        receipt so the failure is observable, never silently swallowed.
        """
        error_type = classify_component_failure(exc)
        message = str(exc) or type(exc).__name__
        run = self.get_run(run_id)
        outputs = member_outputs or {}
        text = outputs.get(member) or f"@{member} failed [{error_type}]: {message}"
        entry = {
            **((run.member_results.get(member) or {}) if run is not None else {}),
            "status": "failed" if error_type != "cancelled" else "cancelled",
            "output": text,
            "error": message,
            "error_type": error_type,
        }
        attempts = run.retry_counts.get(member) if run is not None else None
        if attempts:
            entry["attempts"] = attempts
        logger.warning("Group run %s member @%s failed [%s]: %s", run_id, member, error_type, message)
        if run is None:
            return entry
        self._update(run_id, member_results={**run.member_results, member: entry})
        try:
            from alpha.groups.service import get_group_chat_service

            get_group_chat_service().post_message(
                run.room_name,
                sender=member,
                content=text,
                intent="action",
                metadata={"group_run_id": run_id, "phase": "member_failed", "error_type": error_type},
            )
        except Exception as receipt_exc:  # a lost receipt must not mask the typed error itself
            logger.warning("Group run %s could not post @%s failure receipt", run_id, member, exc_info=True)
            entry["transcript_error"] = f"Failure was recorded but its room receipt could not be posted: {receipt_exc}"
            self._update(run_id, member_results={**run.member_results, member: entry})
        return entry

    def _post_member_receipt(self, run_id: str, room_name: str, sender: str, content: str) -> str | None:
        """Post one run receipt into the room log; return the failure, if any.

        A lost receipt is a gap in the transcript, not a fault of whoever the
        receipt is *about*, so it must never become that component's error. The
        reason is returned so the caller can disclose it on the component's own
        record rather than swallow it.
        """
        from alpha.groups.service import get_group_chat_service

        try:
            get_group_chat_service().post_message(
                room_name,
                sender=sender,
                content=content,
                intent="discussion",
                metadata={"group_run_id": run_id, "phase": "member_result"},
            )
        except Exception as exc:
            logger.warning("Group run %s could not post @%s result receipt", run_id, sender, exc_info=True)
            return f"Result was delivered but its room receipt could not be posted: {exc}"
        return None

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
        from alpha.groups.service import get_group_chat_service

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

        from alpha.bots.kill_switch import is_bot_paused, is_kill_switch_active
        from alpha.bots.registry import get_bot_registry
        from alpha.bots.templates import INACTIVE_STATUSES

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
                self._update(run_id, status="failed", error=f"[{classify_component_failure(exc)}] {exc}")

    def cancel_run(self, run_id: str) -> bool:
        """Signal cancellation; member executions are asked to stop as well."""
        from alpha.subagents.executor import request_cancel_background_task

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
        from alpha.bots.registry import get_bot_registry
        from alpha.config import get_app_config
        from alpha.groups.service import get_group_chat_service
        from alpha.subagents.builtins import BUILTIN_SUBAGENTS
        from alpha.subagents.config import SubagentConfig, resolve_subagent_model_name
        from alpha.subagents.executor import (
            SubagentExecutor,
            SubagentStatus,
            get_background_task_result,
        )
        from alpha.tools import get_available_tools
        from alpha.utils.assembly_io import run_assembly

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
            member_status = "done"
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
                        deadline, wait_budget = _component_wait_deadline(config.timeout_seconds)
                        while True:
                            await asyncio.sleep(_POLL_SECONDS)
                            result = get_background_task_result(execution_id)
                            if result is None:
                                raise RuntimeError(f"Member execution for @{member} disappeared.")
                            if result.status.is_terminal:
                                if result.status is SubagentStatus.COMPLETED and result.result:
                                    member_outputs[member] = result.result
                                    break
                                if result.status is SubagentStatus.CANCELLED:
                                    event = self._cancel_events.get(run_id)
                                    if event is not None and event.is_set():
                                        # Run-level stop the member was asked to make:
                                        # it reports the cancellation, it does not fail.
                                        member_outputs[member] = f"@{member} did not complete (cancelled): {result.error or 'no output'}"
                                        member_status = "cancelled"
                                        break
                                    # Cancelled from under this member (kill switch,
                                    # global subagent cancel). Typed and terminal: it
                                    # must never be retried into a false success.
                                    raise RunComponentError(member, "cancelled", f"@{member} execution cancelled: {result.error or 'no output'}")
                                # FAILED / TIMED_OUT / empty output must feed the
                                # retry machinery below: max_retries previously
                                # covered only infrastructure exceptions, so the
                                # commonest failure mode (the model call itself
                                # failing) was recorded on the first hit and
                                # never retried despite run.max_retries.
                                raise RunComponentError(
                                    member,
                                    "timeout" if result.status is SubagentStatus.TIMED_OUT else "dependency_error",
                                    f"@{member} execution {result.status.value}: {result.error or 'no output'}",
                                )
                            event = self._cancel_events.get(run_id)
                            if event is not None and event.is_set():
                                from alpha.subagents.executor import request_cancel_background_task

                                request_cancel_background_task(execution_id)
                                member_outputs[member] = f"@{member} was cancelled."
                                member_status = "cancelled"
                                break
                            if asyncio.get_running_loop().time() >= deadline:
                                # The execution is still non-terminal after its own
                                # budget. Keep waiting for the cancellation signal
                                # above, but never past the deadline: an unbounded
                                # wait here holds the whole fan-in open and no
                                # sibling's result is ever surfaced.
                                from alpha.subagents.executor import request_cancel_background_task

                                request_cancel_background_task(execution_id)
                                raise RunComponentError(
                                    member,
                                    "timeout",
                                    f"@{member} execution did not reach a terminal status within {wait_budget:g}s",
                                )
                        break  # Success, exit retry loop
                except Exception as exc:
                    if classify_component_failure(exc) == "cancelled":
                        # A cancellation is terminal for this member: retrying it
                        # would re-run work the system already stopped.
                        raise
                    logger.warning("Group run %s member @%s attempt %d failed: %s", run_id, member, attempt + 1, exc)
                    attempt += 1
                    run.retry_counts[member] = attempt
                    self._update(run_id, retry_counts=run.retry_counts)
                    if attempt > max_retries:
                        member_outputs[member] = f"@{member} failed after {max_retries + 1} attempts: {exc}"
                        # Typed, per-member surfacing: the failure lands on this
                        # member's own result (and its own room receipt), not on a
                        # sibling's gather — so no tail recording after this point.
                        self._surface_member_failure(run_id, member, exc, member_outputs=member_outputs)
                        return
                    else:
                        # Retry after a short backoff
                        await asyncio.sleep(min(2**attempt, _RETRY_BACKOFF_CAP))
                        continue

            # The member's execution reached a terminal status, so its verdict
            # is the ``member_status``/``member_outputs`` pair above. The room
            # receipt is a transcript side effect of that verdict, not part of
            # it: it used to be posted after the record and unguarded, so a
            # failing transcript raised out of ``run_member`` and the post-join
            # loop re-recorded a member that had genuinely delivered as a typed
            # failure. Post it first (best-effort, disclosed) and then record
            # the verdict, so a transcript fault can never rewrite it.
            transcript_error = self._post_member_receipt(run_id, run.room_name, member, member_outputs.get(member, ""))
            self._update(
                run_id,
                member_results={
                    **(self.get_run(run_id).member_results if self.get_run(run_id) else {}),
                    member: {
                        "status": member_status,
                        "output": member_outputs.get(member, ""),
                        **({"transcript_error": transcript_error} if transcript_error else {}),
                    },
                },
            )

        # Sibling isolation: ``return_exceptions=True`` stops one member's
        # exception from cancelling/abandoning the others, and every escaping
        # failure is then surfaced as a typed error on that member's own result
        # instead of vanishing into the gather (or into a sibling's record).
        outcomes = await asyncio.gather(*(run_member(m) for m in run.members), return_exceptions=True)
        for member, outcome in zip(run.members, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                self._surface_member_failure(run_id, member, outcome, member_outputs=member_outputs)

        event = self._cancel_events.get(run_id)
        if event is not None and event.is_set():
            self._update(run_id, status="cancelled", error="Run cancelled.")
            return

        # Moderator synthesis pass over the collected member outputs.
        synthesis = ""
        synthesis_error: BaseException | None = None
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
            synthesis_deadline, synthesis_budget = _component_wait_deadline(synthesis_config.timeout_seconds)
            while True:
                await asyncio.sleep(_POLL_SECONDS)
                result = get_background_task_result(synthesis_execution_id)
                if result is None:
                    raise RuntimeError("Synthesis execution disappeared.")
                if result.status.is_terminal:
                    if result.status is not SubagentStatus.COMPLETED:
                        # The moderator pass is a component too: a timeout or an
                        # external cancellation here must not be recorded as a
                        # successful run.
                        raise RunComponentError(
                            "synthesis",
                            "timeout" if result.status is SubagentStatus.TIMED_OUT else ("cancelled" if result.status is SubagentStatus.CANCELLED else "dependency_error"),
                            f"synthesis execution {result.status.value}: {result.error or 'no output'}",
                        )
                    synthesis = result.result or ""
                    if not synthesis.strip():
                        # Completed with nothing to deliver. A member execution
                        # that completes with no output is already a failure
                        # ("@member execution completed: no output"); the
                        # moderator pass had no such check, so it published
                        # synthesis="" and a run whose status was "succeeded" --
                        # the whole objective claimed delivered with an empty
                        # deliverable, plus an empty phase:"synthesis" receipt in
                        # the room log that made the transcript look complete.
                        # No component may report success without a deliverable.
                        raise RunComponentError(
                            "synthesis",
                            "dependency_error",
                            "synthesis execution completed: no deliverable",
                        )
                    break
                if asyncio.get_running_loop().time() >= synthesis_deadline:
                    raise RunComponentError(
                        "synthesis",
                        "timeout",
                        f"synthesis execution did not reach a terminal status within {synthesis_budget:g}s",
                    )
        except Exception as exc:
            synthesis_error = exc
            logger.warning("Group run %s synthesis failed: %s", run_id, exc)
            synthesis = f"Synthesis failed ({exc}). Member outputs are posted individually above."

        rooms.post_message(
            run.room_name,
            sender=run.moderator or "coordinator",
            content=synthesis,
            intent="action",
            metadata={"group_run_id": run_id, "phase": "synthesis"},
        )
        failures: list[str] = []
        for member in run.members:
            entry = run.member_results.get(member) or {}
            if entry.get("status") in {"failed", "cancelled"}:
                kind = entry.get("error_type") or "dependency_error"
                failures.append(f"member @{member} [{kind}]: {entry.get('error') or entry.get('output') or 'no output'}")
        if synthesis_error is not None:
            failures.append(f"synthesis [{classify_component_failure(synthesis_error)}]: {synthesis_error}")

        if failures:
            # Contract: a run never claims success once one of its components
            # has failed. Partial member outputs are still merged and posted so
            # the work that did land stays visible in the room.
            self._update(run_id, status="failed", synthesis=synthesis, error="; ".join(failures))
        else:
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
        from alpha.config.runtime_paths import runtime_home

        live = str((runtime_home() / "groups" / "runs.json").resolve())
    except Exception:
        return _global_runner
    if _global_runner_path != live:
        _global_runner = GroupRunService()
        _global_runner_path = live
    return _global_runner
