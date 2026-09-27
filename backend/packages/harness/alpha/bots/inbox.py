"""Persistent per-bot inbox for Bot Mode DMs.

Fire-and-forget delivery lands here; the recipient reads and acks on its own
schedule. File-backed JSON per bot under the runtime home, capped so a
runaway sender cannot grow state without bound.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

MAX_INBOX_MESSAGES = 200
MAX_BODY_CHARS = 16000

#: Bounded width of a roster last-message preview, in characters.
PREVIEW_CHARS = 120

DMStatus = Literal["unread", "read", "acked"]


def _inbox_dir() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "bots" / "inbox"
    except Exception:
        return Path.cwd() / ".alpha" / "bots" / "inbox"


def _bot_file(bot_name: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in bot_name.lower().strip())[:64] or "unknown"
    return _inbox_dir() / f"{safe}.json"


@dataclass
class DMMessage:
    delivery_id: str
    sender: str
    recipient: str
    body: str
    status: DMStatus = "unread"
    created_at: float = field(default_factory=time.time)
    read_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DMMessage:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        msg = cls(**filtered)
        if msg.status not in ("unread", "read", "acked"):
            msg.status = "unread"
        return msg


class BotInbox:
    """Thread-safe persistent inbox for one bot."""

    def __init__(self, bot_name: str):
        self.bot_name = bot_name.lower().strip()
        self._path = _bot_file(self.bot_name)
        self._lock = threading.Lock()
        self._messages: list[DMMessage] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._messages = [DMMessage.from_dict(m) for m in data.get("messages", [])][-MAX_INBOX_MESSAGES:]
        except Exception:
            logger.warning("Bot inbox load failed for %s; starting empty", self.bot_name, exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "messages": [m.to_dict() for m in self._messages[-MAX_INBOX_MESSAGES:]]}, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            logger.warning("Bot inbox save failed for %s", self.bot_name, exc_info=True)

    def deliver(self, sender: str, body: str) -> DMMessage:
        """Store a message. The attribution prefix is applied by the DM layer."""
        if len(body) > MAX_BODY_CHARS:
            raise ValueError(f"DM body exceeds {MAX_BODY_CHARS} chars.")
        msg = DMMessage(delivery_id=f"dm-{uuid.uuid4().hex[:10]}", sender=sender.lower().strip(), recipient=self.bot_name, body=body)
        with self._lock:
            self._messages.append(msg)
            del self._messages[:-MAX_INBOX_MESSAGES]
            self._save()
        return msg

    def list(self, *, unread_only: bool = False, limit: int = 50) -> list[DMMessage]:
        with self._lock:
            rows = [m for m in self._messages if not unread_only or m.status == "unread"]
        return list(reversed(rows[-limit:]))

    def unread_count(self) -> int:
        with self._lock:
            return sum(1 for m in self._messages if m.status == "unread")

    def mark_read(self, delivery_id: str) -> DMMessage | None:
        with self._lock:
            for m in self._messages:
                if m.delivery_id == delivery_id and m.status == "unread":
                    m.status = "read"
                    m.read_at = time.time()
                    self._save()
                    return m
            return None

    def ack(self, delivery_id: str) -> DMMessage | None:
        with self._lock:
            for m in self._messages:
                if m.delivery_id == delivery_id and m.status != "acked":
                    m.status = "acked"
                    m.read_at = m.read_at or time.time()
                    self._save()
                    return m
            return None


_inboxes: dict[str, BotInbox] = {}
_inboxes_lock = threading.Lock()


def get_bot_inbox(bot_name: str) -> BotInbox:
    key = bot_name.lower().strip()
    with _inboxes_lock:
        try:
            from alpha.config.runtime_paths import runtime_home

            live_dir = str((runtime_home() / "bots" / "inbox").resolve())
        except Exception:
            live_dir = None
        box = _inboxes.get(key)
        if box is None or (live_dir and str(box._path.parent.resolve()) != live_dir):
            box = BotInbox(key)
            _inboxes[key] = box
        return box


# ---------------------------------------------------------------------------
# Roster activity projection
# ---------------------------------------------------------------------------
# The roster row wants "what did this Bot last say" and "is anything waiting",
# which is a *presentation* concern sitting on top of the message store. It
# lives here rather than in a router because this module already owns the
# per-Bot file, the lock, and the failure mode.

#: A projection with nothing to show. Shape is fixed so a caller can render a
#: row without branching on which fields happened to be present.
_EMPTY_ACTIVITY: dict[str, Any] = {
    "unread_count": 0,
    "last_message_preview": None,
    "last_message_at": None,
    "last_message_sender": None,
    "last_message_withheld": False,
}


def _preview_text(body: str) -> tuple[str, bool]:
    """One-line bounded preview, plus whether it had to be withheld.

    A credential-shaped body is withheld **whole**. Truncating a secret still
    ships the first half of it, and the roster is a wider blast radius than the
    DM inbox a user opened deliberately.
    """
    from alpha.bots.portable import scan_text

    if scan_text(body or "").blocked:
        return "", True
    flat = " ".join((body or "").split())
    if not flat:
        return "", False
    if len(flat) > PREVIEW_CHARS:
        return f"{flat[:PREVIEW_CHARS].rstrip()}…", False
    return flat, False


def roster_activity(bot_name: str) -> dict[str, Any]:
    """Newest-message preview and unread count for one Bot. Never raises.

    Opt-in by the caller (``GET /api/bots?activity=true``) because a roster
    read is otherwise a pure registry read, and this makes it a per-Bot inbox
    read plus a secret scan of the newest body.

    The unread count reflects this process's view of the inbox: ``BotInbox``
    caches per name for the process lifetime, so a second Gateway writing the
    same file is not seen here. That is the same single-process boundary the
    inbox itself has, not a new one.
    """
    try:
        box = get_bot_inbox(bot_name)
        newest = box.list(limit=1)
        unread = box.unread_count()
    except Exception:
        # One unreadable inbox must not take a whole roster read down with it.
        logger.warning("Roster activity read failed for %s", bot_name, exc_info=True)
        return dict(_EMPTY_ACTIVITY)
    if not newest:
        return dict(_EMPTY_ACTIVITY)
    last = newest[0]
    preview, withheld = _preview_text(last.body)
    return {
        "unread_count": unread,
        "last_message_preview": None if withheld else preview,
        "last_message_at": last.created_at,
        "last_message_sender": last.sender,
        "last_message_withheld": withheld,
    }
