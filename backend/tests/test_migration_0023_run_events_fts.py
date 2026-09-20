"""FTS index over displayable run-event messages (revision 0023)."""

from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import create_async_engine

from alpha.persistence.bootstrap import _get_alembic_config, _get_head_revision
from alpha.persistence.postgres_schema import build_asyncpg_connect_args

pytestmark = pytest.mark.asyncio

REVISION = "0023_run_events_fts"
PREVIOUS = "0022_scheduled_occurrence_seq"


@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def migration_database(request, tmp_path):
    schema = None
    if request.param == "postgres":
        uri = os.environ.get("TEST_POSTGRES_URI")
        if not uri:
            pytest.skip("requires TEST_POSTGRES_URI (real Postgres migration)")
        parts = urlsplit(uri)
        scheme = "postgresql+asyncpg" if parts.scheme in {"postgres", "postgresql"} else parts.scheme
        query = urlencode([(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key not in {"sslmode", "channel_binding"}])
        uri = urlunsplit(parts._replace(scheme=scheme, query=query))
        schema = f"fts_migration_{uuid.uuid4().hex}"
        engine = create_async_engine(uri, connect_args=build_asyncpg_connect_args(schema))
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}")
    cfg = _get_alembic_config(engine, postgres_schema=schema or "")
    try:
        if schema:
            async with engine.begin() as connection:
                await connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        await asyncio.to_thread(command.upgrade, cfg, PREVIOUS)
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO run_events (thread_id, run_id, user_id, event_type, category, content, event_metadata, seq, created_at) "
                    "VALUES ('thread-legacy', 'run-legacy', 'user-legacy', 'llm.human.input', 'message', "
                    '\'{"content": "legacy recall keyword", "type": "human"}\', \'{}\', 1, CURRENT_TIMESTAMP)'
                )
            )
            await connection.execute(
                sa.text(
                    "INSERT INTO run_events (thread_id, run_id, user_id, event_type, category, content, event_metadata, seq, created_at) "
                    "VALUES ('thread-legacy', 'run-legacy', 'user-legacy', 'run.end', 'run', 'done', '{}', 2, CURRENT_TIMESTAMP)"
                )
            )
        yield engine, cfg
    finally:
        if schema:
            async with engine.begin() as connection:
                await connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def test_fts_revision_is_single_head():
    # Tracks the chain head (0024 landed after this revision; the 0024 suite
    # pins the exact head — this asserts the chain is still linear).
    assert _get_head_revision() == "0024_feedback_category"


async def test_upgrade_indexes_legacy_messages_only(migration_database):
    engine, cfg = migration_database
    await asyncio.to_thread(command.upgrade, cfg, REVISION)
    dialect = engine.dialect.name
    async with engine.connect() as connection:
        if dialect == "sqlite":
            tables = {row[0] for row in (await connection.execute(sa.text("SELECT name FROM sqlite_master WHERE type='table'"))).all()}
            assert "run_events_fts" in tables
            triggers = {row[0] for row in (await connection.execute(sa.text("SELECT name FROM sqlite_master WHERE type='trigger'"))).all()}
            assert {"run_events_fts_insert", "run_events_fts_delete"} <= triggers
            indexed = (await connection.execute(sa.text("SELECT COUNT(*) FROM run_events_fts"))).scalar()
            # Only the category='message' legacy row is backfilled, not run.end.
            assert indexed == 1
            matched = (await connection.execute(sa.text("SELECT COUNT(*) FROM run_events_fts WHERE run_events_fts MATCH 'recall'"))).scalar()
            assert matched == 1
        else:
            index_names = {row[0] for row in (await connection.execute(sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'run_events'"))).all()}
            assert "ix_run_events_content_tsv" in index_names


async def test_triggers_keep_index_current_and_downgrade_cleans_up(migration_database):
    engine, cfg = migration_database
    if engine.dialect.name != "sqlite":
        pytest.skip("trigger lifecycle is SQLite-specific; Postgres uses the expression index")
    await asyncio.to_thread(command.upgrade, cfg, REVISION)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO run_events (thread_id, run_id, user_id, event_type, category, content, event_metadata, seq, created_at) "
                "VALUES ('thread-new', 'run-new', 'user-new', 'llm.ai.response', 'message', "
                '\'{"content": "freshly indexed wording", "type": "ai"}\', \'{}\', 1, CURRENT_TIMESTAMP)'
            )
        )
        live = (await connection.execute(sa.text("SELECT COUNT(*) FROM run_events_fts WHERE run_events_fts MATCH 'freshly'"))).scalar()
        assert live == 1
        await connection.execute(sa.text("DELETE FROM run_events WHERE thread_id = 'thread-new'"))
        gone = (await connection.execute(sa.text("SELECT COUNT(*) FROM run_events_fts WHERE run_events_fts MATCH 'freshly'"))).scalar()
        assert gone == 0
    await asyncio.to_thread(command.downgrade, cfg, PREVIOUS)
    async with engine.connect() as connection:
        tables = {row[0] for row in (await connection.execute(sa.text("SELECT name FROM sqlite_master WHERE type='table'"))).all()}
        assert "run_events_fts" not in tables
