"""Local voice model setup tests (download logic is dependency-free)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import setup_voice


def test_default_asset_paths_are_runtime_local(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(setup_voice, "runtime_home", lambda: tmp_path)

    assert setup_voice.models_dir() == tmp_path / "voice" / "models"
    assert setup_voice.stt_model_path("small") == tmp_path / "voice" / "models" / "faster-whisper" / "small"
    assert setup_voice.tts_voice_path() == tmp_path / "voice" / "models" / "piper" / "en_US-lessac-medium.onnx"


def test_download_verified_is_atomic_and_skips_valid_file(tmp_path: Path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"local model")
    destination = tmp_path / "nested" / "model.bin"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    setup_voice._download_verified(source.as_uri(), destination, digest)

    assert destination.read_bytes() == b"local model"
    assert not list(destination.parent.glob("*.part"))
    mtime = destination.stat().st_mtime_ns
    setup_voice._download_verified(source.as_uri(), destination, digest)
    assert destination.stat().st_mtime_ns == mtime


def test_download_verified_rejects_bad_checksum_without_publishing(tmp_path: Path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"wrong model")
    destination = tmp_path / "model.bin"

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        setup_voice._download_verified(source.as_uri(), destination, "0" * 64)

    assert not destination.exists()
    assert not list(tmp_path.glob("*.part"))


def test_verify_assets_reports_independent_readiness(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(setup_voice, "runtime_home", lambda: tmp_path)
    monkeypatch.setattr(setup_voice, "_module_available", lambda _name: True)
    stt = setup_voice.stt_model_path("base")
    stt.mkdir(parents=True)
    for name in setup_voice.STT_REQUIRED_FILES:
        (stt / name).write_bytes(b"x")

    statuses = setup_voice.verify_assets(stt_model="base")
    assert statuses[0].name == "faster-whisper"
    assert statuses[0].ready is True
    assert statuses[1].name == "piper"
    assert statuses[1].ready is False
    assert "make voice-setup" in statuses[1].detail


def test_verify_assets_requires_optional_dependencies(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(setup_voice, "runtime_home", lambda: tmp_path)
    monkeypatch.setattr(setup_voice, "_stt_assets_ready", lambda _path: True)
    monkeypatch.setattr(setup_voice, "_tts_assets_ready", lambda _path: True)
    monkeypatch.setattr(setup_voice, "_module_available", lambda _name: False)

    statuses = setup_voice.verify_assets(stt_model="small")

    assert [status.ready for status in statuses] == [False, False]
    assert all("dependency missing" in status.detail for status in statuses)


def test_manifest_pins_revisions_and_is_utf8(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(setup_voice, "runtime_home", lambda: tmp_path)

    manifest = setup_voice.write_manifest(stt_model="small", tts_voice=setup_voice.DEFAULT_TTS_VOICE)
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["stt"]["revision"] == setup_voice.STT_REVISIONS["small"]
    assert payload["tts"]["revision"] == setup_voice.PIPER_VOICES_REVISION
    assert payload["tts"]["voice"] == setup_voice.DEFAULT_TTS_VOICE
    assert payload["tts"]["sha256"]["en_US-lessac-medium.onnx"] == setup_voice.PIPER_VOICE_SHA256["en_US-lessac-medium.onnx"]
    assert not manifest.with_suffix(".json.part").exists()


def test_verify_only_main_does_not_download(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(setup_voice, "runtime_home", lambda: tmp_path)
    monkeypatch.setattr(
        setup_voice,
        "verify_assets",
        lambda **_kwargs: [
            setup_voice.AssetStatus("faster-whisper", "stt", True, "ready"),
            setup_voice.AssetStatus("piper", "tts", True, "ready"),
        ],
    )
    monkeypatch.setattr(
        setup_voice,
        "download_stt_model",
        lambda *_args, **_kwargs: pytest.fail("verify-only must not download STT"),
    )
    monkeypatch.setattr(
        setup_voice,
        "download_tts_voice",
        lambda *_args, **_kwargs: pytest.fail("verify-only must not download TTS"),
    )

    assert setup_voice.main(["--verify-only", "--skip-warmup"]) == 0
    assert "Local voice models are ready" in capsys.readouterr().out
