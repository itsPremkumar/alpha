"""Tests for the host-owned dynamic workflow service."""

from __future__ import annotations

import pytest

import alpha.orchestrator.executors as executors_module
from alpha.orchestrator.dynamic_service import DynamicRequest, DynamicWorkflowService
from alpha.orchestrator.loop import ExecutionKernel
from alpha.workflow.dynamic_assembler import AssembledResources
from alpha.workflow.dynamic_bridge import DynamicWorkflowBridge
from alpha.workflow.dynamic_decomposer import DynamicDecomposer
from alpha.workflow.dynamic_perception import DynamicPerceptionEngine
from alpha.workflow.models import WorkflowRunStatus
from alpha.workflow.registry.base import CapabilityDescriptor, RegistryHealth


class _Registry:
    def __init__(self, kind: str) -> None:
        self.name = kind

    def list(self) -> list[CapabilityDescriptor]:
        if self.name == "capabilities":
            return [
                CapabilityDescriptor(
                    id="dynamic_workflow_engine",
                    kind="engine",
                    availability="available",
                    source="test",
                    authority="test",
                    evidence_kind="measured",
                )
            ]
        return []

    def health(self) -> RegistryHealth:
        return RegistryHealth(registry=self.name, status="ok", count=len(self.list()), evidence_kind="measured")


class _Facade:
    def registry(self, kind: str) -> _Registry:
        return _Registry(kind)


class _Assembler:
    def assemble(self, goal, prompt: str, *, provision: bool = True) -> AssembledResources:
        return AssembledResources(
            goal_id=goal.goal_id,
            tools=["read_file"],
            metadata={"provisioned": provision},
        )


def _runner(node, run):
    return {
        "status": "completed",
        "output": {"node": node.id},
        "evidence": f"test executor completed {node.id}",
        "tokens_used": 0,
    }


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def test_service_runs_full_loop_with_decision_and_mode(monkeypatch):
    kernel = ExecutionKernel()
    service = DynamicWorkflowService(
        kernel=kernel,
        assembler=_Assembler(),
        workflow_registry=_Facade(),
    )
    result = service.execute(
        DynamicRequest(
            prompt="implement a feature and verify it",
            mode="bot",
            context={"owner_id": "owner-a"},
            default_executor="alpha.test.executor",
        ),
        node_runner=_runner,
    )

    assert result.status == WorkflowRunStatus.COMPLETED.value
    assert result.run is not None
    assert result.run.metrics["execution_mode"] == "bot"
    assert result.run.owner_id == "owner-a"
    assert result.metadata["acceptance_passed"] is True
    assert {item.stage for item in result.plan.decisions} >= {"intent_perception", "topology_selection", "resource_assembly"}
    assert result.handoff is not None
    assert result.handoff.mode_from == "bot"


def test_service_refuses_recurring_automation_without_scheduler_handoff():
    service = DynamicWorkflowService(
        kernel=ExecutionKernel(),
        assembler=_Assembler(),
        workflow_registry=_Facade(),
    )
    result = service.execute(
        DynamicRequest(prompt="run this every day and report the result"),
        auto_execute=True,
    )

    assert result.status == "unavailable"
    assert result.run is None
    assert any("scheduler" in item for item in result.plan.unavailable)


def test_service_does_not_claim_digest_projection_acceptance(monkeypatch):
    registry = executors_module.ExecutorRegistry()
    registry.register(executors_module.DIGEST_EXECUTOR, executors_module.local_digest_executor)
    monkeypatch.setattr(executors_module, "_REGISTRY", registry)

    service = DynamicWorkflowService(
        kernel=ExecutionKernel(),
        assembler=_Assembler(),
        workflow_registry=_Facade(),
    )
    result = service.execute(
        DynamicRequest(
            prompt="research and implement a workflow",
            default_executor=executors_module.DIGEST_EXECUTOR,
        )
    )

    assert result.status == WorkflowRunStatus.COMPLETED.value
    assert result.metadata["execution_label"] == "local_digest_projection"
    assert result.metadata["acceptance_passed"] is False
    assert "no domain task" in result.metadata["acceptance_reason"]


def test_bridge_dispatches_through_injected_kernel(monkeypatch):
    """A host bridge shares the kernel's claim and mode journal for every wave."""
    kernel = ExecutionKernel()
    calls: list[str] = []
    original_dispatch = kernel.dispatch

    def recording_dispatch(run_id, **kwargs):
        calls.append(run_id)
        return original_dispatch(run_id, **kwargs)

    monkeypatch.setattr(kernel, "dispatch", recording_dispatch)
    intent = DynamicPerceptionEngine().perceive("implement a small feature and verify it")
    goal = DynamicDecomposer().decompose(intent, intent.raw_prompt)
    resources = AssembledResources(goal_id=goal.goal_id, tools=["read_file"])

    bridge = DynamicWorkflowBridge(
        engine=kernel.engine,
        kernel=kernel,
        node_runner=_runner,
        execution_label="external",
    )
    result = bridge.execute_goal(goal, resources)

    assert result.status == WorkflowRunStatus.COMPLETED.value
    assert calls, "the bridge must dispatch through the injected shared kernel"
    run = kernel.engine.get_run(result.run_id)
    assert run is not None
    assert run.metrics["execution_mode"] == "normal"
    assert run.owner_id is None


def test_bridge_kernel_does_not_silently_fall_back_to_registry(monkeypatch):
    """An explicitly unbound bridge remains unbound even when a registry exists."""
    registry = executors_module.ExecutorRegistry()
    registry.register(executors_module.DIGEST_EXECUTOR, executors_module.local_digest_executor)
    monkeypatch.setattr(executors_module, "_REGISTRY", registry)

    intent = DynamicPerceptionEngine().perceive("implement a small feature and verify it")
    goal = DynamicDecomposer().decompose(intent, intent.raw_prompt)
    resources = AssembledResources(goal_id=goal.goal_id)
    kernel = ExecutionKernel()
    bridge = DynamicWorkflowBridge(engine=kernel.engine, kernel=kernel)

    result = bridge.execute_goal(goal, resources)

    assert result.status == "failed"
    assert result.completed_nodes == []
    assert result.metadata["node_runner_bound"] is False
    assert "no node_runner bound" in (result.error_summary or "")
