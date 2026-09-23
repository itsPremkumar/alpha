"""P1 execution kernel: claim -> dispatch -> handoff over the REAL DynamicWorkflowEngine.

This module composes ``alpha.workflow.runtime.DynamicWorkflowEngine`` through
its public API only — it never forks the engine. What the kernel adds is the
P1 policy the engine itself deliberately does not own:

- **claim** — per-run mutual exclusion, so concurrent step/patch/approval
  calls serialize and optimistic-concurrency (OCC) rejections via the patch
  layer are deterministic instead of racy (no lost updates).
- **dispatch** — one scheduling wave per call through an executor resolved
  from the live executor registry (``alpha.orchestrator.executors``); an empty
  registry passes ``None`` so the engine keeps its exact honest unbound
  refusal. After every wave the kernel applies **fail-closed**: a run left
  RUNNING with failed nodes is driven to FAILED and an honest
  ``workflow_failed`` event names the failed node ids (the engine stops
  scheduling those nodes but leaves the run RUNNING — reported as an engine
  gap, fixed here without touching ``runtime.py``).
- **handoff** — the section 13 cross-mode contract
  (objective/completed/findings/files/decisions/remaining) built from real run
  state and recorded as a ``handoff_created`` event; empty lists stay empty
  when nothing was recorded (files/decisions are not invented).
- **mode** — normal and bot mode share this one kernel; only node kinds and
  bound executors differ (DY-R4, section 13). The mode is journaled on the run
  (``metrics['execution_mode']`` + a ``run_mode_selected`` event) so replay can
  reconstruct it.
- **``run_turn``** — the module-level seam of section 6. P1 runs the fast path
  only: paradigm -> mode-mapped graph -> execute -> validate -> handoff.
  Perception/capability discovery/archetype policy land with P2; the seam and
  its honest refusal for unmapped paradigms exist now.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from alpha.orchestrator.executors import get_executor_registry
from alpha.orchestrator.mode_mapper import MODES, map_paradigm
from alpha.orchestrator.replay import replay_run
from alpha.workflow.events import WorkflowEvent
from alpha.workflow.models import (
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowPatch,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.runtime import DynamicWorkflowEngine

# Statuses the kernel treats as "stop dispatching" (mirrors the engine's own
# early-return set plus its fail-closed terminal states).
_TERMINAL_STATUSES = frozenset(
    {
        WorkflowRunStatus.COMPLETED,
        WorkflowRunStatus.FAILED,
        WorkflowRunStatus.CANCELLED,
        WorkflowRunStatus.BUDGET_EXHAUSTED,
        WorkflowRunStatus.WAITING_APPROVAL,
        WorkflowRunStatus.WAITING_EVENT,
        WorkflowRunStatus.SUSPENDED,
    }
)

_FAIL_CLOSED_STATUSES = frozenset({WorkflowRunStatus.PENDING, WorkflowRunStatus.RUNNING})


@dataclass(frozen=True)
class HandoffContract:
    """Section 13 cross-mode handoff payload, built only from real run state."""

    objective: str
    run_id: str
    status: str
    mode_from: str
    mode_to: str
    completed: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "run_id": self.run_id,
            "status": self.status,
            "mode_from": self.mode_from,
            "mode_to": self.mode_to,
            "completed": list(self.completed),
            "findings": list(self.findings),
            "files": list(self.files),
            "decisions": list(self.decisions),
            "remaining": list(self.remaining),
        }


@dataclass(frozen=True)
class TurnContext:
    """Caller inputs for the ``run_turn`` seam (all fields defaulted)."""

    mode: str = "normal"
    paradigm: str = "direct_agent"
    initial_state: dict[str, Any] | None = None
    max_waves: int = 50
    handoff_to: str | None = None


@dataclass(frozen=True)
class TurnOutcome:
    """Honest result of one orchestrated turn — never a fabricated success."""

    run_id: str | None
    workflow_id: str | None
    mode: str
    paradigm: str
    status: str
    waves: int = 0
    failed_nodes: tuple[str, ...] = ()
    reason: str = ""
    handoff: HandoffContract | None = None


class ExecutionKernel:
    """Drives one DynamicWorkflowEngine through claim -> dispatch -> handoff."""

    def __init__(self, engine: DynamicWorkflowEngine | None = None) -> None:
        self.engine = engine if engine is not None else DynamicWorkflowEngine()
        self._claims: dict[str, threading.Lock] = {}
        self._claims_guard = threading.Lock()

    # ------------------------------------------------------------------ claim

    def _lock_for(self, run_id: str) -> threading.Lock:
        with self._claims_guard:
            lock = self._claims.get(run_id)
            if lock is None:
                lock = threading.Lock()
                self._claims[run_id] = lock
            return lock

    @contextmanager
    def claim(self, run_id: str) -> Iterator[None]:
        """Exclusive claim on one run: every kernel mutation happens inside it."""
        lock = self._lock_for(run_id)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    # ------------------------------------------------------------------ start

    def start_run(
        self,
        workflow_id: str,
        *,
        initial_state: dict[str, Any] | None = None,
        mode: str = "normal",
    ) -> WorkflowRun:
        """Start a run and journal its execution mode (normal vs bot, section 13)."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        run = self.engine.start_run(workflow_id, initial_state=initial_state)
        with self.claim(run.run_id):
            run.metrics["execution_mode"] = mode
            self.engine.events.emit("run_mode_selected", run.run_id, mode=mode)
        return run

    # --------------------------------------------------------------- dispatch

    def dispatch(self, run_id: str) -> WorkflowRun:
        """Claim -> execute one scheduling wave -> fail-closed policy."""
        with self.claim(run_id):
            return self._dispatch_locked(run_id)

    def run_to_completion(self, run_id: str, max_waves: int = 50) -> tuple[WorkflowRun, int]:
        """Dispatch waves under one claim until terminal or no progress.

        Returns the run and the number of waves actually dispatched. Bounded by
        ``max_waves`` so an unbounded graph can never spin the kernel forever.
        """
        with self.claim(run_id):
            waves = 0
            while waves < max_waves:
                before = self._progress_signature(run_id)
                run = self._dispatch_locked(run_id)
                waves += 1
                if run.status in _TERMINAL_STATUSES:
                    return run, waves
                if self._progress_signature(run_id) == before:
                    # Honest stop: nothing changed and nothing is terminal
                    # (e.g. a waiting graph); callers see the real status.
                    return run, waves
            run = self.engine.get_run(run_id)
            if run is None:
                raise KeyError(f"Run '{run_id}' not found.")
            return run, waves

    def _dispatch_locked(self, run_id: str) -> WorkflowRun:
        runner = get_executor_registry().build_runner()
        run = self.engine.execute_step(run_id, node_runner=runner)
        self._fail_closed(run)
        return run

    def _progress_signature(self, run_id: str) -> tuple[Any, ...]:
        run = self.engine.get_run(run_id)
        if run is None:
            raise KeyError(f"Run '{run_id}' not found.")
        return (
            run.status,
            run.graph_version,
            len(run.completed_nodes),
            len(run.failed_nodes),
            tuple(sorted((nid, status.value) for nid, status in run.node_states.items())),
        )

    def _fail_closed(self, run: WorkflowRun) -> None:
        """Drive a run that left failed nodes to a terminal FAILED status."""
        if run.failed_nodes and run.status in _FAIL_CLOSED_STATUSES:
            run.status = WorkflowRunStatus.FAILED
            run.updated_at = datetime.now(UTC).isoformat()
            failed = sorted(set(run.failed_nodes))
            self.engine.events.emit(
                "workflow_failed",
                run.run_id,
                reason=(
                    f"fail-closed: {len(failed)} node(s) failed: {failed}; "
                    "see the node_failed events for the real per-node reasons"
                ),
            )

    # ------------------------------------------------------------------ patch

    def apply_patch(self, run_id: str, patch: WorkflowPatch) -> tuple[WorkflowGraph, Any]:
        """Apply a patch under the run's claim so OCC conflicts are deterministic."""
        with self.claim(run_id):
            return self.engine.apply_patch(run_id, patch)

    # -------------------------------------------------------------- approvals

    def resolve_approval(
        self, run_id: str, node_id: str, approved: bool, feedback: str = ""
    ) -> WorkflowRun:
        with self.claim(run_id):
            run = self.engine.resolve_approval(run_id, node_id, approved=approved, feedback=feedback)
            self._fail_closed(run)
            return run

    # ---------------------------------------------------------------- handoff

    def handoff(
        self,
        run_id: str,
        *,
        to_mode: str | None = None,
        objective: str | None = None,
        emit: bool = True,
    ) -> HandoffContract:
        """Build the section 13 handoff contract from real run state.

        ``files``/``decisions`` stay empty unless the run recorded real ones —
        the engine journals no DecisionRecords or file artifacts yet (reported
        gap), so they are honestly empty rather than invented.

        Runs under the run's claim so the contract is a consistent snapshot of
        run state (the ``handoff_created`` emit is a log mutation too).
        """
        with self.claim(run_id):
            run = self.engine.get_run(run_id)
            if run is None:
                raise KeyError(f"Run '{run_id}' not found.")
            definition = self.engine.get_definition(run.workflow_id)
            graph = self.engine.graphs.get(f"{run.workflow_id}:v{run.graph_version}")
            if graph is None and definition is not None:
                graph = definition.graph

            mode_from = str(run.metrics.get("execution_mode", "normal"))
            resolved_objective = objective
            if resolved_objective is None:
                state_objective = run.state.get("objective")
                resolved_objective = (
                    str(state_objective)
                    if state_objective is not None
                    else (definition.name if definition is not None else run.workflow_id)
                )

            findings: list[str] = []
            if graph is not None:
                for nid in run.completed_nodes:
                    node = graph.nodes.get(nid)
                    if node is not None:
                        findings.append(f"{nid}: {node.output!r}")

            remaining = sorted(
                set(run.failed_nodes)
                | {
                    nid
                    for nid, status in run.node_states.items()
                    if status.value not in ("succeeded", "skipped")
                }
            )
            contract = HandoffContract(
                objective=resolved_objective,
                run_id=run.run_id,
                status=run.status.value,
                mode_from=mode_from,
                mode_to=to_mode or mode_from,
                completed=list(run.completed_nodes),
                findings=findings,
                files=[],
                decisions=[],
                remaining=remaining,
            )
            if emit:
                payload = contract.to_dict()
                # ``emit(event_type, run_id, **payload)`` already carries the run
                # identity as the event's ``workflow_run_id`` field; the dict's
                # own ``run_id`` key would collide with that positional argument.
                payload.pop("run_id", None)
                self.engine.events.emit("handoff_created", run.run_id, **payload)
            return contract


# --------------------------------------------------------------- module seam

_DEFAULT_KERNEL: ExecutionKernel | None = None
_DEFAULT_KERNEL_GUARD = threading.Lock()


def get_default_kernel() -> ExecutionKernel:
    """Process-wide default kernel (section 6 seam). Hosts may inject their own."""
    global _DEFAULT_KERNEL
    with _DEFAULT_KERNEL_GUARD:
        if _DEFAULT_KERNEL is None:
            _DEFAULT_KERNEL = ExecutionKernel()
        return _DEFAULT_KERNEL


def set_default_kernel(kernel: ExecutionKernel | None) -> None:
    """Inject (or reset) the default kernel — used by hosts/tests at the seam."""
    global _DEFAULT_KERNEL
    with _DEFAULT_KERNEL_GUARD:
        _DEFAULT_KERNEL = kernel


def run_turn(
    prompt: str,
    ctx: TurnContext | None = None,
    *,
    kernel: ExecutionKernel | None = None,
) -> TurnOutcome:
    """One orchestrated turn through the P1 kernel (the section 6 seam).

    Flow: classify (paradigm/mode from ctx) -> mode-map to a DWE graph ->
    claim/dispatch to a terminal state or the wave bound -> handoff contract.
    An unmapped paradigm returns ``status='not_expressible'`` with the honest
    reason and no run — never a silent success.
    """
    active_kernel = kernel if kernel is not None else get_default_kernel()
    context = ctx if ctx is not None else TurnContext()

    mapping = map_paradigm(
        context.paradigm,
        kernel=active_kernel,
        mode=context.mode,
        prompt=prompt,
        initial_state=context.initial_state,
    )
    if not mapping.expressible or mapping.run is None or mapping.workflow_id is None:
        return TurnOutcome(
            run_id=None,
            workflow_id=mapping.workflow_id,
            mode=context.mode,
            paradigm=mapping.paradigm,
            status="not_expressible",
            reason=mapping.reason,
        )

    run, waves = active_kernel.run_to_completion(mapping.run.run_id, max_waves=context.max_waves)
    contract = active_kernel.handoff(run.run_id, to_mode=context.handoff_to)
    return TurnOutcome(
        run_id=run.run_id,
        workflow_id=mapping.workflow_id,
        mode=context.mode,
        paradigm=mapping.paradigm,
        status=run.status.value,
        waves=waves,
        failed_nodes=tuple(run.failed_nodes),
        reason=mapping.reason,
        handoff=contract,
    )


def recover_run(
    definition: WorkflowDefinition,
    events: Sequence[WorkflowEvent],
) -> tuple[ExecutionKernel, WorkflowRun]:
    """Restart entry point: rebuild a kernel + run from the append-only log.

    The returned kernel wraps a FRESH engine whose run state was replayed from
    ``events``; dispatching on it resumes the run (see ``alpha.orchestrator.replay``).
    """
    engine, run = replay_run(events, definition, engine=None)
    return ExecutionKernel(engine), run


__all__ = [
    "ExecutionKernel",
    "HandoffContract",
    "TurnContext",
    "TurnOutcome",
    "get_default_kernel",
    "recover_run",
    "run_turn",
    "set_default_kernel",
]
