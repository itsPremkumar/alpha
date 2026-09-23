"""RSI lineage: durable candidate identity, parent links, status transitions.

Implements WP-A1 (plan ``references/ALPHA_RSI_IMPLEMENTATION_PLAN.md`` §3,
features #5/#22). Honesty contract (§5.6):

- Every candidate gets exactly ONE durable identity (``candidate_id``);
  status changes go through :meth:`RsiLineageStore.transition`.
- ``parent_id`` is recorded verbatim from the caller — ``None`` becomes JSON
  ``null`` — and the store never guesses a parent: :meth:`RsiLineageStore.ancestry`
  stops at an unresolvable link instead of inventing ancestors.
- ``evidence_kind`` must be in the whitelist ``{measured, simulated,
  heuristic, unverified}``; it is validated at write AND load time. The
  legacy models default ``"unknown"`` maps to the equivalent neutral
  ``"unverified"``; any other value raises ``ValueError`` (fail-closed — no
  fabricated evidence labels are ever stored).
- Persistence failures never raise: the operation completes in-memory and
  ``store.persistence`` flips to ``"degraded"`` with a logged warning
  (precedent: ``EvolutionEngine._record_ledger_event``).
- Corrupt storage degrades honestly (precedent:
  ``EvolutionEngine._load_persisted_ledger``): unreadable/unparseable
  ``lineage.jsonl`` lines are skipped and counted (``store.skipped_lines`` +
  a warning with the real count), never repaired or interpreted; missing
  storage loads as an empty state, never a fabricated history.

Persistence layout (under ``root``, default ``runtime_home()/rsi``):

- ``lineage.jsonl`` — append-only source of truth, one event per
  ``record()``/``transition()``;
- ``lineage_snapshot.json`` — derived cache rebuilt atomically (tmp +
  ``os.replace`` via ``alpha.evolution.identity.atomic_write_json``) on load
  and after every event. A corrupt line never makes it into the snapshot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.identity import atomic_write_json

logger = logging.getLogger(__name__)

#: Allowed record statuses — the honest lifecycle (plan §3 WP-A1): a superset
#: of ``alpha.evolution.engine.CandidateStatus`` plus ``archived``.
LINEAGE_STATUSES: tuple[str, ...] = (
    "candidate",
    "benchmarking",
    "gated",
    "promoted",
    "rejected",
    "rolled_back",
    "archived",
)

#: §5.6 honesty whitelist: an ``evidence_kind`` outside this set would be a
#: fabricated evidence label, so it is refused at the boundary (write AND load).
EVIDENCE_KINDS: frozenset[str] = frozenset({"measured", "simulated", "heuristic", "unverified"})

#: Legacy ``alpha.rsi.models`` results default to ``"unknown"``, which means
#: the same "no verified evidence" thing as the whitelist's ``"unverified"`` —
#: normalized on the way in, never treated as a fifth evidence kind.
_LEGACY_EVIDENCE_KINDS: dict[str, str] = {"unknown": "unverified"}

LINEAGE_FILE_NAME = "lineage.jsonl"
SNAPSHOT_FILE_NAME = "lineage_snapshot.json"
SNAPSHOT_VERSION = 1

_EVENT_RECORDED = "recorded"
_EVENT_TRANSITIONED = "transitioned"


@dataclass
class RsiLineageRecord:
    """One candidate's durable lineage identity (plan §3 WP-A1)."""

    candidate_id: str
    parent_id: str | None
    cycle_id: str
    surface: str
    target: str
    payload_hash: str
    mutation_operator: str
    evidence_kind: str
    created_at: float
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_evidence_kind(value: Any) -> str:
    """Validate against the §5.6 whitelist; map the legacy default honestly."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"evidence_kind must be a non-empty string in {sorted(EVIDENCE_KINDS)}, got {value!r}.")
    normalized = _LEGACY_EVIDENCE_KINDS.get(value, value)
    if normalized not in EVIDENCE_KINDS:
        raise ValueError(f"evidence_kind {value!r} is not in the honesty whitelist {sorted(EVIDENCE_KINDS)} (plan 5.6); refusing to record a fabricated evidence label.")
    return normalized


def _require_status(status: Any) -> str:
    """Validate a lifecycle status against the closed ``LINEAGE_STATUSES`` set."""
    if status not in LINEAGE_STATUSES:
        raise ValueError(f"status {status!r} is not one of the allowed lineage statuses {list(LINEAGE_STATUSES)}.")
    return status


def _get_field(candidate: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a mapping or an attribute-style candidate object."""
    if isinstance(candidate, Mapping):
        return candidate.get(key, default)
    return getattr(candidate, key, default)


def _extract_candidate_id(candidate: Any) -> str:
    """Pull the caller's durable id; refuse to invent one when absent."""
    for key in ("candidate_id", "id", "variant_id"):
        value = _get_field(candidate, key, None)
        if isinstance(value, str) and value.strip():
            return value
    raise ValueError("Candidate must carry a durable id (candidate_id, id, or variant_id); refusing to invent an identity.")


def _canonical_payload_hash(payload: Any) -> str:
    """Real sha256 over the canonical JSON of the payload (non-JSON leaf values
    are stringified deterministically — documented, not hidden)."""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _extract_record_fields(candidate: Any) -> dict[str, Any]:
    """Derive every record field the candidate actually carries.

    Absent facts become honest placeholders (``""`` / ``"unknown"`` / now) —
    never guessed values that would read as measurements.
    """
    candidate_id = _extract_candidate_id(candidate)
    payload_hash = _get_field(candidate, "payload_hash", None)
    if not isinstance(payload_hash, str) or not payload_hash:
        payload = _get_field(candidate, "payload", None)
        payload_hash = "unknown" if payload is None else _canonical_payload_hash(payload)
    cycle_id = _get_field(candidate, "cycle_id", None)
    if not isinstance(cycle_id, str) or not cycle_id:
        cycle_id = "unknown"
    surface = _get_field(candidate, "surface", None)
    target = _get_field(candidate, "target", None)
    created_at = _get_field(candidate, "created_at", None)
    if created_at is None:
        created_at = time.time()
    else:
        created_at = float(created_at)  # garbage in → honest ValueError, never a silent fake timestamp
    return {
        "candidate_id": candidate_id,
        "cycle_id": cycle_id,
        "surface": "" if surface is None else str(surface),
        "target": "" if target is None else str(target),
        "payload_hash": payload_hash,
        "evidence_kind": _normalize_evidence_kind(_get_field(candidate, "evidence_kind", "unverified")),
        "created_at": created_at,
        "status": _get_field(candidate, "status", "candidate"),
    }


def _record_from_event(event: Any) -> RsiLineageRecord | None:
    """Rebuild a record from one JSONL event; ``None`` = corrupt/unusable line."""
    if not isinstance(event, dict) or event.get("event") not in {_EVENT_RECORDED, _EVENT_TRANSITIONED}:
        return None
    payload = event.get("record")
    if not isinstance(payload, dict):
        return None
    if not all(key in payload for key in (field.name for field in fields(RsiLineageRecord))):
        return None
    candidate_id = payload["candidate_id"]
    if not isinstance(candidate_id, str) or not candidate_id:
        return None
    parent_id = payload["parent_id"]
    if parent_id is not None and not isinstance(parent_id, str):
        return None
    for key in ("cycle_id", "surface", "target", "payload_hash", "mutation_operator"):
        if not isinstance(payload[key], str):
            return None
    try:
        evidence_kind = _normalize_evidence_kind(payload["evidence_kind"])
        created_at = float(payload["created_at"])
    except (TypeError, ValueError):
        return None
    if payload["status"] not in LINEAGE_STATUSES:
        return None
    return RsiLineageRecord(
        candidate_id=candidate_id,
        parent_id=parent_id,
        cycle_id=payload["cycle_id"],
        surface=payload["surface"],
        target=payload["target"],
        payload_hash=payload["payload_hash"],
        mutation_operator=payload["mutation_operator"],
        evidence_kind=evidence_kind,
        created_at=created_at,
        status=payload["status"],
    )


class RsiLineageStore:
    """Append-only RSI lineage store with an atomic derived snapshot.

    Durability semantics: ``persistence`` starts ``"ok"`` and flips to
    ``"degraded"`` (sticky for this instance) on the first failed write — the
    append-only file may then contain a hole we will not paper over. Reads
    never raise: missing/corrupt storage loads as an honest empty/partial
    state with ``skipped_lines`` reporting the real count.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else runtime_home() / "rsi"
        self.lineage_path = self.root / LINEAGE_FILE_NAME
        self.snapshot_path = self.root / SNAPSHOT_FILE_NAME
        self.skipped_lines = 0
        self._lock = threading.Lock()
        self._records: dict[str, RsiLineageRecord] = {}
        self._events: list[dict[str, Any]] = []
        self._persistence = "ok"
        self._load()

    @property
    def persistence(self) -> str:
        """``"ok"`` or ``"degraded"`` — write health since construction."""
        return self._persistence

    def _load(self) -> None:
        """Rebuild state from the JSONL source of truth (honest on corruption)."""
        try:
            lines = self.lineage_path.read_text(encoding="utf-8").splitlines()
            readable = True
        except FileNotFoundError:
            lines = []
            readable = True
        except OSError as exc:
            logger.warning("RSI lineage %s is unreadable; starting from an honest empty state: %s", self.lineage_path, exc)
            lines = []
            readable = False
        skipped = 0
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            record = _record_from_event(event)
            if record is None:
                skipped += 1
                continue
            self._records[record.candidate_id] = record
            self._events.append(event)
        self.skipped_lines = skipped
        if skipped:
            logger.warning("Skipped %d corrupt/partial RSI lineage line(s) in %s", skipped, self.lineage_path)
        if readable:
            # Refresh the derived cache from the source of truth — an empty
            # (honest) state when nothing exists, never a stale leftover.
            # On an OSError read we leave the last good snapshot untouched.
            self._write_snapshot()

    def _snapshot_payload(self) -> dict[str, Any]:
        return {
            "version": SNAPSHOT_VERSION,
            "rebuilt_at": time.time(),
            "skipped_corrupt_lines": self.skipped_lines,
            "records": [record.to_dict() for record in self._records.values()],
        }

    def _write_snapshot(self) -> bool:
        try:
            atomic_write_json(self.snapshot_path, self._snapshot_payload())
        except (OSError, TypeError, ValueError) as exc:
            self._persistence = "degraded"
            logger.warning("Could not persist RSI lineage snapshot to %s: %s", self.snapshot_path, exc)
            return False
        return True

    def _append_line(self, line: str) -> bool:
        try:
            self.lineage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.lineage_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except (OSError, TypeError, ValueError) as exc:
            self._persistence = "degraded"
            logger.warning("Could not append RSI lineage event to %s: %s", self.lineage_path, exc)
            return False
        return True

    def _persist(self, line: str) -> None:
        """Best-effort durability for one event; failures degrade, never raise."""
        self._append_line(line)
        self._write_snapshot()

    def record(self, candidate: Any, *, parent_id: str | None, mutation_operator: str) -> RsiLineageRecord:
        """Record a new durable identity for ``candidate`` (exactly once).

        ``parent_id`` and ``mutation_operator`` are caller claims recorded
        verbatim; an absent parent stays ``null`` — the store never guesses.
        """
        extracted = _extract_record_fields(candidate)
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError(f"parent_id must be a string or None, got {type(parent_id).__name__}.")
        if not isinstance(mutation_operator, str):
            raise ValueError(f"mutation_operator must be a string, got {type(mutation_operator).__name__}.")
        status = _require_status(extracted["status"])
        with self._lock:
            candidate_id = extracted["candidate_id"]
            if candidate_id in self._records:
                raise ValueError(f"Candidate '{candidate_id}' already has a recorded lineage identity; a durable identity is recorded once — use transition() for status changes.")
            record = RsiLineageRecord(
                candidate_id=candidate_id,
                parent_id=parent_id,
                cycle_id=extracted["cycle_id"],
                surface=extracted["surface"],
                target=extracted["target"],
                payload_hash=extracted["payload_hash"],
                mutation_operator=mutation_operator,
                evidence_kind=extracted["evidence_kind"],
                created_at=extracted["created_at"],
                status=status,
            )
            event = {
                "event": _EVENT_RECORDED,
                "candidate_id": record.candidate_id,
                "status": record.status,
                "at": time.time(),
                "record": record.to_dict(),
            }
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            self._records[record.candidate_id] = record
            self._events.append(event)
            self._persist(line)
            return record

    def transition(self, candidate_id: str, status: str, *, reason: str = "", evidence: dict | None = None) -> RsiLineageRecord:
        """Move a known candidate to a new lifecycle status.

        Raises ``KeyError`` for an unknown ``candidate_id`` (fail-closed — no
        phantom records) and ``ValueError`` for a status outside
        ``LINEAGE_STATUSES``. ``reason``/``evidence`` are recorded verbatim.
        """
        status = _require_status(status)
        if not isinstance(reason, str):
            raise ValueError(f"reason must be a string, got {type(reason).__name__}.")
        if evidence is not None and not isinstance(evidence, dict):
            raise ValueError(f"evidence must be a dict or None, got {type(evidence).__name__}.")
        with self._lock:
            record = self._records.get(candidate_id)
            if record is None:
                raise KeyError(f"Unknown lineage candidate_id '{candidate_id}'")
            if evidence is not None:
                json.dumps(evidence)  # fail closed on a non-serializable caller blob BEFORE mutating state
            record.status = status
            event = {
                "event": _EVENT_TRANSITIONED,
                "candidate_id": record.candidate_id,
                "status": status,
                "at": time.time(),
                "reason": reason,
                "evidence": evidence,
                "record": record.to_dict(),
            }
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            self._events.append(event)
            self._persist(line)
            return record

    def get(self, candidate_id: str) -> RsiLineageRecord | None:
        with self._lock:
            return self._records.get(candidate_id)

    def ancestry(self, candidate_id: str) -> list[RsiLineageRecord]:
        """Walk ``parent_id`` links from ``candidate_id`` up to the root.

        The walk is cycle-bounded: a visited set plus a store-size depth
        budget stop loops, and an unresolvable parent link ends the chain —
        ancestors are never invented.
        """
        with self._lock:
            chain: list[RsiLineageRecord] = []
            seen: set[str] = set()
            current: str | None = candidate_id
            budget = len(self._records) + 1
            while current is not None and current not in seen and budget > 0:
                budget -= 1
                seen.add(current)
                record = self._records.get(current)
                if record is None:
                    break
                chain.append(record)
                current = record.parent_id
            return chain

    def promotions(self) -> list[dict[str, Any]]:
        """One dict per real promotion event (append-only truth).

        ``current_status`` discloses the candidate's status *now* — a later
        rollback never erases the promotion, and never hides the rollback.
        """
        with self._lock:
            promoted: list[dict[str, Any]] = []
            for event in self._events:
                if event.get("status") != "promoted":
                    continue
                snapshot = event.get("record") if isinstance(event.get("record"), dict) else {}
                current = self._records.get(str(event.get("candidate_id", "")))
                promoted.append(
                    {
                        "candidate_id": event.get("candidate_id"),
                        "cycle_id": snapshot.get("cycle_id", "unknown"),
                        "at": event.get("at"),
                        "reason": event.get("reason", ""),
                        "evidence": event.get("evidence"),
                        "record": dict(snapshot),
                        "current_status": current.status if current else None,
                    }
                )
            return promoted


def record_from_evol_candidate(cand: Any) -> RsiLineageRecord:
    """Mirror an ``alpha.evolution`` candidate into an RSI lineage store.

    Bridge for WP-A1: evolution stays untouched (Phase A wraps at ``alpha/rsi``
    call sites). Idempotent — an already-mirrored candidate returns its
    existing record without appending a second birth event. Unknown facts stay
    honest: no parent → ``null``, no mutation operator → ``"unknown"``, no
    evidence → ``"unverified"``, no cycle concept → ``"unknown"``; the payload
    hash is a real sha256 over the candidate's canonical payload. Persistence
    failures inside the fresh store log a warning (use the store API directly
    when you need the ``persistence`` status).
    """
    store = RsiLineageStore()
    candidate_id = _extract_candidate_id(cand)
    existing = store.get(candidate_id)
    if existing is not None:
        return existing
    operator = _get_field(cand, "mutation_operator", None)
    if not isinstance(operator, str) or not operator:
        operator = "unknown"
    return store.record(cand, parent_id=_get_field(cand, "parent_id", None), mutation_operator=operator)
