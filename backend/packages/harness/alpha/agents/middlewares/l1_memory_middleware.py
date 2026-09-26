"""Middleware that feeds turns into the L1 typed-memory pipeline.

Mirrors :class:`alpha.agents.middlewares.memory_middleware.MemoryMiddleware`
(turn capture after the agent runs, ids resolved while the request context is
alive) but targets the L1 pipeline instead of the pluggable memory manager:
the pipeline owns its own debounce, so this middleware only enqueues.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime

from alpha.agents.memory.l1.gates import l1_enabled
from alpha.agents.memory.l1.pipeline import filter_capture_messages, get_l1_pipeline
from alpha.config.memory_config import get_memory_config
from alpha.runtime.user_context import resolve_runtime_user_id

if TYPE_CHECKING:
    from alpha.config.memory_config import MemoryConfig

logger = logging.getLogger(__name__)


class L1MemoryMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    pass


class L1MemoryMiddleware(AgentMiddleware[L1MemoryMiddlewareState]):
    """Queue each completed turn for L1 extraction (debounced, async).

    Registration is gated by :func:`l1_enabled`, but the gate is checked
    again here at capture time so a hot config reload that turns L1 off
    stops writes immediately without waiting for an agent rebuild.
    """

    state_schema = L1MemoryMiddlewareState

    def __init__(
        self,
        agent_name: str | None = None,
        *,
        memory_config: MemoryConfig | None = None,
        mode: str | None = None,
    ) -> None:
        super().__init__()
        self._agent_name = agent_name
        self._memory_config = memory_config
        self._mode = mode

    # -- shared resolution ------------------------------------------------
    def _enqueue(self, state: L1MemoryMiddlewareState, runtime: Runtime) -> bool:
        cfg = self._memory_config or get_memory_config()
        if not l1_enabled(cfg):
            return False

        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            config_data = get_config()
            thread_id = config_data.get("configurable", {}).get("thread_id")
        if not thread_id:
            logger.debug("L1 capture skipped: no thread_id in context")
            return False

        messages = state.get("messages", [])
        if not messages:
            return False
        capturable = filter_capture_messages(messages)
        if not capturable:
            return False

        # Capture ids here, while the request context is alive: the debounce
        # timer fires on a plain thread where ContextVars do not propagate.
        user_id = resolve_runtime_user_id(runtime)
        return get_l1_pipeline().capture(
            thread_id,
            messages,
            user_id=user_id,
            agent_name=self._agent_name,
            mode=self._mode or cfg.l1.mode,
        )

    @override
    def after_agent(self, state: L1MemoryMiddlewareState, runtime: Runtime) -> dict | None:
        """Queue the conversation for L1 extraction after the agent completes."""
        try:
            self._enqueue(state, runtime)
        except Exception:  # noqa: BLE001 - capture must never break the turn
            logger.exception("L1 capture failed for agent %s", self._agent_name)
        return None

    @override
    async def aafter_agent(self, state: L1MemoryMiddlewareState, runtime: Runtime) -> dict | None:
        """Async boundary: same enqueue, off the event loop."""
        try:
            self._enqueue(state, runtime)
        except Exception:  # noqa: BLE001 - capture must never break the turn
            logger.exception("L1 capture failed for agent %s", self._agent_name)
        return None


__all__ = ["L1MemoryMiddleware"]
