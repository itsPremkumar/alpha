#!/usr/bin/env python3
"""Install and verify Alpha's free, local speech model assets.

The script downloads pinned model files once. Runtime speech inference does not
require a speech API key and does not download models implicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alpha.config.runtime_paths import runtime_home

DEFAULT_STT_MODEL = "small"
DEFAULT_TTS_VOICE = "en_US-lessac-medium"

# Pinned immutable Hub revisions. These models are downloaded during setup only.
STT_REVISIONS = {
    "base": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    "small": "536b0662742c02347bc0e980a01041f333bce120",
}
STT_REPOSITORIES = {
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
}
STT_REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")

PIPER_VOICES_REVISION = "c10ece1aade47bb51c153c893d14e5bf8e5b7117"
PIPER_VOICE_SHA256 = {
    "en_US-lessac-medium.onnx": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
    "en_US-lessac-medium.onnx.json": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
}
PIPER_FILE_SIZES = {
    "en_US-lessac-medium.onnx": 63_201_294,
    "en_US-lessac-medium.onnx.json": 4_885,
}

# Kokoro ONNX TTS (natural, Apache-2.0) — pinned GitHub release assets.
KOKORO_RELEASE_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_MODEL_FILE = "kokoro-v1.0.int8.onnx"
KOKORO_VOICES_FILE = "voices-v1.0.bin"
KOKORO_MODEL_SIZE = 92_361_271
KOKORO_VOICES_SIZE = 28_214_398


@dataclass(frozen=True, slots=True)
class AssetStatus:
    name: str
    path: str
    ready: bool
    detail: str


def models_dir() -> Path:
    """Return the deployment-local speech model directory."""
    return runtime_home() / "voice" / "models"


def stt_model_path(model_size: str = DEFAULT_STT_MODEL) -> Path:
    return models_dir() / "faster-whisper" / model_size


def tts_voice_path(voice_id: str = DEFAULT_TTS_VOICE) -> Path:
    return models_dir() / "piper" / f"{voice_id}.onnx"


def tts_config_path(voice_id: str = DEFAULT_TTS_VOICE) -> Path:
    return models_dir() / "piper" / f"{voice_id}.onnx.json"


def kokoro_model_path() -> Path:
    return models_dir() / "kokoro" / KOKORO_MODEL_FILE


def kokoro_voices_path() -> Path:
    return models_dir() / "kokoro" / KOKORO_VOICES_FILE


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _download_verified(url: str, destination: Path, expected_sha256: str) -> None:
    """Download *url* to an adjacent temp file and atomically publish it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        try:
            if sha256_file(destination) == expected_sha256:
                return
        except OSError:
            pass
        destination.unlink(missing_ok=True)

    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".part", dir=destination.parent)
    temp_path = Path(temp_name)
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(url, timeout=120) as response:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        with temp_path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise RuntimeError(f"checksum mismatch for {destination.name}: expected {expected_sha256}, got {actual}")
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)


def _piper_urls(voice_id: str) -> tuple[tuple[str, str, int | None], ...]:
    if voice_id != DEFAULT_TTS_VOICE:
        raise ValueError(f"this setup currently supports only {DEFAULT_TTS_VOICE!r}; got {voice_id!r}")
    base = f"https://huggingface.co/rhasspy/piper-voices/resolve/{PIPER_VOICES_REVISION}/en/en_US/lessac/medium/"
    return tuple((f"{base}{filename}", filename, PIPER_FILE_SIZES[filename]) for filename in (f"{voice_id}.onnx", f"{voice_id}.onnx.json"))


def download_stt_model(model_size: str = DEFAULT_STT_MODEL) -> Path:
    """Download one pinned faster-whisper model into Alpha's runtime home."""
    if model_size not in STT_REVISIONS:
        raise ValueError(f"unsupported STT model {model_size!r}; choose one of {sorted(STT_REVISIONS)}")
    destination = stt_model_path(model_size)
    if _stt_assets_ready(destination):
        return destination

    from faster_whisper.utils import download_model

    destination.mkdir(parents=True, exist_ok=True)
    download_model(
        model_size,
        output_dir=str(destination),
        local_files_only=False,
        revision=STT_REVISIONS[model_size],
    )
    status = _stt_status(destination)
    if not status.ready:
        raise RuntimeError(f"faster-whisper download did not produce a usable model: {status.detail}")
    return destination


def download_tts_voice(voice_id: str = DEFAULT_TTS_VOICE) -> Path:
    """Download and checksum the pinned local Piper voice."""
    model_path = tts_voice_path(voice_id)
    for url, filename, expected_size in _piper_urls(voice_id):
        destination = model_path.parent / filename
        expected_sha = PIPER_VOICE_SHA256[filename]
        _download_verified(url, destination, expected_sha)
        actual_size = destination.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            raise RuntimeError(f"unexpected size for {filename}: expected {expected_size}, got {actual_size}")
    return model_path


def _kokoro_assets_ready(model_path: Path, voices_path: Path) -> bool:
    return model_path.is_file() and model_path.stat().st_size == KOKORO_MODEL_SIZE and voices_path.is_file() and voices_path.stat().st_size == KOKORO_VOICES_SIZE


def _download_with_size(url: str, destination: Path, expected_size: int) -> None:
    """Download *url* to *destination* and verify the exact byte size."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size == expected_size:
        return
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".part", dir=destination.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(url, timeout=120) as response:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        actual_size = temp_path.stat().st_size
        if actual_size != expected_size:
            raise RuntimeError(f"unexpected size for {destination.name}: expected {expected_size}, got {actual_size}")
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)


def download_kokoro() -> tuple[Path, Path]:
    """Download the pinned Kokoro ONNX model and voices (one-time, no API key)."""
    model_path = kokoro_model_path()
    voices_path = kokoro_voices_path()
    if _kokoro_assets_ready(model_path, voices_path):
        return model_path, voices_path
    _download_with_size(f"{KOKORO_RELEASE_BASE}/{KOKORO_VOICES_FILE}", voices_path, KOKORO_VOICES_SIZE)
    _download_with_size(f"{KOKORO_RELEASE_BASE}/{KOKORO_MODEL_FILE}", model_path, KOKORO_MODEL_SIZE)
    if not _kokoro_assets_ready(model_path, voices_path):
        raise RuntimeError("Kokoro download did not produce usable model assets")
    return model_path, voices_path


def _stt_assets_ready(path: Path) -> bool:
    return all((path / filename).is_file() and (path / filename).stat().st_size > 0 for filename in STT_REQUIRED_FILES)


def _stt_status(path: Path) -> AssetStatus:
    ready = _stt_assets_ready(path)
    return AssetStatus(
        name="faster-whisper",
        path=str(path),
        ready=ready,
        detail="model files present" if ready else "required faster-whisper files are missing",
    )


def _tts_assets_ready(model_path: Path) -> bool:
    config_path = Path(f"{model_path}.json")
    config_name = f"{model_path.name}.json"
    if not model_path.is_file() or not config_path.is_file():
        return False
    if model_path.name not in PIPER_VOICE_SHA256 or config_name not in PIPER_VOICE_SHA256:
        return False
    try:
        return (
            sha256_file(model_path) == PIPER_VOICE_SHA256[model_path.name]
            and sha256_file(config_path) == PIPER_VOICE_SHA256[config_name]
            and model_path.stat().st_size == PIPER_FILE_SIZES[model_path.name]
            and config_path.stat().st_size == PIPER_FILE_SIZES[config_name]
        )
    except OSError:
        return False


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def verify_assets(
    *,
    stt_model: str = DEFAULT_STT_MODEL,
    tts_voice: str = DEFAULT_TTS_VOICE,
) -> list[AssetStatus]:
    stt = stt_model_path(stt_model)
    tts = tts_voice_path(tts_voice)
    kokoro_model = kokoro_model_path()
    kokoro_voices = kokoro_voices_path()
    stt_dependency = _module_available("faster_whisper")
    tts_dependency = _module_available("piper")
    kokoro_dependency = _module_available("kokoro_onnx")
    stt_assets = _stt_assets_ready(stt)
    tts_assets = _tts_assets_ready(tts)
    kokoro_assets = _kokoro_assets_ready(kokoro_model, kokoro_voices)
    stt_ready = stt_dependency and stt_assets
    tts_ready = tts_dependency and tts_assets
    kokoro_ready = kokoro_dependency and kokoro_assets
    return [
        AssetStatus(
            name="faster-whisper",
            path=str(stt),
            ready=stt_ready,
            detail=("dependency installed; model files present" if stt_ready else "dependency missing or model files absent; run `make voice-setup`"),
        ),
        AssetStatus(
            name="kokoro",
            path=str(kokoro_model),
            ready=kokoro_ready,
            detail=("dependency installed; model files present" if kokoro_ready else "dependency missing or model files absent; run `make voice-setup`"),
        ),
        AssetStatus(
            name="piper",
            path=str(tts),
            ready=tts_ready,
            detail=("dependency installed; model files present and checksummed" if tts_ready else "dependency missing or model files absent; run `make voice-setup`"),
        ),
    ]


def write_manifest(*, stt_model: str, tts_voice: str) -> Path:
    root = models_dir()
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "voice_models.json"
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "stt": {
            "engine": "faster-whisper",
            "model": stt_model,
            "repository": STT_REPOSITORIES[stt_model],
            "revision": STT_REVISIONS[stt_model],
            "path": str(stt_model_path(stt_model)),
        },
        "tts": {
            "engine": "piper",
            "note": "piper is the real-time default; kokoro is the optional natural voice (see docs/VOICE_CONVERSATION.md)",
        },
        "tts_kokoro": {
            "engine": "kokoro",
            "voice": "af_bella",
            "repository": "thewh1teagle/kokoro-onnx",
            "release": "model-files-v1.0",
            "model_path": str(kokoro_model_path()),
            "voices_path": str(kokoro_voices_path()),
            "sizes": {KOKORO_MODEL_FILE: KOKORO_MODEL_SIZE, KOKORO_VOICES_FILE: KOKORO_VOICES_SIZE},
        },
        "tts_piper": {
            "engine": "piper",
            "voice": tts_voice,
            "repository": "rhasspy/piper-voices",
            "revision": PIPER_VOICES_REVISION,
            "path": str(tts_voice_path(tts_voice)),
            "sha256": {filename: PIPER_VOICE_SHA256[filename] for filename in (f"{tts_voice}.onnx", f"{tts_voice}.onnx.json")},
        },
    }
    temporary = manifest.with_suffix(".json.part")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, manifest)
    return manifest


def warm_up(*, stt_model: str, tts_voice: str, loader: Callable[[], None] | None = None) -> None:
    """Load both models once so the first conversation does not pay model init."""
    if loader is not None:
        loader()
        return
    from faster_whisper import WhisperModel
    from piper import PiperVoice

    stt = WhisperModel(str(stt_model_path(stt_model)), device="cpu", compute_type="int8", local_files_only=True)
    if not hasattr(stt, "transcribe"):
        raise RuntimeError("faster-whisper warm-up returned an invalid model")
    voice = PiperVoice.load(str(tts_voice_path(tts_voice)))
    if not hasattr(voice, "synthesize_wav"):
        raise RuntimeError("Piper warm-up returned an invalid voice")

    # Warm the optional natural-voice engine too, so a deployment that selects it
    # does not pay ONNX session construction on its first spoken turn. Piper stays
    # the warm real-time default.
    try:
        from kokoro_onnx import Kokoro

        Kokoro(str(kokoro_model_path()), str(kokoro_voices_path()))
    except Exception as exc:  # noqa: BLE001 - optional engine; Piper fallback remains
        print(f"Kokoro warm-up skipped: {type(exc).__name__}: {exc}")

    # openWakeWord downloads its pretrained weights on first load, then caches
    # them on disk. Pre-fetching here keeps the first real wake word silent-free.
    try:
        from openwakeword.utils import download_models

        download_models(model_names=["hey_jarvis"])
    except Exception as exc:  # noqa: BLE001 - optional engine; wake word degrades honestly
        print(f"openWakeWord warm-up skipped: {type(exc).__name__}: {exc}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Alpha's local Whisper + Piper + Kokoro voice assets")
    parser.add_argument("--stt-model", choices=sorted(STT_REVISIONS), default=DEFAULT_STT_MODEL)
    parser.add_argument("--verify-only", action="store_true", help="verify existing files without downloading")
    parser.add_argument("--skip-warmup", action="store_true", help="download/verify without loading model weights")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if not args.verify_only:
            print(f"Downloading faster-whisper {args.stt_model} (one-time, no API key)...")
            download_stt_model(args.stt_model)
            print("Downloading Kokoro TTS model (one-time, no API key)...")
            download_kokoro()
            print(f"Downloading Piper voice {DEFAULT_TTS_VOICE} (one-time, no API key)...")
            download_tts_voice(DEFAULT_TTS_VOICE)

        statuses = verify_assets(stt_model=args.stt_model, tts_voice=DEFAULT_TTS_VOICE)
        for status in statuses:
            marker = "ready" if status.ready else "missing"
            print(f"{status.name}: {marker} - {status.path} ({status.detail})")
        if not all(status.ready for status in statuses):
            return 1

        if not args.skip_warmup:
            print("Loading local models once to verify the installation...")
            warm_up(stt_model=args.stt_model, tts_voice=DEFAULT_TTS_VOICE)
        print("Local voice models are ready. Runtime speech inference stays on this machine.")
        write_manifest(stt_model=args.stt_model, tts_voice=DEFAULT_TTS_VOICE)
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary: show one actionable failure
        print(f"Voice setup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
