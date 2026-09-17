"""Long-Horizon Multi-Session Boulder Handoff Manager.

Implements the Anthropic Fable 5.1 & Deep Agents pattern for long-horizon autonomy:
Synthesizes structured handoff packets before context resets so the agent can work
continuously for hours across multiple sessions without losing state or coherence.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_HANDOFF_DIR = Path(".boulder/handoffs")


@dataclass
class SessionHandoffPackage:
    work_id: str
    task_objective: str
    completed_milestones: list[dict[str, Any]] = field(default_factory=list)
    pending_milestones: list[str] = field(default_factory=list)
    active_hypotheses: list[str] = field(default_factory=list)
    known_failures: list[str] = field(default_factory=list)
    key_artifacts: list[str] = field(default_factory=list)
    next_action: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionHandoffPackage:
        return cls(
            work_id=data.get("work_id", "default"),
            task_objective=data.get("task_objective", ""),
            completed_milestones=data.get("completed_milestones", []),
            pending_milestones=data.get("pending_milestones", []),
            active_hypotheses=data.get("active_hypotheses", []),
            known_failures=data.get("known_failures", []),
            key_artifacts=data.get("key_artifacts", []),
            next_action=data.get("next_action", ""),
            created_at=data.get(
                "created_at", datetime.now(timezone.utc).isoformat()
            ),
        )


class SessionHandoffManager:
    """Manages creation, serialization, and retrieval of multi-session milestone handoffs."""

    def __init__(self, base_path: str | Path | None = None) -> None:
        self.base_path = Path(base_path) if base_path else Path.cwd()
        self.handoff_dir = self.base_path / DEFAULT_HANDOFF_DIR

    def save_handoff(self, handoff: SessionHandoffPackage) -> Path:
        """Persist a handoff package to disk."""
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        filename = f"handoff_{handoff.work_id}.json"
        target = self.handoff_dir / filename
        with open(target, "w", encoding="utf-8") as f:
            json.dump(handoff.to_dict(), f, indent=2)
        # Also maintain a latest pointer
        latest_target = self.handoff_dir / "latest.json"
        with open(latest_target, "w", encoding="utf-8") as f:
            json.dump(handoff.to_dict(), f, indent=2)
        logger.info("Saved Session Handoff [%s] to %s", handoff.work_id, target)
        return target

    def load_latest_handoff(self) -> SessionHandoffPackage | None:
        """Load the most recent handoff if available."""
        latest_file = self.handoff_dir / "latest.json"
        if not latest_file.exists():
            return None
        try:
            with open(latest_file, encoding="utf-8") as f:
                data = json.load(f)
                return SessionHandoffPackage.from_dict(data)
        except Exception as e:
            logger.warning("Failed to load latest handoff: %s", e)
            return None

    def format_for_context(self, handoff: SessionHandoffPackage) -> str:
        """Format the handoff package into a structured prompt context block."""
        lines = [
            "<session_handoff_continuation>",
            f"Active Objective: {handoff.task_objective}",
            f"Handoff Work ID: {handoff.work_id}",
            f"Timestamp: {handoff.created_at}",
            "",
            "Completed Milestones & Evidence:",
        ]
        if handoff.completed_milestones:
            for item in handoff.completed_milestones:
                step = item.get("step", "Unknown")
                evidence = item.get("evidence", "Verified")
                lines.append(f"  [x] {step} (Evidence: {evidence})")
        else:
            lines.append("  (None yet recorded)")

        lines.append("")
        lines.append("Pending Milestones:")
        if handoff.pending_milestones:
            for step in handoff.pending_milestones:
                lines.append(f"  [ ] {step}")
        else:
            lines.append("  (None pending)")

        if handoff.known_failures:
            lines.append("")
            lines.append("Known Pitfalls / Past Failures to Avoid:")
            for fail in handoff.known_failures:
                lines.append(f"  * {fail}")

        if handoff.key_artifacts:
            lines.append("")
            lines.append("Verified Workspace Artifacts:")
            for art in handoff.key_artifacts:
                lines.append(f"  * {art}")

        if handoff.next_action:
            lines.append("")
            lines.append(f"Immediate Next Action: {handoff.next_action}")

        lines.append("</session_handoff_continuation>")
        return "\n".join(lines)


# Singleton
_GLOBAL_HANDOFF_MANAGER: SessionHandoffManager | None = None


def get_handoff_manager(base_path: str | Path | None = None) -> SessionHandoffManager:
    global _GLOBAL_HANDOFF_MANAGER
    if _GLOBAL_HANDOFF_MANAGER is None or base_path is not None:
        _GLOBAL_HANDOFF_MANAGER = SessionHandoffManager(base_path)
    return _GLOBAL_HANDOFF_MANAGER
