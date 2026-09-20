"""Tests for SQLite FTS5 Long-Term Session Recall."""

from alpha.memory.session_search import SessionSearchEngine


def test_session_search_fts5_discovery_and_cron_demotion():
    engine = SessionSearchEngine(db_path=":memory:")

    engine.index_message(
        session_id="sess_user_01",
        message_id="msg_01",
        role="user",
        content="We need to deploy the new auth microservice with JWT tokens",
        source="interactive",
    )
    engine.index_message(
        session_id="sess_cron_99",
        message_id="msg_99",
        role="system",
        content="Cron report: Daily deployment check auth microservice completed successfully",
        source="cron",
    )

    results = engine.search_discovery("auth microservice", limit=10)
    assert len(results) == 2
    assert results[0].session_id == "sess_user_01"
    assert results[0].source == "interactive"
    assert results[1].session_id == "sess_cron_99"


def test_session_search_scroll_and_browse():
    engine = SessionSearchEngine(db_path=":memory:")

    for i in range(1, 6):
        engine.index_message(
            session_id="sess_seq",
            message_id=f"msg_{i}",
            role="user" if i % 2 == 1 else "assistant",
            content=f"Message turn number {i}",
            timestamp=1000.0 + i,
        )

    window = engine.read_scroll_window("sess_seq", around_message_id="msg_3", window_size=1)
    assert len(window) == 3
    assert [m["message_id"] for m in window] == ["msg_2", "msg_3", "msg_4"]

    timeline = engine.browse_timeline(limit=5)
    assert len(timeline) == 1
    assert timeline[0]["session_id"] == "sess_seq"
    assert timeline[0]["turns_count"] == 5
