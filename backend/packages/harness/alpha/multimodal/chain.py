"""The multimodal capability chain: one seam, three tiers, honest exhaustion.

``invoke(capability, payload)`` is THE single module-level seam for every
capability (tts, stt, ocr, vision, image_gen, wake_word). It walks the ordered
tier chain:

* **T1** — configured models declaring the capability (``models[].capabilities``,
  or ``supports_vision`` for vision/ocr) called over their OpenAI-compatible
  HTTP API.
* **T2** — free keyless network providers (edge-tts for tts, AI Horde
  anonymous for image_gen). Providers with nothing honest to offer report
  ``skipped_no_provider`` — never a fake success, never a fabricated engine.
* **T3** — free self-hosted local engines (piper, faster-whisper via
  ``alpha.media.stt``, rapidocr -> pytesseract, openWakeWord).

Failover semantics: BOTH retryable and deterministic engine failures advance —
first within the tier (provider 4xx -> next provider), then across tiers
(tier exhausted -> next tier) — and every hop is recorded as a
``{tier, engine, error, detail, retryable}`` row. When every tier is spent,
:class:`~alpha.multimodal.errors.MultimodalUnavailableError` carries the full
attempt list (HTTP 503 JSON, honest UI error). Success payloads carry the same
``attempts`` history so callers can see the whole walk.

Monkeypatchable module hooks ``_invoke_t1`` / ``_invoke_t2`` / ``_invoke_t3``
are THE tier seams tests stub (the real implementations import lazily inside
them, keeping this module import-light). ``_probe_engine`` is the availability-
matrix seam tests stub — probing observes imports/config only, never network.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from typing import Any

from alpha.multimodal.capabilities import Capability, CapabilityResult
from alpha.multimodal.errors import MultimodalUnavailableError

logger = logging.getLogger(__name__)

TIER_T1 = "T1"
TIER_T2 = "T2"
TIER_T3 = "T3"
TIER_ORDER: tuple[str, str, str] = (TIER_T1, TIER_T2, TIER_T3)

# Honest tier-skip statuses (internal control flow; the HTTP capability matrix
# uses the five probe statuses below).
SKIP_SKIPPED_NO_PROVIDER = "skipped_no_provider"
SKIP_NOT_INSTALLED = "not_installed"
SKIP_NOT_CONFIGURED = "not_configured"
SKIP_NO_LOCAL_ENGINE = "no_local_engine"

# The ONLY statuses the GET /api/multimodal/capabilities rows may carry.
PROBE_STATUSES = frozenset(
    {
        "available",
        "not_installed",
        "not_configured",
        "skipped_no_provider",
        "probe_failed",
    }
)

_PROBE_NOTE = "(import/config observation only; reachability not probed)"


class TierSkip(Exception):
    """A whole tier cannot serve this capability — honest, recorded, not a crash.

    ``rows`` are the capability-matrix-shaped rows recorded into ``attempts``
    (``error`` = the skip status itself, so success payloads and 503 bodies
    both show why the tier was passed over).
    """

    def __init__(self, status: str, detail: str, rows: list[dict[str, Any]] | None = None) -> None:
        self.status = str(status)
        self.detail = str(detail)
        self.rows = [dict(row) for row in (rows or [])]
        super().__init__(f"{self.status}: {self.detail}")


class TierExhausted(Exception):
    """Every engine in this tier failed; the tier's attempt rows are attached."""

    def __init__(self, attempts: list[dict[str, Any]] | None = None) -> None:
        self.attempts = [dict(row) for row in (attempts or [])]
        super().__init__(f"tier exhausted after {len(self.attempts)} attempt(s)")


def _status_code(exc: BaseException) -> int | None:
    """Best-effort HTTP status extraction across provider SDK shapes."""
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
        getattr(exc, "status", None),
    ):
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int):
            return candidate
    return None


def engine_label(exc: BaseException) -> str:
    """``ClassName(status=N)`` — a secret-free attempt label for logs/503 bodies."""
    status = _status_code(exc)
    return f"{type(exc).__name__}(status={status})" if status is not None else type(exc).__name__


def _retryable(exc: BaseException) -> bool:
    # Lazy: alpha.models.fallback pulls langchain; chain import must stay cheap.
    from alpha.models.fallback import is_retryable_llm_error

    return bool(is_retryable_llm_error(exc))


def _detail(exc: BaseException) -> str:
    """Short human-readable cause (bounded; error class + status stay authoritative)."""
    text = str(exc).strip().replace("\n", " ")
    return text[:300]


def attempt_row(
    tier: str,
    engine: str,
    error: str,
    *,
    detail: str = "",
    retryable: bool | None = None,
) -> dict[str, Any]:
    """One attempt row: label ``error`` (``ClassName(status=N)``) + honest detail."""
    return {
        "tier": str(tier),
        "engine": str(engine),
        "error": str(error),
        "detail": str(detail),
        "retryable": retryable,
    }


def failure_row(tier: str, engine: str, exc: BaseException) -> dict[str, Any]:
    """Attempt row for a real engine failure, with the observed retryability."""
    return attempt_row(
        tier,
        engine,
        engine_label(exc),
        detail=_detail(exc),
        retryable=_retryable(exc),
    )


def skip_row(tier: str, engine: str, status: str, detail: str) -> dict[str, Any]:
    """Attempt row for a tier/engine that never ran (``error`` = skip status)."""
    return attempt_row(tier, engine, status, detail=detail, retryable=None)


def skip_rows(tier: str, engines: list[str], status: str, detail: str) -> list[dict[str, Any]]:
    return [skip_row(tier, engine, status, detail) for engine in engines]


def _coerce_capability(capability: str | Capability) -> Capability:
    if isinstance(capability, Capability):
        return capability
    try:
        return Capability(str(capability).strip().lower())
    except ValueError as exc:
        known = ", ".join(c.value for c in Capability)
        raise ValueError(f"unknown capability {capability!r}; known capabilities: {known}") from exc


# ---------------------------------------------------------------------------
# Config observation (import/config only — never a reachability claim)
# ---------------------------------------------------------------------------


def _models_with_capability(capability: str | Capability) -> list[Any]:
    """Config observation: models declaring *capability* (in config order).

    Reads ``models[].capabilities`` plus the legacy ``supports_vision`` flag for
    vision/ocr (the pre-existing vision wiring). Observation only: a model
    listed here is *configured*, not proven reachable.
    """
    cap = str(_coerce_capability(capability))
    try:
        from alpha.config.app_config import get_app_config

        config = get_app_config()
    except Exception as exc:  # noqa: BLE001 - config absence must degrade, not crash the chain
        logger.warning("config observation failed for capability %s: %s", cap, type(exc).__name__)
        return []
    selected: list[Any] = []
    for model in getattr(config, "models", None) or []:
        declared = [str(entry).strip().lower() for entry in (getattr(model, "capabilities", None) or [])]
        legacy_vision = cap in (str(Capability.VISION), str(Capability.OCR)) and bool(getattr(model, "supports_vision", False))
        if cap in declared or legacy_vision:
            selected.append(model)
    return selected


def _voice_config() -> Any:
    """Observation of the ``voice:`` config block (defaults on read failure)."""
    try:
        from alpha.config.app_config import get_app_config

        return get_app_config().voice
    except Exception as exc:  # noqa: BLE001 - matrix must still answer without config
        from alpha.config.voice_config import VoiceConfig

        logger.warning("voice config observation failed: %s", type(exc).__name__)
        voice = VoiceConfig()
        voice._observed_error = f"{type(exc).__name__}: {_detail(exc)}"  # type: ignore[attr-defined]
        return voice


# ---------------------------------------------------------------------------
# Availability matrix (GET /api/multimodal/capabilities) — observation only
# ---------------------------------------------------------------------------


def _probe_engine(
    capability: str,
    tier: str,
    engine: str,
    observer: Callable[[], tuple[str, str]],
) -> dict[str, Any]:
    """One matrix row: run *observer* (import/config observation), catch everything.

    This is the module seam tests stub — probing never touches the network, and
    an observer bug becomes an honest ``probe_failed`` row instead of a 500.
    """
    try:
        status, detail = observer()
    except Exception as exc:  # noqa: BLE001 - a probe must never crash the matrix
        status, detail = "probe_failed", f"{type(exc).__name__}: {_detail(exc)}"
    if status not in PROBE_STATUSES:
        return {
            "capability": str(capability),
            "tier": str(tier),
            "engine": str(engine),
            "status": "probe_failed",
            "detail": f"observer returned unknown status {status!r}; {_PROBE_NOTE}",
        }
    return {
        "capability": str(capability),
        "tier": str(tier),
        "engine": str(engine),
        "status": str(status),
        "detail": f"{detail} {_PROBE_NOTE}",
    }


def _import_observer(module: str) -> Callable[[], tuple[str, str]]:
    def observe() -> tuple[str, str]:
        try:
            importlib.import_module(module)
        except ImportError as exc:
            return "not_installed", f"ImportError: {_detail(exc)}"
        return "available", f"module '{module}' imports; no engine run during this probe"

    return observe


def _tesseract_observer() -> tuple[str, str]:
    try:
        import pytesseract
    except ImportError as exc:
        return "not_installed", f"ImportError: {_detail(exc)}"
    try:
        version = pytesseract.get_tesseract_version()
    except Exception as exc:  # noqa: BLE001 - missing system binary, not a crash
        return "not_installed", f"tesseract binary unavailable: {type(exc).__name__}: {_detail(exc)}"
    return "available", f"pytesseract + tesseract {version} observed locally"


def _t1_observer(capability: Capability) -> Callable[[], tuple[str, str]]:
    def observe() -> tuple[str, str]:
        models = _models_with_capability(capability)
        if not models:
            return "not_configured", f"no configured model declares capability '{capability}'"
        names = ", ".join(str(getattr(model, "name", model)) for model in models)
        return "available", f"configured model(s) [{names}] declare capability '{capability}'"

    return observe


def _t2_specs(capability: Capability) -> list[tuple[str, Callable[[], tuple[str, str]]]]:
    if capability is Capability.TTS:
        return [("edge-tts", _import_observer("edge_tts"))]
    if capability is Capability.IMAGE_GEN:
        return [
            (
                "aihorde-anonymous",
                lambda: (
                    "available",
                    "AI Horde anonymous image API (documented keyless constant; no network call made during this probe)",
                ),
            )
        ]
    detail = {
        Capability.STT: "AI Horde v2 exposes no speech-transcription endpoint; no keyless STT provider ships with Alpha",
        Capability.OCR: "no keyless OCR provider ships with Alpha",
        Capability.VISION: "no keyless image-understanding provider ships with Alpha",
        Capability.WAKE_WORD: "wake word is scored locally from streamed frames; no keyless network provider applies",
    }[capability]
    return [("(none)", lambda detail=detail: (SKIP_SKIPPED_NO_PROVIDER, detail))]


def _t3_specs(capability: Capability) -> list[tuple[str, Callable[[], tuple[str, str]]]]:
    if capability is Capability.TTS:
        return [("piper", _import_observer("piper"))]
    if capability is Capability.STT:
        return [("faster-whisper", _import_observer("faster_whisper"))]
    if capability is Capability.OCR:
        return [("rapidocr", _import_observer("rapidocr_onnxruntime")), ("tesseract", _tesseract_observer)]
    if capability is Capability.WAKE_WORD:
        return [("openwakeword", _import_observer("openwakeword"))]
    detail = {
        Capability.IMAGE_GEN: "local image generation is out of scope (not lightweight); AI Horde anonymous (T2) is the fallback",
        Capability.VISION: "no local vision engine ships with Alpha; a configured vision model (T1) serves this capability",
    }[capability]
    return [("(none)", lambda detail=detail: (SKIP_SKIPPED_NO_PROVIDER, detail))]


def capabilities_report() -> dict[str, Any]:
    """Full availability matrix + observed voice config, observation only.

    One row per capability x tier x engine. ``status`` is limited to
    ``available | not_installed | not_configured | skipped_no_provider |
    probe_failed`` and every detail discloses that reachability was not probed.
    """
    rows: list[dict[str, Any]] = []
    for capability in Capability:
        for engine, observer in _t1_specs_rows(capability):
            rows.append(_probe_engine(capability, TIER_T1, engine, observer))
        for engine, observer in _t2_specs(capability):
            rows.append(_probe_engine(capability, TIER_T2, engine, observer))
        for engine, observer in _t3_specs(capability):
            rows.append(_probe_engine(capability, TIER_T3, engine, observer))

    voice = _voice_config()
    voice_block: dict[str, Any] = {
        "enabled": bool(getattr(voice, "enabled", False)),
        "wake_word": {
            "engine": getattr(getattr(voice, "wake_word", None), "engine", None),
            "threshold": getattr(getattr(voice, "wake_word", None), "threshold", None),
            "armed_default": getattr(getattr(voice, "wake_word", None), "armed_default", None),
        },
        "tts": {
            "autoplay": getattr(getattr(voice, "tts", None), "autoplay", None),
            "voice": getattr(getattr(voice, "tts", None), "voice", None),
        },
        "stt": {
            "model_size": getattr(getattr(voice, "stt", None), "model_size", None),
            "language": getattr(getattr(voice, "stt", None), "language", None),
        },
    }
    observed_error = getattr(voice, "_observed_error", None)
    if observed_error:
        voice_block["detail"] = f"config observation failed, defaults shown: {observed_error}"

    return {
        "rows": rows,
        "voice": voice_block,
        "note": "statuses are import/config observations only; reachability not probed",
    }


def _t1_specs_rows(capability: Capability) -> list[tuple[str, Callable[[], tuple[str, str]]]]:
    models = _models_with_capability(capability)
    if not models:
        return [("(none)", _t1_observer(capability))]
    return [(str(getattr(model, "name", model)), _t1_observer(capability)) for model in models]


# ---------------------------------------------------------------------------
# The single seam
# ---------------------------------------------------------------------------


def _invoke_t1(capability: Capability, payload: dict[str, Any], attempts: list[dict[str, Any]]) -> CapabilityResult:
    """T1 dispatcher: configured models with the capability (lazy engine import)."""
    from alpha.multimodal.engines.provider import run_t1

    return run_t1(capability, payload, attempts)


def _invoke_t2(capability: Capability, payload: dict[str, Any], attempts: list[dict[str, Any]]) -> CapabilityResult:
    """T2 dispatcher: free keyless providers (edge-tts, AI Horde anonymous)."""
    from alpha.multimodal.engines.keyless import run_t2

    return run_t2(capability, payload, attempts)


def _invoke_t3(capability: Capability, payload: dict[str, Any], attempts: list[dict[str, Any]]) -> CapabilityResult:
    """T3 dispatcher: free self-hosted local engines (lazy heavy imports)."""
    from alpha.multimodal.engines.local import run_t3

    return run_t3(capability, payload, attempts)


def invoke(capability: str | Capability, payload: dict[str, Any] | None = None) -> CapabilityResult:
    """Run *capability* through the ordered T1 -> T2 -> T3 chain. THE seam.

    Raises:
        ValueError: unknown capability name (caller bug, honest message).
        MultimodalUnavailableError: every tier was exhausted; ``attempts``
            carries every skip and failure row observed on the way.
    """
    cap = _coerce_capability(capability)
    data = dict(payload or {})
    attempts: list[dict[str, Any]] = []
    hooks = (_invoke_t1, _invoke_t2, _invoke_t3)
    for tier, hook in zip(TIER_ORDER, hooks, strict=True):
        try:
            result = hook(cap, data, attempts)
        except TierSkip as skip:
            attempts.extend(skip.rows or [skip_row(tier, "(none)", skip.status, skip.detail)])
            logger.info("capability %s tier %s skipped: %s (%s)", cap, tier, skip.status, skip.detail)
            continue
        except TierExhausted as exhausted:
            attempts.extend(exhausted.attempts)
            logger.info("capability %s tier %s exhausted: %d attempt(s)", cap, tier, len(exhausted.attempts))
            continue
        if result is None or not getattr(result, "ok", False):
            detail = "tier hook returned no successful result"
            attempts.append(skip_row(tier, "(none)", "probe_failed", detail))
            continue
        result.capability = str(cap)
        result.tier = tier
        result.attempts = [dict(row) for row in attempts]
        return result
    raise MultimodalUnavailableError(str(cap), attempts)
