"""Measure real delegation behavior, end to end, against a running Gateway.

W1 of ``docs/ALPHA_SUBAGENT_SYSTEM_ADVANCED_IMPROVEMENT_PLAN.md``. The question
is not "does the code look right" - it is "when a real lead agent delegates real
work, what actually persists, and what does the runtime actually report?". This
probe drives a catalogue of delegation scenarios through the public Gateway HTTP
surface (the same routes a browser uses), then measures the durable record: run
status, delegation ledger entries, persisted ``subagent.*`` events, per-child
token usage, stop reasons, acceptance verdicts, delivery receipts and workspace
artifacts. Tech-lead systems are measured where they run, in end-to-end
behavior, not only in unit tests - this file is that measurement.

Four rules this file obeys, each because the alternative produces a specific
lie:

1. **Attach mode only (v1).** The probe talks to a Gateway that is already
   running (``--base-url``, default ``http://127.0.0.1:8001``). It never boots
   or kills one, so a probe cannot silently measure its own fixture.
2. **Unmeasured is ``null``, never ``0``.** Every measurement starts as ``None``
   and only a real read replaces it. "No delegations happened" (a measurement:
   ``0``) and "the ledger could not be read" (unmeasured: ``null``) stay
   distinguishable in the JSON, because a reader takes opposite decisions.
   ``queue_wait_seconds`` is deliberately ``null`` everywhere until plan W3.a
   instruments it; see ``UNINSTRUMENTED_FIELDS``.
3. **A scenario finding is data, not probe failure.** ``unmet`` and ``refused``
   mean the probe successfully measured something the expectation did not
   anticipate (a missing verdict, a disabled feature). They do not change the
   process exit code. ``incomplete``/``error`` mean the probe could not measure
   and exit ``1``; schema violations exit ``2``.
4. **The report is schema-gated.** Every report is validated against
   ``support/subagent_probe_report.py`` before it is written; drift surfaces as
   exit code ``2`` with the violations embedded in the report, never as a
   silently different file shape.

Scenario catalogue (v1, 11 scenarios): ``direct_answer``, ``single_delegation``,
``parallel_fanout_3``, ``quick_category``, ``turn_capped``,
``acceptance_tests_pass``, ``acceptance_tests_fail``, ``acceptance_file_pass``,
``acceptance_file_fail``, ``batch_flush_cancel`` (records ``refused`` with the
feature state when durable batches are disabled) and ``control_plane_spawn``.

Usage (from ``backend/``, against a Gateway on :8001)::

    uv run --no-sync python scripts/subagent_delegation_probe.py
    uv run --no-sync python scripts/subagent_delegation_probe.py --list
    uv run --no-sync python scripts/subagent_delegation_probe.py --scenarios direct_answer,single_delegation

Output: ``.alpha/subagent_probe/<run-id>/report.json`` + ``report.md``, plus a
console summary. Exit codes: 0 complete, 1 incomplete, 2 schema violations.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import time
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from support.subagent_probe_report import (  # noqa: E402
    TERMINAL_RUN_STATUSES,
    ProbeReport,
    ScenarioReport,
    new_run_id,
    render_markdown,
    report_exit_code,
    to_jsonable,
    validate_report,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = BACKEND_ROOT / ".alpha" / "subagent_probe"
DEFAULT_BASE_URL = "http://127.0.0.1:8001"
STREAM_MODES: tuple[str, ...] = ("messages-tuple", "values", "custom")
POLL_INTERVAL_SECONDS = 2.0

#: Terminal statuses of the control-plane lifecycle records.
CONTROL_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


class GatewayUnreachable(RuntimeError):
    """The Gateway could not be reached at all; every remaining scenario stops.

    Distinct from a scenario-level error: this means the probe lost its
    measurement target, so continuing would fabricate "no data" for scenarios
    that were never attempted. The report says which ones.
    """


class ProbeFailure(RuntimeError):
    """A measured HTTP failure inside one scenario (bad status, missing field)."""


# ---------------------------------------------------------------------------
# SSE + HTTP helpers (house pattern: see scripts/subagent_lifecycle_probe.py)
# ---------------------------------------------------------------------------


async def parse_sse(lines: Any) -> Any:
    name, parts = "message", []

    def dispatch() -> tuple[str, str] | None:
        if name == "message" and not parts:
            return None
        return (name, "\n".join(parts))

    async for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            f = dispatch()
            if f is not None:
                yield f
            name, parts = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            parts.append(line[5:].lstrip())
    f = dispatch()
    if f is not None:
        yield f


async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        return await client.request(method, url, **kwargs)
    except httpx.TransportError as exc:
        # Every transport-level failure means the probe could not reach its
        # measurement target. The message keeps the exception type so a
        # connect refusal, a read timeout and a mid-stream reset stay
        # distinguishable in the report's reason string.
        raise GatewayUnreachable(f"{method} {url} transport failure: {type(exc).__name__}: {exc}") from exc


def _as_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


async def _get_json(client: httpx.AsyncClient, url: str, **kwargs: Any) -> Any:
    r = await _request(client, "GET", url, **kwargs)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


async def _get_rows(client: httpx.AsyncClient, url: str, **kwargs: Any) -> list[dict[str, Any]] | None:
    body = await _get_json(client, url, **kwargs)
    if isinstance(body, list):
        return [row for row in body if isinstance(row, Mapping)]
    if isinstance(body, Mapping):
        for key in ("data", "events", "items", "rows"):
            value = body.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, Mapping)]
    return None


def _parse_ts(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Scenario catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    expectation: str
    kind: str = "prompt"  # prompt | managed_prompt | batch | control
    prompt: str | None = None
    timeout_seconds: float = 420.0
    subagent_name_prefix: str | None = None


DIRECT_ANSWER_PROMPT = "Reply with exactly: PONG. Do not call any tool, and do not delegate to a subagent."

SINGLE_DELEGATION_PROMPT = (
    "Delegation exercise - you MUST use the `task` tool; doing the work yourself fails the exercise. "
    'Call the `task` tool exactly once with subagent_type "general-purpose" and this exact prompt: '
    '"Compute 6*7 and reply with only the resulting number." '
    "Do not compute the multiplication yourself. After the delegation returns, reply with the number the subagent returned."
)

PARALLEL_FANOUT_PROMPT = (
    "Delegation exercise - you MUST use the `task` tool three times; doing the multiplications yourself fails the exercise. "
    'Emit all three `task` calls in the same turn (subagent_type "general-purpose"), one call per product, with these exact prompts: '
    '"Compute 2*3 and reply with only the resulting number.", '
    '"Compute 3*4 and reply with only the resulting number.", '
    '"Compute 4*5 and reply with only the resulting number." '
    "Then report the three numbers the subagents returned."
)

QUICK_CATEGORY_PROMPT = (
    "Delegation exercise - you MUST use the `task` tool; doing the work yourself fails the exercise. "
    'Call the `task` tool exactly once with subagent_type "general-purpose" and category "quick", and this exact prompt: '
    '"Compute 9*9 and reply with only the resulting number." '
    "Do not compute the multiplication yourself. Then reply with the number the subagent returned."
)

MANAGED_TURN_CAPPED_PROMPT = (
    "Delegation exercise - you MUST use the `task` tool; reading the files yourself fails the exercise. "
    'Call the `task` tool exactly once with subagent_type "{name}" and this exact prompt: '
    '"Call read_file five separate times, once per path, before answering: README.md, AGENTS.md, docs/INDEX.md, '
    'backend/AGENTS.md, CHANGELOG.md. Do not answer before all five calls have returned." '
    "Then report the subagent's status and stop reason exactly as recorded in the delegation result."
)

TESTS_PASS_PROMPT = (
    "Delegation exercise - you MUST use the `task` tool; running the command yourself fails the exercise. "
    'Call the `task` tool exactly once with subagent_type "general-purpose" and acceptance_criteria exactly '
    '["tests_passed:echo 1 passed in 0.01s"], and this exact prompt: '
    '"Run exactly this bash command: echo 1 passed in 0.01s . Then quote its output verbatim in your report." '
    "After the delegation returns, report the acceptance checklist it returned, criterion by criterion."
)

TESTS_FAIL_PROMPT = (
    "Delegation exercise - you MUST use the `task` tool; running the command yourself fails the exercise. "
    'Call the `task` tool exactly once with subagent_type "general-purpose" and acceptance_criteria exactly '
    '["tests_passed:echo 1 failed, 0 passed in 0.01s"], and this exact prompt: '
    '"Run exactly this bash command: echo 1 failed, 0 passed in 0.01s . Then quote its output verbatim in your report." '
    "After the delegation returns, report the acceptance checklist it returned, criterion by criterion."
)

FILE_PASS_PROMPT = (
    "Delegation exercise - you MUST use the `task` tool; writing the file yourself fails the exercise. "
    'Call the `task` tool exactly once with subagent_type "general-purpose" and acceptance_criteria exactly '
    '["file_written:/mnt/user-data/outputs/probe_accept_pass.txt"], and this exact prompt: '
    '"Write the file /mnt/user-data/outputs/probe_accept_pass.txt with write_file, containing exactly the single line: '
    'probe-accepted. Do nothing else." '
    "After the delegation returns, report the acceptance checklist it returned, criterion by criterion."
)

FILE_FAIL_PROMPT = (
    "Delegation exercise - you MUST use the `task` tool; writing the file yourself fails the exercise. "
    'Call the `task` tool exactly once with subagent_type "general-purpose" and acceptance_criteria exactly '
    '["file_written:/mnt/user-data/outputs/probe_accept_absent.txt"], and this exact prompt: '
    '"Do NOT create /mnt/user-data/outputs/probe_accept_absent.txt. Instead write /mnt/user-data/outputs/probe_accept_present.txt '
    'with write_file, containing exactly the single line: probe-not-expected. Do nothing else." '
    "After the delegation returns, report the acceptance checklist it returned, criterion by criterion."
)

BATCH_PROMPT = (
    "Batching exercise - you MUST use the `batch_task` tool; doing the items yourself fails the exercise. "
    'Call the `batch_task` tool exactly once with title "probe batch", subagent_type "general-purpose", and exactly 4 items. '
    'Each item key is "item-1" through "item-4" and each item prompt is "Reply with the word BATCH- followed by your item key." '
    "Immediately after the tool returns the batch id, call `cancel_batch` with that exact batch_id. "
    "Then report the batch id and the cancellation result verbatim."
)

CONTROL_OBJECTIVE = "Reply with exactly: CONTROL-PROBE. Do not call any tools."


def _managed_definition(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "display_name": "Probe Capped Reader",
        "description": "Reads files one per turn; used by the delegation probe to exercise the turn cap.",
        "system_prompt": "You read files. Call read_file once per requested path, in order, and only then answer.",
        "tools": ["read_file"],
        "max_turns": 2,
        "timeout_seconds": 300,
    }


def scenario_catalogue() -> list[Scenario]:
    return [
        Scenario(
            key="direct_answer",
            title="Direct answer, no delegation",
            expectation="the run reaches 'success' with zero ledger delegations and zero persisted subagent events",
            kind="prompt",
            prompt=DIRECT_ANSWER_PROMPT,
            timeout_seconds=240.0,
        ),
        Scenario(
            key="single_delegation",
            title="One delegation completes with telemetry",
            expectation="exactly one delegation completes; its subagent.end event carries a model name and (when the provider reports usage) token counts",
            kind="prompt",
            prompt=SINGLE_DELEGATION_PROMPT,
        ),
        Scenario(
            key="parallel_fanout_3",
            title="Three independent delegations in one turn",
            expectation="three delegations complete; when they ran concurrently their event windows overlap (the measured overlap is recorded either way)",
            kind="prompt",
            prompt=PARALLEL_FANOUT_PROMPT,
        ),
        Scenario(
            key="quick_category",
            title="Quick-category delegation",
            expectation="the quick-category delegation completes and its resolved model is recorded in subagent.end",
            kind="prompt",
            prompt=QUICK_CATEGORY_PROMPT,
        ),
        Scenario(
            key="turn_capped",
            title="Turn-capped managed subagent",
            expectation="the delegation ends with stop reason 'turn_capped' (the probe-created subagent is capped at max_turns=2)",
            kind="managed_prompt",
            prompt=MANAGED_TURN_CAPPED_PROMPT,
            subagent_name_prefix="probe-capped",
        ),
        Scenario(
            key="acceptance_tests_pass",
            title="tests_passed criterion that holds",
            expectation="the tests_passed criterion resolves checked=true and holds=true; an UNVERIFIED verdict is recorded with the checker's reason instead of being smoothed over",
            kind="prompt",
            prompt=TESTS_PASS_PROMPT,
        ),
        Scenario(
            key="acceptance_tests_fail",
            title="tests_passed criterion that does not hold",
            expectation="the tests_passed criterion resolves checked=true and holds=false (a recorded failing summary cannot pass)",
            kind="prompt",
            prompt=TESTS_FAIL_PROMPT,
        ),
        Scenario(
            key="acceptance_file_pass",
            title="file_written criterion that holds",
            expectation="the file_written criterion resolves checked=true and holds=true (the child wrote the file and the read-back proved it)",
            kind="prompt",
            prompt=FILE_PASS_PROMPT,
        ),
        Scenario(
            key="acceptance_file_fail",
            title="file_written criterion that does not hold",
            expectation="the file_written criterion resolves checked=true and holds=false (the claimed file was never written)",
            kind="prompt",
            prompt=FILE_FAIL_PROMPT,
        ),
        Scenario(
            key="batch_flush_cancel",
            title="Durable batch submitted then cancelled",
            expectation="a durable batch was submitted and then cancelled: the persisted batch row shows cancellation; when durable batches are disabled the refusal is recorded with the feature state instead",
            kind="batch",
            prompt=BATCH_PROMPT,
        ),
        Scenario(
            key="control_plane_spawn",
            title="Control-plane spawn reaches a terminal state",
            expectation="POST /api/subagents/control/spawn creates a record visible in the control-plane listing that reaches a terminal state (completed_at set) with a readable result",
            kind="control",
            timeout_seconds=360.0,
        ),
    ]


def _select_scenarios(catalogue: list[Scenario], spec: str) -> list[Scenario]:
    if not spec.strip():
        return catalogue
    wanted = [part.strip() for part in spec.split(",") if part.strip()]
    known = {s.key: s for s in catalogue}
    unknown = [key for key in wanted if key not in known]
    if unknown:
        raise SystemExit(f"unknown scenario key(s): {', '.join(unknown)}; known: {', '.join(sorted(known))}")
    return [known[key] for key in wanted]


# ---------------------------------------------------------------------------
# Run driver
# ---------------------------------------------------------------------------


@dataclass
class RunContext:
    thread_id: str
    run_id: str | None
    run_status: str | None
    wall_seconds: float
    timed_out: bool
    live_task_events: int
    live_custom_frames: int
    sse_errors: list[str]


async def _create_thread(client: httpx.AsyncClient, base_url: str, scenario_key: str) -> str:
    r = await _request(
        client,
        "POST",
        f"{base_url}/api/threads",
        json={"metadata": {"purpose": "subagent-delegation-probe", "scenario": scenario_key}},
    )
    if r.status_code >= 400:
        raise ProbeFailure(f"thread create HTTP {r.status_code}: {r.text[:300]}")
    body = _as_mapping(r.json()) or {}
    thread_id = body.get("thread_id") or body.get("id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ProbeFailure(f"thread create response carried no thread id: {json.dumps(body)[:200]}")
    return thread_id


async def _latest_run_id(client: httpx.AsyncClient, urls: str) -> str | None:
    rows = await _get_rows(client, f"{urls}/runs")
    if not rows:
        return None
    run_id = rows[0].get("run_id")
    return run_id if isinstance(run_id, str) and run_id else None


async def _wait_terminal(client: httpx.AsyncClient, urls: str, run_id: str, timeout: float) -> tuple[str | None, bool]:
    deadline = time.monotonic() + max(60.0, timeout)
    status: str | None = None
    while True:
        detail = await _get_json(client, f"{urls}/runs/{run_id}")
        if isinstance(detail, Mapping):
            value = detail.get("status")
            if isinstance(value, str):
                status = value
                if value in TERMINAL_RUN_STATUSES:
                    return status, False
        if time.monotonic() >= deadline:
            return status, True
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _drive_run(client: httpx.AsyncClient, base_url: str, *, prompt: str, timeout: float, scenario_key: str) -> RunContext:
    thread_id = await _create_thread(client, base_url, scenario_key)
    urls = f"{base_url}/api/threads/{thread_id}"
    body = {
        "input": {"messages": [{"role": "user", "content": prompt}]},
        "metadata": {"purpose": "subagent-delegation-probe", "scenario": scenario_key},
        "stream_mode": list(STREAM_MODES),
        "on_disconnect": "continue",
        # The server-owned delegation switch. ``RunCreateRequest.autonomous``
        # makes ``start_run`` apply ``subagent_enabled`` (and plan mode / no
        # clarification pauses) after ordinary client context is sanitized,
        # which is what puts the ``task`` tool in the lead agent's toolset -
        # the same opt-in the Web UI sends when its delegation mode is on.
        # Without it the tool is absent and a zero-delegation measurement would
        # be a property of the request, not of the model.
        "autonomous": True,
    }
    deadline = time.monotonic() + timeout
    run_id: str | None = None
    custom_frames = 0
    task_events = 0
    sse_errors: list[str] = []
    exceeded = False

    async def timed(source: Any) -> Any:
        nonlocal exceeded
        async for line in source:
            if time.monotonic() > deadline:
                exceeded = True
                return
            yield line

    started = time.monotonic()
    try:
        async with client.stream("POST", f"{urls}/runs/stream", json=body, timeout=httpx.Timeout(30.0, read=timeout)) as resp:
            if resp.status_code >= 400:
                text = (await resp.aread()).decode("utf-8", "replace")
                raise ProbeFailure(f"run stream HTTP {resp.status_code}: {text[:300]}")
            location = resp.headers.get("content-location") or resp.headers.get("Content-Location")
            if location:
                run_id = location.rstrip("/").split("/")[-1] or None
            async for name, data in parse_sse(timed(resp.aiter_lines())):
                if name == "custom":
                    custom_frames += 1
                    payload = _maybe_json(data)
                    event_type = payload.get("type") if isinstance(payload, dict) else None
                    if isinstance(event_type, str) and event_type.startswith(("task_", "subagent")):
                        task_events += 1
                elif name == "error":
                    sse_errors.append(data[:300])
    except httpx.TransportError as exc:
        # The Gateway (or the path to it) died while the run was streaming.
        # This must reach the report as `incomplete` with the transport error:
        # a lost measurement target is not a probe bug (`error`) and certainly
        # not a completed run, which is what the D4 gate checks.
        raise GatewayUnreachable(f"event stream for {urls}/runs/stream broke mid-run: {type(exc).__name__}: {exc}") from exc
    wall_seconds = round(time.monotonic() - started, 3)

    if not run_id:
        run_id = await _latest_run_id(client, urls)
    run_status: str | None = None
    timed_out = False
    if run_id:
        run_status, timed_out = await _wait_terminal(client, urls, run_id, timeout)

    return RunContext(
        thread_id=thread_id,
        run_id=run_id,
        run_status=run_status,
        wall_seconds=wall_seconds,
        timed_out=timed_out or exceeded,
        live_task_events=task_events,
        live_custom_frames=custom_frames,
        sse_errors=sse_errors,
    )


# ---------------------------------------------------------------------------
# Measurement collection
# ---------------------------------------------------------------------------


def _overlap_pairs(rows: list[dict[str, Any]]) -> int | None:
    """Count subagent task windows that overlap, from persisted event stamps."""
    windows: dict[str, tuple[float, float]] = {}
    for row in rows:
        if str(row.get("category")) != "subagent":
            continue
        metadata = _as_mapping(row.get("metadata")) or {}
        content = _as_mapping(row.get("content")) or {}
        task_id = metadata.get("task_id") or content.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        stamp = _parse_ts(row.get("created_at"))
        if stamp is None:
            continue
        start, end = windows.get(task_id, (stamp, stamp))
        windows[task_id] = (min(start, stamp), max(end, stamp))
    if len(windows) < 2:
        return None
    items = list(windows.values())
    pairs = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if a[0] <= b[1] and b[0] <= a[1]:
                pairs += 1
    return pairs


async def _thread_ledger(client: httpx.AsyncClient, urls: str) -> list[dict[str, Any]] | None:
    body = await _get_json(client, f"{urls}/state")
    if not isinstance(body, Mapping):
        return None
    values = body.get("values")
    if not isinstance(values, Mapping):
        values = body
    ledger = values.get("delegations")
    if not isinstance(ledger, list):
        return None
    return [entry for entry in ledger if isinstance(entry, Mapping)]


async def _collect_measurements(client: httpx.AsyncClient, base_url: str, ctx: RunContext, rep: ScenarioReport) -> dict[str, Any]:
    """Fill every measurable field; leave ``None`` where nothing was measured."""
    urls = f"{base_url}/api/threads/{ctx.thread_id}"
    extras: dict[str, Any] = {}
    stop_from_events: list[str] = []

    rows = await _get_rows(client, f"{urls}/runs/{ctx.run_id}/events") if ctx.run_id else None
    if rows is not None:
        subagent_rows = [row for row in rows if str(row.get("category")) == "subagent"]
        counts = Counter(str(row.get("event_type")) for row in subagent_rows)
        task_ids: set[str] = set()
        children: list[dict[str, Any]] = []
        for row in subagent_rows:
            content = _as_mapping(row.get("content")) or {}
            metadata = _as_mapping(row.get("metadata")) or {}
            task_id: str | None = None
            for source in (metadata, content):
                candidate = source.get("task_id")
                if isinstance(candidate, str) and candidate:
                    task_id = candidate
                    break
            if task_id:
                task_ids.add(task_id)
            if row.get("event_type") != "subagent.end":
                continue
            usage = _as_mapping(content.get("usage")) or {}
            stop = content.get("stop_reason")
            if isinstance(stop, str) and stop:
                stop_from_events.append(stop)
            model = content.get("model_name")
            children.append(
                {
                    "task_id": task_id or "",
                    "model": model if isinstance(model, str) and model else None,
                    "input_tokens": _as_int(usage.get("input_tokens")),
                    "output_tokens": _as_int(usage.get("output_tokens")),
                    "total_tokens": _as_int(usage.get("total_tokens")),
                }
            )
        rep.event_persistence = {
            "source": "run_events",
            "total": len(subagent_rows),
            "counts": dict(counts),
            "task_ids": sorted(task_ids),
            "live_task_events": ctx.live_task_events,
        }
        if children:
            rep.tokens_by_child = children
            models = sorted({child["model"] for child in children if child["model"]})
            rep.models = models or None
            model_totals: dict[str, int] = {}
            for child in children:
                total = child["total_tokens"]
                if total is None:
                    input_tokens = child["input_tokens"]
                    output_tokens = child["output_tokens"]
                    if input_tokens is not None or output_tokens is not None:
                        total = (input_tokens or 0) + (output_tokens or 0)
                if child["model"] and total is not None:
                    model_totals[child["model"]] = model_totals.get(child["model"], 0) + total
            rep.tokens_by_model = model_totals or None
        extras["overlap_pairs"] = _overlap_pairs(subagent_rows)

    ledger = await _thread_ledger(client, urls)
    ledger_stop: list[str] = []
    acceptance_leaves: list[dict[str, Any]] = []
    receipt_totals = {"cited": 0, "resolved": 0, "failed": 0, "unknown": 0}
    saw_receipt_verdict = False
    if ledger is not None:
        mine = [entry for entry in ledger if ctx.run_id is None or entry.get("run_id") in (None, ctx.run_id)]
        rep.delegations = len(mine)
        for entry in mine:
            stop = entry.get("stop_reason")
            if isinstance(stop, str) and stop:
                ledger_stop.append(stop)
            verdict = entry.get("acceptance_verdict")
            if isinstance(verdict, Mapping):
                leaves = verdict.get("leaves")
                if isinstance(leaves, list):
                    for leaf in leaves:
                        if not isinstance(leaf, Mapping):
                            continue
                        acceptance_leaves.append(
                            {
                                "criterion": str(leaf.get("criterion") or ""),
                                "family": str(leaf.get("family") or ""),
                                "checked": bool(leaf.get("checked")),
                                "holds": bool(leaf.get("holds")),
                                "detail": str(leaf.get("detail") or ""),
                            }
                        )
            receipts = entry.get("receipt_verdict")
            if isinstance(receipts, Mapping):
                saw_receipt_verdict = True
                for key in ("cited", "resolved", "failed", "unknown"):
                    value = receipts.get(key)
                    if isinstance(value, list):
                        receipt_totals[key] += len(value)
        if acceptance_leaves:
            rep.acceptance_verdicts = acceptance_leaves
        if saw_receipt_verdict:
            rep.receipts = receipt_totals

    if ledger is not None or rows is not None:
        rep.stop_reasons = sorted(set(ledger_stop) | set(stop_from_events))

    if ctx.run_id:
        workspace = await _get_json(client, f"{urls}/runs/{ctx.run_id}/workspace-changes")
        if isinstance(workspace, Mapping) and workspace.get("available") is True:
            files = workspace.get("files")
            artifacts: list[dict[str, str]] = []
            if isinstance(files, list):
                for item in files:
                    if not isinstance(item, Mapping):
                        continue
                    path, status = item.get("path"), item.get("status")
                    if isinstance(path, str) and path and isinstance(status, str) and status:
                        artifacts.append({"path": path, "change": status})
            rep.artifacts = artifacts

    return extras


# ---------------------------------------------------------------------------
# Expectation evaluation
# ---------------------------------------------------------------------------


def _leaves_summary(rep: ScenarioReport) -> str:
    if rep.acceptance_verdicts is None:
        return "no acceptance verdict recorded"
    return "; ".join(f"{leaf['family']}:checked={leaf['checked']},holds={leaf['holds']} ({leaf['detail'][:120]})" for leaf in rep.acceptance_verdicts)


def _eval_direct_answer(rep: ScenarioReport, extra: dict[str, Any]) -> tuple[bool | None, str]:
    if rep.delegations is None or rep.event_persistence is None:
        return None, "cannot evaluate: delegation ledger or persisted event stream unreadable"
    events_total = rep.event_persistence.get("total")
    if rep.run_status == "success" and rep.delegations == 0 and events_total == 0:
        return True, "run reached success with 0 ledger delegations and 0 persisted subagent events"
    return False, f"run_status={rep.run_status}, delegations={rep.delegations}, subagent_events={events_total}"


def _eval_single_delegation(rep: ScenarioReport, extra: dict[str, Any]) -> tuple[bool | None, str]:
    if rep.delegations is None:
        return None, "cannot evaluate: delegation ledger unreadable"
    if rep.event_persistence is None:
        return None, "cannot evaluate: persisted event stream unreadable"
    if rep.delegations != 1:
        return False, f"expected exactly 1 delegation, measured {rep.delegations}"
    if rep.tokens_by_child is None:
        return False, f"delegation recorded but no persisted subagent.end events (event total={rep.event_persistence.get('total')})"
    if not rep.models:
        return False, "subagent.end carried no model name"
    measured = sum(1 for child in rep.tokens_by_child if child["total_tokens"] is not None or child["input_tokens"] is not None or child["output_tokens"] is not None)
    return True, f"1 delegation completed; models={rep.models}; {measured}/{len(rep.tokens_by_child)} children reported provider usage"


def _eval_parallel_fanout(rep: ScenarioReport, extra: dict[str, Any]) -> tuple[bool | None, str]:
    if rep.delegations is None or rep.event_persistence is None:
        return None, "cannot evaluate: delegation ledger or persisted event stream unreadable"
    ends = rep.event_persistence.get("counts", {}).get("subagent.end", 0)
    if rep.delegations != 3:
        return False, f"expected 3 delegations, measured {rep.delegations}"
    if ends != 3:
        return False, f"expected 3 persisted subagent.end events, measured {ends}"
    overlap = extra.get("overlap_pairs")
    overlap_note = f"measured overlap: {overlap} task pair(s) had intersecting event windows" if overlap is not None else "overlap not measurable from persisted timestamps"
    return True, f"3 delegations completed; {overlap_note}"


def _eval_quick_category(rep: ScenarioReport, extra: dict[str, Any]) -> tuple[bool | None, str]:
    if rep.delegations is None:
        return None, "cannot evaluate: delegation ledger unreadable"
    if rep.delegations != 1:
        return False, f"expected exactly 1 delegation, measured {rep.delegations}"
    if not rep.models:
        return False, "delegation completed but no model name was recorded in subagent.end"
    return True, f"quick-category delegation completed via {rep.models}"


def _eval_turn_capped(rep: ScenarioReport, extra: dict[str, Any]) -> tuple[bool | None, str]:
    if rep.stop_reasons is None:
        return None, "cannot evaluate: neither the ledger nor the event stream was readable"
    if "turn_capped" in rep.stop_reasons:
        return True, f"measured stop_reasons={rep.stop_reasons}, run_status={rep.run_status}"
    measured = rep.stop_reasons or "none"
    return False, f"expected stop_reason 'turn_capped', measured {measured} (run_status={rep.run_status})"


def _eval_acceptance_leaf(rep: ScenarioReport, *, family: str, want_holds: bool) -> tuple[bool | None, str]:
    if rep.delegations is None:
        return None, "cannot evaluate: delegation ledger unreadable"
    if rep.acceptance_verdicts is None:
        return False, f"delegation ledger carries no acceptance verdict (delegations={rep.delegations})"
    leaf = next((leaf for leaf in rep.acceptance_verdicts if leaf.get("family") == family), None)
    if leaf is None:
        return False, f"no {family} leaf in the recorded verdicts ({_leaves_summary(rep)})"
    if not leaf["checked"]:
        return False, f"{family} landed UNVERIFIED (checked=false): {leaf['detail']}"
    if bool(leaf["holds"]) != want_holds:
        return False, f"{family} checked, holds={leaf['holds']} (expected {want_holds}): {leaf['detail']}"
    return True, f"{family} checked={leaf['checked']} holds={leaf['holds']}: {leaf['detail']}"


def _eval_batch(rep: ScenarioReport, extra: dict[str, Any]) -> tuple[bool | None, str]:
    rows = extra.get("batches")
    if rows is None:
        return None, "cannot evaluate: thread-scoped batch list unreadable"
    if not rows:
        return False, "no batch rows persisted for this thread after the batch run"
    for row in rows:
        status = str(row.get("status") or "")
        counts = row.get("counts") if isinstance(row.get("counts"), Mapping) else {}
        cancelled = status == "cancelled" or row.get("cancel_requested_at") is not None or (_as_int(counts.get("cancelled")) or 0) > 0
        if cancelled:
            return True, f"batch {row.get('id')} status={status} counts={dict(counts)}"
    first = rows[0]
    return False, f"batch observed but not cancelled: id={first.get('id')} status={first.get('status')} counts={first.get('counts')}"


def _eval_control(rep: ScenarioReport, extra: dict[str, Any]) -> tuple[bool | None, str]:
    record = extra.get("record")
    if record is None:
        return None, "cannot evaluate: no control record was observed"
    status = str(record.get("status") or "")
    completed = record.get("completed_at")
    visible = extra.get("listing_visible")
    detail = f"control record status={status} completed_at={completed} listing_visible={visible}"
    if extra.get("result") is not None:
        detail += f" result={json.dumps(extra['result'])[:200]}"
    if status == "completed" and completed:
        return True, detail
    return False, detail


def _evaluate(scenario: Scenario, rep: ScenarioReport, extra: dict[str, Any]) -> tuple[bool | None, str]:
    if scenario.key == "direct_answer":
        return _eval_direct_answer(rep, extra)
    if scenario.key == "single_delegation":
        return _eval_single_delegation(rep, extra)
    if scenario.key == "parallel_fanout_3":
        return _eval_parallel_fanout(rep, extra)
    if scenario.key == "quick_category":
        return _eval_quick_category(rep, extra)
    if scenario.key == "turn_capped":
        return _eval_turn_capped(rep, extra)
    if scenario.key == "acceptance_tests_pass":
        return _eval_acceptance_leaf(rep, family="tests_passed", want_holds=True)
    if scenario.key == "acceptance_tests_fail":
        return _eval_acceptance_leaf(rep, family="tests_passed", want_holds=False)
    if scenario.key == "acceptance_file_pass":
        return _eval_acceptance_leaf(rep, family="file_written", want_holds=True)
    if scenario.key == "acceptance_file_fail":
        return _eval_acceptance_leaf(rep, family="file_written", want_holds=False)
    if scenario.key == "batch_flush_cancel":
        return _eval_batch(rep, extra)
    if scenario.key == "control_plane_spawn":
        return _eval_control(rep, extra)
    return None, f"no evaluator for scenario {scenario.key!r}"  # pragma: no cover - catalogue is closed


# ---------------------------------------------------------------------------
# Scenario runners
# ---------------------------------------------------------------------------


def _apply_outcome(scenario: Scenario, rep: ScenarioReport, extra: dict[str, Any], notes: list[str]) -> None:
    satisfied, detail = _evaluate(scenario, rep, extra)
    for note in notes:
        detail = f"{detail} | {note}"
    rep.detail = detail
    if satisfied is True:
        rep.outcome = "met"
        rep.expectation_satisfied = True
    elif satisfied is False:
        rep.outcome = "unmet"
        rep.expectation_satisfied = False
    else:
        rep.outcome = "incomplete"
        rep.expectation_satisfied = None


async def _run_prompt_scenario(client: httpx.AsyncClient, base_url: str, scenario: Scenario) -> ScenarioReport:
    rep = ScenarioReport(key=scenario.key, title=scenario.title, expectation=scenario.expectation, stream_modes=list(STREAM_MODES))
    extra: dict[str, Any] = {}
    notes: list[str] = []
    created: str | None = None
    try:
        if scenario.kind == "managed_prompt":
            name = f"{scenario.subagent_name_prefix}-{uuid.uuid4().hex[:6]}"
            created = name
            r = await _request(client, "POST", f"{base_url}/api/subagents", json=_managed_definition(name))
            if r.status_code >= 400:
                rep.outcome = "refused"
                rep.detail = f"managed subagent create refused: HTTP {r.status_code}: {r.text[:300]}"
                return rep
            prompt = (scenario.prompt or "").format(name=name)
        elif scenario.kind == "batch":
            features = await _get_json(client, f"{base_url}/api/features")
            batches_state = features.get("subagent_batches") if isinstance(features, Mapping) else None
            if not isinstance(batches_state, Mapping) or not batches_state.get("enabled"):
                rep.outcome = "refused"
                rep.detail = f"durable subagent batches are not enabled on this deployment: {json.dumps(batches_state)}"
                return rep
            extra["features"] = dict(batches_state)
            prompt = scenario.prompt or ""
        else:
            prompt = scenario.prompt or ""

        ctx = await _drive_run(client, base_url, prompt=prompt, timeout=scenario.timeout_seconds, scenario_key=scenario.key)
        rep.thread_id = ctx.thread_id
        rep.run_id = ctx.run_id
        rep.run_status = ctx.run_status
        rep.wall_seconds = ctx.wall_seconds
        if ctx.timed_out:
            notes.append(f"stream/poll deadline of {scenario.timeout_seconds:.0f}s was hit; run status may not be terminal")
        if ctx.sse_errors:
            notes.append(f"sse error frames: {ctx.sse_errors[:3]}")
        notes.append(f"live custom task frames observed: {ctx.live_task_events}")

        collection = await _collect_measurements(client, base_url, ctx, rep)
        extra.update(collection)

        if scenario.kind == "batch":
            rows = await _get_rows(client, f"{base_url}/api/threads/{ctx.thread_id}/subagent-batches")
            extra["batches"] = rows
    finally:
        if created:
            deleted = await _request(client, "DELETE", f"{base_url}/api/subagents/{created}")
            notes.append(f"cleanup: managed subagent {created} delete HTTP {deleted.status_code}")

    _apply_outcome(scenario, rep, extra, notes)
    return rep


async def _run_control_scenario(client: httpx.AsyncClient, base_url: str, scenario: Scenario) -> ScenarioReport:
    rep = ScenarioReport(key=scenario.key, title=scenario.title, expectation=scenario.expectation, stream_modes=list(STREAM_MODES))
    body = {
        "parent_agent_id": "subagent-probe",
        "objective": CONTROL_OBJECTIVE,
        "role": "general-purpose",
        "timeout_seconds": 300,
    }
    started = time.monotonic()
    spawn = await _request(client, "POST", f"{base_url}/api/subagents/control/spawn", json=body)
    if spawn.status_code >= 400:
        rep.outcome = "refused"
        rep.detail = f"control spawn refused: HTTP {spawn.status_code}: {spawn.text[:300]}"
        return rep
    record = _as_mapping(spawn.json())
    subagent_id = record.get("subagent_id") if record else None
    if not isinstance(subagent_id, str) or not subagent_id:
        rep.outcome = "error"
        rep.detail = f"spawn response carried no subagent_id: {json.dumps(record)[:300]}"
        return rep

    deadline = time.monotonic() + scenario.timeout_seconds
    final = record or {}
    while True:
        status = str(final.get("status") or "")
        if final.get("completed_at") or status in CONTROL_TERMINAL_STATUSES:
            break
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        detail = await _get_json(client, f"{base_url}/api/subagents/control/{subagent_id}")
        if isinstance(detail, Mapping):
            final = detail

    listing = await _get_rows(client, f"{base_url}/api/subagents/control")
    extra: dict[str, Any] = {
        "record": final,
        "listing_visible": listing is not None and any(str(row.get("subagent_id")) == subagent_id for row in listing),
    }
    status = str(final.get("status") or "")
    if status not in CONTROL_TERMINAL_STATUSES:
        cleanup = await _request(client, "POST", f"{base_url}/api/subagents/control/{subagent_id}/cancel", json={"reason": "probe cleanup"})
        extra["cleanup_cancel_http"] = cleanup.status_code
    elif final.get("completed_at"):
        result = await _get_json(client, f"{base_url}/api/subagents/control/{subagent_id}/result")
        if result is not None:
            extra["result"] = result

    rep.thread_id = None
    rep.run_id = subagent_id
    rep.run_status = status or None
    rep.wall_seconds = round(time.monotonic() - started, 3)
    _apply_outcome(scenario, rep, extra, [])
    return rep


async def run_scenario(client: httpx.AsyncClient, base_url: str, scenario: Scenario) -> ScenarioReport:
    if scenario.kind == "control":
        return await _run_control_scenario(client, base_url, scenario)
    return await _run_prompt_scenario(client, base_url, scenario)


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


async def _alpha_version(client: httpx.AsyncClient, base_url: str) -> str | None:
    body = await _get_json(client, f"{base_url}/api/ops/version")
    if isinstance(body, Mapping):
        version = body.get("version")
        if isinstance(version, str) and version:
            return version
    return None


async def run_probe(args: argparse.Namespace, scenarios: list[Scenario]) -> tuple[dict[str, Any], Path]:
    report = ProbeReport(
        run_id=new_run_id(),
        started_at=_utc_now(),
        base_url=args.base_url.rstrip("/"),
        platform=platform.platform(),
        stream_modes=list(STREAM_MODES),
    )
    report.notes.append("queue_wait_seconds is deliberately null everywhere: per-delegation queue wait is uninstrumented until plan W3.a (see UNINSTRUMENTED_FIELDS in scripts/support/subagent_probe_report.py)")
    report.notes.append("live custom task frames count the probe's own SSE consumption; event_persistence compares them against persisted subagent.* rows")
    report.notes.append(
        "every lead-agent run is created with autonomous=true (RunCreateRequest.autonomous): the server applies subagent_enabled after context sanitisation, "
        "so the `task` tool is present and a zero-delegation measurement is about the model, not a missing tool"
    )

    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    unattempted: list[str] = [scenario.key for scenario in scenarios]

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0), headers=headers) as client:
        for scenario in scenarios:
            unattempted.remove(scenario.key)
            effective = scenario
            if args.timeout and args.timeout > 0:
                effective = replace(scenario, timeout_seconds=args.timeout)
            try:
                rep = await run_scenario(client, args.base_url.rstrip("/"), effective)
            except GatewayUnreachable as exc:
                rep = ScenarioReport(
                    key=scenario.key,
                    title=scenario.title,
                    expectation=scenario.expectation,
                    stream_modes=list(STREAM_MODES),
                    outcome="incomplete",
                    detail=f"gateway unreachable: {exc}",
                )
                report.scenarios.append(rep)
                break
            except Exception as exc:  # noqa: BLE001 - a probe bug is a scenario finding, not a crash
                rep = ScenarioReport(
                    key=scenario.key,
                    title=scenario.title,
                    expectation=scenario.expectation,
                    stream_modes=list(STREAM_MODES),
                    outcome="error",
                    detail=f"probe error: {type(exc).__name__}: {exc}",
                )
            report.scenarios.append(rep)
        try:
            report.alpha_version = await _alpha_version(client, args.base_url.rstrip("/"))
        except GatewayUnreachable:
            # Metadata, not a measurement gate: a Gateway that died after the
            # loop (or stayed dead through it) must still yield the report the
            # contract promises - `incomplete` with a reason - and never a
            # traceback with nothing on disk. Measured 2026-10-17: a real run
            # died exactly here and lost every scenario it had completed.
            report.alpha_version = None

    failed = [rep for rep in report.scenarios if rep.outcome in ("error", "incomplete")]
    if failed or unattempted:
        report.verdict = "incomplete"
        reasons: list[str] = []
        if failed:
            reasons.append(f"{failed[0].key}: {failed[0].detail}")
        if unattempted:
            reasons.append(f"not attempted after the probe stopped: {', '.join(unattempted)}")
        report.incomplete_reason = "; ".join(reasons)[:2000]
    else:
        report.verdict = "complete"

    report.ended_at = _utc_now()
    payload = to_jsonable(report)
    report.schema_violations = validate_report(payload)
    payload = to_jsonable(report)

    out_dir = Path(args.out_dir) / report.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(payload), encoding="utf-8")
    return payload, out_dir


def _print_summary(payload: Mapping[str, Any], out_dir: Path) -> None:
    print(f"subagent delegation probe: {payload.get('verdict')} (schema v{payload.get('schema_version')})")
    if payload.get("incomplete_reason"):
        print(f"  incomplete: {payload['incomplete_reason']}")
    for scenario in payload.get("scenarios") or []:
        satisfied = scenario.get("expectation_satisfied")
        mark = "met" if satisfied is True else ("unmet" if satisfied is False else str(scenario.get("outcome")))
        print(f"  [{mark:9s}] {scenario.get('key')}: {str(scenario.get('detail') or '')[:160]}")
    violations = payload.get("schema_violations") or []
    for violation in violations:
        print(f"  schema violation: {violation}")
    print(f"report: {out_dir / 'report.json'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure real subagent delegation behavior against a running Gateway (W1 delegation telemetry probe).")
    parser.add_argument("--base-url", default=os.environ.get("ALPHA_SUBAGENT_PROBE_BASE_URL", DEFAULT_BASE_URL), help=f"Gateway base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--token", default=os.environ.get("ALPHA_SUBAGENT_PROBE_TOKEN") or None, help="Bearer token; omit for an auth-disabled local deployment")
    parser.add_argument("--scenarios", default="", help="comma-separated scenario keys (default: the full catalogue)")
    parser.add_argument("--list", action="store_true", dest="list_scenarios", help="print the scenario catalogue and exit")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUTPUT_ROOT), help="report output root; one subdirectory per probe run id")
    parser.add_argument("--timeout", type=float, default=0.0, help="override every scenario's run timeout, in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    catalogue = scenario_catalogue()
    if args.list_scenarios:
        for scenario in catalogue:
            print(f"{scenario.key:24s} {scenario.title}")
        return 0
    selected = _select_scenarios(catalogue, args.scenarios)
    payload, out_dir = asyncio.run(run_probe(args, selected))
    _print_summary(payload, out_dir)
    return report_exit_code(payload)


if __name__ == "__main__":
    raise SystemExit(main())
