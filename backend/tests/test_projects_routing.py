"""Workload routing: ranking, selection, decay, feedback loop."""

from __future__ import annotations

import pytest

from alpha.projects.routing import decayed_reputation, rank_candidates, record_routing_feedback, select_agent


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    import alpha.bots.registry as bot_reg

    monkeypatch.setattr(bot_reg, "_global_registry", None)
    monkeypatch.setattr(bot_reg, "_global_registry_path", None)
    yield


def test_rank_prefers_capability_match():
    ranked = rank_candidates(["python", "coding"])
    assert ranked
    assert ranked[0].bot_name == "coder"
    assert ranked[0].capability_match > 0


def test_unknown_capability_yields_nothing():
    assert rank_candidates(["lie-flat-optics-xyz"]) == []
    assert select_agent(["lie-flat-optics-xyz"]) is None


def test_excluded_bots_are_skipped():
    ranked = rank_candidates(["python", "coding"], exclude={"coder"})
    assert all(c.bot_name != "coder" for c in ranked)


def test_selection_records_feedback_and_moves_reputation():
    from alpha.bots.registry import get_bot_registry

    record_routing_feedback("coder", success=False, duration_sec=1.0)
    lowered = get_bot_registry().get_bot("coder").reputation_score
    assert lowered < 1.0
    record_routing_feedback("coder", success=True, duration_sec=1.0, quality_score=0.5)
    raised = get_bot_registry().get_bot("coder").reputation_score
    assert raised > lowered


def test_reputation_decay_pulls_toward_prior():
    assert decayed_reputation(1.0, None) == 1.0
    assert decayed_reputation(0.9, "not-a-date") == 0.9
    fresh = decayed_reputation(1.0, "2026-09-15T00:00:00+00:00")
    assert 0.75 < fresh <= 1.0


def test_busy_members_raise_load():
    from types import SimpleNamespace

    memberships = [SimpleNamespace(bot_name="coder", current_task_id="t-1"), SimpleNamespace(bot_name="coder", current_task_id="t-2")]
    ranked = rank_candidates(["python"], memberships=memberships)
    coder = next(c for c in ranked if c.bot_name == "coder")
    assert coder.load > 0
