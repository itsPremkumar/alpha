"""Autonomous Reproduction Engine package."""

from alpha.reproduction.engine import ReproductionEngine
from alpha.reproduction.gates import (
    PostFixVerificationGate,
    PreFixFailureGate,
    RegressionSafetyGuard,
)
from alpha.reproduction.models import (
    GateResult,
    ReproductionReport,
    ReproductionScript,
    ReproductionStatus,
)
from alpha.reproduction.synthesizer import ReproductionSynthesizer

__all__ = [
    "ReproductionStatus",
    "ReproductionScript",
    "GateResult",
    "ReproductionReport",
    "ReproductionSynthesizer",
    "PreFixFailureGate",
    "PostFixVerificationGate",
    "RegressionSafetyGuard",
    "ReproductionEngine",
]
