"""Server-side wake-word session: honest arming, scored frames, one STT handoff per wake.

Honesty rules encoded here (tested in ``test_multimodal_wakeword.py``):

* ``arm()`` reports an *observed* engine state; ``.armed`` only becomes True
  once the engine is ready AND frames have actually flowed — wake-armed is
  never claimed for a silent session.
* Every scored frame discloses its score and threshold; a wake fires at
  ``score >= threshold`` and latches until the score drops below the threshold
  again, so ``transcribe_fn`` runs exactly once per wake.
* A corrupt (odd-length / non-PCM16) frame raises :class:`InvalidFrameError`
  and the session survives.
* A missing openWakeWord install is an honest ``not_installed`` arm status —
  never a fake armed state.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.5
DEFAULT_WINDOW_FRAMES = 50
DEFAULT_SAMPLE_RATE = 16_000
ENGINE_NAME = "openwakeword"


class InvalidFrameError(ValueError):
    """A pushed frame failed the PCM16 sanity check; the session survives it."""


def load_default_score_fn() -> Callable[[bytes], float]:
    """Build the real openWakeWord scorer (lazy import + model load).

    Raises ``ImportError`` when the ``voice`` extra is absent — callers turn
    that into an honest ``not_installed`` status. Pretrained weights download
    once on first load (``openwakeword.utils.download_models``) and are cached
    on disk afterwards.

    ONNX inference is deliberate: the packaged weights ship in both formats,
    ``tflite-runtime`` has no Windows / Python 3.12 wheel, and onnxruntime is
    already required elsewhere in the ``voice`` extra (rapidocr-onnxruntime).
    """
    import numpy as np

    try:
        from openwakeword.model import OWWModel
    except ImportError:
        from openwakeword.model import Model as OWWModel

    # openwakeword 0.6 does not auto-download inside __init__ — fetch both
    # weight formats (no-op for files already cached) or surface the real
    # network/IO error instead of a later opaque "file doesn't exist".
    from openwakeword.utils import download_models

    download_models(model_names=["hey_jarvis"])

    model = OWWModel(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    if hasattr(model, "load_models"):
        model.load_models()  # older openWakeWord versions load lazily

    def score(pcm: bytes) -> float:
        audio = np.frombuffer(pcm, dtype=np.int16)
        predictions = model.predict(audio)
        # After warm-up openWakeWord reports np.float32 scores — which are NOT
        # Python float subclasses — so numpy scalar types must be accepted
        # explicitly or every real score would be discarded as "wrong shape".
        real = (int, float, np.floating, np.integer)
        numeric: list[float] = []
        if isinstance(predictions, dict):
            for value in predictions.values():
                if isinstance(value, real):
                    numeric.append(float(value))
                elif isinstance(value, dict):
                    nested = [float(v) for v in value.values() if isinstance(v, real)]
                    numeric.extend(nested)
        if not numeric:
            raise RuntimeError(f"unexpected openWakeWord prediction shape: {type(predictions).__name__}")
        return max(numeric)

    return score


class WakeWordSession:
    """Buffers PCM16 frames, scores them, and hands audio to STT exactly once per wake."""

    def __init__(
        self,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        score_fn: Callable[[bytes], float] | None = None,
        transcribe_fn: Callable[[bytes], str] | None = None,
        engine_name: str = ENGINE_NAME,
        window_frames: int = DEFAULT_WINDOW_FRAMES,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
    ) -> None:
        if not (0.0 <= float(threshold) <= 1.0):
            raise ValueError(f"wake threshold must be within [0, 1], got {threshold}")
        self.threshold = float(threshold)
        self._score_fn = score_fn
        self._transcribe_fn = transcribe_fn
        self.engine_name = str(engine_name)
        self.window_frames = max(1, int(window_frames))
        self.sample_rate = int(sample_rate)
        self.frames_pushed = 0
        self.wakes = 0
        self._buffer: list[bytes] = []
        self._latched = False
        # An injected scorer IS the engine (tests + wired sessions); otherwise
        # arm() must load the real engine before anything can be claimed ready.
        self.engine_ready = score_fn is not None
        self.arm_status: dict[str, Any] = {}

    def arm(self) -> dict[str, Any]:
        """Observe/load the engine and report an honest arm status dict."""
        if self._score_fn is None:
            try:
                self._score_fn = load_default_score_fn()
            except ImportError as exc:
                self.engine_ready = False
                self.arm_status = {
                    "engine": self.engine_name,
                    "status": "not_installed",
                    "armed": False,
                    "threshold": self.threshold,
                    "detail": f"openWakeWord is not installed (voice extra): {exc}",
                }
                return dict(self.arm_status)
        self.engine_ready = True
        self.arm_status = {
            "engine": self.engine_name,
            "status": "ready",
            "armed": False,
            "threshold": self.threshold,
            "detail": "engine ready; no frames have flowed yet, so .armed stays false until push_frame()",
        }
        return dict(self.arm_status)

    @property
    def armed(self) -> bool:
        """True only when the engine is ready AND frames have actually flowed."""
        return self.engine_ready and self.frames_pushed > 0

    @property
    def buffered_frames(self) -> int:
        return len(self._buffer)

    def push_frame(self, pcm: bytes) -> dict[str, Any]:
        """Score one PCM16 frame; returns a ``score`` or ``wake`` event dict."""
        if not isinstance(pcm, (bytes, bytearray)):
            raise InvalidFrameError(f"frame must be bytes, got {type(pcm).__name__}")
        if len(pcm) == 0:
            raise InvalidFrameError("empty frame is not valid PCM16 audio")
        if len(pcm) % 2 != 0:
            raise InvalidFrameError(f"odd-length frame ({len(pcm)} bytes) is not PCM16 (2 bytes/sample)")
        if not self.engine_ready or self._score_fn is None:
            raise RuntimeError("wake session has no ready engine; call arm() (or inject score_fn) first")

        self.frames_pushed += 1
        self._buffer.append(bytes(pcm))
        if len(self._buffer) > self.window_frames:
            self._buffer.pop(0)

        score = float(self._score_fn(bytes(pcm)))
        event: dict[str, Any] = {
            "type": "score",
            "score": score,
            "threshold": self.threshold,
            "engine": self.engine_name,
            "frames": self.frames_pushed,
        }
        if score >= self.threshold and not self._latched:
            self._latched = True
            self.wakes += 1
            event["type"] = "wake"
            if self._transcribe_fn is not None:
                try:
                    event["transcript"] = str(self._transcribe_fn(b"".join(self._buffer)))
                except Exception as exc:  # noqa: BLE001 - STT failure must not kill the session
                    logger.warning("wake->STT handoff failed: %s", type(exc).__name__)
                    event["transcript_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        elif score < self.threshold:
            self._latched = False
        return event
