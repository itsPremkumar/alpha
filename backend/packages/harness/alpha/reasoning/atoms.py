"""Atom-of-Thoughts dependency DAG and answer-equivalent contraction.

Design input: Teng et al., *Atom of Thoughts for Markov LLM Test-Time Scaling*,
arXiv:2502.12018v4 (NeurIPS 2025).  The paper models a reasoning trajectory as
a temporary dependency DAG and contracts that DAG into the next Markov state.
This module implements an original, deterministic contract over Alpha's typed
:class:`~alpha.reasoning.models.AtomicThought` records: decomposition is
acyclic and cap-bounded, contraction preserves every stated constraint and
success criterion or refuses with an explanation, and expansion restores the
original DAG.

The paper's accuracy results are not reproduced here.  ``account_token_usage``
provides the measurable structural claim on a caller-supplied problem using an
explicit model-independent estimator.  Sleep-time compute (arXiv:2504.13171) is
a related design input, but this module starts no background precompute loop:
a host may cache a contracted state only after making its own predictability
decision.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alpha.reasoning.models import AtomicThought, ClampedModel, UncertaintyCalibration, UncertaintyEstimate

__all__ = [
    "AtomContractError",
    "AtomContractionRefused",
    "AtomDAG",
    "AtomEdge",
    "AtomicState",
    "ContractionStrategy",
    "TOKEN_ESTIMATOR_NAME",
    "TokenAccountingResult",
    "account_token_usage",
    "contract",
    "decompose",
    "equivalent_key",
    "estimate_tokens",
    "expand",
    "frontier",
    "is_acyclic",
]

TOKEN_ESTIMATOR_NAME = "utf8-bytes/4-ceiling"


class AtomContractError(ValueError):
    """A decomposition/contraction invariant would be violated."""


class AtomContractionRefused(AtomContractError):
    """A contraction could not preserve its answer-equivalence contract."""


def _normalize_clause(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _normalized_tuple(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({_normalize_clause(value) for value in values if value.strip()}))


def equivalent_key(
    problem: str,
    success_criteria: Sequence[str] = (),
    constraints: Sequence[str] = (),
) -> str:
    """Return a stable answer-equivalence key.

    The key covers the question and the normative clauses, but not the current
    decomposition.  Two states with the same key therefore describe the same
    answer obligation even when their sub-atom lineage differs.
    """

    payload = {
        "problem": _normalize_clause(problem),
        "success_criteria": _normalized_tuple(success_criteria),
        "constraints": _normalized_tuple(constraints),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    """Estimate tokens without a model download or network access.

    The estimator is deliberately named and deterministic: UTF-8 bytes divided
    by four, rounded up.  It is a structural comparison tool, not a claim about
    any provider's tokenizer.
    """

    if not text:
        return 0
    return math.ceil(len(text.encode("utf-8")) / 4)


class AtomEdge(BaseModel):
    source: str = Field(min_length=1, max_length=128, description="Dependency atom id.")
    target: str = Field(min_length=1, max_length=128, description="Dependent atom id.")
    kind: str = Field(default="requires", min_length=1, max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True)


class AtomDAG(BaseModel):
    """A validated, deterministic dependency DAG."""

    id: str = Field(min_length=1, max_length=128)
    problem: str = Field(min_length=1, max_length=16000)
    success_criteria: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    atoms: list[AtomicThought] = Field(default_factory=list, max_length=1000)
    edges: list[AtomEdge] = Field(default_factory=list, max_length=10_000)
    answer_equivalence_key: str = Field(min_length=1, max_length=128)
    max_depth: int = Field(default=0, ge=0)
    max_width: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_structure(self) -> AtomDAG:
        _topological_atoms(self.atoms)
        expected = {(edge.source, edge.target) for edge in self.edges}
        declared = {(dependency, atom.id) for atom in self.atoms for dependency in atom.dependency_ids}
        if expected != declared:
            raise ValueError("atom dependency_ids and edges must describe exactly the same dependency set")
        if self.answer_equivalence_key != equivalent_key(self.problem, self.success_criteria, self.constraints):
            raise ValueError("answer_equivalence_key does not match problem, success criteria, and constraints")
        return self


def _topological_atoms(atoms: Sequence[AtomicThought]) -> list[AtomicThought]:
    by_id: dict[str, AtomicThought] = {}
    for atom in atoms:
        if atom.id in by_id:
            raise AtomContractError(f"duplicate atom id: {atom.id!r}")
        by_id[atom.id] = atom

    indegree = {atom_id: 0 for atom_id in by_id}
    children: dict[str, list[str]] = {atom_id: [] for atom_id in by_id}
    for atom in atoms:
        for dependency in atom.dependency_ids:
            if dependency not in by_id:
                raise AtomContractError(f"atom {atom.id!r} references unknown dependency {dependency!r}")
            children[dependency].append(atom.id)
            indegree[atom.id] += 1

    ready = [atom_id for atom_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    ordered: list[AtomicThought] = []
    while ready:
        atom_id = heapq.heappop(ready)
        ordered.append(by_id[atom_id])
        for child_id in sorted(children[atom_id]):
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                heapq.heappush(ready, child_id)

    if len(ordered) != len(by_id):
        cyclic = sorted(atom_id for atom_id, degree in indegree.items() if degree > 0)
        raise AtomContractError(f"atom dependency graph contains a cycle involving: {cyclic}")
    return ordered


def _depths(ordered: Sequence[AtomicThought]) -> dict[str, int]:
    by_id = {atom.id: atom for atom in ordered}
    depths = {atom.id: 0 for atom in ordered}
    for atom in ordered:
        for dependency in atom.dependency_ids:
            depths[atom.id] = max(depths[atom.id], depths[by_id[dependency].id] + 1)
    return depths


def decompose(
    problem: str,
    atoms: Sequence[AtomicThought],
    *,
    success_criteria: Sequence[str] = (),
    constraints: Sequence[str] = (),
    max_atoms: int = 12,
    max_depth: int = 4,
    max_width: int = 6,
) -> AtomDAG:
    """Validate and order sub-atoms into a dependency DAG.

    Caps are hard refusals, not truncations: silently dropping a sub-question
    would change the problem.  Ordering is topological with an id tie-break so
    the same input always produces the same DAG.
    """

    if max_atoms < 1:
        raise AtomContractError("max_atoms must be at least 1")
    if max_depth < 0:
        raise AtomContractError("max_depth cannot be negative")
    if max_width < 1:
        raise AtomContractError("max_width must be at least 1")

    key = equivalent_key(problem, success_criteria, constraints)
    candidates = [atom.model_copy(deep=True) for atom in atoms]
    if not candidates:
        candidates = [
            AtomicThought(
                id="atom-0",
                question=problem,
                statement=problem,
                answer_equivalence_key=key,
                constraints=list(constraints),
                success_criteria=list(success_criteria),
                token_estimate=max(1, estimate_tokens(problem)),
            )
        ]

    ordered = _topological_atoms(candidates)
    if len(ordered) > max_atoms:
        raise AtomContractError(f"decomposition has {len(ordered)} atoms, exceeding max_atoms={max_atoms}")

    depths = _depths(ordered)
    observed_depth = max(depths.values(), default=0)
    if observed_depth > max_depth:
        raise AtomContractError(f"decomposition depth {observed_depth} exceeds max_depth={max_depth}")

    width_at_depth: dict[int, int] = {}
    for depth in depths.values():
        width_at_depth[depth] = width_at_depth.get(depth, 0) + 1
    observed_width = max(width_at_depth.values(), default=0)
    if observed_width > max_width:
        raise AtomContractError(f"decomposition width {observed_width} exceeds max_width={max_width}")

    edges = [AtomEdge(source=dependency, target=atom.id) for atom in ordered for dependency in sorted(atom.dependency_ids)]
    return AtomDAG(
        id=f"dag-{key[:16]}",
        problem=problem,
        success_criteria=list(success_criteria),
        constraints=list(constraints),
        atoms=ordered,
        edges=edges,
        answer_equivalence_key=key,
        max_depth=observed_depth,
        max_width=observed_width,
    )


def frontier(dag: AtomDAG) -> tuple[AtomicThought, ...]:
    """Return dependency-free atoms in deterministic topological order."""

    ordered = _topological_atoms(dag.atoms)
    indegree = {atom.id: len(atom.dependency_ids) for atom in ordered}
    return tuple(atom for atom in ordered if indegree[atom.id] == 0)


def is_acyclic(dag: AtomDAG) -> bool:
    """Return whether a validated DAG is acyclic."""

    try:
        _topological_atoms(dag.atoms)
    except AtomContractError:
        return False
    return True


class ContractionStrategy(ClampedModel):
    """How much structure a contraction must retain.

    The unsafe switches exist so the refusal path is testable and explicit.
    Normal callers keep both preservation flags true.
    """

    name: str = Field(default="structured", min_length=1, max_length=64)
    preserve_constraints: bool = True
    preserve_success_criteria: bool = True
    max_statement_chars: int = Field(default=16_000, ge=256)

    @model_validator(mode="after")
    def _clamp_limit(self) -> ContractionStrategy:
        self.clamp_field("max_statement_chars", 256, 1_000_000, integer=True)
        return self


class AtomicState(BaseModel):
    """One answer-equivalent Markov state contracted from a temporary DAG."""

    id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=16000)
    success_criteria: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    statement: str = Field(min_length=1, max_length=1_000_000)
    dependency_edges: list[AtomEdge] = Field(default_factory=list, max_length=10_000)
    answer_equivalence_key: str = Field(min_length=1, max_length=128)
    contracted_from: list[str] = Field(default_factory=list, max_length=1000)
    sub_atoms: list[AtomicThought] = Field(default_factory=list, max_length=1000)
    confidence: UncertaintyEstimate
    token_estimate: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_lineage(self) -> AtomicState:
        ids = [atom.id for atom in self.sub_atoms]
        if len(ids) != len(set(ids)):
            raise ValueError("contracted state contains duplicate sub-atom ids")
        if self.contracted_from != ids:
            raise ValueError("contracted_from must be the exact deterministic sub-atom id order")
        return self


def _validate_strategy(strategy: str | ContractionStrategy | Mapping[str, object]) -> ContractionStrategy:
    if isinstance(strategy, ContractionStrategy):
        return strategy
    if isinstance(strategy, str):
        return ContractionStrategy(name=strategy)
    return ContractionStrategy.model_validate(strategy)


def contract(dag: AtomDAG, strategy: str | ContractionStrategy | Mapping[str, object] = ContractionStrategy()) -> AtomicState:
    """Contract a DAG into one state that keeps the same answer obligation.

    A contraction is refused rather than allowed to drop a stated constraint or
    success criterion.  The refusal names every clause at risk.
    """

    resolved = _validate_strategy(strategy)
    ordered = _topological_atoms(dag.atoms)

    missing_constraints = [] if resolved.preserve_constraints else list(dag.constraints)
    missing_criteria = [] if resolved.preserve_success_criteria else list(dag.success_criteria)
    if missing_constraints or missing_criteria:
        parts = []
        if missing_constraints:
            parts.append(f"constraints={missing_constraints!r}")
        if missing_criteria:
            parts.append(f"success_criteria={missing_criteria!r}")
        raise AtomContractionRefused("refusing contraction because it would drop " + "; ".join(parts))

    lines = [
        f"Answer-equivalent contracted state for: {dag.problem}",
        "Sub-atoms:",
    ]
    for atom in ordered:
        dependency_text = ", ".join(sorted(atom.dependency_ids)) or "none"
        lines.append(f"- [{atom.id}] {atom.statement} (depends on: {dependency_text})")
    if dag.success_criteria:
        lines.append("Success criteria that must still hold:")
        lines.extend(f"- {criterion}" for criterion in dag.success_criteria)
    if dag.constraints:
        lines.append("Constraints that must still hold:")
        lines.extend(f"- {constraint}" for constraint in dag.constraints)
    statement = "\n".join(lines)

    if len(statement) > resolved.max_statement_chars:
        at_risk = [*dag.success_criteria, *dag.constraints]
        raise AtomContractionRefused(f"refusing contraction because the answer-equivalent statement would exceed max_statement_chars={resolved.max_statement_chars}; clauses at risk: {at_risk!r}")

    estimates = [atom.confidence for atom in ordered]
    conservative_value = min(estimate.value for estimate in estimates)
    all_calibrated = all(estimate.calibration_status is UncertaintyCalibration.CALIBRATED for estimate in estimates)
    calibration_ref = None
    if all_calibrated:
        calibration_ref = ";".join(estimate.calibration_ref or "" for estimate in estimates)[:512]
    confidence = UncertaintyEstimate(
        value=conservative_value,
        calibration_status=UncertaintyCalibration.CALIBRATED if all_calibrated else UncertaintyCalibration.HEURISTIC,
        method="conservative minimum of contracted atom estimates",
        calibration_ref=calibration_ref,
        note="A contraction does not increase confidence.",
    )
    lineage_digest = hashlib.sha256(",".join(atom.id for atom in ordered).encode("utf-8")).hexdigest()[:12]
    state_id = f"state-{dag.answer_equivalence_key[:12]}-{lineage_digest}"
    return AtomicState(
        id=state_id,
        question=dag.problem,
        success_criteria=list(dag.success_criteria),
        constraints=list(dag.constraints),
        statement=statement,
        dependency_edges=[edge.model_copy(deep=True) for edge in dag.edges],
        answer_equivalence_key=dag.answer_equivalence_key,
        contracted_from=[atom.id for atom in ordered],
        sub_atoms=[atom.model_copy(deep=True) for atom in ordered],
        confidence=confidence,
        token_estimate=max(1, estimate_tokens(statement)),
    )


def expand(state: AtomicState) -> AtomDAG:
    """Re-inflate a contracted state into its original dependency DAG."""

    if not state.sub_atoms:
        raise AtomContractError("contracted state has no sub-atoms to expand")
    ordered = _topological_atoms(state.sub_atoms)
    if [atom.id for atom in ordered] != state.contracted_from:
        raise AtomContractError("sub-atom order does not match contracted_from lineage")
    depths = _depths(ordered)
    width_at_depth: dict[int, int] = {}
    for depth in depths.values():
        width_at_depth[depth] = width_at_depth.get(depth, 0) + 1
    return AtomDAG(
        id=f"dag-{state.answer_equivalence_key[:16]}",
        problem=state.question,
        success_criteria=list(state.success_criteria),
        constraints=list(state.constraints),
        atoms=[atom.model_copy(deep=True) for atom in ordered],
        edges=[edge.model_copy(deep=True) for edge in state.dependency_edges],
        answer_equivalence_key=state.answer_equivalence_key,
        max_depth=max(depths.values(), default=0),
        max_width=max(width_at_depth.values(), default=0),
    )


class TokenAccountingResult(BaseModel):
    estimator: str
    steps: int
    history_per_step: list[int]
    contracted_per_step: list[int]
    history_total_tokens: int
    contracted_total_tokens: int
    saved_tokens: int
    saved_fraction: float
    break_even_step: int | None
    history_final_step_tokens: int
    contracted_final_step_tokens: int

    model_config = ConfigDict(extra="forbid", frozen=True)


def account_token_usage(
    problem: str,
    success_criteria: Sequence[str],
    constraints: Sequence[str],
    step_outputs: Sequence[str],
    contracted_state_per_step: Sequence[str],
) -> TokenAccountingResult:
    """Measure history-carrying versus contracted-state token cost.

    History mode resends the problem, normative clauses, and every prior step
    output.  Contracted mode resends one answer-equivalent state per step.  The
    estimator is deterministic and disclosed; the result measures structure, not
    provider-specific tokenization or answer quality.
    """

    if len(step_outputs) != len(contracted_state_per_step):
        raise ValueError("step_outputs and contracted_state_per_step must have equal length")
    if not step_outputs:
        raise ValueError("token accounting requires at least one step")

    base = "\n".join(
        [
            f"PROBLEM: {problem}",
            "SUCCESS CRITERIA:",
            *(f"- {criterion}" for criterion in success_criteria),
            "CONSTRAINTS:",
            *(f"- {constraint}" for constraint in constraints),
        ]
    )
    history_per_step: list[int] = []
    contracted_per_step: list[int] = []
    history_cumulative = 0
    contracted_cumulative = 0
    break_even_step: int | None = None
    history: list[str] = []

    for index, step_output in enumerate(step_outputs):
        history_input = base if not history else base + "\nPRIOR STEPS:\n" + "\n".join(history)
        history_tokens = estimate_tokens(history_input)
        contracted_tokens = estimate_tokens(contracted_state_per_step[index])
        history_per_step.append(history_tokens)
        contracted_per_step.append(contracted_tokens)
        history_cumulative += history_tokens
        contracted_cumulative += contracted_tokens
        if break_even_step is None and contracted_cumulative < history_cumulative:
            break_even_step = index + 1
        history.append(step_output)

    history_total = sum(history_per_step)
    contracted_total = sum(contracted_per_step)
    saved = history_total - contracted_total
    saved_fraction = saved / history_total if history_total else 0.0
    return TokenAccountingResult(
        estimator=TOKEN_ESTIMATOR_NAME,
        steps=len(step_outputs),
        history_per_step=history_per_step,
        contracted_per_step=contracted_per_step,
        history_total_tokens=history_total,
        contracted_total_tokens=contracted_total,
        saved_tokens=saved,
        saved_fraction=saved_fraction,
        break_even_step=break_even_step,
        history_final_step_tokens=history_per_step[-1],
        contracted_final_step_tokens=contracted_per_step[-1],
    )
