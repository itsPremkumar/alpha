"""T1 engines: configured models with a capability, OpenAI-compatible HTTP.

Failover contract (see ``alpha.multimodal.chain``): both retryable and
deterministic failures advance to the next model in config order; each hop is
recorded with its real ``retryable`` flag. Keys and base URLs are read from
config only (model extras, then its provider profile) — no secrets exist in
this module. Non-2xx answers raise the free-router ``ProviderError`` so
``alpha.models.fallback.is_retryable_llm_error`` classifies 429/5xx as
retryable and deterministic 4xx as not, reusing existing machinery untouched.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable, Iterable
from functools import partial
from typing import Any

import httpx

from alpha.multimodal.capabilities import Capability, CapabilityResult

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60.0
IMAGE_TIMEOUT_SECONDS = 120.0

_CHAT_PATH = "/chat/completions"
_SPEECH_PATH = "/audio/speech"
_TRANSCRIPTIONS_PATH = "/audio/transcriptions"
_IMAGES_PATH = "/images/generations"

_VISION_OCR_PROMPT = {
    str(Capability.OCR): "Extract all text from this image verbatim, preserving line breaks. Output only the extracted text.",
    str(Capability.VISION): "Describe this image concisely and factually.",
}


def _endpoint(base_url: str, path: str) -> str:
    """Join a provider base URL with an OpenAI-style path (``/v1`` aware)."""
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("model has no base_url/endpoint configured")
    if base.endswith("/v1"):
        return f"{base}{path}"
    return f"{base}/v1{path}"


def _model_extra(model: Any) -> dict[str, Any]:
    extra = getattr(model, "model_extra", None)
    if isinstance(extra, dict):
        return extra
    return {}


def _profile_extra(model: Any, config: Any) -> dict[str, Any]:
    provider_name = getattr(model, "provider", None)
    if not provider_name:
        return {}
    get_provider = getattr(config, "get_provider_config", None)
    profile = get_provider(str(provider_name)) if callable(get_provider) else None
    extra = getattr(profile, "model_extra", None)
    return dict(extra) if isinstance(extra, dict) else {}


def _resolve(model: Any, keys: tuple[str, ...]) -> Any:
    """Model-level key wins, then its provider profile (documented precedence)."""
    from alpha.config.app_config import get_app_config

    try:
        config = get_app_config()
    except Exception:  # noqa: BLE001 - observation-only fallback: model extras still apply
        config = None
    extra = _model_extra(model)
    for key in keys:
        value = extra.get(key)
        if value:
            return value
    if config is not None:
        profile = _profile_extra(model, config)
        for key in keys:
            value = profile.get(key)
            if value:
                return value
    return None


def _base_url(model: Any) -> str:
    value = _resolve(model, ("base_url", "api_base", "endpoint", "api_url", "url"))
    if not value:
        raise ValueError(f"model '{getattr(model, 'name', model)}' declares no base_url/endpoint")
    return str(value)


def _model_name(model: Any) -> str:
    return str(getattr(model, "name", model))


def _api_model_id(model: Any) -> str:
    return str(getattr(model, "model", None) or getattr(model, "name", model))


def _headers(model: Any) -> dict[str, str]:
    key = _resolve(model, ("api_key", "api_token", "token"))
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def _raise_for_status(response: httpx.Response, engine: str) -> None:
    from alpha.models.free_router.providers import ProviderError

    if response.status_code >= 400:
        snippet = (response.text or "").strip().replace("\n", " ")[:300]
        raise ProviderError(engine, f"HTTP {response.status_code}: {snippet}", response.status_code)


def _post_json(model: Any, path: str, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    url = _endpoint(_base_url(model), path)
    response = httpx.post(url, json=body, headers=_headers(model), timeout=timeout)
    _raise_for_status(response, _model_name(model))
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"model '{_model_name(model)}' returned a non-object JSON payload")
    return payload


def _attempt_models(
    models: Iterable[Any],
    dispatch: Callable[[Any], dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[tuple[Any, BaseException]]]:
    """Try each model through *dispatch* until one returns data.

    Dependency-injection seam: tests inject a fake ``dispatch`` to exercise
    failover order without HTTP. Both retryable and deterministic failures
    advance within T1 — the caller records every failure with its real
    retryable flag.
    """
    failures: list[tuple[Any, BaseException]] = []
    for model in models:
        try:
            return dispatch(model), failures
        except Exception as exc:  # noqa: BLE001 - classified per row; BaseExceptions propagate
            logger.info("T1 model '%s' failed, trying next: %s", _model_name(model), type(exc).__name__)
            failures.append((model, exc))
    return None, failures


def _dispatch_tts(model: Any, payload: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": _api_model_id(model),
        "input": payload["text"],
        "response_format": "mp3",
    }
    voice = payload.get("voice")
    if voice:
        body["voice"] = str(voice)
    url = _endpoint(_base_url(model), _SPEECH_PATH)
    response = httpx.post(url, json=body, headers=_headers(model), timeout=DEFAULT_TIMEOUT_SECONDS)
    _raise_for_status(response, _model_name(model))
    audio = response.content or b""
    if not audio:
        raise ValueError(f"model '{_model_name(model)}' returned 0 audio bytes")
    return {"audio": audio, "media_type": response.headers.get("content-type", "audio/mpeg").split(";")[0]}


def _dispatch_stt(model: Any, payload: dict[str, Any]) -> dict[str, Any]:
    audio = payload["audio"]
    suffix = str(payload.get("suffix") or ".wav")
    files = {"file": (f"audio{suffix}", audio, "application/octet-stream")}
    data: dict[str, Any] = {"model": _api_model_id(model)}
    language = payload.get("language")
    if language:
        data["language"] = str(language)
    url = _endpoint(_base_url(model), _TRANSCRIPTIONS_PATH)
    response = httpx.post(url, data=data, files=files, headers=_headers(model), timeout=DEFAULT_TIMEOUT_SECONDS)
    _raise_for_status(response, _model_name(model))
    result = response.json()
    text = result.get("text") if isinstance(result, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"model '{_model_name(model)}' returned no transcript text")
    return {"text": text, "language": (result.get("language") if isinstance(result, dict) else None) or language}


def _dispatch_image_gen(model: Any, payload: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": _api_model_id(model),
        "prompt": payload["prompt"],
        "n": 1,
    }
    if payload.get("size"):
        body["size"] = str(payload["size"])
    result = _post_json(model, _IMAGES_PATH, body, timeout=IMAGE_TIMEOUT_SECONDS)
    entries = result.get("data") if isinstance(result, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"model '{_model_name(model)}' returned no image entries")
    first = entries[0] if isinstance(entries[0], dict) else {}
    if first.get("b64_json"):
        return {"b64": str(first["b64_json"])}
    if first.get("url"):
        return {"url": str(first["url"])}
    raise ValueError(f"model '{_model_name(model)}' image entry carries neither b64_json nor url")


def _data_uri(payload: dict[str, Any]) -> str:
    image = payload.get("image")
    if isinstance(image, (bytes, bytearray)):
        encoded = base64.b64encode(bytes(image)).decode("ascii")
        suffix = str(payload.get("suffix") or ".png").lstrip(".").lower()
        mime = {"jpg": "jpeg"}.get(suffix, suffix) or "png"
        return f"data:image/{mime};base64,{encoded}"
    b64 = payload.get("image_b64")
    if isinstance(b64, str) and b64:
        return f"data:image/png;base64,{b64}"
    raise ValueError("vision/ocr payload requires 'image' bytes or 'image_b64'")


def _dispatch_vision(model: Any, capability: Capability, payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or payload.get("question") or _VISION_OCR_PROMPT[str(capability)])
    body = {
        "model": _api_model_id(model),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _data_uri(payload)}},
                ],
            }
        ],
    }
    result = _post_json(model, _CHAT_PATH, body, timeout=DEFAULT_TIMEOUT_SECONDS)
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"model '{_model_name(model)}' returned no chat choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"model '{_model_name(model)}' returned empty vision content")
    return {"text": text}


def run_t1(capability: Capability, payload: dict[str, Any], attempts: list[dict[str, Any]]) -> CapabilityResult:
    """Serve *capability* from configured models (T1), or skip/exhaust honestly."""
    from alpha.multimodal.chain import (
        SKIP_NOT_CONFIGURED,
        TIER_T1,
        TierExhausted,
        TierSkip,
        failure_row,
        skip_row,
    )

    cap = Capability(str(capability))
    # Lazy to avoid a module-level cycle: chain imports this module lazily too.
    from alpha.multimodal.chain import _models_with_capability

    models = _models_with_capability(cap)
    if not models:
        detail = f"no configured model declares capability '{cap}'"
        raise TierSkip(SKIP_NOT_CONFIGURED, detail, rows=[skip_row(TIER_T1, "(none)", SKIP_NOT_CONFIGURED, detail)])

    dispatch: Callable[[Any], dict[str, Any]]
    if cap is Capability.TTS:
        dispatch = partial(_dispatch_tts, payload=payload)
    elif cap is Capability.STT:
        dispatch = partial(_dispatch_stt, payload=payload)
    elif cap is Capability.IMAGE_GEN:
        dispatch = partial(_dispatch_image_gen, payload=payload)
    elif cap in (Capability.VISION, Capability.OCR):
        dispatch = partial(_dispatch_vision, capability=cap, payload=payload)
    else:
        detail = f"capability '{cap}' has no configured-model (T1) strategy"
        raise TierSkip(
            "skipped_no_provider",
            detail,
            rows=[skip_row(TIER_T1, "(none)", "skipped_no_provider", detail)],
        )

    data, failures = _attempt_models(models, dispatch)
    if data is None:
        rows = [failure_row(TIER_T1, _model_name(model), exc) for model, exc in failures]
        raise TierExhausted(rows) from (failures[-1][1] if failures else None)

    attempts.extend(failure_row(TIER_T1, _model_name(model), exc) for model, exc in failures)
    engine = _model_name(models[len(failures)])
    return CapabilityResult(
        ok=True,
        capability=str(cap),
        engine=engine,
        data=data,
        note=f"served by configured model '{engine}' via its OpenAI-compatible endpoint (T1)",
    )
