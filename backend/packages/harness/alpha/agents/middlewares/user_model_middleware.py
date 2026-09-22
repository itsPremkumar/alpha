"""User-model middleware — drives ``alpha.agents.memory.user_model``.

That package shipped complete: a :class:`UserModelProvider` ABC with six
lifecycle hooks, a ``NullUserModelProvider`` whose every method is a no-op, and a
``FileUserModelProvider`` that persists a per-user dialectic log — plus a
dedicated test suite and **no caller**. Nothing ever constructed a provider, so
``memory.user_model.provider`` in ``config.yaml`` was a knob wired to nothing and
the personalization path never ran.

This middleware is the missing integration point. The hooks map one-to-one:

    ``abefore_agent``     -> ``provider.initialize(runtime, app_config)``
    ``awrap_model_call``  -> ``prefetch`` -> inject ``system_prompt_block`` ->
                             ``handler`` -> ``sync_turn(runtime, prefetched)``
    ``awrap_tool_call``   -> ``handler`` -> ``handle_tool_call(...)``
    ``aafter_agent``      -> ``provider.shutdown()``

The default is ``provider: null`` -> ``NullUserModelProvider``, whose
``system_prompt_block`` returns ``None`` and therefore contributes **zero bytes**
to the system channel. Wiring this middleware is byte-identical to not having it
until an operator opts in — the same guarantee ``shadow_mode`` gives the System
One sites.

Only the async hooks do any work; the sync graph path passes straight through, so
it stays byte-identical too. Those sync passthroughs are **required**, not
optional polish: ``AgentMiddleware.wrap_model_call`` and ``.wrap_tool_call`` raise
``NotImplementedError`` ("Synchronous implementation ... is not available") when
only the async variant is defined, so an async-only middleware would take down any
synchronous ``invoke()``/``stream()``. Driving the provider from the sync path
would mean blocking on coroutines from a possibly-running loop, so personalisation
stays on the async path instead.

Every provider call is individually guarded: personalization must never be able to
take down a run. A provider that raises is logged and dropped for the rest of the
run rather than retried, so a broken provider cannot slow every model call.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime

from alpha.agents.memory.user_model import (
    NullUserModelProvider,
    UserModelProvider,
    create_user_model_provider,
)
from alpha.agents.middlewares._bounded_dict import BoundedDict
from alpha.config.app_config import AppConfig

logger = logging.getLogger(__name__)


def _key(runtime: Runtime) -> tuple[str, str]:
    """Per-run identity, matching the convention in the other middlewares."""
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        thread_id = str(context.get("thread_id") or "unknown-thread")
        run_id = str(context.get("run_id") or context.get("run_attempt_id") or id(runtime))
        return thread_id, run_id
    return "unknown-thread", str(id(runtime))


class UserModelMiddleware(AgentMiddleware[AgentState]):
    """Inject personalised context from the configured user-model provider."""

    def __init__(
        self,
        app_config: AppConfig | None = None,
        *,
        provider: UserModelProvider | None = None,
    ) -> None:
        super().__init__()
        self._app_config = app_config
        # An explicit provider wins, so a test (or a future embedder) can inject
        # one without going through config.
        if provider is not None:
            self._provider: UserModelProvider = provider
        else:
            self._provider = self._build_provider(app_config)
        # ``NullUserModelProvider`` is a pure no-op, so short-circuiting it keeps
        # the default path free of any per-call bookkeeping.
        self._active = not isinstance(self._provider, NullUserModelProvider)
        self._initialized: BoundedDict[tuple[str, str], bool] = BoundedDict(1000)
        self._failed: BoundedDict[tuple[str, str], bool] = BoundedDict(1000)

    @staticmethod
    def _build_provider(app_config: AppConfig | None) -> UserModelProvider:
        """Resolve the configured provider, degrading to the null one.

        A bad ``memory.user_model`` block must never break agent assembly: an
        unknown provider name or a missing ``storage_path`` raises here, and the
        correct response is "run unpersonalised", not "refuse to start".
        """
        if app_config is None:
            return NullUserModelProvider()
        user_model = getattr(getattr(app_config, "memory", None), "user_model", None)
        name = getattr(user_model, "provider", None)
        raw_path = getattr(user_model, "storage_path", None)
        storage_path = Path(raw_path) if raw_path else None
        try:
            return create_user_model_provider(name, config=app_config, storage_path=storage_path)
        except Exception:
            logger.exception(
                "UserModelMiddleware: could not build user-model provider %r; falling back to the null provider",
                name,
            )
            return NullUserModelProvider()

    # -- lifecycle ----------------------------------------------------------

    async def _ensure_initialized(self, runtime: Runtime) -> None:
        key = _key(runtime)
        if self._initialized.get(key) or self._failed.get(key):
            return
        try:
            await self._provider.initialize(runtime, self._app_config_or_none())
        except Exception:
            # Mark as failed so a broken provider is not retried on every turn.
            self._failed[key] = True
            logger.exception("UserModelMiddleware: provider.initialize failed; disabling for this run")
            return
        self._initialized[key] = True

    def _app_config_or_none(self) -> Any:
        """The config captured at construction; ``initialize`` takes it as an argument."""
        return self._app_config

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        if not self._active:
            return None
        await self._ensure_initialized(runtime)
        return None

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        """Sync path: pass through untouched (see the module docstring for why)."""
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        if not self._active:
            return await handler(request)

        runtime = request.runtime
        await self._ensure_initialized(runtime)
        if self._failed.get(_key(runtime)):
            return await handler(request)

        prefetched: dict[str, Any] = {}
        try:
            prefetched = await self._provider.prefetch(runtime)
        except Exception:
            logger.exception("UserModelMiddleware: provider.prefetch failed; continuing without it")

        try:
            block = self._provider.system_prompt_block(runtime)
        except Exception:
            logger.exception("UserModelMiddleware: provider.system_prompt_block failed; injecting nothing")
            block = None

        if block:
            request = request.override(messages=_with_system_block(request.messages, block))

        result = await handler(request)

        try:
            await self._provider.sync_turn(runtime, prefetched)
        except Exception:
            logger.exception("UserModelMiddleware: provider.sync_turn failed; ignoring")
        return result

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        """Sync path: pass through untouched (see the module docstring for why)."""
        return handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        if not self._active:
            return await handler(request)

        result = await handler(request)
        runtime = getattr(request, "runtime", None)
        if runtime is None or self._failed.get(_key(runtime)):
            return result
        tool_call = request.tool_call or {}
        try:
            await self._provider.handle_tool_call(
                runtime,
                str(tool_call.get("name") or ""),
                dict(tool_call.get("args") or {}),
                result,
            )
        except Exception:
            logger.exception("UserModelMiddleware: provider.handle_tool_call failed; ignoring")
        return result

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        if not self._active:
            return None
        key = _key(runtime)
        try:
            await self._provider.shutdown()
        except Exception:
            logger.exception("UserModelMiddleware: provider.shutdown failed; ignoring")
        finally:
            self._initialized.pop(key, None)
            self._failed.pop(key, None)
        return None


def _with_system_block(messages: list[Any], block: str) -> list[Any]:
    """Insert *block* as its own SystemMessage, ahead of the conversation.

    The provider contract is explicit that the block is a separate SystemMessage
    and never merged into the base prompt: when the provider returns ``None``
    the system channel must be byte-identical, and merging would make that
    impossible to reason about. It is placed after any leading system message so
    the base prompt stays first.
    """
    reminder = SystemMessage(content=block)
    index = 1 if messages and isinstance(messages[0], SystemMessage) else 0
    return [*messages[:index], reminder, *messages[index:]]
