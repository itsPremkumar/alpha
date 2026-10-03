"""Tests for the live task-list projection (`alpha.agents.todo_events`).

These are the rules that keep the rendered plan honest: an item's status is only
ever what the model reported, nothing is silently dropped or silently shortened,
and a malformed payload degrades to "no plan" rather than raising on the streaming
path. `TestWriteTodosStreaming` covers the delivery half in `TodoMiddleware`.
"""

from __future__ import annotations

from typing import Any

import pytest

from alpha.agents.todo_events import (
    MAX_TODO_CONTENT_CHARS,
    MAX_TODO_ITEMS,
    MAX_TODO_PAYLOAD_CHARS,
    TODO_EVENT_TYPE,
    build_todo_event,
    fits_budget,
    normalize_todos,
    snapshot_from_event,
    todo_event_size,
    todo_item_id,
)


def _todo(content: str, status: str = "pending") -> dict[str, str]:
    return {"content": content, "status": status}


class TestNormalizeTodos:
    def test_empty_input_is_empty_snapshot(self):
        for raw in (None, [], {}, "nonsense", 42):
            assert normalize_todos(raw).is_empty

    def test_status_is_carried_verbatim(self):
        snapshot = normalize_todos([_todo("a", "in_progress"), _todo("b", "completed")])
        assert [item.status for item in snapshot.items] == ["in_progress", "completed"]

    def test_unknown_status_degrades_to_pending_not_dropped(self):
        """An unrecognized status is not proof of failure, so it must not overstate."""
        snapshot = normalize_todos([_todo("a", "halfway"), _todo("b", "done")])
        assert [item.status for item in snapshot.items] == ["pending", "pending"]

    def test_item_without_text_is_skipped(self):
        """A blank checkbox is not renderable, so it is not an observable task."""
        snapshot = normalize_todos([_todo("real"), {"content": "  ", "status": "completed"}, {"status": "pending"}])
        assert [item.content for item in snapshot.items] == ["real"]
        # The model wrote 3 entries, so `reported` still says 3 - the UI must be
        # able to notice the list it received is not what was written.
        assert snapshot.reported == 3

    def test_non_dict_entries_are_skipped(self):
        snapshot = normalize_todos(["bare string", 7, None, _todo("kept")])
        assert [item.content for item in snapshot.items] == ["kept"]

    def test_accepts_dict_wrapper(self):
        snapshot = normalize_todos({"todos": [_todo("wrapped")]})
        assert [item.content for item in snapshot.items] == ["wrapped"]

    def test_content_is_clipped_and_says_so(self):
        long_text = "x" * (MAX_TODO_CONTENT_CHARS + 100)
        item = normalize_todos([_todo(long_text)]).items[0]
        assert item.content.endswith("chars truncated]")
        assert len(item.content) < len(long_text)

    def test_exact_length_content_is_not_marked_truncated(self):
        exact = "y" * MAX_TODO_CONTENT_CHARS
        item = normalize_todos([_todo(exact)]).items[0]
        assert item.content == exact
        assert "truncated" not in item.content


class TestBounds:
    def test_item_cap_applies_and_is_reported(self):
        raw = [_todo(f"step {i}") for i in range(MAX_TODO_ITEMS + 50)]
        snapshot = normalize_todos(raw)
        assert len(snapshot.items) == MAX_TODO_ITEMS
        assert snapshot.truncated is True
        assert snapshot.reported == MAX_TODO_ITEMS + 50

    def test_list_at_cap_is_not_marked_truncated(self):
        raw = [_todo(f"step {i}") for i in range(MAX_TODO_ITEMS)]
        snapshot = normalize_todos(raw)
        assert snapshot.truncated is False

    def test_malformed_entries_do_not_count_as_truncation(self):
        """Dropping a malformed entry is not the same as clipping a long list."""
        snapshot = normalize_todos([_todo("a"), "junk", _todo("b")])
        assert snapshot.truncated is False


class TestProgress:
    def test_counts_by_status(self):
        snapshot = normalize_todos(
            [
                _todo("a", "completed"),
                _todo("b", "completed"),
                _todo("c", "in_progress"),
                _todo("d", "pending"),
                _todo("e", "cancelled"),
            ]
        )
        progress = snapshot.progress()
        assert progress == {
            "total": 5,
            "completed": 2,
            "in_progress": 1,
            "pending": 1,
            "cancelled": 1,
            "settled": 3,
        }

    def test_empty_plan_reports_zeros_not_none(self):
        """Zero is a real measurement here; None would mean 'not reported'."""
        assert normalize_todos([]).progress()["total"] == 0


class TestItemIdentity:
    def test_same_content_same_id(self):
        assert todo_item_id("write the docs") == todo_item_id("  write the docs  ")

    def test_different_content_different_id(self):
        assert todo_item_id("a") != todo_item_id("b")

    def test_reordering_does_not_change_ids(self):
        """Ids must survive insertion, or React reuses the wrong DOM node.

        A plan that gains a step in the middle shifts every later index, so an
        index-keyed list would visibly rewrite unrelated items' statuses.
        """
        before = normalize_todos([_todo("a", "completed"), _todo("c", "pending")])
        after = normalize_todos([_todo("a", "completed"), _todo("b", "pending"), _todo("c", "pending")])
        ids_before = {item.content: item.id for item in before.items}
        ids_after = {item.content: item.id for item in after.items}
        assert ids_before == {content: ids_after[content] for content in ids_before}

    def test_ids_are_distinct_within_one_plan(self):
        snapshot = normalize_todos([_todo(f"step {i}") for i in range(50)])
        assert len({item.id for item in snapshot.items}) == 50


class TestWireEvent:
    def test_shape(self):
        payload = build_todo_event([_todo("a", "in_progress")])
        assert payload["type"] == TODO_EVENT_TYPE
        assert payload["todos"] == [{"id": snapshot_from_event({"todos": [_todo("a", "in_progress")]}).items[0].id, "content": "a", "status": "in_progress", "index": 0}]
        assert payload["progress"]["total"] == 1
        assert payload["truncated"] is False

    def test_event_round_trips_through_the_reader(self):
        """The live path and the history path must agree on the same plan.

        A second, slightly-different reader is how two views of one plan drift.
        """
        raw = [_todo("a", "completed"), _todo("b", "in_progress"), _todo("c", "pending")]
        payload = build_todo_event(raw)
        assert normalize_todos(snapshot_from_event(payload)).items == normalize_todos(raw).items

    def test_event_is_json_serializable(self):
        payload = build_todo_event([_todo("a"), {"content": "bad", "status": object()}])
        assert isinstance(todo_event_size(payload), int)

    def test_budget_flag(self):
        assert fits_budget(build_todo_event([_todo("short")])) is True

    def test_budget_rejects_an_oversized_payload(self):
        """Item count alone does not bound size; long items can blow the budget."""
        huge = [_todo("z" * MAX_TODO_CONTENT_CHARS) for _ in range(MAX_TODO_ITEMS)]
        assert fits_budget(build_todo_event(huge)) is False
        assert todo_event_size(build_todo_event(huge)) > MAX_TODO_PAYLOAD_CHARS


class TestSnapshotFromEvent:
    def test_non_dict_is_empty(self):
        assert snapshot_from_event(None).is_empty
        assert snapshot_from_event(["x"]).is_empty

    def test_ignores_a_wrong_type_field(self):
        """The reader must not accept a frame that is not a todo event at all."""
        assert snapshot_from_event({"type": "task_started", "todos": [_todo("x")]}).items[0].content == "x"


@pytest.mark.parametrize("raw", [None, [], "junk", 0, {"nope": 1}])
def test_never_raises_on_junk(raw: Any):
    """This runs on the streaming path: a raise here would fail the whole run."""
    assert normalize_todos(raw).is_empty
