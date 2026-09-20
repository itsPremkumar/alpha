from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import BlackboardSnapshot


class BlackboardPersistence:
    """Handles snapshot persistence for the Blackboard."""

    @staticmethod
    def save_json(snapshot: BlackboardSnapshot, target_path: str | Path) -> str:
        path = Path(target_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot.to_dict(), f, indent=2)
        return str(path)

    @staticmethod
    def load_json(source_path: str | Path) -> BlackboardSnapshot:
        path = Path(source_path)
        with open(path, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        return BlackboardSnapshot(
            session_id=data["session_id"],
            goal=data["goal"],
            goal_classification=data["goal_classification"],
            current_phase=data["current_phase"],
            plane_states=data["plane_states"],
            shared_context=data["shared_context"],
            evidence_trail=data["evidence_trail"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )
