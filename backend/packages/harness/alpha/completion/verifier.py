"""Gate execution: run one registered check and record what it observed.

This is the only module that turns a :class:`~alpha.completion.models.Gate`
into :class:`~alpha.completion.models.Evidence`. It sits above
``gates.py`` (which owns execution policy) and below ``service.py`` (which
owns orchestration), and it owns four things neither of those does:

* **Budget.** Every run charges ``scope.budget.gate_runs_used`` first, so a
  runaway plan stops at a declared ceiling instead of at the host's patience.
* **Approval.** ``gate_is_permitted`` decides *before* execution; a gate that
  needs a human says so and records ``blocked`` — it is never run "to see".
* **Status.** The gate's status is derived from the recorded evidence, never
  assigned by the caller, so ``passed`` always means "a current check said so".
* **Events.** ``gate.started``/``gate.passed``/``gate.failed``/``gate.stale``
  land in the journal with the same ids the UI reads.

Verification boundaries follow the plan's reverify modes: ``status`` never
executes, ``verify`` runs what is not current or has not passed, and
``reverify`` runs every required runnable gate including the ones that already
passed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .approvals import gate_is_permitted
from .budget import charge_gate_run
from .errors import BudgetExhausted, CheckUnavailable
from .events import EventKind, emit
from .evidence import current_passing, evaluate_freshness, record_evidence, refresh_freshness
from .gates import CheckContext, CheckRegistry, check_context_for, default_check_registry
from .models import CheckOutcome, CompletionScope, Evidence, Gate, GateStatus, TaskState
from .policy import CompletionSettings

#: How a verification pass chooses which gates to execute.
REVERIFY_MODES = ("status", "verify", "reverify")


@dataclass
class VerificationResult:
    """What one pass over a set of gates did."""

    mode: str
    gate_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    outcomes: dict[str, str] = field(default_factory=dict)
    blocked: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    charged: int = 0

    @property
    def passed(self) -> int:
        return sum(1 for outcome in self.outcomes.values() if outcome is CheckOutcome.PASSED)

    @property
    def ran(self) -> int:
        return len(self.outcomes)

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "ran": self.ran,
            "passed": self.passed,
            "gate_ids": self.gate_ids,
            "evidence_ids": self.evidence_ids,
            "outcomes": {gate_id: outcome.value for gate_id, outcome in self.outcomes.items()},
            "blocked": self.blocked,
            "skipped": self.skipped,
            "charged": self.charged,
        }


def _select(scope: CompletionScope, gate_ids: list[str] | None, mode: str) -> list[Gate]:
    """Which gates this pass will execute."""
    candidates = [scope.gates[gate_id] for gate_id in gate_ids] if gate_ids else list(scope.gates.values())
    selected: list[Gate] = []
    for gate in candidates:
        if mode == "status":
            continue
        if mode == "verify":
            # Only what is not already proven current.
            if gate.status is GateStatus.PASSED:
                continue
            selected.append(gate)
            continue
        if mode == "reverify":
            if gate.required or gate.status is not GateStatus.PASSED:
                selected.append(gate)
            continue
        raise CheckUnavailable(f"unknown reverify mode {mode!r}; expected one of {REVERIFY_MODES}", subject=mode)
    return selected


def run_gate(
    scope: CompletionScope,
    gate: Gate,
    settings: CompletionSettings,
    *,
    registry: CheckRegistry | None = None,
    approvals: dict[str, dict] | None = None,
    now: float | None = None,
    worker: str = "",
    doc: dict | None = None,
) -> Evidence:
    """Execute one gate's check and record the evidence.

    Every non-pass outcome is a *record*: ``blocked`` (policy/approval),
    ``timed_out``, ``invalid_configuration`` and the check's own ``failed``
    all produce evidence, because "the check said no" and "the check never
    ran" must stay distinguishable all the way to the final report.
    """
    moment = time.time() if now is None else now
    registry = registry or default_check_registry()
    approvals = approvals if approvals is not None else {}

    try:
        charge_gate_run(scope)
    except BudgetExhausted:
        gate.status = GateStatus.BLOCKED
        gate.last_failure = scope.budget.exhaustion_reason
        gate.updated_at = moment
        raise

    gate.status = GateStatus.RUNNING
    gate.updated_at = moment
    _emit(doc, scope, EventKind.GATE_STARTED, gate=gate, payload={"check_ref": gate.check_ref})

    # Digest the definition before running: evidence and approvals bind it.
    from .evidence import definition_digest

    gate.definition_digest = definition_digest(gate, settings)

    permitted, reason = gate_is_permitted(gate, settings, approvals)
    if not permitted:
        gate.status = GateStatus.BLOCKED
        gate.last_failure = reason
        gate.updated_at = moment
        evidence = _record_blocked(scope, gate, settings, reason, now=moment, worker=worker)
        _emit(doc, scope, EventKind.GATE_BLOCKED, gate=gate, payload={"reason": reason})
        return evidence

    if gate.check_ref == "manual.review":
        # Never executed: a person records the verdict through the review route.
        gate.status = GateStatus.PENDING
        gate.last_failure = reason
        evidence = registry.run(check_context_for(gate, settings, approved=False))
        recorded = record_evidence(scope, gate, evidence, settings, now=moment, worker=worker)
        _emit(doc, scope, EventKind.GATE_BLOCKED, gate=gate, payload={"reason": evidence.reason})
        return recorded

    ctx: CheckContext = check_context_for(gate, settings, approved=True)
    result = registry.run(ctx)
    evidence = record_evidence(scope, gate, result, settings, now=moment, worker=worker)

    if result.outcome is CheckOutcome.PASSED:
        gate.status = GateStatus.PASSED
        gate.last_failure = ""
        _emit(doc, scope, EventKind.GATE_PASSED, gate=gate, payload={"evidence_id": evidence.evidence_id})
    elif result.outcome is CheckOutcome.FAILED:
        gate.status = GateStatus.FAILED
        gate.last_failure = result.reason
        _emit(doc, scope, EventKind.GATE_FAILED, gate=gate, payload={"evidence_id": evidence.evidence_id, "reason": result.reason})
    elif result.outcome is CheckOutcome.BLOCKED:
        gate.status = GateStatus.BLOCKED
        gate.last_failure = result.reason
        _emit(doc, scope, EventKind.GATE_BLOCKED, gate=gate, payload={"reason": result.reason})
    else:
        # timed_out / not_run / invalid_configuration: the gate is not proven.
        gate.status = GateStatus.FAILED if result.outcome is CheckOutcome.TIMED_OUT else GateStatus.PENDING
        gate.last_failure = result.reason
        _emit(doc, scope, EventKind.GATE_FAILED, gate=gate, payload={"outcome": result.outcome.value, "reason": result.reason})
    gate.updated_at = moment
    return evidence


def verify_task(
    scope: CompletionScope,
    task_id: str,
    settings: CompletionSettings,
    *,
    registry: CheckRegistry | None = None,
    approvals: dict[str, dict] | None = None,
    mode: str = "verify",
    now: float | None = None,
    worker: str = "",
    doc: dict | None = None,
) -> VerificationResult:
    """Run the gates attached to one task and move it to ``VERIFIED``/back.

    A task becomes ``VERIFIED`` only when **every required** gate has current
    passing evidence. A failure hands it back to ``RETURNED`` for repair
    (``VERIFYING -> RETURNED`` is a legal edge precisely for this), so a worker
    gets another attempt instead of the scope being declared done.
    """
    from .state_machine import assert_task_transition

    moment = time.time() if now is None else now
    task = scope.task(task_id)
    if task.state in {TaskState.READY, TaskState.WAITING, TaskState.BLOCKED, TaskState.RETURNED}:
        if task.state is not TaskState.VERIFYING:
            assert_task_transition(task.state, TaskState.VERIFYING)
            task.state = TaskState.VERIFYING
            task.updated_at = moment

    result = VerificationResult(mode=mode)
    task_gates = [scope.gates[gate_id] for gate_id in task.gate_ids if gate_id in scope.gates]
    selected = {gate.gate_id for gate in _select(scope, [gate.gate_id for gate in task_gates], mode)} if mode != "status" else set()
    for gate in task_gates:
        if gate.gate_id not in selected:
            latest = _latest(scope, gate)
            if latest is None:
                result.skipped[gate.gate_id] = "no evidence recorded for this gate"
            else:
                freshness, why = evaluate_freshness(latest, gate, settings, now=moment)
                result.skipped[gate.gate_id] = why or f"already {gate.status.value} and {freshness.value}"
            continue
        try:
            evidence = run_gate(scope, gate, settings, registry=registry, approvals=approvals, now=moment, worker=worker, doc=doc)
        except BudgetExhausted as exc:
            result.blocked[gate.gate_id] = str(exc)
            break
        result.gate_ids.append(gate.gate_id)
        if evidence.evidence_id not in result.evidence_ids:
            result.evidence_ids.append(evidence.evidence_id)
        if gate.status in {GateStatus.BLOCKED, GateStatus.PENDING} and evidence.outcome in {
            CheckOutcome.BLOCKED,
            CheckOutcome.NOT_RUN,
            CheckOutcome.INVALID_CONFIGURATION,
        }:
            result.blocked[gate.gate_id] = evidence.reason
        else:
            result.outcomes[gate.gate_id] = evidence.outcome
        if evidence.outcome in {CheckOutcome.PASSED, CheckOutcome.FAILED}:
            result.charged += 1

    refresh_freshness(scope, settings, now=moment)
    required = [scope.gates[gate_id] for gate_id in task.gate_ids if gate_id in scope.gates]
    all_proven = all((not gate.required) or current_passing(scope, gate, settings, now=moment) is not None for gate in required)
    task = scope.task(task_id)
    if task.state is TaskState.VERIFYING:
        target = TaskState.VERIFIED if all_proven else TaskState.RETURNED
        assert_task_transition(task.state, target)
        task.state = target
        task.blocked_reason = "" if all_proven else (task.blocked_reason or "required gates did not pass")
        task.updated_at = moment
        _emit(
            doc,
            scope,
            EventKind.TASK_VERIFIED if all_proven else EventKind.TASK_RETURNED,
            task_id=task.task_id,
            payload={"gates": str(len(required)), "proven": str(all_proven).lower()},
        )
    return result


def _latest(scope: CompletionScope, gate: Gate) -> Evidence | None:
    for evidence_id in reversed(gate.evidence_ids):
        evidence = scope.evidence.get(evidence_id)
        if evidence is not None:
            return evidence
    return None


def _record_blocked(
    scope: CompletionScope,
    gate: Gate,
    settings: CompletionSettings,
    reason: str,
    *,
    now: float,
    worker: str,
) -> Evidence:

    from .gates import CheckResult

    result = CheckResult(CheckOutcome.BLOCKED, reason=reason)
    return record_evidence(scope, gate, result, settings, now=now, worker=worker)


def _emit(doc: dict | None, scope: CompletionScope, kind: EventKind, *, gate: Gate | None = None, task_id: str = "", payload: dict | None = None) -> None:
    if doc is None:
        return
    emit(
        doc,
        scope.scope_id,
        kind,
        task_id=task_id or (gate.task_id if gate else ""),
        gate_id=gate.gate_id if gate else "",
        payload=payload,
        max_events=2000,
    )


__all__ = [
    "REVERIFY_MODES",
    "VerificationResult",
    "run_gate",
    "verify_task",
]
