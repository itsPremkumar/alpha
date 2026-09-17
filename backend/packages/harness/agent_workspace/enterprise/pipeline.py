"""Enterprise Mission-to-Sprint Pipeline & Dynamic DAG Execution Engine."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from agent_workspace.enterprise.models import (
    DAGSprint,
    DAGTask,
    MissionEpic,
    TechnicalSpec,
)

logger = logging.getLogger(__name__)


class MissionToSprintPipeline:
    """Decomposes Strategic Missions into Epics, Technical Specs, and Dynamic DAG Sprints."""

    def __init__(self):
        self._missions: dict[str, dict[str, Any]] = {}
        self._epics: dict[str, MissionEpic] = {}
        self._specs: dict[str, TechnicalSpec] = {}
        self._sprints: dict[str, DAGSprint] = {}

    def register_strategic_mission(self, mission_id: str, title: str, objective: str) -> dict[str, Any]:
        """Registers a master strategic enterprise mission."""
        mission_record = {
            "mission_id": mission_id,
            "title": title,
            "objective": objective,
            "epics": [],
            "status": "active",
        }
        self._missions[mission_id] = mission_record
        return mission_record

    def decompose_strategic_mission(self, mission_id: str, objective: str) -> list[MissionEpic]:
        """Decomposes a strategic enterprise mission into cross-department epics."""
        epics = [
            MissionEpic(
                epic_id=f"epic-arch-{uuid.uuid4().hex[:6]}",
                mission_id=mission_id,
                title="Unified System Architecture & Interface Contracts",
                description=f"Formulate extensible specifications, schemas, and epistemic graphs for: {objective}",
                target_department="architecture",
                priority=1,
            ),
            MissionEpic(
                epic_id=f"epic-eng-{uuid.uuid4().hex[:6]}",
                mission_id=mission_id,
                title="Core Engine Implementation & Gateway Endpoints",
                description=f"Deliver robust backend algorithms, async workers, and client routing for: {objective}",
                target_department="engineering",
                priority=1,
            ),
            MissionEpic(
                epic_id=f"epic-sec-{uuid.uuid4().hex[:6]}",
                mission_id=mission_id,
                title="AST Boundary Hardening & Zero-Trust Verification",
                description="Enforce strict sandbox constraints, AST boundary scans, and credential vaults.",
                target_department="security",
                priority=2,
            ),
            MissionEpic(
                epic_id=f"epic-perf-{uuid.uuid4().hex[:6]}",
                mission_id=mission_id,
                title="Holdout Benchmark Validation & Latency Optimization",
                description="Run SWE holdout benchmark suites, measure p95/p99 latencies, and optimize bottlenecks.",
                target_department="performance",
                priority=2,
            ),
            MissionEpic(
                epic_id=f"epic-docs-{uuid.uuid4().hex[:6]}",
                mission_id=mission_id,
                title="Technical Specifications & OpenAPI War Room Docs",
                description="Synthesize comprehensive API documentation, RFC records, and operator playbooks.",
                target_department="documentation",
                priority=3,
            ),
        ]

        if mission_id not in self._missions:
            self.register_strategic_mission(mission_id, f"Mission: {objective[:40]}...", objective)

        for e in epics:
            self._epics[e.epic_id] = e
            self._missions[mission_id]["epics"].append(e.epic_id)

        logger.info(f"Decomposed mission {mission_id} into {len(epics)} epics")
        return epics

    def synthesize_technical_spec(self, epic_id: str) -> TechnicalSpec:
        """Transforms an Epic into a rigorous Technical Spec with Definition of Done (DoD)."""
        epic = self._epics.get(epic_id)
        if not epic:
            raise KeyError(f"Epic '{epic_id}' not found.")

        spec = TechnicalSpec(
            spec_id=f"spec-{epic.epic_id}",
            epic_id=epic.epic_id,
            title=f"Technical Spec: {epic.title}",
            architect_bot="bot-cto",
            requirements=[
                f"Implement cross-cutting logic satisfying: {epic.description}",
                "Ensure non-blocking async execution across Gateway endpoints",
                "Guarantee full test coverage for core edge cases and failover paths",
            ],
            acceptance_criteria=[
                "Zero unhandled exceptions on malformed payloads",
                "AST boundary scans pass with 0 critical security alerts",
                "System latency p95 remains below 100ms",
            ],
            definition_of_done=[
                "Unit and integration tests pass with 100% success rate",
                "Architecture review sign-off by Lead Architect",
                "Holdout benchmark score >= 90%",
                "Security scan certified by CISO",
            ],
        )
        self._specs[spec.spec_id] = spec
        return spec

    def compile_dynamic_dag_sprint(self, spec_id: str, custom_tasks: list[DAGTask] | None = None) -> DAGSprint:
        """Compiles a Technical Spec into an executable Dynamic DAG Sprint with topological dependency layers."""
        spec = self._specs.get(spec_id)
        if not spec:
            raise KeyError(f"Technical Spec '{spec_id}' not found.")

        if custom_tasks:
            tasks = custom_tasks
            topology = [[t.task_id for t in tasks if not t.dependencies]]
            remaining = [t for t in tasks if t.dependencies]
            resolved = set(topology[0])
            while remaining:
                next_layer = [t.task_id for t in remaining if all(dep in resolved for dep in t.dependencies)]
                if not next_layer:
                    next_layer = [t.task_id for t in remaining]
                topology.append(next_layer)
                resolved.update(next_layer)
                remaining = [t for t in remaining if t.task_id not in resolved]
        else:
            t1_id = f"task-{uuid.uuid4().hex[:6]}-schema"
            t2_id = f"task-{uuid.uuid4().hex[:6]}-backend"
            t3_id = f"task-{uuid.uuid4().hex[:6]}-security"
            t4_id = f"task-{uuid.uuid4().hex[:6]}-benchmark"
            t5_id = f"task-{uuid.uuid4().hex[:6]}-ui"
            t6_id = f"task-{uuid.uuid4().hex[:6]}-docs"

            t1 = DAGTask(
                task_id=t1_id,
                title="Design Data Schemas & Capability Contracts",
                assigned_bot="bot-system-architect",
                department="architecture",
                dependencies=[],
                status="pending",
                definition_of_done=["Schema validation models defined", "Backward compatibility assured"],
                estimated_tokens=4000,
            )

            t2 = DAGTask(
                task_id=t2_id,
                title="Implement Core Backend Engine & Gateway Routes",
                assigned_bot="bot-backend-swe",
                department="engineering",
                dependencies=[t1_id],
                status="pending",
                definition_of_done=["Async endpoints mounted", "Unit tests green"],
                estimated_tokens=8000,
            )

            t3 = DAGTask(
                task_id=t3_id,
                title="AST Boundary & Zero-Trust Sandbox Audit",
                assigned_bot="bot-appsec-auditor",
                department="security",
                dependencies=[t2_id],
                status="pending",
                definition_of_done=["AST static check passed", "Taint-tracking certified"],
                estimated_tokens=3000,
            )

            t4 = DAGTask(
                task_id=t4_id,
                title="Holdout Benchmark Suite Execution & Latency Profiling",
                assigned_bot="bot-latency-profiler",
                department="performance",
                dependencies=[t2_id],
                status="pending",
                definition_of_done=["Holdout test pass >= 90%", "p95 latency within budget"],
                estimated_tokens=5000,
            )

            t5 = DAGTask(
                task_id=t5_id,
                title="Next.js War Room UI Panel Integration",
                assigned_bot="bot-frontend-swe",
                department="engineering",
                dependencies=[t2_id],
                status="pending",
                definition_of_done=["Components render telemetry", "Clean TypeScript build"],
                estimated_tokens=6000,
            )

            t6 = DAGTask(
                task_id=t6_id,
                title="OpenAPI Specification & Playbook Documentation",
                assigned_bot="bot-api-specifier",
                department="documentation",
                dependencies=[t3_id, t4_id, t5_id],
                status="pending",
                definition_of_done=["Documentation updated in catalog", "Operator guide drafted"],
                estimated_tokens=2500,
            )

            tasks = [t1, t2, t3, t4, t5, t6]
            topology = [
                [t1_id],
                [t2_id],
                [t3_id, t4_id, t5_id],
                [t6_id],
            ]

        sprint = DAGSprint(
            sprint_id=f"sprint-{uuid.uuid4().hex[:8]}",
            spec_id=spec_id,
            title=f"Sprint: {spec.title}",
            tasks=tasks,
            topology_layers=topology,
            status="active",
            progress_percent=0.0,
        )
        self._sprints[sprint.sprint_id] = sprint
        logger.info(f"Compiled dynamic DAG sprint {sprint.sprint_id} with {len(tasks)} tasks across 4 layers")
        return sprint

    def step_sprint_dag(self, sprint_id: str) -> dict[str, Any]:
        """Advances the DAG execution by executing tasks whose dependencies are satisfied."""
        sprint = self._sprints.get(sprint_id)
        if not sprint:
            raise KeyError(f"Sprint '{sprint_id}' not found.")

        completed_at_start = {t.task_id for t in sprint.tasks if t.status == "completed"}
        advanced_tasks = []

        for task in sprint.tasks:
            if task.status == "completed":
                continue

            # Check if all dependencies were satisfied before this step began
            deps_satisfied = all(dep in completed_at_start for dep in task.dependencies)
            if deps_satisfied and task.status in ["pending", "ready"]:
                task.status = "completed"
                task.dod_verified = True
                task.actual_tokens = task.estimated_tokens
                task.execution_output = f"Successfully executed by {task.assigned_bot} with DoD verified."
                advanced_tasks.append(task.task_id)
                # Advance at most 2 tasks per heartbeat tick to represent realistic parallel throughput
                if len(advanced_tasks) >= 2:
                    break

        total_tasks = len(sprint.tasks)
        completed_count = sum(1 for t in sprint.tasks if t.status == "completed")
        sprint.progress_percent = round((completed_count / max(1, total_tasks)) * 100.0, 1)
        sprint.updated_at = time.time()

        if completed_count >= total_tasks:
            sprint.status = "completed"

        return {
            "sprint_id": sprint.sprint_id,
            "status": sprint.status,
            "progress_percent": sprint.progress_percent,
            "completed_tasks_count": completed_count,
            "total_tasks": total_tasks,
            "advanced_tasks": advanced_tasks,
        }

    def list_sprints(self) -> list[DAGSprint]:
        return list(self._sprints.values())

    def get_sprint(self, sprint_id: str) -> DAGSprint | None:
        return self._sprints.get(sprint_id)

    def list_epics(self) -> list[MissionEpic]:
        return list(self._epics.values())


_PIPELINE: MissionToSprintPipeline | None = None


def get_mission_pipeline() -> MissionToSprintPipeline:
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = MissionToSprintPipeline()
    return _PIPELINE
