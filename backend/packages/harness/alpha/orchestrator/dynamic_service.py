"""Host-owned dynamic workflow service.

This module is the opt-in orchestration plane that joins the existing intent,
discovery, goal, and DWE subsystems.  It deliberately does **not** replace
``RunManager`` or create a second Gateway run lifecycle.  A caller supplies an
already-authorized execution context and receives a correlated workflow run;
the parent agent/group service remains responsible for parent cancellation,
streaming, and terminal status.

The service is synchronous by design because the harness executor contract is
synchronous.  Async hosts must invoke it through their dedicated worker/thread
boundary and must not assume that cancelling an ``await`` stops a child node.
Every dynamic choice is returned with an explicit decision record so the
runtime can be audited without trusting model prose.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any

from alpha.orchestration.intent import ComplexityMode, IntentAnalysis, parse_intent
from alpha.orchestrator.executors import DIGEST_EXECUTOR, get_executor_registry
from alpha.orchestrator.loop import ExecutionKernel, HandoffContract
from alpha.workflow.dynamic_assembler import AssembledResources, DynamicResourceAssembler
from alpha.workflow.dynamic_bridge import DynamicWorkflowBridge
from alpha.workflow.dynamic_decomposer import DynamicDecomposer, DynamicGoal, get_dynamic_decomposer
from alpha.workflow.dynamic_perception import DynamicPerceptionEngine, get_dynamic_perception_engine
from alpha.workflow.models import WorkflowDefinition, WorkflowRun
from alpha.workflow.registry import REGISTRY_KINDS, RegistryUnavailable, get_workflow_registry


@dataclass(frozen=True)
class DynamicRequest:
    """Validated inputs for one host-authorized dynamic workflow turn."""

    prompt: str
    mode: str = "normal"
    context: dict[str, Any] = field(default_factory=dict)
    initial_state: dict[str, Any] = field(default_factory=dict)
    max_steps: int = 40
    default_executor: str = DIGEST_EXECUTOR
    project_id: str | None = None


@dataclass(frozen=True)
class DynamicDecision:
    """One explainable planning/selection decision."""

    stage: str
    chosen: str
    candidates: tuple[str, ...] = ()
    scores: dict[str, float] = field(default_factory=dict)
    policy_ids: tuple[str, ...] = ()
    model_id: str | None = None
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "chosen": self.chosen,
            "candidates": list(self.candidates),
            "scores": dict(self.scores),
            "policy_ids": list(self.policy_ids),
            "model_id": self.model_id,
            "why": self.why,
        }


@dataclass
class DynamicPlan:
    """Compiled graph plus the observations that produced it."""

    prompt: str
    mode: str
    intent: dict[str, Any]
    rich_intent: dict[str, Any]
    goal: DynamicGoal | None = None
    resources: AssembledResources | None = None
    definition: WorkflowDefinition | None = None
    decisions: list[DynamicDecision] = field(default_factory=list)
    discovery: dict[str, Any] = field(default_factory=dict)
    unavailable: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "mode": self.mode,
            "intent": dict(self.intent),
            "rich_intent": dict(self.rich_intent),
            "goal": self.goal.to_dict() if self.goal else None,
            "resources": self.resources.to_dict() if self.resources else None,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "discovery": dict(self.discovery),
            "unavailable": list(self.unavailable),
        }


@dataclass
class DynamicServiceResult:
    """Result of compile-only or executed dynamic workflow work."""

    status: str
    plan: DynamicPlan
    run: WorkflowRun | None = None
    handoff: HandoffContract | None = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def run_id(self) -> str | None:
        return self.run.run_id if self.run else None

    @property
    def workflow_id(self) -> str | None:
        return self.plan.definition.id if self.plan.definition else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "reason": self.reason,
            "plan": self.plan.to_dict(),
            "run": self.run.model_dump(mode="json") if self.run else None,
            "handoff": self.handoff.to_dict() if self.handoff else None,
            "metadata": dict(self.metadata),
        }


class DynamicWorkflowService:
    """Compose existing Alpha seams without inventing a parallel runtime.

    The service is intentionally injectable.  Hosts can provide their own
    kernel, assembler, or executor registry; tests can use an empty registry and
    observe the exact honest failure instead of accidentally receiving a digest
    success.
    """

    MODES = ("normal", "bot")

    def __init__(
        self,
        kernel: ExecutionKernel | None = None,
        *,
        perception: DynamicPerceptionEngine | None = None,
        decomposer: DynamicDecomposer | None = None,
        assembler: DynamicResourceAssembler | None = None,
        workflow_registry: Any | None = None,
    ) -> None:
        self.kernel = kernel or ExecutionKernel()
        self.perception = perception or get_dynamic_perception_engine()
        self.decomposer = decomposer or get_dynamic_decomposer()
        self.assembler = assembler or DynamicResourceAssembler()
        self.registry = workflow_registry or get_workflow_registry()
        self._lock = threading.RLock()

    def discover(self, intent: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Read the five production registries and disclose failures.

        A broken registry is never treated as an empty registry.  Optional
        capability gaps are returned as ``unavailable`` strings; required DWE
        execution still fails at the node seam when no executor is bound.
        """
        report: dict[str, Any] = {"registries": {}, "descriptors": {}}
        unavailable: list[str] = []
        for kind in REGISTRY_KINDS:
            try:
                registry = self.registry.registry(kind)
                health = registry.health()
                report["registries"][kind] = health.model_dump(mode="json")
                # Keep the planning payload bounded.  The full registry remains
                # available through its own read-only API.
                descriptors = registry.list()
                report["descriptors"][kind] = [descriptor.model_dump(mode="json") for descriptor in descriptors[:100]]
                if health.status != "ok":
                    unavailable.append(f"{kind}: {health.error or 'registry unavailable'}")
            except (RegistryUnavailable, KeyError, AttributeError, TypeError) as exc:
                detail = f"{type(exc).__name__}: {exc}"
                report["registries"][kind] = {
                    "registry": kind,
                    "status": "unavailable",
                    "count": None,
                    "error": detail,
                    "evidence_kind": "measured",
                }
                unavailable.append(f"{kind}: {detail}")
            except Exception as exc:  # defensive: one registry cannot break planning
                detail = f"{type(exc).__name__}: {exc}"
                report["registries"][kind] = {
                    "registry": kind,
                    "status": "unavailable",
                    "count": None,
                    "error": detail,
                    "evidence_kind": "measured",
                }
                unavailable.append(f"{kind}: {detail}")

        required = ["dynamic_workflow_engine"]
        for capability in required:
            descriptors = report["descriptors"].get("capabilities", [])
            if not any(item.get("id") == capability and item.get("availability") == "available" for item in descriptors):
                unavailable.append(f"capability:{capability}")

        # Surface intent requirements in the report without pretending that a
        # discovered descriptor is a live connection.
        report["required_by_intent"] = {
            "tools": bool(intent.get("need_tool_selection")),
            "skills": bool(intent.get("need_skill_creation") or intent.get("need_skill_download")),
            "mcp": bool(intent.get("need_mcp_selection")),
            "subagents": bool(intent.get("need_subagents")),
        }
        return report, unavailable

    def _topology(self, rich: IntentAnalysis, legacy: dict[str, Any]) -> DynamicDecision:
        complexity = rich.complexity.mode
        if complexity is ComplexityMode.RECURRING_AUTOMATION:
            chosen = "automation"
            candidates = ("automation", "sequential", "dag-waves")
        elif complexity is ComplexityMode.AUTONOMOUS_SWARM:
            chosen = "dynamic-goal"
            candidates = ("dynamic-goal", "dag-waves", "sequential")
        elif complexity is ComplexityMode.STRUCTURED_PLAN:
            chosen = "dag-waves"
            candidates = ("dag-waves", "dynamic-goal", "sequential")
        else:
            chosen = "sequential"
            candidates = ("sequential", "dag-waves", "dynamic-goal")
        return DynamicDecision(
            stage="topology_selection",
            chosen=chosen,
            candidates=candidates,
            scores={name: (1.0 if name == chosen else 0.0) for name in candidates},
            policy_ids=("deterministic_complexity_precedence",),
            why=rich.complexity.reason,
        )

    def plan(
        self,
        request: DynamicRequest,
        *,
        provision: bool = True,
    ) -> DynamicPlan:
        """Perceive, discover, decompose, assemble, and compile one graph."""
        if request.mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}, got {request.mode!r}")
        if not request.prompt.strip():
            raise ValueError("prompt must not be empty")
        if request.max_steps < 1 or request.max_steps > 500:
            raise ValueError("max_steps must be between 1 and 500")

        with self._lock:
            rich = parse_intent(request.prompt)
            legacy_obj = self.perception.perceive(request.prompt, request.context)
            legacy = legacy_obj.to_dict()
            discovery, unavailable = self.discover(legacy)
            decisions = [
                DynamicDecision(
                    stage="intent_perception",
                    chosen=legacy.get("intent_type", "general"),
                    candidates=(legacy.get("intent_type", "general"),),
                    policy_ids=("dynamic_perception_v1",),
                    why="deterministic intent/domain classifier",
                ),
                self._topology(rich, legacy),
            ]

            # A recurring prompt is represented honestly as an automation
            # topology.  The existing scheduler remains the execution owner;
            # this service does not silently create a second cron loop.
            if decisions[1].chosen == "automation":
                goal = None
                resources = None
                definition = None
                unavailable.append("automation: scheduler handoff is host-owned; no workflow graph was started")
                return DynamicPlan(
                    prompt=request.prompt,
                    mode=request.mode,
                    intent=legacy,
                    rich_intent=rich.to_dict(),
                    decisions=decisions,
                    discovery=discovery,
                    unavailable=unavailable,
                )

            goal = self.decomposer.decompose(legacy_obj, request.prompt)
            resources = self.assembler.assemble(
                goal,
                request.prompt,
                # A named bot workflow binds the server-validated profile in
                # the bridge; do not provision a second random specialist
                # roster as a side effect of planning.
                provision=provision and not (request.mode == "bot" and request.context.get("bot_name")),
            )
            bridge = DynamicWorkflowBridge(
                engine=self.kernel.engine,
                kernel=self.kernel,
                execution_label="local_digest_projection" if request.default_executor == DIGEST_EXECUTOR else "external",
                owner_id=request.context.get("owner_id"),
                mode=request.mode,
                bot_name=request.context.get("bot_name") if request.mode == "bot" else None,
                require_compensation_receipt=True,
            )
            definition = bridge.build_workflow_definition(
                goal,
                resources,
                default_executor=request.default_executor,
            )
            definition.owner_id = request.context.get("owner_id")
            self.kernel.engine.register_definition(definition)
            decisions.append(
                DynamicDecision(
                    stage="resource_assembly",
                    chosen="assembled",
                    candidates=("configured_registry", "honest_unavailable"),
                    policy_ids=("registry_discovery_v1",),
                    why="resources are selected from measured registry descriptors; unavailable entries remain explicit",
                )
            )
            return DynamicPlan(
                prompt=request.prompt,
                mode=request.mode,
                intent=legacy,
                rich_intent=rich.to_dict(),
                goal=goal,
                resources=resources,
                definition=definition,
                decisions=decisions,
                discovery=discovery,
                unavailable=unavailable,
            )

    def execute(
        self,
        request: DynamicRequest,
        *,
        node_runner: Any | None = None,
        auto_execute: bool = True,
        provision: bool = True,
    ) -> DynamicServiceResult:
        """Compile and optionally execute through the shared kernel.

        No domain work is claimed when only the digest executor is bound.  The
        run can still complete as a *graph projection*; callers must inspect
        ``metadata.execution_label`` and ``metadata.acceptance_passed``.
        """
        plan = self.plan(request, provision=provision)
        if plan.definition is None:
            return DynamicServiceResult(
                status="unavailable",
                plan=plan,
                reason="; ".join(plan.unavailable) or "workflow could not be compiled",
            )
        if not auto_execute:
            return DynamicServiceResult(status="compiled", plan=plan, metadata={"auto_execute": False})

        runner = node_runner if node_runner is not None else get_executor_registry().build_runner()
        state = {
            "objective": request.prompt[:4000],
            "prompt": request.prompt[:4000],
            **dict(request.initial_state),
        }
        try:
            run = self.kernel.start_run(
                plan.definition.id,
                initial_state=state,
                mode=request.mode,
                owner_id=plan.definition.owner_id,
            )
            for index, decision in enumerate(plan.decisions):
                self.kernel.engine.events.emit(
                    "decision_recorded",
                    run.run_id,
                    decision_ref=f"{run.run_id}:decision:{index}",
                    graph_version=run.graph_version,
                    **decision.to_dict(),
                )
            run, waves = self.kernel.run_to_completion(
                run.run_id,
                max_waves=request.max_steps,
                node_runner=runner,
            )
        except Exception as exc:
            return DynamicServiceResult(
                status="failed",
                plan=plan,
                reason=f"{type(exc).__name__}: {exc}",
                metadata={"execution_error": True},
            )

        handoff = self.kernel.handoff(run.run_id, to_mode=None)
        execution_label = "local_digest_projection" if request.default_executor == DIGEST_EXECUTOR else "external"
        acceptance_passed = run.status.value == "completed" and execution_label != "local_digest_projection"
        return DynamicServiceResult(
            status=run.status.value,
            plan=plan,
            run=run,
            handoff=handoff,
            reason="" if run.status.value == "completed" else (run.waiting_reason or "workflow did not complete"),
            metadata={
                "waves": waves,
                "execution_label": execution_label,
                "acceptance_passed": acceptance_passed,
                "acceptance_reason": (
                    "all bound executor nodes completed with evidence"
                    if acceptance_passed
                    else "digest projection completed graph mechanics only; no domain task was executed"
                    if execution_label == "local_digest_projection"
                    else "workflow did not complete with verified evidence"
                ),
                "node_runner_bound": runner is not None,
                "decisions": [decision.to_dict() for decision in plan.decisions],
                "discovery_unavailable": list(plan.unavailable),
            },
        )


_DEFAULT_SERVICE: DynamicWorkflowService | None = None
_DEFAULT_SERVICE_LOCK = threading.Lock()


def get_dynamic_workflow_service() -> DynamicWorkflowService:
    """Return the process-wide service seam used by hosts/tests."""
    global _DEFAULT_SERVICE
    with _DEFAULT_SERVICE_LOCK:
        if _DEFAULT_SERVICE is None:
            _DEFAULT_SERVICE = DynamicWorkflowService()
        return _DEFAULT_SERVICE


def set_dynamic_workflow_service(service: DynamicWorkflowService | None) -> None:
    """Inject/reset the service singleton explicitly."""
    global _DEFAULT_SERVICE
    with _DEFAULT_SERVICE_LOCK:
        _DEFAULT_SERVICE = service


def run_dynamic_turn(
    prompt: str,
    *,
    mode: str = "normal",
    context: dict[str, Any] | None = None,
    initial_state: dict[str, Any] | None = None,
    max_steps: int = 40,
    default_executor: str = DIGEST_EXECUTOR,
    service: DynamicWorkflowService | None = None,
    kernel: ExecutionKernel | None = None,
) -> DynamicServiceResult:
    """Convenience seam for normal and bot hosts.

    This function is intentionally separate from the legacy paradigm-only
    ``orchestrator.loop.run_turn`` so existing callers retain their exact
    behavior.  New hosts can opt into the full perception/discovery loop here.
    """
    active = service or get_dynamic_workflow_service()
    if kernel is not None and service is None:
        active = DynamicWorkflowService(kernel=kernel)
    request = DynamicRequest(
        prompt=prompt,
        mode=mode,
        context=dict(context or {}),
        initial_state=dict(initial_state or {}),
        max_steps=max_steps,
        default_executor=default_executor,
    )
    return active.execute(request, provision=True)


def fingerprint(value: Any) -> str:
    """Stable, non-secret fingerprint for idempotency/correlation callers."""
    payload = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "DynamicDecision",
    "DynamicPlan",
    "DynamicRequest",
    "DynamicServiceResult",
    "DynamicWorkflowService",
    "fingerprint",
    "get_dynamic_workflow_service",
    "run_dynamic_turn",
    "set_dynamic_workflow_service",
]
