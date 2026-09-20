"""Local speech-to-text worker for voice memos (fully offline-capable).

Uses ``faster-whisper`` when installed; otherwise degrades to a structured
unavailable result instead of failing the run. The Gateway/process never
hard-depends on the extra: voice features probe availability first.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        return False


def transcribe_file(path: str | Path, *, model_size: str = "small", language: str | None = None) -> Transcription:
    """Transcribe one audio file locally. Never raises for missing engine."""
    file_path = Path(path)
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
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return Transcription(ok=False, reason="faster-whisper is not installed; install the voice extra to enable transcription.")
    try:
        model = WhisperModel(model_size, device="auto", compute_type="auto")
        segments, info = model.transcribe(str(file_path), language=language)
        text = " ".join(s.text.strip() for s in segments if s.text and s.text.strip())
        detected = getattr(info, "language", None) or language
        return Transcription(ok=True, text=text, language=detected, engine=f"faster-whisper/{model_size}")
    except Exception as exc:
        logger.warning("Local transcription failed for %s", file_path, exc_info=True)
        return Transcription(ok=False, engine=f"faster-whisper/{model_size}", reason=f"transcription failed: {exc}")
