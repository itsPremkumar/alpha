"""Reflexion & Episodic Trajectory Memory Engine.

Synthesizes the Reflexion (Shinn et al.), Hermes learning loop, and DGM patterns:
Converts failures, tool errors, and repair attempts into durable structured reflections
stored in SQLite so future agent sessions never repeat identical mistakes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/trajectories/reflexion.db")


@dataclass
class ReflexionEntry:
    id: int | None
    problem_signature: str
    observed_failure: str
    root_cause: str
    lesson: str
    confidence: float
    workspace: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReflexionEngine:
    """SQLite-backed episodic failure-reflection engine."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite schema if not present."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS reflexions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    problem_signature TEXT NOT NULL,
                    observed_failure TEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    lesson TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.85,
                    workspace TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_reflexions_sig ON reflexions(problem_signature)"
            )
            conn.commit()

    def record_reflection(
        self,
        problem_signature: str,
        observed_failure: str,
        root_cause: str,
        lesson: str,
        confidence: float = 0.85,
        workspace: str = "",
    ) -> ReflexionEntry:
        """Persist a new failure reflection entry."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO reflexions (
                    problem_signature, observed_failure, root_cause, lesson, confidence, workspace, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    problem_signature.strip(),
                    observed_failure.strip(),
                    root_cause.strip(),
                    lesson.strip(),
                    float(confidence),
                    workspace.strip(),
                    now_iso,
                ),
            )
            conn.commit()
            entry_id = cursor.lastrowid

        entry = ReflexionEntry(
            id=entry_id,
            problem_signature=problem_signature.strip(),
            observed_failure=observed_failure.strip(),
            root_cause=root_cause.strip(),
            lesson=lesson.strip(),
            confidence=confidence,
            workspace=workspace.strip(),
            created_at=now_iso,
        )
        logger.info(
            "Recorded Reflexion [%s]: %s (lesson: %s)",
            entry_id,
            problem_signature,
            lesson[:60],
        )
        return entry

    def query_reflections(
        self,
        query: str,
        limit: int = 5,
        min_confidence: float = 0.5,
    ) -> list[ReflexionEntry]:
        """Query reflections matching tokens from the query string."""
        terms = [t.lower() for t in query.split() if len(t) > 2][:8]
        if not terms:
            terms = [query.lower()[:20]]

        conditions = " OR ".join(
            ["LOWER(problem_signature) LIKE ? OR LOWER(observed_failure) LIKE ? OR LOWER(lesson) LIKE ?"]
            * len(terms)
        )
        params: list[Any] = []
        for t in terms:
            wildcard = f"%{t}%"
            params.extend([wildcard, wildcard, wildcard])

        sql = f"""
            SELECT id, problem_signature, observed_failure, root_cause, lesson, confidence, workspace, created_at
            FROM reflexions
            WHERE confidence >= ? AND ({conditions})
            ORDER BY confidence DESC, id DESC
            LIMIT ?
        """
        params_with_bounds = [min_confidence] + params + [limit]

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(sql, params_with_bounds)
                rows = cursor.fetchall()
            except Exception as e:
                logger.warning("Reflexion query error: %s", e)
                return []

        results: list[ReflexionEntry] = []
        for row in rows:
            results.append(
                ReflexionEntry(
                    id=row[0],
                    problem_signature=row[1],
                    observed_failure=row[2],
                    root_cause=row[3],
                    lesson=row[4],
                    confidence=row[5],
                    workspace=row[6],
                    created_at=row[7],
                )
            )
        return results

    def format_reflections_for_prompt(self, entries: list[ReflexionEntry]) -> str:
        """Format matching reflections as XML/markdown guidelines for lead agent prompt."""
        if not entries:
            return ""

        lines = [
            "<historical_reflexion_lessons>",
            "The following verified lessons were learned from previous failures on similar problems:",
        ]
        for i, r in enumerate(entries, 1):
            lines.append(f"[{i}] Pattern: {r.problem_signature}")
            lines.append(f"    Observed Failure: {r.observed_failure}")
            lines.append(f"    Root Cause: {r.root_cause}")
            lines.append(f"    Crucial Lesson: {r.lesson}")
            lines.append(f"    Confidence: {r.confidence:.0%}")
        lines.append("Use these lessons to proactively prevent similar regressions.")
        lines.append("</historical_reflexion_lessons>")
        return "\n".join(lines)


# Singleton access
_GLOBAL_REFLEXION_ENGINE: ReflexionEngine | None = None


def get_reflexion_engine(db_path: str | Path | None = None) -> ReflexionEngine:
    global _GLOBAL_REFLEXION_ENGINE
    if _GLOBAL_REFLEXION_ENGINE is None or db_path is not None:
        _GLOBAL_REFLEXION_ENGINE = ReflexionEngine(db_path)
    return _GLOBAL_REFLEXION_ENGINE
