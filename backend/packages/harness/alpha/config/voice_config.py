"""Voice & multimodal settings (all defaulted — no config_version bump).

The shipped ``config.example.yaml`` / live ``config.yaml`` gain a ``voice:``
block centrally by the main agent; every field here has a default so an absent
section behaves exactly like ``voice: {enabled: true}``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WakeWordConfig(BaseModel):
    """Server-side wake-word session defaults."""

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
    """Text-to-speech defaults."""

    autoplay: bool = Field(default=False, description="Autoplay assistant speech after each answer.")
    voice: str | None = Field(default=None, description="Engine voice id; null lets the serving engine choose.")
    model_config = ConfigDict(extra="allow")


class SttConfig(BaseModel):
    """Speech-to-text defaults (forwarded to alpha.media.stt / T1 providers)."""

    model_size: str = Field(default="small", description="faster-whisper model size (tiny/base/small/medium/large-*).")
    language: str | None = Field(default=None, description="Language hint; null lets the engine auto-detect.")
    model_config = ConfigDict(extra="allow")


class VoiceConfig(BaseModel):
    """Top-level ``voice:`` block for the mic/speaker/wake-word feature wave."""

    enabled: bool = Field(default=True, description="Master switch; false disables the voice endpoints (honest 404).")
    wake_word: WakeWordConfig = Field(default_factory=WakeWordConfig, description="Wake-word engine settings.")
    tts: TtsConfig = Field(default_factory=TtsConfig, description="Text-to-speech settings.")
    stt: SttConfig = Field(default_factory=SttConfig, description="Speech-to-text settings.")
    model_config = ConfigDict(extra="allow")
