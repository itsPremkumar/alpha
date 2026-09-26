"""Deterministic-first entity extraction with optional injected-model enrichment.

Deterministic extraction only indexes explicit lexical evidence: quoted names,
code/repository identifiers, dates, acronyms, and stopword-bounded capitalized
sequences.  It does not invent an entity when no such evidence exists.  Optional
model enrichment runs afterward through an injected/model-factory seam; a
model failure is reported in the closed status set while deterministic entities
remain intact.  Model construction is deliberately outside this package; hosts
inject the object selected using ``extraction_model``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from alpha.agents.memory.l1.extractor import message_text

from .models import (
    NORMALIZATION_DISCLOSURE,
    EntityCandidate,
    EntityExtractorMode,
    EntityType,
    ExtractionOutcome,
    ExtractionStatus,
    normalize_display_name,
    normalized_name,
)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*|\s*```", re.MULTILINE)
_QUOTED_RE = re.compile(r"(?P<quote>[\"“”])(?P<name>[^\"“”]{2,120})[\"“”]")
_CODE_RE = re.compile(r"`(?P<name>[A-Za-z0-9][A-Za-z0-9_.:/-]{1,100})`")
_ISO_DATE_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?!\d)")
_MONTH_DATE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+"
    r"(?:[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?(?:,?\s+(?:19|20)\d{2})?\b",
    re.IGNORECASE,
)
_CAPITALIZED_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"[A-Z][\w'’]*(?:[.-][A-Za-z0-9][\w'’]*)*"
    r"(?:\s+(?:(?:of|and|de|del|la|le)\s+)?"
    r"[A-Z][\w'’]*(?:[.-][A-Za-z0-9][\w'’]*)*)*"
)
_ACRONYM_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,14}(?![A-Za-z0-9])")
_CODE_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]+(?:[._:/-][A-Za-z0-9]+)+(?![A-Za-z0-9])")
_MAX_RAW_CHARS = 20_000
_MAX_RECORD_CHARS = 4_000
_MAX_CANDIDATES_PER_RECORD = 64

_ORG_SUFFIXES = frozenset(
    {
        "ag",
        "bv",
        "co",
        "company",
        "corp",
        "corporation",
        "gmbh",
        "inc",
        "incorporated",
        "limited",
        "llc",
        "ltd",
        "plc",
        "sa",
        "sarl",
    }
)
_PRODUCT_CONTEXT_RE = re.compile(
    r"(?:\b(?:uses?|used|using|works?\s+with|switched\s+to|recommends?|recommended|"
    r"deploys?|deployed)\b[^\n]{0,24})$",
    re.IGNORECASE,
)
_PLACE_CONTEXT_RE = re.compile(
    r"(?:\b(?:in|at|from|to|visited|visiting|traveled\s+to| flew\s+to)[^\n]{0,12})$",
    re.IGNORECASE,
)
_PERSON_CONTEXT_RE = re.compile(
    r"(?:\b(?:met|meet|meeting|with|by|works?\s+at|works\s+for|"
    r"emailed|called|told)[^\n]{0,18})$",
    re.IGNORECASE,
)
_HONORIFIC_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Mx|Dr|Prof)\.?\s*$", re.IGNORECASE)
_BOUNDARY_WORDS = frozenset(
    {
        "a",
        "all",
        "an",
        "and",
        "at",
        "because",
        "but",
        "by",
        "for",
        "from",
        "he",
        "her",
        "here",
        "his",
        "i",
        "in",
        "is",
        "it",
        "meeting",
        "of",
        "on",
        "our",
        "she",
        "that",
        "the",
        "their",
        "this",
        "to",
        "today",
        "tomorrow",
        "user",
        "we",
        "with",
        "yesterday",
        "you",
        "yours",
    }
)
_SERVICE_CODE_SUFFIXES = ("-service", "_service", "-api", "_api", "-worker", "_worker")

_MODEL_PROMPT = """You enrich an entity index over explicit memory records.
Return ONLY a JSON array. Every object must contain record_id (an id present in
the input), name, and type. Allowed types are person, org, product, project,
service, place, date, other. Optional fields are aliases (array of strings),
confidence (number 0..1), and span ([start, end]). Do not include an entity that
is not explicit in the named record. Use an empty array when uncertain."""


def _clean_response(text: str) -> str:
    return _FENCE_RE.sub("", _THINK_BLOCK_RE.sub("", text)).strip()


def _first_json_payload(text: str) -> tuple[Any | None, bool]:
    """Return ``(payload, saw_json_delimiter)`` from noisy model prose."""
    cleaned = _clean_response(text)
    saw_delimiter = bool(re.search(r"[\[{]", cleaned))
    if not saw_delimiter:
        return None, False
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", cleaned):
        try:
            payload, _ = decoder.raw_decode(cleaned, match.start())
        except json.JSONDecodeError:
            continue
        return payload, True
    return None, True


def extraction_status_known(status: ExtractionStatus | str) -> bool:
    """Whether a status belongs to the closed entity-extraction set."""
    value = status.value if isinstance(status, ExtractionStatus) else str(status)
    return value in {item.value for item in ExtractionStatus}


def _record_id(record: Mapping[str, Any]) -> str:
    value = record.get("id") or record.get("record_id") or ""
    return str(value).strip()[:256]


def _record_text(record: Mapping[str, Any]) -> str:
    value = record.get("content")
    if not isinstance(value, str):
        value = record.get("text")
    return value if isinstance(value, str) else ""


def _trim_candidate_span(text: str, start: int, end: int) -> tuple[str, int, int]:
    name = text[start:end]
    left_trimmed = name.lstrip()
    start += len(name) - len(left_trimmed)
    right_trimmed = left_trimmed.rstrip(" \t\r\n.,;:!?-")
    end = start + len(right_trimmed)
    return right_trimmed, start, end


def _ignored_name(name: str) -> bool:
    tokens = tuple(re.findall(r"[^\W_]+", normalized_name(name), flags=re.UNICODE))
    if not tokens:
        return True
    if all(token in _BOUNDARY_WORDS for token in tokens):
        return True
    return normalized_name(name) in {"", "n/a", "none", "unknown"}


def _candidate_type(
    *,
    raw_name: str,
    text: str,
    start: int,
    end: int,
    rule: str,
) -> EntityType:
    tokens = tuple(re.findall(r"[^\W_]+", normalized_name(raw_name), flags=re.UNICODE))
    compact = raw_name.replace(" ", "").lower()
    before = text[max(0, start - 32) : start]
    after = text[end : min(len(text), end + 32)]
    if rule == "date":
        return EntityType.DATE
    if tokens and tokens[-1] in _ORG_SUFFIXES:
        return EntityType.ORG
    if rule == "code":
        if "/" in raw_name:
            return EntityType.PROJECT
        if compact.endswith(_SERVICE_CODE_SUFFIXES):
            return EntityType.SERVICE
        return EntityType.PRODUCT
    if _PERSON_CONTEXT_RE.search(before) or _HONORIFIC_RE.search(before):
        return EntityType.PERSON
    if re.match(r"\s+(?:works?|worked|is|was|reported|says|said|joined|left)", after, re.IGNORECASE):
        return EntityType.PERSON
    if _PRODUCT_CONTEXT_RE.search(before):
        return EntityType.PRODUCT
    if _PLACE_CONTEXT_RE.search(before):
        return EntityType.PLACE
    if rule == "quoted":
        return EntityType.OTHER
    if len(tokens) >= 2:
        return EntityType.PERSON
    return EntityType.OTHER


def _make_candidate(
    *,
    record_id: str,
    name: str,
    start: int,
    end: int,
    text: str,
    rule: str,
    confidence: float,
) -> EntityCandidate | None:
    display_name, adjusted_start, adjusted_end = _trim_candidate_span(text, start, end)
    if _ignored_name(display_name):
        return None
    return EntityCandidate(
        record_id=record_id,
        canonical_name=display_name,
        entity_type=_candidate_type(
            raw_name=display_name,
            text=text,
            start=adjusted_start,
            end=adjusted_end,
            rule=rule,
        ),
        confidence=confidence,
        span=(adjusted_start, adjusted_end),
        metadata={
            "extractor": "deterministic",
            "rule": rule,
            "normalization": NORMALIZATION_DISCLOSURE,
        },
        extractor="deterministic",
    )


def _deterministic_for_record(record: Mapping[str, Any]) -> list[EntityCandidate]:
    record_id = _record_id(record)
    text = _record_text(record)[:_MAX_RECORD_CHARS]
    if not record_id or not text.strip():
        return []
    found: list[tuple[int, int, int, str, str]] = []
    for match in _QUOTED_RE.finditer(text):
        found.append((match.start("name"), match.end("name"), 100, match.group("name"), "quoted"))
    for match in _CODE_RE.finditer(text):
        found.append((match.start("name"), match.end("name"), 100, match.group("name"), "code"))
    for pattern in (_ISO_DATE_RE, _MONTH_DATE_RE):
        for match in pattern.finditer(text):
            found.append((match.start(), match.end(), 100, match.group(0), "date"))
    for match in _CAPITALIZED_RE.finditer(text):
        found.append((match.start(), match.end(), 80, match.group(0), "capitalized"))
    for match in _ACRONYM_RE.finditer(text):
        found.append((match.start(), match.end(), 70, match.group(0), "acronym"))
    for match in _CODE_IDENTIFIER_RE.finditer(text):
        if "`" not in text[max(0, match.start() - 1) : match.start() + 1]:
            found.append((match.start(), match.end(), 90, match.group(0), "code"))

    found.sort(key=lambda item: (item[0], -item[2], -(item[1] - item[0]), item[4]))
    accepted: list[tuple[int, int, int, str, str]] = []
    for candidate in found:
        if any(candidate[0] < other[1] and other[0] < candidate[1] for other in accepted):
            continue
        accepted.append(candidate)
        if len(accepted) >= _MAX_CANDIDATES_PER_RECORD:
            break

    candidates: list[EntityCandidate] = []
    seen: set[tuple[str, EntityType]] = set()
    for start, end, _priority, raw_name, rule in accepted:
        candidate = _make_candidate(
            record_id=record_id,
            name=raw_name,
            start=start,
            end=end,
            text=text,
            rule=rule,
            confidence=0.9 if rule in {"quoted", "code", "date"} else 0.75,
        )
        if candidate is None:
            continue
        key = (normalized_name(candidate.canonical_name), candidate.entity_type)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def extract_entities(records: Iterable[Mapping[str, Any]]) -> list[EntityCandidate]:
    """Run deterministic extraction over plain record dictionaries only."""
    candidates: list[EntityCandidate] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        candidates.extend(_deterministic_for_record(record))
        if len(candidates) >= _MAX_CANDIDATES_PER_RECORD * 64:
            break
    return candidates


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [normalize_display_name(item) for item in value if isinstance(item, str) and item.strip()]


def _candidate_from_model_object(
    value: Mapping[str, Any],
    *,
    valid_record_ids: frozenset[str],
) -> EntityCandidate | None:
    raw_record_id = value.get("record_id")
    raw_name = value.get("name")
    raw_type = value.get("type")
    if not isinstance(raw_record_id, str) or not isinstance(raw_name, str) or not isinstance(raw_type, str):
        return None
    record_id = raw_record_id.strip()
    name = normalize_display_name(raw_name)
    if record_id not in valid_record_ids or not name or _ignored_name(name):
        return None
    try:
        entity_type = EntityType(raw_type.strip().lower())
    except ValueError:
        return None
    confidence = value.get("confidence", 0.8)
    try:
        confidence_value = min(1.0, max(0.0, float(confidence)))
    except (TypeError, ValueError):
        return None
    span: tuple[int, int] | None = None
    raw_span = value.get("span")
    if isinstance(raw_span, list) and len(raw_span) == 2:
        try:
            candidate_span = (int(raw_span[0]), int(raw_span[1]))
            if candidate_span[0] < 0 or candidate_span[1] <= candidate_span[0]:
                return None
            span = candidate_span
        except (TypeError, ValueError):
            return None
    return EntityCandidate(
        record_id=record_id,
        canonical_name=name,
        entity_type=entity_type,
        aliases=_string_list(value.get("aliases")),
        confidence=confidence_value,
        span=span,
        metadata={
            "extractor": "model",
            "normalization": NORMALIZATION_DISCLOSURE,
        },
        extractor="model",
    )


def parse_model_response(
    text: str | None,
    *,
    valid_record_ids: Iterable[str],
) -> ExtractionOutcome:
    """Parse a strict model JSON array while tolerating fences and surrounding prose."""
    raw = "" if text is None else str(text)[:_MAX_RAW_CHARS]
    if not raw.strip():
        return ExtractionOutcome(
            status=ExtractionStatus.NO_JSON,
            extractor="deterministic+model",
            model_status=ExtractionStatus.NO_JSON,
            raw=raw,
        )
    payload, saw_delimiter = _first_json_payload(raw)
    if payload is None:
        status = ExtractionStatus.PARSE_FAIL if saw_delimiter else ExtractionStatus.NO_JSON
        return ExtractionOutcome(
            status=status,
            extractor="deterministic+model",
            model_status=status,
            raw=raw,
        )
    if not isinstance(payload, list):
        status = ExtractionStatus.NOT_ARRAY
        return ExtractionOutcome(
            status=status,
            extractor="deterministic+model",
            model_status=status,
            raw=raw,
        )
    if not payload:
        status = ExtractionStatus.EMPTY
        return ExtractionOutcome(
            status=status,
            extractor="deterministic+model",
            model_status=status,
            raw=raw,
        )

    valid = frozenset(str(item).strip() for item in valid_record_ids if str(item).strip())
    candidates: list[EntityCandidate] = []
    rejected = 0
    for item in payload:
        if not isinstance(item, Mapping):
            rejected += 1
            continue
        try:
            candidate = _candidate_from_model_object(item, valid_record_ids=valid)
        except (TypeError, ValueError):
            candidate = None
        if candidate is None:
            rejected += 1
            continue
        candidates.append(candidate)
    if not candidates:
        status = ExtractionStatus.EMPTY
        return ExtractionOutcome(
            status=status,
            extractor="deterministic+model",
            model_status=status,
            raw=raw,
            rejected_items=rejected,
        )
    return ExtractionOutcome(
        status=ExtractionStatus.OK,
        extractor="deterministic+model",
        candidates=candidates,
        model_status=ExtractionStatus.OK,
        raw=raw,
        rejected_items=rejected,
        model_count=len(candidates),
    )


def _merge_candidates(
    deterministic: list[EntityCandidate],
    modeled: list[EntityCandidate],
) -> list[EntityCandidate]:
    combined: dict[tuple[str, str, EntityType], EntityCandidate] = {}
    for candidate in (*deterministic, *modeled):
        key = (
            candidate.record_id,
            normalized_name(candidate.canonical_name),
            candidate.entity_type,
        )
        current = combined.get(key)
        if current is None:
            combined[key] = candidate.model_copy(deep=True)
            continue
        aliases = list(current.aliases)
        for alias in (candidate.canonical_name, *candidate.aliases):
            if normalized_name(alias) not in {normalized_name(item) for item in aliases}:
                aliases.append(alias)
        current.aliases = aliases
        current.confidence = max(current.confidence, candidate.confidence)
        if current.span is None:
            current.span = candidate.span
    return sorted(
        combined.values(),
        key=lambda item: (
            item.record_id,
            item.span[0] if item.span is not None else -1,
            normalized_name(item.canonical_name),
            item.entity_type.value,
        ),
    )


class EntityExtractor:
    """Injectable deterministic extractor with optional model enrichment.

    ``extractor="deterministic"`` is a first-class mode that guarantees no
    model invocation.  ``"deterministic+model"`` permits enrichment only when
    the caller also passes ``enable_model=True``.
    """

    def __init__(
        self,
        model: Any = None,
        *,
        model_name: str | None = None,
        extractor: EntityExtractorMode = "deterministic",
    ) -> None:
        if extractor not in ("deterministic", "deterministic+model"):
            raise ValueError("extractor must be deterministic or deterministic+model")
        self._model = model
        self._model_name = model_name
        self._mode: EntityExtractorMode = extractor

    @property
    def mode(self) -> EntityExtractorMode:
        return self._mode

    def _resolve_model(self) -> Any | None:
        """Return only the injected model; hosts own named-model construction."""
        return self._model

    def extract(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        enable_model: bool = False,
    ) -> ExtractionOutcome:
        """Extract candidates. Model and parse failures never raise or replace deterministic output."""
        try:
            materialized = [record for record in records if isinstance(record, Mapping)]
            deterministic = extract_entities(materialized)
        except Exception as exc:  # noqa: BLE001 - extraction is a never-raise boundary
            return ExtractionOutcome(
                status=ExtractionStatus.LLM_ERROR,
                extractor=self._mode,
                model_status=ExtractionStatus.LLM_ERROR if self._mode == "deterministic+model" else None,
                errors=[str(exc)[:500] or type(exc).__name__],
            )
        deterministic_count = len(deterministic)
        should_call_model = self._mode == "deterministic+model" and enable_model
        if not materialized:
            return ExtractionOutcome(
                status=ExtractionStatus.EMPTY,
                extractor=self._mode,
                candidates=[],
                deterministic_count=0,
            )
        if not should_call_model:
            return ExtractionOutcome(
                status=ExtractionStatus.OK if deterministic else ExtractionStatus.EMPTY,
                extractor=self._mode,
                candidates=deterministic,
                deterministic_count=deterministic_count,
            )

        model = self._resolve_model()
        if model is None:
            model_outcome = ExtractionOutcome(
                status=ExtractionStatus.LLM_ERROR,
                extractor="deterministic+model",
                model_status=ExtractionStatus.LLM_ERROR,
                errors=["no_model_configured"],
            )
        else:
            payload = [
                {
                    "id": _record_id(record),
                    "content": _record_text(record)[:_MAX_RECORD_CHARS],
                }
                for record in materialized
                if _record_id(record) and _record_text(record).strip()
            ]
            prompt = f"{_MODEL_PROMPT}\n\nRecords JSON:\n{json.dumps(payload, ensure_ascii=False)}"
            try:
                response = model.invoke(
                    prompt,
                    config={
                        "run_name": "entity_memory_extraction",
                        "metadata": {
                            "component": "entity_memory_extraction",
                            "model_name": self._model_name or "",
                        },
                    },
                )
                model_outcome = parse_model_response(
                    message_text(response),
                    valid_record_ids=(str(item["id"]) for item in payload),
                )
            except Exception as exc:  # noqa: BLE001 - extraction must not break ingest
                model_outcome = ExtractionOutcome(
                    status=ExtractionStatus.LLM_ERROR,
                    extractor="deterministic+model",
                    model_status=ExtractionStatus.LLM_ERROR,
                    errors=[str(exc)[:500] or type(exc).__name__],
                )

        combined = _merge_candidates(deterministic, model_outcome.candidates)
        status = ExtractionStatus.OK if combined else model_outcome.status
        return ExtractionOutcome(
            status=status,
            extractor="deterministic+model",
            candidates=combined,
            model_status=model_outcome.status,
            raw=model_outcome.raw,
            errors=model_outcome.errors,
            rejected_items=model_outcome.rejected_items,
            deterministic_count=deterministic_count,
            model_count=model_outcome.model_count,
        )


__all__ = [
    "EntityExtractor",
    "extract_entities",
    "extraction_status_known",
    "parse_model_response",
]
