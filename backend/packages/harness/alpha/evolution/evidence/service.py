"""The gate's single entry point: proposal in, verdict out.

:func:`evaluate_proposal` is what the central owner wires into
``alpha/evolution`` promotion, ``alpha/rsi`` promotion and the guarded updater.
It is deliberately boring: it *assembles* the already-pure pieces (integrity ->
measurement -> comparison -> gates -> decision) and records the result. Every
piece it calls is injectable, so the whole path is testable without a repo, a
benchmark, a network or a clock of its own.

The default-OFF contract is enforced here, not merely documented: with
``enabled=False`` the function returns before touching a measurement source, a
gate, an integrity report or the provenance log, and the verdict says exactly
that.

Ordering of side effects (one pass, no retries, no hidden writes):

1. evaluator integrity (pure string/fingerprint work, no I/O);
2. measurements from the injected candidate/incumbent sources;
3. the candidate-vs-incumbent comparison against the declared noise floors;
4. the required gates;
5. the pure decision;
6. provenance append, if a log was injected or ``config.provenance_root`` is set.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from alpha.evolution.evidence.compare import compare_measurements
from alpha.evolution.evidence.config import EvolutionEvidenceConfig
from alpha.evolution.evidence.decision import decide_evidence_verdict
from alpha.evolution.evidence.gates import Gate, GateContext, Probe, default_gate_registry, run_required_gates
from alpha.evolution.evidence.integrity import ChangedPath, IntegrityPolicy, check_evaluator_integrity
from alpha.evolution.evidence.measure import MeasurementRequest, MeasurementSource, collect_measurements
from alpha.evolution.evidence.models import ComparisonReport, EvidenceEvaluation, GateResult, Measurement, Proposal, Verdict
from alpha.evolution.evidence.provenance import ProvenanceLog

__all__ = ["default_measurement_requests", "evaluate_proposal"]


def default_measurement_requests(config: EvolutionEvidenceConfig, *, harness_id: str, sample_size: int | None = None) -> tuple[MeasurementRequest, ...]:
    """The minimum honest request set: the primary metric on both sides.

    The central owner should pass the *full* metric set it tracks (so
    regressions elsewhere are caught); this default exists so a first
    integration measures the one metric the config names.
    """

    size = int(config.min_sample_size if sample_size is None else sample_size)
    metric = str(config.primary_metric)
    return (
        MeasurementRequest(metric=metric, side="candidate", harness_id=harness_id, unit="ratio", sample_size=size),
        MeasurementRequest(metric=metric, side="incumbent", harness_id=harness_id, unit="ratio", sample_size=size),
    )


def evaluate_proposal(
    proposal: Proposal,
    *,
    config: EvolutionEvidenceConfig | Mapping[str, Any] | None = None,
    changed_paths: Sequence[ChangedPath | str] = (),
    baseline_fingerprints: Mapping[str, str] | None = None,
    current_fingerprints: Mapping[str, str] | None = None,
    integrity_policy: IntegrityPolicy | None = None,
    candidate_source: MeasurementSource | None = None,
    incumbent_source: MeasurementSource | None = None,
    measurement_requests: Sequence[MeasurementRequest] | None = None,
    gates: Mapping[str, Gate] | None = None,
    reproducibility_probe: Probe | None = None,
    rollback_probe: Probe | None = None,
    repo_root: str | None = None,
    provenance: ProvenanceLog | None = None,
) -> EvidenceEvaluation:
    """Gate one proposal and return the complete, replayable evidence record.

    Args:
        proposal: The change under evaluation.
        config: ``EvolutionEvidenceConfig`` (or a mapping to read into one).
            ``None`` means the default config, which is **disabled** - a no-op.
        changed_paths: Paths the change touches, with diff evidence where the
            caller has it (``ChangedPath``), used by the integrity check.
        baseline_fingerprints / current_fingerprints: Optional SHA-256 sets over
            the evaluation surface, compared by the integrity check.
        integrity_policy: Overrides ``config.integrity_path_globs``.
        candidate_source / incumbent_source: Injected measurement sources. A
            missing source produces ``unavailable`` measurements with the real
            reason - never a zero.
        measurement_requests: What to measure on each side. Defaults to the
            primary metric on both sides.
        gates: Injectable ``{name: gate}`` registry, merged over the four
            built-in gates. Names in ``config.required_gates`` must resolve.
        reproducibility_probe / rollback_probe: Injected probes for the
            reproducibility and rollback gates. Without them those gates report
            ``unavailable`` and the verdict becomes ``insufficient_evidence``.
        repo_root: Repository root for command gates.
        provenance: An explicit :class:`ProvenanceLog`. When omitted and
            ``config.provenance_root`` is set, one is created there.

    Returns:
        :class:`~alpha.evolution.evidence.models.EvidenceEvaluation` whose
        ``verdict`` is the only field that decides whether the change may become
        the new default.
    """

    active_config = EvolutionEvidenceConfig.from_mapping(config) if isinstance(config, Mapping) else (config or EvolutionEvidenceConfig())
    if not active_config.enabled:
        return EvidenceEvaluation(
            proposal=proposal,
            config_enabled=False,
            verdict=_disabled_verdict(proposal, active_config),
            notes=("the evidence gate is disabled by configuration: no source, gate, integrity check or provenance write was touched",),
        )

    try:
        policy = integrity_policy if integrity_policy is not None else active_config.integrity_policy()
        integrity = check_evaluator_integrity(
            changed_paths,
            policy=policy,
            baseline_fingerprints=baseline_fingerprints,
            current_fingerprints=current_fingerprints,
        )
    except Exception as exc:  # a policy that cannot be built is not a pass
        integrity = None
        policy_error = f"{type(exc).__name__}: {exc}"
    else:
        policy_error = ""

    requests = tuple(measurement_requests) if measurement_requests is not None else default_measurement_requests(active_config, harness_id="unspecified-harness")
    candidate_measurements = collect_measurements(candidate_source, [item for item in requests if item.side == "candidate"], missing_detail="no candidate measurement source was injected")
    incumbent_measurements = collect_measurements(incumbent_source, [item for item in requests if item.side == "incumbent"], missing_detail="no incumbent measurement source was injected")
    measurements = candidate_measurements + incumbent_measurements
    try:
        comparison = compare_measurements(candidate_measurements, incumbent_measurements, config=active_config)
        comparison_error = ""
    except ValueError as exc:
        # A malformed request set (a metric measured twice on one side, a
        # mislabelled side) is a caller bug. The gate refuses to decide instead
        # of crashing or silently averaging it away.
        comparison = ComparisonReport(comparisons=(), primary_metric=str(active_config.primary_metric))
        comparison_error = f"the comparison could not be built: {type(exc).__name__}: {exc}"

    registry = dict(default_gate_registry())
    if gates:
        registry.update(gates)
    context = GateContext(
        proposal=proposal,
        config=active_config,
        integrity=integrity,
        comparison=comparison,
        reproducibility_probe=reproducibility_probe,
        rollback_probe=rollback_probe,
        repo_root=repo_root,
    )
    gate_results = run_required_gates(context, registry, required=active_config.required_gates)
    if policy_error:
        gate_results = (
            GateResult(name="evaluator_integrity", status="error", detail=f"the evaluator-integrity policy could not be built: {policy_error}"),
            *gate_results,
        )

    verdict = decide_evidence_verdict(
        proposal=proposal,
        config=active_config,
        integrity=integrity,
        gate_results=gate_results,
        comparison=comparison,
    )
    provenance_status = _record_provenance(
        proposal,
        measurements=measurements,
        comparison=comparison,
        integrity=integrity,
        gate_results=gate_results,
        verdict=verdict,
        config=active_config,
        provenance=provenance,
    )
    notes = tuple(item for item in (f"evaluator-integrity policy could not be built: {policy_error}" if policy_error else "", comparison_error) if item)
    return EvidenceEvaluation(
        proposal=proposal,
        config_enabled=True,
        integrity=integrity,
        measurements=measurements,
        comparisons=comparison,
        gates=gate_results,
        verdict=verdict,
        provenance_status=provenance_status,
        notes=notes,
    )


def _disabled_verdict(proposal: Proposal, config: EvolutionEvidenceConfig) -> Verdict:
    return Verdict(
        proposal_id=proposal.id,
        status="insufficient_evidence",
        reasons=("the evolution evidence gate is disabled by configuration (default-off): no measurement, gate or integrity check ran, so no promotion decision was made",),
        primary_metric=str(config.primary_metric) or None,
    )


def _record_provenance(
    proposal: Proposal,
    *,
    measurements: Sequence[Measurement],
    comparison: ComparisonReport,
    integrity: Any,
    gate_results: Sequence[GateResult],
    verdict: Verdict,
    config: EvolutionEvidenceConfig,
    provenance: ProvenanceLog | None,
) -> str:
    """Append the full chain when a log is available; disclose what happened.

    Four entry kinds, in replay order: the proposal, every measurement, the
    comparison, and the verdict. The verdict entry also carries the gate
    results and the integrity report so :meth:`ProvenanceLog.explain` can answer
    "why is this the current default?" from the chain alone.
    """

    log = provenance
    if log is None and config.provenance_root is not None:
        log = ProvenanceLog(config.provenance_dir(), chain_id=config.provenance_chain_id)
    if log is None:
        return "not_configured"
    log.append("proposal", proposal.id, proposal.to_dict())
    for measurement in measurements:
        log.append("measurement", proposal.id, measurement.to_dict())
    log.append("comparison", proposal.id, comparison.to_dict())
    verdict_payload = dict(verdict.to_dict())
    verdict_payload["gates"] = [gate.to_dict() for gate in gate_results]
    verdict_payload["integrity"] = integrity.to_dict() if integrity is not None else None
    log.append("verdict", proposal.id, verdict_payload)
    return log.persistence
