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

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from alpha.swarm.diversity import panel_decorrelation, panel_voters
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


def _as_float(value: Any, default: float = 0.0) -> float:
    """Best-effort float, falling back rather than raising on a bad estimate."""

    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


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

    @staticmethod
    def parallelism_realised(plan: SwarmPlan) -> dict[str, Any]:
        """Did the swarm actually run tasks concurrently, or one at a time?

        **Why this is measured.**  Kimi's PARL needed a dedicated reward term
        (``r_parallel``) to suppress *serial collapse* - the local optimum where
        an orchestrator defaults to single-agent execution because sequential is
        easier to credit-assign.  A swarm can spawn thirty subagents and still
        run them serially, paying thirty times the coordination for one agent's
        throughput.  Alpha had no signal for this, so a collapsed run was
        indistinguishable from a correct one.

        Overlap is computed from the recorded ``started_at``/``completed_at``
        intervals, not from a counter, so it reflects what actually happened
        rather than what the scheduler intended.  Tasks with no timestamps are
        skipped rather than assumed serial - an unmeasured run is ``unknown``,
        never ``false``.
        """

        intervals: list[tuple[float, float]] = []
        for task in plan.tasks.values():
            started = task.started_at
            completed = task.completed_at
            if started is None or completed is None:
                continue
            start = float(started)
            end = float(completed)
            if end < start:
                continue
            intervals.append((start, end))

        if not intervals:
            return {
                "max_concurrent_observed": None,
                "flagged": False,
                "state": "unknown",
                "basis": "no task recorded both a start and completion timestamp",
            }

        # Sweep the interval endpoints: the maximum number of overlapping
        # intervals is the peak concurrency the run actually reached.
        events: list[tuple[float, int]] = []
        for start, end in intervals:
            events.append((start, 1))
            events.append((end, -1))
        events.sort(key=lambda item: (item[0], item[1]))
        current = 0
        peak = 0
        for _timestamp, delta in events:
            current += delta
            peak = max(peak, current)

        spawned = len(plan.tasks)
        # Collapse means: more than one task existed, and never two at once.
        flagged = spawned > 1 and peak <= 1
        return {
            "max_concurrent_observed": peak,
            "tasks_with_intervals": len(intervals),
            "tasks_spawned": spawned,
            "flagged": flagged,
            "state": "collapsed" if flagged else "parallel",
            "basis": "peak overlap of recorded started_at/completed_at intervals; unknown when no task has both",
        }

    @staticmethod
    def subtask_completion_ratio(plan: SwarmPlan) -> dict[str, Any]:
        """How much of a fan-out produced something worth keeping.

        **Why this is measured.**  The companion degeneracy to serial collapse
        is *spurious parallelism*: the orchestrator spawns many subagents without
        meaningful decomposition, inflating a parallel metric while completing
        little.  Kimi needed ``r_finish`` for exactly this.  Alpha's
        ``loop_indicator`` catches repeated work but not a wide fan-out that
        simply accomplishes little.

        Retried tasks are excluded from the denominator's intent: a task that
        failed and was retried is one unit of work, not two units of fan-out.
        """

        spawned = len(plan.tasks)
        completed = sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.COMPLETED)
        failed = sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.FAILED)
        meaningful = sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.COMPLETED and bool((task.result_summary or "").strip()))
        ratio = round(meaningful / spawned, 6) if spawned else None
        # A wide fan-out that completes little is the shape to flag.  The
        # threshold is deliberately low: four tasks is already a fan-out, and
        # fewer than half of them producing anything is not parallelism.
        #
        # But only once the run has produced evidence.  A freshly spawned plan
        # with four pending tasks and zero completions is not spurious
        # parallelism - it has not started - and flagging it would report a
        # verdict nobody measured.  Hence the terminal-state gate below.
        has_evidence = completed + failed > 0
        flagged = bool(spawned >= 4 and has_evidence and ratio is not None and ratio < 0.5)
        if not has_evidence:
            state = "unknown"
        elif flagged:
            state = "spurious"
        else:
            state = "ok"
        return {
            "tasks_spawned": spawned,
            "tasks_completed": completed,
            "tasks_failed": failed,
            "tasks_with_results": meaningful,
            "completion_ratio": ratio,
            "flagged": flagged,
            "state": state,
            "basis": "completed tasks with a non-empty result over spawned tasks; flagged at >=4 spawned with ratio < 0.5 once any task reached a terminal state",
        }

    @staticmethod
    def cost_projection(plan: SwarmPlan) -> dict[str, Any]:
        """What a not-yet-run plan is predicted to cost, before it spends anything.

        **Why this exists.** ``SwarmBudget`` bounds a run *while* it runs, and
        ``budget_pressure`` reports the ratio *after* spend.  Neither answers the
        only question that decides whether to start: given this decomposition,
        will it fit the ceiling I already declared?  Arena refuses a tournament
        above ``require_confirmation_over_calls`` for exactly this reason; the
        swarm had no equivalent.

        **Only wall-clock is projected, and that is a limitation, not an
        omission.**  The decomposer computes a seconds-weighted critical path
        (``critical_path_seconds``) from per-task ``estimated_seconds``.  There
        is no per-task token or tool-call estimate anywhere in the plan, so
        ``projected_tokens`` and ``projected_tool_calls`` are ``None`` —
        "unmeasured", never ``0`` and never a invented figure.  A projection that
        quietly guessed would be worse than no projection.

        The refusal rule is deliberately self-consistent rather than
        configurable: a plan whose own predicted makespan exceeds its own
        declared ``max_wall_seconds`` cannot honour that budget, so it is refused
        before the first worker starts.  No new config key is needed, and the
        check cannot drift from the budget it is checking.
        """

        metrics = plan.metrics if isinstance(plan.metrics, Mapping) else {}
        projected_wall = max(0.0, _as_float(metrics.get("estimated_parallel_seconds"), plan.critical_path_seconds))
        projected_serial = max(0.0, _as_float(metrics.get("estimated_serial_seconds")))
        speedup = _as_float(metrics.get("estimated_speedup"), plan.estimated_speedup)
        wall_budget = plan.budget.max_wall_seconds
        tool_budget = plan.budget.max_tool_calls

        wall_exceeded = None
        if wall_budget is not None:
            wall_exceeded = projected_wall > float(wall_budget)

        return {
            "projected_tasks": len(plan.tasks),
            "projected_wall_seconds": round(projected_wall, 3),
            "projected_serial_seconds": round(projected_serial, 3),
            "estimated_speedup": round(speedup, 4) if speedup else None,
            "wall_budget_seconds": wall_budget,
            "wall_budget_exceeded": wall_exceeded,
            "tool_budget_calls": tool_budget,
            # Unmeasured, not zero: no per-task token/tool estimate exists.
            "projected_tokens": None,
            "projected_tool_calls": None,
            "max_concurrency": plan.max_concurrency,
            "estimate_method": str(metrics.get("estimate_method") or "not_computed"),
            "basis": "decomposer critical-path estimate; token and tool projections are None because no per-task estimate exists",
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
        parallelism = cls.parallelism_realised(plan)
        completion = cls.subtask_completion_ratio(plan)
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
        # The two degenerate parallel policies.  Both are advisory: a flagged
        # run is reported, never aborted, because a serial run that succeeded is
        # still a success - it is just an expensive one, and the operator is the
        # one who should see that.
        if parallelism["flagged"]:
            risk.append("serial_collapse")
        if completion["flagged"]:
            risk.append("spurious_parallelism")

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
            "parallelism_realised": parallelism,
            "subtask_completion_ratio": completion,
            "risk_signals": risk,
            "deliberation": report,
            "deliberation_rounds_saved": saved,
            # Panel decorrelation is recomputed from the same task summaries
            # every time, so the telemetry read and the aggregator's guard
            # cannot drift; not a panel mode (or <2 outputs) reads `None`.
            "panel_decorrelation": panel_decorrelation([task.result_summary for task in panel_voters(plan)]),
            # The calibrated projection recorded at creation (S7).  Absent for
            # pre-existing plans means "not measured at admission", not zero.
            "cost_projection": plan.metrics.get("calibrated_cost_projection") if isinstance(plan.metrics, Mapping) else None,
            "basis": "derived from persisted plan state; no model call and no estimated token conversion",
        }
