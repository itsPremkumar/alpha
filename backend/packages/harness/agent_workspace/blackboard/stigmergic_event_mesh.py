"""Autonomous Stigmergic Event Mesh and Swarm Pheromone Memory.

Stigmergy is the coordination mechanism used by social insect swarms: agents
never talk to each other directly, they leave traces in a shared environment
and let the strength of those traces steer collective behaviour. This module
implements that substrate for a swarm of coding agents.

Design
------
* Every agent deposits *pheromone signals* on code entities (a file, a symbol,
  a module or an arbitrary entity key).
* Signals decay exponentially with a configurable half-life, so stale
  information evaporates instead of misleading the swarm.
* Peers read the shared field to prioritise work: files with high
  ``DEFECT_SUSPECT`` pheromone attract attention, files under
  ``UNDER_SURGICAL_REFACTOR`` are avoided because another agent already owns
  them, and ``CONVERGENCE_LOCKED`` entities are frozen once verified.
* All state is persisted through a high-throughput SQLite WAL store and
  mirrored by a lock-free in-memory event stream for ultra-low-latency reads.

Exposed agent tools
-------------------
``emit_stigmergic_event`` and ``query_stigmergic_traces``.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

DEFAULT_HALF_LIFE_SEC: float = 900.0
DEFAULT_STRENGTH: float = 1.0
MIN_EFFECTIVE_STRENGTH: float = 0.01
DEFAULT_DB_FILENAME: str = "stigmergic_event_mesh.db"


class PheromoneType(str, Enum):
    """Semantic pheromone classes deposited on code entities."""

    DEFECT_SUSPECT = "DEFECT_SUSPECT"
    UNDER_SURGICAL_REFACTOR = "UNDER_SURGICAL_REFACTOR"
    CANARY_VERIFIED_STABLE = "CANARY_VERIFIED_STABLE"
    CONVERGENCE_LOCKED = "CONVERGENCE_LOCKED"
    COVERAGE_GAP = "COVERAGE_GAP"
    DEPENDENCY_HOTSPOT = "DEPENDENCY_HOTSPOT"

    @classmethod
    def normalize(cls, value: str) -> Optional["PheromoneType"]:
        """Return the enum member matching ``value`` (case-insensitive)."""
        if not value:
            return None
        needle = str(value).strip().upper()
        for member in cls:
            if member.value == needle:
                return member
        aliases = {
            "DEFECT": cls.DEFECT_SUSPECT,
            "SUSPECT": cls.DEFECT_SUSPECT,
            "REFACTOR": cls.UNDER_SURGICAL_REFACTOR,
            "LOCK": cls.UNDER_SURGICAL_REFACTOR,
            "CANARY": cls.CANARY_VERIFIED_STABLE,
            "STABLE": cls.CANARY_VERIFIED_STABLE,
            "CONVERGENCE": cls.CONVERGENCE_LOCKED,
            "LOCKED": cls.CONVERGENCE_LOCKED,
            "COVERAGE": cls.COVERAGE_GAP,
            "HOTSPOT": cls.DEPENDENCY_HOTSPOT,
        }
        return aliases.get(needle)


class EventAction(str, Enum):
    """Operations accepted by the mesh write API."""

    DEPOSIT = "deposit"
    REINFORCE = "reinforce"
    RELEASE = "release"
    PURGE = "purge"


@dataclass
class StigmergicEvent:
    """An immutable record of one pheromone interaction."""

    event_id: str
    agent_id: str
    entity: str
    pheromone: PheromoneType
    action: EventAction
    strength: float
    half_life_sec: float
    timestamp: float
    trace_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "agent_id": self.agent_id,
            "entity": self.entity,
            "pheromone": self.pheromone.value,
            "action": self.action.value,
            "strength": round(self.strength, 6),
            "half_life_sec": self.half_life_sec,
            "timestamp": self.timestamp,
            "trace_id": self.trace_id,
            "metadata": self.metadata,
        }


@dataclass
class PheromoneField:
    """The decayed aggregation of all signals for one entity/pheromone pair."""

    entity: str
    pheromone: PheromoneType
    base_strength: float
    effective_strength: float
    contributors: int
    last_deposit: float
    age_sec: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "pheromone": self.pheromone.value,
            "base_strength": round(self.base_strength, 6),
            "effective_strength": round(self.effective_strength, 6),
            "contributors": self.contributors,
            "last_deposit": self.last_deposit,
            "age_sec": round(self.age_sec, 3),
            "metadata": self.metadata,
        }


def exponential_decay(strength: float, age_sec: float, half_life_sec: float) -> float:
    """Return ``strength`` decayed over ``age_sec`` with the given half-life.

    The canonical stigmergic evaporation formula is
    ``s(t) = s0 * 2 ** (-t / half_life)``. A half-life of zero or negative is
    treated as "no decay" so callers cannot divide by zero.
    """
    if half_life_sec is None or half_life_sec <= 0:
        return float(strength)
    age = max(float(age_sec), 0.0)
    return float(strength) * (2.0 ** (-age / float(half_life_sec)))


class EventStream:
    """Bounded in-memory event stream for low-latency swarm fan-out.

    The stream is a ring buffer: it always keeps the most recent events and
    never grows without bound, which keeps the mesh safe for long-running
    autonomous sessions.
    """

    def __init__(self, capacity: int = 2048):
        self.capacity = max(int(capacity), 1)
        self._buffer: deque[StigmergicEvent] = deque(maxlen=self.capacity)
        self._subscribers: list[Any] = []
        self._lock = threading.Lock()

    def publish(self, event: StigmergicEvent) -> None:
        """Append an event and notify every registered subscriber."""
        with self._lock:
            self._buffer.append(event)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:  # pragma: no cover - subscriber isolation
                continue

    def subscribe(self, callback: Any) -> None:
        """Register a callable invoked for every published event."""
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Any) -> None:
        """Remove a previously registered subscriber."""
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def recent(self, limit: int = 100) -> list[StigmergicEvent]:
        """Return the most recent events, newest last."""
        with self._lock:
            items = list(self._buffer)
        if limit and limit > 0:
            items = items[-int(limit) :]
        return items

    def clear(self) -> None:
        """Drop all buffered events."""
        with self._lock:
            self._buffer.clear()


class StigmergicEventStore:
    """Durable SQLite WAL-backed persistence for stigmergic events.

    Write-ahead logging is enabled so many agent processes can publish and
    read concurrently without blocking one another.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS stigmergic_events (
        event_id       TEXT PRIMARY KEY,
        agent_id       TEXT NOT NULL,
        entity         TEXT NOT NULL,
        pheromone      TEXT NOT NULL,
        action         TEXT NOT NULL,
        strength       REAL NOT NULL,
        half_life_sec  REAL NOT NULL,
        timestamp      REAL NOT NULL,
        trace_id       TEXT NOT NULL DEFAULT '',
        metadata_json  TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_stigmergic_entity
        ON stigmergic_events(entity);
    CREATE INDEX IF NOT EXISTS idx_stigmergic_pheromone
        ON stigmergic_events(pheromone);
    CREATE INDEX IF NOT EXISTS idx_stigmergic_timestamp
        ON stigmergic_events(timestamp);
    """

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = ":memory:" if db_path is None else str(db_path)
        self._lock = threading.RLock()
        self._connection: Optional[sqlite3.Connection] = None
        if self.db_path != ":memory:":
            parent = Path(self.db_path).parent
            if str(parent) and not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # -- lifecycle ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open (or reuse) a SQLite connection configured for WAL."""
        if self._connection is not None:
            return self._connection
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        if self.db_path != ":memory:":
            with contextlib.suppress(sqlite3.DatabaseError):
                connection.execute("PRAGMA journal_mode=WAL")
            with contextlib.suppress(sqlite3.DatabaseError):
                connection.execute("PRAGMA synchronous=NORMAL")
        self._connection = connection
        return connection

    def _initialize(self) -> None:
        """Create the schema."""
        with self._lock:
            connection = self._connect()
            connection.executescript(self._SCHEMA)
            connection.commit()

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            if self._connection is not None:
                with contextlib.suppress(sqlite3.DatabaseError):
                    self._connection.close()
                self._connection = None

    # -- writes ------------------------------------------------------------

    def append(self, event: StigmergicEvent) -> None:
        """Persist one event."""
        with self._lock:
            connection = self._connect()
            connection.execute(
                """
                INSERT OR REPLACE INTO stigmergic_events
                (event_id, agent_id, entity, pheromone, action, strength,
                 half_life_sec, timestamp, trace_id, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.agent_id,
                    event.entity,
                    event.pheromone.value,
                    event.action.value,
                    float(event.strength),
                    float(event.half_life_sec),
                    float(event.timestamp),
                    event.trace_id,
                    json.dumps(event.metadata, sort_keys=True),
                ),
            )
            connection.commit()

    def purge(self, entity: Optional[str] = None, pheromone: Optional[str] = None) -> int:
        """Delete events filtered by entity and/or pheromone."""
        clauses: list[str] = []
        params: list[Any] = []
        if entity:
            clauses.append("entity = ?")
            params.append(entity)
        if pheromone:
            clauses.append("pheromone = ?")
            params.append(pheromone)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            connection = self._connect()
            cursor = connection.execute(
                f"DELETE FROM stigmergic_events{where}", tuple(params)
            )
            connection.commit()
            return int(cursor.rowcount or 0)

    def prune_expired(self, min_effective: float = MIN_EFFECTIVE_STRENGTH, now: Optional[float] = None) -> int:
        """Delete deposit events whose decayed strength has evaporated."""
        current = time.time() if now is None else now
        removed = 0
        with self._lock:
            connection = self._connect()
            rows = connection.execute(
                "SELECT event_id, strength, half_life_sec, timestamp FROM stigmergic_events"
            ).fetchall()
            for row in rows:
                age = current - float(row["timestamp"])
                if exponential_decay(float(row["strength"]), age, float(row["half_life_sec"])) < min_effective:
                    connection.execute(
                        "DELETE FROM stigmergic_events WHERE event_id = ?",
                        (row["event_id"],),
                    )
                    removed += 1
            if removed:
                connection.commit()
        return removed

    # -- reads -------------------------------------------------------------

    def all_events(self) -> list[StigmergicEvent]:
        """Return every persisted event ordered chronologically."""
        with self._lock:
            rows = self._connect().execute(
                "SELECT * FROM stigmergic_events ORDER BY timestamp ASC, event_id ASC"
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def events_for(
        self,
        entity: Optional[str] = None,
        pheromone: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 200,
    ) -> list[StigmergicEvent]:
        """Return filtered events ordered newest-first."""
        clauses: list[str] = []
        params: list[Any] = []
        if entity:
            clauses.append("entity = ?")
            params.append(entity)
        if pheromone:
            clauses.append("pheromone = ?")
            params.append(pheromone)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_clause = " LIMIT ?" if limit and limit > 0 else ""
        if limit_clause:
            params.append(int(limit))
        with self._lock:
            rows = self._connect().execute(
                f"SELECT * FROM stigmergic_events{where} ORDER BY timestamp DESC{limit_clause}",
                tuple(params),
            ).fetchall()
        return [_row_to_event(row) for row in rows]


def _row_to_event(row: sqlite3.Row) -> StigmergicEvent:
    """Convert a SQLite row into a :class:`StigmergicEvent`."""
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        metadata = {}
    return StigmergicEvent(
        event_id=row["event_id"],
        agent_id=row["agent_id"],
        entity=row["entity"],
        pheromone=PheromoneType(row["pheromone"]),
        action=EventAction(row["action"]),
        strength=float(row["strength"]),
        half_life_sec=float(row["half_life_sec"]),
        timestamp=float(row["timestamp"]),
        trace_id=row["trace_id"] or "",
        metadata=metadata,
    )


class StigmergicEventMesh:
    """The shared coordination field for autonomous agent swarms.

    The mesh owns a durable :class:`StigmergicEventStore` and a volatile
    :class:`EventStream`. Writes update both, reads prefer the durable store
    (source of truth) and use the stream only for hot-path introspection.
    """

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        default_half_life_sec: float = DEFAULT_HALF_LIFE_SEC,
        stream_capacity: int = 2048,
    ):
        self.store = StigmergicEventStore(db_path)
        self.stream = EventStream(capacity=stream_capacity)
        self.default_half_life_sec = float(default_half_life_sec)
        self._lock = threading.RLock()
        self._sequence = 0

    # -- writing -----------------------------------------------------------

    def emit(
        self,
        agent_id: str,
        entity: str,
        pheromone: str,
        action: str = EventAction.DEPOSIT.value,
        strength: float = DEFAULT_STRENGTH,
        half_life_sec: Optional[float] = None,
        trace_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> dict[str, Any]:
        """Deposit, reinforce, release or purge a pheromone on an entity.

        Args:
            agent_id: Identifier of the emitting agent.
            entity: Code entity key (file path, symbol, module, ...).
            pheromone: One of the :class:`PheromoneType` names (or an alias).
            action: ``deposit``, ``reinforce``, ``release`` or ``purge``.
            strength: Base strength of the signal before decay.
            half_life_sec: Half-life used for exponential evaporation.
            trace_id: Optional correlation id for a swarm trajectory.
            metadata: Arbitrary JSON-serializable annotations.
            timestamp: Override for the emission time (testing / replay).

        Returns:
            A JSON-serializable result dictionary.
        """
        if not agent_id:
            return _failure("agent_id is required")
        if not entity:
            return _failure("entity is required")

        pheromone_type = PheromoneType.normalize(pheromone)
        if pheromone_type is None:
            return _failure(
                f"unknown pheromone '{pheromone}'; expected one of "
                + ", ".join(item.value for item in PheromoneType)
            )

        try:
            event_action = EventAction(str(action or "deposit").strip().lower())
        except ValueError:
            return _failure(
                f"unknown action '{action}'; expected one of "
                + ", ".join(item.value for item in EventAction)
            )

        resolved_half_life = (
            self.default_half_life_sec if half_life_sec is None else float(half_life_sec)
        )
        if resolved_half_life < 0:
            return _failure("half_life_sec must be >= 0")

        now = time.time() if timestamp is None else float(timestamp)
        effective_strength = float(strength)

        with self._lock:
            if event_action is EventAction.REINFORCE:
                existing = self.store.events_for(entity=entity, pheromone=pheromone_type.value)
                deposits = [e for e in existing if e.action in (EventAction.DEPOSIT, EventAction.REINFORCE)]
                if deposits:
                    latest = max(deposits, key=lambda e: e.timestamp)
                    decayed = exponential_decay(
                        latest.strength, now - latest.timestamp, latest.half_life_sec
                    )
                    effective_strength = decayed + float(strength)

            if event_action in (EventAction.RELEASE, EventAction.PURGE):
                removed = self.store.purge(entity=entity, pheromone=pheromone_type.value)
                event = StigmergicEvent(
                    event_id=_new_event_id(),
                    agent_id=agent_id,
                    entity=entity,
                    pheromone=pheromone_type,
                    action=event_action,
                    strength=0.0,
                    half_life_sec=resolved_half_life,
                    timestamp=now,
                    trace_id=trace_id,
                    metadata=metadata or {},
                )
                self.store.append(event)
                self.stream.publish(event)
                return {
                    "success": True,
                    "action": event_action.value,
                    "entity": entity,
                    "pheromone": pheromone_type.value,
                    "removed": removed,
                    "event": event.to_dict(),
                }

            event = StigmergicEvent(
                event_id=_new_event_id(),
                agent_id=agent_id,
                entity=entity,
                pheromone=pheromone_type,
                action=event_action,
                strength=max(effective_strength, 0.0),
                half_life_sec=resolved_half_life,
                timestamp=now,
                trace_id=trace_id,
                metadata=metadata or {},
            )
            self.store.append(event)
            self.stream.publish(event)

        return {
            "success": True,
            "action": event_action.value,
            "entity": entity,
            "pheromone": pheromone_type.value,
            "strength": round(event.strength, 6),
            "effective_strength": round(
                exponential_decay(event.strength, 0.0, event.half_life_sec), 6
            ),
            "half_life_sec": event.half_life_sec,
            "event": event.to_dict(),
        }

    # -- reading -----------------------------------------------------------

    def field(
        self,
        entity: Optional[str] = None,
        pheromone: Optional[str] = None,
        now: Optional[float] = None,
        min_effective: float = MIN_EFFECTIVE_STRENGTH,
    ) -> list[PheromoneField]:
        """Return decayed pheromone aggregations grouped by entity/pheromone."""
        current = time.time() if now is None else now
        events = [
            event
            for event in self.store.all_events()
            if event.action in (EventAction.DEPOSIT, EventAction.REINFORCE)
        ]
        if entity:
            events = [event for event in events if event.entity == entity]
        if pheromone:
            pheromone_type = PheromoneType.normalize(pheromone)
            if pheromone_type is None:
                return []
            events = [event for event in events if event.pheromone is pheromone_type]

        grouped: dict[tuple[str, PheromoneType], list[StigmergicEvent]] = {}
        for event in events:
            grouped.setdefault((event.entity, event.pheromone), []).append(event)

        fields: list[PheromoneField] = []
        for (entity_key, pheromone_key), group in grouped.items():
            # Only the most recent deposit per agent survives: agents reinforce
            # their own trace instead of stacking duplicates.
            latest_by_agent: dict[str, StigmergicEvent] = {}
            for event in group:
                previous = latest_by_agent.get(event.agent_id)
                if previous is None or event.timestamp >= previous.timestamp:
                    latest_by_agent[event.agent_id] = event

            base = 0.0
            effective = 0.0
            last_deposit = 0.0
            merged_metadata: dict[str, Any] = {}
            for event in latest_by_agent.values():
                age = current - event.timestamp
                base += event.strength
                effective += exponential_decay(event.strength, age, event.half_life_sec)
                last_deposit = max(last_deposit, event.timestamp)
                merged_metadata.update(event.metadata)

            if effective < min_effective:
                continue

            fields.append(
                PheromoneField(
                    entity=entity_key,
                    pheromone=pheromone_key,
                    base_strength=base,
                    effective_strength=effective,
                    contributors=len(latest_by_agent),
                    last_deposit=last_deposit,
                    age_sec=max(current - last_deposit, 0.0),
                    metadata=merged_metadata,
                )
            )

        fields.sort(key=lambda item: item.effective_strength, reverse=True)
        return fields

    def traces(
        self,
        entity: Optional[str] = None,
        pheromone: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[StigmergicEvent]:
        """Return raw chronological traces emitted into the mesh."""
        return self.store.events_for(
            entity=entity, pheromone=pheromone, agent_id=agent_id, limit=limit
        )

    def locked_entities(self, now: Optional[float] = None) -> list[dict[str, Any]]:
        """Return entities currently held by another agent."""
        locks = self.field(pheromone=PheromoneType.UNDER_SURGICAL_REFACTOR.value, now=now)
        held: list[dict[str, Any]] = []
        for item in locks:
            owners = sorted(
                {
                    event.agent_id
                    for event in self.traces(
                        entity=item.entity,
                        pheromone=PheromoneType.UNDER_SURGICAL_REFACTOR.value,
                    )
                    if event.action is not EventAction.RELEASE
                }
            )
            held.append(
                {
                    "entity": item.entity,
                    "strength": round(item.effective_strength, 6),
                    "owners": owners,
                    "age_sec": round(item.age_sec, 3),
                }
            )
        return held

    def prioritize(
        self,
        limit: int = 10,
        now: Optional[float] = None,
        avoid_locked: bool = True,
    ) -> list[dict[str, Any]]:
        """Rank entities by swarm priority (defect pressure minus lock penalty).

        The score rewards high ``DEFECT_SUSPECT`` and ``COVERAGE_GAP`` pressure,
        boosts entities that many distinct agents suspect, and penalises
        entities another agent is already refactoring, so the swarm naturally
        self-organises without any central scheduler.
        """
        current = time.time() if now is None else now
        scores: dict[str, dict[str, Any]] = {}

        for item in self.field(now=current):
            entry = scores.setdefault(
                item.entity,
                {"entity": item.entity, "score": 0.0, "signals": {}, "owners": set()},
            )
            entry["signals"][item.pheromone.value] = round(item.effective_strength, 6)
            if item.pheromone is PheromoneType.DEFECT_SUSPECT:
                entry["score"] += item.effective_strength * 2.0
            elif item.pheromone is PheromoneType.COVERAGE_GAP:
                entry["score"] += item.effective_strength * 1.2
            elif item.pheromone is PheromoneType.DEPENDENCY_HOTSPOT:
                entry["score"] += item.effective_strength * 0.8
            elif item.pheromone is PheromoneType.CANARY_VERIFIED_STABLE:
                entry["score"] -= item.effective_strength * 0.6
            elif item.pheromone is PheromoneType.CONVERGENCE_LOCKED:
                entry["score"] -= item.effective_strength * 1.5
            elif item.pheromone is PheromoneType.UNDER_SURGICAL_REFACTOR:
                entry["score"] -= item.effective_strength * 3.0 if avoid_locked else 0.0
            entry["score"] += 0.25 * max(item.contributors - 1, 0)

        for lock in self.locked_entities(now=current):
            scores.setdefault(
                lock["entity"],
                {"entity": lock["entity"], "score": 0.0, "signals": {}, "owners": set()},
            )["owners"].update(lock["owners"])

        ranked = sorted(scores.values(), key=lambda item: item["score"], reverse=True)
        results: list[dict[str, Any]] = []
        for entry in ranked[: max(int(limit), 0) or len(ranked)]:
            results.append(
                {
                    "entity": entry["entity"],
                    "score": round(entry["score"], 6),
                    "signals": entry["signals"],
                    "owners": sorted(entry["owners"]),
                    "locked": bool(entry["owners"]),
                }
            )
        return results

    def query(
        self,
        mode: str = "field",
        entity: str = "",
        pheromone: str = "",
        agent_id: str = "",
        limit: int = 50,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Unified read API used by the agent tool.

        Args:
            mode: ``field``, ``traces``, ``locks``, ``priorities`` or ``stats``.
            entity: Optional entity filter.
            pheromone: Optional pheromone filter.
            agent_id: Optional emitting-agent filter.
            limit: Maximum number of entries returned.
            now: Optional evaluation timestamp override.

        Returns:
            A JSON-serializable result dictionary.
        """
        mode = (mode or "field").strip().lower()
        if mode == "field":
            fields = self.field(entity=entity or None, pheromone=pheromone or None, now=now)
            fields = fields[: max(int(limit), 0)] if limit else fields
            return _success(mode, [item.to_dict() for item in fields])
        if mode == "traces":
            events = self.traces(
                entity=entity or None,
                pheromone=pheromone or None,
                agent_id=agent_id or None,
                limit=limit,
            )
            return _success(mode, [event.to_dict() for event in events])
        if mode == "locks":
            locks = self.locked_entities(now=now)
            return _success(mode, locks[: max(int(limit), 0)] if limit else locks)
        if mode == "priorities":
            return _success(mode, self.prioritize(limit=limit, now=now))
        if mode == "stats":
            events = self.store.all_events()
            return _success(
                mode,
                [
                    {
                        "event_count": len(events),
                        "field_entries": len(self.field(now=now)),
                        "distinct_entities": len({event.entity for event in events}),
                        "distinct_agents": len({event.agent_id for event in events}),
                        "stream_capacity": self.stream.capacity,
                        "stream_depth": len(self.stream.recent(limit=0)),
                        "default_half_life_sec": self.default_half_life_sec,
                    }
                ],
            )
        return _failure(
            f"unknown mode '{mode}'; expected one of field, traces, locks, priorities, stats"
        )

    # -- maintenance -------------------------------------------------------

    def evaporate(self, min_effective: float = MIN_EFFECTIVE_STRENGTH, now: Optional[float] = None) -> int:
        """Remove fully evaporated signals, returning the number pruned."""
        return self.store.prune_expired(min_effective=min_effective, now=now)

    def close(self) -> None:
        """Release durable resources."""
        self.store.close()


def _new_event_id() -> str:
    """Generate a collision-resistant event identifier."""
    return f"sev-{uuid.uuid4().hex[:16]}"


def _success(mode: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a successful mesh read response."""
    return {"success": True, "mode": mode, "count": len(results), "results": results}


def _failure(message: str) -> dict[str, Any]:
    """Build a failed mesh response."""
    return {"success": False, "error": message, "count": 0, "results": []}


# ---------------------------------------------------------------------------
# Process-wide registry
# ---------------------------------------------------------------------------

_MESH_LOCK = threading.Lock()
_MESHES: dict[str, StigmergicEventMesh] = {}


def get_stigmergic_mesh(
    db_path: Optional[str | Path] = None,
    default_half_life_sec: float = DEFAULT_HALF_LIFE_SEC,
) -> StigmergicEventMesh:
    """Return the shared mesh for ``db_path`` (``:memory:`` when omitted)."""
    key = str(db_path) if db_path else ":memory:"
    with _MESH_LOCK:
        mesh = _MESHES.get(key)
        if mesh is None:
            mesh = StigmergicEventMesh(
                db_path=db_path, default_half_life_sec=default_half_life_sec
            )
            _MESHES[key] = mesh
        return mesh


def default_mesh_path(workspace_root: str | Path) -> str:
    """Return the conventional on-disk mesh path for a workspace."""
    return str(Path(workspace_root) / ".agent-workspace" / DEFAULT_DB_FILENAME)
