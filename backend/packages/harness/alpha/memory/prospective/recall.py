"""Recall and bounded prompt rendering for prospective-memory items."""

from __future__ import annotations

import html
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import ProspectiveConfig
from .models import ProspectiveItem, ProspectiveStatus, TriggerContext
from .triggers import evaluate_trigger_detailed

if TYPE_CHECKING:
    from .store import ProspectiveStore

_MAX_CONTENT_CHARS = 500


@dataclass(slots=True)
class RecallResult:
    """Items plus an honest read/evaluation status for a recall seam."""

    status: str
    items: list[ProspectiveItem] = field(default_factory=list)
    reason: str = ""
    error: str = ""
    text: str = ""

    @property
    def ok(self) -> bool:
        """Whether recall completed without a gate/storage failure."""

        return self.status in {"succeeded", "empty"}


@dataclass(slots=True)
class ProspectiveSummary:
    """Structured status counts for non-prompt surfaces."""

    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    active: int = 0
    due: int = 0
    surfaced: int = 0

    @property
    def empty(self) -> bool:
        """Whether there are no records at all."""

        return self.total == 0

    def to_text(self) -> str:
        """Render a concise, truthful status sentence."""

        if self.empty:
            return "Prospective memory: no items."
        return f"Prospective memory: {self.active} active, {self.due} due, {self.surfaced} surfaced, {self.total} total."


def _store_from(value: Any) -> ProspectiveStore | None:
    if value is None:
        return None
    if hasattr(value, "list_items_result") and hasattr(value, "list_items"):
        return value
    return None


def _coerce_items(items: Iterable[ProspectiveItem | Mapping[str, Any]] | ProspectiveItem | Mapping[str, Any] | None) -> tuple[list[ProspectiveItem], str]:
    if items is None:
        return [], "items_missing"
    if isinstance(items, (ProspectiveItem, Mapping)):
        items = [items]
    result: list[ProspectiveItem] = []
    invalid = False
    for raw in items:
        if isinstance(raw, ProspectiveItem):
            result.append(raw.model_copy(deep=True))
            continue
        try:
            result.append(ProspectiveItem.model_validate(dict(raw)))
        except (TypeError, ValueError):
            invalid = True
    return result, "invalid_item" if invalid else ""


def _resolve_store_and_items(
    user_or_store: Any,
    items: Iterable[ProspectiveItem | Mapping[str, Any]] | None,
    store: ProspectiveStore | None,
) -> tuple[ProspectiveStore | None, list[ProspectiveItem], str, str]:
    active_store = store or _store_from(user_or_store)
    if active_store is not None:
        requested_user = user_or_store if isinstance(user_or_store, str) else None
        read = active_store.list_items_result(requested_user)
        return active_store, read.items, read.status, read.error
    resolved, reason = _coerce_items(items)
    return None, resolved, reason, ""


def due_items_result(
    user: str | ProspectiveStore | None = None,
    now: float | None = None,
    *,
    agent_name: str | None = None,
    store: ProspectiveStore | None = None,
    items: Iterable[ProspectiveItem | Mapping[str, Any]] | None = None,
) -> RecallResult:
    """Return due, unexpired, first-view items with explicit read status."""

    if items is None and user is not None and _store_from(user) is None and not isinstance(user, str):
        items = user
        user = None
    timestamp = time.time() if now is None else float(now)
    active_store, candidate_items, read_status, read_error = _resolve_store_and_items(user, items, store)
    if read_status == "items_missing":
        return RecallResult(status="failed", reason="store_or_items_required")
    if read_status == "failed":
        return RecallResult(status="failed", reason="store_read_failed", error=read_error)
    if read_status == "recovered":
        return RecallResult(status="recovered", reason="corrupt_document_preserved", error=read_error)
    if read_status not in {"", "succeeded", "empty"}:
        return RecallResult(status="failed", reason=read_status)
    user_id = user if isinstance(user, str) else None
    if active_store is not None and not active_store.enabled:
        return RecallResult(status="skipped", reason="disabled")
    selected = [
        item
        for item in candidate_items
        if item.is_active and (user_id is None or item.user_id == user_id) and (agent_name is None or item.agent_name == agent_name) and item.surfaced_at is None and item.is_due_at(timestamp) and not item.is_expired_at(timestamp)
    ]
    selected.sort(key=lambda item: (-item.priority, item.due_at if item.due_at is not None else float("inf"), item.id))
    if not selected:
        return RecallResult(status="empty", reason="no_due_items")
    return RecallResult(status="succeeded", items=selected)


def due_items(
    user: str | ProspectiveStore | None = None,
    now: float | None = None,
    *,
    agent_name: str | None = None,
    store: ProspectiveStore | None = None,
    items: Iterable[ProspectiveItem | Mapping[str, Any]] | None = None,
) -> list[ProspectiveItem]:
    """Convenience list form of :func:`due_items_result`."""

    return due_items_result(user, now, agent_name=agent_name, store=store, items=items).items


def items_for_trigger_result(
    context: TriggerContext | Mapping[str, Any] | ProspectiveStore,
    items: Iterable[ProspectiveItem | Mapping[str, Any]] | ProspectiveStore | None = None,
    *,
    user_id: str | None = None,
    agent_name: str | None = None,
    store: ProspectiveStore | None = None,
    now: float | None = None,
) -> RecallResult:
    """Return active, never-surfaced obligations matching a trigger context."""

    active_store = store
    actual_context: Any = context
    if _store_from(context) is not None:
        active_store = context
        actual_context = items
        if actual_context is None:
            return RecallResult(status="failed", reason="trigger_context_missing")
    if active_store is None and _store_from(items) is not None:
        active_store = items
        items = None
    try:
        trigger_context = actual_context if isinstance(actual_context, TriggerContext) else TriggerContext.model_validate(dict(actual_context))
    except (TypeError, ValueError) as exc:
        return RecallResult(status="failed", reason="trigger_context_invalid", error=str(exc))
    if active_store is not None:
        read = active_store.list_items_result(user_id or trigger_context.user_id)
        if read.status == "failed":
            return RecallResult(status="failed", reason="store_read_failed", error=read.error or read.reason)
        if read.status == "recovered":
            return RecallResult(status="recovered", reason="corrupt_document_preserved", error=read.error)
        if read.status == "skipped":
            return RecallResult(status="skipped", reason="disabled")
        candidate_items = read.items
    else:
        candidate_items, reason = _coerce_items(items)
        if reason:
            return RecallResult(status="failed", reason="store_or_items_required")
    effective_user = user_id or trigger_context.user_id
    effective_agent = agent_name or trigger_context.agent_name
    timestamp = time.time() if now is None else float(now)
    selected: list[ProspectiveItem] = []
    for item in candidate_items:
        if not item.is_active or item.surfaced_at is not None or item.trigger is None:
            continue
        if effective_user is not None and item.user_id != effective_user:
            continue
        if effective_agent is not None and item.agent_name != effective_agent:
            continue
        if item.due_at is not None and item.is_expired_at(timestamp):
            continue
        evaluation = evaluate_trigger_detailed(item.trigger, trigger_context)
        if evaluation.matched:
            selected.append(item.model_copy(deep=True))
    selected.sort(key=lambda item: (-item.priority, item.created_at, item.id))
    if not selected:
        return RecallResult(status="empty", reason="no_trigger_matches")
    return RecallResult(status="succeeded", items=selected)


def items_for_trigger(
    context: TriggerContext | Mapping[str, Any] | ProspectiveStore,
    items: Iterable[ProspectiveItem | Mapping[str, Any]] | ProspectiveStore | None = None,
    *,
    user_id: str | None = None,
    agent_name: str | None = None,
    store: ProspectiveStore | None = None,
    now: float | None = None,
) -> list[ProspectiveItem]:
    """Convenience list form of :func:`items_for_trigger_result`."""

    return items_for_trigger_result(
        context,
        items,
        user_id=user_id,
        agent_name=agent_name,
        store=store,
        now=now,
    ).items


def _render_item(item: ProspectiveItem) -> str:
    content = item.content
    if len(content) > _MAX_CONTENT_CHARS:
        content = f"{content[: _MAX_CONTENT_CHARS - 3]}..."
    due = "" if item.due_at is None else f" due_at={item.due_at:g}"
    return f"- [{item.kind.value} | priority={item.priority}{due}] {html.escape(content, quote=False)}"


def render_block_result(
    items: Iterable[ProspectiveItem | Mapping[str, Any]] | RecallResult | ProspectiveStore,
    *,
    config: ProspectiveConfig | None = None,
    max_surfaced: int | None = None,
    include_surfaced: bool = False,
    title: str = "### Prospective memory",
    now: float | None = None,
) -> RecallResult:
    """Render a bounded block and retain an empty/error status explicitly.

    This formatter does not authorize a read; the store/caller owns the
    enable gate.  Passing already-authorized snapshots keeps it useful in
    hermetic status tests and embedding applications.
    """

    del now  # Rendering is state-surface formatting; expiry is handled by recall.
    if isinstance(items, RecallResult):
        if items.status not in {"succeeded", "empty"}:
            return items
        source_items = items.items
        effective_config = config or ProspectiveConfig()
    elif _store_from(items) is not None:
        source_items = items.list_items()
        effective_config = config or items.config
    else:
        source_items, reason = _coerce_items(items)
        if reason:
            return RecallResult(status="failed", reason=reason)
        effective_config = config or ProspectiveConfig()
    limit = effective_config.max_surfaced_per_recall if max_surfaced is None else int(max_surfaced)
    limit = max(0, limit)
    selected = [item for item in source_items if item.status in {ProspectiveStatus.PENDING, ProspectiveStatus.FIRED} and (include_surfaced or item.surfaced_at is None)]
    selected.sort(key=lambda item: (-item.priority, item.due_at if item.due_at is not None else float("inf"), item.id))
    selected = selected[:limit]
    if not selected:
        return RecallResult(status="empty", reason="no_renderable_items", text="")
    lines = [title]
    lines.extend(_render_item(item) for item in selected)
    return RecallResult(status="succeeded", items=selected, text="\n".join(lines))


def render_block(
    items: Iterable[ProspectiveItem | Mapping[str, Any]] | RecallResult | ProspectiveStore,
    *,
    config: ProspectiveConfig | None = None,
    max_surfaced: int | None = None,
    include_surfaced: bool = False,
    title: str = "### Prospective memory",
) -> str:
    """Return only real rendered obligations; an empty store returns ``""``."""

    return render_block_result(
        items,
        config=config,
        max_surfaced=max_surfaced,
        include_surfaced=include_surfaced,
        title=title,
    ).text


def render_due_block(
    store: ProspectiveStore,
    user_id: str | None,
    now: float | None = None,
    *,
    agent_name: str | None = None,
) -> str:
    """Render the due slice for a user without fabricating an empty block."""

    return render_block(due_items_result(user_id, now, agent_name=agent_name, store=store), config=store.config)


def render_active_block(
    store: ProspectiveStore,
    user_id: str | None,
    *,
    agent_name: str | None = None,
) -> str:
    """Render active pending/fired items for the next prompt turn."""

    return render_block(store.list_items(user_id, agent_name=agent_name), config=store.config)


def render_trigger_block(
    store: ProspectiveStore,
    context: TriggerContext | Mapping[str, Any],
    *,
    user_id: str | None = None,
    agent_name: str | None = None,
) -> str:
    """Render trigger matches for a user without changing lifecycle state."""

    return render_block(
        items_for_trigger_result(context, store, user_id=user_id, agent_name=agent_name),
        config=store.config,
    )


def summary_counts(
    items: Iterable[ProspectiveItem | Mapping[str, Any]],
    *,
    now: float | None = None,
) -> ProspectiveSummary:
    """Build structured status counts for a status surface."""

    timestamp = time.time() if now is None else float(now)
    resolved, _ = _coerce_items(items)
    counts = {status.value: 0 for status in ProspectiveStatus}
    for item in resolved:
        counts[item.status.value] += 1
    return ProspectiveSummary(
        counts=counts,
        total=len(resolved),
        active=sum(item.is_active for item in resolved),
        due=sum(item.is_active and item.surfaced_at is None and item.is_due_at(timestamp) and not item.is_expired_at(timestamp) for item in resolved),
        surfaced=sum(item.surfaced_at is not None for item in resolved),
    )


def summarize(items: Iterable[ProspectiveItem | Mapping[str, Any]], *, now: float | None = None) -> str:
    """Return a concise status sentence, explicitly honest when empty."""

    return summary_counts(items, now=now).to_text()


__all__ = [
    "ProspectiveSummary",
    "RecallResult",
    "due_items",
    "due_items_result",
    "items_for_trigger",
    "items_for_trigger_result",
    "render_block",
    "render_block_result",
    "render_active_block",
    "render_due_block",
    "render_trigger_block",
    "summarize",
    "summary_counts",
]
