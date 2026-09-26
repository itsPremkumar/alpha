"""Tamper-evident, append-only hash chain over the committed lineage.

A committed version is a claim: "this passed the gate, and this was its score".
If that claim can be edited after the fact then the lineage is a suggestion, and
every metric computed from it inherits the edit. So each committed version is
sealed into a chain:

    entry_hash[n] = sha256(canonical_json(payload[n]) || entry_hash[n-1])

Editing a historical entry changes its own digest. Deleting one leaves a
``prev_hash`` that no longer matches its successor. Reordering breaks the
sequence numbers. All three are detected by :func:`verify_entries`.

**What this is and is not.** The digests live beside the data, so this is
*tamper-evident, not tamper-proof*: a writer with full write access to the store
can recompute the whole chain. The guarantee is that an edit which is not
accompanied by a full, correct re-seal is detected, and the guarantee is scoped
and stated rather than implied. Anything stronger needs a signing key held
outside this process, which is the platform decision in
``docs/asi/07_agent_stack_security.md`` and out of scope here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .evidence import GENESIS_HASH

__all__ = ["ChainVerdict", "chain_head", "seal_entry", "verify_entries"]


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode("utf-8")


def _entry_digest(payload: Any, prev_hash: str) -> str:
    return hashlib.sha256(_canonical(payload) + prev_hash.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class ChainVerdict:
    """Outcome of verifying a chain. ``ok`` is the only field that grants trust."""

    ok: bool
    checked: int
    head_hash: str = GENESIS_HASH
    broken_at_seq: int | None = None
    defect: str | None = None
    defects: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "head_hash": self.head_hash,
            "broken_at_seq": self.broken_at_seq,
            "defect": self.defect,
            "defects": list(self.defects),
        }


def chain_head(entries: list[dict[str, Any]] | None) -> str:
    """The ``entry_hash`` a new link must chain onto."""
    if not entries:
        return GENESIS_HASH
    last = entries[-1]
    return str(last.get("entry_hash") or GENESIS_HASH)


def seal_entry(payload: dict[str, Any], *, seq: int, prev_hash: str) -> dict[str, Any]:
    """Seal one payload into a chain link.

    The digest covers the payload *and* the previous link, so the returned
    ``entry_hash`` is only reproducible given the whole history that preceded it.
    """
    entry = {"seq": int(seq), "prev_hash": prev_hash, "payload": payload}
    entry["entry_hash"] = _entry_digest(entry, prev_hash)
    return entry


def verify_entries(entries: list[dict[str, Any]] | None) -> ChainVerdict:
    """Verify a whole chain, reporting the first defect found.

    Three independent checks, so that no single edit can hide:

    1. each ``entry_hash`` recomputes from its own payload and its ``prev_hash``
    2. each ``prev_hash`` equals the previous entry's ``entry_hash``
    3. sequence numbers are contiguous from 1
    """
    if not entries:
        return ChainVerdict(ok=True, checked=0, head_hash=GENESIS_HASH)

    defects: list[str] = []
    broken_at: int | None = None
    prev_hash = GENESIS_HASH
    expected_seq = 1

    for index, entry in enumerate(entries):
        seq = entry.get("seq")
        if seq != expected_seq:
            defects.append(f"entry {index}: sequence is {seq!r}, expected {expected_seq}")
            if broken_at is None:
                broken_at = expected_seq
        if entry.get("prev_hash") != prev_hash:
            defects.append(f"entry {index} (seq {seq}): prev_hash does not match the previous entry_hash")
            if broken_at is None:
                broken_at = int(seq) if isinstance(seq, int) else expected_seq
        recomputed = _entry_digest({"seq": seq, "prev_hash": entry.get("prev_hash"), "payload": entry.get("payload")}, str(entry.get("prev_hash") or ""))
        if recomputed != entry.get("entry_hash"):
            defects.append(f"entry {index} (seq {seq}): entry_hash does not match its contents")
            if broken_at is None:
                broken_at = int(seq) if isinstance(seq, int) else expected_seq
        prev_hash = str(entry.get("entry_hash") or "")
        expected_seq += 1

    return ChainVerdict(
        ok=not defects,
        checked=len(entries),
        head_hash=prev_hash,
        broken_at_seq=broken_at,
        defect=defects[0] if defects else None,
        defects=tuple(defects),
    )
