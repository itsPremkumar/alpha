from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .scoring import EvaluationVector


@dataclass
class VersionRecord:
    """
    Immutable version record in the Autonomous AVO evolution lineage.
    Represents either a committed candidate x_i in P_t or an intermediate trajectory attempt.
    """
    version_id: str = field(default_factory=lambda: f"v_{uuid.uuid4().hex[:8]}")
    parent_id: str | None = None
    hypothesis: str = ""
    modification: str = ""
    correctness: bool = False
    performance_score: float = 0.0  # Scalar compatibility
    quality_score: float = 0.0      # Scalar compatibility
    composite_score: float = 0.0    # Scalar weighted score or geomean
    vector: EvaluationVector | None = None
    git_hash: str | None = None
    diff_summary: str = ""
    trajectory_depth: int = 0
    rejection_reason: str | None = None
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.vector is None and (self.performance_score > 0 or self.quality_score > 0 or self.correctness):
            self.vector = EvaluationVector(
                correctness=self.correctness,
                metrics={
                    "performance": self.performance_score,
                    "quality": self.quality_score,
                },
                metadata=self.metadata,
            )
        elif self.vector is not None:
            self.correctness = self.vector.correctness
            if "performance" in self.vector.metrics:
                self.performance_score = self.vector.metrics["performance"]
            if "quality" in self.vector.metrics:
                self.quality_score = self.vector.metrics["quality"]

    def compute_composite(
        self,
        w_correctness: float = 0.5,
        w_performance: float = 0.3,
        w_quality: float = 0.2,
    ) -> float:
        if not self.correctness:
            self.composite_score = 0.0
            return 0.0

        if self.vector and len(self.vector.metrics) > 2:
            # Multi-dimensional vector: use geometric mean
            self.composite_score = self.vector.geometric_mean()
            return self.composite_score

        score = (
            w_correctness * 1.0
            + w_performance * min(1.0, self.performance_score)
            + w_quality * min(1.0, self.quality_score)
        )
        self.composite_score = round(score, 4)
        return self.composite_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "parent_id": self.parent_id,
            "hypothesis": self.hypothesis,
            "modification": self.modification,
            "correctness": self.correctness,
            "performance_score": self.performance_score,
            "quality_score": self.quality_score,
            "composite_score": self.composite_score,
            "vector": self.vector.to_dict() if self.vector else None,
            "git_hash": self.git_hash,
            "diff_summary": self.diff_summary,
            "trajectory_depth": self.trajectory_depth,
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


class AVOLineage:
    """
    Maintains historical evolution tree and strictly enforces
    Autonomous AVO's matches-or-improves commit policy.
    Unsuccessful intermediate attempts are archived in trajectory memory.
    """

    def __init__(self) -> None:
        self.versions: dict[str, VersionRecord] = {}
        self.rejected_attempts: list[VersionRecord] = []
        self.head_id: str | None = None

    def commit_candidate(self, candidate: VersionRecord) -> bool:
        """
        Autonomous AVO Matches-or-improves commit policy:
          - FAIL correctness -> discard & archive in internal trajectory
          - Worse score than parent -> reject & archive in internal trajectory
          - Matches or improves parent -> accept & commit to P_t
          - Strictly improves current head -> promote to new head
        """
        # Hard correctness gate: candidates that fail correctness receive zero score
        if not candidate.correctness:
            candidate.rejection_reason = "CORRECTNESS_FAILURE"
            self.rejected_attempts.append(candidate)
            return False

        candidate.compute_composite()

        if candidate.parent_id and candidate.parent_id in self.versions:
            parent = self.versions[candidate.parent_id]
            candidate.trajectory_depth = parent.trajectory_depth + 1

            # Check Pareto / scalar improvement
            if candidate.vector and parent.vector:
                if not candidate.vector.matches_or_improves(parent.vector):
                    candidate.rejection_reason = "REGRESSED_BELOW_PARENT_VECTOR"
                    self.rejected_attempts.append(candidate)
                    return False
            elif candidate.composite_score < parent.composite_score:
                candidate.rejection_reason = "REGRESSED_BELOW_PARENT_SCALAR"
                self.rejected_attempts.append(candidate)
                return False
        else:
            candidate.trajectory_depth = 0

        # Accepted into committed lineage P_t
        self.versions[candidate.version_id] = candidate

        # Update head if this is first version or strictly exceeds current head
        if self.head_id is None:
            self.head_id = candidate.version_id
        else:
            current_head = self.versions[self.head_id]
            if candidate.vector and current_head.vector:
                if candidate.vector.dominates(current_head.vector) or (
                    candidate.vector.geometric_mean() > current_head.vector.geometric_mean()
                ):
                    self.head_id = candidate.version_id
            elif candidate.composite_score > current_head.composite_score:
                self.head_id = candidate.version_id

        return True

    def get_version(self, version_id: str) -> VersionRecord | None:
        return self.versions.get(version_id)

    def get_head(self) -> VersionRecord | None:
        if not self.head_id:
            return None
        return self.versions.get(self.head_id)

    def get_history(self) -> list[VersionRecord]:
        return sorted(self.versions.values(), key=lambda v: v.created_at)

    def get_trajectory_archive(self) -> list[VersionRecord]:
        """Returns internal search trajectory including unsuccessful intermediate attempts."""
        all_attempts = list(self.versions.values()) + self.rejected_attempts
        return sorted(all_attempts, key=lambda v: v.created_at)

    def get_pareto_frontier(self) -> list[VersionRecord]:
        """Returns the non-dominated Pareto frontier of all committed versions."""
        committed = [v for v in self.versions.values() if v.vector is not None]
        if not committed:
            return [v for v in self.versions.values() if v.correctness]

        frontier: list[VersionRecord] = []
        for cand in committed:
            assert cand.vector is not None
            is_dominated = False
            for other in committed:
                if other.version_id != cand.version_id and other.vector is not None:
                    if other.vector.dominates(cand.vector):
                        is_dominated = True
                        break
            if not is_dominated and cand not in frontier:
                frontier.append(cand)
        return frontier

    def stats(self) -> dict[str, Any]:
        head = self.get_head()
        return {
            "total_committed": len(self.versions),
            "total_rejected": len(self.rejected_attempts),
            "total_explored": len(self.versions) + len(self.rejected_attempts),
            "head_id": self.head_id,
            "head_score": head.composite_score if head else 0.0,
            "pareto_frontier_size": len(self.get_pareto_frontier()),
        }
