"""Assign one real task to a live Gateway and watch it complete end-to-end.

This is the operator/debugger entry point for "give the agent real work and
monitor everything while it happens". It:

1. creates (or reuses) a thread,
2. streams a run over the same SSE surface the Web UI uses
   (``POST /api/threads/{id}/runs/stream``),
3. prints a timestamped timeline of every SSE frame as it arrives,
4. fetches the durable run record, workspace changes, and final message,
5. prints a verdict and exits non-zero if the run did not honestly complete.

Pure helpers (``parse_sse``, ``evaluate``) are unit-tested in
``tests/test_e2e_real_task_driver.py``; this module stays importable without
a live Gateway so those tests run offline.

Usage (from ``backend/``)::

    uv run python scripts/e2e_real_task.py
    uv run python scripts/e2e_real_task.py --assistant-id coder
    uv run python scripts/e2e_real_task.py --thread-id <existing-thread-id>
    uv run python scripts/e2e_real_task.py --task "List the files in the repo root"

Exit codes: 0 = verified success, 1 = run failed / SSE error frame,
2 = timed out or stream closed without a terminal frame, 3 = request/setup
failure (4xx/5xx before a run started).
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Iterable, Iterator
from typing import Any

import httpx

DEFAULT_TASK = "Create a file named e2e-task-1.md inside your outputs directory containing a level-1 heading '# E2E Task 1' and a numbered list with exactly two items, then read the file back and reply with its exact contents."

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_INCOMPLETE = 2
EXIT_REQUEST_FAILED = 3


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Yield ``(event_name, data)`` tuples from an SSE line stream.

    Follows the ``text/event-stream`` framing rules this Gateway emits:
    ``event:`` names a frame, ``data:`` lines concatenate (newline-joined),
    a blank line dispatches, and ``:`` comment lines are ignored. An event
    with no ``event:`` line dispatches under the default name ``message``.
    A trailing frame not terminated by a blank line is dispatched when the
    stream ends, so a truncated connection still surfaces its last frame
    instead of silently vanishing.
    """
    event_name = "message"
    data_parts: list[str] = []

    def _dispatch() -> tuple[str, str] | None:
        if event_name == "message" and not data_parts:
            return None
        payload = "\n".join(data_parts)
        return (event_name, payload)

    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            frame = _dispatch()
            if frame is not None:
                yield frame
            event_name, data_parts = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_parts.append(line[5:].lstrip())
        # Unknown fields (id:, retry:) are ignored per the SSE spec.
    frame = _dispatch()
    if frame is not None:
        yield frame


def evaluate(events: list[tuple[str, str]], run_status: str | None, run_error: str | None) -> list[str]:
    """Return a list of problems; empty means the run honestly completed.

    A completed run is never "verified" — this checks the *transport and
    durable status* only: no SSE ``error`` frame, a terminal ``end`` frame,
    and a durable ``success`` status with no stored error.
    """
    problems: list[str] = []
    names = [name for name, _ in events]
    error_frames = [(name, data) for name, data in events if name == "error"]
    if error_frames:
        first = error_frames[0][1]
        problems.append(f"{len(error_frames)} SSE error frame(s); first payload: {first[:400]}")
    if "end" not in names:
        problems.append("stream closed without a terminal 'end' frame")
    if run_status is None:
        problems.append("run record not found after streaming")
    elif run_status != "success":
        problems.append(f"durable run status={run_status!r} error={run_error!r}")
    return problems


def _preview(data: str, limit: int = 160) -> str:
    data = data.replace("\n", "\\n")
    return data if len(data) <= limit else data[:limit] + f"... (+{len(data) - limit} chars)"


def _create_thread(client: httpx.Client, base_url: str) -> str:
    resp = client.post(f"{base_url}/api/threads", json={"metadata": {"purpose": "e2e-real-task"}})
    resp.raise_for_status()
    return resp.json()["thread_id"]


def extract_artifact_paths(payload: Any) -> list[str]:
    """Read changed artifact paths from a workspace-changes payload.

    Current shape: ``{summary, files: [{path, root, status, ...}, ...]}``
    (verified live against ``GET /runs/{id}/workspace-changes``); legacy
    ``produced_paths`` / ``paths`` keys are accepted so an older payload
    still verifies instead of silently reporting "(none)".
    """
    if not isinstance(payload, dict):
        return []
    files = payload.get("files")
    if isinstance(files, list):
        return [str(f.get("path")) for f in files if isinstance(f, dict) and f.get("path")]
    for key in ("produced_paths", "paths"):
        value = payload.get(key)
        if isinstance(value, list):
            return [str(v) for v in value]
    return []


def _first_ai_text(messages_payload: Any) -> str | None:
    """Best-effort extraction of the final assistant text from run messages.

    Handles both bare LangChain message dicts and the run-event envelope the
    Gateway serves from ``GET /runs/{id}/messages``
    (``{event_type, content: {type: "ai", ...}}``).
    """
    if isinstance(messages_payload, dict):
        items = messages_payload.get("data", [])
    elif isinstance(messages_payload, list):
        items = messages_payload
    else:
        return None
    text: str | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        message = item
        if message.get("type") not in ("ai", "AIMessage"):
            nested = item.get("content")
            if isinstance(nested, dict) and nested.get("type") in ("ai", "AIMessage"):
                message = nested
            else:
                continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            text = content
        elif isinstance(content, list):
            joined = "".join(c.get("text", "") for c in content if isinstance(c, dict))
            if joined.strip():
                text = joined
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assign a real task to the live Alpha agent and monitor it end-to-end.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--thread-id", default=None, help="Existing thread to run on (default: create a fresh one).")
    parser.add_argument("--assistant-id", default="lead_agent", help="assistant_id to bind, e.g. a roster bot name.")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--timeout", type=float, default=420.0, help="Seconds to wait for the stream to finish.")
    args = parser.parse_args(argv)

    t0 = time.monotonic()

    def log(stage: str, detail: str) -> None:
        print(f"[+{time.monotonic() - t0:7.1f}s] {stage}: {detail}", flush=True)

    try:
        with httpx.Client(timeout=60.0) as client:
            thread_id = args.thread_id or _create_thread(client, args.base_url)
            log("thread", thread_id)

            body = {
                "assistant_id": args.assistant_id,
                "input": {"messages": [{"role": "user", "content": args.task}]},
                "metadata": {"purpose": "e2e-real-task", "driver": "scripts/e2e_real_task.py"},
                "stream_mode": ["messages-tuple", "values", "custom"],
                "on_disconnect": "continue",
            }
            events: list[tuple[str, str]] = []
            run_id: str | None = None
            timed_out = False
            threads_url = f"{args.base_url.rstrip('/')}/api/threads/{thread_id}"
            with client.stream("POST", f"{threads_url}/runs/stream", json=body, timeout=httpx.Timeout(10.0, read=args.timeout)) as resp:
                if resp.status_code >= 400:
                    raw = resp.read().decode("utf-8", "replace")
                    log("stream", f"HTTP {resp.status_code}: {raw[:600]}")
                    return EXIT_REQUEST_FAILED
                location = resp.headers.get("content-location") or resp.headers.get("Content-Location")
                if location:
                    run_id = location.rstrip("/").split("/")[-1] or None
                log("stream", f"HTTP {resp.status_code}, run_id={run_id}")
                line_iter = resp.iter_lines()
                deadline = time.monotonic() + args.timeout

                def _timed(source: Iterator[str]) -> Iterator[str]:
                    nonlocal timed_out
                    for line in source:
                        if time.monotonic() > deadline:
                            timed_out = True
                            return
                        yield line

                seen: dict[str, int] = {}
                for name, data in parse_sse(_timed(line_iter)):
                    events.append((name, data))
                    seen[name] = seen.get(name, 0) + 1
                    if name in ("error", "end", "metadata", "gap") or seen[name] <= 3 or seen[name] % 25 == 0:
                        log(f"sse#{len(events)} {name}", f"#{seen[name]} {_preview(data)}")
                    if name == "metadata" and not run_id:
                        try:
                            run_id = str(json.loads(data).get("run_id") or "") or run_id
                        except (ValueError, TypeError):
                            pass
            if timed_out:
                log("stream", f"timed out after {args.timeout}s (run left to continue: on_disconnect=continue)")
                return EXIT_INCOMPLETE

            # Durable record may finalize a beat after END; poll briefly.
            run_status: str | None = None
            run_error: str | None = None
            for _ in range(12):
                if run_id:
                    rr = client.get(f"{threads_url}/runs/{run_id}")
                    if rr.status_code == 200:
                        payload = rr.json()
                        run_status = payload.get("status")
                        run_error = payload.get("error")
                        if run_status in ("success", "error", "interrupted", "timeout"):
                            break
                time.sleep(1.0)
            log("run", f"status={run_status} error={run_error!r}")

            wc = client.get(f"{threads_url}/runs/{run_id}/workspace-changes") if run_id else None
            if wc is not None and wc.status_code == 200:
                changes = wc.json()
                summary = changes.get("summary") if isinstance(changes, dict) else None
                produced = extract_artifact_paths(changes)
                log("workspace-changes", f"summary={json.dumps(summary) if summary else '(none)'}")
                log("workspace-changes", json.dumps(produced)[:600] if produced else "(no files changed)")
                if any("e2e-task-1.md" in str(p) for p in produced):
                    log("workspace-changes", "expected artifact e2e-task-1.md WAS produced")
            elif wc is not None:
                log("workspace-changes", f"HTTP {wc.status_code}")

            if run_id:
                mr = client.get(f"{threads_url}/runs/{run_id}/messages")
                if mr.status_code == 200:
                    text = _first_ai_text(mr.json())
                    if text:
                        log("reply", text[:600].replace("\n", "\\n"))
    except httpx.HTTPError as exc:
        log("request", f"failed: {exc}")
        return EXIT_REQUEST_FAILED

    problems = evaluate(events, run_status, run_error)
    if problems:
        for p in problems:
            print(f"  FAIL: {p}", flush=True)
        incomplete = "terminal 'end'" in " ".join(problems) or timed_out
        return EXIT_INCOMPLETE if incomplete else EXIT_RUN_FAILED
    print("  VERDICT: stream completed with terminal end, durable status=success, no error frames.", flush=True)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
