"""Failure-aware telemetry for swarm runs.

Aggregated success rates hide the signals that actually predict a bad run.  A
swarm can be 100% "completed" while every worker retried the same tool, every
result said the same thing, and the token budget was already past its warning
line.  This module derives those signals from data the plan already carries —
task states, attempts, measured usage, evidence, and budget counters — so the
answer costs nothing extra to obtain and cannot be fabricated by a model.

Every derived value states its basis.  Where a signal cannot be measured (a
loop that has not repeated yet, a budget with no configured ceiling), the field
reports ``unknown``/``None`` instead of a reassuring number.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from alpha.swarm.models import SwarmPlan, TaskNodeState

__all__ = [
    "BudgetPressure",
    "SwarmTelemetry",
    "normalized_signature",
    "token_novelty",
]

_WORD_RE = re.compile(r"[a-z0-9]+")
_COMPLETED = frozenset({TaskNodeState.COMPLETED})

# Budget pressure thresholds are ratios of measured usage to a *configured*
# ceiling.  An unlimited dimension reports ``unknown`` rather than 0.0 — "no
# limit set" and "nothing used" are different facts.
PRESSURE_WARNING = 0.70
PRESSURE_CRITICAL = 0.90


def normalized_signature(text: str) -> str:
    """Lowercased, punctuation-stripped, whitespace-collapsed signature.

    Used to detect "the same work described twice" without being fooled by
    casing or spacing, which is exactly how a duplicated plan hides.
    """

    words = _WORD_RE.findall(str(text or "").lower())
    return " ".join(words)


def token_novelty(previous: str, current: str) -> float | None:
    """Jaccard novelty of two texts: 1.0 = nothing shared, 0.0 = identical.

    ``None`` when either side has no tokens — an empty result carries no
    information, and scoring it as "no novelty" would invent a finding.
    """

    left = set(_WORD_RE.findall(str(previous or "").lower()))
    right = set(_WORD_RE.findall(str(current or "").lower()))
    if not left or not right:
        return None
    union = left | right
    if not union:
        return None
    return round(1.0 - (len(left & right) / len(union)), 6)


@dataclass(frozen=True)
class BudgetPressure:
    """Measured spend against a configured ceiling, per dimension."""

    tokens: float | None
    tool_calls: float | None
    wall_seconds: float | None
    state: str
    exhausted_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokens": self.tokens,
            "tool_calls": self.tool_calls,
            "wall_seconds": self.wall_seconds,
            "state": self.state,
            "exhausted_reason": self.exhausted_reason,
        }


class SwarmTelemetry:
    """Derives failure-aware signals for one plan.  Stateless across calls."""

    @staticmethod
    def budget_pressure(plan: SwarmPlan, *, now: float | None = None) -> BudgetPressure:
        budget = plan.budget
        token_ratio = (budget.used_tokens / budget.max_tokens) if budget.max_tokens else None
        tool_ratio = (budget.used_tool_calls / budget.max_tool_calls) if budget.max_tool_calls else None
        wall_ratio: float | None = None
        if budget.max_wall_seconds and budget.started_at is not None and now is not None:
            wall_ratio = max(0.0, float(now) - budget.started_at) / budget.max_wall_seconds
        elif budget.max_wall_seconds and budget.started_at is not None:
            wall_ratio = None  # no clock supplied: report unknown, not zero
        ratios = [value for value in (token_ratio, tool_ratio, wall_ratio) if value is not None]
        if budget.exhausted:
            state = "exhausted"
        elif ratios and max(ratios) >= PRESSURE_CRITICAL:
            state = "critical"
        elif ratios and max(ratios) >= PRESSURE_WARNING:
            state = "warning"
        elif ratios:
            state = "ok"
        else:
            state = "unbounded"
        return BudgetPressure(
            tokens=None if token_ratio is None else round(token_ratio, 6),
            tool_calls=None if tool_ratio is None else round(tool_ratio, 6),
            wall_seconds=None if wall_ratio is None else round(wall_ratio, 6),
            state=state,
            exhausted_reason=budget.exhausted_reason,
        )

    @staticmethod
    def loop_indicator(plan: SwarmPlan) -> dict[str, Any]:
        """Detect duplicated objectives or duplicated results — a rework loop.

        Two tasks with the same normalized objective mean the plan was
        decomposed twice over the same work; two completed tasks with the same
        normalized result mean workers are converging on one answer without
        independent value.  Either way the run is spending budget on repeats.
        """

        objective_counts = Counter(normalized_signature(task.objective) for task in plan.tasks.values() if normalized_signature(task.objective))
        repeated_objectives = sorted(signature for signature, count in objective_counts.items() if count > 1)

        result_counts = Counter(normalized_signature(task.result_summary) for task in plan.tasks.values() if task.state in _COMPLETED and task.result_summary and normalized_signature(task.result_summary))
        repeated_results = sorted(signature for signature, count in result_counts.items() if count > 1)

        retried = sorted(task.task_id for task in plan.tasks.values() if task.attempts > 1)
        flagged = bool(repeated_objectives or repeated_results or retried)
        return {
            "flagged": flagged,
            "repeated_objectives": repeated_objectives[:5],
            "repeated_results": repeated_results[:5],
            "retried_task_ids": retried,
            "basis": "normalized objective/result signature equality plus attempt counts",
        }

    @staticmethod
    def tool_instability(plan: SwarmPlan) -> dict[str, Any]:
        """Retry pressure and per-tool failure concentration from evidence rows.

        Evidence rows are worker-declared and may omit tool names, so a run
        with no tool evidence reports ``tool_error_rate=None`` rather than a
        confident 0.0.
        """

        attempts = sum(max(0, int(task.attempts)) for task in plan.tasks.values())
        completions = sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.COMPLETED)
        failures = sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.FAILED)
        retries = max(0, attempts - len(plan.tasks))

        tool_errors: Counter[str] = Counter()
        evidence_rows = 0
        for task in plan.tasks.values():
            for row in task.evidence:
                if not isinstance(row, Mapping):
                    continue
                evidence_rows += 1
                failed = bool(row.get("failed") or row.get("error") or row.get("ok") is False or str(row.get("status", "")).lower() in {"error", "failed", "failure"})
                if failed:
                    tool = str(row.get("tool") or row.get("tool_name") or row.get("name") or "unknown")
                    tool_errors[tool] += 1

        error_rate = None
        if evidence_rows:
            error_rate = round(sum(tool_errors.values()) / evidence_rows, 6)
        return {
            "task_attempts": attempts,
            "task_completions": completions,
            "task_failures": failures,
            "retries": retries,
            "retry_rate": round(retries / attempts, 6) if attempts else 0.0,
            "tool_error_rate": error_rate,
            "evidence_rows": evidence_rows,
            "top_failing_tools": [{"tool": tool, "errors": count} for tool, count in tool_errors.most_common(5)],
            "basis": "attempt deltas and worker-declared evidence rows; tool_error_rate is None when no evidence declares a tool outcome",
        }

    @staticmethod
    def information_gain(plan: SwarmPlan, *, window: int = 8) -> dict[str, Any]:
        """Novelty of successive completed results, oldest first.

        A long run whose results keep re-stating earlier results is not
        learning; averaging novelty over a bounded window keeps early
        genuinely-novel results from permanently masking a later plateau.
        """

        completed = sorted(
            (task for task in plan.tasks.values() if task.state in _COMPLETED and task.result_summary),
            key=lambda task: (task.completed_at or 0.0, task.task_id),
        )
        windowed = completed[-max(1, int(window)) :]
        values = [token_novelty(left.result_summary or "", right.result_summary or "") for left, right in zip(windowed, windowed[1:], strict=False)]
        measured = [value for value in values if value is not None]
        mean = round(sum(measured) / len(measured), 6) if measured else None
        state = "unknown" if mean is None else ("low" if mean < 0.30 else "ok")
        return {
            "mean_novelty": mean,
            "state": state,
            "samples": len(measured),
            "basis": "mean Jaccard novelty between consecutive completed result summaries in the trailing window",
        }

    @staticmethod
    def coordination_overhead(plan: SwarmPlan) -> dict[str, Any]:
        """Messages published per completed task — pure coordination cost."""

        bus_state = plan.blackboard_context.get("messages_state")
        messages = 0
        if isinstance(bus_state, Mapping):
            raw = bus_state.get("messages")
            messages = len(raw) if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)) else 0
        completed = sum(1 for task in plan.tasks.values() if task.state in _COMPLETED)
        return {
            "messages": messages,
            "completed_tasks": completed,
            "messages_per_completed_task": round(messages / completed, 4) if completed else None,
            "basis": "bounded blackboard message count over completed tasks; None before any task completes",
        }

    @classmethod
    def snapshot(cls, plan: SwarmPlan, *, now: float | None = None, deliberation: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Full signal set for one plan, plus any deliberation report available.

        ``deliberation_rounds_saved`` is a count of rounds the sequential test
        did not run — never a token estimate, because no round-level token
        meter exists and a converted figure would be invented.
        """

        pressure = cls.budget_pressure(plan, now=now)
        loop = cls.loop_indicator(plan)
        instability = cls.tool_instability(plan)
        gain = cls.information_gain(plan)
        overhead = cls.coordination_overhead(plan)
        risk: list[str] = []
        if loop["flagged"]:
            risk.append("rework_loop")
        if pressure.state in {"warning", "critical", "exhausted"}:
            risk.append("budget_pressure")
        if instability["retry_rate"] > 0.5:
            risk.append("retry_pressure")
        if gain["state"] == "low":
            risk.append("low_information_gain")
        if instability["tool_error_rate"] is not None and instability["tool_error_rate"] >= 0.5:
            risk.append("tool_instability")

        report = dict(deliberation) if deliberation else None
        saved = None
        if report:
            max_rounds = int(report.get("policy", {}).get("max_rounds", 0) or 0)
            used = int(report.get("rounds_used", 0) or 0)
            saved = max(0, max_rounds - used) if max_rounds else None

        return {
            "budget_pressure": pressure.to_dict(),
            "loop_indicator": loop,
            "tool_instability": instability,
            "information_gain": gain,
            "coordination_overhead": overhead,
            "risk_signals": risk,
            "deliberation": report,
            "deliberation_rounds_saved": saved,
            "basis": "derived from persisted plan state; no model call and no estimated token conversion",
        }
