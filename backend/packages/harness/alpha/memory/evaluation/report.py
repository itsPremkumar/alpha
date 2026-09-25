"""Honest text/JSON rendering for memory evaluation reports.

The renderer never turns ``None`` into zero, rounds a failing rate upward, or
hides an unavailable case.  Numeric values are emitted with Python's direct
representation so a reader can audit the exact measured fraction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from alpha.memory.evaluation.models import SuiteReport

ReportFormat = Literal["text", "json"]


def render_json(report: SuiteReport, *, indent: int = 2) -> str:
    """Render the complete machine-readable report."""

    return json.dumps(report.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True)


def render_text(report: SuiteReport) -> str:
    """Render a human report with explicit scope and unavailable reasons."""

    lines = [
        "Alpha memory evaluation report",
        f"Scope: {report.ran_cases} of {report.total_cases} cases ran; {report.unavailable_cases} unavailable; {report.error_cases} errors.",
        f"Status: {report.status.upper()}",
        f"Evidence: {report.evidence_kind or 'not recorded'}",
    ]
    if report.reason:
        lines.append(f"Reason: {report.reason}")
    lines.append("")
    lines.append("Metrics:")
    for name in report.metrics:
        lines.append(f"  {name}: {_display_metric(report.metrics.get(name))}")
    lines.append("")
    lines.append("Per-ability counts:")
    for ability, counts in report.per_ability.items():
        lines.append(f"  {ability}: total={counts.get('total', 0)} pass={counts.get('pass', 0)} fail={counts.get('fail', 0)} unavailable={counts.get('unavailable', 0)} error={counts.get('error', 0)}")
    if report.thresholds:
        lines.append("")
        lines.append("Configured thresholds:")
        for check in report.thresholds:
            state = "UNAVAILABLE" if check.passed is None else ("PASS" if check.passed else "FAIL")
            current = _display_metric(check.current)
            lines.append(f"  [{state}] {check.metric}: current={current} {check.comparator} {check.threshold!r}; {check.reason or 'configured gate satisfied'}")
    if report.baseline is not None:
        lines.append("")
        lines.append(f"Baseline: {'enabled' if report.baseline.enabled else 'disabled'} ({'PASS' if report.baseline.passed else 'FAIL'}) {report.baseline.path or ''}".rstrip())
        if report.baseline.reason:
            lines.append(f"  {report.baseline.reason}")
        for metric, delta in report.baseline.overall.items():
            lines.append(f"  {metric}: baseline={_display_number(delta.get('baseline'))} current={_display_number(delta.get('current'))} delta={_display_number(delta.get('delta'))} gate={_display_gate(delta.get('passed'))}")
        for ability, values in report.baseline.per_ability.items():
            for metric, delta in values.items():
                lines.append(f"  {ability}.{metric}: baseline={_display_number(delta.get('baseline'))} current={_display_number(delta.get('current'))} delta={_display_number(delta.get('delta'))} gate={_display_gate(delta.get('passed'))}")
    if report.results:
        lines.append("")
        lines.append("Cases:")
        for result in report.results:
            lines.append(f"  [{result.status.upper()}] {result.case_id} ({result.ability})")
            if result.reason:
                lines.append(f"    reason: {result.reason}")
            elif result.status in {"unavailable", "error"}:
                lines.append(f"    reason: {result.status}")
            for outcome in result.question_outcomes:
                evidence = ",".join(outcome.evidence_ids) or "none"
                superseded = ",".join(outcome.superseded_evidence_ids) or "none"
                lines.append(f"    {outcome.question_id}: correct={outcome.correct} evidence_ids={evidence} superseded_evidence_ids={superseded} failure_reasons={'; '.join(outcome.failure_reasons) or 'none'}")
    return "\n".join(lines) + "\n"


def render(report: SuiteReport, *, format: ReportFormat = "text") -> str:
    """Dispatch to text or JSON rendering."""

    if format == "text":
        return render_text(report)
    if format == "json":
        return render_json(report)
    raise ValueError(f"unsupported report format: {format!r}")


def write_report(report: SuiteReport, path: str | Path, *, format: ReportFormat = "json") -> Path:
    """Write a report only to an explicitly supplied path."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render(report, format=format), encoding="utf-8")
    return destination


def _display_metric(value: float | int | None) -> str:
    return "unavailable" if value is None else _display_number(value)


def _display_number(value: object) -> str:
    return "unavailable" if value is None else repr(value)


def _display_gate(value: object) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "UNCONFIGURED"


__all__ = ["ReportFormat", "render", "render_json", "render_text", "write_report"]
