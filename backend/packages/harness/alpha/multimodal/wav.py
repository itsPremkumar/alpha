"""Stdlib WAV helpers: PCM16 frames <-> WAV bytes, with sanity checks.

Kept dependency-free (``wave``/``io`` only) so the WebSocket voice path can
wrap raw browser-captured PCM16 into a container the local STT engine
(``alpha.media.stt``) accepts, without importing any audio library.
"""

from __future__ import annotations

import io
import wave

DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2  # PCM16


class WavError(ValueError):
    """A WAV/PCM sanity check failed (honest, never silently corrected)."""


def pcm16_to_wav(
    pcm: bytes,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
) -> bytes:
    """Wrap raw little-endian PCM16 frames in a minimal WAV container."""
    if sample_rate <= 0 or channels <= 0:
        raise WavError(f"invalid WAV parameters: sample_rate={sample_rate} channels={channels}")
    if len(pcm) % SAMPLE_WIDTH_BYTES != 0:
        raise WavError(f"pcm16 byte length must be a multiple of {SAMPLE_WIDTH_BYTES}, got {len(pcm)}")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


def wav_to_pcm16(data: bytes) -> tuple[bytes, int]:
    """Decode a WAV file to ``(pcm16_frames, sample_rate)``.

    Raises :class:`WavError` for non-WAV input or non-PCM16 encodings instead
    of guessing a conversion.
    """
    if not data:
        raise WavError("empty WAV payload")
    try:
        with wave.open(io.BytesIO(data), "rb") as wav_file:
            if wav_file.getsampwidth() != SAMPLE_WIDTH_BYTES:
                raise WavError(f"expected 16-bit PCM WAV, got sample width {wav_file.getsampwidth()} bytes")
            pcm = wav_file.readframes(wav_file.getnframes())
            rate = wav_file.getframerate()
    except wave.Error as exc:
        raise WavError(f"not a readable WAV file: {exc}") from exc
    if rate <= 0:
        raise WavError(f"invalid WAV sample rate: {rate}")
    return pcm, rate


def bounded_pcm16(pcm: bytes, *, sample_rate: int = DEFAULT_SAMPLE_RATE, max_seconds: float = 120.0) -> bytes:
    """Reject absurdly long PCM streams before they reach an engine."""
    if sample_rate <= 0:
        raise WavError(f"invalid sample rate: {sample_rate}")
    seconds = len(pcm) / (SAMPLE_WIDTH_BYTES * sample_rate)
    if seconds > max_seconds:
        raise WavError(f"pcm16 stream is {seconds:.1f}s, over the {max_seconds:.0f}s bound")
    return pcm
