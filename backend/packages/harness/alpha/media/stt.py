"""Local speech-to-text with one process-cached faster-whisper model.

The public :func:`transcribe_file` signature remains compatible with existing
voice-memo callers while accepting bounded local model/runtime settings. Normal
requests use ``local_files_only=True`` and deployment-local assets; weights are
never downloaded implicitly.

Two bounds this module owns:

* **Fail closed before construction.** ``transcribe_file`` checks
  :func:`whisper_model_assets_present` *before* building a model. faster-whisper
  treats a non-directory first argument as a Hugging Face model id and calls
  ``download_model`` on it, so constructing a model for a missing local model
  directory reaches the network. Checking first keeps request handling offline
  and reports an honest reason instead of a raw ``ValueError``.
* **Duration, not just bytes.** ``MAX_AUDIO_MB`` bounds the container; a 25 MiB
  16 kHz mono PCM16 file is ~13.6 minutes of audio, far past the streaming
  contract's ``max_utterance_seconds``. A WAV header is read when present and the
  clip is refused before it occupies an inference thread.
"""

from __future__ import annotations

import contextlib
import logging
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from alpha.multimodal.local_models import (
    WhisperModelSpec,
    resolve_whisper_model_path,
    transcribe_with_cached_whisper,
    whisper_model_assets_present,
)

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".ogg", ".flac", ".opus", ".webm"})
MAX_AUDIO_MB = 25.0
#: Hard per-clip duration bound. Matches the top of the ``voice.streaming``
#: contract (``max_utterance_seconds`` maxes out at 120s) with headroom for a
#: real long-form memo, and keeps one request from monopolising a CPU for
#: minutes on a worker thread that has no deadline of its own.
MAX_AUDIO_SECONDS = 120.0
#: Assumed PCM16 mono sample rate for a container we cannot cheaply probe. Used
#: only to derive a conservative upper bound on duration from file size.
ASSUMED_SAMPLE_RATE = 16_000


@dataclass
class Transcription:
    ok: bool
    text: str = ""
    language: str | None = None
    engine: str = "none"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def stt_available() -> bool:
    """Whether the optional faster-whisper dependency imports (no model load)."""

    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 - a broken optional native import is unavailable
        return False


def whisper_model_spec(
    *,
    model_size: str = "small",
    model_path: str | None = None,
    device: str = "auto",
    compute_type: str = "int8",
    local_files_only: bool = True,
) -> WhisperModelSpec:
    return WhisperModelSpec(
        model_path=resolve_whisper_model_path(model_path, model_size),
        model_size=model_size,
        device=device,
        compute_type=compute_type,
        local_files_only=local_files_only,
    )


def stt_model_available(
    *,
    model_size: str = "small",
    model_path: str | None = None,
    device: str = "auto",
    compute_type: str = "int8",
    local_files_only: bool = True,
) -> bool:
    try:
        return whisper_model_assets_present(
            whisper_model_spec(
                model_size=model_size,
                model_path=model_path,
                device=device,
                compute_type=compute_type,
                local_files_only=local_files_only,
            )
        )
    except (OSError, ValueError):
        return False


def wav_duration_seconds(file_path: Path) -> float | None:
    """Exact duration from a WAV header, or None when it cannot be read cheaply."""

    if file_path.suffix.lower() != ".wav":
        return None
    try:
        with contextlib.closing(wave.open(str(file_path), "rb")) as handle:
            rate = handle.getframerate()
            frames = handle.getnframes()
            if rate <= 0:
                return None
            return frames / float(rate)
    except (OSError, wave.Error, EOFError):
        return None


def audio_duration_seconds(file_path: Path, size_bytes: int) -> float:
    """Best-effort clip duration: exact for WAV, otherwise bounded by size.

    Compressed containers are not decoded here (that is the engine's job); the
    size-derived figure is an upper bound assuming PCM16 mono, so a 25 MiB
    upload is refused long before it can pin a CPU.
    """

    exact = wav_duration_seconds(file_path)
    if exact is not None:
        return exact
    return size_bytes / float(ASSUMED_SAMPLE_RATE * 2)


def transcribe_file(
    path: str | Path,
    *,
    model_size: str = "small",
    language: str | None = None,
    model_path: str | None = None,
    device: str = "auto",
    compute_type: str = "int8",
    beam_size: int = 5,
    local_files_only: bool = True,
) -> Transcription:
    """Transcribe one audio file locally without raising for expected failures."""

    file_path = Path(path)
    engine = "faster-whisper/local" if model_path else f"faster-whisper/{model_size}"
    if not file_path.exists():
        return Transcription(ok=False, reason=f"audio file not found: {file_path}")
    if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return Transcription(ok=False, reason=f"unsupported audio type '{file_path.suffix}'; supported: {sorted(SUPPORTED_SUFFIXES)}")
    try:
        size_bytes = file_path.stat().st_size
    except OSError as exc:
        return Transcription(ok=False, reason=f"cannot stat audio file: {exc}")
    size_mb = size_bytes / (1024.0 * 1024.0)
    if size_mb > MAX_AUDIO_MB:
        return Transcription(ok=False, reason=f"audio file {size_mb:.1f} MiB exceeds {MAX_AUDIO_MB:.0f} MiB cap.")
    seconds = audio_duration_seconds(file_path, size_bytes)
    if seconds > MAX_AUDIO_SECONDS:
        return Transcription(
            ok=False,
            reason=f"audio clip is {seconds:.0f}s, over the {MAX_AUDIO_SECONDS:.0f}s per-clip cap; split the recording before transcribing.",
        )
    if not stt_available():
        return Transcription(ok=False, reason="faster-whisper is not installed; install the voice extra to enable transcription.")
    try:
        spec = whisper_model_spec(
            model_size=model_size,
            model_path=model_path,
            device=device,
            compute_type=compute_type,
            local_files_only=local_files_only,
        )
    except (OSError, ValueError) as exc:
        return Transcription(ok=False, engine=engine, reason=f"invalid local model configuration: {exc}")
    if not whisper_model_assets_present(spec):
        # Fail closed here rather than letting WhisperModel fall through to its
        # Hugging Face download path for a directory that does not exist.
        return Transcription(
            ok=False,
            engine=engine,
            reason="local faster-whisper model assets are not present for this configuration; run voice setup or configure voice.stt.model_path (path withheld)",
        )
    try:
        segments, info = transcribe_with_cached_whisper(
            file_path,
            spec,
            language=language,
            beam_size=beam_size,
        )
        text = " ".join(segment.text.strip() for segment in segments if segment.text and segment.text.strip())
        detected = getattr(info, "language", None) or language
        return Transcription(ok=True, text=text, language=detected, engine=engine)
    except Exception as exc:
        logger.warning("Local transcription failed for %s", file_path, exc_info=True)
        return Transcription(ok=False, engine=engine, reason=f"transcription failed: {type(exc).__name__}")
