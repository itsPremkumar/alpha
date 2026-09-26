"""Process-wide, provider-keyed circuit breaker registry for LLM calls.

Why this module exists
----------------------
``LLMErrorHandlingMiddleware`` used to keep its circuit breaker *on the
instance*. The agent stack - middleware included - is rebuilt for every run
(``runtime/runs/worker.py`` assembles it through ``run_assembly``), so that
state died with the run: a provider that failed its threshold in run 1 started
run 2 from a clean slate, and the breaker never protected production traffic.
The state was also not keyed per provider, so one provider's outage could not
be told apart from another's.

This module fixes both defects:

* **Scope.** One module-level registry that lives for the lifetime of the
  process - the same scope the process-wide LLM call limiter
  (``_ProcessWideLimiter`` in ``llm_error_handling_middleware``) already uses.
  Every middleware instance, every run, every event loop (lead agent,
  subagent loops, ``asyncio.run`` tests) and the sync graph path share it, so
  breaker state survives the per-run stack rebuild.
* **Key.** ``BreakerKey`` is a *tuple*, never a joined string - see
  :func:`breaker_key_for` for how each component is derived and
  :func:`describe_breaker_key` for the log/render form.

The key: ``(provider, endpoint, model_ids)``
---------------------------------------------
``BreakerKey = tuple[str, str, tuple[str, ...]]`` built from the chat model
the call actually runs on (``request.model``):

``provider``
    ``f"{module}.{qualname}"`` of the chat-model class, after unwrapping any
    ``.bound`` runnable wrapper the middleware stack layered on top. This is
    the provider *client* (``langchain_openai...ChatOpenAI`` vs
    ``langchain_anthropic...ChatAnthropic`` vs Alpha's own
    ``CodexChatModel`` / ``MindIEChatModel`` wrappers).
``endpoint``
    First non-empty connection endpoint among ``openai_api_base``,
    ``anthropic_api_url``, ``base_url``, ``api_base``, ``api_url``,
    ``root_url``, ``endpoint``, ``url``. This is what keeps two clients of the
    *same class* pointed at *different gateways* (e.g. OpenAI vs. a
    self-hosted OpenAI-compatible router, both ``ChatOpenAI`` with the same
    ``model_name``) in separate breakers.
``model_ids``
    Tuple of configured model identifiers: ``fallback_model_names`` for
    ``FallbackChatModel`` (the whole chain is one logical model), otherwise the
    first non-empty of ``model_name`` / ``model`` / ``deployment_name`` /
    ``model_id``.

Collision resistance is structural: the registry key is a tuple and Python
tuple equality is component-wise, so ``("a|b", "c", ...)`` and ``("a", "b|c",
...)`` can never alias each other the way a ``"provider|model"`` string key
would. Distinct providers therefore always get distinct breakers.

Requests that carry no model (direct unit-test calls, or any future caller
that hands the middleware a bare request) fall back to
:func:`default_breaker_key` - derived from the configured default model
(``models[0]``) when the ``AppConfig`` declares one, else
:data:`FALLBACK_BREAKER_KEY`. The middleware pins that fallback as its
construction-time key and adopts the request-derived key of its most recent
call, so its diagnostic accessors (``_circuit_state`` and friends) always
point at the breaker the last call actually used.

Bounded by construction
-----------------------
The registry never grows without limit: it is an LRU capped at
:data:`MAX_REGISTERED_BREAKERS` keys (least-recently-used keys are evicted on
insert) and can be cleared explicitly with :func:`reset_breakers` (test
isolation / configuration reload). A normal configuration has a handful of
models, so eviction only ever bites callers that mint throwaway keys.

Thresholds and recovery timeouts stay *configuration*, not state: they are
passed into :meth:`CircuitBreaker.record_failure` by the middleware that owns
the ``AppConfig`` snapshot, exactly as before.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

# Registry key. Tuple on purpose - component-wise equality cannot collide
# across distinct providers (see module docstring).
BreakerKey = tuple[str, str, tuple[str, ...]]

#: Used when neither the request nor the ``AppConfig`` can name a model.
FALLBACK_BREAKER_KEY: BreakerKey = ("unattributed-provider", "", ("unattributed-model",))

#: Hard bound on distinct keys held at once; least-recently-used keys go first.
MAX_REGISTERED_BREAKERS = 64

# ``.bound`` unwrapping depth (mirrors assembly_descriptor.describe_model_identity):
# the stack may layer a few runnable bindings, but an unbounded chase through a
# self-referential ``bound`` is worse than stopping at a deep wrapper.
_BOUND_UNWRAP_LIMIT = 8

# Connection endpoints, most specific first. Order matters only for
# determinism: the first non-empty one wins.
_ENDPOINT_ATTRS = (
    "openai_api_base",
    "anthropic_api_url",
    "base_url",
    "api_base",
    "api_url",
    "root_url",
    "endpoint",
    "url",
)

# Configured model identifiers, most explicit first.
_MODEL_ID_ATTRS = (
    "model_name",
    "model",
    "deployment_name",
    "model_id",
)


class CircuitBreaker:
    """Mutable breaker state for exactly one :data:`BreakerKey`.

    One instance of this class is shared by every middleware instance serving
    that provider/model for the lifetime of the process, so the transitions
    are real, observable state: ``record_success`` resets, ``record_failure``
    opens at the caller's threshold, ``check`` moves ``open`` ->
    ``half_open`` once the recovery timeout has elapsed and admits exactly one
    probe at a time.

    The per-breaker ``RLock`` makes each transition atomic; callers on
    different loops/threads (lead agent, subagent loops, the sync graph path)
    contend on the same lock. Thresholds are *parameters* of
    :meth:`record_failure`, not stored here, because they belong to the
    ``AppConfig`` snapshot a middleware was built with - the state must be
    shared even when two instances were configured differently.
    """

    __slots__ = ("_key", "_lock", "_failure_count", "_open_until", "_state", "_probe_in_flight", "_probe_token")

    def __init__(self, key: BreakerKey) -> None:
        self._key = key
        self._lock = threading.RLock()
        self._failure_count = 0
        self._open_until = 0.0
        self._state = "closed"
        self._probe_in_flight = False
        self._probe_token: object | None = None

    @property
    def key(self) -> BreakerKey:
        return self._key

    # --- state accessors -------------------------------------------------
    # Lock-guarded so the middleware's diagnostic/compatibility accessors
    # (``_circuit_state`` & co.) observe the same values the transitions do.

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    @failure_count.setter
    def failure_count(self, value: int) -> None:
        with self._lock:
            self._failure_count = int(value)

    @property
    def open_until(self) -> float:
        with self._lock:
            return self._open_until

    @open_until.setter
    def open_until(self, value: float) -> None:
        with self._lock:
            self._open_until = float(value)

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @state.setter
    def state(self, value: str) -> None:
        with self._lock:
            self._state = value

    @property
    def probe_in_flight(self) -> bool:
        with self._lock:
            return self._probe_in_flight

    @probe_in_flight.setter
    def probe_in_flight(self, value: bool) -> None:
        with self._lock:
            self._probe_in_flight = bool(value)

    @property
    def probe_token(self) -> object | None:
        with self._lock:
            return self._probe_token

    @probe_token.setter
    def probe_token(self, value: object | None) -> None:
        with self._lock:
            self._probe_token = value

    def snapshot(self) -> dict[str, Any]:
        """One consistent read of the whole state (assertions / diagnostics)."""
        with self._lock:
            return {
                "key": self._key,
                "state": self._state,
                "failure_count": self._failure_count,
                "open_until": self._open_until,
                "probe_in_flight": self._probe_in_flight,
                "probe_token": self._probe_token,
            }

    # --- transitions -----------------------------------------------------

    def check(self, *, probe_token: object | None = None) -> bool:
        """Return ``True`` when the circuit is OPEN (fast-fail), else ``False``.

        ``open`` + recovery timeout elapsed -> ``half_open``; in ``half_open``
        exactly one caller is admitted (its token recorded) while every other
        caller fast-fails until that probe resolves.
        """
        with self._lock:
            now = time.time()

            if self._state == "open":
                if now < self._open_until:
                    return True
                self._state = "half_open"
                self._probe_in_flight = False
                self._probe_token = None

            if self._state == "half_open":
                if self._probe_in_flight:
                    return True
                self._probe_in_flight = True
                self._probe_token = probe_token
                return False

            return False

    def record_success(self) -> None:
        """Reset on success: back to ``closed`` with a clean failure count."""
        with self._lock:
            if self._state != "closed" or self._failure_count > 0:
                logger.info("Circuit breaker reset (Closed). LLM service recovered. key=%s", describe_breaker_key(self._key))
            self._failure_count = 0
            self._open_until = 0.0
            self._state = "closed"
            self._probe_in_flight = False
            self._probe_token = None

    def record_failure(self, *, threshold: int, recovery_timeout_sec: float) -> None:
        """Count one failure; open once ``threshold`` is reached.

        A failure while ``half_open`` means the probe failed: re-open
        immediately for another full recovery window regardless of the
        threshold (the probe *was* the threshold check).
        """
        with self._lock:
            if self._state == "half_open":
                self._open_until = time.time() + recovery_timeout_sec
                self._state = "open"
                self._probe_in_flight = False
                self._probe_token = None
                logger.error(
                    "Circuit breaker probe failed (Open). Will probe again after %ds. key=%s",
                    recovery_timeout_sec,
                    describe_breaker_key(self._key),
                )
                return

            self._failure_count += 1
            if self._failure_count >= threshold:
                self._open_until = time.time() + recovery_timeout_sec
                if self._state != "open":
                    self._state = "open"
                    self._probe_in_flight = False
                    self._probe_token = None
                    logger.error(
                        "Circuit breaker tripped (Open). Threshold reached (%d). Will probe after %ds. key=%s",
                        threshold,
                        recovery_timeout_sec,
                        describe_breaker_key(self._key),
                    )

    def release_probe(self, *, probe_token: object | None = None) -> None:
        """Release the in-flight half-open probe without recording a failure.

        Used when something other than a classified success/failure consumed
        the probe (a ``GraphBubbleUp`` control-flow signal, a non-retriable
        error, a cancellation), so the circuit admits the next probe instead
        of fast-failing forever. A non-``None`` token only releases the probe
        it owns: an older call cannot free a later call's probe.
        """
        with self._lock:
            if probe_token is not None and self._probe_token is not probe_token:
                return
            if self._state == "half_open":
                self._probe_in_flight = False
                self._probe_token = None

    def reset(self) -> None:
        """Return this key's breaker to the pristine ``closed`` state."""
        with self._lock:
            self._failure_count = 0
            self._open_until = 0.0
            self._state = "closed"
            self._probe_in_flight = False
            self._probe_token = None


# --- registry --------------------------------------------------------------

# Insertion order doubles as LRU order: ``get_breaker`` moves a hit to the end,
# so ``popitem(last=False)`` always drops the least-recently-used key.
_REGISTRY: OrderedDict[BreakerKey, CircuitBreaker] = OrderedDict()
_REGISTRY_LOCK = threading.Lock()


def get_breaker(key: BreakerKey) -> CircuitBreaker:
    """Return (creating if needed) the process-wide breaker for ``key``.

    The returned object is the one every middleware instance serving this key
    shares. It may be evicted later if the registry overflows its bound; the
    next ``get_breaker`` then re-creates a fresh (closed) breaker, which is the
    documented trade-off for keeping the key set bounded.
    """
    with _REGISTRY_LOCK:
        breaker = _REGISTRY.get(key)
        if breaker is None:
            breaker = CircuitBreaker(key)
            _REGISTRY[key] = breaker
            while len(_REGISTRY) > MAX_REGISTERED_BREAKERS:
                evicted, _ = _REGISTRY.popitem(last=False)
                logger.debug("LLM breaker registry evicted LRU key %s (bound=%d)", describe_breaker_key(evicted), MAX_REGISTERED_BREAKERS)
        else:
            _REGISTRY.move_to_end(key)
        return breaker


def peek_breaker(key: BreakerKey) -> CircuitBreaker | None:
    """Return the breaker for ``key`` without creating or re-touching it."""
    with _REGISTRY_LOCK:
        return _REGISTRY.get(key)


def registered_keys() -> tuple[BreakerKey, ...]:
    """Keys currently held, least-recently-used first (test/introspection)."""
    with _REGISTRY_LOCK:
        return tuple(_REGISTRY.keys())


def registered_count() -> int:
    with _REGISTRY_LOCK:
        return len(_REGISTRY)


def reset_breakers(breakers: Iterable[BreakerKey] | None = None) -> None:
    """Explicit lifecycle: drop every breaker (or just ``breakers``).

    Used by test isolation and by configuration reloads; never needed in the
    steady state, where the registry is simply bounded and lives for the
    process.
    """
    with _REGISTRY_LOCK:
        if breakers is None:
            _REGISTRY.clear()
            return
        for key in breakers:
            _REGISTRY.pop(key, None)


# --- key derivation --------------------------------------------------------


def default_breaker_key(app_config: Any) -> BreakerKey:
    """Construction-time fallback key for a middleware built from ``app_config``.

    Derived from the configured default model (``models[0]``): the provider
    profile name when the entry declares one, the unique config entry name as
    the model id. Requests that carry a model override this per call (see
    :func:`breaker_key_for`); requests that carry nothing land here, so two
    middlewares built from the same config still share one breaker.
    """
    models = getattr(app_config, "models", None) or []
    if not models:
        return FALLBACK_BREAKER_KEY
    first = models[0]
    provider = getattr(first, "provider", None)
    name = getattr(first, "name", None)
    return (
        str(provider) if provider else "configured-default",
        "",
        (str(name),) if name else ("default-model",),
    )


def breaker_key_for(request: Any, *, fallback: BreakerKey | None = None) -> BreakerKey:
    """Derive the provider/model key for one model call.

    ``request.model`` is the chat model the call actually runs on; anything
    else (no ``model`` attribute, a ``None`` model) falls back to
    ``fallback`` and finally :data:`FALLBACK_BREAKER_KEY`.

    The provider component identifies the *client class* (after unwrapping
    ``.bound``), the endpoint component separates same-class clients pointed at
    different gateways, and ``model_ids`` carries the configured model name(s).
    """
    model = getattr(request, "model", None)
    if model is None:
        return fallback or FALLBACK_BREAKER_KEY
    if isinstance(model, str):
        # Bare model name (string-valued ``model``): provider unknown, name known.
        return ("builtins.str", "", (model,))
    resolved = _unwrap_bound(model)
    provider = f"{type(resolved).__module__}.{type(resolved).__qualname__}"
    endpoint = _endpoint_of(resolved) or _endpoint_of(model)
    model_ids = _model_ids_of(resolved) or _model_ids_of(model)
    return (provider, endpoint, model_ids or ("unattributed-model",))


def describe_breaker_key(key: BreakerKey) -> str:
    """Human/log-friendly rendering of a key (never used as a key itself)."""
    provider, endpoint, model_ids = key
    models = ",".join(model_ids)
    if endpoint:
        return f"provider={provider} endpoint={endpoint} model={models}"
    return f"provider={provider} model={models}"


def _unwrap_bound(model: Any) -> Any:
    """Peel ``RunnableBinding``-style ``.bound`` layers off a chat model."""
    resolved = model
    seen: set[int] = set()
    for _ in range(_BOUND_UNWRAP_LIMIT):
        if id(resolved) in seen or not hasattr(resolved, "bound"):
            break
        seen.add(id(resolved))
        bound = getattr(resolved, "bound")
        if bound is None or bound is resolved:
            break
        resolved = bound
    return resolved


def _attr_text(obj: Any, names: tuple[str, ...]) -> str:
    """First non-empty string-ish value among ``names``, or ``""``.

    Attribute access is guarded: provider clients expose these as plain
    strings, ``SecretStr``s, ``AnyUrl``/``httpx.URL`` objects, or properties
    that may raise - none of which may break a model call.
    """
    for name in names:
        try:
            raw = getattr(obj, name, None)
        except Exception:
            continue  # identity probing must never raise into a model call
        if raw is None:
            continue
        get_secret = getattr(raw, "get_secret_value", None)
        if callable(get_secret):
            try:
                raw = get_secret()
            except Exception:
                continue
        try:
            text = str(raw).strip()
        except Exception:
            continue
        if text:
            return text
    return ""


def _endpoint_of(model: Any) -> str:
    return _attr_text(model, _ENDPOINT_ATTRS)


def _model_ids_of(model: Any) -> tuple[str, ...]:
    """Model identifiers: the fallback chain when present, else one name."""
    try:
        chain = getattr(model, "fallback_model_names", None)
    except Exception:
        chain = None  # identity probing must never raise into a model call
    if isinstance(chain, (list, tuple)) and chain:
        return tuple(str(member) for member in chain)
    single = _attr_text(model, _MODEL_ID_ATTRS)
    return (single,) if single else ()
