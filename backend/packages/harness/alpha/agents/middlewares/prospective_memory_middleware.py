"""Capture explicit user reminders into prospective memory.

Only clear, user-authored reminder phrases are captured. The subsystem is
default-off and requires an authenticated/runtime owner; ambiguous intent is
left in the conversation rather than inferred into durable state.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import TYPE_CHECKING, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from alpha.config.memory_config import get_memory_config
from alpha.memory.prospective.dates import parse_due_at
from alpha.runtime.user_context import resolve_runtime_user_id

if TYPE_CHECKING:
    from alpha.config.memory_config import MemoryConfig

logger = logging.getLogger(__name__)
_REMINDER = re.compile(
    r"(?:\bremind[ \t]+me[ \t]+to[ \t]+|\bdon't[ \t]+let[ \t]+me[ \t]+forget[ \t]+to[ \t]+|\bdo[ \t]+not[ \t]+let[ \t]+me[ \t]+forget[ \t]+to[ \t]+)(.+)",
    re.IGNORECASE,
)
_MAX_CAPTURE_CHARS = 400


class ProspectiveMemoryMiddlewareState(AgentState):
    """Compatible with the lead agent's shared message state."""


def _message_text(message: HumanMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict) and isinstance(part.get("text"), str))
    return ""


def extract_explicit_reminder(messages: list[object]) -> str | None:
    """Extract the final explicit reminder from the latest human message."""

    latest = next((message for message in reversed(messages) if isinstance(message, HumanMessage)), None)
    if latest is None:
        return None
    match = _REMINDER.search(_message_text(latest))
    if match is None:
        return None
    content = re.split(r"[\r\n]", match.group(1), maxsplit=1)[0].strip(" \t.,!?;:")
    if not content:
        return None
    return content[:_MAX_CAPTURE_CHARS].rstrip()


def _latest_human_message(messages: list[object]) -> HumanMessage | None:
    return next((message for message in reversed(messages) if isinstance(message, HumanMessage)), None)


class ProspectiveMemoryMiddleware(AgentMiddleware[ProspectiveMemoryMiddlewareState]):
    """Persist explicit reminders after a successful agent turn."""

    state_schema = ProspectiveMemoryMiddlewareState

    def __init__(self, agent_name: str | None = None, *, memory_config: MemoryConfig | None = None):
        super().__init__()
        self._agent_name = agent_name
        self._memory_config = memory_config

    def _capture(self, state: ProspectiveMemoryMiddlewareState, runtime: Runtime) -> None:
        cfg = self._memory_config or get_memory_config()
        prospective = getattr(cfg, "prospective", None)
        if not getattr(cfg, "enabled", False) or not getattr(prospective, "enabled", False):
            return
        user_id = resolve_runtime_user_id(runtime)
        if not isinstance(user_id, str) or not user_id.strip() or user_id == "default":
            logger.info("Prospective capture skipped: authenticated owner is unavailable")
            return
        messages = list(state.get("messages", []))
        content = extract_explicit_reminder(messages)
        if not content:
            return
        thread_id = runtime.context.get("thread_id") if isinstance(runtime.context, dict) else None
        latest = _latest_human_message(messages)
        runtime_context = runtime.context if isinstance(runtime.context, dict) else {}
        source_id = getattr(latest, "id", None) or runtime_context.get("run_id")
        # A stable source key makes retries/resumes idempotent. If the runtime
        # cannot identify the user message or run, don't create a duplicate.
        if not source_id:
            logger.info("Prospective capture skipped: stable source identity is unavailable")
            return
        item_id = "prospective_" + hashlib.sha256(f"{user_id}\0{self._agent_name or ''}\0{source_id}\0{content}".encode()).hexdigest()[:32]
        # A confident due time stamps the record and carries the phrase/rule
        # provenance; anything ambiguous keeps due_at unset rather than
        # getting a guessed (wrong) date.
        parsed = parse_due_at(content)
        metadata: dict[str, object] = {"capture_method": "explicit_reminder_phrase"}
        if parsed is not None:
            metadata.update(parsed.to_metadata())
        try:
            from alpha.memory.prospective.models import ProspectiveKind
            from alpha.memory.prospective.store import ProspectiveStore

            result = ProspectiveStore(prospective).create(
                user_id=user_id,
                agent_name=self._agent_name,
                content=content,
                kind=ProspectiveKind.REMINDER,
                item_id=item_id,
                due_at=parsed.due_at if parsed else None,
                source={"kind": "explicit_user_request", "thread_id": str(thread_id or "")},
                metadata=metadata,
            )
            if not result.ok:
                if result.reason == "duplicate_item_id":
                    logger.debug("Prospective reminder was already captured for this source message")
                else:
                    logger.warning("Prospective reminder capture was not persisted (status=%s reason=%s)", result.status, result.reason)
        except Exception as exc:  # noqa: BLE001 - optional memory cannot fail a turn
            logger.warning("Prospective reminder capture failed (%s)", type(exc).__name__, exc_info=True)

    @override
    def after_agent(self, state: ProspectiveMemoryMiddlewareState, runtime: Runtime) -> None:
        self._capture(state, runtime)

    @override
    async def aafter_agent(self, state: ProspectiveMemoryMiddlewareState, runtime: Runtime) -> None:
        await asyncio.to_thread(self._capture, state, runtime)


__all__ = ["ProspectiveMemoryMiddleware", "extract_explicit_reminder"]
