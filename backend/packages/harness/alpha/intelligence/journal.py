"""The learning journal: one append-only, hash-linked record of every learning event.

Why this exists when three provenance ledgers already do
--------------------------------------------------------
Alpha already has hash-linked append-only ledgers in three places —
:mod:`alpha.evolution.evidence.provenance`, :mod:`alpha.config.self_tuning.provenance`
and :mod:`alpha.rsi.lineage`. None of them is wrong, and none of them is replaced
here. Each is scoped to a different object:

* ``evidence.provenance`` chains **proposals and verdicts** for repo changes.
* ``self_tuning.provenance`` chains **config change-sets** through apply/verify/rollback.
* ``rsi.lineage`` chains **evolution candidates** and their status transitions.

What none of them answers is "what did the intelligence layer itself decide,
and on what evidence" — which is the audit question the prompt's journal
requirement is really about. So this is a fourth ledger over a fourth object
type (a :class:`~alpha.intelligence.models.LearningEvent`), following the same
three rules those ledgers established:

1. **Append-only.** Written as JSONL, one event per line, never rewritten.
2. **Hash-linked.** Each entry carries ``prev_hash`` and ``entry_hash``, so
   removing or editing a historical line is detectable rather than invisible.
3. **No silent learning.** :meth:`LearningJournal.append` refuses to be silent —
   it records the mode the decision was made under, so an ``OBSERVE_ONLY``
   computation is permanently distinguishable from a ``PROMOTE`` that changed
   state.

Honesty rules
-------------
* The journal is written **even in** ``OBSERVE_ONLY``. Observing is an event
  too, and a system that only journals its writes cannot be audited for the
  decisions it declined to make.
* :meth:`verify_chain` reports the **first broken index**, not a boolean, so a
  reader can say which line is untrustworthy.
* A corrupt line is quarantined by :meth:`read` and *counted*, never silently
  skipped — the count travels with the result.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.intelligence.models import LearningEvent, LearningMode

logger = logging.getLogger(__name__)

__all__ = [
    "JOURNAL_RELATIVE_PATH",
    "GENESIS_HASH",
    "JournalEntry",
    "JournalVerification",
    "LearningJournal",
    "journal_path",
    "canonical_entry_hash",
]

#: Location under ``runtime_home()``. Deliberately inside the existing runtime
#: tree rather than a new top-level ``alpha-state/``: Alpha already owns
#: ``runtime_home()`` for exactly this kind of derived state, and a second state
#: root would be the duplicate the prompt's own rule 78 forbids.
JOURNAL_RELATIVE_PATH = Path("intelligence") / "learning-journal.jsonl"

#: The ``prev_hash`` of the first entry. A named constant so "before the chain
#: began" is a value in the data rather than an ``if`` in the verifier.
GENESIS_HASH = "0" * 64


def canonical_entry_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON of ``payload`` minus its own hash field.

    Canonical means sorted keys and no insignificant whitespace, so two
    processes that build the same entry produce the same digest regardless of
    dict insertion order.
    """
    body = {key: value for key, value in payload.items() if key != "entry_hash"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class JournalEntry:
    """One persisted journal line, plus its link fields."""

    index: int
    entry_hash: str
    prev_hash: str
    event: LearningEvent
    corrupt: bool = False
    raw: str = ""
    """The original line when :attr:`corrupt` is set, so nothing is unrecoverable."""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "index": self.index,
            "prev_hash": self.prev_hash,
            "event": self.event.to_dict(),
        }
        payload["entry_hash"] = canonical_entry_hash(payload)
        return payload


@dataclass(frozen=True)
class JournalVerification:
    """Result of :meth:`LearningJournal.verify_chain`."""

    ok: bool
    checked: int
    broken_at: int | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "broken_at": self.broken_at,
            "reason": self.reason,
        }


def journal_path() -> Path:
    """Absolute path of the learning journal under ``runtime_home()``."""
    return runtime_home() / JOURNAL_RELATIVE_PATH


class LearningJournal:
    """Append-only, hash-linked learning journal.

    Thread-safe for the single-Gateway-process case via an ``RLock``. This is
    **not** a cross-process ledger: two Gateway workers appending concurrently
    would each hold a different ``prev_hash`` and the chain would fork. That is
    the same honest limitation the swarm and workflow event sinks declare, and
    it is stated rather than papered over — a deployment needing multi-process
    append must put this on the shared SQL event store.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else journal_path()
        self._lock = threading.RLock()

    # -- append -------------------------------------------------------------

    def append(self, event: LearningEvent) -> JournalEntry:
        """Append ``event`` and return the persisted entry.

        Called under the instance lock. A persistence failure raises rather than
        degrading to a log line: the journal is the audit trail, and an audit
        trail that can fail silently is worse than none, because its absence
        reads as "nothing happened".
        """
        if not isinstance(event, LearningEvent):
            raise TypeError(f"append expects a LearningEvent, got {type(event).__name__}")
        with self._lock:
            index, prev_hash = self._tail()
            payload = {"index": index, "prev_hash": prev_hash, "event": event.to_dict()}
            entry_hash = canonical_entry_hash(payload)
            line = json.dumps({**payload, "entry_hash": entry_hash}, ensure_ascii=False, separators=(",", ":"), default=str)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as exc:
                raise RuntimeError(f"could not append to learning journal {self.path}: {exc}") from exc
            return JournalEntry(index=index, entry_hash=entry_hash, prev_hash=prev_hash, event=event)

    def record(
        self,
        kind: str,
        *,
        mode: LearningMode = LearningMode.OBSERVE_ONLY,
        decision: str = "NO_CHANGE",
        reason: str = "",
        **fields: Any,
    ) -> JournalEntry:
        """Convenience constructor: build the event and append it in one call."""
        return self.append(
            LearningEvent(
                kind=kind,
                mode=mode,
                decision=decision,
                reason=reason,
                before=dict(fields.pop("before", {}) or {}),
                after=dict(fields.pop("after", {}) or {}),
                regression=dict(fields.pop("regression", {}) or {}),
                cost=dict(fields.pop("cost", {}) or {}),
                experiences=list(fields.pop("experiences", []) or []),
                expert_id=str(fields.pop("expert_id", "") or ""),
                experiment_id=str(fields.pop("experiment_id", "") or ""),
            )
        )

    # -- read ---------------------------------------------------------------

    def read(self, *, limit: int | None = None) -> tuple[list[JournalEntry], int]:
        """Return ``(entries, corrupt_count)``.

        A line that will not parse, or that is not a JSON object, becomes a
        ``corrupt`` entry carrying its raw text and is **counted**, not dropped.
        Silently skipping a damaged line is how an audit trail loses exactly the
        record somebody needed.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return [], 0
        except OSError as exc:
            logger.warning("learning journal %s is unreadable: %s", self.path, exc)
            return [], 0
        entries: list[JournalEntry] = []
        corrupt = 0
        for offset, line in enumerate(raw.splitlines()):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError(f"line is {type(payload).__name__}, expected object")
                event = LearningEvent.from_dict(payload["event"])
                index = int(payload["index"])
                entry_hash = str(payload["entry_hash"])
                prev_hash = str(payload["prev_hash"])
            except (KeyError, TypeError, ValueError) as exc:
                corrupt += 1
                logger.warning("learning journal %s line %d is corrupt (%s); retained as a corrupt entry", self.path, offset + 1, exc)
                entries.append(JournalEntry(index=offset, entry_hash="", prev_hash="", event=_placeholder_event(), corrupt=True, raw=line))
                continue
            entries.append(JournalEntry(index=index, entry_hash=entry_hash, prev_hash=prev_hash, event=event))
        if limit is not None and limit >= 0:
            entries = entries[-limit:]
        return entries, corrupt

    def tail(self, *, limit: int = 20) -> tuple[list[JournalEntry], int]:
        """Most recent entries, newest last. ``read`` with a limit, named for intent."""
        return self.read(limit=limit)

    def recent(self, *, kinds: tuple[str, ...] = (), limit: int = 20) -> tuple[list[JournalEntry], int]:
        """Entries filtered by ``kind`` (empty tuple means no filter)."""
        entries, corrupt = self.read(limit=None)
        if kinds:
            wanted = set(kinds)
            entries = [entry for entry in entries if entry.event.kind in wanted]
        return entries[-limit:] if limit >= 0 else entries, corrupt

    # -- integrity ----------------------------------------------------------

    def verify_chain(self) -> JournalVerification:
        """Walk the chain and report the **first** broken index.

        Checks, in order per entry: contiguity of ``index``, that ``prev_hash``
        equals the previous entry's ``entry_hash``, and that the recomputed
        digest matches the stored one. A corrupt line breaks the walk and is
        reported as such rather than being skipped past.
        """
        entries, _corrupt = self.read(limit=None)
        if not entries:
            return JournalVerification(ok=True, checked=0, reason="journal is empty")

        expected_prev = GENESIS_HASH
        expected_index = entries[0].index
        checked = 0
        for position, entry in enumerate(entries):
            if entry.corrupt:
                return JournalVerification(
                    ok=False,
                    checked=checked,
                    broken_at=position,
                    reason=f"line {position + 1} is corrupt and breaks the chain",
                )
            if entry.index != expected_index:
                return JournalVerification(
                    ok=False,
                    checked=checked,
                    broken_at=position,
                    reason=f"index gap: expected {expected_index}, found {entry.index}",
                )
            if entry.prev_hash != expected_prev:
                return JournalVerification(
                    ok=False,
                    checked=checked,
                    broken_at=position,
                    reason=f"prev_hash mismatch at index {entry.index}: expected {expected_prev[:12]}…, found {entry.prev_hash[:12]}…",
                )
            recomputed = canonical_entry_hash({"index": entry.index, "prev_hash": entry.prev_hash, "event": entry.event.to_dict()})
            if recomputed != entry.entry_hash:
                return JournalVerification(
                    ok=False,
                    checked=checked,
                    broken_at=position,
                    reason=f"entry_hash mismatch at index {entry.index}: the recorded event does not hash to the stored digest",
                )
            expected_prev = entry.entry_hash
            expected_index += 1
            checked += 1
        return JournalVerification(ok=True, checked=checked, reason=f"{checked} entries verified")

    # -- internals ----------------------------------------------------------

    def _tail(self) -> tuple[int, str]:
        """Next index and the current head hash, read from the last line.

        Reading only the last line keeps append O(1) in journal length rather
        than re-verifying the whole chain on every write. A corrupt final line
        yields the genesis link, which makes the *next* append start a fresh
        segment instead of silently linking onto garbage — a broken tail is
        therefore visible in :meth:`verify_chain` rather than healed invisibly.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0, GENESIS_HASH
        except OSError as exc:
            raise RuntimeError(f"could not read learning journal {self.path} before append: {exc}") from exc
        last = ""
        for line in raw.splitlines():
            if line.strip():
                last = line
        if not last:
            return 0, GENESIS_HASH
        try:
            payload = json.loads(last)
            index = int(payload["index"])
            digest = str(payload["entry_hash"])
            if canonical_entry_hash({"index": index, "prev_hash": str(payload["prev_hash"]), "event": payload["event"]}) != digest:
                return index + 1, GENESIS_HASH
            return index + 1, digest
        except (KeyError, TypeError, ValueError):
            # Unreadable tail: count what we can so indices stay monotonic.
            counted = sum(1 for line in raw.splitlines() if line.strip())
            return counted, GENESIS_HASH


def _placeholder_event() -> LearningEvent:
    """A stand-in event for a corrupt line.

    It carries ``kind="corrupt"`` and the real reason in ``reason`` so a caller
    rendering the journal shows why an entry is blank rather than showing a
    silent gap. It is never appended, so it can never be mistaken for a real
    decision.
    """
    return LearningEvent(kind="corrupt", mode=LearningMode.OBSERVE_ONLY, decision="NO_CHANGE", reason="journal line could not be decoded")
