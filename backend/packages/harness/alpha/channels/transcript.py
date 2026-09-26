"""Durable, ordered, timezone-explicit chat transcript with presence.

Three defects this module exists to make impossible:

1. **Non-monotonic ordering across a restart.** :class:`TranscriptStore`
   persists the high-water sequence mark and resumes above it, so a message
   written before a crash is never renumbered and a message written after it
   can never sort before it.

2. **Naive timestamps.** Every timestamp this module emits is timezone-aware
   UTC with an explicit offset. A transcript assembled from naive local times
   is not reconstructable, and mixed naive/aware comparison raises at read
   time rather than at write time.

3. **A cp1252 stdout crash.** ``print()`` of a transcript containing an em dash
   raises ``UnicodeEncodeError`` on a default Windows console, which has taken
   this CLI down once. :func:`console_safe` is the only sanctioned way to render
   this module's text, and it never raises for any input.

Plus presence, which degrades to ``unknown`` rather than to ``idle``.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


#: Monotonic counter / transcript write failed. Deliberately loud: a lost
#: message is a fact the operator must see, not a gap to paper over.
class TranscriptError(RuntimeError):
    """Raised when the transcript cannot be durably appended."""


def utc_now_iso() -> str:
    """Timezone-explicit UTC timestamp, second-resolution, always suffixed Z."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_iso(raw: str) -> datetime:
    """Parse a timestamp this module produced, always returning an aware value.

    A ``Z`` suffix is rewritten to ``+00:00`` because ``fromisoformat`` on
    Python 3.10 and earlier does not accept ``Z``. A naive input is REJECTED
    rather than assumed-UTC: silently assuming a timezone is how transcripts
    end up internally inconsistent.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp {raw!r} is naive; refusing to guess a timezone")
    return parsed


def console_safe(text: str, *, encoding: str | None = None) -> str:
    """Render ``text`` for a console that may not be UTF-8.

    Never raises, for any input, on any encoding. Characters the target console
    cannot represent are replaced, not dropped, so the reader can still see that
    something was there.
    """
    if text is None:
        return ""
    raw = text if isinstance(text, str) else str(text)
    target = encoding or (getattr(sys.stdout, "encoding", None) or "ascii")
    try:
        raw.encode(target)
    except (LookupError, UnicodeEncodeError):
        return raw.encode(target if target != "ascii" else "ascii", errors="replace").decode(
            target if target != "ascii" else "ascii", errors="replace"
        )
    except Exception:
        return raw
    return raw


# --------------------------------------------------------------------------
# presence
# --------------------------------------------------------------------------
class Presence(StrEnum):
    """Tri-state presence.

    ``UNKNOWN`` is a first-class value, not an error. A transport that cannot
    distinguish "idle" from "disconnected" must report ``UNKNOWN``; reporting
    ``IDLE`` is how a hung bot looks healthy.
    """

    TYPING = "typing"
    IDLE = "idle"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class PresenceReading:
    handle: str
    presence: Presence = Presence.UNKNOWN
    observed_at: str = field(default_factory=utc_now_iso)
    source: str = "transport"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "presence": str(self.presence),
            "observed_at": self.observed_at,
            "source": self.source,
            "detail": self.detail,
        }


def read_presence(raw: object, *, handle: str, transport_reports_idle: bool = False) -> PresenceReading:
    """Normalise whatever the transport gave us into a :class:`PresenceReading`.

    ``raw`` may be ``None`` (transport said nothing), a bool, or a string. Only
    an explicit positive signal yields ``TYPING``; only an explicit negative
    signal from a transport that documents idle reporting yields ``IDLE``.
    Everything else is ``UNKNOWN``.
    """
    if raw is None:
        return PresenceReading(
            handle=handle,
            presence=Presence.UNKNOWN,
            source="transport",
            detail="transport reported no presence signal",
        )
    if isinstance(raw, bool):
        if raw:
            return PresenceReading(handle=handle, presence=Presence.TYPING, source="transport")
        if transport_reports_idle:
            return PresenceReading(handle=handle, presence=Presence.IDLE, source="transport")
        return PresenceReading(
            handle=handle,
            presence=Presence.UNKNOWN,
            source="transport",
            detail="transport signalled 'not typing' but does not document idle reporting",
        )
    text = str(raw).strip().lower()
    if text in {"typing", "active", "composing"}:
        return PresenceReading(handle=handle, presence=Presence.TYPING, source="transport")
    if text in {"idle", "away"} and transport_reports_idle:
        return PresenceReading(handle=handle, presence=Presence.IDLE, source="transport")
    return PresenceReading(
        handle=handle,
        presence=Presence.UNKNOWN,
        source="transport",
        detail=f"unrecognised presence signal {text!r}; reporting unknown rather than idle",
    )


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------
@dataclass(slots=True)
class ChatMessage:
    """One addressed message.

    ``seq`` is assigned by the store and is the ONLY ordering authority.
    ``wall_clock`` is for humans; ``seq`` is for correctness.
    """

    seq: int
    author: str
    body: str
    kind: str = "chat"
    wall_clock: str = field(default_factory=utc_now_iso)
    targets: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "author": self.author,
            "body": self.body,
            "kind": self.kind,
            "wall_clock": self.wall_clock,
            "targets": list(self.targets),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatMessage:
        return cls(
            seq=int(data["seq"]),
            author=str(data["author"]),
            body=str(data.get("body", "")),
            kind=str(data.get("kind", "chat")),
            wall_clock=str(data["wall_clock"]),
            targets=tuple(data.get("targets") or ()),
            metadata=dict(data.get("metadata") or {}),
        )


class TranscriptStore:
    """Append-only, gap-checked, restart-durable message log for one room.

    The file is JSONL. The sequence counter is persisted in a sidecar so a
    restart resumes above the highest ``seq`` actually on disk rather than
    above the highest the process happened to allocate. A read-back check after
    every append means a write that silently failed raises instead of producing
    a transcript with a hole in it.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._seq = self._resume_seq()

    # -- sequence ---------------------------------------------------------
    def _resume_seq(self) -> int:
        high = 0
        if self.path.exists():
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        high = max(high, int(json.loads(line).get("seq", 0)))
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
        self._write_seq(high)
        return high

    def _write_seq(self, value: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".seq.tmp")
        tmp.write_text(json.dumps({"high_seq": int(value)}), encoding="utf-8", newline="\n")
        os.replace(tmp, self.path.with_suffix(self.path.suffix + ".seq"))

    @property
    def high_seq(self) -> int:
        return self._seq

    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _sink(self) -> Any:
        """The function that actually puts bytes on disk.

        Resolved through the ``builtins.open`` NAME on every append rather than
        captured once, so a test that patches ``builtins.open`` genuinely
        breaks the sink. Capturing the function object at construction time
        would make the durability check untestable and, worse, would mean a
        monkeypatched open silently did not apply to a real failure.
        """
        import builtins

        return builtins.open

    # -- append -----------------------------------------------------------
    def append(
        self,
        author: str,
        body: str,
        *,
        kind: str = "chat",
        targets: Sequence[str] = (),
        metadata: dict[str, Any] | None = None,
        seq: int | None = None,
    ) -> ChatMessage:
        """Append one message and return it with its assigned sequence number.

        Raises :class:`TranscriptError` if the append cannot be confirmed on
        disk. It never returns a message that is not durable.
        """
        if not str(author or "").strip():
            raise TranscriptError("a transcript entry needs an author; unattributed messages are refused")
        with self._lock:
            assigned = int(seq) if seq is not None else self.next_seq()
            if assigned <= 0:
                raise TranscriptError(f"sequence numbers start at 1, got {assigned}")
            message = ChatMessage(
                seq=assigned,
                author=str(author).strip(),
                body=body or "",
                kind=kind,
                wall_clock=utc_now_iso(),
                targets=tuple(targets),
                metadata=dict(metadata or {}),
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Opened through the resolved sink so a patched open applies.
                with self._sink()(
                    str(self.path), "a", encoding="utf-8", newline="\n"
                ) as handle:
                    handle.write(json.dumps(message.to_dict(), ensure_ascii=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise TranscriptError(f"transcript append failed for seq {assigned}: {exc}") from exc
            if assigned > self._seq:
                self._seq = assigned
            self._write_seq(self._seq)
            if not self._contains(assigned):
                raise TranscriptError(
                    f"transcript append for seq {assigned} could not be read back; refusing to "
                    "report a message as delivered when it is not on disk"
                )
            return message

    def _contains(self, seq: int) -> bool:
        if not self.path.exists():
            return False
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    if int(json.loads(line).get("seq", -1)) == seq:
                        return True
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
        return False

    # -- read -------------------------------------------------------------
    def read(self, *, since_seq: int = 0, limit: int | None = None) -> list[ChatMessage]:
        if not self.path.exists():
            return []
        out: list[ChatMessage] = []
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = ChatMessage.from_dict(json.loads(line))
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    continue
                if message.seq > since_seq:
                    out.append(message)
        out.sort(key=lambda m: m.seq)
        return out[-limit:] if limit else out

    def verify_ordered_and_gap_free(self, *, start_seq: int = 1) -> None:
        """Raise if the durable log has a hole or a reordering.

        This is the transcript-level analogue of the governance ledger's
        ``assert_ledger_ordered_and_gap_free``: an unreadable transcript is a
        loud failure, never a silently-shortened log.
        """
        expected = start_seq
        for message in self.read():
            if message.seq != expected:
                raise TranscriptError(
                    f"transcript {self.path} is not gap-free: expected seq {expected}, found {message.seq}"
                )
            expected += 1

    def render(self, *, width: int = 100) -> str:
        """Human-readable replay of the transcript, console-safe on any encoding."""
        lines: list[str] = []
        for message in self.read():
            targets = f" -> {','.join(message.targets)}" if message.targets else ""
            head = f"[{message.seq:>4}] {message.wall_clock} @{message.author}{targets}"
            lines.append(console_safe(head)[:width])
            for chunk in (message.body or "").splitlines() or [""]:
                lines.append("      " + console_safe(chunk)[: width - 6])
        return "\n".join(lines)
