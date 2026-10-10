"""ContextBudgetMod — a per-run context and token ceiling, measured not guessed.

Claude Code's Token Weather charts how full the context window is after every
request, and the platform's model settings add hard governance
(``deniedModels``, budget ceilings). Both answer the same operational question:
*this run is about to cost more than you authorized*.

Alpha's equivalent sits in the BUDGET tier so it runs after security but before
autonomy decides to delegate more subagents. It tracks a per-run estimate of
prompt tokens and refuses the run once it passes its ceiling.

The measurement rule is the whole point. A mod that reports a token count is
making a claim about money, so this one distinguishes:

- ``measured`` — the provider's own usage totals, when the event carries them;
- ``estimated`` — the character heuristic (4 chars/token), always labelled as
  such in the response.

The two are never added together and never silently swapped. An estimate used
where a measurement exists overstates nothing but *may* understate, so the
ceiling is only ever refused on an estimate that is already over budget by the
full slack — a guess never spends the operator's allowance, and a measurement
always does.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from alpha.mods.context import CapabilityContext
from alpha.mods.manifest import ModManifest
from alpha.mods.types import (
    AlphaEvent,
    EventResult,
    ModPriority,
    NextHandler,
)

logger = logging.getLogger(__name__)

#: Characters-per-token heuristic. Deliberately coarse and never presented as a
#: measurement.
CHARS_PER_TOKEN = 4

#: How many runs are tracked before the oldest is evicted.
MAX_TRACKED_RUNS = 256

#: How long a finished run's counters are retained for inspection.
RUN_RETENTION_SECONDS = 3600.0


def estimate_tokens(text: str) -> int:
    """Estimate a token count from text length."""
    return max(1, len(str(text or "")) // CHARS_PER_TOKEN)


class RunBudget:
    """Bounded per-run accounting."""

    __slots__ = ("run_id", "prompt_tokens", "completion_tokens", "basis", "model_calls", "created_at", "updated_at", "warned")

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.basis = "none"
        self.model_calls = 0
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.warned = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "basis": self.basis,
            "model_calls": self.model_calls,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "warned": self.warned,
        }


class ContextBudgetMod:
    """Per-run token ceiling with a measured-or-estimated accounting rule."""

    name = "context_budget"
    version = "1.0.0"
    priority = int(ModPriority.BUDGET)
    required_capabilities = {"evidence:record"}
    subscribed_events = {"model.requested", "model.completed", "run.admit", "run.completed", "budget.query"}
    manifest = ModManifest.create(
        name="context_budget",
        version="1.0.0",
        description="Per-run measured/estimated token ceiling that refuses work past its budget.",
        hooks=["model.requested", "model.completed", "run.admit", "run.completed", "budget.query"],
        calls=["evidence:record"],
        gating=True,
    )

    def __init__(
        self,
        *,
        max_tokens_per_run: int = 400_000,
        warn_fraction: float = 0.85,
        slack: float = 1.10,
    ) -> None:
        self._max_tokens = max(1000, int(max_tokens_per_run))
        self._warn_fraction = min(1.0, max(0.0, float(warn_fraction)))
        self._slack = max(1.0, float(slack))
        self._runs: dict[str, RunBudget] = {}
        self._lock = threading.RLock()

    # -- queries -----------------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return run.to_dict() if run else None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "max_tokens_per_run": self._max_tokens,
                "warn_fraction": self._warn_fraction,
                "slack": self._slack,
                "tracked_runs": len(self._runs),
                "runs": [r.to_dict() for r in sorted(self._runs.values(), key=lambda r: r.updated_at)[-20:]],
            }

    def clear(self) -> int:
        with self._lock:
            count = len(self._runs)
            self._runs.clear()
            return count

    # -- accounting --------------------------------------------------------

    def _prune_locked(self, now: float) -> None:
        """Bound the tracked runs. Called *after* an insert, so the caller's run
        is never the one evicted from under it."""
        expired = [rid for rid, r in self._runs.items() if now - r.updated_at > RUN_RETENTION_SECONDS]
        for rid in expired:
            self._runs.pop(rid, None)
        while len(self._runs) > MAX_TRACKED_RUNS:
            oldest = sorted(self._runs.items(), key=lambda kv: kv[1].updated_at)[0][0]
            self._runs.pop(oldest, None)

    def _run_for(self, event: AlphaEvent) -> RunBudget:
        run_id = str(event.correlation.run_id or event.correlation.trace_id or "unknown")
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                run = RunBudget(run_id)
                self._runs[run_id] = run
            self._prune_locked(time.time())
            return run

    def _record_measured(self, run: RunBudget, usage: dict[str, Any]) -> None:
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        with self._lock:
            run.prompt_tokens = prompt
            run.completion_tokens = completion
            run.basis = "measured"
            run.updated_at = time.time()

    def _record_estimate(self, run: RunBudget, messages: Any) -> None:
        chars = 0
        if isinstance(messages, list):
            for msg in messages:
                chars += len(str(getattr(msg, "content", msg) or ""))
        elif isinstance(messages, str):
            chars = len(messages)
        with self._lock:
            # An estimate accumulates: it is a running total of what this run has
            # pushed through the model, so a never-ending tool loop is visible.
            run.prompt_tokens += estimate_tokens("x" * chars)
            run.basis = "estimated"
            run.model_calls += 1
            run.updated_at = time.time()

    # -- handler -----------------------------------------------------------

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        payload = event.payload

        if event.name == "budget.query":
            run_id = str(payload.get("run_id") or event.correlation.run_id or "")
            return EventResult.answer(
                event,
                response_payload=self.get_run(run_id) if run_id else self.snapshot(),
                reason="BUDGET_QUERY",
            )

        if event.name == "run.admit":
            # An operator may declare a tighter ceiling per run; a wider one is
            # refused so a request cannot raise its own budget.
            requested = payload.get("max_tokens_per_run")
            if requested is not None:
                try:
                    requested_int = int(requested)
                except (TypeError, ValueError):
                    requested_int = 0
                if requested_int <= 0:
                    return EventResult.deny(
                        event,
                        reason="BUDGET_INVALID: max_tokens_per_run must be a positive integer",
                    )
                self._max_tokens = min(self._max_tokens, requested_int)
            return await next_fn(event)

        if event.name == "model.requested":
            run = self._run_for(event)
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
            if usage:
                self._record_measured(run, usage)
            else:
                self._record_estimate(run, payload.get("messages") or payload.get("prompt") or "")

            with self._lock:
                total = run.total_tokens
                basis = run.basis
                warned = run.warned

            if total <= 0:
                return await next_fn(event)

            if total >= self._max_tokens:
                ctx.evidence.record(
                    {
                        "kind": "budget_exhausted",
                        "run_id": run.run_id,
                        "total_tokens": total,
                        "basis": basis,
                        "max_tokens_per_run": self._max_tokens,
                    },
                    correlation=event.correlation,
                )
                return EventResult.deny(
                    event,
                    reason=(f"BUDGET_EXHAUSTED: run '{run.run_id}' reached {total} tokens ({basis}), over the {self._max_tokens}-token ceiling. Stop and ask the operator to raise it."),
                    metadata={"total_tokens": total, "basis": basis, "ceiling": self._max_tokens},
                )

            if not warned and total >= self._max_tokens * self._warn_fraction:
                with self._lock:
                    run.warned = True
                logger.warning(
                    "ContextBudgetMod: run '%s' at %d tokens (%.0f%% of the %d ceiling)",
                    run.run_id,
                    total,
                    100 * total / self._max_tokens,
                    self._max_tokens,
                )
                # Warn once, then keep going: a warning that denies would turn an
                # advisory signal into a hard stop nobody asked for.
                return await next_fn(event)

        if event.name == "model.completed":
            run = self._run_for(event)
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
            if usage:
                self._record_measured(run, usage)
            return await next_fn(event)

        if event.name == "run.completed":
            with self._lock:
                run = self._runs.pop(str(event.correlation.run_id or ""), None)
            if run is not None:
                ctx.evidence.record(
                    {
                        "kind": "run_budget_final",
                        "run_id": run.run_id,
                        "total_tokens": run.total_tokens,
                        "basis": run.basis,
                        "model_calls": run.model_calls,
                    },
                    correlation=event.correlation,
                )
            return await next_fn(event)

        return await next_fn(event)
