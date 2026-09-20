"""B-batch: /learn + /moa + /usage commands, curator scheduling, consolidation, nudges, recall digest."""

from __future__ import annotations

import time

from alpha.commands.backend_handlers import handle_learn, handle_moa, handle_usage
from alpha.commands.registry import command_registry
from alpha.learning.nudges import build_memory_nudge, should_nudge
from alpha.skills.curator import (
    SkillCurator,
    curator_interval_hours,
    find_consolidation_candidates,
    propose_consolidations,
    should_run_curator,
)
from alpha.skills.usage import SkillUsageTracker
from alpha.tools.builtins.session_search_tool import summarize_session_hits


def test_learn_usage_and_prompt():
    err = handle_learn("")
    assert err.status == "error" and "/learn" in err.output
    ok = handle_learn("the deploy runbook in docs/")
    assert ok.status == "success" and "deploy runbook" in ok.output
    assert ok.data["action"] == "author_skill" and ok.autonomous_directives


def test_moa_usage_parsing_and_frame():
    err = handle_moa("")
    assert err.status == "error"
    ok = handle_moa("Should we migrate? --advisors reviewer,architect,tester")
    assert ok.status == "success"
    assert ok.data["advisors"] == ["reviewer", "architect", "tester"]
    assert "Final answer:" in ok.output
    defaulted = handle_moa("Just decide")
    assert defaulted.data["advisors"] == ["reviewer", "architect"]


def test_usage_handler_reports_budgets():
    res = handle_usage("")
    assert res.status == "success" and "Usage" in res.output


def test_commands_registered_in_registry():
    for cmd in ("/learn", "/moa", "/usage", "/compress"):
        assert command_registry.get(cmd) is not None, cmd


def test_curator_schedule_gating():
    now = time.time()
    assert should_run_curator(None, now=now) is True
    assert should_run_curator(now, now=now) is False
    assert should_run_curator(now - 8 * 86400.0, now=now) is True
    assert should_run_curator(now - 8 * 86400.0, now=now, paused=True) is False
    assert should_run_curator(now - 8 * 86400.0, now=now, idle_hours=0.5) is False
    assert should_run_curator(now - 8 * 86400.0, now=now, idle_hours=5.0) is True
    assert curator_interval_hours() > 0


def test_consolidation_threshold_sensitivity():
    near_dupes = {
        "a": "Deploy services with docker build push restart verify logs monitor alerts",
        "b": "Deploy services with docker build push restart verify logs monitor checks",
    }
    assert find_consolidation_candidates(near_dupes, similarity=0.5) == [["a", "b"]]
    assert find_consolidation_candidates(near_dupes, similarity=0.99) == []
    cousins = {
        "a": "Deploy services with docker containers and registries",
        "b": "Bake sourdough bread with flour water salt starter",
    }
    assert find_consolidation_candidates(cousins, similarity=0.5) == []
    assert find_consolidation_candidates({}, similarity=0.5) == []


def test_consolidation_candidates_and_proposals():
    bodies = {
        "deploy-a": "Deploy services with docker build push restart verify logs monitor alerts",
        "deploy-b": "Deploy services with docker build push restart verify logs monitor checks",
        "unrelated": "Bake sourdough bread with flour water salt starter oven steam",
    }
    groups = find_consolidation_candidates(bodies, similarity=0.5)
    assert groups == [["deploy-a", "deploy-b"]]
    made: list[tuple[str, str]] = []
    out = propose_consolidations(groups, lambda title, body: made.append((title, body)) or title)
    assert out == ["Consolidate overlapping skills: deploy-a, deploy-b"]
    assert "deploy-a" in made[0][0] and "deploy-b" in made[0][1]
    assert propose_consolidations([], lambda t, b: None) == []


def test_curator_end_to_end_with_schedule(tmp_path):
    root = tmp_path / "skills"
    (root / "dup-a").mkdir(parents=True)
    (root / "dup-a" / "SKILL.md").write_text("# A", encoding="utf-8")
    usage = SkillUsageTracker(usage_file=root / ".usage.json")
    usage.mark_created_by("dup-a", "agent")
    curator = SkillCurator(skills_root=root, usage=usage)
    assert should_run_curator(curator.state.last_run_at) is True
    changed = curator.apply_transitions(stale_after_days=0.0, archive_after_days=9999.0)
    assert changed["staled"] == ["dup-a"]


def test_memory_nudge_cooldowns():
    now = time.time()
    assert should_nudge(None, None, now=now) is False
    assert should_nudge(now - 3600.0, None, now=now) is False
    assert should_nudge(now - 3 * 3600.0, None, now=now) is True
    assert should_nudge(now - 3 * 3600.0, now - 3600.0, now=now) is False
    assert should_nudge(now - 3 * 3600.0, now - 25 * 3600.0, now=now) is True
    text = build_memory_nudge("proj-x")
    assert "persist" in text and "proj-x" in text


def test_session_digest_extractive_and_custom():
    hits = [
        {"thread_id": "t1", "run_id": "r1", "seq": 3, "snippet": "The router password is hunter2 for staging."},
        {"thread_id": "t1", "run_id": "r1", "seq": 7, "snippet": "Rotated the staging router password after the audit."},
        {"thread_id": "t2", "run_id": "r9", "seq": 1, "snippet": "Unrelated note about routers in general."},
    ]
    digest = summarize_session_hits(hits)
    assert "2 thread(s)" in digest and "hunter2" in digest
    assert summarize_session_hits([]) == "No past-session evidence to summarize."
    custom = summarize_session_hits(hits, summarizer=lambda h: "custom digest")
    assert custom == "custom digest"
    broken = summarize_session_hits(hits, summarizer=lambda h: 1 / 0)
    assert "Recall digest" in broken
    long_hits = [{"thread_id": "t", "run_id": "r", "seq": i, "snippet": "x" * 500} for i in range(30)]
    assert len(summarize_session_hits(long_hits, max_chars=500)) <= 1100
