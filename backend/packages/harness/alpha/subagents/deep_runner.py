"""The production execution seam for hierarchical delegation.

## Why this module exists

`hierarchical_delegator.py` implements the whole hierarchical-delegation shape —
an isolated ephemeral workspace per child, hard token and time budgets, and a
`DeepHandoffContract` returned instead of raw terminal output. It is complete and
correct. What it deliberately does not do is *execute* anything: real execution
goes through one module-level seam, `set_deep_agent_runner`, and until something
registers a runner the engine reports an honest `UNRECOVERABLE_ERROR` contract.

Nothing in production ever registered one. Only `test_hierarchical_delegator.py`
did. So hierarchical delegation was built, tested, and unreachable — the same
defect class as the `/subagent:spawn` route that "returned a `ready` record and
nothing ever started it", fixed on 2026-10-06.

This module is that missing runner.

## The one rule

**A contract field is filled only from measured data.** Everything the child run
did not actually produce stays empty. `unified_diff`, `security_stamps`,
`invariant_assertions` and `artifacts` are therefore empty for every run today,
and that is the correct output rather than a gap: the executor does not surface a
measured patch, a security check result, an invariant proof or an artifact
manifest on this path, and inventing plausible entries would be exactly the
fabrication `hierarchical_delegator`'s own docstring refuses.

The fields that *are* populated come from the real `SubagentResult`:

| Contract field | Source |
| --- | --- |
| `executive_summary` | the child's real output text |
| `tokens_consumed` | summed `token_usage_records` (0 when none observed) |
| `test_oracles` | harvested bash executions that genuinely ran |
| `status` | the executor's own terminal status, mapped honestly |
| `error_detail` | the executor's real error text |

## Isolation and capacity are inherited, not reimplemented

The child runs through the same `SubagentExecutor` the GRO candidate lanes and
the swarm specialist workers use, so it inherits the process-wide admission
controller, sandbox leases, receipts and usage accounting. That means enabling
hierarchical delegation cannot exceed the operator's subagent ceiling — it takes
real slots from the same pool.

Following the GRO precedent, an empty tool pool is a **refusal**, not a
tool-less child: a lane with no instruments would look worse for having fewer
tools, and here it would also be a child that cannot do the job it was asked for.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.subagents.capacity import PRIORITY_FOREGROUND
from alpha.subagents.deep_handoff_contract import (
    DeepExecutionStatus,
    DeepHandoffContract,
    build_error_contract,
)

logger = logging.getLogger(__name__)

#: Deep agent type -> the shipped specialist worker that runs it.
#:
#: This is a 1:1 map onto the real `deep-*` catalog entries
#: (`deep-architect`, `deep-debugger`, `deep-security`, `deep-test-synthesizer`,
#: `deep-performance`, `deep-code-reviewer`). A type with no worker is refused by
#: name rather than silently downgraded to `general-purpose`: a security audit
#: quietly run by a generalist is a worse outcome than an honest failure.
DEEP_AGENT_TO_SUBAGENT: dict[str, str] = {
    "architect": "deep-architect",
    "debugger": "deep-debugger",
    "security": "deep-security",
    "test_synthesizer": "deep-test-synthesizer",
    "performance": "deep-performance",
    "code_reviewer": "deep-code-reviewer",
}

#: Contract caps the engine enforces; mirrored so a long child transcript cannot
#: be carried into the parent context whole.
_MAX_SUMMARY_WORDS = 300


def _clip_words(text: str, limit: int) -> str:
    """Clip `text` to `limit` whitespace-separated words."""

    words = (text or "").split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit])


def _measured_tokens(result: Any) -> int:
    """Sum the child's measured token usage, or 0 when none was reported.

    Absent provider usage stays absent rather than being estimated: the executor's
    own contract is explicit that a missing usage report is not zero usage, and
    inventing one would make a run look free.
    """

    total = 0
    for record in getattr(result, "token_usage_records", None) or []:
        if not isinstance(record, dict):
            continue
        for key in ("input_tokens", "output_tokens"):
            value = record.get(key)
            if isinstance(value, (int, float)):
                total += max(0, int(value))
    return total


def _observed_oracles(result: Any) -> list[dict[str, Any]]:
    """Build test oracles only from bash executions that genuinely ran.

    An entry exists because a command was executed and its outcome observed. No
    command means no oracle - never a `not_run` row dressed up as evidence, and
    never a row claiming `passed` without an observed success.
    """

    oracles: list[dict[str, Any]] = []
    for execution in getattr(result, "bash_executions", None) or []:
        if not isinstance(execution, dict):
            continue
        command = str(execution.get("command") or "").strip()
        if not command:
            continue
        marker = str(execution.get("status_marker") or "").strip()
        status = str(execution.get("status") or "").strip()
        # `passed` only on an observed success. Anything else stays what it was.
        oracle_status = "passed" if status == "success" else ("failed" if status else "unknown")
        oracles.append(
            {
                "command": command,
                "status": oracle_status,
                "detail": marker or status or "command executed; no exit marker was recorded",
            }
        )
    return oracles


@dataclass
class DeepAgentSubagentRunner:
    """Execute one isolated deep agent through Alpha's real subagent executor.

    Instances are callables, so one can be handed straight to
    `set_deep_agent_runner`.

    The tool pool comes either from `tools` directly or from
    `tools_supplier`. The supplier exists because the Gateway cannot assemble
    tools at startup: `get_available_tools()` needs MCP initialization, which the
    lifespan has not performed yet. Resolving per child is therefore the first
    honest option. A successful resolution is memoised (tool assembly is not
    free), but an EMPTY one is not cached, so a pool that failed once during a
    startup race does not permanently disable the feature.

    Either way, a child with no instruments refuses rather than running: see the
    module docstring.
    """

    tools: Sequence[Any] = field(default_factory=tuple)
    tools_supplier: Callable[[], Sequence[Any]] | None = None
    thread_id: str = ""
    run_id: str = ""
    model: str | None = None
    user_id: str | None = None
    app_config: Any | None = None

    def _pool(self) -> Sequence[Any]:
        """Return the child's tool pool, memoising only a non-empty resolution."""

        if self.tools:
            return self.tools
        if self.tools_supplier is None:
            return ()
        try:
            resolved = self.tools_supplier() or ()
        except Exception as exc:  # noqa: BLE001 - an unresolvable pool is a refusal, not an escape
            logger.warning("Deep agent tool pool could not be resolved: %s", exc)
            return ()
        if resolved:
            self.tools = tuple(resolved)
        return resolved

    def __call__(self, session: Any) -> DeepHandoffContract:
        """Run the child and return exactly what the run produced."""

        agent_type = str(getattr(session, "agent_type", "") or "")
        worker_name = DEEP_AGENT_TO_SUBAGENT.get(agent_type)
        if worker_name is None:
            return build_error_contract(
                getattr(session, "session_id", ""),
                agent_type,
                f"no shipped specialist worker for deep agent type {agent_type!r}; available: {sorted(DEEP_AGENT_TO_SUBAGENT)}",
            )
        pool = self._pool()
        if not pool:
            return build_error_contract(
                getattr(session, "session_id", ""),
                agent_type,
                "hierarchical delegation has no tool pool available; a child with no instruments cannot do the work it was asked for",
            )

        spec = getattr(session, "spec", None)
        goal = str(getattr(spec, "goal", "") or "")
        try:
            result = self._execute(worker_name, agent_type, session, goal, pool)
        except BaseException as exc:  # noqa: BLE001 - a child failure is a contract, not an escape
            logger.warning("Deep agent %s (%s) raised: %s", getattr(session, "session_id", ""), worker_name, exc)
            return build_error_contract(
                getattr(session, "session_id", ""),
                agent_type,
                f"{type(exc).__name__}: {exc}",
            )
        return self._to_contract(result, session, agent_type)

    # -- internals ---------------------------------------------------------

    def _execute(self, worker_name: str, agent_type: str, session: Any, goal: str, pool: Sequence[Any]) -> Any:
        """Build and run the executor for this child."""

        from alpha.subagents.builtins import BUILTIN_SUBAGENTS
        from alpha.subagents.config import SubagentConfig
        from alpha.subagents.executor import SubagentExecutor

        base = BUILTIN_SUBAGENTS.get(worker_name)
        if base is None:
            raise RuntimeError(f"shipped specialist worker {worker_name!r} is not registered in BUILTIN_SUBAGENTS")
        spec = getattr(session, "spec", None)
        max_iterations = int(getattr(spec, "max_iterations", 0) or 0) or base.max_turns
        time_budget = int(getattr(spec, "time_budget_seconds", 0) or 0) or base.timeout_seconds

        config = SubagentConfig(
            name=f"deep-{agent_type}",
            description=f"hierarchical delegation worker: {agent_type}",
            system_prompt=base.system_prompt,
            tools=list(base.tools) if base.tools is not None else None,
            inherit_all=bool(base.inherit_all),
            # The worker keeps the catalog's own denies, which include nested
            # `task`: a hierarchical child must not spawn another hierarchy.
            disallowed_tools=list(base.disallowed_tools or []),
            skills=list(base.skills) if base.skills else None,
            model=self.model or "inherit",
            max_turns=max_iterations,
            timeout_seconds=time_budget,
        )
        executor = SubagentExecutor(
            config=config,
            tools=list(pool),
            # A hierarchical child's result is returned synchronously to a parent
            # that is waiting on it, so it is foreground work by construction.
            admission_priority=PRIORITY_FOREGROUND,
            app_config=self.app_config,
            parent_model=self.model,
            thread_id=self.thread_id or None,
            run_id=self.run_id or None,
            user_id=self.user_id,
        )
        return executor.execute(self._prompt(goal, spec))

    @staticmethod
    def _prompt(goal: str, spec: Any) -> str:
        """Compose the child brief from the spec.

        Anthropic's finding applies directly: a brief needs an objective, an
        output format, tool guidance and clear boundaries. The spec supplies the
        first three; the boundaries are the parent's own framing below.
        """

        parts = [goal.strip()]
        targets = list(getattr(spec, "target_files", None) or [])
        if targets:
            parts.append("Target files (scoped to this task):\n" + "\n".join(f"- {t}" for t in targets[:40]))
        hints = list(getattr(spec, "context_hints", None) or [])
        if hints:
            parts.append("Context hints:\n" + "\n".join(f"- {h}" for h in hints[:20]))
        parts.append("Report what you actually did and what you actually observed. State failures plainly; do not claim a check you did not run.")
        return "\n\n".join(p for p in parts if p)

    def _to_contract(self, result: Any, session: Any, agent_type: str) -> DeepHandoffContract:
        """Project the real result onto the contract, honestly."""

        from alpha.subagents.executor import SubagentStatus

        session_id = str(getattr(session, "session_id", "") or "")
        status = getattr(result, "status", None)
        output = str(getattr(result, "result", "") or "")
        error = str(getattr(result, "error", "") or "")
        stop_reason = getattr(result, "stop_reason", None)

        if status is not SubagentStatus.COMPLETED:
            detail = error or f"subagent execution ended {getattr(status, 'value', 'with no status')}"
            if stop_reason:
                detail = f"{detail} (stop_reason={stop_reason})"
            return build_error_contract(session_id, agent_type, detail)

        summary = _clip_words(output, _MAX_SUMMARY_WORDS)
        if not summary:
            # A terminal COMPLETED with nothing usable is not a success. The
            # engine's own docstring forbids synthesising one.
            return DeepHandoffContract(
                status=DeepExecutionStatus.PARTIAL_PROGRESS,
                executive_summary="The run completed but produced no usable output.",
                session_id=session_id,
                agent_type=agent_type,
                error_detail="completed with an empty response",
            )

        return DeepHandoffContract(
            # A guardrail-capped completion is not a clean one, so it lands on
            # PARTIAL_PROGRESS rather than SUCCESS.
            status=(DeepExecutionStatus.PARTIAL_PROGRESS if stop_reason else DeepExecutionStatus.SUCCESS),
            executive_summary=summary,
            test_oracles=_observed_oracles(result),
            tokens_consumed=_measured_tokens(result),
            session_id=session_id,
            agent_type=agent_type,
            error_detail=f"stop_reason={stop_reason}" if stop_reason else "",
        )
