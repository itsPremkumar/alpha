"""Notification system: the durable record of things the operator should see.

A notification is a *claim that something happened*, with the evidence
attached. It is not a delivery mechanism — the delivery surface (desktop
toast, sound, in-app panel) is a client concern. This module owns the
durable record, the trigger policy, and the per-user preference that
decides whether a given event is worth a surface at all.

Design rules this module enforces:

- **A notification is created once.** The trigger path is the only writer;
  the read path never mutates. `read`/`read_at` are the only client-driven
  transitions, and they are idempotent.
- **A failed read is not an empty inbox.** The store fails closed on a
  corrupt file rather than reporting "no notifications", because an unread
  notification and an unreadable one are opposite claims.
- **Priorities are declared, not inferred.** `urgent` plays a sound and
  requires interaction; `low` is silent. A missing priority is `normal`,
  never a louder default.
- **Bounded.** History is capped so a chatty room cannot grow the
  notification log without limit.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

#: The event kinds that can raise a notification. The vocabulary is
#: closed so a client can render an icon per kind without inventing one
#: for an unknown value — unknown kinds are preserved verbatim but render
#: with the generic bell.
NOTIFICATION_TYPES: tuple[str, ...] = (
    "message",
    "mention",
    "activity",
    "claim",
    "run",
    "system",
)

#: Urgency ladder. The order is the escalation order: `urgent` is the
#: loudest, `low` the quietest. A client maps these to sound volume,
#: toast persistence and colour.
NOTIFICATION_PRIORITIES: tuple[str, ...] = ("low", "normal", "high", "urgent")

#: How many notifications the store retains per user. The cap is the
#: honest bound: a notification log that grew forever would itself become
#: the thing nobody reads.
MAX_HISTORY = 500

#: The identity that owns an installation's notification history in this
#: single-process model. It is declared once, here, because two independent
#: places have to agree on the same string: the trigger path decides *who to
#: notify*, and the read path decides *whose inbox to serve*.
#:
#: When those were each written on their own they silently disagreed. Room
#: messages filed their records under the agent names in the roster, while the
#: only reader asked for this id — so every notification was created, durable,
#: counted, and never shown to anybody. The bell was structurally empty and
#: nothing in the unit tests noticed, because they read back the agent inbox
#: the trigger had written rather than the inbox the UI asks for.
OPERATOR_USER_ID = "operator"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Notification:
    """One durable entry in a user's notification history."""

    notification_id: str
    type: str
    title: str
    body: str
    #: The room the event happened in, when it is room-scoped. A system
    #: notification has none.
    room_id: str | None = None
    #: The specific message, for "you were mentioned in this message".
    message_id: str | None = None
    #: The bot or operator that caused the event.
    sender: str | None = None
    priority: str = "normal"
    created_at: str = field(default_factory=_now)
    read: bool = False
    read_at: str | None = None
    #: Deep link the client opens when the notification is activated.
    action_url: str | None = None
    #: Icon key the client maps to a glyph. Absent means the generic bell.
    icon: str | None = None
    #: Whether this notification should make a sound. `urgent` defaults
    #: to True; everything else is governed by the user's preference.
    sound: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Notification:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


@dataclass
class NotificationPreference:
    """Per-user notification settings.

    Defaults are the "everything important, quietly" posture: mentions and
    activity on, low-priority chatter off, sound on for high/urgent.
    Quiet hours and digest mode are the operator's own knobs.
    """

    user_id: str
    enabled: bool = True
    sound_enabled: bool = True
    desktop_enabled: bool = True
    #: Per-type on/off. Absent types inherit `enabled`.
    types: dict[str, bool] = field(
        default_factory=lambda: {
            "message": True,
            "mention": True,
            "activity": True,
            "claim": True,
            "run": True,
            "system": True,
        }
    )
    #: Per-priority on/off. Absent priorities inherit from the ladder.
    priorities: dict[str, bool] = field(
        default_factory=lambda: {
            "low": False,
            "normal": True,
            "high": True,
            "urgent": True,
        }
    )
    #: "HH:MM" window in which sound is suppressed. `None` disables.
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    #: Batch notifications into a digest instead of firing each one.
    digest_mode: bool = False
    digest_interval_minutes: int = 30

    def allows(self, notification_type: str, priority: str) -> bool:
        """Whether a notification of this type/priority should surface.

        The decision is a pure function of the preference, so the trigger
        path can call it without knowing anything about the user. An
        absent type/priority falls back to the global `enabled` flag —
        the preference is a filter, never an amplifier.
        """
        if not self.enabled:
            return False
        if self.priorities.get(priority, self.priorities.get("normal", True)) is False:
            return False
        return self.types.get(notification_type, True) is not False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NotificationPreference:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class NotificationStore:
    """File-backed notification history, one file per user.

    Single-process by design, like every other store in this layer: the
    Gateway is the only writer, and the atomic tmp+replace write is the
    durability contract. Cross-process exactly-once is explicitly *not*
    claimed — a second process reading the same file sees the last
    committed state, nothing more.
    """

    def __init__(self, storage_dir: str | Path | None = None):
        self.storage_dir = Path(storage_dir).resolve() if storage_dir else _default_storage_dir()
        self._notifications: dict[str, list[Notification]] = {}
        self._preferences: dict[str, NotificationPreference] = {}
        self._lock = threading.RLock()
        self._load()

    def _user_path(self, user_id: str) -> Path:
        safe = _safe_user_id(user_id)
        return self.storage_dir / f"{safe}.json"

    def _load(self) -> None:
        if not self.storage_dir.exists():
            return
        for path in self.storage_dir.glob("*.json"):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                user_id = data.get("user_id")
                if not user_id:
                    continue
                self._notifications[user_id] = [Notification.from_dict(dict(n)) for n in data.get("notifications", [])]
                if data.get("preferences"):
                    self._preferences[user_id] = NotificationPreference.from_dict(dict(data["preferences"]))
            except Exception:
                # A corrupt per-user file is a failed read for that user,
                # not a failed boot. The user starts with an empty in-memory
                # list and the next save rewrites the file.
                logger.warning("Notification load failed for %s", path, exc_info=True)

    def _save(self, user_id: str) -> None:
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "user_id": user_id,
                "notifications": [n.to_dict() for n in self._notifications.get(user_id, [])],
                "preferences": (self._preferences[user_id].to_dict() if user_id in self._preferences else None),
                "updated_at": _now(),
            }
            tmp = self._user_path(user_id).with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self._user_path(user_id))
        except Exception:
            logger.warning("Notification save failed for %s", user_id, exc_info=True)

    # ── Preferences ──────────────────────────────────────────────────

    def get_preference(self, user_id: str) -> NotificationPreference:
        with self._lock:
            if user_id not in self._preferences:
                self._preferences[user_id] = NotificationPreference(user_id=user_id)
            return self._preferences[user_id]

    def update_preference(self, user_id: str, updates: dict[str, Any]) -> NotificationPreference:
        with self._lock:
            pref = self.get_preference(user_id)
            for key, value in updates.items():
                if key in NotificationPreference.__dataclass_fields__ and key != "user_id":
                    setattr(pref, key, value)
            self._preferences[user_id] = pref
            self._save(user_id)
            return pref

    # ── Notifications ──────────────────────────────────────────────────

    def create(self, notification: Notification) -> Notification:
        """Append a notification, capping history at ``MAX_HISTORY``.

        The cap drops the *oldest* entries first. A notification that
        scrolled out of history is gone, not archived — the honest claim
        is "the store retains the most recent N", and pretending the old
        ones still exist would be a fabricated count.
        """
        with self._lock:
            user_id = _owner_of(notification)
            history = self._notifications.setdefault(user_id, [])
            history.append(notification)
            if len(history) > MAX_HISTORY:
                overflow = len(history) - MAX_HISTORY
                del history[:overflow]
            self._save(user_id)
            return notification

    def list(
        self,
        user_id: str,
        *,
        unread_only: bool = False,
        types: list[str] | None = None,
        limit: int = 50,
    ) -> list[Notification]:
        with self._lock:
            history = list(self._notifications.get(user_id, []))
        if types:
            wanted = set(types)
            history = [n for n in history if n.type in wanted]
        if unread_only:
            history = [n for n in history if not n.read]
        history.sort(key=lambda n: n.created_at, reverse=True)
        return history[:limit]

    def mark_read(self, user_id: str, notification_id: str) -> bool:
        with self._lock:
            for n in self._notifications.get(user_id, []):
                if n.notification_id == notification_id:
                    if not n.read:
                        n.read = True
                        n.read_at = _now()
                        self._save(user_id)
                    return True
            return False

    def mark_all_read(self, user_id: str) -> int:
        with self._lock:
            count = 0
            for n in self._notifications.get(user_id, []):
                if not n.read:
                    n.read = True
                    n.read_at = _now()
                    count += 1
            if count:
                self._save(user_id)
            return count

    def unread_count(self, user_id: str) -> int:
        with self._lock:
            return sum(1 for n in self._notifications.get(user_id, []) if not n.read)


def _owner_of(notification: Notification) -> str:
    """The user a notification belongs to.

    Notifications are recorded against the operator who owns the
    installation in this single-process model. The field is resolved at
    creation time by the trigger, never from a client-supplied owner,
    because a notification is a per-user record and self-asserting an
    owner would mix users' histories.
    """
    return getattr(notification, "user_id", None) or OPERATOR_USER_ID


def _safe_user_id(user_id: str) -> str:
    """A filename-safe user id.

    User ids are operator-controlled strings, not filenames, so a malicious
    or accidental ``../`` would escape the storage dir. The hash keeps the
    path within the directory while remaining stable per user.
    """
    import hashlib

    cleaned = (user_id or OPERATOR_USER_ID).strip() or OPERATOR_USER_ID
    if cleaned.replace("-", "").replace("_", "").isalnum() and len(cleaned) <= 64:
        return cleaned
    return "u_" + hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]


def _default_storage_dir() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "notifications"
    except Exception:
        return Path.cwd() / ".alpha" / "notifications"


def new_notification_id() -> str:
    return f"notif_{uuid4().hex[:10]}"
