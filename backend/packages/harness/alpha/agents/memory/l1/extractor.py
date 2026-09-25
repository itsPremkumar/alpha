"""L1 extraction engine: messages -> scene segments + typed memories.

Design provenance: the extraction contract (system prompt selected by mode,
message batching with background context, strict-JSON array response,
tolerance for think/fence noise) follows ``tencentdb-agent-memory``
``MemoryCore/src/core/l1-extractor.ts`` + ``l1-extraction.ts`` (MIT) — see
``docs/THIRD_PARTY_MEMORY_NOTICES.md``. Model construction follows Alpha's
host conventions (``alpha.models.create_chat_model``, same seam the memory
manager uses for its own extraction).
"""

from __future__ import annotations

import logging
from typing import Any

from .models import ExtractionOutcome
from .parser import parse_extraction_response
from .prompts import format_extraction_prompt, get_extraction_system_prompt
from .quota import usage_from_response

logger = logging.getLogger(__name__)


def _credits_for(response: Any, model_name: str | None) -> float:
    """Credits for one response (0.0 when the response reports no usage)."""
    try:
        return usage_from_response(response, model=model_name)
    except Exception:  # noqa: BLE001 - usage bookkeeping must not fail a run
        return 0.0


def message_text(response: Any) -> str:
    """Normalize an LLM response (str | AIMessage-like) to plain text."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        if parts:
            return "\n".join(parts)
    return str(response)


class L1Extractor:
    """Runs the L1 extraction prompt against an LLM and parses the result.

    The model is injected (constructor arg) so tests can pass a fake without
    touching global state; ``model_name`` selects the host model when no
    model is injected (``memory.l1.extraction_model``).
    """

    def __init__(self, model: Any = None, model_name: str | None = None) -> None:
        self._model = model
        self._model_name = model_name
        self._resolved = model is not None

    @property
    def model(self) -> Any | None:
        return self._resolve_model()

    def _resolve_model(self) -> Any | None:
        if self._resolved:
            return self._model
        self._resolved = True
        try:
            from alpha.models import create_chat_model

            self._model = create_chat_model(name=self._model_name)
        except Exception:  # noqa: BLE001 - missing model is a config state, not a crash
            logger.warning(
                "L1 extraction could not build model %r; extraction disabled for this run",
                self._model_name,
                exc_info=True,
            )
            self._model = None
        return self._model

    def extract(
        self,
        new_messages: list[dict[str, Any]],
        *,
        background_messages: list[dict[str, Any]] | None = None,
        previous_scene_name: str | None = None,
        mode: str = "chat",
        thread_id: str = "",
        user_id: str = "",
    ) -> ExtractionOutcome:
        """Extract scene segments + candidate memories from messages.

        Each message dict carries ``id`` / ``role`` / ``content`` and an
        optional ``timestamp``. Never raises: LLM failures come back as
        ``status="llm_error"`` so the pipeline records an honest report
        instead of crashing the agent turn.
        """
        if not new_messages:
            return ExtractionOutcome(status="empty_scenes")
        model = self._resolve_model()
        if model is None:
            return ExtractionOutcome(status="llm_error", error="no_model_configured")

        system_prompt = get_extraction_system_prompt(mode)
        user_prompt = format_extraction_prompt(
            new_messages=new_messages,
            background_messages=background_messages,
            previous_scene_name=previous_scene_name,
        )
        invoke_config: dict[str, Any] = {
            "run_name": "l1_memory_extraction",
            "metadata": {"thread_id": thread_id, "user_id": user_id, "mode": mode},
        }
        try:
            response = model.invoke(
                f"{system_prompt}\n\n{user_prompt}", config=invoke_config
            )
        except BaseException as exc:  # noqa: BLE001 - report, never crash the turn
            logger.warning("L1 extraction LLM call failed: %s", exc)
            return ExtractionOutcome(status="llm_error", error=str(exc)[:500])

        outcome = parse_extraction_response(message_text(response))
        outcome.credits = _credits_for(response, self._model_name)
        if outcome.status != "ok":
            logger.info(
                "L1 extraction response not usable (status=%s, thread=%s)",
                outcome.status,
                thread_id,
            )
        return outcome


__all__ = ["L1Extractor", "message_text"]
