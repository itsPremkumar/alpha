"""Durable scratchpad: a bounded, append-only reasoning workbench.

Why this exists
--------------
A long-horizon agent reasons a great deal that never belongs in the objective,
the plan, or the status log -- dead ends considered, measurements seen mid-step,
the reason the next experiment was chosen. That reasoning is the most fragile
state in a multi-day run: it lives in the model's context, and context is
compacted, checkpointed, and restarted. Writing it to a bounded, append-only
*file* turns it into durable state the run can re-read after a compaction or a
process restart -- the difference between "pick the next experiment again from
scratch" and "continue the thread".

:class:`Scratchpad` is a ring buffer: :meth:`Scratchpad.append` drops the oldest
entry once the count reaches ``max_entries``. It is a value object; append
returns a new pad.

Honesty rules encoded here
--------------------------
* **Newest-last, count preserved.** The pad keeps its entries in chronological
  order and reports ``count`` even after the ring drops old entries, so a
  rendered "12 notes" is honest about how many were kept rather than how many
  were ever written. :attr:`Scratchpad.dropped` records the overflow.
* **Bounded per entry and in total.** Each entry and the rendered output are
  trimmed, so one rambling note cannot grow the durable file without bound.
* Empty input appends nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

__all__ = ["Scratchpad", "DEFAULT_MAX_SCRATCH_ENTRIES"]

#: Default ring depth. Deep enough to hold a working session's reasoning; small
#: enough that the rendered anchor stays bounded.
DEFAULT_MAX_SCRATCH_ENTRIES: Final[int] = 60

_MAX_ENTRY_CHARS: Final[int] = 1200


@dataclass(frozen=True, slots=True)
class Scratchpad:
    """A bounded ring of free-form reasoning notes."""

    entries: tuple[str, ...] = ()
    max_entries: int = DEFAULT_MAX_SCRATCH_ENTRIES
    dropped: int = 0

    def __post_init__(self) -> None:
        if self.max_entries < 1:
            raise ValueError("Scratchpad.max_entries must be >= 1")
        # If a smaller ring is requested (config change, restore), trim to it now
        # so the invariant ``len(entries) <= max_entries`` holds everywhere.
        if len(self.entries) > self.max_entries:
            overflow = len(self.entries) - self.max_entries
            object.__setattr__(self, "entries", self.entries[overflow:])
            object.__setattr__(self, "dropped", self.dropped + overflow)

    def append(self, note: str) -> Scratchpad:
        """Return a new pad with *note* appended.

        A blank note is a no-op. When the ring is full the oldest entry is
        dropped and :attr:`Scratchpad.dropped` is incremented.
        """
        cleaned = " ".join((note or "").split())[:_MAX_ENTRY_CHARS]
        if not cleaned:
            return self
        entries = (*self.entries, cleaned)
        dropped = self.dropped
        if len(entries) > self.max_entries:
            overflow = len(entries) - self.max_entries
            entries = entries[overflow:]
            dropped += overflow
        return replace(self, entries=entries, dropped=dropped)

    @property
    def count(self) -> int:
        return len(self.entries)

    def tail(self, n: int) -> tuple[str, ...]:
        return self.entries[-n:] if n < len(self.entries) else self.entries

    def to_dict(self) -> dict[str, object]:
        return {
            "entries": list(self.entries),
            "max_entries": self.max_entries,
            "dropped": self.dropped,
        }

    @classmethod
    def from_dict(cls, data: object) -> Scratchpad:
        if not isinstance(data, dict):
            return cls()
        raw = data.get("entries", [])
        entries = tuple(str(e) for e in raw) if isinstance(raw, list) else ()
        max_entries = int(data.get("max_entries", DEFAULT_MAX_SCRATCH_ENTRIES) or DEFAULT_MAX_SCRATCH_ENTRIES)
        dropped = int(data.get("dropped", 0) or 0)
        return cls(entries=entries, max_entries=max_entries, dropped=dropped)
