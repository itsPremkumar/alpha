"""Procedural Skill Memory Engine.

Indexes learned skills, operational recipes, execution playbooks,
preconditions, code routines, and success/failure statistics.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from alpha.memory.cognitive.models import ProceduralSkill
from alpha.memory.cognitive.skill_lifecycle import (
    SkillLifecycle,
    SkillVerdict,
    evaluate,
    rank_for_recall,
    retirement_priority,
    transition,
)

logger = logging.getLogger(__name__)


class ProceduralSkillMemory:
    """Stores reusable agent skills, procedural workflows, and execution heuristics."""

    def __init__(self, max_skills: int = 500) -> None:
        self.max_skills = max_skills
        self._skills: dict[str, ProceduralSkill] = {}

    def register_skill(
        self,
        name: str,
        description: str,
        trigger_pattern: str,
        preconditions: list[str] | None = None,
        steps: list[str] | None = None,
        code_snippet: str = "",
        postconditions: list[str] | None = None,
        skill_id: str | None = None,
        success_count: int = 0,
        failure_count: int = 0,
        last_executed_at: float = 0.0,
        failure_reasons: list[str] | None = None,
        created_at: float | None = None,
        lifecycle: str = "proposed",
        lifecycle_reason: str = "",
    ) -> ProceduralSkill:
        """Register or update a procedural skill."""
        now = time.time()
        c_name = name.strip()

        # Check existing by skill_id or name
        existing = None
        if skill_id and skill_id in self._skills:
            existing = self._skills[skill_id]
        elif not skill_id:
            for s in self._skills.values():
                if s.name.strip().lower() == c_name.lower():
                    existing = s
                    break

        if existing:
            existing.name = c_name
            existing.description = description.strip()
            existing.trigger_pattern = trigger_pattern.strip()
            if preconditions:
                existing.preconditions = preconditions
            if steps:
                existing.steps = steps
            if code_snippet:
                existing.code_snippet = code_snippet.strip()
            if postconditions:
                existing.postconditions = postconditions
            if success_count:
                existing.success_count = max(existing.success_count, success_count)
            if failure_count:
                existing.failure_count = max(existing.failure_count, failure_count)
            if last_executed_at:
                existing.last_executed_at = max(existing.last_executed_at, last_executed_at)
            if failure_reasons:
                for r in failure_reasons:
                    if r not in existing.failure_reasons:
                        existing.failure_reasons.append(r)
            restored_lifecycle = (lifecycle or "proposed").strip().lower()
            if restored_lifecycle and restored_lifecycle != "retired":
                existing.lifecycle = restored_lifecycle
            if (lifecycle_reason or "").strip():
                existing.lifecycle_reason = lifecycle_reason.strip()
            return existing

        skill = ProceduralSkill(
            name=c_name,
            description=description.strip(),
            trigger_pattern=trigger_pattern.strip(),
            preconditions=preconditions or [],
            steps=steps or [],
            code_snippet=code_snippet.strip(),
            postconditions=postconditions or [],
            success_count=success_count,
            failure_count=failure_count,
            last_executed_at=last_executed_at,
            failure_reasons=failure_reasons or [],
            created_at=created_at or now,
            lifecycle=lifecycle or "proposed",
            lifecycle_reason=lifecycle_reason or "",
        )
        if skill_id:
            skill.skill_id = skill_id

        self._skills[skill.skill_id] = skill
        self._enforce_capacity()
        return skill

    def delete_skill(self, skill_id: str) -> bool:
        """Delete a skill by ID."""
        if skill_id in self._skills:
            del self._skills[skill_id]
            return True
        return False

    def _enforce_capacity(self) -> None:
        if len(self._skills) <= self.max_skills:
            return
        # Weakest evidence first, and a skill that is the only one covering its
        # own pattern is held to the end — dropping it removes a capability
        # rather than freeing a slot. The old ascending-`success_rate` sort made
        # an untested new skill tie with a proven one and broke the tie on
        # `created_at`, which could evict a well-tested skill.
        order = retirement_priority(self._skills.values(), patterns=[s.name for s in self._skills.values()])
        excess = len(self._skills) - self.max_skills
        for skill in order[:excess]:
            del self._skills[skill.skill_id]

    def get_skill(self, skill_id: str) -> ProceduralSkill | None:
        return self._skills.get(skill_id)

    def record_outcome(self, skill_id: str, success: bool, reason: str | None = None) -> bool:
        """Reinforce or penalize skill performance."""
        skill = self._skills.get(skill_id)
        if not skill:
            return False

        skill.last_executed_at = time.time()
        if success:
            skill.success_count += 1
        else:
            skill.failure_count += 1
            if reason and reason not in skill.failure_reasons:
                skill.failure_reasons.append(reason)

        # The first recorded outcome is what takes a skill out of "proposed".
        # Promotion and demotion are NOT auto-applied here: they are decisions
        # with a sample-size floor, and a caller that wants them should call
        # `evaluate` + `transition` so the reason is recorded on the skill.
        if skill.lifecycle == SkillLifecycle.PROPOSED.value:
            try:
                transition(skill, SkillLifecycle.VERIFIED, reason=f"first recorded outcome ({'success' if success else 'failure'})")
            except ValueError:  # pragma: no cover - a hand-set illegal state must not break recording
                logger.warning("skill %s carries lifecycle %r that cannot move to verified", skill_id, skill.lifecycle)
        return True

    def rank_for_recall(self, context_text: str, limit: int = 5) -> list[tuple[ProceduralSkill, float]]:
        """Recall skills scored by ``relevance * strength``.

        Same relevance computation as :meth:`find_matching_skills`, but the
        evidence weight is the smoothed effectiveness instead of
        ``(0.5 + 0.5 * success_rate)``. The difference is what happens to a skill
        nobody has ever run: it is recalled at the prior (middle of the
        distribution) rather than at full weight.
        """
        text_lower = context_text.lower()
        scored: list[tuple[ProceduralSkill, float]] = []

        for skill in self._skills.values():
            score = 0.0
            try:
                if re.search(skill.trigger_pattern, context_text, re.IGNORECASE):
                    score = 0.85
            except re.error:
                pass

            pattern_tokens = set(re.findall(r"\w+", skill.trigger_pattern.lower()))
            desc_tokens = set(re.findall(r"\w+", skill.description.lower()))
            all_tokens = pattern_tokens | desc_tokens
            if all_tokens:
                matched_tokens = sum(1 for t in all_tokens if t in text_lower)
                overlap_ratio = matched_tokens / len(all_tokens)
                score = max(score, overlap_ratio)

            if score > 0.15:
                try:
                    scored.append((skill, rank_for_recall(skill, score)))
                except ValueError:  # pragma: no cover - score is bounded above by construction
                    logger.warning("skill %s produced an out-of-range relevance score %.3f", skill.skill_id, score)

        scored.sort(key=lambda x: (x[1], x[0].evidence_count), reverse=True)
        return scored[:limit]

    def evaluate(self, skill_id: str, **kwargs: Any) -> SkillVerdict:
        """The verdict the measurements support for one skill, plus its reason."""
        skill = self._skills.get(skill_id)
        if skill is None:
            raise KeyError(f"no procedural skill with id {skill_id!r}")
        return evaluate(skill, **kwargs)

    def lifecycle_summary(self) -> dict[str, Any]:
        """Counts by *derived* lifecycle state, plus the unproven skill names.

        Derived, not stored: a skill recorded as ``promoted`` that has since
        failed every run is counted where its evidence puts it, and the stored
        value stays on the skill for anyone who wants to see the disagreement.
        """
        counts = {state.value: 0 for state in SkillLifecycle}
        unproven: list[str] = []
        for skill in self._skills.values():
            verdict = evaluate(skill)
            counts[verdict.lifecycle.value] += 1
            if verdict.evidence_count == 0:
                unproven.append(skill.name)
        return {
            "total": len(self._skills),
            **counts,
            "unproven_skills": sorted(unproven),
        }

    def find_matching_skills(self, context_text: str, limit: int = 5) -> list[tuple[ProceduralSkill, float]]:
        """Find matching skills using trigger regex or token overlap."""
        text_lower = context_text.lower()
        scored: list[tuple[ProceduralSkill, float]] = []

        for skill in self._skills.values():
            score = 0.0
            # Check trigger pattern regex
            try:
                if re.search(skill.trigger_pattern, context_text, re.IGNORECASE):
                    score = 0.85
            except re.error:
                pass

            # Check keyword overlap
            pattern_tokens = set(re.findall(r"\w+", skill.trigger_pattern.lower()))
            desc_tokens = set(re.findall(r"\w+", skill.description.lower()))
            all_tokens = pattern_tokens | desc_tokens
            if all_tokens:
                matched_tokens = sum(1 for t in all_tokens if t in text_lower)
                overlap_ratio = matched_tokens / len(all_tokens)
                score = max(score, overlap_ratio)

            if score > 0.15:
                # Modulate by skill success rate
                adjusted_score = score * (0.5 + 0.5 * skill.success_rate)
                scored.append((skill, adjusted_score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def list_skills(self, limit: int = 50) -> list[ProceduralSkill]:
        skills = list(self._skills.values())
        skills.sort(key=lambda s: (s.success_rate, s.success_count), reverse=True)
        return skills[:limit]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_skills": len(self._skills),
            "skills": [s.to_dict() for s in self.list_skills(limit=50)],
        }
