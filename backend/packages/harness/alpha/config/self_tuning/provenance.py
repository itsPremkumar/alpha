"""Append-only, hash-chained configuration provenance.

The record shape follows the audit requirements in Google SRE's *Release
Engineering*: a reviewer must be able to answer who changed a production
setting, what the before/after values were, and why.  Every JSONL record commits
to its predecessor, so removing or reordering an interior entry is detectable.
Callers that keep an external expected count/head can also detect truncation of
the final record.

Secret-looking value paths are redacted.  The package neither stores nor logs
an operator authorization token.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ._locking import os_file_lock
from .config import SelfTuningConfig
from .models import (
    ApplyOutcome,
    CanaryResult,
    ChangeSet,
    Clock,
    ProvenanceAction,
    ProvenanceEntry,
    ValidationResult,
    VerificationResult,
)

GENESIS_HASH = "0" * 64
_REDACTED = "<redacted>"
_SECRET_MARKERS = ("api_key", "secret", "token", "password", "credential", "client_secret")


class ProvenanceChainError(RuntimeError):
    """Raised when the append-only log is missing, reordered, or modified."""


def _redact(path: str, value: Any) -> Any:
    lowered = path.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return _REDACTED
    return value


def _canonical_hash(entry: ProvenanceEntry) -> str:
    payload = entry.model_dump(mode="json", exclude={"entry_hash"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ProvenanceLedger:
    """Serialize appends and verify the complete before/after audit chain."""

    def __init__(self, path: Path, config: SelfTuningConfig, clock: Clock) -> None:
        self.path = path.resolve()
        self.config = config
        self.clock = clock

    @property
    def lock_path(self) -> Path:
        return self.path.with_name(f".{self.path.name}.lock")

    def _parse(self) -> tuple[ProvenanceEntry, ...]:
        if not self.path.exists():
            return ()
        entries: list[ProvenanceEntry] = []
        for line_number, raw in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                raise ProvenanceChainError(f"provenance line {line_number} is blank")
            try:
                payload = json.loads(raw)
                entries.append(ProvenanceEntry.model_validate(payload))
            except (json.JSONDecodeError, ValidationError) as exc:
                raise ProvenanceChainError(f"provenance line {line_number} is invalid: {exc}") from exc
        return tuple(entries)

    @staticmethod
    def verify_entries(
        entries: tuple[ProvenanceEntry, ...],
        *,
        expected_count: int | None = None,
        expected_head: str | None = None,
    ) -> None:
        """Verify sequence, predecessor link, and content hash for every record."""
        previous = GENESIS_HASH
        for index, entry in enumerate(entries, start=1):
            if entry.sequence != index:
                raise ProvenanceChainError(f"provenance sequence is missing or reordered at position {index}: found {entry.sequence}")
            if entry.previous_hash != previous:
                raise ProvenanceChainError(f"provenance predecessor mismatch at sequence {entry.sequence}")
            if _canonical_hash(entry) != entry.entry_hash:
                raise ProvenanceChainError(f"provenance content hash mismatch at sequence {entry.sequence}")
            previous = entry.entry_hash
        if expected_count is not None and len(entries) != expected_count:
            raise ProvenanceChainError(f"provenance count is {len(entries)}, expected {expected_count}")
        if expected_head is not None and (not entries or entries[-1].entry_hash != expected_head):
            raise ProvenanceChainError("provenance head does not match the externally recorded head")

    def read_entries(self, *, expected_count: int | None = None, expected_head: str | None = None) -> tuple[ProvenanceEntry, ...]:
        """Read and fully verify the audit chain."""
        entries = self._parse()
        self.verify_entries(entries, expected_count=expected_count, expected_head=expected_head)
        return entries

    def replay(self, change_set_id: str) -> tuple[ProvenanceEntry, ...]:
        """Return the ordered propose-to-verification facts for one change set."""
        return tuple(entry for entry in self.read_entries() if entry.change_set_id == change_set_id)

    def _write_entry(self, entry: ProvenanceEntry) -> None:
        """Append and fsync one already-sequenced record."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(entry.model_dump_json() + "\n")
            handle.flush()
            import os

            os.fsync(handle.fileno())

    def _next_entry(
        self,
        *,
        action: ProvenanceAction,
        change_set_id: str,
        author: str,
        rationale: str,
        status: str,
        path: str | None = None,
        before: Any = None,
        after: Any = None,
    ) -> ProvenanceEntry:
        """Lock, verify, extend, and fsync the chain with one new entry."""
        with os_file_lock(self.lock_path):
            existing = self._parse()
            self.verify_entries(existing)
            previous = existing[-1].entry_hash if existing else GENESIS_HASH
            unsigned = ProvenanceEntry(
                sequence=len(existing) + 1,
                action=action,
                change_set_id=change_set_id,
                path=path,
                before=before,
                after=after,
                author=author,
                rationale=rationale,
                at=self.clock.now(),
                status=status,
                previous_hash=previous,
                entry_hash=GENESIS_HASH,
            )
            entry = unsigned.model_copy(update={"entry_hash": _canonical_hash(unsigned)})
            self._write_entry(entry)
            return entry

    def append(self, entry: ProvenanceEntry) -> ProvenanceEntry | None:
        """Append one hash-chained record; refuse to extend a damaged chain."""
        if not self.config.enabled:
            return None
        with os_file_lock(self.lock_path):
            existing = self._parse()
            self.verify_entries(existing)
            previous = existing[-1].entry_hash if existing else GENESIS_HASH
            if entry.sequence != len(existing) + 1 or entry.previous_hash != previous:
                raise ProvenanceChainError("refusing to append a provenance record with a broken sequence/predecessor")
            if _canonical_hash(entry) != entry.entry_hash:
                raise ProvenanceChainError("refusing to append a provenance record with an invalid content hash")
            self._write_entry(entry)
            return entry

    def record(self, action: ProvenanceAction, change_set: ChangeSet, status: str, reason: str) -> tuple[ProvenanceEntry, ...]:
        """Record before/after evidence for every change in a proposal."""
        if not self.config.enabled:
            return ()
        recorded: list[ProvenanceEntry] = []
        for change in change_set.changes:
            path = change.target_path
            recorded.append(
                self._next_entry(
                    action=action,
                    change_set_id=change_set.id,
                    author=change.author,
                    rationale=reason,
                    status=status,
                    path=path,
                    before=_redact(path, change.previous_value),
                    after=_redact(path, change.proposed_value),
                )
            )
        return tuple(recorded)

    def record_propose(self, change_set: ChangeSet) -> tuple[ProvenanceEntry, ...]:
        """Record that a proposal was submitted (permission is not implied)."""
        return self.record(ProvenanceAction.PROPOSE, change_set, "proposed", "candidate submitted for validation")

    def record_validation(self, change_set: ChangeSet, result: ValidationResult) -> tuple[ProvenanceEntry, ...]:
        """Record pre-write validation evidence."""
        return self.record(ProvenanceAction.VALIDATE, change_set, "ok" if result.ok else "refused", result.reason or result.blast_radius_class.value)

    def record_canary(self, change_set: ChangeSet, result: CanaryResult) -> tuple[ProvenanceEntry, ...]:
        """Record the bounded canary decision."""
        return self.record(ProvenanceAction.CANARY, change_set, result.decision.value, result.reason)

    def record_apply(self, change_set: ChangeSet, status: ApplyOutcome, reason: str) -> tuple[ProvenanceEntry, ...]:
        """Record the atomic write outcome."""
        return self.record(ProvenanceAction.APPLY, change_set, status.value, reason)

    def record_verify(self, change_set: ChangeSet, result: VerificationResult) -> tuple[ProvenanceEntry, ...]:
        """Record post-apply health evidence."""
        return self.record(ProvenanceAction.VERIFY, change_set, result.status.value, result.reason)

    def record_rollback(self, change_set: ChangeSet, status: ApplyOutcome, reason: str) -> tuple[ProvenanceEntry, ...]:
        """Record an auditable reversal proposal and its terminal outcome."""
        return self.record(ProvenanceAction.ROLLBACK, change_set, status.value, reason)

    def record_confirm(self, change_set: ChangeSet, reason: str) -> tuple[ProvenanceEntry, ...]:
        """Record final health confirmation and transaction cleanup."""
        return self.record(ProvenanceAction.CONFIRM, change_set, "verified", reason)

    def record_recover(self, change_set_id: str, author: str, status: str, reason: str) -> tuple[ProvenanceEntry, ...]:
        """Record startup recovery when no in-memory change set exists."""
        if not self.config.enabled:
            return ()
        entry = self._next_entry(
            action=ProvenanceAction.RECOVER,
            change_set_id=change_set_id,
            author=author,
            rationale=reason,
            status=status,
        )
        return (entry,)
