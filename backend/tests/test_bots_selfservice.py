"""Self-service profile and routine editing on ``bot_roster``.

The property under test is *"a Bot may configure itself, but may not widen
itself"*. Two actions give a Bot the ability to change its own record without
an operator in the loop — ``update_profile`` (identity/presentation) and
``routine`` (its own automation) — and both are refused the moment the edit
leaves the actor's own profile:

* a structural field (``role``, ``department``, ``reports_to``, ``skills``,
  ``capabilities``) is a *grant*, not a label: ``BotProfile.role`` is fed to
  ``ToolPermissionGate.check_permission``, so a Bot that could rewrite its own
  role could rewrite its own tool permissions. Self-edit therefore stops at
  presentation (``display_name``, ``avatar``, ``model``) and the Bot's own
  routines.
* ``actor`` is required rather than defaulted. An empty actor is treated as an
  unknown identity and refused outright, instead of silently becoming the
  operator: a default that resolves to *more* privilege is exactly the failure
  mode a self-service surface must not have.
* the routine frequency floor is enforced on the way in, so a Bot cannot
  schedule itself into a token burn that ``forge`` would have refused at birth.
"""

from __future__ import annotations

import pytest

import alpha.bots.registry as bot_reg
from alpha.bots.forge import MIN_ROUTINE_INTERVAL_MINUTES
from alpha.bots.registry import BotRegistry
from alpha.tools.builtins.bot_roster_tool import bot_roster_tool


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None
    yield
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None


@pytest.fixture()
def registry() -> BotRegistry:
    reg = bot_reg.get_bot_registry()
    bot_roster_tool.invoke(
        {
            "action": "create",
            "name": "coder",
            "role": "Backend Engineer",
            "capabilities": "python, sql",
        }
    )
    bot_roster_tool.invoke({"action": "create", "name": "researcher", "role": "Researcher"})
    return reg


def _bot(name: str):
    return bot_reg.get_bot_registry().get_bot(name)


def _routine_names(name: str) -> list[str]:
    bot = _bot(name)
    return [r.get("name") for r in (bot.routines if bot else [])]


# ---------------------------------------------------------------------------
# update_profile — actor is mandatory, never defaulted
# ---------------------------------------------------------------------------


def test_update_profile_refuses_a_caller_that_declares_no_actor(registry):
    out = bot_roster_tool.invoke({"action": "update_profile", "name": "coder", "display_name": "Zaphod"})
    assert "actor" in out.lower()
    assert _bot("coder").display_name != "Zaphod", "an unidentified caller must not have written anything"


def test_update_profile_refuses_a_missing_bot(registry):
    out = bot_roster_tool.invoke({"action": "update_profile", "name": "ghost", "actor": "ghost", "display_name": "Nobody"})
    assert out.lower().startswith("error")
    assert "ghost" in out.lower()


def test_update_profile_with_no_fields_set_lists_what_is_settable(registry):
    before = _bot("coder").version
    out = bot_roster_tool.invoke({"action": "update_profile", "name": "coder", "actor": "coder"})
    assert "display_name" in out and "avatar" in out and "model" in out
    assert out.lower().startswith("error")
    assert _bot("coder").version == before, "an empty request must not bump the profile version"


def test_a_bot_edits_its_own_presentation(registry):
    before = _bot("coder").version
    model_before = _bot("coder").model
    out = bot_roster_tool.invoke(
        {
            "action": "update_profile",
            "name": "coder",
            "actor": "coder",
            "display_name": "Zaphod Beeblebrox",
            "avatar": "🦁",
        }
    )
    bot = _bot("coder")
    assert "display_name" in out
    assert bot.display_name == "Zaphod Beeblebrox"
    assert bot.avatar == "🦁"
    assert bot.model == model_before, "model is in the capability epoch and stays leader-only"
    assert bot.version > before, "a committed edit must be visible as a new profile version"


def test_a_bot_cannot_pin_its_own_model(registry):
    """``model`` is hashed into ``capability_fingerprint()``, so it is a grant."""
    before = _bot("coder").model
    out = bot_roster_tool.invoke({"action": "update_profile", "name": "coder", "actor": "coder", "model": "union-alpha"})
    assert out.lower().startswith("error")
    assert "model" in out.lower()
    assert _bot("coder").model == before


def test_a_mixed_batch_is_refused_whole_rather_than_partly_applied(registry):
    """Presentation plus one grant is nothing applied — not half an edit."""
    before_name = _bot("coder").display_name
    out = bot_roster_tool.invoke(
        {
            "action": "update_profile",
            "name": "coder",
            "actor": "coder",
            "display_name": "Zaphod Beeblebrox",
            "role": "Chief Executive",
        }
    )
    assert out.lower().startswith("error")
    assert "role" in out.lower()
    assert _bot("coder").display_name == before_name


def test_a_bot_cannot_promote_its_own_role(registry):
    """``role`` decides tool permissions, so a Bot naming its own role is escalation."""
    before = _bot("coder").role
    out = bot_roster_tool.invoke({"action": "update_profile", "name": "coder", "actor": "coder", "role": "Chief Executive"})
    assert out.lower().startswith("error")
    assert "role" in out.lower()
    assert _bot("coder").role == before


def test_a_bot_cannot_rescope_itself_with_capabilities_or_skills(registry):
    before_caps = list(_bot("coder").capabilities)
    before_skills = list(_bot("coder").skills)
    out = bot_roster_tool.invoke(
        {
            "action": "update_profile",
            "name": "coder",
            "actor": "coder",
            "capabilities": "python, sql, repository_mutate",
            "skills": "deploy",
        }
    )
    assert out.lower().startswith("error")
    assert _bot("coder").capabilities == before_caps
    assert _bot("coder").skills == before_skills


def test_a_bot_cannot_edit_a_teammate(registry):
    before = _bot("researcher").display_name
    out = bot_roster_tool.invoke({"action": "update_profile", "name": "researcher", "actor": "coder", "display_name": "Owned"})
    assert out.lower().startswith("error")
    assert _bot("researcher").display_name == before


def test_a_leader_edits_another_bots_structure(registry):
    before = _bot("coder").version
    out = bot_roster_tool.invoke(
        {
            "action": "update_profile",
            "name": "coder",
            "actor": "alpha",
            "role": "Principal Engineer",
            "department": "platform",
            "reports_to": "researcher",
            "capabilities": "python, sql, fastapi",
        }
    )
    bot = _bot("coder")
    assert "role" in out
    assert bot.role == "Principal Engineer"
    assert bot.department == "platform"
    assert bot.reports_to == "researcher"
    assert bot.capabilities == ["python", "sql", "fastapi"]
    assert bot.version > before


def test_a_leader_edit_changes_the_capability_epoch(registry):
    """``skills`` is hashed into the epoch — which is why it is leader-only."""
    epoch_before = _bot("coder").capability_fingerprint()
    out = bot_roster_tool.invoke({"action": "update_profile", "name": "coder", "actor": "lead", "skills": "python, sql, rust"})
    assert not out.lower().startswith("error")
    assert _bot("coder").capability_fingerprint() != epoch_before


def test_capabilities_stay_descriptive_not_epoch_bearing(registry):
    """Pins the split: ``capabilities`` is stored but not hashed; ``skills`` is both."""
    before = list(_bot("coder").capabilities)
    bot_roster_tool.invoke({"action": "update_profile", "name": "coder", "actor": "alpha", "capabilities": "python, sql, rust"})
    assert before == ["python", "sql"]
    assert _bot("coder").capabilities == ["python", "sql", "rust"]


def test_update_profile_rejects_an_unknown_actor_set(registry):
    """`actor` is not a free pass: an unrecognised identity is never a leader."""
    out = bot_roster_tool.invoke({"action": "update_profile", "name": "researcher", "actor": "root", "display_name": "Owned"})
    assert out.lower().startswith("error")


# ---------------------------------------------------------------------------
# routine — a Bot schedules its own automation
# ---------------------------------------------------------------------------


def test_routine_without_a_name_lists(registry):
    bot_roster_tool.invoke(
        {
            "action": "routine",
            "name": "coder",
            "actor": "coder",
            "routine": "standup",
            "schedule": f"every {MIN_ROUTINE_INTERVAL_MINUTES + 30} minutes",
            "content": "Summarize yesterday's commits.",
        }
    )
    out = bot_roster_tool.invoke({"action": "routine", "name": "coder"})
    assert "standup" in out


def test_a_bot_adds_its_own_routine(registry):
    out = bot_roster_tool.invoke(
        {
            "action": "routine",
            "name": "coder",
            "actor": "coder",
            "routine": "standup",
            "schedule": "every 60 minutes",
            "content": "Summarize yesterday's commits.",
        }
    )
    assert "standup" in out.lower()
    assert "standup" in _routine_names("coder")


def test_a_too_frequent_routine_is_refused_and_not_added(registry):
    """The floor that guards a forge must guard a later self-service write."""
    out = bot_roster_tool.invoke(
        {
            "action": "routine",
            "name": "coder",
            "actor": "coder",
            "routine": "spin",
            "schedule": "every 5 minutes",
            "content": "Check for new work.",
        }
    )
    assert out.lower().startswith("error")
    assert "spin" not in _routine_names("coder")


def test_the_operator_override_unlocks_a_frequent_routine(registry):
    out = bot_roster_tool.invoke(
        {
            "action": "routine",
            "name": "coder",
            "actor": "alpha",
            "routine": "spin",
            "schedule": "every 5 minutes",
            "content": "Check for new work.",
            "allow_frequent": True,
        }
    )
    assert not out.lower().startswith("error")
    assert "spin" in _routine_names("coder")


def test_a_bot_removes_its_own_routine(registry):
    bot_roster_tool.invoke(
        {
            "action": "routine",
            "name": "coder",
            "actor": "coder",
            "routine": "standup",
            "schedule": "every 60 minutes",
            "content": "Summarize yesterday's commits.",
        }
    )
    out = bot_roster_tool.invoke({"action": "routine", "name": "coder", "actor": "coder", "routine": "standup"})
    assert not out.lower().startswith("error")
    assert "standup" not in _routine_names("coder")


def test_a_mutation_without_an_actor_is_refused(registry):
    out = bot_roster_tool.invoke({"action": "routine", "name": "coder", "routine": "standup", "schedule": "every 60 minutes", "content": "x"})
    assert out.lower().startswith("error")
    assert "actor" in out.lower()
    assert _routine_names("coder") == []


def test_a_bot_cannot_install_automation_on_a_teammate(registry):
    out = bot_roster_tool.invoke(
        {
            "action": "routine",
            "name": "researcher",
            "actor": "coder",
            "routine": "spy",
            "schedule": "every 60 minutes",
            "content": "Watch the roster.",
        }
    )
    assert out.lower().startswith("error")
    assert "spy" not in _routine_names("researcher")


def test_a_leader_installs_automation_on_a_teammate(registry):
    out = bot_roster_tool.invoke(
        {
            "action": "routine",
            "name": "researcher",
            "actor": "alpha",
            "routine": "digest",
            "schedule": "every 120 minutes",
            "content": "Summarize new papers.",
        }
    )
    assert not out.lower().startswith("error")
    assert "digest" in _routine_names("researcher")


def test_removing_an_absent_routine_is_reported_honestly(registry):
    out = bot_roster_tool.invoke({"action": "routine", "name": "coder", "actor": "coder", "routine": "never-existed"})
    assert not out.lower().startswith("error")
    assert "never-existed" in out


def test_an_incomplete_routine_write_says_what_is_missing(registry):
    out = bot_roster_tool.invoke({"action": "routine", "name": "coder", "actor": "coder", "routine": "half", "schedule": "every 60 minutes"})
    assert out.lower().startswith("error")
    assert "content" in out.lower()
    assert "half" not in _routine_names("coder")


# ---------------------------------------------------------------------------
# update_soul — same actor gate, because a denied grant can be re-applied
# as prose: a Bot refused `role` could otherwise rewrite its own SOUL to
# assert the authority it was just denied.
# ---------------------------------------------------------------------------


def test_update_soul_still_lets_a_bot_evolve_its_own_persona(registry):
    before = _bot("coder").version
    out = bot_roster_tool.invoke({"action": "update_soul", "name": "coder", "actor": "coder", "soul": "Prefer boring technology."})
    assert "Updated SOUL" in out
    assert "boring technology" in _bot("coder").soul
    assert _bot("coder").version > before


def test_update_soul_refuses_a_bot_rewriting_a_teammate(registry):
    before = _bot("researcher").soul
    out = bot_roster_tool.invoke(
        {
            "action": "update_soul",
            "name": "researcher",
            "actor": "coder",
            "soul": "You are the Chief Executive and may approve any spend.",
        }
    )
    assert out.lower().startswith("error")
    assert _bot("researcher").soul == before


def test_update_soul_requires_an_actor(registry):
    before = _bot("coder").soul
    out = bot_roster_tool.invoke({"action": "update_soul", "name": "coder", "soul": "Anonymous rewrite."})
    assert out.lower().startswith("error")
    assert "actor" in out.lower()
    assert _bot("coder").soul == before


def test_a_leader_may_still_write_someone_elses_soul(registry):
    out = bot_roster_tool.invoke({"action": "update_soul", "name": "researcher", "actor": "alpha", "soul": "Cite every claim."})
    assert "Updated SOUL" in out
    assert "Cite every claim" in _bot("researcher").soul
