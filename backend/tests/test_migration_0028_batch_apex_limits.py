"""Upgrade existing batch rows to the persisted APEX admission schema."""

import asyncio

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import create_async_engine

from alpha.persistence import bootstrap


@pytest.mark.asyncio
async def test_batch_apex_limit_migration_preserves_rows_and_downgrades(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'batch-apex.db'}")
    cfg = bootstrap._get_alembic_config(engine)
    try:
        await asyncio.to_thread(bootstrap._upgrade, cfg, "0027_side_effect_ledger")
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO subagent_batches "
                    "(id,user_id,thread_id,submission_key,title,subagent_type,status,total_items,"
                    "max_live_items,max_running_items,max_attempts,execution_spec,created_at,updated_at) "
                    "VALUES ('b','u','t','k','title','general-purpose','completed',1,1,1,2,'{}',"
                    "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )

        await bootstrap.bootstrap_schema(engine, backend="sqlite")
        async with engine.connect() as conn:
            columns = await conn.run_sync(lambda sync: {column["name"] for column in sa.inspect(sync).get_columns("subagent_batches")})
            assert {"apex_session_id", "apex_concurrency_limit"} <= columns
            assert await conn.scalar(sa.text("SELECT status FROM subagent_batches WHERE id='b'")) == "completed"
            assert await conn.scalar(sa.text("SELECT count(*) FROM subagent_batch_session_locks")) == 0

        await asyncio.to_thread(command.downgrade, cfg, "0027_side_effect_ledger")
        async with engine.connect() as conn:
            columns = await conn.run_sync(lambda sync: {column["name"] for column in sa.inspect(sync).get_columns("subagent_batches")})
            assert "apex_session_id" not in columns
            assert "apex_concurrency_limit" not in columns
            assert not await conn.run_sync(lambda sync: sa.inspect(sync).has_table("subagent_batch_session_locks"))
            assert await conn.scalar(sa.text("SELECT status FROM subagent_batches WHERE id='b'")) == "completed"
    finally:
        await engine.dispose()
