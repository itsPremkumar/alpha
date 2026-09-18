"""Tests for Autonomous Skill Curator and Learning Graph."""

import tempfile
import time
from pathlib import Path
from agent_workspace.learning.autonomous_curator import AutonomousSkillCurator, SkillLifecycleState
from agent_workspace.learning.autonomous_learning_graph import AutonomousLearningGraph


def test_autonomous_skill_curator_lifecycle_transitions():
    with tempfile.TemporaryDirectory() as tmpdir:
        curator = AutonomousSkillCurator(
            skills_dir=tmpdir,
            interval_hours=1.0,
            min_idle_hours=0.5,
            stale_after_days=14.0,
            archive_after_days=30.0,
        )

        s1 = curator.register_skill("web_crawler", "def crawl(): pass")
        s2 = curator.register_skill("core_compiler", "def compile(): pass", pinned=True)

        assert s1.state == SkillLifecycleState.ACTIVE
        assert s2.state == SkillLifecycleState.PINNED

        now = time.time()
        s1.last_used_at = now - (15 * 86400)
        s2.last_used_at = now - (15 * 86400)

        transitions = curator.apply_automatic_transitions(current_time=now)
        assert len(transitions) == 1
        assert transitions[0]["skill"] == "web_crawler"
        assert transitions[0]["to"] == "stale"
        assert s1.state == SkillLifecycleState.STALE
        assert s2.state == SkillLifecycleState.PINNED

        s1.last_used_at = now - (35 * 86400)
        transitions2 = curator.apply_automatic_transitions(current_time=now)
        assert len(transitions2) == 1
        assert transitions2[0]["to"] == "archived"
        assert s1.state == SkillLifecycleState.ARCHIVED


def test_autonomous_skill_curator_error_hotspot_self_repair():
    with tempfile.TemporaryDirectory() as tmpdir:
        curator = AutonomousSkillCurator(skills_dir=tmpdir)

        buggy_code = "def process(data):\n    val = data['token']\n    return val\n"
        curator.register_skill("token_processor", buggy_code)

        curator.record_usage("token_processor", success=False, error="KeyError: 'token'")
        curator.record_usage("token_processor", success=False, error="KeyError: 'token'")
        curator.record_usage("token_processor", success=False, error="KeyError: 'token'")

        skill = curator.get_skill("token_processor")
        assert skill.error_rate == 1.0

        patches = curator.detect_and_patch_error_hotspots()
        assert len(patches) == 1
        assert patches[0]["skill"] == "token_processor"
        assert ".get(" in skill.content


def test_autonomous_learning_graph_linking_and_decay():
    graph = AutonomousLearningGraph()

    graph.add_node("skill:git_patch", "skill", "Git Patch Repair", "Surgically patch git repos using diffs", use_count=10)
    graph.add_node("skill:web_fetch", "skill", "Web Fetch", "HTTP requests and URL extraction", use_count=0)

    mem_id = "mem:patch_pref"
    graph.add_node(mem_id, "memory", "Developer uses git diffs", "Always create unified git diffs when patching code")

    edges = graph.link_memory_to_skills(mem_id, "Always create unified git diffs when patching code", threshold=0.1)
    assert len(edges) >= 1
    assert any(e.target_id == "skill:git_patch" for e in edges)

    graph.nodes["skill:web_fetch"].timestamp = time.time() - (40 * 86400)
    pruned = graph.prune_decayed_nodes(max_age_seconds=30 * 86400, min_use_count=1)
    assert pruned == 1
    assert "skill:web_fetch" not in graph.nodes
