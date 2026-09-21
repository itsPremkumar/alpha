"""System One client — Jev by TypeSafe AI.

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
Vercel AI Gateway (default, currently free):
    POST https://ai-gateway.vercel.sh/v1/evaluate
    model: "typesafe-ai/jev"

TypeSafe direct:
    POST https://api.typesafe.ai/v1/systemone
    model: "jev-latest"

Note the two providers use different vocabularies for the boolean question:
the gateway calls it ``boolean``, the TypeSafe API calls it ``noul``. The
client sends the right one per provider and reads either key back.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from alpha.config.system_one_config import (
    PROVIDER_TYPESAFE,
    PROVIDER_VERCEL_GATEWAY,
    RiskTier,
    SystemOneConfig,
)

logger = logging.getLogger(__name__)

QuestionType = Literal["boolean", "choice", "score"]

# Native TypeSafe API calls a boolean question "noul" (a nod to Kahneman / the
# "null" of a yes-no judgement). The Vercel gateway renames it to "boolean".
_NOUL_ALIAS = "noul"

_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524, 529})


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

    instructions: str
    criteria: dict[str, str]

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
        three primitive types.
        """
        if self.confidence is not None:
            return self.confidence >= threshold
        if self.type == "boolean":
            try:
                return abs(float(self.value) - 0.5) * 2 >= threshold
            except (TypeError, ValueError):
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


def _parse_answer(answer_id: str, raw: dict[str, Any]) -> Answer | None:
    """Parse one answer, tolerating both provider vocabularies."""
    if not isinstance(raw, dict):
        return None
    qtype = str(raw.get("type", "")).lower()
    if qtype in ("boolean", _NOUL_ALIAS):
        # Gateway returns "boolean"; native TypeSafe returns "noul".
        value = raw.get("boolean", raw.get(_NOUL_ALIAS))
        if value is None:
            return None
        return Answer(
            id=answer_id,
            type="boolean",
            value=float(value),
            probabilities=raw.get("probabilities") or {},
            confidence=raw.get("confidence"),
        )
    if qtype == "choice":
        value = raw.get("choice")
        if value is None:
            return None
        probs = {str(k): float(v) for k, v in (raw.get("probabilities") or {}).items()}
        return Answer(
            id=answer_id,
            type="choice",
            value=str(value),
            probabilities=probs,
            confidence=raw.get("confidence"),
        )
    if qtype == "score":
        value = raw.get("score")
        if value is None:
            return None
        probs = {str(k): float(v) for k, v in (raw.get("probabilities") or {}).items()}
        legend = {str(k): str(v) for k, v in (raw.get("legend") or {}).items()}
        return Answer(
            id=answer_id,
            type="score",
            value=float(value),
            probabilities=probs,
            confidence=raw.get("confidence"),
            legend=legend,
        )
    return None


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class SystemOneClient:
    """Async client for a System One (Jev) endpoint.

    Thread/loop-safety: the httpx client is created lazily per event loop and
    the circuit breaker state is guarded by an asyncio lock.
    """

    def __init__(self, config: SystemOneConfig | None = None) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    # -- config -----------------------------------------------------------

    @property
    def config(self) -> SystemOneConfig:
        if self._config is None:
            from alpha.config import get_app_config

            self._config = get_app_config().system_one
        return self._config

    def reload(self, config: SystemOneConfig) -> None:
        """Apply a new config (used when config.yaml hot-reloads)."""
        self._config = config
        self._client = None
        self._client_loop = None

    def _resolve_api_key(self) -> str | None:
        """Resolve the API key, supporting '$ENV_VAR' indirection."""
        raw = (self.config.api_key or "").strip()
        if not raw:
            # Convenience defaults so a bare config still works when the
            # standard env vars are present.
            for env_name in ("AI_GATEWAY_API_KEY", "VERCEL_AI_GATEWAY_API_KEY", "TYPESAFE_API_KEY", "JEV_API_KEY"):
                value = os.getenv(env_name)
                if value:
                    return value.strip()
            return None
        if raw.startswith("$"):
            return (os.getenv(raw[1:]) or "").strip() or None
        return raw

    def _endpoint(self) -> str:
        base = (self.config.base_url or "").rstrip("/")
        suffix = "systemone" if self.config.provider == PROVIDER_TYPESAFE else "evaluate"
        return f"{base}/{suffix}"

    def _boolean_key(self) -> str:
        return _NOUL_ALIAS if self.config.provider == PROVIDER_TYPESAFE else "boolean"

    def threshold_for(self, tier: str | RiskTier | None = None) -> float:
        """Confidence floor for a risk tier; None means the global floor.

        Call sites should pass a RiskTier so a destructive action demands more
        certainty than a read. Degrades to `min_confidence` for unknown tiers.
        """
        if tier is None:
            return self.config.min_confidence
        return self.config.threshold_for(tier)

    # -- availability -----------------------------------------------------

    def is_available(self) -> bool:
        """Cheap synchronous check: enabled, configured, breaker not open."""
        cfg = self.config
        if not cfg.enabled:
            return False
        if not self._resolve_api_key():
            return False
        if self._circuit_open_until and time.monotonic() < self._circuit_open_until:
            return False
        return True

    def _record_failure(self) -> None:
        cfg = self.config
        self._consecutive_failures += 1
        if self._consecutive_failures >= cfg.circuit_breaker_threshold and not self._circuit_open_until:
            self._circuit_open_until = time.monotonic() + cfg.circuit_breaker_cooldown_s
            logger.warning(
                "System One circuit breaker opened after %d consecutive failures; falling back for %.0fs.",
                self._consecutive_failures,
                cfg.circuit_breaker_cooldown_s,
            )

    def _record_success(self) -> None:
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

        threshold = min_confidence if min_confidence is not None else self.threshold_for(tier)

        if not self.is_available():
            return None

        api_key = self._resolve_api_key()
        if not api_key:
            logger.debug("System One disabled: no API key resolved.")
            return None

        payload = {
            "model": cfg.model,
            "state": state,
            "questions": {qid: q.to_payload(self._boolean_key()) for qid, q in questions.items()},
        }

        started = time.monotonic()
        last_error: str = ""
        for attempt in range(cfg.max_retries + 1):
            try:
                client = self._get_client()
                response = await client.post(
                    self._endpoint(),
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < cfg.max_retries:
                    await self._sleep_backoff(attempt)
                    continue
                self._record_failure()
                logger.warning("System One request failed after %d attempts (%s); falling back.", attempt + 1, last_error)
                return None
            except Exception as exc:  # defensive: never break the caller
                self._record_failure()
                logger.warning("System One unexpected error (%s); falling back.", exc)
                return None

            if response.status_code in _RETRYABLE_STATUS:
                last_error = f"HTTP {response.status_code}"
                if attempt < cfg.max_retries:
                    await self._sleep_backoff(attempt, response.headers.get("retry-after"))
                    continue
                self._record_failure()
                logger.warning("System One returned %s after %d attempts; falling back.", last_error, attempt + 1)
                return None

            if response.status_code >= 400:
                body = response.text[:300]
                self._record_failure()
                if response.status_code in (401, 403):
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
                self._record_failure()
                logger.warning("System One returned non-JSON response; falling back.")
                return None

            self._record_success()
            result = self._to_result(data, (time.monotonic() - started) * 1000.0)
            if result is None:
                return None
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
                    )
                )
            except Exception as exc:  # pragma: no cover
                logger.debug("System One could not record %s: %s", qid, exc)
                return

    async def _sleep_backoff(self, attempt: int, retry_after: str | None = None) -> None:
        """Exponential backoff with jitter, honouring Retry-After."""
        delay = None
        if retry_after:
            try:
                delay = float(retry_after)
            except (TypeError, ValueError):
                delay = None
        if delay is None:
            delay = min(0.25 * (2**attempt), 4.0)
        await asyncio.sleep(delay + random.uniform(0, 0.15))

    def _to_result(self, data: dict[str, Any], latency_ms: float) -> EvaluationResult | None:
        raw_answers = data.get("answers")
        if not isinstance(raw_answers, dict) or not raw_answers:
            logger.warning("System One response carried no answers; falling back.")
            return None
        answers: dict[str, Answer] = {}
        for qid, raw in raw_answers.items():
            parsed = _parse_answer(qid, raw if isinstance(raw, dict) else {})
            if parsed is not None:
                answers[qid] = parsed
        if not answers:
            return None
        usage = data.get("usage") or {}
        return EvaluationResult(
            answers=answers,
            model=str(data.get("model", "")),
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
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
    criteria: dict[str, str],
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
    if answer is None or answer.type != expected:
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
    return {qid: a for qid, a in result.answers.items() if a.meets(threshold)}


def config_or_none() -> SystemOneConfig | None:
    """Return the active System One config, or None if loading fails."""
    try:
        from alpha.config import get_app_config

        return get_app_config().system_one
    except Exception:  # pragma: no cover - config errors must never break callers
        return None


__all__ = [
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
    "evaluate_many",
    "get_system_one_client",
    "reset_system_one_client",
]
