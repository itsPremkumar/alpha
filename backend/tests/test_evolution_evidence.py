"""Regression tests for the self-evolution evidence gate.

Each acceptance criterion for ``alpha/evolution/evidence`` is pinned here, in
the shape of the criterion it defends:

* an improvement beyond the noise floor with a clean judge is accepted;
* every attempt to weaken the judge (modified test, loosened threshold, added
  skip marker, changed baseline, deleted case, deleted test, loosened
  assertion, fingerprint mismatch) is rejected **with the exact path + reason**;
* a delta inside the noise floor is ``within_noise``, not an improvement;
* an improvement in one metric plus a regression in another is rejected;
* an ``unavailable`` required gate yields ``insufficient_evidence``;
* a timed-out / non-zero / unparsable command is never a passing ``0.0``;
* an irreversible/external/financial/data-destroying change yields
  ``needs_human`` even when every measurement improves;
* provenance replay reconstructs the accepted chain and detects a missing
  entry;
* every config key is read by a declared consumer;
* editing this package's own decision logic is detected.

Hermetic by construction: ``tmp_path`` for every write, an injected clock,
scripted measurement sources, argv-only commands that finish in milliseconds,
and no network, no gateway import, no real sleeping beyond the one
sub-200 ms timeout case.
"""

from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from alpha.evolution.evidence import (
    ChangedPath,
    CommandSource,
    EvolutionEvidenceConfig,
    GateResult,
    IntegrityPolicy,
    MeasurementRequest,
    ProbeOutcome,
    Proposal,
    ProvenanceLog,
    ScriptedSource,
    Verdict,
    check_evaluator_integrity,
    default_gate_registry,
    default_integrity_policy,
    evaluate_proposal,
    new_proposal,
    replay_chain,
    run_argv,
    run_required_gates,
)
from alpha.evolution.evidence.gates import GateContext
from alpha.evolution.evidence.models import EVIDENCE_LABELS, EVIDENCE_MEASURED, EVIDENCE_SIMULATED, Measurement

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "evolution" / "evidence"
SELF_DECISION_PATH = "backend/packages/harness/alpha/evolution/evidence/decision.py"
FIXED_NOW = 1_700_000_000.0
HARNESS_ID = "bench-v1"
SAMPLE_SIZE = 20


def _clock() -> float:
    return FIXED_NOW


def _config(**overrides: Any) -> EvolutionEvidenceConfig:
    values: dict[str, Any] = {"enabled": True, "min_sample_size": 5, "max_sample_size_gap": 2}
    values.update(overrides)
    return EvolutionEvidenceConfig(**values)


def _proposal(**overrides: Any) -> Proposal:
    values: dict[str, Any] = {
        "id": "prop-1",
        "kind": "skill",
        "author": "skill-evolution-engine",
        "declared_intent": "make the routing skill pick the right tool more often",
        "touched_paths": ("skills/routing/SKILL.md",),
        "patch_ref": "refs/prop-1.patch",
        "rollback_ref": "refs/prop-1.rollback",
    }
    values.update(overrides)
    return new_proposal(clock=_clock, **values)


def _requests(metrics: tuple[str, ...] = ("task_success_rate",), *, harness_id: str = HARNESS_ID, sample_size: int = SAMPLE_SIZE) -> tuple[MeasurementRequest, ...]:
    units = {"p95_seconds": "seconds"}
    return tuple(MeasurementRequest(metric=metric, side=side, harness_id=harness_id, unit=units.get(metric, "ratio"), sample_size=sample_size) for metric in metrics for side in ("candidate", "incumbent"))


def _sources_for(metric: str, candidate_value: float, incumbent_value: float, **kwargs: Any) -> tuple[ScriptedSource, ScriptedSource]:
    """Two scripted sources for one metric (the hermetic stand-in for a harness)."""

    evidence = kwargs.get("evidence", EVIDENCE_MEASURED)
    return (
        ScriptedSource({(metric, "candidate"): candidate_value}, evidence=evidence),
        ScriptedSource({(metric, "incumbent"): incumbent_value}, evidence=evidence),
    )


def _probe_ok(proposal: Proposal) -> ProbeOutcome:
    return ProbeOutcome(True, f"re-ran the declared pipeline for {proposal.id} twice and got identical output")


def _probe_fail(proposal: Proposal) -> ProbeOutcome:
    return ProbeOutcome(False, f"the declared output for {proposal.id} differed between two runs with identical inputs")


def _probe_unavailable(proposal: Proposal) -> ProbeOutcome:
    return ProbeOutcome(None, f"the reproducibility harness for {proposal.id} could not be reached")


class _ExplodingSource:
    def measure(self, request: MeasurementRequest) -> Measurement:
        raise AssertionError("the disabled gate touched a measurement source")


class _RetagSource:
    """A source that re-issues the request with a different harness/unit/n.

    Used to build the "different sample-size regime / different harness"
    scenarios a well-behaved host can also produce by running two harnesses.
    """

    def __init__(self, source: ScriptedSource, *, harness_id: str | None = None, unit: str | None = None, sample_size: int | None = None, side: str | None = None) -> None:
        self._source = source
        self._harness_id = harness_id
        self._unit = unit
        self._sample_size = sample_size
        self._side = side

    def measure(self, request: MeasurementRequest) -> Measurement:
        return self._source.measure(
            MeasurementRequest(
                metric=request.metric,
                side=self._side or request.side,
                harness_id=self._harness_id or request.harness_id,
                unit=self._unit or request.unit,
                sample_size=request.sample_size if self._sample_size is None else self._sample_size,
            )
        )


def _retag(source: ScriptedSource, **overrides: Any) -> _RetagSource:
    return _RetagSource(source, **overrides)


class _ExplodingGate:
    def __call__(self, context: GateContext) -> GateResult:
        raise AssertionError("the disabled gate ran a gate")


class _ExplodingProvenance:
    def append(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the disabled gate wrote provenance")


def _evaluate(**kwargs: Any) -> Any:
    """Call the gate with sensible defaults; override only what a test cares about."""

    default_candidate, default_incumbent = _sources_for("task_success_rate", 0.92, 0.80)
    values: dict[str, Any] = {
        "proposal": _proposal(),
        "config": _config(),
        "changed_paths": ("skills/routing/SKILL.md",),
        "candidate_source": default_candidate,
        "incumbent_source": default_incumbent,
        "measurement_requests": _requests(),
        "reproducibility_probe": _probe_ok,
    }
    values.update(kwargs)
    return evaluate_proposal(**values)


# --------------------------------------------------------------------------- #
# 1. the happy path
# --------------------------------------------------------------------------- #


def test_improvement_beyond_the_noise_floor_with_clean_integrity_is_accepted() -> None:
    result = _evaluate()

    assert result.verdict.status == "accepted", result.verdict.reasons
    assert result.verdict.accepted is True
    assert result.integrity is not None and result.integrity.status == "clean"
    assert [gate.status for gate in result.gates] == ["pass", "pass", "pass", "pass"]
    primary = result.comparisons.primary if result.comparisons else None
    assert primary is not None and primary.is_improvement and primary.exceeds_noise_floor
    assert primary.delta == pytest.approx(0.12)
    assert primary.noise_floor == 0.01


def test_accepted_verdict_serializes_for_the_provenance_record() -> None:
    result = _evaluate()
    payload = result.to_dict()
    assert payload["verdict"]["status"] == "accepted"
    assert payload["comparisons"]["comparisons"][0]["candidate"]["value"] == 0.92
    assert payload["measurements"]
    assert all("value" in measurement for measurement in payload["measurements"])


# --------------------------------------------------------------------------- #
# 2. the judge may not be tampered with
# --------------------------------------------------------------------------- #

TAMPER_CASES: tuple[tuple[str, ChangedPath, str], ...] = (
    (
        "modified_test_file",
        ChangedPath(path="backend/tests/test_evolution_evidence.py", change_type="modified", added_lines=("def test_gate():", "    assert 1 == 1")),
        "backend/tests/test_evolution_evidence.py",
    ),
    (
        "loosened_threshold_config",
        ChangedPath(path="backend/ruff.toml", change_type="modified", added_lines=("line-length = 400",)),
        "backend/ruff.toml",
    ),
    (
        "added_skip_marker",
        ChangedPath(path="backend/tests/test_gate_surface.py", change_type="modified", added_lines=("@pytest.mark.skip", "def test_gate():", "    assert 1 == 1")),
        "backend/tests/test_gate_surface.py",
    ),
    (
        "changed_baseline",
        ChangedPath(path="backend/packages/harness/alpha/memory/evaluation/baseline.json", change_type="modified", added_lines=('{"metrics": {"task_success_rate": 0.0}}',)),
        "backend/packages/harness/alpha/memory/evaluation/baseline.json",
    ),
    (
        "deleted_benchmark_case",
        ChangedPath(path="backend/packages/harness/alpha/memory/evaluation/cases/alpha-abstention-001.json", change_type="deleted"),
        "backend/packages/harness/alpha/memory/evaluation/cases/alpha-abstention-001.json",
    ),
)


@pytest.mark.parametrize(("case_id", "change", "path"), TAMPER_CASES, ids=[case[0] for case in TAMPER_CASES])
def test_weakening_the_judge_is_rejected_with_the_exact_path_and_reason(case_id: str, change: ChangedPath, path: str) -> None:
    result = _evaluate(changed_paths=("skills/routing/SKILL.md", change))

    assert result.verdict.status == "rejected", (case_id, result.verdict.reasons)
    assert result.integrity is not None and result.integrity.status in {"suspect", "compromised"}
    finding = result.integrity.finding_for(path)
    assert finding is not None, (case_id, result.integrity.findings)
    assert finding.reason
    joined = " | ".join(result.verdict.reasons)
    assert path in joined, joined
    assert finding.reason[:40] in joined, joined


def test_a_deleted_test_file_is_a_blocker_not_a_suspect() -> None:
    report = check_evaluator_integrity([ChangedPath(path="backend/tests/test_evolution_evidence.py", change_type="deleted")])
    assert report.status == "compromised"
    finding = report.finding_for("backend/tests/test_evolution_evidence.py")
    assert finding is not None and finding.code == "test_deleted" and finding.severity == "blocker"


def test_a_loosened_assertion_is_detected_from_the_real_diff_lines() -> None:
    change = ChangedPath(
        path="backend/tests/test_gate_surface.py",
        change_type="modified",
        removed_lines=(
            "    assert verdict.status == 'accepted'",
            "    assert finding.path == 'p'",
        ),
        added_lines=("    assert verdict.status is not None",),
    )
    report = check_evaluator_integrity([change])
    assert report.status == "compromised"
    finding = report.finding_for("backend/tests/test_gate_surface.py")
    assert finding is not None and finding.code == "assertion_loosened"
    assert "exact assertions dropped" in finding.reason


def test_a_tolerance_escape_is_detected() -> None:
    change = ChangedPath(path="backend/tests/test_gate_surface.py", change_type="modified", added_lines=("    assert metric == pytest.approx(0.9, abs=0.5)",))
    report = check_evaluator_integrity([change])
    finding = report.finding_for("backend/tests/test_gate_surface.py")
    assert finding is not None and finding.code == "tolerance_added"


def test_an_unexplained_evaluation_surface_change_is_suspect_not_allowed() -> None:
    report = check_evaluator_integrity([ChangedPath(path="backend/packages/harness/alpha/benchmarks/suites.py", change_type="modified", added_lines=("X = 1",))])
    assert report.status == "suspect"
    assert report.clean is False and report.blocking is True
    finding = report.findings[0]
    assert finding.code == "evaluation_surface_modified" and finding.severity == "suspect"


def test_a_fingerprint_mismatch_against_the_injected_baseline_is_a_blocker() -> None:
    policy = default_integrity_policy()
    report = check_evaluator_integrity(
        [],
        policy=policy,
        baseline_fingerprints={"backend/packages/harness/alpha/memory/evaluation/metrics.py": "sha256:aaa"},
        current_fingerprints={"backend/packages/harness/alpha/memory/evaluation/metrics.py": "sha256:bbb"},
    )
    assert report.status == "compromised"
    assert {finding.code for finding in report.findings} == {"fingerprint_mismatch"}
    assert "backend/packages/harness/alpha/memory/evaluation/metrics.py" in report.reason


def test_editing_this_packages_own_decision_logic_is_detected() -> None:
    policy = default_integrity_policy()
    assert policy.classify(SELF_DECISION_PATH) == "gate_self"

    classified = check_evaluator_integrity([ChangedPath(path=SELF_DECISION_PATH, change_type="modified", added_lines=("VERDICT_STATUSES = ('accepted',)",))], policy=policy)
    assert classified.status == "compromised"
    finding = classified.finding_for(SELF_DECISION_PATH)
    assert finding is not None and finding.code == "gate_self_modified"
    assert "decision table" in finding.reason
    assert any(SELF_DECISION_PATH in reason for reason in _evaluate(changed_paths=(SELF_DECISION_PATH,)).verdict.reasons)

    real_bytes = (PACKAGE_DIR / "decision.py").read_bytes()
    untouched = hashlib.sha256(real_bytes).hexdigest()
    edited = hashlib.sha256(real_bytes + b"\n# a one-line edit that would accept anything\n").hexdigest()
    assert untouched != edited
    byte_level = check_evaluator_integrity(
        [],
        policy=policy,
        baseline_fingerprints={SELF_DECISION_PATH: edited},
        current_fingerprints={SELF_DECISION_PATH: untouched},
    )
    assert byte_level.status == "compromised"
    assert {finding.code for finding in byte_level.findings} == {"fingerprint_mismatch"}


def test_every_module_of_this_package_is_an_evaluation_surface() -> None:
    policy = default_integrity_policy()
    for module in sorted(PACKAGE_DIR.glob("*.py")):
        relative = f"backend/packages/harness/alpha/evolution/evidence/{module.name}"
        assert policy.classify(relative) == "gate_self", relative


def test_an_absolute_path_cannot_dodge_the_evaluation_surface() -> None:
    policy = default_integrity_policy()
    absolute = "C:/Users/dev/alpha-evogate/backend/tests/test_release_gate.py"
    assert policy.classify(absolute) == "tests"
    assert check_evaluator_integrity([absolute]).status == "suspect"


def test_a_relative_path_is_matched_as_written_not_by_its_tail() -> None:
    policy = default_integrity_policy()
    assert policy.classify("skills/routing/tests/helpers.py") is None
    assert policy.classify("backend/tests/test_release_gate.py") == "tests"


# --------------------------------------------------------------------------- #
# 3. the noise floor
# --------------------------------------------------------------------------- #


def test_a_delta_inside_the_noise_floor_is_within_noise_and_not_accepted() -> None:
    result = _evaluate(
        config=_config(noise_floors={"task_success_rate": 0.01}),
        candidate_source=ScriptedSource({("task_success_rate", "candidate"): 0.805}, evidence=EVIDENCE_MEASURED),
        incumbent_source=ScriptedSource({("task_success_rate", "incumbent"): 0.800}, evidence=EVIDENCE_MEASURED),
    )

    primary = result.comparisons.primary if result.comparisons else None
    assert primary is not None
    assert primary.status == "within_noise"
    assert primary.exceeds_noise_floor is False
    assert primary.is_improvement is False
    assert result.verdict.status == "rejected"
    assert any("within the declared noise floor" in reason for reason in result.verdict.reasons)


def test_a_delta_exactly_equal_to_the_floor_is_still_within_noise() -> None:
    result = _evaluate(
        config=_config(noise_floors={"task_success_rate": 0.01}),
        candidate_source=ScriptedSource({("task_success_rate", "candidate"): 0.810}, evidence=EVIDENCE_MEASURED),
        incumbent_source=ScriptedSource({("task_success_rate", "incumbent"): 0.800}, evidence=EVIDENCE_MEASURED),
    )
    primary = result.comparisons.primary if result.comparisons else None
    assert primary is not None and primary.status == "within_noise" and result.verdict.status == "rejected"


def test_a_metric_without_a_declared_noise_floor_is_not_an_improvement() -> None:
    result = _evaluate(
        config=_config(noise_floors={"some_other_metric": 0.01}),
        candidate_source=ScriptedSource({("task_success_rate", "candidate"): 0.99}, evidence=EVIDENCE_MEASURED),
        incumbent_source=ScriptedSource({("task_success_rate", "incumbent"): 0.10}, evidence=EVIDENCE_MEASURED),
    )
    primary = result.comparisons.primary if result.comparisons else None
    assert primary is not None and primary.status == "incomparable"
    assert result.verdict.status == "insufficient_evidence"


def test_improving_one_metric_while_regressing_another_is_rejected() -> None:
    candidate = ScriptedSource({("task_success_rate", "candidate"): 0.92, ("p95_seconds", "candidate"): 3.1}, evidence=EVIDENCE_MEASURED)
    incumbent = ScriptedSource({("task_success_rate", "incumbent"): 0.80, ("p95_seconds", "incumbent"): 2.4}, evidence=EVIDENCE_MEASURED)
    result = _evaluate(config=_config(), candidate_source=candidate, incumbent_source=incumbent, measurement_requests=_requests(("task_success_rate", "p95_seconds")))

    assert result.comparisons is not None
    primary = result.comparisons.primary
    assert primary is not None and primary.is_improvement
    regressions = result.comparisons.regressions
    assert [item.metric for item in regressions] == ["p95_seconds"]
    assert regressions[0].direction == "lower_is_better"
    assert result.verdict.status == "rejected"
    assert any("p95_seconds" in reason and "regressed" in reason for reason in result.verdict.reasons)


def test_a_lower_is_better_metric_improves_when_it_drops() -> None:
    candidate = ScriptedSource({("task_success_rate", "candidate"): 0.92, ("p95_seconds", "candidate"): 1.2}, evidence=EVIDENCE_MEASURED)
    incumbent = ScriptedSource({("task_success_rate", "incumbent"): 0.80, ("p95_seconds", "incumbent"): 2.4}, evidence=EVIDENCE_MEASURED)
    result = _evaluate(candidate_source=candidate, incumbent_source=incumbent, measurement_requests=_requests(("task_success_rate", "p95_seconds")))
    assert result.verdict.status == "accepted", result.verdict.reasons


# --------------------------------------------------------------------------- #
# 4. an unmeasured thing is never a pass
# --------------------------------------------------------------------------- #


def test_a_missing_measurement_source_is_insufficient_evidence_not_zero() -> None:
    result = _evaluate(candidate_source=None, incumbent_source=ScriptedSource({("task_success_rate", "incumbent"): 0.80}, evidence=EVIDENCE_MEASURED))
    candidate = [item for item in result.measurements if item.side == "candidate"][0]
    assert candidate.value is None
    assert candidate.status == "unavailable"
    assert candidate.evidence == "unverified"
    assert result.verdict.status == "insufficient_evidence"


def test_simulated_evidence_can_never_be_accepted() -> None:
    result = _evaluate(
        candidate_source=ScriptedSource({("task_success_rate", "candidate"): 0.99}, evidence=EVIDENCE_SIMULATED),
        incumbent_source=ScriptedSource({("task_success_rate", "incumbent"): 0.10}, evidence=EVIDENCE_SIMULATED),
    )
    assert result.verdict.status == "insufficient_evidence"
    assert any("simulated" in reason for reason in result.verdict.reasons)
    assert EVIDENCE_LABELS == ("measured", "simulated", "heuristic", "unverified")


def test_too_few_samples_is_insufficient_evidence() -> None:
    result = _evaluate(config=_config(min_sample_size=50), measurement_requests=_requests(sample_size=20))
    primary = result.comparisons.primary if result.comparisons else None
    assert primary is not None and primary.status == "insufficient_evidence"
    assert "sample" in primary.reason
    assert result.verdict.status == "insufficient_evidence"


def test_different_sample_size_regimes_are_incomparable() -> None:
    candidate, incumbent = _sources_for("task_success_rate", 0.92, 0.80)
    result = _evaluate(
        config=_config(min_sample_size=1, max_sample_size_gap=2),
        candidate_source=_retag(candidate, sample_size=20),
        incumbent_source=_retag(incumbent, sample_size=3),
        measurement_requests=_requests(sample_size=20),
    )
    primary = result.comparisons.primary if result.comparisons else None
    assert primary is not None and primary.status == "incomparable"
    assert "regimes" in primary.reason
    assert result.verdict.status == "insufficient_evidence"


def test_a_mismatched_harness_id_is_incomparable() -> None:
    candidate, incumbent = _sources_for("task_success_rate", 0.92, 0.80)
    result = _evaluate(
        candidate_source=_retag(candidate, harness_id="bench-v2"),
        incumbent_source=_retag(incumbent, harness_id="bench-v1"),
    )
    primary = result.comparisons.primary if result.comparisons else None
    assert primary is not None and primary.status == "incomparable"
    assert "different harnesses" in primary.reason
    assert result.verdict.status == "insufficient_evidence"


def test_incomparable_units_are_refused() -> None:
    candidate, incumbent = _sources_for("task_success_rate", 0.92, 0.80)
    result = _evaluate(
        candidate_source=_retag(candidate, unit="percent"),
        incumbent_source=_retag(incumbent, unit="ratio"),
        measurement_requests=_requests(),
    )
    primary = result.comparisons.primary if result.comparisons else None
    assert primary is not None and primary.status == "incomparable"
    assert "units are incomparable" in primary.reason


def test_a_measurement_with_a_value_but_no_status_is_impossible() -> None:
    with pytest.raises(ValueError):
        Measurement(metric="m", side="candidate", value=0.0, unit="ratio", sample_size=5, harness_id="h", evidence=EVIDENCE_MEASURED, status="unavailable")
    with pytest.raises(ValueError):
        Measurement(metric="m", side="candidate", value=None, unit="ratio", sample_size=5, harness_id="h", evidence=EVIDENCE_MEASURED, status="measured")


def test_a_metric_measured_twice_on_one_side_is_refused_not_averaged() -> None:
    from alpha.evolution.evidence.compare import compare_measurements, index_measurements

    doubled = [
        Measurement(metric="task_success_rate", side="candidate", value=0.9, sample_size=20, harness_id=HARNESS_ID),
        Measurement(metric="task_success_rate", side="candidate", value=0.5, sample_size=20, harness_id=HARNESS_ID),
    ]
    with pytest.raises(ValueError, match="more than once"):
        index_measurements(doubled, side="candidate")
    incumbent = [Measurement(metric="task_success_rate", side="incumbent", value=0.8, sample_size=20, harness_id=HARNESS_ID)]
    with pytest.raises(ValueError, match="more than once"):
        compare_measurements(doubled, incumbent, config=_config())


def test_a_malformed_request_set_yields_insufficient_evidence_not_a_crash() -> None:
    doubled = MeasurementRequest(metric="task_success_rate", side="candidate", harness_id=HARNESS_ID, sample_size=SAMPLE_SIZE)
    result = _evaluate(measurement_requests=(doubled, doubled, *_requests()[1:]))
    assert result.verdict.status == "insufficient_evidence"
    assert any("more than once" in note for note in result.notes)


# --------------------------------------------------------------------------- #
# 5. required gates
# --------------------------------------------------------------------------- #


def test_an_unavailable_required_gate_yields_insufficient_evidence() -> None:
    result = _evaluate(reproducibility_probe=_probe_unavailable)
    gate = next(item for item in result.gates if item.name == "reproducibility")
    assert gate.status == "unavailable"
    assert result.verdict.status == "insufficient_evidence"
    assert any("reproducibility" in reason and "unavailable" in reason for reason in result.verdict.reasons)


def test_a_missing_reproducibility_probe_is_unavailable_not_a_pass() -> None:
    result = _evaluate(reproducibility_probe=None)
    assert [item.status for item in result.gates if item.name == "reproducibility"] == ["unavailable"]
    assert result.verdict.status == "insufficient_evidence"


def test_a_failed_reproducibility_probe_rejects() -> None:
    result = _evaluate(reproducibility_probe=_probe_fail)
    assert result.verdict.status == "rejected"
    assert any("reproducibility" in reason for reason in result.verdict.reasons)


def test_a_gate_without_a_rollback_reference_fails_and_blocks() -> None:
    result = _evaluate(proposal=_proposal(rollback_ref=None))
    assert [item.status for item in result.gates if item.name == "rollback_path"] == ["fail"]
    assert result.verdict.status == "rejected"


def test_a_gate_that_raises_becomes_an_error_and_blocks() -> None:
    def exploding_gate(context: GateContext) -> GateResult:
        raise RuntimeError("the gate subprocess died")

    result = _evaluate(gates={"blast_radius": exploding_gate})
    gate = next(item for item in result.gates if item.name == "blast_radius")
    assert gate.status == "error"
    assert "RuntimeError: the gate subprocess died" in gate.detail
    assert result.verdict.status == "insufficient_evidence"


def test_a_required_gate_with_no_implementation_is_an_error() -> None:
    result = _evaluate(config=_config(required_gates=("evaluator_integrity", "reproducibility", "rollback_path", "blast_radius", "no_such_gate")))
    gate = next(item for item in result.gates if item.name == "no_such_gate")
    assert gate.status == "error"
    assert result.verdict.status == "insufficient_evidence"


def test_a_silent_gate_is_an_error_rather_than_a_pass() -> None:
    from alpha.evolution.evidence.gates import GateResult as _GateResult

    result = _evaluate(gates={"blast_radius": lambda context: _GateResult(name="blast_radius", status="pass", detail="   ")})
    gate = next(item for item in result.gates if item.name == "blast_radius")
    assert gate.status == "error"
    assert result.verdict.status == "insufficient_evidence"


def test_every_required_gate_runs_even_after_one_fails() -> None:
    context = GateContext(proposal=_proposal(), config=_config(), integrity=check_evaluator_integrity(["skills/routing/SKILL.md"]))
    results = run_required_gates(context, default_gate_registry(), required=("evaluator_integrity", "reproducibility", "rollback_path", "blast_radius"))
    assert [item.name for item in results] == ["evaluator_integrity", "reproducibility", "rollback_path", "blast_radius"]
    assert [item.status for item in results] == ["pass", "unavailable", "pass", "pass"]


def test_an_over_wide_blast_radius_is_rejected() -> None:
    result = _evaluate(config=_config(max_touched_paths=1), proposal=_proposal(touched_paths=("skills/a/SKILL.md", "skills/b/SKILL.md", "skills/c/SKILL.md")))
    assert [item.status for item in result.gates if item.name == "blast_radius"] == ["fail"]
    assert result.verdict.status == "rejected"


def test_a_declared_blast_radius_is_honoured_over_the_touched_path_count() -> None:
    proposal = _proposal(touched_paths=("skills/a/SKILL.md",), blast_radius=99)
    assert proposal.effective_blast_radius == 99
    result = _evaluate(config=_config(max_touched_paths=10), proposal=proposal)
    assert result.verdict.status == "rejected"


# --------------------------------------------------------------------------- #
# 6. command measurements are argv-only and never fake a zero
# --------------------------------------------------------------------------- #


def test_run_argv_refuses_a_command_string() -> None:
    with pytest.raises(ValueError, match="argv list"):
        run_argv("python -c 'print(1)'")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="argv list"):
        CommandSource(argv="python -c print(1)")  # type: ignore[arg-type]


def test_a_command_measurement_is_measured_only_on_exit_zero() -> None:
    source = CommandSource(argv=(sys.executable, "-c", "print(0.87)"), timeout_seconds=20.0)
    measurement = source.measure(_requests()[0])
    assert measurement.status == "measured"
    assert measurement.evidence == EVIDENCE_MEASURED
    assert measurement.value == pytest.approx(0.87)


@pytest.mark.parametrize(
    ("argv", "timeout", "expected_status", "label"),
    [
        ((sys.executable, "-c", "import time; time.sleep(5)"), 0.25, "unavailable", "timeout"),
        ((sys.executable, "-c", "raise SystemExit(3)"), 20.0, "error", "non-zero exit"),
        ((sys.executable, "-c", "print('not a number')"), 20.0, "unavailable", "unparsable output"),
        (("this-executable-does-not-exist-alpha-evogate",), 20.0, "error", "spawn failure"),
    ],
    ids=["timeout", "non_zero_exit", "unparsable", "spawn_failure"],
)
def test_a_failed_command_never_becomes_a_passing_zero(argv: tuple[str, ...], timeout: float, expected_status: str, label: str) -> None:
    source = CommandSource(argv=argv, timeout_seconds=timeout, max_output_chars=200)
    measurement = source.measure(_requests()[0])
    assert measurement.value is None, label
    assert measurement.status == expected_status, (label, measurement.detail)
    assert measurement.evidence == "unverified"
    assert measurement.detail, label
    assert "0.0" not in str(measurement.value)


def test_a_non_zero_command_says_so_explicitly() -> None:
    source = CommandSource(argv=(sys.executable, "-c", "raise SystemExit(3)"), timeout_seconds=20.0)
    measurement = source.measure(_requests()[0])
    assert "a non-zero exit is not a measurement of 0.0" in measurement.detail
    assert "exited 3" in measurement.detail


def test_a_non_zero_command_blocks_acceptance() -> None:
    result = _evaluate(
        candidate_source=CommandSource(argv=(sys.executable, "-c", "raise SystemExit(1)"), timeout_seconds=20.0),
        incumbent_source=ScriptedSource({("task_success_rate", "incumbent"): 0.80}, evidence=EVIDENCE_MEASURED),
    )
    assert result.verdict.status == "insufficient_evidence"


def test_command_output_is_capped() -> None:
    source = CommandSource(argv=(sys.executable, "-c", "print('x' * 5000)"), timeout_seconds=20.0, max_output_chars=100)
    source.measure(_requests()[0])
    assert source.runs[0].truncated is True
    assert len(source.runs[0].stdout) <= 200


def test_no_shell_interpolation_network_or_sleeping_exists_in_this_package() -> None:
    """AST scan: no shell execution, no network client, no sleeping, no eval.

    Text matching would trip over the docstrings that say "there is no
    ``shell=True`` here", so the check walks the parsed AST instead.
    """

    banned_modules = {"requests", "urllib", "socket", "httpx", "telnetlib", "ftplib", "asyncio"}
    banned_calls = {"eval", "exec", "compile", "__import__"}
    for module in sorted(PACKAGE_DIR.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned_modules, f"{module.name} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in banned_modules, f"{module.name} imports from {node.module}"
                if root == "time":
                    assert {alias.name for alias in node.names}.isdisjoint({"sleep"}, f"{module.name} imports time.sleep")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in banned_calls, f"{module.name} calls {node.func.id}()"
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    assert not (node.func.value.id == "os" and node.func.attr in {"system", "popen", "spawn"}), f"{module.name} calls os.{node.func.attr}()"
                for keyword in node.keywords:
                    assert not (keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True), f"{module.name} passes shell=True"
            elif isinstance(node, ast.Attribute):
                assert not (isinstance(node.value, ast.Name) and node.value.id == "time" and node.attr == "sleep"), f"{module.name} calls time.sleep()"


# --------------------------------------------------------------------------- #
# 7. the gate may refuse to decide
# --------------------------------------------------------------------------- #

HUMAN_CASES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("irreversible_flag", {"reversible": False, "rollback_ref": None}),
    ("external_publish", {"kind": "release", "touched_paths": ("scripts/release_publish.sh",)}),
    ("financial", {"kind": "billing_tuning", "touched_paths": ("backend/app/gateway/billing.py",)}),
    ("data_destroying", {"kind": "schema_change", "touched_paths": ("backend/app/persistence/migrate_0019_0020.py",)}),
)


@pytest.mark.parametrize(("label", "overrides"), HUMAN_CASES, ids=[case[0] for case in HUMAN_CASES])
def test_classified_changes_need_a_human_even_when_everything_improves(label: str, overrides: dict[str, Any]) -> None:
    proposal = _proposal(**overrides)
    result = _evaluate(proposal=proposal, changed_paths=proposal.touched_paths)
    assert result.verdict.status == "needs_human", (label, result.verdict.reasons)
    assert result.verdict.human_categories
    assert any("refuses to decide" in reason for reason in result.verdict.reasons)


def test_needs_human_is_not_a_soft_reject() -> None:
    result = _evaluate(proposal=_proposal(kind="release", touched_paths=("scripts/release_publish.sh",)), changed_paths=("scripts/release_publish.sh",))
    assert result.verdict.status == "needs_human"
    assert result.verdict.accepted is False and result.verdict.blocking is True
    assert "external" in result.verdict.human_categories


def test_a_verdict_without_reasons_cannot_be_constructed() -> None:
    with pytest.raises(ValueError, match="reason"):
        Verdict(proposal_id="p", status="rejected", reasons=())


def test_a_needs_human_verdict_must_name_its_category() -> None:
    with pytest.raises(ValueError, match="category"):
        Verdict(proposal_id="p", status="needs_human", reasons=("because",))


# --------------------------------------------------------------------------- #
# 8. provenance
# --------------------------------------------------------------------------- #


def _accepted_chain(tmp_path: Path) -> EvolutionEvidenceConfig:
    config = _config(provenance_root=str(tmp_path))
    result = _evaluate(config=config)
    assert result.verdict.status == "accepted"
    return config


def test_provenance_replay_reconstructs_the_accepted_chain(tmp_path: Path) -> None:
    config = _accepted_chain(tmp_path)
    report = replay_chain(config.chain_dir())

    assert report.intact is True
    assert [entry.kind for entry in report.entries] == ["proposal", "measurement", "measurement", "comparison", "verdict"]
    assert [entry.seq for entry in report.entries] == [1, 2, 3, 4, 5]
    assert report.default_proposal_id == "prop-1"
    assert report.current_default is not None and report.current_default.accepted

    explained = report.explain()
    assert explained["is_current_default"] is True
    assert explained["verdict"]["status"] == "accepted"
    assert [gate["name"] for gate in explained["verdict"]["gates"]] == ["evaluator_integrity", "reproducibility", "rollback_path", "blast_radius"]
    assert explained["verdict"]["integrity"]["status"] == "clean"


def test_provenance_detects_a_missing_entry(tmp_path: Path) -> None:
    config = _accepted_chain(tmp_path)
    ledger = config.chain_dir() / "evidence.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5

    ledger.write_text("\n".join([lines[0], *lines[2:]]) + "\n", encoding="utf-8")
    report = replay_chain(config.chain_dir())
    assert report.intact is False
    codes = {issue.code for issue in report.issues}
    assert "sequence_gap" in codes
    assert "broken_link" in codes
    assert any("missing or the chain was reordered" in issue.detail for issue in report.issues)


def test_provenance_detects_reordering_and_a_truncated_tail(tmp_path: Path) -> None:
    config = _accepted_chain(tmp_path)
    ledger = config.chain_dir() / "evidence.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()

    ledger.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    reordered = replay_chain(config.chain_dir())
    assert reordered.intact is False
    assert "sequence_gap" in {issue.code for issue in reordered.issues}

    ledger.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    truncated = replay_chain(config.chain_dir())
    assert "truncated_tail" in {issue.code for issue in truncated.issues}


def test_provenance_detects_a_rewritten_entry(tmp_path: Path) -> None:
    config = _accepted_chain(tmp_path)
    ledger = config.chain_dir() / "evidence.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    lines[3] = lines[3].replace('"measured"', '"simulated"')
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = replay_chain(config.chain_dir())
    assert "entry_hash_mismatch" in {issue.code for issue in report.issues}


def test_provenance_log_reports_degraded_persistence_without_raising(tmp_path: Path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    log = ProvenanceLog(blocker, chain_id="chain", clock=_clock)
    entry = log.append("proposal", "p-1", {"id": "p-1"})
    assert entry.seq == 1
    assert log.persistence == "degraded"
    assert "chain" in str(log.path)


def test_a_disabled_gate_writes_no_provenance(tmp_path: Path) -> None:
    log = ProvenanceLog(tmp_path, chain_id="chain", clock=_clock)
    result = _evaluate(config=EvolutionEvidenceConfig(), provenance=log)
    assert result.provenance_status == "not_written"
    assert replay_chain(tmp_path / "chain").entries == ()


# --------------------------------------------------------------------------- #
# 9. configuration
# --------------------------------------------------------------------------- #

CONFIG_READERS: dict[str, str] = {
    "enabled": "service.py",
    "primary_metric": "compare.py",
    "noise_floors": "compare.py",
    "metric_directions": "compare.py",
    "required_gates": "gates.py",
    "min_sample_size": "compare.py",
    "max_sample_size_gap": "compare.py",
    "max_touched_paths": "decision.py",
    "integrity_path_globs": "integrity.py",
    "human_classification_patterns": "decision.py",
    "provenance_root": "service.py",
    "provenance_chain_id": "service.py",
    "command_timeout_seconds": "gates.py",
    "max_output_chars": "gates.py",
}


def test_every_config_key_is_read_by_a_declared_consuming_module() -> None:
    assert set(CONFIG_READERS) == set(EvolutionEvidenceConfig.field_names())
    for key, module_name in CONFIG_READERS.items():
        text = (PACKAGE_DIR / module_name).read_text(encoding="utf-8")
        assert re.search(rf"\b{re.escape(key)}\b", text), f"{key} is declared but never read by {module_name}"


def test_every_config_key_round_trips_through_its_readers() -> None:
    values: dict[str, Any] = {
        "enabled": True,
        "primary_metric": "p95_seconds",
        "noise_floors": {"p95_seconds": 0.25},
        "metric_directions": {"p95_seconds": "lower_is_better"},
        "required_gates": ["evaluator_integrity", "reproducibility", "rollback_path", "blast_radius"],
        "min_sample_size": 7,
        "max_sample_size_gap": 3,
        "max_touched_paths": 12,
        "integrity_path_globs": {"tests": ["backend/tests/**/*"]},
        "human_classification_patterns": {"external": ["*publish*"]},
        "provenance_root": "tmp-evidence",
        "provenance_chain_id": "unit-chain",
        "command_timeout_seconds": 120.0,
        "max_output_chars": 900,
    }
    config = EvolutionEvidenceConfig.from_mapping(values)

    serialized = config.to_dict()
    assert serialized == values
    for key, value in values.items():
        assert serialized[key] == value
        assert config.read_key(key) == getattr(config, key)
        assert config[key] == getattr(config, key)
        assert key in config
    assert config.keys() == EvolutionEvidenceConfig.field_names()
    assert config.direction_for("p95_seconds") == "lower_is_better"
    assert config.noise_floor_for("p95_seconds") == 0.25
    assert config.noise_floor_for("unheard_of") is None
    assert config.chain_dir().as_posix().endswith("tmp-evidence/unit-chain")
    policy = config.integrity_policy()
    assert policy.classify("backend/tests/test_x.py") == "tests"
    assert policy.classify("skills/routing/SKILL.md") is None

    with pytest.raises(ValueError, match="unknown evolution evidence config key"):
        EvolutionEvidenceConfig.from_mapping({"enabeld": True})
    with pytest.raises(ValueError, match="unknown evolution evidence config key"):
        EvolutionEvidenceConfig.from_mapping({"noise_floor": 0.1})


def test_the_gate_is_off_by_default() -> None:
    assert EvolutionEvidenceConfig().enabled is False
    assert EvolutionEvidenceConfig().required_gates == ("evaluator_integrity", "reproducibility", "rollback_path", "blast_radius")


def test_a_disabled_gate_is_a_total_no_op() -> None:
    result = evaluate_proposal(
        _proposal(),
        config=EvolutionEvidenceConfig(),
        changed_paths=("backend/tests/test_x.py",),
        candidate_source=_ExplodingSource(),
        incumbent_source=_ExplodingSource(),
        measurement_requests=_requests(),
        gates={"evaluator_integrity": _ExplodingGate()},
        reproducibility_probe=_ExplodingGate(),
        provenance=_ExplodingProvenance(),
    )

    assert result.config_enabled is False
    assert result.verdict.status == "insufficient_evidence"
    assert "disabled" in result.verdict.reasons[0]
    assert result.integrity is None
    assert result.measurements == ()
    assert result.comparisons is None
    assert result.gates == ()
    assert result.provenance_status == "not_written"


def test_invalid_config_values_are_refused() -> None:
    with pytest.raises(ValueError, match="noise floor"):
        EvolutionEvidenceConfig(noise_floors={"m": -1.0})
    with pytest.raises(ValueError, match="direction"):
        EvolutionEvidenceConfig(metric_directions={"m": "sideways"})
    with pytest.raises(ValueError, match="required_gates"):
        EvolutionEvidenceConfig(required_gates=())
    with pytest.raises(ValueError, match="provenance_chain_id"):
        EvolutionEvidenceConfig(provenance_chain_id="../escape")
    with pytest.raises(ValueError, match="command_timeout_seconds"):
        EvolutionEvidenceConfig(command_timeout_seconds=0.0)
    with pytest.raises(ValueError, match="min_sample_size"):
        EvolutionEvidenceConfig(min_sample_size=0)


# --------------------------------------------------------------------------- #
# 10. package surface and injection hygiene
# --------------------------------------------------------------------------- #


def test_the_package_exports_lazily() -> None:
    import alpha.evolution.evidence as evidence

    assert evidence.Proposal is Proposal
    assert "install_lazy_exports" in (PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8")
    module = __import__("alpha.evolution.evidence", fromlist=["__getattr__"])
    assert getattr(module.__getattr__, "_lazy_exports", False) is True
    assert "evaluate_proposal" in evidence.__all__
    assert "decide_evidence_verdict" in evidence.__all__


def test_the_integrity_policy_is_injectable_and_the_default_protects_the_judge() -> None:
    # The policy is operator-injectable (tests and operators may narrow or extend
    # the surface); the DEFAULT policy is what makes the gate self-protecting.
    narrowed = IntegrityPolicy.from_mapping({"tests": ["docs/**/*"]})
    assert narrowed.classify("backend/tests/test_x.py") is None
    assert narrowed.classify("docs/tests/test_x.py") == "tests"
    assert default_integrity_policy().classify("backend/tests/test_x.py") == "tests"
    with pytest.raises(ValueError, match="unknown integrity categories"):
        IntegrityPolicy(path_globs={"not_a_category": ["x"]})


def test_a_marker_extension_cannot_remove_the_defaults() -> None:
    policy = IntegrityPolicy(extra_skip_markers=("@weird",))
    assert "pytest.mark.skip" in policy.skip_markers
    assert "@weird" in policy.skip_markers


def test_changed_paths_accept_plain_strings() -> None:
    report = check_evaluator_integrity(["backend/tests/test_x.py"])
    assert report.status == "suspect"
    assert report.checked_paths == ("backend/tests/test_x.py",)


def test_a_malformed_integrity_input_is_an_error_status_not_a_pass() -> None:
    report = check_evaluator_integrity([42])  # type: ignore[list-item]
    assert report.status == "error"
    assert report.reason
    fingerprint_report = check_evaluator_integrity([], baseline_fingerprints=["not", "a", "mapping"])  # type: ignore[arg-type]
    assert fingerprint_report.status == "error"
    assert fingerprint_report.reason


def test_verdict_statuses_are_a_closed_set() -> None:
    from alpha.evolution.evidence.models import VERDICT_STATUSES

    assert VERDICT_STATUSES == ("accepted", "rejected", "needs_human", "insufficient_evidence")
    with pytest.raises(ValueError):
        Verdict(status="approved", proposal_id="p", reasons=("because",))  # type: ignore[arg-type]


def test_sources_for_helper_is_used_consistently() -> None:
    candidate, incumbent = _sources_for("task_success_rate", 0.92, 0.80)
    result = _evaluate(candidate_source=candidate, incumbent_source=incumbent)
    assert result.verdict.status == "accepted", result.verdict.reasons
