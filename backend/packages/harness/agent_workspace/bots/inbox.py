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

DMStatus = Literal["unread", "read", "acked"]


def _inbox_dir() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "bots" / "inbox"
    except Exception:
        return Path.cwd() / ".agent_workspace" / "bots" / "inbox"


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
            from agent_workspace.config.runtime_paths import runtime_home

            live_dir = str((runtime_home() / "bots" / "inbox").resolve())
        except Exception:
            live_dir = None
        box = _inboxes.get(key)
        if box is None or (live_dir and str(box._path.parent.resolve()) != live_dir):
            box = BotInbox(key)
            _inboxes[key] = box
        return box
