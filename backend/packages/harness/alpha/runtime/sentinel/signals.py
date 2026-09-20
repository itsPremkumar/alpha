"""Signal model for the Sentinel autonomous repair loop.

A ``Signal`` is one observed fault, normalised from whatever source produced it
(watchdog anomaly, log line, test failure, dead process). Everything downstream
— diagnosis, repair, verification, commit — works in terms of Signals, so new
sources can be added without touching the rest of the loop.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Ordered worst-first. Anything unrecognised is treated as "high": an unknown
# fault should not be quietly deprioritised.
SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low")
_SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
DEFAULT_SEVERITY = "high"

# Recognised sources. Not enforced as an enum so new sources need no schema
# change, but these are what the built-in collectors emit.
SOURCES: tuple[str, ...] = ("watchdog", "logs", "tests", "process")


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class Signal:
    """One observed fault.

    ``fingerprint`` is the dedup key: the same underlying fault seen repeatedly
    must produce the same fingerprint, otherwise the loop will try to fix it
    forever. It is derived from (source, kind, normalised message) — never from
    timestamps or absolute paths.
    """

    source: str
    kind: str
    message: str
    severity: str = DEFAULT_SEVERITY
    context: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    detected_at: float = field(default_factory=_now)

    def __post_init__(self) -> None:
        # Normalise severity without mutating (frozen dataclass).
        sev = (self.severity or "").strip().lower()
        if sev not in _SEVERITY_RANK:
            sev = DEFAULT_SEVERITY
        object.__setattr__(self, "severity", sev)

        if not self.fingerprint:
            object.__setattr__(self, "fingerprint", self.compute_fingerprint())

    def compute_fingerprint(self) -> str:
        return compute_fingerprint(self.source, self.kind, self.message)

    @property
    def rank(self) -> int:
        """Lower is more severe."""
        return _SEVERITY_RANK.get(self.severity, _SEVERITY_RANK[DEFAULT_SEVERITY])

    def iso_time(self) -> str:
        return datetime.fromtimestamp(self.detected_at, UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "context": dict(self.context),
            "fingerprint": self.fingerprint,
            "detected_at": self.iso_time(),
        }


# ── Normalisation / fingerprinting ─────────────────────────────────────────

# Things that vary between two occurrences of the *same* fault and must not
# split the fingerprint: numbers, hex ids, timestamps, uuids.
#
# Deliberately NOT normalised: quoted strings. Replacing 'foo'/'bar' with <str>
# would collapse "No module named 'foo'" and "No module named 'bar'" into one
# fingerprint — two genuinely different faults — and the second would then never
# be repaired because the tracker thinks it has already been tried. The
# discriminating detail is usually exactly what's inside the quotes.
_NOISE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hash>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?\b"), "<timestamp>"),
    (re.compile(r"\b\d+(?:\.\d+)*\b"), "<n>"),
)


def normalize_message(message: str) -> str:
    """Strip run-specific noise so identical faults collapse to one key."""
    text = (message or "").strip()
    for pattern, replacement in _NOISE_PATTERNS:
        text = pattern.sub(replacement, text)
    # Collapse whitespace last, after substitutions may have changed lengths.
    return re.sub(r"\s+", " ", text).strip()


def compute_fingerprint(source: str, kind: str, message: str) -> str:
    """Stable dedup key for (source, kind, normalised message)."""
    raw = f"{source.strip().lower()}|{kind.strip().lower()}|{normalize_message(message)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


# ── Attempt tracking ───────────────────────────────────────────────────────

@dataclass
class AttemptRecord:
    fingerprint: str
    attempts: int = 0
    last_attempt_at: float = 0.0
    last_outcome: str | None = None  # "fixed" | "failed" | "escalated"


class SignalTracker:
    """Tries to stop the loop hammering one unfixable fault forever.

    Two independent brakes:
    - ``max_attempts``: give up on a fingerprint after N tries.
    - ``cooldown_seconds``: wait between tries so a flapping fault (or a fix
      that takes time to take effect) is not retried in a tight loop.

    Both default to values that permit normal repair work; the point is a
    *ceiling*, not to make the system timid.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        cooldown_seconds: float = 60.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        self.max_attempts = max_attempts
        self.cooldown_seconds = cooldown_seconds
        self._records: dict[str, AttemptRecord] = {}

    def record(self, fingerprint: str) -> AttemptRecord:
        return self._records.setdefault(fingerprint, AttemptRecord(fingerprint=fingerprint))

    def get(self, fingerprint: str) -> AttemptRecord | None:
        return self._records.get(fingerprint)

    def should_act(self, signal: Signal, *, now: float | None = None) -> bool:
        """True when this signal is worth acting on right now."""
        rec = self.get(signal.fingerprint)
        if rec is None:
            return True
        if rec.attempts >= self.max_attempts:
            return False
        if rec.last_attempt_at:
            elapsed = (now if now is not None else _now()) - rec.last_attempt_at
            if elapsed < self.cooldown_seconds:
                return False
        return True

    def mark_attempt(self, signal: Signal, *, now: float | None = None) -> AttemptRecord:
        rec = self.record(signal.fingerprint)
        rec.attempts += 1
        rec.last_attempt_at = now if now is not None else _now()
        return rec

    def mark_outcome(self, signal: Signal, outcome: str) -> AttemptRecord:
        rec = self.record(signal.fingerprint)
        rec.last_outcome = outcome
        return rec

    def reset(self, fingerprint: str) -> None:
        """Forget a fingerprint — used after a successful fix."""
        self._records.pop(fingerprint, None)

    def __len__(self) -> int:
        return len(self._records)


def dedupe(signals: list[Signal]) -> list[Signal]:
    """Collapse signals that share a fingerprint, keeping the first."""
    seen: set[str] = set()
    out: list[Signal] = []
    for s in signals:
        if s.fingerprint in seen:
            continue
        seen.add(s.fingerprint)
        out.append(s)
    return out


def sort_by_severity(signals: list[Signal]) -> list[Signal]:
    """Worst first, then earliest seen."""
    return sorted(signals, key=lambda s: (s.rank, s.detected_at))
