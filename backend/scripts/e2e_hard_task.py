"""Give Alpha a hard, real task and watch the whole execution lifecycle happen.

This is not a mock. It boots the real Gateway through the real lifespan, posts
a real run over the same SSE surface the Web UI uses, and records every frame
the server actually emitted: tool calls, tool results, tool errors, custom
progress events, the terminal status and the durable run record.

The task is a genuine multi-part engineering job -- research a spec, write
several files, run a test suite, read failures, correct them, re-run, verify an
artifact on real data, and report honestly -- because the lifecycle defects this
harness exists to surface only appear when the agent has to *use* tools, hit a
failure, and recover from it.

Usage (from ``backend/``)::

    python scripts/e2e_hard_task.py
    python scripts/e2e_hard_task.py --task "..." --timeout 900
    python scripts/e2e_hard_task.py --json logs/hard_task_run.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

HOST = "127.0.0.1"
PORT = int(os.environ.get("ALPHA_E2E_PORT", "8098"))
BASE = f"http://{HOST}:{PORT}"

#: The real task.
#:
#: Deliberately scoped to the capabilities this deployment actually has.
#: ``config.yaml`` sets ``sandbox.allow_host_bash: false`` and
#: ``allow_in_process_repl: false``, so ``bash`` and ``python_repl`` are
#: legitimately unbound and the grounding gate correctly refuses them -- verified
#: directly (``is_host_bash_allowed() is False``; 144 tools offered, none of them
#: a shell). Asking a run to execute code on this deployment tests the operator's
#: security choice, not the agent.
#:
#: What remains is still genuinely hard and exercises the lifecycle: research over
#: the network, several files, a read-modify-write correction loop against a
#: write-guard, arithmetic self-verification, and artifact delivery. Every clause
#: maps to a capability that must be *used*, not described.
HARD_TASK = """\
Do this job completely, using tools. Do not describe it - do it.

1. Use web search to find out what Unicode general category the character
   U+2019 (RIGHT SINGLE QUOTATION MARK) belongs to, and which Unicode version
   introduced it. Report exactly what the sources say. If the lookup fails, say
   so explicitly instead of guessing.

2. Create /mnt/user-data/outputs/unicode_notes.md with a short markdown table
   whose rows are: the code point, its general category, the Unicode version,
   and a one-line description. Write the file with the write tool.

3. Create /mnt/user-data/outputs/count_report.md containing the counts of the
   characters in this exact sentence, which you must compute yourself: the
   number of characters, the number of words, the number of uppercase letters,
   and the number of distinct characters. Show your working.

4. Re-read both files with the read tool and verify the numbers in
   count_report.md against what you wrote. If you find an arithmetic mistake,
   correct the file with str_replace (you must read the file before editing it)
   and state that you corrected it.

5. Call present_files on both output files so they are delivered to the user.

Finish with a short honest summary: what the search returned, both file paths,
your verification result, and anything you could not confirm.
"""


# ---------------------------------------------------------------------------
# SSE framing (own implementation; the Gateway's framing is pinned by
# tests/test_e2e_real_task_driver.py for the original driver)
# ---------------------------------------------------------------------------


async def parse_sse(lines: Any) -> Any:
    """Frame an SSE line stream into ``(event_name, data)`` pairs.

    Async, because the caller is streaming from a live socket. A frame not
    terminated by a blank line is dispatched when the stream ends, so a
    truncated connection still surfaces its last frame rather than dropping it.
    """
    event_name = "message"
    data_parts: list[str] = []

    def dispatch() -> tuple[str, str] | None:
        if event_name == "message" and not data_parts:
            return None
        return (event_name, "\n".join(data_parts))

    async for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            frame = dispatch()
            if frame is not None:
                yield frame
            event_name, data_parts = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_parts.append(line[5:].lstrip())
    frame = dispatch()
    if frame is not None:
        yield frame


def _loads(data: str) -> Any:
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        return None


def _tool_names_of(payload: Any, out: list[str]) -> None:
    """Collect every tool name in one SSE frame.

    A frame is often a **list** of messages, and a single message may carry
    several `tool_calls`. The first version of this harness only looked for a
    top-level dict `name`, so it reported `distinct_tools_used: 0` on a run that
    had in fact written two files with `write_file` -- a harness that reports a
    clean sheet for a real run is worse than no harness.
    """
    if isinstance(payload, list):
        for item in payload:
            _tool_names_of(item, out)
        return
    if not isinstance(payload, dict):
        return

    # An executed tool result is `type: "tool"` and names itself.
    if payload.get("type") in ("tool", "ToolMessage"):
        name = payload.get("name")
        if isinstance(name, str) and name:
            out.append(name)
    # A pending call carries `tool_calls: [{name, args}, ...]`.
    for call in payload.get("tool_calls") or []:
        if isinstance(call, dict) and isinstance(call.get("name"), str):
            out.append(call["name"])
    # Streamed chunk deltas carry `tool_call_chunks`.
    for chunk in payload.get("tool_call_chunks") or []:
        if isinstance(chunk, dict) and isinstance(chunk.get("name"), str):
            out.append(chunk["name"])
    # A receipt names the tool it belongs to.
    receipt = payload.get("additional_kwargs", {}).get("alpha_tool_receipt") if isinstance(payload.get("additional_kwargs"), dict) else None
    if isinstance(receipt, dict) and isinstance(receipt.get("tool_name"), str):
        out.append(receipt["tool_name"])

    for key in ("message", "value", "state"):
        nested = payload.get(key)
        if isinstance(nested, (dict, list)):
            _tool_names_of(nested, out)


def _is_error(payload: Any) -> bool:
    if isinstance(payload, dict):
        if payload.get("status") == "error" or payload.get("error"):
            return True
        content = payload.get("content")
        if isinstance(content, str) and re.match(r"^\s*(error|traceback|exception)\b", content, re.I):
            return True
        if isinstance(content, str) and "Error:" in content:
            return True
    return False


class Timeline:
    """Records the run so the verdict is derived from evidence, not impression."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {}
        self.tool_calls: dict[str, int] = {}
        self.tool_errors: dict[str, int] = {}
        self.first_tool_ms: float | None = None
        self.error_frames: list[str] = []
        self.timed_out = False

    def add(self, name: str, data: str) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1
        payload = _loads(data)
        if name == "error":
            self.error_frames.append(data[:600])
        found: list[str] = []
        _tool_names_of(payload, found)
        # A streamed chunk and its executed result name the same tool; count
        # unique names per frame so one call is not counted three times.
        for tool in sorted(set(found)):
            self.tool_calls[tool] = self.tool_calls.get(tool, 0) + 1
            if self.first_tool_ms is None:
                self.first_tool_ms = round((time.monotonic() - self.t0) * 1000, 1)
            if _is_error(payload):
                self.tool_errors[tool] = self.tool_errors.get(tool, 0) + 1
        self.events.append({"t_ms": round((time.monotonic() - self.t0) * 1000, 1), "event": name, "data": data[:4000]})

    def summary(self) -> dict[str, Any]:
        return {
            "frames": len(self.events),
            "by_event": dict(sorted(self.counts.items())),
            "tool_calls": dict(sorted(self.tool_calls.items(), key=lambda kv: -kv[1])),
            "tool_errors": dict(sorted(self.tool_errors.items(), key=lambda kv: -kv[1])),
            "distinct_tools_used": len(self.tool_calls),
            "total_tool_frames": sum(self.tool_calls.values()),
            "first_tool_ms": self.first_tool_ms,
            "error_frames": len(self.error_frames),
        }


async def run(args: argparse.Namespace) -> int:
    import uvicorn

    from app.gateway.app import app

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", lifespan="on", access_log=False)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    deadline = time.time() + 240
    while not server.started and time.time() < deadline:
        await asyncio.sleep(0.5)
    if not server.started:
        server.should_exit = True
        await task
        print("BOOT_FAILED: the Gateway never started", flush=True)
        return 2
    print(f"[+0.0s] BOOT_OK on {BASE}", flush=True)

    timeline = Timeline()
    verdict: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            tr = await client.post(f"{BASE}/api/threads", json={"metadata": {"purpose": "e2e-hard-task"}})
            tr.raise_for_status()
            thread_id = tr.json()["thread_id"]
            print(f"[+{time.monotonic() - timeline.t0:.1f}s] thread={thread_id}", flush=True)

            body = {
                "assistant_id": "lead_agent",
                "input": {"messages": [{"role": "user", "content": args.task}]},
                "metadata": {"purpose": "e2e-hard-task"},
                "stream_mode": ["messages-tuple", "values", "custom"],
                "on_disconnect": "continue",
            }
            run_id: str | None = None
            urls = f"{BASE}/api/threads/{thread_id}"
            deadline_at = time.monotonic() + args.timeout
            async with client.stream("POST", f"{urls}/runs/stream", json=body, timeout=httpx.Timeout(30.0, read=args.timeout)) as resp:
                if resp.status_code >= 400:
                    print(f"STREAM HTTP {resp.status_code}: {(await resp.aread()).decode('utf-8', 'replace')[:800]}", flush=True)
                    server.should_exit = True
                    await task
                    return 3
                location = resp.headers.get("content-location") or resp.headers.get("Content-Location")
                run_id = location.rstrip("/").split("/")[-1] if location else None
                print(f"[+{time.monotonic() - timeline.t0:.1f}s] stream HTTP {resp.status_code} run_id={run_id}", flush=True)

                async def timed(source: Any) -> Any:
                    async for line in source:
                        if time.monotonic() > deadline_at:
                            timeline.timed_out = True
                            return
                        yield line

                async for name, data in parse_sse(timed(resp.aiter_lines())):
                    timeline.add(name, data)
                    if name in ("error", "end", "gap") or timeline.counts[name] <= 2 or timeline.counts[name] % 40 == 0:
                        print(f"[+{time.monotonic() - timeline.t0:7.1f}s] #{timeline.counts[name]:<4} {name:<16} {data[:170]}", flush=True)
                    if name == "metadata" and not run_id:
                        payload = _loads(data)
                        if isinstance(payload, dict):
                            run_id = str(payload.get("run_id") or "") or run_id

            status: str | None = None
            error: str | None = None
            for _ in range(30):
                if run_id:
                    rr = await client.get(f"{urls}/runs/{run_id}")
                    if rr.status_code == 200:
                        p = rr.json()
                        status, error = p.get("status"), p.get("error")
                        if status in ("success", "error", "interrupted", "timeout", "canceled"):
                            break
                await asyncio.sleep(1.0)
            print(f"[+{time.monotonic() - timeline.t0:7.1f}s] durable status={status!r} error={error!r}", flush=True)

            verdict["durable_status"] = status
            verdict["durable_error"] = error

            if run_id:
                wc = await client.get(f"{urls}/runs/{run_id}/workspace-changes")
                if wc.status_code == 200:
                    payload = wc.json()
                    files = payload.get("files") or []
                    verdict["workspace_summary"] = payload.get("summary")
                    verdict["workspace_files"] = [f.get("path") for f in files if isinstance(f, dict)]
                    print(f"[+{time.monotonic() - timeline.t0:7.1f}s] workspace files: {verdict['workspace_files']}", flush=True)

                mr = await client.get(f"{urls}/runs/{run_id}/messages")
                if mr.status_code == 200:
                    payload = mr.json()
                    items = payload.get("data", []) if isinstance(payload, dict) else payload
                    text = None
                    for item in items or []:
                        if not isinstance(item, dict):
                            continue
                        msg = item
                        if msg.get("type") not in ("ai", "AIMessage"):
                            nested = item.get("content")
                            if isinstance(nested, dict) and nested.get("type") in ("ai", "AIMessage"):
                                msg = nested
                            else:
                                continue
                        content = msg.get("content")
                        if isinstance(content, str) and content.strip():
                            text = content
                        elif isinstance(content, list):
                            joined = "".join(c.get("text", "") for c in content if isinstance(c, dict))
                            if joined.strip():
                                text = joined
                    verdict["final_text"] = text
                    print("=" * 78, flush=True)
                    print("FINAL ASSISTANT TEXT:", flush=True)
                    print((text or "(none)")[:4000], flush=True)
                    print("=" * 78, flush=True)
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=120)
        except TimeoutError:
            pass

    summary = timeline.summary()
    verdict["timeline"] = summary
    verdict["error_frames_sample"] = timeline.error_frames[:5]
    verdict["timed_out"] = timeline.timed_out

    print("\n" + "=" * 78, flush=True)
    print("E2E VERDICT", flush=True)
    print("=" * 78, flush=True)
    print(json.dumps(summary, indent=2), flush=True)
    print(f"durable status      : {verdict.get('durable_status')!r}", flush=True)
    print(f"durable error       : {verdict.get('durable_error')!r}", flush=True)
    print(f"workspace files     : {verdict.get('workspace_files')}", flush=True)
    print(f"sse error frames    : {len(timeline.error_frames)}", flush=True)
    print(f"timed out           : {timeline.timed_out}", flush=True)
    distinct = summary["distinct_tools_used"]
    print(f"distinct tools used : {distinct}", flush=True)

    problems: list[str] = []
    if timeline.timed_out:
        problems.append(f"stream did not finish within {args.timeout}s")
    if timeline.error_frames:
        problems.append(f"{len(timeline.error_frames)} SSE error frame(s)")
    if status != "success":
        problems.append(f"durable status={status!r} error={error!r}")
    if distinct == 0:
        problems.append("the agent used no tools: it answered conversationally instead of doing the work")
    if not verdict.get("workspace_files"):
        problems.append("no workspace file changes were recorded")

    print("\nPROBLEMS:", flush=True)
    if problems:
        for p in problems:
            print(f"  - {p}", flush=True)
    else:
        print("  none", flush=True)

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps({"verdict": verdict, "problems": problems, "events": timeline.events[:800]}, indent=2), encoding="utf-8")
        print(f"wrote {args.json}", flush=True)

    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default=HARD_TASK)
    ap.add_argument("--timeout", type=float, default=1500.0)
    ap.add_argument("--json", default="../logs/e2e_hard_task.json")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
