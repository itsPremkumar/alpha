"""Coverage analysis for destructive authority.

The question is deliberately narrow: for each capability classified as
non-reversible, is there an enforcement point whose *default* posture is deny
and whose check can actually fail?  A gate that exists in source but is
default-allow, empty-and-therefore-allowing, or structurally constant does not
count.

This is a reporting calculation only.  It never edits a gate, changes a
posture, or grants authority.  The inverse check is first-class: a gate whose
condition can never fail is reported even when every test is green, because a
silently broken guard is indistinguishable from a working one at runtime.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .models import AuthorityRecord, CoverageSummary, Gate, GatePosture, Reversibility


def _is_destructive(record: AuthorityRecord) -> bool:
    return record.capability.reversibility_class is not Reversibility.REVERSIBLE


def _is_effective_protector(gate: Gate) -> bool:
    return gate.default_posture is GatePosture.DEFAULT_DENY and gate.can_fail


def compute_coverage(
    records: Iterable[AuthorityRecord],
    gates: Iterable[Gate] = (),
) -> CoverageSummary:
    """Return deterministic coverage plus ungated/ineffective findings."""

    ordered_records = sorted(records, key=lambda item: (item.capability.source_file, item.capability.source_line, item.capability.id))
    ordered_gates = sorted(gates, key=lambda item: (item.source_file, item.source_line, item.id))
    destructive = [record for record in ordered_records if _is_destructive(record)]
    protected: list[str] = []
    ungated: list[str] = []
    associated_ids: set[str] = set()
    for record in ordered_records:
        gate = record.gate
        if gate is not None:
            associated_ids.add(gate.id)
        metadata_gate_ids = record.capability.metadata.get("gate_ids", ())
        if isinstance(metadata_gate_ids, (list, tuple, set, frozenset)):
            associated_ids.update(str(item) for item in metadata_gate_ids)
        if not _is_destructive(record):
            continue
        if gate is not None and _is_effective_protector(gate):
            protected.append(record.capability.id)
        else:
            ungated.append(record.capability.id)

    ineffective = tuple(gate.id for gate in ordered_gates if not gate.can_fail)
    record_ids = {record.capability.id for record in ordered_records}
    gates_with_nothing = tuple(gate.id for gate in ordered_gates if gate.id not in associated_ids and not (set(gate.protects) & record_ids))
    denominator = len(destructive)
    numerator = len(protected)
    percentage = round((numerator / denominator) * 100, 1) if denominator else 0.0
    return CoverageSummary(
        denominator=denominator,
        numerator=numerator,
        percentage=percentage,
        destructive_capability_count=denominator,
        gated_destructive_capability_count=numerator,
        ungated_destructive=tuple(sorted(ungated)),
        gates_protecting_nothing=gates_with_nothing,
        ineffective_gates=ineffective,
    )


def coverage_for_report(report: object) -> CoverageSummary:
    """Coverage helper for a report-like object without importing census."""

    records = getattr(report, "records", ())
    gates = getattr(report, "gates", ())
    return compute_coverage(records, gates)


def ungated_destructive_capabilities(records: Sequence[AuthorityRecord]) -> tuple[str, ...]:
    """Return ids of non-reversible records without an effective deny gate."""

    coverage = compute_coverage(records)
    return coverage.ungated_destructive


def ineffective_gates(gates: Sequence[Gate]) -> tuple[str, ...]:
    """Return gates whose static evidence says their check cannot fail."""

    return tuple(sorted(gate.id for gate in gates if not gate.can_fail))


__all__ = [
    "compute_coverage",
    "coverage_for_report",
    "ineffective_gates",
    "ungated_destructive_capabilities",
]
