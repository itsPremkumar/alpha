"""Report schema for the subagent delegation probe (W1, Phase 0).

This module is the *pure* contract between
``backend/scripts/subagent_delegation_probe.py`` (the producer) and the offline
tests (``backend/tests/test_subagent_delegation_probe_schema.py``). It owns
three things and nothing else:

1. **The report shape.** :class:`ProbeReport` and :class:`ScenarioReport` with
   every joinable field the W1 plan names: outcome, stop reason, tokens (per
   model), wall time, queue wait, acceptance verdicts, receipts, artifacts and
   event persistence.
2. **Null honesty.** Every measured field starts as ``None`` and only a real
   measurement may replace it. A field that was not measured serializes as
   JSON ``null``, never as ``0``/``""``/``[]`` — "no delegations happened" (a
   measurement: ``0``) and "the ledger could not be read" (unmeasured:
   ``null``) must stay distinguishable. :func:`to_jsonable` therefore keeps
   every key present and converts non-finite floats (NaN/inf) to ``None``;
   a number nobody measured is not a number.
3. **Drift detection.** :func:`validate_report` returns one violation string
   per drifted field (missing key, unknown key, wrong type, vocabulary breach,
   outcome/expectation inconsistency). The probe runs it before writing and
   exits ``2`` when it fails, so the report format cannot silently drift away
   from this file. :func:`report_exit_code` pins the exit-code mapping;
   :func:`render_markdown` renders the report without ever printing a stand-in
   number for ``None``.

Spec (plan §W1): "A pure report-schema module under ``backend/tests/support/``
(or ``scripts/support/``) with a dataclass + tests; the probe fails on schema
drift." This is the ``scripts/support/`` placement; the offline suite pins the
schema, the null-honesty rule and the delete-a-field negative control.
"""

from __future__ import annotations

import dataclasses
import json
import math
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

REPORT_SCHEMA_VERSION = 1

#: How a scenario ended relative to its declared expectation.
SCENARIO_OUTCOMES: tuple[str, ...] = ("met", "unmet", "refused", "incomplete", "error")

#: Report-level verdict. ``complete`` means every requested scenario produced a
#: measured outcome; anything that stopped the probe (Gateway died, schema
#: drift preventing a write) is ``incomplete`` with a reason.
REPORT_VERDICTS: tuple[str, ...] = ("complete", "incomplete")

#: Terminal run statuses the Gateway reports (subset used for polling).
TERMINAL_RUN_STATUSES: tuple[str, ...] = ("success", "error", "interrupted", "timeout", "canceled")

#: The measured field a scenario *declares* it cannot produce yet, so no
#: reader misreads the ``null`` as a broken measurement. W3.a instruments
#: per-delegation queue wait; until then the probe records ``null``.
UNINSTRUMENTED_FIELDS: tuple[str, ...] = ("queue_wait_seconds",)


@dataclass
class ScenarioReport:
    """One scenario's joined measurements.

    Every field is ``None`` until a measurement lands. Do not add defaults
    like ``0``/``[]``: those encode "measured zero", which is a different
    fact from "not measured".
    """

    key: str
    title: str
    expectation: str
    outcome: str | None = None
    detail: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    run_status: str | None = None
    #: Guardrail-cap reasons observed for this run's delegations
    #: (``token_capped`` / ``turn_capped`` / ``loop_capped``). ``[]`` is a
    #: measurement ("no cap fired"); ``None`` means the ledger was unreadable.
    stop_reasons: list[str] | None = None
    #: Number of terminal ledger entries recorded for this run. ``0`` is a
    #: measurement ("no delegations"), ``None`` means the ledger was unreadable.
    delegations: int | None = None
    models: list[str] | None = None
    tokens_by_model: dict[str, int] | None = None
    #: ``[{"task_id": str, "model": str|None, "input_tokens": int|None,
    #: "output_tokens": int|None, "total_tokens": int|None}, ...]`` from the
    #: persisted ``subagent.end`` events.
    tokens_by_child: list[dict[str, Any]] | None = None
    wall_seconds: float | None = None
    queue_wait_seconds: float | None = None
    #: ``[{"criterion": str, "family": str, "checked": bool, "holds": bool,
    #: "detail": str}, ...]`` projected from the delegation ledger's
    #: ``acceptance_verdict`` leaves.
    acceptance_verdicts: list[dict[str, Any]] | None = None
    #: ``{"cited": int, "resolved": int, "failed": int, "unknown": int}``
    #: summed from ledger ``receipt_verdict`` blocks; ``None`` when no entry
    #: carried one (receipt verdicts are optional per delegation).
    receipts: dict[str, int] | None = None
    #: ``[{"path": str, "change": str}, ...]`` from the run's
    #: workspace-changes snapshot; ``None`` when that snapshot was unavailable.
    artifacts: list[dict[str, Any]] | None = None
    #: ``{"source": str, "total": int, "counts": {event_type: n},
    #: "task_ids": [str, ...]}`` over persisted ``subagent.*`` events;
    #: ``None`` when the event read failed.
    event_persistence: dict[str, Any] | None = None
    #: Run stream modes the probe requested; ``custom`` is the structural
    #: precondition for persisted ``subagent.*`` events.
    stream_modes: list[str] | None = None
    expectation_satisfied: bool | None = None


@dataclass
class ProbeReport:
    """Top-level report for one probe invocation."""

    run_id: str
    started_at: str
    base_url: str
    platform: str
    stream_modes: list[str]
    notes: list[str] = field(default_factory=list)
    schema_version: int = REPORT_SCHEMA_VERSION
    ended_at: str | None = None
    alpha_version: str | None = None
    verdict: str = "incomplete"
    incomplete_reason: str | None = None
    scenarios: list[ScenarioReport] = field(default_factory=list)
    schema_violations: list[str] = field(default_factory=list)


#: Ordered key lists derived from the dataclasses — the single source of truth
#: for validation, serialization and tests.
SCENARIO_FIELDS: tuple[str, ...] = tuple(f.name for f in dataclasses.fields(ScenarioReport))
REPORT_FIELDS: tuple[str, ...] = tuple(f.name for f in dataclasses.fields(ProbeReport))


def new_run_id() -> str:
    """A sortable, unique run id: UTC timestamp + random suffix."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4)


def _sanitize(value: Any) -> Any:
    """Recursively replace non-finite floats with ``None``.

    A NaN or infinity is not a measurement — it is the absence of one wearing
    a number's clothes — so it serializes as ``null`` like every other
    unmeasured field. Also normalizes tuples into lists for JSON.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def to_jsonable(report: ProbeReport) -> dict[str, Any]:
    """Serialize the report to a JSON-ready dict.

    Every schema key is present even when its value is ``None``, so a consumer
    can distinguish "field absent from an old report" (schema drift, caught by
    :func:`validate_report`) from "field present but not measured" (``null``).
    """
    payload = _sanitize(dataclasses.asdict(report))
    # Assert the shape the docstring promises; drift here is a producer bug.
    missing = [name for name in REPORT_FIELDS if name not in payload]
    if missing:  # pragma: no cover - dataclasses.asdict cannot drop fields
        raise AssertionError(f"serialized report lost required keys: {missing}")
    return payload


def _violation(violations: list[str], path: str, message: str) -> None:
    violations.append(f"{path}: {message}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _check_keys(violations: list[str], path: str, payload: Mapping[str, Any], expected: tuple[str, ...]) -> bool:
    ok = True
    for name in expected:
        if name not in payload:
            _violation(violations, path, f"missing key {name!r}")
            ok = False
    for name in payload:
        if name not in expected:
            _violation(violations, path, f"unknown key {name!r} (schema drift; bump REPORT_SCHEMA_VERSION to add a field)")
            ok = False
    return ok


def _validate_optional_str(violations: list[str], path: str, value: Any) -> None:
    if value is not None and not isinstance(value, str):
        _violation(violations, path, f"expected str or null, got {type(value).__name__}")


def _validate_optional_number(violations: list[str], path: str, value: Any, *, minimum: float = 0.0) -> None:
    if value is None:
        return
    if not _is_number(value):
        _violation(violations, path, f"expected a finite number or null, got {type(value).__name__}")
    elif value < minimum:
        _violation(violations, path, f"expected >= {minimum}, got {value}")


def _validate_scenario(violations: list[str], index: int, scenario: Any) -> None:
    path = f"scenarios[{index}]"
    if not isinstance(scenario, Mapping):
        _violation(violations, path, f"expected an object, got {type(scenario).__name__}")
        return
    _check_keys(violations, path, scenario, SCENARIO_FIELDS)

    _validate_optional_str(violations, f"{path}.key", scenario.get("key"))
    _validate_optional_str(violations, f"{path}.title", scenario.get("title"))
    _validate_optional_str(violations, f"{path}.expectation", scenario.get("expectation"))
    _validate_optional_str(violations, f"{path}.detail", scenario.get("detail"))
    _validate_optional_str(violations, f"{path}.thread_id", scenario.get("thread_id"))
    _validate_optional_str(violations, f"{path}.run_id", scenario.get("run_id"))
    _validate_optional_str(violations, f"{path}.run_status", scenario.get("run_status"))

    stop_reasons = scenario.get("stop_reasons")
    if stop_reasons is not None and (not isinstance(stop_reasons, list) or any(not isinstance(r, str) for r in stop_reasons)):
        _violation(violations, f"{path}.stop_reasons", "expected a list of strings or null")

    outcome = scenario.get("outcome")
    if outcome is None:
        _violation(violations, f"{path}.outcome", "not measured — every scenario must record an outcome")
    elif not isinstance(outcome, str) or outcome not in SCENARIO_OUTCOMES:
        _violation(violations, f"{path}.outcome", f"unknown outcome {outcome!r}; expected one of {SCENARIO_OUTCOMES}")

    delegations = scenario.get("delegations")
    if delegations is not None and (not _is_int(delegations) or delegations < 0):
        _violation(violations, f"{path}.delegations", f"expected a non-negative int or null, got {delegations!r}")

    models = scenario.get("models")
    if models is not None and (not isinstance(models, list) or any(not isinstance(m, str) for m in models)):
        _violation(violations, f"{path}.models", "expected a list of strings or null")

    tokens_by_model = scenario.get("tokens_by_model")
    if tokens_by_model is not None:
        if not isinstance(tokens_by_model, Mapping):
            _violation(violations, f"{path}.tokens_by_model", "expected an object or null")
        else:
            for name, count in tokens_by_model.items():
                if not isinstance(name, str) or not _is_int(count) or count < 0:
                    _violation(violations, f"{path}.tokens_by_model[{name!r}]", f"expected a non-negative int, got {count!r}")

    tokens_by_child = scenario.get("tokens_by_child")
    if tokens_by_child is not None:
        if not isinstance(tokens_by_child, list):
            _violation(violations, f"{path}.tokens_by_child", "expected a list or null")
        else:
            for i, child in enumerate(tokens_by_child):
                child_path = f"{path}.tokens_by_child[{i}]"
                if not isinstance(child, Mapping):
                    _violation(violations, child_path, "expected an object")
                    continue
                if not isinstance(child.get("task_id"), str):
                    _violation(violations, f"{child_path}.task_id", "expected a string")
                _validate_optional_str(violations, f"{child_path}.model", child.get("model"))
                for token_key in ("input_tokens", "output_tokens", "total_tokens"):
                    value = child.get(token_key)
                    if value is not None and (not _is_int(value) or value < 0):
                        _violation(violations, f"{child_path}.{token_key}", f"expected a non-negative int or null, got {value!r}")

    _validate_optional_number(violations, f"{path}.wall_seconds", scenario.get("wall_seconds"))
    _validate_optional_number(violations, f"{path}.queue_wait_seconds", scenario.get("queue_wait_seconds"))

    acceptance = scenario.get("acceptance_verdicts")
    if acceptance is not None:
        if not isinstance(acceptance, list):
            _violation(violations, f"{path}.acceptance_verdicts", "expected a list or null")
        else:
            for i, leaf in enumerate(acceptance):
                leaf_path = f"{path}.acceptance_verdicts[{i}]"
                if not isinstance(leaf, Mapping):
                    _violation(violations, leaf_path, "expected an object")
                    continue
                if not isinstance(leaf.get("criterion"), str):
                    _violation(violations, f"{leaf_path}.criterion", "expected a string")
                if not isinstance(leaf.get("family"), str):
                    _violation(violations, f"{leaf_path}.family", "expected a string")
                if not isinstance(leaf.get("checked"), bool):
                    _violation(violations, f"{leaf_path}.checked", "expected a bool")
                if not isinstance(leaf.get("holds"), bool):
                    _violation(violations, f"{leaf_path}.holds", "expected a bool")
                if not isinstance(leaf.get("detail"), str):
                    _violation(violations, f"{leaf_path}.detail", "expected a string")

    receipts = scenario.get("receipts")
    if receipts is not None:
        if not isinstance(receipts, Mapping):
            _violation(violations, f"{path}.receipts", "expected an object or null")
        else:
            for key in ("cited", "resolved", "failed", "unknown"):
                value = receipts.get(key)
                if not _is_int(value) or value < 0:
                    _violation(violations, f"{path}.receipts[{key!r}]", f"expected a non-negative int, got {value!r}")

    artifacts = scenario.get("artifacts")
    if artifacts is not None:
        if not isinstance(artifacts, list):
            _violation(violations, f"{path}.artifacts", "expected a list or null")
        else:
            for i, artifact in enumerate(artifacts):
                artifact_path = f"{path}.artifacts[{i}]"
                if not isinstance(artifact, Mapping):
                    _violation(violations, artifact_path, "expected an object")
                    continue
                if not isinstance(artifact.get("path"), str):
                    _violation(violations, f"{artifact_path}.path", "expected a string")
                if not isinstance(artifact.get("change"), str):
                    _violation(violations, f"{artifact_path}.change", "expected a string")

    event_persistence = scenario.get("event_persistence")
    if event_persistence is not None:
        if not isinstance(event_persistence, Mapping):
            _violation(violations, f"{path}.event_persistence", "expected an object or null")
        else:
            if not isinstance(event_persistence.get("source"), str):
                _violation(violations, f"{path}.event_persistence.source", "expected a string")
            total = event_persistence.get("total")
            if not _is_int(total) or total < 0:
                _violation(violations, f"{path}.event_persistence.total", f"expected a non-negative int, got {total!r}")
            counts = event_persistence.get("counts")
            if not isinstance(counts, Mapping):
                _violation(violations, f"{path}.event_persistence.counts", "expected an object")
            else:
                for name, count in counts.items():
                    if not isinstance(name, str) or not _is_int(count) or count < 0:
                        _violation(violations, f"{path}.event_persistence.counts[{name!r}]", f"expected a non-negative int, got {count!r}")

    modes = scenario.get("stream_modes")
    if modes is not None and (not isinstance(modes, list) or any(not isinstance(m, str) for m in modes)):
        _violation(violations, f"{path}.stream_modes", "expected a list of strings or null")

    satisfied = scenario.get("expectation_satisfied")
    if satisfied is not None and not isinstance(satisfied, bool):
        _violation(violations, f"{path}.expectation_satisfied", f"expected bool or null, got {type(satisfied).__name__}")

    # Outcome/expectation consistency: a refusal, transport death or probe
    # error did not satisfy or fail the expectation — it failed to test it.
    if outcome == "met" and satisfied is not True:
        _violation(violations, f"{path}.expectation_satisfied", "outcome 'met' requires expectation_satisfied=true")
    elif outcome == "unmet" and satisfied is not False:
        _violation(violations, f"{path}.expectation_satisfied", "outcome 'unmet' requires expectation_satisfied=false")
    elif outcome in ("refused", "incomplete", "error") and satisfied is not None:
        _violation(
            violations,
            f"{path}.expectation_satisfied",
            f"outcome {outcome!r} means the expectation was not tested; expectation_satisfied must be null",
        )


def validate_report(payload: Any) -> list[str]:
    """Return one violation string per schema breach; empty list means valid.

    This is the drift gate: the probe calls it before writing and exits ``2``
    when the list is non-empty (see :func:`report_exit_code`). The negative
    control in the offline suite deletes a captured field and pins that the
    violation names it.
    """
    violations: list[str] = []
    if not isinstance(payload, Mapping):
        return [f"report: expected an object, got {type(payload).__name__}"]
    if not _check_keys(violations, "report", payload, REPORT_FIELDS):
        pass

    version = payload.get("schema_version")
    if version != REPORT_SCHEMA_VERSION or isinstance(version, bool):
        _violation(violations, "report.schema_version", f"expected {REPORT_SCHEMA_VERSION}, got {version!r}")

    for name in ("run_id", "started_at", "base_url", "platform"):
        if not isinstance(payload.get(name), str) or not payload.get(name):
            _violation(violations, f"report.{name}", "expected a non-empty string")

    _validate_optional_str(violations, "report.ended_at", payload.get("ended_at"))
    _validate_optional_str(violations, "report.alpha_version", payload.get("alpha_version"))

    verdict = payload.get("verdict")
    if verdict not in REPORT_VERDICTS:
        _violation(violations, "report.verdict", f"unknown verdict {verdict!r}; expected one of {REPORT_VERDICTS}")

    incomplete_reason = payload.get("incomplete_reason")
    if verdict == "incomplete":
        if not isinstance(incomplete_reason, str) or not incomplete_reason.strip():
            _violation(violations, "report.incomplete_reason", "an incomplete report must say why")
    elif incomplete_reason is not None:
        _violation(violations, "report.incomplete_reason", "a complete report must not carry an incomplete reason")

    modes = payload.get("stream_modes")
    if not isinstance(modes, list) or not modes or any(not isinstance(m, str) for m in modes):
        _violation(violations, "report.stream_modes", "expected a non-empty list of strings")

    notes = payload.get("notes")
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        _violation(violations, "report.notes", "expected a list of strings")

    violations_list = payload.get("schema_violations")
    if not isinstance(violations_list, list) or any(not isinstance(v, str) for v in violations_list):
        _violation(violations, "report.schema_violations", "expected a list of strings")

    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        _violation(violations, "report.scenarios", "expected a non-empty list")
        return violations
    for index, scenario in enumerate(scenarios):
        _validate_scenario(violations, index, scenario)
    return violations


def report_exit_code(payload: Mapping[str, Any]) -> int:
    """Pinned exit-code mapping for the probe process.

    ``2`` — schema violations (the probe's own contract is broken; fix the
    report shape, never "fix" the data). ``1`` — the run is incomplete (the
    Gateway died, the probe could not measure). ``0`` — every requested
    scenario produced a measured outcome. Note that a scenario *finding*
    (``unmet``/``refused``) does not change the exit code: it is data, not
    probe failure.
    """
    violations = payload.get("schema_violations") or []
    if violations:
        return 2
    if payload.get("verdict") == "incomplete":
        return 1
    return 0


def _render_cell(value: Any) -> str:
    """Render one table cell; ``None`` is the honest em dash, never a number."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, (list, dict)):
        return str(json.dumps(value, ensure_ascii=False, default=str))
    return str(value)


def render_markdown(payload: Mapping[str, Any]) -> str:
    """Render the report as Markdown for the operator console and the audit trail."""
    lines: list[str] = []
    lines.append(f"# Subagent delegation probe — {payload.get('run_id', '?')}")
    lines.append("")
    lines.append(f"- verdict: **{payload.get('verdict', '?')}**")
    if payload.get("incomplete_reason"):
        lines.append(f"- incomplete reason: {payload['incomplete_reason']}")
    lines.append(f"- base_url: {payload.get('base_url', '?')}")
    lines.append(f"- alpha_version: {_render_cell(payload.get('alpha_version'))}")
    lines.append(f"- platform: {payload.get('platform', '?')}")
    lines.append(f"- schema_version: {payload.get('schema_version', '?')}")
    lines.append(f"- stream_modes: {', '.join(payload.get('stream_modes') or []) or '—'}")
    lines.append(f"- started_at: {payload.get('started_at', '?')}")
    lines.append(f"- ended_at: {_render_cell(payload.get('ended_at'))}")
    notes = payload.get("notes") or []
    if notes:
        lines.append("- notes:")
        lines.extend(f"  - {note}" for note in notes)
    violations = payload.get("schema_violations") or []
    if violations:
        lines.append("- schema violations:")
        lines.extend(f"  - {violation}" for violation in violations)
    lines.append("")

    headers = [
        "scenario",
        "outcome",
        "run status",
        "stop reasons",
        "delegations",
        "models",
        "total tokens",
        "wall s",
        "queue wait s",
        "acceptance",
        "receipts",
        "artifacts",
        "events",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "---|" * len(headers))
    for scenario in payload.get("scenarios") or []:
        tokens = scenario.get("tokens_by_model")
        total_tokens = None if tokens is None else sum(int(v) for v in tokens.values())
        acceptance = scenario.get("acceptance_verdicts")
        acceptance_summary = None
        if acceptance is not None:
            holds = sum(1 for leaf in acceptance if leaf.get("checked") and leaf.get("holds"))
            acceptance_summary = f"{holds}/{len(acceptance)} hold"
        receipts = scenario.get("receipts")
        receipts_summary = None if receipts is None else f"cited {receipts.get('cited')}"
        events = scenario.get("event_persistence")
        events_summary = None if events is None else f"{events.get('total')} rows"
        stop_reasons = scenario.get("stop_reasons")
        row = [
            str(scenario.get("key", "?")),
            str(scenario.get("outcome", "?")),
            _render_cell(scenario.get("run_status")),
            _render_cell(stop_reasons),
            _render_cell(scenario.get("delegations")),
            _render_cell(scenario.get("models")),
            _render_cell(total_tokens),
            _render_cell(scenario.get("wall_seconds")),
            _render_cell(scenario.get("queue_wait_seconds")),
            _render_cell(acceptance_summary),
            _render_cell(receipts_summary),
            _render_cell(None if scenario.get("artifacts") is None else len(scenario.get("artifacts") or [])),
            _render_cell(events_summary),
        ]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Scenario details")
    for scenario in payload.get("scenarios") or []:
        lines.append("")
        lines.append(f"### {scenario.get('key', '?')} — {scenario.get('title', '')}")
        lines.append("")
        lines.append(f"- expectation: {scenario.get('expectation', '?')}")
        lines.append(f"- outcome: {scenario.get('outcome', '?')} (expectation satisfied: {_render_cell(scenario.get('expectation_satisfied'))})")
        if scenario.get("detail"):
            lines.append(f"- detail: {scenario['detail']}")
        lines.append(f"- thread_id: {_render_cell(scenario.get('thread_id'))} · run_id: {_render_cell(scenario.get('run_id'))}")
        if scenario.get("tokens_by_child") is not None:
            lines.append("- tokens_by_child:")
            lines.append(f"  ```json\n  {json.dumps(scenario['tokens_by_child'], ensure_ascii=False, default=str)}\n  ```")
        if scenario.get("acceptance_verdicts"):
            lines.append("- acceptance_verdicts:")
            lines.append(f"  ```json\n  {json.dumps(scenario['acceptance_verdicts'], ensure_ascii=False, default=str)}\n  ```")
        if scenario.get("artifacts"):
            lines.append("- artifacts:")
            lines.append(f"  ```json\n  {json.dumps(scenario['artifacts'], ensure_ascii=False, default=str)}\n  ```")
        if scenario.get("event_persistence") is not None:
            lines.append(f"- event_persistence: {json.dumps(scenario['event_persistence'], ensure_ascii=False, default=str)}")
    lines.append("")
    return "\n".join(lines)
