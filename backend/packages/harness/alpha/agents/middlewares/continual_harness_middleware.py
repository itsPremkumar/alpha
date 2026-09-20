"""Middleware to inject Continual Harness knowledge into agent prompt context.

Reads learned directives, project memories, and failure rules from local and
global HarnessState and injects them as a hidden system-reminder SystemMessage.
"""

from __future__ import annotations

import logging
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from alpha.harness.continual.state import HarnessState

logger = logging.getLogger(__name__)

CONTINUAL_HARNESS_REMINDER_KEY = "continual_harness_reminder"


class ContinualHarnessMiddleware(AgentMiddleware):
    """Injects active Continual Harness entries into agent conversation context."""

    def __init__(
        self,
        local_state: HarnessState | None = None,
        global_state: HarnessState | None = None,
    ):
        super().__init__()
        self._local_state = local_state
        self._global_state = global_state

    def _get_harness_context(self) -> str:
        parts: list[str] = []

        # 1. Global state (cross-session operating knowledge)
        g_state = self._global_state or HarnessState(scope="global")
        g_text = g_state.format_for_prompt()
        if g_text:
            parts.append(g_text)

        # 2. Local state (thread/workspace specific knowledge)
        l_state = self._local_state or HarnessState(scope="local")
        l_text = l_state.format_for_prompt()
        if l_text:
            parts.append(l_text)

        if not parts:
            return ""

        return "\n\n".join(parts)

    @override
    def before_agent(self, state: Any, runtime: Runtime) -> dict | None:
        """Inject continual harness reminders before agent runs."""
        context = self._get_harness_context()
        if not context:
            return None

        reminder_message = SystemMessage(
            content=f"<system-reminder>\n{context}\n</system-reminder>",
            additional_kwargs={
                "hide_from_ui": True,
                CONTINUAL_HARNESS_REMINDER_KEY: True,
            },
        )
        return {"messages": [reminder_message]}
