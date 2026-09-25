"""Real-time voice WebSocket authorization, limits, and conversation protocol."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from alpha.config.voice_config import StreamingConfig, VoiceConfig
from alpha.multimodal import chain
from alpha.multimodal.capabilities import CapabilityResult
from app.gateway.deps import get_config
from app.gateway.routers import multimodal as multimodal_router


class EnergyDetector:
    """Deterministic production-detector stand-in; no webrtcvad package needed."""

    def __init__(self, *, sample_rate: int, frame_ms: int):
        del sample_rate, frame_ms

    def is_speech(self, frame: bytes) -> bool:
        return any(frame)


def _app(voice: VoiceConfig) -> FastAPI:
    app = FastAPI()
    app.include_router(multimodal_router.router)
    app.dependency_overrides[get_config] = lambda: SimpleNamespace(voice=voice)
    return app


def _patch_ws(monkeypatch, voice: VoiceConfig, *, permissions: list[str] | None = None):
    monkeypatch.setattr(
        multimodal_router,
        "_authenticate_ws",
        AsyncMock(return_value=SimpleNamespace(id="voice-user")),
    )
    monkeypatch.setattr(
        multimodal_router,
        "resolve_route_permissions",
        AsyncMock(return_value=["runs:create"] if permissions is None else permissions),
    )
    monkeypatch.setattr(multimodal_router, "get_config", lambda: SimpleNamespace(voice=voice))
    monkeypatch.setattr(
        chain,
        "capabilities_report",
        lambda: {"rows": [], "voice": {"enabled": voice.enabled}, "note": "test observation"},
    )
    monkeypatch.setattr(multimodal_router, "WebRtcSpeechDetector", EnergyDetector)
    multimodal_router._VOICE_SESSION_LIMITER.clear_for_test()


def _b64(frame: bytes) -> str:
    return base64.b64encode(frame).decode("ascii")


def _speech_frame(sample_rate: int = 8_000, frame_ms: int = 20) -> bytes:
    return b"\x01\x00" * (sample_rate * frame_ms // 1_000)


def _silence_frame(sample_rate: int = 8_000, frame_ms: int = 20) -> bytes:
    return b"\x00\x00" * (sample_rate * frame_ms // 1_000)


def _receive_until(ws, event_type: str):
    for _ in range(20):
        event = ws.receive_json()
        if event.get("type") == event_type:
            return event
    raise AssertionError(f"did not receive event type {event_type!r}")


def test_voice_ws_requires_runs_create_permission(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig(), permissions=["runs:read"])
    with TestClient(_app(VoiceConfig())) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/multimodal/voice"):
                pass
    assert exc_info.value.code == 4403


def test_conversation_start_reports_missing_vad_without_closing_socket(monkeypatch):
    voice = VoiceConfig()
    _patch_ws(monkeypatch, voice)

    def missing_vad(**_kwargs):
        raise multimodal_router.SpeechDetectorUnavailable("webrtcvad-wheels is not installed")

    monkeypatch.setattr(multimodal_router, "WebRtcSpeechDetector", missing_vad)
    with TestClient(_app(voice)) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            _receive_until(ws, "capabilities")
            ws.send_json({"type": "conversation_start"})
            event = ws.receive_json()
            assert event["type"] == "engine"
            assert event["capability"] == "speech_detector"
            assert event["status"] == "not_installed"
            ws.send_json({"type": "ping"})
            assert _receive_until(ws, "status")["event"] == "pong"


def test_conversation_emits_partial_then_final_and_resets_utterance_id(monkeypatch):
    voice = VoiceConfig(
        streaming=StreamingConfig(
            sample_rate=8_000,
            frame_ms=20,
            pre_roll_ms=40,
            speech_start_ms=40,
            endpoint_silence_ms=100,
            partial_interval_ms=100,
            max_utterance_seconds=2,
            max_frame_bytes=4_096,
            max_sessions=2,
        )
    )
    _patch_ws(monkeypatch, voice)
    calls: list[dict] = []

    def fake_invoke(capability, payload):
        assert capability == "stt"
        calls.append(payload)
        return CapabilityResult(
            ok=True,
            capability="stt",
            tier="T3",
            engine="faster-whisper",
            data={"text": f"turn-{len(calls)}", "language": "en"},
            attempts=[],
            note="local",
        )

    monkeypatch.setattr(chain, "invoke", fake_invoke)

    with TestClient(_app(voice)) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            _receive_until(ws, "capabilities")
            ws.send_json({"type": "conversation_start"})
            started = _receive_until(ws, "status")
            assert started["event"] == "started"

            for _ in range(7):
                ws.send_json({"type": "audio", "data": _b64(_speech_frame())})
            partial = _receive_until(ws, "transcript")
            assert partial["final"] is False
            assert partial["utterance_id"] == 1
            assert partial["text"] == "turn-1"

            for _ in range(5):
                ws.send_json({"type": "audio", "data": _b64(_silence_frame())})
            final = _receive_until(ws, "transcript")
            assert final["final"] is True
            assert final["utterance_id"] == 1

            for _ in range(7):
                ws.send_json({"type": "audio", "data": _b64(_speech_frame())})
            second_partial = _receive_until(ws, "transcript")
            assert second_partial["final"] is False
            assert second_partial["utterance_id"] == 2
            for _ in range(5):
                ws.send_json({"type": "audio", "data": _b64(_silence_frame())})
            second = _receive_until(ws, "transcript")
            assert second["final"] is True
            assert second["utterance_id"] == 2

            ws.send_json({"type": "conversation_stop"})
            stopped = _receive_until(ws, "status")
            assert stopped["event"] == "stopped"

    assert len(calls) == 4  # one partial + one final for each of two utterances


def test_endpoint_and_next_speech_in_one_chunk_preserve_ordered_final(monkeypatch):
    voice = VoiceConfig(
        streaming=StreamingConfig(
            sample_rate=8_000,
            frame_ms=20,
            pre_roll_ms=40,
            speech_start_ms=40,
            endpoint_silence_ms=100,
            partial_interval_ms=100,
            max_utterance_seconds=2,
            max_frame_bytes=4_096,
            max_sessions=2,
        )
    )
    _patch_ws(monkeypatch, voice)
    result = CapabilityResult(
        ok=True,
        capability="stt",
        tier="T3",
        engine="faster-whisper",
        data={"text": "first-final", "language": "en"},
        attempts=[],
        note="local",
    )
    monkeypatch.setattr(chain, "invoke", lambda *_args, **_kwargs: result)

    # Nine exact 20ms frames: start id=1, five silence frames close it, then two
    # speech frames start id=2. Endpointing has advanced to id=2 before the router
    # emits the ordered id=1 final event.
    combined = _speech_frame() * 2 + _silence_frame() * 5 + _speech_frame() * 2
    with TestClient(_app(voice)) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            _receive_until(ws, "capabilities")
            ws.send_json({"type": "conversation_start"})
            _receive_until(ws, "status")
            ws.send_json({"type": "audio", "data": _b64(combined)})
            final = _receive_until(ws, "transcript")
            assert final["final"] is True
            assert final["utterance_id"] == 1
            assert final["text"] == "first-final"


def test_oversize_encoded_and_decoded_frames_are_rejected_before_decode_or_stt(monkeypatch):
    voice = VoiceConfig(streaming=StreamingConfig(max_frame_bytes=640, max_sessions=2))
    _patch_ws(monkeypatch, voice)
    monkeypatch.setattr(chain, "invoke", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("STT must not run")))

    with TestClient(_app(voice)) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            _receive_until(ws, "capabilities")
            ws.send_json({"type": "conversation_start"})
            _receive_until(ws, "status")

            ws.send_json({"type": "audio", "data": "A" * 900})
            encoded_error = ws.receive_json()
            assert "encoded audio frame exceeds" in encoded_error["message"]

            # 641 decoded bytes has the exact encoded bound for 640, then fails decoded validation.
            ws.send_json({"type": "audio", "data": _b64(b"\x00" * 641)})
            decoded_error = ws.receive_json()
            assert "decoded audio frame exceeds" in decoded_error["message"]

            # The same socket remains reusable after either client error.
            ws.send_json({"type": "ping"})
            pong = _receive_until(ws, "status")
            assert pong["event"] == "pong"


def test_direct_transcribe_allows_a_full_utterance_beyond_one_ws_frame(monkeypatch):
    voice = VoiceConfig(
        streaming=StreamingConfig(
            sample_rate=8_000,
            max_utterance_seconds=2,
            max_frame_bytes=640,
            max_sessions=2,
        )
    )
    _patch_ws(monkeypatch, voice)
    result = CapabilityResult(
        ok=True,
        capability="stt",
        tier="T3",
        engine="faster-whisper",
        data={"text": "long push to talk", "language": "en"},
        attempts=[],
        note="local",
    )
    calls = []
    monkeypatch.setattr(chain, "invoke", lambda capability, payload: calls.append((capability, payload)) or result)

    # max_frame_bytes is ~40 ms at 8 kHz, while push-to-talk must be able to
    # carry the complete bounded utterance (up to two seconds here).
    with TestClient(_app(voice)) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            _receive_until(ws, "capabilities")
            ws.send_json({"type": "transcribe", "data": _b64(b"\x00\x00" * 2_048)})
            transcript = _receive_until(ws, "transcript")

    assert transcript["text"] == "long push to talk"
    assert calls[0][0] == "stt"


def test_direct_transcribe_is_voice_gated(monkeypatch):
    voice = VoiceConfig(enabled=False)
    _patch_ws(monkeypatch, voice)
    monkeypatch.setattr(chain, "invoke", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("STT must not run")))

    with TestClient(_app(voice)) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            _receive_until(ws, "capabilities")
            ws.send_json({"type": "transcribe", "data": _b64(_silence_frame())})
            event = ws.receive_json()
    assert event == {
        "type": "engine",
        "capability": "stt",
        "status": "not_configured",
        "detail": "voice features are disabled (voice.enabled=false)",
    }


def test_process_local_session_limit_rejects_only_the_excess_socket(monkeypatch):
    voice = VoiceConfig(streaming=StreamingConfig(max_sessions=1))
    _patch_ws(monkeypatch, voice)

    with TestClient(_app(voice)) as client:
        first = client.websocket_connect("/api/multimodal/voice")
        first_socket = first.__enter__()
        _receive_until(first_socket, "capabilities")

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/multimodal/voice"):
                pass
        assert exc_info.value.code == 4429

        first.__exit__(None, None, None)
        with client.websocket_connect("/api/multimodal/voice") as reopened:
            _receive_until(reopened, "capabilities")
