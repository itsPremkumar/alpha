from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .evidence import RejectionCategory, RejectionReason, record_score
from .lineage_chain import ChainVerdict, chain_head, seal_entry, verify_entries
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
    #: The benchmark namespace this version's score belongs to.
    #:
    #: Scores are only commensurable within one benchmark. Comparing a score on
    #: task B against the best committed score on task A is a category error, and
    #: a lineage that does it will permanently refuse every candidate on B once A
    #: has a high number. AVO's "best committed version so far" means so far
    #: *within the same benchmark suite*; this field is what makes that true here.
    task_id: str = "default"
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
            "task_id": self.task_id,
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
        #: Append-only, tamper-evident chain over the committed lineage. Each link
        #: covers its own payload and the previous link, so editing or removing a
        #: committed version is detectable. See :mod:`alpha.avo.lineage_chain`.
        self.chain: list[dict[str, Any]] = []

    def best_committed(self, task_id: str | None = None) -> VersionRecord | None:
        """The highest-scoring committed version, or ``None`` if nothing is committed.

        This is the comparison target AVO's commit rule names: *"matches or
        improves the benchmark score relative to the best committed version so
        far"*. It is deliberately **not** the parent. A candidate may beat its
        immediate parent and still be worse than the best committed version, and
        in that case it is a regression and is rejected.

        ``task_id`` scopes the search to one benchmark. Scores from different
        benchmarks are not commensurable, so comparing across them would reject
        correct work forever.
        """
        best: VersionRecord | None = None
        best_score = float("-inf")
        for record in self.versions.values():
            if task_id is not None and record.task_id != task_id:
                continue
            score = record_score(record)
            if score > best_score:
                best, best_score = record, score
        return best

    def commit_candidate(self, candidate: VersionRecord) -> bool:
        """
        Autonomous AVO Matches-or-improves commit policy:
          - FAIL correctness -> discard & archive in internal trajectory
          - Worse score than the BEST COMMITTED version on the same benchmark -> reject & archive
          - Matches or improves it -> accept & commit to P_t
          - Strictly improves current head -> promote to new head

        Note on the comparison target: this used to compare against
        ``candidate.parent_id``. That is not the rule -- a version that improves
        on its immediate parent but is worse than an earlier commit is a
        regression, and admitting it lets the committed lineage ratchet
        downwards one local step at a time. The reference is
        :meth:`best_committed`, scoped to the candidate's ``task_id``.
        """
        # Hard correctness gate: candidates that fail correctness receive zero score
        if not candidate.correctness:
            self._reject(candidate, RejectionReason.CORRECTNESS_FAILURE)
            return False

        candidate.compute_composite()

        best = self.best_committed(candidate.task_id)
        if candidate.parent_id and candidate.parent_id in self.versions:
            candidate.trajectory_depth = self.versions[candidate.parent_id].trajectory_depth + 1
        else:
            candidate.trajectory_depth = 0

        if best is not None:
            reference_id = best.version_id
            if candidate.vector and best.vector and len(candidate.vector.metrics) > 1 and len(best.vector.metrics) > 1:
                if not candidate.vector.matches_or_improves(best.vector):
                    self._reject(candidate, RejectionReason.SCORE_REGRESSION, reference=reference_id)
                    return False
            elif record_score(candidate) < record_score(best) - 1e-12:
                self._reject(candidate, RejectionReason.SCORE_REGRESSION, reference=reference_id)
                return False

        # Accepted into committed lineage P_t
        self.versions[candidate.version_id] = candidate

        # Update head if this is the first version for this benchmark, or if it
        # strictly exceeds the current head *on the same benchmark*.
        current_head = self.versions.get(self.head_id) if self.head_id else None
        if current_head is None or current_head.task_id != candidate.task_id:
            self.head_id = candidate.version_id
        elif candidate.vector and current_head.vector:
            if candidate.vector.dominates(current_head.vector) or (
                candidate.vector.geometric_mean() > current_head.vector.geometric_mean()
            ):
                self.head_id = candidate.version_id
        elif candidate.composite_score > current_head.composite_score:
            self.head_id = candidate.version_id

        self.seal(candidate)
        return True

    def _reject(self, candidate: VersionRecord, reason: RejectionReason, *, reference: str | None = None) -> None:
        """Record a failed attempt in the trajectory with a machine-readable reason.

        The attempt is kept. A loop that only remembers successes cannot learn
        from its failures, so the rejection is a first-class record rather than a
        dropped candidate.
        """
        candidate.rejection_reason = reason.value
        candidate.metadata["rejection_category"] = reason.category.value
        if reference is not None:
            candidate.metadata["compared_against"] = reference
        self.rejected_attempts.append(candidate)

    # ------------------------------------------------------------------
    # gate-aware paths
    # ------------------------------------------------------------------
    def seal(self, record: VersionRecord) -> dict[str, Any]:
        """Append one committed version to the tamper-evident chain."""
        entry = seal_entry(
            record.to_dict(),
            seq=len(self.chain) + 1,
            prev_hash=chain_head(self.chain),
        )
        self.chain.append(entry)
        return entry

    def commit_record(self, record: VersionRecord, decision: Any) -> bool:
        """Commit a candidate that :class:`~alpha.avo.commit_gate.CommitGate` accepted.

        Unlike :meth:`commit_candidate` this path does not re-derive the verdict:
        the gate already applied correctness, invariants and the best-committed
        comparison. Re-deriving them here would be a second, weaker, unaudited
        rule, so the gate's decision is the one that is recorded.
        """
        record.metadata["gate_fingerprint"] = getattr(decision, "gate_fingerprint", "")
        record.metadata["gate_version"] = getattr(decision, "gate_version", 0)
        record.metadata["invariant_scope"] = getattr(decision, "invariant_scope", "not_run")
        record.metadata["change_kind"] = getattr(getattr(decision, "change_kind", None), "value", None)
        record.metadata["author"] = getattr(decision, "author", "unknown")
        record.rejection_reason = None
        record.compute_composite()
        self.versions[record.version_id] = record

        previous_best = self.best_committed(record.task_id)
        if previous_best is None or record_score(record) >= record_score(previous_best) - 1e-12:
            current = self.versions.get(self.head_id)
            if current is None or current.task_id != record.task_id or record_score(record) >= record_score(current) - 1e-12:
                self.head_id = record.version_id
        self.seal(record)
        return True

    def record_trajectory(self, record: VersionRecord, decision: Any) -> None:
        """Record a candidate the gate refused, with the reason and its category."""
        record.metadata["gate_fingerprint"] = getattr(decision, "gate_fingerprint", "")
        record.metadata["invariant_scope"] = getattr(decision, "invariant_scope", "not_run")
        record.metadata["author"] = getattr(decision, "author", "unknown")
        record.metadata["compared_against"] = getattr(decision, "best_committed_id", None)
        category = getattr(decision, "category", None) or RejectionCategory.ABANDONED
        record.metadata["rejection_category"] = getattr(category, "value", str(category))
        record.metadata["rejection_detail"] = getattr(decision, "reason", "")
        record.rejection_reason = getattr(decision, "reason_code", None) or getattr(decision, "reason", "rejected")
        self.rejected_attempts.append(record)

    def verify_chain(self) -> ChainVerdict:
        """Verify the committed lineage has not been edited after the fact."""
        return verify_entries(self.chain)

    def rejection_breakdown(self) -> dict[str, int]:
        """Rejections counted by category, so the trajectory can be read at a glance."""
        counts: dict[str, int] = {}
        for record in self.rejected_attempts:
            key = str(record.metadata.get("rejection_category") or "uncategorised")
            counts[key] = counts.get(key, 0) + 1
        return counts

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
        best = self.best_committed()
        chain = self.verify_chain()
        return {
            "total_committed": len(self.versions),
            "total_rejected": len(self.rejected_attempts),
            "total_explored": len(self.versions) + len(self.rejected_attempts),
            "head_id": self.head_id,
            "head_score": head.composite_score if head else 0.0,
            "best_committed_id": best.version_id if best else None,
            "best_committed_score": record_score(best) if best else 0.0,
            "pareto_frontier_size": len(self.get_pareto_frontier()),
            "rejections_by_category": self.rejection_breakdown(),
            "chain_length": len(self.chain),
            "chain_intact": chain.ok,
            "chain_defects": list(chain.defects),
        }
