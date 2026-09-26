"""Typed records for Alpha's social and audience-scoped shared memory.

The package fills the social/shared row (row 18) in ``docs/MEMORY_TYPES.md``.
Records are deliberately transport-neutral: the file store validates them on
write and load, while all cross-scope authorization lives in ``sharing.py``.
No model in this module treats membership in a team or an audience label as
permission to read another scope.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CounterpartKind = Literal["user", "agent", "bot", "group"]
FactSensitivity = Literal["public", "team", "private"]
InteractionStatus = Literal["completed", "partial", "failed", "cancelled", "unknown"]

INTERACTION_STATUSES: frozenset[str] = frozenset({"completed", "partial", "failed", "cancelled", "unknown"})


def normalize_scope(value: str) -> str:
    """Return one non-empty, case-preserving audience scope.

    Scope identity is exact and case-sensitive. Path components are sanitized
    separately; folding case here could merge two distinct user identities.
    """

    scope = str(value or "").strip()
    if not scope:
        raise ValueError("scope must be a non-empty string")
    return scope


def _record_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _clean_strings(values: list[str] | tuple[str, ...], *, limit: int = 64) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text[:240])
        if len(cleaned) >= limit:
            break
    return cleaned


class _ClampedRecord(BaseModel):
    """Base record that retains a disclosure whenever a numeric value is clamped."""

    model_config = ConfigDict(extra="forbid")

    clamping_disclosures: list[str] = Field(default_factory=list)

    def _clamp_float(self, name: str, low: float, high: float, *, default: float) -> None:
        original = getattr(self, name)
        value = float(original)
        if math.isnan(value):
            value = default
        clamped = min(high, max(low, value))
        setattr(self, name, clamped)
        if clamped != original:
            self.clamping_disclosures.append(f"{name} clamped from {original!r} to {clamped!r} (allowed {low}..{high})")

    def _clamp_non_negative_int(self, name: str) -> None:
        original = getattr(self, name)
        clamped = max(0, int(original))
        setattr(self, name, clamped)
        if clamped != original:
            self.clamping_disclosures.append(f"{name} clamped from {original!r} to {clamped!r} (minimum 0)")


class Counterpart(_ClampedRecord):
    """One person, agent, bot, or group known inside one owner scope."""

    id: str = Field(default_factory=lambda: _record_id("cp"))
    kind: CounterpartKind = "user"
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    first_seen: float = Field(default_factory=time.time)
    last_seen: float = Field(default_factory=time.time)
    interaction_count: int = 0
    traits: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_and_clamp(self) -> Counterpart:
        self.display_name = self.display_name.strip()
        if not self.display_name:
            raise ValueError("Counterpart.display_name must be non-empty")
        self.id = self.id.strip()
        if not self.id:
            raise ValueError("Counterpart.id must be non-empty")
        self.aliases = _clean_strings(self.aliases)
        self._clamp_non_negative_int("interaction_count")
        self._clamp_float("first_seen", 0.0, float("inf"), default=0.0)
        self._clamp_float("last_seen", 0.0, float("inf"), default=0.0)
        if self.last_seen < self.first_seen:
            original = self.last_seen
            self.last_seen = self.first_seen
            self.clamping_disclosures.append(f"last_seen raised from {original!r} to first_seen {self.first_seen!r}")
        return self

    def identity_keys(self) -> set[str]:
        """Case-folded identifiers used for local counterpart resolution."""

        values = {self.id, self.display_name, *self.aliases}
        return {value.strip().casefold() for value in values if value.strip()}


class Relationship(_ClampedRecord):
    """Relationship state owned by exactly one scope."""

    owner_scope: str
    counterpart_id: str
    trust: float = 0.5
    formality: float = 0.5
    shared_topic_count: int = 0
    shared_topics: list[str] = Field(default_factory=list)
    last_interaction_at: float = Field(default_factory=time.time)
    decay_half_life_days: float = 30.0
    notes: str = ""

    @model_validator(mode="after")
    def _normalize_and_clamp(self) -> Relationship:
        self.owner_scope = normalize_scope(self.owner_scope)
        self.counterpart_id = self.counterpart_id.strip()
        if not self.counterpart_id:
            raise ValueError("Relationship.counterpart_id must be non-empty")
        self.shared_topics = _clean_strings(self.shared_topics, limit=128)
        topic_count = len(self.shared_topics)
        if self.shared_topic_count != topic_count:
            original_count = self.shared_topic_count
            self.shared_topic_count = topic_count
            self.clamping_disclosures.append(f"shared_topic_count adjusted from {original_count!r} to retained topic count {topic_count!r}")
        self._clamp_float("trust", 0.0, 1.0, default=0.0)
        self._clamp_float("formality", 0.0, 1.0, default=0.0)
        self._clamp_non_negative_int("shared_topic_count")
        self._clamp_float("decay_half_life_days", 0.01, 3650.0, default=30.0)
        self._clamp_float("last_interaction_at", 0.0, float("inf"), default=0.0)
        return self


class SharedFact(_ClampedRecord):
    """Knowledge owned by one scope and exposed only through the sharing policy."""

    id: str = Field(default_factory=lambda: _record_id("fact"))
    owner_scope: str
    content: str
    audience: list[str] = Field(default_factory=list)
    sensitivity: FactSensitivity = "private"
    created_by: str
    created_at: float = Field(default_factory=time.time)
    expires_at: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize(self) -> SharedFact:
        self.id = self.id.strip()
        self.owner_scope = normalize_scope(self.owner_scope)
        self.created_by = normalize_scope(self.created_by)
        self.content = self.content.strip()
        if not self.id:
            raise ValueError("SharedFact.id must be non-empty")
        if not self.content:
            raise ValueError("SharedFact.content must be non-empty")
        audiences: list[str] = []
        for value in self.audience:
            scope = normalize_scope(value)
            if scope not in audiences:
                audiences.append(scope)
        self.audience = audiences
        self._clamp_float("created_at", 0.0, float("inf"), default=0.0)
        if self.expires_at is not None:
            self._clamp_float("expires_at", 0.0, float("inf"), default=0.0)
        return self

    def is_expired(self, now: float | None = None) -> bool:
        at = time.time() if now is None else float(now)
        return self.expires_at is not None and self.expires_at <= at


class AudienceGrant(_ClampedRecord):
    """Explicit authority for one grantee to read one fact or one owner scope.

    ``fact_id=None`` is a scope grant. Private facts reject scope grants, so a
    private disclosure must always name the exact fact.
    """

    id: str = Field(default_factory=lambda: _record_id("grant"))
    owner_scope: str
    fact_id: str | None = None
    grantee_scope: str
    granted_by: str
    granted_at: float = Field(default_factory=time.time)
    expires_at: float | None = None
    revoked_at: float | None = None
    expired_at: float | None = None
    reason: str = "explicit audience grant"

    @model_validator(mode="after")
    def _normalize(self) -> AudienceGrant:
        self.id = self.id.strip()
        self.owner_scope = normalize_scope(self.owner_scope)
        self.grantee_scope = normalize_scope(self.grantee_scope)
        self.granted_by = normalize_scope(self.granted_by)
        if not self.id:
            raise ValueError("AudienceGrant.id must be non-empty")
        if self.fact_id is not None:
            self.fact_id = self.fact_id.strip()
            if not self.fact_id:
                raise ValueError("AudienceGrant.fact_id cannot be blank when supplied")
        self.reason = self.reason.strip() or "explicit audience grant"
        self._clamp_float("granted_at", 0.0, float("inf"), default=0.0)
        for name in ("expires_at", "revoked_at", "expired_at"):
            if getattr(self, name) is not None:
                self._clamp_float(name, 0.0, float("inf"), default=0.0)
        return self

    @property
    def target(self) -> str:
        return f"fact:{self.fact_id}" if self.fact_id else f"scope:{self.owner_scope}"

    def applies_to(self, fact_id: str) -> bool:
        return self.fact_id is None or self.fact_id == fact_id

    def is_active(self, now: float | None = None) -> bool:
        at = time.time() if now is None else float(now)
        if self.revoked_at is not None and self.revoked_at <= at:
            return False
        if self.expired_at is not None and self.expired_at <= at:
            return False
        return self.expires_at is None or self.expires_at > at


class InteractionSummary(_ClampedRecord):
    """One bounded interaction summary and its honest generation provenance."""

    id: str = Field(default_factory=lambda: _record_id("interaction"))
    counterpart_id: str
    summary: str
    outcomes: list[str] = Field(default_factory=list)
    status: InteractionStatus = "unknown"
    source: Literal["model", "deterministic"] = "deterministic"
    topics: list[str] = Field(default_factory=list)
    interaction_count: int = 1
    created_at: float = Field(default_factory=time.time)
    fallback_reason: str = ""
    model_name: str | None = None

    @model_validator(mode="after")
    def _normalize_and_clamp(self) -> InteractionSummary:
        self.id = self.id.strip()
        self.counterpart_id = self.counterpart_id.strip()
        if not self.id:
            raise ValueError("InteractionSummary.id must be non-empty")
        if not self.counterpart_id:
            raise ValueError("InteractionSummary.counterpart_id must be non-empty")
        self.summary = self.summary.strip()
        if not self.summary:
            raise ValueError("InteractionSummary.summary must be non-empty")
        self.outcomes = _clean_strings(self.outcomes, limit=32)
        self.topics = _clean_strings(self.topics, limit=32)
        self.fallback_reason = self.fallback_reason.strip()
        self._clamp_non_negative_int("interaction_count")
        self._clamp_float("created_at", 0.0, float("inf"), default=0.0)
        return self


class AccessDecision(BaseModel):
    """A permission result that always states why access was allowed or denied."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    owner_scope: str
    reader_scope: str
    allowed: bool
    reason: str
    message: str
    cross_scope: bool
    grant: AudienceGrant | None = None

    @model_validator(mode="after")
    def _require_disclosure(self) -> AccessDecision:
        if self.cross_scope and self.allowed and self.grant is None:
            raise ValueError("an allowed cross-scope decision must disclose its AudienceGrant")
        if not self.reason.strip() or not self.message.strip():
            raise ValueError("every access decision needs a reason and message")
        return self


class VisibleFacts(BaseModel):
    """Allowed fact bodies plus non-secret allow/deny disclosures."""

    model_config = ConfigDict(extra="forbid")

    reader_scope: str
    status: Literal["ok", "empty", "disabled"] = "ok"
    reason: str = ""
    facts: list[SharedFact] = Field(default_factory=list)
    decisions: list[AccessDecision] = Field(default_factory=list)

    @property
    def allowed(self) -> list[AccessDecision]:
        return [decision for decision in self.decisions if decision.allowed]

    @property
    def denied(self) -> list[AccessDecision]:
        return [decision for decision in self.decisions if not decision.allowed]


__all__ = [
    "INTERACTION_STATUSES",
    "AccessDecision",
    "AudienceGrant",
    "Counterpart",
    "CounterpartKind",
    "FactSensitivity",
    "InteractionStatus",
    "InteractionSummary",
    "Relationship",
    "SharedFact",
    "VisibleFacts",
    "normalize_scope",
]
