"""Acceptance contracts: what "done" means, fixed before the work starts.

The contract is built once from the request and then **validated
deterministically** — a model may propose it, but a model is never the sole
validator of its own plan. ``validate_contract`` is a pure function over the
contract, the task DAG and the gate set, and its ``error`` findings are what
stop dispatch and finalization (``ContractInvalid``), while ``warning``
findings are advisory.

What the validator refuses, in one sentence each:

* a contract with no obligations at all, or requirements bound to no gate —
  "finished implementation" is not a proof obligation;
* an empty expectation — a gate that measures nothing can never fail;
* an unregistered check — only reviewed check implementations may run;
* a requirement with no owner task — the quietly-forgotten secondary
  constraint the plan calls out;
* a parent with children but no integration criterion — individually
  successful children are not a functioning whole;
* a digest that no longer matches its contents — a contract edited without a
  revision change.

``extract_request_clauses`` is a *heuristic* splitter used only by request
reconciliation (``reconciliation.py``); it is labelled as heuristic wherever
its result reaches a report, because "the request had six clauses" is a
reading of prose, not a measurement.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import Contract, ContractFinding, Gate, Requirement, TaskNode

#: Phrases that describe effort or feeling instead of an observable outcome.
#: Matching one in a *gate title* is a warning (titles are display); matching
#: one where the expectation should be is an error, already covered by
#: ``Expectation.is_empty()``.
VAGUE_PHRASES: tuple[str, ...] = (
    "works correctly",
    "works well",
    "is advanced",
    "is robust",
    "looks good",
    "finish implementation",
    "complete the work",
    "properly implemented",
    "tests pass",
    "all tests pass",
    "make sure it works",
    "high quality",
    "as expected",
)

#: Tokens too generic to connect a request clause to a requirement intent.
_STOPWORDS = frozenset(
    """
    a an the and or but if then else for to of in on at by with from as is are be been being
    it its this that these those i you we they he she them us my your our their will would
    can could should must may might do does did done have has had not no yes so than then
    there here when where which who whom what how all any some more most other into over
    under about after before again further once please need needs wants want make makes made
    using use used also just only very much many
    """.split()
)

_CLAUSE_SPLIT = re.compile(r"[\n;•]+|\s(?:and then|and also|also,|finally,)\s", re.IGNORECASE)
_NUMBERING = re.compile(r"^\s*(?:[-*+]|\d+[.)]|[:>])\s*")
_WORD = re.compile(r"[A-Za-z0-9_./-]+")

#: Share of a clause's distinct words a requirement must name before the
#: clause counts as represented. High on purpose: the failure this audit
#: exists to catch is the easy half of a request standing in for the whole.
CLAUSE_COVERAGE_FLOOR = 0.75

#: Minimum words for a fragment to count as an obligation ("Thanks!" is not
#: one). Counted over words rather than significant tokens — see
#: :func:`extract_request_clauses`.
MIN_CLAUSE_WORDS = 3


class RequirementInput(BaseModel):
    """Caller-supplied requirement before ids and revisions are stamped."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(default="", max_length=64)
    intent: str = Field(min_length=1, max_length=1000)
    mandatory: bool = True
    observable: str = Field(default="", max_length=1000, description="How it will be proven; empty is a validation error for a mandatory requirement.")
    task_id: str = ""
    gate_ids: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=2000)


def canonical_json(payload: Any) -> str:
    """Stable serialization: sorted keys, no whitespace variance, UTF-8."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str)


def digest_of(payload: Any) -> str:
    """SHA-256 over the canonical form of *payload*."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def contract_digest(contract: Contract) -> str:
    """Digest over the obligations only — findings and timestamps excluded.

    Re-validating a contract must not change its digest (validation is a
    *reading* of the contract), while adding, removing or rewording a
    requirement must.
    """
    return digest_of(
        {
            "mode": contract.mode,
            "version": contract.version,
            "requirements": [
                {
                    "id": req.requirement_id,
                    "intent": req.intent,
                    "mandatory": req.mandatory,
                    "observable": req.observable,
                }
                for req in sorted(contract.requirements, key=lambda r: r.requirement_id)
            ],
        }
    )


def build_contract(
    *,
    contract_id: str,
    mode: str,
    requirements: list[RequirementInput],
    request_revision: int = 1,
    now: float | None = None,
) -> Contract:
    """Turn caller requirements into the persisted contract skeleton.

    Ids are assigned here (``req-1`` …) so a caller that supplies none still
    gets a stable, addressable inventory; supplied ids are validated for
    uniqueness by :func:`validate_contract` rather than silently rewritten.
    """
    moment = time.time() if now is None else now
    stamped = [
        Requirement(
            requirement_id=item.requirement_id or f"req-{index}",
            intent=item.intent.strip(),
            mandatory=item.mandatory,
            observable=item.observable.strip(),
            task_id=item.task_id,
            gate_ids=list(item.gate_ids),
            request_revision=request_revision,
            notes=item.notes,
        )
        for index, item in enumerate(requirements, start=1)
    ]
    contract = Contract(
        contract_id=contract_id,
        version=1,
        mode=mode,
        requirements=stamped,
        created_at=moment,
        updated_at=moment,
    )
    contract.digest = contract_digest(contract)
    return contract


def _tokens(text: str) -> set[str]:
    return {token for token in _WORD.findall(text.lower()) if token not in _STOPWORDS and len(token) > 1}


def extract_request_clauses(request_text: str) -> list[str]:
    """Split a request into candidate obligations (heuristic, disclosed as such).

    Splits on line breaks, bullets and explicit sequencing words, then strips
    enumeration markers. Trivial fragments (< 3 **words**) are dropped:
    "Thanks!" is not an obligation.

    The count is of words, not of distinct significant tokens, and that
    distinction is load-bearing: "publish the dashboard" leaves only two
    non-stopword tokens, so counting those discarded the very clause this
    audit exists to surface — a dropped obligation read as a clean sweep.
    """
    clauses: list[str] = []
    for chunk in _CLAUSE_SPLIT.split(request_text or ""):
        text = _NUMBERING.sub("", chunk).strip()
        text = re.sub(r"\s+", " ", text).strip(" .,:!?")
        if len(text.split()) < MIN_CLAUSE_WORDS:
            continue
        clauses.append(text)
    return clauses


def clause_covered(clause: str, requirement: Requirement) -> bool:
    """Whether *requirement* plausibly represents *clause*.

    Deliberately generous on purpose: reconciliation must flag anything it
    cannot tie to an obligation, and an under-match shows up as an honest
    ``unrepresented`` line rather than a silently dropped requirement.

    Generosity is directional, and the direction is load-bearing. A clause the
    requirement subsumes is covered; a requirement that is merely a *substring*
    of a longer clause is not, because that is precisely the dropped-clause
    case — "Write the report" must not cover "Write the report and publish the
    dashboard", or the audit would be blind to the half the plan left out. The
    ratio therefore measures how much of the **clause** the requirement names,
    never how much of the requirement the clause touches.
    """
    clause_tokens = _tokens(clause)
    intent_tokens = _tokens(requirement.intent) | _tokens(requirement.observable)
    if not clause_tokens or not intent_tokens:
        return False
    if clause.lower() in requirement.intent.lower():
        return True
    overlap = clause_tokens & intent_tokens
    if not overlap:
        return False
    return len(overlap) / len(clause_tokens) >= CLAUSE_COVERAGE_FLOOR


def validate_contract(
    contract: Contract,
    *,
    tasks: dict[str, TaskNode],
    gates: dict[str, Gate],
    known_checks: frozenset[str] | set[str],
) -> list[ContractFinding]:
    """Deterministic contract validation. ``error`` findings block dispatch."""
    findings: list[ContractFinding] = []

    def error(code: str, message: str, subject: str = "") -> None:
        findings.append(ContractFinding(code=code, severity="error", message=message, subject_id=subject))

    def warn(code: str, message: str, subject: str = "") -> None:
        findings.append(ContractFinding(code=code, severity="warning", message=message, subject_id=subject))

    if not contract.requirements:
        error("contract_empty", "the contract declares no requirements, so there is nothing that could prove the request was carried out")
    if not gates:
        error("no_gates", "the contract has no acceptance gates; every requirement needs at least one observable check or an explicit manual review")

    seen: set[str] = set()
    for req in contract.requirements:
        if req.requirement_id in seen:
            error("duplicate_id", f"duplicate requirement id {req.requirement_id!r}", req.requirement_id)
        seen.add(req.requirement_id)
        if req.intent.lower() in {phrase for phrase in VAGUE_PHRASES}:
            warn("vague_requirement", f"requirement intent {req.intent!r} states effort, not an observable outcome", req.requirement_id)
        if req.mandatory:
            if not req.task_id:
                error("requirement_unassigned", f"mandatory requirement {req.requirement_id!r} is assigned to no task", req.requirement_id)
            elif req.task_id not in tasks:
                error("requirement_task_missing", f"requirement {req.requirement_id!r} names task {req.task_id!r}, which is not in the plan", req.requirement_id)
            if not req.observable.strip():
                error("requirement_no_observable", f"mandatory requirement {req.requirement_id!r} declares no observable", req.requirement_id)
            if not req.gate_ids:
                error("requirement_without_gate", f"mandatory requirement {req.requirement_id!r} is not bound to any gate", req.requirement_id)
            for gate_id in req.gate_ids:
                if gate_id not in gates:
                    error("requirement_gate_missing", f"requirement {req.requirement_id!r} names gate {gate_id!r}, which is not in the plan", req.requirement_id)

    gate_seen: set[str] = set()
    for gate in gates.values():
        if gate.gate_id in gate_seen:
            error("duplicate_id", f"duplicate gate id {gate.gate_id!r}", gate.gate_id)
        gate_seen.add(gate.gate_id)
        if gate.task_id not in tasks:
            error("gate_unknown_task", f"gate {gate.gate_id!r} belongs to task {gate.task_id!r}, which is not in the plan", gate.gate_id)
        elif gate.gate_id not in tasks[gate.task_id].gate_ids:
            warn("gate_not_attached", f"gate {gate.gate_id!r} is not listed in task {gate.task_id!r} gate_ids", gate.gate_id)
        if gate.check_ref not in known_checks:
            error("gate_unregistered_check", f"gate {gate.gate_id!r} names check {gate.check_ref!r}, which is not a registered check implementation", gate.gate_id)
        if gate.expectation.is_empty():
            error(
                "gate_without_expectation",
                f"gate {gate.gate_id!r} measures nothing — an expectation must assert an exit code, markers, sizes, an artifact or a status code",
                gate.gate_id,
            )
        lowered = gate.title.lower()
        if any(phrase in lowered for phrase in VAGUE_PHRASES):
            warn("vague_gate_title", f"gate {gate.gate_id!r} is titled {gate.title!r}, which describes effort rather than an observation", gate.gate_id)

    for task in tasks.values():
        if task.parent_id is not None and task.parent_id not in tasks:
            error("task_parent_missing", f"task {task.task_id!r} names parent {task.parent_id!r}, which is not in the plan", task.task_id)
        for dep in task.depends_on:
            if dep not in tasks:
                error("task_dependency_missing", f"task {task.task_id!r} depends on {dep!r}, which is not in the plan", task.task_id)
            if dep == task.task_id:
                error("task_self_dependency", f"task {task.task_id!r} depends on itself", task.task_id)
        if not task.expected_outputs and not task.gate_ids and not task.integration:
            error("task_no_output", f"task {task.task_id!r} declares no expected output and no gate, so its completion is unmeasurable", task.task_id)
        children = [candidate for candidate in tasks.values() if candidate.parent_id == task.task_id]
        if children and not task.integration:
            integration_gates = [gate for gate in gates.values() if gate.task_id == task.task_id and gate.integration]
            if not integration_gates:
                error(
                    "parent_without_integration_gate",
                    f"parent task {task.task_id!r} has children but declares no integration criterion; individually passing children are not a functioning whole",
                    task.task_id,
                )

    stored = contract.digest
    recomputed = contract_digest(contract)
    if stored and stored != recomputed:
        error(
            "digest_mismatch",
            "the contract changed after its digest was computed; bump the request revision and re-stamp the digest before dispatch",
            contract.contract_id,
        )

    return findings


def apply_validation(contract: Contract, findings: list[ContractFinding], *, now: float | None = None) -> Contract:
    """Write findings + validity onto the contract.

    The digest is **not** re-stamped here: it is the fingerprint of the
    obligations, stamped when they are built or revised (``build_contract``),
    so a contract edited behind the builder's back still reports
    ``digest_mismatch`` instead of silently blessing the edit. Validation is a
    reading of the contract, and re-stamping it would make the check
    self-fulfilling.
    """
    moment = time.time() if now is None else now
    contract.findings = findings
    contract.valid = not any(finding.severity == "error" for finding in findings)
    contract.validated_at = moment
    contract.updated_at = moment
    return contract


__all__ = [
    "CLAUSE_COVERAGE_FLOOR",
    "MIN_CLAUSE_WORDS",
    "VAGUE_PHRASES",
    "RequirementInput",
    "apply_validation",
    "build_contract",
    "canonical_json",
    "clause_covered",
    "contract_digest",
    "digest_of",
    "extract_request_clauses",
    "validate_contract",
]
