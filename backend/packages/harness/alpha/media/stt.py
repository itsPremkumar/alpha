"""Local speech-to-text with one process-cached faster-whisper model.

The public :func:`transcribe_file` signature remains compatible with existing
voice-memo callers while accepting bounded local model/runtime settings. Normal
requests use ``local_files_only=True`` and deployment-local assets; weights are
never downloaded implicitly.
"""

from __future__ import annotations

import logging
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
        size_mb = file_path.stat().st_size / (1024.0 * 1024.0)
    except OSError as exc:
        return Transcription(ok=False, reason=f"cannot stat audio file: {exc}")
    if size_mb > MAX_AUDIO_MB:
        return Transcription(ok=False, reason=f"audio file {size_mb:.1f} MiB exceeds {MAX_AUDIO_MB:.0f} MiB cap.")
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
