"""Provider failover for chat models.

A :class:`FallbackChatModel` wraps an ordered chain of already-built chat
models and retries a call on the next member when the current one fails with
a *retryable* error (rate limit 429, server 5xx, timeout/connection failure).
Deterministic failures (400/401/403/404, validation, content blocks) raise
immediately without touching the rest of the chain.

Design notes:

* It IS a ``BaseChatModel``, so ``bind_tools``, ``with_structured_output``,
  streaming, and the middleware chain keep working untouched — every call
  routes through :meth:`_generate` / :meth:`_stream`, which own the loop.
* Members are always fully built single models (chains flatten transitively
  in the factory); a member never contains another wrapper.
* Only :class:`Exception` is caught — ``KeyboardInterrupt``/``SystemExit``
  always propagate.
* Failover is per call, never mid-stream: if a member dies mid-stream the
  next member restarts the response. Consumers may observe a partial prefix
  followed by a complete response.
* The serving member is observable via :meth:`get_last_effective_model`
  (thread-local, observability only). Token/cost attribution still follows
  the requesting model name — per-effective-model accounting is a follow-up.
* Error messages and logs carry model *names* and error *classes* only, never
  exception text or config values, so secrets cannot leak through failover.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from typing import Any

from langchain.chat_models import BaseChatModel
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable

logger = logging.getLogger(__name__)

# Exception class names treated as transport-level failures regardless of the
# SDK that raised them (matched by name so no provider SDK import is needed).
_RETRYABLE_ERROR_NAMES = frozenset(
    {
        "APITimeoutError",
        "APIConnectionError",
        "TimeoutError",
        "TimeoutException",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "RemoteProtocolError",
        "StreamChunkTimeoutError",
        "StreamClosedError",
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
        "BadGatewayError",
        "GatewayTimeoutError",
        "OverloadedError",
    }
)


def _error_status_code(exc: BaseException) -> int | None:
    """Best-effort HTTP status extraction across provider SDK shapes."""
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
        getattr(exc, "status", None),
    ):
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int):
            return candidate
    return None


def _error_label(exc: BaseException) -> str:
    """Short secret-free label for logs and exhausted-chain errors."""
    status = _error_status_code(exc)
    label = type(exc).__name__
    return f"{label}(status={status})" if status is not None else label


def is_retryable_llm_error(exc: BaseException) -> bool:
    """Whether a failed LLM call is worth retrying on another provider.

    Retryable: HTTP 429 and 5xx (by status, any SDK), plus timeout/connection
    failures (by type name or builtin ``TimeoutError``/``ConnectionError``).
    Everything else — auth, not-found, bad request, validation, content
    filtering — is deterministic and must surface immediately.
    """
    status = _error_status_code(exc)
    if status == 429:
        return True
    if isinstance(status, int) and status >= 500:
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return type(exc).__name__ in _RETRYABLE_ERROR_NAMES


class ModelFallbackExhaustedError(RuntimeError):
    """Raised when every model in a fallback chain failed.

    Carries the chain ``attempts`` as ``(model_name, error_label)`` pairs.
    Labels contain error classes/statuses only — never messages or secrets.
    """

    def __init__(self, requested: str, attempts: list[tuple[str, str]]) -> None:
        self.requested = requested
        self.attempts = list(attempts)
        detail = " -> ".join(f"{name}({label})" for name, label in self.attempts)
        super().__init__(f"All {len(self.attempts)} model(s) in the fallback chain for '{requested}' failed: {detail}")


class FallbackChatModel(BaseChatModel):
    """A chat model that fails over across an ordered member chain.

    Members are built chat models or, after :meth:`bind_tools`, their bound
    variants — anything supporting the ``Runnable`` ``invoke``/``stream``
    contract. Bound tools travel inside each member binding (exactly like a
    directly bound provider client); per-call ``kwargs`` are intentionally
    NOT forwarded to members, because an unknown kwarg would corrupt the
    provider payload. Per-call sampling overrides belong in
    ``create_chat_model(model_overrides=...)`` at build time.
    """

    def __init__(self, instances: list[Runnable], model_names: list[str]) -> None:
        if not instances or len(instances) != len(model_names):
            raise ValueError("FallbackChatModel needs a non-empty 1:1 instances/names pair")
        super().__init__()
        # Plain attributes via object.__setattr__: pydantic must not see these
        # as model fields (members are arbitrary objects, not serializable).
        object.__setattr__(self, "_instances", list(instances))
        object.__setattr__(self, "_model_names", list(model_names))
        object.__setattr__(self, "_thread_state", threading.local())

    @property
    def _llm_type(self) -> str:
        return "fallback-chat-model"

    @property
    def fallback_model_names(self) -> list[str]:
        """Ordered chain member names (primary first)."""
        return list(self._model_names)

    def get_last_effective_model(self) -> str | None:
        """Name of the member that served the most recent call on this thread."""
        return getattr(self._thread_state, "effective_model", None)

    def _note_effective_model(self, name: str) -> None:
        self._thread_state.effective_model = name

    def bind_tools(self, tools, tool_choice=None, **kwargs):
        """Bind tools on every member, preserving failover.

        Mirrors ecosystem practice (each runner binds independently): the
        returned wrapper serves bound members in the same order, so a failover
        still offers the model its tools. ``tool_choice``/extra bind kwargs
        apply to every member identically.
        """
        bound_members = []
        for member in self._instances:
            bind = getattr(member, "bind_tools", None)
            if bind is None:
                raise NotImplementedError(f"Fallback member '{type(member).__name__}' does not implement bind_tools")
            bound_members.append(bind(tools, tool_choice=tool_choice, **kwargs))
        bound_wrapper = FallbackChatModel(instances=bound_members, model_names=list(self._model_names))
        if isinstance(getattr(self, "profile", None), dict):
            bound_wrapper.profile = dict(self.profile)
        return bound_wrapper

    def _attempt_invoke(
        self,
        messages: list[BaseMessage],
        attempt: Runnable,
        name: str,
        stop: list[str] | None,
    ) -> BaseMessage:
        # (messages, stop) is the shared Runnable invoke shape for bare models
        # and bound variants alike; bound tools travel inside the member.
        message = attempt.invoke(messages, stop=stop)
        self._note_effective_model(name)
        return message

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager  # attempts run as nested member invocations (own runs)
        del kwargs  # never forwarded: unknown kwargs corrupt provider payloads (see class docstring)
        attempts: list[tuple[str, str]] = []
        for name, instance in zip(self._model_names, self._instances):
            try:
                message = self._attempt_invoke(messages, instance, name, stop)
                if not isinstance(message, AIMessage):
                    message = AIMessage(content=message.content if hasattr(message, "content") else str(message))
                return ChatResult(generations=[ChatGeneration(message=message)])
            except Exception as exc:  # noqa: BLE001 - classified below; BaseExceptions propagate
                if not is_retryable_llm_error(exc):
                    raise
                attempts.append((name, _error_label(exc)))
                logger.warning(
                    "Model '%s' failed with retryable error; failing over (%d/%d attempted)",
                    name,
                    len(attempts),
                    len(self._instances),
                )
        raise ModelFallbackExhaustedError(self._model_names[0], attempts)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        del run_manager  # same nesting rationale as _generate
        del kwargs  # see _generate: bound tools travel inside the member
        attempts: list[tuple[str, str]] = []
        for name, instance in zip(self._model_names, self._instances):
            try:
                yielded_any = False
                for chunk in instance.stream(messages, stop=stop):
                    yielded_any = True
                    self._note_effective_model(name)
                    # _stream contracts on ChatGenerationChunk; member streams
                    # yield bare message chunks.
                    yield chunk if isinstance(chunk, ChatGenerationChunk) else ChatGenerationChunk(message=chunk)
                if not yielded_any:
                    # Empty stream counts as a failure of this member: fall
                    # through to the next one rather than ending silently.
                    raise _EmptyStreamError(name)
                return
            except _EmptyStreamError as exc:
                attempts.append((exc.model_name, "EmptyStream"))
                logger.warning("Model '%s' returned an empty stream; failing over", exc.model_name)
            except Exception as exc:  # noqa: BLE001 - classified below; BaseExceptions propagate
                if not is_retryable_llm_error(exc):
                    raise
                attempts.append((name, _error_label(exc)))
                logger.warning(
                    "Model '%s' failed with retryable error; failing over (%d/%d attempted)",
                    name,
                    len(attempts),
                    len(self._instances),
                )
        raise ModelFallbackExhaustedError(self._model_names[0], attempts)


class _EmptyStreamError(Exception):
    """Internal sentinel: a member streamed zero chunks."""

    def __init__(self, model_name: str) -> None:
        super().__init__(model_name)
        self.model_name = model_name
