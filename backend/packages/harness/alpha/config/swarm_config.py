"""Swarm engine operator policy (bot-mode master plan, phase 3.1).

The section starts with the one process-level policy knob recovery needs.
``swarm.decomposition`` joins it when model-backed decomposition ships (3.2);
both live here rather than in code so every swarm policy name is readable in
``config.yaml``.
"""

from typing import Literal

from pydantic import BaseModel, Field


class SwarmConfig(BaseModel):
    """Operator policy for decisions the swarm engine makes once per process."""

    resume_on_start: Literal["none", "owned", "all"] = Field(
        default="none",
        description=(
            "startup-only: alpha.swarm.recovery.auto_resume_on_start() runs once during "
            "Gateway startup, so an edit needs a restart. A restart always PARKS an "
            "in-flight plan first (paused + terminal_reason=process_restart_requires_resume, "
            "leases cleared) — this flag only chooses what the startup pass does next: "
            "'none' (default) parks and waits for an explicit resume; 'owned' additionally "
            "resumes recovered plans owned by the installation default owner; 'all' resumes "
            "every recovered plan. Cost-bearing resume is opt-in (plan 11.3)."
        ),
    )
