"""Original-request-to-contract reconciliation — asked vs. accounted for.

The finalizer cannot answer "did we do what the user asked?" from the
contract alone, because the contract is a *reading* of the request and a
reading can drop a clause. So the last step before a completion claim re-reads
the stored ``request_snapshot`` and compares it against the contract
inventory.

Honesty about the method is part of the contract: clause extraction is a
**heuristic** (``contract.extract_request_clauses``), so the report says
``basis: heuristic_clause_match`` rather than presenting a prose split as a
measurement. An unrepresented clause is a *finding the user can read*, not a
silent gap — the failure mode the plan calls out is the agent implementing the
easy half of a broad request and forgetting a secondary constraint.

Three buckets, deliberately distinct:

* ``unrepresented`` — a request clause no requirement covers.
* ``unmet`` — a mandatory requirement with no *current* passing evidence.
* ``unverifiable`` — a requirement bound to no observable at all, so it can
  never be proven either way.
"""

from __future__ import annotations

import time

from .contract import clause_covered, extract_request_clauses
from .evidence import current_passing
from .models import CompletionScope, ReconciliationReport, RequirementStatus
from .policy import CompletionSettings


def reconcile(scope: CompletionScope, settings: CompletionSettings, *, now: float | None = None) -> ReconciliationReport:
    """Audit the stored request against the contract and current evidence."""
    moment = time.time() if now is None else now
    clauses = extract_request_clauses(scope.request_snapshot)
    requirements = scope.contract.requirements

    unrepresented: list[str] = []
    for clause in clauses:
        if not any(clause_covered(clause, requirement) for requirement in requirements):
            unrepresented.append(clause)

    unmet: list[str] = []
    unverifiable: list[str] = []
    for requirement in requirements:
        if not requirement.mandatory:
            continue
        if not requirement.observable.strip() or not requirement.gate_ids:
            unverifiable.append(requirement.requirement_id)
            continue
        proven = any(current_passing(scope, scope.gates[gate_id], settings, now=moment) is not None for gate_id in requirement.gate_ids if gate_id in scope.gates)
        if not proven:
            unmet.append(requirement.requirement_id)

    clean = not unrepresented and not unmet and not unverifiable
    notes = ""
    if clean and not clauses:
        # "Nothing to reconcile" is not the same as "everything reconciled";
        # a snapshot we could not split into clauses proves no coverage.
        notes = "the request snapshot yielded no candidate clauses, so this clean result covers no obligations"
    return ReconciliationReport(
        basis="heuristic_clause_match",
        clauses_seen=len(clauses),
        unrepresented=unrepresented,
        unmet=unmet,
        unverifiable=unverifiable,
        clean=clean,
        notes=notes,
    )


def requirement_statuses(scope: CompletionScope, settings: CompletionSettings, *, now: float | None = None) -> dict[str, RequirementStatus]:
    """Three-valued verdict per requirement: ``met`` / ``not_met`` / ``unverified``.

    Deliberately the same vocabulary as ``alpha.mission.acceptance``: an
    unreviewed requirement is ``unverified``, never ``met``.
    """
    moment = time.time() if now is None else now
    statuses: dict[str, RequirementStatus] = {}
    for requirement in scope.contract.requirements:
        gate_ids = [gate_id for gate_id in requirement.gate_ids if gate_id in scope.gates]
        if not requirement.mandatory:
            statuses[requirement.requirement_id] = RequirementStatus.UNVERIFIED
            continue
        if not gate_ids or not requirement.observable.strip():
            statuses[requirement.requirement_id] = RequirementStatus.UNVERIFIED
            continue
        outcomes = []
        for gate_id in gate_ids:
            gate = scope.gates[gate_id]
            evidence = current_passing(scope, gate, settings, now=moment)
            if evidence is not None:
                outcomes.append(RequirementStatus.MET)
            elif gate.status.value in {"failed", "blocked", "abandoned", "waived"}:
                outcomes.append(RequirementStatus.NOT_MET)
            else:
                outcomes.append(RequirementStatus.UNVERIFIED)
        if RequirementStatus.MET in outcomes:
            statuses[requirement.requirement_id] = RequirementStatus.MET
        elif RequirementStatus.NOT_MET in outcomes:
            statuses[requirement.requirement_id] = RequirementStatus.NOT_MET
        else:
            statuses[requirement.requirement_id] = RequirementStatus.UNVERIFIED
    return statuses


def write_statuses(scope: CompletionScope, settings: CompletionSettings, *, now: float | None = None) -> dict[str, RequirementStatus]:
    """Persist the verdicts onto the contract (used before rendering a report)."""
    statuses = requirement_statuses(scope, settings, now=now)
    for requirement in scope.contract.requirements:
        requirement.status = statuses.get(requirement.requirement_id, RequirementStatus.UNVERIFIED)
    return statuses


__all__ = ["reconcile", "requirement_statuses", "write_statuses"]
