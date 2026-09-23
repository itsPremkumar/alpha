"""T3 engines: free self-hosted local engines (piper, whisper, rapidocr, openWakeWord).

Every heavy library imports inside its engine function; a missing engine is an
honest ``not_installed`` skip row (never a fabricated fallback), and a local
engine that ran but failed becomes a real attempt row. STT delegates to the
existing ``alpha.media.stt`` worker verbatim (reuse, no reimplementation).
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from alpha.multimodal.capabilities import Capability, CapabilityResult

logger = logging.getLogger(__name__)

OCR_ENGINES = (
    ("rapidocr", "rapidocr_onnxruntime"),
    ("tesseract", "pytesseract"),
)


def piper_tts(text: str, voice: str | None = None) -> bytes:
    """Local offline TTS via Piper -> WAV bytes.

    Raises :class:`alpha.multimodal.chain.TierSkip` when piper or a voice model
    is absent (honest ``not_installed`` / ``not_configured``), so the chain can
    record and finish without pretending audio exists.
    """
    from alpha.multimodal.chain import SKIP_NOT_CONFIGURED, SKIP_NOT_INSTALLED, TIER_T3, TierSkip, skip_row

    voice_path = voice or os.getenv("ALPHA_PIPER_VOICE")
    if not voice_path:
        detail = "piper voice model not configured (set voice.tts.voice or ALPHA_PIPER_VOICE to a .onnx voice path)"
        raise TierSkip(SKIP_NOT_CONFIGURED, detail, rows=[skip_row(TIER_T3, "piper", SKIP_NOT_CONFIGURED, detail)])
    try:
        from piper import PiperVoice
    except ImportError as exc:
        detail = f"piper-tts is not installed (voice extra): {exc}"
        raise TierSkip(SKIP_NOT_INSTALLED, detail, rows=[skip_row(TIER_T3, "piper", SKIP_NOT_INSTALLED, detail)]) from exc

    import io
    import wave

    path = Path(str(voice_path))
    if not path.exists():
        detail = f"piper voice model not found at {path.name} (path withheld)"
        raise TierSkip(SKIP_NOT_CONFIGURED, detail, rows=[skip_row(TIER_T3, "piper", SKIP_NOT_CONFIGURED, detail)])

    voice_obj = PiperVoice.load(str(path))
    buffer = io.BytesIO()
    # piper-tts 1.3+ ships synthesize_wav; older builds expose synthesize(.., wav_file).
    synthesize_wav = getattr(voice_obj, "synthesize_wav", None)
    if callable(synthesize_wav):
        synthesize_wav(text, buffer)
    else:
        with wave.open(buffer, "wb") as wav_file:
            voice_obj.synthesize(text, wav_file)
    audio = buffer.getvalue()
    if not audio:
        raise RuntimeError("piper returned 0 audio bytes")
    return audio


def stt_local(audio: bytes, suffix: str, model_size: str, language: str | None) -> dict[str, Any]:
    """Local speech-to-text through the existing ``alpha.media.stt`` worker."""
    from alpha.media import stt as media_stt
    from alpha.multimodal.chain import SKIP_NOT_INSTALLED, TIER_T3, TierExhausted, TierSkip, failure_row, skip_row

    if not media_stt.stt_available():
        detail = "faster-whisper is not installed (voice extra); no local STT engine"
        raise TierSkip(SKIP_NOT_INSTALLED, detail, rows=[skip_row(TIER_T3, "faster-whisper", SKIP_NOT_INSTALLED, detail)])

    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav")
    try:
        handle.write(audio)
        handle.close()
        transcription = media_stt.transcribe_file(handle.name, model_size=model_size, language=language)
    finally:
        try:
            os.unlink(handle.name)
        except OSError:  # pragma: no cover - best-effort cleanup
            pass
    if not transcription.ok:
        raise TierExhausted([failure_row(TIER_T3, "faster-whisper", RuntimeError(transcription.reason))])
    return {
        "text": transcription.text,
        "language": transcription.language,
        "engine": transcription.engine,
        "note": f"transcribed locally by {transcription.engine} (T3)",
    }


def ocr_local(image: bytes) -> dict[str, Any]:
    """Local OCR: RapidOCR first, then a system Tesseract if present."""
    import numpy as np

    from alpha.multimodal.chain import (
        SKIP_NOT_INSTALLED,
        TIER_T3,
        TierExhausted,
        TierSkip,
        failure_row,
        skip_row,
    )

    def _rapidocr(data: bytes) -> str:
        from rapidocr_onnxruntime import RapidOCR

        # LoadImage decodes raw ``bytes`` through PIL; handing it a 1-D
        # uint8 array raises LoadImageError (ndim 1 not in [2, 3]).
        engine = RapidOCR()
        result, _elapse = engine(data)
        if not result:
            return ""
        return "\n".join(str(entry[1]) for entry in result if len(entry) >= 2)

    def _pytesseract(data: bytes) -> str:
        import cv2
        import pytesseract

        image_array = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image_array is None:
            raise ValueError("image could not be decoded by OpenCV")
        return pytesseract.image_to_string(image_array)

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    ran_any = False
    for engine_name, module in OCR_ENGINES:
        try:
            text = _rapidocr(image) if module == "rapidocr_onnxruntime" else _pytesseract(image)
        except ImportError as exc:
            rows.append(skip_row(TIER_T3, engine_name, SKIP_NOT_INSTALLED, f"{module} is not installed: {exc}"))
            continue
        except Exception as exc:  # noqa: BLE001 - engine ran and failed: honest attempt row
            row = failure_row(TIER_T3, engine_name, exc)
            failures.append(row)
            rows.append(row)
            continue
        ran_any = True
        if text and text.strip():
            rows.extend(failures)
            return {"text": text.strip(), "engine": engine_name, "note": f"OCR by {engine_name} (T3)"}
        rows.append(failure_row(TIER_T3, engine_name, RuntimeError("engine returned no text")))

    if not ran_any and all(row.get("retryable") is None for row in rows):
        detail = "no local OCR engine installed (rapidocr-onnxruntime / pytesseract)"
        raise TierSkip(SKIP_NOT_INSTALLED, detail, rows=rows or [skip_row(TIER_T3, "(none)", SKIP_NOT_INSTALLED, detail)])
    raise TierExhausted(rows)


def wake_local(payload: dict[str, Any]) -> dict[str, Any]:
    """Local wake-word engine observation/scoring through ``wakeword.load_default_score_fn``."""
    from alpha.multimodal.chain import SKIP_NOT_CONFIGURED, SKIP_NOT_INSTALLED, TIER_T3, TierSkip, skip_row

    try:
        from alpha.multimodal.wakeword import load_default_score_fn
    except ImportError as exc:  # pragma: no cover - package import itself must stay light
        detail = f"wakeword module unavailable: {exc}"
        raise TierSkip(SKIP_NOT_INSTALLED, detail, rows=[skip_row(TIER_T3, "openwakeword", SKIP_NOT_INSTALLED, detail)]) from exc

    pcm = payload.get("pcm16")
    if isinstance(pcm, (bytes, bytearray)) and pcm:
        try:
            score_fn = load_default_score_fn()
        except ImportError as exc:
            detail = f"openWakeWord is not installed (voice extra): {exc}"
            raise TierSkip(SKIP_NOT_INSTALLED, detail, rows=[skip_row(TIER_T3, "openwakeword", SKIP_NOT_INSTALLED, detail)]) from exc
        score = float(score_fn(bytes(pcm)))
        threshold = float(payload.get("threshold") or 0.5)
        return {
            "score": score,
            "threshold": threshold,
            "woke": score >= threshold,
            "engine": "openwakeword",
            "note": f"scored {score:.3f} against threshold {threshold:.3f} by openwakeword (T3)",
        }
    detail = "wake scoring requires 'pcm16' frames in the payload; engine presence observed only"
    raise TierSkip(SKIP_NOT_CONFIGURED, detail, rows=[skip_row(TIER_T3, "openwakeword", SKIP_NOT_CONFIGURED, detail)])


def run_t3(capability: Capability, payload: dict[str, Any], attempts: list[dict[str, Any]]) -> CapabilityResult:
    """Serve *capability* from local engines (T3), or skip/exhaust honestly."""
    from alpha.multimodal.chain import SKIP_NO_LOCAL_ENGINE, TIER_T3, TierSkip, skip_row

    cap = Capability(str(capability))

    if cap is Capability.TTS:
        text = str(payload.get("text") or "")
        # TierSkip propagates from piper_tts (not_installed / not_configured).
        audio = piper_tts(text, payload.get("voice"))
        return CapabilityResult(
            ok=True,
            capability=str(cap),
            engine="piper",
            data={"audio": audio, "media_type": "audio/wav"},
            note="synthesized offline by piper (T3)",
        )

    if cap is Capability.STT:
        data = stt_local(
            bytes(payload["audio"]),
            str(payload.get("suffix") or ".wav"),
            str(payload.get("model_size") or "small"),
            payload.get("language"),
        )
        return CapabilityResult(ok=True, capability=str(cap), engine=str(data.get("engine") or "faster-whisper"), data=data, note=str(data.get("note") or ""))

    if cap is Capability.OCR:
        data = ocr_local(bytes(payload["image"]))
        return CapabilityResult(ok=True, capability=str(cap), engine=str(data.get("engine") or "ocr"), data=data, note=str(data.get("note") or ""))

    if cap is Capability.WAKE_WORD:
        data = wake_local(payload)
        return CapabilityResult(
            ok=True,
            capability=str(cap),
            engine=str(data.get("engine") or "openwakeword"),
            data=data,
            note=str(data.get("note") or ""),
        )

    detail = {
        Capability.IMAGE_GEN: "local image generation is out of scope (not lightweight); T2 AI Horde is the fallback",
        Capability.VISION: "no local vision engine ships with Alpha; a configured vision model (T1) serves this capability",
    }.get(cap, f"no local engine ships with Alpha for '{cap}'")
    raise TierSkip(
        SKIP_NO_LOCAL_ENGINE,
        detail,
        rows=[skip_row(TIER_T3, "(none)", SKIP_NO_LOCAL_ENGINE, detail)],
    )
