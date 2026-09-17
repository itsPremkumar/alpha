"""Built-in Task Evaluation Benchmark LangChain Tool."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.evaluation.benchmark import (
    STANDARD_BENCHMARKS,
    BenchmarkTaskCategory,
    BenchmarkTaskSpec,
    EvaluationRunner,
    TaskEvaluationResult,
)

_BENCHMARK_RESULTS: list[TaskEvaluationResult] = []


@tool("run_task_evaluation_benchmark", parse_docstring=True)
def run_task_evaluation_benchmark(
    action: str,
    task_id: str = "research_001",
    agent_response: str = "",
    tool_calls_json: str = "[]",
    elapsed_time_sec: float = 2.5,
    cost_usd: float = 0.01,
    exit_code: int = 0,
) -> str:
    """Run standard agentic benchmarks (research, coding, browser, computer, multi-agent) and report scores.

    Args:
        action: 'list_benchmarks', 'evaluate_run', 'get_leaderboard_summary', 'clear_results'.
        task_id: Target benchmark ID ('research_001', 'coding_001', 'browser_001', 'computer_001', 'multi_agent_001', 'long_horizon_001').
        agent_response: Narrative output produced by the agent to be scored.
        tool_calls_json: JSON list of tool execution logs with keys 'tool_name', 'exit_code', 'error'.
        elapsed_time_sec: Elapsed execution time in seconds.
        cost_usd: Estimated model/tool cost in USD.
        exit_code: Final return code of the task run.
    """
    try:
        t_calls = json.loads(tool_calls_json) if tool_calls_json else []
    except Exception:
        t_calls = []

    try:
        if action == "list_benchmarks":
            return json.dumps([b.to_dict() for b in STANDARD_BENCHMARKS], indent=2)

        elif action == "evaluate_run":
            spec = next((b for b in STANDARD_BENCHMARKS if b.task_id == task_id), None)
            if not spec:
                spec = BenchmarkTaskSpec(
                    task_id=task_id,
                    name=f"Custom Benchmark: {task_id}",
                    category=BenchmarkTaskCategory.CODING,
                    prompt="Execute task",
                    expected_output_keywords=["success", "verified"],
                )

            res = EvaluationRunner.evaluate_task(
                spec=spec,
                agent_response=agent_response,
                tool_calls_log=t_calls,
                elapsed_time_sec=elapsed_time_sec,
                cost_usd=cost_usd,
                exit_code=exit_code,
            )
            _BENCHMARK_RESULTS.append(res)
            return json.dumps({"status": "evaluated", "result": res.to_dict()}, indent=2)

        elif action == "get_leaderboard_summary":
            summary = EvaluationRunner.run_benchmark_summary(_BENCHMARK_RESULTS)
            return json.dumps(summary, indent=2)

        elif action == "clear_results":
            _BENCHMARK_RESULTS.clear()
            return json.dumps({"status": "cleared", "total_records": 0})

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error running benchmark evaluation: {exc}"
