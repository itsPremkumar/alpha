"""Bot Profile and Capability Epoch Engine (inspired by Hermes Bot Mode, upgraded).

Defines first-class Bot profiles with custom SOUL instructions, toolsets,
skills, and deterministic 12-hex capability epoch fingerprinting.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class BotProfile:
    """A first-class autonomous Bot persona."""

    name: str
    display_name: str
    role: str
    soul: str
    model: str | None = None
    toolsets: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    # Inventory #1/#25: avatar label plus lifecycle status. Both are display /
    # scheduling metadata and intentionally excluded from the capability
    # fingerprint so status flips never churn the epoch.
    avatar: str = ""
    status: str = "active"
    # match API & runtime tracking
    last_active: str | None = None
    version: int = 1
    # Organizational hierarchy & responsibility (Master Inventory #17-#20)
    department: str = "engineering"
    reports_to: str | None = None
    responsibilities: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    # Liveness & succession (Master Inventory #27-#35)
    heartbeat: str | None = None
    succession_fallback: str | None = None
    # Performance & reputation metrics (Master Inventory #51-#52)
    reputation_score: float = 1.0
    task_stats: dict[str, Any] = field(
        default_factory=lambda: {
            "completed": 0,
            "failed": 0,
            "total_runs": 0,
            "avg_duration_sec": 0.0,
        }
    )
    # Bot-owned routines (Master Inventory #9)
    routines: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def capability_fingerprint(self) -> str:
        """Compute a deterministic 12-hex digest of the bot's capability surface.

        Hashed surface includes: name, role, soul content, model, sorted toolsets, and sorted skills.
        """
        surface = {
            "name": self.name.lower().strip(),
            "role": self.role.strip(),
            "soul_hash": hashlib.sha256(self.soul.encode("utf-8")).hexdigest(),
            "model": self.model or "default",
            "toolsets": sorted(self.toolsets),
            "skills": sorted(self.skills),
        }
        raw_json = json.dumps(surface, sort_keys=True)
        return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()[:12]

    def epoch_header(self) -> str:
        """The capability epoch stamp to embed in system prompts."""
        return f"Capability epoch: {self.capability_fingerprint()}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["epoch"] = self.capability_fingerprint()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BotProfile:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


def generate_default_soul(name: str, role: str) -> str:
    """Generate an authoritative, context-aware SOUL for auto-provisioned bots."""
    return f"""# SOUL.md - {name.capitalize()} ({role})

You are **{name}**, operating as a specialized AI teammate in the role of **{role}**.

## Core Directives:
1. Actively contribute your specialized domain expertise in group chats and tasks.
2. In team discussions, address teammates with their @handle when handing off work.
3. Be concise, direct, and action-oriented. Avoid cheerful filler text.
4. When you have nothing essential to add to a turn, respond with `(pass)`.
5. Claim tasks from the Kanban board matching your specialty and submit deliverables for peer review.
"""
