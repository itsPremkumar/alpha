"""Does `autonomous: true` actually register the `task` tool?

`AGENTS.md` documents `RunCreateRequest.autonomous` as the Gateway-owned opt-in,
and `app/gateway/services.py` sets `body_context["subagent_enabled"] = True`
when it is set. Everything measured so far in this session showed delegation
absent, and nothing distinguished "delegation is broken" from "delegation is
switched off for this deployment". This probe is that distinction.

It reuses the SSE parser and tool-name collector from `orchestration_probe.py`
rather than hand-rolling them: a first draft inlined its own parser, and it
reported **zero** tools for every run including ones earlier probes had observed
carrying 10. An instrument that cannot see a working run must not be used to
conclude the run had no tools.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx

B = "http://127.0.0.1:8001"
_HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_op = _load("orchestration_probe")

TERMINAL = {"success", "error", "interrupted", "timeout", "canceled"}


async def probe(autonomous: bool, prompt: str) -> dict[str, Any]:
    c = httpx.AsyncClient(timeout=900.0)
    try:
        t = await c.post(f"{B}/api/threads", json={"metadata": {"purpose": "autonomous-probe"}})
        tid = t.json()["thread_id"]
        body: dict[str, Any] = {
            "input": {"messages": [{"role": "user", "content": prompt}]},
            "metadata": {"purpose": "autonomous-probe"},
            "stream_mode": ["messages-tuple", "values", "custom"],
            "on_disconnect": "continue",
        }
        if autonomous:
            body["autonomous"] = True
        urls = f"{B}/api/threads/{tid}"
        tools: set[str] = set()
        rid = ""
        async with c.stream("POST", f"{urls}/runs/stream", json=body, timeout=httpx.Timeout(30.0, read=900.0)) as r:
            if r.status_code >= 400:
                return {"autonomous": autonomous, "http": r.status_code, "body": (await r.aread())[:200].decode("utf-8", "replace")}
            loc = r.headers.get("content-location") or r.headers.get("Content-Location")
            rid = loc.rstrip("/").split("/")[-1] if loc else ""
            async for _n, d in _op.parse_sse(r.aiter_lines()):
                try:
                    _op.walk_tools(json.loads(d), tools)
                except (ValueError, TypeError):
                    pass

        status = None
        for _ in range(300):
            if rid:
                rr = await c.get(f"{urls}/runs/{rid}")
                if rr.status_code == 200:
                    status = rr.json().get("status")
                    if status in TERMINAL:
                        break
            await asyncio.sleep(2.0)
        return {"autonomous": autonomous, "status": status, "n_tools": len(tools), "tools": sorted(tools)}
    finally:
        try:
            await c.delete(f"{B}/api/threads/{tid}")
        except Exception:  # noqa: BLE001
            pass
        await c.aclose()


async def main() -> None:
    # A prompt that NEEDS a tool. The first draft of this probe asked for
    # "reply with exactly PROBE-OK" and both columns came back with zero tools --
    # correctly, because a fixed string requires none. A probe that cannot tell
    # "no tool needed" from "no tool registered" measures nothing, so the prompt
    # must make tool use the only way to succeed.
    prompt = (
        "Delegate this to a subagent using the task tool with subagent_type=general-purpose. "
        "Ask it to write /mnt/user-data/outputs/probe_delegated.md containing one line of text. "
        "If the task tool is not available to you, say exactly 'NO TASK TOOL' and name the tools "
        "you do have that come closest. Do not write the file yourself."
    )
    rows = []
    for flag in (False, True):
        r = await probe(flag, prompt)
        rows.append(r)
        has = "task" in (r.get("tools") or [])
        print(f"autonomous={str(flag):<5} status={r.get('status')!r:<10} http={r.get('http')} tools={r.get('n_tools', 0):<3} task_registered={has}")
        if r.get("tools"):
            print(f"    {', '.join(r['tools'][:26])}")
        if r.get("body"):
            print(f"    body: {r['body']}")
    a = {frozenset(r.get("tools") or []) for r in rows}
    print(f"\ndistinguishing? {'YES' if len(a) > 1 else 'NO - identical tool sets'}")


if __name__ == "__main__":
    asyncio.run(main())
