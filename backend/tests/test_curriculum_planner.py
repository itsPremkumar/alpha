
from alpha.learning.curriculum import (
    CapabilityGap,
    Curriculum,
    CurriculumBuilder,
)


def test_capability_gap_priority_calculation():
    # Gap 1: Large gap (0.9 - 0.4 = 0.5), Easy difficulty (0.2) -> Priority = 0.5 / 0.2 = 2.5
    gap1 = CapabilityGap(capability="regex_parsing", current_score=0.4, target_score=0.9, difficulty=0.2)
    assert gap1.gap_size == 0.5
    assert gap1.priority == 2.5

    # Gap 2: Smaller gap (0.9 - 0.7 = 0.2), Hard difficulty (0.8) -> Priority = 0.2 / 0.8 = 0.25
    gap2 = CapabilityGap(capability="distributed_consensus", current_score=0.7, target_score=0.9, difficulty=0.8)
    assert gap2.gap_size == 0.2
    assert gap2.priority == 0.25

    # Gap 1 should have much higher priority than Gap 2
    assert gap1.priority > gap2.priority


def test_curriculum_builder_discovery_and_scheduling():
    builder = CurriculumBuilder(default_target_score=0.90)

    perf_map = {
        "unit_test_authoring": 0.95,  # Above target -> should NOT be a gap
        "ast_refactoring": 0.60,      # Gap = 0.30, diff = 0.3 -> prio = 1.0
        "memory_leak_detection": 0.40, # Gap = 0.50, diff = 0.25 -> prio = 2.0
    }
    difficulties = {
        "ast_refactoring": 0.3,
        "memory_leak_detection": 0.25,
    }

    curriculum = builder.build_curriculum(perf_map, difficulties=difficulties)
    assert isinstance(curriculum, Curriculum)
    assert len(curriculum.gaps) == 2

    # Memory leak detection has higher priority -> must be first in gaps
    assert curriculum.gaps[0].capability == "memory_leak_detection"
    assert curriculum.gaps[1].capability == "ast_refactoring"

    # Tasks generated: 2 tasks per gap -> 4 tasks total
    assert len(curriculum.tasks) == 4
    task_caps = [t.capability for t in curriculum.tasks]
    assert "memory_leak_detection" in task_caps
    assert "ast_refactoring" in task_caps

    d = curriculum.to_dict()
    assert "curriculum_id" in d
    assert d["total_tasks"] == 4
