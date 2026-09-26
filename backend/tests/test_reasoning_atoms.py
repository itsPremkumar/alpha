from __future__ import annotations

import pytest

from alpha.reasoning.atoms import (
    AtomContractError,
    AtomContractionRefused,
    ContractionStrategy,
    account_token_usage,
    contract,
    decompose,
    equivalent_key,
    estimate_tokens,
    expand,
    frontier,
    is_acyclic,
)
from alpha.reasoning.models import AtomicThought

PROBLEM = "Reduce the deployment rollback failure rate without weakening the production approval gate."
CRITERIA = ("Rollback succeeds in the focused reproducer.", "No existing regression test changes meaning.")
CONSTRAINTS = ("Do not modify external authorization policy.", "Keep the change reversible.", "Preserve audit events.")


def atom(atom_id: str, statement: str, *dependencies: str) -> AtomicThought:
    return AtomicThought(
        id=atom_id,
        question=f"Resolve {atom_id}",
        statement=statement,
        dependency_ids=list(dependencies),
        answer_equivalence_key=equivalent_key(PROBLEM, CRITERIA, CONSTRAINTS),
        constraints=list(CONSTRAINTS),
        success_criteria=list(CRITERIA),
    )


def test_decomposition_is_deterministic_acyclic_and_frontier_is_stable() -> None:
    atoms = [
        atom("c", "Confirm the independent rollback test passes.", "b"),
        atom("a", "Reproduce the rollback failure from the incident record."),
        atom("b", "Patch the smallest reversible rollback boundary.", "a"),
    ]
    first = decompose(PROBLEM, atoms, success_criteria=CRITERIA, constraints=CONSTRAINTS)
    second = decompose(PROBLEM, list(reversed(atoms)), success_criteria=CRITERIA, constraints=CONSTRAINTS)

    assert [item.id for item in first.atoms] == ["a", "b", "c"]
    assert first.model_dump() == second.model_dump()
    assert is_acyclic(first) is True
    assert [item.id for item in frontier(first)] == ["a"]
    assert first.max_depth == 2


def test_decomposition_refuses_cycles_and_cap_violations() -> None:
    with pytest.raises(AtomContractError, match="cycle"):
        decompose(
            PROBLEM,
            [atom("a", "A", "b"), atom("b", "B", "a")],
            success_criteria=CRITERIA,
            constraints=CONSTRAINTS,
        )
    with pytest.raises(AtomContractError, match="unknown dependency"):
        decompose(PROBLEM, [atom("a", "A", "missing")], success_criteria=CRITERIA, constraints=CONSTRAINTS)
    with pytest.raises(AtomContractError, match="max_atoms"):
        decompose(
            PROBLEM,
            [atom("a", "A"), atom("b", "B")],
            success_criteria=CRITERIA,
            constraints=CONSTRAINTS,
            max_atoms=1,
        )
    with pytest.raises(AtomContractError, match="max_width"):
        decompose(
            PROBLEM,
            [atom("a", "A"), atom("b", "B")],
            success_criteria=CRITERIA,
            constraints=CONSTRAINTS,
            max_width=1,
        )
    with pytest.raises(AtomContractError, match="max_depth"):
        decompose(
            PROBLEM,
            [atom("a", "A"), atom("b", "B", "a")],
            success_criteria=CRITERIA,
            constraints=CONSTRAINTS,
            max_depth=0,
        )


def test_contraction_preserves_every_normative_clause_and_expansion_restores_dag() -> None:
    dag = decompose(
        PROBLEM,
        [
            atom("a", "Reproduce the incident with the recorded release artifact."),
            atom("b", "Identify the first irreversible rollback transition.", "a"),
            atom("c", "Apply the minimal reversible correction.", "b"),
        ],
        success_criteria=CRITERIA,
        constraints=CONSTRAINTS,
    )
    state = contract(dag)

    assert state.answer_equivalence_key == dag.answer_equivalence_key
    assert state.contracted_from == ["a", "b", "c"]
    for clause in (*CRITERIA, *CONSTRAINTS):
        assert clause in state.statement
    assert state.confidence.value == 0.5
    assert state.confidence.calibration_status.value == "heuristic"
    assert expand(state).model_dump() == dag.model_dump()


def test_contraction_refuses_and_names_clauses_it_would_drop() -> None:
    dag = decompose(
        PROBLEM,
        [atom("a", "Reproduce the incident."), atom("b", "Patch the boundary.", "a")],
        success_criteria=CRITERIA,
        constraints=CONSTRAINTS,
    )
    with pytest.raises(AtomContractionRefused) as excinfo:
        contract(dag, ContractionStrategy(preserve_constraints=False))
    message = str(excinfo.value)
    assert "would drop" in message
    assert "Do not modify external authorization policy." in message
    assert "Rollback succeeds in the focused reproducer." not in message

    with pytest.raises(AtomContractionRefused) as excinfo:
        contract(dag, ContractionStrategy(preserve_success_criteria=False))
    assert "Rollback succeeds in the focused reproducer." in str(excinfo.value)

    with pytest.raises(AtomContractionRefused, match="max_statement_chars"):
        contract(dag, ContractionStrategy(max_statement_chars=256))


def test_token_accounting_measures_history_growth_versus_contracted_states() -> None:
    all_atoms = [
        atom("a", "Reproduce the incident and capture the failing release artifact digest."),
        atom("b", "Trace the first irreversible transition in the rollback path.", "a"),
        atom("c", "Select the minimal reversible boundary using the recorded approval policy.", "b"),
        atom("d", "Apply the correction and preserve every audit event.", "c"),
        atom("e", "Run the focused reproducer and independent regression verification.", "d"),
        atom("f", "Prepare the operator summary with limitations and evidence references.", "e"),
    ]
    step_outputs = [
        (
            f"Step {index}: resolved one dependency layer and recorded a bounded observation. "
            "The observation includes the selected boundary, the evidence identifiers that justify it, "
            "the verification command that will exercise it, and the limitations that remain unresolved. "
            "This synthetic step text makes history-carrying cost explicit and measurable."
        )
        for index in range(len(all_atoms))
    ]
    contracted_states: list[str] = []
    for index in range(len(all_atoms)):
        remaining = all_atoms[index:]
        # Make the remaining graph independent so each state contracts cleanly.
        independent = [
            AtomicThought(
                id=item.id,
                question=item.question,
                statement=item.statement,
                answer_equivalence_key=item.answer_equivalence_key,
                constraints=item.constraints,
                success_criteria=item.success_criteria,
            )
            for item in remaining
        ]
        dag = decompose(PROBLEM, independent, success_criteria=CRITERIA, constraints=CONSTRAINTS)
        contracted_states.append(contract(dag).statement)

    result = account_token_usage(PROBLEM, CRITERIA, CONSTRAINTS, step_outputs, contracted_states)

    assert result.steps == 6
    assert result.history_per_step[-1] > result.history_per_step[0]
    assert result.history_total_tokens > result.contracted_total_tokens
    assert result.saved_tokens > 0
    assert result.break_even_step is not None
    assert result.estimator == "utf8-bytes/4-ceiling"
    assert result.history_final_step_tokens > result.contracted_final_step_tokens


def test_token_accounting_does_not_claim_a_break_even_for_a_one_step_problem() -> None:
    dag = decompose(
        PROBLEM,
        [atom("a", "Answer directly from the already verified incident record.")],
        success_criteria=CRITERIA,
        constraints=CONSTRAINTS,
    )
    contracted = contract(dag).statement
    result = account_token_usage(PROBLEM, CRITERIA, CONSTRAINTS, ["A short bounded answer."], [contracted])
    assert result.break_even_step is None
    assert result.contracted_total_tokens > result.history_total_tokens


def test_token_estimator_is_deterministic_and_empty_safe() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
