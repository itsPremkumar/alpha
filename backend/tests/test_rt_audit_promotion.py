"""Audit regressions for ``alpha.subagents.promotion``.

Two defects in the promotion sink:

1. **Path traversal / arbitrary file write.** ``promote_to_specialist_bot``
   interpolates the caller-supplied ``bot_name`` straight into a filesystem
   path (``bots_dir / f"{clean_name}.json"``) after only ``.lower().strip()``,
   which does not remove ``/``, ``\\`` or ``..``. ``bot_name`` arrives from the
   model-facing ``subagent_control(action="promote", bot_name=...)`` tool, so a
   model can write a JSON profile outside ``bots/profiles`` whose body is
   partly model-chosen. ``_save_metric`` has the same shape for ``role``.

2. **Honesty.** The persisted profile's system prompt claims
   ``total_executions`` *"successful task executions"*, but
   ``total_executions`` includes every failure. A role promoted at the 0.80
   reliability floor can have failed 1 execution in 5, and the durable Bot
   profile then overstates its own track record.
"""

from __future__ import annotations

import json

import pytest

from alpha.subagents.promotion import SubagentPromotionManager


@pytest.fixture
def promotion_home(tmp_path, monkeypatch):
    """Point both the metrics store and the bots/profiles sink at *tmp_path*.

    The module reads the process home from the environment, so every spelling
    that has been used for it is set here: the fixture must keep steering the
    sink at *tmp_path* whichever name the tree currently reads, and the
    assertions below are about the sink's behaviour, not the variable's name.
    """
    for variable in ("ALPHA_HOME", "AGENT_WORKSPACE_HOME"):
        monkeypatch.setenv(variable, str(tmp_path))
    return tmp_path


def _profile_text(profile: dict) -> str:
    return profile["system_prompt"]


# ---------------------------------------------------------------------------
# 1. Path traversal: the bot-name component must stay inside bots/profiles
# ---------------------------------------------------------------------------


def test_promote_refuses_a_bot_name_that_escapes_the_profiles_directory(promotion_home):
    manager = SubagentPromotionManager(storage_dir=promotion_home / "subagents" / "metrics")
    profiles = promotion_home / "bots" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="bot_name"):
        manager.promote_to_specialist_bot("critic", bot_name="../../escaped")

    # Nothing landed outside the profiles directory.
    assert not (promotion_home / "escaped.json").exists()
    assert not (promotion_home / "bots" / "escaped.json").exists()
    assert list(promotion_home.rglob("escaped.json")) == []


def test_promote_refuses_a_windows_style_escaping_bot_name(promotion_home):
    manager = SubagentPromotionManager(storage_dir=promotion_home / "subagents" / "metrics")

    with pytest.raises(ValueError, match="bot_name"):
        manager.promote_to_specialist_bot("critic", bot_name="..\\..\\escaped")

    assert list(promotion_home.rglob("escaped.json")) == []


def test_promote_refuses_a_role_that_escapes_the_metrics_directory(promotion_home):
    manager = SubagentPromotionManager(storage_dir=promotion_home / "subagents" / "metrics")

    with pytest.raises(ValueError, match="role"):
        manager.record_execution("../escaped", success=True)

    assert not (promotion_home / "subagents" / "escaped.json").exists()
    assert list(promotion_home.rglob("escaped.json")) == []


def test_promote_still_writes_a_legitimate_profile_inside_the_directory(promotion_home):
    manager = SubagentPromotionManager(storage_dir=promotion_home / "subagents" / "metrics")
    profiles = promotion_home / "bots" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)

    profile = manager.promote_to_specialist_bot("postgres_optimizer", bot_name="bot-postgres-optimizer")

    written = profiles / "bot-postgres-optimizer.json"
    assert written.exists()
    assert json.loads(written.read_text(encoding="utf-8")) == profile


# ---------------------------------------------------------------------------
# 2. Honesty: the durable profile must not count failures as successes
# ---------------------------------------------------------------------------


def test_promoted_profile_does_not_count_failed_executions_as_successful(promotion_home):
    manager = SubagentPromotionManager(storage_dir=promotion_home / "subagents" / "metrics")
    (promotion_home / "bots" / "profiles").mkdir(parents=True, exist_ok=True)

    for _ in range(3):
        manager.record_execution("flaky_optimizer", success=True)
    manager.record_execution("flaky_optimizer", success=False)

    metric = manager._metrics["flaky_optimizer"]
    assert metric.total_executions == 4
    assert metric.success_count == 3

    prompt = _profile_text(manager.promote_to_specialist_bot("flaky_optimizer"))

    assert "4 successful task executions" not in prompt
    assert "3 successful task executions" in prompt


def test_recorded_failures_are_disclosed_in_the_promoted_profile(promotion_home):
    manager = SubagentPromotionManager(storage_dir=promotion_home / "subagents" / "metrics")
    (promotion_home / "bots" / "profiles").mkdir(parents=True, exist_ok=True)

    for _ in range(4):
        manager.record_execution("mostly_good", success=True)
    manager.record_execution("mostly_good", success=False)

    profile = manager.promote_to_specialist_bot("mostly_good")

    assert profile["total_executions"] == 5
    assert profile["successful_executions"] == 4
    assert profile["failed_executions"] == 1
    assert "4/5" in _profile_text(profile)


def test_a_perfect_role_still_reports_its_own_total(promotion_home):
    manager = SubagentPromotionManager(storage_dir=promotion_home / "subagents" / "metrics")
    (promotion_home / "bots" / "profiles").mkdir(parents=True, exist_ok=True)
    for _ in range(5):
        manager.record_execution("clean_optimizer", success=True)

    profile = manager.promote_to_specialist_bot("clean_optimizer")

    assert "5/5" in _profile_text(profile)
    assert profile["failed_executions"] == 0


def test_unknown_role_promotes_without_inventing_history(promotion_home):
    manager = SubagentPromotionManager(storage_dir=promotion_home / "subagents" / "metrics")
    (promotion_home / "bots" / "profiles").mkdir(parents=True, exist_ok=True)

    profile = manager.promote_to_specialist_bot("never_recorded")

    assert profile["total_executions"] == 0
    assert profile["successful_executions"] == 0
    assert profile["failed_executions"] == 0
    assert "no recorded executions" in _profile_text(profile)


def test_promotion_is_idempotent_for_a_legitimate_name(promotion_home):
    manager = SubagentPromotionManager(storage_dir=promotion_home / "subagents" / "metrics")
    profiles = promotion_home / "bots" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)

    first = manager.promote_to_specialist_bot("critic", bot_name="bot-critic")
    second = manager.promote_to_specialist_bot("critic", bot_name="BOT-Critic ")

    assert first["name"] == second["name"] == "bot-critic"
    assert (profiles / "bot-critic.json").exists()


def test_sanitized_default_name_is_used_when_no_bot_name_is_supplied(promotion_home):
    manager = SubagentPromotionManager(storage_dir=promotion_home / "subagents" / "metrics")
    profiles = promotion_home / "bots" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)

    profile = manager.promote_to_specialist_bot("Postgres_Optimizer")

    assert profile["name"] == "bot-postgres-optimizer"
    assert (profiles / "bot-postgres-optimizer.json").exists()
