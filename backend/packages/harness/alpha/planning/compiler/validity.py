from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReplanDecision(str, Enum):
    NOMINAL = "nominal"
    LOCAL_REPLAN = "local_replan"
    GLOBAL_REPLAN = "global_replan"


@dataclass
class PlanValidityReport:
    validity_score: float  # 0.0 to 1.0
    decision: ReplanDecision
    passed_tasks: int
    failed_tasks: int
    total_tasks: int
    invariant_breach: bool
    diagnostic: str
    should_replan: bool = False
    next_action: str = "continue"
    failed_task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "validity_score": self.validity_score,
            "decision": self.decision.value,
            "passed_tasks": self.passed_tasks,
            "failed_tasks": self.failed_tasks,
            "total_tasks": self.total_tasks,
            "invariant_breach": self.invariant_breach,
            "diagnostic": self.diagnostic,
            "should_replan": self.should_replan,
            "next_action": self.next_action,
            "failed_task_ids": list(self.failed_task_ids),
        }


class PlanValidityMonitor:
    """
    Monitors live plan execution health.
    Evaluates validity score V in [0.0, 1.0]:
      V >= 0.70 -> NOMINAL execution
      0.40 <= V < 0.70 -> LOCAL_REPLAN (patch single subgoal)
      V < 0.40 or Invariant Breach -> GLOBAL_REPLAN (full mission recompilation)

    Optional signals (tool_error_rate, stalled) only ever *lower* the score,
    so historical (passed, failed, total, consecutive) behavior is preserved.
    """

    @staticmethod
    def evaluate(
        passed_tasks: int,
        failed_tasks: int,
        total_tasks: int,
        invariant_breach: bool = False,
        consecutive_failures: int = 0,
        *,
        tool_error_rate: float = 0.0,
        stalled: bool = False,
        failed_task_ids: list[str] | None = None,
    ) -> PlanValidityReport:
        if total_tasks <= 0:
            total_tasks = 1

        if invariant_breach:
            return PlanValidityReport(
                validity_score=0.0,
                decision=ReplanDecision.GLOBAL_REPLAN,
                passed_tasks=passed_tasks,
                failed_tasks=failed_tasks,
                total_tasks=total_tasks,
                invariant_breach=True,
                diagnostic="CRITICAL: Kernel safety invariant breached. Forcing GLOBAL_REPLAN.",
                should_replan=True,
                next_action="global_replan: halt waves, recompile mission",
                failed_task_ids=list(failed_task_ids or []),
            )

        pass_rate = passed_tasks / total_tasks
        fail_penalty = (failed_tasks / total_tasks) * 0.5
        consecutive_penalty = min(0.3, consecutive_failures * 0.15)
        tool_penalty = min(0.2, max(0.0, float(tool_error_rate)) * 0.2)
        stall_penalty = 0.15 if stalled else 0.0

        raw_v = pass_rate - fail_penalty - consecutive_penalty - tool_penalty - stall_penalty
        v = round(max(0.0, min(1.0, raw_v)), 3)

        if v >= 0.70 and consecutive_failures == 0 and not stalled:
            decision = ReplanDecision.NOMINAL
            diag = "Plan health is NOMINAL. Continuing current execution waves."
            next_action = "continue"
            should_replan = False
        elif v >= 0.40 and consecutive_failures <= 1 and not stalled:
            decision = ReplanDecision.LOCAL_REPLAN
            diag = f"Plan health degraded (V={v}). Triggering LOCAL_REPLAN for failed subgoals."
            next_action = "local_replan: patch failed subgoals only, keep completed waves"
            should_replan = True
        else:
            decision = ReplanDecision.GLOBAL_REPLAN
            diag = f"Plan health critical (V={v}, consecutive_fails={consecutive_failures}). Triggering GLOBAL_REPLAN."
            next_action = "global_replan: halt waves, recompile mission"
            should_replan = True

        return PlanValidityReport(
            validity_score=v,
            decision=decision,
            passed_tasks=passed_tasks,
            failed_tasks=failed_tasks,
            total_tasks=total_tasks,
            invariant_breach=False,
            diagnostic=diag,
            should_replan=should_replan,
            next_action=next_action,
            failed_task_ids=list(failed_task_ids or []),
        )

    @staticmethod
    def evaluate_wave(
        wave_task_results: dict[str, bool],
        cumulative_passed: int,
        cumulative_failed: int,
        total_tasks: int,
        *,
        consecutive_failures: int = 0,
        invariant_breach: bool = False,
    ) -> PlanValidityReport:
        """After-each-wave hook: fold one wave's results into the cumulative score.

        wave_task_results maps task_id -> success. Failed ids are carried on
        the report so the orchestrator can local-replan exactly those subgoals.
        """
        failed_ids = [tid for tid, ok in wave_task_results.items() if not ok]
        wave_pass = sum(1 for ok in wave_task_results.values() if ok)
        wave_fail = len(wave_task_results) - wave_pass
        return PlanValidityMonitor.evaluate(
            passed_tasks=cumulative_passed + wave_pass,
            failed_tasks=cumulative_failed + wave_fail,
            total_tasks=total_tasks,
            invariant_breach=invariant_breach,
            consecutive_failures=consecutive_failures,
            failed_task_ids=failed_ids,
        )
