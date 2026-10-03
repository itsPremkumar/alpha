"""Wake-word end-to-end: real engine loading, honest arming, and real scoring.

The engine is exercised through `load_default_score_fn` when openWakeWord is
installed, and through injected scorers otherwise, so the honesty rules are
pinned either way. Run with the voice extra for the real-engine path.
"""

from __future__ import annotations

import numpy as np
import pytest

from alpha.multimodal.wakeword import (
    DEFAULT_THRESHOLD,
    ENGINE_NAME,
    InvalidFrameError,
    WakeWordSession,
    load_default_score_fn,
)


def _silence(frames: int = 50) -> bytes:
    return b"\x00\x00" * (1600 * frames)


def _tone(frames: int = 50, freq: float = 220.0, rate: int = 16000) -> bytes:
    t = np.arange(frames * 1600) / rate
    wave = (np.sin(2 * np.pi * freq * t) * 12000).astype(np.int16)
    return wave.tobytes()


class TestRealEngine:
    """The shipped engine, when installed."""

    def test_loads_and_scores_silence_below_threshold(self) -> None:
        try:
            score = load_default_score_fn()
        except ImportError as exc:
            pytest.skip(f"openWakeWord is not installed: {exc}")
        assert score(_silence()) < 0.5, "silence must not reach the wake threshold"

    def test_speech_shaped_audio_is_accepted_by_the_scorer(self) -> None:
        try:
            score = load_default_score_fn()
        except ImportError as exc:
            pytest.skip(f"openWakeWord is not installed: {exc}")
        # Not a wake word, so this must stay low — the point is that the scorer
        # returns a real number rather than raising on real audio.
        assert isinstance(score(_tone()), float)

    def test_session_arms_against_the_real_engine(self) -> None:
        session = WakeWordSession()
        status = session.arm()
        if status["status"] != "ready":
            pytest.skip(f"openWakeWord unavailable: {status['detail']}")
        assert status["engine"] == ENGINE_NAME
        assert status["armed"] is False, "armed must be false before frames flow"


class TestHonestArming:
    def test_missing_engine_reports_not_installed_never_fake_armed(self) -> None:
        session = WakeWordSession()
        session._score_fn = None  # force the load path
        status = session.arm()
        if status["status"] == "ready":
            pytest.skip("openWakeWord is installed; the not_installed path cannot be observed here")
        assert status["status"] == "not_installed"
        assert status["armed"] is False
        assert session.armed is False
        assert status["detail"], "a refusal must carry its reason"

    def test_armed_requires_both_engine_and_frames(self) -> None:
        session = WakeWordSession(score_fn=lambda b: 0.0)
        assert session.engine_ready is True
        assert session.armed is False, "a ready engine with no frames is not armed"
        session.push_frame(_silence(1))
        assert session.armed is True

    def test_no_scorer_and_no_arm_is_not_ready(self) -> None:
        assert WakeWordSession().engine_ready is False

    def test_threshold_must_be_a_probability(self) -> None:
        for bad in (-0.1, 1.5, float("nan")):
            with pytest.raises(ValueError, match="threshold"):
                WakeWordSession(threshold=bad)


class TestScoringHonesty:
    def test_every_frame_discloses_score_and_threshold(self) -> None:
        session = WakeWordSession(score_fn=lambda b: 0.42, threshold=DEFAULT_THRESHOLD)
        event = session.push_frame(_silence(1))
        assert event["type"] == "score"
        assert event["score"] == pytest.approx(0.42)
        assert event["threshold"] == pytest.approx(DEFAULT_THRESHOLD)
        assert event["engine"] == ENGINE_NAME
        assert event["frames"] == 1

    def test_below_threshold_is_a_score_not_a_wake(self) -> None:
        session = WakeWordSession(score_fn=lambda b: 0.49, threshold=0.5)
        assert session.push_frame(_silence(1))["type"] == "score"
        assert session.wakes == 0

    def test_wake_fires_once_and_latches_until_the_score_drops(self) -> None:
        scores = iter([0.9, 0.95, 0.2, 0.1, 0.85, 0.88])
        session = WakeWordSession(score_fn=lambda b: next(scores), threshold=0.5)
        kinds = [session.push_frame(_silence(1))["type"] for _ in range(6)]
        assert kinds == ["wake", "score", "score", "score", "wake", "score"]
        assert session.wakes == 2, "a latched wake must not re-fire while the score stays high"

    def test_wake_window_is_bounded(self) -> None:
        session = WakeWordSession(score_fn=lambda b: 0.0, window_frames=4)
        for _ in range(20):
            session.push_frame(_silence(1))
        assert session.buffered_frames == 4, "the pre-roll window must stay bounded"

    def test_corrupt_frames_raise_and_the_session_survives(self) -> None:
        session = WakeWordSession(score_fn=lambda b: 0.0)
        session.push_frame(_silence(1))
        for payload in (b"", b"\x00\x00\x00"):
            with pytest.raises(InvalidFrameError):
                session.push_frame(payload)
        with pytest.raises(InvalidFrameError):
            session.push_frame("not bytes")  # type: ignore[arg-type]
        assert session.push_frame(_silence(1))["type"] == "score", "the session must survive corrupt frames"

    def test_scoring_without_a_ready_engine_is_a_runtime_error(self) -> None:
        session = WakeWordSession()
        with pytest.raises(RuntimeError, match="arm"):
            session.push_frame(_silence(1))

    def test_stt_handoff_runs_exactly_once_per_wake(self) -> None:
        calls: list[bytes] = []
        session = WakeWordSession(score_fn=lambda b: 0.95, transcribe_fn=lambda pcm: calls.append(pcm) or "hello")
        session.push_frame(_silence(10))
        session.push_frame(_silence(10))
        session.push_frame(_silence(10))
        assert len(calls) == 1, "one transcription per wake, not per frame"

    def test_stt_failure_is_disclosed_not_swallowed(self) -> None:
        def boom(_pcm: bytes) -> str:
            raise RuntimeError("stt exploded")

        session = WakeWordSession(score_fn=lambda b: 0.95, transcribe_fn=boom)
        event = session.push_frame(_silence(10))
        assert event["type"] == "wake"
        assert "transcript_error" in event, "a failed handoff must be disclosed"
        assert "stt exploded" in event["transcript_error"]
        assert session.push_frame(_silence(1))["type"] == "score", "the session survives a failed handoff"
