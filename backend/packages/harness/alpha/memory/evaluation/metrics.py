"""Deterministic metric definitions for memory evaluation.

Every function in this module is a pure measurement over runner output.  A
``None`` result means *undefined/no observation* (for example, no required
refusal was present); it is never silently converted to zero.  This distinction
is what lets a report distinguish an unavailable backend from a measured
failure.

Formulas used by the suite are documented beside the corresponding function:

* ``recall@k = |retrieved[:k] ∩ relevant| / |relevant|``.
* ``accuracy = correct observations / observations``.
* ``abstention_rate = correct required refusals / required refusals``.
* ``update_correctness = current answer matches AND no superseded value occurs``.
* ``contamination_rate = answers citing foreign-scope records / measured answers``.
* ``write_precision = expected-stored IDs ∩ stored IDs / stored IDs``.
* ``evidence_traceability = non-abstention answers with valid returned IDs / non-abstention answers``.
* ``token_efficiency = composed answer tokens / useful cited facts``.

The runner records the raw numerators and denominators in each outcome/result;
the suite aggregates those counts rather than averaging rounded percentages.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from alpha.memory.evaluation.models import Case, CaseResult, QuestionOutcome

_TOKEN_RE = re.compile(r"\b[\w]+(?:[-'][\w]+)*\b", re.UNICODE)
_REFUSAL_RE = re.compile(
    r"\b(?:i\s+(?:do\s+not|don['’]t)\s+know|"
    r"i\s+(?:cannot|can't)\s+(?:answer|determine|know)|"
    r"unknown|unsure|not\s+sure|cannot\s+determine|can't\s+determine|"
    r"insufficient\s+information|not\s+enough\s+information|"
    r"there\s+is\s+no\s+(?:information|record|memory)|no\s+memory|no\s+answer|"
    r"not\s+available|unclear|"
    r"refus(?:e|al)|declin(?:e|ing))\b",
    re.IGNORECASE,
)
_ZERO_WIDTH_RE = re.compile(r"[\W_]+", re.UNICODE)


def token_count(value: Any) -> int:
    """Count composed answer tokens with one fixed tokenizer."""

    return len(_TOKEN_RE.findall("" if value is None else str(value)))


def normalize_answer(value: Any) -> str:
    """Normalize case, punctuation, and whitespace for deterministic matching."""

    return " ".join(_ZERO_WIDTH_RE.sub(" ", ("" if value is None else str(value)).casefold()).split())


def is_refusal(value: Any) -> bool:
    """Return whether an answer is an explicit refusal/empty answer.

    Empty and whitespace-only answers are refusals by definition.  The explicit
    phrase list is intentionally small and deterministic; a guessed fact is
    never converted into a refusal merely because it is low confidence.
    """

    text = ("" if value is None else str(value)).strip()
    return not text or bool(_REFUSAL_RE.search(text))


def _contains_phrase(haystack: Any, needle: Any) -> bool:
    """Token-boundary phrase containment, avoiding matches such as 5/25."""

    left = normalize_answer(haystack)
    right = normalize_answer(needle)
    if not left or not right:
        return False
    if left == right:
        return True
    pattern = re.compile(rf"(?<!\w){re.escape(right)}(?!\w)")
    return bool(pattern.search(left))


def answer_matches(answer: Any, expected: Any, alternatives: Sequence[Any] = ()) -> bool:
    """Match an answer exactly or as a complete phrase inside a short answer.

    The phrase rule permits a natural answer such as ``"The current color is
    blue."`` while preventing a numeric/lucky substring such as ``5`` matching
    ``25``.  Alternatives are treated exactly like the primary expected value.
    """

    candidates = [expected, *alternatives]
    return any(_contains_phrase(answer, candidate) for candidate in candidates if candidate is not None and str(candidate).strip())


def recall_at_k(retrieved_ids: Iterable[Any], relevant_ids: Iterable[Any], k: int) -> float | None:
    """Compute recall@k; return ``None`` when no relevant IDs are declared."""

    if k < 0:
        raise ValueError("k must be non-negative")
    relevant = {str(item) for item in relevant_ids if item is not None and str(item)}
    if not relevant:
        return None
    retrieved = [str(item) for item in retrieved_ids if item is not None and str(item)][:k]
    return len(set(retrieved) & relevant) / len(relevant)


def accuracy(observations: Iterable[Any]) -> float | None:
    """Compute ``correct / total``; return ``None`` for no observations."""

    values = [bool(item) for item in observations]
    if not values:
        return None
    return sum(values) / len(values)


def abstention_rate(outcomes: Sequence[QuestionOutcome | Mapping[str, Any]]) -> float | None:
    """Compute correct required refusals divided by all required refusals."""

    required = [outcome for outcome in outcomes if _outcome_field(outcome, "expects_abstention", False)]
    if not required:
        return None
    correct = sum(1 for outcome in required if _outcome_field(outcome, "correct", False) and _outcome_is_refusal(outcome))
    return correct / len(required)


def update_correctness(
    answer: Any,
    current: Any,
    superseded_values: Sequence[Any] = (),
    alternatives: Sequence[Any] = (),
) -> bool:
    """Require the current value and reject every superseded value."""

    if not answer_matches(answer, current, alternatives):
        return False
    return not any(_contains_phrase(answer, value) for value in superseded_values if value is not None and str(value).strip())


def scope_matches(case_scope: Mapping[str, Any] | None, record_scope: Mapping[str, Any] | None) -> bool:
    """Return whether a record's explicit scope agrees with the case scope.

    Missing record-side fields are treated as unspecified rather than foreign:
    a real adapter can attach the requested scope after its backend returns a
    record.  Any explicitly supplied, conflicting field is a hard failure.
    """

    expected = dict(case_scope or {})
    actual = dict(record_scope or {})
    for key, value in expected.items():
        if key in actual and actual[key] != value:
            return False
    return True


def contamination_hits(
    outcomes: Sequence[QuestionOutcome | Mapping[str, Any]],
    case_scopes: Mapping[str, Mapping[str, Any]] | None = None,
) -> int:
    """Count answers whose cited evidence includes a foreign-scope record."""

    scopes = case_scopes or {}
    hits = 0
    for outcome in outcomes:
        foreign = _string_values(_outcome_field(outcome, "foreign_evidence_ids", ()))
        if foreign:
            hits += 1
            continue
        outcome_scope = scopes.get(str(_outcome_field(outcome, "question_id", "")))
        record_scopes = _outcome_field(outcome, "evidence_scopes", {})
        if outcome_scope is not None and isinstance(record_scopes, Mapping):
            if any(not scope_matches(outcome_scope, value) for value in record_scopes.values() if isinstance(value, Mapping)):
                hits += 1
    return hits


def contamination_rate(
    outcomes: Sequence[QuestionOutcome | Mapping[str, Any]],
    case_scopes: Mapping[str, Mapping[str, Any]] | None = None,
) -> float | None:
    """Compute foreign-scope answers divided by measured answers."""

    if not outcomes:
        return None
    return contamination_hits(outcomes, case_scopes) / len(outcomes)


def write_precision(stored_ids: Iterable[Any], expected_stored_ids: Iterable[Any]) -> float | None:
    """Compute expected IDs among all stored IDs.

    When an expected set exists but the provider stored nothing, the measured
    precision is ``0.0`` (a real admission failure).  It is ``None`` only when
    there is neither a stored set nor an expected set to measure.
    """

    stored = {str(item) for item in stored_ids if item is not None and str(item)}
    expected = {str(item) for item in expected_stored_ids if item is not None and str(item)}
    if not stored:
        return 0.0 if expected else None
    return len(stored & expected) / len(stored)


def write_recall(stored_ids: Iterable[Any], expected_stored_ids: Iterable[Any]) -> float | None:
    """Compute expected IDs retrieved from storage; no expectation is undefined."""

    stored = {str(item) for item in stored_ids if item is not None and str(item)}
    expected = {str(item) for item in expected_stored_ids if item is not None and str(item)}
    if not expected:
        return None
    return len(stored & expected) / len(expected)


def evidence_traceability(outcomes: Sequence[QuestionOutcome | Mapping[str, Any]]) -> float | None:
    """Compute answers backed by at least one valid returned record ID."""

    measured = [outcome for outcome in outcomes if not _outcome_field(outcome, "expects_abstention", False)]
    if not measured:
        return None
    valid = 0
    for outcome in measured:
        evidence = set(_string_values(_outcome_field(outcome, "evidence_ids", ())))
        returned = set(_string_values(_outcome_field(outcome, "returned_record_ids", ())))
        if evidence and evidence <= returned:
            valid += 1
    return valid / len(measured)


def token_efficiency(outcomes: Sequence[QuestionOutcome | Mapping[str, Any]]) -> float | None:
    """Compute composed answer tokens per useful cited fact."""

    total_tokens = 0
    total_facts = 0
    for outcome in outcomes:
        if _outcome_field(outcome, "expects_abstention", False) or _outcome_is_refusal(outcome):
            continue
        facts = max(0, int(_outcome_field(outcome, "useful_fact_count", 0) or 0))
        if facts == 0:
            continue
        tokens = int(_outcome_field(outcome, "composed_tokens", 0) or 0)
        if tokens <= 0:
            tokens = token_count(_outcome_field(outcome, "answer", ""))
        total_tokens += max(0, tokens)
        total_facts += facts
    if total_facts == 0:
        return None
    return total_tokens / total_facts


def aggregate_metrics(cases: Sequence[Case], results: Sequence[CaseResult]) -> dict[str, float | None]:
    """Aggregate the requested suite metrics from measured case results only.

    ``unavailable`` and ``error`` results contribute to scope counts but never
    contribute a numeric numerator or denominator.  This is the central
    no-fake-score rule of the harness.
    """

    case_by_id = {case.id: case for case in cases}
    measured = [result for result in results if result.measured]
    extraction_correct = 0
    extraction_total = 0
    multi_correct = 0
    multi_total = 0
    temporal_correct = 0
    temporal_total = 0
    update_correct = 0
    update_total = 0
    abstention_correct = 0
    abstention_total = 0
    contamination_answer_count = 0
    contamination_answer_hits = 0
    write_correct = 0
    write_total = 0
    write_expected_observed = False
    evidence_correct = 0
    evidence_total = 0
    token_total = 0
    fact_total = 0

    for result in measured:
        case = case_by_id.get(result.case_id)
        if case is None:
            continue
        if case.ability == "extraction" and result.extraction_relevant_ids:
            extraction_total += len(result.extraction_relevant_ids)
            extraction_correct += len(set(result.extraction_retrieved_ids) & set(result.extraction_relevant_ids))
        if case.ability == "multi_session":
            for outcome in result.question_outcomes:
                multi_total += 1
                multi_correct += int(outcome.correct)
        if case.ability == "temporal":
            for outcome in result.question_outcomes:
                temporal_total += 1
                temporal_correct += int(outcome.correct)
        if case.ability == "update":
            for outcome in result.question_outcomes:
                update_total += 1
                update_correct += int(outcome.update_correct if outcome.update_correct is not None else outcome.correct)
        for outcome in result.question_outcomes:
            if outcome.expects_abstention:
                abstention_total += 1
                abstention_correct += int(outcome.correct and outcome.is_refusal)
            contamination_answer_count += 1
            contamination_answer_hits += int(bool(outcome.foreign_evidence_ids))
            if not outcome.expects_abstention:
                evidence_total += 1
                evidence = set(outcome.evidence_ids)
                returned = set(outcome.returned_record_ids)
                evidence_correct += int(bool(evidence) and evidence <= returned)
                if not outcome.is_refusal:
                    fact_total += outcome.useful_fact_count
                    token_total += outcome.composed_tokens or token_count(outcome.answer)
        if result.expected_stored_ids:
            write_expected_observed = True
        if result.write_precision is not None:
            write_total += len(result.stored_ids)
            write_correct += len(set(result.stored_ids) & set(result.expected_stored_ids))

    return {
        "extraction_recall": _ratio(extraction_correct, extraction_total),
        "multi_session_accuracy": _ratio(multi_correct, multi_total),
        "temporal_accuracy": _ratio(temporal_correct, temporal_total),
        "update_accuracy": _ratio(update_correct, update_total),
        "abstention_rate": _ratio(abstention_correct, abstention_total),
        "contamination_rate": _ratio(contamination_answer_hits, contamination_answer_count),
        "write_precision": _ratio(write_correct, write_total) if write_total else (0.0 if write_expected_observed else None),
        "evidence_traceability": _ratio(evidence_correct, evidence_total),
        "token_efficiency": _ratio(token_total, fact_total),
    }


def metric_denominators(cases: Sequence[Case], results: Sequence[CaseResult]) -> dict[str, int]:
    """Return the raw denominator for every requested aggregate metric."""

    case_by_id = {case.id: case for case in cases}
    measured = [result for result in results if result.measured]
    denominators = {
        name: 0
        for name in (
            "extraction_recall",
            "multi_session_accuracy",
            "temporal_accuracy",
            "update_accuracy",
            "abstention_rate",
            "contamination_rate",
            "write_precision",
            "evidence_traceability",
            "token_efficiency",
        )
    }
    for result in measured:
        case = case_by_id.get(result.case_id)
        if case is None:
            continue
        if case.ability == "extraction":
            denominators["extraction_recall"] += len(result.extraction_relevant_ids)
        if case.ability == "multi_session":
            denominators["multi_session_accuracy"] += len(result.question_outcomes)
        if case.ability == "temporal":
            denominators["temporal_accuracy"] += len(result.question_outcomes)
        if case.ability == "update":
            denominators["update_accuracy"] += len(result.question_outcomes)
        for outcome in result.question_outcomes:
            if outcome.expects_abstention:
                denominators["abstention_rate"] += 1
            denominators["contamination_rate"] += 1
            if not outcome.expects_abstention:
                denominators["evidence_traceability"] += 1
                if not outcome.is_refusal:
                    denominators["token_efficiency"] += outcome.useful_fact_count
        if result.write_precision is not None:
            denominators["write_precision"] += len(result.stored_ids)
    return denominators


def metric_observed(cases: Sequence[Case], results: Sequence[CaseResult]) -> dict[str, bool]:
    """Identify metrics with at least one valid observation."""

    denominators = metric_denominators(cases, results)
    observed = {name: bool(value) for name, value in denominators.items()}
    observed["write_precision"] = denominators["write_precision"] > 0 or any(result.measured and result.expected_stored_ids for result in results)
    return observed


def ability_accuracy(cases: Sequence[Case], results: Sequence[CaseResult], ability: str) -> float | None:
    """Compute question accuracy for one ability, excluding unavailable cases."""

    case_by_id = {case.id: case for case in cases}
    values: list[bool] = []
    for result in results:
        case = case_by_id.get(result.case_id)
        if result.measured and case is not None and case.ability == ability:
            values.extend(outcome.correct for outcome in result.question_outcomes)
    return accuracy(values)


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator <= 0 else numerator / denominator


def _outcome_field(outcome: QuestionOutcome | Mapping[str, Any], name: str, default: Any) -> Any:
    if isinstance(outcome, Mapping):
        return outcome.get(name, default)
    return getattr(outcome, name, default)


def _string_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value if item is not None and str(item))
    return (str(value),)


def _outcome_is_refusal(outcome: QuestionOutcome | Mapping[str, Any]) -> bool:
    return is_refusal(_outcome_field(outcome, "answer", ""))


# Explicit aliases make the formula names easy to discover without introducing
# a second grading implementation.
compute_recall_at_k = recall_at_k
compute_accuracy = accuracy
compute_abstention_rate = abstention_rate
compute_update_correctness = update_correctness
compute_contamination_rate = contamination_rate
compute_write_precision = write_precision
compute_evidence_traceability = evidence_traceability
compute_token_efficiency = token_efficiency

__all__ = [
    "ability_accuracy",
    "abstention_rate",
    "accuracy",
    "aggregate_metrics",
    "answer_matches",
    "compute_abstention_rate",
    "compute_accuracy",
    "compute_contamination_rate",
    "compute_evidence_traceability",
    "compute_recall_at_k",
    "compute_token_efficiency",
    "compute_update_correctness",
    "compute_write_precision",
    "contamination_hits",
    "contamination_rate",
    "evidence_traceability",
    "is_refusal",
    "metric_denominators",
    "metric_observed",
    "normalize_answer",
    "recall_at_k",
    "scope_matches",
    "token_count",
    "token_efficiency",
    "update_correctness",
    "write_precision",
    "write_recall",
]
