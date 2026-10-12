"""SQLite journal mode is initialized once rather than on every connect."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from alpha.persistence.engine import close_engine, get_engine, init_engine


def test_wal_mode_persists_without_repeating_journal_mode_on_connection(tmp_path) -> None:
    async def verify() -> None:
        await init_engine(
            "sqlite",
            url=f"sqlite+aiosqlite:///{tmp_path / 'wal-init.db'}",
            sqlite_dir=str(tmp_path),
        )
        engine = get_engine()
        assert engine is not None
        try:
            async with engine.connect() as first:
                first_mode = (await first.execute(text("PRAGMA journal_mode"))).scalar_one()
                async with engine.connect() as second:
                    second_mode = (await second.execute(text("PRAGMA journal_mode"))).scalar_one()
                    foreign_keys = (await second.execute(text("PRAGMA foreign_keys"))).scalar_one()
                    busy_timeout = (await second.execute(text("PRAGMA busy_timeout"))).scalar_one()
                    synchronous = (await second.execute(text("PRAGMA synchronous"))).scalar_one()
            assert first_mode.lower() == second_mode.lower() == "wal"
            assert foreign_keys == 1
            assert busy_timeout == 30000
            assert synchronous == 1
        finally:
            await close_engine()

    asyncio.run(verify())
