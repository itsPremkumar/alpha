"""Thread-safe, per-user JSON store for prospective-memory items."""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.agents.memory.l1.paths import atomic_write_text, safe_segment
from alpha.memory._store_format import (
    STORE_FORMAT_UNSUPPORTED,
    StoreFormatVerdict,
    classify_store_format,
    format_disclosure,
)

from .config import ProspectiveConfig, load_prospective_config, prospective_root
from .lifecycle import (
    cancel as lifecycle_cancel,
)
from .lifecycle import (
    complete as lifecycle_complete,
)
from .lifecycle import (
    create_item as lifecycle_create_item,
)
from .lifecycle import (
    expand_recurring,
)
from .lifecycle import (
    expire_due as lifecycle_expire_due,
)
from .lifecycle import (
    fire as lifecycle_fire,
)
from .lifecycle import (
    mark_surfaced as lifecycle_mark_surfaced,
)
from .models import LifecycleResult, ProspectiveItem, ProspectiveKind, ProspectiveStatus, TriggerSpec
from .provenance import ProvenanceResult, append_event

logger = logging.getLogger(__name__)
_SCHEMA_VERSION = 1
_STORE_ID = "prospective.items"


class ProspectiveStoreUnavailable(RuntimeError):
    """Raised when a write is refused because the scope is not safely writable."""


def _now_value(now: float | None) -> float | None:
    try:
        value = time.time() if now is None else float(now)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


@dataclass(slots=True)
class StoreReadResult:
    """Disclosed result for a read that may have recovered a corrupt scope."""

    status: str
    items: list[ProspectiveItem] = field(default_factory=list)
    reason: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether the read completed without an unresolved storage error."""

        return self.status in {"succeeded", "recovered", "empty"}


@dataclass(slots=True)
class ExpiryBatchResult:
    """Summary of one ``expire_due`` sweep."""

    status: str
    items: list[ProspectiveItem] = field(default_factory=list)
    results: list[LifecycleResult] = field(default_factory=list)
    reason: str = ""
    error: str = ""
    provenance_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the sweep itself completed without a storage failure."""

        return self.status in {"succeeded", "skipped", "empty"}

    @property
    def expired(self) -> list[ProspectiveItem]:
        """Alias for the changed expired items."""

        return self.items

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> ProspectiveItem:
        return self.items[index]


def prospective_user_dir(root: Path, user_id: str | None) -> Path:
    """Return the isolated per-user prospective directory."""

    return root / "users" / safe_segment(user_id or "default") / "prospective"


def prospective_items_path(root: Path, user_id: str | None) -> Path:
    """Return the one JSON document owned by a user."""

    return prospective_user_dir(root, user_id) / "items.json"


class ProspectiveStore:
    """Atomic, thread-safe prospective items partitioned by user.

    A user is the persistence scope, so an agent name remains a field on each
    item and can be filtered without allowing a write for one user to leak
    into another user's document.  Every mutation loads, changes, and persists
    while holding that user's re-entrant lock.
    """

    def __init__(
        self,
        config: ProspectiveConfig | Mapping[str, Any] | None = None,
        *,
        storage_path: str | Path | None = None,
        enabled: bool | None = None,
    ) -> None:
        if config is None:
            if enabled is None:
                loaded = load_prospective_config(storage_path=storage_path)
            else:
                loaded = ProspectiveConfig(
                    enabled=enabled,
                    storage_path=str(storage_path) if storage_path is not None else None,
                )
        elif isinstance(config, Mapping):
            loaded = ProspectiveConfig.model_validate(config)
        else:
            loaded = config
        if storage_path is not None and loaded.storage_path is None:
            loaded = loaded.model_copy(update={"storage_path": str(storage_path)})
        self.config = loaded
        self._root = prospective_root(loaded.storage_path)
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}
        self._blocked: set[str] = set()
        self._format_refusals: dict[str, StoreFormatVerdict] = {}

    @property
    def root(self) -> Path:
        """Resolved state root used by this store."""

        return self._root

    @property
    def enabled(self) -> bool:
        """Whether this store permits public mutations/recall."""

        return bool(self.config.enabled)

    def _key(self, user_id: str | None) -> str:
        return str(user_id or "default")

    def _lock_for(self, user_id: str | None) -> threading.RLock:
        key = self._key(user_id)
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    def _path_for(self, user_id: str | None) -> Path:
        return prospective_items_path(self._root, user_id)

    def _empty_document(self) -> dict[str, Any]:
        return {"schema": _SCHEMA_VERSION, "items": []}

    def _load_unlocked(self, user_id: str | None) -> tuple[dict[str, Any], str, str]:
        path = self._path_for(user_id)
        key = self._key(user_id)
        if not path.exists():
            self._blocked.discard(key)
            self._format_refusals.pop(key, None)
            return self._empty_document(), "empty", ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            if isinstance(exc, OSError):
                self._blocked.add(key)
                logger.error("Prospective store: could not read document %s (%s)", path, exc)
                return self._empty_document(), "failed", str(exc)
            return self._preserve_corrupt(key, path, exc), "recovered", str(exc)

        # The bytes parsed. A marker this build does not implement means another
        # build owns the file; quarantining it would destroy that data.
        verdict = classify_store_format(store=_STORE_ID, path=path, raw=payload, supported_version=_SCHEMA_VERSION)
        if verdict.refusal:
            self._format_refusals[key] = verdict
            self._blocked.add(key)
            logger.error(
                "Prospective store: refusing %s (%s); document left in place and writes blocked",
                verdict.path,
                format_disclosure(verdict),
            )
            return self._empty_document(), STORE_FORMAT_UNSUPPORTED, format_disclosure(verdict)
        self._format_refusals.pop(key, None)
        try:
            if not isinstance(payload, Mapping):
                raise ValueError("document root must be an object")
            raw_items = payload.get("items")
            if not isinstance(raw_items, list):
                raise ValueError("items must be a list")
            items = [ProspectiveItem.model_validate(item) for item in raw_items]
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return self._preserve_corrupt(key, path, exc), "recovered", str(exc)
        return {"schema": _SCHEMA_VERSION, "items": items}, "succeeded", ""

    def _preserve_corrupt(self, key: str, path: Path, exc: BaseException) -> dict[str, Any]:
        """Preserve genuinely unreadable bytes; never used for a version mismatch."""

        backup = path.with_name(f"{path.name}.corrupt-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
        try:
            path.replace(backup)
        except OSError as preserve_exc:
            self._blocked.add(key)
            logger.error(
                "Prospective store: corrupt document %s could not be preserved as %s: %s; original=%s",
                path,
                backup.name,
                preserve_exc,
                exc,
            )
            return self._empty_document()
        self._blocked.discard(key)
        logger.error("Prospective store: corrupt document %s preserved as %s; scope restarts empty (%s)", path, backup.name, exc)
        return self._empty_document()

    def format_refusal(self, user_id: str | None = None) -> StoreFormatVerdict | None:
        """Return the version refusal held for a user, or ``None``."""

        return self._format_refusals.get(self._key(user_id))

    def _persist_unlocked(self, user_id: str | None, document: Mapping[str, Any]) -> None:
        # The single choke point every mutation funnels through. Refusing here
        # is what makes a version refusal non-destructive: nothing can reach
        # the live path and republish this build's older format over a document
        # a newer build still owns. (The sibling `_blocked` set in this module
        # is maintained but never read, so it cannot do this job.)
        verdict = self._format_refusals.get(self._key(user_id))
        if verdict is not None:
            raise ProspectiveStoreUnavailable(format_disclosure(verdict))
        payload = {
            "schema": _SCHEMA_VERSION,
            "items": [item.model_dump(mode="json") for item in document.get("items", [])],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False)
        atomic_write_text(self._path_for(user_id), text)

    @staticmethod
    def _copy_item(item: ProspectiveItem) -> ProspectiveItem:
        return item.model_copy(deep=True)

    @staticmethod
    def _filtered(
        items: Iterable[ProspectiveItem],
        *,
        user_id: str | None,
        agent_name: str | None,
        status: ProspectiveStatus | str | None,
    ) -> list[ProspectiveItem]:
        wanted_status: ProspectiveStatus | None = None
        if status is not None:
            try:
                wanted_status = status if isinstance(status, ProspectiveStatus) else ProspectiveStatus(status)
            except ValueError:
                return []
        selected = [item for item in items if (user_id is None or item.user_id == str(user_id)) and (agent_name is None or item.agent_name == agent_name) and (wanted_status is None or item.status is wanted_status)]
        selected.sort(key=lambda item: (-item.priority, item.due_at if item.due_at is not None else float("inf"), item.created_at, item.id))
        return [item.model_copy(deep=True) for item in selected]

    def _disabled(self) -> LifecycleResult:
        return LifecycleResult(status="skipped", reason="disabled")

    def _failed(self, reason: str, error: str, *, item: ProspectiveItem | None = None) -> LifecycleResult:
        return LifecycleResult(status="failed", item=item, reason=reason, error=error or reason)

    def _record_provenance(
        self,
        result: LifecycleResult,
        item: ProspectiveItem,
        event: str,
        *,
        reason: str,
        from_status: ProspectiveStatus | str | None,
        to_status: ProspectiveStatus | str | None,
        now: float,
    ) -> ProvenanceResult:
        provenance = append_event(
            item,
            event,
            reason=reason,
            from_status=from_status,
            to_status=to_status,
            config=self.config,
            now=now,
        )
        if result.provenance_status == "failed" and provenance.ok:
            return provenance
        if result.provenance_status == "failed" and not provenance.ok:
            result.provenance_error = "; ".join(part for part in (result.provenance_error, provenance.error) if part)
            return provenance
        result.provenance_status = provenance.status
        result.provenance_error = provenance.error
        return provenance

    def list_items_result(
        self,
        user_id: str | None = None,
        *,
        agent_name: str | None = None,
        status: ProspectiveStatus | str | None = None,
    ) -> StoreReadResult:
        """List a user scope and disclose gate/corruption/read outcomes."""

        if not self.enabled:
            return StoreReadResult(status="skipped", reason="disabled")
        with self._lock_for(user_id):
            try:
                document, load_status, error = self._load_unlocked(user_id)
            except Exception as exc:  # noqa: BLE001 - a read must not break a turn
                return StoreReadResult(status="failed", reason="storage_read_failed", error=str(exc))
            items = self._filtered(document["items"], user_id=user_id, agent_name=agent_name, status=status)
            if load_status == "failed":
                return StoreReadResult(status="failed", reason="corrupt_document_preservation_failed", error=error)
            if load_status == "recovered":
                return StoreReadResult(status="recovered", items=items, reason="corrupt_document_preserved", error=error)
            if not items:
                return StoreReadResult(status="empty", reason="no_matching_items")
            return StoreReadResult(status="succeeded", items=items)

    def list_items(
        self,
        user_id: str | None = None,
        *,
        agent_name: str | None = None,
        status: ProspectiveStatus | str | None = None,
    ) -> list[ProspectiveItem]:
        """Return a defensive snapshot; use ``*_result`` when status matters."""

        return self.list_items_result(user_id, agent_name=agent_name, status=status).items

    def get_result(self, item_id: str, *, user_id: str | None = None) -> StoreReadResult:
        """Read one item with explicit status/reason disclosure."""

        if not self.enabled:
            return StoreReadResult(status="skipped", reason="disabled")
        listed = self.list_items_result(user_id)
        for item in listed.items:
            if item.id == item_id:
                return StoreReadResult(status="succeeded", items=[item])
        if listed.status == "failed":
            return listed
        return StoreReadResult(status="failed", reason="not_found", error=f"item {item_id!r} not found")

    def get(self, item_id: str, *, user_id: str | None = None) -> ProspectiveItem | None:
        """Return one item or ``None`` for a missing/disabled scope."""

        result = self.get_result(item_id, user_id=user_id)
        return result.items[0] if result.items else None

    def count(self, user_id: str | None = None) -> int:
        """Count all records in one user's scope."""

        return len(self.list_items(user_id))

    def create(
        self,
        *,
        user_id: str | None = None,
        content: str = "",
        kind: ProspectiveKind | str = ProspectiveKind.REMINDER,
        agent_name: str | None = None,
        priority: int | None = None,
        due_at: float | None = None,
        trigger: TriggerSpec | Mapping[str, Any] | None = None,
        source: str | dict[str, Any] | None = None,
        linked_record_ids: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        now: float | None = None,
        item_id: str | None = None,
        id: str | None = None,
        grace_until: float | None = None,
        recurrence_interval: float | None = None,
        recurring_max_occurrences: int | None = None,
        occurrence: int = 1,
        item: ProspectiveItem | None = None,
        reason: str = "created",
    ) -> LifecycleResult:
        """Create an item while enforcing both per-user limits and the gate."""

        if not self.enabled:
            return self._disabled()
        timestamp = _now_value(now)
        if timestamp is None:
            return self._failed("timestamp_invalid", "now must be a finite epoch")
        candidate_result = lifecycle_create_item(
            content,
            user_id=user_id,
            kind=kind,
            agent_name=agent_name,
            priority=priority,
            due_at=due_at,
            trigger=trigger,
            source=source,
            linked_record_ids=linked_record_ids,
            metadata=metadata,
            now=timestamp,
            item_id=item_id,
            id=id,
            grace_until=grace_until,
            recurrence_interval=recurrence_interval,
            recurring_max_occurrences=recurring_max_occurrences,
            occurrence=occurrence,
            config=self.config,
            item=item,
        )
        if not candidate_result.ok or candidate_result.item is None:
            return candidate_result
        candidate = candidate_result.item
        if user_id is not None and candidate.user_id != str(user_id):
            return self._failed("owner_mismatch", "item user_id does not match requested scope", item=candidate)
        with self._lock_for(candidate.user_id):
            try:
                document, load_status, error = self._load_unlocked(candidate.user_id)
            except Exception as exc:  # noqa: BLE001 - disclose read failure
                return self._failed("storage_read_failed", str(exc), item=candidate)
            if load_status == "failed":
                return self._failed("corrupt_document_preservation_failed", error, item=candidate)
            items = list(document["items"])
            if any(item.id == candidate.id for item in items):
                return LifecycleResult(
                    status="refused",
                    item=candidate,
                    reason="duplicate_item_id",
                )
            if len(items) >= self.config.max_items_per_user:
                return LifecycleResult(
                    status="refused",
                    item=candidate,
                    reason="max_items_per_user",
                    current=len(items),
                    limit=self.config.max_items_per_user,
                )
            active = sum(item.status in {ProspectiveStatus.PENDING, ProspectiveStatus.FIRED} for item in items)
            if active >= self.config.max_pending_per_user:
                return LifecycleResult(
                    status="refused",
                    item=candidate,
                    reason="max_pending_per_user",
                    current=active,
                    limit=self.config.max_pending_per_user,
                )
            items.append(candidate)
            try:
                self._persist_unlocked(candidate.user_id, {"items": items})
            except Exception as exc:  # noqa: BLE001 - storage errors are data, not turn failures
                return self._failed("storage_write_failed", str(exc), item=candidate)
            result = LifecycleResult(
                status="succeeded",
                item=self._copy_item(candidate),
                changed=True,
                event="created",
                reason=reason,
            )
            self._record_provenance(
                result,
                candidate,
                "created",
                reason=reason,
                from_status=None,
                to_status=candidate.status,
                now=timestamp,
            )
            return result

    def _mutate(
        self,
        item_id: str,
        user_id: str | None,
        operation: str,
        *,
        now: float | None,
        reason: str,
    ) -> LifecycleResult:
        if not self.enabled:
            return self._disabled()
        timestamp = _now_value(now)
        if timestamp is None:
            return self._failed("timestamp_invalid", "now must be a finite epoch")
        with self._lock_for(user_id):
            try:
                document, load_status, error = self._load_unlocked(user_id)
            except Exception as exc:  # noqa: BLE001
                return self._failed("storage_read_failed", str(exc))
            if load_status == "failed":
                return self._failed("corrupt_document_preservation_failed", error)
            items = list(document["items"])
            index = next((position for position, item in enumerate(items) if item.id == item_id), None)
            if index is None:
                return self._failed("not_found", f"item {item_id!r} not found")
            original = items[index]
            if operation == "cancel":
                transition = lifecycle_cancel(original, now=timestamp, reason=reason)
            elif operation == "complete":
                transition = lifecycle_complete(original, now=timestamp, reason=reason)
            elif operation == "fire":
                transition = lifecycle_fire(original, now=timestamp, reason=reason)
            elif operation == "surface":
                transition = lifecycle_mark_surfaced(original, now=timestamp, reason=reason)
            else:
                return self._failed("unknown_operation", f"unknown lifecycle operation {operation!r}", item=original)
            if not transition.ok or transition.item is None:
                return transition
            updated = transition.item
            if operation == "fire" and updated.due_at is None:
                try:
                    updated = updated.model_copy(
                        update={
                            "due_at": timestamp,
                            "grace_until": timestamp + self.config.expiry_grace_hours * 3600.0,
                        }
                    )
                except (TypeError, ValueError) as exc:
                    return self._failed("fire_validation_failed", str(exc), item=updated)
                transition.item = updated
            items[index] = updated
            expansion: LifecycleResult | None = None
            if operation == "complete" and updated.kind is ProspectiveKind.RECURRING:
                expansion = expand_recurring(updated, now=timestamp, config=self.config)
                if expansion.ok and expansion.item is not None:
                    if len(items) >= self.config.max_items_per_user:
                        expansion = LifecycleResult(
                            status="refused",
                            item=updated,
                            reason="max_items_per_user",
                            current=len(items),
                            limit=self.config.max_items_per_user,
                        )
                    else:
                        active = sum(item.status in {ProspectiveStatus.PENDING, ProspectiveStatus.FIRED} for item in items)
                        if active >= self.config.max_pending_per_user:
                            expansion = LifecycleResult(
                                status="refused",
                                item=updated,
                                reason="max_pending_per_user",
                                current=active,
                                limit=self.config.max_pending_per_user,
                            )
                        else:
                            items.append(expansion.item)
                            transition.next_item = expansion.item
            try:
                self._persist_unlocked(user_id, {"items": items})
            except Exception as exc:  # noqa: BLE001
                return self._failed("storage_write_failed", str(exc), item=updated)
            self._record_provenance(
                transition,
                updated,
                transition.event if operation != "surface" else "surfaced",
                reason=reason,
                from_status=original.status,
                to_status=updated.status,
                now=timestamp,
            )
            if expansion is not None:
                transition.details["recurring_status"] = expansion.status
                transition.details["recurring_reason"] = expansion.reason
                if expansion.error:
                    transition.details["recurring_error"] = expansion.error
                if expansion.ok and expansion.item is not None:
                    next_provenance = self._record_provenance(
                        transition,
                        expansion.item,
                        "created",
                        reason=expansion.reason,
                        from_status=None,
                        to_status=expansion.item.status,
                        now=timestamp,
                    )
                    if not next_provenance.ok and not transition.provenance_error:
                        transition.provenance_error = next_provenance.error
            return transition

    def cancel(
        self,
        item_id: str,
        *,
        user_id: str | None = None,
        now: float | None = None,
        reason: str = "cancelled by request",
    ) -> LifecycleResult:
        """Cancel an item without raising on illegal or missing state."""

        return self._mutate(item_id, user_id, "cancel", now=now, reason=reason)

    def complete(
        self,
        item_id: str,
        *,
        user_id: str | None = None,
        now: float | None = None,
        reason: str = "completed",
    ) -> LifecycleResult:
        """Complete an item and, when bounded, create its next occurrence."""

        return self._mutate(item_id, user_id, "complete", now=now, reason=reason)

    def fire(
        self,
        item_id: str,
        *,
        user_id: str | None = None,
        now: float | None = None,
        reason: str = "trigger fired",
    ) -> LifecycleResult:
        """Mark an item fired; an undated trigger becomes due immediately.

        This makes a trigger-bound obligation eligible for the next bounded
        recall pass without pretending it was a time-based reminder.
        """

        return self._mutate(item_id, user_id, "fire", now=now, reason=reason)

    def mark_surfaced(
        self,
        item_id: str,
        *,
        user_id: str | None = None,
        now: float | None = None,
        reason: str = "surfaced to the agent",
    ) -> LifecycleResult:
        """Record first surface; repeated calls return ``already_surfaced``."""

        return self._mutate(item_id, user_id, "surface", now=now, reason=reason)

    #: Readable aliases used by lifecycle-oriented callers.
    create_item = create
    mark_fired = fire
    surface = mark_surfaced

    def expire_due(
        self,
        now: float | None = None,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> ExpiryBatchResult:
        """Expire all due items whose grace window has elapsed."""

        if not self.enabled:
            return ExpiryBatchResult(status="skipped", reason="disabled")
        timestamp = _now_value(now)
        if timestamp is None:
            return ExpiryBatchResult(status="failed", reason="timestamp_invalid", error="now must be a finite epoch")
        with self._lock_for(user_id):
            try:
                document, load_status, error = self._load_unlocked(user_id)
            except Exception as exc:  # noqa: BLE001
                return ExpiryBatchResult(status="failed", reason="storage_read_failed", error=str(exc))
            if load_status == "failed":
                return ExpiryBatchResult(status="failed", reason="corrupt_document_preservation_failed", error=error)
            items = list(document["items"])
            changed: list[ProspectiveItem] = []
            results: list[LifecycleResult] = []
            provenance_errors: list[str] = []
            for index, item in enumerate(items):
                if user_id is not None and item.user_id != str(user_id):
                    continue
                if agent_name is not None and item.agent_name != agent_name:
                    continue
                transition = lifecycle_expire_due(item, now=timestamp)
                results.append(transition)
                if transition.ok and transition.item is not None:
                    items[index] = transition.item
                    changed.append(transition.item)
            if not changed:
                return ExpiryBatchResult(status="skipped", items=[], results=results, reason="no_due_items")
            try:
                self._persist_unlocked(user_id, {"items": items})
            except Exception as exc:  # noqa: BLE001
                return ExpiryBatchResult(status="failed", items=[], results=results, reason="storage_write_failed", error=str(exc))
            for original_result in results:
                if not original_result.ok or original_result.item is None:
                    continue
                item = original_result.item
                provenance = append_event(
                    item,
                    "expired",
                    reason=original_result.reason,
                    from_status=ProspectiveStatus.PENDING if item.fired_at is None else ProspectiveStatus.FIRED,
                    to_status=item.status,
                    config=self.config,
                    now=timestamp,
                )
                if not provenance.ok:
                    provenance_errors.append(provenance.error)
            return ExpiryBatchResult(
                status="succeeded",
                items=changed,
                results=results,
                provenance_errors=provenance_errors,
            )

    def due_items(
        self,
        user_id: str | None = None,
        now: float | None = None,
        *,
        agent_name: str | None = None,
    ) -> list[ProspectiveItem]:
        """Return first-view, unexpired due items for one user."""

        if not self.enabled:
            return []
        timestamp = _now_value(now)
        if timestamp is None:
            return []
        items = self.list_items(user_id, agent_name=agent_name)
        return [item for item in items if item.is_active and item.surfaced_at is None and item.is_due_at(timestamp) and not item.is_expired_at(timestamp)]

    def items_for_trigger(
        self,
        context: Any,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[ProspectiveItem]:
        """Return active, never-surfaced items matching a runtime trigger."""

        from .recall import items_for_trigger as recall_items_for_trigger

        return recall_items_for_trigger(
            context,
            self,
            user_id=user_id,
            agent_name=agent_name,
        )

    def reset(self) -> None:
        """Drop process-local locks; persisted documents remain untouched."""

        with self._locks_guard:
            self._locks.clear()
            self._blocked.clear()

    def status_summary(self, user_id: str | None = None) -> dict[str, int]:
        """Return status counts for a status surface."""

        counts = {status.value: 0 for status in ProspectiveStatus}
        for item in self.list_items(user_id):
            counts[item.status.value] += 1
        return counts


__all__ = [
    "ExpiryBatchResult",
    "ProspectiveStore",
    "StoreReadResult",
    "prospective_items_path",
    "prospective_user_dir",
]
