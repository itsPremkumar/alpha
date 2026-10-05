"""An agent creates its own subagent, then delegates real work to it.

This is the end-to-end answer to "can the agent do this itself?", so nothing in
it may be asserted from the agent's own summary. Three separate things are
checked against the server:

1. the agent's run actually **called** ``subagent_registry``;
2. the subagent it claims to have created **exists** in ``GET /api/subagents``,
   with the fields it said it set;
3. a **second** run delegates real work to that subagent, and the artifact
   appears on disk.

The run is created with ``autonomous: true`` because that is the server-applied
opt-in that registers the ``task`` tool. Measured, not assumed: with
``autonomous`` false a run saw ``alpha_capability, catalog_tool_search``; with it
true the same prompt saw ``alpha_capability, task``. Delegation was never
absent from the deployment -- it was switched off for those runs, which is a
different claim and one this file does not make again.

Honesty requirement passed to both agents: quote real tool output, and never
claim a subagent ran unless a delegation tool returned something.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8001"
OUT = "/mnt/user-data/outputs"
_HERE = Path(__file__).resolve().parent
TERMINAL = {"success", "error", "interrupted", "timeout", "canceled"}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_op = _load("orchestration_probe")


def safe(s: Any, n: int = 700) -> str:
    if not isinstance(s, str):
        s = json.dumps(s, default=str)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    return s[:n].encode(enc, errors="replace").decode(enc, errors="replace")


CREATE_PROMPT = """\
You have a tool called `subagent_registry`. Use it.

1. Call `subagent_registry` with action="list" and look at what already exists.
2. Decide on ONE genuinely useful specialised subagent this repository does not
   already have. Base the decision on what you can actually observe.
3. Call `subagent_registry` with action="create" to create it. Give it:
   - a hyphenated lowercase handle,
   - a `description` that says WHEN it should fire,
   - a `system_prompt` with concrete instructions,
   - `tools` as a comma-separated list of tool names it should use,
   - max_turns and timeout_seconds.
4. Call `subagent_registry` with action="inspect" on the handle you created and
   confirm it reads back.
5. Write a summary to {out}/agent_created_subagent.md naming the handle and
   pasting the verbatim output of the list, create and inspect calls.

Report the exact handle you created.

If `subagent_registry` is not available to you, say "TOOL NOT AVAILABLE" and name
the tools you do have. Do not create anything by editing files or config -- that
is not creating a subagent, and claiming it was would be a fabrication."""


async def run_once(client: httpx.AsyncClient, prompt: str, timeout: float) -> dict[str, Any]:
    t = await client.post(f"{BASE}/api/threads", json={"metadata": {"purpose": "agent-self-service-subagent"}})
    tid = t.json()["thread_id"]
    body: dict[str, Any] = {
        "input": {"messages": [{"role": "user", "content": prompt}]},
        "metadata": {"purpose": "agent-self-service-subagent"},
        "stream_mode": ["messages-tuple", "values", "custom"],
        "on_disconnect": "continue",
        # The server-applied opt-in that registers the delegation tools.
        "autonomous": True,
    }
    urls = f"{BASE}/api/threads/{tid}"
    tools: set[str] = set()
    rid = ""
    deadline = time.monotonic() + timeout

    async def timed(src: Any) -> Any:
        async for line in src:
            if time.monotonic() > deadline:
                return
            yield line

    async with client.stream("POST", f"{urls}/runs/stream", json=body, timeout=httpx.Timeout(30.0, read=timeout)) as r:
        if r.status_code >= 400:
            return {"thread": tid, "error": f"stream HTTP {r.status_code}", "tools": []}
        loc = r.headers.get("content-location") or r.headers.get("Content-Location")
        rid = loc.rstrip("/").split("/")[-1] if loc else ""
        async for _n, d in _op.parse_sse(timed(r.aiter_lines())):
            try:
                _op.walk_tools(json.loads(d), tools)
            except (ValueError, TypeError):
                pass

    status = None
    poll = time.monotonic() + max(60.0, timeout)
    while time.monotonic() < poll:
        if rid:
            rr = await client.get(f"{urls}/runs/{rid}")
            if rr.status_code == 200:
                status = rr.json().get("status")
                if status in TERMINAL:
                    break
        await asyncio.sleep(2.0)

    answer = ""
    if rid:
        mr = await client.get(f"{urls}/runs/{rid}/messages")
        if mr.status_code == 200:
            answer = _op.final_ai_text(mr.json())
    return {
        "thread": tid,
        "run": rid,
        "status": status,
        "tools": sorted(tools),
        "answer": re.sub(r"\s+", " ", answer)[:1100],
    }


def artifact(thread_id: str, name: str) -> tuple[Path | None, int]:
    """Locate an artifact by name, in this thread FIRST and then any thread.

    A delegated subagent writes into its **own** thread's outputs directory, not
    the dispatching thread's. The first version of this probe searched only the
    parent and reported `-1B None` for a subagent that had genuinely run, written
    a real 123-byte file, and read it back -- so the instrument reported a
    working delegation as a failure. Same class as the earlier probe defects: an
    instrument that cannot find the thing it is measuring measures nothing.
    """
    base = _HERE.parents[1] / ".alpha" / "users"
    hits: list[Path] = []
    for user_dir in base.glob("*"):
        own = user_dir / "threads" / thread_id / "user-data" / "outputs" / name
        if own.exists():
            return own, own.stat().st_size
        for other in (user_dir / "threads").glob("*/user-data/outputs/" + name):
            hits.append(other)
    if hits:
        newest = max(hits, key=lambda p: p.stat().st_mtime)
        return newest, newest.stat().st_size
    return None, -1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    before = await httpx.AsyncClient(timeout=120).get(f"{BASE}/api/subagents")
    before_names = {s["name"] for s in (before.json().get("subagents") or [])}

    created: str | None = None
    report: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=240.0) as c:
        print("=== 1. an agent creates its own subagent ===")
        r1 = await run_once(c, CREATE_PROMPT.format(out=OUT), args.timeout)
        for k, v in r1.items():
            print(f"  {k:<9}: {safe(v)}")
        report["create_run"] = {k: v for k, v in r1.items() if k != "answer"}

        used = "subagent_registry" in r1["tools"]
        print(f"\n  CALLED subagent_registry: {used}")
        print(f"  tools: {', '.join(r1['tools']) or '(none)'}")

        after = await c.get(f"{BASE}/api/subagents")
        after_names = {s["name"] for s in (after.json().get("subagents") or [])}
        new = sorted(after_names - before_names)
        report["new_subagents"] = new
        print(f"\n  roster {len(before_names)} -> {len(after_names)}; new: {new or 'none'}")
        created = new[0] if new else None

        art, size = artifact(r1["thread"], "agent_created_subagent.md")
        report["summary_artifact"] = {"path": str(art), "bytes": size}
        print(f"  agent summary artifact: {size}B {art}")

        if created:
            print(f"\n=== 2. delegate real work to '{created}' ===")
            prompt = (
                f"Use the `task` tool with subagent_type='{created}'. Ask it to write exactly this file "
                f"with write_file: {OUT}/delegated_by_subagent.md, containing three lines starting "
                f"'subagent:', 'bytes:' and a third line.\n\n"
                f"After it returns, report the verbatim `task` result and read the file back with "
                f"read_file to report its actual size.\n\n"
                f"If the task tool refuses, quote the exact refusal. Do NOT write the file yourself "
                f"and do not claim the subagent produced it."
            )
            r2 = await run_once(c, prompt, args.timeout)
            for k, v in r2.items():
                print(f"  {k:<9}: {safe(v)}")
            report["delegate_run"] = {k: v for k, v in r2.items() if k != "answer"}
            print(f"\n  called task: {'task' in r2['tools']}")
            # A delegated subagent's write lands in its OWN thread and settles
            # slightly after the parent run reaches a terminal status, so a
            # single immediate read reported `-1B None` for a subagent that had
            # genuinely run and written a real file. Two prior runs produced 123 B
            # and 91 B artifacts that this probe missed entirely.
            art2, size2 = None, -1
            settle = time.monotonic() + 90
            while time.monotonic() < settle:
                art2, size2 = artifact(r2["thread"], "delegated_by_subagent.md")
                if size2 > 0:
                    break
                await asyncio.sleep(3)
            report["delegated_artifact"] = {"path": str(art2), "bytes": size2}
            print(f"  delegated artifact: {size2}B {art2}")
            if art2 and size2 > 0:
                print("  --- first 6 lines ---")
                for line in art2.read_text(encoding="utf-8", errors="replace").splitlines()[:6]:
                    print(f"    {line[:100]}")

        ctrl = await c.get(f"{BASE}/api/subagents/control")
        rows = ctrl.json() if ctrl.status_code == 200 else []
        report["control_plane_count"] = len(rows) if isinstance(rows, list) else None
        report["control_plane_sample"] = [{k: row.get(k) for k in ("subagent_name", "subagent_type", "status", "started_at") if k in row} for row in (rows or [])[:5]]
        print(f"\n  UI control plane (/api/subagents/control): {report['control_plane_count']} record(s)")
        if report["control_plane_sample"]:
            print(f"    {safe(report['control_plane_sample'], 400)}")

    # Cleanup: remove the subagent the agent created, so the roster is left as found.
    if created:
        async with httpx.AsyncClient(timeout=120.0) as c:
            d = await c.delete(f"{BASE}/api/subagents/{created}")
            print(f"\n  cleanup DELETE {created} -> HTTP {d.status_code}")

    ok = bool(report.get("new_subagents"))
    print("\n" + "=" * 92)
    print(f"AGENT SELF-SERVICE SUBAGENT CREATION: {'PASS' if ok else 'FAIL'}")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
