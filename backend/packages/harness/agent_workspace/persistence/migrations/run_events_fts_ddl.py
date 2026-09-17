"""Full-text search objects for displayable run-event messages.

Single source of truth shared by alembic revision ``0023_run_events_fts``
(upgrade path) and ``DbRunEventStore`` runtime ensure (fresh databases
stamped past migrations, plus drift self-healing). All statements are
idempotent (``IF NOT EXISTS`` / ``NOT EXISTS`` backfill) and plain SQL so
both the sync alembic bind and async SQLAlchemy sessions can execute them.
"""

from __future__ import annotations

FTS_TABLE = "run_events_fts"
PG_INDEX = "ix_run_events_content_tsv"

# Text projection shared by the SQLite triggers and the Postgres index: the
# readable body when content is a LangChain message dict, else the raw
# stored text (JSON or plain).
SQLITE_TEXT_PROJECTION = (
    "CASE WHEN json_valid(content) AND json_type(content) = 'object' "
    "THEN COALESCE(json_extract(content, '$.content'), content) "
    "ELSE content END"
)

SQLITE_MSG_TYPE_PROJECTION = (
    "CASE WHEN json_valid(content) AND json_type(content) = 'object' "
    "THEN COALESCE(json_extract(content, '$.type'), 'other') "
    "ELSE 'text' END"
)

# Trigger-body variants: inside CREATE TRIGGER, bare column names do NOT
# resolve to the triggering row (SQLite raises "no such column"), so the
# projections must be qualified with NEW. The SELECT backfill above keeps the
# bare versions (NEW is invalid outside a trigger).
SQLITE_TRIGGER_TEXT_PROJECTION = (
    "CASE WHEN json_valid(NEW.content) AND json_type(NEW.content) = 'object' "
    "THEN COALESCE(json_extract(NEW.content, '$.content'), NEW.content) "
    "ELSE NEW.content END"
)

SQLITE_TRIGGER_MSG_TYPE_PROJECTION = (
    "CASE WHEN json_valid(NEW.content) AND json_type(NEW.content) = 'object' "
    "THEN COALESCE(json_extract(NEW.content, '$.type'), 'other') "
    "ELSE 'text' END"
)

SQLITE_FTS_DDL: tuple[str, ...] = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS run_events_fts USING fts5("
    "content, thread_id UNINDEXED, run_id UNINDEXED, "
    "event_type UNINDEXED, msg_type UNINDEXED, seq UNINDEXED, "
    "user_id UNINDEXED, created_at UNINDEXED, tokenize='porter')",
    "DROP TRIGGER IF EXISTS run_events_fts_insert",
    "CREATE TRIGGER run_events_fts_insert AFTER INSERT ON run_events "
    "WHEN NEW.category = 'message' BEGIN "
    "INSERT INTO run_events_fts(rowid, content, thread_id, run_id, "
    "event_type, msg_type, seq, user_id, created_at) "
    f"VALUES (NEW.id, {SQLITE_TRIGGER_TEXT_PROJECTION}, NEW.thread_id, NEW.run_id, "
    f"NEW.event_type, {SQLITE_TRIGGER_MSG_TYPE_PROJECTION}, "
    "NEW.seq, NEW.user_id, NEW.created_at); END",
    "DROP TRIGGER IF EXISTS run_events_fts_delete",
    "CREATE TRIGGER run_events_fts_delete AFTER DELETE ON run_events "
    "BEGIN DELETE FROM run_events_fts WHERE rowid = OLD.id; END",
    "INSERT INTO run_events_fts(rowid, content, thread_id, run_id, "
    "event_type, msg_type, seq, user_id, created_at) "
    f"SELECT id, {SQLITE_TEXT_PROJECTION}, thread_id, run_id, event_type, "
    f"{SQLITE_MSG_TYPE_PROJECTION}, seq, user_id, created_at "
    "FROM run_events WHERE category = 'message' "
    "AND NOT EXISTS (SELECT 1 FROM run_events_fts WHERE rowid = run_events.id)",
)

SQLITE_FTS_DROP: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS run_events_fts_insert",
    "DROP TRIGGER IF EXISTS run_events_fts_delete",
    "DROP TABLE IF EXISTS run_events_fts",
)

# Postgres text projection + tsvector expression (shared by the GIN index
# and the search query so both sides always agree).
PG_TEXT_PROJECTION = "COALESCE(content->>'content', content::text)"
PG_TSVECTOR_EXPR = f"to_tsvector('english', {PG_TEXT_PROJECTION})"

PG_INDEX_DDL = (
    f"CREATE INDEX IF NOT EXISTS {PG_INDEX} ON run_events "
    f"USING GIN ({PG_TSVECTOR_EXPR})"
)
PG_INDEX_DROP = f"DROP INDEX IF EXISTS {PG_INDEX}"
