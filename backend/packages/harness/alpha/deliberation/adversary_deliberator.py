"""Multi-Perspective Red-Team & Adversary Deliberator.

Actively stress-tests proposed architectures, Living Specifications, and API schemas.
Breaks multi-agent sycophancy by aggressively challenging optimistic assumptions,
identifying boundary edge cases, and formulating concrete failure-mode attack vectors.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class AdversaryAttackVector:
    """A concrete failure scenario identified during adversarial stress-testing."""

    name: str
    category: Literal["concurrency", "security", "scalability", "fault_tolerance"]
    scenario: str
    likelihood: Literal["low", "medium", "high"]
    impact: Literal["low", "medium", "high", "critical"]
    mitigation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AdversaryCritique:
    """The outcome of an adversarial deliberation run."""

    critique_id: str
    target_title: str
    sycophancy_flagged: bool
    assumptions_challenged: list[str] = field(default_factory=list)
    attack_vectors: list[AdversaryAttackVector] = field(default_factory=list)
    risk_score: float = 0.0
    recommendations: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["attack_vectors"] = [v.to_dict() for v in self.attack_vectors]
        return data


class AdversaryDeliberator:
    """Performs adversarial red-team stress testing on designs and specifications."""

    def stress_test_design(
        self,
        title: str,
        design_content: str,
        *,
        context_constraints: list[str] | None = None,
    ) -> AdversaryCritique:
        """Run red-team evaluation identifying failure surfaces and challenging assumptions."""
        critique_id = f"ADV-{uuid.uuid4().hex[:8].upper()}"
        d_lower = design_content.lower()

        assumptions: list[str] = []
        vectors: list[AdversaryAttackVector] = []
        recommendations: list[str] = []

        # 1. Concurrency & Contention Check
        has_anti_lock = any(neg in d_lower for neg in ("no lock", "without lock", "no mutex", "locks are not needed", "no need for lock", "no locks"))
        has_proper_sync = any(k in d_lower for k in ("acquire_lock", "distributed lock", "atomic transaction", "row_lock", "concurrency lock", "mutex lock"))
        if has_anti_lock or not has_proper_sync:
            assumptions.append("Assumes single-threaded or serial access without concurrency conflicts.")
            vectors.append(
                AdversaryAttackVector(
                    name="Concurrent Mutation Race Condition",
                    category="concurrency",
                    scenario="Two bots or workers update shared state simultaneously, overwriting changes.",
                    likelihood="high",
                    impact="high",
                    mitigation="Introduce resource lock leasing or optimistic revision counters.",
                )
            )
            recommendations.append("Explicitly specify mutex locking or versioned ETags on mutation endpoints.")

        # 2. Fault Tolerance & Network Partitions
        if not any(k in d_lower for k in ("retry", "timeout", "circuit breaker", "fallback", "failover")):
            assumptions.append("Assumes external downstream dependencies and networks never timeout.")
            vectors.append(
                AdversaryAttackVector(
                    name="Unbounded Hang on Network Timeout",
                    category="fault_tolerance",
                    scenario="External LLM provider or database hangs indefinitely on socket read.",
                    likelihood="high",
                    impact="critical",
                    mitigation="Enforce strict client socket timeouts and fallbacks.",
                )
            )
            recommendations.append("Add 10s request timeouts and fallback chains for downstream services.")

        # 3. Scalability & Memory Exhaustion
        if not any(k in d_lower for k in ("pagination", "limit", "budget", "truncate", "chunk")):
            assumptions.append("Assumes inputs and results fit into working memory indefinitely.")
            vectors.append(
                AdversaryAttackVector(
                    name="Unbounded In-Memory Payload Growth",
                    category="scalability",
                    scenario="Accumulating 10,000+ events or large diffs exhausts node memory.",
                    likelihood="medium",
                    impact="high",
                    mitigation="Enforce pagination (limit/offset) and ring-buffer log limits.",
                )
            )
            recommendations.append("Apply a hard maximum query limit (e.g. limit=100) on list endpoints.")

        # 4. Sycophancy Detection
        sycophancy = False
        if any(phrase in d_lower for phrase in ("foolproof", "cannot fail", "100% reliable", "perfect", "no risks")):
            sycophancy = True
            assumptions.append("Flagged unwarranted optimism: proposal claims zero failure modes.")
            recommendations.append("Replace optimistic claims with explicit error handling paths.")

        # Compute calculated risk score
        risk_score = round(min(1.0, len(vectors) * 0.25 + (0.2 if sycophancy else 0.0)), 2)

        return AdversaryCritique(
            critique_id=critique_id,
            target_title=title,
            sycophancy_flagged=sycophancy,
            assumptions_challenged=assumptions,
            attack_vectors=vectors,
            risk_score=risk_score,
            recommendations=recommendations,
        )


def get_adversary_deliberator() -> AdversaryDeliberator:
    return AdversaryDeliberator()
