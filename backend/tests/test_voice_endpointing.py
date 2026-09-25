"""Pure PCM16 endpointing tests (no WebRTC VAD package required)."""

from __future__ import annotations

import builtins

import pytest

from alpha.multimodal.endpointing import (
    EndpointingConfig,
    ProcessSessionLimiter,
    SpeechDetectorUnavailable,
    StreamingEndpointDetector,
    WebRtcSpeechDetector,
)


class ScriptedDetector:
    def __init__(self, speech_flags: list[bool]):
        self.flags = iter(speech_flags)
        self.frames: list[bytes] = []

    def is_speech(self, frame: bytes) -> bool:
        assert len(frame) == 40
        self.frames.append(frame)
        return next(self.flags)


def _frame(value: int, *, samples: int = 20) -> bytes:
    return bytes([value]) * (samples * 2)


def _events(session: StreamingEndpointDetector, data: bytes):
    return session.push(data)


def test_exact_frames_and_trailing_remainder_are_preserved_without_webrtcvad():
    detector = ScriptedDetector([False, True, False, False])
    config = EndpointingConfig(
        sample_rate=1_000,
        frame_ms=20,
        pre_roll_ms=40,
        speech_start_ms=20,
        endpoint_silence_ms=40,
        partial_interval_ms=0,
        max_utterance_seconds=5,
    )
    session = StreamingEndpointDetector(detector, config)

    assert _events(session, _frame(1)) == []
    events = _events(session, _frame(2) + b"\x03\x04")
    assert [event.kind for event in events] == ["speech_started"]
    assert session.buffered_byte_count == 2

    events = _events(session, _frame(3) + b"\x05\x06")
    assert events == []
    events = _events(session, _frame(4) + b"\x07\x08")
    assert [event.kind for event in events] == ["endpoint"]
    assert all(len(frame) == 40 for frame in detector.frames)
    assert session.buffered_byte_count == 6


def test_odd_pcm_is_rejected_without_mutating_retained_remainder():
    detector = ScriptedDetector([False, True])
    config = EndpointingConfig(
        sample_rate=1_000,
        frame_ms=20,
        pre_roll_ms=20,
        speech_start_ms=20,
        endpoint_silence_ms=20,
        partial_interval_ms=0,
        max_utterance_seconds=2,
    )
    session = StreamingEndpointDetector(detector, config)
    _events(session, b"\x01\x00")

    with pytest.raises(ValueError, match="multiple of 2"):
        _events(session, b"\x02")
    assert session.buffered_byte_count == 2


def test_preroll_threshold_partial_endpoint_and_reset_for_next_utterance():
    detector = ScriptedDetector(
        [
            False,
            False,
            True,
            True,
            True,
            True,
            True,
            True,
            False,
            False,
            True,
            True,
            False,
            False,
        ]
    )
    config = EndpointingConfig(
        sample_rate=1_000,
        frame_ms=20,
        pre_roll_ms=40,
        speech_start_ms=40,
        endpoint_silence_ms=40,
        partial_interval_ms=80,
        max_utterance_seconds=2,
    )
    session = StreamingEndpointDetector(detector, config)

    assert _events(session, _frame(0) * 2) == []
    events = _events(session, _frame(1) * 6)
    assert [event.kind for event in events] == ["speech_started", "partial"]
    partial = events[1]
    assert partial.utterance_id == 1
    assert partial.audio == _frame(0) * 2 + _frame(1) * 6

    events = _events(session, _frame(0) + _frame(0))
    endpoints = [event for event in events if event.kind == "endpoint"]
    assert len(endpoints) == 1
    assert endpoints[0].utterance_id == 1
    assert endpoints[0].reason == "silence"
    assert endpoints[0].duration_ms == 200
    assert session.speaking is False

    events = _events(session, _frame(1) + _frame(1) + _frame(0) + _frame(0))
    assert [event.kind for event in events] == ["speech_started", "endpoint"]
    assert events[0].utterance_id == 2


def test_max_utterance_emits_a_bounded_final_event_before_any_extra_frame():
    detector = ScriptedDetector([True, True, True, True])
    config = EndpointingConfig(
        sample_rate=1_000,
        frame_ms=20,
        pre_roll_ms=0,
        speech_start_ms=20,
        endpoint_silence_ms=1_000,
        partial_interval_ms=0,
        max_utterance_seconds=0.05,
    )
    session = StreamingEndpointDetector(detector, config)

    events = _events(session, _frame(1) * 3)
    endpoint = events[-1]
    assert endpoint.kind == "endpoint"
    assert endpoint.reason == "max_duration"
    assert endpoint.duration_ms == 50
    assert len(endpoint.audio) == 100
    assert session.speaking is False


def test_production_detector_reports_missing_optional_dependency(monkeypatch):
    real_import = builtins.__import__

    def missing_webrtcvad(name, *args, **kwargs):
        if name == "webrtcvad":
            raise ImportError("No module named 'webrtcvad'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_webrtcvad)
    with pytest.raises(SpeechDetectorUnavailable, match="voice extra"):
        WebRtcSpeechDetector(sample_rate=16_000, frame_ms=20)


def test_process_session_limiter_is_exact_and_release_safe():
    limiter = ProcessSessionLimiter()
    assert limiter.try_acquire(1)
    assert not limiter.try_acquire(1)
    assert limiter.active == 1
    limiter.release()
    limiter.release()  # idempotent cleanup
    assert limiter.active == 0
    assert limiter.try_acquire(2)
    limiter.clear_for_test()
    assert limiter.active == 0
