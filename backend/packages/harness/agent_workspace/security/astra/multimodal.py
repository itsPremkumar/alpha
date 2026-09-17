from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent_workspace.security.astra.multimodal")


@dataclass
class VisualEvidenceItem:
    """Represents a grounded visual observation (screenshot, rendered UI, diagram)."""
    evidence_id: str
    media_type: str  # "image/png", "image/jpeg", "svg"
    sha256_hash: str
    dimensions: tuple[int, int]  # width, height
    bounding_boxes: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "media_type": self.media_type,
            "sha256_hash": self.sha256_hash,
            "dimensions": self.dimensions,
            "bounding_boxes": self.bounding_boxes,
            "description": self.description,
            "timestamp": self.timestamp,
        }


class MultimodalGrounding:
    """
    Multimodal Observation & Grounding Engine inspired by Project Astra.
    Validates visual artifacts and tracks bounding boxes and UI coordinates.
    """

    def __init__(self) -> None:
        self.evidence_archive: dict[str, VisualEvidenceItem] = {}

    def register_visual_evidence(
        self,
        image_bytes: bytes,
        media_type: str = "image/png",
        dimensions: tuple[int, int] = (1920, 1080),
        description: str = "",
        bounding_boxes: list[dict[str, Any]] | None = None,
    ) -> VisualEvidenceItem:
        h = hashlib.sha256(image_bytes).hexdigest()[:16]
        ev_id = f"vis_{h[:8]}"

        item = VisualEvidenceItem(
            evidence_id=ev_id,
            media_type=media_type,
            sha256_hash=h,
            dimensions=dimensions,
            bounding_boxes=bounding_boxes or [],
            description=description,
        )
        self.evidence_archive[ev_id] = item
        return item

    def get_evidence(self, evidence_id: str) -> VisualEvidenceItem | None:
        return self.evidence_archive.get(evidence_id)


@dataclass
class ProactiveTrigger:
    trigger_id: str
    condition_type: str  # "file_modified", "status_change", "threshold"
    target: str
    is_active: bool = True
    created_at: float = field(default_factory=time.time)


class ProactiveTriggerEngine:
    """
    Proactive Background Trigger Engine inspired by Project Astra.
    Monitors events and conditions, firing autonomous wakes when external state changes.
    """

    def __init__(self) -> None:
        self.triggers: dict[str, ProactiveTrigger] = {}
        self.fired_events: list[dict[str, Any]] = []

    def register_trigger(
        self,
        trigger_id: str,
        condition_type: str,
        target: str,
    ) -> ProactiveTrigger:
        t = ProactiveTrigger(
            trigger_id=trigger_id,
            condition_type=condition_type,
            target=target,
        )
        self.triggers[trigger_id] = t
        logger.info(f"Registered proactive trigger '{trigger_id}' on {condition_type}:{target}")
        return t

    def check_condition(
        self,
        trigger_id: str,
        current_val: Any,
        expected_val: Any,
    ) -> bool:
        trigger = self.triggers.get(trigger_id)
        if not trigger or not trigger.is_active:
            return False

        if current_val == expected_val:
            self.fired_events.append({
                "trigger_id": trigger_id,
                "target": trigger.target,
                "timestamp": time.time(),
            })
            logger.info(f"PROACTIVE_WAKEUP: Trigger '{trigger_id}' fired on match.")
            return True

        return False
