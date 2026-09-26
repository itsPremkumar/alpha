"""Router tests: HTTP contracts + voice WebSocket honesty (plan §8.3).

Every chain/engine call is stubbed at THE seams (``chain.invoke``,
``chain.capabilities_report``, ``wakeword.load_default_score_fn``,
``multimodal_router._authenticate_ws`` / ``.get_config``) — nothing fabricates
an engine: attempt rows in assertions come from the code under test, and
"must not run" seams raise loudly instead of silently passing.
"""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from alpha.config.voice_config import SttConfig, TtsConfig, VoiceConfig, WakeWordConfig
from alpha.multimodal import chain
from alpha.multimodal import wakeword as wakeword_mod
from alpha.multimodal.capabilities import CapabilityResult
from alpha.multimodal.errors import MultimodalUnavailableError
from app.gateway.deps import get_config
from app.gateway.routers import multimodal as multimodal_router


@pytest.fixture(autouse=True)
def _isolate_workspace(tmp_path, monkeypatch):
    """Plan §8: tests isolate AGENT_WORKSPACE_HOME to a temp dir."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FRAME = b"\x00\x00" * 100  # 200 bytes, even-length non-empty PCM16


def _report() -> dict:
    """Canned capabilities report covering all five probe statuses."""
    return {
        "rows": [
            {"capability": "tts", "tier": "T1", "engine": "(none)", "status": "not_configured", "detail": "no configured model declares capability 'tts' (import/config observation only; reachability not probed)"},
            {"capability": "tts", "tier": "T2", "engine": "edge-tts", "status": "available", "detail": "module 'edge_tts' imports (import/config observation only; reachability not probed)"},
            {"capability": "stt", "tier": "T3", "engine": "faster-whisper", "status": "not_installed", "detail": "ImportError: No module named 'faster_whisper' (import/config observation only; reachability not probed)"},
            {"capability": "ocr", "tier": "T2", "engine": "(none)", "status": "skipped_no_provider", "detail": "no keyless OCR provider ships with Alpha (import/config observation only; reachability not probed)"},
            {"capability": "vision", "tier": "T3", "engine": "rapidocr", "status": "probe_failed", "detail": "RuntimeError: probe exploded (import/config observation only; reachability not probed)"},
        ],
        "voice": {
            "enabled": True,
            "wake_word": {"engine": "openwakeword", "threshold": 0.5, "armed_default": False},
            "tts": {"autoplay": False, "voice": None},
            "stt": {"model_size": "small", "language": None},
        },
        "note": "statuses are import/config observations only; reachability not probed",
    }


def _app(voice: VoiceConfig | None = None) -> FastAPI:
    """Authed HTTP test app with the multimodal router + voice config override."""
    app = make_authed_test_app()
    app.include_router(multimodal_router.router)
    cfg = SimpleNamespace(voice=voice or VoiceConfig())
    app.dependency_overrides[get_config] = lambda: cfg
    return app


def _ws_app() -> FastAPI:
    """Bare app for WebSocket tests (auth comes from patched ``_authenticate_ws``)."""
    app = FastAPI()
    app.include_router(multimodal_router.router)
    return app


def _patch_ws(monkeypatch, voice: VoiceConfig, *, authenticated: bool = True, report: dict | None = None):
    """Patch the WS module seams: auth, config observation, capabilities report."""
    monkeypatch.setattr(
        multimodal_router,
        "_authenticate_ws",
        AsyncMock(return_value=SimpleNamespace(id="ws-user") if authenticated else None),
    )
    monkeypatch.setattr(multimodal_router, "get_config", lambda: SimpleNamespace(voice=voice))
    monkeypatch.setattr(multimodal_router, "resolve_route_permissions", AsyncMock(return_value=["runs:create"]))
    monkeypatch.setattr(chain, "capabilities_report", lambda: report if report is not None else _report())
    multimodal_router._VOICE_SESSION_LIMITER.clear_for_test()


def _stub_invoke(monkeypatch, *, result=None, error=None, calls=None):
    """Patch THE ``chain.invoke`` seam; records (capability, payload) into *calls*."""

    def fake(capability, payload):
        if calls is not None:
            calls.append((capability, payload))
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(chain, "invoke", fake)


def _fail_invoke(*_args, **_kwargs):
    raise AssertionError("chain.invoke must not be called for this request")


def _scripted_scorer(values):
    it = iter(values)

    def score(pcm):
        del pcm
        return next(it, 0.1)

    return score


def _expect_ws_close(app: FastAPI, code: int, *, headers: dict | None = None) -> None:
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/multimodal/voice", headers=headers or {}):
                pass
    assert exc_info.value.code == code


def _attempts(*, engine: str = "edge-tts", error: str = "ConnectionError") -> list[dict]:
    return [
        {"tier": "T1", "engine": "(none)", "error": "not_configured", "detail": "no configured model declares capability 'tts'", "retryable": None},
        {"tier": "T2", "engine": engine, "error": error, "detail": "connection refused", "retryable": True},
    ]


# ---------------------------------------------------------------------------
# GET /api/multimodal/capabilities
# ---------------------------------------------------------------------------


def test_capabilities_returns_probe_matrix_and_voice_block(monkeypatch):
    monkeypatch.setattr(chain, "capabilities_report", lambda: _report())
    with TestClient(_app()) as client:
        response = client.get("/api/multimodal/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"rows", "voice", "note"}
    assert all(row["status"] in chain.PROBE_STATUSES for row in body["rows"])
    assert body["voice"]["enabled"] is True
    assert body["voice"]["wake_word"] == {"engine": "openwakeword", "threshold": 0.5, "armed_default": False}
    assert "reachability not probed" in body["note"]


def test_capabilities_requires_authentication(monkeypatch):
    monkeypatch.setattr(chain, "capabilities_report", lambda: _report())
    app = FastAPI()
    app.include_router(multimodal_router.router)
    with TestClient(app) as client:
        response = client.get("/api/multimodal/capabilities")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/multimodal/tts
# ---------------------------------------------------------------------------


def test_tts_returns_audio_bytes_with_engine_headers_and_forwards_payload(monkeypatch):
    calls: list[tuple[str, dict]] = []
    result = CapabilityResult(
        ok=True,
        capability="tts",
        tier="T2",
        engine="edge-tts",
        data={"audio": b"ID3-fake-mp3-bytes", "media_type": "audio/mpeg"},
        attempts=_attempts(),
        note="served by edge-tts after T1 not_configured",
    )
    _stub_invoke(monkeypatch, result=result, calls=calls)

    with TestClient(_app()) as client:
        response = client.post("/api/multimodal/tts", json={"text": "  hello alpha  "})

    assert response.status_code == 200
    assert response.content == b"ID3-fake-mp3-bytes"
    assert response.headers["x-alpha-engine"] == "edge-tts"
    assert response.headers["x-alpha-tier"] == "T2"
    assert response.headers["content-type"].startswith("audio/mpeg")

    assert len(calls) == 1
    capability, payload = calls[0]
    assert capability == "tts"
    assert payload["text"] == "hello alpha"  # stripped
    assert set(payload) == {
        "text",
        "voice",
        "engine",
        "model_path",
        "length_scale",
        "noise_scale",
        "volume",
    }


def test_tts_uses_config_default_voice_unless_request_overrides(monkeypatch):
    calls: list[tuple[str, dict]] = []
    result = CapabilityResult(ok=True, capability="tts", tier="T3", engine="piper", data={"audio": b"RIFF", "media_type": "audio/wav"}, attempts=[], note="")
    _stub_invoke(monkeypatch, result=result, calls=calls)
    voice = VoiceConfig(tts=TtsConfig(voice="config-voice"))

    with TestClient(_app(voice)) as client:
        client.post("/api/multimodal/tts", json={"text": "hi"})
        client.post("/api/multimodal/tts", json={"text": "hi", "voice": "req-voice", "engine": "hinted"})

    assert calls[0][1]["voice"] == "config-voice"
    assert calls[1][1]["voice"] == "req-voice"
    assert calls[1][1]["engine"] == "hinted"


def test_tts_exhaustion_is_503_with_nested_attempts_detail(monkeypatch):
    error = MultimodalUnavailableError("tts", _attempts())
    _stub_invoke(monkeypatch, error=error)

    with TestClient(_app()) as client:
        response = client.post("/api/multimodal/tts", json={"text": "hello"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "multimodal_unavailable"
    assert detail["capability"] == "tts"
    assert detail["attempts"] == error.attempts
    assert detail["message"] == str(error)


def test_tts_zero_audio_bytes_is_503_not_silence(monkeypatch):
    result = CapabilityResult(ok=True, capability="tts", tier="T3", engine="piper", data={"audio": b"", "media_type": "audio/wav"}, attempts=_attempts(engine="piper"), note="")
    _stub_invoke(monkeypatch, result=result)

    with TestClient(_app()) as client:
        response = client.post("/api/multimodal/tts", json={"text": "hello"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["message"] == "serving engine returned 0 audio bytes"
    assert detail["attempts"] == result.attempts


def test_tts_rejects_empty_and_oversized_text_without_invoking_chain(monkeypatch):
    monkeypatch.setattr(chain, "invoke", _fail_invoke)

    with TestClient(_app()) as client:
        empty = client.post("/api/multimodal/tts", json={"text": "   "})
        oversized = client.post("/api/multimodal/tts", json={"text": "x" * 4001})

    assert empty.status_code == 422
    assert empty.json()["detail"] == "text is required"
    assert oversized.status_code == 422
    assert oversized.json()["detail"] == "text exceeds 4000 characters"


def test_tts_honest_404_when_voice_disabled(monkeypatch):
    monkeypatch.setattr(chain, "invoke", _fail_invoke)
    voice = VoiceConfig(enabled=False)

    with TestClient(_app(voice)) as client:
        response = client.post("/api/multimodal/tts", json={"text": "hello"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Voice features are disabled (voice.enabled=false)"


# ---------------------------------------------------------------------------
# POST /api/multimodal/stt
# ---------------------------------------------------------------------------


def test_stt_returns_plan_transcript_contract(monkeypatch):
    calls: list[tuple[str, dict]] = []
    result = CapabilityResult(
        ok=True,
        capability="stt",
        tier="T3",
        engine="faster-whisper",
        data={"text": "hello world", "language": "en"},
        attempts=_attempts(engine="faster-whisper", error="RuntimeError"),
        note="local whisper",
    )
    _stub_invoke(monkeypatch, result=result, calls=calls)

    with TestClient(_app()) as client:
        response = client.post("/api/multimodal/stt", files={"audio": ("clip.wav", b"RIFF-fake-wav", "audio/wav")})

    assert response.status_code == 200
    body = response.json()
    # Plan §5: {ok, text, language, engine, tier, attempts, note}
    assert set(body) == {"ok", "text", "language", "engine", "tier", "attempts", "note"}
    assert body["ok"] is True
    assert body["text"] == "hello world"
    assert body["language"] == "en"
    assert body["engine"] == "faster-whisper"
    assert body["tier"] == "T3"
    assert body["attempts"] == result.attempts

    capability, payload = calls[0]
    assert capability == "stt"
    assert payload["audio"] == b"RIFF-fake-wav"
    assert payload["suffix"] == ".wav"


def test_stt_forwards_config_stt_settings_into_payload(monkeypatch):
    calls: list[tuple[str, dict]] = []
    result = CapabilityResult(ok=True, capability="stt", tier="T3", engine="faster-whisper", data={"text": "bonjour", "language": "fr"}, attempts=[], note="")
    _stub_invoke(monkeypatch, result=result, calls=calls)
    voice = VoiceConfig(stt=SttConfig(model_size="base", language="fr"))

    with TestClient(_app(voice)) as client:
        response = client.post("/api/multimodal/stt", files={"audio": ("clip.mp3", b"ID3", "audio/mpeg")})

    assert response.status_code == 200
    payload = calls[0][1]
    assert payload["model_size"] == "base"
    assert payload["language"] == "fr"
    assert payload["suffix"] == ".mp3"


def test_stt_upload_validation_rejects_without_invoking_chain(monkeypatch):
    monkeypatch.setattr(chain, "invoke", _fail_invoke)

    with TestClient(_app()) as client:
        bad_suffix = client.post("/api/multimodal/stt", files={"audio": ("clip.txt", b"x", "text/plain")})
        empty = client.post("/api/multimodal/stt", files={"audio": ("clip.wav", b"", "audio/wav")})
        oversize = client.post("/api/multimodal/stt", files={"audio": ("clip.wav", b"\x00" * (26 * 1024 * 1024), "audio/wav")})

    assert bad_suffix.status_code == 422
    assert "unsupported audio type" in bad_suffix.json()["detail"]
    assert empty.status_code == 422
    assert empty.json()["detail"] == "audio upload is empty"
    assert oversize.status_code == 422
    assert oversize.json()["detail"] == "audio file exceeds 25 MiB"


def test_stt_exhaustion_is_503_with_attempts(monkeypatch):
    error = MultimodalUnavailableError(
        "stt",
        [
            {"tier": "T2", "engine": "(none)", "error": "skipped_no_provider", "detail": "AI Horde v2 exposes no speech-transcription endpoint", "retryable": None},
            {"tier": "T3", "engine": "faster-whisper", "error": "RuntimeError", "detail": "model load failed", "retryable": False},
        ],
    )
    _stub_invoke(monkeypatch, error=error)

    with TestClient(_app()) as client:
        response = client.post("/api/multimodal/stt", files={"audio": ("clip.wav", b"RIFF", "audio/wav")})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "multimodal_unavailable"
    assert detail["capability"] == "stt"
    assert len(detail["attempts"]) == 2


def test_stt_honest_404_when_voice_disabled(monkeypatch):
    monkeypatch.setattr(chain, "invoke", _fail_invoke)
    voice = VoiceConfig(enabled=False)

    with TestClient(_app(voice)) as client:
        response = client.post("/api/multimodal/stt", files={"audio": ("clip.wav", b"RIFF", "audio/wav")})

    assert response.status_code == 404
    assert response.json()["detail"] == "Voice features are disabled (voice.enabled=false)"


# ---------------------------------------------------------------------------
# POST /api/multimodal/ocr (NOT voice-gated — disclosed design)
# ---------------------------------------------------------------------------


def test_ocr_returns_text_contract_with_attempts(monkeypatch):
    calls: list[tuple[str, dict]] = []
    result = CapabilityResult(
        ok=True,
        capability="ocr",
        tier="T3",
        engine="rapidocr",
        data={"text": "INVOICE 42"},
        attempts=_attempts(engine="rapidocr", error="RuntimeError"),
        note="local rapidocr",
    )
    _stub_invoke(monkeypatch, result=result, calls=calls)

    with TestClient(_app()) as client:
        response = client.post("/api/multimodal/ocr", files={"image": ("shot.png", b"\x89PNG-fake", "image/png")})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["text"] == "INVOICE 42"
    assert body["engine"] == "rapidocr"
    assert body["attempts"] == result.attempts
    capability, payload = calls[0]
    assert capability == "ocr"
    assert payload == {"image": b"\x89PNG-fake", "suffix": ".png"}


def test_ocr_upload_validation_rejects_without_invoking_chain(monkeypatch):
    monkeypatch.setattr(chain, "invoke", _fail_invoke)

    with TestClient(_app()) as client:
        bad_suffix = client.post("/api/multimodal/ocr", files={"image": ("shot.exe", b"MZ", "application/octet-stream")})
        empty = client.post("/api/multimodal/ocr", files={"image": ("shot.jpg", b"", "image/jpeg")})
        oversize = client.post("/api/multimodal/ocr", files={"image": ("shot.gif", b"x" * (15 * 1024 * 1024 + 1), "image/gif")})

    assert bad_suffix.status_code == 422
    assert "unsupported image type" in bad_suffix.json()["detail"]
    assert empty.status_code == 422
    assert empty.json()["detail"] == "image upload is empty"
    assert oversize.status_code == 422
    assert oversize.json()["detail"] == "image exceeds 15 MiB"


def test_ocr_exhaustion_is_503_with_attempts(monkeypatch):
    error = MultimodalUnavailableError(
        "ocr",
        [
            {"tier": "T2", "engine": "(none)", "error": "skipped_no_provider", "detail": "no keyless OCR provider ships with Alpha", "retryable": None},
            {"tier": "T3", "engine": "tesseract", "error": "not_installed", "detail": "pytesseract is not installed", "retryable": None},
        ],
    )
    _stub_invoke(monkeypatch, error=error)

    with TestClient(_app()) as client:
        response = client.post("/api/multimodal/ocr", files={"image": ("shot.png", b"\x89PNG", "image/png")})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["capability"] == "ocr"
    assert [row["tier"] for row in detail["attempts"]] == ["T2", "T3"]


def test_ocr_still_runs_when_voice_disabled(monkeypatch):
    """OCR is not voice-gated (plan §5): only tts/stt/ws-arm honor voice.enabled."""
    result = CapabilityResult(ok=True, capability="ocr", tier="T3", engine="rapidocr", data={"text": "gated? no"}, attempts=[], note="")
    _stub_invoke(monkeypatch, result=result)
    voice = VoiceConfig(enabled=False)

    with TestClient(_app(voice)) as client:
        response = client.post("/api/multimodal/ocr", files={"image": ("shot.png", b"\x89PNG", "image/png")})

    assert response.status_code == 200
    assert response.json()["text"] == "gated? no"


# ---------------------------------------------------------------------------
# POST /api/multimodal/image-gen (NOT voice-gated — disclosed design)
# ---------------------------------------------------------------------------


def test_image_gen_returns_url_success(monkeypatch):
    calls: list[tuple[str, dict]] = []
    result = CapabilityResult(ok=True, capability="image_gen", tier="T2", engine="aihorde-anonymous", data={"url": "https://img.example/x.png"}, attempts=_attempts(engine="aihorde-anonymous", error="TimeoutError"), note="keyless AI Horde")
    _stub_invoke(monkeypatch, result=result, calls=calls)

    with TestClient(_app()) as client:
        response = client.post("/api/multimodal/image-gen", json={"prompt": "a red fox", "size": "512x512"})

    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "https://img.example/x.png"
    assert "b64" not in body
    assert body["engine"] == "aihorde-anonymous"
    assert body["attempts"] == result.attempts
    capability, payload = calls[0]
    assert capability == "image_gen"
    assert payload == {"prompt": "a red fox", "size": "512x512"}


def test_image_gen_returns_b64_success(monkeypatch):
    result = CapabilityResult(ok=True, capability="image_gen", tier="T1", engine="union-alpha", data={"b64": "ZmFrZS1pbWFnZQ=="}, attempts=[], note="T1 model")
    _stub_invoke(monkeypatch, result=result)

    with TestClient(_app()) as client:
        response = client.post("/api/multimodal/image-gen", json={"prompt": "a red fox"})

    assert response.status_code == 200
    body = response.json()
    assert body["b64"] == "ZmFrZS1pbWFnZQ=="
    assert "url" not in body


def test_image_gen_served_but_empty_is_503(monkeypatch):
    result = CapabilityResult(ok=True, capability="image_gen", tier="T2", engine="aihorde-anonymous", data={}, attempts=_attempts(engine="aihorde-anonymous", error="TimeoutError"), note="")
    _stub_invoke(monkeypatch, result=result)

    with TestClient(_app()) as client:
        response = client.post("/api/multimodal/image-gen", json={"prompt": "a red fox"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["message"] == "serving engine returned neither url nor b64"
    assert detail["attempts"] == result.attempts


def test_image_gen_exhaustion_is_503_with_attempts(monkeypatch):
    error = MultimodalUnavailableError("image_gen", _attempts(engine="aihorde-anonymous", error="TimeoutError"))
    _stub_invoke(monkeypatch, error=error)

    with TestClient(_app()) as client:
        response = client.post("/api/multimodal/image-gen", json={"prompt": "a red fox"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "multimodal_unavailable"
    assert detail["capability"] == "image_gen"


def test_image_gen_prompt_validation_without_invoking_chain(monkeypatch):
    monkeypatch.setattr(chain, "invoke", _fail_invoke)

    with TestClient(_app()) as client:
        empty = client.post("/api/multimodal/image-gen", json={"prompt": "   "})
        oversized = client.post("/api/multimodal/image-gen", json={"prompt": "x" * 4001})

    assert empty.status_code == 422
    assert empty.json()["detail"] == "prompt is required"
    assert oversized.status_code == 422
    assert oversized.json()["detail"] == "prompt exceeds 4000 characters"


def test_image_gen_still_runs_when_voice_disabled(monkeypatch):
    """image-gen is not voice-gated (plan §5) — disclosed, tested, not hidden."""
    result = CapabilityResult(ok=True, capability="image_gen", tier="T2", engine="aihorde-anonymous", data={"url": "https://img.example/y.png"}, attempts=[], note="")
    _stub_invoke(monkeypatch, result=result)

    with TestClient(_app(VoiceConfig(enabled=False))) as client:
        response = client.post("/api/multimodal/image-gen", json={"prompt": "a red fox"})

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# WebSocket /api/multimodal/voice — auth boundary
# ---------------------------------------------------------------------------


def test_voice_ws_closes_4401_when_unauthenticated():
    app = _ws_app()
    with patch.object(multimodal_router, "_authenticate_ws", AsyncMock(return_value=None)):
        _expect_ws_close(app, 4401)


def test_voice_ws_closes_4403_for_cross_origin_upgrade():
    app = _ws_app()
    with patch.object(multimodal_router, "_authenticate_ws", AsyncMock(return_value=SimpleNamespace(id="ws-user"))):
        _expect_ws_close(app, 4403, headers={"origin": "https://evil.example.com"})


# ---------------------------------------------------------------------------
# WebSocket — session flow
# ---------------------------------------------------------------------------


def test_voice_ws_connect_sends_capabilities_then_pong(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig())
    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            first = ws.receive_json()
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()

    assert first["type"] == "capabilities"
    assert first["rows"] == _report()["rows"]
    assert first["voice"]["enabled"] is True
    assert "reachability not probed" in first["note"]
    assert pong == {"type": "status", "state": "idle", "event": "pong"}


def test_voice_ws_rejects_audio_frames_before_arm(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig())
    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            ws.receive_json()  # capabilities
            ws.send_json({"type": "audio", "data": base64.b64encode(_FRAME).decode()})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert error["message"] == 'audio frames ignored: wake session is not armed (send {"type":"arm"} first)'


def test_voice_ws_arm_reports_not_installed_when_scorer_missing(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig())

    def missing():
        raise ImportError("No module named 'openwakeword'")

    monkeypatch.setattr(wakeword_mod, "load_default_score_fn", missing)

    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            ws.receive_json()  # capabilities
            ws.send_json({"type": "arm"})
            event = ws.receive_json()

    assert event["type"] == "engine"
    assert event["capability"] == "wake_word"
    assert event["status"] == "not_installed"
    assert "voice extra" in event["detail"]


def test_voice_ws_arm_not_configured_when_voice_disabled(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig(enabled=False))

    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            ws.receive_json()  # capabilities
            ws.send_json({"type": "arm"})
            event = ws.receive_json()

    assert event == {
        "type": "engine",
        "capability": "wake_word",
        "status": "not_configured",
        "detail": "voice features are disabled (voice.enabled=false)",
    }


def test_voice_ws_arm_ready_but_never_claims_armed_without_frames(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig(wake_word=WakeWordConfig(threshold=0.5)))
    monkeypatch.setattr(wakeword_mod, "load_default_score_fn", lambda: _scripted_scorer([0.1]))

    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            ws.receive_json()  # capabilities
            ws.send_json({"type": "arm"})
            event = ws.receive_json()
            # Frames have not flowed: ping must not report wake_armed state claim beyond arm_status
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()

    assert event["type"] == "status"
    assert event["state"] == "wake_armed"
    assert event["status"] == "ready"
    assert event["armed"] is False  # honesty: engine ready != frames flowing
    assert event["threshold"] == 0.5
    assert "armed stays false until push_frame()" in event["detail"]
    assert pong["state"] == "wake_armed"


def test_voice_ws_full_lifecycle_beacon_wake_transcript_disarm(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig(wake_word=WakeWordConfig(threshold=0.5)))
    # 10 silent frames (score beacon at frame 10) then a waking frame.
    monkeypatch.setattr(wakeword_mod, "load_default_score_fn", lambda: _scripted_scorer([0.1] * 10 + [0.92]))

    stt_calls: list[tuple[str, dict]] = []

    def fake_invoke(capability, payload):
        stt_calls.append((capability, payload))
        return CapabilityResult(
            ok=True,
            capability="stt",
            tier="T3",
            engine="faster-whisper",
            data={"text": "hello world", "language": "en"},
            attempts=[],
            note="stubbed STT",
        )

    monkeypatch.setattr(chain, "invoke", fake_invoke)

    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            ws.receive_json()  # capabilities
            ws.send_json({"type": "arm"})
            arm_event = ws.receive_json()
            assert arm_event["state"] == "wake_armed"

            for _ in range(10):
                ws.send_json({"type": "audio", "data": base64.b64encode(_FRAME).decode()})
            beacon = ws.receive_json()

            ws.send_json({"type": "audio", "data": base64.b64encode(_FRAME).decode()})
            wake = ws.receive_json()
            transcript = ws.receive_json()

            # Corrupt frame must be honestly rejected; session survives it.
            ws.send_json({"type": "audio", "data": base64.b64encode(b"\x01\x02\x03").decode()})
            odd_error = ws.receive_json()
            # Bad base64 must be honestly rejected too.
            ws.send_json({"type": "audio", "data": "!!not-base64!!"})
            b64_error = ws.receive_json()

            ws.send_json({"type": "disarm"})
            disarmed = ws.receive_json()
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()

    # Score beacon every 10 frames — score/threshold always disclosed.
    assert beacon == {"type": "score", "score": 0.1, "threshold": 0.5, "frames": 10}

    assert wake == {"type": "wake", "score": 0.92, "threshold": 0.5, "engine": "openwakeword", "frames": 11}
    # The transcript names the STT engine that produced the text — not the wake scorer.
    assert transcript == {
        "type": "transcript",
        "text": "hello world",
        "language": None,
        "engine": "faster-whisper",
        "tier": "T3",
        "final": True,
    }

    assert odd_error["type"] == "error"
    assert odd_error["message"].startswith("InvalidFrameError: odd-length frame (3 bytes)")
    assert b64_error == {"type": "error", "message": "audio frame is not valid base64"}

    assert disarmed == {"type": "status", "state": "idle", "event": "disarmed"}
    assert pong == {"type": "status", "state": "idle", "event": "pong"}

    # Exactly one STT handoff for one wake (latched), WAV-wrapped PCM payload.
    assert len(stt_calls) == 1
    capability, payload = stt_calls[0]
    assert capability == "stt"
    assert payload["suffix"] == ".wav"
    assert payload["audio"].startswith(b"RIFF")


def test_voice_ws_wake_stt_failure_reports_engine_event(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig())
    monkeypatch.setattr(wakeword_mod, "load_default_score_fn", lambda: _scripted_scorer([0.9]))
    error = MultimodalUnavailableError(
        "stt",
        [{"tier": "T3", "engine": "faster-whisper", "error": "RuntimeError", "detail": "model load failed", "retryable": False}],
    )
    _stub_invoke(monkeypatch, error=error)

    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            ws.receive_json()  # capabilities
            ws.send_json({"type": "arm"})
            ws.receive_json()  # wake_armed
            ws.send_json({"type": "audio", "data": base64.b64encode(_FRAME).decode()})
            wake = ws.receive_json()
            engine_event = ws.receive_json()

    assert wake["type"] == "wake"
    assert engine_event["type"] == "engine"
    assert engine_event["capability"] == "stt"
    assert engine_event["status"] == "unavailable"
    assert "No engine could serve capability 'stt'" in engine_event["detail"]


def test_voice_ws_direct_transcribe_success(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig())
    result = CapabilityResult(ok=True, capability="stt", tier="T3", engine="faster-whisper", data={"text": "push to talk", "language": "en"}, attempts=[], note="")
    _stub_invoke(monkeypatch, result=result)

    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            ws.receive_json()  # capabilities
            ws.send_json({"type": "transcribe", "data": base64.b64encode(_FRAME).decode()})
            event = ws.receive_json()
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()

    assert event == {
        "type": "transcript",
        "text": "push to talk",
        "language": "en",
        "engine": "faster-whisper",
        "tier": "T3",
        "final": True,
    }
    assert pong == {"type": "status", "state": "idle", "event": "pong"}


def test_voice_ws_direct_transcribe_exhaustion_carries_attempts(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig())
    error = MultimodalUnavailableError(
        "stt",
        [{"tier": "T2", "engine": "(none)", "error": "skipped_no_provider", "detail": "no keyless STT provider", "retryable": None}],
    )
    _stub_invoke(monkeypatch, error=error)

    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            ws.receive_json()  # capabilities
            ws.send_json({"type": "transcribe", "data": base64.b64encode(_FRAME).decode()})
            event = ws.receive_json()

    assert event["type"] == "engine"
    assert event["capability"] == "stt"
    assert event["status"] == "unavailable"
    assert event["attempts"] == error.attempts


def test_voice_ws_direct_transcribe_rejects_bad_input_honestly(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig())
    # WavError must fire BEFORE the chain: invoke exploding proves it never ran.
    monkeypatch.setattr(chain, "invoke", _fail_invoke)

    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            ws.receive_json()  # capabilities
            ws.send_json({"type": "transcribe", "data": "!!not-base64!!"})
            bad_b64 = ws.receive_json()
            ws.send_json({"type": "transcribe", "data": base64.b64encode(b"\x01\x02\x03").decode()})
            odd = ws.receive_json()

    assert bad_b64 == {"type": "error", "message": "transcribe payload is not valid base64"}
    assert odd["type"] == "error"
    assert odd["message"].startswith("WavError: pcm16 byte length must be a multiple of 2")


# ---------------------------------------------------------------------------
# WebSocket — protocol errors
# ---------------------------------------------------------------------------


def test_voice_ws_unknown_message_type_lists_expected(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig())
    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            ws.receive_json()  # capabilities
            ws.send_json({"type": "nope"})
            event = ws.receive_json()

    assert event == {
        "type": "error",
        "message": "unknown message type 'nope'; expected arm|disarm|audio|transcribe|conversation_start|conversation_stop|ping",
    }


def test_voice_ws_malformed_json_is_reported_not_fatal(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig())
    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            ws.receive_json()  # capabilities
            ws.send_text("{not json")
            event = ws.receive_json()
            # Socket survives: ping still answers.
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()

    assert event == {"type": "error", "message": "invalid JSON message; expected an object with a 'type'"}
    assert pong["event"] == "pong"


def test_voice_ws_non_object_message_is_reported(monkeypatch):
    _patch_ws(monkeypatch, VoiceConfig())
    with TestClient(_ws_app()) as client:
        with client.websocket_connect("/api/multimodal/voice") as ws:
            ws.receive_json()  # capabilities
            ws.send_text("[1, 2]")
            event = ws.receive_json()

    assert event == {"type": "error", "message": "invalid message; expected a JSON object"}
