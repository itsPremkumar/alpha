"""Models for Episodic Experience Memory and Reflection.

Experience kinds (TTSE-style bank + legacy episodic lessons):
- ``EPISODE`` — legacy task-trajectory record (lessons/pitfalls); the default so
  every pre-existing serialized record keeps loading unchanged.
- ``FACT`` — stable information learned from execution or confirmed sources
  (e.g. "this repo uses pnpm").
- ``TIP`` — reusable action strategy (e.g. "run X before Y").

Honesty rules baked into the model:
- ``confidence`` starts at the disclosed neutral baseline (0.5). It is only ever
  moved by arithmetic over real reuse evidence (``success_count`` /
  ``failure_count``), never invented — see ``hygiene.py``.
- ``evidence`` holds references (trace ids / observed outcomes) backing the
  claim; the store refuses FACT/TIP records without it.
- ``audit`` records every maintenance mutation performed on the record so no
  rewrite is silent.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

# Disclosed neutral baseline: every new record starts here. Values above this
# are only legitimate when computed from real reuse evidence; values below it
# come from observed failures (hygiene down-rank).
NEUTRAL_CONFIDENCE = 0.5


class OutcomeType(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


class ExperienceKind(StrEnum):
    """Stored experience kinds.

    ``EPISODE`` is the backward-compatible default for old records that were
    serialized without a ``kind`` field.
    """

    EPISODE = "EPISODE"
    FACT = "FACT"
    TIP = "TIP"


@dataclass
class ExperienceRecord:
    """Episodic memory record capturing lessons, pitfalls, and solutions from past trajectories.

    Also carries the TTSE-style FACT/TIP bank fields. Every new field is
    optional with an honest default so old serialized JSON keeps loading.
    """

    task_goal: str
    outcome: OutcomeType
    lessons_learned: list[str] = field(default_factory=list)
    pitfalls_to_avoid: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    error_types: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    experience_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    # --- FACT/TIP bank fields (added later; optional, backward compatible) ---
    kind: ExperienceKind = ExperienceKind.EPISODE
    statement: str = ""
    confidence: float = NEUTRAL_CONFIDENCE
    evidence: list[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    reuse_count: int = 0
    expires_at: float | None = None
    last_reviewed: float | None = None
    hygiene_note: str = ""
    audit: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Coerce plain-string enum inputs and validate honesty-critical fields."""
        if not isinstance(self.kind, ExperienceKind):
            self.kind = ExperienceKind(str(self.kind).strip().upper())
        if not isinstance(self.outcome, OutcomeType):
            self.outcome = OutcomeType(str(self.outcome).strip().lower())
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence must be within [0.0, 1.0], got {self.confidence!r}")
        self.confidence = confidence
        for count_field in ("success_count", "failure_count", "reuse_count"):
            value = int(getattr(self, count_field))
            if value < 0:
                raise ValueError(f"{count_field} must be >= 0, got {value!r}")
            setattr(self, count_field, value)

    def is_expired(self, now: float | None = None) -> bool:
        """True when ``expires_at`` is set and has already passed."""
        if self.expires_at is None:
            return False
        return self.expires_at <= (now if now is not None else time.time())

    def append_audit(
        self,
        action: str,
        detail: dict[str, Any] | None = None,
        ts: float | None = None,
    ) -> dict[str, Any]:
        """Append an audit entry so maintenance mutations on this record are never silent."""
        entry: dict[str, Any] = {
            "ts": ts if ts is not None else time.time(),
            "action": action,
            "detail": dict(detail or {}),
        }
        self.audit.append(entry)
        return entry

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["outcome"] = self.outcome.value
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperienceRecord:
        payload = dict(data)
        if "outcome" in payload and isinstance(payload["outcome"], str):
            payload["outcome"] = OutcomeType(payload["outcome"])
        if "kind" in payload and isinstance(payload["kind"], str):
            payload["kind"] = ExperienceKind(payload["kind"].strip().upper())
        return cls(**payload)
