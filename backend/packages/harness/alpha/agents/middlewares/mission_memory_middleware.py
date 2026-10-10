"""Inject the durable mission anchor before every model turn.

This middleware is the reason a long-horizon run does not drift. The durable
mission stack (:mod:`alpha.runtime.missions`) holds the objective, the plan, the
status log and the scratchpad; this middleware re-reads it before *every* model
request and projects the compact, bounded anchor into the request so the target is
never more than one turn out of view -- even after a context compaction, and
across a process restart.

Design choices that matter:

* **Injection is per-request and ephemeral** (a ``wrap_model_call`` override), not
  a persisted ``before_model`` message. The anchor is recomputed each turn from
  the live durable file, so persisting it would bloat the checkpoint with a
  stale copy that compaction then has to summarise. The anchor is a projection,
  never state.
* **Idempotent.** If the request already carries the anchor (a re-entry within
  the same request, a resumed stream), nothing is inserted again.
* **Fail-open.** A missing mission, a corrupt file, an unreadable path, or an
  unresolved scope is "no anchor" -- it never breaks agent assembly, matching how
  the durable-runtime treats a broken measurement. The per-request budget is
  bounded by ``mission_memory.max_anchor_chars``.

The middleware is stateless except for the scope it captures in ``before_agent``
(owner + thread), which is exactly what :class:`ThreadDataMiddleware` resolves.
The instance is created per-run, so that captured scope cannot outlive its run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, override

from alpha_extension_api import ContentKind, provenance_kwargs
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from alpha.agents.middlewares.message_utils import insert_after_leading_system_messages
from alpha.config.paths import get_paths
from alpha.runtime.missions import ANCHOR_HEADER, MissionManager, render_anchor
from alpha.runtime.user_context import resolve_runtime_user_id

if TYPE_CHECKING:
    from alpha.config.app_config import AppConfig

logger = logging.getLogger(__name__)

#: Marker key stamped into the injected message so a future observer can tell
#: this reminder apart from any other hidden message, and so injection is
#: detectable without matching on rendered text alone.
_ANCHOR_MARKER: str = "mission_memory_anchor"

__all__ = ["MissionMemoryMiddleware"]


class MissionMemoryMiddleware(AgentMiddleware[AgentState]):
    """Re-anchor every model call on the durable objective + current milestone."""

    def __init__(self, *, app_config: AppConfig) -> None:
        super().__init__()
        self._cfg = app_config.mission_memory
        self._owner: str | None = None
        self._thread_id: str | None = None

    # ---- scope -------------------------------------------------------------------

    def _thread_from_context(self) -> str | None:
        configurable = get_config().get("configurable", {}) or {}
        return configurable.get("thread_id")

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        context = (getattr(runtime, "context", None) or {}) if runtime is not None else {}
        thread = context.get("thread_id") or self._thread_from_context()
        self._thread_id = str(thread) if thread else None
        try:
            self._owner = resolve_runtime_user_id(runtime)
        except Exception:  # pragma: no cover - defensive; owner resolution is total
            self._owner = None
        return None

    def _scope(self) -> tuple[str | None, str | None]:
        owner = self._owner
        if owner is None:
            try:
                owner = resolve_runtime_user_id(None)
            except Exception:
                owner = None
        thread = self._thread_id or self._thread_from_context()
        return (owner, str(thread) if thread else None)

    # ---- injection ---------------------------------------------------------------

    @staticmethod
    def _anchor_present(messages: list) -> bool:
        return any(isinstance(getattr(m, "content", None), str) and ANCHOR_HEADER in m.content for m in messages)

    def _build_anchor(self, owner: str, thread_id: str) -> str:
        manager = MissionManager(
            get_paths(),
            max_status_lines=self._cfg.max_status_lines,
            max_scratch_entries=self._cfg.max_scratch_entries,
        )
        stack = manager.load(owner, thread_id)
        if not stack.is_active():
            return ""
        return render_anchor(
            stack,
            status_tail=self._cfg.status_tail,
            scratch_tail=self._cfg.scratch_tail,
            max_chars=self._cfg.max_anchor_chars,
        )

    def _build_override(self, request: ModelRequest) -> ModelRequest:
        """Build the anchor-injected request override.

        This is synchronous memory I/O (a bounded, path-safe file read). It is the
        whole reason ``awrap_model_call`` runs it through ``asyncio.to_thread``:
        the durable-runtime's event-loop discipline forbids blocking reads on the
        Gateway's async model path, the same rule fleet admission follows.
        """
        if not getattr(self._cfg, "enabled", False):
            return request
        owner, thread = self._scope()
        if not owner or not thread:
            return request
        messages = list(request.messages or [])
        if not messages or self._anchor_present(messages):
            return request
        try:
            anchor = self._build_anchor(owner, thread)
        except Exception:  # fail-open: never break the model call for memory
            logger.debug("mission anchor build failed", exc_info=True)
            return request
        if not anchor:
            return request
        reminder = SystemMessage(
            content=anchor,
            additional_kwargs={
                "hide_from_ui": True,
                _ANCHOR_MARKER: True,
                **provenance_kwargs(ContentKind.MIDDLEWARE_INJECTION, "mission_memory"),
            },
        )
        return request.override(messages=insert_after_leading_system_messages(messages, [reminder]))

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelCallResult:
        return handler(self._build_override(request))

    @override
    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelCallResult:
        # The blocking memory read runs off the event loop; the handler is awaited
        # on it. Injected as a projection, never checkpointed into graph state.
        overridden = await asyncio.to_thread(self._build_override, request)
        return await handler(overridden)
