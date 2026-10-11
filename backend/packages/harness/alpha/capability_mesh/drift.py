"""Capability drift detection: a detector that reports, never acts (plan §23).

Performance moves when a provider updates a model, a tool starts failing, a
skill goes stale, or the project environment changes. This module watches the
quality stream each :class:`~alpha.capability_mesh.memory.CapabilityRecord`
carries and reports *where* it moved, with the observable counts behind every
verdict — it is a measuring instrument, deliberately NOT an actuator.

The boundary is load-bearing and stated in the plan's own response list:
this layer detects drift, marks profiles degraded/stale and preserves audit
evidence. The policy actions that follow — limiting assignments, running a
benchmark, rolling back a configuration — belong to a later policy layer that
consumes :class:`DriftReport`; nothing here mutates a passport, a record, a
config, or a routing decision. Report, then let policy act on the report.

Four rules decide the shape of the signal:

* **Windows, not averages.** Each (entity, capability) stream splits into a
  baseline window (the oldest ``baseline_samples`` records) and a recent
  window (the newest ``recent_samples``). When the stream is shorter than the
  two windows combined the slices overlap — that only *attenuates* the
  measured shift, which is the conservative direction.
* **A short window is UNKNOWN, never a direction.** If either side has fewer
  than its required number of *measurable* (quality-carrying) samples the
  signal is ``UNKNOWN`` with ``shift=None`` and evidence naming the shortfall.
  An unmeasured attempt is not a data point about quality.
* **Materiality is a threshold on the shift**, ``|recent_mean - baseline_mean|
  >= threshold``; UNCHANGED and DEGRADED/IMPROVED are separated by that line,
  and a material improvement stays flagged ``material`` for audit even though
  only degradation lands in ``DriftReport.degraded_entities``.
* **Staleness is outcome evidence aging out.** An entity is stale when it
  once earned a DELIVERED outcome but its newest outcome-bearing record is
  older than the revalidation window ``derive_evidence_level`` already owns.
  An entity that never earned an outcome is *unverified*, not stale — that is
  an honest gap, not decay, and must never read as drift.

Comparability follows the package-wide rule: only records whose
``profile_version`` equals the report's are in scope, so a rubric change can
never masquerade as capability drift.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from alpha.capability_mesh.memory import DELIVERED_OUTCOMES, CapabilityRecord
from alpha.capability_mesh.models import EvidenceLevel, _parse_timestamp, derive_evidence_level

DEFAULT_BASELINE_SAMPLES = 20
DEFAULT_RECENT_SAMPLES = 5
DEFAULT_MATERIALITY_THRESHOLD = 0.15


class DriftDirection(StrEnum):
    """The measured direction of one (entity, capability) quality stream."""

    IMPROVED = "improved"
    DEGRADED = "degraded"
    UNCHANGED = "unchanged"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DriftSignal:
    """One measured comparison of baseline vs recent quality for one capability.

    ``evidence`` is the human-readable justification and it always names the
    observable counts (window sizes, means, threshold) — a verdict nobody can
    re-derive from the counts is an assertion, not a measurement.
    """

    capability: str
    entity_id: str
    baseline_mean: float | None
    recent_mean: float | None
    baseline_samples: int
    recent_samples: int
    shift: float | None
    direction: DriftDirection
    material: bool
    evidence: str

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "direction", DriftDirection(str(self.direction)))
        except ValueError as error:
            allowed = ", ".join(member.value for member in DriftDirection)
            raise ValueError(f"unknown direction {self.direction!r}; expected one of: {allowed}") from error
        if not self.capability:
            raise ValueError("capability must be non-empty")
        if not self.entity_id:
            raise ValueError("entity_id must be non-empty")
        for name in ("baseline_samples", "recent_samples"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be an int >= 0, got {value!r}")
        for name in ("baseline_mean", "recent_mean"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value))):
                raise ValueError(f"{name} must be a finite number or None, got {value!r}")
        if not self.evidence:
            raise ValueError("evidence must be non-empty: a signal without its observable justification is an assertion")

        if self.direction is DriftDirection.UNKNOWN:
            # UNKNOWN is the honest answer to an unmeasurable comparison: no
            # shift, and never flagged material.
            if self.shift is not None:
                raise ValueError("direction 'unknown' requires shift=None: a fabricated direction cannot carry a number")
            if self.material:
                raise ValueError("direction 'unknown' is never material: there is no measured shift to be material")
            return
        if self.shift is None or (not isinstance(self.shift, (int, float)) or isinstance(self.shift, bool) or not math.isfinite(float(self.shift))):
            raise ValueError(f"direction {self.direction.value!r} requires a finite shift, got {self.shift!r}")
        if self.baseline_mean is None or self.recent_mean is None:
            raise ValueError(f"direction {self.direction.value!r} requires both window means")
        if self.direction is DriftDirection.IMPROVED and self.shift <= 0:
            raise ValueError(f"direction 'improved' requires a positive shift, got {self.shift!r}")
        if self.direction is DriftDirection.DEGRADED and self.shift >= 0:
            raise ValueError("direction 'degraded' requires a negative shift")
        if bool(self.material) is not (self.direction in (DriftDirection.IMPROVED, DriftDirection.DEGRADED)):
            raise ValueError(f"material={self.material!r} disagrees with direction {self.direction.value!r}: materiality is exactly |shift| >= threshold")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "entity_id": self.entity_id,
            "baseline_mean": self.baseline_mean,
            "recent_mean": self.recent_mean,
            "baseline_samples": self.baseline_samples,
            "recent_samples": self.recent_samples,
            "shift": self.shift,
            "direction": self.direction.value,
            "material": self.material,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class DriftReport:
    """What drift detection found, with the audit evidence to re-derive it.

    ``degraded_entities`` are entities carrying a material DEGRADED signal;
    ``stale_entities`` are entities whose DELIVERED-outcome evidence has aged
    past the revalidation window. Both are sorted, de-duplicated projections
    of ``signals`` and the record stream — nothing else. This report is a
    proposal for a policy layer to act on, never an action.
    """

    signals: tuple[DriftSignal, ...]
    degraded_entities: tuple[str, ...]
    stale_entities: tuple[str, ...]
    checked_at: str
    profile_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "signals", tuple(self.signals))
        for signal in self.signals:
            if not isinstance(signal, DriftSignal):
                raise ValueError(f"signals must contain DriftSignal entries, got {signal!r}")
        object.__setattr__(self, "degraded_entities", tuple(sorted(set(self.degraded_entities))))
        object.__setattr__(self, "stale_entities", tuple(sorted(set(self.stale_entities))))
        if not self.checked_at:
            raise ValueError("checked_at must be non-empty")
        if not self.profile_version:
            raise ValueError("profile_version must be non-empty (no verdict without its profile version)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "signals": [signal.to_dict() for signal in self.signals],
            "degraded_entities": list(self.degraded_entities),
            "stale_entities": list(self.stale_entities),
            "checked_at": self.checked_at,
            "profile_version": self.profile_version,
        }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def detect_drift(
    records: Iterable[CapabilityRecord],
    *,
    profile_version: str,
    baseline_samples: int = DEFAULT_BASELINE_SAMPLES,
    recent_samples: int = DEFAULT_RECENT_SAMPLES,
    threshold: float = DEFAULT_MATERIALITY_THRESHOLD,
    now: datetime | None = None,
) -> DriftReport:
    """Compare each (entity, capability) baseline window to its recent window.

    Pure reader: it consumes records, returns a report, and mutates nothing —
    not the records, not a passport, not any routing state. A window short of
    its required measurable samples yields an UNKNOWN signal whose evidence
    names the shortfall; only a material negative shift marks an entity
    degraded, and only outcome evidence older than the revalidation window
    marks one stale. Everything is scoped to ``profile_version`` so a rubric
    change cannot impersonate drift.
    """
    if not profile_version:
        raise ValueError("profile_version must be non-empty (no verdict without its profile version)")
    for name, value in (("baseline_samples", baseline_samples), ("recent_samples", recent_samples)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be an int >= 1, got {value!r}")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(float(threshold)) or threshold <= 0:
        raise ValueError(f"threshold must be a finite number > 0, got {threshold!r}")
    reference = now if now is not None else datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    reference = reference.astimezone(UTC)

    materialised = list(records)
    scoped = [record for record in materialised if record.profile_version == profile_version]

    # The signal population covers every known (entity, capability) pair, but
    # only profile-scoped records contribute values: asking about a rubric no
    # record was measured under must report "no comparable evidence" (an
    # UNKNOWN signal naming the zero) rather than silently omitting the entity.
    streams: dict[tuple[str, str], list[CapabilityRecord]] = {}
    for record in materialised:
        streams.setdefault((record.entity_id, record.capability), []).append(record)

    signals: list[DriftSignal] = []
    for entity_id, capability in sorted(streams):
        stream = sorted(streams[(entity_id, capability)], key=lambda record: (_parse_timestamp(record.recorded_at), record.record_id))
        stream = [record for record in stream if record.profile_version == profile_version]
        baseline = stream[:baseline_samples]
        recent = stream[-recent_samples:]
        baseline_values = [float(record.quality) for record in baseline if record.quality is not None]
        recent_values = [float(record.quality) for record in recent if record.quality is not None]
        baseline_mean = _mean(baseline_values)
        recent_mean = _mean(recent_values)

        if len(baseline_values) < baseline_samples or len(recent_values) < recent_samples:
            evidence = f"baseline window holds {len(baseline_values)} of {baseline_samples} required measurable sample(s) and recent window {len(recent_values)} of {recent_samples}: direction is unknown, not estimated"
            signals.append(
                DriftSignal(
                    capability=capability,
                    entity_id=entity_id,
                    baseline_mean=baseline_mean,
                    recent_mean=recent_mean,
                    baseline_samples=len(baseline_values),
                    recent_samples=len(recent_values),
                    shift=None,
                    direction=DriftDirection.UNKNOWN,
                    material=False,
                    evidence=evidence,
                )
            )
            continue

        shift = recent_mean - baseline_mean
        material = abs(shift) >= threshold
        if not material:
            direction = DriftDirection.UNCHANGED
        else:
            direction = DriftDirection.IMPROVED if shift > 0 else DriftDirection.DEGRADED
        evidence = (
            f"baseline mean {baseline_mean:.3f} over {len(baseline_values)} sample(s) vs recent mean {recent_mean:.3f} "
            f"over {len(recent_values)}: shift {shift:+.3f} is {'at least' if material else 'below'} the materiality threshold {threshold:g}"
        )
        signals.append(
            DriftSignal(
                capability=capability,
                entity_id=entity_id,
                baseline_mean=baseline_mean,
                recent_mean=recent_mean,
                baseline_samples=len(baseline_values),
                recent_samples=len(recent_values),
                shift=shift,
                direction=direction,
                material=material,
                evidence=evidence,
            )
        )

    degraded = {signal.entity_id for signal in signals if signal.material and signal.direction is DriftDirection.DEGRADED}

    outcome_stamps: dict[str, list[datetime]] = {}
    for record in scoped:
        if record.outcome in DELIVERED_OUTCOMES:
            outcome_stamps.setdefault(record.entity_id, []).append(_parse_timestamp(record.recorded_at))
    stale: set[str] = set()
    for entity_id, stamps in outcome_stamps.items():
        newest = max(stamps)
        level = derive_evidence_level(len(stamps), has_outcome=True, last_evaluated_at=newest.isoformat(), now=reference)
        # Outcome evidence that aged past the revalidation window is
        # OUTCOME_VERIFIED rather than RECENTLY_REVALIDATED: it once was
        # verified, and that verification is now stale.
        if level is EvidenceLevel.OUTCOME_VERIFIED:
            stale.add(entity_id)

    return DriftReport(signals=tuple(signals), degraded_entities=tuple(degraded), stale_entities=tuple(stale), checked_at=reference.isoformat(), profile_version=profile_version)


__all__ = [
    "DEFAULT_BASELINE_SAMPLES",
    "DEFAULT_MATERIALITY_THRESHOLD",
    "DEFAULT_RECENT_SAMPLES",
    "DriftDirection",
    "DriftReport",
    "DriftSignal",
    "detect_drift",
]
