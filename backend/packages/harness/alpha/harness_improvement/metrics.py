"""Metrics and the deterministic synthetic benchmark (guide §14, §15, §20).

Two audiences:

* :func:`summarize_run_records` answers "what did the improvement system
  actually do" from durable run records only. Counts that were not measured are
  ``None``, never ``0`` — a metric that reads as zero when nothing was recorded
  is the same class of bug as a validator that reads ``not_run`` as a pass.
* :func:`synthetic_suite` and :func:`held_out_suite` are fixed, deterministic
  task definitions with hard acceptance checks, so baseline-versus-improved
  comparisons run repeatedly (offline, in tests, in CI) without provider calls
  and without drift in the dataset. They are **not** a performance claim about
  Alpha; they exercise the machinery and expose a regression if one exists.

The summary reports *process* metrics beside *outcome* metrics on purpose: a
candidate that resolved the targeted failure while doubling tool errors is not
a win, and one number cannot show both.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.harness_improvement.models import (
    CandidateScope,
    ContrastVerdict,
    DeliverableCheck,
    ImprovementStage,
    ProbeOutcome,
    TaskContract,
    compute_task_fingerprint,
)

#: The stage buckets that end a run; a run that never reached one is unfinished.
TERMINAL_STAGE_STRINGS: frozenset[str] = frozenset({str(s) for s in (ImprovementStage.COMPLETED, ImprovementStage.FAILED, ImprovementStage.CANCELLED, ImprovementStage.BUDGET_EXHAUSTED)})


@dataclass(frozen=True, slots=True)
class SyntheticTask:
    """A fixed task definition with deterministic acceptance checks."""

    name: str
    task_type: str
    domain_tags: tuple[str, ...]
    outcome: str
    deliverables: tuple[str, ...]
    checks: tuple[DeliverableCheck, ...]
    targeted_failure: str | None = None
    shared_inputs: dict[str, str] = field(default_factory=dict)

    def contract(self) -> TaskContract:
        task_text = f"Synthetic benchmark task {self.name}: {self.outcome}"
        return TaskContract(
            task=task_text,
            outcome=self.outcome,
            deliverables=self.deliverables,
            checks=self.checks,
            domain_tags=self.domain_tags,
            task_type=self.task_type,
            task_fingerprint=compute_task_fingerprint(task_text),
        )


def _artifact_checks(name: str) -> tuple[DeliverableCheck, ...]:
    return (
        DeliverableCheck(name=f"{name}.exists", path="outputs/report.json", kind="exists"),
        DeliverableCheck(name=f"{name}.parses", path="outputs/report.json", kind="parses_json"),
        DeliverableCheck(name=f"{name}.min_bytes", path="outputs/report.json", kind="min_bytes", min_bytes=24),
        DeliverableCheck(name=f"{name}.status_field", path="outputs/report.json", kind="text_contains", detail='"status"'),
    )


#: The fixed synthetic suite. Two artifact tasks and one document task; each has
#: a named targeted failure so a probe can resolve exactly one of them.
SYNTHETIC_TASKS: tuple[SyntheticTask, ...] = (
    SyntheticTask(
        name="artifact-verify",
        task_type="artifact",
        domain_tags=("synthetic", "artifact"),
        outcome="Write outputs/report.json with a status field and reopen it to confirm it parses",
        deliverables=("outputs/report.json",),
        checks=_artifact_checks("artifact-verify"),
        targeted_failure="artifact-verify.parses",
    ),
    SyntheticTask(
        name="artifact-schema",
        task_type="artifact",
        domain_tags=("synthetic", "artifact", "schema"),
        outcome="Write outputs/report.json matching the expected keys and verify the file reopens",
        deliverables=("outputs/report.json",),
        checks=_artifact_checks("artifact-schema"),
        targeted_failure="artifact-schema.parses",
    ),
    SyntheticTask(
        name="document-sections",
        task_type="document",
        domain_tags=("synthetic", "document"),
        outcome="Write outputs/summary.md with Scope, Evidence and Result sections",
        deliverables=("outputs/summary.md",),
        checks=(
            DeliverableCheck(name="document-sections.exists", path="outputs/summary.md", kind="exists"),
            DeliverableCheck(name="document-sections.sections", path="outputs/summary.md", kind="required_sections", sections=("Scope", "Evidence", "Result")),
        ),
        targeted_failure="document-sections.sections",
    ),
)

#: Held-out tasks with the same shape but different names and tags: an
#: improvement that only generalizes to the training names is not generalizing.
HELD_OUT_TASKS: tuple[SyntheticTask, ...] = (
    SyntheticTask(
        name="held-out-artifact",
        task_type="artifact",
        domain_tags=("synthetic", "artifact", "held-out"),
        outcome="Write outputs/report.json with a status field and confirm it parses after writing",
        deliverables=("outputs/report.json",),
        checks=_artifact_checks("held-out-artifact"),
        targeted_failure="held-out-artifact.parses",
    ),
    SyntheticTask(
        name="held-out-document",
        task_type="document",
        domain_tags=("synthetic", "document", "held-out"),
        outcome="Write outputs/summary.md with Scope, Evidence and Result sections",
        deliverables=("outputs/summary.md",),
        checks=(
            DeliverableCheck(name="held-out-document.exists", path="outputs/summary.md", kind="exists"),
            DeliverableCheck(name="held-out-document.sections", path="outputs/summary.md", kind="required_sections", sections=("Scope", "Evidence", "Result")),
        ),
        targeted_failure="held-out-document.sections",
    ),
)


def synthetic_suite() -> tuple[SyntheticTask, ...]:
    return SYNTHETIC_TASKS


def held_out_suite() -> tuple[SyntheticTask, ...]:
    return HELD_OUT_TASKS


def summarize_run_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Process/outcome metrics from durable run records.

    Every counter is exact; every *rate* is ``None`` when its denominator is 0,
    because "0 of 0" is not a percentage.
    """
    runs = list(records)
    stages: dict[str, int] = {}
    verdicts = {str(v): 0 for v in ContrastVerdict}
    scopes = {str(s): 0 for s in CandidateScope}
    candidates_proposed = 0
    candidates_validated = 0
    candidates_rejected = 0
    probes_run = 0
    probes_errored = 0
    experiences_written = 0
    lines = {"adopted": 0, "rejected": 0}
    tool_errors = 0
    tokens_in = 0
    tokens_out = 0
    costs: list[float] = []
    for record in runs:
        stage = str(record.get("stage", ""))
        stages[stage] = stages.get(stage, 0) + 1
        proposals = record.get("proposals") or []
        candidates_proposed += len(proposals)
        for validation in record.get("validations") or []:
            if validation.get("accepted"):
                candidates_validated += 1
            else:
                candidates_rejected += 1
        for outcome in record.get("probe_outcomes") or []:
            probes_run += 1
            if outcome.get("errored"):
                probes_errored += 1
            tool_errors += int(outcome.get("tool_error_count", 0) or 0)
            usage = outcome.get("token_usage") or {}
            if usage.get("input_tokens") is not None:
                tokens_in += int(usage["input_tokens"])
            if usage.get("output_tokens") is not None:
                tokens_out += int(usage["output_tokens"])
            if usage.get("cost_usd") is not None:
                costs.append(float(usage["cost_usd"]))
        for contrast in record.get("contrasts") or []:
            verdict = str(contrast.get("verdict", ""))
            if verdict in verdicts:
                verdicts[verdict] += 1
            scope = str(contrast.get("scope", ""))
            if scope in scopes:
                scopes[scope] += 1
        composition = record.get("composition") or {}
        lines["adopted"] += len(composition.get("adopted") or [])
        lines["rejected"] += len(composition.get("rejected") or [])
        experiences_written += len(record.get("experiences") or [])

    completed = stages.get(str(ImprovementStage.COMPLETED), 0)
    finished = sum(stages.get(stage, 0) for stage in TERMINAL_STAGE_STRINGS)
    return {
        "runs": {
            "total": len(runs),
            "by_stage": stages,
            "unfinished": len(runs) - finished,
            "completed_rate": None if not runs else round(completed / len(runs), 4),
        },
        "candidates": {
            "proposed": candidates_proposed,
            "validated": candidates_validated,
            "rejected_by_validator": candidates_rejected,
        },
        "probes": {"run": probes_run, "errored": probes_errored},
        "contrasts": {"by_verdict": verdicts, "by_scope": scopes},
        "composition": lines,
        "experiences_written": experiences_written,
        "measured_process": {
            "tool_errors": tool_errors,
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "cost_usd": round(sum(costs), 6) if costs else None,
            "cost_observations": len(costs),
        },
        "durability": "single-process JSON/JSONL; never cross-process exactly-once",
    }


def success_rate(probe_outcomes: Iterable[ProbeOutcome], *, validator_name: str | None = None) -> float | None:
    """Fraction of probes whose named validator passed; ``None`` when unmeasured.

    With ``validator_name=None`` the rate is over "no validator failed"; an
    outcome with **no validator results at all** is excluded from the
    denominator rather than counted as a failure or a pass.
    """
    considered = 0
    passed = 0
    for outcome in probe_outcomes:
        results = outcome.validator_results
        if validator_name is None:
            if not results:
                continue
            considered += 1
            if all(str(r.status) != "failed" for r in results):
                passed += 1
        else:
            matching = [r for r in results if r.name == validator_name]
            if not matching:
                continue
            considered += 1
            if all(str(r.status) == "passed" for r in matching):
                passed += 1
    if considered == 0:
        return None
    return round(passed / considered, 4)


def compare_baseline_to_improved(
    *,
    baseline: Sequence[ProbeOutcome],
    improved: Sequence[ProbeOutcome],
    validator_name: str | None = None,
) -> dict[str, Any]:
    """Baseline-versus-improved measurement on a fixed suite (guide §20).

    Returns measured rates plus the delta; any unmeasured arm produces ``None``
    rates and a ``None`` delta with the reason, never a zero.
    """
    base_rate = success_rate(baseline, validator_name=validator_name)
    improved_rate = success_rate(improved, validator_name=validator_name)
    if base_rate is None or improved_rate is None:
        return {
            "validator_name": validator_name,
            "baseline_rate": base_rate,
            "improved_rate": improved_rate,
            "delta": None,
            "reason": "at least one arm produced no measurable validator results",
            "baseline_n": len(list(baseline)),
            "improved_n": len(list(improved)),
        }
    return {
        "validator_name": validator_name,
        "baseline_rate": base_rate,
        "improved_rate": improved_rate,
        "delta": round(improved_rate - base_rate, 4),
        "reason": "",
        "baseline_n": len(list(baseline)),
        "improved_n": len(list(improved)),
    }
