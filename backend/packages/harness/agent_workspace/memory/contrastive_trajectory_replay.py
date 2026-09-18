"""Contrastive Trajectory Replay and Negative-Path Episodic Memory Engine.

Maintains a dual-contrastive episodic memory buffer of past task attempts storing tuples of:
(Failure Signature, Erroneous Hypothesis, Failed Patch, Winning Resolution).

Provides:
- Negative Constraint Injection: Retrieves known failed reasoning paths and formats them
  as explicit negative constraints ("Do NOT attempt approach X; known to fail with error Y").
- Cyclic Trap Detection: Prunes hallucination loops and prevents cyclical debugging traps.
- Winning Resolution Retrieval: Recommends proven fix strategies for identical failure signatures.

Architectural Invariants:
- Zero Human-in-the-Loop Blocking: 100% autonomous operation.
- Strict Enterprise Naming: Clean, professional, unbranded terminology.
- 100% English code, comments, docstrings, and diagnostics.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain.tools import tool

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryRecord:
    """Episodic memory record capturing failed hypotheses and proven resolutions."""

    record_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    task_id: str = "general_task"
    failure_signature: str = ""
    erroneous_hypothesis: str = ""
    failed_patch: str = ""
    winning_resolution: Optional[str] = None
    occurrence_count: int = 1
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ContrastiveTrajectoryReplay:
    """Episodic memory buffer for negative constraint injection and loop detection."""

    def __init__(self, persistence_path: Optional[Path] = None) -> None:
        self.persistence_path = persistence_path
        self.records: Dict[str, TrajectoryRecord] = {}
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        """Load persisted memory records from disk if configured."""
        if not self.persistence_path or not self.persistence_path.is_file():
            return
        try:
            data = json.loads(self.persistence_path.read_text(encoding="utf-8"))
            for item in data:
                rec = TrajectoryRecord(**item)
                self.records[rec.record_id] = rec
        except Exception as e:
            logger.warning("Failed to load contrastive memory from disk: %s", e)

    def _save_to_disk(self) -> None:
        """Persist memory records to disk."""
        if not self.persistence_path:
            return
        try:
            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
            serialized = [r.to_dict() for r in self.records.values()]
            self.persistence_path.write_text(json.dumps(serialized, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save contrastive memory to disk: %s", e)

    def _tokenize(self, text: str) -> Set[str]:
        """Convert text into normalized alphanumeric token set."""
        tokens = re.findall(r"[a-zA-Z0-9_\-\.]+", text.lower())
        return set(tokens)

    def _compute_similarity(self, query_tokens: Set[str], target_text: str) -> float:
        """Calculate Jaccard token overlap similarity."""
        if not query_tokens:
            return 0.0
        target_tokens = self._tokenize(target_text)
        if not target_tokens:
            return 0.0
        intersection = query_tokens.intersection(target_tokens)
        union = query_tokens.union(target_tokens)
        return len(intersection) / len(union) if union else 0.0

    def record_outcome(
        self,
        task_id: str,
        failure_signature: str,
        erroneous_hypothesis: str,
        failed_patch: str,
        winning_resolution: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TrajectoryRecord:
        """Store or update a trajectory outcome in the episodic buffer."""
        sig_tokens = self._tokenize(failure_signature)
        hyp_tokens = self._tokenize(erroneous_hypothesis)

        # Check for near-duplicate record to increment count
        for rec in self.records.values():
            if (
                self._compute_similarity(sig_tokens, rec.failure_signature) > 0.8
                and self._compute_similarity(hyp_tokens, rec.erroneous_hypothesis) > 0.8
            ):
                rec.occurrence_count += 1
                rec.timestamp = time.time()
                if winning_resolution and not rec.winning_resolution:
                    rec.winning_resolution = winning_resolution
                if metadata:
                    rec.metadata.update(metadata)
                self._save_to_disk()
                return rec

        new_rec = TrajectoryRecord(
            task_id=task_id,
            failure_signature=failure_signature,
            erroneous_hypothesis=erroneous_hypothesis,
            failed_patch=failed_patch,
            winning_resolution=winning_resolution,
            metadata=metadata or {},
        )
        self.records[new_rec.record_id] = new_rec
        self._save_to_disk()
        return new_rec

    def query_negative_constraints(
        self,
        query: str,
        failure_signature: Optional[str] = None,
        top_k: int = 5,
        similarity_threshold: float = 0.15,
    ) -> List[Dict[str, Any]]:
        """Retrieve matching failed paths and format them as explicit negative constraints."""
        query_tokens = self._tokenize(query)
        sig_tokens = self._tokenize(failure_signature or "")

        scored: List[Tuple[float, TrajectoryRecord]] = []
        for rec in self.records.values():
            score_q = self._compute_similarity(query_tokens, rec.erroneous_hypothesis + " " + rec.failure_signature)
            score_s = self._compute_similarity(sig_tokens, rec.failure_signature) if failure_signature else 0.0
            total_score = max(score_q, score_s)

            if total_score >= similarity_threshold:
                scored.append((total_score, rec))

        scored.sort(key=lambda x: x[0], reverse=True)
        results: List[Dict[str, Any]] = []

        for sim, rec in scored[:top_k]:
            patch_preview = rec.failed_patch.strip()
            if len(patch_preview) > 120:
                patch_preview = patch_preview[:120] + "..."

            constraint_text = (
                f"NEGATIVE CONSTRAINT: Do NOT attempt approach '{rec.erroneous_hypothesis}'. "
                f"Previous attempt encountered failure signature '{rec.failure_signature}' "
                f"with failed patch: [{patch_preview}]."
            )
            item: Dict[str, Any] = {
                "record_id": rec.record_id,
                "similarity_score": round(sim, 4),
                "negative_constraint": constraint_text,
                "erroneous_hypothesis": rec.erroneous_hypothesis,
                "failure_signature": rec.failure_signature,
                "occurrence_count": rec.occurrence_count,
            }
            if rec.winning_resolution:
                item["proven_winning_resolution"] = (
                    f"RECOMMENDED RESOLUTION: {rec.winning_resolution}"
                )
            results.append(item)

        return results

    def detect_cyclic_trap(
        self, proposed_hypothesis: str, proposed_patch: Optional[str] = None
    ) -> Dict[str, Any]:
        """Detect if agent is about to repeat a known failed reasoning path."""
        hyp_tokens = self._tokenize(proposed_hypothesis)
        patch_tokens = self._tokenize(proposed_patch or "") if proposed_patch else set()

        for rec in self.records.values():
            sim_h = self._compute_similarity(hyp_tokens, rec.erroneous_hypothesis)
            sim_p = self._compute_similarity(patch_tokens, rec.failed_patch) if proposed_patch else 0.0

            if sim_h >= 0.75 or sim_p >= 0.75:
                return {
                    "is_cyclic_trap": True,
                    "matched_record_id": rec.record_id,
                    "previous_failure_signature": rec.failure_signature,
                    "warning": (
                        f"CYCLIC TRAP DETECTED: Proposed action matches previously failed hypothesis "
                        f"'{rec.erroneous_hypothesis}' (similarity: {max(sim_h, sim_p):.2f}). Pivot required."
                    ),
                    "suggested_winning_resolution": rec.winning_resolution,
                }

        return {
            "is_cyclic_trap": False,
            "warning": None,
            "suggested_winning_resolution": None,
        }


# Global memory manager instance
_GLOBAL_CONTRASTIVE_MEMORY: Optional[ContrastiveTrajectoryReplay] = None


def get_contrastive_memory() -> ContrastiveTrajectoryReplay:
    """Singleton access to the contrastive episodic memory engine."""
    global _GLOBAL_CONTRASTIVE_MEMORY
    if _GLOBAL_CONTRASTIVE_MEMORY is None:
        storage_path = Path(".agent-workspace") / "contrastive_memory.json"
        _GLOBAL_CONTRASTIVE_MEMORY = ContrastiveTrajectoryReplay(persistence_path=storage_path)
    return _GLOBAL_CONTRASTIVE_MEMORY


@tool("query_contrastive_memory", parse_docstring=True)
def query_contrastive_memory(
    query: str,
    failure_signature: Optional[str] = None,
    top_k: int = 5,
) -> Dict[str, Any]:
    """Retrieve negative constraints and failed reasoning paths from episodic memory.

    Searches past task executions to find similar failure signatures, erroneous hypotheses,
    and cyclical debugging traps. Automatically injects negative constraints into context
    to prevent repeating previously failed strategies.

    Args:
        query: Problem description, task goal, or planned hypothesis to search against.
        failure_signature: Optional stack trace, exception message, or compiler error.
        top_k: Maximum number of negative constraints to return (default 5).

    Returns:
        Structured dictionary containing matched negative constraints and winning resolutions.
    """
    try:
        mem = get_contrastive_memory()
        constraints = mem.query_negative_constraints(
            query=query,
            failure_signature=failure_signature,
            top_k=max(1, min(top_k, 20)),
        )
        return {
            "success": True,
            "data": {
                "matched_constraints_count": len(constraints),
                "negative_constraints": constraints,
                "summary": (
                    f"Retrieved {len(constraints)} relevant negative constraints from contrastive memory."
                    if constraints
                    else "No prior negative trajectories matched query."
                ),
            },
        }
    except Exception as e:
        logger.exception("Error querying contrastive memory")
        return {
            "success": False,
            "data": {
                "error": str(e),
                "matched_constraints_count": 0,
                "negative_constraints": [],
            },
        }


@tool("record_trajectory_outcome", parse_docstring=True)
def record_trajectory_outcome(
    task_id: str,
    failure_signature: str,
    erroneous_hypothesis: str,
    failed_patch: str,
    winning_resolution: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Record an episodic trajectory outcome into contrastive memory.

    Stores tuples of (Failure Signature, Erroneous Hypothesis, Failed Patch, Winning Resolution)
    to enable future negative constraint injection and prevent cyclical reasoning traps.

    Args:
        task_id: Identifier of the task or debugging session.
        failure_signature: Specific exception, assertion failure, or error message.
        erroneous_hypothesis: False assumption or hypothesis that led to the failure.
        failed_patch: Code modification or action that failed validation.
        winning_resolution: Optional patch or strategy that successfully resolved the issue.
        metadata: Optional dictionary of supplementary contextual information.

    Returns:
        Structured dictionary with record ID and confirmation status.
    """
    try:
        mem = get_contrastive_memory()
        rec = mem.record_outcome(
            task_id=task_id,
            failure_signature=failure_signature,
            erroneous_hypothesis=erroneous_hypothesis,
            failed_patch=failed_patch,
            winning_resolution=winning_resolution,
            metadata=metadata,
        )
        return {
            "success": True,
            "data": {
                "record_id": rec.record_id,
                "task_id": rec.task_id,
                "occurrence_count": rec.occurrence_count,
                "has_winning_resolution": bool(rec.winning_resolution),
                "status": "recorded_successfully",
            },
        }
    except Exception as e:
        logger.exception("Error recording trajectory outcome")
        return {
            "success": False,
            "data": {
                "error": str(e),
                "task_id": task_id,
            },
        }
