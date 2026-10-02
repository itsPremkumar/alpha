"""Live task-list projection for `write_todos`.

`TodoMiddleware` already keeps the authoritative list in the `todos` thread-state
channel, and `write_todos` already replaces it wholesale on every call. What was
missing is the *delivery*: the Gateway's `custom` stream never carried the list, so
the frontend had a `TodoBlock` component and a `ChatMessage.todos` field with
nothing to render. This module is the missing half - it turns a `write_todos`
payload into one wire event, and folds a stored snapshot back out of persisted
history so a reloaded thread shows its plan again.

Three rules govern everything here, and they are the reason this is a separate
module rather than inline middleware code:

1. **A task list is an observation, not a promise.** The status shown is the
   status the model reported. `cancelled` exists because the model can drop or
   revise items, and the UI must be able to say so rather than render a dropped
   item as permanently pending.

2. **Never infer progress the run did not report.** There is deliberately no
   "percent complete" derived from file mtimes, tool counts or elapsed time. The
   only fields that reach the wire are fields the model actually sent.

3. **Bounded, or it is a denial-of-service surface.** The list arrives inside an
   SSE frame on every single model turn, so both the item count and each item's
   text are capped. A truncated item says it was truncated rather than reading as
   a shorter task.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Final, Literal

#: Wire event name. `contracts/run_event_stream_contract.json` classifies
#: `add_event_type` as an additive, non-breaking change, so a new `todos_*` name
#: needs no contract edit. The name is prefixed to sit beside the existing
#: `task_*` subagent events without colliding with them.
TODO_EVENT_TYPE: Final[str] = "todos_updated"

TodoStatus = Literal["pending", "in_progress", "completed", "cancelled"]

#: The four states a plan item can hold. `LangChain's Todo` is
#: `pending | in_progress | completed`; `cancelled` is Alpha's addition for an item
#: the model removed as no longer relevant, because the tool contract explicitly
#: allows dropping items and a UI that cannot express "dropped" will keep showing
#: work that was abandoned on purpose.
TODO_STATUSES: Final[frozenset[str]] = frozenset({"pending", "in_progress", "completed", "cancelled"})

#: Cap on items per event. A real plan is under a dozen; 200 is a runaway guard,
#: not a design target. The tool description already tells the model to keep plans
#: short, so hitting this means the model ignored its instructions.
MAX_TODO_ITEMS: Final[int] = 200

#: Cap on one item's text. Long enough for a real step, short enough that 200
#: items still fit in a frame the SSE decoder will accept.
MAX_TODO_CONTENT_CHARS: Final[int] = 500

#: Cap on the whole serialized list. The frontend applies its own frame ceiling in
#: `sse-reducer.ts`; this is the backend-side half of the same bound.
MAX_TODO_PAYLOAD_CHARS: Final[int] = 32_000


def _clip(text: str, limit: int) -> str:
    """Clip *text* to *limit*, saying so when it does not fit.

    Silently shortening a task description would make a truncated step look like a
    genuinely short one, which is exactly the kind of quiet misrepresentation this
    codebase treats as a bug.
    """
    if len(text) <= limit:
        return text
    return f"{text[:limit]} [+{len(text) - limit} chars truncated]"


def todo_item_id(content: str) -> str:
    """Stable identity for a plan item, derived from its text.

    `write_todos` replaces the whole list on every call and the tool contract tells
    the model never to rewrite an item it has already completed, so content is the
    only durable key available. Index is unusable: the model may reorder or insert
    an item mid-list, which would renumber every item after it and make React reuse
    the wrong DOM node - visibly, the UI would appear to change an unrelated task's
    status.

    A short hash is enough. Collisions between two genuinely different plan items
    are not a correctness concern here (both would simply share a node and both
    still render their own text), and a longer id would only bloat every frame.
    """
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class TodoItem:
    """One normalized plan item. Every field here was present on the wire."""

    id: str
    content: str
    status: TodoStatus
    index: int


@dataclass(frozen=True, slots=True)
class TodoSnapshot:
    """A whole plan, plus the counters the UI needs to render honestly.

    `truncated` is carried rather than inferred so the UI can say "showing 200 of
    more" instead of presenting a silently-clipped list as if it were complete.
    """

    items: tuple[TodoItem, ...] = ()
    #: How many items the model actually sent, before any cap was applied.
    reported: int = 0
    #: True when `reported` exceeded what we are willing to send.
    truncated: bool = False
    #: Total per-status counts, computed over the items we are sending.
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.items

    def progress(self) -> dict[str, int]:
        """Settled / total, plus a breakdown by status.

        Deliberately not a single percentage. A plan that is 1-of-3 done and a plan
        that is 9-of-10 done both render as a bar, but the bar alone hides the
        difference between "almost finished" and "barely started", which is the
        thing a user watching a long task actually needs to know.
        """
        total = len(self.items)
        return {
            "total": total,
            "completed": self.counts.get("completed", 0),
            "in_progress": self.counts.get("in_progress", 0),
            "pending": self.counts.get("pending", 0),
            "cancelled": self.counts.get("cancelled", 0),
            "settled": self.counts.get("completed", 0) + self.counts.get("cancelled", 0),
        }


def _normalize_status(raw: Any) -> TodoStatus:
    """Coerce a model-reported status to a known one.

    An unrecognized status becomes `pending` rather than being dropped or guessed
    at: the item genuinely exists and is genuinely not-done, so `pending` is the
    only claim that cannot overstate progress. Dropping it would make a plan look
    shorter than the model wrote it.
    """
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in TODO_STATUSES:
            return value  # type: ignore[return-value]
    return "pending"


def _normalize_item(raw: Any, index: int) -> TodoItem | None:
    """Build one `TodoItem` from a raw entry, or None if it is not an item.

    A non-mapping entry (a bare string, a number) is skipped rather than coerced:
    there is no text to show, so there is nothing honest to render.

    An entry that is already a `TodoItem` passes straight through. Normalization
    has to be idempotent, because a snapshot genuinely gets projected twice: the
    history path pulls an item list out of a stored event and re-normalizes it,
    and a caller who forwards our own output should not silently lose every item.
    """
    if isinstance(raw, TodoItem):
        return raw
    if not isinstance(raw, dict):
        return None
    content = raw.get("content")
    if not isinstance(content, str) or not content.strip():
        # An item with no text is not an observable task. Skip it instead of
        # rendering an empty checkbox the user cannot act on or read.
        return None
    return TodoItem(
        id=todo_item_id(content),
        content=_clip(content.strip(), MAX_TODO_CONTENT_CHARS),
        status=_normalize_status(raw.get("status")),
        index=index,
    )


def normalize_todos(raw: Any) -> TodoSnapshot:
    """Turn a `todos` value from state, a tool payload, or a stored row into a snapshot.

    Accepts anything: the state channel holds `list[Todo]`, a raw `write_todos`
    payload holds the same shape, and a persisted row may hold a dict wrapper. A
    malformed value yields an empty snapshot rather than raising, because this runs
    on the streaming path where an exception would take down the run instead of
    degrading one panel.
    """
    if isinstance(raw, dict):
        raw = raw.get("todos", raw.get("items"))
    if isinstance(raw, TodoSnapshot):
        # Re-projecting an existing snapshot is legitimate: the history path reads
        # a stored event, projects it, and hands the result to code that projects
        # again. Idempotence keeps that from silently emptying the plan.
        raw = list(raw.items)
    if not isinstance(raw, list):
        return TodoSnapshot()

    reported = len(raw)
    items: list[TodoItem] = []
    for entry in raw[:MAX_TODO_ITEMS]:
        item = _normalize_item(entry, len(items))
        if item is not None:
            items.append(item)

    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1

    return TodoSnapshot(
        items=tuple(items),
        reported=reported,
        # Only a cap on *items* makes the list incomplete. A dropped malformed
        # entry is not truncation - the model never wrote a task there.
        truncated=reported > MAX_TODO_ITEMS,
        counts=counts,
    )


def todo_event(snapshot: TodoSnapshot) -> dict[str, Any]:
    """Serialize a snapshot into the `todos_updated` wire payload.

    Field names are snake_case to match every other custom event on this stream
    (`task_started` uses `task_id`/`description`/`model_name`).
    """
    progress = snapshot.progress()
    return {
        "type": TODO_EVENT_TYPE,
        "todos": [{"id": item.id, "content": item.content, "status": item.status, "index": item.index} for item in snapshot.items],
        "progress": progress,
        # Reported alongside, not instead of, the caps: the frontend can render an
        # honest "showing 200 of 340" without re-deriving anything.
        "reported": snapshot.reported,
        "truncated": snapshot.truncated,
    }


def build_todo_event(raw: Any) -> dict[str, Any]:
    """Normalize then serialize - the one call a producer needs."""
    return todo_event(normalize_todos(raw))


def todo_event_size(payload: dict[str, Any]) -> int:
    """Serialized size of a payload, for the caller's frame budget."""
    import json

    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def fits_budget(payload: dict[str, Any]) -> bool:
    """Whether a payload is within the frame budget.

    A list can be within the item cap yet still exceed the character cap if the
    model wrote very long items, so the character bound is checked separately
    rather than assumed to follow from the item count.
    """
    return todo_event_size(payload) <= MAX_TODO_PAYLOAD_CHARS


def snapshot_from_event(payload: Any) -> TodoSnapshot:
    """Rebuild a snapshot from a received `todos_updated` payload.

    Used by the history path, where a stored event must be re-rendered through the
    *same* normalization as a live one. Rendering a stored list by a second,
    slightly different code path is how two views of the same plan drift apart.
    """
    if not isinstance(payload, dict):
        return TodoSnapshot()
    return normalize_todos(payload.get("todos"))
