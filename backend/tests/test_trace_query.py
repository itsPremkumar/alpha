"""The query surface: filters, the existing cursor, aggregates, and the route.

What is pinned
--------------
* Every filter narrows without changing ordering or cursor semantics, so a
  filtered read pages the same way an unfiltered one does.
* The cursor is the **existing** ``after_seq``, so one reader serves both the run
  feed and the behaviour trace.
* An aggregate describes exactly the population the filtered page returns -- it is
  computed from the same rows, not from a second query.
* A trace filter on the shared ``/events`` route never hides a display row, and
  a row the build cannot read is kept rather than dropped or refused.
* A non-integer ``layer`` is a 422, not a silently-empty result.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from alpha.observability.trace.codes import TraceLayer
from alpha.observability.trace.contract import TraceEnvelope
from alpha.observability.trace.query import (
    MAX_LIMIT,
    TraceFilter,
    aggregate,
    coerce_ints,
    coerce_set,
    envelopes_from_run_events,
    matches,
    query,
)
from alpha.observability.trace.writer import reset_writer


@pytest.fixture(autouse=True)
def _no_installed_writer():
    reset_writer()
    yield
    reset_writer()


def _envelope(seq: int, event_type: str, **overrides: Any) -> TraceEnvelope:
    """Build one envelope with a *pinned* clock.

    ``ts_wall`` is explicit because a time-range filter is only testable when the
    timestamps are ordered by construction rather than by how fast the machine
    built the corpus. Six envelopes created in a tight loop share one wall-clock
    reading, which is real behaviour and useless as a fixture.
    """
    base: dict[str, Any] = {
        "event_type": event_type,
        "run_id": "run-1",
        "trace_id": "trace-1",
        "thread_id": "t-1",
        "seq": seq,
        "agent_name": "lead-agent",
        "ts_wall": 1_700_000_000.0 + seq,
        "ts_monotonic": float(seq),
        "payload": {"provider": "space-bunny", "model": "space-bunny-free", "finish_reason": "stop", "latency_ms": 10.0 * seq},
        "provider": "space-bunny",
        "model": "space-bunny-free",
    }
    base.update(overrides)
    return TraceEnvelope.build(**base)


def _corpus() -> list[TraceEnvelope]:
    return [
        _envelope(1, "model.call.requested"),
        _envelope(
            2,
            "model.call.completed",
            payload={
                "provider": "space-bunny",
                "model": "space-bunny-free",
                "finish_reason": "stop",
                "latency_ms": 120.0,
                "ttfb_ms": 40.0,
                "tokens": {"input_tokens": 100, "output_tokens": 50, "cached_tokens": 10},
                "cost_usd": 0.002,
                "reasoning": None,
            },
        ),
        _envelope(3, "tool.select.decided", tool="bash", payload={"candidates": ["bash", "read_file"], "chosen": "bash", "reason": "literal match"}),
        _envelope(4, "err.raised", severity="error", error_code="TIMEOUT", payload={"error_code": "TIMEOUT", "message": "timed out", "retried": True}),
        _envelope(5, "sub.spawned", agent_depth=1, subagent_id="task-1", payload={"reason": "delegated", "depth": 1}),
        _envelope(
            6,
            "model.call.completed",
            provider="union-alpha",
            model="union-alpha-pro",
            payload={"provider": "union-alpha", "model": "union-alpha-pro", "finish_reason": "length", "latency_ms": 900.0, "ttfb_ms": 800.0, "tokens": {"input_tokens": 900, "output_tokens": 5}, "cost_usd": 0.01, "reasoning": None},
        ),
    ]


# ---------------------------------------------------------------------------
# filters
# ---------------------------------------------------------------------------


def test_an_empty_filter_selects_everything():
    assert TraceFilter().is_empty()
    assert all(matches(envelope, TraceFilter()) for envelope in _corpus())


def test_a_non_empty_filter_does_not_report_itself_empty():
    """`is_empty` gates a per-row predicate skip in the route; a filter that
    narrowed while claiming to be empty would make that optimisation lossy."""
    assert not TraceFilter(tools=frozenset({"bash"})).is_empty()
    assert not TraceFilter(only_truncated=True).is_empty()
    assert not TraceFilter(since_ts=1.0).is_empty()
    assert not TraceFilter(severities=frozenset({"error"})).is_empty()


@pytest.mark.parametrize(
    ("criteria", "expected"),
    [
        (TraceFilter(event_types=frozenset({"err.raised"})), [4]),
        (TraceFilter(layers=frozenset({int(TraceLayer.MODEL)})), [1, 2, 6]),
        (TraceFilter(layers=frozenset({int(TraceLayer.ERROR), int(TraceLayer.SUBAGENT)})), [4, 5]),
        (TraceFilter(tools=frozenset({"bash"})), [3]),
        (TraceFilter(providers=frozenset({"union-alpha"})), [6]),
        (TraceFilter(models=frozenset({"space-bunny-free"})), [1, 2, 3, 4, 5]),
        (TraceFilter(error_codes=frozenset({"TIMEOUT"})), [4]),
        (TraceFilter(agent_names=frozenset({"lead-agent"})), [1, 2, 3, 4, 5, 6]),
        (TraceFilter(agent_names=frozenset({"nobody"})), []),
        (TraceFilter(severities=frozenset({"error"})), [4]),
        (TraceFilter(min_severity="error"), [4]),
        (TraceFilter(subagent_ids=frozenset({"task-1"})), [5]),
        (TraceFilter(nodes=frozenset({"nope"})), []),
    ],
)
def test_each_filter_narrows_to_exactly_the_right_rows(criteria: TraceFilter, expected: list[int]):
    assert [envelope.seq for envelope in _corpus() if matches(envelope, criteria)] == expected


def test_time_range_filter():
    corpus = _corpus()
    assert [e.seq for e in corpus if matches(e, TraceFilter(since_ts=corpus[1].ts_wall))] == [2, 3, 4, 5, 6]
    assert [e.seq for e in corpus if matches(e, TraceFilter(until_ts=corpus[0].ts_wall))] == [1]
    assert [e.seq for e in corpus if matches(e, TraceFilter(since_ts=corpus[-1].ts_wall, until_ts=corpus[-1].ts_wall))] == [6]


def test_comma_separated_strings_and_lists_agree():
    """Two spellings of the same filter must not be able to disagree; a trailing
    space in ``tool=bash, read_file`` used to be the kind of thing that looks
    like an empty result rather than a matched one."""
    assert coerce_set("a, b ,c") == coerce_set(["a", "b", "c"]) == frozenset({"a", "b", "c"})
    assert coerce_set("") is None
    assert coerce_set(None) is None
    assert coerce_ints("2,13") == frozenset({2, 13})


def test_a_non_integer_layer_filter_is_refused_not_ignored():
    with pytest.raises(ValueError, match="comma-separated list of integers"):
        coerce_ints("2,banana")


def test_describe_echoes_only_the_active_filters():
    assert TraceFilter().describe() == {}
    described = TraceFilter(tools=frozenset({"bash"}), min_severity="error").describe()
    assert described == {"tools": ["bash"], "min_severity": "error"}


# ---------------------------------------------------------------------------
# the existing cursor
# ---------------------------------------------------------------------------


def test_paging_uses_the_existing_after_seq_cursor_and_returns_its_last_seq():
    corpus = _corpus()
    first = query(corpus, limit=2)
    assert [e.seq for e in first.events] == [1, 2]
    assert first.after_seq == 2
    assert first.has_more is True

    second = query(corpus, after_seq=first.after_seq, limit=2)
    assert [e.seq for e in second.events] == [3, 4]

    third = query(corpus, after_seq=second.after_seq, limit=2)
    assert [e.seq for e in third.events] == [5, 6]
    assert third.has_more is False
    # ``total_matched`` is a per-page count, and is None whenever there is more:
    # a reader must not mistake "this page held 2" for "the run held 2".
    assert third.total_matched == 2
    assert first.total_matched is None


def test_paging_to_the_end_never_reports_a_false_has_more():
    corpus = _corpus()
    seen: list[int] = []
    cursor = None
    while True:
        page = query(corpus, after_seq=cursor, limit=2)
        seen.extend(envelope.seq for envelope in page.events)
        if not page.has_more:
            break
        cursor = page.after_seq
    assert seen == [1, 2, 3, 4, 5, 6]


def test_the_cursor_is_strictly_greater_so_a_replay_never_duplicates():
    corpus = _corpus()
    page = query(corpus, after_seq=3, limit=10)
    assert [e.seq for e in page.events] == [4, 5, 6]


def test_a_filtered_page_still_pages_on_the_same_cursor():
    corpus = _corpus()
    criteria = TraceFilter(layers=frozenset({int(TraceLayer.MODEL)}))
    page = query(corpus, criteria, limit=1)
    assert [e.seq for e in page.events] == [1]
    assert page.after_seq == 1
    assert [e.seq for e in query(corpus, criteria, after_seq=page.after_seq, limit=10).events] == [2, 6]


def test_out_of_order_input_is_returned_in_seq_order():
    corpus = list(reversed(_corpus()))
    assert [e.seq for e in query(corpus, limit=10).events] == [1, 2, 3, 4, 5, 6]


def test_a_limit_above_the_cap_is_clamped_rather_than_refused():
    assert len(query(_corpus(), limit=MAX_LIMIT * 10).events) == 6


def test_a_non_positive_limit_is_refused():
    for bad in (0, -1):
        with pytest.raises(ValueError, match="positive integer"):
            query(_corpus(), limit=bad)


# ---------------------------------------------------------------------------
# aggregates
# ---------------------------------------------------------------------------


def test_the_aggregate_summarises_the_same_rows_the_page_returns():
    corpus = _corpus()
    criteria = TraceFilter(layers=frozenset({int(TraceLayer.MODEL)}))
    selected = [envelope for envelope in corpus if matches(envelope, criteria)]
    summary = aggregate(selected)
    assert summary.total_events == len(query(corpus, criteria, limit=100).events)
    assert summary.input_tokens == 1000
    assert summary.output_tokens == 55
    assert summary.cached_tokens == 10
    assert round(summary.cost_usd, 6) == 0.012
    assert summary.model_calls == 3
    assert summary.error_counts == {}


def test_latency_percentiles_use_a_stated_nearest_rank_definition():
    summary = aggregate([envelope for envelope in _corpus() if envelope.event_type == "model.call.completed"])
    assert summary.latency_ms_p50 == 120.0
    assert summary.latency_ms_p99 == 900.0
    assert summary.ttfb_ms_p99 == 800.0


def test_an_empty_population_reports_none_not_zero_for_percentiles():
    """Zero would read as "the p99 was zero milliseconds"."""
    summary = aggregate([])
    assert summary.total_events == 0
    assert summary.latency_ms_p50 is None
    assert summary.latency_ms_p99 is None


def test_error_counts_come_from_the_registry_code_on_the_envelope():
    summary = aggregate(_corpus())
    assert summary.error_counts == {"TIMEOUT": 1}
    assert summary.counts_by_layer[str(int(TraceLayer.ERROR))] == 1
    assert summary.counts_by_severity == {"info": 5, "error": 1}


def test_truncation_is_counted_in_the_aggregate():
    from alpha.observability.trace.contract import TraceBounds

    big = TraceEnvelope.build(
        event_type="env.run.opened",
        run_id="run-1",
        trace_id="t",
        seq=99,
        payload={"model_config_sha256": "h", "config_version": "7", **{f"item_{i}": "y" * 60 for i in range(60)}},
        bounds=TraceBounds(max_payload_bytes=256),
    )
    summary = aggregate([big])
    assert summary.truncated_events == 1
    assert summary.dropped_payload_items == big.dropped_items


def test_the_aggregate_response_is_json_shaped():
    response = aggregate(_corpus()).to_response()
    assert set(response) >= {"total_events", "counts_by_type", "counts_by_layer", "counts_by_severity", "error_counts", "tokens", "cost_usd", "latency_ms", "ttfb_ms"}
    assert response["tokens"] == {"input": 1000, "output": 55, "cached": 10, "reasoning": 0}
    assert response["latency_ms"]["p50"] == 120.0


# ---------------------------------------------------------------------------
# reading back from the durable store
# ---------------------------------------------------------------------------


def test_envelopes_are_rebuilt_from_durable_rows():
    corpus = _corpus()
    rows = [envelope.to_run_event() for envelope in corpus]
    assert envelopes_from_run_events(rows) == tuple(corpus)


def test_an_unreadable_row_is_skipped_and_counted_not_fatal():
    """One row this build cannot read must not make the other 4,000 unreachable."""
    rows = [envelope.to_run_event() for envelope in _corpus()]
    rows.insert(2, {"event_type": "model.call.completed", "category": "trace", "content": {}, "metadata": {"schema_version": 99}})
    rows.insert(3, {"event_type": "run.start", "category": "trace", "content": "legacy", "metadata": {}})
    restored = envelopes_from_run_events(rows)
    assert len(restored) == len(rows) - 2


# ---------------------------------------------------------------------------
# the route
# ---------------------------------------------------------------------------


def _authed_app():
    from _router_auth_helpers import make_authed_test_app

    from app.gateway.routers import thread_runs

    app = make_authed_test_app()
    app.include_router(thread_runs.router)
    return app, thread_runs


def _app_with_rows(rows: list[dict]):
    """Seed the **real** in-memory store rather than a mock.

    A mock returns every row whatever the route asked for, so a test against one
    cannot see the ``limit``/``after_seq`` behaviour the route depends on --
    which is exactly the behaviour a cursor test exists to pin.
    """
    from alpha.runtime.events.store.memory import MemoryRunEventStore
    from alpha.runtime.runs.manager import EditReplayVisibility

    app, thread_runs = _authed_app()
    store = MemoryRunEventStore()
    # ``seq`` is assigned by the store, so it is stripped from the seed rows: a
    # row carrying one would be rejected by ``put_batch``, and the store's own
    # assignment is what the cursor test needs to be honest about anyway.
    seed = [{key: value for key, value in row.items() if key != "seq"} for row in rows]
    asyncio.run(store.put_batch([{**row, "thread_id": "t-1", "run_id": "run-1"} for row in seed]))
    store.list_messages = AsyncMock(return_value=[])
    store.list_messages_by_run = AsyncMock(return_value=[])
    app.state.run_event_store = store
    run_manager = MagicMock()
    run_manager.list_successful_regenerate_sources = AsyncMock(return_value=set())
    run_manager.list_edit_replay_visibility = AsyncMock(return_value=EditReplayVisibility())
    run_manager.list_by_thread = AsyncMock(return_value=[])
    app.state.run_manager = run_manager
    return app, thread_runs, store


def _rows() -> list[dict]:
    corpus = _corpus()
    display = {"event_type": "llm.human.input", "category": "message", "content": {"type": "human", "text": "hi"}, "metadata": {}}
    return [display, *[envelope.to_run_event() for envelope in corpus]]


def test_an_unfiltered_read_is_unchanged():
    from fastapi.testclient import TestClient

    app, _thread_runs, _store = _app_with_rows(_rows())
    with TestClient(app) as client:
        response = client.get("/api/threads/t-1/runs/run-1/events")
    assert response.status_code == 200
    assert len(response.json()) == 7


def test_a_tool_filter_narrows_the_trace_rows_and_never_the_display_row():
    from fastapi.testclient import TestClient

    app, _thread_runs, _store = _app_with_rows(_rows())
    with TestClient(app) as client:
        response = client.get("/api/threads/t-1/runs/run-1/events", params={"tool": "bash"})
    assert response.status_code == 200
    body = response.json()
    assert sorted(row["event_type"] for row in body) == ["llm.human.input", "tool.select.decided"], "the chat message is always returned"


def test_a_layer_filter_narrows():
    from fastapi.testclient import TestClient

    app, _thread_runs, _store = _app_with_rows(_rows())
    with TestClient(app) as client:
        response = client.get("/api/threads/t-1/runs/run-1/events", params={"layer": "13"})
    assert sorted(row["event_type"] for row in response.json()) == ["err.raised", "llm.human.input"]


def test_a_severity_filter_narrows():
    from fastapi.testclient import TestClient

    app, _thread_runs, _store = _app_with_rows(_rows())
    with TestClient(app) as client:
        response = client.get("/api/threads/t-1/runs/run-1/events", params={"min_severity": "error"})
    assert sorted(row["event_type"] for row in response.json()) == ["err.raised", "llm.human.input"]


def test_an_unknown_severity_is_422_not_a_silently_empty_result():
    from fastapi.testclient import TestClient

    app, _thread_runs, _store = _app_with_rows(_rows())
    with TestClient(app) as client:
        response = client.get("/api/threads/t-1/runs/run-1/events", params={"severity": "catastrophic"})
    assert response.status_code == 422


def test_a_non_integer_layer_is_422():
    from fastapi.testclient import TestClient

    app, _thread_runs, _store = _app_with_rows(_rows())
    with TestClient(app) as client:
        response = client.get("/api/threads/t-1/runs/run-1/events", params={"layer": "banana"})
    assert response.status_code == 422


def test_a_row_the_build_cannot_read_is_served_rather_than_hidden():
    from fastapi.testclient import TestClient

    rows = _rows()
    rows.append({"event_type": "model.call.completed", "category": "trace", "content": {}, "metadata": {"schema_version": 99, "run_id": "run-1"}})
    app, _thread_runs, _store = _app_with_rows(rows)
    with TestClient(app) as client:
        response = client.get("/api/threads/t-1/runs/run-1/events", params={"tool": "bash"})
    body = response.json()
    # The unreadable row keeps its own event_type, so the row list is the evidence:
    # the display row, the matching trace row, and the unreadable row that was
    # kept rather than dropped or refused.
    assert [row["event_type"] for row in body] == ["llm.human.input", "tool.select.decided", "model.call.completed"]
    assert body[-1]["metadata"]["schema_version"] == 99


def test_the_after_seq_cursor_pages_the_shared_route():
    from fastapi.testclient import TestClient

    app, _thread_runs, _store = _app_with_rows(_rows())
    total = len(_rows())
    with TestClient(app) as client:
        first = client.get("/api/threads/t-1/runs/run-1/events", params={"limit": 3}).json()
        second = client.get("/api/threads/t-1/runs/run-1/events", params={"limit": 3, "after_seq": first[-1]["seq"]}).json()
    assert len(first) == 3
    assert {row["seq"] for row in first} & {row["seq"] for row in second} == set(), "the cursor must neither re-read nor skip"
    assert len(first) + len(second) == total


def test_the_summary_endpoint_agrees_with_the_list_endpoint():
    from fastapi.testclient import TestClient

    app, _thread_runs, _store = _app_with_rows(_rows())
    with TestClient(app) as client:
        listing = client.get("/api/threads/t-1/runs/run-1/events", params={"layer": "2"}).json()
        summary = client.get("/api/threads/t-1/runs/run-1/events/summary", params={"layer": "2"}).json()

    trace_rows = [row for row in listing if (row.get("metadata") or {}).get("schema_version") == 1]
    assert summary["trace_events"] == len(trace_rows)
    assert summary["tokens"]["input"] == 1000
    assert summary["tokens"]["output"] == 55
    assert summary["latency_ms"]["p99"] == 900.0
    assert summary["filters"] == {"layers": [2]}


def test_the_summary_counts_only_rows_that_claimed_to_be_trace_rows():
    """A chat message sharing the run's feed is not a failed envelope. Counting it
    would report a decode failure that never happened."""
    from fastapi.testclient import TestClient

    app, _thread_runs, _store = _app_with_rows(_rows())
    with TestClient(app) as client:
        clean = client.get("/api/threads/t-1/runs/run-1/events/summary").json()
    assert clean["skipped_unreadable"] == 0, "the display row is not a trace row, so it is not a skip"

    rows = _rows()
    rows.append({"event_type": "model.call.completed", "category": "trace", "content": {}, "metadata": {"schema_version": 99}})
    app, _thread_runs, _store = _app_with_rows(rows)
    with TestClient(app) as client:
        broken = client.get("/api/threads/t-1/runs/run-1/events/summary").json()
    assert broken["skipped_unreadable"] == 1


def test_the_summary_discloses_a_capped_scan():
    """A capped read must say so. A summary that quietly described 3 of 7 events
    while reporting no cap would be worse than no summary."""
    from fastapi.testclient import TestClient

    app, _thread_runs, _store = _app_with_rows(_rows())
    with TestClient(app) as client:
        summary = client.get("/api/threads/t-1/runs/run-1/events/summary", params={"limit": 3}).json()
    assert summary["scanned"] == 3
    assert summary["truncated_scan"] is True


def test_the_summary_reports_no_cap_on_a_full_read():
    from fastapi.testclient import TestClient

    app, _thread_runs, _store = _app_with_rows(_rows())
    with TestClient(app) as client:
        summary = client.get("/api/threads/t-1/runs/run-1/events/summary").json()
    assert summary["scanned"] == 7
    assert summary["truncated_scan"] is False
    assert summary["skipped_unreadable"] == 0
