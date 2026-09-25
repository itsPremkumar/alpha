"""Multimodal HTTP/WebSocket API: capabilities, TTS, STT, OCR, image-gen, voice WS.

Authorized additive router for the voice/multimodal wave (contract
``references/ALPHA_VOICE_MULTIMODAL_PLAN.md`` §5):

* Every chain call runs through ``asyncio.to_thread`` — ``chain.invoke`` is
  synchronous and can block on real engines.
* Exhaustion is a 503 whose ``detail`` is the honest attempts JSON
  (``error/capability/attempts/message``), never placeholder bytes/text.
* The WebSocket reuses ``browser.py``'s authentication/origin helpers, then
  independently requires the authenticated user's ``runs:create`` permission.
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
from alpha.config.voice_config import VOICE_ID_PATTERN, StreamingConfig, VoiceConfig
from alpha.media.stt import MAX_AUDIO_MB, SUPPORTED_SUFFIXES
from alpha.multimodal import chain
from alpha.multimodal.endpointing import (
    EndpointEvent,
    EndpointingConfig,
    ProcessSessionLimiter,
    SpeechDetectorUnavailable,
    StreamingEndpointDetector,
    WebRtcSpeechDetector,
)
from alpha.multimodal.errors import MultimodalUnavailableError
from alpha.multimodal.wakeword import InvalidFrameError, WakeWordSession
from alpha.multimodal.wav import WavError, pcm16_to_wav
from app.gateway.authz import require_permission, resolve_route_permissions
from app.gateway.deps import get_config
from app.gateway.routers.browser import _authenticate_ws, _ws_origin_allowed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/multimodal", tags=["multimodal"])

MAX_TTS_CHARS = 4000
MAX_IMAGE_PROMPT_CHARS = 4000
MAX_IMAGE_BYTES = 15 * 1024 * 1024
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"})
MAX_AUDIO_BYTES = int(MAX_AUDIO_MB * 1024 * 1024)
_VOICE_SESSION_LIMITER = ProcessSessionLimiter()


def _max_encoded_audio_bytes(max_decoded_bytes: int) -> int:
    """Exact base64 framing bound checked before attempting an expensive decode."""

    return 4 * ((int(max_decoded_bytes) + 2) // 3)


class TtsRequest(BaseModel):
    text: str = Field(..., description="Text to synthesize")
    voice: str | None = Field(
        default=None,
        pattern=VOICE_ID_PATTERN,
        description="Safe Piper voice id; never a filesystem path. Null uses voice.tts.voice.",
    )
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


def _success_payload(result: Any, *, stt: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True, "text": result.data.get("text")}
    if stt:
        # Plan §5 STT contract: {ok, text, language, engine, tier, attempts, note}.
        payload["language"] = result.data.get("language")
    payload.update(
        {
            "engine": result.engine,
            "tier": result.tier,
            "attempts": result.attempts,
            "note": result.note,
        }
    )
    return payload


@router.get(
    "/capabilities",
    summary="Multimodal Capability Matrix",
    description=(
        "Honest availability matrix: one row per capability x tier x engine with "
        "status in available|not_installed|not_configured|skipped_no_provider|policy_disabled|probe_failed, "
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
    description=(f"Synthesize speech through the local-first capability chain (max {MAX_TTS_CHARS} characters). Returns binary audio with X-Alpha-Engine / X-Alpha-Tier headers; 503 carries the attempts JSON."),
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

    tts = config.voice.tts
    payload: dict[str, Any] = {
        "text": text,
        "voice": body.voice or tts.voice,
        "engine": body.engine or tts.engine,
        "model_path": tts.model_path,
        "length_scale": tts.length_scale,
        "noise_scale": tts.noise_scale,
        "volume": tts.volume,
    }
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
    description=(f"Multipart audio -> transcript via the chain (suffixes {sorted(SUPPORTED_SUFFIXES)}, max {MAX_AUDIO_MB:.0f} MiB; 422 on bad suffix/oversize, 503 + attempts JSON on exhaustion)."),
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

    stt = config.voice.stt
    payload: dict[str, Any] = {
        "audio": data,
        "suffix": suffix,
        "model_path": stt.model_path,
        "model_size": stt.model_size,
        "device": stt.device,
        "compute_type": stt.compute_type,
        "beam_size": stt.beam_size,
        "local_files_only": stt.local_files_only,
        "language": stt.language,
    }
    try:
        result = await asyncio.to_thread(chain.invoke, "stt", payload)
    except MultimodalUnavailableError as exc:
        raise _unavailable(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _success_payload(result, stt=True)


@router.post(
    "/ocr",
    summary="Image to Text (OCR)",
    description=(f"Multipart image -> text via rapidocr -> tesseract -> vision-T1 (suffixes {sorted(IMAGE_SUFFIXES)}, max {MAX_IMAGE_BYTES // (1024 * 1024)} MiB; 503 + attempts JSON on exhaustion)."),
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
    description=(f"Prompt -> image via configured model (T1) or AI Horde anonymous (T2), max {MAX_IMAGE_PROMPT_CHARS} characters. Returns {{url|b64, engine, tier, attempts, note}}; 503 + attempts JSON on exhaustion."),
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
    """Sync wake/PTT/conversation handoff: PCM16 -> WAV -> chain STT."""
    wav_bytes = pcm16_to_wav(pcm, sample_rate=voice.streaming.sample_rate)
    if len(wav_bytes) > MAX_AUDIO_BYTES:
        raise ValueError(f"audio exceeds {MAX_AUDIO_MB:.0f} MiB after wrapping")
    stt = voice.stt
    return chain.invoke(
        "stt",
        {
            "audio": wav_bytes,
            "suffix": ".wav",
            "model_path": stt.model_path,
            "model_size": stt.model_size,
            "device": stt.device,
            "compute_type": stt.compute_type,
            "beam_size": stt.beam_size,
            "local_files_only": stt.local_files_only,
            "language": stt.language,
        },
    )


def _decode_bounded_audio(
    data: Any,
    streaming: StreamingConfig,
    *,
    label: str,
    max_decoded_bytes: int | None = None,
) -> bytes:
    """Validate encoded and decoded size before base64 decode/STT work."""

    if not isinstance(data, str) or not data:
        raise ValueError(f"{label} payload is empty")
    decoded_limit = streaming.max_frame_bytes if max_decoded_bytes is None else int(max_decoded_bytes)
    if decoded_limit <= 0:
        raise ValueError(f"invalid decoded byte limit for {label}: {decoded_limit}")
    encoded_limit = _max_encoded_audio_bytes(decoded_limit)
    if len(data) > encoded_limit:
        raise ValueError(f"encoded {label} exceeds {encoded_limit} characters")
    try:
        decoded = base64.b64decode(data, validate=True)
    except Exception as exc:  # noqa: BLE001 - client framing error, socket stays open
        raise ValueError(f"{label} is not valid base64") from exc
    if not decoded:
        raise ValueError(f"{label} payload is empty")
    if len(decoded) > decoded_limit:
        raise ValueError(f"decoded {label} exceeds {decoded_limit} bytes")
    return decoded


@router.websocket("/voice")
async def voice_websocket(websocket: WebSocket) -> None:
    """Authenticated realtime voice + legacy wake/PTT over the existing JSON socket.

    Audio remains bounded base16-encoded PCM16 JSON. Conversation transcripts are
    metadata-only; the browser submits a final transcript through the existing
    thread-run/SSE path rather than this socket constructing a parallel run.
    """

    user = await _authenticate_ws(websocket)
    if user is None:
        await websocket.close(code=4401)
        return
    if not _ws_origin_allowed(websocket):
        await websocket.close(code=4403)
        return
    try:
        permissions = await resolve_route_permissions(user, is_internal=False)
    except Exception:  # noqa: BLE001 - authz provider failures fail closed
        permissions = []
    if "runs:create" not in permissions:
        await websocket.close(code=4403)
        return

    initial_voice = _observed_voice_config()
    if not _VOICE_SESSION_LIMITER.try_acquire(initial_voice.streaming.max_sessions):
        await websocket.close(code=4429)
        return

    try:
        await websocket.accept()

        async def _send(event: dict[str, Any]) -> None:
            await websocket.send_text(json.dumps(event))

        state = "idle"
        session: WakeWordSession | None = None
        conversation: StreamingEndpointDetector | None = None
        conversation_epoch = 0
        last_final_utterance_id = 0

        try:
            report = await asyncio.to_thread(chain.capabilities_report)
        except Exception as exc:  # noqa: BLE001 - never fail the handshake on a probe bug
            report = {"rows": [], "voice": {}, "note": f"capabilities probe failed: {type(exc).__name__}: {str(exc)[:200]}"}
        await _send({"type": "capabilities", **report})

        def _make_session(voice_cfg: VoiceConfig) -> WakeWordSession:
            transcript_source: dict[str, Any] = {}

            def transcribe(pcm: bytes) -> str:
                current_voice = _observed_voice_config()
                if not current_voice.enabled:
                    raise RuntimeError("voice features are disabled (voice.enabled=false)")
                result = _transcribe_pcm(pcm, current_voice)
                transcript_source["engine"] = result.engine
                transcript_source["tier"] = result.tier
                return str(result.data.get("text") or "")

            wake_session = WakeWordSession(threshold=voice_cfg.wake_word.threshold, transcribe_fn=transcribe)
            wake_session.transcript_source = transcript_source
            return wake_session

        async def _emit_endpoint_transcript(
            event: EndpointEvent,
            *,
            active: StreamingEndpointDetector,
            epoch: int,
        ) -> None:
            nonlocal conversation, conversation_epoch, last_final_utterance_id, state
            # Fence delayed work by conversation generation and monotonic utterance
            # order. A large exact-frame chunk may produce endpoint(id=N) followed by
            # speech_started(id=N+1), so current object state may already be N+1 when
            # the ordered N event is emitted.
            if conversation is not active or conversation_epoch != epoch or event.utterance_id > active.utterance_id:
                return
            if event.kind == "speech_started":
                state = "listening"
                await _send(
                    {
                        "type": "status",
                        "state": state,
                        "event": "speech_started",
                        "utterance_id": event.utterance_id,
                        "duration_ms": event.duration_ms,
                    }
                )
                return

            if event.is_final:
                last_final_utterance_id = event.utterance_id
            elif event.utterance_id <= last_final_utterance_id:
                return

            voice_cfg = _observed_voice_config()
            if not voice_cfg.enabled:
                conversation = None
                conversation_epoch += 1
                state = "idle"
                await _send(
                    {
                        "type": "engine",
                        "capability": "stt",
                        "status": "not_configured",
                        "detail": "voice features are disabled (voice.enabled=false)",
                        "final": event.is_final,
                        "utterance_id": event.utterance_id,
                    }
                )
                return

            try:
                result = await asyncio.to_thread(_transcribe_pcm, event.audio, voice_cfg)
            except MultimodalUnavailableError as exc:
                await _send(
                    {
                        "type": "engine",
                        "capability": "stt",
                        "status": "unavailable",
                        "detail": str(exc),
                        "attempts": exc.attempts,
                        "final": event.is_final,
                        "utterance_id": event.utterance_id,
                        "duration_ms": event.duration_ms,
                    }
                )
                return
            except (WavError, ValueError) as exc:
                await _send(
                    {
                        "type": "engine",
                        "capability": "stt",
                        "status": "failed",
                        "detail": f"{type(exc).__name__}: {exc}",
                        "final": event.is_final,
                        "utterance_id": event.utterance_id,
                    }
                )
                return

            # The active utterance id is checked again after the blocking handoff;
            # endpointing is serialized per socket, so no stale partial can overtake
            # the final marked event.
            if conversation is not active or conversation_epoch != epoch or event.utterance_id > active.utterance_id or (not event.is_final and event.utterance_id <= last_final_utterance_id):
                return
            await _send(
                {
                    "type": "transcript",
                    "text": str(result.data.get("text") or ""),
                    "language": result.data.get("language"),
                    "engine": result.engine,
                    "tier": result.tier,
                    "final": event.is_final,
                    "utterance_id": event.utterance_id,
                    "duration_ms": event.duration_ms,
                    "reason": event.reason,
                }
            )

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

            if kind == "conversation_start":
                voice_cfg = _observed_voice_config()
                if not voice_cfg.enabled:
                    await _send(
                        {
                            "type": "engine",
                            "capability": "speech_detector",
                            "status": "not_configured",
                            "detail": "voice features are disabled (voice.enabled=false)",
                        }
                    )
                    continue
                if conversation is not None:
                    await _send({"type": "error", "message": "conversation is already active"})
                    continue
                streaming = voice_cfg.streaming
                try:
                    detector = WebRtcSpeechDetector(sample_rate=streaming.sample_rate, frame_ms=streaming.frame_ms)
                    endpointing = StreamingEndpointDetector(
                        detector,
                        EndpointingConfig(
                            sample_rate=streaming.sample_rate,
                            frame_ms=streaming.frame_ms,
                            pre_roll_ms=streaming.pre_roll_ms,
                            speech_start_ms=streaming.speech_start_ms,
                            endpoint_silence_ms=streaming.endpoint_silence_ms,
                            partial_interval_ms=streaming.partial_interval_ms,
                            max_utterance_seconds=streaming.max_utterance_seconds,
                        ),
                    )
                except (SpeechDetectorUnavailable, ImportError) as exc:
                    await _send(
                        {
                            "type": "engine",
                            "capability": "speech_detector",
                            "status": "not_installed",
                            "detail": str(exc),
                        }
                    )
                    continue
                except ValueError as exc:
                    await _send(
                        {
                            "type": "engine",
                            "capability": "speech_detector",
                            "status": "not_configured",
                            "detail": str(exc),
                        }
                    )
                    continue
                conversation = endpointing
                conversation_epoch += 1
                last_final_utterance_id = 0
                state = "listening"
                await _send(
                    {
                        "type": "status",
                        "state": state,
                        "event": "started",
                        "sample_rate": streaming.sample_rate,
                        "frame_ms": streaming.frame_ms,
                    }
                )
                continue

            if kind == "conversation_stop":
                conversation = None
                conversation_epoch += 1
                state = "idle"
                await _send({"type": "status", "state": state, "event": "stopped"})
                continue

            if kind == "arm":
                voice_cfg = _observed_voice_config()
                if not voice_cfg.enabled:
                    await _send({"type": "engine", "capability": "wake_word", "status": "not_configured", "detail": "voice features are disabled (voice.enabled=false)"})
                    continue
                if conversation is not None:
                    await _send({"type": "error", "message": "stop conversation mode before arming wake word"})
                    continue
                session = _make_session(voice_cfg)
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
                voice_cfg = _observed_voice_config()
                if not voice_cfg.enabled:
                    if conversation is not None:
                        conversation = None
                        conversation_epoch += 1
                    session = None
                    state = "idle"
                    await _send({"type": "engine", "capability": "stt", "status": "not_configured", "detail": "voice features are disabled (voice.enabled=false)"})
                    continue
                try:
                    pcm = _decode_bounded_audio(message.get("data"), voice_cfg.streaming, label="audio frame")
                except ValueError as exc:
                    await _send({"type": "error", "message": str(exc)})
                    continue

                if conversation is not None:
                    active = conversation
                    epoch = conversation_epoch
                    try:
                        events = active.push(pcm)
                    except (TypeError, ValueError) as exc:
                        await _send({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
                        continue
                    for event in events:
                        await _emit_endpoint_transcript(event, active=active, epoch=epoch)
                    continue

                if session is None:
                    await _send({"type": "error", "message": 'audio frames ignored: wake session is not armed (send {"type":"arm"} first)'})
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
                        source = getattr(session, "transcript_source", None) or {}
                        await _send(
                            {
                                "type": "transcript",
                                "text": event["transcript"],
                                "language": None,
                                "engine": source.get("engine") or (session.engine_name if session else "openwakeword"),
                                "tier": source.get("tier"),
                                "final": True,
                            }
                        )
                    elif "transcript_error" in event:
                        await _send({"type": "engine", "capability": "stt", "status": "unavailable", "detail": event["transcript_error"], "final": True})
                elif int(event.get("frames", 0)) % 10 == 0:
                    await _send({"type": "score", "score": event["score"], "threshold": event["threshold"], "frames": event["frames"]})
                continue

            if kind == "transcribe":
                voice_cfg = _observed_voice_config()
                if not voice_cfg.enabled:
                    await _send(
                        {
                            "type": "engine",
                            "capability": "stt",
                            "status": "not_configured",
                            "detail": "voice features are disabled (voice.enabled=false)",
                        }
                    )
                    continue
                try:
                    max_transcribe_bytes = int(voice_cfg.streaming.sample_rate * 2 * voice_cfg.streaming.max_utterance_seconds)
                    pcm = _decode_bounded_audio(
                        message.get("data"),
                        voice_cfg.streaming,
                        label="transcribe payload",
                        max_decoded_bytes=max_transcribe_bytes,
                    )
                except ValueError as exc:
                    await _send({"type": "error", "message": str(exc)})
                    continue
                previous_state = state
                state = "processing"
                try:
                    result = await asyncio.to_thread(_transcribe_pcm, pcm, voice_cfg)
                except MultimodalUnavailableError as exc:
                    await _send({"type": "engine", "capability": "stt", "status": "unavailable", "detail": str(exc), "attempts": exc.attempts, "final": True})
                    state = previous_state
                    continue
                except (WavError, ValueError) as exc:
                    await _send({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
                    state = previous_state
                    continue
                await _send(
                    {
                        "type": "transcript",
                        "text": str(result.data.get("text") or ""),
                        "language": result.data.get("language"),
                        "engine": result.engine,
                        "tier": result.tier,
                        "final": True,
                    }
                )
                # Direct transcription does not synthesize or play audio. The
                # frontend speaks a later response through POST /tts and reports
                # that playback state itself.
                state = "wake_armed" if session is not None else "idle"
                continue

            await _send(
                {
                    "type": "error",
                    "message": f"unknown message type {kind!r}; expected arm|disarm|audio|transcribe|conversation_start|conversation_stop|ping",
                }
            )
    finally:
        _VOICE_SESSION_LIMITER.release()
