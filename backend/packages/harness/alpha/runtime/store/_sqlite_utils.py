"""Shared SQLite connection utilities for store and checkpointer providers."""

from __future__ import annotations

import pathlib

from alpha.config.paths import resolve_path

#: Milliseconds a connection on the shared ``alpha.db`` waits for SQLite's
#: write lock before failing ``database is locked``. This is the contract
#: shared by every writer on the file: the app ORM engine
#: (``persistence/engine.py::_enable_sqlite_wal``) and the sync agents store
#: (``persistence/agents/sql.py``) already apply it via their connect
#: listeners, and the LangGraph checkpointer applies it through
#: ``runtime/checkpointer/async_provider.py``. A connection with a thinner
#: budget than its peers is the first to starve out under sustained writes.
SQLITE_BUSY_TIMEOUT_MS = 30_000

#: The connection-time PRAGMA set for every shared ``alpha.db`` connection.
#: ``journal_mode`` persists on the database file once set, but
#: ``synchronous`` and ``busy_timeout`` are per-connection -- each new
#: connection must be told, or it silently reverts to the driver defaults
#: (FULL and 5 seconds).
SQLITE_CONNECT_PRAGMAS: tuple[str, ...] = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA foreign_keys=ON;",
    f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};",
)


async def apply_sqlite_connect_pragmas(conn) -> None:
    """Run :data:`SQLITE_CONNECT_PRAGMAS` on an open aiosqlite connection.

    Applied right after connect, before the connection is handed to any
    writer, so no consumer of this helper can observe the driver defaults.
    """
    for statement in SQLITE_CONNECT_PRAGMAS:
        cursor = await conn.execute(statement)
        await cursor.close()


def resolve_sqlite_conn_str(raw: str) -> str:
    """Return a SQLite connection string ready for use with store/checkpointer backends.

    SQLite special strings (``":memory:"`` and ``file:`` URIs) are returned
    unchanged.  Plain filesystem paths — relative or absolute — are resolved
    to an absolute string via :func:`resolve_path`.
    """
    if raw == ":memory:" or raw.startswith("file:"):
        return raw
    return str(resolve_path(raw))


def ensure_sqlite_parent_dir(conn_str: str) -> None:
    """Create parent directory for a SQLite filesystem path.

    No-op for in-memory databases (``":memory:"``) and ``file:`` URIs.
    """
    if conn_str != ":memory:" and not conn_str.startswith("file:"):
        pathlib.Path(conn_str).parent.mkdir(parents=True, exist_ok=True)
