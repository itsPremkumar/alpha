"""Continuous Goal-Driven Autonomous Execution Runner with Self-Healing."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from alpha.harness.continuous.loop_detector import ToolLoopDetector
from alpha.harness.continuous.models import Goal, Milestone
from alpha.harness.continuous.store import GoalStore, get_goal_store
from alpha.safety.guard import get_safety_guard
from alpha.trajectory.store import get_trajectory_store

logger = logging.getLogger(__name__)


class ContinuousGoalRunner:
    """Executes goals in an autonomous, continuous loop with milestone verification and self-healing."""

    def __init__(self, store: GoalStore | None = None):
        self.store = store or get_goal_store()
        self.safety = get_safety_guard()
        self.trajectory = get_trajectory_store()
        self.loop_detector = ToolLoopDetector()

    def start_goal(
        self,
        title: str,
        description: str = "",
        max_iterations: int = 100,
    ) -> Goal:
        """Initialize and validate a goal, automatically provisioning initial milestones if needed."""
        # 1. Ethical & Safety Guardrails Check
        safety_decision = self.safety.evaluate_goal(f"{title}: {description}")
        if not safety_decision.allowed:
            raise PermissionError(f"Safety Violation: {safety_decision.reason}")

        goal = self.store.create_goal(title=title, description=description, max_iterations=max_iterations)

        # Auto-provision initial milestones
        m1 = self.store.add_milestone(goal.goal_id, "Analyze environment and dependencies", "Inspect codebase and state")
        m2 = self.store.add_milestone(
            goal.goal_id,
            "Implement core solution",
            "Execute changes and core logic",
            dependencies=[m1.milestone_id] if m1 else None,
        )
        self.store.add_milestone(
            goal.goal_id,
            "Verify acceptance and quality gates",
            "Run automated tests and verify criteria",
            dependencies=[m2.milestone_id] if m2 else None,
        )

        self.store.update_goal_status(goal.goal_id, "executing", strategy_note="Goal started with 3 auto-decomposed milestones.")
        return goal

    def get_next_runnable_milestone(self, goal: Goal) -> Milestone | None:
        """Find the next milestone whose dependencies are all verified."""
        verified_ids = {m.milestone_id for m in goal.milestones.values() if m.status == "verified"}
        for ms in goal.milestones.values():
            if ms.status in ("pending", "in_progress"):
                if all(dep in verified_ids for dep in ms.dependencies):
                    return ms
        return None

    def step(
        self,
        goal_id: str,
        executor_fn: Callable[[Milestone], tuple[bool, str, str]] | None = None,
    ) -> dict[str, Any]:
        """Execute a single autonomous progress turn on the goal."""
        goal = self.store.get_goal(goal_id)
        if not goal:
            return {"status": "error", "error": f"Goal '{goal_id}' not found."}

        if goal.status in ("achieved", "paused", "blocked"):
            return {"status": "ok", "goal_status": goal.status, "message": f"Goal is {goal.status}."}

        goal.iteration += 1
        if goal.iteration > goal.max_iterations:
            self.store.update_goal_status(goal.goal_id, "blocked", "Max iterations exceeded.")
            return {"status": "blocked", "reason": "Max iterations reached."}

        # Select next milestone
        milestone = self.get_next_runnable_milestone(goal)
        if not milestone:
            # Check if all milestones are verified
            all_verified = all(m.status == "verified" for m in goal.milestones.values())
            if all_verified and goal.milestones:
                self.store.update_goal_status(goal.goal_id, "achieved", "All milestones successfully verified.")
                return {"status": "achieved", "goal_id": goal_id, "iteration": goal.iteration}
            else:
                self.store.update_goal_status(goal.goal_id, "blocked", "Unresolved milestone dependencies.")
                return {"status": "blocked", "reason": "No runnable milestones."}

        # Transition milestone to in_progress
        milestone.status = "in_progress"
        milestone.attempts += 1
        goal.current_milestone_id = milestone.milestone_id

        # Execute turn
        if executor_fn:
            success, output, error = executor_fn(milestone)
        else:
            # Default self-contained mock step
            success = True
            output = f"Executed step for milestone: {milestone.title}"
            error = ""

        # Self-healing and strategy adaptation
        if success:
            self.store.update_milestone_status(
                goal_id=goal.goal_id,
                milestone_id=milestone.milestone_id,
                status="verified",
                evidence=output,
            )
            step_status = "success"
        else:
            milestone.strategy_history.append(f"Attempt {milestone.attempts} failed: {error}. Adapting strategy.")
            self.store.update_goal_status(
                goal_id=goal.goal_id,
                status="adapting",
                strategy_note=f"Pivoting strategy on milestone '{milestone.title}' due to: {error}",
            )
            if milestone.attempts >= milestone.max_attempts:
                self.store.update_milestone_status(goal.goal_id, milestone.milestone_id, "failed", error=error)
            step_status = "failure"

        # Check for repetitive tool loops
        loop_res = self.loop_detector.record_and_evaluate(
            tool_name="milestone_executor",
            args={"milestone_id": milestone.milestone_id, "attempts": milestone.attempts},
            result=output if success else error,
            success=success,
        )
        if loop_res.is_loop:
            self.store.update_goal_status(
                goal_id=goal.goal_id,
                status="adapting",
                strategy_note=f"Tool-Loop Breaker: {loop_res.recommendation}",
            )

        # Record trajectory step
        self.trajectory.record_step(
            goal_id=goal.goal_id,
            step_index=goal.iteration,
            thought=f"Working towards milestone: {milestone.title}",
            tool_name="milestone_executor",
            tool_input={"milestone_id": milestone.milestone_id, "attempts": milestone.attempts},
            tool_output=output,
            milestone_id=milestone.milestone_id,
            status=step_status,
            error=error,
        )

        return {
            "status": "ok",
            "goal_id": goal_id,
            "iteration": goal.iteration,
            "milestone_id": milestone.milestone_id,
            "milestone_status": milestone.status,
            "success": success,
        }

    def run_until_complete(
        self,
        goal_id: str,
        max_steps: int = 20,
        executor_fn: Callable[[Milestone], tuple[bool, str, str]] | None = None,
    ) -> Goal:
        """Run continuous autonomous loop until goal is achieved or steps exhausted."""
        for _ in range(max_steps):
            res = self.step(goal_id, executor_fn=executor_fn)
            if res.get("status") in ("achieved", "blocked", "paused", "error"):
                break
        return self.store.get_goal(goal_id)  # type: ignore[return-value]


_global_goal_runner = ContinuousGoalRunner()


def get_goal_runner() -> ContinuousGoalRunner:
    return _global_goal_runner
