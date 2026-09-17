from __future__ import annotations

import uuid

from .models import CapabilityGap, Curriculum, PracticeTask


class CurriculumBuilder:
    """
    Discovers capability gaps, estimates learning difficulty,
    prioritizes by gap/difficulty, and synthesizes structured practice curricula.
    """

    def __init__(self, default_target_score: float = 0.90) -> None:
        self.default_target_score = default_target_score

    def discover_gaps(
        self,
        performance_map: dict[str, float],
        targets: dict[str, float] | None = None,
        difficulties: dict[str, float] | None = None,
    ) -> list[CapabilityGap]:
        """Discovers capability gaps where current performance < target."""
        gaps: list[CapabilityGap] = []
        targets = targets or {}
        difficulties = difficulties or {}

        for cap, score in performance_map.items():
            target = targets.get(cap, self.default_target_score)
            if score < target:
                diff = difficulties.get(cap, 0.5)
                gap = CapabilityGap(
                    capability=cap,
                    current_score=score,
                    target_score=target,
                    difficulty=diff,
                    evidence=[f"Empirical benchmark score: {score:.2f} < target {target:.2f}"],
                )
                gaps.append(gap)

        # Sort gaps by priority descending (highest urgency first)
        gaps.sort(key=lambda g: g.priority, reverse=True)
        return gaps

    def generate_tasks_for_gap(self, gap: CapabilityGap) -> list[PracticeTask]:
        """Synthesizes structured practice exercises for a given capability gap."""
        cap = gap.capability
        tasks = [
            PracticeTask(
                task_id=f"pt_{cap}_{uuid.uuid4().hex[:6]}_base",
                capability=cap,
                description=f"Baseline exercise: resolve elementary {cap} edge case.",
                difficulty=round(gap.difficulty * 0.7, 2),
                expected_outcome="Pass all assertion tests with zero unhandled exceptions.",
            ),
            PracticeTask(
                task_id=f"pt_{cap}_{uuid.uuid4().hex[:6]}_adv",
                capability=cap,
                description=f"Advanced stress exercise: resolve complex, adversarial {cap} scenario.",
                difficulty=round(gap.difficulty, 2),
                expected_outcome="Pass full regression suite and formal AST verification gates.",
            ),
        ]
        return tasks

    def build_curriculum(
        self,
        performance_map: dict[str, float],
        targets: dict[str, float] | None = None,
        difficulties: dict[str, float] | None = None,
    ) -> Curriculum:
        """Constructs an ordered training curriculum targeting all detected capability gaps."""
        gaps = self.discover_gaps(performance_map, targets=targets, difficulties=difficulties)
        curriculum_id = f"curr_{uuid.uuid4().hex[:10]}"
        all_tasks: list[PracticeTask] = []

        for gap in gaps:
            tasks = self.generate_tasks_for_gap(gap)
            all_tasks.extend(tasks)

        return Curriculum(
            curriculum_id=curriculum_id,
            gaps=gaps,
            tasks=all_tasks,
        )
