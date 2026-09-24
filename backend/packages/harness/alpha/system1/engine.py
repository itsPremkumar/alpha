"""System 1 fast decision engine: cloud Jev client + local free fallback.

Contract:

* The cloud Jev path is configured ONLY when ``$JEV_API_KEY`` is present in
  the environment (or injected explicitly for tests/DI). The key is never
  hardcoded, never logged, never committed. An absent key selects the local
  path with no error — the local free classifier is the DEFAULT engine.
* Blocking-I/O invariant: the sync API never performs HTTP while an event
  loop is running in the calling thread. In that situation the cloud call is
  skipped and the real local decision is returned with a disclosed
  ``fallback_reason``; async callers use ``achoice``/``ascore``/``anoul``
  (``httpx.AsyncClient``), which never block the loop.
* Fail-closed honesty: a cloud response violating its contract (winner outside
  the candidate set, malformed distribution, out-of-range score, non-boolean
  noul decision) is rejected and disclosed as a fallback — never accepted,
  never fabricated. When BOTH cloud and local paths raise, the exception
  propagates; no decision object is invented.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Sequence
from typing import Any

from alpha.system1.classifier import LocalReflexClassifier
from alpha.system1.models import (
    ENGINE_JEV,
    ENGINE_LOCAL,
    ChoiceRequest,
    ChoiceResult,
    NoulRequest,
    NoulResult,
    ScoreRequest,
    ScoreResult,
)

logger = logging.getLogger(__name__)

JEV_API_KEY_ENV = "JEV_API_KEY"
JEV_API_BASE_ENV = "JEV_API_BASE"
JEV_API_TIMEOUT_ENV = "JEV_API_TIMEOUT_SECONDS"
# Spec-referenced vendor endpoint (TypeSafe AI Jev); overridable per deploy.
DEFAULT_JEV_API_BASE = "https://api.typesafe.ai/v1"
DEFAULT_JEV_TIMEOUT_SECONDS = 3.0

_SYNC_LOOP_SKIP_REASON = "sync System 1 API called from a running event loop; cloud HTTP skipped to avoid blocking the loop (use achoice/ascore/anoul for the cloud path); decision computed by the local classifier"


def _short(exc: BaseException) -> str:
    return str(exc)[:300]


def _cloud_failure_reason(operation: str, exc: BaseException) -> str:
    return f"jev_cloud_failed during {operation}: {type(exc).__name__}: {_short(exc)}"


def _sync_cloud_block_reason() -> str | None:
    """None when blocking HTTP is safe here; otherwise the disclosed skip reason."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return None
    return _SYNC_LOOP_SKIP_REASON


def _validate_choice_request(context: str, candidates: Sequence[str]) -> ChoiceRequest:
    if not isinstance(context, str):
        raise ValueError(f"context must be a string, got {type(context).__name__}")
    if isinstance(candidates, (str, bytes)):
        raise ValueError("candidates must be a sequence of names, not a single string")
    cleaned: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError("every candidate must be a non-empty string")
        cleaned.append(candidate)
    return ChoiceRequest(context=context, candidates=cleaned)


def _validate_score_request(context: str, *, question: str, scale: tuple[float, float]) -> ScoreRequest:
    if not isinstance(context, str):
        raise ValueError(f"context must be a string, got {type(context).__name__}")
    if not isinstance(question, str):
        raise ValueError(f"question must be a string, got {type(question).__name__}")
    return ScoreRequest(context=context, question=question, scale=scale)


def _validate_noul_request(context: str, question: str, *, threshold: float) -> NoulRequest:
    if not isinstance(context, str):
        raise ValueError(f"context must be a string, got {type(context).__name__}")
    if not isinstance(question, str):
        raise ValueError(f"question must be a string, got {type(question).__name__}")
    return NoulRequest(context=context, question=question, threshold=threshold)


def _parse_choice_response(data: dict[str, Any], request: ChoiceRequest) -> ChoiceResult:
    result = ChoiceResult.model_validate({**data, "engine": ENGINE_JEV, "fallback_reason": None})
    if set(result.probabilities) != set(request.candidates):
        raise ValueError("cloud probability keys do not exactly match the candidate set")
    if result.winner not in request.candidates:
        raise ValueError(f"cloud winner {result.winner!r} is not in the candidate set")
    return result


def _parse_score_response(data: dict[str, Any], request: ScoreRequest) -> ScoreResult:
    return ScoreResult.model_validate({**data, "scale": request.scale, "engine": ENGINE_JEV, "fallback_reason": None})


def _parse_noul_response(data: dict[str, Any], request: NoulRequest) -> NoulResult:
    return NoulResult.model_validate({**data, "threshold": request.threshold, "engine": ENGINE_JEV, "fallback_reason": None})


class System1Engine:
    """Dual-engine System 1 harness (Jev cloud when keyed, local otherwise)."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_base: str | None = None,
        timeout_seconds: float | None = None,
        local_classifier: LocalReflexClassifier | None = None,
    ) -> None:
        resolved = api_key if api_key is not None else os.environ.get(JEV_API_KEY_ENV)
        self._api_key: str | None = resolved.strip() if isinstance(resolved, str) and resolved.strip() else None
        base = (api_base if api_base is not None else os.environ.get(JEV_API_BASE_ENV)) or DEFAULT_JEV_API_BASE
        self._api_base: str = base.rstrip("/")
        raw_timeout = timeout_seconds if timeout_seconds is not None else os.environ.get(JEV_API_TIMEOUT_ENV)
        try:
            self._timeout_seconds = float(raw_timeout) if raw_timeout is not None else DEFAULT_JEV_TIMEOUT_SECONDS
        except (TypeError, ValueError):
            logger.warning("Invalid %s=%r; using default %.1fs", JEV_API_TIMEOUT_ENV, raw_timeout, DEFAULT_JEV_TIMEOUT_SECONDS)
            self._timeout_seconds = DEFAULT_JEV_TIMEOUT_SECONDS
        self.local: LocalReflexClassifier = local_classifier or LocalReflexClassifier()
        if not self.use_cloud:
            logger.info("JEV_API_KEY not present; System 1 defaulting to the local free classifier (zero token cost, CPU-only)")

    @property
    def use_cloud(self) -> bool:
        """True only when a Jev API key was provided (env ``$JEV_API_KEY`` or explicit DI)."""
        return self._api_key is not None

    @property
    def backend(self) -> str:
        return ENGINE_JEV if self._api_key is not None else ENGINE_LOCAL

    # ------------------------------------------------------------------ sync API

    def choice(self, context: str, candidates: Sequence[str]) -> ChoiceResult:
        """Pick exactly one winner from ``candidates`` (local by default)."""
        request = _validate_choice_request(context, candidates)
        if self.use_cloud:
            block_reason = _sync_cloud_block_reason()
            if block_reason is None:
                try:
                    return _parse_choice_response(self._post_json("/choice", request.model_dump()), request)
                except Exception as exc:
                    return self._local_choice(request, _cloud_failure_reason("choice", exc))
            return self._local_choice(request, block_reason)
        return self._local_choice(request)

    def score(self, context: str, *, question: str = "", scale: tuple[float, float] = (0.0, 1.0)) -> ScoreResult:
        """Continuous ordered score with confidence (local by default)."""
        request = _validate_score_request(context, question=question, scale=scale)
        if self.use_cloud:
            block_reason = _sync_cloud_block_reason()
            if block_reason is None:
                try:
                    return _parse_score_response(self._post_json("/score", request.model_dump()), request)
                except Exception as exc:
                    return self._local_score(request, _cloud_failure_reason("score", exc))
            return self._local_score(request, block_reason)
        return self._local_score(request)

    def noul(self, context: str, question: str, *, threshold: float = 0.5) -> NoulResult:
        """Strict binary gate with probability (local by default)."""
        request = _validate_noul_request(context, question, threshold=threshold)
        if self.use_cloud:
            block_reason = _sync_cloud_block_reason()
            if block_reason is None:
                try:
                    return _parse_noul_response(self._post_json("/noul", request.model_dump()), request)
                except Exception as exc:
                    return self._local_noul(request, _cloud_failure_reason("noul", exc))
            return self._local_noul(request, block_reason)
        return self._local_noul(request)

    # ---------------------------------------------------------------- async API

    async def achoice(self, context: str, candidates: Sequence[str]) -> ChoiceResult:
        """Async cloud-safe variant (httpx.AsyncClient; never blocks the loop)."""
        request = _validate_choice_request(context, candidates)
        if self.use_cloud:
            try:
                return _parse_choice_response(await self._apost_json("/choice", request.model_dump()), request)
            except Exception as exc:
                return self._local_choice(request, _cloud_failure_reason("choice", exc))
        return self._local_choice(request)

    async def ascore(self, context: str, *, question: str = "", scale: tuple[float, float] = (0.0, 1.0)) -> ScoreResult:
        """Async cloud-safe variant of :meth:`score`."""
        request = _validate_score_request(context, question=question, scale=scale)
        if self.use_cloud:
            try:
                return _parse_score_response(await self._apost_json("/score", request.model_dump()), request)
            except Exception as exc:
                return self._local_score(request, _cloud_failure_reason("score", exc))
        return self._local_score(request)

    async def anoul(self, context: str, question: str, *, threshold: float = 0.5) -> NoulResult:
        """Async cloud-safe variant of :meth:`noul`."""
        request = _validate_noul_request(context, question, threshold=threshold)
        if self.use_cloud:
            try:
                return _parse_noul_response(await self._apost_json("/noul", request.model_dump()), request)
            except Exception as exc:
                return self._local_noul(request, _cloud_failure_reason("noul", exc))
        return self._local_noul(request)

    # ---------------------------------------------------------------- internals

    def _local_choice(self, request: ChoiceRequest, fallback_reason: str | None = None) -> ChoiceResult:
        result = self.local.predict_choice(request.context, request.candidates)
        if fallback_reason is None:
            return result
        return result.model_copy(update={"fallback_reason": fallback_reason})

    def _local_score(self, request: ScoreRequest, fallback_reason: str | None = None) -> ScoreResult:
        result = self.local.predict_score(request.context, question=request.question, scale=request.scale)
        if fallback_reason is None:
            return result
        return result.model_copy(update={"fallback_reason": fallback_reason})

    def _local_noul(self, request: NoulRequest, fallback_reason: str | None = None) -> NoulResult:
        result = self.local.predict_noul(request.context, request.question, threshold=request.threshold)
        if fallback_reason is None:
            return result
        return result.model_copy(update={"fallback_reason": fallback_reason})

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Blocking Jev HTTP call — only reached from the sync API off-loop."""
        import httpx

        if not self._api_key:
            raise RuntimeError("Jev cloud client invoked without an API key")
        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.post(f"{self._api_base}{path}", json=payload, headers=self._headers())
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"Jev cloud returned a non-object JSON payload ({type(data).__name__})")
        return data

    async def _apost_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Async Jev HTTP call (httpx.AsyncClient) for the async API."""
        import httpx

        if not self._api_key:
            raise RuntimeError("Jev cloud client invoked without an API key")
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(f"{self._api_base}{path}", json=payload, headers=self._headers())
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"Jev cloud returned a non-object JSON payload ({type(data).__name__})")
        return data


_ENGINE: System1Engine | None = None
_ENGINE_LOCK = threading.Lock()


def get_system1_engine() -> System1Engine:
    """Process-wide singleton engine.

    Resolves ``$JEV_API_KEY`` at first construction; call
    :func:`reset_system1_engine` after changing the environment in-process.
    """
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = System1Engine()
    return _ENGINE


def reset_system1_engine() -> None:
    """Drop the cached singleton (tests, or env changes between decisions)."""
    global _ENGINE
    with _ENGINE_LOCK:
        _ENGINE = None
