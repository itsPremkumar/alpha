"""S1 tests: skill-creation nudge cadence, hydration, and prompt honesty.

Pins Hermes turn_finalizer.py:675-682 fire gate + turn_context.py:701-706
hydration, and the honesty contract: real counts only, literal
"no usage evidence recorded" for empty stats, no claimed improvements.
"""

import pytest

from alpha.skills.creation_nudge import (
    SkillNudgeState,
    build_skill_review_prompt,
    hydrate,
    should_review_skills,
)
from alpha.skills.usage import SkillUsage, SkillUsageTracker


@pytest.fixture(autouse=True)
def _workspace_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    return tmp_path


def test_should_review_skills_gate_matrix():
    assert should_review_skills(3, 3, skill_tool_available=True) is True
    assert should_review_skills(3, 2, skill_tool_available=True) is False
    assert should_review_skills(3, 4, skill_tool_available=True) is True
    assert should_review_skills(3, 9, skill_tool_available=False) is False
    assert should_review_skills(0, 9, skill_tool_available=True) is False
    assert should_review_skills(-1, 9, skill_tool_available=True) is False


def test_cadence_fires_at_exactly_n_and_resets_on_fire():
    state = SkillNudgeState(interval=3, iters_since_skill=0)
    assert state.tick(1) is False
    assert state.iters_since_skill == 1
    assert state.tick(1) is False
    assert state.iters_since_skill == 2
    assert state.tick(1) is True  # fires at exactly N
    assert state.iters_since_skill == 0  # reset on fire
    # second cycle needs the full cadence again
    assert state.tick(1) is False
    assert state.tick(1) is False
    assert state.tick(1) is True
    assert state.iters_since_skill == 0


def test_multi_iteration_tick_reaches_threshold_once():
    state = SkillNudgeState(interval=5, iters_since_skill=0)
    assert state.tick(2) is False
    assert state.iters_since_skill == 2
    assert state.tick(3) is True
    assert state.iters_since_skill == 0
    # negative increments are ignored (iterations cannot run backwards)
    assert state.tick(-4) is False
    assert state.iters_since_skill == 0


def test_disabled_at_zero_or_negative_interval_never_fires():
    for interval in (0, -3):
        state = SkillNudgeState(interval=interval)
        for _ in range(5):
            assert state.tick(1) is False
        assert state.iters_since_skill == 5  # count kept, not zeroed
        assert should_review_skills(interval, 99, skill_tool_available=True) is False


def test_no_fire_without_skill_tool():
    state = SkillNudgeState(interval=2, skill_tool_available=False)
    assert state.tick(1) is False
    assert state.tick(1) is False  # cadence reached but tool missing
    assert state.iters_since_skill == 2  # count KEPT, never silently reset
    state.skill_tool_available = True
    assert state.tick(1) is True  # accumulated count fires once tool returns
    assert state.iters_since_skill == 0


def test_explicit_reset_drops_count_without_firing():
    state = SkillNudgeState(interval=2, iters_since_skill=7)
    state.reset()
    assert state.iters_since_skill == 0
    assert state.tick(1) is False


def test_hydrate_resume_modulo():
    assert hydrate(7, 3).iters_since_skill == 1
    assert hydrate(4, 3).iters_since_skill == 1
    assert hydrate(3, 3).iters_since_skill == 0
    assert hydrate(0, 3).iters_since_skill == 0
    assert hydrate(-5, 3).iters_since_skill == 0
    zero = hydrate(5, 0)
    assert zero.interval == 0
    assert zero.iters_since_skill == 0
    resumed = hydrate(7, 3)
    assert resumed.interval == 3
    # resumed counter continues the cadence: fires after 2 more single-iter ticks
    assert resumed.tick(1) is False
    assert resumed.tick(1) is True
    assert resumed.iters_since_skill == 0


def test_prompt_empty_stats_honest_unknown_and_no_numbers():
    prompt = build_skill_review_prompt([], None)
    assert "no usage evidence recorded" in prompt
    assert not any(ch.isdigit() for ch in prompt)  # no fabricated numbers at all
    lowered = prompt.lower()
    for banned in ("improve", "improved", "improvement", "better", "%"):
        assert banned not in lowered


def test_prompt_real_counts_only():
    rows = [SkillUsage(name="pdf-tools", uses=3), SkillUsage(name="csv-tools", uses=0)]
    prompt = build_skill_review_prompt(["trace://a", "trace://b"], rows)
    assert "2 experience reference(s) attached" in prompt
    assert "pdf-tools: 3 recorded use(s)" in prompt
    assert "csv-tools: 0 recorded use(s)" in prompt  # real zero: the row exists
    assert "no usage evidence recorded" not in prompt
    assert "improv" not in prompt.lower()


def test_prompt_accepts_mapping_single_row_or_empty_sequence():
    mapped = build_skill_review_prompt(["x"], {"alpha-skill": 5})
    assert "alpha-skill: 5 recorded use(s)" in mapped
    single = build_skill_review_prompt([], SkillUsage(name="solo-skill", uses=2))
    assert "solo-skill: 2 recorded use(s)" in single
    assert "no usage evidence recorded" in build_skill_review_prompt(["x"], [])
    assert "no experience references attached" in build_skill_review_prompt([], None)


def test_prompt_reads_real_tracker_stats(tmp_path):
    tracker = SkillUsageTracker(usage_file=tmp_path / "usage.json")
    for _ in range(3):
        tracker.record_use("demo-skill", created_by="agent")
    prompt = build_skill_review_prompt(["exp-1"], tracker.all_stats())
    assert "demo-skill: 3 recorded use(s)" in prompt
    assert "1 experience reference(s) attached" in prompt
