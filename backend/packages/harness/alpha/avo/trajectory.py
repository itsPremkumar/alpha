"""The lineage as the variation step's context.

AVO's ``P_t`` is a first-class object that the agent consults, and the paper is
specific about what consulting it buys: the agent *examines multiple prior
implementations within a single step, comparing their profiling characteristics
to identify bottlenecks*, and later steps *shift toward micro-architectural
tuning guided by patterns observed across the accumulated lineage*.

That requires three things a flat "here is the current head" context cannot give:

* every attempt, including the rejected ones, with **why** it was rejected
* the metrics of each attempt side by side, so characteristics can be compared
* the best committed version, explicitly separated from the head

:class:`TrajectoryView` is that view. It is built from an
:class:`~alpha.avo.lineage.AVOLineage` and handed to the variation step as its
context, so the agent can revisit an earlier approach instead of starting blind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evidence import record_score

__all__ = ["AttemptView", "TrajectoryView"]


@dataclass(frozen=True)
class AttemptView:
    """One explored direction and what became of it."""

    version_id: str
    parent_id: str | None
    hypothesis: str
    modification: str
    committed: bool
    score: float
    metrics: dict[str, float]
    task_id: str
    rejection_reason: str | None
    rejection_category: str | None
    change_kind: str | None
    invariant_scope: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "parent_id": self.parent_id,
            "hypothesis": self.hypothesis,
            "modification": self.modification,
            "committed": self.committed,
            "score": self.score,
            "metrics": self.metrics,
            "task_id": self.task_id,
            "rejection_reason": self.rejection_reason,
            "rejection_category": self.rejection_category,
            "change_kind": self.change_kind,
            "invariant_scope": self.invariant_scope,
        }


@dataclass
class TrajectoryView:
    """Everything the variation step is allowed to know about what has been tried."""

    committed: list[AttemptView] = field(default_factory=list)
    rejected: list[AttemptView] = field(default_factory=list)
    best_committed_id: str | None = None
    best_committed_score: float = 0.0
    head_id: str | None = None
    task_id: str = "all"

    @classmethod
    def from_lineage(cls, lineage: Any, *, limit: int = 20, task_id: str | None = None) -> TrajectoryView:
        """Build the view. Reads committed versions and the rejected trajectory.

        ``task_id`` scopes the view to one benchmark. Passing ``None`` shows
        everything, which is right for an audit and wrong for a variation step --
        a search should not be steering itself with another benchmark's history.
        """
        committed = [_attempt(r, True) for r in getattr(lineage, "versions", {}).values() if task_id is None or r.task_id == task_id]
        rejected = [_attempt(r, False) for r in getattr(lineage, "rejected_attempts", []) if task_id is None or r.task_id == task_id]
        best = lineage.best_committed(task_id) if hasattr(lineage, "best_committed") else None
        return cls(
            committed=sorted(committed, key=lambda a: a.score, reverse=True)[:limit],
            rejected=sorted(rejected, key=lambda a: a.score, reverse=True)[:limit],
            best_committed_id=getattr(best, "version_id", None),
            best_committed_score=record_score(best) if best is not None else 0.0,
            head_id=getattr(lineage, "head_id", None),
            task_id=task_id or "all",
        )

    # -- what the agent gets -------------------------------------------
    def tried_modifications(self) -> list[str]:
        """The concrete edits already attempted, so the agent can avoid repeating one."""
        return [a.modification for a in (*self.committed, *self.rejected) if a.modification]

    def failure_reasons(self) -> dict[str, int]:
        """Why attempts failed, counted by category. Lets the agent see what is closed off."""
        counts: dict[str, int] = {}
        for attempt in self.rejected:
            key = attempt.rejection_category or "uncategorised"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def frontier(self) -> list[AttemptView]:
        """The best attempt per distinct modification shape -- the things worth comparing."""
        seen: set[str] = set()
        out: list[AttemptView] = []
        for attempt in sorted(self.committed, key=lambda a: a.score, reverse=True):
            if attempt.modification in seen:
                continue
            seen.add(attempt.modification)
            out.append(attempt)
        return out[:5]

    def to_agent_context(self) -> dict[str, Any]:
        """The context block handed to the variation step.

        Deliberately includes the rejected attempts and the best-committed
        reference, not just the head. The point of consulting the lineage is to
        let the agent compare and revisit; a context containing only the head
        would make that impossible.
        """
        return {
            "explored_count": len(self.committed) + len(self.rejected),
            "committed_count": len(self.committed),
            "rejected_count": len(self.rejected),
            "task_id": self.task_id,
            "head_id": self.head_id,
            "best_committed_id": self.best_committed_id,
            "best_committed_score": self.best_committed_score,
            "tried_modifications": self.tried_modifications(),
            "failure_reasons": self.failure_reasons(),
            "frontier": [a.to_dict() for a in self.frontier()],
            "recent_rejected": [a.to_dict() for a in self.rejected[:5]],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "head_id": self.head_id,
            "best_committed_id": self.best_committed_id,
            "best_committed_score": self.best_committed_score,
            "committed": [a.to_dict() for a in self.committed],
            "rejected": [a.to_dict() for a in self.rejected],
        }


def _attempt(record: Any, committed: bool) -> AttemptView:
    vector = getattr(record, "vector", None)
    metadata = getattr(record, "metadata", None) or {}
    return AttemptView(
        version_id=getattr(record, "version_id", "unknown"),
        parent_id=getattr(record, "parent_id", None),
        hypothesis=str(getattr(record, "hypothesis", "")),
        modification=str(getattr(record, "modification", "")),
        committed=committed,
        metrics=dict(getattr(vector, "metrics", {}) or {}) if vector else {},
        score=record_score(record),
        task_id=str(getattr(record, "task_id", "default")),
        rejection_reason=getattr(record, "rejection_reason", None),
        rejection_category=metadata.get("rejection_category"),
        change_kind=metadata.get("change_kind"),
        invariant_scope=metadata.get("invariant_scope"),
    )
