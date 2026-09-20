"""Middleware to inject Continual Harness knowledge into agent prompt context.

Reads learned directives, project memories, and failure rules from local and
global HarnessState and injects them as a hidden system-reminder SystemMessage.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from alpha.harness.continual.state import HarnessState

logger = logging.getLogger(__name__)

CONTINUAL_HARNESS_REMINDER_KEY = "continual_harness_reminder"

# Local-state file name used when resolving the per-thread workspace harness.
_LOCAL_HARNESS_DIR = ".alpha-harness"
_LOCAL_HARNESS_FILE = "state.json"


class ContinualHarnessMiddleware(AgentMiddleware):
    """Injects active Continual Harness entries into agent conversation context.

    Fail-open by design: any disk/state error degrades to "no reminder" instead of
    breaking agent assembly or a run. When no explicit states are supplied, the
    global state resolves from the standard location and the local state resolves
    from the current thread workspace (``runtime.context["workspace_path"]``) when
    available, falling back to the process working directory.
    """

    def __init__(
        self,
        local_state: HarnessState | None = None,
        global_state: HarnessState | None = None,
    ):
        super().__init__()
        self._local_state = local_state
        self._global_state = global_state

    def _resolve_local_state(self, runtime: Any) -> HarnessState:
        if self._local_state is not None:
            return self._local_state
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            workspace = context.get("workspace_path") or context.get("workspace_dir")
            if workspace:
                try:
                    return HarnessState(
                        file_path=Path(str(workspace)) / _LOCAL_HARNESS_DIR / _LOCAL_HARNESS_FILE,
                        scope="local",
                    )
                except (OSError, ValueError):
                    logger.debug("Continual harness workspace path invalid", exc_info=True)
        return HarnessState(scope="local")

    def _get_harness_context(self, runtime: Any = None) -> str:
        parts: list[str] = []

        # 1. Global state (cross-session operating knowledge)
        try:
            g_state = self._global_state or HarnessState(scope="global")
            g_text = g_state.format_for_prompt()
            if g_text:
                parts.append(g_text)
        except Exception:
            logger.debug("Continual harness global state unavailable", exc_info=True)

        # 2. Local state (thread/workspace specific knowledge)
        try:
            l_state = self._resolve_local_state(runtime)
            l_text = l_state.format_for_prompt()
            if l_text:
                parts.append(l_text)
        except Exception:
            logger.debug("Continual harness local state unavailable", exc_info=True)

        if not parts:
            return ""

        return "\n\n".join(parts)

    @override
    def before_agent(self, state: Any, runtime: Runtime) -> dict | None:
        """Inject continual harness reminders before agent runs (fail-open)."""
        try:
            context = self._get_harness_context(runtime)
        except Exception:
            logger.debug("Continual harness context build failed", exc_info=True)
            return None
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
