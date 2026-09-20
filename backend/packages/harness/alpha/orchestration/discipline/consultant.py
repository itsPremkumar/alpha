"""Plan Consultant Specialist Agent (Enterprise Strategic Reviewer Profile).

Enterprise Pre-Planning Gap Analysis Engine:
- Model Assignment: anthropic/claude-3-7-sonnet (reasoning: high)
- Purpose: Pre-planning gap analysis pass before plans are finalized
- Strengths: High compliance with complex mechanics, UI/UX contracts, and edge-case anticipation
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class GapAnalysisReport:
    """Findings from pre-planning gap analysis."""
    task_title: str
    gaps_identified: list[str] = field(default_factory=list)
    suggested_additions: list[str] = field(default_factory=list)
    ui_specifications: list[str] = field(default_factory=list)
    readiness_score: float = 1.0  # 0.0 (incomplete) to 1.0 (production-ready)
    model_family: str = "anthropic/claude-3-7-sonnet"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PlanConsultant:
    """Pre-planning advisor auditing draft plans for blindspots and UI completeness."""

    def __init__(self, model_id: str = "anthropic/claude-3-7-sonnet"):
        self.model_id: str = model_id

    def analyze_gaps(
        self,
        task_title: str,
        task_description: str,
        proposed_steps: list[str],
        is_visual_or_frontend: bool = False,
    ) -> GapAnalysisReport:
        """Run structured gap analysis across error handling, dependencies, and UX."""
        gaps: list[str] = []
        additions: list[str] = []
        ui_specs: list[str] = []

        all_text = (task_title + " " + task_description + " " + " ".join(proposed_steps)).lower()

        # 1. Error handling check
        if not any(k in all_text for k in ["error", "fail", "fallback", "exception", "rollback", "retry"]):
            gaps.append("Plan lacks explicit failure recovery or error fallback handling.")
            additions.append("Add try/except block or rollback mechanism for partial failure states.")

        # 2. Test / Verification check
        if not any(k in all_text for k in ["test", "verify", "audit", "assert", "check"]):
            gaps.append("Plan does not include automated verification or regression testing.")
            additions.append("Add unit test suite verification step before marking task complete.")

        # 3. Frontend / UI completeness check
        if is_visual_or_frontend or any(k in all_text for k in ["ui", "frontend", "css", "component", "canvas", "layout"]):
            if not any(k in all_text for k in ["responsive", "mobile", "layout", "flex", "grid"]):
                gaps.append("Frontend task lacks responsive layout constraints (mobile vs desktop).")
                ui_specs.append("Ensure responsive viewport layout using CSS flexbox/grid.")
            if not any(k in all_text for k in ["theme", "color", "dark", "light", "token"]):
                ui_specs.append("Adopt semantic design tokens (neutral background, contrast-safe text).")
            if not any(k in all_text for k in ["loading", "empty", "spinner", "skeleton"]):
                gaps.append("Missing UI empty/loading state specification.")
                ui_specs.append("Implement loading skeleton and empty-state fallbacks.")

        # 4. Compute readiness score
        penalty = len(gaps) * 0.2
        readiness = max(0.2, round(1.0 - penalty, 2))

        return GapAnalysisReport(
            task_title=task_title,
            gaps_identified=gaps,
            suggested_additions=additions,
            ui_specifications=ui_specs,
            readiness_score=readiness,
            model_family=self.model_id,
        )
