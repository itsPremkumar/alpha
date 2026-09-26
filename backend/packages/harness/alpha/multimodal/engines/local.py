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
from typing import Any

from alpha.multimodal.capabilities import Capability, CapabilityResult

logger = logging.getLogger(__name__)

OCR_ENGINES = (
    ("rapidocr", "rapidocr_onnxruntime"),
    ("tesseract", "pytesseract"),
)


def piper_tts(
    text: str,
    voice: str,
    *,
    model_path: str | None = None,
    length_scale: float = 1.0,
    noise_scale: float = 0.667,
    volume: float = 0.9,
) -> bytes:
    """Local offline TTS through the process-cached Piper voice.

    The *voice* argument is a validated safe id. Filesystem selection comes only
    from trusted config/env and is never taken from a client request.
    """
    from alpha.multimodal.chain import SKIP_NOT_CONFIGURED, SKIP_NOT_INSTALLED, TIER_T3, TierExhausted, TierSkip, failure_row, skip_row
    from alpha.multimodal.local_models import PiperModelSpec, piper_model_assets_present, resolve_piper_model_path, synthesize_with_cached_piper

    try:
        path = resolve_piper_model_path(voice, model_path, os.getenv("ALPHA_PIPER_VOICE"))
    except ValueError as exc:
        raise TierSkip(SKIP_NOT_CONFIGURED, str(exc), rows=[skip_row(TIER_T3, "piper", SKIP_NOT_CONFIGURED, str(exc))]) from exc
    spec = PiperModelSpec(
        model_path=path,
        length_scale=length_scale,
        noise_scale=noise_scale,
        volume=volume,
    )
    try:
        import piper  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - broken optional native import is unavailable
        detail = f"piper-tts is not installed (voice extra): {exc}"
        raise TierSkip(SKIP_NOT_INSTALLED, detail, rows=[skip_row(TIER_T3, "piper", SKIP_NOT_INSTALLED, detail)]) from exc
    if not piper_model_assets_present(spec):
        detail = "piper voice model assets are not present (run voice setup or configure voice.tts.model_path; path withheld)"
        raise TierSkip(SKIP_NOT_CONFIGURED, detail, rows=[skip_row(TIER_T3, "piper", SKIP_NOT_CONFIGURED, detail)])
    try:
        audio = synthesize_with_cached_piper(text, spec)
    except ImportError as exc:
        detail = f"piper-tts is not installed (voice extra): {exc}"
        raise TierSkip(SKIP_NOT_INSTALLED, detail, rows=[skip_row(TIER_T3, "piper", SKIP_NOT_INSTALLED, detail)]) from exc
    except Exception as exc:
        logger.warning("Local Piper synthesis failed", exc_info=True)
        safe_error = RuntimeError("piper synthesis failed")
        raise TierExhausted([failure_row(TIER_T3, "piper", safe_error)]) from exc
    if not audio:
        raise TierExhausted([failure_row(TIER_T3, "piper", RuntimeError("piper returned 0 audio bytes"))])
    return audio


def stt_local(
    audio: bytes,
    suffix: str,
    model_size: str,
    language: str | None,
    *,
    model_path: str | None = None,
    device: str = "auto",
    compute_type: str = "int8",
    beam_size: int = 1,
    local_files_only: bool = True,
) -> dict[str, Any]:
    """Local speech-to-text through the cached ``alpha.media.stt`` worker."""
    from alpha.media import stt as media_stt
    from alpha.multimodal.chain import SKIP_NOT_CONFIGURED, SKIP_NOT_INSTALLED, TIER_T3, TierExhausted, TierSkip, failure_row, skip_row

    if not media_stt.stt_available():
        detail = "faster-whisper is not installed (voice extra); no local STT engine"
        raise TierSkip(SKIP_NOT_INSTALLED, detail, rows=[skip_row(TIER_T3, "faster-whisper", SKIP_NOT_INSTALLED, detail)])
    if not media_stt.stt_model_available(
        model_size=model_size,
        model_path=model_path,
        device=device,
        compute_type=compute_type,
        local_files_only=local_files_only,
    ):
        detail = "faster-whisper model assets are not present (run voice setup or configure voice.stt.model_path; path withheld)"
        raise TierSkip(SKIP_NOT_CONFIGURED, detail, rows=[skip_row(TIER_T3, "faster-whisper", SKIP_NOT_CONFIGURED, detail)])

    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".wav")
    try:
        handle.write(audio)
        handle.close()
        transcription = media_stt.transcribe_file(
            handle.name,
            model_size=model_size,
            language=language,
            model_path=model_path,
            device=device,
            compute_type=compute_type,
            beam_size=beam_size,
            local_files_only=local_files_only,
        )
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
        audio = piper_tts(
            text,
            str(payload.get("voice") or "en_US-lessac-medium"),
            model_path=payload.get("model_path"),
            length_scale=float(payload.get("length_scale", 1.0)),
            noise_scale=float(payload.get("noise_scale", 0.667)),
            volume=float(payload.get("volume", 0.9)),
        )
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
            model_path=payload.get("model_path"),
            device=str(payload.get("device") or "auto"),
            compute_type=str(payload.get("compute_type") or "int8"),
            beam_size=int(payload.get("beam_size") or 1),
            local_files_only=bool(payload.get("local_files_only", True)),
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
