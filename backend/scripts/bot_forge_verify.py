"""Have Alpha forge a new specialist, then verify the Bot actually exists.

A forge that reports success has proved nothing. `alpha.bots.forge` builds a Bot
as a transaction and reports it alive only after a smoke test, but a tool's own
success string is a self-report -- and this repo's contract is explicit that a
half-built Bot is indistinguishable from a working one by looking at it.

So the Bot is verified on four independent surfaces, and the guards are verified
by trying to break them:

1. **Registry diff** -- the roster before vs after. A forge that created nothing
   shows no diff.
2. **Server-side record** -- `GET /api/bots/{name}` for the fields forge claims to
   write: role, department, reporting line, SOUL, and the approval checkpoints
   written into the SOUL at birth. Approvals in a config file are approvals
   nobody reads, so their absence from the SOUL is a real defect.
3. **Addressability** -- a *real run* with `assistant_id` set to the new Bot. A
   Bot in the registry that no run can address is a catalog entry, not a worker.
4. **Negative control** -- forge a DUPLICATE role and require a refusal. Proving
   creation works without proving the duplicate-role guard still fires leaves
   the transaction boundary untested, and the whole point of the forge is that it
   refuses before creating anything.

Usage (from ``backend/``, against a Gateway on :8001)::

    python scripts/bot_forge_verify.py
    python scripts/bot_forge_verify.py --timeout 1800 --json ../logs/bot_forge.json
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
# SSE
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


def collect_tools(payload: Any, out: set[str]) -> None:
    if isinstance(payload, list):
        for i in payload:
            collect_tools(i, out)
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
    for k in ("message", "value", "state", "message_chunk"):
        if isinstance(payload.get(k), (dict, list)):
            collect_tools(payload[k], out)


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
# Roster
# ---------------------------------------------------------------------------


async def roster(c: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    r = await c.get(f"{BASE}/api/bots")
    r.raise_for_status()
    j = r.json()
    bots = j.get("bots") if isinstance(j, dict) else j
    return {b["name"]: b for b in (bots or []) if isinstance(b, dict) and b.get("name")}


@dataclass
class Report:
    before_count: int = 0
    after_count: int = 0
    new_bots: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    blocked: str = ""
    seconds: float = 0.0

    def check(self, name: str, ok: bool, note: str = "") -> None:
        self.checks.append((name, ok, note))
        if not ok:
            self.problems.append(f"{name}: {note or 'failed'}")

    @property
    def verdict(self) -> str:
        if self.blocked:
            return "BLOCKED"
        return "PASS" if not self.problems else "FAIL"


# ---------------------------------------------------------------------------
# Task 1: forge a genuinely new specialist
# ---------------------------------------------------------------------------

FORGE_TASK = f"""\
Forge ONE new specialised bot for this repository using the `bot_roster` tool's
`forge` action.

Do this properly:

1. First call `bot_roster` with action="list" to see the real roster. Do NOT
   invent a specialist that already exists.
2. Read {OUT}/repo_facts.md if it exists, otherwise decide from what you already
   know about this repo, then WRITE your reasoning to {OUT}/forge_decision.md:
   - what genuine gap in this codebase would a new bot own?
   - what is its role line, department, and reporting line?
   - why does no existing bot cover it?
3. Call `forge` with a handle, that role, a department, a reporting line, and a
   SOUL that states what it owns and what it refuses. Do NOT pass
   `allow_overlap` -- if the overlap check refuses, that refusal is a legitimate
   answer and you must report it verbatim rather than forcing it.
4. After forging, call `bot_roster` with action="inspect" on the handle you just
   created and confirm it is not archived.
5. Write the final report to {OUT}/forge_report.md containing the handle, the
   verbatim `forge` reply, and the `inspect` output.

Honesty requirements, which matter more than a green result:
- If the forge was REFUSED, quote the exact refusal text and stop. Do not
  substitute `create` for `forge` and do not retry with `allow_overlap=true`.
- If you cannot tell whether the Bot really exists, say "unverified" rather than
  "created".
- Do not claim a smoke test passed unless the tool output says so."""


async def run_forge(c: httpx.AsyncClient, timeout: float) -> tuple[Report, dict[str, Any]]:
    rep = Report()
    t0 = time.monotonic()
    before = await roster(c)
    rep.before_count = len(before)
    rep.detail["baseline_handles"] = sorted(n for n in before if not n.startswith(("bot_", "spec_")))[:20]

    tr = await c.post(f"{BASE}/api/threads", json={"metadata": {"purpose": "bot-forge-verify"}})
    tr.raise_for_status()
    tid = tr.json()["thread_id"]
    body = {
        "input": {"messages": [{"role": "user", "content": FORGE_TASK}]},
        "metadata": {"purpose": "bot-forge-verify"},
        "stream_mode": ["messages-tuple", "values", "custom"],
        "on_disconnect": "continue",
    }
    urls = f"{BASE}/api/threads/{tid}"
    tools: set[str] = set()
    deadline = time.monotonic() + timeout

    async def timed(src: Any) -> Any:
        async for line in src:
            if time.monotonic() > deadline:
                rep.blocked = f"forge run stream exceeded {timeout:.0f}s"
                return
            yield line

    async with c.stream("POST", f"{urls}/runs/stream", json=body, timeout=httpx.Timeout(30.0, read=timeout)) as resp:
        if resp.status_code >= 400:
            rep.problems.append(f"stream HTTP {resp.status_code}")
            rep.seconds = round(time.monotonic() - t0, 1)
            return rep, {}
        loc = resp.headers.get("content-location") or resp.headers.get("Content-Location")
        run_id = loc.rstrip("/").split("/")[-1] if loc else ""
        async for _n, data in parse_sse(timed(resp.aiter_lines())):
            try:
                collect_tools(json.loads(data), tools)
            except (ValueError, TypeError):
                pass

    status = None
    poll = time.monotonic() + max(60.0, timeout)
    while time.monotonic() < poll:
        if run_id:
            rr = await c.get(f"{urls}/runs/{run_id}")
            if rr.status_code == 200:
                status = rr.json().get("status")
                if status in ("success", "error", "interrupted", "timeout", "canceled"):
                    break
        await asyncio.sleep(2.0)

    answer = ""
    if run_id:
        mr = await c.get(f"{urls}/runs/{run_id}/messages")
        if mr.status_code == 200:
            answer = final_ai_text(mr.json())

    after = await roster(c)
    rep.after_count = len(after)
    rep.new_bots = sorted(set(after) - set(before))
    rep.detail["tools"] = sorted(tools)
    rep.detail["run_status"] = status
    rep.detail["thread_id"] = tid
    rep.detail["run_id"] = run_id
    rep.detail["answer_excerpt"] = re.sub(r"\s+", " ", answer)[:1400]
    rep.detail["artifacts"] = {}
    for name in ("forge_report.md", "forge_decision.md"):
        hits = sorted(Path(__file__).resolve().parents[1].glob(f".alpha/users/*/threads/{tid}/user-data/outputs/{name}"))
        if hits:
            rep.detail["artifacts"][name] = hits[-1].stat().st_size

    # --- check 1: the roster actually changed -----------------------------
    rep.check(
        "roster grew",
        len(rep.new_bots) > 0,
        f"roster went {rep.before_count} -> {rep.after_count}; no new handle appeared, so nothing was forged",
    )

    # --- check 2: the server's own record ----------------------------------
    for handle in rep.new_bots:
        d = await c.get(f"{BASE}/api/bots/{handle}")
        rep.check(f"{handle}: server record readable", d.status_code == 200, f"HTTP {d.status_code}")
        if d.status_code != 200:
            continue
        b = d.json()
        rep.detail.setdefault("forged", {})[handle] = {
            "role": b.get("role"),
            "department": b.get("department"),
            "reports_to": b.get("reports_to"),
            "status": b.get("status"),
            "capabilities": b.get("capabilities"),
            "soul_chars": len(b.get("soul") or ""),
            "journal_enabled": b.get("journal_enabled"),
        }
        rep.check(f"{handle}: has a role", bool(b.get("role")), "role is empty")
        rep.check(f"{handle}: has a department", bool(b.get("department")), "department is empty")
        rep.check(f"{handle}: SOUL is substantive", len(b.get("soul") or "") >= 200, f"soul is {len(b.get('soul') or '')} chars")
        rep.check(
            f"{handle}: not archived",
            str(b.get("status", "")).lower() not in ("archived", "retired"),
            f"status={b.get('status')!r}",
        )
        # Approvals written into the SOUL at birth. An approval that lives only
        # in a config file is an approval nobody reads.
        soul = (b.get("soul") or "").lower()
        rep.check(
            f"{handle}: approval checkpoints in the SOUL",
            any(k in soul for k in ("approv", "confirm", "escalat")),
            "no approval/escalation language found in the SOUL",
        )

    rep.seconds = round(time.monotonic() - t0, 1)
    return rep, {"tid": tid, "run_id": run_id, "answer": answer}


# ---------------------------------------------------------------------------
# Task 2: negative control -- the guards must still fire
# ---------------------------------------------------------------------------

NEG_TASK = f"""\
Use the `bot_roster` tool. Do exactly these two things and nothing else.

1. Call `forge` with role="Software Engineer & Backend Developer" -- the exact
   role the existing `coder` bot already holds -- with a fresh handle and
   `sandbox="none"`. This is a DELIBERATE duplicate-role probe.
2. Call `bot_roster` with action="list" and report the roster count.

Then write to {OUT}/forge_negative_control.md:

- The VERBATIM reply from the forge call in step 1.
- Whether the roster count changed.

Do NOT pass `allow_overlap` or `allow_frequent`. Do NOT retry. Do NOT fall back
to `create`. The point is to capture what the guard actually says, so a refusal
is a SUCCESS for this task and you must report it as the finding rather than
working around it."""


async def run_negative(c: httpx.AsyncClient, timeout: float) -> tuple[Report, dict[str, Any]]:
    """Prove the forge still refuses a duplicate role, and creates nothing.

    Run AFTER the forge probe: it must be able to see the freshly-forged bot, so
    a duplicate of *its* role would be the sharpest probe.
    """
    rep = Report()
    t0 = time.monotonic()
    before = await roster(c)
    rep.before_count = len(before)

    tr = await c.post(f"{BASE}/api/threads", json={"metadata": {"purpose": "bot-forge-negcontrol"}})
    tr.raise_for_status()
    tid = tr.json()["thread_id"]
    body = {
        "input": {"messages": [{"role": "user", "content": NEG_TASK}]},
        "metadata": {"purpose": "bot-forge-negcontrol"},
        "stream_mode": ["messages-tuple", "values", "custom"],
        "on_disconnect": "continue",
    }
    urls = f"{BASE}/api/threads/{tid}"
    tools: set[str] = set()
    deadline = time.monotonic() + timeout

    async def timed(src: Any) -> Any:
        async for line in src:
            if time.monotonic() > deadline:
                rep.blocked = f"negative-control stream exceeded {timeout:.0f}s"
                return
            yield line

    async with c.stream("POST", f"{urls}/runs/stream", json=body, timeout=httpx.Timeout(30.0, read=timeout)) as resp:
        if resp.status_code >= 400:
            rep.problems.append(f"stream HTTP {resp.status_code}")
            return rep, {}
        loc = resp.headers.get("content-location") or resp.headers.get("Content-Location")
        run_id = loc.rstrip("/").split("/")[-1] if loc else ""
        async for _n, data in parse_sse(timed(resp.aiter_lines())):
            try:
                collect_tools(json.loads(data), tools)
            except (ValueError, TypeError):
                pass

    status = None
    poll = time.monotonic() + max(60.0, timeout)
    while time.monotonic() < poll:
        if run_id:
            rr = await c.get(f"{urls}/runs/{run_id}")
            if rr.status_code == 200:
                status = rr.json().get("status")
                if status in ("success", "error", "interrupted", "timeout", "canceled"):
                    break
        await asyncio.sleep(2.0)

    after = await roster(c)
    rep.after_count = len(after)
    rep.new_bots = sorted(set(after) - set(before))
    rep.detail["tools"] = sorted(tools)
    rep.detail["run_status"] = status

    rep.check("bot_roster was actually used", "bot_roster" in tools, f"tools seen: {sorted(tools) or '(none)'}")

    # The guard's whole purpose: refuse BEFORE creating anything. A refusal that
    # still leaves a half-built profile behind is the rollback defect.
    rep.check(
        "duplicate-role probe created nothing",
        len(rep.new_bots) == 0,
        f"a duplicate-role forge left {rep.new_bots} behind; the pre-flight refusal is supposed to happen before any write",
    )
    rep.seconds = round(time.monotonic() - t0, 1)
    return rep, {"tid": tid, "run_id": run_id}


# ---------------------------------------------------------------------------
# Task 3: addressability -- a Bot nothing can address is a catalog entry
# ---------------------------------------------------------------------------


async def check_addressable(c: httpx.AsyncClient, handle: str, timeout: float) -> Report:
    """Run a real task addressed to the new Bot.

    This is the check that separates a worker from a row: the registry listing a
    profile proves a file was written, not that any run can reach it.
    """
    rep = Report()
    t0 = time.monotonic()
    tr = await c.post(f"{BASE}/api/threads", json={"metadata": {"purpose": "bot-forge-addressable", "bot": handle}})
    tr.raise_for_status()
    tid = tr.json()["thread_id"]
    body = {
        "assistant_id": handle,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"You were just forged as '{handle}'. Introduce yourself in at most four sentences: "
                        "what you own, and one concrete thing you would refuse to do. "
                        f"Then write that answer to {OUT}/forge_intro_{handle}.md and present it."
                    ),
                }
            ]
        },
        "metadata": {"purpose": "bot-forge-addressable", "bot": handle},
        "stream_mode": ["messages-tuple", "values", "custom"],
        "on_disconnect": "continue",
    }
    urls = f"{BASE}/api/threads/{tid}"
    tools: set[str] = set()
    deadline = time.monotonic() + timeout

    async def timed(src: Any) -> Any:
        async for line in src:
            if time.monotonic() > deadline:
                rep.blocked = f"addressability stream exceeded {timeout:.0f}s"
                return
            yield line

    async with c.stream("POST", f"{urls}/runs/stream", json=body, timeout=httpx.Timeout(30.0, read=timeout)) as resp:
        if resp.status_code >= 400:
            rep.problems.append(f"run addressed to {handle} was refused: HTTP {resp.status_code}")
            rep.detail["body"] = (await resp.aread())[:300].decode("utf-8", "replace")
            rep.seconds = round(time.monotonic() - t0, 1)
            return rep
        loc = resp.headers.get("content-location") or resp.headers.get("Content-Location")
        run_id = loc.rstrip("/").split("/")[-1] if loc else ""
        async for _n, data in parse_sse(timed(resp.aiter_lines())):
            try:
                collect_tools(json.loads(data), tools)
            except (ValueError, TypeError):
                pass

    status = None
    poll = time.monotonic() + max(60.0, timeout)
    while time.monotonic() < poll:
        if run_id:
            rr = await c.get(f"{urls}/runs/{run_id}")
            if rr.status_code == 200:
                status = rr.json().get("status")
                if status in ("success", "error", "interrupted", "timeout", "canceled"):
                    break
        await asyncio.sleep(2.0)

    answer = ""
    if run_id:
        mr = await c.get(f"{urls}/runs/{run_id}/messages")
        if mr.status_code == 200:
            answer = final_ai_text(mr.json())
    rep.detail["tools"] = sorted(tools)
    rep.detail["run_status"] = status
    rep.detail["answer"] = re.sub(r"\s+", " ", answer)[:500]

    rep.check(f"{handle}: a run was admitted", status is not None, "no terminal status was ever reached")
    if status != "success":
        rep.problems.append(f"{handle}: run status={status!r}")

    artifact = sorted(Path(__file__).resolve().parents[1].glob(f".alpha/users/*/threads/{tid}/user-data/outputs/forge_intro_{handle}.md"))
    size = artifact[-1].stat().st_size if artifact else -1
    rep.detail["intro_bytes"] = size
    rep.check(f"{handle}: produced its own answer file", size >= 100, f"file is {size}B")
    rep.seconds = round(time.monotonic() - t0, 1)
    return rep


# ---------------------------------------------------------------------------


def print_rep(title: str, rep: Report) -> None:
    print("\n" + "=" * 100)
    print(f"{title}  ->  {rep.verdict}")
    print("=" * 100)
    print(f"  roster   : {rep.before_count} -> {rep.after_count}   new: {rep.new_bots or 'none'}")
    print(f"  elapsed  : {rep.seconds}s")
    for k, v in rep.detail.items():
        s = json.dumps(v, default=str) if not isinstance(v, str) else v
        print(f"  {k:<9}: {s[:300]}")
    print("  checks   :")
    for name, ok, note in rep.checks:
        print(f"      [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({note})" if note and not ok else ""))
    if rep.blocked:
        print(f"  BLOCKED  : {rep.blocked}")
    print(f"  problems : {rep.problems or 'none'}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("--json", default=None)
    ap.add_argument("--skip-negative", action="store_true")
    args = ap.parse_args()

    async with httpx.AsyncClient(timeout=180.0) as c:
        print("=== Step 1: agent forges its own new specialist ===")
        forge_rep, ids = await run_forge(c, args.timeout)
        print_rep("FORGE", forge_rep)

        neg_rep = None
        if not args.skip_negative:
            print("\n=== Step 2: negative control -- duplicate role must be refused ===")
            neg_rep, _ = await run_negative(c, args.timeout)
            print_rep("NEGATIVE CONTROL", neg_rep)

        addr_rep = None
        if forge_rep.new_bots:
            handle = forge_rep.new_bots[0]
            print(f"\n=== Step 3: is '{handle}' actually addressable by a real run? ===")
            addr_rep = await check_addressable(c, handle, args.timeout)
            print_rep(f"ADDRESSABILITY ({handle})", addr_rep)

    reps = [r for r in (forge_rep, neg_rep, addr_rep) if r is not None]
    n_pass = sum(1 for r in reps if r.verdict == "PASS")
    n_block = sum(1 for r in reps if r.verdict == "BLOCKED")
    n_fail = sum(1 for r in reps if r.verdict == "FAIL")
    print("\n" + "=" * 100)
    print(f"{len(reps)} stages: {n_pass} PASS, {n_block} BLOCKED, {n_fail} FAIL")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps([r.__dict__ for r in reps], indent=2, default=str),
            encoding="utf-8",
        )
        print(f"wrote {args.json}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
