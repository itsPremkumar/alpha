"""GRO execution adapters: how one candidate lane actually runs its task.

Two shapes:

- ``SyncCallableAdapter`` wraps a plain callable — tests and hosts that already
  have their own execution seam.
- ``SubagentExecutionAdapter`` runs the lane through Alpha's real
  ``SubagentExecutor``, so a candidate group *is* subagent mode: the process-wide
  subagent admission controller, sandbox leases, receipts and usage accounting
  are inherited rather than re-implemented. Each lane constructs the executor
  the same way the swarm specialist worker does (one built-in agent config per
  candidate, one bounded tool pool shared across lanes).

Adapters never score or rank: they produce a ``CandidateOutcome`` — execution
facts only — and ``alpha.gro.evaluation`` decides what they mean.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from alpha.gro.models import CandidateOutcome, CandidateSpec, CandidateStatus


class ExecutionAdapter(Protocol):
    """Runs one candidate lane and returns its execution facts."""

    def run(self, spec: CandidateSpec) -> CandidateOutcome:  # pragma: no cover - protocol
        ...


@dataclass
class SyncCallableAdapter:
    """Wrap ``fn(spec)`` as an adapter.

    ``fn`` may return a ``CandidateOutcome``, a plain result string (mapped to
    ``COMPLETED``), or raise (mapped to ``FAILED`` with the exception text).
    """

    fn: Callable[[CandidateSpec], Any]

    def run(self, spec: CandidateSpec) -> CandidateOutcome:
        began = time.time()
        try:
            raw = self.fn(spec)
        except BaseException as exc:  # noqa: BLE001 - a lane failure is an outcome, not an escape
            return CandidateOutcome(
                candidate_id=spec.candidate_id,
                status=CandidateStatus.FAILED,
                duration_ms=(time.time() - began) * 1000.0,
                error=f"{type(exc).__name__}: {exc}",
            )
        if isinstance(raw, CandidateOutcome):
            if raw.duration_ms <= 0:
                raw.duration_ms = (time.time() - began) * 1000.0
            return raw
        return CandidateOutcome(
            candidate_id=spec.candidate_id,
            status=CandidateStatus.COMPLETED,
            output_text="" if raw is None else str(raw),
            duration_ms=(time.time() - began) * 1000.0,
        )


@dataclass
class SubagentExecutionAdapter:
    """Run each candidate lane as a real subagent execution.

    ``tools`` is the bounded pool assembled once for the whole run (the swarm
    runner's specialist-pool shape); an empty pool is a refusal at run time,
    not a silent tool-less candidate — a lane with no tools would score worse
    for having fewer instruments, which poisons the comparison.
    """

    thread_id: str
    run_id: str
    tools: Sequence[Any]
    model: str | None = None
    user_id: str | None = None
    app_config: Any | None = None
    max_turns: int | None = None
    agent_name: str = "general-purpose"
    #: Extra context injected into every candidate prompt (thread-level notes).
    context_suffix: str = ""
    build_config: Callable[[CandidateSpec], Any] | None = None
    tool_pool_error: str = ""

    def run(self, spec: CandidateSpec) -> CandidateOutcome:
        if self.tool_pool_error:
            raise RuntimeError(f"GRO candidate {spec.candidate_id} cannot execute: {self.tool_pool_error}")
        if not self.tools:
            raise RuntimeError(f"GRO candidate {spec.candidate_id} cannot execute: no tool pool was assembled for the run")

        from alpha.subagents.builtins import BUILTIN_SUBAGENTS
        from alpha.subagents.config import SubagentConfig
        from alpha.subagents.executor import SubagentExecutor, SubagentStatus

        config = self._build_config(spec, BUILTIN_SUBAGENTS, SubagentConfig)
        executor = SubagentExecutor(
            config=config,
            tools=list(self.tools),
            app_config=self.app_config,
            parent_model=self.model,
            thread_id=self.thread_id,
            run_id=self.run_id,
            user_id=self.user_id,
            acceptance_criteria=list(spec.acceptance_criteria),
        )
        prompt = spec.to_prompt()
        if self.context_suffix:
            prompt = f"{prompt}\n{self.context_suffix}"
        began = time.time()
        result = executor.execute(prompt)
        return self._map_result(spec, result, SubagentStatus, began)

    def _build_config(self, spec: CandidateSpec, builtins: Any, config_cls: Any) -> Any:
        if self.build_config is not None:
            return self.build_config(spec)
        base = builtins.get(self.agent_name) or builtins.get("general-purpose")
        if base is None:
            raise RuntimeError("no built-in subagent configuration is available for a GRO candidate")
        return config_cls(
            name=f"gro-{spec.candidate_id}",
            description=f"GRO candidate {spec.candidate_id} ({spec.strategy.strategy_id})",
            system_prompt=base.system_prompt,
            tools=list(base.tools) if base.tools is not None else None,
            inherit_all=bool(base.inherit_all),
            disallowed_tools=list(base.disallowed_tools or []),
            skills=list(base.skills) if base.skills else None,
            model=self.model or "inherit",
            max_turns=self.max_turns if self.max_turns is not None else base.max_turns,
            timeout_seconds=base.timeout_seconds,
        )

    @staticmethod
    def _map_result(spec: CandidateSpec, result: Any, status_enum: Any, began: float) -> CandidateOutcome:
        status = getattr(result, "status", None)
        output_text = str(getattr(result, "result", "") or "")
        error = str(getattr(result, "error", "") or "")
        stop_reason = getattr(result, "stop_reason", None)
        if stop_reason:
            error = f"{error} (stop_reason={stop_reason})" if error else f"stop_reason={stop_reason}"
        usage = SubagentExecutionAdapter._measured_usage(result)
        receipts = getattr(result, "tool_receipts", None) or []
        bash_executions = list(getattr(result, "bash_executions", None) or [])
        if status is status_enum.COMPLETED:
            mapped = CandidateStatus.COMPLETED
        elif status is not None:
            mapped = CandidateStatus.FAILED
            if not error:
                error = f"subagent execution {getattr(status, 'value', 'unknown')}"
        else:
            mapped = CandidateStatus.FAILED
            error = error or "subagent executor reported no status"
        if mapped is CandidateStatus.COMPLETED and not output_text.strip():
            # A terminal COMPLETED with no text is not a result — the swarm
            # specialist path raises on the same condition.
            mapped = CandidateStatus.FAILED
            error = "candidate completed with an empty response"
        return CandidateOutcome(
            candidate_id=spec.candidate_id,
            status=mapped,
            output_text=output_text,
            usage=usage,
            duration_ms=(time.time() - began) * 1000.0,
            error=error,
            bash_executions=bash_executions,
            tool_receipt_count=len(receipts),
        )

    @staticmethod
    def _measured_usage(result: Any) -> dict[str, int] | None:
        """Child-measured token totals, or ``None`` when the provider reported none."""

        input_tokens = 0
        output_tokens = 0
        seen = False
        for record in getattr(result, "token_usage_records", None) or []:
            if not isinstance(record, dict):
                continue
            raw_in = record.get("input_tokens")
            raw_out = record.get("output_tokens")
            if raw_in is not None or raw_out is not None:
                seen = True
            input_tokens += max(0, _as_int(raw_in))
            output_tokens += max(0, _as_int(raw_out))
        if not seen:
            return None
        return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}


def _as_int(value: Any) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
