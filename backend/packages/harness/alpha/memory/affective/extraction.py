"""Optional model-based affect extraction with a closed, honest result set.

The model contract is a JSON array of objects containing all six semantic
fields. Parsing tolerates code fences, think blocks, and prose around the JSON
payload, but never repairs a missing label by guessing. Caller-supplied labels
through :meth:`AffectiveMemory.ingest_explicit` remain the preferred path when
a model is unavailable.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Mapping
from typing import Any

from alpha.agents.memory.l1.extractor import message_text

from .models import AffectEvent, AffectSource, AffectSubject, ExtractionOutcome, ExtractionStatus

logger = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*|\s*```", re.MULTILINE)
_REQUIRED_FIELDS = frozenset({"content", "subject", "valence", "arousal", "intensity", "confidence"})
_OPTIONAL_FIELDS = frozenset({"record_refs"})
_STATUS_VALUES = frozenset(status.value for status in ExtractionStatus)
_MAX_RAW_CHARS = 20000
_MAX_CONTENT_CHARS = 4000

_SYSTEM_PROMPT = """You extract affective memory from an interaction.
Return ONLY a JSON array. Every object must contain these required fields:
content (non-empty string), subject (user|agent|interaction|topic),
valence (number from -1 unpleasant to +1 pleasant), arousal (number from 0 calm to 1 activated),
intensity (number from 0 subtle to 1 strong), and confidence (number from 0 to 1).
record_refs (optional array of source message or record ids) is the only other permitted key.
Use an empty array when the text has no reliable emotional signal. Do not infer a label
from tone alone when the interaction provides no affective evidence."""


def _clean_response(text: str) -> str:
    without_think = _THINK_BLOCK_RE.sub("", text)
    return _FENCE_RE.sub("", without_think).strip()


def _first_json_payload(text: str) -> Any | None:
    """Decode the first complete JSON value at a bracket in noisy prose."""
    cleaned = _clean_response(text)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", cleaned):
        try:
            payload, _ = decoder.raw_decode(cleaned, match.start())
        except json.JSONDecodeError:
            continue
        return payload
    return None


def _record_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:256] for item in value if str(item).strip()][:64]


def _event_from_object(
    value: Mapping[str, Any],
    *,
    user_id: str,
    agent_name: str | None,
    created_at: float,
) -> AffectEvent:
    if not _REQUIRED_FIELDS.issubset(value):
        raise ValueError("affect object is missing a required semantic field")
    if not set(value).issubset(_REQUIRED_FIELDS | _OPTIONAL_FIELDS):
        raise ValueError("affect object contains an unapproved field")
    content = value["content"]
    if not isinstance(content, str):
        raise ValueError("affect content must be a string")
    return AffectEvent(
        user_id=user_id,
        agent_name=agent_name,
        subject=AffectSubject(str(value["subject"]).strip().lower()),
        content=content[:_MAX_CONTENT_CHARS],
        valence=value["valence"],
        arousal=value["arousal"],
        intensity=value["intensity"],
        confidence=value["confidence"],
        source=AffectSource.MODEL_INFERRED,
        record_refs=_record_refs(value.get("record_refs")),
        created_at=created_at,
    )


def parse_extraction_response(
    text: str | None,
    *,
    user_id: str,
    agent_name: str | None = None,
    created_at: float | None = None,
) -> ExtractionOutcome:
    """Parse a model response without ever inferring absent affect labels."""
    raw = "" if text is None else str(text)[:_MAX_RAW_CHARS]
    if not raw.strip():
        return ExtractionOutcome(status=ExtractionStatus.NO_JSON, raw=raw)
    if not re.search(r"[\[{]", _clean_response(raw)):
        return ExtractionOutcome(status=ExtractionStatus.NO_JSON, raw=raw)

    payload = _first_json_payload(raw)
    if payload is None:
        return ExtractionOutcome(status=ExtractionStatus.PARSE_FAIL, raw=raw)
    if isinstance(payload, dict):
        payload = payload.get("events")
    if not isinstance(payload, list):
        return ExtractionOutcome(status=ExtractionStatus.NOT_ARRAY, raw=raw)
    if not payload:
        return ExtractionOutcome(status=ExtractionStatus.EMPTY, raw=raw)

    timestamp = time.time() if created_at is None else float(created_at)
    events: list[AffectEvent] = []
    rejected = 0
    for item in payload:
        if not isinstance(item, Mapping):
            rejected += 1
            continue
        try:
            event = _event_from_object(
                item,
                user_id=user_id,
                agent_name=agent_name,
                created_at=timestamp,
            )
        except (TypeError, ValueError):
            rejected += 1
            continue
        events.append(event)
    if not events:
        return ExtractionOutcome(
            status=ExtractionStatus.EMPTY,
            raw=raw,
            rejected_items=rejected,
        )
    return ExtractionOutcome(
        status=ExtractionStatus.OK,
        events=events,
        raw=raw,
        rejected_items=rejected,
    )


def extraction_status_known(status: str | ExtractionStatus) -> bool:
    """Return whether ``status`` is in the closed extraction status set."""
    value = status.value if isinstance(status, ExtractionStatus) else str(status)
    return value in _STATUS_VALUES


class AffectiveExtractor:
    """Invoke an injected model, or a named host model when explicitly configured."""

    def __init__(self, model: Any = None, model_name: str | None = None) -> None:
        self._model = model
        self._model_name = model_name
        self._resolved = model is not None

    def _resolve_model(self) -> Any | None:
        if self._resolved:
            return self._model
        self._resolved = True
        if not self._model_name:
            return None
        try:
            from alpha.models import create_chat_model

            self._model = create_chat_model(name=self._model_name)
        except Exception:  # noqa: BLE001 - model setup is a disclosed runtime state
            logger.warning(
                "Affective extraction could not build configured model %r",
                self._model_name,
                exc_info=True,
            )
            self._model = None
        return self._model

    def extract(
        self,
        text: str,
        *,
        user_id: str,
        agent_name: str | None = None,
        now: float | None = None,
    ) -> ExtractionOutcome:
        """Extract affect events. This method never raises for model/parse failures."""
        if not text.strip():
            return ExtractionOutcome(status=ExtractionStatus.EMPTY)
        model = self._resolve_model()
        if model is None:
            return ExtractionOutcome(
                status=ExtractionStatus.LLM_ERROR,
                error="no_model_configured",
            )
        prompt = f"{_SYSTEM_PROMPT}\n\nInteraction text:\n{text[:12000]}"
        try:
            response = model.invoke(
                prompt,
                config={
                    "run_name": "affective_memory_extraction",
                    "metadata": {"user_id": user_id, "agent_name": agent_name or ""},
                },
            )
        except Exception as exc:  # noqa: BLE001 - extraction failure must not break a turn
            logger.warning("Affective extraction model call failed: %s", exc)
            return ExtractionOutcome(
                status=ExtractionStatus.LLM_ERROR,
                error=str(exc)[:500],
            )
        return parse_extraction_response(
            message_text(response),
            user_id=user_id,
            agent_name=agent_name,
            created_at=now,
        )


__all__ = [
    "AffectiveExtractor",
    "extraction_status_known",
    "parse_extraction_response",
]
