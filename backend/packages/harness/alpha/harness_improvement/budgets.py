"""Budgets, the ledger and circuit breakers (guide §12.3, §17).

Three honesty rules decide this module:

* **A ceiling has three states, not two.** *Enforced* (measured and below the
  cap), *exhausted* (measured and at/above the cap) and *unenforceable* (the
  provider reports no usage for this dimension, so the cap cannot be checked).
  An unenforceable cap is reported as unenforceable — never silently treated as
  "under budget", which is the failure mode where a cost cap protects nothing.
* **Refusal names the bound.** :class:`BudgetExceeded` carries the bound, the
  measured spend and the ceiling; a caller never has to guess which limit
  tripped.
* **The deliverable is reserved before research starts.** refinement stages may
  not spend into the delivery reserve, so a run that improves the harness
  cannot consume the budget the task artifact needs (guide §17 "Cost
  accounting").
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from alpha.harness_improvement.models import TokenUsage
from alpha.harness_improvement.store import BudgetLedgerEntry, utc_now


class BudgetExceeded(RuntimeError):
    """A hard ceiling would be crossed. The bound, spend and cap are named."""

    def __init__(self, *, bound: str, spent: Any, ceiling: Any, stage: str) -> None:
        super().__init__(f"budget bound {bound!r} exceeded at stage {stage!r}: spent={spent!r}, ceiling={ceiling!r}")
        self.bound = bound
        self.spent = spent
        self.ceiling = ceiling
        self.stage = stage


@dataclass(frozen=True, slots=True)
class BudgetCeilings:
    """The operator's hard limits for one improvement run.

    Defaults follow the guide's §17 sketch: one sequential step, two
    candidates, one concurrent probe, five minutes wall clock, measured model
    and tool traffic. ``None`` on a token/cost dimension means "unstated" — the
    dimensions the run *can* measure are still enforced.
    """

    max_model_calls: int | None = 24
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost_usd: float | None = None
    max_wall_time_seconds: int = 300
    max_tool_calls_per_probe: int = 60
    max_model_turns_per_probe: int = 80
    max_parallel_probes: int = 1
    deliverable_reserve_fraction: float = 0.25

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_cost_usd": self.max_cost_usd,
            "max_wall_time_seconds": self.max_wall_time_seconds,
            "max_tool_calls_per_probe": self.max_tool_calls_per_probe,
            "max_model_turns_per_probe": self.max_model_turns_per_probe,
            "max_parallel_probes": self.max_parallel_probes,
            "deliverable_reserve_fraction": self.deliverable_reserve_fraction,
        }


@dataclass(frozen=True, slots=True)
class StageCharge:
    """What one stage spent, as measured — missing fields stay ``None``."""

    stage: str
    model_calls: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    elapsed_ms: int = 0
    tool_calls: int = 0
    model_turns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "model_calls": self.model_calls,
            "usage": self.usage.to_dict(),
            "elapsed_ms": self.elapsed_ms,
            "tool_calls": self.tool_calls,
            "model_turns": self.model_turns,
        }


@dataclass(slots=True)
class BudgetState:
    """The run's measured spend against its ceilings.

    ``unmeasured`` accumulates the ``(stage, dimension)`` pairs a provider did
    not report for. Consumers must surface it: "no cost was reported" and "no
    cost was incurred" are different facts, and only the second one belongs in
    a comparison table.
    """

    ceilings: BudgetCeilings
    run_id: str
    model_calls: int = 0
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    cost_usd: float | None = 0.0
    tool_calls: int = 0
    model_turns: int = 0
    elapsed_ms: int = 0
    started_at: float | None = None
    unmeasured: list[str] = field(default_factory=list)
    charges: list[StageCharge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # -- accounting ----------------------------------------------------------
    def charge(self, charge: StageCharge) -> StageCharge:
        """Record one stage's measured spend.

        A dimension the stage did not report is *not* added as zero: it is
        listed in :attr:`unmeasured` and the run total for that dimension
        becomes ``None`` (unknown), which propagates honestly to the report.
        """
        self.charges.append(charge)
        self.model_calls += charge.model_calls
        self.tool_calls += charge.tool_calls
        self.model_turns += charge.model_turns
        self.elapsed_ms += charge.elapsed_ms
        usage = charge.usage
        if usage.input_tokens is None:
            self.unmeasured.append(f"{charge.stage}:input_tokens")
            self.input_tokens = None
        elif self.input_tokens is not None:
            self.input_tokens += usage.input_tokens
        if usage.output_tokens is None:
            self.unmeasured.append(f"{charge.stage}:output_tokens")
            self.output_tokens = None
        elif self.output_tokens is not None:
            self.output_tokens += usage.output_tokens
        if usage.cost_usd is None:
            self.unmeasured.append(f"{charge.stage}:cost_usd")
            self.cost_usd = None
        elif self.cost_usd is not None:
            self.cost_usd += usage.cost_usd
        return charge

    def ledger_entry(self, charge: StageCharge, *, model: str | None = None) -> BudgetLedgerEntry:
        usage = charge.usage
        return BudgetLedgerEntry(
            run_id=self.run_id,
            stage=charge.stage,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
            elapsed_ms=charge.elapsed_ms,
            recorded_at=utc_now(),
            ceilings=self.ceilings.to_dict(),
        )

    # -- circuit breakers ----------------------------------------------------
    def check_model_call(self, *, stage: str, model_calls: int = 1) -> None:
        """Refuse the next model-spending step when a measured bound is crossed."""
        if self.ceilings.max_model_calls is not None and self.model_calls + model_calls > self.ceilings.max_model_calls:
            raise BudgetExceeded(bound="max_model_calls", spent=self.model_calls, ceiling=self.ceilings.max_model_calls, stage=stage)
        self._check_measured("max_input_tokens", self.input_tokens, stage)
        self._check_measured("max_output_tokens", self.output_tokens, stage)
        self._check_measured("max_cost_usd", self.cost_usd, stage)

    def check_wall_clock(self, *, stage: str, now: float | None = None) -> None:
        if self.started_at is None:
            return
        elapsed = (now if now is not None else time.monotonic()) - self.started_at
        if elapsed > self.ceilings.max_wall_time_seconds:
            raise BudgetExceeded(bound="max_wall_time_seconds", spent=round(elapsed, 3), ceiling=self.ceilings.max_wall_time_seconds, stage=stage)

    def check_probe_limits(self, *, stage: str, tool_calls: int, model_turns: int) -> None:
        """Per-probe ceilings are checked against the probe's own measurements."""
        if tool_calls > self.ceilings.max_tool_calls_per_probe:
            raise BudgetExceeded(bound="max_tool_calls_per_probe", spent=tool_calls, ceiling=self.ceilings.max_tool_calls_per_probe, stage=stage)
        if model_turns > self.ceilings.max_model_turns_per_probe:
            raise BudgetExceeded(bound="max_model_turns_per_probe", spent=model_turns, ceiling=self.ceilings.max_model_turns_per_probe, stage=stage)

    def check_reserve(self, *, stage: str, dimension: str = "max_model_calls") -> None:
        """Refuse research spending that would eat the deliverable reserve.

        Only meaningful for a countable dimension with a stated ceiling; for an
        unstated or unmeasured dimension the check is skipped and the reason is
        recorded, because pretending to enforce it would be worse than saying
        the bound does not exist.
        """
        ceiling = getattr(self.ceilings, dimension, None)
        spent = getattr(self, dimension, None)
        if ceiling is None or spent is None:
            self.warnings.append(f"reserve check skipped at {stage}: {dimension} is {'unstated' if ceiling is None else 'unmeasured'}")
            return
        reserve = int(ceiling * self.ceilings.deliverable_reserve_fraction)
        if spent >= ceiling - reserve:
            raise BudgetExceeded(bound=f"{dimension}_deliverable_reserve", spent=spent, ceiling=ceiling - reserve, stage=stage)

    def _check_measured(self, bound: str, spent: int | float | None, stage: str) -> None:
        ceiling = getattr(self.ceilings, bound, None)
        if ceiling is None:
            return
        if spent is None:
            self.warnings.append(f"{bound} cannot be enforced at {stage}: usage was not reported by the provider")
            return
        if spent > ceiling:
            raise BudgetExceeded(bound=bound, spent=spent, ceiling=ceiling, stage=stage)

    # -- reporting -----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """A status payload whose unknown dimensions stay ``null``."""
        return {
            "run_id": self.run_id,
            "ceilings": self.ceilings.to_dict(),
            "spent": {
                "model_calls": self.model_calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cost_usd": self.cost_usd,
                "tool_calls": self.tool_calls,
                "model_turns": self.model_turns,
                "elapsed_ms": self.elapsed_ms,
            },
            "remaining": {
                "model_calls": None if self.ceilings.max_model_calls is None else self.ceilings.max_model_calls - self.model_calls,
                "cost_usd": None if self.ceilings.max_cost_usd is None or self.cost_usd is None else round(self.ceilings.max_cost_usd - self.cost_usd, 6),
            },
            "unmeasured": sorted(set(self.unmeasured)),
            "warnings": self.warnings,
            "durability": "single-process JSON/JSONL; never cross-process exactly-once",
        }


def estimate_run_cost(*, candidates: int, mode: str = "sequential") -> dict[str, int]:
    """A pre-run call-count shape, not a cost claim.

    Returns the *number of model-spending executions* the configured run will
    perform (baseline, proposers, probes, successor, reflection), so admission
    can compare it to a ceiling. It deliberately does not estimate tokens:
    guessing token counts from call counts would be a fabricated number.
    """
    probes = max(0, int(candidates))
    steps = 1 if mode == "sequential" else 2
    return {
        "baseline_runs": 1,
        "editor_calls": 2 * steps,
        "probe_runs": probes * steps,
        "successor_runs": 1 if probes else 0,
        "max_model_executions": 1 + 2 * steps + probes * steps + (1 if probes else 0),
    }
