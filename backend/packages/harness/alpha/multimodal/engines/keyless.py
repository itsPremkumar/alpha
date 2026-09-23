"""T2 engines: free keyless network providers (edge-tts, AI Horde anonymous).

No personal API key exists here — the only credential is AI Horde's
*documented* anonymous constant reused from the free router. Every engine
failure becomes a real attempt row; capabilities with no honest keyless
provider report ``skipped_no_provider`` instead of pretending.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from alpha.multimodal.capabilities import Capability, CapabilityResult

logger = logging.getLogger(__name__)

DEFAULT_EDGE_VOICE = "en-US-AriaNeural"

HORDE_BASE_URL = "https://aihorde.net/api/v2"
HORDE_SUBMIT_TIMEOUT = 30.0
HORDE_POLL_TIMEOUT = 30.0
HORDE_QUEUE_BUDGET_SECONDS = 120.0
HORDE_POLL_INTERVAL_SECONDS = 3.0
HORDE_CLIENT_AGENT = "AlphaMultimodalWave/1.0 (anonymous image generation; contact: local operator)"
HORDE_DEFAULT_SIZE = (512, 512)


def edge_tts_synthesize(text: str, voice: str | None = None) -> bytes:
    """Synthesize *text* to MP3 bytes through edge-tts (async API, sync caller).

    ``asyncio.run`` is safe here because the router always calls the chain via
    ``asyncio.to_thread`` (no running loop on this thread).
    """
    import edge_tts

    async def _synthesize() -> bytes:
        communicate = edge_tts.Communicate(text, voice or DEFAULT_EDGE_VOICE)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    return asyncio.run(_synthesize())


def _parse_size(size: str | None) -> tuple[int, int]:
    if not size:
        return HORDE_DEFAULT_SIZE
    try:
        width_str, height_str = str(size).lower().split("x", 1)
        width, height = int(width_str), int(height_str)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"size must look like '512x512', got {size!r}") from exc
    if not (64 <= width <= 1024 and 64 <= height <= 1024):
        raise ValueError(f"size {width}x{height} outside AI Horde's 64..1024 bounds")
    return width, height


def aihorde_image(prompt: str, size: str | None = None, *, budget_seconds: float = HORDE_QUEUE_BUDGET_SECONDS) -> dict[str, Any]:
    """One anonymous AI Horde generation: submit, poll within budget, return bytes/URL.

    Reuses the free router's single HTTP seam (``providers.request``) so tests
    stub one function; raises honestly on HTTP errors, faults, and queue
    timeouts (``TimeoutError`` carries the observed budget).
    """
    from alpha.models.free_router.providers import AI_HORDE_ANONYMOUS_KEY, ProviderError, request

    width, height = _parse_size(size)
    body = {
        "prompt": prompt,
        "params": {
            "width": width,
            "height": height,
            "steps": 30,
            "sampler_name": "k_euler",
            "n": 1,
        },
        "nsfw": False,
        "r2": False,
        "models": ["Deliberate"],
    }
    headers = {"apikey": AI_HORDE_ANONYMOUS_KEY, "Client-Agent": HORDE_CLIENT_AGENT}
    response = request(
        "POST",
        f"{HORDE_BASE_URL}/generate/async",
        headers=headers,
        json_body=body,
        timeout=HORDE_SUBMIT_TIMEOUT,
    )
    if response.status_code >= 400:
        snippet = (response.text or "").strip().replace("\n", " ")[:300]
        raise ProviderError("aihorde-anonymous", f"HTTP {response.status_code}: {snippet}", response.status_code)
    payload = response.json()
    job_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(job_id, str) or not job_id:
        raise ProviderError("aihorde-anonymous", "submit response carried no job id")

    deadline = time.monotonic() + budget_seconds
    while time.monotonic() < deadline:
        status = request(
            "GET",
            f"{HORDE_BASE_URL}/generate/status/{job_id}",
            headers=headers,
            timeout=HORDE_POLL_TIMEOUT,
        )
        if status.status_code >= 400:
            snippet = (status.text or "").strip().replace("\n", " ")[:300]
            raise ProviderError("aihorde-anonymous", f"HTTP {status.status_code}: {snippet}", status.status_code)
        status_payload = status.json()
        if isinstance(status_payload, dict) and status_payload.get("faulted"):
            raise ProviderError("aihorde-anonymous", "generation faulted in the AI Horde queue")
        if isinstance(status_payload, dict) and status_payload.get("done"):
            generations = status_payload.get("generations")
            if not isinstance(generations, list) or not generations:
                raise ProviderError("aihorde-anonymous", "generation finished with no images")
            first = generations[0] if isinstance(generations[0], dict) else {}
            if isinstance(first.get("b64"), str) and first["b64"]:
                return {"b64": first["b64"]}
            if isinstance(first.get("url"), str) and first["url"]:
                return {"url": first["url"]}
            raise ProviderError("aihorde-anonymous", "generation entry carries neither b64 nor url")
        time.sleep(HORDE_POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"AI Horde anonymous queue did not finish within {budget_seconds:.0f}s")


def run_t2(capability: Capability, payload: dict[str, Any], attempts: list[dict[str, Any]]) -> CapabilityResult:
    """Serve *capability* from keyless providers (T2), or skip honestly."""
    from alpha.multimodal.chain import (
        SKIP_NOT_INSTALLED,
        SKIP_SKIPPED_NO_PROVIDER,
        TIER_T2,
        TierExhausted,
        TierSkip,
        failure_row,
        skip_row,
    )

    cap = Capability(str(capability))

    if cap is Capability.TTS:
        text = str(payload.get("text") or "")
        voice = payload.get("voice")
        try:
            audio = edge_tts_synthesize(text, str(voice) if voice else None)
        except ImportError as exc:
            detail = f"edge-tts is not installed (voice extra): {exc}"
            raise TierSkip(SKIP_NOT_INSTALLED, detail, rows=[skip_row(TIER_T2, "edge-tts", SKIP_NOT_INSTALLED, detail)]) from exc
        except Exception as exc:  # noqa: BLE001 - real engine failure -> attempt row
            raise TierExhausted([failure_row(TIER_T2, "edge-tts", exc)]) from exc
        if not audio:
            row = failure_row(TIER_T2, "edge-tts", RuntimeError("edge-tts returned 0 audio bytes"))
            raise TierExhausted([row])
        return CapabilityResult(
            ok=True,
            capability=str(cap),
            engine="edge-tts",
            data={"audio": audio, "media_type": "audio/mpeg"},
            note="synthesized by edge-tts (Microsoft Edge online neural voices, keyless; T2)",
        )

    if cap is Capability.IMAGE_GEN:
        prompt = str(payload.get("prompt") or "")
        try:
            image = aihorde_image(prompt, payload.get("size"))
        except Exception as exc:  # noqa: BLE001 - HTTP/queue failures become honest attempt rows
            raise TierExhausted([failure_row(TIER_T2, "aihorde-anonymous", exc)]) from exc
        return CapabilityResult(
            ok=True,
            capability=str(cap),
            engine="aihorde-anonymous",
            data=image,
            note="generated by AI Horde anonymous queue (keyless; T2) — queue wait included in latency",
        )

    detail = {
        Capability.STT: "AI Horde v2 exposes no speech-transcription endpoint; no keyless STT provider ships with Alpha",
        Capability.OCR: "no keyless OCR provider ships with Alpha",
        Capability.VISION: "no keyless image-understanding provider ships with Alpha",
        Capability.WAKE_WORD: "wake word is scored locally from streamed frames; no keyless network provider applies",
    }.get(cap, f"no keyless provider ships with Alpha for '{cap}'")
    raise TierSkip(
        SKIP_SKIPPED_NO_PROVIDER,
        detail,
        rows=[skip_row(TIER_T2, "(none)", SKIP_SKIPPED_NO_PROVIDER, detail)],
    )
