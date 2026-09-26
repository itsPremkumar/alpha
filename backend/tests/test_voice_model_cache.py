"""Safe local voice model path resolution and one-active-model cache tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from alpha.multimodal.local_models import (
    FasterWhisperModelCache,
    PiperModelCache,
    PiperModelSpec,
    WhisperModelSpec,
    resolve_piper_model_path,
    resolve_whisper_model_path,
)


def test_default_paths_are_below_runtime_home_voice_models(tmp_path: Path):
    piper = resolve_piper_model_path(
        "en_US-lessac-medium",
        configured_path=None,
        legacy_path=None,
        home=tmp_path,
    )
    whisper = resolve_whisper_model_path(configured_path=None, model_size="small", home=tmp_path)

    assert piper == tmp_path / "voice" / "models" / "piper" / "en_US-lessac-medium.onnx"
    assert whisper == tmp_path / "voice" / "models" / "faster-whisper" / "small"


def test_configured_path_wins_and_legacy_env_is_operator_only_fallback(tmp_path: Path):
    configured = tmp_path / "operator" / "custom.onnx"
    legacy = tmp_path / "legacy.onnx"
    configured_resolved = resolve_piper_model_path(
        "custom_voice",
        str(configured),
        str(legacy),
        home=tmp_path / "runtime",
    )
    legacy_resolved = resolve_piper_model_path("custom_voice", None, str(legacy), home=tmp_path / "runtime")

    assert configured_resolved == configured.resolve()
    assert legacy_resolved == legacy.resolve()


def test_client_style_voice_id_cannot_escape_model_directory(tmp_path: Path):
    with pytest.raises(ValueError, match="voice id"):
        resolve_piper_model_path("../secret", None, None, home=tmp_path)
    with pytest.raises(ValueError, match="voice id"):
        resolve_piper_model_path("folder/voice", None, None, home=tmp_path)


def test_whisper_cache_reuses_one_model_and_reloads_when_effective_key_changes(tmp_path: Path):
    created: list[object] = []

    def factory(spec: WhisperModelSpec):
        model = object()
        created.append((spec, model))
        return model

    cache = FasterWhisperModelCache(factory=factory)
    first = WhisperModelSpec(
        model_path=tmp_path / "small",
        model_size="small",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    )
    same = WhisperModelSpec(
        model_path=tmp_path / "small",
        model_size="small",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    )
    changed = WhisperModelSpec(
        model_path=tmp_path / "small",
        model_size="base",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    )

    first_model = cache.get(first)
    assert first_model is cache.get(same)
    changed_model = cache.get(changed)
    assert changed_model is not first_model
    assert len(created) == 2
    assert created[0][0].local_files_only is True

    cache.clear_for_test()
    assert cache.active_key is None


def test_piper_cache_reuses_model_when_only_synthesis_settings_change(tmp_path: Path):
    created: list[PiperModelSpec] = []

    synthesis: dict[str, object] = {}

    class FakeVoice:
        def synthesize_wav(self, text, wav_file, syn_config=None):
            synthesis.update(text=text, config=syn_config)
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22_050)
            wav_file.writeframes(b"\x00\x00" * 32)

    cache = PiperModelCache(
        factory=lambda spec: created.append(spec) or FakeVoice(),
        synthesis_config_factory=lambda spec: SimpleNamespace(
            length_scale=spec.length_scale,
            noise_scale=spec.noise_scale,
            volume=spec.volume,
        ),
    )
    first = PiperModelSpec(
        model_path=tmp_path / "voice.onnx",
        length_scale=1.0,
        noise_scale=0.667,
        volume=0.9,
    )
    changed = PiperModelSpec(
        model_path=first.model_path,
        length_scale=1.2,
        noise_scale=0.667,
        volume=0.9,
    )

    first_model = cache.get(first)
    audio = cache.synthesize("hello", first)
    assert first_model is cache.get(first)
    assert audio.startswith(b"RIFF")
    assert synthesis["text"] == "hello"
    assert synthesis["config"].volume == 0.9
    changed_model = cache.get(changed)
    assert changed_model is first_model
    assert len(created) == 1
    changed_audio = cache.synthesize("updated", changed)
    assert changed_audio.startswith(b"RIFF")
    assert synthesis["text"] == "updated"
    assert synthesis["config"].length_scale == 1.2
    first.model_path.write_bytes(b"new model bytes")
    reloaded_model = cache.get(first)
    assert reloaded_model is not first_model
    assert len(created) == 2
    cache.clear_for_test()
    assert cache.active_key is None


def test_whisper_model_path_resolver_makes_relative_operator_paths_runtime_local(tmp_path: Path):
    assert resolve_whisper_model_path("models/custom", "small", home=tmp_path) == (tmp_path / "models" / "custom").resolve()
