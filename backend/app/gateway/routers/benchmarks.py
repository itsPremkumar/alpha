"""Benchmark plane API: versioned suites plus a demo offline suite."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])


def _ensure_demo_suite() -> None:
    from alpha.benchmarks import BenchmarkCase, BenchmarkSuite, get_benchmark_runner
    from alpha.benchmarks.suites import register_eval_suites

    register_eval_suites()
    runner = get_benchmark_runner()
    if any(s["name"] == "workforce-smoke" for s in runner.list_suites()):
        return

    def _eval(case: BenchmarkCase) -> tuple[bool, float, str]:
        from alpha.projects.evidence import check_completion
        from alpha.recovery import decide

        if case.case_id == "evidence-gate":
            gate = check_completion([{"kind": "commit", "reference": "abc"}, {"kind": "tests_passed", "reference": "r"}, {"kind": "lint", "reference": "r"}])
            return gate.passed, 1.0 if gate.passed else 0.0, "evidence gate enforces commit+tests+lint"
        if case.case_id == "recovery-bounded":
            first = decide("model timed out", attempt=1)
            last = decide("model timed out", attempt=99)
            ok = first.action == "retry" and last.action == "fallback_model"
            return ok, 1.0 if ok else 0.0, "retries bounded, terminal strategy engaged"
        return False, 0.0, f"no evaluator branch for {case.case_id}"

    runner.register_suite(
        BenchmarkSuite(name="workforce-smoke", version="v1", cases=[BenchmarkCase(case_id="evidence-gate", title="Evidence gate blocks incomplete work"), BenchmarkCase(case_id="recovery-bounded", title="Recovery budgets terminate")]),
        _eval,
    )


class RunSuiteRequest(BaseModel):
    case_ids: list[str] | None = None


@router.get("/suites")
async def list_suites() -> dict:
    def _do():
        _ensure_demo_suite()
        from alpha.benchmarks import get_benchmark_runner

        return get_benchmark_runner().list_suites()

    return {"suites": await asyncio.to_thread(_do)}


@router.post("/suites/{name}/run")
async def run_suite(name: str, body: RunSuiteRequest) -> dict:
    def _do():
        _ensure_demo_suite()
        from alpha.benchmarks import get_benchmark_runner

        return get_benchmark_runner().run_suite(name, case_ids=body.case_ids)

    try:
        return await asyncio.to_thread(_do)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/results")
async def recent_results(limit: int = 50) -> dict:
    def _do():
        from alpha.benchmarks import get_benchmark_runner

        return get_benchmark_runner().recent_results(limit=min(limit, 200))

    return {"results": await asyncio.to_thread(_do)}
