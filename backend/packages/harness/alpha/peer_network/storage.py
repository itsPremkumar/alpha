"""Durable SQLite storage for Alpha peer discovery and conversations.

The store is intentionally independent of FastAPI and LangGraph.  It is a
small, synchronous repository that the async service calls through
``asyncio.to_thread`` at its boundary.  SQLite is bundled with Python, needs no
server, and is sufficient for the single-node peer network; a future libp2p or
multi-node adapter can replace this repository without changing the wire API.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import utc_now

NETWORK_OWNER = "installation"


def token_digest(token: str) -> str:
    """Return a one-way digest suitable for a local bearer-token check."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(token_digest(left), token_digest(right))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class PeerNetworkStore:
    """Thread-safe SQLite repository for one Alpha installation."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS peers (
                    agent_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    version TEXT NOT NULL DEFAULT 'unknown',
                    capabilities_json TEXT NOT NULL DEFAULT '[]',
                    skills_json TEXT NOT NULL DEFAULT '[]',
                    supports_json TEXT NOT NULL DEFAULT '[]',
                    url TEXT NOT NULL,
                    websocket_url TEXT,
                    preferred_transport TEXT NOT NULL DEFAULT 'HTTP',
                    source TEXT NOT NULL DEFAULT 'unknown',
                    trust TEXT NOT NULL DEFAULT 'discovered',
                    outbound_token TEXT,
                    token_hash TEXT,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    paired_at TEXT,
                    card_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversation_participants (
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    agent_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'participant',
                    PRIMARY KEY (conversation_id, agent_id)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    sender_id TEXT NOT NULL,
                    recipients_json TEXT NOT NULL DEFAULT '[]',
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'queued',
                    direction TEXT NOT NULL DEFAULT 'outbound',
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    read_at TEXT,
                    remote_id TEXT,
                    delivery_error TEXT,
                    idempotency_key TEXT
                );

                CREATE TABLE IF NOT EXISTS deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
                    recipient_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    transport TEXT,
                    error TEXT,
                    delivered_at TEXT,
                    read_at TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_idempotency
                    ON messages(owner_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
                    ON messages(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_deliveries_status
                    ON deliveries(status, recipient_id);
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def upsert_peer(
        self,
        *,
        card: dict[str, Any],
        source: str,
        trust: str = "discovered",
        outbound_token: str | None = None,
        paired: bool = False,
        owner_id: str = NETWORK_OWNER,
    ) -> dict[str, Any]:
        now = utc_now()
        agent_id = str(card["agent_id"])
        with self._lock, self._conn:
            existing = self._conn.execute("SELECT * FROM peers WHERE agent_id = ?", (agent_id,)).fetchone()
            first_seen = existing["first_seen"] if existing else now
            prior_trust = existing["trust"] if existing else trust
            # A discovered refresh must not downgrade a paired peer.
            effective_trust = "paired" if prior_trust == "paired" or paired else trust
            self._conn.execute(
                """
                INSERT INTO peers (
                    agent_id, owner_id, name, description, version, capabilities_json,
                    skills_json, supports_json, url, websocket_url, preferred_transport,
                    source, trust, outbound_token, token_hash, first_seen, last_seen,
                    paired_at, card_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    version=excluded.version,
                    capabilities_json=excluded.capabilities_json,
                    skills_json=excluded.skills_json,
                    supports_json=excluded.supports_json,
                    url=excluded.url,
                    websocket_url=excluded.websocket_url,
                    preferred_transport=excluded.preferred_transport,
                    source=excluded.source,
                    trust=excluded.trust,
                    outbound_token=COALESCE(excluded.outbound_token, peers.outbound_token),
                    token_hash=COALESCE(excluded.token_hash, peers.token_hash),
                    last_seen=excluded.last_seen,
                    paired_at=COALESCE(excluded.paired_at, peers.paired_at),
                    card_json=excluded.card_json
                """,
                (
                    agent_id,
                    owner_id,
                    str(card.get("name") or agent_id),
                    str(card.get("description") or ""),
                    str(card.get("version") or "unknown"),
                    _json(card.get("capabilities") or []),
                    _json(card.get("skills") or []),
                    _json(card.get("supports") or []),
                    str(card.get("url") or ""),
                    card.get("websocket_url"),
                    str(card.get("preferred_transport") or "HTTP"),
                    source,
                    effective_trust,
                    outbound_token,
                    token_digest(outbound_token) if outbound_token else None,
                    first_seen,
                    now,
                    now if paired or not existing else existing["paired_at"],
                    _json(card),
                ),
            )
        return self.get_peer(agent_id)  # type: ignore[return-value]

    def get_peer(self, agent_id: str, *, include_secret: bool = False) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM peers WHERE agent_id = ?", (agent_id,)).fetchone()
        if row is None:
            return None
        result = {
            "agent_id": row["agent_id"],
            "owner_id": row["owner_id"],
            "name": row["name"],
            "description": row["description"],
            "version": row["version"],
            "capabilities": _loads(row["capabilities_json"], []),
            "skills": _loads(row["skills_json"], []),
            "supports": _loads(row["supports_json"], []),
            "url": row["url"],
            "websocket_url": row["websocket_url"],
            "preferred_transport": row["preferred_transport"],
            "source": row["source"],
            "trust": row["trust"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "paired_at": row["paired_at"],
            "card": _loads(row["card_json"], {}),
        }
        if include_secret:
            result["outbound_token"] = row["outbound_token"]
        return result

    def list_peers(self, *, skill: str | None = None, trust: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT agent_id FROM peers ORDER BY last_seen DESC").fetchall()
        peers = [self.get_peer(row["agent_id"]) for row in rows]
        result = [peer for peer in peers if peer is not None]
        if skill:
            needle = skill.casefold()
            result = [peer for peer in result if needle in {str(item).casefold() for item in peer["capabilities"]} or any(needle in str(item).casefold() for item in peer["skills"])]
        if trust:
            result = [peer for peer in result if peer["trust"] == trust]
        return result

    def set_peer_trust(self, agent_id: str, trust: str) -> dict[str, Any] | None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE peers SET trust = ? WHERE agent_id = ?", (trust, agent_id))
        return self.get_peer(agent_id)

    def set_peer_token(self, agent_id: str, token: str) -> bool:
        with self._lock, self._conn:
            result = self._conn.execute(
                "UPDATE peers SET outbound_token = ?, token_hash = ?, trust = 'paired', paired_at = COALESCE(paired_at, ?) WHERE agent_id = ?",
                (token, token_digest(token), utc_now(), agent_id),
            )
        return result.rowcount > 0

    def find_peer_by_token(self, token: str, agent_id: str | None = None) -> dict[str, Any] | None:
        """Find the paired peer matching a token, optionally disambiguating the sender.

        A single operator may intentionally share one pairing code with several
        peers.  Token-only lookup is therefore not sufficient for authenticated
        inbound messages; HTTP/WebSocket receive paths pass the claimed sender
        id as a second selector.
        """

        if not token:
            return None
        digest = token_digest(token)
        with self._lock:
            if agent_id:
                row = self._conn.execute(
                    "SELECT agent_id FROM peers WHERE token_hash = ? AND agent_id = ?",
                    (digest, agent_id),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT agent_id FROM peers WHERE token_hash = ? ORDER BY agent_id LIMIT 1",
                    (digest,),
                ).fetchone()
        return self.get_peer(row["agent_id"], include_secret=False) if row else None

    def create_conversation(
        self,
        *,
        conversation_id: str,
        mode: str,
        title: str,
        participants: list[str],
        metadata: dict[str, Any] | None = None,
        owner_id: str = NETWORK_OWNER,
    ) -> dict[str, Any]:
        now = utc_now()
        clean_participants = list(dict.fromkeys(participants))
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO conversations(conversation_id, owner_id, mode, title, status, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at
                """,
                (conversation_id, owner_id, mode, title, _json(metadata or {}), now, now),
            )
            for participant in clean_participants:
                self._conn.execute(
                    "INSERT OR IGNORE INTO conversation_participants(conversation_id, agent_id, role) VALUES (?, ?, 'participant')",
                    (conversation_id, participant),
                )
        return self.get_conversation(conversation_id, owner_id=owner_id)  # type: ignore[return-value]

    def add_participant(self, conversation_id: str, agent_id: str, *, role: str = "participant") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO conversation_participants(conversation_id, agent_id, role) VALUES (?, ?, ?)",
                (conversation_id, agent_id, role),
            )

    def get_conversation(self, conversation_id: str, *, owner_id: str = NETWORK_OWNER) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE conversation_id = ? AND owner_id = ?",
                (conversation_id, owner_id),
            ).fetchone()
            if row is None:
                return None
            participants = [
                participant["agent_id"]
                for participant in self._conn.execute(
                    "SELECT agent_id FROM conversation_participants WHERE conversation_id = ? ORDER BY agent_id",
                    (conversation_id,),
                ).fetchall()
            ]
        return {
            "conversation_id": row["conversation_id"],
            "owner_id": row["owner_id"],
            "mode": row["mode"],
            "title": row["title"],
            "status": row["status"],
            "metadata": _loads(row["metadata_json"], {}),
            "participants": participants,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_conversations(self, *, owner_id: str = NETWORK_OWNER) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT conversation_id FROM conversations WHERE owner_id = ? ORDER BY updated_at DESC",
                (owner_id,),
            ).fetchall()
        return [conversation for conversation in (self.get_conversation(row["conversation_id"], owner_id=owner_id) for row in rows) if conversation]

    def touch_conversation(self, conversation_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE conversations SET updated_at = ? WHERE conversation_id = ?", (utc_now(), conversation_id))

    def add_message(
        self,
        *,
        message_id: str,
        conversation_id: str,
        sender_id: str,
        recipients: list[str],
        kind: str,
        text: str,
        payload: dict[str, Any],
        direction: str,
        status: str,
        remote_id: str | None = None,
        idempotency_key: str | None = None,
        delivery_error: str | None = None,
        delivery_status: str = "queued",
        delivery_recipients: list[str] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._conn:
            if idempotency_key:
                existing = self._conn.execute(
                    "SELECT message_id FROM messages WHERE owner_id = ? AND idempotency_key = ?",
                    (NETWORK_OWNER, idempotency_key),
                ).fetchone()
                if existing:
                    return self.get_message(existing["message_id"], owner_id=NETWORK_OWNER)  # type: ignore[return-value]
            try:
                self._conn.execute(
                    """
                    INSERT INTO messages(
                        message_id, owner_id, conversation_id, sender_id, recipients_json, kind,
                        text, payload_json, status, direction, created_at, remote_id, delivery_error, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        NETWORK_OWNER,
                        conversation_id,
                        sender_id,
                        _json(list(dict.fromkeys(recipients))),
                        kind,
                        text,
                        _json(payload),
                        status,
                        direction,
                        now,
                        remote_id,
                        delivery_error,
                        idempotency_key,
                    ),
                )
            except sqlite3.IntegrityError:
                # Another delivery worker may have claimed the same id. The
                # durable first result wins; never turn a replay into a 500.
                return self.get_message(message_id, owner_id=NETWORK_OWNER)  # type: ignore[return-value]
            for recipient in dict.fromkeys(delivery_recipients if delivery_recipients is not None else recipients):
                self._conn.execute(
                    "INSERT INTO deliveries(delivery_id, message_id, recipient_id, status, delivered_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        f"{message_id}:{recipient}",
                        message_id,
                        recipient,
                        delivery_status,
                        utc_now() if delivery_status in {"delivered", "read"} else None,
                    ),
                )
        self.touch_conversation(conversation_id)
        return self.get_message(message_id, owner_id=NETWORK_OWNER)  # type: ignore[return-value]

    def get_message(self, message_id: str, *, owner_id: str = NETWORK_OWNER) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE message_id = ? AND owner_id = ?",
                (message_id, owner_id),
            ).fetchone()
            if row is None:
                return None
            deliveries = self._conn.execute(
                "SELECT recipient_id, status, transport, error, delivered_at, read_at FROM deliveries WHERE message_id = ? ORDER BY recipient_id",
                (message_id,),
            ).fetchall()
        return {
            "message_id": row["message_id"],
            "conversation_id": row["conversation_id"],
            "sender_id": row["sender_id"],
            "recipients": _loads(row["recipients_json"], []),
            "kind": row["kind"],
            "text": row["text"],
            "payload": _loads(row["payload_json"], {}),
            "status": row["status"],
            "direction": row["direction"],
            "created_at": row["created_at"],
            "delivered_at": row["delivered_at"],
            "read_at": row["read_at"],
            "remote_id": row["remote_id"],
            "delivery_error": row["delivery_error"],
            "idempotency_key": row["idempotency_key"],
            "deliveries": [dict(delivery) for delivery in deliveries],
        }

    def list_messages(self, conversation_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT message_id FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?",
                (conversation_id, max(1, min(int(limit), 1000))),
            ).fetchall()
        messages = [self.get_message(row["message_id"]) for row in rows]
        return list(reversed([message for message in messages if message]))

    def update_delivery(
        self,
        message_id: str,
        recipient_id: str,
        *,
        status: str,
        transport: str | None = None,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        delivered_at = now if status in {"delivered", "read"} else None
        read_at = now if status == "read" else None
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE deliveries
                SET status = ?, transport = COALESCE(?, transport), error = ?,
                    delivered_at = COALESCE(?, delivered_at), read_at = COALESCE(?, read_at)
                WHERE message_id = ? AND recipient_id = ?
                """,
                (status, transport, error, delivered_at, read_at, message_id, recipient_id),
            )
            statuses = [row["status"] for row in self._conn.execute("SELECT status FROM deliveries WHERE message_id = ?", (message_id,)).fetchall()]
            aggregate = "failed" if "failed" in statuses else "read" if statuses and all(value == "read" for value in statuses) else "delivered" if statuses and all(value in {"delivered", "read"} for value in statuses) else "queued"
            self._conn.execute(
                "UPDATE messages SET status = ?, delivered_at = COALESCE(?, delivered_at), delivery_error = ? WHERE message_id = ?",
                (aggregate, delivered_at, error, message_id),
            )

    def mark_message_read(self, message_id: str, *, recipient_id: str | None = None) -> None:
        with self._lock, self._conn:
            if recipient_id:
                self.update_delivery(message_id, recipient_id, status="read")
            else:
                rows = self._conn.execute("SELECT recipient_id FROM deliveries WHERE message_id = ?", (message_id,)).fetchall()
                for row in rows:
                    self.update_delivery(message_id, row["recipient_id"], status="read")

    def pending_messages(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT m.message_id FROM messages m
                JOIN deliveries d ON d.message_id = m.message_id
                WHERE d.status = 'queued' AND m.direction = 'outbound'
                GROUP BY m.message_id ORDER BY m.created_at ASC LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [message for message in (self.get_message(row["message_id"]) for row in rows) if message]

    def counts(self) -> dict[str, int]:
        with self._lock:
            peers = self._conn.execute("SELECT COUNT(*) AS count FROM peers").fetchone()["count"]
            conversations = self._conn.execute("SELECT COUNT(*) AS count FROM conversations").fetchone()["count"]
            messages = self._conn.execute("SELECT COUNT(*) AS count FROM messages").fetchone()["count"]
        return {"peers": int(peers), "conversations": int(conversations), "messages": int(messages)}


__all__ = ["NETWORK_OWNER", "PeerNetworkStore", "token_digest", "tokens_equal"]
