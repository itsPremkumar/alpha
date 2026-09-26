"""Local voice configuration defaults and bounded validation."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from alpha.config.voice_config import (
    StreamingConfig,
    SttConfig,
    TtsConfig,
    VoiceConfig,
    VoiceRoutingConfig,
)


def test_voice_defaults_are_local_cpu_first_and_bounded():
    voice = VoiceConfig()

    assert voice.enabled is True
    assert voice.routing == VoiceRoutingConfig(mode="local_only")
    assert voice.tts.engine == "piper"
    assert voice.tts.voice == "en_US-lessac-medium"
    assert voice.tts.model_path is None
    assert voice.tts.length_scale == 1.0
    assert voice.tts.noise_scale == pytest.approx(0.667)
    assert voice.tts.volume == pytest.approx(0.9)
    assert voice.stt.model_size == "small"
    assert voice.stt.model_path is None
    assert voice.stt.device == "auto"
    assert voice.stt.compute_type == "int8"
    assert voice.stt.beam_size == 1
    assert voice.stt.local_files_only is True
    assert voice.streaming == StreamingConfig(
        sample_rate=16_000,
        frame_ms=20,
        pre_roll_ms=200,
        speech_start_ms=60,
        endpoint_silence_ms=700,
        partial_interval_ms=900,
        max_utterance_seconds=30,
        max_frame_bytes=65_536,
        max_sessions=4,
    )


@pytest.mark.parametrize("voice_id", ["../secret", "folder/voice", "folder\\voice", ".", "x" * 65, "bad voice"])
def test_tts_voice_is_a_safe_id_not_a_path(voice_id: str):
    with pytest.raises(ValidationError):
        TtsConfig(voice=voice_id)


def test_legacy_null_tts_voice_upgrades_to_the_setup_default():
    assert TtsConfig(voice=None).voice == "en_US-lessac-medium"


def test_operator_model_path_is_bounded_but_may_be_absolute():
    path = Path("/models/piper/custom.onnx")
    cfg = TtsConfig(model_path=path)
    assert cfg.model_path == path
    with pytest.raises(ValidationError):
        SttConfig(model_path="x" * 1025)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("beam_size", 0),
        ("beam_size", 21),
        ("device", "gpu-with spaces"),
        ("compute_type", "bad type"),
    ],
)
def test_stt_settings_have_bounded_validation(field: str, value: object):
    with pytest.raises(ValidationError):
        SttConfig(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sample_rate", 12_345),
        ("frame_ms", 15),
        ("pre_roll_ms", -1),
        ("speech_start_ms", 0),
        ("endpoint_silence_ms", 99),
        ("partial_interval_ms", 0),
        ("max_utterance_seconds", 0),
        ("max_utterance_seconds", 121),
        ("max_frame_bytes", 31),
        ("max_sessions", 0),
        ("max_sessions", 33),
    ],
)
def test_streaming_settings_reject_unsafe_values(field: str, value: object):
    with pytest.raises(ValidationError):
        StreamingConfig(**{field: value})


def test_config_example_documents_complete_local_voice_schema():
    root = Path(__file__).resolve().parents[2]
    payload = yaml.safe_load((root / "config.example.yaml").read_text(encoding="utf-8"))

    assert payload["config_version"] >= 46
    voice = payload["voice"]
    assert voice["routing"]["mode"] == "local_only"
    assert voice["tts"]["voice"] == "en_US-lessac-medium"
    assert voice["stt"]["local_files_only"] is True
    assert voice["streaming"]["endpoint_silence_ms"] == 700
    assert voice["streaming"]["max_sessions"] == 4


def test_voice_extra_is_bounded_local_runtime_and_root_forwards_it():
    backend_root = Path(__file__).resolve().parents[1]
    harness = tomllib.loads((backend_root / "packages" / "harness" / "pyproject.toml").read_text(encoding="utf-8"))
    root = tomllib.loads((backend_root / "pyproject.toml").read_text(encoding="utf-8"))

    voice_dependencies = harness["project"]["optional-dependencies"]["voice"]
    assert voice_dependencies == [
        "faster-whisper>=1.2.1,<2",
        "piper-tts>=1.8.0,<2",
        "rapidocr-onnxruntime>=1.4.4,<2",
        "tokenizers>=0.21,<1",
        "webrtcvad-wheels>=2.0.11,<3",
    ]
    assert not any(dependency.startswith(("edge-tts", "openwakeword")) for dependency in voice_dependencies)
    assert root["project"]["optional-dependencies"]["voice"] == ["agent-workspace-harness[voice]"]
