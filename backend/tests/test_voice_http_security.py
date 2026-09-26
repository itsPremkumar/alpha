"""Voice HTTP request safety and complete operator-config forwarding."""

from __future__ import annotations

from types import SimpleNamespace

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from alpha.config.voice_config import SttConfig, TtsConfig, VoiceConfig
from alpha.multimodal import chain
from alpha.multimodal.capabilities import CapabilityResult
from app.gateway.deps import get_config
from app.gateway.routers import multimodal as multimodal_router


def _app(voice: VoiceConfig):
    app = make_authed_test_app()
    app.include_router(multimodal_router.router)
    app.dependency_overrides[get_config] = lambda: SimpleNamespace(voice=voice)
    return app


def test_tts_request_rejects_filesystem_voice_paths_before_chain(monkeypatch):
    monkeypatch.setattr(chain, "invoke", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("chain must not run")))
    with TestClient(_app(VoiceConfig())) as client:
        response = client.post("/api/multimodal/tts", json={"text": "hello", "voice": "../../secret"})

    assert response.status_code == 422


def test_tts_forwards_only_operator_model_settings_and_safe_voice(monkeypatch, tmp_path):
    calls: list[dict] = []
    result = CapabilityResult(
        ok=True,
        capability="tts",
        tier="T3",
        engine="piper",
        data={"audio": b"RIFF", "media_type": "audio/wav"},
        attempts=[],
        note="local",
    )

    def invoke(capability, payload):
        assert capability == "tts"
        calls.append(payload)
        return result

    monkeypatch.setattr(chain, "invoke", invoke)
    voice = VoiceConfig(
        tts=TtsConfig(
            voice="configured_voice",
            model_path=str(tmp_path / "operator.onnx"),
            length_scale=1.1,
            noise_scale=0.5,
            volume=0.8,
        )
    )

    with TestClient(_app(voice)) as client:
        response = client.post("/api/multimodal/tts", json={"text": "hi", "voice": "request_voice"})

    assert response.status_code == 200
    assert calls[0]["voice"] == "request_voice"
    assert calls[0]["model_path"] == str(tmp_path / "operator.onnx")
    assert calls[0]["length_scale"] == 1.1
    assert calls[0]["noise_scale"] == 0.5
    assert calls[0]["volume"] == 0.8


def test_stt_forwards_effective_local_only_runtime_settings(monkeypatch):
    calls: list[dict] = []
    result = CapabilityResult(
        ok=True,
        capability="stt",
        tier="T3",
        engine="faster-whisper",
        data={"text": "hello", "language": "en"},
        attempts=[],
        note="local",
    )
    monkeypatch.setattr(chain, "invoke", lambda capability, payload: calls.append(payload) or result)
    voice = VoiceConfig(
        stt=SttConfig(
            model_path="/operator/models/whisper",
            model_size="small",
            device="cpu",
            compute_type="int8",
            beam_size=2,
            local_files_only=True,
            language="en",
        )
    )

    with TestClient(_app(voice)) as client:
        response = client.post("/api/multimodal/stt", files={"audio": ("clip.wav", b"RIFF", "audio/wav")})

    assert response.status_code == 200
    assert calls[0] == {
        "audio": b"RIFF",
        "suffix": ".wav",
        "model_path": "/operator/models/whisper",
        "model_size": "small",
        "device": "cpu",
        "compute_type": "int8",
        "beam_size": 2,
        "local_files_only": True,
        "language": "en",
    }
