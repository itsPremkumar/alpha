"""The runner seam: how the arena drives sub-agents.

The engine never imports the sub-agent executor directly.
It speaks to an :class:`ArenaAgentRunner` - an async
callable that takes one fully-rendered job and returns one
measured result - which is what makes the whole tournament
testable with zero model calls, and what keeps the harness
import graph light (the executor module is imported lazily
by the production runner, never at arena import time).

The production runner wraps ``SubagentExecutor``. Two
consequences are load-bearing:

* **The strategy card rides the task text, never a system
  prompt.** Operator- and engine-supplied text is only ever
  downgraded to the untrusted channel, so a card cannot
  masquerade as an instruction the executor itself issued.
* **One job is one delegation.** The arena's budget counts
  exactly what the runner reports back - a job that
  produced no output still cost its call, and its tokens
  are whatever the executor measured, never a guess.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: A sub-agent reply that produced no text. First-class
#: sentinel: twice-failed jobs are reported as this, never
#: silently retried forever.
NO_OUTPUT = "NO OUTPUT"


@dataclass(frozen=True)
class ArenaJob:
    """One sub-agent dispatch, fully rendered.

    ``prompt`` is the completed template; ``reply_hint`` is
    the path the sub-agent was told to write its answer to
    (the arena reads the file back, because a sub-agent's
    final message is not guaranteed to survive long runs -
    the file is).
    """

    kind: str
    run_id: str
    agent_id: str
    prompt: str
    reply_hint: str
    attempt: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "reply_hint": self.reply_hint,
            "attempt": self.attempt,
        }


@dataclass(frozen=True)
class ArenaJobResult:
    """The measured outcome of one job.

    ``ok`` means the job produced usable output. ``tokens``
    and ``calls`` are what the runner actually spent - the
    arena's budget is charged from these, so a lying runner
    would understate cost, which is why the production
    runner reads them off the executor's own records.
    """

    ok: bool
    text: str
    error: str | None = None
    tokens: int = 0
    calls: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "tokens": self.tokens,
            "calls": self.calls,
        }


class ArenaAgentRunner(Protocol):
    """What the executor needs from a sub-agent backend."""

    async def run_job(self, job: ArenaJob) -> ArenaJobResult:
        """Dispatch one job and return its measured result."""
        ...


#: A scripted runner: maps ``(kind, agent_id)`` to a result
#: or a result factory. Tests use it to play out a whole
#: tournament deterministically.
ScriptedResult = Callable[[ArenaJob], ArenaJobResult | Awaitable[ArenaJobResult]]


class ScriptedArenaRunner:
    """A runner that answers from a script, not a model.

    Used by the test suite to drive full tournaments -
    spawn, attack, defend, judge - with no sub-agents and
    no I/O. A job the script does not cover fails with
    ``NO OUTPUT``, which is also how a test exercises the
    failure policy.
    """

    def __init__(self, script: Callable[[ArenaJob], Any] | None = None) -> None:
        self._script = script
        self.dispatched: list[ArenaJob] = []

    async def run_job(self, job: ArenaJob) -> ArenaJobResult:
        self.dispatched.append(job)
        if self._script is None:
            return ArenaJobResult(ok=False, text="", error=NO_OUTPUT)
        outcome = self._script(job)
        if asyncio.iscoroutine(outcome):
            outcome = await outcome
        # For spawn and defend jobs, create the expected file so the
        # executor's file-existence check passes. The script's return
        # value is used as the file content.
        if job.kind in ("spawn", "defend") and job.reply_hint:
            try:
                reply_path = Path(job.reply_hint)
                reply_path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(outcome, ArenaJobResult):
                    content = outcome.text
                elif isinstance(outcome, str):
                    content = outcome
                else:
                    content = ""
                reply_path.write_text(content or "solution content", encoding="utf-8")
            except OSError:
                pass  # Best effort; executor will handle missing file
        if isinstance(outcome, ArenaJobResult):
            return outcome
        if isinstance(outcome, str):
            return ArenaJobResult(ok=True, text=outcome)
        return ArenaJobResult(ok=False, text="", error=NO_OUTPUT)


class SubagentArenaRunner:
    """The production runner: one job, one ``SubagentExecutor``.

    ``executor_factory`` receives a ``SubagentConfig`` and
    returns an executor bound to the caller's toolset,
    sandbox, thread and identity. It is supplied by the
    service, because those bindings belong to the request
    that started the run - not to the arena package, which
    must stay import-light and must never import ``app.*``.

    The executor runs synchronously on a worker thread
    (``asyncio.to_thread``), so a long delegation never
    parks the Gateway event loop.
    """

    def __init__(
        self,
        executor_factory: Callable[[Any], Any],
        *,
        max_turns: int = 150,
        timeout_seconds: int = 1800,
    ) -> None:
        self._factory = executor_factory
        self._max_turns = max_turns
        self._timeout_seconds = timeout_seconds

    async def run_job(self, job: ArenaJob) -> ArenaJobResult:
        from alpha.subagents.config import SubagentConfig

        config = SubagentConfig(
            name=f"arena-{job.run_id}",
            description=f"Arena {job.kind} worker ({job.agent_id})",
            # The card and the task are in the prompt: a
            # system prompt would elevate them above the
            # untrusted channel they belong to.
            system_prompt=None,
            model="inherit",
            max_turns=self._max_turns,
            timeout_seconds=self._timeout_seconds,
        )
        executor = self._factory(config)
        try:
            result = await asyncio.to_thread(executor.execute, job.prompt)
        except Exception as error:  # a dead sub-agent is data, not a crash
            return ArenaJobResult(ok=False, text="", error=f"{type(error).__name__}: {error}")
        text = str(result.result or "")
        tokens = _sum_tokens(getattr(result, "token_usage_records", None))
        ok = bool(text.strip())
        return ArenaJobResult(
            ok=ok,
            text=text,
            error=None if ok else NO_OUTPUT,
            tokens=tokens,
            calls=1,
        )


def _sum_tokens(records: Any) -> int:
    """Defensively sum the token usage an executor reported.

    The record shape is the executor's to evolve, so this
    reads the fields it can find and reports 0 when it finds
    none - a missing measurement is disclosed as 0 spend,
    never fabricated.
    """
    total = 0
    if not records:
        return 0
    for record in records:
        if isinstance(record, dict):
            for key in ("total_tokens", "tokens", "token_count"):
                value = record.get(key)
                if isinstance(value, (int, float)):
                    total += int(value)
                    break
        else:
            for key in ("total_tokens", "tokens", "token_count"):
                value = getattr(record, key, None)
                if isinstance(value, (int, float)):
                    total += int(value)
                    break
    return total


__all__ = [
    "ArenaAgentRunner",
    "ArenaJob",
    "ArenaJobResult",
    "NO_OUTPUT",
    "ScriptedArenaRunner",
    "ScriptedResult",
    "SubagentArenaRunner",
]
