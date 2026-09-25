"""Pure weighted admission scoring from plan section 10.

The default coefficients are the plan's source-of-truth formula.  A missing
signal contributes exactly ``0.0`` and is listed in ``missing_signals``; it is
never silently imputed.  Contributions are rounded to 12 decimal places only
to make JSON/UI output stable across platforms.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .models import AdmissionCandidate

SCORE_SIGNALS: tuple[str, ...] = (
    "importance",
    "future_utility",
    "novelty",
    "confidence",
    "recurrence",
    "task_relevance",
    "explicit_user_request",
)
DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "importance": 0.25,
    "future_utility": 0.20,
    "novelty": 0.15,
    "confidence": 0.15,
    "recurrence": 0.10,
    "task_relevance": 0.10,
    "explicit_user_request": 0.05,
}
_SCORE_PRECISION = 12


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Weighted total, per-term contributions, and explicit disclosures."""

    score: float
    terms: Mapping[str, float]
    missing_signals: tuple[str, ...]
    disclosures: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "terms", MappingProxyType(dict(self.terms)))


def validate_score_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """Validate and copy a complete normalized weight table.

    Partial overrides are rejected: a missing coefficient would silently alter
    the documented admission formula.  Weights must be finite, non-negative,
    and sum to one within floating-point tolerance.
    """

    supplied = set(weights)
    expected = set(SCORE_SIGNALS)
    if supplied != expected:
        missing = sorted(expected - supplied)
        unknown = sorted(supplied - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise ValueError("score weights must contain exactly the documented signals: " + ", ".join(details))
    normalized: dict[str, float] = {}
    for name in SCORE_SIGNALS:
        raw = weights[name]
        if isinstance(raw, bool):
            raise ValueError(f"score weight {name!r} must be numeric, not boolean")
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"score weight {name!r} must be numeric") from exc
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"score weight {name!r} must be finite and non-negative")
        normalized[name] = value
    if not math.isclose(math.fsum(normalized.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("score weights must sum to 1.0")
    return normalized


def score_candidate(
    candidate: AdmissionCandidate,
    *,
    weights: Mapping[str, float] | None = None,
) -> ScoreResult:
    """Return the pure section-10 score and its weighted term breakdown."""

    table = validate_score_weights(DEFAULT_SCORE_WEIGHTS if weights is None else weights)
    terms: dict[str, float] = {}
    missing: list[str] = []
    disclosures: list[str] = []
    for name in SCORE_SIGNALS:
        raw_value = getattr(candidate, name)
        if raw_value is None:
            value = 0.0
            missing.append(name)
            disclosures.append(f"missing_signal:{name}=0.0")
        else:
            value = float(raw_value)
        contribution = round(table[name] * value, _SCORE_PRECISION)
        if contribution == 0.0:
            contribution = 0.0
        terms[name] = contribution

    raw_total = math.fsum(terms.values())
    bounded_total = min(1.0, max(0.0, raw_total))
    score = round(bounded_total, _SCORE_PRECISION)
    if raw_total > 1.0:
        disclosures.append(f"score_clamped:high:{raw_total:.12g}->1.0")
    elif raw_total < 0.0:
        disclosures.append(f"score_clamped:low:{raw_total:.12g}->0.0")
    return ScoreResult(
        score=score,
        terms=terms,
        missing_signals=tuple(missing),
        disclosures=tuple(disclosures),
    )


def weighted_score(
    candidate: AdmissionCandidate,
    *,
    weights: Mapping[str, float] | None = None,
) -> float:
    """Convenience wrapper returning only the bounded total."""

    return score_candidate(candidate, weights=weights).score


__all__ = [
    "DEFAULT_SCORE_WEIGHTS",
    "SCORE_SIGNALS",
    "ScoreResult",
    "score_candidate",
    "validate_score_weights",
    "weighted_score",
]
