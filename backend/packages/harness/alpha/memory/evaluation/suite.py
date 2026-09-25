"""Case loading, suite aggregation, thresholds, and explicit baseline I/O."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from alpha.memory.evaluation.config import EvaluationConfig
from alpha.memory.evaluation.metrics import (
    aggregate_metrics,
    metric_denominators,
    metric_observed,
)
from alpha.memory.evaluation.models import (
    ABILITIES,
    METRIC_NAMES,
    BaselineComparison,
    Case,
    CaseResult,
    SuiteReport,
    ThresholdCheck,
)
from alpha.memory.evaluation.runner import (
    MemoryProvider,
    ProviderKind,
    ProviderUnavailable,
    ScriptedProvider,
    run_case,
)

_DEFAULT_CASES_DIR = Path(__file__).resolve().parent / "cases"


class CaseLoadError(ValueError):
    """Raised when a bundled or injected case file is invalid."""


def default_cases_dir() -> Path:
    """Return the package-local original case directory."""

    return _DEFAULT_CASES_DIR


def load_cases(cases_dir: str | Path | None = None, *, max_cases: int | None = None) -> list[Case]:
    """Load sorted JSON cases from an explicitly selected directory.

    JSON is parsed with UTF-8 and no network/provider access.  A malformed case
    fails loudly; it is never silently skipped or assigned a score.
    """

    directory = Path(cases_dir) if cases_dir is not None else default_cases_dir()
    if not directory.exists():
        raise CaseLoadError(f"memory evaluation cases directory does not exist: {directory}")
    if not directory.is_dir():
        raise CaseLoadError(f"memory evaluation cases path is not a directory: {directory}")
    paths = sorted(directory.glob("*.json"), key=lambda path: path.name)
    if max_cases is not None:
        if isinstance(max_cases, bool) or int(max_cases) < 1:
            raise ValueError("max_cases must be a positive integer or None")
        paths = paths[: int(max_cases)]
    cases: list[Case] = []
    seen: set[str] = set()
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("case JSON must contain an object")
            case = Case.from_dict(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CaseLoadError(f"invalid memory evaluation case {path}: {exc}") from exc
        if case.id in seen:
            raise CaseLoadError(f"duplicate memory evaluation case id: {case.id}")
        seen.add(case.id)
        cases.append(case)
    return cases


def load_case_file(path: str | Path) -> Case:
    """Load exactly one case file, useful for a caller-owned case directory."""

    case_path = Path(path)
    try:
        payload = json.loads(case_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("case JSON must contain an object")
        return Case.from_dict(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CaseLoadError(f"invalid memory evaluation case {case_path}: {exc}") from exc


def aggregate_results(cases: Sequence[Case], results: Sequence[CaseResult]) -> SuiteReport:
    """Build a report without running providers or applying a baseline."""

    case_list = list(cases)
    result_list = list(results)
    per_ability = _per_ability_counts(case_list, result_list)
    metrics = aggregate_metrics(case_list, result_list)
    denominators = metric_denominators(case_list, result_list)
    observed = metric_observed(case_list, result_list)
    ability_metrics = _ability_metrics(case_list, result_list)
    unavailable = sum(1 for result in result_list if result.status == "unavailable")
    errors = sum(1 for result in result_list if result.status == "error")
    measured = sum(1 for result in result_list if result.measured)
    evidence_kinds = {result.evidence_kind for result in result_list}
    evidence_kind = next(iter(evidence_kinds)) if len(evidence_kinds) == 1 else ("mixed" if evidence_kinds else "")
    return SuiteReport(
        results=result_list,
        per_ability=per_ability,
        metrics=metrics,
        ability_metrics=ability_metrics,
        metric_denominators=denominators,
        metric_observed=observed,
        total_cases=len(case_list),
        ran_cases=len(result_list) - unavailable,
        measured_cases=measured,
        unavailable_cases=unavailable,
        error_cases=errors,
        provider_kind=result_list[0].provider_kind if result_list else "",
        evidence_kind=evidence_kind,
    )


def evaluate_thresholds(report: SuiteReport, config: EvaluationConfig) -> list[ThresholdCheck]:
    """Apply only thresholds explicitly present on ``config``.

    A metric with no measured observations is not assigned zero.  Its check is
    ``passed=None`` and carries a reason, so a caller can fail closed without
    inventing a score.
    """

    checks: list[ThresholdCheck] = []
    for metric, comparator, threshold in config.threshold_specs():
        current = report.metrics.get(metric)
        observed = report.metric_observed.get(metric, False)
        if current is None or not observed:
            checks.append(
                ThresholdCheck(
                    metric=metric,
                    comparator=comparator,
                    threshold=threshold,
                    current=current,
                    passed=None,
                    reason="metric has no measured observations",
                )
            )
            continue
        if comparator == "min":
            passed = current >= threshold
            operator = ">="
        else:
            passed = current <= threshold
            operator = "<="
        checks.append(
            ThresholdCheck(
                metric=metric,
                comparator=comparator,
                threshold=threshold,
                current=current,
                passed=passed,
                reason="" if passed else f"{current!r} does not satisfy {operator} {threshold!r}",
            )
        )
    return checks


def compare_baseline(
    report: SuiteReport,
    baseline: Mapping[str, Any] | str | Path | None = None,
    *,
    config: EvaluationConfig | Mapping[str, Any],
    path: str | None = None,
    baseline_path: str | Path | None = None,
) -> BaselineComparison:
    """Compare a report with a baseline and expose per-ability deltas.

    Baseline I/O is never implicit: callers pass a parsed mapping and ``path``
    is recorded for provenance.  Numeric deltas are current minus baseline.
    Pass/fail is based on the same configured metric thresholds used by the
    live report; a metric without a configured threshold is reported as
    ``passed=None`` rather than silently inventing a gate.
    """

    active_config = EvaluationConfig.from_mapping(config) if isinstance(config, Mapping) else config
    if baseline is None and baseline_path is not None:
        baseline = baseline_path
        path = path or str(baseline_path)
    if baseline is None:
        return BaselineComparison(enabled=False, passed=False, path=path, reason="no baseline supplied")
    if isinstance(baseline, SuiteReport):
        baseline = baseline.to_dict()
    if isinstance(baseline, (str, Path)):
        path = path or str(baseline)
        baseline = load_baseline(baseline)
    baseline_metrics = _baseline_metrics(baseline)
    overall = _compare_metric_mapping(report.metrics, baseline_metrics, active_config)
    per_ability: dict[str, dict[str, dict[str, Any]]] = {}
    baseline_abilities = _baseline_abilities(baseline)
    for ability, current_values in report.ability_metrics.items():
        if ability in baseline_abilities:
            per_ability[ability] = _compare_metric_mapping(current_values, baseline_abilities[ability], active_config)
    failed = [item for item in overall.values() if item.get("passed") is False]
    for values in per_ability.values():
        failed.extend(item for item in values.values() if item.get("passed") is False)
    reason = "" if not failed else f"{len(failed)} configured metric gate(s) failed"
    return BaselineComparison(enabled=True, passed=not failed, path=path, overall=overall, per_ability=per_ability, reason=reason)


def load_baseline(path: str | Path) -> dict[str, Any]:
    """Read a baseline JSON file explicitly and return its object payload."""

    baseline_path = Path(path)
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("memory evaluation baseline must contain a JSON object")
    return dict(payload)


def write_baseline(report: SuiteReport, path: str | Path, *, overwrite: bool = False) -> Path:
    """Write a baseline only when a caller explicitly supplies ``path``."""

    destination = Path(path)
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def run_suite(
    cases: Sequence[Case | Mapping[str, Any]] | Case | Mapping[str, Any] | str | Path | None = None,
    provider: MemoryProvider | Any | Callable[[Case], Any] | None = None,
    *,
    config: EvaluationConfig | Mapping[str, Any] | None = None,
    provider_kind: ProviderKind = "real",
    kind: ProviderKind | None = None,
    provider_factory: Callable[[Case], Any] | None = None,
    cases_dir: str | Path | None = None,
    baseline_path: str | Path | None = None,
    baseline: str | Path | None = None,
    clock: Callable[[], float] | None = None,
) -> SuiteReport:
    """Load and run cases, aggregate them, and optionally compare a baseline.

    A disabled config returns an empty, explicit ``disabled`` report before it
    reads cases or touches the provider.  This is the no-op behavior required
    for hermetic test suites.  A real provider must be injected; scripted mode
    is opt-in and labelled as an oracle in each result.
    """

    if kind is not None:
        provider_kind = kind
    active_config = EvaluationConfig.from_mapping(config) if isinstance(config, Mapping) else (config or EvaluationConfig())
    if not active_config.enabled:
        return _disabled_report(provider_kind, active_config)
    if provider_kind not in {"real", "scripted"}:
        raise ValueError(f"unsupported provider kind: {provider_kind!r}")
    selected_cases = _resolve_cases(cases, cases_dir, active_config)
    effective_provider_kind = provider_kind
    if provider_kind == "real" and provider is not None and getattr(provider, "provider_kind", None) in {"real", "scripted"}:
        effective_provider_kind = getattr(provider, "provider_kind")
    results: list[CaseResult] = []
    for case in selected_cases:
        try:
            case_provider = _provider_for_case(case, provider, provider_factory, effective_provider_kind)
            results.append(run_case(case, case_provider, provider_kind=effective_provider_kind, clock=clock))
        except Exception as exc:
            unavailable = isinstance(exc, ProviderUnavailable) or "unavailable" in str(exc).casefold()
            results.append(
                CaseResult(
                    case_id=case.id,
                    ability=case.ability,
                    status="unavailable" if unavailable else "error",
                    reason=("provider factory unavailable: " if unavailable else "provider factory error: ") + str(exc),
                    expected_stored_ids=case.expected_stored_ids,
                    provider_kind=effective_provider_kind,
                    evidence_kind="unavailable" if unavailable else "provider_error",
                )
            )
    report = aggregate_results(selected_cases, results)
    report.provider_kind = provider_kind
    if provider_kind == "real" and results and all(result.evidence_kind == "scripted_oracle" for result in results):
        report.provider_kind = "scripted"
    report.thresholds = evaluate_thresholds(report, active_config)
    selected_baseline_path = baseline_path if baseline_path is not None else (baseline if baseline is not None else active_config.baseline_path)
    if selected_baseline_path is not None:
        try:
            baseline_payload = load_baseline(selected_baseline_path)
            report.baseline = compare_baseline(report, baseline_payload, config=active_config, path=str(selected_baseline_path))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            report.baseline = BaselineComparison(enabled=True, passed=False, path=str(selected_baseline_path), reason=f"baseline unavailable: {exc}")
    report.metadata.update(
        {
            "cases_dir": str(_resolve_cases_dir(cases_dir, active_config)),
            "max_cases": active_config.max_cases,
            "storage_path": active_config.storage_path,
            "thresholds_configured": True,
        }
    )
    return report


def _disabled_report(provider_kind: str, config: EvaluationConfig) -> SuiteReport:
    report = SuiteReport(
        results=[],
        per_ability=_empty_ability_counts(),
        metrics={name: None for name in METRIC_NAMES},
        ability_metrics={},
        metric_denominators={name: 0 for name in METRIC_NAMES},
        metric_observed={name: False for name in METRIC_NAMES},
        total_cases=0,
        ran_cases=0,
        measured_cases=0,
        unavailable_cases=0,
        error_cases=0,
        enabled=False,
        provider_kind=provider_kind,
        evidence_kind="not_run",
        reason="memory evaluation disabled by config",
    )
    report.metadata["thresholds_configured"] = True
    report.metadata["baseline_path"] = config.baseline_path
    return report


def _resolve_cases(
    cases: Sequence[Case | Mapping[str, Any]] | Case | Mapping[str, Any] | str | Path | None,
    cases_dir: str | Path | None,
    config: EvaluationConfig,
) -> list[Case]:
    if isinstance(cases, Case):
        return [cases]
    if isinstance(cases, Mapping):
        return [Case.from_dict(cases)]
    if isinstance(cases, (str, Path)):
        return load_cases(cases, max_cases=config.max_cases)
    if cases is None:
        directory = _resolve_cases_dir(cases_dir, config)
        return load_cases(directory, max_cases=config.max_cases)
    normalized = [case if isinstance(case, Case) else Case.from_dict(case) for case in cases]
    if config.max_cases is not None:
        normalized = normalized[: config.max_cases]
    if len({case.id for case in normalized}) != len(normalized):
        raise ValueError("memory evaluation case IDs must be unique")
    return normalized


def _resolve_cases_dir(cases_dir: str | Path | None, config: EvaluationConfig) -> Path:
    if cases_dir is not None:
        return Path(cases_dir)
    if config.cases_dir is not None:
        return Path(config.cases_dir)
    return default_cases_dir()


def _provider_for_case(
    case: Case,
    provider: MemoryProvider | Any | Callable[[Case], Any] | None,
    provider_factory: Callable[[Case], Any] | None,
    provider_kind: ProviderKind,
) -> Any:
    if provider_factory is not None:
        return provider_factory(case)
    if callable(provider) and not hasattr(provider, "write"):
        return provider(case)
    if provider is not None:
        return provider
    if provider_kind == "scripted":
        return ScriptedProvider.from_case(case)
    return None


def _per_ability_counts(cases: Sequence[Case], results: Sequence[CaseResult]) -> dict[str, dict[str, int]]:
    counts = _empty_ability_counts()
    case_by_id = {case.id: case for case in cases}
    for result in results:
        case = case_by_id.get(result.case_id)
        ability = case.ability if case is not None else result.ability
        if ability not in counts:
            counts[ability] = {"total": 0, "pass": 0, "fail": 0, "unavailable": 0, "error": 0}
        counts[ability]["total"] += 1
        if result.status in counts[ability]:
            counts[ability][result.status] += 1
    return counts


def _empty_ability_counts() -> dict[str, dict[str, int]]:
    return {ability: {"total": 0, "pass": 0, "fail": 0, "unavailable": 0, "error": 0} for ability in ABILITIES}


def _ability_metrics(cases: Sequence[Case], results: Sequence[CaseResult]) -> dict[str, dict[str, float | None]]:
    metrics_by_ability: dict[str, dict[str, float | None]] = {}
    primary = {
        "extraction": "extraction_recall",
        "multi_session": "multi_session_accuracy",
        "temporal": "temporal_accuracy",
        "update": "update_accuracy",
        "abstention": "abstention_rate",
        "contamination": "contamination_rate",
        "write_precision": "write_precision",
        "evidence_traceability": "evidence_traceability",
        "token_efficiency": "token_efficiency",
        "procedural": "",
        "failure_avoidance": "",
    }
    for ability in ABILITIES:
        ability_cases = [case for case in cases if case.ability == ability]
        ability_results = [result for result in results if result.case_id in {case.id for case in ability_cases}]
        if not ability_cases:
            continue
        aggregate = aggregate_metrics(ability_cases, ability_results)
        measured_results = [result for result in ability_results if result.measured]
        pass_rate = (sum(1 for result in measured_results if result.status == "pass") / len(measured_results)) if measured_results else None
        question_values = [outcome.correct for result in measured_results for outcome in result.question_outcomes]
        question_accuracy = (sum(question_values) / len(question_values)) if question_values else None
        values: dict[str, float | None] = {"pass_rate": pass_rate, "question_accuracy": question_accuracy}
        for name in ("extraction_recall", "multi_session_accuracy", "temporal_accuracy", "update_accuracy", "abstention_rate", "contamination_rate", "write_precision", "evidence_traceability", "token_efficiency"):
            if aggregate.get(name) is not None:
                values[name] = aggregate[name]
        if primary[ability] in values:
            values["primary_metric"] = values[primary[ability]]  # type: ignore[assignment]
        metrics_by_ability[ability] = values
    return metrics_by_ability


def _baseline_metrics(baseline: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("metrics", "overall_metrics"):
        value = baseline.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    report = baseline.get("report")
    if isinstance(report, Mapping) and isinstance(report.get("metrics"), Mapping):
        return dict(report["metrics"])
    return {}


def _baseline_abilities(baseline: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    for key in ("ability_metrics", "abilities", "per_ability_metrics"):
        value = baseline.get(key)
        if isinstance(value, Mapping):
            return {str(ability): dict(metrics) for ability, metrics in value.items() if isinstance(metrics, Mapping)}
    report = baseline.get("report")
    if isinstance(report, Mapping) and isinstance(report.get("ability_metrics"), Mapping):
        return {str(ability): dict(metrics) for ability, metrics in report["ability_metrics"].items() if isinstance(metrics, Mapping)}
    return {}


def _compare_metric_mapping(current: Mapping[str, Any], baseline: Mapping[str, Any], config: EvaluationConfig) -> dict[str, dict[str, Any]]:
    comparison: dict[str, dict[str, Any]] = {}
    for metric in sorted(set(current) | set(baseline)):
        current_value = current.get(metric)
        baseline_value = baseline.get(metric)
        if not _is_number(current_value) or not _is_number(baseline_value):
            if _is_number(current_value) or _is_number(baseline_value):
                comparison[str(metric)] = {
                    "baseline": float(baseline_value) if _is_number(baseline_value) else None,
                    "current": float(current_value) if _is_number(current_value) else None,
                    "delta": None,
                    "comparator": "",
                    "threshold": None,
                    "passed": None,
                    "reason": "one side of the comparison is unavailable",
                }
            continue
        delta = float(current_value) - float(baseline_value)
        threshold = config.threshold_for(str(metric))
        if threshold is None:
            passed: bool | None = None
            comparator = ""
            threshold_value = None
            reason = "no configured threshold for delta"
        else:
            comparator, threshold_value = threshold
            passed = float(current_value) >= threshold_value if comparator == "min" else float(current_value) <= threshold_value
            reason = "" if passed else f"current value {current_value!r} fails configured {comparator} threshold {threshold_value!r}"
        comparison[str(metric)] = {
            "baseline": float(baseline_value),
            "current": float(current_value),
            "delta": delta,
            "comparator": comparator,
            "threshold": threshold_value,
            "passed": passed,
            "reason": reason,
        }
    return comparison


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


__all__ = [
    "CaseLoadError",
    "aggregate_results",
    "compare_baseline",
    "default_cases_dir",
    "evaluate_thresholds",
    "load_baseline",
    "load_case_file",
    "load_cases",
    "run_suite",
    "write_baseline",
]
