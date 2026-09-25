"""Process-local caches and safe path resolution for offline speech models.

This module is import-light: model libraries load only inside cache factories or
synthesis calls. A cache retains one active model per engine, serializes model
construction, and serializes inference because Whisper/Piper objects are not
assumed thread-safe. Effective configuration is part of each cache key, so a
hot-reloaded model/device/compute/synthesis change cannot reuse stale weights.
"""

from __future__ import annotations

import re
import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.config.voice_config import is_valid_voice_id

_WHISPER_ASSET_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
_MODEL_SIZE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True, slots=True)
class WhisperModelSpec:
    """Effective identity/settings for one cached faster-whisper model."""

    model_path: Path | None
    model_size: str
    device: str
    compute_type: str
    local_files_only: bool = True

    @property
    def model_reference(self) -> str:
        return str(self.model_path) if self.model_path is not None else self.model_size

    @property
    def cache_key(self) -> tuple[str, str, str, str, bool]:
        return (
            str(self.model_path.resolve()) if self.model_path is not None else self.model_size,
            self.model_size,
            self.device,
            self.compute_type,
            self.local_files_only,
        )


@dataclass(frozen=True, slots=True)
class PiperModelSpec:
    """Effective identity/settings for one cached Piper model."""

    model_path: Path
    length_scale: float
    noise_scale: float
    volume: float

    def __post_init__(self) -> None:
        if self.length_scale <= 0 or self.noise_scale < 0 or self.volume < 0:
            raise ValueError("Piper synthesis settings must be non-negative and length_scale must be positive")

    @property
    def cache_key(self) -> tuple[str, int, int]:
        path = self.model_path.resolve()
        try:
            stat = path.stat()
        except OSError:
            return (str(path), 0, 0)
        return (str(path), stat.st_mtime_ns, stat.st_size)


def _operator_path(value: str | Path, *, home: Path | None = None) -> Path:
    root = (home or runtime_home()).resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def resolve_piper_model_path(
    voice_id: str,
    configured_path: str | Path | None,
    legacy_path: str | Path | None,
    *,
    home: Path | None = None,
) -> Path:
    """Resolve a trusted Piper model path without ever interpolating a client path.

    Priority is operator ``voice.tts.model_path``, then the optional legacy
    ``ALPHA_PIPER_VOICE`` operator environment fallback, then the deployment-local
    ``voice/models/piper/<safe-voice-id>.onnx`` default.
    """

    if not is_valid_voice_id(voice_id):
        raise ValueError("Piper voice id must match ^[A-Za-z0-9_-]{1,64}$")
    if configured_path:
        return _operator_path(configured_path, home=home)
    if legacy_path:
        return _operator_path(legacy_path, home=home)
    root = (home or runtime_home()).resolve()
    return root / "voice" / "models" / "piper" / f"{voice_id}.onnx"


def resolve_whisper_model_path(
    configured_path: str | Path | None,
    model_size: str,
    *,
    home: Path | None = None,
) -> Path:
    """Resolve the deployment-local faster-whisper directory for *model_size*."""

    if _MODEL_SIZE_RE.fullmatch(model_size) is None:
        raise ValueError("faster-whisper model size must match ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    if configured_path:
        return _operator_path(configured_path, home=home)
    return (home or runtime_home()).resolve() / "voice" / "models" / "faster-whisper" / model_size


def whisper_model_assets_present(spec: WhisperModelSpec) -> bool:
    """Check required setup-time files without importing/loading model weights."""

    if spec.model_path is None:
        return False
    return all((spec.model_path / filename).is_file() and (spec.model_path / filename).stat().st_size > 0 for filename in _WHISPER_ASSET_FILES)


def piper_model_assets_present(spec: PiperModelSpec) -> bool:
    """Return whether both Piper weights and their adjacent JSON config exist."""

    try:
        config_path = Path(f"{spec.model_path}.json")
        return spec.model_path.is_file() and spec.model_path.stat().st_size > 0 and config_path.is_file() and config_path.stat().st_size > 0
    except OSError:
        return False


class FasterWhisperModelCache:
    """One active Whisper model, keyed by effective local/runtime settings."""

    def __init__(self, *, factory: Callable[[WhisperModelSpec], Any] | None = None) -> None:
        self._factory = factory or self._default_factory
        self._load_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._inference_semaphore = threading.BoundedSemaphore(1)
        self._key: tuple[str, str, str, str, bool] | None = None
        self._model: Any | None = None

    @staticmethod
    def _default_factory(spec: WhisperModelSpec) -> Any:
        from faster_whisper import WhisperModel

        return WhisperModel(
            spec.model_reference,
            device=spec.device,
            compute_type=spec.compute_type,
            local_files_only=spec.local_files_only,
        )

    @property
    def active_key(self) -> tuple[str, str, str, str, bool] | None:
        with self._state_lock:
            return self._key

    def get(self, spec: WhisperModelSpec) -> Any:
        with self._load_lock:
            with self._state_lock:
                if self._model is not None and self._key == spec.cache_key:
                    return self._model
            # Construct outside the state lock but under the single-flight build
            # lock. A failed construction leaves the previous active model intact.
            model = self._factory(spec)
            with self._state_lock:
                self._key = spec.cache_key
                self._model = model
            return model

    def transcribe(
        self,
        path: str | Path,
        spec: WhisperModelSpec,
        *,
        language: str | None,
        beam_size: int,
    ) -> tuple[Any, Any]:
        with self._inference_semaphore:
            model = self.get(spec)
            return model.transcribe(str(path), language=language, beam_size=beam_size)

    def clear_for_test(self) -> None:
        with self._inference_semaphore:
            with self._load_lock:
                with self._state_lock:
                    self._key = None
                    self._model = None


class PiperModelCache:
    """One active Piper voice, keyed by model path and synthesis settings."""

    def __init__(
        self,
        *,
        factory: Callable[[PiperModelSpec], Any] | None = None,
        synthesis_config_factory: Callable[[PiperModelSpec], Any] | None = None,
    ) -> None:
        self._factory = factory or self._default_factory
        self._synthesis_config_factory = synthesis_config_factory or self._default_synthesis_config
        self._load_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._inference_semaphore = threading.BoundedSemaphore(1)
        self._key: tuple[str, int, int] | None = None
        self._voice: Any | None = None

    @staticmethod
    def _default_factory(spec: PiperModelSpec) -> Any:
        from piper import PiperVoice

        return PiperVoice.load(str(spec.model_path))

    @staticmethod
    def _default_synthesis_config(spec: PiperModelSpec) -> Any:
        from piper import SynthesisConfig

        return SynthesisConfig(
            length_scale=spec.length_scale,
            noise_scale=spec.noise_scale,
            volume=spec.volume,
        )

    @property
    def active_key(self) -> tuple[str, int, int] | None:
        with self._state_lock:
            return self._key

    def get(self, spec: PiperModelSpec) -> Any:
        with self._load_lock:
            with self._state_lock:
                if self._voice is not None and self._key == spec.cache_key:
                    return self._voice
            voice = self._factory(spec)
            with self._state_lock:
                self._key = spec.cache_key
                self._voice = voice
            return voice

    def synthesize(self, text: str, spec: PiperModelSpec) -> bytes:
        """Serialize Piper inference and return bounded WAV bytes (no placeholder audio)."""

        with self._inference_semaphore:
            voice = self.get(spec)
            synthesis = self._synthesis_config_factory(spec)
            buffer = BytesIO()
            synthesize_wav = getattr(voice, "synthesize_wav", None)
            with wave.open(buffer, "wb") as wav_file:
                if callable(synthesize_wav):
                    synthesize_wav(text, wav_file, syn_config=synthesis)
                else:
                    # The supported Piper 1.8 API has synthesize_wav. Keep an
                    # honest compatibility branch for older operator-managed builds.
                    chunks = list(voice.synthesize(text, syn_config=synthesis))
                    for index, chunk in enumerate(chunks):
                        if index == 0:
                            wav_file.setframerate(chunk.sample_rate)
                            wav_file.setsampwidth(chunk.sample_width)
                            wav_file.setnchannels(chunk.sample_channels)
                        wav_file.writeframes(chunk.audio_int16_bytes)
            audio = buffer.getvalue()
            if not audio:
                raise RuntimeError("piper returned 0 audio bytes")
            return audio

    def clear_for_test(self) -> None:
        with self._inference_semaphore:
            with self._load_lock:
                with self._state_lock:
                    self._key = None
                    self._voice = None


_WHISPER_CACHE = FasterWhisperModelCache()
_PIPER_CACHE = PiperModelCache()


def get_whisper_model(spec: WhisperModelSpec) -> Any:
    return _WHISPER_CACHE.get(spec)


def transcribe_with_cached_whisper(
    path: str | Path,
    spec: WhisperModelSpec,
    *,
    language: str | None,
    beam_size: int,
) -> tuple[Any, Any]:
    return _WHISPER_CACHE.transcribe(path, spec, language=language, beam_size=beam_size)


def synthesize_with_cached_piper(text: str, spec: PiperModelSpec) -> bytes:
    return _PIPER_CACHE.synthesize(text, spec)


def clear_whisper_model_cache_for_test() -> None:
    """Clear the active Whisper reference without loading weights."""

    _WHISPER_CACHE.clear_for_test()


def clear_piper_model_cache_for_test() -> None:
    """Clear the active Piper reference without loading weights."""

    _PIPER_CACHE.clear_for_test()


def clear_model_caches_for_test() -> None:
    """Clear both cached model references; no model weights are loaded here."""

    clear_whisper_model_cache_for_test()
    clear_piper_model_cache_for_test()
