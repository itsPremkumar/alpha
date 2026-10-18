"""Swarm engine operator policy (bot-mode master plan, phases 3.1-3.2).

Two process-level policy knobs live here rather than in code so every swarm
policy name is readable in ``config.yaml``: the startup recovery pass
(``swarm.resume_on_start``, 3.1) and the plan decomposition source
(``swarm.decomposition``, 3.2).  The first is startup-only; the second is read
per ``create_swarm`` and therefore hot-reloads like any other request-time
config.
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
    decomposition: Literal["auto", "template", "model"] = Field(
        default="template",
        description=(
            "where a plan's tasks come from: 'template' (default) selects the fixed graph "
            "shapes and never spends a token — plan 11.3's cost gate; 'model' runs one "
            "bounded LLM call and scores the candidates it returns; 'auto' attempts the "
            "same call only under an explicitly declared positive budget.max_tokens, so a "
            "heuristic default can never start spending on its own initiative. Every plan "
            "records the outcome in metrics.decomposition: on any failure (no invoke, "
            "budget pre-check, raising call, invalid reply, no valid candidate) the "
            "template plan is used and the reason is named — source='model' is never "
            "recorded without a real call, and tokens are measured or None, never "
            "fabricated."
        ),
    )
