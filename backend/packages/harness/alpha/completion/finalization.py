"""The finalization guard — the only route to ``COMPLETE``.

This module is the answer to "what is the one component every final answer
passes through?" (plan §20.2). Adapters, workers and models may *propose*
completion; only :func:`finalize` may write it, and it only writes what the
recorded evidence supports.

The invariant it enforces, in one sentence: **a scope is ``complete`` only
when every mandatory requirement maps to current passing evidence, every
required task is verified, every parent integration gate holds, no dispatch
wave is unresolved, and the original request reconciles against the contract.**

Design decisions worth stating:

* **``evaluate`` and ``finalize`` are separate.** ``evaluate`` is pure: it
  computes the same report without touching status, which is what observe mode
  (``completion.mode: observe``) and the UI's dry-run preview use. In observe
  mode ``finalize`` calls it and records ``enforced: false`` — it never
  reports a completion it did not apply.
* **A waiver is a blocker, not a pass.** ``WAIVED`` and ``ABANDONED`` gates
  appear in the blocker list: a visible owner decision that skipped a proof is
  not the proof.
* **Not run is not passed.** ``PENDING``/``RUNNING``/``STALE``/``BLOCKED``
  required gates all block, and ``not_rerun`` downgrades an otherwise-clean
  report to ``partial`` naming the checks that were skipped.
* **Progress is semantic.** The fingerprint covers gate/evidence/task/dispatch
  state, not text, so rewording a plan cannot burn a retry; a bounded number
  of attempts with a frozen fingerprint ends in ``handoff_required`` with a
  diagnostic rather than an endless loop.
"""

from __future__ import annotations

import hashlib
import json
import time

from .contract import contract_digest
from .errors import ScopeStateError
from .evidence import current_passing, refresh_freshness
from .models import (
    Blocker,
    CompletionScope,
    FinalReport,
    GateStatus,
    RequirementStatus,
    ScopeStatus,
    TaskState,
)
from .policy import CompletionSettings
from .reconciliation import reconcile, write_statuses
from .state_machine import assert_scope_transition, can_transition_scope, is_terminal_scope


def progress_fingerprint(scope: CompletionScope) -> str:
    """Digest over *decided state only*.

    Gate statuses, evidence outcomes, task states, dispatch states and
    requirement verdicts. Deliberately excludes prose — a comment rewrite or a
    plan reword must not count as progress, and must not consume a retry.
    """
    payload = {
        "gates": sorted(f"{gate.gate_id}:{gate.status.value}:{gate.attempts}" for gate in scope.gates.values()),
        "evidence": sorted(f"{ev.evidence_id}:{ev.outcome.value}:{ev.freshness.value}" for ev in scope.evidence.values()),
        "tasks": sorted(f"{task.task_id}:{task.state.value}:{task.attempts}" for task in scope.tasks.values()),
        "dispatches": sorted(f"{d.dispatch_id}:{d.state.value}" for d in scope.dispatches.values()),
        "requirements": sorted(f"{r.requirement_id}:{r.status.value}" for r in scope.contract.requirements),
        "contract": contract_digest(scope.contract),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def collect_blockers(
    scope: CompletionScope,
    settings: CompletionSettings,
    *,
    now: float | None = None,
    reconciliation_report=None,
) -> list[Blocker]:
    """Every reason completion cannot be claimed, each with its subject."""
    moment = time.time() if now is None else now
    blockers: list[Blocker] = []

    # 1. The contract itself must be usable.
    if not scope.contract.valid:
        for finding in scope.contract.blocking_findings:
            blockers.append(
                Blocker(
                    code=f"contract.{finding.code}",
                    message=finding.message,
                    subject_id=finding.subject_id,
                    kind="unverified",
                )
            )
        if not scope.contract.blocking_findings and scope.contract.findings == []:
            blockers.append(Blocker(code="contract.unvalidated", message="the acceptance contract has never been validated", kind="unverified"))

    # 2. Mandatory requirements map to current passing evidence.
    statuses = write_statuses(scope, settings, now=moment)
    for requirement in scope.contract.requirements:
        if not requirement.mandatory:
            continue
        status = statuses.get(requirement.requirement_id, RequirementStatus.UNVERIFIED)
        if status is RequirementStatus.MET:
            continue
        blockers.append(
            Blocker(
                code="requirement.unmet" if status is RequirementStatus.NOT_MET else "requirement.unverified",
                message=(f"mandatory requirement {requirement.requirement_id!r} ({requirement.intent[:160]}) is {status.value}; no current passing evidence decides it"),
                subject_id=requirement.requirement_id,
                kind="unverified" if status is RequirementStatus.UNVERIFIED else "failed",
            )
        )

    # 3. Required gates: only PASSED satisfies, and only while its evidence is current.
    for gate in sorted(scope.gates.values(), key=lambda item: item.gate_id):
        if not gate.required:
            continue
        if gate.status is GateStatus.PASSED and current_passing(scope, gate, settings, now=moment) is not None:
            continue
        if gate.status in {GateStatus.WAIVED, GateStatus.ABANDONED}:
            blockers.append(
                Blocker(
                    code=f"gate.{gate.status.value}",
                    message=f"required gate {gate.gate_id!r} ({gate.title}) was {gate.status.value}: a visible decision to skip it, not a pass",
                    subject_id=gate.gate_id,
                    kind="omitted",
                )
            )
        elif gate.status is GateStatus.STALE:
            blockers.append(
                Blocker(
                    code="gate.stale",
                    message=f"required gate {gate.gate_id!r} has stale evidence and must be re-run",
                    subject_id=gate.gate_id,
                    kind="unverified",
                )
            )
        elif gate.status in {GateStatus.BLOCKED}:
            blockers.append(
                Blocker(
                    code="gate.blocked",
                    message=f"required gate {gate.gate_id!r} is blocked: {gate.last_failure or 'policy or approval outstanding'}",
                    subject_id=gate.gate_id,
                    kind="blocked",
                )
            )
        elif gate.status is GateStatus.FAILED:
            blockers.append(
                Blocker(
                    code="gate.failed",
                    message=f"required gate {gate.gate_id!r} failed: {gate.last_failure or 'expectation not met'}",
                    subject_id=gate.gate_id,
                    kind="failed",
                )
            )
        else:
            blockers.append(
                Blocker(
                    code="gate.not_proven",
                    message=f"required gate {gate.gate_id!r} is {gate.status.value} — not run is never a pass",
                    subject_id=gate.gate_id,
                    kind="unverified",
                )
            )

    # 4. Required tasks must be verified; leaves and integration parents alike.
    for task in sorted(scope.tasks.values(), key=lambda item: item.task_id):
        if task.state is TaskState.VERIFIED:
            continue
        if task.state is TaskState.ABANDONED:
            blockers.append(
                Blocker(
                    code="task.abandoned",
                    message=f"task {task.task_id!r} was abandoned: {task.blocked_reason or 'no reason recorded'}",
                    subject_id=task.task_id,
                    kind="abandoned",
                )
            )
        elif task.state is TaskState.BLOCKED:
            blockers.append(
                Blocker(
                    code="task.blocked",
                    message=f"task {task.task_id!r} is blocked: {task.blocked_reason or 'no reason recorded'}",
                    subject_id=task.task_id,
                    kind="blocked",
                )
            )
        elif task.state is TaskState.FAILED:
            blockers.append(
                Blocker(
                    code="task.failed",
                    message=f"task {task.task_id!r} failed with no permitted retry left (attempts {task.attempts}/{task.max_attempts})",
                    subject_id=task.task_id,
                    kind="failed",
                )
            )
        else:
            # Name the real reason. "Its gates have not passed" is only true
            # when they actually have not — a task left in `returned` with
            # every required gate passing has *not been verified yet*, and
            # saying otherwise sends the reader after a check that already
            # succeeded.
            unproven = [gate.gate_id for gate in scope.gates_of(task.task_id) if gate.required and current_passing(scope, gate, settings, now=moment) is None]
            if unproven:
                detail = "required gates without current passing evidence: " + ", ".join(unproven)
            else:
                detail = "every required gate passes, but the task has not been verified — verify it after the worker returns"
            blockers.append(
                Blocker(
                    code="task.unverified",
                    message=f"task {task.task_id!r} is {task.state.value}: {detail}",
                    subject_id=task.task_id,
                    kind="unverified",
                )
            )
        # Declared outputs are only real if a check produced them.
        if task.expected_outputs and not any(current_passing(scope, gate, settings, now=moment) is not None for gate in scope.gates_of(task.task_id)):
            blockers.append(
                Blocker(
                    code="output.unproven",
                    message=f"task {task.task_id!r} declares outputs {task.expected_outputs} with no current passing gate proving them",
                    subject_id=task.task_id,
                    kind="unverified",
                )
            )

    # 5. No dispatch wave may be left unresolved.
    from .dispatch import unresolved_waves

    for report in unresolved_waves(scope):
        blockers.append(
            Blocker(
                code="dispatch.unresolved",
                message=f"launch wave {report.wave_id} is unresolved ({', '.join(report.unresolved)}); a partial wave is never terminal",
                subject_id=report.wave_id,
                kind="blocked",
            )
        )
    for dispatch in sorted(scope.dispatches.values(), key=lambda item: item.dispatch_id):
        if dispatch.state.value == "launch_failed":
            blockers.append(
                Blocker(
                    code="dispatch.launch_failed",
                    message=f"dispatch {dispatch.dispatch_id!r} never started: {dispatch.launch_error}",
                    subject_id=dispatch.dispatch_id,
                    kind="failed",
                )
            )
        elif dispatch.state.value == "unknown":
            blockers.append(
                Blocker(
                    code="dispatch.unknown",
                    message=f"dispatch {dispatch.dispatch_id!r} has an unreadable state: {dispatch.terminal_reason}",
                    subject_id=dispatch.dispatch_id,
                    kind="blocked",
                )
            )

    # 6. The original request must reconcile.
    report = reconciliation_report if reconciliation_report is not None else reconcile(scope, settings, now=moment)
    for clause in report.unrepresented:
        blockers.append(
            Blocker(
                code="request.unrepresented",
                message=f"request clause is covered by no requirement: {clause}",
                kind="unverified",
            )
        )
    for requirement_id in report.unverifiable:
        blockers.append(
            Blocker(
                code="requirement.unverifiable",
                message=f"requirement {requirement_id!r} declares no observable, so it can never be proven",
                subject_id=requirement_id,
                kind="unverified",
            )
        )

    # 7. Budget.
    if scope.budget.exhausted:
        blockers.append(Blocker(code="budget.exhausted", message=scope.budget.exhaustion_reason, kind="blocked"))

    return blockers


def _decide_target(blockers: list[Blocker]) -> ScopeStatus:
    """Map blockers onto the plan's final-status vocabulary, most severe first."""
    if not blockers:
        return ScopeStatus.COMPLETE
    kinds = {blocker.kind for blocker in blockers}
    codes = {blocker.code for blocker in blockers}
    if "abandoned" in kinds or "dispatch.unknown" in codes or any(code.startswith("dispatch.unresolved") for code in codes):
        return ScopeStatus.HANDOFF_REQUIRED
    if "failed" in kinds:
        return ScopeStatus.FAILED
    if "blocked" in kinds:
        return ScopeStatus.BLOCKED
    return ScopeStatus.PARTIAL


def evaluate(
    scope: CompletionScope,
    settings: CompletionSettings,
    *,
    now: float | None = None,
    rerun_required: bool = True,
    rerun_gate_ids: set[str] | None = None,
) -> FinalReport:
    """Compute the honest outcome **without changing the scope's status**.

    This is what observe mode persists and what the UI previews. It refreshes
    evidence freshness first, so a report can never be built on a check whose
    inputs changed underneath it.
    """
    moment = time.time() if now is None else now
    refresh_freshness(scope, settings, now=moment)
    report_reconciliation = reconcile(scope, settings, now=moment)
    blockers = collect_blockers(scope, settings, now=moment, reconciliation_report=report_reconciliation)

    if not rerun_required:
        skipped = sorted(gate.gate_id for gate in scope.gates.values() if gate.required and gate.gate_id not in (rerun_gate_ids or set()))
        if skipped:
            blockers.append(
                Blocker(
                    code="verification.not_rerun",
                    message=(f"this attempt did not re-run required checks {skipped}; a full verification pass is not claimed, so the outcome is downgraded"),
                    kind="unverified",
                )
            )
    else:
        skipped = []

    target = _decide_target(blockers)

    gate_rows = list(scope.gates.values())
    counts = {
        "ran": sum(1 for gate in gate_rows if gate.evidence_ids),
        "passed": sum(1 for gate in gate_rows if gate.status is GateStatus.PASSED),
        "failed": sum(1 for gate in gate_rows if gate.status is GateStatus.FAILED),
        "stale": sum(1 for gate in gate_rows if gate.status is GateStatus.STALE),
        "not_run": sum(1 for gate in gate_rows if gate.status in {GateStatus.PENDING, GateStatus.RUNNING}),
    }
    statuses = {r.requirement_id: r.status for r in scope.contract.requirements}
    met = sum(1 for r in scope.contract.requirements if r.mandatory and statuses.get(r.requirement_id) is RequirementStatus.MET)

    summary = _summarize(target, blockers, counts, met, len(scope.contract.requirements), report_reconciliation)
    return FinalReport(
        status=target,
        enforced=False,
        complete=target is ScopeStatus.COMPLETE,
        blockers=blockers,
        checks_ran=counts["ran"],
        checks_passed=counts["passed"],
        checks_failed=counts["failed"],
        checks_stale=counts["stale"],
        checks_not_run=counts["not_run"],
        requirements_met=met,
        requirements_total=sum(1 for requirement in scope.contract.requirements if requirement.mandatory),
        not_rerun=skipped,
        reconciliation=report_reconciliation,
        summary=summary,
        generated_at=moment,
        attempt=scope.finalize_attempts,
    )


def finalize(
    scope: CompletionScope,
    settings: CompletionSettings,
    *,
    now: float | None = None,
    rerun_required: bool = True,
    rerun_gate_ids: set[str] | None = None,
) -> FinalReport:
    """Evaluate, then move the scope — the only writer of ``COMPLETE``.

    In observe mode the evaluation is recorded and the status is left alone
    (``enforced`` stays false), because an observe-mode engine that reported a
    completion it did not apply would be the exact lie this module exists to
    prevent.
    """
    moment = time.time() if now is None else now
    if scope.status is ScopeStatus.CANCELLED:
        raise ScopeStateError(f"scope {scope.scope_id!r} is cancelled; a cancelled scope is never finalized", subject=scope.scope_id)
    if is_terminal_scope(scope.status) and scope.status is not ScopeStatus.COMPLETE:
        raise ScopeStateError(f"scope {scope.scope_id!r} is {scope.status.value} (terminal) and cannot be finalized", subject=scope.scope_id)

    report = evaluate(scope, settings, now=moment, rerun_required=rerun_required, rerun_gate_ids=rerun_gate_ids)
    scope.finalize_attempts += 1
    report.attempt = scope.finalize_attempts

    fingerprint = progress_fingerprint(scope)
    unchanged = fingerprint == scope.progress_fingerprint
    scope.progress_fingerprint = fingerprint
    scope.no_progress_count = scope.no_progress_count + 1 if unchanged else 0

    handoff_for_no_progress = False
    if scope.no_progress_count >= settings.no_progress_limit and report.status is not ScopeStatus.COMPLETE:
        handoff_for_no_progress = True
        report.blockers.append(
            Blocker(
                code="progress.stalled",
                message=(f"{scope.no_progress_count} consecutive finalization attempts produced no change in gate, evidence, task, dispatch or requirement state; stopping with a handoff instead of looping"),
                kind="no_progress",
            )
        )
        report.status = ScopeStatus.HANDOFF_REQUIRED
        report.complete = False

    if not settings.guarding:
        # Observe mode or a disabled engine: record the decision, change
        # nothing — an engine that reported a completion it did not apply
        # would be the exact lie this module exists to prevent.
        report.enforced = False
        prefix = "[observe] " if settings.enforcing else "[disabled] "
        report.summary = f"{prefix}{report.summary}"
        scope.final_report = report
        return report

    if scope.status is ScopeStatus.PLANNING:
        raise ScopeStateError(
            f"scope {scope.scope_id!r} is still planning; start the work before finalizing it",
            subject=scope.scope_id,
        )
    if scope.status is not ScopeStatus.VERIFYING:
        # Moving *into* VERIFYING first is what makes the guard a gate: the
        # engine must evaluate recorded evidence before anything may claim
        # completion, and ``COMPLETE`` is reachable only from here.
        if not can_transition_scope(scope.status, ScopeStatus.VERIFYING):
            # A parked scope (``FAILED``, ``HANDOFF_REQUIRED``) re-enters
            # verification only by going back through real work: both edges
            # are legal, and the reason records why rather than a status
            # jumping a state the graph does not contain.
            parked = scope.status
            assert_scope_transition(parked, ScopeStatus.RUNNING)
            scope.status = ScopeStatus.RUNNING
            scope.status_reason = f"re-opened from {parked.value} for finalization"
        assert_scope_transition(scope.status, ScopeStatus.VERIFYING)
        scope.status = ScopeStatus.VERIFYING
        scope.status_reason = "evaluating recorded evidence"

    assert_scope_transition(scope.status, report.status)
    scope.status = report.status
    scope.status_reason = report.summary[:500]
    report.enforced = True
    scope.finished_at = moment if report.status in {ScopeStatus.COMPLETE, ScopeStatus.PARTIAL, ScopeStatus.FAILED} else scope.finished_at
    if report.status is ScopeStatus.COMPLETE:
        scope.last_verified_revision = scope.request_revision
    if handoff_for_no_progress:
        scope.status_reason = "handoff required: no measurable progress"
    scope.final_report = report
    scope.updated_at = moment
    return report


def evaluate_only(
    scope: CompletionScope,
    settings: CompletionSettings,
    *,
    now: float | None = None,
) -> FinalReport:
    """Alias for :func:`evaluate`, named for callers that never mutate."""
    return evaluate(scope, settings, now=now)


def _summarize(
    target: ScopeStatus,
    blockers: list[Blocker],
    counts: dict[str, int],
    met: int,
    total: int,
    report_reconciliation,
) -> str:
    """Build the honest response the plan's renderer asks for."""
    lines: list[str] = []
    headline = {
        ScopeStatus.COMPLETE: "Complete — every mandatory requirement has current passing evidence.",
        ScopeStatus.PARTIAL: "Partial — useful work landed, but something required is still unproven.",
        ScopeStatus.BLOCKED: "Blocked — a dependency, permission or decision prevents safe continuation.",
        ScopeStatus.FAILED: "Failed — a required check or task failed with no permitted retry left.",
        ScopeStatus.HANDOFF_REQUIRED: "Handoff required — state is unrecoverable without a human.",
    }.get(target, f"Outcome: {target.value}.")
    lines.append(headline)
    lines.append(f"Requirements met: {met}/{total}. Checks: {counts['passed']} passed, {counts['failed']} failed, {counts['stale']} stale, {counts['not_run']} not run.")
    if blockers:
        lines.append("What is not proven:")
        for blocker in blockers[:12]:
            lines.append(f"  - [{blocker.code}] {blocker.message}")
        if len(blockers) > 12:
            lines.append(f"  - ...and {len(blockers) - 12} more")
    if report_reconciliation is not None and report_reconciliation.unrepresented:
        lines.append(f"Request clauses with no requirement covering them: {len(report_reconciliation.unrepresented)} (basis: {report_reconciliation.basis}).")
    return "\n".join(lines)


__all__ = [
    "collect_blockers",
    "evaluate",
    "evaluate_only",
    "finalize",
    "progress_fingerprint",
]
