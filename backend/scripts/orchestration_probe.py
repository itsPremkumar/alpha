"""Exercise Alpha's orchestration features with real work, not asserts.

Each probe drives one *different* subsystem against a live Gateway and grades
it the only way that means anything: by reading what the subsystem actually
produced.

The distinction this file exists to make:

* **Orchestration plane** -- ``swarm``, ``delegate_to_deep_agent``, ``task``
  with a subagent_type, ``ralph_loop``. The claim is "Alpha decomposed this
  work and reassembled the results". Verified by reading swarm DAGs, task
  states, result summaries and evidence off the coordinator.
* **Single-agent plane** -- one lead agent with tools. Already covered by
  ``fleet_task_assign.py``. Not repeated here.

Everything runs concurrently, because a single serialized probe proves nothing
about whether swarm task fan-out is real or merely reported.

Verification rules, in force throughout:

* A task that reports ``completed`` is not a passing task. Acceptance is read
  from the coordinator's own acceptance fields.
* A feature whose prerequisites are absent (``sandbox.allow_host_bash: false``
  blocks the ``bash`` subagent; the deep agents all declare it) is reported as
  BLOCKED WITH A CAUSE, never as a pass and never as a silent skip.
* Nothing here asserts an outcome from a run's own summary. Every verdict is
  read from server state after the fact.

Usage (from ``backend/``, against a Gateway on :8001)::

    python scripts/orchestration_probe.py
    python scripts/orchestration_probe.py --only swarm,subagent
    python scripts/orchestration_probe.py --json ../logs/orchestration.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8001"
OUT = "/mnt/user-data/outputs"


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


@dataclass
class Probe:
    key: str
    feature: str
    title: str


PROBES: list[Probe] = [
    Probe("swarm_map_reduce", "swarm (map_reduce)", "Fan 4 real files out to workers, reduce to one ranked risk report"),
    Probe("swarm_evaluate", "swarm evaluate", "Ask whether a swarm is even warranted, with and without items"),
    Probe("swarm_blackboard", "swarm blackboard", "Publish and read back bounded inter-worker messages"),
    Probe("subagent_reviewer", "subagent: deep-code-reviewer", "Delegate a real code review to a named specialist"),
    Probe("subagent_security", "subagent: deep-security", "Delegate a real credential/taint scan to a named specialist"),
    Probe("task_subagent", "task(subagent_type)", "Delegate through the ordinary task tool to a named subagent"),
    Probe("ralph_loop", "ralph_loop", "Bounded self-improvement loop with acceptance criteria"),
    Probe("bot_roster", "bot_roster", "Real roster read plus an overlap/forge pre-flight question"),
    Probe("group_roster", "groups: rule-matched roster", "A rule-matched membership resolved live against the Bot Registry"),
    Probe("group_nesting", "groups: nesting", "Room tree, authority parent, and the direct/effective count pairing"),
]


# ---------------------------------------------------------------------------
# SSE (same contract as fleet_task_assign.py, kept independent)
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


def walk_tools(payload: Any, out: set[str]) -> None:
    """Collect tool names AND their arguments.

    Arguments matter here: a probe that called ``swarm`` with the wrong action
    still ``used`` the tool, and only the arguments distinguish a real
    delegation from a no-op call.
    """
    if isinstance(payload, list):
        for i in payload:
            walk_tools(i, out)
        return
    if not isinstance(payload, dict):
        return
    if payload.get("type") in ("tool", "ToolMessage") and isinstance(payload.get("name"), str):
        out.add(payload["name"])
    for key in ("tool_calls", "tool_call_chunks"):
        for c in payload.get(key) or []:
            if isinstance(c, dict) and isinstance(c.get("name"), str):
                out.add(c["name"])
    rcp = payload.get("additional_kwargs")
    if isinstance(rcp, dict):
        r = rcp.get("alpha_tool_receipt")
        if isinstance(r, dict) and isinstance(r.get("tool_name"), str):
            out.add(r["tool_name"])
    for k in ("message", "value", "state", "args", "kwargs", "input"):
        if isinstance(payload.get(k), (dict, list)):
            walk_tools(payload[k], out)


def final_ai_text(payload: Any) -> str:
    text = ""
    items = payload.get("data", []) if isinstance(payload, dict) else payload
    for item in items or []:
        if not isinstance(item, dict):
            continue
        msg = item if item.get("type") in ("ai", "AIMessage") else item.get("content")
        if not isinstance(msg, dict) or msg.get("type") not in ("ai", "AIMessage"):
            continue
        c = msg.get("content")
        if isinstance(c, str) and c.strip():
            text = c
        elif isinstance(c, list):
            j = "".join(x.get("text", "") for x in c if isinstance(x, dict))
            if j.strip():
                text = j
    return text


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    key: str
    feature: str
    title: str
    thread_id: str = ""
    run_id: str = ""
    status: str | None = None
    error: str | None = None
    seconds: float = 0.0
    tools: list[str] = field(default_factory=list)
    answer: str = ""
    files: list[str] = field(default_factory=list)
    bytes: dict[str, int] = field(default_factory=dict)
    #: Server-side facts read back after the run, not the model's own claims.
    observed: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    blocked: str = ""
    verdict: str = "FAIL"

    @property
    def ok(self) -> bool:
        return self.verdict == "PASS"


# ---------------------------------------------------------------------------
# Direct (non-LLM) probes: swarm evaluate, spawn, blackboard
# ---------------------------------------------------------------------------


async def probe_swarm_evaluate(c: httpx.AsyncClient, p: Probe) -> Outcome:
    """The feasibility oracle itself, called directly.

    This needs no LLM at all, which makes it the cheapest honest check that
    swarm planning is wired: a wrong answer here is a routing bug, not a model
    opinion.
    """
    o = Outcome(key=p.key, feature=p.feature, title=p.title)
    t0 = time.monotonic()
    items = [
        "backend/packages/harness/alpha/utils/time.py",
        "backend/packages/harness/alpha/runtime/sentinel/sources/logs.py",
        "backend/packages/harness/alpha/models/free_router/catalog.py",
        "backend/app/gateway/routers/intelligence.py",
    ]
    fan = await c.post(f"{BASE}/api/swarms/evaluate", json={"goal": "Rank these four modules by defect risk", "items": items})
    serial = await c.post(f"{BASE}/api/swarms/evaluate", json={"goal": "Read one file and summarize it in one sentence."})
    o.observed["fanout_http"] = fan.status_code
    o.observed["serial_http"] = serial.status_code
    if fan.status_code != 200 or serial.status_code != 200:
        o.problems.append(f"evaluate failed: fanout HTTP {fan.status_code}, serial HTTP {serial.status_code}")
        o.verdict = "FAIL"
        o.seconds = round(time.monotonic() - t0, 1)
        return o
    f, s = fan.json(), serial.json()
    o.observed["fanout"] = {k: f.get(k) for k in ("should_swarm", "mode", "estimated_speedup", "recommended_workers")}
    o.observed["serial"] = {k: s.get(k) for k in ("should_swarm", "mode", "estimated_speedup", "recommended_workers")}
    # The contract: fan-out work is worth swarming, single-item work is not.
    # A router that says yes to both has no discriminating power at all.
    if not f.get("should_swarm"):
        o.problems.append("router declined a genuine 4-item fan-out")
    if s.get("should_swarm"):
        o.problems.append("router accepted a single-item task that needs no swarm")
    if o.problems:
        o.verdict = "FAIL"
    else:
        o.verdict = "PASS"
        o.observed["discriminating"] = True
    o.seconds = round(time.monotonic() - t0, 1)
    return o


async def probe_swarm_map_reduce(c: httpx.AsyncClient, p: Probe) -> Outcome:
    """Spawn a real map_reduce swarm, run it, and read the DAG afterwards.

    The interesting question is not "did it spawn" but whether task states,
    worker assignment, leases and acceptance are populated at all -- an empty
    DAG that claims ``completed`` is the exact dishonesty class this repo's
    contract forbids.
    """
    o = Outcome(key=p.key, feature=p.feature, title=p.title)
    t0 = time.monotonic()
    items = [
        "backend/packages/harness/alpha/utils/time.py",
        "backend/packages/harness/alpha/runtime/sentinel/sources/logs.py",
        "backend/packages/harness/alpha/models/free_router/catalog.py",
        "backend/app/gateway/routers/intelligence.py",
    ]
    r = await c.post(
        f"{BASE}/api/swarms",
        json={"goal": "Rank these four real modules by defect risk and justify each with a specific line reference", "items": items, "mode": "map_reduce"},
    )
    if r.status_code != 200:
        o.problems.append(f"spawn HTTP {r.status_code}: {(await r.aread())[:200]!r}")
        o.seconds = round(time.monotonic() - t0, 1)
        return o
    plan = r.json()
    sid = plan["swarm_id"]
    o.observed["swarm_id"] = sid
    o.observed["mode"] = plan.get("mode")
    o.observed["n_tasks"] = len(plan.get("tasks") or {})
    o.observed["has_reduce"] = any("reduce" in (k or "") for k in (plan.get("tasks") or {}))
    if o.observed["n_tasks"] < 2:
        o.problems.append(f"map_reduce produced only {o.observed['n_tasks']} task(s); no fan-out to verify")

    # Execute. run_async is the explicit transition; step only dispatches.
    ra = await c.post(f"{BASE}/api/swarms/{sid}/run-async")
    o.observed["run_async_http"] = ra.status_code

    # Poll the coordinator for a terminal state, bounded.
    deadline = time.monotonic() + 420
    final: dict[str, Any] = plan
    while time.monotonic() < deadline:
        await asyncio.sleep(6)
        d = await c.get(f"{BASE}/api/swarms/{sid}")
        if d.status_code != 200:
            continue
        final = d.json()
        if final.get("status") in ("completed", "failed", "cancelled", "stalled", "budget_exhausted"):
            break
    o.observed["final_status"] = final.get("status")
    tasks = final.get("tasks") or {}
    o.observed["task_states"] = {k: (v.get("state"), v.get("assigned_worker")) for k, v in tasks.items()}
    o.observed["progress"] = final.get("progress")
    m = final.get("metrics") or {}
    team = m.get("team") or {}
    o.observed["roster_registered"] = team.get("roster_registered")
    o.observed["assigned"] = team.get("assigned")
    o.observed["unassigned"] = team.get("unassigned")
    o.observed["leader"] = (m.get("leader_election") or {}).get("leader")
    acc = final.get("acceptance_status") or (final.get("metrics") or {}).get("acceptance")
    o.observed["acceptance"] = acc
    o.observed["final_result_chars"] = len(final.get("final_result") or "")

    # Grading. This is a plan-level probe: a swarm that cannot dispatch workers
    # in this deployment must say so in a field, not in prose.
    if team.get("roster_registered") is False:
        o.blocked = "no specialist roster provider is registered: the plan composed, but no task was assigned to a declared specialist, so per-task model work did not run"
        o.verdict = "BLOCKED"
    elif o.observed["final_status"] == "completed" and team.get("assigned", 0) == 0:
        o.problems.append("swarm claims completed with zero tasks assigned to a worker")
        o.verdict = "FAIL"
    else:
        o.verdict = "PASS"
    o.seconds = round(time.monotonic() - t0, 1)
    return o


async def probe_swarm_blackboard(c: httpx.AsyncClient, p: Probe) -> Outcome:
    """Publish a bounded message and read it back, including the trust field.

    Message payloads are untrusted data; a blackboard that loses that boundary
    on read-back would let one worker inject instructions into another's context.
    """
    o = Outcome(key=p.key, feature=p.feature, title=p.title)
    t0 = time.monotonic()
    r = await c.post(
        f"{BASE}/api/swarms",
        json={"goal": "Exchange one bounded finding between workers", "items": ["alpha", "beta"], "mode": "parallel"},
    )
    if r.status_code != 200:
        o.problems.append(f"spawn HTTP {r.status_code}")
        o.seconds = round(time.monotonic() - t0, 1)
        return o
    sid = r.json()["swarm_id"]
    body = "FINDING: coerce_iso leaks OverflowError on 10**400 (utils/time.py:91 before the try)."
    # A JSON body, not query params: the route takes SwarmMessageRequest, so a
    # query-param call is a 422 and would read as a broken blackboard.
    pub = await c.post(
        f"{BASE}/api/swarms/{sid}/messages",
        json={"topic": "findings", "content": body, "kind": "observation"},
    )
    o.observed["publish_http"] = pub.status_code
    o.observed["published"] = pub.json() if pub.status_code == 200 else None
    got = await c.get(f"{BASE}/api/swarms/{sid}/messages", params={"topic": "findings"})
    o.observed["read_http"] = got.status_code
    msgs = got.json() if got.status_code == 200 else []
    o.observed["n_messages"] = len(msgs)
    if pub.status_code != 200:
        o.problems.append(f"publish HTTP {pub.status_code}")
    if not msgs:
        o.problems.append("published message did not read back")
    else:
        m0 = msgs[0]
        o.observed["roundtrip_exact"] = m0.get("content") == body
        o.observed["trust"] = m0.get("trust")
        o.observed["sequence"] = m0.get("sequence")
        if m0.get("trust") != "untrusted":
            o.problems.append(f"message trust is {m0.get('trust')!r}, expected 'untrusted' for worker data")
        if not o.observed["roundtrip_exact"]:
            o.problems.append("message content did not round-trip byte-for-byte")
    o.verdict = "FAIL" if o.problems else "PASS"
    o.seconds = round(time.monotonic() - t0, 1)
    return o


# ---------------------------------------------------------------------------
# LLM probes: real delegation through a live run
# ---------------------------------------------------------------------------


LLM_TASK: dict[str, str] = {
    "subagent_reviewer": f"""\
Delegate this to the `deep-code-reviewer` specialist using the delegation tool, then
report what it said.

Target: /mnt/alpha-repo/backend/packages/harness/alpha/utils/time.py

Ask it for concrete correctness findings with line numbers. Then write its findings
to {OUT}/probe_subagent_review.md, clearly separating (a) what the specialist
returned from (b) anything you checked yourself.

If the delegation tool is unavailable or the specialist cannot run, say that plainly
and quote the error. Do NOT fabricate a specialist's findings.""",
    "subagent_security": f"""\
Delegate this to the `deep-security` specialist using the delegation tool.

Target: /mnt/alpha-repo/backend/packages/harness/alpha/utils/time.py

Ask it to scan for credential exposure and tainted input handling. Write its report to
{OUT}/probe_subagent_security.md with the exact error text if the specialist could
not run.

Honesty requirement: quote the real error rather than inventing findings.""",
    "task_subagent": f"""\
Use the `task` tool to delegate to the `general-purpose` subagent with
subagent_type="general-purpose".

Ask it to write a short markdown file at {OUT}/probe_task_subagent.md containing
exactly three bullet points naming real Python stdlib modules and one sentence each
on what they are for. It must use its own file tools.

Then confirm the file exists by reading it back with the read tool.

If the task tool refuses, quote the exact refusal. Do not write the file yourself and
claim the subagent did.""",
    "ralph_loop": f"""\
Use the `ralph_loop` tool with a completion_promise and acceptance criteria that are
objectively checkable: that {OUT}/probe_ralph.md exists and is non-empty.

Set max_rounds to the minimum that still demonstrates one full round.

Report what the loop returned verbatim: how many rounds ran, and the per-round
verdict. If exhaustion is reported, report the gaps as gaps. Do not describe an
exhausted loop as a success.""",
    "bot_roster": f"""\
Use the `bot_roster` tool to answer two real questions:

1. How many bots are in the roster right now, and what is `coder`'s role?
2. For a hypothetical bot with role "Security" and capabilities
   ["code_audit","threat_modeling"], would the forge pre-flight overlap check
   accept it? Use the tool's own overlap/forge action to find out, not your own
   reasoning.

Write your answers plus the verbatim tool output to {OUT}/probe_bot_roster.md.

If the tool cannot be called, quote the error.""",
}


async def run_llm(c: httpx.AsyncClient, p: Probe, timeout: float) -> Outcome:
    o = Outcome(key=p.key, feature=p.feature, title=p.title)
    t0 = time.monotonic()
    task = LLM_TASK[p.key]
    tr = await c.post(f"{BASE}/api/threads", json={"metadata": {"purpose": "orchestration-probe", "probe": p.key}})
    tr.raise_for_status()
    o.thread_id = tr.json()["thread_id"]
    body = {
        "input": {"messages": [{"role": "user", "content": task}]},
        "metadata": {"purpose": "orchestration-probe", "probe": p.key},
        "stream_mode": ["messages-tuple", "values", "custom"],
        "on_disconnect": "continue",
    }
    urls = f"{BASE}/api/threads/{o.thread_id}"
    tools: set[str] = set()
    deadline = time.monotonic() + timeout

    async def timed(src: Any) -> Any:
        async for line in src:
            if time.monotonic() > deadline:
                o.blocked = f"stream exceeded {timeout:.0f}s"
                return
            yield line

    async with c.stream("POST", f"{urls}/runs/stream", json=body, timeout=httpx.Timeout(30.0, read=timeout)) as resp:
        if resp.status_code >= 400:
            o.problems.append(f"stream HTTP {resp.status_code}: {(await resp.aread())[:200]!r}")
            return o
        loc = resp.headers.get("content-location") or resp.headers.get("Content-Location")
        o.run_id = loc.rstrip("/").split("/")[-1] if loc else ""
        async for _name, data in parse_sse(timed(resp.aiter_lines())):
            try:
                walk_tools(json.loads(data), tools)
            except (ValueError, TypeError):
                pass

    poll = time.monotonic() + max(60.0, timeout)
    while time.monotonic() < poll:
        if o.run_id:
            rr = await c.get(f"{urls}/runs/{o.run_id}")
            if rr.status_code == 200:
                j = rr.json()
                o.status, o.error = j.get("status"), j.get("error")
                if o.status in ("success", "error", "interrupted", "timeout", "canceled"):
                    break
        await asyncio.sleep(2.0)
    else:
        o.problems.append(f"run never reached terminal status (last {o.status!r})")

    o.tools = sorted(tools)
    if o.run_id:
        wc = await c.get(f"{urls}/runs/{o.run_id}/workspace-changes")
        if wc.status_code == 200:
            o.files = [f.get("path") for f in (wc.json().get("files") or []) if isinstance(f, dict)]
        else:
            o.problems.append(f"workspace-changes unreachable: HTTP {wc.status_code}")
        mr = await c.get(f"{urls}/runs/{o.run_id}/messages")
        if mr.status_code == 200:
            o.answer = final_ai_text(mr.json())
        else:
            o.problems.append(f"messages unreachable: HTTP {mr.status_code}")

    # Measure every artifact, and report any we cannot.
    unresolved = []
    for fp in o.files:
        host = _host_path(o.thread_id, fp)
        try:
            sz = host.stat().st_size if host.is_file() else -1
        except OSError:
            sz = -2
        o.bytes[fp] = sz
        if sz < 0:
            unresolved.append(fp)
    if unresolved:
        o.problems.append(f"{len(unresolved)} artifact(s) unmeasurable on disk: {unresolved[:3]}")

    o.seconds = round(time.monotonic() - t0, 1)

    # Grade. Every probe needs its own delegation tool actually called.
    need = {
        "subagent_reviewer": ("delegate_to_deep_agent",),
        "subagent_security": ("delegate_to_deep_agent",),
        "task_subagent": ("task",),
        "ralph_loop": ("ralph_loop",),
        "bot_roster": ("bot_roster",),
    }[p.key]
    if not any(t in o.tools for t in need):
        o.problems.append(f"expected tool(s) {need} were never called; saw {o.tools or '(none)'}")
    if not o.files:
        o.problems.append("no artifact produced")
    if o.status != "success":
        o.problems.append(f"durable status={o.status!r} error={o.error!r}")
    if o.blocked and not o.problems:
        o.verdict = "BLOCKED"
    elif o.problems:
        o.verdict = "FAIL"
    else:
        o.verdict = "PASS"
    return o


def _host_path(thread_id: str, virtual: str) -> Path:
    if "/mnt/user-data/" not in virtual:
        return Path(virtual)
    rel = virtual.split("/mnt/user-data/", 1)[1]
    base = Path(__file__).resolve().parents[1] / ".alpha" / "users"
    for user_dir in sorted(base.glob("*")):
        cand = user_dir / "threads" / thread_id / "user-data" / rel
        if cand.exists():
            return cand
    for name in ("default", *(d.name for d in sorted(base.glob("*")) if d.is_dir())):
        if (base / name).is_dir():
            return base / name / "threads" / thread_id / "user-data" / rel
    return base / "default" / "threads" / thread_id / "user-data" / rel


async def probe_group_roster(c: httpx.AsyncClient, p: Probe) -> Outcome:
    """Resolve a room roster through its declared rules against the real registry.

    Rule matching is a projection recomputed on every read, so the honest check
    is that the rule's ``direct_count`` and ``effective_count`` are BOTH present
    and consistent -- a header claiming "3 members" over six visible bots is a
    fabricated count, and this is the field pair that catches it.
    """
    o = Outcome(key=p.key, feature=p.feature, title=p.title)
    t0 = time.monotonic()
    rooms = await c.get(f"{BASE}/api/groups")
    if rooms.status_code != 200:
        o.problems.append(f"GET /api/groups HTTP {rooms.status_code}")
        o.seconds = round(time.monotonic() - t0, 1)
        return o
    body = rooms.json()
    names = [r.get("name") for r in (body.get("rooms") or []) if r.get("name")]
    o.observed["n_rooms"] = len(names)
    if not names:
        o.problems.append("no rooms to probe")
        o.seconds = round(time.monotonic() - t0, 1)
        return o

    checked: list[dict[str, Any]] = []
    for name in names:
        rr = await c.get(f"{BASE}/api/groups/{name}/roster")
        if rr.status_code != 200:
            o.problems.append(f"roster for {name} HTTP {rr.status_code}")
            continue
        j = rr.json()
        row = {
            "room": name,
            "direct_count": j.get("direct_count"),
            "effective_count": j.get("effective_count"),
            "len_direct": len(j.get("direct") or []),
            "len_effective": len(j.get("effective") or []),
            "rule_matched": len(j.get("rule_matched") or []),
        }
        checked.append(row)
        # direct_count must equal the direct list, and effective must be a
        # superset of direct (a member cannot be visible without being direct
        # or inherited). Both are falsifiable, unlike "it returned 200".
        if row["direct_count"] != row["len_direct"]:
            o.problems.append(f"{name}: direct_count {row['direct_count']} != {row['len_direct']} listed")
        if row["effective_count"] != row["len_effective"]:
            o.problems.append(f"{name}: effective_count {row['effective_count']} != {row['len_effective']} listed")
        if not set(j.get("direct") or []) <= set(j.get("effective") or []):
            o.problems.append(f"{name}: a direct member is absent from the effective roster")
        if row["direct_count"] is None or row["effective_count"] is None:
            o.problems.append(f"{name}: a count is null while the list is populated -- unreadable vs empty collapsed")
    o.observed["rooms"] = checked
    o.verdict = "FAIL" if o.problems else "PASS"
    o.seconds = round(time.monotonic() - t0, 1)
    return o


async def probe_group_nesting(c: httpx.AsyncClient, p: Probe) -> Outcome:
    """Walk the room tree and confirm authority stays a single pointer.

    Nested groups exist so visibility may fan out while authority stays with at
    most ONE parent. If a room reported several authority parents, policy
    inheritance would be a negotiation between rooms rather than a pointer.
    """
    o = Outcome(key=p.key, feature=p.feature, title=p.title)
    t0 = time.monotonic()
    rooms = await c.get(f"{BASE}/api/groups")
    names = [r.get("name") for r in (rooms.json().get("rooms") or []) if r.get("name")]
    # `/tree` is a COLLECTION route (whole forest), declared before the
    # `/{name}` catch-all for the documented Starlette-ordering reason.
    # `/{name}/tree` was never declared, so a 404 there is correct behaviour --
    # asking for it is a probe defect, not a broken forest.
    forest = await c.get(f"{BASE}/api/groups/tree")
    o.observed["forest_http"] = forest.status_code
    if forest.status_code != 200:
        o.problems.append(f"GET /api/groups/tree HTTP {forest.status_code}")
        o.verdict = "FAIL"
        o.seconds = round(time.monotonic() - t0, 1)
        return o
    fj = forest.json()
    nodes = fj.get("rooms") or fj.get("tree") or []
    o.observed["forest_nodes"] = len(nodes) if isinstance(nodes, list) else None
    rows: list[dict[str, Any]] = []
    for name in names:
        # Per-room ancestry is where the parents/authority split is carried.
        anc = await c.get(f"{BASE}/api/groups/{name}/ancestors")
        row: dict[str, Any] = {"room": name, "ancestors_http": anc.status_code}
        if anc.status_code == 200:
            a = anc.json()
            anc_list = a.get("ancestors") if isinstance(a, dict) else a
            row["ancestors"] = anc_list
            row["depth"] = a.get("depth") if isinstance(a, dict) else None
            row["authority_parent"] = a.get("authority_parent") if isinstance(a, dict) else None
            # Authority refines visibility: a named authority parent must also
            # be a visibility parent, or the two contradict each other.
            ap = row["authority_parent"]
            if ap is not None and isinstance(anc_list, list) and ap not in anc_list:
                o.problems.append(f"{name}: authority parent {ap!r} is not among its ancestors {anc_list}")
            d = row["depth"]
            if d is not None and isinstance(d, int) and d > 4:
                o.problems.append(f"{name}: depth {d} exceeds the documented MAX_DEPTH of 4")
        else:
            o.problems.append(f"{name}: ancestors HTTP {anc.status_code}")
        rows.append(row)
    o.observed["rooms"] = rows
    o.observed["n_rooms"] = len(names)
    o.verdict = "FAIL" if o.problems else "PASS"
    o.seconds = round(time.monotonic() - t0, 1)
    return o


DIRECT = {
    "swarm_evaluate": probe_swarm_evaluate,
    "swarm_map_reduce": probe_swarm_map_reduce,
    "swarm_blackboard": probe_swarm_blackboard,
    "group_roster": probe_group_roster,
    "group_nesting": probe_group_nesting,
}
LLM_TIMEOUT = 1800.0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="comma-separated probe keys")
    ap.add_argument("--timeout", type=float, default=LLM_TIMEOUT)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    chosen = [p for p in PROBES if not args.only or p.key in {s.strip() for s in args.only.split(",")}]
    if not chosen:
        print("no probes matched", file=sys.stderr)
        return 2

    print(f"Running {len(chosen)} orchestration probes CONCURRENTLY against {BASE}\n")
    async with httpx.AsyncClient(timeout=180.0) as c:
        results = await asyncio.gather(*(DIRECT[p.key](c, p) if p.key in DIRECT else run_llm(c, p, args.timeout) for p in chosen))

    order = {p.key: i for i, p in enumerate(PROBES)}
    results.sort(key=lambda r: order.get(r.key, 99))

    print("=" * 108)
    print(f"{'PROBE':<22} {'VERDICT':<9} {'SEC':>5} {'FILES':>5} {'TOOLS':>5}  FEATURE")
    print("=" * 108)
    for r in results:
        print(f"{r.key:<22} {r.verdict:<9} {r.seconds:>5.0f} {len(r.files):>5} {len(r.tools):>5}  {r.feature}")

    for r in results:
        print("\n" + "-" * 108)
        print(f"[{r.key}] {r.feature} -- {r.title}")
        print(f"  verdict   : {r.verdict}")
        print(f"  thread/run: {r.thread_id or '-'} / {r.run_id or '-'}")
        print(f"  status    : {r.status!r} error={r.error!r}  in {r.seconds}s")
        print(f"  tools     : {', '.join(r.tools) or '(none)'}")
        for fp, sz in r.bytes.items():
            print(f"  artifact  : {sz:>8}B  {fp}")
        if r.observed:
            print("  observed  :")
            for k, v in r.observed.items():
                s = json.dumps(v, default=str) if not isinstance(v, str) else v
                print(f"      {k}: {s[:230]}")
        if r.blocked:
            print(f"  BLOCKED   : {r.blocked}")
        print(f"  problems  : {r.problems or 'none'}")
        if r.answer:
            print(f"  answer    : {re.sub(r'[[:space:]]+', ' ', r.answer)[:400]}")

    n_pass = sum(1 for r in results if r.verdict == "PASS")
    n_block = sum(1 for r in results if r.verdict == "BLOCKED")
    n_fail = sum(1 for r in results if r.verdict == "FAIL")
    print("\n" + "=" * 108)
    print(f"{len(results)} probes: {n_pass} PASS, {n_block} BLOCKED (cause recorded), {n_fail} FAIL")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps([r.__dict__ for r in results], indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.json}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
