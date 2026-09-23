"""Capability vocabulary for the multimodal chain (shared with config validation)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Capability(StrEnum):
    """Every capability the T1->T2->T3 chain can serve.

    ``wake_word`` is a chain capability but deliberately NOT a model
    capability: it is served by a local engine over streamed mic frames,
    never by a configured model.
    """

    WAKE_WORD = "wake_word"
    STT = "stt"
    TTS = "tts"
    VISION = "vision"
    OCR = "ocr"
    IMAGE_GEN = "image_gen"


#: Allowed values for ``models[].capabilities`` in config.yaml. Unknown values
#: raise a loud ``ValueError`` at config load — a typo must never silently
#: disable a model's participation in the chain.
MODEL_CAPABILITIES: frozenset[str] = frozenset(
    {
        "tts",
        "stt",
        "image_gen",
        "vision",
        "ocr",
    }
)


@dataclass
class CapabilityResult:
    """Outcome of one successful ``chain.invoke`` call.

    ``attempts`` carries the full honest chain history for this call — every
    tier skip and every failed engine that ran before the winner (and, on
    exhaustion, every attempt of the :class:`MultimodalUnavailableError`).
    """

    ok: bool
    capability: str
    tier: str | None = None
    engine: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""
