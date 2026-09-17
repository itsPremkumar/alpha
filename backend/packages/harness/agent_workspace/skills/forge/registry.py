from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .models import Skill

logger = logging.getLogger("agent_workspace.skills.forge.registry")


class SkillRegistry:
    """Persistent in-memory & on-disk registry of forged, tested skills."""

    def __init__(self) -> None:
        self.skills: dict[str, Skill] = {}
        self.seen_hashes: dict[str, str] = {}  # hash -> skill_name

    def register(self, skill: Skill, min_pass_rate: float = 0.80) -> bool:
        """
        Registers a skill. Rejects if pass rate < min_pass_rate (when tests have been run)
        or if an identical skill hash already exists.
        """
        h = skill.hash()
        if h in self.seen_hashes and self.seen_hashes[h] != skill.name:
            logger.info("Skill '%s' duplicate of '%s' (hash=%s)", skill.name, self.seen_hashes[h], h)
            return False

        if skill.test_count > 0 and skill.test_pass_rate < min_pass_rate:
            logger.warning(
                "Skill '%s' rejected: pass rate %.2f < %.2f",
                skill.name,
                skill.test_pass_rate,
                min_pass_rate,
            )
            return False

        self.skills[skill.name] = skill
        self.seen_hashes[h] = skill.name
        return True

    def test_skill(
        self,
        skill: Skill,
        test_cases: list[dict[str, Any]],
        executor: Callable[[str, dict[str, Any]], bool] | None = None,
    ) -> float:
        """
        Runs synthetic test cases against a skill.
        Updates skill.test_pass_rate and skill.test_count.
        """
        if not test_cases:
            return 1.0

        passed = 0
        for i, tc in enumerate(test_cases):
            params = tc.get("params", {})
            try:
                rendered = skill.instantiate(**params)
                success = executor(rendered, tc) if executor else True
                if success:
                    passed += 1
            except Exception as e:
                logger.debug("Test %d for skill %s failed: %s", i, skill.name, e)

        skill.test_count = len(test_cases)
        skill.test_pass_rate = round(passed / len(test_cases), 3)
        return skill.test_pass_rate

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def export_to_dir(self, output_dir: str | Path) -> list[str]:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        created_paths: list[str] = []
        for s in self.skills.values():
            skill_folder = out / s.name
            skill_folder.mkdir(parents=True, exist_ok=True)
            skill_file = skill_folder / "SKILL.md"
            skill_file.write_text(s.to_skill_md(), encoding="utf-8")
            created_paths.append(str(skill_file))
        return created_paths

    def stats(self) -> dict[str, Any]:
        return {
            "total_skills": len(self.skills),
            "skill_names": sorted(list(self.skills.keys())),
        }
