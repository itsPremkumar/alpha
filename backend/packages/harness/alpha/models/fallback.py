"""Provider failover for chat models.

A :class:`FallbackChatModel` wraps an ordered chain of already-built chat
models and retries a call on the next member when the current one fails with
a *retryable* error (rate limit 429, credit/quota exhaustion, server 5xx,
timeout/connection failure). Deterministic failures (400/401/403/404,
validation, content blocks) raise immediately without touching the rest of
the chain.

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
  (thread-local, observability only), and every switch is recorded as a
  structured :class:`FailoverEvent` (also thread-local, plus an optional
  ``on_failover`` callback) instead of only being logged.
* Credit exhaustion is a *typed* condition at the provider boundary:
  :class:`CreditExhaustedError` normalizes the many provider spellings
  (HTTP 402, ``insufficient_quota``, "out of credits", ...), is treated as
  retryable so the chain fails over instead of surfacing a generic failure,
  and marks :class:`ModelFallbackExhaustedError` with
  ``budget_status="CREDIT_EXHAUSTED"`` when the whole chain ran out of credit.
* Token/cost accounting is wired here, on the real egress path: after a
  member serves a call, the ``usage`` the provider reported is charged to the
  cost governor (:func:`alpha.models.cost_governor.record_token_usage`) for
  the member that actually served it, attributed to the model that served it.
  Accounting never fails an answer — a tripped budget breaker is recorded on
  the failover state, not raised.
* Error messages and logs carry model *names* and error *classes* only, never
  exception text or config values, so secrets cannot leak through failover.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from langchain.chat_models import BaseChatModel
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable

logger = logging.getLogger(__name__)

# Reason codes recorded on a FailoverEvent. Stable strings, not exception text.
REASON_CREDIT_EXHAUSTED = "credit_exhausted"
REASON_RATE_LIMITED = "rate_limited"
REASON_SERVER_ERROR = "server_error"
REASON_TRANSPORT_ERROR = "transport_error"
REASON_EMPTY_STREAM = "empty_stream"
REASON_BUDGET_EXHAUSTED = "budget_exhausted"

#: ``budget_status`` stamped on an exhausted chain that ran out of credit.
#: Mirrors the ``budget_status="BUDGET_EXHAUSTED"`` contract the token budget
#: middleware already stamps, so one reader can classify both.
CREDIT_EXHAUSTED_STATUS = "CREDIT_EXHAUSTED"

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

# Provider error ``code``/``type`` values that mean "this account has no money
# left". Matched case-insensitively against the exception's ``code`` /
# ``error_code`` attributes and the OpenAI-shaped ``body.error.code``/``type``.
_CREDIT_EXHAUSTION_CODES = frozenset(
    {
        "insufficient_quota",
        "insufficient_credit",
        "insufficient_credits",
        "insufficient_balance",
        "quota_exceeded",
        "quota_exhausted",
        "exceeded_quota",
        "credit_exhausted",
        "credits_exhausted",
        "billing_hard_limit_reached",
        "payment_required",
        "balance_not_enough",
    }
)

# Message fragments that mean the same thing. Deliberately narrower than the
# middleware's generic quota vocabulary: "quota" alone is a *rate* limit at
# several providers, and mislabelling a 429 as credit exhaustion would send
# operators chasing billing for a throttling problem.
_CREDIT_EXHAUSTION_PATTERNS = (
    "insufficient_quota",
    "insufficient credit",
    "insufficient credits",
    "insufficient balance",
    "out of credits",
    "no credits remaining",
    "credit balance is too low",
    "credits exhausted",
    "exceeded your current quota",
    "quota exhausted",
    "billing hard limit",
    "hard limit reached",
    "payment required",
    "please add credits",
    "add credits",
    "top up your balance",
    "余额不足",
    "额度不足",
    "额度已用尽",
    "配额不足",
    "积分不足",
    "超出限额",
    "欠费",
    "请充值",
)

# Rate-limit phrasing suppresses *message-based* credit detection: several
# providers answer a throttle with "quota exceeded for quota metric
# RequestsPerMinute". An explicit error ``code`` still wins, because only the
# provider can say which one it meant.
_RATE_LIMIT_HINTS = (
    "rate limit",
    "rate-limit",
    "ratelimit",
    "too many requests",
    "requests per",
    "per minute",
    "quota metric",
    "tokens per",
    "concurrent",
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


def _error_text(exc: BaseException) -> str:
    """Lowercased provider message/code text, used for classification only.

    Never logged or surfaced: classification is the only permitted use, which
    is what keeps provider payloads (which can echo a key) out of failover
    diagnostics.
    """
    parts: list[str] = []
    for attr in ("message", "code", "error_code", "type"):
        value = getattr(exc, attr, None)
        if isinstance(value, str) and value:
            parts.append(value)
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            for key in ("message", "code", "type"):
                value = error.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)
    detail = str(exc).strip()
    if detail:
        parts.append(detail)
    return " ".join(parts).lower()


def is_credit_exhausted_error(exc: BaseException) -> bool:
    """Whether a provider error means *out of credit / quota*, not *slow down*.

    Credit exhaustion is a routing problem, not a code problem: another
    provider (or the keyless free router) can serve the same prompt, so it must
    drive failover instead of surfacing as a generic failure. Detected from
    HTTP 402, from a provider error ``code``/``type``, or from message text —
    with rate-limit phrasing suppressing the text path so a throttle is not
    mislabelled as a billing outage.
    """
    if isinstance(exc, CreditExhaustedError):
        return True
    if _error_status_code(exc) == 402:
        return True
    text = _error_text(exc)
    if any(code in text for code in _CREDIT_EXHAUSTION_CODES):
        return True
    if any(hint in text for hint in _RATE_LIMIT_HINTS):
        return False
    return any(pattern in text for pattern in _CREDIT_EXHAUSTION_PATTERNS)


def normalize_provider_error(exc: BaseException) -> BaseException:
    """Type a credit-exhausted provider error at the model boundary.

    Returns :class:`CreditExhaustedError` when *exc* is credit exhaustion and
    is not already typed, otherwise *exc* unchanged. The original exception is
    kept as ``__cause__``-equivalent (``original``) so nothing is lost, while
    every downstream consumer — the failover loop, an error-handling
    middleware, an operator reading logs — gets one class and one reason code
    to switch on instead of pattern-matching provider prose.
    """
    if isinstance(exc, CreditExhaustedError) or not is_credit_exhausted_error(exc):
        return exc
    status = _error_status_code(exc)
    return CreditExhaustedError(model_name=type(exc).__name__, status=status, original=exc)


#: ``langchain_core`` raises this when a member's ``_stream`` yields no chunks
#: (a provider answered, but with nothing in it). It is a member failure, not a
#: deterministic one, so it must move the chain forward like the internal
#: empty-stream sentinel does - otherwise a silent provider dead-ends the call.
_EMPTY_RESPONSE_MARKERS = ("no generation chunks were returned",)


def _is_empty_response_error(exc: BaseException) -> bool:
    text = _error_text(exc)
    return any(marker in text for marker in _EMPTY_RESPONSE_MARKERS)


def _failover_reason(exc: BaseException) -> str:
    """Secret-free reason code for a switch, stable across providers."""
    if is_credit_exhausted_error(exc):
        return REASON_CREDIT_EXHAUSTED
    if _is_empty_response_error(exc):
        return REASON_EMPTY_STREAM
    status = _error_status_code(exc)
    if status == 429:
        return REASON_RATE_LIMITED
    if isinstance(status, int) and status >= 500:
        return REASON_SERVER_ERROR
    return REASON_TRANSPORT_ERROR


def is_retryable_llm_error(exc: BaseException) -> bool:
    """Whether a failed LLM call is worth retrying on another provider.

    Retryable: credit/quota exhaustion (another provider still has money), an
    empty/short member response, HTTP 429 and 5xx (by status, any SDK), plus
    timeout/connection failures (by type name or builtin
    ``TimeoutError``/``ConnectionError``). Everything else — auth, not-found,
    bad request, validation, content filtering — is deterministic and must
    surface immediately.
    """
    if is_credit_exhausted_error(exc):
        return True
    if _is_empty_response_error(exc):
        return True
    status = _error_status_code(exc)
    if status == 429:
        return True
    if isinstance(status, int) and status >= 500:
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return type(exc).__name__ in _RETRYABLE_ERROR_NAMES


class CreditExhaustedError(RuntimeError):
    """A provider rejected the call because the account is out of credit.

    The provider-boundary error type that makes credit exhaustion a real,
    routed condition: it is retryable on another provider, it is the only
    credit-related class the failover loop raises internally, and it carries
    the normalized reason code ``credit_exhausted``.

    Never carries the provider's message — only its class name and HTTP
    status, so a payload that echoes a key cannot travel with the error.
    """

    reason = REASON_CREDIT_EXHAUSTED

    def __init__(self, model_name: str, status: int | None = None, original: BaseException | None = None) -> None:
        self.model_name = model_name
        self.status = status
        self.original = original
        detail = f" (status={status})" if status is not None else ""
        super().__init__(f"Provider '{model_name}' reported credit exhaustion{detail}")


@dataclass(frozen=True)
class FailoverEvent:
    """One recorded provider switch.

    Provider switches used to exist only as log lines, which made "why did
    this answer come from a different model?" unanswerable outside a log
    tail. This is the machine-readable form: model names, a stable reason code
    and a secret-free error label, with no message or config value.
    """

    from_model: str
    to_model: str | None
    reason: str
    error_label: str
    credit_exhausted: bool = False
    budget_breaker: str | None = None


class ModelFallbackExhaustedError(RuntimeError):
    """Raised when every model in a fallback chain failed.

    Carries the chain ``attempts`` as ``(model_name, error_label)`` pairs.
    Labels contain error classes/statuses only — never messages or secrets.
    ``credit_exhausted`` is True when at least one member failed for lack of
    credit, and ``budget_status`` is then ``"CREDIT_EXHAUSTED"`` so a caller
    can react to a money problem without re-parsing provider prose.
    """

    def __init__(
        self,
        requested: str,
        attempts: list[tuple[str, str]],
        *,
        credit_exhausted_models: list[str] | None = None,
    ) -> None:
        self.requested = requested
        self.attempts = list(attempts)
        self.credit_exhausted_models = list(credit_exhausted_models or [])
        self.credit_exhausted = bool(self.credit_exhausted_models)
        self.budget_status = CREDIT_EXHAUSTED_STATUS if self.credit_exhausted else None
        detail = " -> ".join(f"{name}({label})" for name, label in self.attempts)
        suffix = f" [budget_status={self.budget_status}]" if self.budget_status else ""
        super().__init__(f"All {len(self.attempts)} model(s) in the fallback chain for '{requested}' failed: {detail}{suffix}")


class FallbackChatModel(BaseChatModel):
    """A chat model that fails over across an ordered member chain.

    Members are built chat models or, after :meth:`bind_tools`, their bound
    variants — anything supporting the ``Runnable`` ``invoke``/``stream``
    contract. Bound tools travel inside each member binding (exactly like a
    directly bound provider client); per-call ``kwargs`` are intentionally
    NOT forwarded to members, because an unknown kwarg would corrupt the
    provider payload. Per-call sampling overrides belong in
    ``create_chat_model(model_overrides=...)`` at build time.

    ``on_failover`` is an optional callback invoked with each
    :class:`FailoverEvent`; a callback that raises is logged and ignored, so
    observability can never break a call.
    """

    #: Failover events retained per thread. Bounded so a long-lived wrapper on
    #: a busy thread cannot grow without limit; the last N is what an operator
    #: needs to explain the answer that was just produced.
    _EVENT_HISTORY = 20

    def __init__(self, instances: list[Runnable], model_names: list[str], *, on_failover: Any = None) -> None:
        if not instances or len(instances) != len(model_names):
            raise ValueError("FallbackChatModel needs a non-empty 1:1 instances/names pair")
        super().__init__()
        # Plain attributes via object.__setattr__: pydantic must not see these
        # as model fields (members are arbitrary objects, not serializable).
        object.__setattr__(self, "_instances", list(instances))
        object.__setattr__(self, "_model_names", list(model_names))
        object.__setattr__(self, "_thread_state", threading.local())
        object.__setattr__(self, "_on_failover", on_failover)

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

    def get_last_failover_events(self) -> list[FailoverEvent]:
        """Provider switches recorded on this thread, oldest first.

        The machine-readable counterpart to the failover log lines: a caller
        can explain a served answer ("served by B, A was out of credit")
        without tailing logs.
        """
        events = getattr(self._thread_state, "events", None)
        return list(events) if events is not None else []

    def get_last_credit_exhausted_models(self) -> list[str]:
        """Chain members that failed for lack of credit on this thread."""
        return [event.from_model for event in self.get_last_failover_events() if event.credit_exhausted]

    def _note_event(self, event: FailoverEvent) -> None:
        events = getattr(self._thread_state, "events", None)
        if events is None:
            events = self._thread_state.events = deque(maxlen=self._EVENT_HISTORY)
        events.append(event)
        callback = getattr(self, "_on_failover", None)
        if callback is None:
            return
        try:
            callback(event)
        except Exception:  # observability must never break a call
            logger.warning("on_failover callback raised for a provider switch", exc_info=True)

    def _note_switch(self, name: str, exc: BaseException, next_name: str | None, attempted: int) -> FailoverEvent:
        """Record a provider switch and log it (secret-free, as before)."""
        reason = _failover_reason(exc)
        event = FailoverEvent(
            from_model=name,
            to_model=next_name,
            reason=reason,
            error_label=_error_label(exc),
            credit_exhausted=reason == REASON_CREDIT_EXHAUSTED,
        )
        self._note_event(event)
        logger.warning(
            "Model '%s' failed with retryable error (%s); failing over to '%s' (%d/%d attempted)",
            name,
            event.error_label,
            next_name or "<chain exhausted>",
            attempted,
            len(self._instances),
        )
        return event

    def _note_budget_event(self, name: str, breaker: str) -> None:
        """Record a post-answer budget stop; the answer itself still ships."""
        self._note_event(
            FailoverEvent(
                from_model=name,
                to_model=None,
                reason=REASON_BUDGET_EXHAUSTED,
                error_label=breaker,
                budget_breaker=breaker,
            )
        )

    def _account_usage(self, name: str, input_tokens: int, output_tokens: int) -> None:
        """Charge a served call's real provider usage to the cost governor.

        This is the production caller that makes cost accounting a live path
        rather than a library nobody calls. Non-raising by construction —
        accounting failure is logged, never propagated, because the answer was
        already produced and the money was already spent. A tripped breaker
        becomes a recorded :class:`FailoverEvent` (``budget_exhausted``) so the
        stop signal is observable without failing the call.
        """
        if input_tokens <= 0 and output_tokens <= 0:
            return
        try:
            from alpha.models.cost_governor import record_token_usage

            result = record_token_usage(name, input_tokens, output_tokens)
            if result.recorded:
                logger.debug(
                    "Recorded usage for model '%s': %d in / %d out ($%.6f, project '%s')",
                    name,
                    result.input_tokens,
                    result.output_tokens,
                    result.cost_usd,
                    result.project_id,
                )
            if result.breaker is not None:
                self._note_budget_event(name, result.breaker.blocked_by)
        except Exception:  # pragma: no cover - record_token_usage is non-raising
            logger.warning("Cost accounting call failed for model '%s'", name, exc_info=True)

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
        bound_wrapper = FallbackChatModel(
            instances=bound_members,
            model_names=list(self._model_names),
            on_failover=getattr(self, "_on_failover", None),
        )
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

    def _next_member(self, index: int) -> str | None:
        return self._model_names[index + 1] if index + 1 < len(self._model_names) else None

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
        credit_exhausted: list[str] = []
        for index, (name, instance) in enumerate(zip(self._model_names, self._instances)):
            try:
                message = self._attempt_invoke(messages, instance, name, stop)
                if not isinstance(message, AIMessage):
                    message = AIMessage(content=message.content if hasattr(message, "content") else str(message))
                tokens_in, tokens_out = _usage_counts(message)
                self._account_usage(name, tokens_in, tokens_out)
                return ChatResult(generations=[ChatGeneration(message=message)])
            except Exception as exc:  # noqa: BLE001 - classified below; BaseExceptions propagate
                if not is_retryable_llm_error(exc):
                    raise
                event = self._note_switch(name, exc, self._next_member(index), len(attempts) + 1)
                if event.credit_exhausted:
                    credit_exhausted.append(name)
                attempts.append((name, event.error_label))
        raise ModelFallbackExhaustedError(self._model_names[0], attempts, credit_exhausted_models=credit_exhausted)

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
        credit_exhausted: list[str] = []
        for index, (name, instance) in enumerate(zip(self._model_names, self._instances)):
            tokens_in = 0
            tokens_out = 0
            try:
                yielded_any = False
                for chunk in instance.stream(messages, stop=stop):
                    yielded_any = True
                    self._note_effective_model(name)
                    # Streaming usage arrives on the final chunk (and only when
                    # the client asked for it), so the running maximum is the
                    # safe accumulator: it equals the final total for the
                    # normal case and cannot double-count a provider that
                    # repeats cumulative totals.
                    chunk_in, chunk_out = _usage_counts(chunk)
                    tokens_in = max(tokens_in, chunk_in)
                    tokens_out = max(tokens_out, chunk_out)
                    # _stream contracts on ChatGenerationChunk; member streams
                    # yield bare message chunks.
                    yield chunk if isinstance(chunk, ChatGenerationChunk) else ChatGenerationChunk(message=chunk)
                if not yielded_any:
                    # Empty stream counts as a failure of this member: fall
                    # through to the next one rather than ending silently.
                    raise _EmptyStreamError(name)
                self._account_usage(name, tokens_in, tokens_out)
                return
            except _EmptyStreamError as exc:
                attempts.append((exc.model_name, "EmptyStream"))
                self._note_event(
                    FailoverEvent(
                        from_model=exc.model_name,
                        to_model=self._next_member(index),
                        reason=REASON_EMPTY_STREAM,
                        error_label="EmptyStream",
                    )
                )
                logger.warning("Model '%s' returned an empty stream; failing over", exc.model_name)
            except Exception as exc:  # noqa: BLE001 - classified below; BaseExceptions propagate
                if not is_retryable_llm_error(exc):
                    raise
                event = self._note_switch(name, exc, self._next_member(index), len(attempts) + 1)
                if event.credit_exhausted:
                    credit_exhausted.append(name)
                attempts.append((name, event.error_label))
        raise ModelFallbackExhaustedError(self._model_names[0], attempts, credit_exhausted_models=credit_exhausted)


def _usage_counts(message: Any) -> tuple[int, int]:
    """Best-effort ``(input_tokens, output_tokens)`` a provider actually reported.

    Reads LangChain's normalized ``usage_metadata`` first (the field every
    provider adapter is supposed to fill from the real payload) and falls back
    to the OpenAI-shaped ``response_metadata["usage"]``. A ``ChatGeneration`` /
    ``ChatGenerationChunk`` wrapper is unwrapped, so a caller can pass either
    shape. Returns ``(0, 0)`` when the provider reported nothing: an invented
    count would corrupt cost accounting, so absence is reported as absence.
    """
    inner = getattr(message, "message", None)
    if inner is not None and not hasattr(message, "usage_metadata"):
        message = inner
    for source in (getattr(message, "usage_metadata", None), _response_usage(message)):
        if not isinstance(source, dict):
            continue
        tokens_in = _coerce_tokens(source.get("input_tokens", source.get("prompt_tokens")))
        tokens_out = _coerce_tokens(source.get("output_tokens", source.get("completion_tokens")))
        if tokens_in or tokens_out:
            return tokens_in, tokens_out
    return (0, 0)


def _response_usage(message: Any) -> Any:
    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        return response_metadata.get("usage")
    return None


def _coerce_tokens(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return 0


class _EmptyStreamError(Exception):
    """Internal sentinel: a member streamed zero chunks."""

    def __init__(self, model_name: str) -> None:
        super().__init__(model_name)
        self.model_name = model_name
