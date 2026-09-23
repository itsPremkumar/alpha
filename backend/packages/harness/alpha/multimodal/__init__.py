"""Voice & multimodal capability wave: mic/speaker/wake-word + TTS/STT/OCR/image/vision.

Thin re-exports only — heavy engines import lazily inside the tier hooks of
:mod:`alpha.multimodal.chain`, keeping ``import alpha.multimodal`` cheap for
``test_cold_imports`` and making every module in this package reachable for
``test_no_orphan_modules``.
"""

from alpha.multimodal.capabilities import Capability, CapabilityResult
from alpha.multimodal.chain import invoke
from alpha.multimodal.errors import MultimodalUnavailableError

__all__ = [
    "Capability",
    "CapabilityResult",
    "MultimodalUnavailableError",
    "invoke",
]
