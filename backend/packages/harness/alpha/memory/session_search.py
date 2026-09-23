"""Autonomous Long-Term Session Recall Engine with SQLite FTS5.

Provides full-text search indexing across conversation turns with BM25 ranking,
cron vocabulary demotion, lineage tracking, and multi-modal queries (DISCOVERY,
SCROLL, READ, BROWSE).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Safety bounds (Hermes session_search_tool port, backward-compatible additions):
# Sources NEVER surfaced by discovery (internal kanban/subagent/tool chatter).
_HIDDEN_SESSION_SOURCES = ("kanban", "subagent", "tool")
# Per-message content cap on read paths; longer content is cut here and the
# envelope entry carries an explicit truncated=True flag (never silently).
_READ_MAX_CONTENT = 2000
# Rows one discovery scan pulls from FTS before hidden-source/exclusion
# filtering, so filtered-out rows still have headroom.
_DISCOVER_SCAN_LIMIT = 300
# Max caller-supplied exclude_session_ids honored per discovery call; extra
# ids are dropped whole (clamped), never partially applied.
_EXCLUDE_SESSION_IDS_CAP = 20


def _cap_content(content: str) -> tuple[str, bool]:
    """Return (content, truncated): content cut at _READ_MAX_CONTENT chars."""
    if len(content) <= _READ_MAX_CONTENT:
        return content, False
    return content[:_READ_MAX_CONTENT], True


@dataclass
class SessionSearchResult:
    session_id: str
    message_id: str
    role: str
    snippet: str
    source: str
    timestamp: float
    score: float


class SessionSearchEngine:
    """SQLite FTS5 conversation memory indexing and recall engine."""

    def __init__(self, db_path: Path | str = ":memory:"):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self._init_db()

    def _init_db(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
                """
            )
            # FTS5 Virtual Table for full-text search
            self.conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    message_id UNINDEXED,
                    session_id UNINDEXED,
                    role UNINDEXED,
                    content,
                    source UNINDEXED,
                    content='messages',
                    content_rowid='rowid'
                )
                """
            )
            # Triggers to keep FTS in sync with messages table
            self.conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, message_id, session_id, role, content, source)
                    VALUES (new.rowid, new.message_id, new.session_id, new.role, new.content, new.source);
                END
                """
            )

    def index_message(
        self,
        session_id: str,
        message_id: str,
        role: str,
        content: str,
        source: str = "interactive",
        timestamp: float | None = None,
    ) -> None:
        """Insert and index a conversation turn."""
        ts = timestamp or time.time()
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO messages (message_id, session_id, role, content, source, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, content, source, ts),
            )

    def search_discovery(self, query: str, limit: int = 10, *, exclude_session_ids: list[str] | None = None) -> list[SessionSearchResult]:
        """Discovery mode: BM25 keyword search with cron session demotion.

        Safety bounds (backward-compatible keyword addition): scans at most
        ``_DISCOVER_SCAN_LIMIT`` rows so hidden-source/exclusion filtering
        still has headroom; rows whose source is in ``_HIDDEN_SESSION_SOURCES``
        are never returned; up to ``_EXCLUDE_SESSION_IDS_CAP`` caller-supplied
        session ids are honored (extra ids are dropped whole, never partially
        applied). Cron rows are NEVER auto-excluded by policy — they stay
        demoted by the +5.0 penalty.
        """
        clean_q = query.replace('"', '""').replace("'", "''")
        excluded: set[str] = set()
        if exclude_session_ids:
            excluded = set(list(exclude_session_ids)[: _EXCLUDE_SESSION_IDS_CAP])
        cursor = self.conn.cursor()
        # Query FTS5 with bm25 score
        cursor.execute(
            """
            SELECT m.session_id, m.message_id, m.role, m.content, m.source, m.timestamp, rank
            FROM messages_fts f
            JOIN messages m ON f.message_id = m.message_id
            WHERE messages_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (clean_q, _DISCOVER_SCAN_LIMIT),
        )
        rows = cursor.fetchall()

        results = []
        for session_id, msg_id, role, content, source, ts, rank in rows:
            if source in _HIDDEN_SESSION_SOURCES:
                continue  # internal chatter is never surfaced by discovery
            if session_id in excluded:
                continue
            # Cron vocabulary demotion: penalty factor to prevent recall blindness
            adjusted_score = float(rank)
            if source == "cron":
                adjusted_score += 5.0  # FTS5 bm25 rank is lower-is-better (more negative)

            snippet = content[:200] + ("..." if len(content) > 200 else "")
            results.append(
                SessionSearchResult(
                    session_id=session_id,
                    message_id=msg_id,
                    role=role,
                    snippet=snippet,
                    source=source,
                    timestamp=ts,
                    score=round(adjusted_score, 3),
                )
            )

        # Sort by adjusted score ascending (lower rank is more relevant in SQLite FTS5)
        results.sort(key=lambda r: r.score)
        return results[:limit]

    async def asearch_discovery_smart(self, query: str, limit: int = 10) -> list[SessionSearchResult]:
        """BM25 recall, then System One reranks by actual relevance.

        BM25 finds terms; it cannot tell whether a hit answers the question.
        The rerank only reorders what BM25 already returned, so the worst case
        is BM25's own order — a candidate can move up, never disappear.
        """
        results = self.search_discovery(query, limit=limit)
        if len(results) < 2:
            return results
        try:
            from alpha.memory.rerank import apply_rerank, rerank

            ranking = await rerank(query, [r.snippet for r in results], site="session_search")
        except Exception:
            return results
        return apply_rerank(results, ranking)

    def search_discovery_smart(self, query: str, limit: int = 10) -> list[SessionSearchResult]:
        """Sync wrapper; falls back to BM25 inside a running loop."""
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.asearch_discovery_smart(query, limit=limit))
        return self.search_discovery(query, limit=limit)

    def read_scroll_window(self, session_id: str, around_message_id: str, window_size: int = 3) -> list[dict[str, Any]]:
        """Scroll mode: returns sliding window around an anchor message."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT message_id, role, content, timestamp
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp ASC
            """,
            (session_id,),
        )
        msgs = cursor.fetchall()

        idx = -1
        for i, m in enumerate(msgs):
            if m[0] == around_message_id:
                idx = i
                break

        if idx == -1:
            return []

        start = max(0, idx - window_size)
        end = min(len(msgs), idx + window_size + 1)
        window: list[dict[str, Any]] = []
        for m in msgs[start:end]:
            content, truncated = _cap_content(m[2])
            window.append(
                {
                    "message_id": m[0],
                    "role": m[1],
                    "content": content,
                    "timestamp": m[3],
                    "truncated": truncated,
                }
            )
        return window

    def read_session_tail(self, session_id: str, *, limit: int = 20) -> dict[str, Any]:
        """Read mode: the last ``limit`` messages of a session, chronologically.

        Each message carries a per-message content cap at
        ``_READ_MAX_CONTENT`` chars with an explicit ``truncated`` flag; the
        envelope ``truncated`` is True when ANY message was cut. Read paths
        return stored rows as-is otherwise — hidden-source filtering applies
        to discovery only, so explicit reads keep working.
        """
        count = max(int(limit), 0)
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT message_id, role, content, source, timestamp
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp DESC, rowid DESC
            LIMIT ?
            """,
            (session_id, count),
        )
        rows = list(reversed(cursor.fetchall()))
        messages: list[dict[str, Any]] = []
        for message_id, role, content, source, ts in rows:
            capped, truncated = _cap_content(content)
            messages.append(
                {
                    "message_id": message_id,
                    "role": role,
                    "content": capped,
                    "source": source,
                    "timestamp": ts,
                    "truncated": truncated,
                }
            )
        return {
            "session_id": session_id,
            "messages": messages,
            "count": len(messages),
            "truncated": any(message["truncated"] for message in messages),
        }

    def browse_timeline(self, limit: int = 10, *, since_seconds: float | None = None) -> list[dict[str, Any]]:
        """Browse mode: returns recent distinct sessions with turn counts.

        ``since_seconds`` (optional, keyword-only) bounds the existing
        ``timestamp`` column relative to now (call time); ``None`` keeps the
        previous unbounded behavior exactly.
        """
        cursor = self.conn.cursor()
        if since_seconds is None:
            cursor.execute(
                """
                SELECT session_id, count(*), min(timestamp), max(timestamp)
                FROM messages
                GROUP BY session_id
                ORDER BY max(timestamp) DESC
                LIMIT ?
                """,
                (limit,),
            )
        else:
            cursor.execute(
                """
                SELECT session_id, count(*), min(timestamp), max(timestamp)
                FROM messages
                WHERE timestamp >= ?
                GROUP BY session_id
                ORDER BY max(timestamp) DESC
                LIMIT ?
                """,
                (time.time() - float(since_seconds), limit),
            )
        return [
            {
                "session_id": r[0],
                "turns_count": r[1],
                "started_at": r[2],
                "last_active": r[3],
            }
            for r in cursor.fetchall()
        ]
