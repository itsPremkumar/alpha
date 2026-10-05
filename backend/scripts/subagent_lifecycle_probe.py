"""Create a managed subagent for real, then prove it is a worker and not a row.

The user-visible question is "can this thing make and use a subagent?". Three
parts of that answer are separate, and this file keeps them separate because
conflating them is how a catalog entry gets reported as a capability:

1. **Can an AGENT create one?** Checked first, by actually asking one. If the
   agent has no tool for it, that is the answer and it must be reported as
   *absent*, not worked around via HTTP and presented as if the agent could.
2. **Can an ADMIN create one, with the guards intact?** Checked with a real
   create plus a negative control per guard, because a create that accepts
   everything is not a create.
3. **Does the new subagent actually RUN?** Checked by driving a real task
   through it and reading the artifact off disk. This is the step that usually
   fails, and the failure mode is deliberately reported as BLOCKED-with-cause
   rather than as a pass, because a subagent that is listed but never dispatched
   is exactly the "declared is not wired" failure this repo forbids.

Every created subagent is deleted in a ``finally`` so a probe that fails
mid-flight leaves nothing behind.

Usage (from ``backend/``, against a Gateway on :8001)::

    python scripts/subagent_lifecycle_probe.py
    python scripts/subagent_lifecycle_probe.py --skip-agent-attempt --json ../logs/subagents.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8001"
OUT = "/mnt/user-data/outputs"


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
    for k in ("message", "value", "state"):
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


@dataclass
class R:
    key: str
    title: str
    verdict: str = "FAIL"
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    blocked: str = ""
    observed: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0

    def check(self, name: str, ok: bool, note: str = "") -> bool:
        self.checks.append((name, ok, note))
        if not ok:
            self.problems.append(f"{name}: {note or 'failed'}")
        return ok

    def expect_refusal(self, name: str, resp: httpx.Response, must_contain: str = "") -> bool:
        body = ""
        try:
            body = resp.text
        except Exception:  # noqa: BLE001
            pass
        refused = resp.status_code in (400, 403, 404, 409, 422)
        if refused and must_contain:
            refused = must_contain.lower() in body.lower()
        return self.check(
            name,
            refused,
            f"expected a refusal containing {must_contain!r}; got HTTP {resp.status_code}: {body[:200]}",
        )


def host_path(thread_id: str, virtual: str) -> Path | None:
    if "/mnt/user-data/" not in virtual:
        return None
    rel = virtual.split("/mnt/user-data/", 1)[1]
    base = Path(__file__).resolve().parents[1] / ".alpha" / "users"
    for user_dir in base.glob("*"):
        cand = user_dir / "threads" / thread_id / "user-data" / rel
        if cand.exists():
            return cand
    return None


# ---------------------------------------------------------------------------
# 1. Can an AGENT create a subagent?
# ---------------------------------------------------------------------------

AGENT_CREATE_TASK = """\
Create a new managed subagent called `probe-agent-made` for this repository.

You must do this with a TOOL you actually have. Try, in order:
1. Any tool whose name or description covers creating, registering or managing a
   subagent (check `alpha_capability` action=search if you are unsure what
   exists).
2. If no such tool exists, say so plainly and list the tools you do have that
   came closest.

Then write your findings to {out}/agent_subagent_attempt.md, including:
- the exact name of any tool you called and its verbatim output;
- or, if none exists, the tool names you checked.

Do NOT shell out, do NOT edit config.yaml, and do NOT use write_file to fake a
subagent definition file. Those are not creating a subagent and reporting them as
success would be a fabrication. If you cannot create one, say "NO TOOL EXISTS"
in your answer."""


async def probe_agent_attempt(c: httpx.AsyncClient, timeout: float) -> R:
    """Ask a real agent to create a subagent, and record what actually happened."""
    r = R(key="agent_attempt", title="Can an agent create a managed subagent?")
    t0 = time.monotonic()
    tr = await c.post(f"{BASE}/api/threads", json={"metadata": {"purpose": "subagent-probe"}})
    tr.raise_for_status()
    tid = tr.json()["thread_id"]
    body = {
        "input": {"messages": [{"role": "user", "content": AGENT_CREATE_TASK.format(out=OUT)}]},
        "metadata": {"purpose": "subagent-probe"},
        "stream_mode": ["messages-tuple", "values", "custom"],
        "on_disconnect": "continue",
    }
    urls = f"{BASE}/api/threads/{tid}"
    tools: set[str] = set()
    deadline = time.monotonic() + timeout

    async def timed(src: Any) -> Any:
        async for line in src:
            if time.monotonic() > deadline:
                r.blocked = f"stream exceeded {timeout:.0f}s"
                return
            yield line

    async with c.stream("POST", f"{urls}/runs/stream", json=body, timeout=httpx.Timeout(30.0, read=timeout)) as resp:
        if resp.status_code >= 400:
            r.problems.append(f"stream HTTP {resp.status_code}")
            return r
        loc = resp.headers.get("content-location") or resp.headers.get("Content-Location")
        run_id = loc.rstrip("/").split("/")[-1] if loc else ""
        async for _n, d in parse_sse(timed(resp.aiter_lines())):
            try:
                collect_tools(json.loads(d), tools)
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
    r.observed["thread_id"] = tid
    r.observed["run_status"] = status
    r.observed["tools_called"] = sorted(tools)
    r.observed["answer"] = re.sub(r"\s+", " ", answer)[:900]

    # Did the agent create one? Checked against the server, not its own claim.
    listing = await c.get(f"{BASE}/api/subagents")
    names = {s["name"] for s in (listing.json().get("subagents") or [])} if listing.status_code == 200 else set()
    r.observed["n_subagents_now"] = len(names)
    created = "probe-agent-made" in names
    r.observed["agent_created_it"] = created
    r.check(
        "the agent's answer matches server state",
        created == ("NO TOOL EXISTS" not in answer.upper() and "no tool exists" not in answer.lower()),
        f"agent_created={created} but the answer implies otherwise: {answer[:200]}",
    )
    if created:
        r.verdict = "PASS"
    else:
        # Not a product failure: this is a capability finding, and the honest
        # verdict is ABSENT so it cannot be read as a green row.
        r.blocked = "no model-facing tool can create a managed subagent; creation is admin-HTTP-only"
        r.verdict = "BLOCKED"
    r.seconds = round(time.monotonic() - t0, 1)
    return r


# ---------------------------------------------------------------------------
# 2. Admin create + guards
# ---------------------------------------------------------------------------


def definition(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "display_name": "Probe Artifact Verifier",
        "description": "Writes and verifies a small artifact file, then reports its byte size. Use when a file must be written and read back as proof.",
        "system_prompt": (
            "You verify artifacts. When asked to write a file, use write_file with the exact path given, "
            "then read it back with read_file and report the byte size you actually observed. "
            "Never claim a file exists that you have not read back."
        ),
        "tools": ["write_file", "read_file"],
        "max_turns": 8,
        "timeout_seconds": 240,
    }


async def probe_admin_create(c: httpx.AsyncClient) -> R:
    r = R(key="admin_create", title="Admin create + guard negative controls")
    t0 = time.monotonic()
    name = f"probe-verifier-{uuid.uuid4().hex[:6]}"
    r.observed["created_name"] = name

    before = await c.get(f"{BASE}/api/subagents")
    before_names = {s["name"] for s in (before.json().get("subagents") or [])} if before.status_code == 200 else set()
    r.observed["baseline_count"] = len(before_names)

    # --- negative controls FIRST, so a permissive server is caught ---------
    r.expect_refusal(
        "an invalid name (underscore) is refused",
        await c.post(f"{BASE}/api/subagents", json={**definition("bad_name"), "name": "bad_name"}),
        "match",
    )
    listing0 = await c.get(f"{BASE}/api/subagents")
    builtins = {s["name"] for s in (listing0.json().get("subagents") or [])} if listing0.status_code == 200 else set()
    collide = next((n for n in ("bash", "general-purpose") if n in builtins), None)
    if collide:
        r.expect_refusal(
            f"a builtin name collision ({collide}) is refused",
            await c.post(f"{BASE}/api/subagents", json={**definition(collide), "name": collide}),
            "",
        )
    else:
        r.observed["collision_probe"] = "skipped: no builtin name available to collide with"
    r.expect_refusal(
        "an unknown model is refused",
        await c.post(f"{BASE}/api/subagents", json={**definition(f"{name}-m"), "model": "no-such-model-xyz"}),
        "unknown model",
    )
    r.expect_refusal(
        "a missing description is refused",
        await c.post(f"{BASE}/api/subagents", json={"name": f"{name}-d", "system_prompt": "x"}),
        "",
    )
    r.expect_refusal(
        "an extra unknown field is refused (extra=forbid)",
        await c.post(f"{BASE}/api/subagents", json={**definition(f"{name}-x"), "totally_unknown_field": 1}),
        "",
    )
    r.expect_refusal(
        "a zero max_turns is refused (ge=1)",
        await c.post(f"{BASE}/api/subagents", json={**definition(f"{name}-t"), "max_turns": 0}),
        "",
    )

    # Nothing above should have created anything.
    mid = await c.get(f"{BASE}/api/subagents")
    mid_names = {s["name"] for s in (mid.json().get("subagents") or [])} if mid.status_code == 200 else set()
    r.observed["count_after_negative_controls"] = len(mid_names)
    r.check(
        "no refused create left a definition behind",
        mid_names == before_names,
        f"refused creates added: {sorted(mid_names - before_names)}",
    )

    # --- the real create ----------------------------------------------------
    cr = await c.post(f"{BASE}/api/subagents", json=definition(name))
    r.observed["create_http"] = cr.status_code
    r.check("managed subagent created", cr.status_code in (200, 201), f"HTTP {cr.status_code}: {cr.text[:250]}")
    if cr.status_code >= 400:
        r.seconds = round(time.monotonic() - t0, 1)
        return r
    cj = cr.json()
    r.observed["source"] = cj.get("source")
    r.observed["enabled"] = cj.get("enabled")
    r.observed["model"] = cj.get("model")
    r.observed["editable"] = cj.get("editable")
    r.observed["conflict"] = cj.get("conflict")
    r.observed["disallowed_tools"] = cj.get("disallowed_tools")
    r.check("reported as managed", cj.get("source") == "managed", f"source={cj.get('source')!r}")
    r.check("editable, not a shadowed builtin", cj.get("editable") is True, f"editable={cj.get('editable')!r}")
    r.check("no config conflict", cj.get("conflict") is False, f"conflict={cj.get('conflict')!r}")
    # The forced-disallowed set is the no-nested-loop guard; it must be present
    # even though the request did not ask for it.
    dis = set(cj.get("disallowed_tools") or [])
    r.observed["required_disallowed"] = sorted(dis)
    for required in ("task", "ralph_loop"):
        r.check(f"{required} is force-disallowed", required in dis, f"disallowed_tools={sorted(dis)}")

    # --- update -------------------------------------------------------------
    ur = await c.put(f"{BASE}/api/subagents/{name}", json={"display_name": "Probe Verifier v2", "max_turns": 9})
    r.observed["update_http"] = ur.status_code
    r.check("update accepted", ur.status_code == 200, f"HTTP {ur.status_code}: {ur.text[:200]}")
    if ur.status_code == 200:
        r.observed["updated"] = {"display_name": ur.json().get("display_name"), "max_turns": ur.json().get("max_turns")}
        r.check("update is durable on read-back", ur.json().get("max_turns") == 9, f"max_turns={ur.json().get('max_turns')}")

    # --- disable ------------------------------------------------------------
    dr = await c.put(f"{BASE}/api/subagents/{name}", json={"enabled": False})
    r.observed["disable_http"] = dr.status_code
    dis_listing = await c.get(f"{BASE}/api/subagents")
    row = next((s for s in (dis_listing.json().get("subagents") or []) if s["name"] == name), None)
    r.observed["after_disable"] = row.get("enabled") if row else None
    r.check(
        "disable is durable on read-back",
        bool(row) and row.get("enabled") is False,
        f"enabled={row.get('enabled') if row else None!r}",
    )
    await c.put(f"{BASE}/api/subagents/{name}", json={"enabled": True})

    r.seconds = round(time.monotonic() - t0, 1)
    r.verdict = "PASS" if not r.problems else "FAIL"
    return r


# ---------------------------------------------------------------------------
# 3. Does it actually RUN?
# ---------------------------------------------------------------------------

RUN_TASK = """\
Delegate to the managed subagent `PROBE_NAME` using the delegation tool available
to you (check `list_available_deep_agents` / `alpha_capability` action=search for
the exact tool name).

Ask it to write exactly this file with the write_file tool:
  /mnt/user-data/outputs/PROBE_ARTIFACT.md

...containing exactly three lines:
1. a line beginning `probe-subagent:`
2. a line beginning `probe-artifact-bytes:`
3. any third line.

After the subagent returns, report:
- the exact tool you called and its verbatim return value;
- whether the file exists, and its byte size if you can read it.

Honesty requirements:
- If the delegation tool is not available, or the subagent is not listed, say so
  and quote the exact error or the list you actually saw.
- If the delegation fails, report the failure. Do NOT write the file yourself and
  do not claim the subagent produced it."""


async def probe_subagent_run(c: httpx.AsyncClient, name: str, timeout: float) -> R:
    """Drive a real task through the newly created subagent."""
    r = R(key="subagent_run", title=f"Does '{name}' actually run?")
    t0 = time.monotonic()
    art = f"probe_{uuid.uuid4().hex[:6]}.md"
    tr = await c.post(f"{BASE}/api/threads", json={"metadata": {"purpose": "subagent-run-probe"}})
    tr.raise_for_status()
    tid = tr.json()["thread_id"]
    body = {
        "input": {"messages": [{"role": "user", "content": RUN_TASK.replace("PROBE_NAME", name).replace("PROBE_ARTIFACT", art)}]},
        "metadata": {"purpose": "subagent-run-probe"},
        "stream_mode": ["messages-tuple", "values", "custom"],
        "on_disconnect": "continue",
    }
    urls = f"{BASE}/api/threads/{tid}"
    tools: set[str] = set()
    deadline = time.monotonic() + timeout

    async def timed(src: Any) -> Any:
        async for line in src:
            if time.monotonic() > deadline:
                r.blocked = f"stream exceeded {timeout:.0f}s"
                return
            yield line

    async with c.stream("POST", f"{urls}/runs/stream", json=body, timeout=httpx.Timeout(30.0, read=timeout)) as resp:
        if resp.status_code >= 400:
            r.problems.append(f"stream HTTP {resp.status_code}")
            return r
        loc = resp.headers.get("content-location") or resp.headers.get("Content-Location")
        run_id = loc.rstrip("/").split("/")[-1] if loc else ""
        async for _n, d in parse_sse(timed(resp.aiter_lines())):
            try:
                collect_tools(json.loads(d), tools)
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
    r.observed["thread_id"] = tid
    r.observed["run_status"] = status
    r.observed["tools_called"] = sorted(tools)
    r.observed["answer"] = re.sub(r"\s+", " ", answer)[:900]

    host = host_path(tid, f"outputs/{art}")
    size = host.stat().st_size if (host and host.is_file()) else -1
    r.observed["artifact_path"] = f"/mnt/user-data/outputs/{art}"
    r.observed["artifact_bytes"] = size
    r.check("the subagent actually produced a file", size >= 40, f"artifact is {size}B at {host}")

    r.seconds = round(time.monotonic() - t0, 1)
    if size >= 40:
        r.verdict = "PASS"
    else:
        r.blocked = "the subagent was created and listed but produced no artifact. See tools_called and answer for whether a delegation tool exists in this deployment."
        r.verdict = "BLOCKED"
    return r


def safe(s: Any, limit: int = 250) -> str:
    """Render a value for the console without dying on it.

    A model answer can contain any character, and the Windows console codec
    raises `UnicodeEncodeError` on a U+2705 rather than degrading -- which
    aborted the whole probe after stage 1 had already run. Swallowing an
    exception here would hide the text, so the character is replaced and the
    reason is not lost: it is visible as a substitution.
    """
    if not isinstance(s, str):
        s = json.dumps(s, default=str)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    return s[:limit].encode(enc, errors="replace").decode(enc, errors="replace")


def show(title: str, r: R) -> None:
    print("\n" + "=" * 100)
    print(f"{title}  ->  {r.verdict}")
    print("=" * 100)
    print(f"  elapsed : {r.seconds}s")
    for k, v in r.observed.items():
        print(f"  {k:<26}: {safe(v)}")
    print("  checks  :")
    for name, ok, note in r.checks:
        print(f"      [{'PASS' if ok else 'FAIL'}] {safe(name, 120)}" + (f"\n             -> {safe(note, 220)}" if note and not ok else ""))
    if r.blocked:
        print(f"  BLOCKED : {r.blocked}")
    print(f"  problems: {r.problems or 'none'}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=1500.0)
    ap.add_argument("--json", default=None)
    ap.add_argument("--skip-agent-attempt", action="store_true")
    args = ap.parse_args()

    results: list[R] = []
    created: str | None = None
    async with httpx.AsyncClient(timeout=240.0) as c:
        if not args.skip_agent_attempt:
            print("=== 1. can an AGENT create a subagent? ===")
            a = await probe_agent_attempt(c, args.timeout)
            show("AGENT ATTEMPT", a)
            results.append(a)

        print("\n=== 2. admin create + guard negative controls ===")
        b = await probe_admin_create(c)
        show("ADMIN CREATE", b)
        results.append(b)
        created = b.observed.get("created_name") if b.verdict == "PASS" else None

        if created:
            print(f"\n=== 3. does '{created}' actually run? ===")
            try:
                d = await probe_subagent_run(c, created, args.timeout)
            except Exception as exc:  # noqa: BLE001
                d = R(key="subagent_run", title=created)
                d.check("run probe completed", False, f"raised {type(exc).__name__}: {exc}")
                d.verdict = "FAIL"
            show(f"SUBAGENT RUN ({created})", d)
            results.append(d)

    # cleanup, always
    if created:
        async with httpx.AsyncClient(timeout=60.0) as c:
            dr = await c.delete(f"{BASE}/api/subagents/{created}")
            print(f"\ncleanup DELETE /api/subagents/{created} -> HTTP {dr.status_code}")
            listing = await c.get(f"{BASE}/api/subagents")
            left = [s["name"] for s in (listing.json().get("subagents") or []) if s["name"] == created]
            print(f"still present after delete: {left or 'no'}")

    print("\n" + "=" * 100)
    counts = {v: sum(1 for r in results if r.verdict == v) for v in ("PASS", "FAIL", "BLOCKED")}
    print(f"{len(results)} stages: {counts['PASS']} PASS, {counts['BLOCKED']} BLOCKED (cause recorded), {counts['FAIL']} FAIL")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps([r.__dict__ for r in results], indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.json}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
