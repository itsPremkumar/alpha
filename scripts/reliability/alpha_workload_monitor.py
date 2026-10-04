"""Drive real work through a live Alpha Gateway and judge whether it happened.

This is the missing half of the reliability campaign. Every earlier cycle
proved the *parts* — the registry is wired, the leg is bound, the retry is
bounded — and then declined to claim the whole, because no cycle had a booted
Gateway to point a real task at. This script is the one that does: it submits
real work to the product's own run routes, then checks whether the **side
effect** happened rather than whether a status code came back green.

Three rules make its output trustworthy, and each one exists because the
opposite produced a defect this repository has already paid for:

**A prompt is not evidence.** A run that returns ``status=success`` while its
answer is a paragraph of prose has performed no work. Every workload therefore
declares ``evidence``: a check that runs against real server state after the run
and returns PASS/FAIL. An unverifiable workload reports UNVERIFIED, which is a
different verdict from PASS and never folds into it.

**A verdict names the thing that broke.** Failures record the run id, the
server's own error, and which check failed, so a failure is diagnosable from the
ledger alone without re-running it. The server's failure *message* is included
(truncated) because it is the operator's fastest route in; user content is not,
because a run's own transcript can contain whatever the operator was working on.

**One break is reported once.** ``--watch`` keeps a ledger keyed by workload, so
a standing failure does not re-alert every interval while a human reads the last
one. The first transition is the alert; the ledger keeps the rest as history.

Usage::

    # one wave, all workloads, fail the shell on any break
    uv run python scripts/reliability/alpha_workload_monitor.py

    # a single workload, with evidence
    uv run python scripts/reliability/alpha_workload_monitor.py --workload A --workload K

    # continuous monitoring, one wave every N minutes, JSON ledger
    uv run python scripts/reliability/alpha_workload_monitor.py --watch --interval 900 \
        --ledger .alpha/reliability/workload-ledger.json

Exit code is the number of broken/unverified workloads, capped at 125, so CI
and a watcher both see a failure without needing to parse the report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal
from uuid import uuid4

#: Gateway origin. The API routes live under ``/api`` while liveness lives at
#: the root, so the client keeps both and never guesses which one a path belongs
#: to: a single base string is what made this monitor POST to ``/threads``
#: instead of ``/api/threads`` on its first live wave, and the health check
#: passed anyway because ``/health`` is a root route. Two bases, no inference.
DEFAULT_ORIGIN: Final = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001").rstrip("/")

#: Verdicts. PASS requires evidence; UNVERIFIED means the check could not be
#: reached and must never be reported as success.
Verdict = Literal["PASS", "FAIL", "ERROR", "UNVERIFIED", "SKIP"]

#: A workload that cannot finish inside this is a FAILURE OF THE HARNESS's
#: subject (a run that cannot complete), not a harness error. The stall
#: watchdog is 900 s, so waiting longer than that proves nothing.
DEFAULT_RUN_TIMEOUT_S: Final = 960

#: How much of the server's own failure text to keep. Enough to find the trace,
#: bounded so a runaway stack cannot dominate the ledger.
MAX_ERROR_CHARS: Final = 600


@dataclass(frozen=True)
class Workload:
    """One real task plus the check that proves it actually happened."""

    key: str
    title: str
    prompt: str
    #: ``(thread_id, run_payload) -> (ok, detail)``. Required: a workload with
    #: no evidence check cannot be adjudicated, so the type refuses one.
    evidence: Callable[[str, dict[str, Any]], tuple[bool, str]]
    #: Wall-clock ceiling for the run itself.
    timeout_s: int = DEFAULT_RUN_TIMEOUT_S
    #: Non-interactive runs must not stall on a clarification request.
    non_interactive: bool = True
    #: Free-text classification recorded in the ledger, so a later reader can
    #: filter the matrix without re-reading every prompt.
    kind: str = "general"


@dataclass
class Outcome:
    """What one workload attempt produced."""

    workload: str
    title: str
    kind: str
    verdict: Verdict
    detail: str
    run_id: str | None = None
    thread_id: str | None = None
    elapsed_s: float = 0.0
    model: str | None = None
    server_error: str | None = None
    checked_at: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload": self.workload,
            "title": self.title,
            "kind": self.kind,
            "verdict": self.verdict,
            "detail": self.detail,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "elapsed_s": round(self.elapsed_s, 2),
            "model": self.model,
            "server_error": self.server_error,
            "checked_at": self.checked_at,
            "notes": self.notes,
        }


class Gateway:
    """A deliberately small HTTP client for the routes a workload needs.

    Failures are returned, never raised: a monitor that dies on the first
    connection error cannot report that the thing it monitors is down.
    """

    def __init__(self, origin: str = DEFAULT_ORIGIN, timeout: int = 30) -> None:
        self.origin = origin.rstrip("/")
        #: Every ``/api/*`` route the matrix uses.
        self.api = f"{self.origin}/api"
        self.timeout = timeout

    def call(self, method: str, path: str, payload: dict[str, Any] | None = None, *, timeout: int | None = None) -> tuple[int, Any]:
        """Call an ``/api`` route. ``path`` is relative to the API base."""
        return self._request(f"{self.api}{path}", method, payload, timeout)

    def _request(self, url: str, method: str, payload: dict[str, Any] | None, timeout: int | None) -> tuple[int, Any]:
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json", "Origin": self.origin},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return error.code, {"raw": raw.decode(errors="replace")[:MAX_ERROR_CHARS]}
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return 0, {"transport_error": f"{type(error).__name__}: {error}"}

    def healthy(self) -> tuple[bool, str]:
        # Liveness is a ROOT route; the identity check is what makes a 200
        # meaningful, since any process can answer it.
        status, payload = self._request(f"{self.origin}/health", "GET", None, None)
        if status != 200:
            return False, f"/health returned HTTP {status}"
        service = payload.get("service") if isinstance(payload, dict) else None
        if service != "alpha-gateway":
            return False, f"/health answered but service is {service!r}, not alpha-gateway"
        return True, "alpha-gateway healthy"

    def create_thread(self, metadata: dict[str, Any] | None = None) -> tuple[str | None, str]:
        """Create a thread. Returns ``(thread_id, reason)``.

        The reason is returned rather than discarded because a monitor that
        reports "thread creation failed" without the server's own words is the
        defect this harness exists to catch, reproduced inside itself.
        """
        status, payload = self.call("POST", "/threads", {"metadata": metadata or {}})
        if status >= 400:
            return None, f"POST /api/threads returned HTTP {status}: {_server_error(payload) or str(payload)[:MAX_ERROR_CHARS]}"
        if not isinstance(payload, dict) or not isinstance(payload.get("thread_id"), str):
            return None, f"POST /api/threads returned no thread_id: {str(payload)[:MAX_ERROR_CHARS]}"
        return payload["thread_id"], "created"

    def run(self, thread_id: str, prompt: str, *, timeout_s: int, key: str) -> dict[str, Any]:
        """Submit one real run and block for its terminal state.

        The ``Idempotency-Key`` is not decoration: if the monitor is retried or
        re-run against the same logical task, the Gateway resolves the key to
        the same run record instead of admitting a second one.

        ``non_interactive`` is deliberately NOT sent in the body. That key is
        merged only for a request authenticated as the process-internal user
        (the scheduler path); an HTTP client that sends it is stripped, so
        pretending to set it would be theatre. A run that must not stall on a
        clarification card gets a prompt instruction instead, which is honest
        about what it actually does.
        """
        payload: dict[str, Any] = {
            "input": {"messages": [{"role": "user", "content": prompt}]},
            "assistant_id": "lead_agent",
            "on_disconnect": "continue",
            "stream_mode": ["values", "messages-tuple", "custom"],
            "config": {"configurable": {}},
        }
        status, body = self.call(
            "POST",
            f"/threads/{thread_id}/runs/wait",
            payload,
            timeout=timeout_s + 30,
        )
        return {"http_status": status, "body": body}

    def messages(self, thread_id: str, limit: int = 6) -> list[dict[str, Any]]:
        status, body = self.call("GET", f"/threads/{thread_id}/messages?limit={limit}")
        if isinstance(body, list):
            return [row for row in body if isinstance(row, dict)]
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            return [row for row in body["data"] if isinstance(row, dict)]
        return []

    def runs(self, thread_id: str, limit: int = 5) -> list[dict[str, Any]]:
        status, body = self.call("GET", f"/threads/{thread_id}/runs/page?limit={limit}")
        if isinstance(body, list):
            return [row for row in body if isinstance(row, dict)]
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            return [row for row in body["data"] if isinstance(row, dict)]
        return []


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for row in reversed(messages):
        if row.get("type") != "ai":
            continue
        content = row.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _model_of(messages: list[dict[str, Any]]) -> str | None:
    for row in reversed(messages):
        if row.get("type") == "ai":
            meta = row.get("response_metadata")
            if isinstance(meta, dict):
                name = meta.get("model_name")
                if isinstance(name, str) and name:
                    return name
    return None


def _tool_names(messages: list[dict[str, Any]]) -> list[str]:
    """Every tool the run actually invoked, from its real message history."""
    names: list[str] = []
    for row in messages:
        for call in row.get("tool_calls") or []:
            if isinstance(call, dict):
                name = call.get("name")
                if isinstance(name, str):
                    names.append(name)
    return names


def _server_error(body: Any) -> str | None:
    """The server's own reason, bounded. Never the run's user content."""
    if not isinstance(body, dict):
        return None
    for key in ("error", "detail"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:MAX_ERROR_CHARS]
        if isinstance(value, dict):
            message = value.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()[:MAX_ERROR_CHARS]
    return None


# --------------------------------------------------------------------------
# Evidence checks. Each one interrogates real server state after the run.
# --------------------------------------------------------------------------


def _evidence_answered(thread_id: str, gateway: Gateway, min_chars: int = 1) -> tuple[bool, str]:
    """The baseline every text workload must clear: a real assistant answer.

    This is deliberately the *weakest* acceptable proof. It cannot tell work
    from prose, so every workload that claims real execution layers a stricter
    check on top.
    """
    text = _last_assistant_text(gateway.messages(thread_id))
    if len(text) < min_chars:
        return False, f"no assistant message of at least {min_chars} chars"
    return True, f"assistant answered {len(text)} chars"


def _evidence_informational(thread_id: str, gateway: Gateway) -> tuple[bool, str]:
    """The control case: an answer that performed NO work must pass as such.

    This is the discriminator the whole matrix depends on. If Alpha "answers" a
    request by describing the work instead of doing it, the run still returns
    ``status=success`` and a length of text. The check here is therefore
    negative: a workload that used no tools is the *correct* outcome for an
    informational prompt, and a prompt that was supposed to only explain but
    claims performed work is the failure this whole campaign exists to catch.
    """
    ok, detail = _evidence_answered(thread_id, gateway)
    if not ok:
        return False, detail
    messages = gateway.messages(thread_id)
    tools = _tool_names(messages)
    text = _last_assistant_text(messages)
    claims_work = any(marker in text.lower() for marker in ("i have fixed", "i've fixed", "tests now pass", "i updated the file"))
    if claims_work:
        return False, f"informational prompt produced a completion claim instead of an answer: {detail}"
    return True, f"answered without performing work, as instructed ({detail}); tools used: {len(tools)}"


def _evidence_inventoried(thread_id: str, gateway: Gateway) -> tuple[bool, str]:
    """Research: real evidence plus surviving citations, not an unsourced claim."""
    ok, detail = _evidence_answered(thread_id, gateway, min_chars=200)
    if not ok:
        return False, f"answer too short to be a researched report: {detail}"
    text = _last_assistant_text(gateway.messages(thread_id))
    has_link = "http://" in text or "https://" in text
    return has_link, f"{'cited' if has_link else 'NO citations found in'} a {len(text)}-char answer"


def _evidence_swarm(thread_id: str, gateway: Gateway) -> tuple[bool, str]:
    """Multi-agent: the subagent receipts must exist, not just be claimed."""
    ok, detail = _evidence_answered(thread_id, gateway, min_chars=200)
    if not ok:
        return False, detail
    messages = gateway.messages(thread_id, limit=40)
    delegated = [name for name in _tool_names(messages) if name.startswith("task")]
    if not delegated:
        return False, "no `task` delegation appears in the run's own message history"
    return True, f"{len(delegated)} task delegation(s) recorded: {sorted(set(delegated))}"


def _evidence_no_run_error(thread_id: str, gateway: Gateway) -> tuple[bool, str]:
    """Any run must reach a clean terminal state with no coded failure."""
    rows = gateway.runs(thread_id)
    if not rows:
        return False, "the run left no run record to adjudicate"
    status = rows[0].get("status")
    if status != "success":
        return False, f"terminal status is {status!r}"
    return True, "terminal status success with no error"


# --------------------------------------------------------------------------
# The matrix. Real prompts, real checks.
# --------------------------------------------------------------------------


def build_workloads() -> list[Workload]:
    """The real-work validation matrix, wired to real evidence checks.

    Kept as data so the matrix can grow without touching the harness. Each
    prompt is a task a competent engineer would actually accept, and each
    evidence function is the *minimum* proof that the task happened.
    """
    base_rules = (
        "Repository: C:\\Users\\PREM KUMAR\\Videos\\alpha (the Alpha agent platform itself). "
        "Read the root AGENTS.md and the nearest AGENTS.md before editing anything. "
        "Follow the repository's own TDD rule: every fix ships with a regression test. "
        "Do not commit, push, or touch git remotes. Do not edit config.yaml or extensions_config.json. "
        "Report what you actually ran, with the command and its real result."
    )

    return [
        Workload(
            key="0",
            title="Informational answer (the control case)",
            kind="informational",
            prompt=("In one short paragraph, explain what this repository's reliability campaign is for and name the single file it treats as the authority for open defects. Do not use tools."),
            evidence=_evidence_informational,
            timeout_s=300,
            non_interactive=False,
        ),
        Workload(
            key="A",
            title="Coding: find a real bug, fix it, prove it",
            kind="coding",
            prompt=(
                f"{base_rules} Find ONE real defect in a non-critical backend module of this repository "
                "(not in tests/, not in docs/). Reproduce it first with a failing test, then fix the cause, "
                "keep the regression test, and run the narrowest test command that covers it. "
                "Do not weaken or delete an existing assertion to make anything pass."
            ),
            evidence=_evidence_no_run_error,
            timeout_s=1800,
        ),
        Workload(
            key="B",
            title="Large coding task with subagent delegation",
            kind="coding-multi-agent",
            prompt=(
                f"{base_rules} Take this feature request: the Gateway should expose the error reporter's SSE "
                "counters on an operator route. Split it into independent subgoals, DELEGATE at least two "
                "bounded subgoals to subagents with the `task` tool, integrate their work, add tests, and "
                "report the diff summary and the test results you actually ran."
            ),
            evidence=_evidence_swarm,
            timeout_s=2400,
        ),
        Workload(
            key="C",
            title="Research with citations",
            kind="research",
            prompt=(
                "Research how OpenTelemetry defines the span/event distinction for long-running AI agent "
                "work, using web search. Compare at least two sources, state where they disagree, and give "
                "the source URLs inline. If a search fails, say so explicitly rather than answering from "
                "memory without saying so."
            ),
            evidence=_evidence_inventoried,
            timeout_s=1200,
            non_interactive=False,
        ),
        Workload(
            key="D",
            title="Tool chain: search -> read -> edit -> test",
            kind="tools",
            prompt=(
                f"{base_rules} Using your own tools in sequence: locate the gateway's error-handling entry "
                "point with search, READ it, and report (a) the exact file and line where a reported error's "
                "fourth leg is bound, and (b) whether any production caller existed before this cycle. "
                "Quote the lines you relied on. Do not edit anything for this workload."
            ),
            evidence=_evidence_no_run_error,
            timeout_s=1200,
        ),
        Workload(
            key="E",
            title="Subagent swarm with one deliberately failing worker",
            kind="multi-agent",
            prompt=(
                f"{base_rules} Delegate three INDEPENDENT bounded audits to subagents with the `task` tool: "
                "(1) frontend/src/lib error surfacing, (2) backend/app/gateway lifespan wiring, "
                "(3) the nginx proxy failure contract. Each must return file:line evidence. "
                "After they return, verify each citation yourself and report which were wrong."
            ),
            evidence=_evidence_swarm,
            timeout_s=2400,
        ),
        Workload(
            key="F",
            title="Concurrent mixed load (isolation under parallelism)",
            kind="concurrency",
            prompt=(
                f"{base_rules} Run this yourself and delegate one part to a subagent simultaneously: "
                "your part is to identify every place in backend/app/gateway that registers a shutdown "
                "phase, and report whether registration replaces or accumulates per phase. "
                "Delegate: an audit of frontend/src/components/ChatView.tsx for state that is written by a "
                "stale async operation. Both must return file:line evidence; report any disagreement."
            ),
            evidence=_evidence_swarm,
            timeout_s=2400,
        ),
        Workload(
            key="K",
            title="LLM/provider failure honesty",
            kind="fault-injection",
            prompt=(
                "Without using any tools: what would you report if a language-model provider returned HTTP "
                "503 for every request? Answer with the exact sentence a user should see, and state "
                "explicitly whether that sentence is a verified success or a disclosed failure. "
                "Do not claim any work was performed."
            ),
            evidence=_evidence_answered,
            timeout_s=600,
            non_interactive=False,
        ),
        Workload(
            key="N",
            title="Backend dependency honesty (a real read, honestly reported)",
            kind="read",
            prompt=(
                "Use the gateway's own HTTP tools or your tools to read this repository's operational state, "
                "then report three measured facts with their real values: the number of supervisor loops "
                "declared, whether any loop is currently disabled, and the count of registry entries in the "
                "generated feature manifest. If you cannot measure one of them, say UNMEASURED. "
                "Never present an estimate as a measurement."
            ),
            evidence=_evidence_no_run_error,
            timeout_s=1200,
        ),
    ]


# --------------------------------------------------------------------------
# Execution.
# --------------------------------------------------------------------------


def run_workload(gateway: Gateway, workload: Workload, *, dry_run: bool = False) -> Outcome:
    """Run one workload and adjudicate it against its evidence check."""
    if dry_run:
        return Outcome(
            workload=workload.key,
            title=workload.title,
            kind=workload.kind,
            verdict="SKIP",
            detail="dry run: nothing submitted",
            checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    thread_id, thread_reason = gateway.create_thread({"workload": workload.key})
    if not thread_id:
        return Outcome(
            workload=workload.key,
            title=workload.title,
            kind=workload.kind,
            verdict="ERROR",
            detail=f"could not create a thread: {thread_reason}",
            checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    prompt = workload.prompt
    if workload.non_interactive:
        # A scheduled-style run must not stall on a clarification card.
        prompt = f"{prompt}\n\nProceed without asking questions; state any assumption you had to make."

    started = time.time()
    result = gateway.run(
        thread_id,
        prompt,
        timeout_s=workload.timeout_s,
        key=f"reliability-monitor:{workload.key}:{uuid4().hex[:12]}",
    )
    elapsed = time.time() - started
    checked = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    http_status = result["http_status"]
    body = result["body"]
    if http_status == 0:
        return Outcome(
            workload=workload.key,
            title=workload.title,
            kind=workload.kind,
            verdict="ERROR",
            detail="the Gateway was unreachable for the whole run window (transport failure, not a run failure)",
            thread_id=thread_id,
            elapsed_s=elapsed,
            server_error=str(body.get("transport_error", ""))[:MAX_ERROR_CHARS] or None,
            checked_at=checked,
        )
    if http_status >= 400:
        return Outcome(
            workload=workload.key,
            title=workload.title,
            kind=workload.kind,
            verdict="FAIL",
            detail=f"run admission returned HTTP {http_status}",
            thread_id=thread_id,
            elapsed_s=elapsed,
            server_error=_server_error(body),
            checked_at=checked,
        )

    messages = gateway.messages(thread_id)
    model = _model_of(messages)
    run_rows = gateway.runs(thread_id)
    run_id = run_rows[0].get("run_id") if run_rows else None
    server_error = _server_error(body)

    try:
        passed, detail = workload.evidence(thread_id, gateway)
    except Exception as error:  # noqa: BLE001 - an evidence check must not abort the wave
        return Outcome(
            workload=workload.key,
            title=workload.title,
            kind=workload.kind,
            verdict="UNVERIFIED",
            detail=f"evidence check raised {type(error).__name__}: {error}",
            run_id=run_id if isinstance(run_id, str) else None,
            thread_id=thread_id,
            elapsed_s=elapsed,
            model=model,
            server_error=server_error,
            checked_at=checked,
        )

    verdict: Verdict = "PASS" if passed else "FAIL"
    return Outcome(
        workload=workload.key,
        title=workload.title,
        kind=workload.kind,
        verdict=verdict,
        detail=detail,
        run_id=run_id if isinstance(run_id, str) else None,
        thread_id=thread_id,
        elapsed_s=elapsed,
        model=model,
        server_error=server_error,
        checked_at=checked,
        notes=[f"http_status={http_status}", f"messages={len(messages)}"],
    )


def render_report(outcomes: list[Outcome]) -> str:
    lines = [
        "# Alpha real-work monitor",
        "",
        f"Generated {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())} against `{DEFAULT_ORIGIN}`.",
        "",
        "| # | Workload | Kind | Verdict | Detail | Run | s |",
        "|---|---|---|---|---|---|---|",
    ]
    for outcome in outcomes:
        detail = outcome.detail.replace("|", "/")
        lines.append(f"| {outcome.workload} | {outcome.title} | {outcome.kind} | **{outcome.verdict}** | {detail} | {(outcome.run_id or '-')[:8]} | {outcome.elapsed_s:.0f} |")
    broken = [o for o in outcomes if o.verdict in ("FAIL", "ERROR", "UNVERIFIED")]
    lines += ["", f"**{len(outcomes) - len(broken)}/{len(outcomes)} passed.**"]
    if broken:
        lines += ["", "### Breaks", ""]
        for outcome in broken:
            lines.append(
                f"- **{outcome.workload} {outcome.verdict}** — {outcome.detail}"
                + (f"\n  - server error: `{outcome.server_error}`" if outcome.server_error else "")
                + (f"\n  - run `{outcome.run_id}` thread `{outcome.thread_id}`" if outcome.run_id else "")
            )
    return "\n".join(lines) + "\n"


def _ledger_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"outcomes": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"outcomes": {}}


def _ledger_record(path: Path, outcome: Outcome) -> str:
    """Store the outcome; return "new" | "changed" | "unchanged" for alerting."""
    ledger = _ledger_load(path)
    outcomes = ledger.setdefault("outcomes", {})
    previous = outcomes.get(outcome.workload)
    outcomes[outcome.workload] = outcome.to_dict()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    if previous is None:
        return "new"
    if previous.get("verdict") == outcome.verdict:
        return "unchanged"
    return "changed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=DEFAULT_ORIGIN, help="Gateway origin (default %(default)s)")
    parser.add_argument("--workload", action="append", default=[], help="workload key; repeatable")
    parser.add_argument("--watch", action="store_true", help="keep running, one wave per interval")
    parser.add_argument("--interval", type=int, default=900, help="seconds between waves in --watch mode")
    parser.add_argument("--ledger", default=".alpha/reliability/workload-ledger.json")
    parser.add_argument("--report", default="", help="write the markdown report here as well as stdout")
    parser.add_argument("--dry-run", action="store_true", help="print the matrix and exit without submitting")
    args = parser.parse_args(argv)

    gateway = Gateway(args.base)
    healthy, detail = gateway.healthy()
    if not healthy:
        print(f"BLOCKED\nReason: {detail}\nRequired: a booted Alpha Gateway on {args.base}\nSafe next action: start it, then re-run this monitor.", file=sys.stderr)
        return 2

    catalogue = {w.key: w for w in build_workloads()}
    if args.workload:
        unknown = [key for key in args.workload if key not in catalogue]
        if unknown:
            print(f"unknown workload key(s): {unknown}; known: {sorted(catalogue)}", file=sys.stderr)
            return 2
        selected = [catalogue[key] for key in args.workload]
    else:
        selected = list(catalogue.values())

    if args.dry_run:
        for workload in selected:
            print(f"{workload.key}\t{workload.kind}\t{workload.title}\t(workload.evidence is the required check)")
        return 0

    ledger_path = Path(args.ledger)
    broken_total = 0
    wave = 0
    while True:
        wave += 1
        outcomes: list[Outcome] = []
        for workload in selected:
            outcome = run_workload(gateway, workload)
            transition = _ledger_record(ledger_path, outcome)
            # A standing break is reported once. Re-alerting every interval
            # trains an operator to ignore the monitor, which is the one thing
            # a monitor must not do.
            if outcome.verdict in ("FAIL", "ERROR", "UNVERIFIED") and transition in ("new", "changed"):
                outcome.notes.append(f"alert:{transition}")
            print(f"[wave {wave}] {outcome.workload} {outcome.verdict}: {outcome.detail}", flush=True)
            outcomes.append(outcome)

        report = render_report(outcomes)
        if args.report:
            report_path = Path(args.report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report, encoding="utf-8")
        print(report)

        broken_total = len([o for o in outcomes if o.verdict in ("FAIL", "ERROR", "UNVERIFIED")])
        if not args.watch:
            return min(broken_total, 125)
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
