from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CognitiveMode(str, Enum):
    FAST = "fast"
    DELIBERATIVE = "deliberative"
    RESEARCH = "research"
    EXPLORATORY = "exploratory"
    ADVERSARIAL = "adversarial"
    RECOVERY = "recovery"


class BiasFlag(str, Enum):
    CONFIRMATION_BIAS = "confirmation_bias"
    PREMATURE_CONVERGENCE = "premature_convergence"
    PLAN_STAGNATION = "plan_stagnation"
    TOOL_MISUSE = "tool_misuse"
    OVERCONFIDENCE = "overconfidence"


@dataclass
class MetacognitiveAssessment:
    mode: CognitiveMode
    confidence: float
    calibrated_confidence: float
    detected_biases: list[BiasFlag] = field(default_factory=list)
    stagnation_score: float = 0.0
    recommendation: str = "Continue nominal execution"
    should_switch_strategy: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "confidence": self.confidence,
            "calibrated_confidence": self.calibrated_confidence,
            "detected_biases": [b.value for b in self.detected_biases],
            "stagnation_score": self.stagnation_score,
            "recommendation": self.recommendation,
            "should_switch_strategy": self.should_switch_strategy,
            "timestamp": self.timestamp,
        }
