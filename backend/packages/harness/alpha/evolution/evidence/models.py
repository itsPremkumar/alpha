"""Honest data contracts for Alpha's self-evolution evidence gate.

This package is the *bar* a proposed self-change must clear before it becomes
the new default. Alpha already has self-evolution machinery
(``alpha/evolution``, ``alpha/rsi``, ``scripts/auto_update.py``, skill
evolution) but no component that decides whether a change is actually an
improvement. Without one, "self-evolving" degrades into "edits itself and
hopes". Every type in this module follows the honesty contract already
established by ``alpha/memory/evaluation``:

* **closed status sets** - a status is either in the closed set or a
  construction error; there is no "unknown" that can be read as a pass;
* **unavailable is never a number** - a measurement that did not happen carries
  ``value=None`` plus a status of ``unavailable``/``error``. It is never
  coerced to ``0.0`` and never marked as a pass;
* **evidence labels are explicit** - ``measured`` / ``simulated`` /
  ``heuristic`` / ``unverified`` (the same whitelist
  ``alpha.rsi.lineage.EVIDENCE_KINDS`` uses), with only ``measured`` able to
  gate a promotion;
* **thresholds live in config, never in a model default** - a comparison is
  only ever made against a *declared* noise floor (see
  :mod:`alpha.evolution.evidence.compare`).

Nothing in this module performs I/O, reads a clock, or imports Alpha's
production stacks. Every timestamp arrives through an injected clock by the
caller (:func:`new_proposal`), so a decision is a pure function of its inputs.

Design references (cited for the *approach* only - no source was copied):
* Amodei et al., "Concrete Problems in AI Safety" - evaluation integrity as a
  first-class concern: an agent that can edit its own tests cannot be trusted
  to improve.
* Krakovna et al., "Specification Gaming" (DeepMind, 2020) - reward-hacking /
  specification-gaming; a "measured improvement" that weakened the measuring
  apparatus is not an improvement.
* the evaluator-integrity and self-modification literature on automated
  program repair (repairing the benchmark and the judge in the same patch).
* this repository's own ``alpha/rsi/evaluator_manifest.py`` (SHA-256 manifest
  over the evaluator surface) and ``alpha/memory/evaluation`` (closed result
  statuses, ``None`` instead of zero, measured-only gating).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "COMPARISON_STATUSES",
    "DIRECTIONS",
    "EVIDENCE_LABELS",
    "EVIDENCE_HEURISTIC",
    "EVIDENCE_KINDS",
    "EVIDENCE_MEASURED",
    "EVIDENCE_SIMULATED",
    "EVIDENCE_UNVERIFIED",
    "GATE_STATUSES",
    "HIGHER_IS_BETTER",
    "INTEGRITY_SEVERITIES",
    "INTEGRITY_STATUSES",
    "LOWER_IS_BETTER",
    "MEASUREMENT_SIDES",
    "MEASUREMENT_STATUSES",
    "VERDICT_STATUSES",
    "Comparison",
    "ComparisonReport",
    "EvaluationIntegrityReport",
    "EvidenceEvaluation",
    "GateResult",
    "IntegrityFinding",
    "Measurement",
    "Proposal",
    "Verdict",
    "new_proposal",
]

#: Evidence labels. The first three mirror ``alpha.rsi.lineage.EVIDENCE_KINDS``;
#: only ``measured`` may gate a promotion (see :mod:`alpha.evolution.evidence.decision`).
EVIDENCE_LABELS: tuple[str, ...] = ("measured", "simulated", "heuristic", "unverified")
EVIDENCE_MEASURED = "measured"
EVIDENCE_SIMULATED = "simulated"
EVIDENCE_HEURISTIC = "heuristic"
EVIDENCE_UNVERIFIED = "unverified"

#: Which side of the A/B comparison a measurement belongs to.
MEASUREMENT_SIDES: tuple[str, ...] = ("candidate", "incumbent")

#: Metric directions. ``higher_is_better`` (accuracy) vs ``lower_is_better``
#: (latency, error count) - declared per metric in config, never guessed.
DIRECTIONS: tuple[str, ...] = ("higher_is_better", "lower_is_better")
HIGHER_IS_BETTER = "higher_is_better"
LOWER_IS_BETTER = "lower_is_better"

#: Execution status of one measurement attempt. ``unavailable`` means the
#: measurement could not be taken (timeout, missing source, unparsable output);
#: ``error`` means the harness ran and failed. Neither is a number and neither
#: is a pass.
MEASUREMENT_STATUSES: tuple[str, ...] = ("measured", "unavailable", "error")

#: Per-metric comparison outcomes. ``within_noise`` is explicitly *not* an
#: improvement, and ``incomparable``/``insufficient_evidence`` are not verdicts.
COMPARISON_STATUSES: tuple[str, ...] = ("improved", "regressed", "within_noise", "incomparable", "insufficient_evidence")

#: Gate outcomes. Mirrors the memory-evaluation result vocabulary; an
#: ``unavailable``/``error`` required gate blocks acceptance as
#: ``insufficient_evidence`` and can never read as a pass.
GATE_STATUSES: tuple[str, ...] = ("pass", "fail", "unavailable", "error")

#: The four verdicts this gate can reach. ``needs_human`` is a first-class
#: refusal to decide, not a soft failure.
VERDICT_STATUSES: tuple[str, ...] = ("accepted", "rejected", "needs_human", "insufficient_evidence")

#: Evaluator-integrity outcomes. ``suspect`` is the conservative default for
#: any unexplained change on an evaluation surface: never silently allowed.
INTEGRITY_STATUSES: tuple[str, ...] = ("clean", "suspect", "compromised", "error")
INTEGRITY_SEVERITIES: tuple[str, ...] = ("blocker", "suspect")

EvidenceLabel = Literal["measured", "simulated", "heuristic", "unverified"]
MeasurementSide = Literal["candidate", "incumbent"]
MeasurementStatus = Literal["measured", "unavailable", "error"]
ComparisonStatus = Literal["improved", "regressed", "within_noise", "incomparable", "insufficient_evidence"]
GateStatus = Literal["pass", "fail", "unavailable", "error"]
VerdictStatus = Literal["accepted", "rejected", "needs_human", "insufficient_evidence"]
IntegrityStatus = Literal["clean", "suspect", "compromised", "error"]

#: Backwards-compatible alias: the whitelist name used by ``alpha.rsi``.
EVIDENCE_KINDS: frozenset[str] = frozenset(EVIDENCE_LABELS)


def _require_text(value: Any, *, name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{name} must be a non-empty string")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text_tuple(value: Any, *, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, Sequence):
        items = tuple(value)
    else:
        raise TypeError(f"{name} must be a sequence of strings, got {type(value).__name__}")
    cleaned: list[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return tuple(cleaned)


def _require_status(value: Any, allowed: tuple[str, ...], *, name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {list(allowed)}, got {value!r}")
    return str(value)


def _finite_number(value: Any, *, name: str) -> float:
    """Return a finite float or raise: a bool/NaN/inf is a contract violation."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return number


def _optional_number(value: Any, *, name: str) -> float | None:
    return None if value is None else _finite_number(value, name=name)


def _non_negative_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}")
    return int(value)


@dataclass(frozen=True)
class Proposal:
    """One proposed self-change, as it is presented to the gate.

    The model records *claims*, never verdicts. ``declared_intent``,
    ``blast_radius``, ``reversible`` and ``rollback_ref`` are the author's
    statements; the gate's job is to refuse to take them on trust.

    Fields:
        id: Durable proposal identity (never invented by the gate).
        kind: Proposal kind (``skill``, ``prompt``, ``routing``, ``code``, ...).
        author: Who produced the change (agent id, human, or a test label).
        created_at: Unix seconds read from an **injected** clock by
            :func:`new_proposal`; the model itself never reads a clock.
        declared_intent: The author's stated goal, kept verbatim.
        touched_paths: Repo-relative paths the change touches (used for blast
            radius and evaluator-integrity classification).
        artifact_paths: Produced artifacts (reports, bundles) referenced by path.
        patch_ref / diff_ref: References to the candidate patch/diff. At least
            one of ``patch_ref``/``diff_ref``/``artifact_paths`` is expected;
            absence is disclosed by the reproducibility gate, not assumed.
        blast_radius: The author's declared blast radius (a count). ``0`` means
            "not declared" and falls back to ``len(touched_paths)`` - see
            :attr:`effective_blast_radius`.
        reversible: Whether a rollback path is claimed to exist.
        rollback_ref: Reference to the rollback artefact (a ref, tag, patch or
            bundle id). Required for acceptance when ``reversible`` is true.
        supersedes: The proposal id this one replaces, if any.
        notes: Free-form disclosure carried verbatim into provenance.
    """

    id: str
    kind: str
    author: str
    created_at: float
    declared_intent: str
    touched_paths: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    patch_ref: str | None = None
    diff_ref: str | None = None
    blast_radius: int = 0
    reversible: bool = True
    rollback_ref: str | None = None
    supersedes: str | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_text(self.id, name="proposal.id"))
        object.__setattr__(self, "kind", _require_text(self.kind, name="proposal.kind"))
        object.__setattr__(self, "author", _require_text(self.author, name="proposal.author"))
        object.__setattr__(self, "created_at", _finite_number(self.created_at, name="proposal.created_at"))
        object.__setattr__(self, "declared_intent", _require_text(self.declared_intent, name="proposal.declared_intent"))
        object.__setattr__(self, "touched_paths", _text_tuple(self.touched_paths, name="proposal.touched_paths"))
        object.__setattr__(self, "artifact_paths", _text_tuple(self.artifact_paths, name="proposal.artifact_paths"))
        object.__setattr__(self, "patch_ref", _optional_text(self.patch_ref))
        object.__setattr__(self, "diff_ref", _optional_text(self.diff_ref))
        object.__setattr__(self, "blast_radius", _non_negative_int(self.blast_radius, name="proposal.blast_radius"))
        object.__setattr__(self, "reversible", bool(self.reversible))
        object.__setattr__(self, "rollback_ref", _optional_text(self.rollback_ref))
        object.__setattr__(self, "supersedes", _optional_text(self.supersedes))
        object.__setattr__(self, "notes", _text_tuple(self.notes, name="proposal.notes"))

    @property
    def effective_blast_radius(self) -> int:
        """Declared blast radius, falling back to the number of touched paths.

        ``0`` declared is treated as "undeclared" rather than "harmless": an
        undeclared blast radius may never make a change look small.
        """

        if self.blast_radius > 0:
            return int(self.blast_radius)
        return len(self.touched_paths)

    @property
    def has_patch_reference(self) -> bool:
        return bool(self.patch_ref or self.diff_ref or self.artifact_paths)

    @property
    def has_rollback_path(self) -> bool:
        """A rollback path exists only when a ref was actually supplied."""

        return bool(self.reversible and self.rollback_ref)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "author": self.author,
            "created_at": self.created_at,
            "declared_intent": self.declared_intent,
            "touched_paths": list(self.touched_paths),
            "artifact_paths": list(self.artifact_paths),
            "patch_ref": self.patch_ref,
            "diff_ref": self.diff_ref,
            "blast_radius": self.blast_radius,
            "effective_blast_radius": self.effective_blast_radius,
            "reversible": self.reversible,
            "rollback_ref": self.rollback_ref,
            "supersedes": self.supersedes,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Proposal:
        if not isinstance(data, Mapping):
            raise TypeError(f"proposal payload must be a mapping, got {type(data).__name__}")
        known = {
            "id",
            "kind",
            "author",
            "created_at",
            "declared_intent",
            "touched_paths",
            "artifact_paths",
            "patch_ref",
            "diff_ref",
            "blast_radius",
            "reversible",
            "rollback_ref",
            "supersedes",
            "notes",
        }
        return cls(**{key: value for key, value in data.items() if key in known})


def new_proposal(
    *,
    id: str,
    kind: str,
    author: str,
    declared_intent: str,
    clock: Any,
    touched_paths: Sequence[str] = (),
    artifact_paths: Sequence[str] = (),
    patch_ref: str | None = None,
    diff_ref: str | None = None,
    blast_radius: int = 0,
    reversible: bool = True,
    rollback_ref: str | None = None,
    supersedes: str | None = None,
    notes: Sequence[str] = (),
) -> Proposal:
    """Build a :class:`Proposal` from an **injected** clock.

    ``clock`` is required (there is deliberately no ``time.time`` default) and
    must return a real number of Unix seconds. Tests pass a constant callable;
    production passes ``time.time`` at the call site. The gate never reads a
    clock on its own, so a verdict is reproducible byte-for-byte from its
    recorded inputs.
    """

    if not callable(clock):
        raise TypeError("clock must be a callable returning Unix seconds; the evidence gate never reads a global clock")
    return Proposal(
        id=id,
        kind=kind,
        author=author,
        created_at=_finite_number(clock(), name="clock()"),
        declared_intent=declared_intent,
        touched_paths=tuple(touched_paths),
        artifact_paths=tuple(artifact_paths),
        patch_ref=patch_ref,
        diff_ref=diff_ref,
        blast_radius=blast_radius,
        reversible=reversible,
        rollback_ref=rollback_ref,
        supersedes=supersedes,
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class Measurement:
    """One measured value for one metric on one side of the comparison.

    ``status`` says whether the number was *obtained*; ``evidence`` says how
    much epistemic weight it carries. A scripted oracle that produced ``0.9``
    has ``status="measured"`` but ``evidence="simulated"``: the value exists,
    and it still may not gate a promotion. When nothing was measured, ``value``
    is ``None`` - never ``0.0``.
    """

    metric: str
    side: str
    value: float | None
    unit: str = "ratio"
    sample_size: int = 0
    harness_id: str = ""
    evidence: str = EVIDENCE_MEASURED
    status: str = "measured"
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric", _require_text(self.metric, name="measurement.metric"))
        object.__setattr__(self, "side", _require_status(self.side, MEASUREMENT_SIDES, name="measurement.side"))
        object.__setattr__(self, "value", _optional_number(self.value, name="measurement.value"))
        object.__setattr__(self, "unit", _require_text(self.unit, name="measurement.unit"))
        object.__setattr__(self, "sample_size", _non_negative_int(self.sample_size, name="measurement.sample_size"))
        object.__setattr__(self, "harness_id", _require_text(self.harness_id, name="measurement.harness_id"))
        object.__setattr__(self, "evidence", _require_status(self.evidence, EVIDENCE_LABELS, name="measurement.evidence"))
        object.__setattr__(self, "status", _require_status(self.status, MEASUREMENT_STATUSES, name="measurement.status"))
        object.__setattr__(self, "detail", str(self.detail or ""))
        if self.status != "measured" and self.value is not None:
            raise ValueError(f"measurement {self.metric!r}/{self.side} has status {self.status!r} but a numeric value {self.value!r}: an unmeasured metric must never read as a number")
        if self.status == "measured" and self.value is None:
            raise ValueError(f"measurement {self.metric!r}/{self.side} claims status 'measured' without a value")

    @property
    def is_measured(self) -> bool:
        """True only for a value that was obtained AND labelled ``measured``."""

        return self.status == "measured" and self.evidence == EVIDENCE_MEASURED

    @property
    def is_sufficient(self) -> bool:
        """True when at least one sample backed the value."""

        return self.is_measured and self.sample_size >= 1

    def with_evidence(self, evidence: str) -> Measurement:
        """Return a copy relabelled with a different evidence kind."""

        return Measurement(
            metric=self.metric,
            side=self.side,
            value=self.value,
            unit=self.unit,
            sample_size=self.sample_size,
            harness_id=self.harness_id,
            evidence=evidence,
            status=self.status,
            detail=self.detail,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "side": self.side,
            "value": self.value,
            "unit": self.unit,
            "sample_size": self.sample_size,
            "harness_id": self.harness_id,
            "evidence": self.evidence,
            "status": self.status,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Measurement:
        if not isinstance(data, Mapping):
            raise TypeError(f"measurement payload must be a mapping, got {type(data).__name__}")
        known = {"metric", "side", "value", "unit", "sample_size", "harness_id", "evidence", "status", "detail"}
        return cls(**{key: value for key, value in data.items() if key in known})


@dataclass(frozen=True)
class Comparison:
    """Candidate vs incumbent for one metric, against a declared noise floor.

    ``exceeds_noise_floor`` is the whole point: a delta smaller than (or equal
    to) the declared floor is ``within_noise``, which is **not** an
    improvement. ``delta``/``relative_delta`` stay ``None`` when the two sides
    are not comparable (mismatched harness, unit, or sample-size regime) so a
    fabricated delta can never be serialized.
    """

    metric: str
    unit: str
    direction: str
    candidate: Measurement
    incumbent: Measurement
    noise_floor: float
    status: str
    delta: float | None = None
    relative_delta: float | None = None
    exceeds_noise_floor: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric", _require_text(self.metric, name="comparison.metric"))
        object.__setattr__(self, "unit", _require_text(self.unit, name="comparison.unit"))
        if self.direction not in DIRECTIONS:
            raise ValueError(f"comparison.direction must be one of {list(DIRECTIONS)}, got {self.direction!r}")
        if not isinstance(self.candidate, Measurement) or not isinstance(self.incumbent, Measurement):
            raise TypeError("comparison requires Measurement objects for both sides")
        if self.candidate.metric != self.metric or self.incumbent.metric != self.metric:
            raise ValueError("comparison.metric must match both measurements' metric names")
        if self.candidate.side != "candidate" or self.incumbent.side != "incumbent":
            raise ValueError("comparison requires the candidate measurement on the 'candidate' side and the incumbent on the 'incumbent' side")
        object.__setattr__(self, "noise_floor", _finite_number(self.noise_floor, name="comparison.noise_floor"))
        if self.noise_floor < 0:
            raise ValueError(f"comparison.noise_floor must be >= 0, got {self.noise_floor!r}")
        object.__setattr__(self, "status", _require_status(self.status, COMPARISON_STATUSES, name="comparison.status"))
        object.__setattr__(self, "delta", _optional_number(self.delta, name="comparison.delta"))
        object.__setattr__(self, "relative_delta", _optional_number(self.relative_delta, name="comparison.relative_delta"))
        object.__setattr__(self, "exceeds_noise_floor", bool(self.exceeds_noise_floor))
        object.__setattr__(self, "reason", _require_text(self.reason, name="comparison.reason"))
        if self.status in {"improved", "regressed", "within_noise"} and self.delta is None:
            raise ValueError(f"comparison {self.metric!r} has status {self.status!r} without a delta: a decided comparison must carry its computed delta")

    @property
    def is_regression(self) -> bool:
        return self.status == "regressed"

    @property
    def is_improvement(self) -> bool:
        return self.status == "improved"

    @property
    def is_decided(self) -> bool:
        return self.status in {"improved", "regressed", "within_noise"}

    @property
    def is_usable_evidence(self) -> bool:
        """True when the comparison produced a real verdict we may act on."""

        return self.status in {"improved", "regressed", "within_noise"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "unit": self.unit,
            "direction": self.direction,
            "candidate": self.candidate.to_dict(),
            "incumbent": self.incumbent.to_dict(),
            "noise_floor": self.noise_floor,
            "status": self.status,
            "delta": self.delta,
            "relative_delta": self.relative_delta,
            "exceeds_noise_floor": self.exceeds_noise_floor,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ComparisonReport:
    """All per-metric comparisons for one proposal, plus the primary verdict.

    ``usable`` is false when the primary metric is missing or not decided, which
    the decision function maps to ``insufficient_evidence`` - never to a pass.
    """

    comparisons: tuple[Comparison, ...] = ()
    primary_metric: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparisons", tuple(self.comparisons))
        object.__setattr__(self, "primary_metric", _require_text(self.primary_metric, name="comparison_report.primary_metric"))

    @property
    def primary(self) -> Comparison | None:
        return next((item for item in self.comparisons if item.metric == self.primary_metric), None)

    @property
    def regressions(self) -> tuple[Comparison, ...]:
        return tuple(item for item in self.comparisons if item.is_regression)

    @property
    def improvements(self) -> tuple[Comparison, ...]:
        return tuple(item for item in self.comparisons if item.is_improvement)

    @property
    def undecided(self) -> tuple[Comparison, ...]:
        return tuple(item for item in self.comparisons if not item.is_usable_evidence)

    @property
    def usable(self) -> bool:
        primary = self.primary
        return primary is not None and primary.is_usable_evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_metric": self.primary_metric,
            "usable": self.usable,
            "comparisons": [item.to_dict() for item in self.comparisons],
        }


@dataclass(frozen=True)
class GateResult:
    """One precondition gate's real outcome.

    ``status`` is a closed set and ``detail`` is mandatory: a gate that cannot
    explain itself is treated as an ``error`` by
    :func:`alpha.evolution.evidence.gates.run_required_gates`, so a silent gate
    can never be read as a pass.
    """

    name: str
    status: str
    detail: str
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_text(self.name, name="gate_result.name"))
        object.__setattr__(self, "status", _require_status(self.status, GATE_STATUSES, name="gate_result.status"))
        object.__setattr__(self, "detail", _require_text(self.detail, name="gate_result.detail"))
        object.__setattr__(self, "duration_ms", _finite_number(self.duration_ms, name="gate_result.duration_ms"))
        if self.duration_ms < 0:
            raise ValueError(f"gate_result.duration_ms must be >= 0, got {self.duration_ms!r}")

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    @property
    def blocking(self) -> bool:
        """Failures block; unavailable/error block as insufficient evidence."""

        return self.status in {"fail", "unavailable", "error"}

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail, "duration_ms": self.duration_ms}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GateResult:
        if not isinstance(data, Mapping):
            raise TypeError(f"gate result payload must be a mapping, got {type(data).__name__}")
        known = {"name", "status", "detail", "duration_ms"}
        return cls(**{key: value for key, value in data.items() if key in known})


@dataclass(frozen=True)
class IntegrityFinding:
    """One evaluator-integrity finding, always naming an exact path + reason."""

    path: str
    code: str
    reason: str
    category: str = "unknown"
    severity: str = "blocker"

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _require_text(self.path, name="integrity_finding.path"))
        object.__setattr__(self, "code", _require_text(self.code, name="integrity_finding.code"))
        object.__setattr__(self, "reason", _require_text(self.reason, name="integrity_finding.reason"))
        object.__setattr__(self, "category", _require_text(self.category, name="integrity_finding.category"))
        object.__setattr__(self, "severity", _require_status(self.severity, INTEGRITY_SEVERITIES, name="integrity_finding.severity"))

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "code": self.code, "reason": self.reason, "category": self.category, "severity": self.severity}


@dataclass(frozen=True)
class EvaluationIntegrityReport:
    """Closed-status verdict on "was the judge tampered with?".

    ``clean``      - no evaluation surface was touched.
    ``suspect``    - an evaluation surface changed in a way we cannot explain
                     away (never silently allowed; blocks acceptance).
    ``compromised``- a specific weakening was detected (deleted test, added
                     skip marker, loosened assertion, changed threshold,
                     changed metric, changed baseline, fingerprint mismatch).
    ``error``      - the check could not run (bad inputs); blocks as
                     insufficient evidence.
    """

    status: str
    findings: tuple[IntegrityFinding, ...] = ()
    checked_paths: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_status(self.status, INTEGRITY_STATUSES, name="integrity_report.status"))
        object.__setattr__(self, "findings", tuple(self.findings))
        object.__setattr__(self, "checked_paths", _text_tuple(self.checked_paths, name="integrity_report.checked_paths"))
        object.__setattr__(self, "reason", str(self.reason or ""))

    @property
    def clean(self) -> bool:
        return self.status == "clean"

    @property
    def blocking(self) -> bool:
        """Any non-clean status blocks acceptance."""

        return self.status != "clean"

    def finding_for(self, path: str) -> IntegrityFinding | None:
        for finding in self.findings:
            if finding.path == str(path):
                return finding
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "checked_paths": list(self.checked_paths),
            "findings": [finding.to_dict() for finding in self.findings],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvaluationIntegrityReport:
        if not isinstance(data, Mapping):
            raise TypeError(f"integrity report payload must be a mapping, got {type(data).__name__}")
        return cls(
            status=str(data.get("status", "error")),
            findings=tuple(IntegrityFinding(**finding) for finding in data.get("findings", ()) if isinstance(finding, Mapping)),
            checked_paths=_text_tuple(data.get("checked_paths", ()), name="checked_paths"),
            reason=str(data.get("reason", "")),
        )


@dataclass(frozen=True)
class Verdict:
    """The gate's answer for one proposal, with the reasons it will be held to.

    ``accepted`` is the only status that may become the new default.
    ``needs_human`` is a deliberate refusal to decide (irreversible, external,
    financial or data-destroying changes). ``insufficient_evidence`` means the
    gate could not establish a verdict - it is never a soft accept.
    """

    proposal_id: str
    status: str
    reasons: tuple[str, ...]
    primary_metric: str | None = None
    human_categories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _require_text(self.proposal_id, name="verdict.proposal_id"))
        object.__setattr__(self, "status", _require_status(self.status, VERDICT_STATUSES, name="verdict.status"))
        object.__setattr__(self, "reasons", _text_tuple(self.reasons, name="verdict.reasons"))
        if not self.reasons:
            raise ValueError("verdict.reasons must disclose at least one real reason: a verdict without a reason is not reviewable")
        object.__setattr__(self, "primary_metric", _optional_text(self.primary_metric))
        object.__setattr__(self, "human_categories", _text_tuple(self.human_categories, name="verdict.human_categories"))
        if self.status == "needs_human" and not self.human_categories:
            raise ValueError("a needs_human verdict must name the category (irreversible/external/financial/data_destroying) that forced the human review")

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    @property
    def blocking(self) -> bool:
        """Every non-accepted status blocks the change from becoming default."""

        return self.status != "accepted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "status": self.status,
            "reasons": list(self.reasons),
            "primary_metric": self.primary_metric,
            "human_categories": list(self.human_categories),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Verdict:
        if not isinstance(data, Mapping):
            raise TypeError(f"verdict payload must be a mapping, got {type(data).__name__}")
        return cls(
            proposal_id=str(data.get("proposal_id", "")),
            status=str(data.get("status", "insufficient_evidence")),
            reasons=_text_tuple(data.get("reasons", ()), name="reasons"),
            primary_metric=data.get("primary_metric"),
            human_categories=_text_tuple(data.get("human_categories", ()), name="human_categories"),
        )


@dataclass(frozen=True)
class EvidenceEvaluation:
    """The complete, serializable result of gating one proposal.

    Carries the evidence a reviewer needs to replay the decision later: the
    proposal, the integrity report, every measurement, the comparison, the gate
    results and the verdict.
    """

    proposal: Proposal
    verdict: Verdict
    config_enabled: bool = True
    integrity: EvaluationIntegrityReport | None = None
    measurements: tuple[Measurement, ...] = ()
    comparisons: ComparisonReport | None = None
    gates: tuple[GateResult, ...] = ()
    provenance_status: str = "not_written"
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "measurements", tuple(self.measurements))
        object.__setattr__(self, "gates", tuple(self.gates))
        object.__setattr__(self, "notes", _text_tuple(self.notes, name="evidence_evaluation.notes"))
        object.__setattr__(self, "provenance_status", str(self.provenance_status or "not_written"))

    @property
    def accepted(self) -> bool:
        return self.verdict.accepted

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_enabled": self.config_enabled,
            "proposal": self.proposal.to_dict(),
            "integrity": self.integrity.to_dict() if self.integrity is not None else None,
            "measurements": [measurement.to_dict() for measurement in self.measurements],
            "comparisons": self.comparisons.to_dict() if self.comparisons is not None else None,
            "gates": [gate.to_dict() for gate in self.gates],
            "verdict": self.verdict.to_dict(),
            "provenance_status": self.provenance_status,
            "notes": list(self.notes),
        }
