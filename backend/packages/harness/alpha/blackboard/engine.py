from __future__ import annotations

import time
import uuid
from typing import Any

from .models import (
    BlackboardSnapshot,
    CognitivePhase,
    EvidenceItem,
    GoalClassification,
    PlaneState,
    PlaneStatus,
)

ALL_20_PLANES = [
    "plane_1_self_evolution",
    "plane_2_self_awareness",
    "plane_3_meta_reasoning",
    "plane_4_deep_research",
    "plane_5_metacognition",
    "plane_6_deep_cognition",
    "plane_7_search_optimization",
    "plane_8_multi_agent_orchestration",
    "plane_9_reflexion",
    "plane_10_tree_of_thoughts",
    "plane_11_hierarchical_planning",
    "plane_12_action_selection",
    "plane_13_multi_round_verification",
    "plane_14_avo_evolutionary_search",
    "plane_15_memory_consolidation",
    "plane_16_benchmark_strategy",
    "plane_17_twenty_four_seven_operation",
    "plane_18_personal_singularity",
    "plane_19_emergent_depth",
    "plane_20_governed_self_modification",
]


class BlackboardEngine:
    """Enterprise-grade 20-Plane Shared Blackboard Engine."""

    def __init__(
        self,
        session_id: str | None = None,
        goal: str = "",
        goal_classification: GoalClassification = GoalClassification.COMPLEX,
    ) -> None:
        self.session_id = session_id or f"bb_{uuid.uuid4().hex[:12]}"
        self.goal = goal
        self.goal_classification = (
            goal_classification
            if isinstance(goal_classification, GoalClassification)
            else GoalClassification(goal_classification)
        )
        self.current_phase = CognitivePhase.ANALYZE
        self.created_at = time.time()
        self.updated_at = self.created_at

        # Initialize all 20 planes into default idle states
        self.plane_states: dict[str, PlaneState] = {
            plane_id: PlaneState(plane_id=plane_id, status=PlaneStatus.IDLE)
            for plane_id in ALL_20_PLANES
        }
        self.shared_context: dict[str, Any] = {}
        self.evidence_trail: list[EvidenceItem] = []

    def set_phase(self, phase: CognitivePhase | str) -> None:
        self.current_phase = (
            phase if isinstance(phase, CognitivePhase) else CognitivePhase(phase)
        )
        self.updated_at = time.time()

    def set_plane_state(
        self,
        plane_id: str,
        status: PlaneStatus | str,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> PlaneState:
        if plane_id not in self.plane_states:
            self.plane_states[plane_id] = PlaneState(plane_id=plane_id)

        ps = self.plane_states[plane_id]
        status_enum = (
            status if isinstance(status, PlaneStatus) else PlaneStatus(status)
        )
        ps.status = status_enum
        if output is not None:
            ps.output = output
        if error is not None:
            ps.error = error
        if status_enum == PlaneStatus.RUNNING:
            ps.started_at = time.time()
        elif status_enum in (
            PlaneStatus.COMPLETED,
            PlaneStatus.FAILED,
            PlaneStatus.SKIPPED,
        ):
            ps.completed_at = time.time()

        self.updated_at = time.time()
        return ps

    def get_plane_state(self, plane_id: str) -> PlaneState:
        if plane_id not in self.plane_states:
            self.plane_states[plane_id] = PlaneState(plane_id=plane_id)
        return self.plane_states[plane_id]

    def record_evidence(
        self,
        plane_id: str,
        evidence_type: str,
        content: dict[str, Any],
        confidence: float = 1.0,
    ) -> EvidenceItem:
        clamped_conf = max(0.0, min(1.0, float(confidence)))
        item = EvidenceItem(
            plane_id=plane_id,
            evidence_type=evidence_type,
            content=content,
            confidence=clamped_conf,
        )
        self.evidence_trail.append(item)
        self.updated_at = time.time()
        return item

    def query_evidence(
        self,
        plane_id: str | None = None,
        evidence_type: str | None = None,
        min_confidence: float = 0.0,
    ) -> list[EvidenceItem]:
        results: list[EvidenceItem] = []
        for item in self.evidence_trail:
            if plane_id and item.plane_id != plane_id:
                continue
            if evidence_type and item.evidence_type != evidence_type:
                continue
            if item.confidence < min_confidence:
                continue
            results.append(item)
        return results

    def set_shared_context(self, key: str, value: Any) -> None:
        self.shared_context[key] = value
        self.updated_at = time.time()

    def get_shared_context(self, key: str, default: Any = None) -> Any:
        return self.shared_context.get(key, default)

    def create_snapshot(self) -> BlackboardSnapshot:
        return BlackboardSnapshot(
            session_id=self.session_id,
            goal=self.goal,
            goal_classification=self.goal_classification.value,
            current_phase=self.current_phase.value,
            plane_states={k: v.to_dict() for k, v in self.plane_states.items()},
            shared_context=dict(self.shared_context),
            evidence_trail=[item.to_dict() for item in self.evidence_trail],
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return self.create_snapshot().to_dict()
