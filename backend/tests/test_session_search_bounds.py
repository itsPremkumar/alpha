"""M1 tests: session_search safety bounds (Hermes session_search_tool port).

Pins: module bound constants; hidden session sources (kanban/subagent/tool)
never returned by discovery; exclude_session_ids clamped at 20; cron demoted
+5.0 and never auto-excluded; read_session_tail / read_scroll_window
per-message 2000-char cap with an explicit truncated flag; browse_timeline
since_seconds bound on the existing timestamp column; existing discovery and
discovery_smart shapes stay backward compatible for the unchanged consumer.
"""

import time

import pytest

from alpha.memory import session_search as session_search_module
from alpha.memory.session_search import SessionSearchEngine


@pytest.fixture(autouse=True)
def _workspace_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    return tmp_path


def _engine() -> SessionSearchEngine:
    return SessionSearchEngine(db_path=":memory:")


def test_module_bound_constants():
    assert session_search_module._HIDDEN_SESSION_SOURCES == ("kanban", "subagent", "tool")
    assert session_search_module._READ_MAX_CONTENT == 2000
    assert session_search_module._DISCOVER_SCAN_LIMIT == 300
    assert session_search_module._EXCLUDE_SESSION_IDS_CAP == 20
    # cron stays DEMOTED, never auto-excluded: it is not a hidden source
    assert "cron" not in session_search_module._HIDDEN_SESSION_SOURCES


def test_hidden_session_sources_never_returned_by_discovery():
    engine = _engine()
    for source in ["interactive", "cron", "kanban", "subagent", "tool"]:
        engine.index_message(
            session_id=f"s-{source}",
            message_id=f"m-{source}",
            role="user",
            content="shared keyword alphaomega",
            source=source,
        )
    results = engine.search_discovery("alphaomega", limit=10)
    sources = {r.source for r in results}
    assert sources == {"interactive", "cron"}
    assert "kanban" not in sources
    assert "subagent" not in sources
    assert "tool" not in sources
    assert len(results) == 2


def test_exclude_session_ids_filters_matching_sessions():
    engine = _engine()
    for index in range(25):
        engine.index_message(f"sess-{index}", f"msg-{index}", "user", "common recall token", "interactive")
    excluded = [f"sess-{index}" for index in range(20)]
    results = engine.search_discovery("common recall token", limit=10, exclude_session_ids=excluded)
    # scan limit (300) covers all 25 rows before filtering -> exact eligible set
    assert {r.session_id for r in results} == {f"sess-{index}" for index in range(20, 25)}
    assert len(results) == 5


def test_exclude_session_ids_clamped_at_20():
    engine = _engine()
    for index in range(25):
        engine.index_message(f"sess-{index}", f"msg-{index}", "user", "shared boundary token", "interactive")
    # 21 ids: only the first 20 are honored (sess-1..sess-20); the 21st is dropped
    over_cap = [f"sess-{index}" for index in range(1, 22)]
    results = engine.search_discovery("shared boundary token", limit=25, exclude_session_ids=over_cap)
    returned = {r.session_id for r in results}
    assert "sess-20" not in returned  # 20th honored exclusion
    assert "sess-21" in returned  # 21st element beyond the cap -> dropped
    assert returned == {("sess-0")} | {f"sess-{index}" for index in range(21, 25)}
    # empty / None exclusions are no-ops, oversized lists never error
    assert len(engine.search_discovery("shared boundary token", limit=25, exclude_session_ids=[])) == 25
    assert len(engine.search_discovery("shared boundary token", limit=25, exclude_session_ids=None)) == 25
    assert len(engine.search_discovery("shared boundary token", limit=25, exclude_session_ids=[f"x-{i}" for i in range(50)])) == 25


def test_cron_stays_demoted_never_auto_excluded():
    engine = _engine()
    engine.index_message("sess-i", "m-i", "user", "identical recall body", "interactive")
    engine.index_message("sess-c", "m-c", "system", "identical recall body", "cron")
    results = engine.search_discovery("identical recall body", limit=10)
    by_source = {r.source: r for r in results}
    assert set(by_source) == {"interactive", "cron"}
    # lower-is-better rank + the +5.0 penalty puts cron behind (abs covers 3-decimal rounding)
    assert by_source["cron"].score > by_source["interactive"].score
    assert by_source["cron"].score - by_source["interactive"].score == pytest.approx(5.0, abs=0.01)
    # excluding an unrelated session never hides cron
    kept = engine.search_discovery("identical recall body", limit=10, exclude_session_ids=["sess-i"])
    assert [r.source for r in kept] == ["cron"]


def test_read_session_tail_truncates_per_message_with_flag():
    engine = _engine()
    engine.index_message("sess-t", "m1", "user", "short one", "interactive", timestamp=1000.0)
    engine.index_message("sess-t", "m2", "assistant", "L" * 3000, "interactive", timestamp=2000.0)
    engine.index_message("sess-t", "m3", "user", "short three", "interactive", timestamp=3000.0)
    tail = engine.read_session_tail("sess-t", limit=2)
    assert tail["session_id"] == "sess-t"
    assert tail["count"] == 2
    assert [m["message_id"] for m in tail["messages"]] == ["m2", "m3"]  # chronological tail
    assert tail["messages"][0]["content"] == "L" * 2000
    assert len(tail["messages"][0]["content"]) == 2000
    assert tail["messages"][0]["truncated"] is True
    assert tail["truncated"] is True  # envelope flag is explicit
    assert tail["messages"][1]["content"] == "short three"
    assert tail["messages"][1]["truncated"] is False
    full = engine.read_session_tail("sess-t", limit=10)
    assert [m["message_id"] for m in full["messages"]] == ["m1", "m2", "m3"]
    assert full["messages"][0]["truncated"] is False
    assert full["messages"][1]["truncated"] is True
    assert full["truncated"] is True
    # default limit path + empty results keep the envelope shape
    empty = engine.read_session_tail("sess-t", limit=0)
    assert empty["messages"] == []
    assert empty["count"] == 0
    assert empty["truncated"] is False
    ghost = engine.read_session_tail("ghost-session")
    assert ghost["messages"] == []
    assert ghost["truncated"] is False
    for message in tail["messages"]:
        assert {"message_id", "role", "content", "source", "timestamp", "truncated"} <= set(message)


def test_scroll_window_content_cap_and_flag():
    engine = _engine()
    engine.index_message("sess-s", "ms1", "user", "alpha", timestamp=1001.0)
    engine.index_message("sess-s", "ms2", "assistant", "B" * 2500, timestamp=1002.0)
    engine.index_message("sess-s", "ms3", "user", "omega", timestamp=1003.0)
    window = engine.read_scroll_window("sess-s", around_message_id="ms2", window_size=1)
    assert [m["message_id"] for m in window] == ["ms1", "ms2", "ms3"]
    assert window[1]["content"] == "B" * 2000
    assert window[1]["truncated"] is True
    assert window[0]["truncated"] is False
    assert window[2]["truncated"] is False
    assert window[0]["content"] == "alpha"


def test_browse_timeline_since_seconds_bounds_existing_timestamp_column():
    engine = _engine()
    now = time.time()
    engine.index_message("old-sess", "o1", "user", "old", timestamp=now - 100_000)
    engine.index_message("new-sess", "n1", "user", "new", timestamp=now - 5)
    all_sessions = engine.browse_timeline(limit=10)
    assert {s["session_id"] for s in all_sessions} == {"old-sess", "new-sess"}
    recent = engine.browse_timeline(limit=10, since_seconds=1000)
    assert [s["session_id"] for s in recent] == ["new-sess"]
    assert recent[0]["turns_count"] == 1
    # default keeps prior unbounded behavior exactly
    assert len(engine.browse_timeline(limit=10)) == 2


def test_backward_compatible_shapes_for_existing_consumer():
    engine = _engine()
    engine.index_message("sess-b", "mb", "user", "consumer keyword", "interactive")
    # old positional discovery call
    results = engine.search_discovery("consumer keyword", 10)
    assert len(results) == 1
    # consumer kwargs (code_agentic_core.py:706-712): search_discovery_smart(query=, limit=)
    smart = engine.search_discovery_smart(query="consumer keyword", limit=5)
    assert len(smart) == 1
    # consumer reads these result attributes unchanged
    first = results[0]
    assert first.session_id == "sess-b"
    assert first.source == "interactive"
    assert isinstance(first.snippet, str)
    assert isinstance(first.score, float)
    assert first.message_id == "mb"
