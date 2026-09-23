"""Multimodal HTTP/WebSocket API: capabilities, TTS, STT, OCR, image-gen, voice WS.

Authorized additive router for the voice/multimodal wave (contract
``references/ALPHA_VOICE_MULTIMODAL_PLAN.md`` §5):

* Every chain call runs through ``asyncio.to_thread`` — ``chain.invoke`` is
  synchronous and can block on real engines.
* Exhaustion is a 503 whose ``detail`` is the honest attempts JSON
  (``error/capability/attempts/message``), never placeholder bytes/text.
* The WebSocket reuses ``browser.py``'s ``_authenticate_ws`` /
  ``_ws_origin_allowed`` by import only — zero auth-module edits (4401/4403).
* Upload limits reuse ``alpha.media.stt``'s documented suffixes and 25 MiB cap;
  images get an explicit suffix allowlist and a 15 MiB cap.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field

from alpha.config.app_config import AppConfig
from alpha.config.voice_config import VoiceConfig
from alpha.media.stt import MAX_AUDIO_MB, SUPPORTED_SUFFIXES
from alpha.multimodal import chain
from alpha.multimodal.errors import MultimodalUnavailableError
from alpha.multimodal.wakeword import InvalidFrameError, WakeWordSession
from alpha.multimodal.wav import WavError, pcm16_to_wav
from app.gateway.authz import require_permission
from app.gateway.deps import get_config
from app.gateway.routers.browser import _authenticate_ws, _ws_origin_allowed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/multimodal", tags=["multimodal"])

MAX_TTS_CHARS = 4000
MAX_IMAGE_PROMPT_CHARS = 4000
MAX_IMAGE_BYTES = 15 * 1024 * 1024
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"})
MAX_AUDIO_BYTES = int(MAX_AUDIO_MB * 1024 * 1024)


class TtsRequest(BaseModel):
    text: str = Field(..., description="Text to synthesize")
    voice: str | None = Field(default=None, description="Engine voice id; null uses engine/config default")
    engine: str | None = Field(default=None, description="Optional engine hint recorded in the chain payload")


class ImageGenRequest(BaseModel):
    prompt: str = Field(..., description="Image generation prompt")
    size: str | None = Field(default=None, description="Optional size like '512x512'")


def _voice_config(config: AppConfig) -> VoiceConfig:
    return config.voice


def _require_voice_enabled(config: AppConfig) -> None:
    if not _voice_config(config).enabled:
        raise HTTPException(status_code=404, detail="Voice features are disabled (voice.enabled=false)")


def _unavailable(exc: MultimodalUnavailableError) -> HTTPException:
    """Honest 503: the real attempt list, never a fabricated success."""
    return HTTPException(
        status_code=503,
        detail={
            "error": "multimodal_unavailable",
            "capability": exc.capability,
            "attempts": exc.attempts,
            "message": str(exc),
        },
    )


def _success_payload(result: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "text": result.data.get("text"),
        "engine": result.engine,
        "tier": result.tier,
        "attempts": result.attempts,
        "note": result.note,
    }


@router.get(
    "/capabilities",
    summary="Multimodal Capability Matrix",
    description=(
        "Honest availability matrix: one row per capability x tier x engine with "
        "status in available|not_installed|not_configured|skipped_no_provider|probe_failed, "
        "plus the observed voice config. Import/config observation only — reachability is never probed."
    ),
)
@require_permission("runs", "read")
async def capabilities(request: Request) -> dict[str, Any]:
    del request  # Required by the auth decorator.
    return await asyncio.to_thread(chain.capabilities_report)


@router.post(
    "/tts",
    summary="Text to Speech",
    description=(
        f"Synthesize speech through the T1->T2->T3 chain (max {MAX_TTS_CHARS} characters). "
        "Returns binary audio with X-Alpha-Engine / X-Alpha-Tier headers; 503 carries the attempts JSON."
    ),
)
@require_permission("runs", "create")
async def tts(body: TtsRequest, request: Request, config: AppConfig = Depends(get_config)) -> Response:
    del request  # Required by the auth decorator.
    _require_voice_enabled(config)

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")
    if len(text) > MAX_TTS_CHARS:
        raise HTTPException(status_code=422, detail=f"text exceeds {MAX_TTS_CHARS} characters")

    payload: dict[str, Any] = {"text": text, "voice": body.voice or config.voice.tts.voice, "engine": body.engine}
    try:
        result = await asyncio.to_thread(chain.invoke, "tts", payload)
    except MultimodalUnavailableError as exc:
        raise _unavailable(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    audio = result.data.get("audio")
    if not isinstance(audio, (bytes, bytearray)) or not audio:
        # An engine that "succeeded" with zero bytes is treated as exhaustion —
        # no placeholder silence is ever returned.
        raise HTTPException(
            status_code=503,
            detail={
                "error": "multimodal_unavailable",
                "capability": "tts",
                "attempts": result.attempts,
                "message": "serving engine returned 0 audio bytes",
            },
        )
    media_type = str(result.data.get("media_type") or "application/octet-stream")
    return Response(
        content=bytes(audio),
        media_type=media_type,
        headers={
            "X-Alpha-Engine": str(result.engine or "unspecified"),
            "X-Alpha-Tier": str(result.tier or "unspecified"),
        },
    )


@router.post(
    "/stt",
    summary="Speech to Text",
    description=(
        f"Multipart audio -> transcript via the chain (suffixes {sorted(SUPPORTED_SUFFIXES)}, "
        f"max {MAX_AUDIO_MB:.0f} MiB; 422 on bad suffix/oversize, 503 + attempts JSON on exhaustion)."
    ),
)
@require_permission("runs", "create")
async def stt(request: Request, config: AppConfig = Depends(get_config), audio: UploadFile = File(...)) -> dict[str, Any]:
    del request  # Required by the auth decorator.
    _require_voice_enabled(config)

    suffix = Path(audio.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=422, detail=f"unsupported audio type '{suffix}'; supported: {sorted(SUPPORTED_SUFFIXES)}")
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="audio upload is empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=422, detail=f"audio file exceeds {MAX_AUDIO_MB:.0f} MiB")

    payload: dict[str, Any] = {
        "audio": data,
        "suffix": suffix,
        "model_size": config.voice.stt.model_size,
        "language": config.voice.stt.language,
    }
    try:
        result = await asyncio.to_thread(chain.invoke, "stt", payload)
    except MultimodalUnavailableError as exc:
        raise _unavailable(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _success_payload(result)


@router.post(
    "/ocr",
    summary="Image to Text (OCR)",
    description=(
        f"Multipart image -> text via rapidocr -> tesseract -> vision-T1 (suffixes {sorted(IMAGE_SUFFIXES)}, "
        f"max {MAX_IMAGE_BYTES // (1024 * 1024)} MiB; 503 + attempts JSON on exhaustion)."
    ),
)
@require_permission("runs", "create")
async def ocr(request: Request, image: UploadFile = File(...)) -> dict[str, Any]:
    del request  # Required by the auth decorator.

    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=422, detail=f"unsupported image type '{suffix}'; supported: {sorted(IMAGE_SUFFIXES)}")
    data = await image.read()
    if not data:
        raise HTTPException(status_code=422, detail="image upload is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=422, detail=f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MiB")

    payload: dict[str, Any] = {"image": data, "suffix": suffix}
    try:
        result = await asyncio.to_thread(chain.invoke, "ocr", payload)
    except MultimodalUnavailableError as exc:
        raise _unavailable(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _success_payload(result)


@router.post(
    "/image-gen",
    summary="Generate Image",
    description=(
        f"Prompt -> image via configured model (T1) or AI Horde anonymous (T2), max {MAX_IMAGE_PROMPT_CHARS} characters. "
        "Returns {url|b64, engine, tier, attempts, note}; 503 + attempts JSON on exhaustion."
    ),
)
@require_permission("runs", "create")
async def image_gen(body: ImageGenRequest, request: Request) -> dict[str, Any]:
    del request  # Required by the auth decorator.

    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")
    if len(prompt) > MAX_IMAGE_PROMPT_CHARS:
        raise HTTPException(status_code=422, detail=f"prompt exceeds {MAX_IMAGE_PROMPT_CHARS} characters")

    payload: dict[str, Any] = {"prompt": prompt, "size": body.size}
    try:
        result = await asyncio.to_thread(chain.invoke, "image_gen", payload)
    except MultimodalUnavailableError as exc:
        raise _unavailable(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response: dict[str, Any] = {
        "ok": True,
        "engine": result.engine,
        "tier": result.tier,
        "attempts": result.attempts,
        "note": result.note,
    }
    if result.data.get("b64"):
        response["b64"] = result.data["b64"]
    elif result.data.get("url"):
        response["url"] = result.data["url"]
    else:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "multimodal_unavailable",
                "capability": "image_gen",
                "attempts": result.attempts,
                "message": "serving engine returned neither url nor b64",
            },
        )
    return response


def _observed_voice_config() -> VoiceConfig:
    """Observed ``voice:`` config for the WS path (Depends does not apply here)."""
    try:
        return get_config().voice
    except Exception as exc:  # noqa: BLE001 - WS must still answer honestly without config
        logger.warning("voice config unavailable on WS path: %s", type(exc).__name__)
        return VoiceConfig()


def _transcribe_pcm(pcm: bytes, voice: VoiceConfig) -> Any:
    """Sync wake/transcribe handoff: PCM16 -> WAV -> chain STT (runs in a worker thread)."""
    wav_bytes = pcm16_to_wav(pcm)
    if len(wav_bytes) > MAX_AUDIO_BYTES:
        raise ValueError(f"audio exceeds {MAX_AUDIO_MB:.0f} MiB after wrapping")
    return chain.invoke(
        "stt",
        {
            "audio": wav_bytes,
            "suffix": ".wav",
            "model_size": voice.stt.model_size,
            "language": voice.stt.language,
        },
    )


@router.websocket("/voice")
async def voice_websocket(websocket: WebSocket) -> None:
    """Voice WebSocket: wake-arm, stream PCM16 frames, get wake/transcript events.

    Auth mirrors ``browser.py`` exactly (import/call only, zero auth edits):
    4401 unauthenticated, 4403 cross-origin upgrade. Server -> client events:
    ``capabilities`` (on connect), ``status``, ``score``/``wake`` (score always
    disclosed), ``transcript``, ``engine`` (honest failures), ``error``.
    Client -> server messages: ``arm``, ``disarm``, ``audio`` (b64 pcm16 16k
    mono), ``transcribe`` (b64 pcm16), ``ping``.
    """
    user = await _authenticate_ws(websocket)
    if user is None:
        await websocket.close(code=4401)
        return
    if not _ws_origin_allowed(websocket):
        await websocket.close(code=4403)
        return
    await websocket.accept()

    async def _send(event: dict[str, Any]) -> None:
        await websocket.send_text(json.dumps(event))

    voice = _observed_voice_config()
    state = "idle"

    try:
        report = await asyncio.to_thread(chain.capabilities_report)
    except Exception as exc:  # noqa: BLE001 - never fail the handshake on a probe bug
        report = {"rows": [], "voice": {}, "note": f"capabilities probe failed: {type(exc).__name__}: {str(exc)[:200]}"}
    await _send({"type": "capabilities", **report})

    session: WakeWordSession | None = None

    def _make_session(voice_cfg: VoiceConfig) -> WakeWordSession:
        def transcribe(pcm: bytes) -> str:
            result = _transcribe_pcm(pcm, voice_cfg)
            return str(result.data.get("text") or "")

        return WakeWordSession(threshold=voice_cfg.wake_word.threshold, transcribe_fn=transcribe)

    while True:
        try:
            message = await websocket.receive_json()
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 - malformed frame must not kill the socket
            await _send({"type": "error", "message": "invalid JSON message; expected an object with a 'type'"})
            continue
        if not isinstance(message, dict):
            await _send({"type": "error", "message": "invalid message; expected a JSON object"})
            continue

        kind = str(message.get("type") or "")
        if kind == "ping":
            await _send({"type": "status", "state": state, "event": "pong"})
            continue

        if kind == "arm":
            if not voice.enabled:
                await _send({"type": "engine", "capability": "wake_word", "status": "not_configured", "detail": "voice features are disabled (voice.enabled=false)"})
                continue
            session = _make_session(voice)
            arm_status = await asyncio.to_thread(session.arm)
            if arm_status.get("status") == "ready":
                state = "wake_armed"
                await _send({"type": "status", "state": state, **arm_status})
            else:
                session = None
                await _send({"type": "engine", "capability": "wake_word", "status": str(arm_status.get("status")), "detail": str(arm_status.get("detail"))})
            continue

        if kind == "disarm":
            session = None
            state = "idle"
            await _send({"type": "status", "state": state, "event": "disarmed"})
            continue

        if kind == "audio":
            if session is None:
                await _send({"type": "error", "message": "audio frames ignored: wake session is not armed (send {\"type\":\"arm\"} first)"})
                continue
            try:
                pcm = base64.b64decode(str(message.get("data") or ""), validate=True)
            except Exception:  # noqa: BLE001 - bad base64 is a client error, honestly reported
                await _send({"type": "error", "message": "audio frame is not valid base64"})
                continue
            try:
                event = await asyncio.to_thread(session.push_frame, pcm)
            except (InvalidFrameError, RuntimeError) as exc:
                await _send({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
                continue
            if event.get("type") == "wake":
                await _send(
                    {
                        "type": "wake",
                        "score": event["score"],
                        "threshold": event["threshold"],
                        "engine": event["engine"],
                        "frames": event["frames"],
                    }
                )
                if "transcript" in event:
                    await _send({"type": "transcript", "text": event["transcript"], "engine": session.engine_name if session else "openwakeword"})
                elif "transcript_error" in event:
                    await _send({"type": "engine", "capability": "stt", "status": "unavailable", "detail": event["transcript_error"]})
            elif int(event.get("frames", 0)) % 10 == 0:
                # Periodic score beacons keep the client honest about live frames
                # without flooding one event per frame.
                await _send({"type": "score", "score": event["score"], "threshold": event["threshold"], "frames": event["frames"]})
            continue

        if kind == "transcribe":
            try:
                pcm = base64.b64decode(str(message.get("data") or ""), validate=True)
            except Exception:  # noqa: BLE001 - bad base64 is a client error, honestly reported
                await _send({"type": "error", "message": "transcribe payload is not valid base64"})
                continue
            previous_state = state
            state = "processing"
            try:
                result = await asyncio.to_thread(_transcribe_pcm, pcm, voice)
            except MultimodalUnavailableError as exc:
                await _send({"type": "engine", "capability": "stt", "status": "unavailable", "detail": str(exc), "attempts": exc.attempts})
                state = previous_state
                continue
            except (WavError, ValueError) as exc:
                await _send({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
                state = previous_state
                continue
            state = "speaking"
            await _send({"type": "transcript", "text": str(result.data.get("text") or ""), "engine": result.engine, "tier": result.tier})
            state = "wake_armed" if session is not None else "idle"
            continue

        await _send({"type": "error", "message": f"unknown message type {kind!r}; expected arm|disarm|audio|transcribe|ping"})
