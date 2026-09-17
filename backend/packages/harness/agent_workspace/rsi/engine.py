"""RSIEngine: Recursive Self-Improvement closed-loop engine."""

from __future__ import annotations

import logging
from typing import Any

from agent_workspace.rsi.models import (
    ABTestResult,
    HoldoutResult,
    RSICandidate,
    RSIHypothesis,
    RSIResult,
    RSIStage,
)

logger = logging.getLogger(__name__)


class RSIEngine:
    """Orchestrates closed-loop autonomous self-improvement cycles."""

    def __init__(self):
        self.stage = RSIStage.IDLE
        self.active_configurations: dict[str, dict[str, Any]] = {
            "compaction": {"max_budget_chars": 100_000, "keep_last_observations": 4},
            "tool_router": {"retry_limit": 3, "timeout_seconds": 30},
            "context_pruner": {"strip_threshold": 500},
        }

    def run_rsi_cycle(
        self,
        bottleneck: str,
        target_component: str = "compaction",
        force_promote: bool = False,
    ) -> RSIResult:
        """Generate a candidate and simulated evaluation preview without activation."""
        self.stage = RSIStage.BOTTLENECK_DETECTED

        # 1. Generate hypothesis
        hypothesis = self._generate_hypothesis(bottleneck, target_component)
        self.stage = RSIStage.HYPOTHESIS_GENERATED

        # 2. Synthesize candidate modification
        candidate = self._create_candidate(hypothesis)
        self.stage = RSIStage.CANDIDATE_CREATED

        # 3. Execute A/B test simulation
        ab_test = self._run_ab_test(candidate)
        self.stage = RSIStage.AB_TEST_RUNNING

        # 4. Evaluate against holdout regression suite
        holdout = self._run_holdout_evaluation(candidate, ab_test)
        self.stage = RSIStage.HOLDOUT_EVALUATION

        self.stage = RSIStage.PREVIEW
        evidence = [
            f"Bottleneck supplied: '{bottleneck}'",
            f"Proposed configuration: {candidate.modified_config}",
            f"Simulated A/B scores: baseline={ab_test.baseline_score} -> candidate={ab_test.candidate_score}",
            f"Simulated holdout score: {holdout.score}; no regression suite executed.",
            "Promotion blocked: preview evidence is not release evidence; no runtime configuration was changed.",
        ]
        if force_promote:
            evidence.append("force_promote cannot bypass evidence or deployment requirements.")
        self._last_summary = evidence[-1]
        return RSIResult(
            promoted=False,
            stage=self.stage,
            hypothesis=hypothesis,
            candidate=candidate,
            ab_test=ab_test,
            holdout=holdout,
            evidence=evidence,
            evidence_kind="simulated",
        )

    def _generate_hypothesis(self, bottleneck: str, target_component: str) -> RSIHypothesis:
        return RSIHypothesis(
            bottleneck=bottleneck,
            description=f"Mitigate bottleneck '{bottleneck[:60]}' by tuning {target_component} parameters.",
            expected_improvement=0.20,
            target_component=target_component,
        )

    def _create_candidate(self, hypothesis: RSIHypothesis) -> RSICandidate:
        comp = hypothesis.target_component
        orig = dict(self.active_configurations.get(comp, {"param": 10}))
        modified = dict(orig)

        # Optimize based on component
        if comp == "compaction":
            modified["max_budget_chars"] = int(orig.get("max_budget_chars", 100_000) * 0.8)
            modified["keep_last_observations"] = max(2, orig.get("keep_last_observations", 4) - 1)
        elif comp == "tool_router":
            modified["timeout_seconds"] = min(60, orig.get("timeout_seconds", 30) + 15)
        else:
            modified["param"] = orig.get("param", 10) + 5

        return RSICandidate(
            hypothesis_id=hypothesis.id,
            component=comp,
            original_config=orig,
            modified_config=modified,
        )

    def _run_ab_test(self, candidate: RSICandidate) -> ABTestResult:
        # Simulated empirical benchmark comparison
        baseline = 0.72
        candidate_score = 0.88
        improved = candidate_score > baseline
        return ABTestResult(
            candidate_id=candidate.id,
            baseline_score=baseline,
            candidate_score=candidate_score,
            improved=improved,
            confidence=0.92,
            latency_delta_ms=-140.0,
            evidence_kind="simulated",
        )

    def _run_holdout_evaluation(self, candidate: RSICandidate, ab_test: ABTestResult) -> HoldoutResult:
        baseline_holdout = 0.80
        holdout_score = 0.89
        return HoldoutResult(
            candidate_id=candidate.id,
            improved=holdout_score > baseline_holdout,
            regressed=False,
            score=holdout_score,
            baseline_score=baseline_holdout,
            evidence=["Simulated holdout preview; no regression benchmarks were executed."],
            evidence_kind="simulated",
        )

    def get_status(self) -> dict[str, Any]:
        """Return status dictionary for UI and API hydration."""
        return {
            "stage": self.stage.value,
            "active_configurations": dict(self.active_configurations),
            "last_cycle_summary": getattr(self, "_last_summary", "RSI system stable and monitoring for bottleneck signatures."),
        }


_RSI_ENGINES: dict[str, RSIEngine] = {}


def get_rsi_engine(project_id: str = "default") -> RSIEngine:
    """Project-scoped singleton accessor for RSIEngine."""
    if project_id not in _RSI_ENGINES:
        _RSI_ENGINES[project_id] = RSIEngine()
    return _RSI_ENGINES[project_id]
