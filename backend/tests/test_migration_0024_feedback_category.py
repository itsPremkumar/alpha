"""Optional feedback category column (revision 0024)."""

from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agent_workspace.persistence.bootstrap import _get_alembic_config, _get_head_revision
from agent_workspace.persistence.postgres_schema import build_asyncpg_connect_args

pytestmark = pytest.mark.asyncio

REVISION = "0024_feedback_category"
PREVIOUS = "0023_run_events_fts"


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
        schema = f"feedback_category_migration_{uuid.uuid4().hex}"
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
            await connection.execute(sa.text("INSERT INTO feedback (feedback_id, run_id, thread_id, user_id, rating, created_at) VALUES ('fb-legacy', 'run-legacy', 'thread-legacy', 'user-legacy', -1, CURRENT_TIMESTAMP)"))
        yield engine, cfg
    finally:
        if schema:
            async with engine.begin() as connection:
                await connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


async def _feedback_columns(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as connection:
        return await connection.run_sync(lambda sc: [col["name"] for col in sa.inspect(sc).get_columns("feedback")])


async def test_revision_is_current_head() -> None:
    assert _get_head_revision() == REVISION


async def test_upgrade_adds_nullable_category_legacy_rows_read_null(migration_database) -> None:
    engine, cfg = migration_database
    await asyncio.to_thread(command.upgrade, cfg, REVISION)
    assert "category" in await _feedback_columns(engine)
    async with engine.connect() as connection:
        value = (await connection.execute(sa.text("SELECT category FROM feedback WHERE feedback_id = 'fb-legacy'"))).scalar_one()
    assert value is None


async def test_categorized_insert_round_trips(migration_database) -> None:
    engine, cfg = migration_database
    await asyncio.to_thread(command.upgrade, cfg, REVISION)
    async with engine.begin() as connection:
        await connection.execute(sa.text("INSERT INTO feedback (feedback_id, run_id, thread_id, user_id, rating, category, created_at) VALUES ('fb-new', 'run-legacy', 'thread-legacy', 'user-other', 1, 'grounding', CURRENT_TIMESTAMP)"))
        value = (await connection.execute(sa.text("SELECT category FROM feedback WHERE feedback_id = 'fb-new'"))).scalar_one()
    assert value == "grounding"


async def test_downgrade_removes_category_and_reupgrade_restores(migration_database) -> None:
    engine, cfg = migration_database
    await asyncio.to_thread(command.upgrade, cfg, REVISION)
    await asyncio.to_thread(command.downgrade, cfg, PREVIOUS)
    assert "category" not in await _feedback_columns(engine)
    # Re-upgrade must be a no-op-safe pass (idempotent safe_add_column).
    await asyncio.to_thread(command.upgrade, cfg, REVISION)
    assert "category" in await _feedback_columns(engine)
