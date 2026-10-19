"""Contrast reports: the narrow questions of guide §7 step 6, answered honestly.

A contrast never says "the candidate is better". It says which of the eight
narrow questions have evidence, which do not, and what remains unknown:

* ``targeted_failure_resolved`` is ``None`` when no targeted validator was
  named, or when the baseline was not failing one — "the failure did not
  recur" is only meaningful for a failure that was observed.
* **``CONFOUNDED`` outranks everything.** Differing environments, a probe that
  errored, or a task-fingerprint mismatch means the two runs are not
  comparable, and averaging them would manufacture a verdict.
* **Single-run uncertainty is stated, not hidden.** Deterministic validators
  first; the report lists where only one observation exists, because one run
  cannot separate an improvement from run-to-run noise.

The verdict vocabulary is exactly the guide's four: ``positive``, ``neutral``,
``negative``, ``confounded``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from alpha.harness_improvement.models import (
    CandidateScope,
    ContrastReport,
    ContrastVerdict,
    ProbeOutcome,
    ValidatorResult,
    ValidatorStatus,
)
from alpha.harness_improvement.probes import environment_confounders
from alpha.harness_improvement.validators import validator_summary


def _status_for(results: Iterable[ValidatorResult], name: str) -> ValidatorStatus | None:
    for result in results:
        if result.name == name:
            return result.status
    return None


def _regressions(baseline: ProbeOutcome, candidate: ProbeOutcome) -> tuple[str, ...]:
    """Checks that passed under the baseline but not under the candidate."""
    base_status = {r.name: r.status for r in baseline.validator_results}
    regressions: list[str] = []
    for result in candidate.validator_results:
        before = base_status.get(result.name)
        if before is ValidatorStatus.PASSED and result.status is not ValidatorStatus.PASSED:
            regressions.append(f"{result.name}: {before} -> {result.status}")
    for name, status in base_status.items():
        if status is ValidatorStatus.PASSED and name not in {r.name for r in candidate.validator_results}:
            regressions.append(f"{name}: passed under baseline but was not evaluated under the candidate")
    return tuple(regressions)


def _improvements(baseline: ProbeOutcome, candidate: ProbeOutcome) -> tuple[str, ...]:
    """Checks or process metrics that measurably improved."""
    base_status = {r.name: r.status for r in baseline.validator_results}
    improvements: list[str] = []
    for result in candidate.validator_results:
        before = base_status.get(result.name)
        if result.status is ValidatorStatus.PASSED and before is not None and before is not ValidatorStatus.PASSED:
            improvements.append(f"{result.name}: {before} -> passed")
    if baseline.errored and not candidate.errored:
        improvements.append("probe execution: errored -> completed")
    if candidate.tool_error_count < baseline.tool_error_count:
        improvements.append(f"tool errors: {baseline.tool_error_count} -> {candidate.tool_error_count}")
    if candidate.tool_calls < baseline.tool_calls:
        improvements.append(f"tool calls: {baseline.tool_calls} -> {candidate.tool_calls}")
    if candidate.model_turns < baseline.model_turns:
        improvements.append(f"model turns: {baseline.model_turns} -> {candidate.model_turns}")
    return tuple(improvements)


def _delta(before: int | float | None, after: int | float | None) -> int | float | None:
    if before is None or after is None:
        return None
    return after - before


def build_contrast(
    baseline: ProbeOutcome,
    candidate: ProbeOutcome,
    *,
    proposal_scope: CandidateScope = CandidateScope.INVARIANT,
    targeted_validator: str | None = None,
    expected_candidate_bundle_id: str | None = None,
    repeats: int = 1,
    extra_notes: Iterable[str] = (),
) -> ContrastReport:
    """Compare a candidate probe against the baseline probe for the same task.

    ``repeats`` is the number of observations per arm. It does not change the
    math (there is no significance test here); it changes the *uncertainty*
    text, which is the honest part of the report.
    """
    notes: list[str] = list(extra_notes)
    uncertainty: list[str] = []
    evidence_refs: list[str] = []
    if baseline.trace_artifact:
        evidence_refs.append(baseline.trace_artifact)
    if candidate.trace_artifact:
        evidence_refs.append(candidate.trace_artifact)
    evidence_refs.extend(f"probe:{baseline.run_id}")
    evidence_refs.extend(f"probe:{candidate.run_id}")

    confounders: list[str] = []
    if baseline.task_fingerprint != candidate.task_fingerprint:
        confounders.append(f"task fingerprint differs: {baseline.task_fingerprint} vs {candidate.task_fingerprint}")
    if expected_candidate_bundle_id is not None and candidate.bundle_id != expected_candidate_bundle_id:
        confounders.append(f"candidate probe ran bundle {candidate.bundle_id!r} but the expected candidate bundle was {expected_candidate_bundle_id!r}")
    if baseline.errored:
        confounders.append(f"baseline probe errored: {baseline.error or 'no error message'}")
    if candidate.errored:
        confounders.append(f"candidate probe errored: {candidate.error or 'no error message'}")
    env_confounders = environment_confounders(
        _environment_from(baseline.environment),
        _environment_from(candidate.environment),
    )
    if env_confounders:
        confounders.append(f"environment differs on: {', '.join(env_confounders)}")

    base_status = _status_for(baseline.validator_results, targeted_validator) if targeted_validator else None
    cand_status = _status_for(candidate.validator_results, targeted_validator) if targeted_validator else None
    targeted_resolved: bool | None
    if targeted_validator is None:
        targeted_resolved = None
        uncertainty.append("no targeted failure validator was named, so 'the failure did not recur' cannot be evaluated")
    elif base_status is None or cand_status is None:
        targeted_resolved = None
        uncertainty.append(f"targeted validator {targeted_validator!r} did not run on {'baseline' if base_status is None else 'candidate'}; absence of a result is not a pass")
    elif base_status is ValidatorStatus.PASSED:
        targeted_resolved = None
        uncertainty.append(f"targeted validator {targeted_validator!r} already passed under the baseline, so there was no observed failure to resolve")
    else:
        targeted_resolved = cand_status is ValidatorStatus.PASSED

    regressions = _regressions(baseline, candidate)
    improvements = _improvements(baseline, candidate)

    if confounders:
        verdict = ContrastVerdict.CONFOUNDED
    elif candidate.errored:
        verdict = ContrastVerdict.NEGATIVE
    elif regressions:
        verdict = ContrastVerdict.NEGATIVE
    elif targeted_resolved is False:
        # The hypothesis named a failure that happened again: whatever else
        # improved, the targeted claim is disconfirmed, so this cannot be a win.
        verdict = ContrastVerdict.NEUTRAL
    elif targeted_resolved is True:
        verdict = ContrastVerdict.POSITIVE
    elif improvements:
        verdict = ContrastVerdict.POSITIVE
    else:
        verdict = ContrastVerdict.NEUTRAL

    if repeats < 2:
        uncertainty.append("single observation per arm: an effect at this scale cannot be separated from run-to-run variance")
    if baseline.token_usage.cost_usd is None or candidate.token_usage.cost_usd is None:
        uncertainty.append("at least one arm did not report provider cost; cost deltas are unknown, not zero")
    uncertainty.extend(str(note) for note in notes if note)

    deltas: dict[str, Any] = {
        "tool_error_count": _delta(baseline.tool_error_count, candidate.tool_error_count),
        "tool_calls": _delta(baseline.tool_calls, candidate.tool_calls),
        "model_turns": _delta(baseline.model_turns, candidate.model_turns),
        "elapsed_ms": _delta(baseline.elapsed_ms, candidate.elapsed_ms),
        "input_tokens": _delta(baseline.token_usage.input_tokens, candidate.token_usage.input_tokens),
        "output_tokens": _delta(baseline.token_usage.output_tokens, candidate.token_usage.output_tokens),
        "cost_usd": _delta(baseline.token_usage.cost_usd, candidate.token_usage.cost_usd),
        "validators": {"baseline": validator_summary(baseline.validator_results), "candidate": validator_summary(candidate.validator_results)},
        "repeats_per_arm": repeats,
    }

    return ContrastReport(
        candidate_id=candidate.candidate_id,
        scope=proposal_scope,
        verdict=verdict,
        targeted_failure_resolved=targeted_resolved,
        improvements=improvements,
        regressions=regressions,
        deltas=deltas,
        uncertainty=tuple(uncertainty),
        evidence_refs=tuple(evidence_refs),
        notes=f"confounders: {'; '.join(confounders)}" if confounders else "",
    )


def _environment_from(raw: dict[str, Any]) -> Any:
    """Rebuild a minimal environment view from a serialized probe outcome.

    The probe outcome stores the environment as a plain dict; rehydrating it
    through :class:`ProbeEnvironment` keeps the confounder comparison using the
    one field list that owns it.
    """
    from alpha.harness_improvement.probes import ProbeEnvironment

    return ProbeEnvironment(
        role=str(raw.get("role", "")),
        model=raw.get("model"),
        alpha_runtime_version=str(raw.get("alpha_runtime_version", "unknown")),
        tool_schema_version=str(raw.get("tool_schema_version", "unknown")),
        policy_version=str(raw.get("policy_version", "unknown")),
        network_mode=str(raw.get("network_mode", "deny_by_default")),
        isolate_filesystem=bool(raw.get("isolate_filesystem", True)),
        isolate_memory_writes=bool(raw.get("isolate_memory_writes", True)),
        allow_external_side_effects=bool(raw.get("allow_external_side_effects", False)),
        python_version=str(raw.get("python_version", "unknown")),
        platform_name=str(raw.get("platform_name", "unknown")),
        seed=raw.get("seed"),
    )


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """One candidate's probe outcome and its contrast, tied to its proposal."""

    candidate_id: str
    scope: CandidateScope
    contrast: ContrastReport
    outcome: ProbeOutcome

    def to_dict(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id, "scope": str(self.scope), "contrast": self.contrast.to_dict(), "outcome": self.outcome.to_dict()}
