"""Capture a real chat SSE stream verbatim, to refresh a frontend test fixture.

This records what the Gateway ACTUALLY sends, so the frontend SSE reducer can
be proven against real server output instead of a hand-written approximation
of it. It is how `src/lib/chat-run-replay.test.mjs` found that the reducer
discarded the root agent node's own answer frames.

The captured request is byte-for-byte the one the browser sends
(`frontend/src/components/ChatView.tsx`, the `runs/stream` POST).

Usage (Gateway must be up on :8001):

    cd frontend
    python ../scripts/capture_chat_sse.py src/lib/fixtures/chat-run-capture.json

Environment overrides:
    ALPHA_GATEWAY_BASE   default http://127.0.0.1:8001
    ALPHA_CAPTURE_PROMPT default "Reply with exactly the word: pong"
    ALPHA_CAPTURE_MODEL  default union-alpha
    ALPHA_CAPTURE_TIMEOUT default 300 seconds

The `id:` SSE field matters and is preserved: the reducer keys deduplication and
ordering off it, so a capture that drops it replays as a `protocol` failure.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("ALPHA_GATEWAY_BASE", "http://127.0.0.1:8001")
PROMPT = os.environ.get("ALPHA_CAPTURE_PROMPT", "Reply with exactly the word: pong")
MODEL = os.environ.get("ALPHA_CAPTURE_MODEL", "union-alpha")
TIMEOUT = float(os.environ.get("ALPHA_CAPTURE_TIMEOUT", "300"))

if len(sys.argv) > 1:
    OUT = Path(sys.argv[1])
else:
    raise SystemExit(
        "usage: python scripts/capture_chat_sse.py <output.json>\n"
        "  e.g. python scripts/capture_chat_sse.py frontend/src/lib/fixtures/chat-run-capture.json"
    )


def post_json(path: str, payload: object) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


code, text = post_json("/api/threads", {})
if code not in (200, 201):
    raise SystemExit(f"thread create failed: {code} {text[:300]}")
thread_id = json.loads(text).get("thread_id")
print(f"thread_id={thread_id}", flush=True)

payload = {
    "assistant_id": "lead_agent",
    "on_disconnect": "continue",
    "stream_mode": ["messages-tuple", "values"],
    "input": {"messages": [{"role": "user", "content": PROMPT}]},
    "config": {"configurable": {"model_name": MODEL}},
}
req = urllib.request.Request(
    f"{BASE}/api/threads/{thread_id}/runs/stream",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    method="POST",
)

raw = bytearray()
started = time.time()
content_location = ""
with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
    content_location = r.headers.get("Content-Location") or ""
    while True:
        chunk = r.read(4096)
        if not chunk:
            break
        raw += chunk

raw_text = raw.decode("utf-8", "replace")

# Count frames the way the SSE spec does, purely for reporting.
frames = [
    block for block in raw_text.replace("\r\n", "\n").split("\n\n")
    if block.strip()
]

doc = {
    "thread_id": thread_id,
    "content_location": content_location,
    "model": MODEL,
    "prompt": PROMPT,
    "elapsed_s": round(time.time() - started, 2),
    "raw_sse": raw_text,
    "frame_count": len(frames),
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"captured {len(frames)} frames / {len(raw)} bytes in {doc['elapsed_s']}s -> {OUT}", flush=True)
