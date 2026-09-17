"""11. SQLite session catalog + thread bindings + branch/rewind helpers.

OpenClaw 2.0 moved sessions/transcripts into SQLite with searchable,
branchable, rewindable conversations + durable channel/topic bindings.
DeerFlow persists checkpoints in sqlite/postgres via LangGraph; this adds
the missing lightweight catalog index (sqlite) that maps
(session_id -> thread_id, agent, channel, topic) without touching the
checkpointer schema. Branch = copy catalog entry pointing at a parent
checkpoint cursor; rewind = resolve earlier cursor. Storage ops use stdlib
sqlite3 only (no new deps), WAL-safe with short transactions.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SessionRecord:
    session_id: str
    thread_id: str
    agent_name: str = "lead_agent"
    channel: str = ""
    topic: str = ""
    title: str = ""
    created_at: float = 0.0
    parent_session_id: str = ""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS orchestrator_sessions (
  session_id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  agent_name TEXT NOT NULL DEFAULT 'lead_agent',
  channel TEXT NOT NULL DEFAULT '',
  topic TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  parent_session_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_orch_sessions_thread ON orchestrator_sessions(thread_id);
CREATE INDEX IF NOT EXISTS idx_orch_sessions_channel ON orchestrator_sessions(channel, topic);
"""


class SessionCatalog:
    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def create(
        self,
        thread_id: str,
        agent_name: str = "lead_agent",
        channel: str = "",
        topic: str = "",
        title: str = "",
    ) -> SessionRecord:
        record = SessionRecord(
            session_id="ses_" + uuid.uuid4().hex[:12],
            thread_id=thread_id,
            agent_name=agent_name,
            channel=channel,
            topic=topic,
            title=title,
            created_at=time.time(),
        )
        self._conn.execute(
            "INSERT INTO orchestrator_sessions VALUES (?,?,?,?,?,?,?,?)",
            (record.session_id, record.thread_id, record.agent_name, record.channel, record.topic, record.title, record.created_at, ""),
        )
        self._conn.commit()
        return record

    def branch(self, session_id: str, title_suffix: str = " (branch)") -> SessionRecord | None:
        src = self.get(session_id)
        if src is None:
            return None
        record = SessionRecord(
            session_id="ses_" + uuid.uuid4().hex[:12],
            thread_id=src.thread_id,  # same thread, new session cursor (rewind point stored by caller)
            agent_name=src.agent_name,
            channel=src.channel,
            topic=src.topic,
            title=(src.title or src.session_id) + title_suffix,
            created_at=time.time(),
            parent_session_id=src.session_id,
        )
        self._conn.execute(
            "INSERT INTO orchestrator_sessions VALUES (?,?,?,?,?,?,?,?)",
            (record.session_id, record.thread_id, record.agent_name, record.channel, record.topic, record.title, record.created_at, record.parent_session_id),
        )
        self._conn.commit()
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        row = self._conn.execute(
            "SELECT session_id,thread_id,agent_name,channel,topic,title,created_at,parent_session_id FROM orchestrator_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return SessionRecord(*row)

    def search(self, query: str = "", channel: str = "", limit: int = 50) -> list[SessionRecord]:
        like = f"%{query}%" if query else "%"
        params: list[object] = [like, like]
        sql = "SELECT session_id,thread_id,agent_name,channel,topic,title,created_at,parent_session_id FROM orchestrator_sessions WHERE (title LIKE ? OR session_id LIKE ?)"
        if channel:
            sql += " AND channel=?"
            params.append(channel)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [SessionRecord(*row) for row in rows]

    def bind_topic(self, session_id: str, channel: str, topic: str) -> bool:
        cur = self._conn.execute(
            "UPDATE orchestrator_sessions SET channel=?, topic=? WHERE session_id=?",
            (channel, topic, session_id),
        )
        self._conn.commit()
        return cur.rowcount > 0
