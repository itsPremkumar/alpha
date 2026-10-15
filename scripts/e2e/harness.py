#!/usr/bin/env python3
"""Alpha end-to-end driver: one prompt in, one honest JSON verdict out.

This is infrastructure, not a feature. It exists so another agent can go from
"task assigned" to "result on disk" with a single command, and — more
importantly — so that result cannot be mistaken for something it is not.

    backend\\.venv\\Scripts\\python.exe scripts/e2e/harness.py "Say pong"

What it does, in order:

1. **Attach or start the stack.** `--attach` (the default) uses whatever is
   already listening. `--start` additionally launches the Gateway (and, with
   `--start-frontend`, the Next.js server) *only when they are not already
   healthy*, and remembers exactly which processes it created so teardown can
   kill the tree and nothing else.
2. **Wait for `/health`.** The Gateway's cold start is minutes, not seconds
   (`--boot-timeout`, default 420s). A Gateway that never answers is reported
   as `blocked`, never as a pass.
3. **Create a real thread** — `POST /api/threads`.
4. **Send a real prompt** — `POST /api/threads/{id}/runs/stream` with
   `stream_mode=["values","messages-tuple","custom"]`, which is the same shape
   the browser sends (`frontend/src/components/ChatView.tsx`). `custom` is
   required or subagent `task_*` progress is simply absent from the stream.
5. **Consume the SSE stream to its terminal frame**, writing the raw bytes to
   `raw.sse` and a bounded, structured index of every frame into the result.
6. **Read the server's own token accounting** from
   `GET /api/threads/{id}/token-usage` and report it beside a client-side sum
   computed from the stream. The two are reported separately on purpose; when
   they disagree the disagreement is the finding.
7. **Tear down**, killing only processes this run spawned.

The honesty contract (this is the whole point of the file)
-----------------------------------------------------------

* A completed run is never a verified run. `verdict: "pass"` means "the
  Gateway accepted a real prompt, streamed it to a terminal `end` frame that
  the server marked `ok: true`/`status: success`, and produced answer text".
  It does **not** mean the answer is correct, that the feature behind it works,
  or that anything about it has been independently confirmed.
  `verdict_scope` in the JSON says so in the file itself.
* A measurement nobody took is `null`, never `0`. If the token endpoint is
  unreachable, `tokens.server_reported` is `null` with a reason beside it — not
  an empty dict that reads as "zero tokens used".
* A truncated or timed-out stream is a failure with the partial bytes kept. It
  is never rounded up to "probably fine".
* The process exit code is `0` only for `pass`. `fail` and `blocked` both exit
  non-zero, so a CI gate or a wrapping agent cannot miss them.
* Nothing is written outside the evidence directory, and an existing evidence
  directory is never overwritten — a collision gets a fresh numbered run
  directory.

Requirements
------------

Standard library only. Run it with the backend venv interpreter (or any
Python 3.11+); it does not import `alpha`, so it is safe to run while the
Gateway is down.

```powershell
backend\\.venv\\Scripts\\python.exe scripts/e2e/harness.py "Say pong"
backend\\.venv\\Scripts\\python.exe scripts/e2e/harness.py --health-only
backend\\.venv\\Scripts\\python.exe scripts/e2e/harness.py --start "Say pong"
```

Environment overrides: `ALPHA_E2E_BASE`, `ALPHA_E2E_GATEWAY`, `ALPHA_E2E_FRONTEND`,
`ALPHA_E2E_MODEL`, `ALPHA_E2E_TIMEOUT`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GATEWAY = os.environ.get("ALPHA_E2E_GATEWAY", "http://127.0.0.1:8001")
DEFAULT_FRONTEND = os.environ.get("ALPHA_E2E_FRONTEND", "http://127.0.0.1:3000")
DEFAULT_PROMPT = os.environ.get("ALPHA_E2E_PROMPT", "Reply with exactly the word: pong")
DEFAULT_MODEL = os.environ.get("ALPHA_E2E_MODEL", "union-alpha")
DEFAULT_ASSISTANT = "lead_agent"
DEFAULT_TIMEOUT = float(os.environ.get("ALPHA_E2E_TIMEOUT", "600"))
DEFAULT_BOOT_TIMEOUT = float(os.environ.get("ALPHA_E2E_BOOT_TIMEOUT", "420"))
STREAM_MODES = ["values", "messages-tuple", "custom"]

# Frame payloads are stored in the result for diagnosis, but an unbounded copy
# of every `values` frame would make the evidence file unreadable. The raw SSE
# is always written whole, so truncation is disclosed rather than lossy.
MAX_EVENTS_STORED = 400
MAX_EVENT_PAYLOAD_CHARS = 2000

VERDICT_SCOPE = (
    "A 'pass' verdict means only that the Gateway accepted a real prompt and "
    "streamed it to a terminal frame the server itself marked ok. It is NOT a "
    "verification that the answer is correct, that a feature works, or that "
    "anything has been independently confirmed. A completed run is not a "
    "verified run."
)


# --------------------------------------------------------------------------
# process ownership
# --------------------------------------------------------------------------


def find_listener_pids(port: int) -> list[int]:
    """PIDs currently LISTENING on `port`.

    Used only to confirm teardown actually released a port this run opened. A
    wrapper chain (`uv run` -> console script -> interpreter) can leave a
    grandchild holding the socket after its parent dies, and "I called
    terminate()" is not evidence that the port came back.
    """
    cmd = ["netstat", "-ano", "-p", "TCP"] if os.name == "nt" else ["ss", "-ltnp"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=25, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids: set[int] = set()
    for line in out.splitlines():
        if "LISTEN" not in line.upper():
            continue
        if not re.search(rf"[.:]{re.escape(str(port))}\s", line):
            continue
        for token in reversed(line.split()):
            if token.isdigit():
                pids.add(int(token))
                break
    return sorted(pids)


def port_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(2.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def release_port(port: int, *, label: str) -> dict[str, Any]:
    """Kill whatever still holds `port`, then report whether it really came back."""
    before = find_listener_pids(port)
    if not before:
        return {"released": True, "killed_pids": []}
    killed: list[int] = []
    for pid in before:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30, check=False)
            else:  # pragma: no cover - POSIX is not the verified platform here
                import signal as _signal

                os.kill(pid, _signal.SIGKILL)
            killed.append(pid)
        except (OSError, subprocess.SubprocessError):
            continue
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not port_is_listening(port):
            return {"released": True, "killed_pids": killed}
        time.sleep(1.0)
    return {
        "released": False,
        "killed_pids": killed,
        "still_bound_by": find_listener_pids(port),
        "note": f"port {port} is STILL listening after teardown; another process owns it",
    }


class Spawned:
    """One process this run created, plus how to kill its whole tree."""

    def __init__(self, label: str, popen: subprocess.Popen, log_path: Path, port: int | None = None) -> None:
        self.label = label
        self.popen = popen
        self.log_path = log_path
        self.port = port
        self.terminated = False

    def terminate(self) -> dict[str, Any]:
        """Kill the process tree, then *prove* the port came back.

        Killing the tracked PID is not sufficient on its own: a wrapper chain
        can orphan a grandchild that still holds the socket, and a harness that
        reports "terminated" while a service it started keeps running has
        fabricated its own teardown. So the port is re-checked and any
        surviving listener is killed explicitly.
        """
        if self.terminated:
            return {"label": self.label, "pid": self.popen.pid, "killed": False, "note": "already terminated"}
        self.terminated = True
        pid = self.popen.pid
        method = "terminate"
        error: str | None = None
        try:
            if os.name == "nt":
                done = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                method = "taskkill /T /F"
                if done.returncode != 0:
                    error = (done.stderr or done.stdout or "").strip()[:400]
                    if self.popen.poll() is None:
                        self.popen.kill()
            else:
                import signal as _signal

                try:
                    os.killpg(os.getpgid(pid), _signal.SIGTERM)
                    method = "killpg SIGTERM"
                except (ProcessLookupError, PermissionError, OSError):
                    self.popen.terminate()
                    method = "terminate"
        except Exception as exc:  # noqa: BLE001 - teardown must not raise
            error = f"{type(exc).__name__}: {exc}"
            try:
                self.popen.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.popen.wait(timeout=15)
        except Exception:  # noqa: BLE001
            pass

        record: dict[str, Any] = {"label": self.label, "pid": pid, "killed": True, "method": method, "error": error}
        if self.port is not None:
            record["port"] = self.port
            record.update(release_port(self.port, label=self.label))
        return record


SPAWNED: list[Spawned] = []
TEARDOWN: list[dict[str, Any]] = []


def _teardown_all() -> None:
    for item in reversed(SPAWNED):
        item.terminate()


def teardown_all() -> None:
    """Release everything this run spawned and record what happened.

    Idempotent (`Spawned.terminate` guards on `terminated`), so it can be
    called explicitly *before* the result is written — the evidence file must
    carry the real teardown outcome, not an empty list — while the `finally`
    block still calls it as a safety net for an early exception.
    """
    if TEARDOWN:
        return
    records = [entry.terminate() for entry in reversed(SPAWNED)]
    TEARDOWN.extend(records)
    for record in records:
        if record.get("released") is False:
            print(f"[teardown] WARNING {record.get('note')} (pids {record.get('still_bound_by')})", flush=True)
        elif "port" in record:
            print(f"[teardown] {record['label']} :{record['port']} released (killed pids {record.get('killed_pids')})", flush=True)
    if records:
        print(f"[teardown] released {len(records)} spawned process(es)", flush=True)


def _install_signal_handlers() -> None:
    def _handler(signum: int, _frame: Any) -> None:
        _teardown_all()
        # Re-raise with the default disposition so the exit code is honest.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


@dataclass
class HttpResult:
    status: int
    headers: dict[str, str]
    body: bytes
    elapsed_s: float
    error: str | None = None

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8", "replace"))


def request(
    url: str,
    *,
    method: str = "GET",
    payload: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    cookies: dict[str, str] | None = None,
) -> HttpResult:
    """One HTTP round-trip. A transport failure becomes status 0, not an exception.

    Status 0 is reserved for "no HTTP response was produced", which is a
    categorically different fact from "the server answered 500".
    """
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    hdrs = {"Accept": "application/json", "User-Agent": "alpha-e2e-driver/1.0"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    if cookies:
        hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    if headers:
        hdrs.update(headers)

    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return HttpResult(
                status=resp.status,
                headers={k.lower(): v for k, v in resp.headers.items()},
                body=raw,
                elapsed_s=round(time.monotonic() - started, 3),
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return HttpResult(
            status=exc.code,
            headers={k.lower(): v for k, v in (exc.headers or {}).items()},
            body=raw,
            elapsed_s=round(time.monotonic() - started, 3),
        )
    except Exception as exc:  # noqa: BLE001
        return HttpResult(
            status=0,
            headers={},
            body=b"",
            elapsed_s=round(time.monotonic() - started, 3),
            error=f"{type(exc).__name__}: {exc}",
        )


def is_healthy(base: str, timeout: float = 5.0) -> tuple[bool, dict[str, Any]]:
    result = request(f"{base.rstrip('/')}/health", timeout=timeout)
    detail: dict[str, Any] = {
        "base": base,
        "status": result.status,
        "elapsed_s": result.elapsed_s,
        "error": result.error,
    }
    if result.status == 200:
        detail["body"] = result.text()[:400]
        return True, detail
    return False, detail


def wait_for_health(base: str, deadline_s: float, *, label: str, quiet: bool = False) -> tuple[bool, dict[str, Any]]:
    started = time.monotonic()
    attempts = 0
    last: dict[str, Any] = {}
    while time.monotonic() - started < deadline_s:
        attempts += 1
        ok, detail = is_healthy(base)
        last = detail
        last["attempts"] = attempts
        if ok:
            last["boot_wait_s"] = round(time.monotonic() - started, 2)
            if not quiet:
                print(f"  {label} healthy after {last['boot_wait_s']}s ({attempts} probes)", flush=True)
            return True, last
        time.sleep(3.0)
    last["boot_wait_s"] = round(time.monotonic() - started, 2)
    last["timed_out_after_s"] = round(deadline_s, 1)
    return False, last


# --------------------------------------------------------------------------
# SSE
# --------------------------------------------------------------------------


@dataclass
class SseFrame:
    event: str | None
    data: str
    frame_id: str | None = None
    comments: list[str] = field(default_factory=list)

    def parsed(self) -> Any:
        if not self.data:
            return None
        try:
            return json.loads(self.data)
        except (ValueError, TypeError):
            return None


def parse_sse(raw_text: str) -> list[SseFrame]:
    """Parse an SSE body per the spec: blank line delimits, `:` is a comment,
    multiple `data:` lines join with newlines, a frame with no `event:` is not
    named (it is still a frame and is still recorded)."""
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    frames: list[SseFrame] = []
    event: str | None = None
    data_lines: list[str] = []
    comments: list[str] = []
    frame_id: str | None = None

    def flush() -> None:
        nonlocal event, data_lines, comments, frame_id
        if data_lines or event is not None or comments:
            frames.append(SseFrame(event=event, data="\n".join(data_lines), frame_id=frame_id, comments=comments))
        event, data_lines, comments, frame_id = None, [], [], None

    for line in normalized.split("\n"):
        if line == "":
            flush()
            continue
        if line.startswith(":"):
            comments.append(line[1:].strip())
            continue
        if ":" not in line:
            # A bare field name with no value is legal and carries nothing.
            continue
        field_name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field_name == "event":
            event = value
        elif field_name == "data":
            data_lines.append(value)
        elif field_name == "id":
            frame_id = value
        elif field_name == "retry":
            pass
    flush()
    return frames


def _message_id(message: dict[str, Any]) -> str | None:
    mid = message.get("id")
    return mid if isinstance(mid, str) else None


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _visible(message: dict[str, Any]) -> bool:
    kwargs = message.get("additional_kwargs")
    if isinstance(kwargs, dict) and kwargs.get("hide_from_ui") is True:
        return False
    return True


def _usage_tokens(message: dict[str, Any]) -> tuple[int | None, int | None]:
    usage = message.get("usage_metadata")
    if not isinstance(usage, dict):
        return None, None
    inp = usage.get("input_tokens")
    out = usage.get("output_tokens")
    return (inp if isinstance(inp, int) else None, out if isinstance(out, int) else None)


def analyse_stream(frames: list[SseFrame]) -> dict[str, Any]:
    """Fold frames into the structured facts the result reports."""
    event_types: dict[str, int] = {}
    stored: list[dict[str, Any]] = []
    truncated = False

    saw_end = False
    end_payload: Any = None
    error_events: list[dict[str, Any]] = []
    run_id: str | None = None
    thread_id: str | None = None

    # Client-side token sum, deduped by message id keeping the largest reading.
    # AGENTS.md: a provider may fire `on_llm_end` twice for one generation, so
    # the first (usage-less) frame must not zero out a later real one.
    usage_by_message: dict[str, tuple[int, int]] = {}
    usage_without_id = 0

    ai_text_by_id: dict[str, str] = {}
    last_values_messages: list[dict[str, Any]] | None = None

    for index, frame in enumerate(frames):
        name = frame.event or "(unnamed)"
        event_types[name] = event_types.get(name, 0) + 1

        payload = frame.parsed()

        if name == "metadata" and isinstance(payload, dict):
            run_id = payload.get("run_id") or run_id
            thread_id = payload.get("thread_id") or thread_id

        if name == "end":
            saw_end = True
            end_payload = payload

        if name == "error" or (isinstance(payload, dict) and payload.get("error")):
            error_events.append({"event": name, "payload": _clip(payload)})

        # `messages-tuple` arrives as a JSON list of message dicts.
        if name == "messages" and isinstance(payload, list):
            for message in payload:
                if not isinstance(message, dict):
                    continue
                mid = _message_id(message)
                inp, out = _usage_tokens(message)
                if inp is not None or out is not None:
                    total = (inp or 0) + (out or 0)
                    if mid is None:
                        usage_without_id += total
                    elif mid not in usage_by_message or total > sum(usage_by_message[mid]):
                        usage_by_message[mid] = (inp or 0, out or 0)
                if message.get("type") == "ai" and _visible(message):
                    text = _message_text(message)
                    if mid is not None and text:
                        ai_text_by_id[mid] = text

        if name == "values" and isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            last_values_messages = [m for m in payload["messages"] if isinstance(m, dict)]

        if len(stored) < MAX_EVENTS_STORED:
            data_text = frame.data
            stored.append(
                {
                    "index": index,
                    "event": frame.event,
                    "id": frame.frame_id,
                    "data_chars": len(data_text),
                    "payload": _clip(data_text),
                }
            )
        else:
            truncated = True

    client_input = sum(v[0] for v in usage_by_message.values()) + usage_without_id
    client_output = sum(v[1] for v in usage_by_message.values())

    answer = ""
    answer_id: str | None = None
    visible_ai = [m for m in (last_values_messages or []) if m.get("type") == "ai" and _visible(m)]
    if visible_ai:
        answer = _message_text(visible_ai[-1])
        answer_id = _message_id(visible_ai[-1])
    elif ai_text_by_id:
        answer_id = max(ai_text_by_id)
        answer = ai_text_by_id[answer_id]

    end_status: str | None = None
    end_ok: bool | None = None
    if isinstance(end_payload, dict):
        raw_status = end_payload.get("status")
        end_status = raw_status if isinstance(raw_status, str) else None
        end_ok = end_payload["ok"] if isinstance(end_payload.get("ok"), bool) else None

    return {
        "event_types": event_types,
        "events_total": len(frames),
        "events_stored": len(stored),
        "events_truncated": truncated,
        "frames": stored,
        "saw_end_frame": saw_end,
        "end_status": end_status,
        "end_ok": end_ok,
        "end_payload": _clip(end_payload),
        "error_events": error_events,
        "run_id": run_id,
        "thread_id": thread_id,
        "answer_text": answer,
        "answer_message_id": answer_id,
        "tokens_client_side": {
            "input_tokens": client_input,
            "output_tokens": client_output,
            "total_tokens": client_input + client_output,
            "basis": "sum of usage_metadata over deduplicated message frames in the stream",
            "messages_with_usage": len(usage_by_message),
        },
    }


def _clip(value: Any) -> Any:
    """Bound a payload for the evidence file without ever raising.

    Re-parsing truncated JSON fails, so the truncated form is a *string*
    marker rather than a broken object: `result.json` must stay loadable
    whatever the run produced, because an unreadable result is the one outcome
    that cannot be diagnosed.
    """
    if isinstance(value, str):
        return value if len(value) <= MAX_EVENT_PAYLOAD_CHARS else value[:MAX_EVENT_PAYLOAD_CHARS] + f"...[+{len(value) - MAX_EVENT_PAYLOAD_CHARS} chars]"
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)[:MAX_EVENT_PAYLOAD_CHARS]
    if len(text) <= MAX_EVENT_PAYLOAD_CHARS:
        return value
    return text[:MAX_EVENT_PAYLOAD_CHARS] + f"...[+{len(text) - MAX_EVENT_PAYLOAD_CHARS} chars, truncated; see raw.sse for the complete frame]"


# --------------------------------------------------------------------------
# evidence directories
# --------------------------------------------------------------------------


def new_evidence_dir(domain: str) -> Path:
    """Create a fresh, never-overwritten directory for this run.

    `docs/evidence/<domain>/<date>/<time>-<n>/` — a collision gets the next
    free number rather than replacing whatever was there.
    """
    now = datetime.now()
    parent = REPO_ROOT / "docs" / "evidence" / domain / now.strftime("%Y-%m-%d")
    parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 1000):
        candidate = parent / f"{now.strftime('%H%M%S')}-{attempt:02d}"
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
    raise SystemExit(f"could not allocate a fresh evidence directory under {parent}")


# --------------------------------------------------------------------------
# stack control
# --------------------------------------------------------------------------


def spawn(label: str, cmd: list[str], cwd: Path, env: dict[str, str], log_path: Path, port: int | None = None) -> Spawned:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8", errors="replace")
    kwargs: dict[str, Any] = {"cwd": str(cwd), "stdout": handle, "stderr": subprocess.STDOUT, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    popen = subprocess.Popen(cmd, **kwargs)
    entry = Spawned(label, popen, log_path, port=port)
    SPAWNED.append(entry)
    return entry


def tail(path: Path, lines: int = 40) -> str | None:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return None


def start_gateway(base: str, *, boot_timeout: float) -> dict[str, Any]:
    backend = REPO_ROOT / "backend"
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", ".")
    env["PYTHONPATH"] = "."
    # The documented local/E2E auth mode. Recorded in the result so the reader
    # knows which identity ran the prompt.
    env.setdefault("ALPHA_AUTH_DISABLED", "1")
    port = url_port(base)
    log_path = REPO_ROOT / "logs" / f"e2e-driver-gateway-{port}.log"
    cmd = ["uv", "run", "--no-sync", "uvicorn", "app.gateway.app:app", "--host", "127.0.0.1", "--port", str(port)]
    print(f"  starting Gateway on :{port} (log: {log_path})", flush=True)
    entry = spawn("gateway", cmd, backend, env, log_path, port=port)
    ok, health = wait_for_health(base, boot_timeout, label="Gateway")
    health["spawned"] = True
    health["pid"] = entry.popen.pid
    health["command"] = cmd
    health["log"] = str(log_path.relative_to(REPO_ROOT))
    if not ok:
        health["log_tail"] = tail(log_path)
    return health


def start_frontend(base: str, *, boot_timeout: float) -> dict[str, Any]:
    frontend = REPO_ROOT / "frontend"
    env = dict(os.environ)
    env.setdefault("PORT", "3000")
    env.setdefault("NEXT_PUBLIC_GATEWAY_URL", DEFAULT_GATEWAY)
    if not env.get("BETTER_AUTH_SECRET"):
        # A throwaway local-only value; the driver never performs a real login.
        env["BETTER_AUTH_SECRET"] = "e2e-harness-local-only-not-a-real-credential"
    log_path = REPO_ROOT / "logs" / f"e2e-driver-frontend-{url_port(base)}.log"
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "pnpm.py"), "run", "start"]
    print(f"  starting Frontend on :{url_port(base)} (log: {log_path})", flush=True)
    entry = spawn("frontend", cmd, frontend, env, log_path, port=url_port(base))
    ok, health = wait_for_frontend(base, boot_timeout)
    health["spawned"] = True
    health["pid"] = entry.popen.pid
    health["command"] = cmd
    health["log"] = str(log_path.relative_to(REPO_ROOT))
    if not ok:
        health["log_tail"] = tail(log_path)
    return health


def wait_for_frontend(base: str, deadline_s: float) -> tuple[bool, dict[str, Any]]:
    started = time.monotonic()
    attempts = 0
    detail: dict[str, Any] = {"base": base}
    while time.monotonic() - started < deadline_s:
        attempts += 1
        result = request(base, timeout=10.0)
        detail = {
            "base": base,
            "status": result.status,
            "elapsed_s": result.elapsed_s,
            "error": result.error,
            "attempts": attempts,
        }
        if 200 <= result.status < 400:
            detail["boot_wait_s"] = round(time.monotonic() - started, 2)
            return True, detail
        time.sleep(2.0)
    detail["timed_out_after_s"] = round(deadline_s, 1)
    return False, detail


def url_port(base: str, default: int = 8001) -> int:
    tail = base.rstrip("/").rsplit(":", 1)
    if len(tail) == 2 and tail[1].isdigit():
        return int(tail[1])
    return default


# --------------------------------------------------------------------------
# the run itself
# --------------------------------------------------------------------------


@dataclass
class Step:
    name: str
    status: str
    detail: str = ""
    elapsed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail, "elapsed_s": self.elapsed_s}


def stream_run(base: str, thread_id: str, prompt: str, model: str, assistant: str, timeout: float) -> tuple[dict[str, Any], str]:
    """POST the run and read the stream to its terminal frame.

    Returns (stream_facts, raw_sse_text). A timeout keeps whatever bytes
    arrived — a partial stream is evidence, not something to round up.
    """
    payload = {
        "assistant_id": assistant,
        "on_disconnect": "continue",
        "stream_mode": STREAM_MODES,
        "input": {"messages": [{"role": "user", "content": prompt}]},
        "config": {"configurable": {"model_name": model}},
    }
    url = f"{base.rstrip('/')}/api/threads/{thread_id}/runs/stream"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "alpha-e2e-driver/1.0",
        },
        method="POST",
    )

    chunks = bytearray()
    http_status: int | None = None
    content_location: str | None = None
    elapsed = 0.0
    timed_out = False
    transport_error: str | None = None
    started = time.monotonic()
    deadline = started + timeout

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            http_status = resp.status
            content_location = resp.headers.get("Content-Location")
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    line = resp.readline()
                except TimeoutError:
                    timed_out = True
                    break
                except OSError as exc:
                    transport_error = f"{type(exc).__name__}: {exc}"
                    break
                if not line:
                    break
                chunks += line
    except urllib.error.HTTPError as exc:
        http_status = exc.code
        content_location = exc.headers.get("Content-Location") if exc.headers else None
        chunks += exc.read()
        transport_error = f"HTTPError {exc.code}: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        transport_error = f"{type(exc).__name__}: {exc}"

    elapsed = round(time.monotonic() - started, 3)
    raw_text = chunks.decode("utf-8", "replace")
    facts = analyse_stream(parse_sse(raw_text))
    facts["http_status"] = http_status
    facts["content_location"] = content_location
    facts["request_url"] = url
    facts["elapsed_s"] = elapsed
    facts["bytes"] = len(chunks)
    facts["timed_out"] = timed_out
    facts["transport_error"] = transport_error
    if timed_out:
        facts["stream_closed_early"] = not facts["saw_end_frame"]
    return facts, raw_text


def server_tokens(base: str, thread_id: str) -> dict[str, Any]:
    """The Gateway's own aggregate. `null` when it could not be read — never 0."""
    result = request(f"{base.rstrip('/')}/api/threads/{thread_id}/token-usage", timeout=30.0)
    if result.status != 200:
        return {
            "value": None,
            "http_status": result.status,
            "error": result.error or f"HTTP {result.status}",
            "body": result.text()[:300],
        }
    try:
        payload = result.json()
    except (ValueError, TypeError) as exc:
        return {"value": None, "http_status": result.status, "error": f"unreadable JSON: {exc}"}
    if not isinstance(payload, dict):
        return {"value": None, "http_status": result.status, "error": "payload was not a JSON object"}
    return {"value": _clip(payload), "http_status": result.status, "error": None}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/e2e/harness.py",
        description="Send one real prompt to Alpha and record an honest JSON verdict.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT, help="the prompt to send (default: %(default)r)")
    parser.add_argument("--base", default=os.environ.get("ALPHA_E2E_BASE", DEFAULT_GATEWAY), help="Gateway base URL (default: %(default)s)")
    parser.add_argument("--frontend", default=DEFAULT_FRONTEND, help="Frontend base URL, used by --start-frontend")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="model name from config.yaml (default: %(default)s)")
    parser.add_argument("--assistant", default=DEFAULT_ASSISTANT, help="assistant_id (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="stream timeout in seconds (default: %(default)s)")
    parser.add_argument("--boot-timeout", type=float, default=DEFAULT_BOOT_TIMEOUT, help="seconds to wait for a cold Gateway (default: %(default)s)")
    parser.add_argument("--start", action="store_true", help="start the Gateway if it is not already healthy")
    parser.add_argument("--start-frontend", action="store_true", help="also start the Next.js server if it is not already serving")
    parser.add_argument("--health-only", action="store_true", help="check the stack and exit without sending a prompt")
    parser.add_argument("--domain", default="e2e-driver", help="evidence subdirectory under docs/evidence/ (default: %(default)s)")
    parser.add_argument("--out", default=None, help="explicit result JSON path (default: a fresh dated evidence directory)")
    parser.add_argument("--keep-thread", action="store_true", help="do not delete the thread after the run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _install_signal_handlers()

    started_at = datetime.now(UTC)
    wall_start = time.monotonic()
    steps: list[Step] = []
    errors: list[dict[str, Any]] = []
    stack: dict[str, Any] = {"mode": "start" if (args.start or args.start_frontend) else "attach"}

    base = args.base.rstrip("/")
    frontend_base = args.frontend.rstrip("/")

    try:
        # -- 1. stack ------------------------------------------------------
        print("[1/5] stack", flush=True)
        gateway_ok, gateway_health = is_healthy(base)
        if not gateway_ok and (args.start or args.start_frontend):
            gateway_health = start_gateway(base, boot_timeout=args.boot_timeout)
            gateway_ok = gateway_health.get("status") == 200
        stack["gateway"] = gateway_health
        if gateway_ok:
            steps.append(Step("gateway_health", "pass", f"HTTP {gateway_health.get('status')}"))
        else:
            steps.append(Step("gateway_health", "fail", f"Gateway not healthy: {gateway_health.get('error') or gateway_health.get('status')}"))
            errors.append({"stage": "gateway_health", "detail": gateway_health})
            teardown_all()

        if args.start_frontend:
            fe_ok2, fe_health = wait_for_frontend(frontend_base, 10.0)
            if not fe_ok2:
                fe_health = start_frontend(frontend_base, boot_timeout=args.boot_timeout)
            stack["frontend"] = fe_health
            steps.append(Step("frontend_health", "pass" if fe_health.get("status", 0) < 400 else "fail", f"HTTP {fe_health.get('status')}"))

        if not gateway_ok:
            return _finish(
                out_path=Path(args.out) if args.out else None,
                domain=args.domain,
                verdict="blocked",
                verdict_reason="The Gateway never became healthy, so no prompt was sent. Nothing was verified.",
                steps=steps,
                errors=errors,
                stack=stack,
                started_at=started_at,
                wall_s=round(time.monotonic() - wall_start, 3),
                prompt=args.prompt,
                model=args.model,
            )

        stack["auth_mode"] = "ALPHA_AUTH_DISABLED (anonymous synthetic admin)" if os.environ.get("ALPHA_AUTH_DISABLED") == "1" else "as configured by the running Gateway"

        # -- 2. thread -----------------------------------------------------
        print("[2/5] create thread", flush=True)
        t0 = time.monotonic()
        created = request(f"{base}/api/threads", method="POST", payload={}, timeout=60.0)
        thread_id: str | None = None
        if created.status in (200, 201):
            try:
                data = created.json()
                if isinstance(data, dict) and isinstance(data.get("thread_id"), str):
                    thread_id = data["thread_id"]
            except (ValueError, TypeError):
                pass
        thread_detail = {"http_status": created.status, "thread_id": thread_id, "error": created.error, "body": created.text()[:400]}
        if thread_id:
            steps.append(Step("create_thread", "pass", thread_id, round(time.monotonic() - t0, 3)))
        else:
            steps.append(Step("create_thread", "fail", f"HTTP {created.status}", round(time.monotonic() - t0, 3)))
            errors.append({"stage": "create_thread", "detail": thread_detail})
            teardown_all()
            return _finish(
                out_path=Path(args.out) if args.out else None,
                domain=args.domain,
                verdict="blocked",
                verdict_reason="The thread could not be created, so no prompt was sent. Nothing was verified.",
                steps=steps,
                errors=errors,
                stack=stack,
                started_at=started_at,
                wall_s=round(time.monotonic() - wall_start, 3),
                prompt=args.prompt,
                model=args.model,
            )

        # -- 3. stream -----------------------------------------------------
        print(f"[3/5] stream (timeout {args.timeout:.0f}s)", flush=True)
        stream, raw_sse = stream_run(base, thread_id, args.prompt, args.model, args.assistant, args.timeout)
        steps.append(
            Step(
                "stream_run",
                "pass" if stream.get("saw_end_frame") else "fail",
                f"HTTP {stream.get('http_status')} · {stream.get('events_total')} frames · {stream.get('bytes')} bytes · {stream.get('elapsed_s')}s · terminal frame {stream.get('end_status')!r}",
                float(stream.get("elapsed_s") or 0.0),
            )
        )

        # -- 4. tokens -----------------------------------------------------
        print("[4/5] token accounting", flush=True)
        tokens_server = server_tokens(base, thread_id)
        tokens = {
            "client_side": stream["tokens_client_side"],
            "server_reported": tokens_server.get("value"),
            "server_read_error": tokens_server.get("error"),
            "server_http_status": tokens_server.get("http_status"),
        }

        # -- 5. verdict ----------------------------------------------------
        verdict, reason = _judge(stream, steps)

        if args.keep_thread:
            cleanup = {"thread_deleted": False, "reason": "--keep-thread"}
        else:
            cleanup = {"thread_deleted": True}
            deleted = request(f"{base}/api/threads/{thread_id}", method="DELETE", timeout=30.0)
            cleanup["delete_http_status"] = deleted.status
            cleanup["thread_deleted"] = 200 <= deleted.status < 300

        if not cleanup.get("thread_deleted"):
            cleanup["reason"] = cleanup.get("reason") or "the thread was not removed; re-run without --keep-thread to clean up"

        teardown_all()
        return _finish(
            out_path=Path(args.out) if args.out else None,
            domain=args.domain,
            verdict=verdict,
            verdict_reason=reason,
            steps=steps,
            errors=errors,
            stack=stack,
            started_at=started_at,
            wall_s=round(time.monotonic() - wall_start, 3),
            prompt=args.prompt,
            model=args.model,
            thread=thread_detail,
            stream=stream,
            tokens=tokens,
            cleanup=cleanup,
            raw_sse=raw_sse,
        )

    finally:
        # Safety net. The normal path calls `teardown_all()` before writing the
        # result so the evidence file carries the real outcome; this catches the
        # paths that raise or return early. It is idempotent.
        teardown_all()


def _judge(stream: dict[str, Any], steps: list[Step]) -> tuple[str, str]:
    """The verdict is a function of what the stream actually did."""
    if stream.get("http_status") != 200:
        return "fail", f"The stream request returned HTTP {stream.get('http_status')} ({stream.get('transport_error') or 'no transport error'})."
    if stream.get("transport_error"):
        return "fail", f"The stream failed at the transport layer: {stream['transport_error']}"
    if stream.get("timed_out"):
        return "fail", f"The stream timed out after {stream.get('elapsed_s')}s with {'no' if stream.get('saw_end_frame') else 'no'} terminal frame. Partial bytes were kept."
    if not stream.get("saw_end_frame"):
        return "fail", "The stream ended without a terminal `end` frame, so the run's outcome was never reported by the server."
    if stream.get("end_ok") is False or (stream.get("end_status") or "").lower() not in ("success", "completed"):
        return "fail", f"The server's terminal frame reported status={stream.get('end_status')!r} ok={stream.get('end_ok')!r}, not a success."
    if stream.get("error_events"):
        return "fail", f"The stream carried {len(stream['error_events'])} error event(s)."
    if not (stream.get("answer_text") or "").strip():
        return "fail", "The run reported success but produced no visible assistant text. That is not a pass."
    return "pass", "The Gateway accepted a real prompt and streamed it to a terminal frame the server marked ok, with assistant text produced."


def _finish(
    *,
    out_path: Path | None,
    domain: str,
    verdict: str,
    verdict_reason: str,
    steps: list[Step],
    errors: list[dict[str, Any]],
    stack: dict[str, Any],
    started_at: datetime,
    wall_s: float,
    prompt: str,
    model: str,
    thread: dict[str, Any] | None = None,
    stream: dict[str, Any] | None = None,
    tokens: dict[str, Any] | None = None,
    cleanup: dict[str, Any] | None = None,
    raw_sse: str | None = None,
) -> int:
    if out_path is None:
        evidence = new_evidence_dir(domain)
        out_path = evidence / "result.json"
    else:
        out_path = out_path if out_path.is_absolute() else (REPO_ROOT / out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_dir = out_path.parent

    if raw_sse is not None:
        (evidence_dir / "raw.sse").write_text(raw_sse, encoding="utf-8")

    result = {
        "schema": "alpha-e2e-driver/v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "started_at": started_at.isoformat(),
        "wall_clock_s": wall_s,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "verdict_scope": VERDICT_SCOPE,
        "prompt": prompt,
        "model": model,
        "stack": stack,
        "steps": [step.to_dict() for step in steps],
        "thread": thread,
        "stream": stream,
        "tokens": tokens,
        "cleanup": cleanup,
        "teardown": TEARDOWN,
        "errors": errors,
        "artifacts": {
            "result_json": str(out_path.relative_to(REPO_ROOT)) if REPO_ROOT in out_path.parents else str(out_path),
            "raw_sse": str((evidence_dir / "raw.sse").relative_to(REPO_ROOT)) if raw_sse is not None else None,
        },
    }

    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print("", flush=True)
    print(f"verdict : {verdict}", flush=True)
    print(f"reason  : {verdict_reason}", flush=True)
    if stream:
        print(f"stream  : HTTP {stream.get('http_status')} · {stream.get('events_total')} frames · {stream.get('bytes')} bytes · {stream.get('elapsed_s')}s", flush=True)
        print(f"events  : {stream.get('event_types')}", flush=True)
        answer = (stream.get("answer_text") or "").strip().replace("\n", " ")
        print(f"answer  : {answer[:200] if answer else '(none)'}", flush=True)
    if tokens:
        cs = tokens.get("client_side") or {}
        print(
            f"tokens  : client-side {cs.get('total_tokens')} (in {cs.get('input_tokens')} / out {cs.get('output_tokens')}) · server-reported {tokens.get('server_reported') if tokens.get('server_reported') is not None else 'NOT REPORTED'}",
            flush=True,
        )
    print(f"result  : {result['artifacts']['result_json']}", flush=True)

    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
