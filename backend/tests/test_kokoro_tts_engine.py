"""Kokoro natural-voice engine: dispatch, honesty rows, and cache behaviour.

These tests never load model weights. Synthesis is exercised through injected
factories so the engine contract (argument mapping, WAV bytes, honest skip
rows) is pinned without a ~92 MB ONNX download.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from alpha.multimodal.capabilities import Capability
from alpha.multimodal.engines import local as local_engines
from alpha.multimodal.local_models import (
    KokoroModelSpec,
    resolve_kokoro_model_path,
)


def _install_fake_kokoro(monkeypatch: pytest.MonkeyPatch, *, audio: bytes | None = None) -> dict:
    """Install a fake kokoro_onnx module; return the recorded create() calls."""
    calls: dict = {"create": []}

    class _Kokoro:
        def __init__(self, model_path: str, voices_path: str) -> None:
            calls["model_path"] = model_path
            calls["voices_path"] = voices_path

        def create(self, text: str, voice: str, speed: float = 1.0):
            calls["create"].append({"text": text, "voice": voice, "speed": speed})
            return b"", 24000

    module = ModuleType("kokoro_onnx")
    module.Kokoro = _Kokoro  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kokoro_onnx", module)

    def _fake_synthesize(text: str, spec: KokoroModelSpec, *, speed: float = 1.0, volume: float = 0.9) -> bytes:
        calls["synthesize"] = {"text": text, "spec": spec, "speed": speed, "volume": volume}
        return audio if audio is not None else b"RIFFfake-wav"

    monkeypatch.setattr("alpha.multimodal.local_models.synthesize_with_cached_kokoro", _fake_synthesize)
    return calls


class TestKokoroModelSpec:
    def test_rejects_non_positive_speed(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="speed must be positive"):
            KokoroModelSpec(model_path=tmp_path / "m.onnx", voices_path=tmp_path / "v.bin", voice="af_bella", speed=0.0)

    def test_rejects_negative_volume(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="volume non-negative"):
            KokoroModelSpec(model_path=tmp_path / "m.onnx", voices_path=tmp_path / "v.bin", voice="af_bella", volume=-0.1)

    def test_cache_key_tracks_model_mtime_and_voice(self, tmp_path: Path) -> None:
        model = tmp_path / "m.onnx"
        model.write_bytes(b"x")
        first = KokoroModelSpec(model_path=model, voices_path=tmp_path / "v.bin", voice="af_bella").cache_key
        second = KokoroModelSpec(model_path=model, voices_path=tmp_path / "v.bin", voice="am_adam").cache_key
        assert first[0] == second[0]
        assert first[-1] != second[-1], "a different voice must not reuse a cache entry"

    def test_missing_model_file_yields_zeroed_stat_cache_key(self, tmp_path: Path) -> None:
        spec = KokoroModelSpec(model_path=tmp_path / "absent.onnx", voices_path=tmp_path / "v.bin", voice="af_bella")
        assert spec.cache_key[1:] == (0, 0, "af_bella")


class TestResolveKokoroModelPath:
    def test_default_paths_are_deployment_local(self, tmp_path: Path) -> None:
        spec = resolve_kokoro_model_path("af_bella", None, None, home=tmp_path)
        assert spec.model_path == tmp_path / "voice" / "models" / "kokoro" / "kokoro-v1.0.int8.onnx"
        assert spec.voices_path == tmp_path / "voice" / "models" / "kokoro" / "voices-v1.0.bin"

    def test_rejects_a_voice_id_that_is_not_a_safe_filename(self, tmp_path: Path) -> None:
        for bad in ("../escape", "af bella", "", "a" * 65, "af/../x"):
            with pytest.raises(ValueError, match="Kokoro voice id"):
                resolve_kokoro_model_path(bad, None, None, home=tmp_path)

    def test_operator_paths_win_over_defaults(self, tmp_path: Path) -> None:
        spec = resolve_kokoro_model_path("af_bella", tmp_path / "custom.onnx", tmp_path / "custom.bin", home=tmp_path / "root")
        assert spec.model_path == (tmp_path / "custom.onnx").resolve()
        assert spec.voices_path == (tmp_path / "custom.bin").resolve()


class TestKokoroTts:
    def test_returns_synthesized_audio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_kokoro(monkeypatch)
        audio = local_engines.kokoro_tts("hello there", "af_bella", speed=1.1, volume=0.8)
        assert audio == b"RIFFfake-wav"

    def test_forwards_voice_speed_and_volume_to_synthesis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _install_fake_kokoro(monkeypatch)
        local_engines.kokoro_tts("hi", "am_adam", speed=1.25, volume=0.5)
        assert calls["synthesize"]["spec"].voice == "am_adam"
        assert calls["synthesize"]["speed"] == 1.25
        assert calls["synthesize"]["volume"] == 0.5

    def test_missing_package_is_an_honest_not_installed_skip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "kokoro_onnx", None)
        with pytest.raises(Exception) as excinfo:
            local_engines.kokoro_tts("hi", "af_bella")
        text = str(excinfo.value)
        assert "not_installed" in text, "a missing engine must report not_installed, never a fabricated failure"
        rows = getattr(excinfo.value, "rows", []) or []
        assert any(row.get("engine") == "kokoro" for row in rows)

    def test_unsafe_voice_id_skips_as_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_kokoro(monkeypatch)
        with pytest.raises(Exception) as excinfo:
            local_engines.kokoro_tts("hi", "../escape")
        assert "not_configured" in str(excinfo.value)

    def test_zero_bytes_is_treated_as_exhaustion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_kokoro(monkeypatch, audio=b"")
        with pytest.raises(Exception) as excinfo:
            local_engines.kokoro_tts("hi", "af_bella")
        # The summary string is generic; the real reason travels on the attempt rows.
        rows = getattr(excinfo.value, "attempts", []) or []
        assert any("0 audio bytes" in str(row) for row in rows), f"zero-byte synthesis must be reported on an attempt row, got {rows}"


class TestTtsEngineDispatch:
    """run_t3 must route on the engine field and label the result honestly."""

    def _result(self, monkeypatch: pytest.MonkeyPatch, payload: dict):
        return local_engines.run_t3(Capability.TTS, payload, [])

    def test_kokoro_engine_is_routed_and_labelled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_kokoro(monkeypatch)
        monkeypatch.setattr(local_engines, "kokoro_tts", lambda *a, **k: b"RIFFkokoro")
        result = self._result(monkeypatch, {"text": "hi", "engine": "kokoro", "voice": "af_bella"})
        assert result.engine == "kokoro"
        assert result.data["audio"] == b"RIFFkokoro"
        assert result.data["media_type"] == "audio/wav"
        assert "kokoro" in result.note

    def test_piper_remains_the_default_and_the_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        def _piper(text, voice, **kwargs):
            seen["voice"] = voice
            return b"RIFFpiper"

        monkeypatch.setattr(local_engines, "piper_tts", _piper)
        result = self._result(monkeypatch, {"text": "hi"})
        assert result.engine == "piper"
        assert seen["voice"] == "en_US-lessac-medium", "Piper keeps its documented default voice"

    def test_kokoro_speed_reaches_the_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_kokoro(monkeypatch)
        calls = _install_fake_kokoro(monkeypatch)
        local_engines.run_t3(Capability.TTS, {"text": "hi", "engine": "kokoro", "voice": "af_sarah", "speed": 1.3}, [])
        assert calls["synthesize"]["spec"].voice == "af_sarah"
        assert calls["synthesize"]["speed"] == 1.3
