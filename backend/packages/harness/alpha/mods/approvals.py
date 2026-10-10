"""Durable hold and approval store — turning a ``DEFER`` into a real gate.

``alpha/mods/AGENTS.md`` states the gap plainly: a mod's ``DEFER`` is an honest
hold signal, but it is **not** a complete approval flow until an authenticated
operator can durably release or reject *the same action and its idempotency
key*. This module is that flow.

The idempotency key is the load-bearing piece. A hold is keyed on the identity of
the action — tool name, canonical arguments, run, and tool call — not on a random
id, for two reasons:

1. **Retrying a held action re-opens the same hold.** The run's retry loop
   re-dispatches ``tool.requested`` after a failure; without a stable key that
   would create a second hold for one decision, and the operator who approved
   the first would find the second still pending.
2. **A different action never reuses a decision.** The key includes the tool
   call id, so an approval grants exactly the invocation it was asked about.

Durability is deliberately modest and honestly scoped: an atomic JSON file under
``runtime_home()``. That is atomic and restart-recoverable for **one** Gateway
process. It is not a shared multi-worker repository, and this module does not
claim cross-process exactly-once — the same boundary every other local store in
this repo states.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)

#: File name under ``runtime_home()``.
HOLD_STORE_FILENAME = "mod_holds.json"

#: How long a pending hold stays decidable before it is expired rather than
#: silently held forever.
DEFAULT_HOLD_TTL_SECONDS = 4 * 3600.0

#: How many holds the store retains, decided and pending together.
MAX_HOLD_RECORDS = 500


class HoldDecision(StrEnum):
    """The operator outcomes a hold can carry."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class HoldStatus(StrEnum):
    """Whether a dispatch should run, refuse, or wait.

    Kept distinct from :class:`HoldDecision` because "the store says approved"
    and "this dispatch may proceed" are different facts: an expired hold that was
    approved is still expired.
    """

    PROCEED = "proceed"
    REFUSE = "refuse"
    WAIT = "wait"


@dataclass
class HoldRecord:
    """One durable hold on one action."""

    hold_id: str
    idempotency_key: str
    tool_name: str
    tool_args: dict[str, Any]
    risk_level: str
    reason: str
    run_id: str = ""
    tool_call_id: str = ""
    event_id: str = ""
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    decided_by: str = ""
    decision: str = HoldDecision.PENDING.value
    decision_reason: str = ""
    impact: dict[str, Any] = field(default_factory=dict)
    expires_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_terminal(self) -> bool:
        return self.decision in (HoldDecision.APPROVED.value, HoldDecision.REJECTED.value, HoldDecision.EXPIRED.value)

    def is_expired(self, now: float | None = None) -> bool:
        """True when the hold's TTL has passed.

        Deliberately **not** gated on the decision: an approval whose TTL has
        elapsed is a decision about a world that no longer exists, so expiry
        voids it. Gating this on ``decision == pending`` would let a stale
        approval outlive the action it was asked about, which is precisely the
        replay this whole module exists to prevent.
        """
        if not self.expires_at:
            return False
        return (now or time.time()) >= self.expires_at


def compute_idempotency_key(tool_name: str, tool_args: dict[str, Any], run_id: str, tool_call_id: str) -> str:
    """The stable identity of one action.

    Arguments are canonicalized by sorted JSON so a reordered dict — the same
    action re-dispatched by a different code path — produces the same key. The
    hash is truncated to 40 hex characters: enough to avoid collisions inside
    one installation, short enough to read in a log line.
    """
    canonical = json.dumps(tool_args if isinstance(tool_args, dict) else {}, sort_keys=True, default=str)
    raw = f"{tool_name}|{canonical}|{run_id}|{tool_call_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


class HoldStore:
    """Atomic, bounded, restart-recoverable store of held actions."""

    def __init__(self, *, root_dir: Path | str | None = None, ttl_seconds: float = DEFAULT_HOLD_TTL_SECONDS):
        self._explicit_root = Path(root_dir) if root_dir is not None else None
        self._ttl_seconds = max(60.0, float(ttl_seconds))
        self._lock = threading.RLock()
        self._records: dict[str, HoldRecord] = {}

    # -- persistence -------------------------------------------------------

    @property
    def store_file(self) -> Path:
        root = self._explicit_root or runtime_home()
        return Path(root) / HOLD_STORE_FILENAME

    def _load_locked(self) -> None:
        path = self.store_file
        try:
            if not path.exists():
                return
            raw = json.loads(path.read_text(encoding="utf-8"))
            rows = raw.get("holds") if isinstance(raw, dict) else None
            if not isinstance(rows, list):
                # An unreadable shape is disclosed, not treated as empty: a
                # store that answers "no holds" while holds sit on disk would
                # let a held action run unapproved.
                logger.error("Mod hold store at %s has an unreadable shape; existing holds are not enforceable", path)
                return
            for row in rows:
                if isinstance(row, dict):
                    try:
                        record = HoldRecord(**{k: v for k, v in row.items() if k in HoldRecord.__dataclass_fields__})
                    except TypeError:
                        continue
                    self._merge_locked(record)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Mod hold store could not be read (%s); pending holds cannot be released from disk", exc)

    def _merge_locked(self, incoming: HoldRecord) -> None:
        """Adopt a record read from disk without clobbering live in-memory state.

        A blind overwrite was a real bug: every operation reloads from disk, so a
        record mutated in this process (an aged expiry, a decision not yet
        flushed) was silently replaced by its on-disk snapshot and the mutation
        vanished. A decided record therefore only wins over an undecided one,
        and otherwise the later decision timestamp wins — which is what lets an
        approval taken in a *second* process reach a first one that already read.
        """
        current = self._records.get(incoming.hold_id)
        if current is None:
            self._records[incoming.hold_id] = incoming
            return
        if incoming.is_terminal() and not current.is_terminal():
            self._records[incoming.hold_id] = incoming
            return
        incoming_ts = incoming.decided_at or incoming.created_at
        current_ts = current.decided_at or current.created_at
        if incoming_ts > current_ts:
            self._records[incoming.hold_id] = incoming

    def _save_locked(self) -> bool:
        """Atomically replace the store. Returns whether the write landed."""
        path = self.store_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"schema": 1, "updated_at": time.time(), "holds": [r.to_dict() for r in self._records.values()]}
            fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, default=str)
                os.replace(tmp_name, path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            return True
        except OSError as exc:
            logger.error("Mod hold store could not be written (%s); the approval is not durable", exc)
            return False

    # -- operations --------------------------------------------------------

    def open_hold(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        run_id: str,
        tool_call_id: str,
        risk_level: str,
        reason: str,
        impact: dict[str, Any] | None = None,
    ) -> tuple[HoldRecord, bool]:
        """Create a hold, or return the existing one for the same action.

        Returns ``(record, created)``. ``created=False`` means a hold for this
        exact action already exists, so the caller must not render a second
        approval card — the operator is already looking at the first one.
        """
        key = compute_idempotency_key(tool_name, tool_args, run_id, tool_call_id)
        with self._lock:
            self._load_locked()
            for record in self._records.values():
                if record.idempotency_key == key:
                    if record.is_expired():
                        # Expiry voids whatever was decided. A record that was
                        # already recorded as expired is left as it stands.
                        if not record.is_terminal():
                            record.decision = HoldDecision.EXPIRED.value
                            record.decided_at = time.time()
                            record.decision_reason = "expired before a decision"
                            self._save_locked()
                        return record, False
                    return record, False
            hold_id = f"hold_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}"
            record = HoldRecord(
                hold_id=hold_id,
                idempotency_key=key,
                tool_name=str(tool_name),
                tool_args=dict(tool_args) if isinstance(tool_args, dict) else {},
                risk_level=str(risk_level),
                reason=str(reason),
                run_id=str(run_id or ""),
                tool_call_id=str(tool_call_id or ""),
                impact=dict(impact or {}),
                expires_at=time.time() + self._ttl_seconds,
            )
            self._records[hold_id] = record
            self._prune_locked()
            self._save_locked()
            return record, True

    def status_for(self, tool_name: str, tool_args: dict[str, Any], run_id: str, tool_call_id: str) -> tuple[HoldStatus, HoldRecord | None]:
        """Resolve a dispatch against the store.

        The order is the contract: an expired hold is expired *even if it was
        approved*, because a decision older than the TTL is a decision about a
        different world.
        """
        key = compute_idempotency_key(tool_name, tool_args, run_id, tool_call_id)
        with self._lock:
            self._load_locked()
            for record in self._records.values():
                if record.idempotency_key != key:
                    continue
                if record.is_expired():
                    if not record.is_terminal():
                        record.decision = HoldDecision.EXPIRED.value
                        record.decided_at = time.time()
                        record.decision_reason = "expired before a decision"
                        self._save_locked()
                    return HoldStatus.WAIT, record
                if record.decision == HoldDecision.APPROVED.value:
                    return HoldStatus.PROCEED, record
                if record.decision == HoldDecision.REJECTED.value:
                    return HoldStatus.REFUSE, record
                return HoldStatus.WAIT, record
        return HoldStatus.WAIT, None

    def decide(self, hold_id: str, decision: HoldDecision, *, operator: str, reason: str = "") -> HoldRecord | None:
        """Record an authenticated operator decision."""
        if decision not in (HoldDecision.APPROVED, HoldDecision.REJECTED):
            raise ValueError(f"decide() accepts only approved/rejected, got {decision}")
        with self._lock:
            self._load_locked()
            record = self._records.get(hold_id)
            if record is None:
                return None
            if record.is_terminal():
                # A decided hold is not re-decided. Two operators disagreeing is
                # a conversation, not a second write that overwrites the first.
                return record
            record.decision = decision.value
            record.decided_at = time.time()
            record.decided_by = str(operator or "unknown")
            record.decision_reason = str(reason or "")[:500]
            durable = self._save_locked()
            if not durable:
                logger.error("Decision on hold %s could not be persisted; it applies to this process only", hold_id)
            return record

    def approve(self, hold_id: str, *, operator: str, reason: str = "") -> HoldRecord | None:
        return self.decide(hold_id, HoldDecision.APPROVED, operator=operator, reason=reason)

    def reject(self, hold_id: str, *, operator: str, reason: str = "") -> HoldRecord | None:
        return self.decide(hold_id, HoldDecision.REJECTED, operator=operator, reason=reason)

    def get(self, hold_id: str) -> HoldRecord | None:
        with self._lock:
            self._load_locked()
            record = self._records.get(hold_id)
            return record

    def list_holds(self, *, decision: str | None = None, limit: int = 100) -> list[HoldRecord]:
        with self._lock:
            self._load_locked()
            rows = list(self._records.values())
        if decision:
            rows = [r for r in rows if r.decision == decision]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[: max(0, int(limit))]

    def _prune_locked(self) -> int:
        """Bound the store: decided records age out first, then oldest pending."""
        if len(self._records) <= MAX_HOLD_RECORDS:
            return 0
        ordered = sorted(self._records.values(), key=lambda r: (r.is_terminal(), r.created_at))
        surplus = len(self._records) - MAX_HOLD_RECORDS
        for record in ordered[:surplus]:
            self._records.pop(record.hold_id, None)
        return surplus


_default_hold_store: HoldStore | None = None
_default_store_lock = threading.Lock()


def get_hold_store() -> HoldStore:
    """The process-wide hold store."""
    global _default_hold_store
    with _default_store_lock:
        if _default_hold_store is None:
            _default_hold_store = HoldStore()
        return _default_hold_store


def set_hold_store(store: HoldStore | None) -> None:
    global _default_hold_store
    with _default_store_lock:
        _default_hold_store = store
