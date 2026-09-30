"""Data models for Recursive Self-Improvement (RSI) loop."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class RSIStage(str, Enum):
    IDLE = "idle"
    BOTTLENECK_DETECTED = "bottleneck_detected"
    HYPOTHESIS_GENERATED = "hypothesis_generated"
    CANDIDATE_CREATED = "candidate_created"
    AB_TEST_RUNNING = "ab_test_running"
    HOLDOUT_EVALUATION = "holdout_evaluation"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    PREVIEW = "preview"


@dataclass
class RSIHypothesis:
    """Hypothesis explaining an agent bottleneck and proposing an optimization."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    bottleneck: str = ""
    description: str = ""
    expected_improvement: float = 0.15
    target_component: str = "tool_router"
    created_at: float = field(default_factory=time.time)


@dataclass
class RSICandidate:
    """Candidate modification generated to resolve an identified bottleneck."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    hypothesis_id: str = ""
    component: str = ""
    original_config: dict[str, Any] = field(default_factory=dict)
    modified_config: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class ABTestResult:
    """A/B comparison of a candidate configuration.

    Every score is optional and defaults to ``None`` because **an unmeasured
    comparison has no number**. ``run_rsi_cycle`` cannot execute a benchmark, so
    it reports ``None`` plus ``not_measured_reason`` rather than a plausible
    constant: a fabricated score is a positive claim about code that never ran,
    and a downstream consumer cannot tell ``0.88`` from a real measurement by
    reading it. ``alpha.rsi.holdout.holdout_gate`` only accepts
    ``evidence_kind="measured"``, so simulated evidence can never promote.
    """

    candidate_id: str
    baseline_score: float | None
    candidate_score: float | None
    improved: bool | None
    confidence: float | None
    latency_delta_ms: float | None = None
    evidence_kind: str = "unknown"
    not_measured_reason: str = ""


@dataclass
class HoldoutResult:
    """Holdout regression verdict for a candidate configuration.

    ``improved``/``regressed`` are tri-state for the same reason as
    :class:`ABTestResult`: "not run" must never read as "not regressed", or a
    skipped regression suite looks identical to a passing one.
    """

    candidate_id: str
    improved: bool | None
    regressed: bool | None
    score: float | None
    baseline_score: float | None
    evidence: list[str] = field(default_factory=list)
    evidence_kind: str = "unknown"
    not_measured_reason: str = ""


@dataclass
class RSIResult:
    """End-to-end outcome of an autonomous RSI cycle."""

    promoted: bool
    stage: RSIStage
    hypothesis: RSIHypothesis
    candidate: RSICandidate
    ab_test: ABTestResult | None = None
    holdout: HoldoutResult | None = None
    evidence: list[str] = field(default_factory=list)
    evidence_kind: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "promoted": self.promoted,
            "stage": self.stage.value,
            "hypothesis": asdict(self.hypothesis),
            "candidate": asdict(self.candidate),
            "ab_test": asdict(self.ab_test) if self.ab_test else None,
            "holdout": asdict(self.holdout) if self.holdout else None,
            "evidence": self.evidence,
            "evidence_kind": self.evidence_kind,
        }
