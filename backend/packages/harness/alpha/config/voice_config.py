"""Bounded operator configuration for local voice and multimodal inference.

The default speech route is deliberately local-only. Model files are selected by
trusted configuration (or deployment-local defaults under ``runtime_home()``);
browser/API callers may select a safe Piper voice *id*, never a filesystem path.
All fields are hot-reloadable: model cache identity is derived from the effective
values on each invocation rather than captured at process startup.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

VOICE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_VOICE_ID_RE = re.compile(VOICE_ID_PATTERN)
VoiceId = Annotated[str, StringConstraints(pattern=VOICE_ID_PATTERN, min_length=1, max_length=64)]
_ModelSize = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]


def is_valid_voice_id(value: object) -> bool:
    """Return whether *value* is safe to use as one filename component."""

    return isinstance(value, str) and _VOICE_ID_RE.fullmatch(value) is not None


class VoiceRoutingConfig(BaseModel):
    """Speech routing policy. Non-speech multimodal capabilities are unaffected."""

    mode: Literal["local_only", "automatic"] = Field(
        default="local_only",
        description="TTS/STT route. local_only prevents T1/T2 speech execution and records policy skips before T3.",
    )
    model_config = ConfigDict(extra="allow")


class WakeWordConfig(BaseModel):
    """Legacy/manual wake-word session defaults (not required for conversation mode)."""

    engine: str = Field(default="openwakeword", description="Wake-word engine id (only openwakeword ships today).")
    threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Score in [0, 1] at which a frame wakes the session; the scored value is always disclosed.",
    )
    armed_default: bool = Field(
        default=False,
        description="Whether the UI starts wake-armed. Off by default: the mic stays user-initiated (privacy).",
    )
    model_config = ConfigDict(extra="allow")


class TtsConfig(BaseModel):
    """Local Piper text-to-speech defaults."""

    autoplay: bool = Field(default=True, description="Autoplay assistant speech after each completed answer.")
    engine: Literal["piper"] = Field(default="piper", description="Local TTS engine. Piper is the only supported runtime engine.")
    voice: VoiceId = Field(
        default="en_US-lessac-medium",
        description="Safe Piper voice id used as the default model filename; never a filesystem path.",
    )
    model_path: str | Path | None = Field(
        default=None,
        description="Operator-owned Piper .onnx path. Null uses runtime_home()/voice/models/piper/<voice>.onnx.",
    )
    length_scale: float = Field(default=1.0, ge=0.25, le=4.0, description="Piper speaking-time scale; lower is faster.")
    noise_scale: float = Field(default=0.667, ge=0.0, le=2.0, description="Piper phoneme-duration noise scale.")
    volume: float = Field(default=0.9, ge=0.0, le=2.0, description="Post-synthesis linear PCM volume multiplier.")

    @field_validator("voice", mode="before")
    @classmethod
    def _default_legacy_null_voice(cls, value: object) -> object:
        # Config v45 allowed null. Preserve that file while immediately pinning
        # the safe setup voice; no runtime path ever interpolates null.
        return "en_US-lessac-medium" if value is None else value

    @field_validator("model_path")
    @classmethod
    def _validate_model_path(cls, value: str | Path | None) -> str | Path | None:
        if value is not None and not 1 <= len(str(value)) <= 1024:
            raise ValueError("model_path must contain between 1 and 1024 characters")
        return value

    model_config = ConfigDict(extra="allow")


class SttConfig(BaseModel):
    """Local faster-whisper transcription defaults."""

    model_size: _ModelSize = Field(default="small", description="Deployment-local faster-whisper model size or id.")
    model_path: str | Path | None = Field(
        default=None,
        description="Operator-owned faster-whisper directory. Null uses runtime_home()/voice/models/faster-whisper/<model_size>.",
    )
    language: str | None = Field(
        default=None,
        min_length=2,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Optional language hint; null lets faster-whisper auto-detect.",
    )
    device: Literal["auto", "cpu", "cuda"] = Field(default="auto", description="faster-whisper inference device.")
    compute_type: str = Field(
        default="int8",
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="faster-whisper/CTranslate2 compute type (int8 is the CPU-safe default).",
    )
    beam_size: int = Field(default=1, ge=1, le=20, description="Bounded decoder beam size used for local transcription.")
    local_files_only: bool = Field(
        default=True,
        description="Require model assets to exist before construction; request handling never downloads weights. Keep true outside explicit setup.",
    )

    @field_validator("model_path")
    @classmethod
    def _validate_model_path(cls, value: str | Path | None) -> str | Path | None:
        if value is not None and not 1 <= len(str(value)) <= 1024:
            raise ValueError("model_path must contain between 1 and 1024 characters")
        return value

    model_config = ConfigDict(extra="allow")


class StreamingConfig(BaseModel):
    """Bounds and timing for Gateway-owned PCM16 endpointing."""

    sample_rate: Literal[8_000, 16_000, 32_000, 48_000] = Field(default=16_000, description="Accepted mono PCM16 sample rate.")
    frame_ms: Literal[10, 20, 30] = Field(default=20, description="Exact WebRTC VAD frame duration in milliseconds.")
    pre_roll_ms: int = Field(default=200, ge=0, le=2_000, description="Audio retained before speech-start confirmation.")
    speech_start_ms: int = Field(default=60, ge=20, le=2_000, description="Consecutive speech required before an utterance starts.")
    endpoint_silence_ms: int = Field(default=700, ge=100, le=5_000, description="Trailing silence that closes an utterance.")
    partial_interval_ms: int = Field(default=900, ge=100, le=10_000, description="Minimum cadence between interim transcript requests.")
    max_utterance_seconds: float = Field(default=30.0, ge=1.0, le=120.0, description="Hard per-utterance duration bound.")
    max_frame_bytes: int = Field(default=65_536, ge=64, le=262_144, description="Maximum decoded PCM16 bytes accepted in one WS audio message.")
    max_sessions: int = Field(default=4, ge=1, le=32, description="Process-local simultaneous authenticated voice WebSocket limit.")

    @model_validator(mode="after")
    def _validate_frame_budget(self) -> StreamingConfig:
        exact_frame_bytes = self.sample_rate * 2 * self.frame_ms // 1_000
        if self.max_frame_bytes < exact_frame_bytes:
            raise ValueError(f"max_frame_bytes must hold at least one {self.frame_ms}ms PCM16 frame ({exact_frame_bytes} bytes)")
        if self.pre_roll_ms + self.endpoint_silence_ms > self.max_utterance_seconds * 1_000:
            raise ValueError("pre_roll_ms + endpoint_silence_ms cannot exceed max_utterance_seconds")
        return self

    model_config = ConfigDict(extra="allow")


class VoiceConfig(BaseModel):
    """Top-level ``voice:`` settings for speech and legacy wake-word controls."""

    enabled: bool = Field(default=True, description="Master switch for every speech operation, including direct PTT transcription.")
    routing: VoiceRoutingConfig = Field(default_factory=VoiceRoutingConfig, description="TTS/STT tier routing policy.")
    wake_word: WakeWordConfig = Field(default_factory=WakeWordConfig, description="Optional/manual wake-word settings.")
    tts: TtsConfig = Field(default_factory=TtsConfig, description="Local Piper settings.")
    stt: SttConfig = Field(default_factory=SttConfig, description="Local faster-whisper settings.")
    streaming: StreamingConfig = Field(default_factory=StreamingConfig, description="Realtime PCM16 endpointing bounds.")
    model_config = ConfigDict(extra="allow")
