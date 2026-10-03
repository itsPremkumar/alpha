"""The LangGraph checkpointer's SQLite connection must wait 30s for the write lock.

The shipped bug this file exists to prevent
--------------------------------------------
``alpha.db`` is one shared WAL file with many writers: the app ORM engine
(``persistence/engine.py``), the sync agents store (``persistence/agents/sql.py``),
the runs manager, the supervisor loops, and the LangGraph checkpointer all
commit to it. Every engine path widens the sqlite driver's default 5-second
busy timeout to 30 seconds (``PRAGMA busy_timeout=30000``); the checkpointer
did not, because ``AsyncSqliteSaver.from_conn_string`` connects with the plain
driver default.

One writer on the file waiting 5 seconds while every other writer waits 30
is the whole bug: under sustained writes a checkpoint write starved out
behind the engine's writers, and a live e2e run created its output file and
then died mid-flight with ``OperationalError: database is locked``
(run ``19f01090-de36-40d1-8be5-00a768a60c81``, 2026-10-02).

What the tests pin
------------------
1. A checkpoint write stays pending beyond the driver's 5-second default
   while another connection holds the write lock, and completes once the
   lock is released -- it never fails ``database is locked``.
2. The checkpointer connection carries the shared PRAGMA set (WAL,
   synchronous=NORMAL, busy_timeout=30000) and the 30-second constant is
   pinned so a silent change is a test failure, not a quieter outage.

Both tests run through ``_async_checkpointer`` -- the production provider
path -- so replacing the hardened opener with a plain ``from_conn_string``
call re-breaks test 2 (busy_timeout reverts to 5000) and test 1 (the write
gives up at 5s).
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from alpha.runtime.checkpointer.async_provider import _async_checkpointer
from alpha.runtime.store._sqlite_utils import SQLITE_BUSY_TIMEOUT_MS


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _pragma(conn, name: str):
    cursor = await conn.execute(f"PRAGMA {name}")
    row = await cursor.fetchone()
    return row[0]


@pytest.mark.anyio
async def test_checkpoint_write_waits_out_a_concurrent_writer(tmp_path) -> None:
    db = str(tmp_path / "busy.db")
    # Seed the table before any lock exists so the only contention under test
    # is the holder's write lock versus the checkpointer's write.
    seed = sqlite3.connect(db)
    seed.execute("CREATE TABLE probe (x INTEGER)")
    seed.commit()
    seed.close()

    config = SimpleNamespace(type="sqlite", connection_string=db)
    async with _async_checkpointer(config) as saver:
        # Entry ran saver.setup(), so the schema exists and the lock taken
        # now is purely the test's.
        holder = sqlite3.connect(db)
        holder.execute("BEGIN IMMEDIATE")
        try:

            async def _write() -> None:
                await saver.conn.execute("INSERT INTO probe VALUES (1)")
                await saver.conn.commit()

            task = asyncio.create_task(_write())
            await asyncio.sleep(6.0)
            # With the driver's 5s default this task has ALREADY failed with
            # "database is locked"; with the widened 30s timeout it is still
            # waiting for the holder.
            assert not task.done(), "checkpoint write gave up before the holder released the lock"
            holder.execute("COMMIT")
            holder.close()
            holder = None
            await asyncio.wait_for(task, timeout=25.0)
        finally:
            if holder is not None:
                holder.close()  # close() rolls the open transaction back
    # The write is durable and the count is exactly one.
    verify = sqlite3.connect(db)
    try:
        count = verify.execute("SELECT COUNT(*) FROM probe").fetchone()[0]
    finally:
        verify.close()
    assert count == 1


@pytest.mark.anyio
async def test_checkpointer_connection_carries_the_shared_pragma_set(tmp_path) -> None:
    config = SimpleNamespace(type="sqlite", connection_string=str(tmp_path / "pragmas.db"))
    async with _async_checkpointer(config) as saver:
        assert await _pragma(saver.conn, "busy_timeout") == SQLITE_BUSY_TIMEOUT_MS
        assert await _pragma(saver.conn, "journal_mode") == "wal"
        assert await _pragma(saver.conn, "synchronous") == 1  # NORMAL
    # The 30s budget is the contract shared with persistence/engine.py and
    # persistence/agents/sql.py; changing it must be a deliberate, visible act.
    assert SQLITE_BUSY_TIMEOUT_MS == 30_000
