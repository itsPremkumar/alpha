"""Bounded adversarial repair loop with explicit dispositions.

After a defense the defender may repair remaining attacks. Each
attack is resolved to exactly one disposition, and the loop is
bounded (`max_repair_cycles`, default 2) so a match can never spin.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Disposition(str, Enum):
    FIXED = "FIXED"        # defender changed the artifact to remove the flaw
    CONCEDED = "CONCEDED"  # defender agrees the flaw is real and did not fix it
    REBUTTED = "REBUTTED"  # defender showed the attack does not apply
    DEFERRED = "DEFERRED"  # bounded loop ran out; recorded honestly


@dataclass(frozen=True)
class RepairEntry:
    attack_index: int
    disposition: Disposition
    note: str = ""
    cycle: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "attack_index": self.attack_index,
            "disposition": self.disposition.value,
            "note": self.note,
            "cycle": self.cycle,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepairEntry:
        return cls(
            attack_index=int(data["attack_index"]),
            disposition=Disposition(str(data["disposition"]).upper()),
            note=str(data.get("note", "")),
            cycle=int(data.get("cycle", 1)),
        )


@dataclass
class RepairRound:
    cycle: int
    entries: list[RepairEntry] = field(default_factory=list)
    revised_path: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "entries": [e.to_dict() for e in self.entries],
            "revised_path": self.revised_path,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepairRound:
        return cls(
            cycle=int(data["cycle"]),
            entries=[RepairEntry.from_dict(e) for e in data.get("entries", [])],
            revised_path=data.get("revised_path", ""),
            created_at=data.get("created_at", time.time()),
        )


def parse_repairs(text: str | None, cycle: int = 1) -> list[RepairEntry]:
    """Parse ``ATTACK n: FIXED|CONCEDED|REBUTTED|DEFERRED. note`` lines."""
    if not text:
        return []
    entries: list[RepairEntry] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.upper().startswith("ATTACK"):
            continue
        try:
            head, _, rest = line.partition(":")
            index = int(head.split()[1])
            verdict_text, _, note = rest.partition(".")
            disposition = Disposition(verdict_text.strip().upper())
        except (IndexError, ValueError):
            continue
        entries.append(RepairEntry(attack_index=index, disposition=disposition, note=note.strip(), cycle=cycle))
    return entries


def unresolved_attacks(
    attack_indexes: list[int],
    entries: list[RepairEntry],
) -> list[int]:
    """Attacks with no disposition yet (or only DEFERRED) in the last cycle."""
    resolved: dict[int, Disposition] = {}
    for entry in entries:
        resolved[entry.attack_index] = entry.disposition
    return [
        idx
        for idx in attack_indexes
        if resolved.get(idx) in (None, Disposition.DEFERRED)
    ]


def fatal_conceded(entries: list[RepairEntry]) -> bool:
    """A FATAL attack the defender conceded decides the match on its own.

    Only CONCEDED counts: FIXED and REBUTTED answer the attack, DEFERRED
    means the loop ran out and is handled by the normal rubric path.
    """
    return any(e.disposition == Disposition.CONCEDED for e in entries)


def is_terminal(entries: list[RepairEntry], attack_indexes: list[int], max_cycles: int, cycle: int) -> bool:
    """The loop stops when every attack has a real disposition or the cap is hit."""
    if cycle >= max_cycles:
        return True
    return not unresolved_attacks(attack_indexes, entries)


__all__ = [
    "Disposition",
    "RepairEntry",
    "RepairRound",
    "parse_repairs",
    "unresolved_attacks",
    "fatal_conceded",
    "is_terminal",
]
