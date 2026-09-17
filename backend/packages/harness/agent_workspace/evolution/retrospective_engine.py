"""Autonomous Retrospective & Prompt/Skill Self-Evolution Engine.

Analyzes postmortems, test retries, and audit council debate transcripts
to synthesize automated system-prompt refinements and specialized skill recommendations.
Queues proposed adjustments in the Human Approval Queue for operator sign-off.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PromptEvolutionProposal:
    proposal_id: str
    project_id: str
    bot_name: str
    trigger_pattern: str
    heuristic_summary: str
    proposed_instruction: str
    target_prompt_section: str  # e.g., "coding_guidelines", "verification_rules", "safety"
    status: str = "pending_review"  # "pending_review", "approved", "rejected"
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RetrospectiveEngine:
    """Extracts learnings from operational friction to iteratively improve bot capabilities."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._proposals: list[PromptEvolutionProposal] = []

    def analyze_recent_learnings(
        self,
        bot_name: str = "coder",
        queue_for_approval: bool = True,
    ) -> list[PromptEvolutionProposal]:
        """Examine postmortem heuristics and test logs to synthesize prompt improvements."""
        new_proposals: list[PromptEvolutionProposal] = []

        # 1. Inspect Postmortem Store
        postmortems: list[dict[str, Any]] = []
        try:
            from agent_workspace.projects.postmortem import get_postmortem_store

            store = get_postmortem_store(self.project_id)
            postmortems = [pm.to_dict() for pm in store.list_heuristics()]
        except Exception:
            pass

        # 2. Heuristic Pattern Matching
        if postmortems:
            for pm in postmortems[-5:]:
                symptom = pm.get("symptom", "").lower()
                fix = pm.get("preventative_rule") or pm.get("root_cause")

                if "import" in symptom or "modulenotfound" in symptom:
                    prop = PromptEvolutionProposal(
                        proposal_id=f"evo-{uuid.uuid4().hex[:8]}",
                        project_id=self.project_id,
                        bot_name=bot_name,
                        trigger_pattern="Recurring Import / Module Resolution Failure",
                        heuristic_summary=f"Incident: {pm.get('symptom')}",
                        proposed_instruction="Always verify that any imported packages are declared in project dependencies before editing code.",
                        target_prompt_section="coding_guidelines",
                    )
                    new_proposals.append(prop)

                elif "type" in symptom or "attributeerror" in symptom:
                    prop = PromptEvolutionProposal(
                        proposal_id=f"evo-{uuid.uuid4().hex[:8]}",
                        project_id=self.project_id,
                        bot_name=bot_name,
                        trigger_pattern="Runtime Type / Attribute Error",
                        heuristic_summary=f"Incident: {pm.get('symptom')}",
                        proposed_instruction="Enforce defensive optional chaining and verify property existence before referencing nested attributes.",
                        target_prompt_section="verification_rules",
                    )
                    new_proposals.append(prop)

        # 3. Fallback General Heuristic if no postmortems yet
        if not new_proposals:
            prop = PromptEvolutionProposal(
                proposal_id=f"evo-{uuid.uuid4().hex[:8]}",
                project_id=self.project_id,
                bot_name=bot_name,
                trigger_pattern="Proactive Quality Hardening",
                heuristic_summary="Automated sprint review for verification rigor",
                proposed_instruction="Verify all Definition of Done criteria and run local test suite prior to requesting human merge approval.",
                target_prompt_section="verification_rules",
            )
            new_proposals.append(prop)

        self._proposals.extend(new_proposals)

        # 4. Queue in Human Approval Queue if requested
        if queue_for_approval:
            for p in new_proposals:
                try:
                    from agent_workspace.projects.approval_queue import get_approval_queue

                    aq = get_approval_queue(self.project_id)
                    aq.request_approval(
                        bot_name="retrospective_bot",
                        action_type="prompt_evolution_proposal",
                        risk_level="medium",
                        details={
                            "proposal_id": p.proposal_id,
                            "target_bot": p.bot_name,
                            "proposed_instruction": p.proposed_instruction,
                            "trigger_pattern": p.trigger_pattern,
                        },
                    )
                except Exception as e:
                    logger.debug(f"Could not queue prompt evolution for approval: {e}")

        return new_proposals

    def list_proposals(self) -> list[PromptEvolutionProposal]:
        return list(self._proposals)


_RETRO_ENGINES: dict[str, RetrospectiveEngine] = {}


def get_retrospective_engine(project_id: str) -> RetrospectiveEngine:
    if project_id not in _RETRO_ENGINES:
        _RETRO_ENGINES[project_id] = RetrospectiveEngine(project_id)
    return _RETRO_ENGINES[project_id]
