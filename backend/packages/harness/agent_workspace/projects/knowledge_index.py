"""Cross-Project Heuristic Knowledge Index & Pattern Sharing.

Enables agents to discover, query, and reuse generalized heuristics, solutions,
and lessons learned across distinct projects without violating project isolation
or leaking private source code or credentials.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_workspace.config.runtime_paths import runtime_home
from agent_workspace.projects.postmortem import get_postmortem_engine

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _global_knowledge_path() -> Path:
    return runtime_home() / "knowledge" / "heuristics_index.json"


@dataclass
class IndexedPattern:
    """A sanitized, reusable engineering heuristic indexed across projects."""

    pattern_id: str
    origin_project_id: str
    origin_bot: str
    title: str
    problem_statement: str
    solution_heuristic: str
    tags: list[str] = field(default_factory=list)
    indexed_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IndexedPattern:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


def sanitize_text(text: str) -> str:
    """Sanitize potential private tokens or passwords from knowledge text."""
    # Redact potential authorization headers or keys
    s = re.sub(r"(?i)(bearer|key|token|password)\s*[:=]\s*['\"][^'\"]+['\"]", r"\1: [REDACTED]", text)
    # Redact absolute user home directory paths if present
    s = re.sub(r"[A-Za-z]:\\[Users|home]\\[^\\]+", "~", s)
    return s


class CrossProjectKnowledgeIndex:
    """Thread-safe index for discovering cross-project engineering heuristics."""

    def __init__(self, storage_path: Path | None = None):
        self._path = storage_path or _global_knowledge_path()
        self._lock = threading.Lock()
        self._patterns: dict[str, IndexedPattern] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("patterns", []):
                p = IndexedPattern.from_dict(item)
                self._patterns[p.pattern_id] = p
        except Exception:
            logger.warning("Failed to load cross-project knowledge index", exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            payload = {
                "version": 1,
                "patterns": [p.to_dict() for p in self._patterns.values()],
                "updated_at": _now(),
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self._path)
        except Exception:
            logger.warning("Failed to save cross-project knowledge index", exc_info=True)

    def index_heuristic(
        self,
        origin_project: str,
        origin_bot: str,
        title: str,
        problem_statement: str,
        solution_heuristic: str,
        tags: list[str] | None = None,
    ) -> IndexedPattern:
        """Sanitize and index a problem-solution heuristic."""
        pat_id = f"PAT-{uuid.uuid4().hex[:8].upper()}"
        pattern = IndexedPattern(
            pattern_id=pat_id,
            origin_project_id=origin_project,
            origin_bot=origin_bot,
            title=sanitize_text(title),
            problem_statement=sanitize_text(problem_statement),
            solution_heuristic=sanitize_text(solution_heuristic),
            tags=[t.lower().strip() for t in (tags or [])],
        )

        with self._lock:
            self._patterns[pat_id] = pattern
            self._save()

        logger.info("Indexed cross-project pattern %s from %s", pat_id, origin_project)
        return pattern

    def sync_from_project_postmortems(self, project_id: str) -> int:
        """Scan project postmortems and index previously unseen lessons."""
        engine = get_postmortem_engine(project_id)
        postmortems = engine.list_postmortems()
        added_count = 0

        with self._lock:
            existing_problems = {p.problem_statement for p in self._patterns.values()}

            for pm in postmortems:
                clean_prob = sanitize_text(pm.error_summary)
                if clean_prob not in existing_problems:
                    pat_id = f"PAT-{uuid.uuid4().hex[:8].upper()}"
                    pattern = IndexedPattern(
                        pattern_id=pat_id,
                        origin_project_id=project_id,
                        origin_bot=pm.bot_name,
                        title=f"Fix for {pm.error_summary[:60]}",
                        problem_statement=clean_prob,
                        solution_heuristic=sanitize_text(pm.preventative_rule),
                        tags=["postmortem", "self_improvement", pm.bot_name.lower()],
                    )
                    self._patterns[pat_id] = pattern
                    existing_problems.add(clean_prob)
                    added_count += 1

            if added_count > 0:
                self._save()

        return added_count

    def search(
        self,
        query: str,
        *,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[IndexedPattern]:
        """Search patterns by keywords in problem statement, solution heuristic, or tags."""
        words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 2]
        filter_tags = {t.lower() for t in (tags or [])}

        with self._lock:
            scored: list[tuple[IndexedPattern, int]] = []
            for p in self._patterns.values():
                score = 0
                search_corpus = f"{p.title} {p.problem_statement} {p.solution_heuristic}".lower()

                for w in words:
                    if w in search_corpus:
                        score += 2

                if filter_tags:
                    p_tags = set(p.tags)
                    score += len(filter_tags.intersection(p_tags)) * 3

                if score > 0:
                    scored.append((p, score))

            scored.sort(key=lambda t: -t[1])
            return [p for p, _ in scored[:limit]]

    def list_patterns(self, origin_project: str | None = None) -> list[IndexedPattern]:
        with self._lock:
            patterns = list(self._patterns.values())
        if origin_project:
            patterns = [p for p in patterns if p.origin_project_id == origin_project]
        return patterns


_global_index: CrossProjectKnowledgeIndex | None = None
_index_lock = threading.Lock()


def get_cross_project_knowledge_index() -> CrossProjectKnowledgeIndex:
    global _global_index
    with _index_lock:
        if _global_index is None:
            _global_index = CrossProjectKnowledgeIndex()
        return _global_index
