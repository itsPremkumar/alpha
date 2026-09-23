"""Real hidden holdout evaluation for RSI candidates (plan WP-A3, feature #3).

This module exists because the pre-existing RSI "holdout" is explicitly
*simulated* (``RSIEngine._run_holdout_evaluation`` returns the constant
``0.89`` with ``evidence_kind="simulated"``).  Here the hidden suite is
genuinely executed on a :class:`~alpha.benchmarks.runner.BenchmarkRunner`
and every score comes from that executed run — never from a constant.

Honesty contract (plan section 3 WP-A3 / section 5.6):

- Hidden cases live only in this module; their IDs are returned solely by
  ``register_hidden_suite()`` / ``hidden_case_ids()`` and never appear in
  candidate-facing payloads such as ``run_rsi_cycle().to_dict()`` or tool
  JSON (anti-gaming, spec section 20/section 60).
- ``run_holdout()`` with no candidate view, an unregistered suite, an
  incomplete case selection, or a suite-level execution error returns
  ``score=0.5, evidence_kind="unverified"`` carrying the *real* error text —
  a missing measurement is neutral and can never pass the gate.
- Evaluator exceptions inside a run are surfaced by ``BenchmarkRunner`` as
  ``passed=False, detail="evaluator raised: …"`` (fail-closed with the real
  message) and make ``holdout_gate()`` return ``(False, "holdout regressions")``.
- ``holdout_gate()`` only ever passes on ``evidence_kind="measured"`` results
  produced by an executed run; ``simulated``/``heuristic``/``unverified``
  inputs (including the existing ``0.89``/``96.4`` simulated scores) are
  rejected with the unverified reason.

The suite runs on a module-owned runner rather than the shared
``get_benchmark_runner()`` singleton so hidden case IDs and results can
never leak through the gateway ``/benchmarks/recent_results`` endpoint.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from alpha.benchmarks.runner import BenchmarkCase, BenchmarkRunner, BenchmarkSuite

HOLDOUT_SUITE_NAME = "rsi-hidden-holdout"
HOLDOUT_SUITE_VERSION = "v1"


@dataclass
class HoldoutCase:
    """One hidden holdout case; candidates never see fixture or expected."""

    case_id: str
    fixture: dict[str, Any]
    expected: dict[str, Any]
    hidden: bool = True


@dataclass
class HoldoutResultRecord:
    """Execution record for one hidden case, written only from a real run."""

    suite: str
    suite_version: str
    case_id: str
    passed: bool
    score: float
    detail: str
    evidence_kind: str
    ran_at: str


# Hidden, small, deterministic suite: each case checks one candidate-view path
# against a hidden expected range.  Thresholds are policy inputs (precedent:
# release_gate thresholds) that candidates cannot observe during generation.
_HIDDEN_CASES: tuple[HoldoutCase, ...] = (
    HoldoutCase(
        case_id="holdout-compaction-budget",
        fixture={"kind": "range", "path": ["compaction", "max_budget_chars"]},
        expected={"min": 10_000, "max": 1_000_000},
    ),
    HoldoutCase(
        case_id="holdout-compaction-keep-observations",
        fixture={"kind": "range", "path": ["compaction", "keep_last_observations"]},
        expected={"min": 1, "max": 16},
    ),
    HoldoutCase(
        case_id="holdout-router-retry-limit",
        fixture={"kind": "range", "path": ["tool_router", "retry_limit"]},
        expected={"min": 1, "max": 10},
    ),
    HoldoutCase(
        case_id="holdout-router-timeout",
        fixture={"kind": "range", "path": ["tool_router", "timeout_seconds"]},
        expected={"min": 1, "max": 300},
    ),
    HoldoutCase(
        case_id="holdout-pruner-strip-threshold",
        fixture={"kind": "range", "path": ["context_pruner", "strip_threshold"]},
        expected={"min": 1, "max": 100_000},
    ),
)

_STATE_LOCK = threading.Lock()
_RUNNER: BenchmarkRunner | None = None
# The candidate view under evaluation; set only for the duration of a
# run_holdout() call (the benchmark Evaluator callable takes no extra args).
_ACTIVE_VIEW: dict[str, Any] = {}


def _ensure_runner() -> BenchmarkRunner:
    global _RUNNER
    with _STATE_LOCK:
        if _RUNNER is None:
            _RUNNER = BenchmarkRunner()
        return _RUNNER


def _dig(view: dict[str, Any], path: list[Any]) -> tuple[bool, Any]:
    cursor: Any = view
    for part in path:
        if not isinstance(cursor, dict) or part not in cursor:
            return False, None
        cursor = cursor[part]
    return True, cursor


def _hidden_holdout_evaluator(case: BenchmarkCase) -> tuple[bool, float, str]:
    """Real, deterministic evaluation of the active candidate view."""
    kind = case.fixture.get("kind")
    if kind == "range":
        path = case.fixture.get("path")
        if not isinstance(path, list) or not path:
            return False, 0.0, f"malformed holdout fixture for {case.case_id}: path must be a non-empty list"
        dotted = ".".join(str(part) for part in path)
        found, value = _dig(_ACTIVE_VIEW, path)
        if not found:
            return False, 0.0, f"missing candidate_view path '{dotted}'"
        low = case.expected.get("min")
        high = case.expected.get("max")
        if isinstance(low, bool) or not isinstance(low, (int, float)) or isinstance(high, bool) or not isinstance(high, (int, float)):
            return False, 0.0, f"malformed holdout expected range for '{dotted}': {case.expected!r}"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, 0.0, f"candidate_view path '{dotted}' is not numeric: {value!r}"
        ok = low <= value <= high
        relation = "within" if ok else "outside"
        return ok, 1.0 if ok else 0.0, f"{dotted}={value!r} {relation} [{low!r}, {high!r}]"
    return False, 0.0, f"unknown holdout fixture kind {kind!r}"


def register_hidden_suite() -> list[str]:
    """Register the hidden holdout suite on the module-owned BenchmarkRunner.

    Idempotent.  Returns the hidden case IDs; this module is the only
    sanctioned source of those IDs (they stay out of candidate payloads).
    """
    if not all(case.hidden for case in _HIDDEN_CASES):
        raise RuntimeError("hidden holdout suite may only contain hidden=True cases")
    runner = _ensure_runner()
    suite = BenchmarkSuite(
        name=HOLDOUT_SUITE_NAME,
        version=HOLDOUT_SUITE_VERSION,
        cases=[
            BenchmarkCase(case_id=case.case_id, title=case.case_id, fixture=dict(case.fixture), expected=dict(case.expected))
            for case in _HIDDEN_CASES
        ],
    )
    runner.register_suite(suite, _hidden_holdout_evaluator)
    return [case.case_id for case in _HIDDEN_CASES]


def hidden_case_ids() -> list[str]:
    """Return the hidden case IDs (copy); never included in candidate payloads."""
    return [case.case_id for case in _HIDDEN_CASES]


def holdout_not_run(reason: str) -> dict[str, Any]:
    """Canonical not-run payload: neutral 0.5, unverified, real reason disclosed."""
    return {"results": [], "passed": 0, "failed": 0, "score": 0.5, "evidence_kind": "unverified", "error": reason}


def run_holdout(candidate_view: dict | None = None, *, case_ids: list[str] | None = None) -> dict:
    """Execute the hidden holdout suite against ``candidate_view``.

    Returns ``{"results": [...], "passed": n, "failed": m, "score": float,
    "evidence_kind": "measured" | "unverified", "error": str | None}``.
    A run only counts as ``measured`` when cases were actually executed;
    every other path fails closed to ``score=0.5, evidence_kind="unverified"``
    with the real error text in ``error``.
    """
    global _ACTIVE_VIEW
    if not isinstance(candidate_view, dict):
        return holdout_not_run("holdout not run: candidate_view was not provided")
    runner = _ensure_runner()
    with _STATE_LOCK:
        _ACTIVE_VIEW = dict(candidate_view)
        try:
            raw = runner.run_suite(HOLDOUT_SUITE_NAME, case_ids=case_ids or None)
        except Exception as exc:
            return holdout_not_run(f"holdout not run: {type(exc).__name__}: {exc}")
        finally:
            _ACTIVE_VIEW = {}
    version = str(raw.get("version", HOLDOUT_SUITE_VERSION))
    records = [
        HoldoutResultRecord(
            suite=HOLDOUT_SUITE_NAME,
            suite_version=version,
            case_id=str(item["case_id"]),
            passed=bool(item["passed"]),
            score=float(item["score"]),
            detail=str(item["detail"]),
            evidence_kind="measured",
            ran_at=datetime.fromtimestamp(float(item["created_at"]), tz=UTC).isoformat(),
        )
        for item in raw["results"]
    ]
    passed_n = sum(1 for record in records if record.passed)
    failed_n = len(records) - passed_n
    payload_results = [asdict(record) for record in records]
    if case_ids:
        executed = {record.case_id for record in records}
        missing = [cid for cid in dict.fromkeys(case_ids) if cid not in executed]
        if missing:
            return {
                "results": payload_results,
                "passed": passed_n,
                "failed": failed_n,
                "score": 0.5,
                "evidence_kind": "unverified",
                "error": f"holdout not fully run: requested case(s) did not execute: {', '.join(missing)}",
            }
    if not records:
        return holdout_not_run("holdout not run: no holdout cases executed")
    return {
        "results": payload_results,
        "passed": passed_n,
        "failed": failed_n,
        "score": passed_n / len(records),
        "evidence_kind": "measured",
        "error": None,
    }


def holdout_gate(result: dict) -> tuple[bool, str]:
    """Gate on a run_holdout() payload: only executed, measured, clean runs pass."""
    if not isinstance(result, dict):
        return False, "holdout unverified — cannot gate on unverified evidence"
    passed_count = result.get("passed")
    failed_count = result.get("failed")
    counts_ok = (
        isinstance(passed_count, int)
        and not isinstance(passed_count, bool)
        and isinstance(failed_count, int)
        and not isinstance(failed_count, bool)
        and passed_count >= 0
        and failed_count >= 0
        and passed_count + failed_count > 0
    )
    if result.get("evidence_kind") != "measured" or not counts_ok:
        return False, "holdout unverified — cannot gate on unverified evidence"
    if failed_count > 0:
        return False, "holdout regressions"
    return True, "holdout passed"
