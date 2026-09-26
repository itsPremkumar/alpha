"""Validated records for Alpha's entity-memory layer.

The entity layer is an additive index over stored memories.  It records what a
raw record mentioned and links equivalent names; it does not replace the L1
records or Alpha's structured subject-predicate-object belief graph.

Name handling is deliberately explicit: display names receive Unicode NFKC
normalization and whitespace collapsing, while the normalized lookup value is
NFKC + casefolded.  Every persisted entity and mention carries a disclosure of
that policy so a caller never mistakes display spelling for lookup identity.
All implementation in this module is original.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NORMALIZATION_DISCLOSURE = "unicode_nfkc+whitespace_collapse+casefold"
_WHITESPACE_RE = re.compile(r"\s+")


class EntityType(StrEnum):
    """Closed entity taxonomy used by deterministic and model extraction."""

    PERSON = "person"
    ORG = "org"
    PRODUCT = "product"
    PROJECT = "project"
    SERVICE = "service"
    PLACE = "place"
    DATE = "date"
    OTHER = "other"


class EntityLinkRelation(StrEnum):
    """Closed relationship set stored in the entity graph."""

    ALIAS_OF = "alias_of"
    MENTIONS = "mentions"
    RELATED_TO = "related_to"


class ExtractionStatus(StrEnum):
    """Closed extraction result set shared by model and combined extraction."""

    OK = "ok"
    NO_JSON = "no_json"
    PARSE_FAIL = "parse_fail"
    NOT_ARRAY = "not_array"
    EMPTY = "empty"
    LLM_ERROR = "llm_error"


class IngestStatus(StrEnum):
    """Closed outcome set for one entity-ingest attempt."""

    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"


EntityExtractorMode = Literal["deterministic", "deterministic+model"]


def normalize_display_name(value: str) -> str:
    """Return a bounded, NFKC-normalized display name with collapsed whitespace."""
    normalized = _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", str(value))).strip()
    return normalized[:256]


def normalized_name(value: str) -> str:
    """Return the disclosed NFKC/whitespace/casefold lookup form."""
    return normalize_display_name(value).casefold()


def make_entity_id(canonical_name: str, entity_type: EntityType | str) -> str:
    """Build a stable id from normalized name plus type, never from call order."""
    kind = entity_type.value if isinstance(entity_type, EntityType) else str(entity_type).strip().lower()
    material = f"{kind}|{normalized_name(canonical_name)}".encode()
    return f"ent_{hashlib.sha256(material).hexdigest()[:24]}"


def _clean_strings(values: list[str], *, limit: int) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = normalize_display_name(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned[:limit])
    return output


def _unique_values(values: list[str], *, limit: int = 256) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value).strip()[:limit]
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            output.append(cleaned)
    return output


def _clean_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key)[:64]: item for key, item in list(value.items())[:64]}


class EntityCandidate(BaseModel):
    """One extraction candidate before a stable entity id is assigned."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1, max_length=256)
    canonical_name: str = Field(min_length=1, max_length=256)
    entity_type: EntityType = EntityType.OTHER
    aliases: list[str] = Field(default_factory=list, max_length=128)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    span: tuple[int, int] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    extractor: str = Field(default="deterministic", min_length=1, max_length=64)

    @field_validator("record_id")
    @classmethod
    def _clean_record_id(cls, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("record_id must not be blank")
        return cleaned

    @field_validator("canonical_name")
    @classmethod
    def _normalize_candidate_name(cls, value: str) -> str:
        cleaned = normalize_display_name(value)
        if not cleaned:
            raise ValueError("canonical_name must not be blank")
        return cleaned

    @field_validator("aliases")
    @classmethod
    def _normalize_candidate_aliases(cls, values: list[str]) -> list[str]:
        return _clean_strings(values, limit=256)

    @field_validator("metadata")
    @classmethod
    def _normalize_candidate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _clean_metadata(value)

    @field_validator("span")
    @classmethod
    def _validate_span(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is not None and (value[0] < 0 or value[1] <= value[0]):
            raise ValueError("span must be a non-negative half-open range")
        return value


class Entity(BaseModel):
    """A retained named entity and the raw records that mention it."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    canonical_name: str = Field(min_length=1, max_length=256)
    normalized_name: str = Field(min_length=1, max_length=256)
    entity_type: EntityType = EntityType.OTHER
    aliases: list[str] = Field(default_factory=list, max_length=128)
    mention_count: int = Field(default=0, ge=0)
    first_seen: float = Field(default=0.0, ge=0.0)
    last_seen: float = Field(default=0.0, ge=0.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_record_ids: list[str] = Field(default_factory=list, max_length=4096)
    metadata: dict[str, Any] = Field(default_factory=dict)
    merged_from: list[str] = Field(default_factory=list, max_length=4096)
    normalization_disclosure: Literal["unicode_nfkc+whitespace_collapse+casefold"] = NORMALIZATION_DISCLOSURE

    @model_validator(mode="before")
    @classmethod
    def _fill_normalized_name(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if not str(data.get("normalized_name") or "").strip() and isinstance(data.get("canonical_name"), str):
            data["normalized_name"] = normalized_name(data["canonical_name"])
        return data

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("entity id must not be blank")
        return cleaned

    @field_validator("canonical_name")
    @classmethod
    def _normalize_canonical_name(cls, value: str) -> str:
        cleaned = normalize_display_name(value)
        if not cleaned:
            raise ValueError("canonical_name must not be blank")
        return cleaned

    @field_validator("normalized_name")
    @classmethod
    def _normalize_lookup_name(cls, value: str) -> str:
        cleaned = normalized_name(value)
        if not cleaned:
            raise ValueError("normalized_name must not be blank")
        return cleaned

    @field_validator("aliases")
    @classmethod
    def _normalize_aliases(cls, values: list[str]) -> list[str]:
        return _clean_strings(values, limit=256)

    @field_validator("source_record_ids", "merged_from")
    @classmethod
    def _normalize_ids(cls, values: list[str]) -> list[str]:
        return _unique_values(values)

    @field_validator("metadata")
    @classmethod
    def _normalize_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _clean_metadata(value)

    def is_promoted(self, min_mentions_to_promote: int) -> bool:
        """Whether this retained entity meets the configured promotion floor."""
        return self.mention_count >= min_mentions_to_promote


class Mention(BaseModel):
    """One surface form observed in one source memory record."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1, max_length=256)
    entity_id: str = Field(min_length=1, max_length=128)
    surface_form: str = Field(min_length=1, max_length=256)
    span: tuple[int, int] | None = None
    created_at: float = Field(default=0.0, ge=0.0)
    normalization_disclosure: Literal["unicode_nfkc+whitespace_collapse+casefold"] = NORMALIZATION_DISCLOSURE

    @field_validator("record_id", "entity_id")
    @classmethod
    def _clean_identifiers(cls, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("identifier must not be blank")
        return cleaned

    @field_validator("entity_id")
    @classmethod
    def _validate_entity_id(cls, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("entity_id must not be blank")
        return cleaned

    @field_validator("surface_form")
    @classmethod
    def _normalize_surface(cls, value: str) -> str:
        cleaned = normalize_display_name(value)
        if not cleaned:
            raise ValueError("surface_form must not be blank")
        return cleaned

    @field_validator("span")
    @classmethod
    def _validate_span(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is not None and (value[0] < 0 or value[1] <= value[0]):
            raise ValueError("span must be a non-negative half-open range")
        return value


class EntityLink(BaseModel):
    """A directed link between retained entity nodes."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=128)
    target_id: str = Field(min_length=1, max_length=128)
    relation: EntityLinkRelation
    weight: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("source_id", "target_id")
    @classmethod
    def _validate_link_id(cls, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("link endpoint must not be blank")
        return cleaned


class EntityEviction(BaseModel):
    """Forensic record for an entity removed by deterministic retention."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1, max_length=128)
    canonical_name: str = Field(min_length=1, max_length=256)
    aliases: list[str] = Field(default_factory=list, max_length=128)
    mention_count: int = Field(ge=0)
    source_record_ids: list[str] = Field(default_factory=list, max_length=4096)
    merged_from: list[str] = Field(default_factory=list, max_length=4096)
    linked_entity_ids: list[str] = Field(default_factory=list, max_length=4096)
    removed_mention_count: int = Field(ge=0)
    removed_link_count: int = Field(ge=0)
    evicted_at: float = Field(ge=0.0)
    reason: Literal["max_entities_per_user"] = "max_entities_per_user"

    @field_validator("entity_id")
    @classmethod
    def _validate_evicted_id(cls, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("evicted entity id must not be blank")
        return cleaned

    @field_validator("canonical_name")
    @classmethod
    def _normalize_evicted_name(cls, value: str) -> str:
        return normalize_display_name(value)

    @field_validator("aliases")
    @classmethod
    def _normalize_evicted_aliases(cls, values: list[str]) -> list[str]:
        return _clean_strings(values, limit=256)

    @field_validator("source_record_ids", "merged_from", "linked_entity_ids")
    @classmethod
    def _normalize_eviction_ids(cls, values: list[str]) -> list[str]:
        return _unique_values(values)


class ExtractionOutcome(BaseModel):
    """Combined deterministic-first extraction result."""

    model_config = ConfigDict(extra="forbid")

    status: ExtractionStatus
    extractor: EntityExtractorMode = "deterministic"
    candidates: list[EntityCandidate] = Field(default_factory=list)
    model_status: ExtractionStatus | None = None
    raw: str = ""
    errors: list[str] = Field(default_factory=list, max_length=32)
    rejected_items: int = Field(default=0, ge=0)
    deterministic_count: int = Field(default=0, ge=0)
    model_count: int = Field(default=0, ge=0)

    @property
    def ok(self) -> bool:
        """Whether extraction produced at least one valid candidate."""
        return self.status is ExtractionStatus.OK and bool(self.candidates)


class EntityIngestResult(BaseModel):
    """Honest per-batch result; model degradation never erases deterministic data."""

    model_config = ConfigDict(extra="forbid")

    status: IngestStatus
    extraction_status: ExtractionStatus
    model_status: ExtractionStatus | None = None
    extractor: EntityExtractorMode = "deterministic"
    records_considered: int = Field(default=0, ge=0)
    candidates: int = Field(default=0, ge=0)
    deterministic_candidates: int = Field(default=0, ge=0)
    model_candidates: int = Field(default=0, ge=0)
    stored_mentions: int = Field(default=0, ge=0)
    new_entities: int = Field(default=0, ge=0)
    resolved_entities: int = Field(default=0, ge=0)
    merged_entities: int = Field(default=0, ge=0)
    evicted_entities: int = Field(default=0, ge=0)
    entity_ids: list[str] = Field(default_factory=list, max_length=4096)
    evicted_entity_ids: list[str] = Field(default_factory=list, max_length=4096)
    rejected_items: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list, max_length=32)
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status is IngestStatus.SUCCEEDED


__all__ = [
    "NORMALIZATION_DISCLOSURE",
    "Entity",
    "EntityCandidate",
    "EntityEviction",
    "EntityExtractorMode",
    "EntityIngestResult",
    "EntityLink",
    "EntityLinkRelation",
    "EntityType",
    "ExtractionOutcome",
    "ExtractionStatus",
    "IngestStatus",
    "Mention",
    "make_entity_id",
    "normalize_display_name",
    "normalized_name",
]
