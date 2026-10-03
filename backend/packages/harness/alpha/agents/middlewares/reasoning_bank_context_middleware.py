"""Project a bounded ReasoningBank recall into the model request.

Every model call gets at most ONE fresh, bounded recall of measured past
strategies appended as a hidden HumanMessage — the same contract
``ViewImageMiddleware`` follows: the payload lives only in that request,
never enters state, and a checkpoint can never strand a stale block.

Trust classification: the recall text is past-agent strategy text, user-
influenceable, so it rides the untrusted channel exactly once (``HumanMessage``),
stamped with provenance metadata and forced invisible in the UI. It never
bombards the model: at most one block per call, bounded characters, no
per-turn growth of checkpointed history.

Injection is driven entirely by (score > 0) overlap between the current
user message and the bank's records — an empty bank produces exactly the
previous middleware behavior.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, override

from alpha_extension_api import ContentKind, provenance_kwargs
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

_RECALL_LIMIT = 3
_RECALL_MAX_CHARS = 800

_ID_PREFIX = "__reasoning_bank__"
_REMINDER_KEY = "reasoning_bank_reminder"


def _is_marker(message: Any) -> bool:
    additional = getattr(message, "additional_kwargs", None)
    return bool(isinstance(additional, dict) and additional.get(_REMINDER_KEY) is True) and str(getattr(message, "id", "") or "").startswith(_ID_PREFIX)


def _is_user_injection_target(message: Any) -> bool:
    return isinstance(message, HumanMessage) and not isinstance(message, SystemMessage) and not _is_marker(message)


def _extract_query(message: Any) -> str:
    """Return a bounded string query from a HumanMessage's content."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()[:400]
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text") if part.get("type") == "text" else None
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts).strip()[:400]
    return ""


class ReasoningBankContextMiddleware(AgentMiddleware):
    """Append one hidden reasoning-bank recall per model call."""

    def __init__(self, *, app_config: Any = None) -> None:
        super().__init__()
        self._app_config = app_config

    def release_policy_parameters(self) -> dict[str, object]:
        return {"reasoning_bank_recall": _enabled(self._app_config)}

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._inject(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._inject(request))

    # -- internals ------------------------------------------------------------

    def _inject(self, request: ModelRequest) -> ModelRequest:
        if not _enabled(self._app_config):
            return request

        # Sweep stranded marker messages first. Both the ID prefix and the
        # server-owned kwarg must match, so a user-authored stand-in is
        # never dropped and marker stamping stays authoritative.
        messages = list(getattr(request, "messages", []) or [])
        kept = [message for message in messages if not _is_marker(message)]

        user_messages = [m for m in kept if _is_user_injection_target(m)]
        if not user_messages:
            return request if kept == messages else request.override(messages=kept)
        last_user = user_messages[-1]
        query = _extract_query(last_user)
        if not query:
            return request if kept == messages else request.override(messages=kept)

        try:
            from alpha.reasoning_bank import get_reasoning_bank

            evidence = get_reasoning_bank().render(query, limit=_RECALL_LIMIT, max_chars=_RECALL_MAX_CHARS)
        except Exception:
            logger.debug("ReasoningBankMiddleware: bank unavailable; skipping injection", exc_info=True)
            return request if kept == messages else request.override(messages=kept)

        if not evidence:
            return request if kept == messages else request.override(messages=kept)

        injected = HumanMessage(
            content=f"<reasoning_bank>\n{evidence}\n</reasoning_bank>",
            id=f"{_ID_PREFIX}{getattr(last_user, 'id', '') or 'current'}",
            additional_kwargs={
                "hide_from_ui": True,
                _REMINDER_KEY: True,
                **provenance_kwargs(ContentKind.MEMORY, "reasoning_bank"),
            },
        )
        return request.override(messages=[*kept, injected])


def _enabled(app_config: Any) -> bool:
    if app_config is None:
        return True
    raw = getattr(app_config, "reasoning_bank", None)
    if raw is None:
        return True
    if isinstance(raw, dict):
        if raw.get("enabled") is False:
            return False
        if raw.get("injection_enabled") is False:
            return False
        return True
    enabled = getattr(raw, "enabled", None)
    if enabled is False:
        return False
    injection_enabled = getattr(raw, "injection_enabled", None)
    if injection_enabled is False:
        return False
    return True


__all__ = ["ReasoningBankContextMiddleware"]
