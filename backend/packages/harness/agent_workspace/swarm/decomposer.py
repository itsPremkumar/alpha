"""Swarm Task Decomposer: Translates high-level goals into executable DAG plans."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from agent_workspace.swarm.estimator import SwarmBenefitEstimator
from agent_workspace.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState


class SwarmTaskDecomposer:
    """Decomposes goals into a Directed Acyclic Graph (DAG) of swarm tasks."""

    @classmethod
    def decompose(
        cls,
        goal: str,
        mode: SwarmMode = SwarmMode.AUTO,
        items: Sequence[str] | None = None,
        swarm_id: str | None = None,
        max_concurrency: int = 8,
    ) -> SwarmPlan:
        """Constructs a SwarmPlan with dependency-linked SwarmTaskNode instances."""
        sid = swarm_id or f"swm-{uuid.uuid4().hex[:8]}"

        # Resolve mode if AUTO
        if mode == SwarmMode.AUTO:
            decision = SwarmBenefitEstimator.estimate(goal, items=items)
            mode = decision.mode if decision.should_swarm else SwarmMode.PARALLEL

        tasks: dict[str, SwarmTaskNode] = {}

        if mode in (SwarmMode.MAP_REDUCE, SwarmMode.PARALLEL) and items and len(items) > 0:
            tasks = cls._decompose_map_reduce(goal, items)
        elif mode == SwarmMode.DEBATE:
            tasks = cls._decompose_debate(goal)
        elif mode in (SwarmMode.ENSEMBLE, SwarmMode.SCATTER_GATHER):
            tasks = cls._decompose_ensemble(goal)
        elif mode == SwarmMode.CODING_WORKTREE:
            tasks = cls._decompose_coding_worktree(goal)
        else:
            # Default Hierarchical decomposition
            tasks = cls._decompose_hierarchical(goal)

        plan = SwarmPlan(
            swarm_id=sid,
            goal=goal,
            mode=mode,
            tasks=tasks,
            max_concurrency=max_concurrency,
        )

        cls._compute_critical_path_and_speedup(plan)
        return plan

    @classmethod
    def _decompose_map_reduce(cls, goal: str, items: Sequence[str]) -> dict[str, SwarmTaskNode]:
        tasks: dict[str, SwarmTaskNode] = {}
        map_task_ids: list[str] = []

        for idx, item in enumerate(items, 1):
            tid = f"task-map-{idx}"
            map_task_ids.append(tid)
            tasks[tid] = SwarmTaskNode(
                task_id=tid,
                objective=f"Process target item ({idx}/{len(items)}): {item} [Goal: {goal}]",
                dependencies=[],
                worker_type="ephemeral",
                state=TaskNodeState.PENDING,
            )

        reduce_id = "task-reduce"
        tasks[reduce_id] = SwarmTaskNode(
            task_id=reduce_id,
            objective=f"Reconcile, deduplicate, rank evidence and synthesize final report for: {goal}",
            dependencies=map_task_ids,
            worker_type="permanent_bot",
            assigned_worker="architect",
            state=TaskNodeState.PENDING,
        )
        return tasks

    @classmethod
    def _decompose_debate(cls, goal: str) -> dict[str, SwarmTaskNode]:
        return {
            "task-pro": SwarmTaskNode(
                task_id="task-pro",
                objective=f"Argue in favor and detail high-value upsides/strengths for: {goal}",
                dependencies=[],
                worker_type="ephemeral",
                state=TaskNodeState.PENDING,
            ),
            "task-con": SwarmTaskNode(
                task_id="task-con",
                objective=f"Analyze adversarial counterarguments, risks, and failure modes for: {goal}",
                dependencies=[],
                worker_type="ephemeral",
                state=TaskNodeState.PENDING,
            ),
            "task-evidence": SwarmTaskNode(
                task_id="task-evidence",
                objective=f"Gather empirical evidence, benchmark figures, and citations for: {goal}",
                dependencies=[],
                worker_type="ephemeral",
                state=TaskNodeState.PENDING,
            ),
            "task-judge": SwarmTaskNode(
                task_id="task-judge",
                objective=f"Synthesize competing arguments, resolve contradictions, and deliver balanced verdict on: {goal}",
                dependencies=["task-pro", "task-con", "task-evidence"],
                worker_type="permanent_bot",
                assigned_worker="ceo",
                state=TaskNodeState.PENDING,
            ),
        }

    @classmethod
    def _decompose_ensemble(cls, goal: str) -> dict[str, SwarmTaskNode]:
        return {
            "task-sol-1": SwarmTaskNode(
                task_id="task-sol-1",
                objective=f"Formulate approach 1 (Simplicity & Reliability focus) for: {goal}",
                dependencies=[],
                worker_type="ephemeral",
            ),
            "task-sol-2": SwarmTaskNode(
                task_id="task-sol-2",
                objective=f"Formulate approach 2 (Performance & High Scalability focus) for: {goal}",
                dependencies=[],
                worker_type="ephemeral",
            ),
            "task-sol-3": SwarmTaskNode(
                task_id="task-sol-3",
                objective=f"Formulate approach 3 (Zero-Dependency & Security focus) for: {goal}",
                dependencies=[],
                worker_type="ephemeral",
            ),
            "task-evaluator": SwarmTaskNode(
                task_id="task-evaluator",
                objective=f"Score and combine candidate approaches into an optimal synthesized strategy for: {goal}",
                dependencies=["task-sol-1", "task-sol-2", "task-sol-3"],
                worker_type="permanent_bot",
                assigned_worker="architect",
            ),
        }

    @classmethod
    def _decompose_coding_worktree(cls, goal: str) -> dict[str, SwarmTaskNode]:
        return {
            "task-spec": SwarmTaskNode(
                task_id="task-spec",
                objective=f"Define component interface contracts and architectural boundaries for: {goal}",
                dependencies=[],
                worker_type="permanent_bot",
                assigned_worker="architect",
            ),
            "task-backend": SwarmTaskNode(
                task_id="task-backend",
                objective=f"Implement backend logic, routers and schemas in isolated worktree for: {goal}",
                dependencies=["task-spec"],
                worker_type="ephemeral",
                worktree_path=".worktrees/backend-impl",
            ),
            "task-frontend": SwarmTaskNode(
                task_id="task-frontend",
                objective=f"Implement frontend components, state and UI in isolated worktree for: {goal}",
                dependencies=["task-spec"],
                worker_type="ephemeral",
                worktree_path=".worktrees/frontend-impl",
            ),
            "task-test": SwarmTaskNode(
                task_id="task-test",
                objective=f"Author unit and integration test fixtures in isolated worktree for: {goal}",
                dependencies=["task-backend", "task-frontend"],
                worker_type="ephemeral",
                worktree_path=".worktrees/testing-suite",
            ),
            "task-integrate": SwarmTaskNode(
                task_id="task-integrate",
                objective=f"Synthesize unified patch, resolve git conflicts, and verify clean test pass for: {goal}",
                dependencies=["task-test"],
                worker_type="permanent_bot",
                assigned_worker="coder",
            ),
        }

    @classmethod
    def _decompose_hierarchical(cls, goal: str) -> dict[str, SwarmTaskNode]:
        return {
            "task-research": SwarmTaskNode(
                task_id="task-research",
                objective=f"Deep research, requirements survey, and constraints for: {goal}",
                dependencies=[],
                worker_type="permanent_bot",
                assigned_worker="researcher",
            ),
            "task-arch": SwarmTaskNode(
                task_id="task-arch",
                objective=f"System architecture and component design for: {goal}",
                dependencies=["task-research"],
                worker_type="permanent_bot",
                assigned_worker="architect",
            ),
            "task-worker-a": SwarmTaskNode(
                task_id="task-worker-a",
                objective=f"Execute primary implementation stream for: {goal}",
                dependencies=["task-arch"],
                worker_type="ephemeral",
            ),
            "task-worker-b": SwarmTaskNode(
                task_id="task-worker-b",
                objective=f"Execute auxiliary/support implementation stream for: {goal}",
                dependencies=["task-arch"],
                worker_type="ephemeral",
            ),
            "task-qa": SwarmTaskNode(
                task_id="task-qa",
                objective=f"Independent quality assurance and red-team audit for: {goal}",
                dependencies=["task-worker-a", "task-worker-b"],
                worker_type="permanent_bot",
                assigned_worker="tester",
            ),
        }

    @classmethod
    def _compute_critical_path_and_speedup(cls, plan: SwarmPlan) -> None:
        """Calculates critical path using DAG topological distance."""
        if not plan.tasks:
            plan.critical_path_seconds = 0.0
            plan.estimated_speedup = 1.0
            return

        # Estimate nominal duration of 15s per node
        durations = {tid: 15.0 for tid in plan.tasks}
        earliest_finish: dict[str, float] = {}

        # Topological traversal
        visited = set()

        def get_finish(tid: str) -> float:
            if tid in earliest_finish:
                return earliest_finish[tid]
            node = plan.tasks.get(tid)
            if not node or not node.dependencies:
                fin = durations.get(tid, 15.0)
            else:
                max_dep = max(get_finish(d) for d in node.dependencies if d in plan.tasks) if node.dependencies else 0.0
                fin = max_dep + durations.get(tid, 15.0)
            earliest_finish[tid] = fin
            visited.add(tid)
            return fin

        for tid in plan.tasks:
            get_finish(tid)

        critical_path = max(earliest_finish.values()) if earliest_finish else 15.0
        total_serial = sum(durations.values())
        speedup = round(total_serial / max(critical_path, 1.0), 2)

        plan.critical_path_seconds = critical_path
        plan.estimated_speedup = max(speedup, 1.0)
