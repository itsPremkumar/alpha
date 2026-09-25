"""Append-only provenance: "why is this the current default?" must be answerable.

Every proposal, measurement, comparison and verdict is written to one
append-only JSONL chain. The chain is hash-linked: each entry records the
digest of the previous entry, so removing or reordering a line breaks the chain
and is detected on replay. A ``head.json`` sidecar records the latest
``(seq, entry_hash)`` atomically after each append, which is what makes a
*truncated tail* detectable too - something a bare hash chain cannot do on its
own.

Honesty contract:

* **append-only.** Entries are never rewritten; a second write to the same
  ``(chain_id, seq)`` is impossible because ``seq`` is derived from the last
  entry on disk.
* **tamper-evident, not tamper-proof.** The digests live next to the data, so
  an attacker with write access to the whole directory could recompute the
  chain. The log is an audit record for reviewers, not a signing key. The
  residual risk is stated in the package report.
* **persistence is disclosed, never assumed.** An append that fails does not
  raise and is never reported as success: ``persistence`` flips to
  ``"degraded"`` with a logged warning carrying the real error, and the caller
  sees that state (the same precedent as ``alpha.rsi.lineage``).
* **replay never repairs.** Corrupt lines are reported with their line number
  and skipped; they are never rewritten or interpreted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.evolution.evidence.models import Verdict

logger = logging.getLogger(__name__)

__all__ = [
    "ENTRY_KINDS",
    "HEAD_FILE_NAME",
    "LEDGER_NAME",
    "ProvenanceEntry",
    "ProvenanceLog",
    "ReplayIssue",
    "ReplayReport",
    "replay_chain",
]

#: The closed set of things the chain records.
ENTRY_KINDS: tuple[str, ...] = ("proposal", "measurement", "comparison", "verdict")
LEDGER_NAME = "evidence.jsonl"
HEAD_FILE_NAME = "head.json"
_GENESIS_HASH = "0" * 64
_HEAD_VERSION = 1


def _suppressed_unlink(path: Path) -> None:
    """Best-effort removal of a staging file (never raises)."""

    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - defensive
        pass


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProvenanceEntry:
    """One link in the chain."""

    seq: int
    kind: str
    subject: str
    recorded_at: float
    prev_hash: str
    entry_hash: str
    payload: Mapping[str, Any]

    @property
    def hashed_payload(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "subject": self.subject,
            "recorded_at": self.recorded_at,
            "prev_hash": self.prev_hash,
            "payload": dict(self.payload),
        }

    def recompute_hash(self) -> str:
        return _digest(self.hashed_payload)

    def to_dict(self) -> dict[str, Any]:
        return {**self.hashed_payload, "entry_hash": self.entry_hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProvenanceEntry:
        if not isinstance(data, Mapping):
            raise TypeError(f"provenance entry must be a mapping, got {type(data).__name__}")
        missing = [key for key in ("seq", "kind", "subject", "recorded_at", "prev_hash", "entry_hash", "payload") if key not in data]
        if missing:
            raise ValueError(f"provenance entry is missing key(s) {missing}")
        return cls(
            seq=int(data["seq"]),
            kind=str(data["kind"]),
            subject=str(data["subject"]),
            recorded_at=float(data["recorded_at"]),
            prev_hash=str(data["prev_hash"]),
            entry_hash=str(data["entry_hash"]),
            payload=dict(data["payload"]),
        )


@dataclass(frozen=True)
class ReplayIssue:
    """One detected provenance defect (missing, reordered, corrupt, tampered)."""

    code: str
    detail: str
    seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "seq": self.seq}


@dataclass(frozen=True)
class ReplayReport:
    """The result of replaying one chain."""

    entries: tuple[ProvenanceEntry, ...]
    issues: tuple[ReplayIssue, ...]
    current_default: Verdict | None = None
    default_proposal_id: str | None = None
    skipped_lines: int = 0

    @property
    def intact(self) -> bool:
        return not self.issues

    def entries_for(self, proposal_id: str) -> tuple[ProvenanceEntry, ...]:
        return tuple(entry for entry in self.entries if entry.subject == str(proposal_id))

    def explain(self, proposal_id: str | None = None) -> dict[str, Any]:
        """Answer "why is this the current default?" with the recorded chain."""

        target = str(proposal_id) if proposal_id is not None else (self.default_proposal_id or "")
        entries = self.entries_for(target)
        verdicts = [entry for entry in entries if entry.kind == "verdict"]
        return {
            "proposal_id": target or None,
            "is_current_default": bool(target) and target == (self.default_proposal_id or ""),
            "chain_intact": self.intact,
            "entries": [entry.to_dict() for entry in entries],
            "verdict": dict(verdicts[-1].payload) if verdicts else None,
            "verdict_entry": verdicts[-1].to_dict() if verdicts else None,
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "intact": self.intact,
            "skipped_lines": self.skipped_lines,
            "entries": [entry.to_dict() for entry in self.entries],
            "issues": [issue.to_dict() for issue in self.issues],
            "current_default": self.current_default.to_dict() if self.current_default is not None else None,
            "default_proposal_id": self.default_proposal_id,
        }


class ProvenanceLog:
    """Hash-linked append-only JSONL log for one chain id.

    ``root`` is a directory; the log file is ``<root>/<chain_id>/evidence.jsonl``
    and the head sidecar ``<root>/<chain_id>/head.json``. The clock is injected
    (tests pass a constant); ``append`` is the only write path.
    """

    def __init__(self, root: str | Path, *, chain_id: str = "evolution-evidence", clock: Any = None) -> None:
        self.root = Path(root)
        self.chain_id = str(chain_id).strip()
        if not self.chain_id:
            raise ValueError("provenance chain_id must be a non-empty string")
        if "/" in self.chain_id or "\\" in self.chain_id or ".." in self.chain_id:
            raise ValueError(f"provenance chain_id must be a single safe path component, got {chain_id!r}")
        self._clock = clock if clock is not None else time.time
        self._persistence = "ok"

    @property
    def directory(self) -> Path:
        return self.root / self.chain_id

    @property
    def path(self) -> Path:
        return self.directory / LEDGER_NAME

    @property
    def head_path(self) -> Path:
        return self.directory / HEAD_FILE_NAME

    @property
    def persistence(self) -> str:
        """``"ok"`` or sticky ``"degraded"`` after a failed write."""

        return self._persistence

    def _now(self) -> float:
        return float(self._clock())

    def last_entry(self) -> ProvenanceEntry | None:
        """The last valid entry on disk, or ``None`` for an empty/corrupt tail."""

        lines = self._read_lines()
        for raw in reversed(lines):
            if not raw.strip():
                continue
            try:
                return ProvenanceEntry.from_dict(json.loads(raw))
            except (TypeError, ValueError):
                continue
        return None

    def _read_lines(self) -> list[str]:
        try:
            return self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except OSError as exc:
            logger.warning("Evolution evidence provenance %s is unreadable: %s", self.path, exc)
            return []

    def append(self, kind: str, subject: str, payload: Mapping[str, Any]) -> ProvenanceEntry:
        """Append one entry. Never raises; a failed write is disclosed instead.

        The returned entry is the entry that *would* be recorded. Check
        ``persistence`` (or the ``appended`` flag in the returned mapping) to
        know whether it reached the disk.
        """

        if kind not in ENTRY_KINDS:
            raise ValueError(f"provenance kind must be one of {list(ENTRY_KINDS)}, got {kind!r}")
        if not str(subject).strip():
            raise ValueError("provenance subject must be a non-empty string (usually the proposal id)")
        if not isinstance(payload, Mapping):
            raise TypeError(f"provenance payload must be a mapping, got {type(payload).__name__}")
        previous = self.last_entry()
        seq = (previous.seq + 1) if previous is not None else 1
        prev_hash = previous.entry_hash if previous is not None else _GENESIS_HASH
        entry = ProvenanceEntry(
            seq=seq,
            kind=kind,
            subject=str(subject),
            recorded_at=self._now(),
            prev_hash=prev_hash,
            entry_hash="",
            payload=dict(payload),
        )
        entry = ProvenanceEntry(
            seq=entry.seq,
            kind=entry.kind,
            subject=entry.subject,
            recorded_at=entry.recorded_at,
            prev_hash=entry.prev_hash,
            entry_hash=entry.recompute_hash(),
            payload=entry.payload,
        )
        line = _canonical(entry.to_dict())
        self._write_line(line, entry)
        return entry

    def _write_line(self, line: str, entry: ProvenanceEntry) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, TypeError, ValueError) as exc:
            self._persistence = "degraded"
            logger.warning("Could not append evolution evidence provenance entry %s to %s: %s", entry.seq, self.path, exc)
            return
        self._write_head(entry)

    def _write_head(self, entry: ProvenanceEntry) -> None:
        payload = {"version": _HEAD_VERSION, "chain_id": self.chain_id, "seq": entry.seq, "entry_hash": entry.entry_hash}
        tmp_path = self.head_path.with_name(f"{self.head_path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp_path.write_text(_canonical(payload) + "\n", encoding="utf-8")
            os.replace(tmp_path, self.head_path)
        except (OSError, TypeError, ValueError) as exc:
            self._persistence = "degraded"
            _suppressed_unlink(tmp_path)
            logger.warning("Could not update evolution evidence provenance head at %s: %s (tail truncation will not be detectable)", self.head_path, exc)

    def read_head(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.head_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return dict(payload) if isinstance(payload, dict) else None

    def replay(self) -> ReplayReport:
        return replay_chain(self.directory, chain_id=self.chain_id)

    def explain(self, proposal_id: str | None = None) -> dict[str, Any]:
        """Replay and explain the accepted chain for ``proposal_id``."""

        return self.replay().explain(proposal_id)


def replay_chain(directory: str | Path, *, chain_id: str | None = None) -> ReplayReport:
    """Replay one chain, verifying sequence, linkage and digests.

    Detects and reports:

    * ``sequence_gap`` - a missing entry (or a reordered file) in the middle;
    * ``broken_link`` - an entry whose ``prev_hash`` does not match the
      previous entry's digest (reordering or a rewritten line);
    * ``entry_hash_mismatch`` - a line whose digest does not match its content;
    * ``corrupt_line`` - a line that is not a usable entry (reported, skipped,
      never repaired);
    * ``head_mismatch``/``truncated_tail`` - the sidecar head does not match the
      last entry on disk, which is how a truncated tail is caught.

    The current default is the last ``verdict`` entry whose recorded status is
    ``accepted``; a later non-accepted verdict for the same proposal does not
    silently erase that record.
    """

    base = Path(directory)
    issues: list[ReplayIssue] = []
    entries: list[ProvenanceEntry] = []
    skipped = 0
    path = base / LEDGER_NAME
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raw_lines = []
    except OSError as exc:
        return ReplayReport(entries=(), issues=(ReplayIssue(code="unreadable_ledger", detail=f"{path} is unreadable: {exc}"),), skipped_lines=0)

    previous: ProvenanceEntry | None = None
    for index, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            continue
        try:
            entry = ProvenanceEntry.from_dict(json.loads(raw))
        except (TypeError, ValueError) as exc:
            skipped += 1
            issues.append(ReplayIssue(code="corrupt_line", detail=f"line {index} of {path} is not a usable provenance entry: {type(exc).__name__}: {exc}", seq=None))
            continue
        if entry.kind not in ENTRY_KINDS:
            skipped += 1
            issues.append(ReplayIssue(code="unknown_kind", detail=f"line {index} of {path} records unknown kind {entry.kind!r}", seq=entry.seq))
            continue
        if entry.recompute_hash() != entry.entry_hash:
            skipped += 1
            issues.append(ReplayIssue(code="entry_hash_mismatch", detail=f"line {index} of {path} (seq {entry.seq}) does not match its own digest: recorded {entry.entry_hash}, recomputed {entry.recompute_hash()}", seq=entry.seq))
            continue
        expected_seq = (previous.seq + 1) if previous is not None else 1
        if entry.seq != expected_seq:
            issues.append(ReplayIssue(code="sequence_gap", detail=f"line {index} of {path} has seq {entry.seq} but {expected_seq} was expected: an entry is missing or the chain was reordered", seq=entry.seq))
        expected_prev = previous.entry_hash if previous is not None else _GENESIS_HASH
        if entry.prev_hash != expected_prev:
            issues.append(
                ReplayIssue(
                    code="broken_link",
                    detail=(f"line {index} of {path} (seq {entry.seq}) links to {entry.prev_hash[:12]}... but the previous entry hashes to {expected_prev[:12]}...: the chain was reordered or rewritten"),
                    seq=entry.seq,
                )
            )
        entries.append(entry)
        previous = entry

    head_path = base / HEAD_FILE_NAME
    try:
        head = json.loads(head_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        head = None
    except (OSError, ValueError) as exc:
        head = None
        issues.append(ReplayIssue(code="unreadable_head", detail=f"{head_path} is unreadable: {exc}"))
    if isinstance(head, Mapping):
        recorded_hash = str(head.get("entry_hash", ""))
        if entries and recorded_hash != entries[-1].entry_hash:
            issues.append(
                ReplayIssue(
                    code="truncated_tail",
                    detail=(f"{head_path} records seq {head.get('seq')!r} hash {recorded_hash[:12]}... but the last line on disk is seq {entries[-1].seq} hash {entries[-1].entry_hash[:12]}...: entries were removed from the tail"),
                    seq=int(head.get("seq", 0) or 0),
                )
            )
    elif chain_id is not None and entries:
        issues.append(ReplayIssue(code="missing_head", detail=f"{head_path} does not exist although {len(entries)} entr(ies) were recorded; tail truncation cannot be detected", seq=entries[-1].seq))

    current_default: Verdict | None = None
    default_proposal_id: str | None = None
    for entry in entries:
        if entry.kind != "verdict":
            continue
        try:
            verdict = Verdict.from_dict(entry.payload)
        except (TypeError, ValueError):
            continue
        if verdict.accepted:
            current_default = verdict
            default_proposal_id = verdict.proposal_id
    return ReplayReport(
        entries=tuple(entries),
        issues=tuple(issues),
        current_default=current_default,
        default_proposal_id=default_proposal_id,
        skipped_lines=skipped,
    )
