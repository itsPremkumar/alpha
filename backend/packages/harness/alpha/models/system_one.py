"""System One client — hosted Jev or local Laya.

System One models are *not* LLMs. They never generate text. You send a
``state`` (string / object / array) plus a map of typed ``questions`` and get
back typed, calibrated decisions with probabilities and confidence:

=========  ==================================================  ===============================
``type``   question                                            answer
=========  ==================================================  ===============================
boolean    Is this true?                                       ``boolean``: 0..1 probability
choice     Which of these N options? (<=255)                   ``choice`` + ``probabilities`` + ``confidence``
score      Which level on this ordered rubric? (2-10 levels)   ``score`` + ``probabilities`` + ``confidence``
=========  ==================================================  ===============================

Everything is evaluated in parallel in a single request, so asking 10
questions costs about the same as asking 1. Output tokens are free.

This module is designed to be a *fast path that is safe to ignore*: every
entry point returns ``None`` when System One is disabled, unreachable, or not
confident enough, which is the signal for the caller to fall back to its
existing heuristic or LLM path. Callers must never depend on System One
being available.

Endpoints
---------
Vercel AI Gateway (default hosted route):
    POST https://ai-gateway.vercel.sh/v1/evaluate
    model: "typesafe-ai/jev"

TypeSafe direct:
    POST https://api.typesafe.ai/v1/systemone
    model: "jev-latest"

Local Laya (Apache-2.0 weights, self-hosted):
    POST http://127.0.0.1:8000/v1/systemone
    model: "english", "multilingual", "typed-decisions", or empty for auto-routing

Laya intentionally implements the TypeSafe/Jev wire contract. The gateway
renames the boolean question to ``boolean``; TypeSafe and Laya use ``noul``.
The client sends the right vocabulary per provider and reads either key back.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import os
import random
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from alpha.config.system_one_config import (
    PROVIDER_LAYA,
    PROVIDER_TYPESAFE,
    PROVIDER_VERCEL_GATEWAY,
    RiskTier,
    SystemOneConfig,
    provider_base_url,
)

logger = logging.getLogger(__name__)

QuestionType = Literal["boolean", "choice", "score"]

# Native TypeSafe API calls a boolean question "noul" (a nod to Kahneman / the
# "null" of a yes-no judgement). The Vercel gateway renames it to "boolean".
_NOUL_ALIAS = "noul"

_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524, 529})
_LAYA_DEFAULT_URL = "http://127.0.0.1:8000"

#: The hosted System One wire contract accepts at most 255 options in one
#: choice question. Laya applies a smaller, checkpoint-specific budget; the
#: client uses :func:`choice_option_limit` to choose the right bound.
MAX_CHOICE_OPTIONS = 255


class SystemOneError(Exception):
    """Base error for System One failures."""


class SystemOneUnavailable(SystemOneError):
    """System One cannot answer right now; the caller should fall back."""


class SystemOneConfigError(SystemOneError):
    """System One is misconfigured (e.g. missing API key)."""


# --------------------------------------------------------------------------
# Questions
# --------------------------------------------------------------------------


@dataclass
class BooleanQuestion:
    """A yes/no question. Returns the probability the answer is yes."""

    instructions: str
    criteria: dict[str, str] | None = None

    def to_payload(self, boolean_key: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": boolean_key, "instructions": self.instructions}
        if self.criteria:
            payload["criteria"] = self.criteria
        return payload


@dataclass
class ChoiceQuestion:
    """Pick one of a fixed set of unordered options (max 255)."""

    instructions: Any
    criteria: dict[str, Any]

    def to_payload(self, boolean_key: str) -> dict[str, Any]:
        return {"type": "choice", "instructions": self.instructions, "criteria": self.criteria}


@dataclass
class ScoreQuestion:
    """Rate the state along an ordered rubric (2-10 levels)."""

    instructions: str
    criteria: list[str]

    def to_payload(self, boolean_key: str) -> dict[str, Any]:
        return {"type": "score", "instructions": self.instructions, "criteria": self.criteria}


Question = BooleanQuestion | ChoiceQuestion | ScoreQuestion


# --------------------------------------------------------------------------
# Answers
# --------------------------------------------------------------------------


@dataclass
class Answer:
    """A typed answer under the id you chose.

    ``value`` is the directly usable result: a probability for boolean, the
    chosen option for choice, and the numeric level for score. ``confidence``
    is None for boolean answers, which carry no separate confidence signal —
    use the distance of ``value`` from 0.5 instead.
    """

    id: str
    type: QuestionType
    value: float | str
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None
    legend: dict[str, str] = field(default_factory=dict)

    @property
    def boolean(self) -> float | None:
        """Yes-probability, or None if this is not a boolean answer."""
        return float(self.value) if self.type == "boolean" else None

    @property
    def choice(self) -> str | None:
        """Chosen option, or None if this is not a choice answer."""
        return str(self.value) if self.type == "choice" else None

    @property
    def score(self) -> float | None:
        """Numeric level (can land between levels), or None if not a score."""
        try:
            return float(self.value) if self.type == "score" else None
        except (TypeError, ValueError):
            return None

    def meets(self, threshold: float) -> bool:
        """Is this answer confident enough to act on?

        Boolean answers have no confidence field, so their certainty is the
        distance of the probability from the 0.5 coin-flip midpoint, scaled to
        0..1. That makes a single `meets()` threshold meaningful across all
        three primitive types. Invalid provider values fail closed here as well
        as in :meth:`validate`, because some callers use ``meets`` directly.
        """
        try:
            if self.confidence is not None:
                if not 0.0 <= self.confidence <= 1.0:
                    return False
                if self.type == "choice" and self.probabilities:
                    values = list(self.probabilities.values())
                    if any(not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values):
                        return False
                    if abs(sum(values) - 1.0) >= 0.02:
                        return False
                return self.confidence >= threshold
            if self.type == "boolean":
                value = float(self.value)
                if not 0.0 <= value <= 1.0:
                    return False
                return abs(value - 0.5) * 2 >= threshold
            if self.type == "choice" and self.probabilities:
                values = list(self.probabilities.values())
                if any(not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values):
                    return False
                if abs(sum(values) - 1.0) >= 0.02:
                    return False
            if self.type == "score":
                value = float(self.value)
                if not math.isfinite(value):
                    return False
        except (TypeError, ValueError, OverflowError):
            return False
        return False

    def validate(self, allowed: set[str] | list[str] | dict[str, Any]) -> bool:
        """Well-formedness check on a choice answer, ported from browser-use/jev-ultrafast.

        System One cannot produce a type error, so this is not defence against
        the model misbehaving — it is defence against *us* misreading it: a
        truncated response, a criteria set that drifted from the request, or an
        answer whose reported choice is not actually the argmax. Cheap to run
        and it turns a silent wrong branch into a visible fallback.
        """
        if self.type != "choice":
            return False
        allowed_set = set(allowed)
        try:
            probs = self.probabilities
            if not probs or set(probs) != allowed_set:
                return False
            if str(self.value) not in allowed_set:
                return False
            values = [*probs.values()]
            confidence = self.confidence if self.confidence is not None else 0.0
            if not all(isinstance(n, (int, float)) and math.isfinite(n) and 0.0 <= n <= 1.0 for n in [*values, confidence]):
                return False
            if abs(sum(values) - 1.0) >= 0.02:
                return False
            return probs[str(self.value)] >= max(values) - 1e-6
        except (KeyError, TypeError, ValueError):
            return False


@dataclass
class EvaluationResult:
    """Result of one System One call."""

    answers: dict[str, Answer]
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0

    def get(self, answer_id: str) -> Answer | None:
        return self.answers.get(answer_id)


@dataclass
class PartitionedChoiceResult:
    """A confidence-gated choice over a catalog larger than one request.

    ``ranking`` always contains every input option: the final shortlist order is
    followed by options that did not survive an earlier partition, in their
    original deterministic order. This lets ranking callers use the semantic
    result without silently dropping candidates.
    """

    value: str
    probabilities: dict[str, float]
    confidence: float | None
    ranking: list[str]
    scores: dict[str, float]
    requests: int
    rounds: int
    model: str = ""
    latency_ms: float = 0.0

    @property
    def partitioned(self) -> bool:
        return self.rounds > 1

    @property
    def choice(self) -> str:
        return self.value


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _probability_map(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    parsed: dict[str, float] = {}
    for key, raw_value in value.items():
        number = _finite_float(raw_value)
        if number is None:
            return None
        parsed[str(key)] = number
    return parsed


def _optional_confidence(value: Any) -> float | None:
    if value is None:
        return None
    return _finite_float(value)


def _parse_answer(answer_id: str, raw: dict[str, Any]) -> Answer | None:
    """Parse one answer, tolerating both provider vocabularies.

    System One is an external decision boundary: malformed provider JSON must
    become ``None`` (caller fallback), never an exception or a fabricated
    probability.
    """
    if not isinstance(raw, dict):
        return None
    qtype = str(raw.get("type", "")).lower()
    confidence = _optional_confidence(raw.get("confidence"))
    if confidence is None and raw.get("confidence") is not None:
        return None
    probabilities_value = raw.get("probabilities")
    probabilities = {} if probabilities_value is None else _probability_map(probabilities_value)
    if probabilities is None:
        return None
    if qtype in ("boolean", _NOUL_ALIAS):
        # Gateway returns "boolean"; native TypeSafe returns "noul".
        value = _finite_float(raw.get("boolean", raw.get(_NOUL_ALIAS)))
        if value is None or not 0.0 <= value <= 1.0:
            return None
        return Answer(id=answer_id, type="boolean", value=value, probabilities=probabilities, confidence=confidence)
    if qtype == "choice":
        value = raw.get("choice")
        if value is None:
            return None
        return Answer(id=answer_id, type="choice", value=str(value), probabilities=probabilities, confidence=confidence)
    if qtype == "score":
        value = _finite_float(raw.get("score"))
        if value is None:
            return None
        legend_value = raw.get("legend")
        if legend_value is not None and not isinstance(legend_value, dict):
            return None
        legend = {str(key): str(item) for key, item in (legend_value or {}).items()}
        return Answer(id=answer_id, type="score", value=value, probabilities=probabilities, confidence=confidence, legend=legend)
    return None


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class SystemOneClient:
    """Async client for a System One endpoint (Jev or local Laya).

    Thread/loop-safety: the httpx client is created lazily per event loop and
    the circuit breaker state is guarded by a short-lived thread lock.
    """

    def __init__(self, config: SystemOneConfig | None = None) -> None:
        self._config = config
        # A client constructed without an explicit config must follow Alpha's
        # hot-reloadable AppConfig. Tests and embedded callers that pass a config
        # keep that stable override until they call reload().
        self._config_from_app = config is None
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        # Breaker state is touched by sync call sites and async handlers; a
        # thread lock keeps the transition atomic across event loops.
        self._lock = threading.Lock()
        self._half_open_probe = False
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._breaker_generation = 0
        self._in_flight = 0

    # -- config -----------------------------------------------------------

    @property
    def config(self) -> SystemOneConfig:
        if self._config is None or self._config_from_app:
            from alpha.config import get_app_config

            self._config = get_app_config().system_one
        return self._config

    def reload(self, config: SystemOneConfig) -> None:
        """Apply a new explicit config and discard the transport client."""
        self._config = config
        self._config_from_app = False
        self._client = None
        self._client_loop = None
        with self._lock:
            self._half_open_probe = False
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0
            self._breaker_generation += 1
            self._in_flight = 0

    def _resolve_api_key(self) -> str | None:
        """Resolve the API key, supporting '$ENV_VAR' indirection.

        Laya's default loopback server may be intentionally unauthenticated, so
        an absent key is valid for that provider. Do not fall back to a cloud
        gateway key there: accidentally sending it to a local server is both
        surprising and a credential leak.
        """
        raw = (self.config.api_key or "").strip()
        if not raw:
            if self.config.provider == PROVIDER_LAYA:
                value = os.getenv("LAYA_API_KEY")
                return value.strip() if value and value.strip() else None
            if self.config.provider == PROVIDER_TYPESAFE:
                env_names = ("TYPESAFE_API_KEY", "JEV_API_KEY")
            else:
                env_names = ("AI_GATEWAY_API_KEY", "VERCEL_AI_GATEWAY_API_KEY")
            # Convenience defaults so a bare hosted config still works when the
            # matching standard env vars are present. Never cross provider
            # boundaries: a gateway key must not be sent to TypeSafe or Laya.
            for env_name in env_names:
                value = os.getenv(env_name)
                if value:
                    return value.strip()
            return None
        if raw.startswith("$"):
            env_name = raw[1:]
            if self.config.provider == PROVIDER_LAYA and env_name in {"AI_GATEWAY_API_KEY", "VERCEL_AI_GATEWAY_API_KEY", "TYPESAFE_API_KEY", "JEV_API_KEY"}:
                logger.warning("Ignoring a hosted-provider key configured for local Laya; use $LAYA_API_KEY instead.")
                return None
            if self.config.provider == PROVIDER_TYPESAFE and env_name in {"AI_GATEWAY_API_KEY", "VERCEL_AI_GATEWAY_API_KEY"}:
                logger.warning("Ignoring a Vercel gateway key configured for TypeSafe; use $TYPESAFE_API_KEY instead.")
                return None
            return (os.getenv(env_name) or "").strip() or None
        return raw

    def _endpoint(self) -> str:
        base = (self.config.base_url or provider_base_url(self.config.provider)).rstrip("/")
        if self.config.provider == PROVIDER_LAYA:
            suffix = "v1/systemone"
        elif self.config.provider == PROVIDER_TYPESAFE:
            suffix = "v1/systemone"
        else:
            suffix = "v1/evaluate"
        # Accept a fully-qualified endpoint as well as a provider base URL. This
        # is useful for reverse proxies and avoids the old /v1/v1 duplication.
        if base.endswith("/" + suffix) or (self.config.provider == PROVIDER_LAYA and base.endswith("/systemone")):
            return base
        if base.endswith("/v1") and suffix.startswith("v1/"):
            return f"{base}/{suffix[3:]}"
        return f"{base}/{suffix}"

    def _boolean_key(self) -> str:
        return _NOUL_ALIAS if self.config.provider in (PROVIDER_TYPESAFE, PROVIDER_LAYA) else "boolean"

    def threshold_for(self, tier: str | RiskTier | None = None) -> float:
        """Confidence floor for a risk tier; None means the global floor.

        Call sites should pass a RiskTier so a destructive action demands more
        certainty than a read. Degrades to `min_confidence` for unknown tiers.
        """
        if tier is None:
            return self.config.min_confidence
        return self.config.threshold_for(tier)

    def _laya_url_is_loopback(self) -> bool:
        """Return whether the configured Laya endpoint is local-only.

        A keyless local model must never be reachable through an arbitrary
        remote URL. Literal loopback hosts and the conventional ``localhost``
        alias are accepted without DNS; other hostnames require an API key.
        """
        try:
            parsed = urlparse(self.config.base_url or _LAYA_DEFAULT_URL)
            host = parsed.hostname
            if not host:
                return False
            if host.lower() == "localhost":
                return True
            try:
                return ipaddress.ip_address(host).is_loopback
            except ValueError:
                return False
        except (TypeError, ValueError):
            return False

    def choice_option_limit(self) -> int:
        """Maximum number of options safe for one choice request.

        Hosted Jev accepts the documented 255-option ceiling. Laya's English
        and multilingual checkpoints are deliberately more conservative by
        default; callers can partition a larger catalog before asking.
        """
        if self.config.provider == PROVIDER_LAYA:
            return self.config.laya_max_choice_options
        return MAX_CHOICE_OPTIONS

    def _payload(
        self,
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, Question],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "state": state,
            "questions": {qid: question.to_payload(self._boolean_key()) for qid, question in questions.items()},
        }
        if self.config.model:
            payload["model"] = self.config.model
        return payload

    def _laya_request_is_safe(self, state: str | dict[str, Any] | list[Any], questions: dict[str, Question]) -> bool:
        """Reject Laya requests its checkpoint budget cannot represent safely.

        Laya's server accepts a larger body than its short English checkpoint can
        tokenize without truncation. A silent answer over truncated evidence is
        worse than the caller's existing fallback, so enforce conservative
        bounds before the HTTP request.
        """
        cfg = self.config
        if cfg.provider != PROVIDER_LAYA:
            return True
        if len(questions) > cfg.laya_max_questions:
            logger.debug("Laya request has %d questions (limit %d); falling back.", len(questions), cfg.laya_max_questions)
            return False
        try:
            state_chars = len(state) if isinstance(state, str) else len(str(state))
        except Exception:
            return False
        if state_chars > cfg.laya_max_state_chars:
            logger.debug("Laya state is %d characters (limit %d); falling back.", state_chars, cfg.laya_max_state_chars)
            return False
        for question in questions.values():
            if isinstance(question, ChoiceQuestion) and len(question.criteria) > cfg.laya_max_choice_options:
                logger.debug(
                    "Laya choice has %d options (limit %d); falling back to shortlist/partitioning.",
                    len(question.criteria),
                    cfg.laya_max_choice_options,
                )
                return False
            if isinstance(question, ScoreQuestion) and not 2 <= len(question.criteria) <= 10:
                logger.debug("Laya score has %d rubric levels; falling back to the existing path.", len(question.criteria))
                return False
        try:
            serialized_chars = len(json.dumps(self._payload(state, questions), ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        except (TypeError, ValueError):
            return False
        # The state and option caps protect the checkpoint, while this payload
        # cap protects the HTTP/model boundary from duplicated instructions and
        # oversized labels. Keep it deliberately conservative for local Laya.
        payload_limit = cfg.laya_max_request_chars
        if serialized_chars > payload_limit:
            logger.debug("Laya serialized request is %d characters (limit %d); falling back.", serialized_chars, payload_limit)
            return False
        return True

    # -- availability -----------------------------------------------------

    def _reserve_request_slot(self) -> tuple[int, bool] | None:
        """Reserve a request and return ``(breaker_generation, is_probe)``.

        Closed-state calls may run concurrently. Once the breaker opens, only
        one half-open probe is admitted; the generation fence prevents a late
        result from an older request from changing the new breaker state.
        """
        with self._lock:
            now = time.monotonic()
            if self._circuit_open_until and now < self._circuit_open_until:
                return None
            is_probe = bool(self._circuit_open_until)
            if is_probe:
                if self._half_open_probe:
                    return None
                self._half_open_probe = True
            else:
                self._in_flight += 1
            return self._breaker_generation, is_probe

    def _release_request_slot(self, reservation: tuple[int, bool], *, success: bool) -> None:
        generation, is_probe = reservation
        with self._lock:
            if is_probe:
                self._half_open_probe = False
            else:
                self._in_flight = max(0, self._in_flight - 1)
            if not success or generation != self._breaker_generation:
                return
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0

    def is_available(self) -> bool:
        """Cheap synchronous check: enabled, configured, breaker not open.

        A local Laya server is allowed to run without a bearer token. Hosted
        Jev routes still require a key before any callout is attempted.
        """
        cfg = self.config
        if not cfg.enabled:
            return False
        if cfg.provider == PROVIDER_LAYA and not self._resolve_api_key() and not self._laya_url_is_loopback():
            logger.warning("Refusing keyless Laya endpoint outside loopback: %s", cfg.base_url)
            return False
        if cfg.provider != PROVIDER_LAYA and not self._resolve_api_key():
            return False
        with self._lock:
            if self._circuit_open_until and time.monotonic() < self._circuit_open_until:
                return False
        return True

    def _record_failure(self, reservation: tuple[int, bool]) -> None:
        cfg = self.config
        generation, is_probe = reservation
        with self._lock:
            if generation != self._breaker_generation:
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= cfg.circuit_breaker_threshold:
                self._circuit_open_until = time.monotonic() + cfg.circuit_breaker_cooldown_s
                self._breaker_generation += 1
                logger.warning(
                    "System One circuit breaker opened after %d consecutive failures; falling back for %.0fs.",
                    self._consecutive_failures,
                    cfg.circuit_breaker_cooldown_s,
                )
            elif is_probe:
                # A failed half-open probe re-opens the circuit for another
                # cooldown, even when a threshold change made the count smaller.
                self._circuit_open_until = time.monotonic() + cfg.circuit_breaker_cooldown_s
                self._breaker_generation += 1

    def _record_success(self, reservation: tuple[int, bool]) -> None:
        generation, _is_probe = reservation
        with self._lock:
            if generation != self._breaker_generation:
                return
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0

    # -- http -------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._client_loop is not loop or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.config.timeout_ms / 1000.0)
            self._client_loop = loop
        return self._client

    # -- main entry -------------------------------------------------------

    async def evaluate(
        self,
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, Question],
        *,
        min_confidence: float | None = None,
        site: str = "",
        tier: str | RiskTier | None = None,
    ) -> EvaluationResult | None:
        """Evaluate typed questions with a single circuit-breaker reservation.

        Reservation is separate from the implementation so every early return
        (disabled, unsafe payload, transport failure, or shadow mode) releases
        the half-open probe slot before the caller continues.
        """
        if not questions:
            return None
        reservation = self._reserve_request_slot()
        if reservation is None:
            return None
        try:
            result = await self._evaluate_impl(
                state,
                questions,
                min_confidence=min_confidence,
                site=site,
                tier=tier,
                reservation=reservation,
            )
        except asyncio.CancelledError:
            self._release_request_slot(reservation, success=False)
            raise
        except Exception as exc:
            self._release_request_slot(reservation, success=False)
            logger.warning("System One unexpected error (%s); falling back.", exc)
            return None
        self._release_request_slot(reservation, success=result is not None)
        return result

    async def _evaluate_impl(
        self,
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, Question],
        *,
        min_confidence: float | None = None,
        site: str = "",
        tier: str | RiskTier | None = None,
        reservation: tuple[int, bool],
    ) -> EvaluationResult | None:
        """Evaluate `state` against `questions` in one parallel request.

        Args:
            site: Call-site label (e.g. ``"guardrail"``, ``"browser"``) attached
                to the decision record so calibration can be reported per site.
            tier: Risk tier, used for the confidence floor and recorded too.

        Returns None — never raises — when System One is disabled,
        misconfigured, unreachable, or the breaker is open. Callers treat None
        as "use the fallback path".

        In **shadow mode** this always returns None after recording, so every
        call site runs its existing path while the decisions are measured. That
        is how you find out whether a stated 0.9 means 90% right before letting
        any site act on one.
        """
        if not questions:
            return None
        cfg = self.config
        if not cfg.enabled:
            return None
        if not self._laya_request_is_safe(state, questions):
            return None

        threshold = min_confidence if min_confidence is not None else self.threshold_for(tier)

        if not self.is_available():
            return None

        api_key = self._resolve_api_key()
        if cfg.provider != PROVIDER_LAYA and not api_key:
            logger.debug("System One disabled: no API key resolved.")
            return None

        payload = self._payload(state, questions)

        request_headers = {"Content-Type": "application/json"}
        if api_key:
            request_headers["Authorization"] = f"Bearer {api_key}"

        started = time.monotonic()
        deadline = started + (cfg.timeout_ms / 1000.0) * (cfg.max_retries + 1)
        last_error: str = ""
        for attempt in range(cfg.max_retries + 1):
            try:
                client = self._get_client()
                response = await self._post_with_deadline(client, payload, request_headers, deadline)
            except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < cfg.max_retries and await self._sleep_backoff(attempt, deadline=deadline):
                    continue
                self._record_failure(reservation)
                logger.warning("System One request failed after %d attempts (%s); falling back.", attempt + 1, last_error)
                return None
            except Exception as exc:  # defensive: never break the caller
                self._record_failure(reservation)
                logger.warning("System One unexpected error (%s); falling back.", exc)
                return None

            if response.status_code in _RETRYABLE_STATUS:
                last_error = f"HTTP {response.status_code}"
                if attempt < cfg.max_retries and await self._sleep_backoff(
                    attempt,
                    response.headers.get("retry-after"),
                    deadline=deadline,
                ):
                    continue
                self._record_failure(reservation)
                logger.warning("System One returned %s after %d attempts; falling back.", last_error, attempt + 1)
                return None

            if response.status_code >= 400:
                body = response.text[:300]
                if response.status_code == 422:
                    # Question validation is deterministic caller input, not a
                    # transport outage. Do not open the circuit for a malformed
                    # Laya/Jev request; the existing call-site fallback applies.
                    logger.warning("System One rejected the typed question (HTTP 422): %s; falling back.", body)
                    return None
                self._record_failure(reservation)
                if response.status_code in (401, 403):
                    if self.config.provider == PROVIDER_LAYA:
                        logger.warning(
                            "Laya System One auth/quota rejected (HTTP %d): %s. Check LAYA_API_KEY on both the server and Alpha; an unauthenticated loopback server should return 401 only when it was started with a key.",
                            response.status_code,
                            body,
                        )
                    else:
                        logger.warning(
                            "System One auth/quota rejected (HTTP %d): %s. Check the API key and that the Vercel account has a card on file to unlock free credits.",
                            response.status_code,
                            body,
                        )
                else:
                    logger.warning("System One returned HTTP %d: %s; falling back.", response.status_code, body)
                return None

            try:
                data = response.json()
            except ValueError:
                self._record_failure(reservation)
                logger.warning("System One returned non-JSON response; falling back.")
                return None

            result = self._to_result(data, (time.monotonic() - started) * 1000.0)
            if result is None:
                self._record_failure(reservation)
                logger.warning("System One returned a malformed decision payload; falling back.")
                return None
            self._record_success(reservation)
            self._emit_records(result, site=site, tier=tier, threshold=threshold)
            if cfg.shadow_mode:
                logger.debug(
                    "System One shadow: recorded %d answer(s) for %r, returning None so the caller uses its existing path.",
                    len(result.answers),
                    site or "unlabelled",
                )
                return None
            if cfg.log_decisions:
                logger.debug(
                    "System One [%s] %.0fms in=%s out=%s answers=%s",
                    result.model,
                    result.latency_ms,
                    result.input_tokens,
                    result.output_tokens,
                    {k: (a.value, a.confidence) for k, a in result.answers.items()},
                )
            return result

        return None

    def _emit_records(
        self,
        result: EvaluationResult,
        *,
        site: str,
        tier: str | RiskTier | None,
        threshold: float,
    ) -> None:
        """Append this request's answers to the calibration log.

        Best-effort in both directions: if the log is off or unwritable the
        decision is unaffected, and if recording raises the caller still gets
        its answer.
        """
        cfg = self.config
        if not (cfg.record_decisions or cfg.shadow_mode):
            return
        try:
            from alpha.evaluation.system_one_calibration import DecisionRecord, get_recorder

            recorder = get_recorder()
        except Exception as exc:  # pragma: no cover - recording must never break a decision
            logger.debug("System One calibration unavailable (%s); skipping record.", exc)
            return
        tier_name = tier.value if isinstance(tier, RiskTier) else (str(tier) if tier else "")
        now = time.time()
        for qid, answer in result.answers.items():
            try:
                recorder.record(
                    DecisionRecord(
                        ts=now,
                        site=site,
                        tier=tier_name,
                        question_id=qid,
                        type=answer.type,
                        value=answer.value,
                        confidence=answer.confidence,
                        threshold=threshold,
                        latency_ms=result.latency_ms,
                        model=result.model,
                        shadow=cfg.shadow_mode,
                        provider=cfg.provider,
                    )
                )
            except Exception as exc:  # pragma: no cover
                logger.debug("System One could not record %s: %s", qid, exc)
                return

    async def _sleep_backoff(self, attempt: int, retry_after: str | None = None, deadline: float | None = None) -> bool:
        """Back off briefly, never sleeping past the call deadline."""
        delay = None
        if retry_after:
            try:
                candidate = float(retry_after)
                if math.isfinite(candidate):
                    delay = max(0.0, min(candidate, 4.0))
            except (TypeError, ValueError):
                delay = None
        if delay is None:
            delay = min(0.25 * (2**attempt), 4.0)
        delay = min(delay + random.uniform(0, 0.15), 4.0)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            delay = min(delay, remaining)
        await asyncio.sleep(delay)
        return deadline is None or time.monotonic() < deadline

    async def _post_with_deadline(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        headers: dict[str, str],
        deadline: float,
    ) -> httpx.Response:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("System One call deadline expired")
        return await asyncio.wait_for(
            client.post(self._endpoint(), json=payload, headers=headers),
            timeout=remaining,
        )

    def _to_result(self, data: Any, latency_ms: float) -> EvaluationResult | None:
        if not isinstance(data, dict):
            logger.warning("System One response was not an object; falling back.")
            return None
        raw_answers = data.get("answers")
        if not isinstance(raw_answers, dict) or not raw_answers:
            logger.warning("System One response carried no answers; falling back.")
            return None
        answers: dict[str, Answer] = {}
        for qid, raw in raw_answers.items():
            if not isinstance(raw, dict):
                return None
            parsed = _parse_answer(str(qid), raw)
            if parsed is None:
                return None
            answers[str(qid)] = parsed
        if not answers:
            return None
        raw_usage = data.get("usage")
        if raw_usage is None:
            usage: dict[str, Any] = {}
        elif isinstance(raw_usage, dict):
            usage = raw_usage
        else:
            return None

        def _token_count(key: str) -> int:
            value = usage.get(key, 0)
            number = _finite_float(value)
            if number is None or number < 0:
                raise ValueError(f"invalid usage.{key}")
            return int(number)

        try:
            input_tokens = _token_count("input_tokens")
            output_tokens = _token_count("output_tokens")
        except (TypeError, ValueError, OverflowError):
            return None
        model = data.get("model", "")
        if model is None:
            model = ""
        elif not isinstance(model, str):
            return None
        return EvaluationResult(
            answers=answers,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )


# --------------------------------------------------------------------------
# Singleton
# --------------------------------------------------------------------------

_client: SystemOneClient | None = None


def get_system_one_client() -> SystemOneClient:
    """Process-wide System One client."""
    global _client
    if _client is None:
        _client = SystemOneClient()
    return _client


def reset_system_one_client() -> None:
    """Drop the singleton (tests / config reload)."""
    global _client
    _client = None


# --------------------------------------------------------------------------
# High-level helpers — the shape most call sites want
# --------------------------------------------------------------------------


async def decide_boolean(
    state: str | dict[str, Any] | list[Any],
    instructions: str,
    *,
    criteria: dict[str, str] | None = None,
    min_confidence: float | None = None,
    tier: str | RiskTier | None = None,
    site: str = "",
    client: SystemOneClient | None = None,
) -> float | None:
    """P(yes) in 0..1, or None when System One can't answer confidently.

    None means "fall back" — it does not mean False.
    """
    return await _one("q", BooleanQuestion(instructions, criteria), state, min_confidence, client, "boolean", tier, site)


async def decide_choice(
    state: str | dict[str, Any] | list[Any],
    instructions: str,
    criteria: dict[str, Any],
    *,
    min_confidence: float | None = None,
    tier: str | RiskTier | None = None,
    site: str = "",
    client: SystemOneClient | None = None,
) -> str | None:
    """Chosen option, or None when System One can't answer confidently."""
    return await _one("q", ChoiceQuestion(instructions, criteria), state, min_confidence, client, "choice", tier, site)


async def decide_score(
    state: str | dict[str, Any] | list[Any],
    instructions: str,
    criteria: list[str],
    *,
    min_confidence: float | None = None,
    tier: str | RiskTier | None = None,
    site: str = "",
    client: SystemOneClient | None = None,
) -> float | None:
    """Numeric level (can land between levels), or None to fall back."""
    return await _one("q", ScoreQuestion(instructions, criteria), state, min_confidence, client, "score", tier, site)


def _partition_instructions(instructions: Any, note: str) -> Any:
    """Add partition context without destroying structured instructions."""
    if isinstance(instructions, dict):
        return {**instructions, "partition": note}
    if isinstance(instructions, list):
        return [*instructions, note]
    return f"{instructions} {note}".strip()


async def evaluate_choice_partitioned(
    state: str | dict[str, Any] | list[Any],
    instructions: Any,
    criteria: dict[str, Any],
    *,
    min_confidence: float | None = None,
    tier: str | RiskTier | None = None,
    site: str = "",
    client: SystemOneClient | None = None,
    shortlist_per_partition: int = 3,
    question_id: str = "choice",
    deadline: float | None = None,
    state_projector: Callable[[Sequence[str]], str | dict[str, Any] | list[Any]] | None = None,
) -> PartitionedChoiceResult | None:
    """Evaluate a large choice set with bounded, confidence-gated partitioning.

    Laya's local checkpoints have a smaller practical option budget than the
    hosted Jev API. Rather than truncating a catalog and silently dropping the
    correct answer, this helper evaluates deterministic partitions, retains a
    shortlist from each partition, and asks a final choice question over that
    shortlist. Every stage uses the same provider client and the same fallback
    contract: ``None`` means the caller should use its existing path.

    The final probabilities are the provider's final-stage distribution; they
    are not multiplied across partitions, which would manufacture a confidence
    number the provider did not produce. ``ranking`` retains every original
    option, which makes the helper safe to use for catalog selection as well as
    direct routing.
    """
    cli = client or get_system_one_client()
    if not criteria:
        return None
    limit = cli.choice_option_limit()
    threshold = min_confidence if min_confidence is not None else cli.threshold_for(tier)
    deadline_at = time.monotonic() + deadline if deadline is not None and deadline > 0 else None
    max_requests: int | None = getattr(cli.config, "laya_max_partition_requests", 16) if cli.config.provider == PROVIDER_LAYA else None
    requests = 0

    def _remaining() -> float | None:
        if deadline_at is None:
            return None
        remaining = deadline_at - time.monotonic()
        return remaining if remaining > 0 else 0.0

    def _project_state(option_ids: Sequence[str]) -> str | dict[str, Any] | list[Any] | None:
        if state_projector is None:
            return state
        try:
            return state_projector(option_ids)
        except Exception:
            logger.debug("Partitioned choice state projection failed; falling back.", exc_info=True)
            return None

    async def _call(
        partition_state: str | dict[str, Any] | list[Any] | None,
        questions: dict[str, Question],
        *,
        call_site: str,
    ) -> EvaluationResult | None:
        if partition_state is None:
            return None
        if max_requests is not None and requests >= max_requests:
            logger.debug("Partitioned choice exhausted its %d-request budget; falling back.", max_requests)
            return None
        remaining = _remaining()
        if remaining is not None and remaining <= 0:
            return None
        try:
            call = cli.evaluate(
                partition_state,
                questions,
                min_confidence=threshold,
                site=call_site,
                tier=tier,
            )
            if remaining is None:
                return await call
            return await asyncio.wait_for(call, timeout=remaining)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Partitioned System One request failed (%s); falling back.", exc)
            return None

    def _direct(result: EvaluationResult | None) -> PartitionedChoiceResult | None:
        if result is None:
            return None
        answer = result.get(question_id)
        if answer is None or not answer.validate(criteria) or not answer.meets(threshold):
            return None
        ordered = sorted(criteria, key=lambda key: -answer.probabilities.get(key, 0.0))
        return PartitionedChoiceResult(
            value=str(answer.value),
            probabilities=dict(answer.probabilities),
            confidence=answer.confidence,
            ranking=ordered,
            scores=dict(answer.probabilities),
            requests=1,
            rounds=1,
            model=result.model,
            latency_ms=result.latency_ms,
        )

    if len(criteria) <= limit:
        if len(criteria) == 1:
            key = next(iter(criteria))
            return PartitionedChoiceResult(
                value=key,
                probabilities={key: 1.0},
                confidence=1.0,
                ranking=[key],
                scores={key: 1.0},
                requests=0,
                rounds=0,
            )
        direct_state = _project_state(tuple(criteria))
        result = await _call(
            direct_state,
            {question_id: ChoiceQuestion(instructions, criteria)},
            call_site=site,
        )
        requests += 1
        return _direct(result)

    if shortlist_per_partition < 1:
        shortlist_per_partition = 1
    original_order = list(criteria)
    items = list(criteria.items())
    partitions = [items[offset : offset + limit] for offset in range(0, len(items), limit)]
    if max_requests is not None and len(partitions) + 1 > max_requests:
        logger.debug("Partitioned choice needs at least %d requests but budget is %d; falling back.", len(partitions) + 1, max_requests)
        return None
    if max_requests is not None:
        # Pick the largest per-partition shortlist that still fits the total
        # request budget, including a possible shortlist tournament and final.
        while shortlist_per_partition > 1:
            shortlist_size = min(len(criteria), len(partitions) * shortlist_per_partition)
            tournament_groups = 0 if shortlist_size <= limit else (shortlist_size + limit - 1) // limit
            projected_requests = len(partitions) + tournament_groups + 1
            if projected_requests <= max_requests:
                break
            shortlist_per_partition -= 1
        shortlist_size = min(len(criteria), len(partitions) * shortlist_per_partition)
        tournament_groups = 0 if shortlist_size <= limit else (shortlist_size + limit - 1) // limit
        if len(partitions) + tournament_groups + 1 > max_requests:
            logger.debug("Partitioned choice cannot fit the local request budget; falling back.")
            return None
    shortlist: dict[str, str] = {}
    partition_scores: dict[str, float] = {}
    rounds = 1
    total_latency = 0.0
    model = ""

    for index, partition in enumerate(partitions):
        partition_criteria = dict(partition)
        partition_state = _project_state(tuple(partition_criteria))
        result = await _call(
            partition_state,
            {
                question_id: ChoiceQuestion(
                    _partition_instructions(instructions, f"partition {index + 1} of {len(partitions)}; choose the best option from this partition"),
                    partition_criteria,
                )
            },
            call_site=f"{site or 'choice'}:partition:{index + 1}",
        )
        requests += 1
        if result is None:
            return None
        total_latency += result.latency_ms
        model = result.model or model
        answer = result.get(question_id)
        if answer is None or not answer.validate(partition_criteria) or not answer.meets(threshold):
            return None
        ordered = sorted(partition_criteria, key=lambda key: -answer.probabilities.get(key, 0.0))
        for rank, key in enumerate(ordered):
            # Keep a useful deterministic fallback score for options that do
            # not reach the final shortlist. These are not presented as
            # calibrated global probabilities.
            partition_scores[key] = 1.0 / (rank + 1)
        for key in ordered[: min(shortlist_per_partition, len(ordered))]:
            shortlist[key] = criteria[key]

    if not shortlist:
        return None

    # The number of partitions can be large enough that the union of their
    # shortlists exceeds one request. A second bounded tournament is safer than
    # truncating by dict insertion order.
    if len(shortlist) > limit:
        rounds += 1
        short_items = list(shortlist.items())
        final_groups = [short_items[offset : offset + limit] for offset in range(0, len(short_items), limit)]
        survivors: dict[str, str] = {}
        for index, group in enumerate(final_groups):
            group_criteria = dict(group)
            if len(group_criteria) == 1:
                key = next(iter(group_criteria))
                survivors[key] = shortlist[key]
                partition_scores[key] = max(partition_scores.get(key, 0.0), 1.0)
                continue
            group_state = _project_state(tuple(group_criteria))
            result = await _call(
                group_state,
                {question_id: ChoiceQuestion(_partition_instructions(instructions, f"surviving shortlist partition {index + 1}; choose the best option"), group_criteria)},
                call_site=f"{site or 'choice'}:shortlist:{index + 1}",
            )
            requests += 1
            if result is None:
                return None
            total_latency += result.latency_ms
            model = result.model or model
            answer = result.get(question_id)
            if answer is None or not answer.validate(group_criteria) or not answer.meets(threshold):
                return None
            for rank, key in enumerate(sorted(group_criteria, key=lambda key: -answer.probabilities.get(key, 0.0))):
                partition_scores[key] = max(partition_scores.get(key, 0.0), 1.0 / (rank + 1))
            ordered = sorted(group_criteria, key=lambda key: -answer.probabilities.get(key, 0.0))
            survivors[ordered[0]] = shortlist[ordered[0]]
        shortlist = survivors

    final_state = _project_state(tuple(shortlist))
    final = await _call(
        final_state,
        {question_id: ChoiceQuestion(_partition_instructions(instructions, "choose the best option from the surviving shortlist"), shortlist)},
        call_site=f"{site or 'choice'}:final",
    )
    requests += 1
    if final is None:
        return None
    total_latency += final.latency_ms
    model = final.model or model
    answer = final.get(question_id)
    if answer is None or not answer.validate(shortlist) or not answer.meets(threshold):
        return None

    final_order = sorted(shortlist, key=lambda key: -answer.probabilities.get(key, 0.0))
    ranking = [*final_order, *(key for key in original_order if key not in shortlist)]
    scores = dict(partition_scores)
    scores.update(answer.probabilities)
    return PartitionedChoiceResult(
        value=str(answer.value),
        probabilities=dict(answer.probabilities),
        confidence=answer.confidence,
        ranking=ranking,
        scores=scores,
        requests=requests,
        rounds=rounds,
        model=model,
        latency_ms=total_latency,
    )


def _answer_matches_question(answer: Answer, question: Question) -> bool:
    """Fail closed when a provider answer does not match its request shape."""
    if isinstance(question, BooleanQuestion):
        if answer.type != "boolean":
            return False
        try:
            value = float(answer.value)
        except (TypeError, ValueError, OverflowError):
            return False
        return math.isfinite(value) and 0.0 <= value <= 1.0
    if isinstance(question, ChoiceQuestion):
        return answer.type == "choice" and answer.validate(question.criteria)
    if isinstance(question, ScoreQuestion):
        return answer.type == "score" and answer.score is not None and math.isfinite(answer.score)
    return False


async def _one(
    qid: str,
    question: Question,
    state: str | dict[str, Any] | list[Any],
    min_confidence: float | None,
    client: SystemOneClient | None,
    expected: QuestionType,
    tier: str | RiskTier | None = None,
    site: str = "",
) -> Any:
    cli = client or get_system_one_client()
    if min_confidence is not None:
        threshold = min_confidence
    else:
        threshold = cli.threshold_for(tier)
    try:
        result = await cli.evaluate(state, {qid: question}, min_confidence=threshold, site=site, tier=tier)
    except SystemOneError:
        return None
    if result is None:
        return None
    answer = result.get(qid)
    if answer is None or not _answer_matches_question(answer, question):
        return None
    if not answer.meets(threshold):
        logger.debug("System One %s below confidence %.2f (conf=%.2f); falling back.", qid, threshold, answer.confidence or 0.0)
        return None
    return answer.value


async def evaluate_many(
    state: str | dict[str, Any] | list[Any],
    questions: dict[str, Question],
    *,
    min_confidence: float | None = None,
    tier: str | RiskTier | None = None,
    site: str = "",
    client: SystemOneClient | None = None,
) -> dict[str, Answer]:
    """Evaluate many questions at once; returns only answers that clear the threshold.

    This is the efficient path: all questions ride on a single request and the
    state is ingested once. Missing/low-confidence ids are simply absent from
    the returned dict, so callers fall back per-question.
    """
    cli = client or get_system_one_client()
    if min_confidence is not None:
        threshold = min_confidence
    else:
        threshold = cli.threshold_for(tier)
    try:
        result = await cli.evaluate(state, questions, min_confidence=threshold, site=site, tier=tier)
    except SystemOneError:
        return {}
    if result is None:
        return {}
    return {qid: answer for qid, answer in result.answers.items() if qid in questions and _answer_matches_question(answer, questions[qid]) and answer.meets(threshold)}


def config_or_none() -> SystemOneConfig | None:
    """Return the active System One config, or None if loading fails."""
    try:
        from alpha.config import get_app_config

        return get_app_config().system_one
    except Exception:  # pragma: no cover - config errors must never break callers
        return None


__all__ = [
    "PROVIDER_LAYA",
    "PROVIDER_TYPESAFE",
    "PROVIDER_VERCEL_GATEWAY",
    "Answer",
    "BooleanQuestion",
    "ChoiceQuestion",
    "EvaluationResult",
    "Question",
    "ScoreQuestion",
    "SystemOneClient",
    "SystemOneConfig",
    "SystemOneConfigError",
    "SystemOneError",
    "SystemOneUnavailable",
    "config_or_none",
    "decide_boolean",
    "decide_choice",
    "decide_score",
    "evaluate_choice_partitioned",
    "evaluate_many",
    "get_system_one_client",
    "reset_system_one_client",
]
