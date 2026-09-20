"""Kibitzer Resident Memory Sidecar.

Runs as a resident, lightweight background memory judge:
1. Receives bounded, redacted events (prompt, tool name, arguments, results).
2. Mask secrets and credentials before evaluating relevance.
3. Evaluates relevance against a stored memory bank.
4. Returns non-intrusive 1-line recall hints ("recalled memory: <hint>").
5. Tracks delivered hints to eliminate duplicate or spammy context clutter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|bearer|secret|password|token)\s*[:=]\s*['\"][^'\"]+['\"]"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),
]


def redact_secrets(text: str) -> str:
    """Mask credentials and sensitive tokens from text."""
    redacted = text
    for pat in SECRET_PATTERNS:
        redacted = pat.sub("[REDACTED_SECRET]", redacted)
    return redacted


@dataclass
class MemoryEntry:
    id: str
    topic: str
    keywords: list[str]
    hint: str
    details: str = ""


class KibitzerMemoryBank:
    """Storage of long-term project knowledge and mitigation hints."""

    def __init__(self):
        self.entries: list[MemoryEntry] = []

    def add_entry(self, entry_id: str, topic: str, keywords: list[str], hint: str, details: str = "") -> None:
        self.entries.append(MemoryEntry(
            id=entry_id,
            topic=topic,
            keywords=[k.lower() for k in keywords],
            hint=hint,
            details=details,
        ))

    def search_relevant(self, query_text: str) -> list[MemoryEntry]:
        clean = query_text.lower()
        matches = []
        for e in self.entries:
            if any(k in clean for k in e.keywords):
                matches.append(e)
        return matches


class KibitzerObserver:
    """Sidecar observer that evaluates agent activity and issues nudges."""

    def __init__(self, memory_bank: KibitzerMemoryBank | None = None, max_nudges_per_turn: int = 2):
        self.bank = memory_bank or KibitzerMemoryBank()
        self.max_nudges = max_nudges_per_turn
        self.delivered_ids: set[str] = set()
        self.turn_count: int = 0

    def observe(
        self,
        prompt: str = "",
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
        tool_result: str = "",
    ) -> list[str]:
        """Observe step activity and return any relevant 1-line memory nudges."""
        self.turn_count += 1
        combined = f"{prompt} {tool_name} {str(tool_args or {})} {tool_result}"
        safe_text = redact_secrets(combined)

        matches = self.bank.search_relevant(safe_text)
        nudges: list[str] = []

        for m in matches:
            if m.id not in self.delivered_ids:
                nudges.append(f"recalled memory: {m.hint}")
                self.delivered_ids.add(m.id)
                if len(nudges) >= self.max_nudges:
                    break

        return nudges

    def reset_delivered(self) -> None:
        """Clear delivered cache for new session."""
        self.delivered_ids.clear()
