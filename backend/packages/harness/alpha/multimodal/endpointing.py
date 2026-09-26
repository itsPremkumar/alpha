"""Pure streaming PCM16 endpointing with an injected speech detector.

The endpointing state machine has no WebRTC dependency: tests and embedders can
inject any ``is_speech(frame)`` detector. The production detector below lazily
uses ``webrtcvad-wheels`` and reports a truthful import error when that optional
package is absent.

Bytes are retained until an exact detector frame is available. Odd-length PCM16
chunks are rejected without mutating the retained remainder.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass
from typing import Literal, Protocol

from alpha.multimodal.wav import SAMPLE_WIDTH_BYTES


class SpeechDetector(Protocol):
    """Minimal injected detector contract."""

    def is_speech(self, frame: bytes) -> bool: ...


class SpeechDetectorUnavailable(ImportError):
    """The optional production VAD package is not installed."""


@dataclass(frozen=True, slots=True)
class EndpointingConfig:
    """Timing and hard bounds for one reusable conversation endpoint."""

    sample_rate: int = 16_000
    frame_ms: int = 20
    pre_roll_ms: int = 200
    speech_start_ms: int = 60
    endpoint_silence_ms: int = 700
    partial_interval_ms: int = 900
    max_utterance_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.frame_ms not in {10, 20, 30}:
            raise ValueError("frame_ms must be 10, 20, or 30")
        if self.pre_roll_ms < 0 or self.speech_start_ms <= 0:
            raise ValueError("pre_roll_ms must be non-negative and speech_start_ms must be positive")
        if self.endpoint_silence_ms <= 0 or self.partial_interval_ms < 0 or self.max_utterance_seconds <= 0:
            raise ValueError("endpoint/partial/utterance settings must be positive (partial may be disabled with 0)")

    @property
    def frame_bytes(self) -> int:
        return self.sample_rate * SAMPLE_WIDTH_BYTES * self.frame_ms // 1_000

    @property
    def max_utterance_bytes(self) -> int:
        return int(self.sample_rate * SAMPLE_WIDTH_BYTES * self.max_utterance_seconds)

    @property
    def pre_roll_frames(self) -> int:
        return math.ceil(self.pre_roll_ms / self.frame_ms)

    @property
    def speech_start_frames(self) -> int:
        return max(1, math.ceil(self.speech_start_ms / self.frame_ms))


@dataclass(frozen=True, slots=True)
class EndpointEvent:
    """One endpointing transition and the exact audio associated with it."""

    kind: Literal["speech_started", "partial", "endpoint"]
    utterance_id: int
    audio: bytes
    duration_ms: int
    reason: str = ""

    @property
    def is_final(self) -> bool:
        return self.kind == "endpoint"


class StreamingEndpointDetector:
    """Reusable VAD/endpoint state machine; each endpoint resets for the next turn."""

    def __init__(self, detector: SpeechDetector, config: EndpointingConfig | None = None) -> None:
        self.detector = detector
        self.config = config or EndpointingConfig()
        self._pending = bytearray()
        self._pre_roll: deque[bytes] = deque(maxlen=self.config.pre_roll_frames)
        self._speech_start: deque[bytes] = deque(maxlen=self.config.speech_start_frames)
        self._speech_run_frames = 0
        self._utterance = bytearray()
        self._speaking = False
        self._silence_ms = 0
        self._next_partial_ms: int | None = None
        self._utterance_id = 0

    @property
    def speaking(self) -> bool:
        return self._speaking

    @property
    def utterance_id(self) -> int:
        return self._utterance_id

    @property
    def buffered_byte_count(self) -> int:
        """Bytes retained because they do not yet form an exact detector frame."""

        return len(self._pending)

    @property
    def utterance_byte_count(self) -> int:
        return len(self._utterance)

    def reset(self) -> None:
        """Discard the current partial utterance while retaining the utterance counter."""

        self._pending.clear()
        self._pre_roll.clear()
        self._speech_start.clear()
        self._speech_run_frames = 0
        self._utterance.clear()
        self._speaking = False
        self._silence_ms = 0
        self._next_partial_ms = None

    def push(self, data: bytes | bytearray | memoryview) -> list[EndpointEvent]:
        """Append PCM16 bytes and return all transitions completed by this chunk.

        An odd byte count is a malformed frame and is rejected before the pending
        buffer is changed. Every complete detector frame is consumed exactly once;
        any trailing even remainder remains buffered for the next call.
        """

        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("PCM16 data must be bytes-like")
        incoming = bytes(data)
        if len(incoming) % SAMPLE_WIDTH_BYTES:
            raise ValueError(f"PCM16 byte length must be a multiple of {SAMPLE_WIDTH_BYTES}, got {len(incoming)}")
        if not incoming:
            return []

        self._pending.extend(incoming)
        events: list[EndpointEvent] = []
        frame_bytes = self.config.frame_bytes
        while len(self._pending) >= frame_bytes:
            frame = bytes(self._pending[:frame_bytes])
            del self._pending[:frame_bytes]
            events.extend(self._consume_frame(frame))
        return events

    def _consume_frame(self, frame: bytes) -> list[EndpointEvent]:
        is_speech = bool(self.detector.is_speech(frame))
        if not self._speaking:
            return self._consume_pre_speech(frame, is_speech)
        return self._consume_active(frame, is_speech)

    def _consume_pre_speech(self, frame: bytes, is_speech: bool) -> list[EndpointEvent]:
        if not is_speech:
            self._speech_start.clear()
            self._speech_run_frames = 0
            self._pre_roll.append(frame)
            return []

        self._speech_start.append(frame)
        self._speech_run_frames += 1
        if self._speech_run_frames < self.config.speech_start_frames:
            return []

        self._utterance_id += 1
        self._utterance = bytearray(b"".join((*self._pre_roll, *self._speech_start)))
        self._pre_roll.clear()
        self._speech_start.clear()
        self._speaking = True
        self._silence_ms = 0
        duration_ms = self._duration_ms()
        self._next_partial_ms = duration_ms + self.config.partial_interval_ms if self.config.partial_interval_ms else None
        return [
            EndpointEvent(
                kind="speech_started",
                utterance_id=self._utterance_id,
                audio=bytes(self._utterance),
                duration_ms=duration_ms,
                reason="speech_start",
            )
        ]

    def _consume_active(self, frame: bytes, is_speech: bool) -> list[EndpointEvent]:
        max_bytes = self.config.max_utterance_bytes
        remaining = max(0, max_bytes - len(self._utterance))
        self._utterance.extend(frame[:remaining])
        if is_speech:
            self._silence_ms = 0
        else:
            self._silence_ms += self.config.frame_ms

        duration_ms = self._duration_ms()
        if len(self._utterance) >= max_bytes:
            return [self._endpoint_event("max_duration")]
        if self._silence_ms >= self.config.endpoint_silence_ms:
            return [self._endpoint_event("silence")]

        if self._next_partial_ms is not None and duration_ms >= self._next_partial_ms:
            interval = self.config.partial_interval_ms
            self._next_partial_ms = max(duration_ms + interval, self._next_partial_ms + interval)
            return [
                EndpointEvent(
                    kind="partial",
                    utterance_id=self._utterance_id,
                    audio=bytes(self._utterance),
                    duration_ms=duration_ms,
                    reason="partial_interval",
                )
            ]
        return []

    def _duration_ms(self) -> int:
        return int(len(self._utterance) * 1_000 / (self.config.sample_rate * SAMPLE_WIDTH_BYTES))

    def _endpoint_event(self, reason: str) -> EndpointEvent:
        event = EndpointEvent(
            kind="endpoint",
            utterance_id=self._utterance_id,
            audio=bytes(self._utterance),
            duration_ms=self._duration_ms(),
            reason=reason,
        )
        # Auto-reset is the socket-reuse invariant: the next speech frame starts a
        # fresh utterance while the monotonically increasing id fences stale work.
        self._utterance.clear()
        self._pre_roll.clear()
        self._speech_run_frames = 0
        self._speaking = False
        self._silence_ms = 0
        self._next_partial_ms = None
        return event


class WebRtcSpeechDetector:
    """Production detector backed by the optional ``webrtcvad-wheels`` package."""

    def __init__(self, *, sample_rate: int, frame_ms: int, aggressiveness: int = 2) -> None:
        if sample_rate not in {8_000, 16_000, 32_000, 48_000}:
            raise ValueError("WebRTC VAD supports 8000, 16000, 32000, or 48000 Hz")
        if frame_ms not in {10, 20, 30}:
            raise ValueError("WebRTC VAD supports 10, 20, or 30 ms frames")
        if aggressiveness not in range(4):
            raise ValueError("WebRTC VAD aggressiveness must be in [0, 3]")
        try:
            import webrtcvad

            self._vad = webrtcvad.Vad(aggressiveness)
        except Exception as exc:  # noqa: BLE001 - optional native dependency is unavailable/unusable
            raise SpeechDetectorUnavailable(f"webrtcvad-wheels is not installed or unusable (voice extra): {exc}") from exc
        self._sample_rate = sample_rate

    def is_speech(self, frame: bytes) -> bool:
        return bool(self._vad.is_speech(frame, self._sample_rate))


class ProcessSessionLimiter:
    """Small process-local count used to bound simultaneous voice WebSockets."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def try_acquire(self, maximum: int) -> bool:
        maximum = int(maximum)
        if maximum < 1:
            return False
        with self._lock:
            if self._active >= maximum:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._active > 0:
                self._active -= 1

    def clear_for_test(self) -> None:
        with self._lock:
            self._active = 0
