"""Task Evaluation Benchmark Suite and Flywheel Evals Engine.

Inspired by Chapters 36 and 37 of the Master Architecture Blueprint:
- Standard task evaluation suite (research_001, coding_001, browser_001, computer_001, multi_agent_001, long_horizon_001)
- Quantitative scoring: Task Success (0/1), Tool Precision, Verification Accuracy, Hallucination Score, Cost USD, Latency
- Automated evaluation runner & leaderboard scorecard
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class BenchmarkTaskCategory(str, Enum):
    RESEARCH = "research"
    CODING = "coding"
    BROWSER = "browser"
    COMPUTER = "computer"
    DOCUMENT = "document"
    MULTI_AGENT = "multi_agent"
    LONG_HORIZON = "long_horizon"


@dataclass
class BenchmarkTaskSpec:
    """Specification of an autonomous evaluation benchmark task."""
    task_id: str
    name: str
    category: BenchmarkTaskCategory
    prompt: str
    expected_output_keywords: list[str] = field(default_factory=list)
    required_tool_patterns: list[str] = field(default_factory=list)
    max_budget_usd: float = 1.0
    max_time_sec: float = 120.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        return data


@dataclass
class TaskEvaluationResult:
    """Comprehensive multi-dimensional scorecard for a benchmark task run."""
    task_id: str
    success: bool
    tool_precision: float = 1.0
    verification_score: float = 1.0
    hallucination_score: float = 0.0  # 0.0 is perfect, 1.0 is severe hallucination
    elapsed_time_sec: float = 0.0
    cost_usd: float = 0.0
    notes: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "tool_precision": round(self.tool_precision, 3),
            "verification_score": round(self.verification_score, 3),
            "hallucination_score": round(self.hallucination_score, 3),
            "elapsed_time_sec": round(self.elapsed_time_sec, 2),
            "cost_usd": round(self.cost_usd, 5),
            "notes": self.notes,
            "timestamp": self.timestamp,
        }


# Standard Benchmark Suite Catalog (Chapter 37)
STANDARD_BENCHMARKS: list[BenchmarkTaskSpec] = [
    BenchmarkTaskSpec(
        task_id="research_001",
        name="Deep Competitor Technical Analysis",
        category=BenchmarkTaskCategory.RESEARCH,
        prompt="Synthesize competitive landscape and architectural differences between OmO and Hermes agent systems.",
        expected_output_keywords=["hermes", "omo", "architecture", "memory", "orchestration"],
        required_tool_patterns=["search", "read"],
        max_budget_usd=0.25,
        max_time_sec=60.0,
    ),
    BenchmarkTaskSpec(
        task_id="coding_001",
        name="Implement Red-Black Tree Rotation with Tests",
        category=BenchmarkTaskCategory.CODING,
        prompt="Write a Python red-black tree with left_rotate and right_rotate and verified unit tests.",
        expected_output_keywords=["def left_rotate", "def right_rotate", "def test_", "assert"],
        required_tool_patterns=["python_repl", "write_to_file"],
        max_budget_usd=0.50,
        max_time_sec=90.0,
    ),
    BenchmarkTaskSpec(
        task_id="browser_001",
        name="Web Data Extraction and Table Normalization",
        category=BenchmarkTaskCategory.BROWSER,
        prompt="Navigate to corporate documentation, extract API rate limits table, and serialize as JSON.",
        expected_output_keywords=["rate_limit", "requests_per_minute", "tier"],
        required_tool_patterns=["browser", "read_url"],
        max_budget_usd=0.30,
        max_time_sec=75.0,
    ),
    BenchmarkTaskSpec(
        task_id="computer_001",
        name="Sandboxed Terminal Diagnostics and Repair",
        category=BenchmarkTaskCategory.COMPUTER,
        prompt="Diagnose missing library dependency in a sandboxed repo and resolve it without root privileges.",
        expected_output_keywords=["dependency", "installed", "exit code 0"],
        required_tool_patterns=["bash", "terminal"],
        max_budget_usd=0.40,
        max_time_sec=80.0,
    ),
    BenchmarkTaskSpec(
        task_id="multi_agent_001",
        name="Tri-Agent Collaborative System Specification",
        category=BenchmarkTaskCategory.MULTI_AGENT,
        prompt="Coordinate Planner, Implementer, and Critic agents to produce an RFC specification.",
        expected_output_keywords=["rfc", "planner", "critic", "approved", "consensus"],
        required_tool_patterns=["council", "group_chat"],
        max_budget_usd=0.75,
        max_time_sec=120.0,
    ),
    BenchmarkTaskSpec(
        task_id="long_horizon_001",
        name="Full-Stack Milestone Goal Pursuit",
        category=BenchmarkTaskCategory.LONG_HORIZON,
        prompt="Execute an end-to-end multi-step migration with backward compatibility checks and rollback plan.",
        expected_output_keywords=["migration", "backward_compatible", "verified", "rollback"],
        required_tool_patterns=["work_queue", "evidence_matrix"],
        max_budget_usd=1.00,
        max_time_sec=180.0,
    ),
]


class EvaluationRunner:
    """Evaluates agent responses, tool invocations, and computes multi-dimensional benchmarks."""

    @staticmethod
    def evaluate_task(
        spec: BenchmarkTaskSpec,
        agent_response: str,
        tool_calls_log: list[dict[str, Any]] | None = None,
        elapsed_time_sec: float = 1.0,
        cost_usd: float = 0.01,
        exit_code: int = 0,
    ) -> TaskEvaluationResult:
        t_calls = tool_calls_log or []
        resp_lower = agent_response.lower()

        # 1. Keyword check
        matched_keywords = [
            kw for kw in spec.expected_output_keywords if kw.lower() in resp_lower
        ]
        keyword_ratio = len(matched_keywords) / len(spec.expected_output_keywords) if spec.expected_output_keywords else 1.0

        # 2. Tool pattern check
        called_tools = [str(tc.get("tool_name", "")).lower() for tc in t_calls]
        matched_tools = 0
        for pattern in spec.required_tool_patterns:
            if any(pattern in tool_name for tool_name in called_tools):
                matched_tools += 1
        tool_coverage = (matched_tools / len(spec.required_tool_patterns)) if spec.required_tool_patterns else 1.0

        # 3. Tool precision: check if any tools failed
        failed_tools = sum(1 for tc in t_calls if tc.get("exit_code", 0) != 0 or tc.get("error"))
        tool_precision = 1.0 - (failed_tools / len(t_calls)) if t_calls else 1.0

        # 4. Hallucination scoring: check for contradiction between claims and exit codes
        hallucination_score = 0.0
        if "successfully completed" in resp_lower and exit_code != 0:
            hallucination_score = 0.8
        elif keyword_ratio < 0.3:
            hallucination_score = 0.4

        # 5. Success gate: needs keyword coverage, tool coverage, exit code 0, and within budget/time
        success = (
            keyword_ratio >= 0.6
            and exit_code == 0
            and elapsed_time_sec <= (spec.max_time_sec * 1.25)
            and cost_usd <= (spec.max_budget_usd * 1.5)
        )

        verification_score = (keyword_ratio * 0.6) + (tool_coverage * 0.4)

        return TaskEvaluationResult(
            task_id=spec.task_id,
            success=success,
            tool_precision=tool_precision,
            verification_score=verification_score,
            hallucination_score=hallucination_score,
            elapsed_time_sec=elapsed_time_sec,
            cost_usd=cost_usd,
            notes=f"Matched {len(matched_keywords)}/{len(spec.expected_output_keywords)} keywords. Tools: {matched_tools}/{len(spec.required_tool_patterns)}.",
        )

    @classmethod
    def run_benchmark_summary(cls, results: list[TaskEvaluationResult]) -> dict[str, Any]:
        """Aggregate multiple task results into a consolidated leaderboard scorecard."""
        if not results:
            return {"total_tasks": 0, "pass_rate": 0.0}

        total = len(results)
        passed = sum(1 for r in results if r.success)
        avg_precision = sum(r.tool_precision for r in results) / total
        avg_verification = sum(r.verification_score for r in results) / total
        avg_hallucination = sum(r.hallucination_score for r in results) / total
        avg_latency = sum(r.elapsed_time_sec for r in results) / total
        total_cost = sum(r.cost_usd for r in results)

        return {
            "total_benchmarks": total,
            "passed_count": passed,
            "pass_rate": round(passed / total, 3),
            "avg_tool_precision": round(avg_precision, 3),
            "avg_verification_score": round(avg_verification, 3),
            "avg_hallucination_score": round(avg_hallucination, 3),
            "avg_latency_sec": round(avg_latency, 2),
            "total_cost_usd": round(total_cost, 5),
            "tasks": [r.to_dict() for r in results],
        }
