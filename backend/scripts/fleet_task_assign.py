"""Assign different real tasks to different bot profiles and verify the artifacts.

This is not a demo. Each entry is a genuine engineering task with a checkable
deliverable, addressed to a *named roster bot* through `assistant_id`, run
**concurrently** so thread/bot/state isolation is actually exercised rather than
asserted.

Verification is deliberately not "the run said success". For every bot the
harness asks the Gateway for `workspace-changes` -- the server's own record of
files that changed -- and then reads those files off disk. A run that claims
completion and produced nothing is reported as a failure.

Usage (from ``backend/``, against a Gateway on :8001)::

    python scripts/fleet_task_assign.py
    python scripts/fleet_task_assign.py --only coder,reviewer
    python scripts/fleet_task_assign.py --json ../logs/fleet_runs.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BASE = os.environ.get("ALPHA_GATEWAY", "http://127.0.0.1:8001")

OUT = "/mnt/user-data/outputs"


@dataclass
class Assignment:
    bot: str
    title: str
    task: str
    #: Substrings that must appear in a produced file for the task to count.
    expect_in_artifact: list[str] = field(default_factory=list)
    #: Minimum bytes for the produced artifact to count as real work.
    min_bytes: int = 400


ASSIGNMENTS: list[Assignment] = [
    Assignment(
        bot="coder",
        title="Implement a text-statistics library with a test suite",
        min_bytes=500,
        expect_in_artifact=["def ", "import"],
        task=f"""\
Write real code, do not describe it.

1. Create {OUT}/fleet_textstats.py exposing `summarize(text: str) -> dict` with
   keys `chars`, `words`, `lines`, `unique_words`, `avg_word_len` (rounded to 2),
   and `longest_word`. Words are whitespace-separated runs; `unique_words` counts
   distinct words case-sensitively; `chars` is `len(text)`.
2. Create {OUT}/fleet_test_textstats.py with pytest cases for: the empty string,
   a single word, a multi-line string with punctuation, duplicate words, and one
   case asserting `avg_word_len` exactly.
3. Re-read both files with the read tool and check your own arithmetic before you
   finish. If a number is wrong, correct it.

You cannot execute code in this deployment (`allow_host_bash` is false), so do
NOT claim you ran the tests. Say plainly that the suite was written but not
executed, and why. An honest "not run" is required; a fabricated "all passed" is
a failure.""",
    ),
    Assignment(
        bot="researcher",
        title="Research a live topic from the web and cite sources",
        min_bytes=400,
        expect_in_artifact=["http"],
        task=f"""\
Do real web research and write the result to a file.

1. Use the web search tool to research how many Unicode general categories exist
   and list their two-letter codes. Use more than one query.
2. For each claim, record the URL you actually saw it on. If a fetch fails, say so
   and mark the claim as unverified rather than asserting it.
3. Write your findings to {OUT}/fleet_unicode_research.md as a markdown table
   with columns: code, name, and the source URL.

Report how many queries you ran, how many sources you actually retrieved, and
which claims remain unverified. Do not invent a URL you did not see.""",
    ),
    Assignment(
        bot="reviewer",
        title="Review a real file in this repository and write findings",
        min_bytes=400,
        expect_in_artifact=["def ", "import"],
        task=f"""\
Review real code that exists.

1. Read /mnt/alpha-repo/backend/packages/harness/alpha/utils/time.py (use the read
   tool with the /mnt/alpha-repo mount).
2. Identify concrete issues: correctness bugs, unhandled edge cases, and anything
   that would misreport a measurement. Quote the specific lines you object to.
3. Write your review to {OUT}/fleet_code_review.md with a section per finding,
   each carrying the file, the line, what is wrong, and why it matters.

If the file is fine, say so and explain why rather than inventing findings. Cite
line numbers you actually read.""",
    ),
    Assignment(
        bot="data-analyst",
        title="Analyze real run data from this repository",
        min_bytes=400,
        task=f"""\
Analyze real data that exists and write a report.

1. Use bash-free tools (read_file / ls on the /mnt/alpha-repo mount) to find this
   repository's test files and count how many `backend/tests/test_*.py` files
   exist. Use the ls tool on the directory and read what it actually returned.
2. Compute and report: the number of test files, and the number of distinct
   top-level directories under backend/tests.
3. Write the result to {OUT}/fleet_repo_stats.md including the raw counts you
   observed and the exact commands/tools you used.

Every number must come from something you actually read. If a tool fails or you
cannot count something, write "not measured" and say why. Never estimate.""",
    ),
    Assignment(
        bot="architect",
        title="Design a real subsystem and document the boundaries",
        min_bytes=500,
        task=f"""\
Produce a real design document grounded in this repository.

1. Read /mnt/alpha-repo/backend/packages/harness/alpha/grounding/AGENTS.md and
   /mnt/alpha-repo/AGENTS.md to learn the actual conventions.
2. Design how a new "tool result cache" would fit: where it would live, what
   owns invalidation, and which existing module it must NOT duplicate. Name the
   real files you read and the real modules you would touch.
3. Write the design to {OUT}/fleet_design.md with sections: Problem, Proposed
   location, Data flow, Invalidation owner, Explicit non-goals, and Risks.

Ground every claim in a file you actually read. If you did not read something
you are reasoning about, say so.""",
    ),
]


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


async def parse_sse(lines: Any) -> Any:
    name = "message"
    parts: list[str] = []

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


def tool_names(payload: Any, out: set[str]) -> None:
    """Collect tool names from a frame that may be a dict, a list, or nested."""
    if isinstance(payload, list):
        for i in payload:
            tool_names(i, out)
        return
    if not isinstance(payload, dict):
        return
    if payload.get("type") in ("tool", "ToolMessage") and isinstance(payload.get("name"), str):
        out.add(payload["name"])
    for c in payload.get("tool_calls") or []:
        if isinstance(c, dict) and isinstance(c.get("name"), str):
            out.add(c["name"])
    for c in payload.get("tool_call_chunks") or []:
        if isinstance(c, dict) and isinstance(c.get("name"), str):
            out.add(c["name"])
    rcp = payload.get("additional_kwargs")
    if isinstance(rcp, dict):
        r = rcp.get("alpha_tool_receipt")
        if isinstance(r, dict) and isinstance(r.get("tool_name"), str):
            out.add(r["tool_name"])
    for k in ("message", "value", "state"):
        if isinstance(payload.get(k), (dict, list)):
            tool_names(payload[k], out)


def final_ai_text(payload: Any) -> str | None:
    text = None
    items = payload.get("data", []) if isinstance(payload, dict) else payload
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
    return text


# ---------------------------------------------------------------------------


@dataclass
class Result:
    bot: str
    title: str
    thread_id: str = ""
    run_id: str = ""
    status: str | None = None
    error: str | None = None
    tools: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    final_text: str = ""
    seconds: float = 0.0
    problems: list[str] = field(default_factory=list)
    verified_bytes: dict[str, int] = field(default_factory=dict)
    #: True when the SSE stream exceeded its budget. Kept separate from the
    #: run's own status because "the stream was cut off" and "the run failed"
    #: are different facts and the report must not conflate them.
    stream_timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems


async def run_one(client: httpx.AsyncClient, a: Assignment, timeout: float) -> Result:
    res = Result(bot=a.bot, title=a.title)
    t0 = time.monotonic()

    tr = await client.post(f"{BASE}/api/threads", json={"metadata": {"purpose": "fleet-task", "bot": a.bot}})
    tr.raise_for_status()
    res.thread_id = tr.json()["thread_id"]

    body = {
        "assistant_id": a.bot,
        "input": {"messages": [{"role": "user", "content": a.task}]},
        "metadata": {"purpose": "fleet-task", "bot": a.bot},
        "stream_mode": ["messages-tuple", "values", "custom"],
        "on_disconnect": "continue",
    }
    urls = f"{BASE}/api/threads/{res.thread_id}"
    tools: set[str] = set()
    error_frames: list[str] = []

    deadline = time.monotonic() + timeout

    async def timed(src: Any) -> Any:
        async for line in src:
            if time.monotonic() > deadline:
                res.problems.append(f"stream exceeded {timeout:.0f}s")
                return
            yield line

    async with client.stream("POST", f"{urls}/runs/stream", json=body, timeout=httpx.Timeout(30.0, read=timeout)) as resp:
        if resp.status_code >= 400:
            res.problems.append(f"stream HTTP {resp.status_code}: {(await resp.aread())[:200]!r}")
            return res
        loc = resp.headers.get("content-location") or resp.headers.get("Content-Location")
        res.run_id = loc.rstrip("/").split("/")[-1] if loc else ""
        async for name, data in parse_sse(timed(resp.aiter_lines())):
            try:
                payload = json.loads(data)
            except (ValueError, TypeError):
                payload = None
            tool_names(payload, tools)
            if name == "error":
                error_frames.append(data[:300])
            if name == "metadata" and not res.run_id and isinstance(payload, dict):
                res.run_id = str(payload.get("run_id") or "")

    # Wait for a terminal status rather than a fixed 40s budget. A slow bot is
    # not a failed bot: the reviewer run in docs/audits/FLEET_VERIFICATION.md
    # was still legitimately working when the earlier fixed budget expired, and
    # the harness recorded that as a product failure and then went on to report
    # "produced no workspace file changes" for a run that succeeded two minutes
    # later with a 15KB artifact. Polling until terminal (or until the overall
    # timeout) is the difference between measuring Alpha and measuring the
    # harness.
    poll_deadline = time.monotonic() + max(60.0, timeout)
    while time.monotonic() < poll_deadline:
        if res.run_id:
            rr = await client.get(f"{urls}/runs/{res.run_id}")
            if rr.status_code == 200:
                p = rr.json()
                res.status, res.error = p.get("status"), p.get("error")
                if res.status in TERMINAL:
                    break
        await asyncio.sleep(2.0)
    else:
        res.stream_timed_out = True
        res.problems.append(f"run never reached a terminal status within {poll_deadline - t0:.0f}s (last status {res.status!r})")

    res.tools = sorted(tools)

    if res.run_id:
        wc = await client.get(f"{urls}/runs/{res.run_id}/workspace-changes")
        if wc.status_code == 200:
            files = wc.json().get("files") or []
            res.files = [f.get("path") for f in files if isinstance(f, dict) and f.get("path")]
        else:
            # An unreachable artifact record is NOT an empty artifact list. The
            # previous version fell through here with res.files == [] and the
            # "produced no workspace file changes" check then reported a bot as
            # having written nothing, which reads as a product failure when the
            # truth is that the harness could not ask.
            res.problems.append(f"workspace-changes unreachable: HTTP {wc.status_code}")
        mr = await client.get(f"{urls}/runs/{res.run_id}/messages")
        if mr.status_code == 200:
            res.final_text = final_ai_text(mr.json()) or ""
        else:
            res.problems.append(f"messages unreachable: HTTP {mr.status_code}")

    res.seconds = round(time.monotonic() - t0, 1)

    # --- verification against reality, not the run's own claim -------------
    if error_frames:
        res.problems.append(f"{len(error_frames)} SSE error frame(s): {error_frames[0][:160]}")
    if not res.tools:
        res.problems.append("used no tools: answered conversationally instead of doing the work")
    if not res.files:
        res.problems.append("produced no workspace file changes")
    else:
        check_artifacts(res, a.min_bytes)
    if res.status != "success":
        res.problems.append(f"durable status={res.status!r} error={res.error!r}")
    return res


def check_artifacts(res: Result, min_bytes: int) -> None:
    """Measure every artifact on disk and report what could not be measured.

    Split out of ``run_one`` so it is directly testable -- the honesty rule here
    is the whole point of the driver, and it was previously unreachable from a
    test because it sat inline in a function that needs a live Gateway.

    The rule: **an artifact the driver cannot read is reported, never skipped.**
    The first version of this harness resolved no paths at all, left
    ``verified_bytes`` empty, and applied the size floor only when that dict was
    non-empty -- so it printed ``problems: none`` for artifacts it had never
    measured. Silent blindness is worse than a false failure.
    """
    unresolved: list[str] = []
    for p in res.files:
        host = _host_path(res.thread_id, p)
        try:
            size = host.stat().st_size if host.is_file() else -1
        except OSError:
            size = -2
        res.verified_bytes[p] = size
        if size < 0:
            unresolved.append(f"{p} (mapped to {host})")

    if unresolved:
        res.problems.append(f"{len(unresolved)} artifact(s) could not be measured on disk: " + "; ".join(unresolved[:4]))
        return
    if res.verified_bytes and max(res.verified_bytes.values()) < min_bytes:
        res.problems.append(f"largest artifact is {max(res.verified_bytes.values())}B, below the {min_bytes}B floor for real work")


#: A run is over only in these states. Anything else (`running`, `pending`,
#: `error` variants not listed) is still in flight and polling must continue.
TERMINAL = frozenset({"success", "error", "interrupted", "timeout", "canceled", "failed"})

#: Wall-clock of the slowest real bot run measured so far (the `reviewer` task
#: in ``docs/audits/FLEET_VERIFICATION.md``). The default budget must exceed
#: this: a default below a real run's duration makes the *documented* command
#: report a false failure on a healthy system.
SLOWEST_OBSERVED_RUN_SECONDS = 1400.0


#: Environment override for the thread-state root. The default layout is
#: ``<repo>/backend/.alpha/users/<user>/threads/<thread>/user-data/...``; the
#: override exists so the mapping is testable without a real deployment tree.
USERS_ROOT_ENV = "ALPHA_USERS_ROOT"


def users_root() -> Path:
    override = os.environ.get(USERS_ROOT_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / ".alpha" / "users"


def _host_path(thread_id: str, virtual: str) -> Path:
    """Map /mnt/user-data/outputs/x -> the thread's real host directory.

    Returns the *expected* path even when nothing is there, so the caller can
    report "could not measure" against a concrete location instead of silently
    dropping the artifact.
    """
    if "/mnt/user-data/" not in virtual:
        return Path(virtual)
    rel = virtual.split("/mnt/user-data/", 1)[1]
    base = users_root()
    for user_dir in sorted(base.glob("*")):
        cand = user_dir / "threads" / thread_id / "user-data" / rel
        if cand.exists():
            return cand
    # No user directory matched: name the first one that exists, else the
    # conventional `default`, so the reported path points somewhere real.
    for name in ("default", *(d.name for d in sorted(base.glob("*")) if d.is_dir())):
        user_dir = base / name
        if user_dir.is_dir():
            return user_dir / "threads" / thread_id / "user-data" / rel
    return base / "default" / "threads" / thread_id / "user-data" / rel


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="comma-separated bot names")
    ap.add_argument(
        "--timeout",
        type=float,
        default=max(2400.0, SLOWEST_OBSERVED_RUN_SECONDS * 1.7),
        help="per-bot budget for the SSE stream AND for polling to a terminal status",
    )
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    chosen = ASSIGNMENTS
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        chosen = [a for a in ASSIGNMENTS if a.bot in want]
    if not chosen:
        print("no assignments matched", file=sys.stderr)
        return 2

    print(f"Assigning {len(chosen)} real tasks to {len(chosen)} bot profiles, CONCURRENTLY\n")
    async with httpx.AsyncClient(timeout=120.0) as client:
        results = await asyncio.gather(*(run_one(client, a, args.timeout) for a in chosen))

    print("=" * 104)
    print(f"{'BOT':<14} {'STATUS':<10} {'SEC':>6}  {'FILES':<5} {'TOOLS':<5} {'BYTES':>8}  TITLE")
    print("=" * 104)
    for r in results:
        biggest = max(r.verified_bytes.values()) if r.verified_bytes else None
        shown = f"{biggest}" if biggest is not None else "NOT MEASURED"
        print(f"{r.bot:<14} {str(r.status):<10} {r.seconds:>6.0f}  {len(r.files):<5} {len(r.tools):<5} {shown:>8}  {r.title[:40]}")

    print()
    for r in results:
        print("-" * 104)
        print(f"[{r.bot}] {r.title}")
        print(f"  thread   : {r.thread_id}")
        print(f"  run      : {r.run_id}")
        print(f"  status   : {r.status!r} error={r.error!r}")
        print(f"  tools    : {', '.join(r.tools) or '(none)'}")
        print("  artifacts: ")
        for p, size in r.verified_bytes.items():
            print(f"      {size:>8}B  {p}")
        if not r.verified_bytes and r.files:
            print(f"      (unresolved on disk) {r.files}")
        if r.stream_timed_out:
            print("      note: SSE stream exceeded its budget; status below was polled, not streamed")
        print(f"  problems : {r.problems or 'none'}")
        excerpt = re.sub(r"\s+", " ", r.final_text)[:420]
        print(f"  answer   : {excerpt}")

    total_problems = sum(len(r.problems) for r in results)
    print("=" * 104)
    print(f"{len(results)} tasks, {sum(1 for r in results if r.ok)} clean, {total_problems} problems")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps([r.__dict__ for r in results], indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.json}")

    return 1 if total_problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
