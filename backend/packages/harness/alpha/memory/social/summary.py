"""Optional model-generated interaction summaries with honest deterministic fallback.

The model is always injected. This module never resolves a model name, reads
credentials, or performs network I/O. Any absent model, exception, malformed
response, unknown status, or schema violation produces a clearly labelled
bounded count/topic summary instead of raising or claiming model success.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .config import SocialConfig
from .models import INTERACTION_STATUSES, InteractionStatus, InteractionSummary

_ALLOWED_RESPONSE_KEYS = frozenset({"summary", "outcomes", "status"})
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


class SummaryModel(Protocol):
    """Minimal injected model seam (LangChain ``invoke`` is compatible)."""

    def invoke(self, prompt: str) -> Any: ...


def _safe_text(value: Any, limit: int) -> str:
    try:
        return str(value or "").strip()[:limit]
    except Exception:
        return ""


def _clean_topics(topics: Sequence[str] | None) -> list[str]:
    cleaned: list[str] = []
    try:
        values = topics or ()
        for topic in values:
            text = _safe_text(topic, 120)
            if text and text not in cleaned:
                cleaned.append(text)
            if len(cleaned) >= 16:
                break
    except Exception:
        return cleaned
    return cleaned


def _clean_outcomes(outcomes: Sequence[str] | None) -> list[str]:
    cleaned: list[str] = []
    try:
        values = outcomes or ()
        for outcome in values:
            text = _safe_text(outcome, 240)
            if text and text not in cleaned:
                cleaned.append(text)
            if len(cleaned) >= 16:
                break
    except Exception:
        return cleaned
    return cleaned


def _deterministic_summary(
    *,
    counterpart_id: str,
    status: InteractionStatus,
    outcomes: list[str],
    topics: list[str],
    interaction_count: int,
    created_at: float,
    reason: str,
    model_name: str | None,
) -> InteractionSummary:
    topic_text = ", ".join(topics[-5:]) if topics else "none recorded"
    outcome_text = "; ".join(outcomes[-3:]) if outcomes else status
    text = f"Deterministic interaction summary ({reason}): {max(0, interaction_count)} interaction(s); latest topics: {topic_text}; latest outcomes: {outcome_text}."
    return InteractionSummary(
        counterpart_id=counterpart_id,
        summary=text[:4_000],
        outcomes=outcomes,
        status=status,
        source="deterministic",
        topics=topics,
        interaction_count=max(0, interaction_count),
        created_at=created_at,
        fallback_reason=reason,
        model_name=model_name,
    )


def _response_payload(response: Any) -> tuple[Mapping[str, Any] | None, str]:
    if isinstance(response, Mapping):
        return response, "ok"
    if response is None:
        return None, "no_json"
    try:
        text = response if isinstance(response, str) else getattr(response, "content", None)
    except Exception:
        return None, "no_json"
    if not isinstance(text, str) or not text.strip():
        return None, "no_json"
    candidate = text.strip()
    fenced = _FENCE_RE.match(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0:
        return None, "no_json"
    if end <= start:
        return None, "parse_error"
    try:
        parsed = json.loads(candidate[start : end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, "parse_error"
    if not isinstance(parsed, Mapping):
        return None, "schema_error"
    return parsed, "ok"


def _parse_model_response(
    response: Any,
) -> tuple[tuple[str, list[str], InteractionStatus] | None, str]:
    payload, response_status = _response_payload(response)
    if payload is None:
        return None, response_status
    if set(payload) != _ALLOWED_RESPONSE_KEYS:
        return None, "schema_error"
    summary = payload.get("summary")
    outcomes = payload.get("outcomes")
    outcome_status = payload.get("status")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 4_000:
        return None, "schema_error"
    if not isinstance(outcomes, list) or any(not isinstance(item, str) for item in outcomes):
        return None, "schema_error"
    if outcome_status not in INTERACTION_STATUSES:
        return None, "schema_error"
    parsed = (summary.strip(), [item.strip() for item in outcomes if item.strip()], outcome_status)
    return parsed, "ok"


def _prompt(
    *,
    counterpart_id: str,
    status: InteractionStatus,
    outcomes: list[str],
    topics: list[str],
    interaction_count: int,
    model_name: str | None,
) -> str:
    model_label = model_name or "injected-summary-model"
    return (
        "Summarize this social-memory interaction. Return exactly one JSON object with "
        'keys "summary" (string), "outcomes" (array of strings), and "status" '
        "(one of completed, partial, failed, cancelled, unknown). No markdown or extra keys.\n"
        f"Model label: {model_label}\n"
        f"Counterpart id: {counterpart_id}\n"
        f"Observed status: {status}\n"
        f"Interaction count: {max(0, interaction_count)}\n"
        f"Outcomes: {json.dumps(outcomes, ensure_ascii=False)}\n"
        f"Topics: {json.dumps(topics, ensure_ascii=False)}"
    )


def summarize_interaction(
    counterpart_id: str,
    *,
    status: str = "unknown",
    outcomes: Sequence[str] | None = None,
    topics: Sequence[str] | None = None,
    interaction_count: int = 1,
    model: SummaryModel | None = None,
    config: SocialConfig | None = None,
    now: float | None = None,
) -> InteractionSummary:
    """Generate a model summary when explicitly enabled, otherwise fall back safely."""

    cfg = config or SocialConfig()
    created_at = time.time() if now is None else float(now)
    clean_outcomes = _clean_outcomes(outcomes)
    clean_topics = _clean_topics(topics)
    try:
        count = max(0, int(interaction_count))
    except (TypeError, ValueError, OverflowError):
        count = 0
    valid_status = isinstance(status, str) and status in INTERACTION_STATUSES
    clean_status: InteractionStatus = status if valid_status else "unknown"
    input_reason = "" if valid_status else "invalid_input_status"
    clean_counterpart_id = _safe_text(counterpart_id, 240) or "unknown"

    def fallback(reason: str) -> InteractionSummary:
        return _deterministic_summary(
            counterpart_id=clean_counterpart_id,
            status=clean_status,
            outcomes=clean_outcomes,
            topics=clean_topics,
            interaction_count=count,
            created_at=created_at,
            reason=reason,
            model_name=cfg.summary_model,
        )

    if not cfg.enable_model_summaries:
        return fallback(input_reason or "model_disabled")
    if model is None:
        return fallback(input_reason or "model_unavailable")

    try:
        response = model.invoke(
            _prompt(
                counterpart_id=clean_counterpart_id,
                status=clean_status,
                outcomes=clean_outcomes,
                topics=clean_topics,
                interaction_count=count,
                model_name=cfg.summary_model,
            )
        )
    except Exception:
        return fallback(input_reason or "model_error")

    try:
        parsed, parse_status = _parse_model_response(response)
    except Exception:
        return fallback(input_reason or "schema_error")
    if parsed is None:
        return fallback(input_reason or parse_status)
    summary, parsed_outcomes, parsed_status = parsed
    return InteractionSummary(
        counterpart_id=clean_counterpart_id,
        summary=summary,
        outcomes=parsed_outcomes,
        status=parsed_status,
        source="model",
        topics=clean_topics,
        interaction_count=count,
        created_at=created_at,
        model_name=cfg.summary_model,
    )


__all__ = ["SummaryModel", "summarize_interaction"]
