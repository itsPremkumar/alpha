"""Procedural Skill Memory Engine.

Indexes learned skills, operational recipes, execution playbooks,
preconditions, code routines, and success/failure statistics.
"""

from __future__ import annotations

import re
import time
from typing import Any

from agent_workspace.memory.cognitive.models import ProceduralSkill


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
        sorted_keys = sorted(
            self._skills.keys(),
            key=lambda k: (self._skills[k].success_rate, self._skills[k].success_count, self._skills[k].created_at),
        )
        excess = len(self._skills) - self.max_skills
        for k in sorted_keys[:excess]:
            del self._skills[k]

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
        return True

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
