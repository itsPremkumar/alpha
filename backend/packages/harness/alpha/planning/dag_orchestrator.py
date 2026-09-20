"""Dynamic DAG Workflow Compiler and Multi-Wave Orchestrator.

Translates CognitiveMetaPlanner plans into parallel execution DAGs,
scheduling independent wave tasks (e.g. Frontend in Worktree A, Backend in Worktree B)
concurrently and synchronizing with project Kanban and goals.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

from alpha.planning.meta_planner import CognitiveMetaPlanner, MetaPlan, MetaPlanTask

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class DagTaskNode:
    task_id: str
    wave: int
    objective: str
    assignee: str
    dependencies: list[str] = field(default_factory=list)
    worktree_path: str | None = None
    expected_artifact: str | None = None
    status: str = "pending"  # pending, ready, running, completed, failed, blocked
    result: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DagSchedule:
    dag_id: str
    prompt: str
    paradigm: str
    swarm_mode: str
    risk_tier: str
    total_waves: int
    waves: dict[int, list[DagTaskNode]] = field(default_factory=dict)
    all_nodes: dict[str, DagTaskNode] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dag_id": self.dag_id,
            "prompt": self.prompt,
            "paradigm": self.paradigm,
            "swarm_mode": self.swarm_mode,
            "risk_tier": self.risk_tier,
            "total_waves": self.total_waves,
            "waves": {str(k): [n.to_dict() for n in v] for k, v in self.waves.items()},
            "nodes": {k: v.to_dict() for k, v in self.all_nodes.items()},
            "created_at": self.created_at,
        }

    def ready_tasks_for_wave(self, wave: int) -> list[DagTaskNode]:
        """Return tasks in the given wave whose dependencies are all completed."""
        candidates = self.waves.get(wave, [])
        ready = []
        for task in candidates:
            if task.status in ("completed", "running"):
                continue
            deps_met = all(
                self.all_nodes.get(d) and self.all_nodes[d].status == "completed"
                for d in task.dependencies
            )
            if deps_met:
                task.status = "ready"
                ready.append(task)
            else:
                task.status = "blocked"
        return ready


class DynamicDagOrchestrator:
    """Compiles and executes dynamic multi-wave DAG workflows from meta-plans."""

    def __init__(self) -> None:
        self._schedules: dict[str, DagSchedule] = {}

    def compile_plan_to_dag(self, prompt: str, meta_plan: MetaPlan | None = None) -> DagSchedule:
        """Compile a goal or meta-plan into an executable multi-wave DAG schedule."""
        plan = meta_plan or CognitiveMetaPlanner.evaluate_and_plan(prompt=prompt)
        dag_id = f"dag-{uuid.uuid4().hex[:8]}"

        waves: dict[int, list[DagTaskNode]] = {}
        all_nodes: dict[str, DagTaskNode] = {}

        for task in plan.execution_waves:
            node = DagTaskNode(
                task_id=task.task_id,
                wave=task.wave,
                objective=task.objective,
                assignee=task.assignee,
                dependencies=list(task.dependencies),
                worktree_path=task.worktree_path,
                expected_artifact=task.expected_artifact,
                status="ready" if task.wave == 1 and not task.dependencies else "pending",
            )
            waves.setdefault(task.wave, []).append(node)
            all_nodes[node.task_id] = node

        swarm_val = plan.decision.swarm_mode.value if plan.decision.swarm_mode else "none"
        total_w = max(waves.keys()) if waves else 0

        schedule = DagSchedule(
            dag_id=dag_id,
            prompt=prompt,
            paradigm=plan.decision.paradigm.value,
            swarm_mode=swarm_val,
            risk_tier=plan.decision.risk_tier,
            total_waves=total_w,
            waves=waves,
            all_nodes=all_nodes,
        )

        self._schedules[dag_id] = schedule
        logger.info(
            "Compiled DAG %s with %d tasks across %d waves for '%s'",
            dag_id,
            len(all_nodes),
            total_w,
            prompt[:40],
        )
        return schedule

    def get_schedule(self, dag_id: str) -> DagSchedule | None:
        return self._schedules.get(dag_id)

    def advance_wave(
        self,
        dag_id: str,
        wave: int,
        worker_executor: Callable[[DagTaskNode], str] | None = None,
    ) -> list[DagTaskNode]:
        """Execute or mark all ready tasks in a wave."""
        schedule = self.get_schedule(dag_id)
        if not schedule:
            raise KeyError(f"DAG '{dag_id}' not found.")

        ready_tasks = schedule.ready_tasks_for_wave(wave)
        for task in ready_tasks:
            task.status = "running"
            task.started_at = _now()
            if worker_executor:
                try:
                    out = worker_executor(task)
                    task.result = out
                    task.status = "completed"
                except Exception as e:
                    task.result = f"Failed: {e}"
                    task.status = "failed"
            else:
                # Mock or manual completion
                task.result = f"Completed wave {wave} task: {task.objective}"
                task.status = "completed"
            task.completed_at = _now()

        return ready_tasks


_DEFAULT_ORCHESTRATOR: DynamicDagOrchestrator | None = None


def get_dag_orchestrator() -> DynamicDagOrchestrator:
    global _DEFAULT_ORCHESTRATOR
    if _DEFAULT_ORCHESTRATOR is None:
        _DEFAULT_ORCHESTRATOR = DynamicDagOrchestrator()
    return _DEFAULT_ORCHESTRATOR
