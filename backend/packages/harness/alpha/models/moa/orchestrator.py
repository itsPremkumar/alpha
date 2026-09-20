"""Mixture-of-Agents parallel fan-out and consensus aggregator engine."""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Callable
from dataclasses import dataclass

from alpha.models.moa.redact import redact_pii_and_secrets


@dataclass
class MoACandidate:
    model_name: str
    response: str = ""
    duration_ms: float = 0.0
    error: str | None = None
    success: bool = True


@dataclass
class MoAResult:
    prompt: str
    candidates: list[MoACandidate]
    consensus_response: str
    total_duration_ms: float = 0.0


class MoAOrchestrator:
    """Dispatches reasoning tasks to parallel candidate models and aggregates consensus."""

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers

    def execute_moa_round(
        self,
        prompt: str,
        candidate_models: list[str],
        worker_fn: Callable[[str, str], str],
        aggregator_fn: Callable[[str, list[MoACandidate]], str] | None = None,
    ) -> MoAResult:
        """Run parallel candidate generation followed by consensus synthesis."""
        start_t = time.perf_counter()
        clean_prompt = redact_pii_and_secrets(prompt)
        candidates: list[MoACandidate] = []

        # 1. Parallel candidate dispatch
        def _invoke_worker(m_name: str) -> MoACandidate:
            t0 = time.perf_counter()
            try:
                raw_ans = worker_fn(m_name, clean_prompt)
                clean_ans = redact_pii_and_secrets(raw_ans)
                elapsed = (time.perf_counter() - t0) * 1000.0
                return MoACandidate(model_name=m_name, response=clean_ans, duration_ms=elapsed, success=True)
            except Exception as e:
                elapsed = (time.perf_counter() - t0) * 1000.0
                return MoACandidate(model_name=m_name, error=str(e), duration_ms=elapsed, success=False)

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(self.max_workers, len(candidate_models) or 1)) as pool:
            futures = [pool.submit(_invoke_worker, m) for m in candidate_models]
            for fut in concurrent.futures.as_completed(futures):
                candidates.append(fut.result())

        # Sort candidates deterministically by model name
        candidates.sort(key=lambda c: c.model_name)

        # 2. Consensus synthesis
        if aggregator_fn:
            consensus = aggregator_fn(clean_prompt, candidates)
        else:
            # Default synthesis heuristic
            successful = [c for c in candidates if c.success]
            if not successful:
                consensus = "Error: All MoA candidate models failed."
            else:
                blocks = [f"### Perspective from `{c.model_name}`:\n{c.response.strip()}" for c in successful]
                consensus = f"## Synthesized MoA Consensus ({len(successful)} models):\n\n" + "\n\n".join(blocks)

        total_time = (time.perf_counter() - start_t) * 1000.0
        return MoAResult(
            prompt=clean_prompt,
            candidates=candidates,
            consensus_response=consensus,
            total_duration_ms=total_time,
        )


_global_moa = MoAOrchestrator()


def get_moa_orchestrator() -> MoAOrchestrator:
    return _global_moa
