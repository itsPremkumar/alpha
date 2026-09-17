"""Swarm Benefit Estimator and Critical-Path Analyzer.

Determines mathematically whether a goal or workload warrants an autonomous swarm
by comparing serial execution against parallel critical path plus orchestration overhead.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from agent_workspace.swarm.models import SwarmDecision, SwarmMode


class SwarmBenefitEstimator:
    """Calculates expected speedup and evaluates whether a task deserves a swarm."""

    # Keywords signaling naturally parallelizable subtasks
    BATCH_KEYWORDS = ("each", "all", "batch", "every", "multiple", "compare", "list of", "urls", "documents", "repos")
    CODE_KEYWORDS = ("refactor", "codebase", "architecture", "microservices", "frontend and backend", "full stack", "audit")
    RESEARCH_KEYWORDS = ("research", "investigate", "benchmark", "pros and cons", "competitor", "literature", "debate")

    @classmethod
    def estimate(
        cls,
        goal: str,
        items: Sequence[str] | None = None,
        is_code: bool = False,
        files_count: int = 1,
        mode_hint: SwarmMode = SwarmMode.AUTO,
        max_concurrency_limit: int = 12,
    ) -> SwarmDecision:
        """Estimate whether a goal deserves a swarm and what mode to choose."""
        goal_lower = goal.lower()
        item_count = len(items) if items else 0

        # 1. Explicit Batch Workload (e.g. processing a list of items/URLs/queries)
        if item_count > 1:
            workers = min(item_count, max_concurrency_limit)
            serial_sec = item_count * 20.0
            rounds = math.ceil(item_count / workers)
            parallel_stage_sec = rounds * 20.0
            spawn_sec = workers * 0.4
            merge_sec = 3.0
            overhead_sec = spawn_sec + merge_sec

            total_parallel_sec = parallel_stage_sec + overhead_sec
            speedup = round(serial_sec / max(total_parallel_sec, 1.0), 2)
            net_benefit = serial_sec - total_parallel_sec

            should_swarm = item_count >= 3 and net_benefit > 10.0
            mode = SwarmMode.MAP_REDUCE if mode_hint in (SwarmMode.AUTO, SwarmMode.PARALLEL) else mode_hint

            reason = (
                f"Batch workload of {item_count} items. Deploying {workers} workers reduces critical path from {serial_sec:.0f}s to {total_parallel_sec:.0f}s ({speedup}x speedup, saving {net_benefit:.0f}s)."
                if should_swarm
                else f"Batch count of {item_count} is too small to overcome swarm spawn and merge overhead."
            )

            return SwarmDecision(
                should_swarm=should_swarm,
                mode=mode,
                reason=reason,
                estimated_serial_seconds=serial_sec,
                estimated_parallel_seconds=total_parallel_sec,
                estimated_speedup=speedup if should_swarm else 1.0,
                recommended_workers=workers if should_swarm else 1,
                estimated_overhead_seconds=overhead_sec,
            )

        # 2. Multi-File / Architectural Coding Workload
        has_code_signal = is_code or files_count >= 3 or any(kw in goal_lower for kw in cls.CODE_KEYWORDS)
        if has_code_signal and ("frontend" in goal_lower and "backend" in goal_lower or files_count >= 3):
            workers = min(max(files_count, 3), 6)
            serial_sec = 180.0
            parallel_stage_sec = 50.0
            overhead_sec = 15.0  # worktree provisioning + patch merge
            total_parallel_sec = parallel_stage_sec + overhead_sec
            speedup = round(serial_sec / total_parallel_sec, 2)

            return SwarmDecision(
                should_swarm=True,
                mode=SwarmMode.CODING_WORKTREE if mode_hint == SwarmMode.AUTO else mode_hint,
                reason=f"Multi-component development task identified. Worktree-isolated workers achieve estimated {speedup}x speedup.",
                estimated_serial_seconds=serial_sec,
                estimated_parallel_seconds=total_parallel_sec,
                estimated_speedup=speedup,
                recommended_workers=workers,
                estimated_overhead_seconds=overhead_sec,
            )

        # 3. Deep Research / Debate / Multi-Hypothesis Analysis
        if any(kw in goal_lower for kw in cls.RESEARCH_KEYWORDS) and ("vs" in goal_lower or "compare" in goal_lower or "debate" in goal_lower):
            workers = 4
            serial_sec = 120.0
            parallel_stage_sec = 35.0
            overhead_sec = 10.0
            total_parallel_sec = parallel_stage_sec + overhead_sec
            speedup = round(serial_sec / total_parallel_sec, 2)
            mode = SwarmMode.DEBATE if "debate" in goal_lower else SwarmMode.SCATTER_GATHER

            return SwarmDecision(
                should_swarm=True,
                mode=mode if mode_hint == SwarmMode.AUTO else mode_hint,
                reason=f"Multi-perspective analytical inquiry. Scatter-gather workers explore competing hypotheses concurrently ({speedup}x speedup).",
                estimated_serial_seconds=serial_sec,
                estimated_parallel_seconds=total_parallel_sec,
                estimated_speedup=speedup,
                recommended_workers=workers,
                estimated_overhead_seconds=overhead_sec,
            )

        # 4. Check for explicit batch wording (e.g. "research 20 companies", "scan 50 open source repositories")
        num_match = re.search(
            r"\b(\d+)\s+(?:[\w-]+\s+){0,4}(companies|repositories|repos|urls|websites|documents|files|endpoints|papers)\b",
            goal_lower,
        )
        if num_match:
            count = int(num_match.group(1))
            if count >= 3:
                workers = min(count, max_concurrency_limit)
                serial_sec = count * 18.0
                rounds = math.ceil(count / workers)
                parallel_stage_sec = rounds * 18.0
                overhead_sec = workers * 0.5 + 4.0
                total_parallel_sec = parallel_stage_sec + overhead_sec
                speedup = round(serial_sec / total_parallel_sec, 2)

                return SwarmDecision(
                    should_swarm=True,
                    mode=SwarmMode.MAP_REDUCE,
                    reason=f"Extracted {count} target entities from goal. Distributed map-reduce will achieve {speedup}x speedup across {workers} workers.",
                    estimated_serial_seconds=serial_sec,
                    estimated_parallel_seconds=total_parallel_sec,
                    estimated_speedup=speedup,
                    recommended_workers=workers,
                    estimated_overhead_seconds=overhead_sec,
                )

        # 5. Default: Single-Agent / Simple Sequential (Do Not Swarm)
        # Prevents fake parallelism on trivial or serial prompts
        return SwarmDecision(
            should_swarm=False,
            mode=SwarmMode.AUTO,
            reason="Goal appears focused and sequentially bounded. Swarm coordination overhead exceeds expected parallel gains; using single agent.",
            estimated_serial_seconds=15.0,
            estimated_parallel_seconds=15.0,
            estimated_speedup=1.0,
            recommended_workers=1,
            estimated_overhead_seconds=0.0,
        )
