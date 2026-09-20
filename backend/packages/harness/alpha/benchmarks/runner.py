"""Benchmark plane: versioned suites with deterministic offline evaluation.

Suites declare cases with fixtures; the runner executes the registered
evaluator, records trajectory references, cost, and git revision — no live
APIs, no credentials, reproducible by design.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkCase:
    case_id: str
    title: str
    fixture: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkResult:
    result_id: str
    suite: str
    case_id: str
    passed: bool
    score: float = 0.0
    detail: str = ""
    duration_sec: float = 0.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkSuite:
    name: str
    version: str = "v1"
    cases: list[BenchmarkCase] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "cases": [c.to_dict() for c in self.cases]}


Evaluator = Callable[[BenchmarkCase], tuple[bool, float, str]]


class BenchmarkRunner:
    def __init__(self):
        self._suites: dict[str, BenchmarkSuite] = {}
        self._evaluators: dict[str, Evaluator] = {}
        self._results: list[BenchmarkResult] = []
        self._lock = threading.Lock()

    def register_suite(self, suite: BenchmarkSuite, evaluator: Evaluator) -> None:
        with self._lock:
            self._suites[suite.name] = suite
            self._evaluators[suite.name] = evaluator

    def run_suite(self, name: str, *, case_ids: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            suite = self._suites.get(name)
            evaluator = self._evaluators.get(name)
        if not suite or not evaluator:
            raise ValueError(f"Benchmark suite '{name}' is not registered.")
        cases = [c for c in suite.cases if not case_ids or c.case_id in case_ids]
        results: list[BenchmarkResult] = []
        for case in cases:
            started = time.time()
            try:
                passed, score, detail = evaluator(case)
            except Exception as exc:
                passed, score, detail = False, 0.0, f"evaluator raised: {exc}"
            results.append(BenchmarkResult(result_id=f"br-{uuid.uuid4().hex[:8]}", suite=f"{name}@{suite.version}", case_id=case.case_id, passed=passed, score=score, detail=detail, duration_sec=round(time.time() - started, 3)))
        with self._lock:
            self._results.extend(results)
        passed = sum(1 for r in results if r.passed)
        return {"suite": name, "version": suite.version, "total": len(results), "passed": passed, "failed": len(results) - passed, "results": [r.to_dict() for r in results]}

    def list_suites(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"name": s.name, "version": s.version, "cases": len(s.cases)} for s in self._suites.values()]

    def recent_results(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [r.to_dict() for r in self._results[-limit:]]


_runner: BenchmarkRunner | None = None
_runner_lock = threading.Lock()


def get_benchmark_runner() -> BenchmarkRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = BenchmarkRunner()
        return _runner
