"""Durable Persistence Manager for Autonomous AVO Lineage (P_t) and Knowledge Base (K).

Saves and restores evolutionary lineage trees, Pareto frontiers, and domain knowledge
to disk (.avo/ directory) so long-horizon optimization runs can persist and resume
across sessions without state loss.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .knowledge import DomainKnowledgeBase, KnowledgeEntry
from .lineage import AVOLineage, VersionRecord
from .scoring import EvaluationVector

logger = logging.getLogger("alpha.avo.persistence")

DEFAULT_AVO_DIR = Path(".avo")


class LineageIntegrityError(RuntimeError):
    """Raised when a persisted lineage fails chain verification on load.

    Deliberately *not* swallowed. The previous behaviour -- log a warning and
    return ``None`` -- would let a caller substitute an empty lineage for a
    tampered one, which is precisely the "assert a state it never measured"
    failure this package exists to prevent.
    """


class AVOPersistenceManager:
    """Manages disk persistence for AVO Lineage and Domain Knowledge."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir).resolve() if base_dir else Path.cwd().resolve()
        self.avo_dir = self.base_dir / DEFAULT_AVO_DIR

    def _target(self, filename: str) -> Path:
        if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename or ":" in filename or filename in (".", ".."):
            raise ValueError("Invalid AVO persistence filename")
        target = self.avo_dir / filename
        if self.avo_dir.resolve() != self.avo_dir or target.resolve() != target:
            raise ValueError("AVO persistence path escapes storage directory")
        return target

    @staticmethod
    def _write(target: Path, payload: dict[str, Any]) -> None:
        from alpha.agents.memory.backends.deermem.deermem.core.storage import _atomic_write

        _atomic_write(target, json.dumps(payload, indent=2, allow_nan=False).encode("utf-8"))

    def save_lineage(self, lineage: AVOLineage, filename: str = "lineage.json") -> Path:
        """Serialize complete AVOLineage tree and head state to disk."""
        target = self._target(filename)

        versions_data: dict[str, Any] = {}
        for vid, v in lineage.versions.items():
            versions_data[vid] = v.to_dict()

        rejected_data = [t.to_dict() for t in getattr(lineage, "rejected_attempts", [])]

        payload = {
            "head_id": lineage.head_id,
            "root_id": getattr(lineage, "root_id", None),
            "committed_count": len(lineage.versions),
            "total_evaluations": len(lineage.versions) + len(rejected_data),
            "versions": versions_data,
            "rejected_attempts": rejected_data,
            "chain": list(getattr(lineage, "chain", []) or []),
        }

        self._write(target, payload)

        logger.info("Persisted AVO Lineage (%d versions) to %s", len(versions_data), target)
        return target

    def load_lineage(self, filename: str = "lineage.json") -> AVOLineage | None:
        """Load an AVOLineage from disk if present."""
        target = self._target(filename)
        if not target.exists():
            return None

        try:
            with open(target, encoding="utf-8") as f:
                data = json.load(f)

            lineage = AVOLineage()
            lineage.head_id = data.get("head_id")

            versions: dict[str, VersionRecord] = {}
            for vid, vdata in data.get("versions", {}).items():
                vector = None
                if vdata.get("vector"):
                    v_dict = vdata["vector"]
                    vector = EvaluationVector(
                        metrics=v_dict.get("metrics", {}),
                        correctness=v_dict.get("correctness", True),
                        metadata=v_dict.get("metadata", {}),
                    )

                record = VersionRecord(
                    version_id=vdata["version_id"],
                    parent_id=vdata.get("parent_id"),
                    hypothesis=vdata.get("hypothesis", ""),
                    modification=vdata.get("modification", ""),
                    correctness=vdata.get("correctness", False),
                    performance_score=vdata.get("performance_score", 0.0),
                    quality_score=vdata.get("quality_score", 0.0),
                    composite_score=vdata.get("composite_score", 0.0),
                    vector=vector,
                    git_hash=vdata.get("git_hash"),
                    diff_summary=vdata.get("diff_summary", ""),
                    trajectory_depth=vdata.get("trajectory_depth", 0),
                    rejection_reason=vdata.get("rejection_reason"),
                    created_at=vdata.get("created_at", 0.0),
                    metadata=vdata.get("metadata", {}),
                )
                versions[vid] = record

            lineage.versions = versions

            rejected: list[VersionRecord] = []
            for rdata in data.get("rejected_attempts", []):
                rec = VersionRecord(
                    version_id=rdata["version_id"],
                    parent_id=rdata.get("parent_id"),
                    hypothesis=rdata.get("hypothesis", ""),
                    modification=rdata.get("modification", ""),
                    correctness=rdata.get("correctness", False),
                    performance_score=rdata.get("performance_score", 0.0),
                    quality_score=rdata.get("quality_score", 0.0),
                    composite_score=rdata.get("composite_score", 0.0),
                    git_hash=rdata.get("git_hash"),
                    diff_summary=rdata.get("diff_summary", ""),
                    trajectory_depth=rdata.get("trajectory_depth", 0),
                    rejection_reason=rdata.get("rejection_reason"),
                    created_at=rdata.get("created_at", 0.0),
                    metadata=rdata.get("metadata", {}),
                )
                rejected.append(rec)
            lineage.rejected_attempts = rejected

            # Restore the append-only chain and refuse a lineage whose committed
            # history has been edited after the fact. A lineage is only
            # trustworthy if its history verifies, and a caller that silently
            # accepted a mutated one would be reporting an unmeasured state.
            chain = data.get("chain") or []
            lineage.chain = chain
            verdict = lineage.verify_chain()
            if not verdict.ok:
                logger.error("Refusing AVO lineage from %s: chain verification failed (%s)", target, verdict.defect)
                raise LineageIntegrityError(f"avo lineage chain is not intact: {verdict.defect}")
            if chain and len(chain) != len(lineage.versions):
                logger.error(
                    "Refusing AVO lineage from %s: %d chain links but %d committed versions",
                    target,
                    len(chain),
                    len(lineage.versions),
                )
                raise LineageIntegrityError(
                    f"avo lineage chain length {len(chain)} does not match {len(lineage.versions)} committed versions"
                )

            return lineage
        except LineageIntegrityError:
            raise
        except Exception as e:
            logger.warning("Failed to load AVO Lineage from %s: %s", target, e)
            return None

    def save_knowledge_base(self, kb: DomainKnowledgeBase, filename: str = "knowledge.json") -> Path:
        """Persist domain knowledge entries (patterns & anti-patterns) to disk."""
        target = self._target(filename)

        payload = {"entries": [e.to_dict() for e in kb.entries]}
        self._write(target, payload)

        logger.info("Persisted Domain Knowledge (%d entries) to %s", len(kb.entries), target)
        return target

    def load_knowledge_base(self, filename: str = "knowledge.json") -> DomainKnowledgeBase | None:
        """Load domain knowledge entries from disk."""
        target = self._target(filename)
        if not target.exists():
            return None

        try:
            with open(target, encoding="utf-8") as f:
                data = json.load(f)

            kb = DomainKnowledgeBase()
            # If disk has custom entries, replace default ones
            entries_data = data.get("entries", [])
            if entries_data:
                kb.entries = [
                    KnowledgeEntry(
                        category=e["category"],
                        title=e["title"],
                        content=e["content"],
                        tags=e.get("tags", []),
                        impact_description=e.get("impact_description", ""),
                        created_at=e.get("created_at", 0.0),
                    )
                    for e in entries_data
                ]
            return kb
        except Exception as e:
            logger.warning("Failed to load Domain Knowledge from %s: %s", target, e)
            return None
