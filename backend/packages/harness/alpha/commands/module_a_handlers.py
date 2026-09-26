"""Real handlers for the five Module-A spec commands.

``/boost``, ``/schedule``, ``/grill-me``, ``/teamwork-preview`` and
``/self-heal`` resolve as spec commands the catalog did not define; the
intent resolver reported them as ``registered=False`` (see
``alpha/orchestration/intent.py`` and the DY-R3 honesty contract). These
handlers give each one a REAL seam and an honest result:

* ``/boost`` — runs the router's OWN deterministic classifier over the task
  text (``classify_domain`` → ``classify_complexity`` → ``resolve_thinking_mode``)
  and reports the tier / token-budget / model-tier hint it derived. Budgets are
  hints for the model router, never guarantees of provider behavior, and the
  command changes nothing.
* ``/schedule`` — creates a REAL cron job through ``alpha.scheduler.cron_manager``
  (a 5-field cron expression, or a deterministic ``every Nm|Nh|Nd`` shorthand).
  Anything the shorthand cannot express exactly is rejected with the reason.
* ``/grill-me`` — one REAL model turn through ``alpha.utils.oneshot_llm`` that
  interrogates the stated goal with hard questions. The model's own text is
  returned verbatim; failures and empty answers surface as such.
* ``/teamwork-preview`` — READ-ONLY composition preview built from the real bot
  roster file, the real group-run service and the real swarm coordinator. Every
  source that cannot be read is disclosed; nothing is assigned or started.
* ``/self-heal`` — REAL diagnosis only: emergency-stop state, the durable
  Sentinel report journal (estop / latest passes with their real error strings)
  and the real control endpoints that actually apply repairs. It never claims a
  fix it did not perform.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from alpha.commands.registry import CommandExecutionResult

_INTERVAL_RE = re.compile(r"^every\s+(\d+)\s*([mhd])$", re.IGNORECASE)
#: One cron field: digits, wildcards, steps, ranges, lists, ``?``/``#`` and the
#: three-letter month/day names. Used to find where the schedule ends and the
#: command/prompt begins, so a malformed schedule is rejected, never guessed.
_CRON_FIELD_RE = re.compile(
    r"^(?:[0-9*/,\-?#]+|[A-Z]{3})$",
)


def _run_async(factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Run a coroutine from synchronous handler code.

    Commands execute on a worker thread (``asyncio.to_thread`` in the gateway),
    so there is normally no running loop; when a loop *is* running (a direct
    in-process call), the coroutine is executed on a fresh loop in a helper
    thread so we never re-enter the caller's loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    box: dict[str, Any] = {}

    def runner() -> None:
        box["value"] = asyncio.run(factory())

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:  # pragma: no cover - defensive
        raise box["error"]
    return box.get("value")


# ── /boost ───────────────────────────────────────────────────────────


def handle_boost(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """Report the thinking tier / token budget the router derives for a task."""
    raw = args.strip()
    if not raw:
        return CommandExecutionResult(
            status="error",
            command="/boost",
            output="Usage: /boost <task text>\nExample: /boost refactor the auth layer and prove it with tests",
        )
    try:
        from alpha.orchestration.intent import classify_complexity, classify_domain, resolve_thinking_mode

        domain = classify_domain(raw)
        complexity = classify_complexity(raw)
        mode = resolve_thinking_mode(raw, domain=domain, complexity=complexity)
    except Exception as exc:
        return CommandExecutionResult(
            status="error",
            command="/boost",
            output=f"Thinking-mode preview unavailable: {type(exc).__name__}: {exc}",
        )
    mode_dict = mode.to_dict() if hasattr(mode, "to_dict") else dict(mode)
    return CommandExecutionResult(
        status="success",
        command="/boost",
        output=(
            f"Thinking routing for this task (derived, nothing changed):\n"
            f"  tier: {mode_dict.get('tier')}\n"
            f"  token budget hint: {mode_dict.get('token_budget')}\n"
            f"  model tier hint: {mode_dict.get('model_tier_hint')}\n"
            f"  reason: {mode_dict.get('reason')}\n"
            "Budgets and tier hints guide the model router; they are not guarantees of provider behavior. "
            "This command reports the derivation only — it changes no configuration."
        ),
        data={"action": "boost_preview", "thinking_mode": mode_dict, "changed_anything": False},
    )


# ── /schedule ────────────────────────────────────────────────────────


def _interval_to_cron(spec: str) -> tuple[str | None, str | None]:
    """Convert ``every 30m`` / ``every 2h`` / ``every 1d`` to a cron expression.

    Returns ``(cron_expression, None)`` or ``(None, reason)`` when the interval
    cannot be expressed exactly — the command never guesses a schedule.
    """
    match = _INTERVAL_RE.match(spec.strip())
    if not match:
        return None, f"unrecognised schedule '{spec}' — use a 5-field cron expression or 'every <N>[m|h|d]'"
    amount, unit = int(match.group(1)), match.group(2).lower()
    if amount < 1:
        return None, "interval must be at least 1"
    if unit == "m":
        if amount > 59:
            return None, f"every {amount}m exceeds the minute field (1-59); use hours or a cron expression"
        return f"*/{amount} * * * *", None
    if unit == "h":
        if amount > 23 or 24 % amount != 0:
            return None, f"every {amount}h is not an exact hour division of the day; use a cron expression"
        return f"0 */{amount} * * *", None
    if amount > 31:
        return None, f"every {amount}d exceeds the day-of-month field; use a cron expression"
    return f"0 0 */{amount} * *", None


def handle_schedule(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """Create a real cron job for a command or prompt."""
    raw = args.strip()
    if not raw:
        return CommandExecutionResult(
            status="error",
            command="/schedule",
            output=(
                "Usage: /schedule <cron | every 30m> <command or prompt>\n"
                "Examples:\n"
                "  /schedule */15 * * * * /status\n"
                "  /schedule every 6h summarize yesterday's incidents\n"
                "The job is created in the real cron manager; the manager sets its first run immediately."
            ),
        )
    tokens = raw.split()
    if tokens[0].lower() == "every":
        if len(tokens) < 3:
            return CommandExecutionResult(
                status="error",
                command="/schedule",
                output=(
                    "Both a schedule and a command/prompt are required.\n"
                    "Usage: /schedule <cron | every 30m> <command or prompt>"
                ),
            )
        schedule_spec = " ".join(tokens[:2])
        payload = " ".join(tokens[2:]).strip()
    else:
        # A cron expression is five whitespace-separated fields; anything after
        # the fifth field is the command/prompt. Fields are recognised by
        # pattern (digits, *, /, -, ,, ?, # and month/day names) so a 4-field
        # cron is REJECTED instead of being silently shifted by the payload.
        field_count = 0
        for token in tokens:
            if field_count < 5 and _CRON_FIELD_RE.match(token):
                field_count += 1
            else:
                break
        if field_count != 5:
            return CommandExecutionResult(
                status="error",
                command="/schedule",
                output=(
                    f"Schedule rejected: a cron expression needs 5 fields, found {field_count}.\n"
                    "Usage: /schedule <5-field cron> <command or prompt>\n"
                    "Example: /schedule */15 * * * * /status"
                ),
            )
        schedule_spec = " ".join(tokens[:5])
        payload = " ".join(tokens[5:]).strip()
    if not payload:
        return CommandExecutionResult(
            status="error", command="/schedule", output="The command/prompt to run must not be empty."
        )
    if schedule_spec.lower().startswith("every"):
        cron_expression, reason = _interval_to_cron(schedule_spec)
    else:
        fields = schedule_spec.split()
        cron_expression, reason = (schedule_spec, None) if len(fields) == 5 else (None, f"a cron expression needs 5 fields, got {len(fields)}")
    if cron_expression is None:
        return CommandExecutionResult(status="error", command="/schedule", output=f"Schedule rejected: {reason}")

    digest = hashlib.sha256(f"{cron_expression}|{payload}".encode()).hexdigest()[:6]
    slug = re.sub(r"[^a-z0-9]+", "-", payload.lower()).strip("-")[:32] or "task"
    name = f"sched-{slug}-{digest}"
    try:
        from alpha.scheduler.cron_manager import get_cron_manager

        job = get_cron_manager().add_job(name, cron_expression, payload)
    except Exception as exc:
        return CommandExecutionResult(
            status="error",
            command="/schedule",
            output=f"Cron job not created: {type(exc).__name__}: {exc}",
        )
    job_dict = job.to_dict() if hasattr(job, "to_dict") else {"name": getattr(job, "name", name)}
    thread_id = (context or {}).get("thread_id")
    return CommandExecutionResult(
        status="success",
        command="/schedule",
        output=(
            f"Scheduled: {job_dict.get('name', name)}\n"
            f"  cron: {cron_expression}\n"
            f"  runs: {payload}\n"
            f"  next run (manager-reported): {job_dict.get('next_run')}\n"
            "The cron manager sets a new job's first run immediately; edit or remove it through the Scheduled view."
        ),
        data={"action": "schedule_created", "job": job_dict, "cron_expression": cron_expression, "thread_id": thread_id},
    )


# ── /grill-me ────────────────────────────────────────────────────────

_GRILL_SYSTEM = (
    "You are a rigorous interviewer. Given the user's goal, plan or claim, ask the hardest questions that "
    "would expose hidden assumptions, missing requirements, failure modes and unmeasurable claims. "
    "Return 4-7 numbered questions, each one sentence, ordered from most to least fundamental. "
    "Do not answer the question yourself and do not add encouragement."
)


def handle_grill_me(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """Run one real model turn that interrogates the user's goal."""
    raw = args.strip()
    if not raw:
        return CommandExecutionResult(
            status="error",
            command="/grill-me",
            output="Usage: /grill-me <goal, plan or claim>\nExample: /grill-me we will launch on Friday with one engineer",
        )
    thread_id = (context or {}).get("thread_id")
    try:
        from alpha.config import get_app_config
        from alpha.utils.oneshot_llm import run_oneshot_llm

        text = _run_async(
            lambda: run_oneshot_llm(
                system_instruction=_GRILL_SYSTEM,
                user_content=f"Interrogate this goal/plan/claim:\n<subject>\n{raw}\n</subject>",
                run_name="grill_me",
                app_config=get_app_config(),
                thread_id=thread_id if isinstance(thread_id, str) else None,
            )
        )
    except Exception as exc:
        return CommandExecutionResult(
            status="error",
            command="/grill-me",
            output=f"Interrogation not run: {type(exc).__name__}: {exc}",
        )
    questions = (text or "").strip()
    if not questions:
        return CommandExecutionResult(
            status="ok",
            command="/grill-me",
            output="The model returned no text for this subject — no questions were produced (nothing is invented).",
            data={"action": "grill", "questions": "", "produced": False},
        )
    return CommandExecutionResult(
        status="success",
        command="/grill-me",
        output=questions,
        data={"action": "grill", "questions": questions, "produced": True},
        autonomous_directives=[
            "Answer every question with evidence before treating the plan as ready to execute.",
        ],
    )


# ── /teamwork-preview ────────────────────────────────────────────────


def _read_roster() -> tuple[list[dict[str, Any]], str | None]:
    """Read the real bot roster file; never construct or seed anything."""
    try:
        from alpha.config.runtime_paths import runtime_home

        path = Path(runtime_home()) / "bots" / "roster.json"
        if not path.exists():
            return [], f"no roster file at {path}"
        payload = json.loads(path.read_text(encoding="utf-8"))
        bots = payload.get("bots") if isinstance(payload, dict) else payload
        if not isinstance(bots, list):
            return [], f"roster file {path} does not contain a bot list"
        return [b for b in bots if isinstance(b, dict)], None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


def handle_teamwork_preview(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """Read-only preview of the real team surfaces. Starts nothing."""
    bots, roster_error = _read_roster()
    lines: list[str] = ["Teamwork preview (read-only — nothing was started or assigned)", ""]
    if roster_error:
        lines.append(f"Bot roster: unavailable — {roster_error}")
    else:
        lines.append(f"Bot roster ({len(bots)} available):")
        for bot in bots[:20]:
            name = bot.get("name") or bot.get("display_name") or "(unnamed)"
            role = bot.get("role") or bot.get("specialty") or "role not declared"
            status = bot.get("status") or "status not declared"
            lines.append(f"  - {name} — {role} ({status})")
        if len(bots) > 20:
            lines.append(f"  … and {len(bots) - 20} more")

    disclosures: list[str] = []
    try:
        from alpha.groups.runner import get_group_run_service

        service = get_group_run_service()
        lister = getattr(service, "list_groups", None) or getattr(service, "groups", None)
        groups = lister() if callable(lister) else None
        if isinstance(groups, list):
            lines.append("")
            lines.append(f"Group runs recorded in this process: {len(groups)}")
        else:
            disclosures.append("group-run service reachable but exposes no listable groups from this seam")
    except Exception as exc:
        disclosures.append(f"group-run service unavailable: {type(exc).__name__}: {exc}")

    try:
        from alpha.swarm.coordinator import get_swarm_coordinator

        coordinator = get_swarm_coordinator()
        status_fn = getattr(coordinator, "status", None) or getattr(coordinator, "snapshot", None)
        if callable(status_fn):
            snapshot = status_fn()
            described = json.dumps(snapshot, default=str)[:400] if snapshot is not None else "no snapshot returned"
            lines.append("")
            lines.append(f"Swarm coordinator: {described}")
        else:
            disclosures.append("swarm coordinator reachable but exposes no status/snapshot method")
    except Exception as exc:
        disclosures.append(f"swarm coordinator unavailable: {type(exc).__name__}: {exc}")

    if disclosures:
        lines.append("")
        lines.append("Not measured in this preview:")
        lines.extend(f"  - {d}" for d in disclosures)
    return CommandExecutionResult(
        status="success",
        command="/teamwork-preview",
        output="\n".join(lines),
        data={"action": "teamwork_preview", "bots": len(bots), "disclosures": disclosures, "read_only": True},
    )


# ── /self-heal ───────────────────────────────────────────────────────


def handle_self_heal(args: str, context: dict[str, Any] | None = None) -> CommandExecutionResult:
    """Diagnose self-healing state from real sources; apply nothing."""
    lines = ["Self-heal diagnosis (no fix is applied by this command)", ""]

    try:
        from alpha.runtime.estop import get_estop_manager

        status = get_estop_manager().get_status()
        engaged = None
        if isinstance(status, dict):
            for key in ("is_engaged", "engaged"):
                if isinstance(status.get(key), bool):
                    engaged = status[key]
                    break
        lines.append(
            f"Emergency stop: {'ENGAGED' if engaged is True else ('clear' if engaged is False else f'state not reported ({status})')}"
        )
    except Exception as exc:
        lines.append(f"Emergency stop: unavailable — {type(exc).__name__}: {exc}")

    try:
        from alpha.runtime.sentinel.report_store import default_sentinel_report_store

        history = default_sentinel_report_store().history(limit=5)
        lines.append(f"Sentinel passes on record: {history.total} (showing newest {len(history.entries)})")
        for entry in history.entries:
            report = entry.get("report", {}) if isinstance(entry, dict) else {}
            lines.append(
                f"  - {entry.get('recorded_at', '?')} [{entry.get('trigger', '?')}"
                f"{' repair' if entry.get('auto_heal') is True else ''}]: "
                f"scanned {report.get('scanned', '?')}, fixed {report.get('fixed', '?')}, "
                f"reverted {report.get('reverted', '?')}, escalated {report.get('escalated', '?')}"
            )
            for error in (report.get("errors") or [])[:3]:
                lines.append(f"      error: {error}")
        if not history.entries:
            lines.append("  (no passes recorded yet)")
    except Exception as exc:
        # Fail closed: a corrupt/unreadable journal is reported with its real
        # reason, never as "no passes".
        lines.append(f"Sentinel journal: unreadable — {type(exc).__name__}: {exc}")

    lines.extend(
        [
            "",
            "To actually repair: run a Sentinel repair pass (Supervisor tab, or "
            "POST /api/autonomy/sentinel/run with auto_heal=true). It checkpoints, verifies and reverts "
            "anything that comes back red. Worker recovery runs through POST /api/supervision/recover and "
            "/api/supervision/adopt.",
        ]
    )
    return CommandExecutionResult(
        status="success",
        command="/self-heal",
        output="\n".join(lines),
        data={"action": "self_heal_diagnosis", "applied_fix": False},
    )
