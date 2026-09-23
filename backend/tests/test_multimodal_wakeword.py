"""Wake-word session tests: honest arming, score disclosure, latching, WAV sanity (plan §8.4).

Nothing here fabricates an engine: ``load_default_score_fn`` is stubbed at its
single module seam (or the session gets an injected scorer — the documented
test seam), and every assertion checks that observed state matches what the
session actually did (frames flowed or not, wakes latched or not).
"""

import io
import wave

import pytest

from alpha.config.voice_config import VoiceConfig
from alpha.multimodal import wakeword
from alpha.multimodal.wakeword import InvalidFrameError, WakeWordSession
from alpha.multimodal.wav import WavError, bounded_pcm16, pcm16_to_wav, wav_to_pcm16


@pytest.fixture(autouse=True)
def _isolate_workspace(tmp_path, monkeypatch):
    """Plan §8: tests isolate AGENT_WORKSPACE_HOME to a temp dir."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def _frame(fill: int = 0) -> bytes:
    """Distinct even-length PCM16 frame (200 bytes = 100 samples)."""
    return bytes([fill & 0xFF]) * 200


def _recorder(scripts: list[str]):
    """Transcribe fn returning scripted results, one per wake."""
    calls: list[bytes] = []

    def transcribe(pcm: bytes) -> str:
        calls.append(pcm)
        return scripts.pop(0) if scripts else "extra"

    return transcribe, calls


def _scripted(values: list[float]):
    it = iter(values)

    def score(_pcm: bytes) -> float:
        return next(it, 0.1)

    return score


# ---------------------------------------------------------------------------
# Construction / threshold honesty
# ---------------------------------------------------------------------------


def test_threshold_outside_unit_interval_is_rejected():
    with pytest.raises(ValueError, match=r"wake threshold must be within \[0, 1\]"):
        WakeWordSession(threshold=1.5)
    with pytest.raises(ValueError, match=r"wake threshold must be within \[0, 1\]"):
        WakeWordSession(threshold=-0.1)
    # Boundaries are valid.
    assert WakeWordSession(threshold=0.0).threshold == 0.0
    assert WakeWordSession(threshold=1.0).threshold == 1.0


def test_default_threshold_matches_voice_config_default():
    assert WakeWordSession().threshold == VoiceConfig().wake_word.threshold == 0.5
    assert WakeWordSession().engine_name == wakeword.ENGINE_NAME == "openwakeword"
    assert WakeWordSession().window_frames == wakeword.DEFAULT_WINDOW_FRAMES == 50
    assert WakeWordSession().sample_rate == wakeword.DEFAULT_SAMPLE_RATE == 16_000


def test_window_frames_zero_is_coerced_to_one():
    assert WakeWordSession(window_frames=0).window_frames == 1
    assert WakeWordSession(window_frames=-3).window_frames == 1


# ---------------------------------------------------------------------------
# Honest arming
# ---------------------------------------------------------------------------


def test_arm_missing_engine_reports_not_installed_and_never_armed(monkeypatch):
    def missing():
        raise ImportError("No module named 'openwakeword'")

    monkeypatch.setattr(wakeword, "load_default_score_fn", missing)
    session = WakeWordSession()

    status = session.arm()

    assert status == {
        "engine": "openwakeword",
        "status": "not_installed",
        "armed": False,
        "threshold": 0.5,
        "detail": "openWakeWord is not installed (voice extra): No module named 'openwakeword'",
    }
    assert session.engine_ready is False
    assert session.armed is False
    # Nothing to score with — push_frame says so instead of pretending.
    with pytest.raises(RuntimeError, match="call arm\\(\\)"):
        session.push_frame(_frame())


def test_arm_injected_scorer_ready_but_armed_false_until_frames_flow():
    session = WakeWordSession(score_fn=lambda _pcm: 0.1)

    status = session.arm()

    assert status["status"] == "ready"
    assert status["armed"] is False
    assert "armed stays false until push_frame()" in status["detail"]
    assert session.armed is False  # engine ready, zero frames — not wake-armed
    assert session.frames_pushed == 0

    session.push_frame(_frame())

    assert session.armed is True  # now frames have actually flowed
    assert session.frames_pushed == 1


def test_arm_without_scorer_and_without_engine_reports_not_installed(monkeypatch):
    monkeypatch.setattr(wakeword, "load_default_score_fn", lambda: (_ for _ in ()).throw(ImportError("voice extra absent")))
    session = WakeWordSession()
    assert session.arm()["status"] == "not_installed"
    assert session.arm_status["status"] == "not_installed"  # sticky observation
    assert session.armed is False


# ---------------------------------------------------------------------------
# Frame validation
# ---------------------------------------------------------------------------


def test_invalid_frames_rejected_session_survives(monkeypatch):
    del monkeypatch
    session = WakeWordSession(score_fn=lambda _pcm: 0.1)
    session.arm()

    with pytest.raises(InvalidFrameError, match="frame must be bytes, got str"):
        session.push_frame("not-bytes")
    with pytest.raises(InvalidFrameError, match="empty frame is not valid PCM16 audio"):
        session.push_frame(b"")
    with pytest.raises(InvalidFrameError, match=r"odd-length frame \(3 bytes\) is not PCM16"):
        session.push_frame(b"\x01\x02\x03")

    assert session.frames_pushed == 0  # rejected frames never counted

    event = session.push_frame(_frame())
    assert event["type"] == "score"  # session still works after the rejects
    assert session.frames_pushed == 1


def test_every_event_discloses_score_threshold_engine_and_frames():
    session = WakeWordSession(score_fn=_scripted([0.42]), threshold=0.75)
    session.arm()

    event = session.push_frame(_frame())

    assert event == {
        "type": "score",
        "score": 0.42,
        "threshold": 0.75,
        "engine": "openwakeword",
        "frames": 1,
    }
    # Non-default engine name is disclosed verbatim.
    custom = WakeWordSession(score_fn=_scripted([0.9]), engine_name="custom-engine")
    custom.arm()
    assert custom.push_frame(_frame())["engine"] == "custom-engine"


# ---------------------------------------------------------------------------
# Wake latching — exactly one STT handoff per wake
# ---------------------------------------------------------------------------


def test_wake_latches_until_score_drops_then_can_wake_again():
    transcribe, calls = _recorder(["first", "second"])
    session = WakeWordSession(
        score_fn=_scripted([0.1, 0.9, 0.95, 0.2, 0.9]),
        transcribe_fn=transcribe,
        window_frames=3,
    )
    session.arm()

    events = [session.push_frame(_frame(i)) for i in range(5)]

    assert [e["type"] for e in events] == ["score", "wake", "score", "score", "wake"]
    assert session.wakes == 2
    assert session.frames_pushed == 5
    # Scores always disclosed, waking or not.
    assert [e["score"] for e in events] == [0.1, 0.9, 0.95, 0.2, 0.9]
    assert all(e["threshold"] == 0.5 and e["engine"] == "openwakeword" for e in events)

    # One handoff per wake; buffer is the windowed frames at handoff time.
    assert len(calls) == 2
    assert calls[0] == _frame(0) + _frame(1)  # frames 1-2 buffered before wake
    assert calls[1] == _frame(2) + _frame(3) + _frame(4)  # window_frames=3 at second wake
    assert calls[0] != calls[1]


def test_wake_transcript_appears_on_event():
    session = WakeWordSession(score_fn=_scripted([0.9]), transcribe_fn=lambda _pcm: "hello there")
    session.arm()

    event = session.push_frame(_frame())

    assert event["type"] == "wake"
    assert event["transcript"] == "hello there"
    assert "transcript_error" not in event


def test_transcribe_failure_is_disclosed_and_session_recovers():
    attempts = {"n": 0}

    def flaky(pcm: bytes) -> str:
        del pcm
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("engine down")
        return "recovered"

    session = WakeWordSession(score_fn=_scripted([0.9, 0.2, 0.9]), transcribe_fn=flaky)
    session.arm()

    first = session.push_frame(_frame())
    second = session.push_frame(_frame())
    third = session.push_frame(_frame())

    assert first["type"] == "wake"
    assert first["transcript_error"] == "RuntimeError: engine down"
    assert "transcript" not in first  # failure never dressed up as a transcript
    assert second["type"] == "score"  # score below threshold re-arms the latch
    assert third["type"] == "wake"
    assert third["transcript"] == "recovered"
    assert session.wakes == 2  # STT failure did not kill the session


def test_wake_without_transcribe_fn_carries_no_transcript_keys():
    session = WakeWordSession(score_fn=_scripted([0.9]))
    session.arm()

    event = session.push_frame(_frame())

    assert event["type"] == "wake"
    assert "transcript" not in event
    assert "transcript_error" not in event


def test_buffer_is_window_bounded_across_long_stream():
    session = WakeWordSession(score_fn=lambda _pcm: 0.1, window_frames=4, transcribe_fn=None)
    session.arm()

    for _ in range(20):
        session.push_frame(_frame())

    assert session.frames_pushed == 20
    assert session.buffered_frames == 4  # window never grows past its bound
    assert session.wakes == 0


# ---------------------------------------------------------------------------
# WAV helpers
# ---------------------------------------------------------------------------


def test_pcm16_wav_roundtrip():
    pcm = bytes(range(256)) * 4  # 1024 bytes, even
    wav_bytes = pcm16_to_wav(pcm)

    assert wav_bytes.startswith(b"RIFF")
    decoded, rate = wav_to_pcm16(wav_bytes)
    assert decoded == pcm
    assert rate == 16_000


def test_pcm16_odd_length_is_rejected_not_corrected():
    with pytest.raises(WavError, match="must be a multiple of 2, got 3"):
        pcm16_to_wav(b"\x01\x02\x03")


def test_wav_decode_rejects_junk_empty_and_non_pcm16():
    with pytest.raises(WavError, match="empty WAV payload"):
        wav_to_pcm16(b"")
    with pytest.raises(WavError, match="not a readable WAV file"):
        wav_to_pcm16(b"definitely not a wav file")

    # 8-bit WAV: real container, wrong encoding — rejected, not guessed around.
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(1)
        wav_file.setframerate(8000)
        wav_file.writeframes(b"\x80" * 16)
    with pytest.raises(WavError, match="expected 16-bit PCM WAV, got sample width 1"):
        wav_to_pcm16(buffer.getvalue())


def test_bounded_pcm16_accepts_120s_and_rejects_over_bound():
    ok = b"\x00" * (120 * 16_000 * 2)  # exactly 120s at 16 kHz PCM16
    assert bounded_pcm16(ok) is ok

    too_long = b"\x00" * (121 * 16_000 * 2)
    with pytest.raises(WavError, match=r"121\.0s, over the 120s bound"):
        bounded_pcm16(too_long)

    with pytest.raises(WavError, match="invalid sample rate"):
        bounded_pcm16(b"\x00\x00", sample_rate=0)
