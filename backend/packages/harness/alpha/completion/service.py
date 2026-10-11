"""``CompletionEngine`` — the reusable facade over the whole subsystem.

Everything the Gateway router and the model-facing tool need is here, so
neither of them composes the internals itself. The engine is deliberately
**synchronous**: it reads a locked ledger, executes bounded checks and writes
back. Gateway entry points must call it through ``asyncio.to_thread`` — a
blocking call on the event loop stalls every other run in the process.

**One lock, one write.** Every composite operation runs as a single
``repository.mutate_scope(scope_id, fn)`` whose ``fn(scope, doc)`` may both
change the scope and append the event (or approval) that change implies. The
lock is not re-entrant across instances, so nesting a second acquisition
inside ``fn`` would deadlock; passing ``doc`` through is what makes the
atomicity achievable rather than aspirational.

What the engine refuses, and why, is the subsystem's contract:

* dispatch requires a **valid** contract (``ContractInvalid``) — a vague plan
  is repaired, never executed;
* status changes go through ``state_machine`` — a model cannot hand itself
  ``verified`` or ``complete``;
* ``finalize`` is the only route to ``COMPLETE``, and in ``observe`` mode it
  records the decision without applying it;
* ownership conflicts are refused **before** launch — coordination, not
  isolation;
* an unreadable ledger is ``StoreUnreadable`` (HTTP 503), never an empty list.

Nothing here imports ``app.*``: the harness must not depend on the Gateway
(``tests/test_harness_boundary.py``).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import budget as budget_mod
from . import dispatch as dispatch_mod
from .approvals import effective_approval, gate_is_permitted  # noqa: F401 - re-exported for callers
from .approvals import grant as grant_approval
from .approvals import request_approval as describe_approval
from .approvals import revoke as revoke_approval
from .contract import RequirementInput, apply_validation, build_contract, contract_digest, validate_contract
from .errors import ApprovalRefused, BudgetExhausted, ContractInvalid, ScopeStateError
from .events import EventKind, emit
from .evidence import definition_digest, refresh_freshness
from .finalization import evaluate, progress_fingerprint
from .finalization import finalize as run_finalize
from .gates import CheckRegistry, default_check_registry
from .models import (
    ApprovalPolicy,
    CompletionScope,
    DispatchRecord,
    Evidence,
    Expectation,
    FinalReport,
    Gate,
    GateKind,
    GateStatus,
    RiskTier,
    ScopeStatus,
    TaskNode,
    TaskState,
)
from .ownership import claim_paths, release_claim, sweep_expired
from .policy import CompletionSettings
from .reconciliation import reconcile
from .repository import CompletionRepository, scope_summary
from .scheduler import assert_acyclic, dispatch_capacity, promote_ready, propagate_failure, scheduling_view, verdict_for
from .state_machine import assert_scope_transition, assert_task_transition
from .verifier import VerificationResult, run_gate, verify_task


# --------------------------------------------------------------------------------------
# Caller input shapes (extra="forbid": a field must be chosen, not leaked)
# --------------------------------------------------------------------------------------
class GateInput(BaseModel):
    """One gate as the planner declares it, before ids/digests are stamped."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str = Field(default="", max_length=64)
    task_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    required: bool = True
    kind: GateKind = GateKind.REGISTERED
    check_ref: str = Field(min_length=1, max_length=80)
    parameters: dict[str, Any] = Field(default_factory=dict)
    expectation: Expectation = Field(default_factory=Expectation)
    owner: str = ""
    risk_tier: RiskTier = RiskTier.LOW
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTOMATIC
    integration: bool = False


class TaskInput(BaseModel):
    """One node of the bounded task tree."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default="", max_length=64)
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(default="", max_length=4000)
    parent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    gate_ids: list[str] = Field(default_factory=list)
    owner: str = ""
    owns: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    integration: bool = False
    max_attempts: int = Field(default=3, ge=1, le=20)
    depth: int = Field(default=0, ge=0, le=8)


class ScopeCreateRequest(BaseModel):
    """Everything needed to open a scope: request, contract, plan and gates."""

    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=200_000, description="The original user request, preserved verbatim for reconciliation.")
    requirements: list[RequirementInput] = Field(default_factory=list, min_length=1)
    tasks: list[TaskInput] = Field(default_factory=list, min_length=1)
    gates: list[GateInput] = Field(default_factory=list, min_length=1)
    mode: str = Field(default="normal", max_length=32)
    owner_id: str = Field(default="", max_length=128)
    thread_id: str = Field(default="", max_length=128)
    scope_id: str = Field(default="", max_length=64)


# --------------------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------------------
class CompletionEngine:
    """Owns one settings snapshot and the ledger beneath it.

    Construct one per request boundary (or once per process) from
    ``CompletionSettings``; the settings snapshot is what evidence and
    approvals bind to, so re-reading config between two calls is deliberate
    rather than a cache miss.
    """

    def __init__(self, settings: CompletionSettings, *, registry: CheckRegistry | None = None) -> None:
        self.settings = settings
        self.registry = registry or default_check_registry()
        self.repo = CompletionRepository(settings)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def health(self) -> dict[str, Any]:
        payload = self.repo.health()
        payload.update(
            {
                "enabled": self.settings.enabled,
                "mode": self.settings.mode,
                "enforcing": self.settings.guarding,
                "workspace_root": str(self.settings.workspace_root),
                "checks": sorted(self.registry.names()),
                "policy_id": self.settings.policy_id,
                "limits": {
                    "max_tasks": self.settings.max_tasks,
                    "max_depth": self.settings.max_depth,
                    "max_concurrency": self.settings.max_concurrency,
                    "max_gate_runs": self.settings.max_gate_runs,
                    "wall_clock_seconds": self.settings.wall_clock_seconds,
                    "no_progress_limit": self.settings.no_progress_limit,
                    "max_scopes": self.settings.max_scopes,
                },
            }
        )
        return payload

    # ------------------------------------------------------------------
    # Contract + plan
    # ------------------------------------------------------------------
    def create_scope(self, request: ScopeCreateRequest, *, now: float | None = None) -> CompletionScope:
        """Validate and persist a scope. Raises ``ContractInvalid`` on a bad plan.

        A refused contract is still persisted first: a rejected plan is a
        record the user can read and repair, not a silent no-op that leaves
        them wondering whether it was ever considered.
        """
        moment = time.time() if now is None else now
        scope_id = request.scope_id or f"sc-{uuid.uuid4().hex[:12]}"

        for item in request.tasks:
            if item.depth > self.settings.max_depth:
                raise BudgetExhausted(
                    f"task {item.task_id or item.title!r} declares depth {item.depth}, above the scope ceiling of {self.settings.max_depth}; stop decomposing and integrate or hand off",
                    subject=scope_id,
                )

        tasks: dict[str, TaskNode] = {}
        for index, item in enumerate(request.tasks, start=1):
            task_id = item.task_id or f"task-{index}"
            tasks[task_id] = TaskNode(
                task_id=task_id,
                title=item.title,
                objective=item.objective,
                parent_id=item.parent_id,
                depends_on=list(item.depends_on),
                gate_ids=list(item.gate_ids),
                owner=item.owner,
                owns=list(item.owns),
                expected_outputs=list(item.expected_outputs),
                integration=item.integration,
                max_attempts=item.max_attempts,
                depth=item.depth,
                created_at=moment,
                updated_at=moment,
            )

        gates: dict[str, Gate] = {}
        for index, item in enumerate(request.gates, start=1):
            gate_id = item.gate_id or f"gate-{index}"
            gates[gate_id] = Gate(
                gate_id=gate_id,
                task_id=item.task_id,
                title=item.title,
                required=item.required,
                kind=item.kind,
                check_ref=item.check_ref,
                parameters=dict(item.parameters),
                expectation=item.expectation,
                owner=item.owner,
                risk_tier=item.risk_tier,
                approval_policy=item.approval_policy,
                integration=item.integration,
                created_from_request_revision=1,
                created_at=moment,
                updated_at=moment,
            )

        contract = build_contract(contract_id=f"ct-{scope_id}", mode=request.mode, requirements=request.requirements, now=moment)
        findings = validate_contract(contract, tasks=tasks, gates=gates, known_checks=self.registry.names())
        apply_validation(contract, findings, now=moment)
        assert_acyclic(tasks)

        scope = CompletionScope(
            scope_id=scope_id,
            owner_id=request.owner_id,
            thread_id=request.thread_id,
            request_snapshot=request.request,
            mode=request.mode,
            status=ScopeStatus.PLANNING,
            contract=contract,
            tasks=tasks,
            gates=gates,
            budget=budget_mod.budget_for(self.settings),
            created_at=moment,
            updated_at=moment,
        )
        # Bind every gate's definition digest now: approvals and evidence key
        # off it, and a gate that has never been digested cannot be approved.
        for gate in gates.values():
            gate.definition_digest = definition_digest(gate, self.settings)
        scope.progress_fingerprint = progress_fingerprint(scope)

        self.repo.create(scope, now=moment)
        self._emit(
            scope,
            EventKind.CONTRACT_VALIDATION_FAILED if not contract.valid else EventKind.CONTRACT_CREATED,
            payload={"digest": contract.digest, "requirements": str(len(contract.requirements))},
        )
        if not contract.valid:
            raise ContractInvalid(
                f"contract for scope {scope_id!r} has {len(contract.blocking_findings)} blocking findings; work may not start",
                findings=contract.blocking_findings,
                subject=scope_id,
            )
        return scope

    def revise_contract(
        self,
        scope_id: str,
        requirements: list[RequirementInput],
        *,
        now: float | None = None,
    ) -> CompletionScope:
        """Amend the obligations: version + digest + request revision move together."""
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> CompletionScope:
            if scope.status in {ScopeStatus.COMPLETE, ScopeStatus.CANCELLED}:
                raise ScopeStateError(f"scope {scope_id!r} is {scope.status.value}; its contract is frozen", subject=scope_id)
            contract = build_contract(
                contract_id=scope.contract.contract_id,
                mode=scope.contract.mode,
                requirements=requirements,
                request_revision=scope.request_revision + 1,
                now=moment,
            )
            contract.version = scope.contract.version + 1
            contract.digest = contract_digest(contract)
            findings = validate_contract(contract, tasks=scope.tasks, gates=scope.gates, known_checks=self.registry.names())
            apply_validation(contract, findings, now=moment)
            scope.contract = contract
            scope.request_revision += 1
            for gate in scope.gates.values():
                gate.created_from_request_revision = scope.request_revision
                gate.definition_digest = definition_digest(gate, self.settings)
                if gate.status in {GateStatus.PASSED, GateStatus.STALE}:
                    # The obligations moved, so what the gate previously proved
                    # no longer answers the question now being asked.
                    gate.status = GateStatus.STALE
            emit(
                doc,
                scope.scope_id,
                EventKind.CONTRACT_REVISED,
                payload={"version": str(contract.version), "revision": str(scope.request_revision)},
                max_events=self.settings.max_events,
            )
            if not contract.valid:
                raise ContractInvalid(
                    f"revised contract has {len(contract.blocking_findings)} blocking findings",
                    findings=contract.blocking_findings,
                    subject=scope_id,
                )
            return scope

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def add_tasks(
        self,
        scope_id: str,
        tasks: list[TaskInput],
        gates: list[GateInput],
        *,
        now: float | None = None,
    ) -> CompletionScope:
        """Extend the plan under the task/depth ceilings (never clamped)."""
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> CompletionScope:
            if scope.status in {ScopeStatus.COMPLETE, ScopeStatus.CANCELLED}:
                raise ScopeStateError(f"scope {scope_id!r} is {scope.status.value}; the plan is frozen", subject=scope_id)
            added_gates: list[str] = []
            for item in tasks:
                budget_mod.assert_can_add_task(scope)
                if item.depth > scope.budget.max_depth:
                    raise BudgetExhausted(
                        f"task depth {item.depth} exceeds the scope ceiling of {scope.budget.max_depth}",
                        subject=scope_id,
                    )
                task_id = item.task_id or f"task-{len(scope.tasks) + 1}"
                while task_id in scope.tasks:
                    task_id = f"{task_id}-{len(scope.tasks) + 1}"
                scope.tasks[task_id] = TaskNode(
                    task_id=task_id,
                    title=item.title,
                    objective=item.objective,
                    parent_id=item.parent_id,
                    depends_on=list(item.depends_on),
                    gate_ids=list(item.gate_ids),
                    owner=item.owner,
                    owns=list(item.owns),
                    expected_outputs=list(item.expected_outputs),
                    integration=item.integration,
                    max_attempts=item.max_attempts,
                    depth=item.depth,
                    created_at=moment,
                    updated_at=moment,
                )
            for item in gates:
                gate_id = item.gate_id or f"gate-{len(scope.gates) + 1}"
                while gate_id in scope.gates:
                    gate_id = f"{gate_id}-{len(scope.gates) + 1}"
                scope.gates[gate_id] = Gate(
                    gate_id=gate_id,
                    task_id=item.task_id,
                    title=item.title,
                    required=item.required,
                    kind=item.kind,
                    check_ref=item.check_ref,
                    parameters=dict(item.parameters),
                    expectation=item.expectation,
                    owner=item.owner,
                    risk_tier=item.risk_tier,
                    approval_policy=item.approval_policy,
                    integration=item.integration,
                    created_from_request_revision=scope.request_revision,
                    definition_digest="",
                    created_at=moment,
                    updated_at=moment,
                )
                added_gates.append(gate_id)
                parent = scope.tasks.get(item.task_id)
                if parent is not None and gate_id not in parent.gate_ids:
                    parent.gate_ids.append(gate_id)
            assert_acyclic(scope.tasks)
            findings = validate_contract(scope.contract, tasks=scope.tasks, gates=scope.gates, known_checks=self.registry.names())
            apply_validation(scope.contract, findings, now=moment)
            for gate in scope.gates.values():
                if not gate.definition_digest:
                    gate.definition_digest = definition_digest(gate, self.settings)
            emit(
                doc,
                scope.scope_id,
                EventKind.CONTRACT_REVISED,
                payload={"tasks": str(len(tasks)), "gates": str(len(added_gates))},
                max_events=self.settings.max_events,
            )
            return scope

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self, scope_id: str, *, now: float | None = None) -> CompletionScope:
        """``planning -> running`` (and a parked scope back into work).

        Refused while the contract is invalid, on an already-running scope
        (a no-op carries no new evidence) and on a terminal one — a silent
        no-op here would leave the caller believing a cancelled scope was
        started.
        """
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> CompletionScope:
            if not scope.contract.valid:
                raise ContractInvalid(
                    f"scope {scope_id!r} has an invalid contract; dispatch is refused until it is repaired",
                    findings=scope.contract.blocking_findings,
                    subject=scope_id,
                )
            if scope.status is ScopeStatus.RUNNING:
                return scope
            previous = scope.status
            assert_scope_transition(previous, ScopeStatus.RUNNING)
            scope.status = ScopeStatus.RUNNING
            if previous is ScopeStatus.PLANNING:
                scope.status_reason = "work started"
                budget_mod.start_deadline(scope, now=moment)
                promote_ready(scope, now=moment)
                emit(doc, scope.scope_id, EventKind.SCOPE_STARTED, max_events=self.settings.max_events)
            else:
                scope.status_reason = f"work resumed from {previous.value}"
                promote_ready(scope, now=moment)
                emit(doc, scope.scope_id, EventKind.SCOPE_STARTED, payload={"resumed_from": previous.value}, max_events=self.settings.max_events)
            return scope

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def cancel(self, scope_id: str, reason: str = "", *, now: float | None = None) -> CompletionScope:
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> CompletionScope:
            if scope.status is ScopeStatus.CANCELLED:
                return scope
            assert_scope_transition(scope.status, ScopeStatus.CANCELLED)
            scope.status = ScopeStatus.CANCELLED
            scope.status_reason = reason or "cancelled by request"
            for task in scope.tasks.values():
                if task.state in {TaskState.IN_FLIGHT, TaskState.READY, TaskState.WAITING, TaskState.RETURNED, TaskState.VERIFYING}:
                    task.state = TaskState.ABANDONED
                    task.blocked_reason = scope.status_reason
            for claim in list(scope.ownership):
                release_claim(scope, claim.task_id)
            emit(
                doc,
                scope.scope_id,
                EventKind.SCOPE_CANCELLED,
                payload={"reason": scope.status_reason},
                max_events=self.settings.max_events,
            )
            return scope

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def status(self, scope_id: str, *, now: float | None = None) -> dict[str, Any]:
        """Full projection: summary + plan tree + gates + evidence + verdicts.

        Freshness is **recomputed on read**, never trusted from storage: a
        caller that edits a gated file and then asks for status must see the
        gate demoted, not a green tick earned before the edit. The
        recomputation is in-memory — the projection tells the truth without
        being the writer that persists it.
        """
        moment = time.time() if now is None else now
        scope = self.repo.load(scope_id)
        sweep_expired(scope, now=moment)
        refresh_freshness(scope, self.settings, now=moment)
        view = scheduling_view(scope)
        return {
            "scope": scope_summary(scope),
            "contract": {
                "contract_id": scope.contract.contract_id,
                "version": scope.contract.version,
                "digest": scope.contract.digest,
                "valid": scope.contract.valid,
                "findings": [finding.model_dump(mode="json") for finding in scope.contract.findings],
                "requirements": [requirement.model_dump(mode="json") for requirement in scope.contract.requirements],
            },
            "tasks": [_task_projection(scope, task, view.verdicts.get(task.task_id)) for task in scope.tasks.values()],
            "gates": [_gate_projection(scope, gate) for gate in scope.gates.values()],
            "dispatches": [record.model_dump(mode="json") for record in scope.dispatches.values()],
            "ownership": [claim.model_dump(mode="json") for claim in scope.ownership],
            "budget": scope.budget.model_dump(mode="json"),
            "final_report": scope.final_report.model_dump(mode="json") if scope.final_report else None,
            "scheduling": view.summary(),
            "mode": self.settings.mode,
            "enforcing": self.settings.enforcing,
            "policy_id": self.settings.policy_id,
        }

    def list_scopes(
        self,
        *,
        owner_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.repo.list(owner_id=owner_id, thread_id=thread_id, limit=limit)

    # ------------------------------------------------------------------
    # Scheduling + dispatch
    # ------------------------------------------------------------------
    def schedule(self, scope_id: str, *, now: float | None = None) -> dict[str, Any]:
        """Recompute readiness and return what may start under the ceiling."""
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> dict[str, Any]:
            if scope.status is ScopeStatus.PLANNING:
                raise ScopeStateError(f"scope {scope_id!r} is still planning; start it before scheduling", subject=scope_id)
            budget_mod.check_deadline(scope, now=moment)
            moved = promote_ready(scope, now=moment)
            for task_id in moved:
                emit(doc, scope.scope_id, EventKind.TASK_READY, task_id=task_id, max_events=self.settings.max_events)
            capacity = dispatch_capacity(scope, self.settings.max_concurrency)
            view = scheduling_view(scope, limit=capacity)
            return {
                **view.summary(),
                "dispatchable": [task.task_id for task in view.dispatchable],
                "promoted": moved,
                "capacity": capacity,
                "budget_exhausted": scope.budget.exhausted,
            }

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def dispatch(self, scope_id: str, task_id: str, *, agent: str = "", now: float | None = None) -> DispatchRecord:
        """Request a launch. The task stays ``READY`` until the host confirms."""
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> DispatchRecord:
            if not scope.contract.valid:
                raise ContractInvalid(
                    "dispatch refused: the contract is not valid",
                    findings=scope.contract.blocking_findings,
                    subject=scope_id,
                )
            budget_mod.check_deadline(scope, now=moment)
            task = scope.task(task_id)
            verdict = verdict_for(scope, task)
            if not verdict.ready:
                raise ScopeStateError(f"task {task_id!r} is not dispatchable: {verdict.reason}", subject=task_id)
            if task.owns:
                claim_paths(scope, task, task.owns, self.settings, now=moment)
                emit(
                    doc,
                    scope.scope_id,
                    EventKind.OWNERSHIP_CLAIMED,
                    task_id=task_id,
                    payload={"paths": ", ".join(task.owns)},
                    max_events=self.settings.max_events,
                )
            record = dispatch_mod.request_launch(scope, task, agent=agent, now=moment)
            emit(
                doc,
                scope.scope_id,
                EventKind.TASK_DISPATCHED,
                task_id=task_id,
                payload={"dispatch_id": record.dispatch_id, "agent": agent},
                max_events=self.settings.max_events,
            )
            return record

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def confirm_launch(self, scope_id: str, dispatch_id: str, handle: str, *, now: float | None = None) -> DispatchRecord:
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> DispatchRecord:
            record = dispatch_mod.confirm_start(scope, dispatch_id, handle, now=moment)
            emit(
                doc,
                scope.scope_id,
                EventKind.TASK_STARTED,
                task_id=record.task_id,
                payload={"handle": handle, "dispatch_id": dispatch_id},
                max_events=self.settings.max_events,
            )
            return record

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def launch_failed(self, scope_id: str, dispatch_id: str, error: str, *, now: float | None = None) -> DispatchRecord:
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> DispatchRecord:
            record = dispatch_mod.fail_launch(scope, dispatch_id, error, now=moment)
            emit(
                doc,
                scope.scope_id,
                EventKind.DISPATCH_LAUNCH_FAILED,
                task_id=record.task_id,
                payload={"error": error},
                max_events=self.settings.max_events,
            )
            return record

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def worker_returned(
        self,
        scope_id: str,
        dispatch_id: str,
        *,
        artifacts: list[str] | None = None,
        reason: str = "",
        now: float | None = None,
    ) -> TaskNode:
        """A worker reported a result: ``RETURNED``, never ``VERIFIED``."""
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> TaskNode:
            _, task = dispatch_mod.record_return(scope, dispatch_id, artifacts=artifacts, reason=reason, now=moment)
            emit(
                doc,
                scope.scope_id,
                EventKind.TASK_RETURNED,
                task_id=task.task_id,
                payload={"dispatch_id": dispatch_id, "artifacts": str(len(artifacts or []))},
                max_events=self.settings.max_events,
            )
            return task

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def mark_task_blocked(self, scope_id: str, task_id: str, reason: str, *, now: float | None = None) -> TaskNode:
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> TaskNode:
            task = scope.task(task_id)
            if task.state is not TaskState.BLOCKED:
                assert_task_transition(task.state, TaskState.BLOCKED)
                task.state = TaskState.BLOCKED
            task.blocked_reason = reason
            task.updated_at = moment
            for parked in propagate_failure(scope, task_id, now=moment, reason=reason):
                emit(
                    doc,
                    scope.scope_id,
                    EventKind.TASK_BLOCKED,
                    task_id=parked,
                    payload={"reason": reason},
                    max_events=self.settings.max_events,
                )
            emit(
                doc,
                scope.scope_id,
                EventKind.TASK_BLOCKED,
                task_id=task_id,
                payload={"reason": reason},
                max_events=self.settings.max_events,
            )
            return task

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def recover(self, scope_id: str, host_states: dict[str, str], *, now: float | None = None) -> dict[str, Any]:
        """Reconnect reconciliation: fold what the host reports into the ledger."""
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> dict[str, Any]:
            changed = dispatch_mod.reconcile(scope, host_states, now=moment)
            expired = sweep_expired(scope, now=moment)
            promote_ready(scope, now=moment)
            return {
                "changed": changed,
                "expired_claims": [claim.task_id for claim in expired],
                "unresolved_waves": [report.to_dict() for report in dispatch_mod.unresolved_waves(scope)],
            }

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------
    def run_gate(
        self,
        scope_id: str,
        gate_id: str,
        *,
        now: float | None = None,
        worker: str = "",
    ) -> Evidence:
        """Execute one gate through the ledger (budget-charged, policy-checked)."""
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> Evidence:
            gate = scope.gate(gate_id)
            evidence = run_gate(
                scope,
                gate,
                self.settings,
                registry=self.registry,
                approvals=_approvals(doc),
                now=moment,
                worker=worker,
            )
            emit(
                doc,
                scope.scope_id,
                EventKind.EVIDENCE_CREATED,
                gate_id=gate_id,
                task_id=gate.task_id,
                payload={"outcome": evidence.outcome.value, "evidence_id": evidence.evidence_id},
                max_events=self.settings.max_events,
            )
            return evidence

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def verify_task(
        self,
        scope_id: str,
        task_id: str,
        *,
        mode: str = "verify",
        now: float | None = None,
        worker: str = "",
    ) -> VerificationResult:
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> VerificationResult:
            result = verify_task(
                scope,
                task_id,
                self.settings,
                registry=self.registry,
                approvals=_approvals(doc),
                mode=mode,
                now=moment,
                worker=worker,
            )
            task = scope.task(task_id)
            if task.state is TaskState.VERIFIED:
                for ready_id in promote_ready(scope, now=moment):
                    emit(doc, scope.scope_id, EventKind.TASK_READY, task_id=ready_id, max_events=self.settings.max_events)
            elif task.state is TaskState.RETURNED and task.attempts >= task.max_attempts:
                task.state = TaskState.FAILED
                task.blocked_reason = f"retry budget exhausted after {task.attempts} attempts"
                propagate_failure(scope, task_id, now=moment, reason=task.blocked_reason)
            return result

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    # ------------------------------------------------------------------
    # Approvals + manual review
    # ------------------------------------------------------------------
    def approval_request(self, scope_id: str, gate_id: str) -> dict[str, Any]:
        scope = self.repo.load(scope_id)
        return describe_approval(scope.gate(gate_id))

    def approve(self, scope_id: str, gate_id: str, approver: str, *, reason: str = "", now: float | None = None) -> dict[str, Any]:
        """Approve a gate's *current* definition; grant and record atomically."""
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> dict[str, Any]:
            gate = scope.gate(gate_id)
            record = grant_approval(gate, approver, reason=reason, now=moment)
            doc.setdefault("approvals", {})[gate_id] = record.model_dump(mode="json")
            emit(
                doc,
                scope.scope_id,
                EventKind.APPROVAL_GRANTED,
                gate_id=gate_id,
                task_id=gate.task_id,
                payload={"approved_by": record.approved_by, "digest": record.definition_digest[:12], "reason": record.reason},
                max_events=self.settings.max_events,
            )
            return record.model_dump(mode="json")

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def revoke_approval(self, scope_id: str, gate_id: str, *, reason: str = "", now: float | None = None) -> bool:
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> bool:
            gate = scope.gate(gate_id)
            existed = revoke_approval(gate, reason=reason)
            doc.setdefault("approvals", {}).pop(gate_id, None)
            if existed:
                emit(
                    doc,
                    scope.scope_id,
                    EventKind.APPROVAL_REVOKED,
                    gate_id=gate_id,
                    task_id=gate.task_id,
                    payload={"reason": reason},
                    max_events=self.settings.max_events,
                )
            return existed

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def review_gate(
        self,
        scope_id: str,
        gate_id: str,
        *,
        verdict: str,
        reviewer: str,
        notes: str = "",
        now: float | None = None,
    ) -> Gate:
        """Record a human's verdict on a ``manual.review`` gate.

        The verdict lands as *evidence attributed to a named reviewer* — a
        manual review is never disguised as an automated pass, and the gate's
        status still follows the recorded outcome rather than the caller's
        wording. A failed review is ``FAILED`` evidence, not ``NOT_RUN``.
        """
        moment = time.time() if now is None else now
        if verdict not in {"pass", "fail"}:
            raise ApprovalRefused(f"verdict must be 'pass' or 'fail', got {verdict!r}", subject=gate_id)
        if not reviewer or not reviewer.strip():
            raise ApprovalRefused("a manual review needs a named reviewer", subject=gate_id)

        def _apply(scope: CompletionScope, doc: dict) -> Gate:
            gate = scope.gate(gate_id)
            if gate.check_ref != "manual.review":
                raise ApprovalRefused(f"gate {gate_id!r} is a {gate.check_ref!r} check, not a manual review", subject=gate_id)
            from .models import CheckOutcome

            evidence = run_gate(
                scope,
                gate,
                self.settings,
                registry=self.registry,
                approvals=_approvals(doc),
                now=moment,
                worker=reviewer,
            )
            outcome = CheckOutcome.PASSED if verdict == "pass" else CheckOutcome.FAILED
            # Overwrite with the human verdict: the check itself only reports
            # not_run, and the reviewer's decision is the observation.
            evidence.outcome = outcome
            evidence.reason = notes or f"manual review by {reviewer.strip()}: {verdict}"
            evidence.summary = evidence.reason[:4000]
            evidence.worker = reviewer.strip()
            evidence.context = {**evidence.context, "reviewer": evidence.worker, "verdict": verdict}
            gate.status = GateStatus.PASSED if outcome is CheckOutcome.PASSED else GateStatus.FAILED
            gate.last_failure = "" if outcome is CheckOutcome.PASSED else evidence.reason
            gate.updated_at = moment
            emit(
                doc,
                scope.scope_id,
                EventKind.GATE_PASSED if outcome is CheckOutcome.PASSED else EventKind.GATE_FAILED,
                gate_id=gate_id,
                task_id=gate.task_id,
                payload={"reviewer": evidence.worker, "verdict": verdict},
                max_events=self.settings.max_events,
            )
            return gate

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------
    def claim_paths(self, scope_id: str, task_id: str, paths: list[str], *, now: float | None = None) -> list[str]:
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> list[str]:
            task = scope.task(task_id)
            claim = claim_paths(scope, task, paths, self.settings, now=moment)
            emit(
                doc,
                scope.scope_id,
                EventKind.OWNERSHIP_CLAIMED,
                task_id=task_id,
                payload={"paths": ", ".join(claim.paths)},
                max_events=self.settings.max_events,
            )
            return claim.paths

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def release_paths(self, scope_id: str, task_id: str, *, reason: str = "", now: float | None = None) -> bool:
        moment = time.time() if now is None else now

        def _apply(scope: CompletionScope, doc: dict) -> bool:
            released = release_claim(scope, task_id)
            if released:
                emit(
                    doc,
                    scope.scope_id,
                    EventKind.OWNERSHIP_RELEASED,
                    task_id=task_id,
                    payload={"reason": reason},
                    max_events=self.settings.max_events,
                )
            return released

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------
    def preview_finalization(self, scope_id: str, *, now: float | None = None) -> FinalReport:
        """The decision without the mutation (what the UI shows before a commit)."""
        scope = self.repo.load(scope_id)
        return evaluate(scope, self.settings, now=now)

    def finalize(
        self,
        scope_id: str,
        *,
        rerun_required: bool = True,
        rerun_gate_ids: Iterable[str] | None = None,
        now: float | None = None,
    ) -> FinalReport:
        """The completion guard. The only route to ``COMPLETE``."""
        moment = time.time() if now is None else now
        rerun = set(rerun_gate_ids or ())

        def _apply(scope: CompletionScope, doc: dict) -> FinalReport:
            report = run_finalize(
                scope,
                self.settings,
                now=moment,
                rerun_required=rerun_required,
                rerun_gate_ids=rerun,
            )
            if report.complete:
                kind = EventKind.SCOPE_FINALIZED
            elif any(blocker.code.startswith("request.") for blocker in report.blockers):
                kind = EventKind.SCOPE_RECONCILIATION_FAILED
            elif report.status is ScopeStatus.HANDOFF_REQUIRED:
                kind = EventKind.SCOPE_HANDOFF_REQUIRED
            else:
                kind = EventKind.SCOPE_FINALIZED
            emit(
                doc,
                scope.scope_id,
                kind,
                payload={
                    "status": report.status.value,
                    "blockers": str(len(report.blockers)),
                    "enforced": str(report.enforced).lower(),
                },
                max_events=self.settings.max_events,
            )
            return report

        return self.repo.mutate_scope(scope_id, _apply, now=moment)

    def reconcile_request(self, scope_id: str, *, now: float | None = None):
        """Re-read the stored request against the contract (read-only audit)."""
        scope = self.repo.load(scope_id)
        return reconcile(scope, self.settings, now=now)

    def events(self, scope_id: str, *, limit: int = 200, kinds: set[str] | None = None) -> list[dict]:
        return self.repo.events(scope_id, limit=limit, kinds=kinds)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _emit(self, scope: CompletionScope, kind: EventKind, *, payload: dict | None = None) -> None:
        """Append an event in its own write (used outside a composite mutation)."""
        self.repo.mutate_document(
            lambda doc: emit(
                doc,
                scope.scope_id,
                kind,
                payload=payload,
                max_events=self.settings.max_events,
            )
        )


def _approvals(doc: dict) -> dict[str, dict]:
    """Read the approval table from the document already under the lock."""
    return dict(doc.get("approvals", {}))


def _task_projection(scope: CompletionScope, task: TaskNode, verdict) -> dict[str, Any]:
    gates = [scope.gates[gate_id] for gate_id in task.gate_ids if gate_id in scope.gates]
    return {
        **task.model_dump(mode="json"),
        "children": [child.task_id for child in scope.children_of(task.task_id)],
        "gate_statuses": {gate.gate_id: gate.status.value for gate in gates},
        "dispatchable": bool(verdict.ready) if verdict else False,
        "readiness_reason": verdict.reason if verdict else "",
        "blocking": list(verdict.blocking_task_ids) if verdict else [],
    }


def _gate_projection(scope: CompletionScope, gate: Gate) -> dict[str, Any]:
    latest = None
    for evidence_id in reversed(gate.evidence_ids):
        if evidence_id in scope.evidence:
            latest = scope.evidence[evidence_id]
            break
    return {
        **gate.model_dump(mode="json"),
        "latest_evidence": latest.model_dump(mode="json") if latest else None,
        "approval_live": bool(gate.approval and gate.definition_digest and gate.approval.definition_digest == gate.definition_digest),
    }


__all__ = [
    "CompletionEngine",
    "GateInput",
    "ScopeCreateRequest",
    "TaskInput",
    "effective_approval",
    "gate_is_permitted",
    "scope_summary",
]
