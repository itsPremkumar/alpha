"""Autonomous Reproduction Engine package."""

from agent_workspace.reproduction.engine import ReproductionEngine
from agent_workspace.reproduction.gates import (
    PostFixVerificationGate,
    PreFixFailureGate,
    RegressionSafetyGuard,
)
from agent_workspace.reproduction.models import (
    GateResult,
    ReproductionReport,
    ReproductionScript,
    ReproductionStatus,
)
from agent_workspace.reproduction.synthesizer import ReproductionSynthesizer

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
