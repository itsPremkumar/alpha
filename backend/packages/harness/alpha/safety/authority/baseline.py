"""Stored census baselines and pure change detection.

A baseline is a deliberate operator action: it is written by an explicit
``baseline``/``write-baseline`` command, never as a side effect of scanning.
Comparison is pure and deterministic.  The most important signal is a posture
flip: a gate that changed from ``default_deny`` to ``default_allow`` is called
out first and marked critical, because that is the exact shape of a silent
authority widening.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import AuditReport, Gate, GatePosture, Reversibility


class PostureFlip(BaseModel):
    """One gate whose default posture changed between baseline and current."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gate_id: str
    source: str
    before: GatePosture
    after: GatePosture
    severity: str = "critical"
    reason: str = "default-deny gate widened to default-allow"


class BaselineDiff(BaseModel):
    """Deterministic difference between two census reports."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_revision: str
    current_revision: str
    new_capabilities: tuple[str, ...] = Field(default_factory=tuple)
    new_destructive_capabilities: tuple[str, ...] = Field(default_factory=tuple)
    removed_capabilities: tuple[str, ...] = Field(default_factory=tuple)
    new_gates: tuple[str, ...] = Field(default_factory=tuple)
    removed_gates: tuple[str, ...] = Field(default_factory=tuple)
    posture_flips: tuple[PostureFlip, ...] = Field(default_factory=tuple)
    new_unknowns: tuple[str, ...] = Field(default_factory=tuple)
    resolved_unknowns: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def has_posture_flip(self) -> bool:
        return bool(self.posture_flips)

    @property
    def has_changes(self) -> bool:
        return bool(self.new_capabilities or self.removed_capabilities or self.new_gates or self.removed_gates or self.posture_flips or self.new_unknowns or self.resolved_unknowns)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _as_report(value: AuditReport | Mapping[str, Any]) -> AuditReport:
    if isinstance(value, AuditReport):
        return value
    return AuditReport.model_validate(dict(value))


def _capability_map(report: AuditReport) -> dict[str, Any]:
    return {record.capability.id: record for record in report.records}


def _gate_map(report: AuditReport) -> dict[str, Gate]:
    return {gate.id: gate for gate in report.gates}


def _unknown_keys(report: AuditReport) -> set[str]:
    return {f"{item.path}:{item.line}:{item.kind}:{item.rule_id}" for item in report.unknowns}


def diff_reports(
    baseline: AuditReport | Mapping[str, Any],
    current: AuditReport | Mapping[str, Any],
) -> BaselineDiff:
    """Compare two reports without reading or writing any file."""

    old = _as_report(baseline)
    new = _as_report(current)
    old_caps = _capability_map(old)
    new_caps = _capability_map(new)
    old_gates = _gate_map(old)
    new_gates = _gate_map(new)
    new_cap_ids = sorted(set(new_caps) - set(old_caps))
    new_destructive = tuple(capability_id for capability_id in new_cap_ids if new_caps[capability_id].capability.reversibility_class is not Reversibility.REVERSIBLE)
    old_unknowns = _unknown_keys(old)
    new_unknowns = _unknown_keys(new)
    flips: list[PostureFlip] = []
    for gate_id in sorted(set(old_gates) & set(new_gates)):
        before = old_gates[gate_id].default_posture
        after = new_gates[gate_id].default_posture
        if before is not after:
            flips.append(
                PostureFlip(
                    gate_id=gate_id,
                    source=new_gates[gate_id].source,
                    before=before,
                    after=after,
                    reason=f"posture changed from {before.value} to {after.value}",
                )
            )
    flips.sort(key=lambda item: item.gate_id)
    return BaselineDiff(
        baseline_revision=old.revision,
        current_revision=new.revision,
        new_capabilities=tuple(new_cap_ids),
        new_destructive_capabilities=new_destructive,
        removed_capabilities=tuple(sorted(set(old_caps) - set(new_caps))),
        new_gates=tuple(sorted(set(new_gates) - set(old_gates))),
        removed_gates=tuple(sorted(set(old_gates) - set(new_gates))),
        posture_flips=tuple(flips),
        new_unknowns=tuple(sorted(new_unknowns - old_unknowns)),
        resolved_unknowns=tuple(sorted(old_unknowns - new_unknowns)),
    )


def compare_census(
    baseline: AuditReport | Mapping[str, Any],
    current: AuditReport | Mapping[str, Any],
) -> BaselineDiff:
    """Alias for :func:`diff_reports`."""

    return diff_reports(baseline, current)


def baseline_payload(report: AuditReport) -> dict[str, Any]:
    """Return the versioned JSON-compatible baseline payload."""

    return {
        "schema_version": report.schema_version,
        "revision": report.revision,
        "report": report.to_dict(),
    }


def write_baseline(
    path: str | Path,
    report: AuditReport,
    *,
    revision: str | None = None,
) -> Path:
    """Explicitly write one baseline path; scanning never calls this."""

    snapshot = report.model_copy(update={"revision": revision or report.revision})
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = baseline_payload(snapshot)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return destination


def load_baseline(path: str | Path) -> AuditReport:
    """Load and validate a stored baseline report."""

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("authority baseline must contain a JSON object")
    report_payload = raw.get("report", raw)
    return AuditReport.model_validate(report_payload)


def save_baseline(
    path: str | Path,
    report: AuditReport,
    *,
    revision: str | None = None,
) -> Path:
    """Alias for :func:`write_baseline`."""

    return write_baseline(path, report, revision=revision)


__all__ = [
    "BaselineDiff",
    "PostureFlip",
    "baseline_payload",
    "compare_census",
    "diff_reports",
    "load_baseline",
    "save_baseline",
    "write_baseline",
]
