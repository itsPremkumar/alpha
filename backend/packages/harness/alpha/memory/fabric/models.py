"""Canonical Alpha memory envelope (schema v2) and its security boundary.

The envelope follows sections 8, 9, 18-21 of
``references/ALPHA_ADVANCED_OPEN_SOURCE_AGENTIC_MEMORY_SYSTEM.md``.  It is a
cross-cutting carrier for the memory types catalogued in
``docs/MEMORY_TYPES.md`` rather than a twentieth memory type: episodic,
semantic, procedural, spatio-temporal, and the other records all use the same
scope, evidence, quality, representation, and lifecycle fields.
"""

from __future__ import annotations

import hashlib
import math
import time
import uuid
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Self
from urllib.parse import quote, unquote

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SCOPE_FIELDS = (
    "tenant_id",
    "user_id",
    "agent_id",
    "project_id",
    "team_id",
    "session_id",
    "task_id",
)
_SCOPE_LABELS = {
    "tenant_id": "tenant",
    "user_id": "user",
    "agent_id": "agent",
    "project_id": "project",
    "team_id": "team",
    "session_id": "session",
    "task_id": "task",
}
_SCOPE_FIELDS_BY_LABEL = {label: field_name for field_name, label in _SCOPE_LABELS.items()}
_SCOPE_ALIASES = {
    "tenants": "tenant_id",
    "tenant": "tenant_id",
    "users": "user_id",
    "user": "user_id",
    "agents": "agent_id",
    "agent": "agent_id",
    "projects": "project_id",
    "project": "project_id",
    "teams": "team_id",
    "team": "team_id",
    "sessions": "session_id",
    "session": "session_id",
    "tasks": "task_id",
    "task": "task_id",
}
# Percent-zero is reserved as the missing-component marker. A literal value
# ``"%00"`` is encoded as ``"%2500"`` and therefore cannot collide with it.
_SCOPE_WILDCARD = "%00"
_SCOPE_PREFIX = "alpha://memory/"


def generate_memory_id() -> str:
    """Return a collision-resistant canonical memory identifier."""

    return f"mem_{uuid.uuid4().hex}"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _finite_timestamp(value: float | None, field_name: str) -> float | None:
    if value is None:
        return None
    timestamp = float(value)
    if not math.isfinite(timestamp):
        raise ValueError(f"{field_name} must be finite")
    return timestamp


class MemoryScope(BaseModel):
    """One namespace path in the hierarchy from tenant to individual task.

    Missing dimensions are wildcards in :meth:`qualified`.  A record scope
    permits a reader only when the reader has every value fixed by the record;
    additional reader dimensions make it a descendant.  In particular, a
    record fixed to one user never permits a different or unscoped reader.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    tenant_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    project_id: str | None = None
    team_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None

    @model_validator(mode="after")
    def _normalize_components(self) -> Self:
        for name in _SCOPE_FIELDS:
            object.__setattr__(self, name, _optional_text(getattr(self, name)))
        return self

    def qualified(self) -> str:
        """Return the stable, ordered namespace URI for this scope."""

        components = []
        for name in _SCOPE_FIELDS:
            value = getattr(self, name)
            encoded = _SCOPE_WILDCARD if value is None else quote(value, safe="")
            components.append(f"{_SCOPE_LABELS[name]}={encoded}")
        return f"{_SCOPE_PREFIX}{'/'.join(components)}"

    @classmethod
    def from_qualified(cls, value: str) -> MemoryScope:
        """Parse a canonical URI or a short ACL scope such as ``project:p``."""

        text = str(value or "").strip()
        if not text:
            raise ValueError("scope string must not be blank")
        if "://" not in text and ":" in text:
            label, component = text.split(":", 1)
            field_name = _SCOPE_ALIASES.get(label.strip().lower())
            if field_name is None:
                raise ValueError(f"unknown scope label {label!r}")
            return cls(**{field_name: component})
        if not text.startswith(_SCOPE_PREFIX):
            raise ValueError("scope URI must start with alpha://memory/")
        payload = text[len(_SCOPE_PREFIX) :]
        values: dict[str, str | None] = {}
        previous_index = -1
        for segment in payload.split("/"):
            label, separator, component = segment.partition("=")
            if not separator:
                raise ValueError(f"invalid scope component {segment!r}")
            field_name = _SCOPE_FIELDS_BY_LABEL.get(label)
            if field_name is None:
                raise ValueError(f"unknown scope component {label!r}")
            index = _SCOPE_FIELDS.index(field_name)
            if index <= previous_index:
                raise ValueError("scope components must be unique and ordered")
            previous_index = index
            if component == _SCOPE_WILDCARD:
                values[field_name] = None
            else:
                values[field_name] = _optional_text(unquote(component))
        return cls.model_validate(values)

    def permits(self, other: MemoryScope) -> bool:
        """Whether ``other`` is equal to or a descendant of this scope."""

        if not isinstance(other, MemoryScope):
            raise TypeError("scope comparison requires a MemoryScope")
        if self.user_id is not None and other.user_id != self.user_id:
            return False
        for field_name in _SCOPE_FIELDS:
            record_value = getattr(self, field_name)
            if record_value is not None and record_value != getattr(other, field_name):
                return False
        return True

    def is_descendant_of(self, other: MemoryScope) -> bool:
        """Whether this scope is strictly below ``other``."""

        return self != other and other.permits(self)

    def __str__(self) -> str:
        return self.qualified()


class Quality(BaseModel):
    """Unit quality signals with persistent disclosure of input clamping."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    importance: float = 0.5
    confidence: float = 0.5
    salience: float = 0.5
    novelty: float = 0.5
    clamped_fields: list[str] = Field(default_factory=list)
    clamping_disclosure: str = ""

    @model_validator(mode="after")
    def _clamp_unit_scores(self) -> Self:
        prior_fields = list(self.clamped_fields)
        prior_disclosure = self.clamping_disclosure
        clamped_fields: list[str] = []
        disclosures: list[str] = []
        for name in ("importance", "confidence", "salience", "novelty"):
            original = float(getattr(self, name))
            if math.isnan(original):
                raise ValueError(f"{name} must be a number")
            clamped = min(1.0, max(0.0, original))
            object.__setattr__(self, name, clamped)
            if clamped != original:
                clamped_fields.append(name)
                disclosures.append(f"{name}={original!r}->{clamped!r}")
        object.__setattr__(self, "clamped_fields", clamped_fields or prior_fields)
        disclosure = f"clamped: {', '.join(disclosures)}" if disclosures else prior_disclosure
        object.__setattr__(self, "clamping_disclosure", disclosure)
        return self

    @property
    def was_clamped(self) -> bool:
        """Whether any quality input was clamped during validation."""

        return bool(self.clamped_fields)

    @property
    def clamp_disclosure(self) -> str:
        """Stable disclosure compatible with other Alpha memory models."""

        return self.clamping_disclosure or "none"


class Representations(BaseModel):
    """Explicit claims about which derived representations actually exist."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    raw: bool = False
    text: bool = False
    embedding: bool = False
    graph: bool = False
    summary: bool = False


class SecurityClassification(StrEnum):
    """Allowed sensitivity labels for canonical memory."""

    NORMAL = "normal"
    SENSITIVE = "sensitive"
    SECRET = "secret"


class Security(BaseModel):
    """Sensitivity and explicit scope grants for one envelope."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    classification: SecurityClassification = SecurityClassification.NORMAL
    acl: list[str] = Field(default_factory=list)

    @field_validator("classification", mode="before")
    @classmethod
    def _normalize_classification(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("acl", mode="before")
    @classmethod
    def _normalize_acl(cls, value: Any) -> list[str]:
        candidates = [value] if isinstance(value, str) else value
        if candidates is None:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text and text not in seen:
                normalized.append(text)
                seen.add(text)
        return normalized

    def permits(self, reader_scope: MemoryScope) -> bool:
        """Return whether this security policy permits retrieval by a scope."""

        if self.classification is not SecurityClassification.SECRET:
            return True
        for scope_text in self.acl:
            try:
                allowed_scope = MemoryScope.from_qualified(scope_text)
            except (TypeError, ValueError):
                continue
            if allowed_scope.permits(reader_scope):
                return True
        return False


class SourceRef(BaseModel):
    """Evidence chain from an envelope to its originating event or artifact."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    kind: str = "unknown"
    event_id: str | None = None
    uri: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    tool_call_id: str | None = None
    content_hash: str | None = None
    observed_at: float | None = None

    @field_validator("kind", mode="before")
    @classmethod
    def _normalize_kind(cls, value: Any) -> str:
        return str(value or "unknown").strip().lower() or "unknown"

    @field_validator("event_id", "uri", "session_id", "agent_id", "task_id", "tool_call_id", "content_hash", mode="before")
    @classmethod
    def _optional_source_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("observed_at")
    @classmethod
    def _finite_observed_at(cls, value: float | None) -> float | None:
        return _finite_timestamp(value, "observed_at")


class Timestamps(BaseModel):
    """Transaction, observation, validity, access, and expiry timestamps."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    created_at: float = Field(default_factory=time.time)
    observed_at: float | None = None
    valid_from: float | None = None
    valid_to: float | None = None
    last_accessed_at: float | None = None
    expires_at: float | None = None

    @model_validator(mode="after")
    def _normalize_interval(self) -> Self:
        for name in ("created_at", "observed_at", "valid_from", "valid_to", "last_accessed_at", "expires_at"):
            object.__setattr__(self, name, _finite_timestamp(getattr(self, name), name))
        if self.valid_from is None:
            object.__setattr__(self, "valid_from", self.created_at)
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must be greater than or equal to valid_from")
        return self


class LifecycleStatus(StrEnum):
    """Canonical memory lifecycle states."""

    ACTIVE = "active"
    COMPRESSED = "compressed"
    ARCHIVED = "archived"
    PURGED = "purged"


class Lifecycle(BaseModel):
    """Evolution state, access pressure, links, and retention controls."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    status: LifecycleStatus = LifecycleStatus.ACTIVE
    access_count: int = Field(default=0, ge=0)
    decay_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    ttl_seconds: float | None = Field(default=None, gt=0.0)
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: list[str] = Field(default_factory=list)
    contradicts: list[str] = Field(default_factory=list)
    verified_by: list[str] = Field(default_factory=list)
    pinned: bool = False

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("supersedes", "superseded_by", "contradicts", "verified_by", mode="before")
    @classmethod
    def _normalize_links(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        links: list[str] = []
        seen: set[str] = set()
        for candidate in value:
            text = str(candidate or "").strip()
            if text and text not in seen:
                links.append(text)
                seen.add(text)
        return links


class MemoryEnvelope(BaseModel):
    """Canonical v2 envelope shared by every durable Alpha memory type."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str = Field(default_factory=generate_memory_id, min_length=1)
    scope: MemoryScope = Field(default_factory=MemoryScope)
    types: list[str] = Field(min_length=1)
    subtype: str | None = None
    content: str = Field(min_length=1)
    summary: str | None = None
    entities: list[str] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    source: SourceRef = Field(default_factory=SourceRef)
    timestamps: Timestamps = Field(default_factory=Timestamps)
    quality: Quality = Field(default_factory=Quality)
    lifecycle: Lifecycle = Field(default_factory=Lifecycle)
    representations: Representations = Field(default_factory=Representations)
    tags: list[str] = Field(default_factory=list)
    security: Security = Field(default_factory=Security)

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("scope", mode="before")
    @classmethod
    def _coerce_scope(cls, value: Any) -> Any:
        if isinstance(value, MemoryScope):
            return value
        if isinstance(value, str):
            return MemoryScope.from_qualified(value)
        return value

    @field_validator("types", mode="before")
    @classmethod
    def _normalize_types(cls, value: Any) -> list[str]:
        candidates = [value] if isinstance(value, str) else value
        if candidates is None:
            return []
        types: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            text = str(candidate or "").strip().lower()
            if text and text not in seen:
                types.append(text)
                seen.add(text)
        return types

    @field_validator("subtype", "summary", mode="before")
    @classmethod
    def _optional_envelope_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("content")
    @classmethod
    def _nonempty_content(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("memory content must not be empty")
        return text

    @field_validator("entities", "tags", mode="before")
    @classmethod
    def _normalize_text_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return [str(candidate).strip() for candidate in value if str(candidate).strip()]

    @field_validator("relations", mode="before")
    @classmethod
    def _normalize_relations(cls, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, (list, tuple)):
            raise ValueError("relations must be a list of objects")
        relations: list[dict[str, Any]] = []
        for relation in value:
            if not isinstance(relation, Mapping):
                raise ValueError("each relation must be an object")
            relations.append(dict(relation))
        return relations

    @classmethod
    def create(
        cls,
        content: str,
        *,
        scope: MemoryScope | Mapping[str, Any] | str | None = None,
        types: list[str] | str,
        subtype: str | None = None,
        summary: str | None = None,
        entities: list[str] | None = None,
        relations: list[dict[str, Any]] | None = None,
        source: SourceRef | Mapping[str, Any] | None = None,
        timestamps: Timestamps | Mapping[str, Any] | None = None,
        quality: Quality | Mapping[str, Any] | None = None,
        lifecycle: Lifecycle | Mapping[str, Any] | None = None,
        representations: Representations | Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        security: Security | Mapping[str, Any] | None = None,
        id: str | None = None,
        now: float | None = None,
        ttl_seconds: float | None = None,
        decay_rate: float | None = None,
        max_tags: int = 64,
        allow_secret_classification: bool = False,
    ) -> MemoryEnvelope:
        """Build a validated canonical record with honest representation flags."""

        timestamp = time.time() if now is None else _finite_timestamp(now, "now")
        if timestamp is None:
            raise ValueError("now must be finite")
        selected_tags = list(tags or [])
        if len(selected_tags) > max_tags:
            raise ValueError(f"memory has {len(selected_tags)} tags; maximum is {max_tags}")
        if isinstance(scope, str):
            selected_scope = MemoryScope.from_qualified(scope)
        elif isinstance(scope, Mapping):
            selected_scope = MemoryScope.model_validate(scope)
        elif scope is None:
            selected_scope = MemoryScope()
        else:
            selected_scope = scope
        if isinstance(source, Mapping):
            selected_source = SourceRef.model_validate(source)
        elif source is None:
            selected_source = SourceRef(kind="caller", observed_at=timestamp)
        else:
            selected_source = source
        if selected_source.content_hash is None:
            digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
            source_payload = selected_source.model_dump()
            source_payload["content_hash"] = f"sha256:{digest}"
            if source_payload.get("observed_at") is None:
                source_payload["observed_at"] = timestamp
            selected_source = SourceRef.model_validate(source_payload)
        if isinstance(timestamps, Mapping):
            selected_timestamps = Timestamps.model_validate(timestamps)
        elif timestamps is None:
            expiry = timestamp + ttl_seconds if ttl_seconds is not None else None
            selected_timestamps = Timestamps(created_at=timestamp, observed_at=timestamp, valid_from=timestamp, expires_at=expiry)
        else:
            selected_timestamps = timestamps
        if isinstance(lifecycle, Mapping):
            selected_lifecycle = Lifecycle.model_validate(lifecycle)
        elif lifecycle is None:
            selected_lifecycle = Lifecycle(decay_rate=0.05 if decay_rate is None else decay_rate, ttl_seconds=ttl_seconds)
        else:
            selected_lifecycle = lifecycle
        if isinstance(quality, Mapping):
            selected_quality = Quality.model_validate(quality)
        elif quality is None:
            selected_quality = Quality()
        else:
            selected_quality = quality
        if isinstance(representations, Mapping):
            selected_representations = Representations.model_validate(representations)
        elif representations is None:
            selected_representations = Representations(raw=True, text=True, summary=bool(_optional_text(summary)))
        else:
            selected_representations = representations
        if isinstance(security, Mapping):
            selected_security = Security.model_validate(security)
        elif security is None:
            selected_security = Security()
        else:
            selected_security = security
        if selected_security.classification is SecurityClassification.SECRET and not allow_secret_classification:
            raise ValueError("secret classification is rejected at admission by default")
        return cls.model_validate(
            {
                "id": id or generate_memory_id(),
                "scope": selected_scope,
                "types": types,
                "subtype": subtype,
                "content": content,
                "summary": summary,
                "entities": list(entities or []),
                "relations": list(relations or []),
                "source": selected_source,
                "timestamps": selected_timestamps,
                "quality": selected_quality,
                "lifecycle": selected_lifecycle,
                "representations": selected_representations,
                "tags": selected_tags,
                "security": selected_security,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible canonical representation."""

        return self.model_dump(mode="json")

    def is_expired(self, now: float | None = None) -> bool:
        """Whether the retention expiry boundary has been reached."""

        moment = time.time() if now is None else _finite_timestamp(now, "now")
        if moment is None:
            raise ValueError("now must be finite")
        return self.timestamps.expires_at is not None and moment >= self.timestamps.expires_at

    def is_true_at(self, when: float) -> bool:
        """Whether this version is temporally valid at an injected instant."""

        moment = _finite_timestamp(when, "when")
        if moment is None or self.lifecycle.status is LifecycleStatus.PURGED or self.is_expired(moment):
            return False
        valid_from = self.timestamps.valid_from
        if valid_from is None or moment < valid_from:
            return False
        return self.timestamps.valid_to is None or moment <= self.timestamps.valid_to

    def as_of(self, when: float) -> MemoryEnvelope | None:
        """Return this envelope when valid at ``when``, otherwise ``None``."""

        return self if self.is_true_at(when) else None

    def current(self) -> MemoryEnvelope | None:
        """Return this envelope when it is currently valid and retrievable."""

        return self.as_of(time.time())

    def visible_to(self, scope: MemoryScope, *, now: float | None = None) -> bool:
        """Apply lifecycle, expiry, namespace, and secret-ACL retrieval checks."""

        moment = time.time() if now is None else _finite_timestamp(now, "now")
        if moment is None:
            raise ValueError("now must be finite")
        if self.lifecycle.status is LifecycleStatus.PURGED or self.is_expired(moment):
            return False
        if not self.scope.permits(scope):
            return False
        return self.security.permits(scope)

    def touch(self, now: float | None = None) -> None:
        """Record one successful retrieval for decay and retention policy."""

        moment = time.time() if now is None else _finite_timestamp(now, "now")
        if moment is None:
            raise ValueError("now must be finite")
        self.lifecycle.access_count += 1
        self.timestamps.last_accessed_at = moment


__all__ = [
    "Lifecycle",
    "LifecycleStatus",
    "MemoryEnvelope",
    "MemoryScope",
    "Quality",
    "Representations",
    "Security",
    "SecurityClassification",
    "SourceRef",
    "Timestamps",
    "generate_memory_id",
]
