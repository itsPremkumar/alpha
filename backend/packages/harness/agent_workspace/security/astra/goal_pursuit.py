"""Astra Autonomous Goal Pursuit Engine & Agent Highlighting.

Inspired by Google DeepMind Project Astra:
- Autonomous goal formulation & milestone decomposition
- Proactive discrepancy evaluation between perceived world and target goal
- Visual Agent Highlighting (anchoring recommendations and objects with bounding boxes)
- Multimodal evidence-based milestone completion
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from agent_workspace.security.astra.spatiotemporal import (
    BoundingBox,
    SpatialObject,
    SpatioTemporalCache,
)


class MilestoneStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SATISFIED = "satisfied"
    FAILED = "failed"


@dataclass
class Milestone:
    """A concrete milestone required to achieve the high-level goal."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = ""
    description: str = ""
    required_evidence_type: str = "general"  # "test_pass", "file_created", "object_located", "command_success"
    status: MilestoneStatus = MilestoneStatus.PENDING
    satisfied_at: float | None = None
    proof_evidence: str = ""

    def mark_satisfied(self, evidence: str) -> None:
        self.status = MilestoneStatus.SATISFIED
        self.satisfied_at = time.time()
        self.proof_evidence = evidence

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class AgentHighlight:
    """On-screen or visual highlight to ground agent attention (Project Astra feature)."""
    highlight_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    label: str = ""
    bbox: BoundingBox = field(default_factory=BoundingBox)
    color: str = "#4285F4"  # Astra Google Blue, Green (#34A853), Red (#EA4335)
    note: str = ""
    created_at: float = field(default_factory=time.time)
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bbox"] = self.bbox.to_dict()
        return data


class AstraGoalHarness:
    """Orchestrates autonomous goal pursuit with spatial perception and agent highlighting."""

    def __init__(
        self,
        goal_statement: str,
        constraints: list[str] | None = None,
        goal_id: str | None = None,
        spatial_cache: SpatioTemporalCache | None = None,
    ):
        self.goal_id: str = goal_id or str(uuid.uuid4())[:12]
        self.goal_statement: str = goal_statement
        self.constraints: list[str] = constraints or []
        self.spatial_cache: SpatioTemporalCache = spatial_cache or SpatioTemporalCache()
        self.milestones: list[Milestone] = []
        self.highlights: list[AgentHighlight] = []
        self.state: str = "formulating"  # formulating -> pursuing -> verifying -> achieved -> failed
        self.created_at: float = time.time()
        self.completed_at: float | None = None

    def add_milestone(
        self,
        title: str,
        description: str = "",
        required_evidence_type: str = "general",
    ) -> Milestone:
        milestone = Milestone(
            title=title,
            description=description,
            required_evidence_type=required_evidence_type,
        )
        self.milestones.append(milestone)
        return milestone

    def add_highlight(
        self,
        label: str,
        bbox: BoundingBox,
        color: str = "#4285F4",
        note: str = "",
    ) -> AgentHighlight:
        """Create a visual anchor highlight on screen / perceived canvas."""
        highlight = AgentHighlight(
            label=label,
            bbox=bbox,
            color=color,
            note=note,
        )
        self.highlights.append(highlight)
        return highlight

    def clear_highlights(self) -> None:
        self.highlights.clear()

    def evaluate_discrepancy(self) -> dict[str, Any]:
        """Measure current gap between perceived reality and target goal state."""
        total = len(self.milestones)
        if total == 0:
            return {
                "goal_id": self.goal_id,
                "discrepancy_score": 0.0,
                "progress_percent": 100,
                "pending_milestones": [],
                "state": self.state,
            }

        satisfied = [m for m in self.milestones if m.status == MilestoneStatus.SATISFIED]
        pending = [m for m in self.milestones if m.status in (MilestoneStatus.PENDING, MilestoneStatus.ACTIVE)]
        failed = [m for m in self.milestones if m.status == MilestoneStatus.FAILED]

        discrepancy_score = len(pending) / total
        progress_pct = int((len(satisfied) / total) * 100)

        next_milestone = pending[0] if pending else None

        return {
            "goal_id": self.goal_id,
            "goal_statement": self.goal_statement,
            "discrepancy_score": discrepancy_score,
            "progress_percent": progress_pct,
            "satisfied_count": len(satisfied),
            "pending_count": len(pending),
            "failed_count": len(failed),
            "next_milestone": next_milestone.to_dict() if next_milestone else None,
            "active_highlights_count": len([h for h in self.highlights if h.active]),
            "state": self.state,
        }

    def pursue_step(
        self,
        action_name: str,
        step_input: dict[str, Any],
        tool_result: str | None = None,
        observed_objects: list[SpatialObject] | None = None,
        screen_context: str = "",
    ) -> dict[str, Any]:
        """Execute one goal pursuit step with perception ingestion and milestone verification."""
        if self.state == "formulating":
            self.state = "pursuing"

        # 1. Ingest observation into spatio-temporal memory
        if observed_objects or screen_context:
            self.spatial_cache.ingest_frame(
                objects=observed_objects,
                screen_context=screen_context,
            )

        # 2. Check if current action or output satisfies active milestone
        eval_result = self.evaluate_discrepancy()
        next_m_dict = eval_result.get("next_milestone")

        milestone_satisfied = False
        if next_m_dict:
            target_id = next_m_dict["id"]
            milestone = next(m for m in self.milestones if m.id == target_id)
            milestone.status = MilestoneStatus.ACTIVE

            # Evaluate evidence satisfaction
            if tool_result and ("pass" in tool_result.lower() or "success" in tool_result.lower() or "ok" in tool_result.lower()):
                milestone.mark_satisfied(evidence=tool_result)
                milestone_satisfied = True
            elif observed_objects:
                for obj in observed_objects:
                    if obj.label.lower() in milestone.description.lower() or obj.label.lower() in milestone.title.lower():
                        milestone.mark_satisfied(evidence=f"Observed '{obj.label}' at bbox {obj.bbox.to_dict()}")
                        # Automatically create highlight
                        self.add_highlight(
                            label=obj.label,
                            bbox=obj.bbox,
                            color="#34A853",  # Green
                            note=f"Grounding proof for milestone: {milestone.title}",
                        )
                        milestone_satisfied = True
                        break

        # 3. Check goal completion
        updated_eval = self.evaluate_discrepancy()
        if updated_eval["pending_count"] == 0 and updated_eval["failed_count"] == 0:
            self.state = "achieved"
            self.completed_at = time.time()

        return {
            "goal_id": self.goal_id,
            "action_executed": action_name,
            "milestone_satisfied": milestone_satisfied,
            "goal_state": self.state,
            "progress_percent": updated_eval["progress_percent"],
            "discrepancy_score": updated_eval["discrepancy_score"],
            "highlights": [h.to_dict() for h in self.highlights if h.active],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "goal_statement": self.goal_statement,
            "constraints": self.constraints,
            "state": self.state,
            "progress": self.evaluate_discrepancy(),
            "milestones": [m.to_dict() for m in self.milestones],
            "highlights": [h.to_dict() for h in self.highlights if h.active],
            "total_frames_perceived": self.spatial_cache.total_frames(),
        }
