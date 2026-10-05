"""An agent must be able to create a subagent -- with the admin contract intact.

The gap this closes was measured, not assumed. Asked to create a subagent, a real
agent called `bot_roster` and reported success:

    "A suitable tool existed, and I used it. This is not a 'NO TOOL EXISTS'
     result. Tool called: `bot_roster` (action `create`)."

`bot_roster` creates a **bot**. Server state after that run: the subagent count
was unchanged. Of 132 registered tools, none registered a subagent -- managed
subagent creation was reachable only through `POST /api/subagents`, an admin
route. So the agent's claim was false and the capability was absent.

These tests pin the three properties that make the new tool acceptable rather
than merely present:

1. **It reaches subagent management at all** (the missing capability).
2. **A model-initiated create gets the same contract as an operator's.** The
   tool validates through `ManagedSubagentDefinition` rather than a hand-rolled
   dict, so every existing guard applies instead of being reimplemented and
   quietly weakened. That is the same rule `IMPROVEMENT_PLAN.md` states: pin
   the consumer, not the declaration.
3. **It does not widen itself.** The tool is absent from `SUBAGENT_TOOLS`, and
   `REQUIRED_DISALLOWED_TOOLS` is enforced on the stored definition, so no
   subagent can nest delegation no matter what a create asks for.

The honesty case is pinned too, because the reason the gap survived is that
nothing answered the question: `list` must show what already exists so a
near-duplicate is visible *before* a create.
"""

from __future__ import annotations

import json

import pytest

from alpha.persistence.managed_subagents import (
    ManagedSubagentDefinition,
    get_managed_subagent_store,
)
from alpha.persistence.managed_subagents.base import REQUIRED_DISALLOWED_TOOLS
from alpha.tools.builtins.subagent_registry_tool import subagent_registry_tool
from alpha.tools.tools import BUILTIN_TOOLS, SUBAGENT_TOOLS, get_available_tools

PROBE = "probe-tool-made"


def _get(name: str):
    """Read a managed definition, treating both absence forms as "not there".

    The store does not return ``None`` for a missing name -- it raises, and it
    raises **two different things** depending on the name:
    ``FileNotFoundError`` for a well-formed name that is simply absent, and
    ``ValueError`` for a name the store itself considers malformed. Catching only
    the first made a guard that held report as a crash.
    """
    try:
        return get_managed_subagent_store().get(name)
    except (FileNotFoundError, ValueError):
        return None


def call(**kw) -> str:
    return subagent_registry_tool.invoke(kw)


@pytest.fixture(autouse=True)
def _clean():
    """Never leave a probe definition behind, whatever the test does."""
    store = get_managed_subagent_store()
    yield
    try:
        store.delete(PROBE)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 1. The capability exists and is reachable
# ---------------------------------------------------------------------------


def test_the_tool_is_registered_for_the_lead_agent() -> None:
    """The regression: no tool registered a subagent, so none could create one."""
    names = {getattr(t, "name", "") for t in BUILTIN_TOOLS}
    assert "subagent_registry" in names
    assert "subagent_registry" in {getattr(t, "name", "") for t in get_available_tools(include_mcp=False, subagent_enabled=True)}


def test_the_model_facing_name_is_subagent_registry() -> None:
    """The decorated name is the contract with the model, not the Python symbol."""
    assert subagent_registry_tool.name == "subagent_registry"


# ---------------------------------------------------------------------------
# 2. It must not widen itself
# ---------------------------------------------------------------------------


def test_a_subagent_cannot_create_a_subagent() -> None:
    """The boundary that stops self-widening, matching `bot_roster`'s own rule.

    `SUBAGENT_TOOLS` is an explicit allowlist, so exclusion here is structural
    rather than a check someone has to remember to add.
    """
    assert "subagent_registry" not in {getattr(t, "name", "") for t in SUBAGENT_TOOLS}


def test_task_is_still_available_to_the_lead_agent() -> None:
    """The tool must not have displaced delegation to get in."""
    names = {getattr(t, "name", "") for t in get_available_tools(include_mcp=False, subagent_enabled=True)}
    assert "task" in names


# ---------------------------------------------------------------------------
# 3. A model-initiated create gets the operator's contract
# ---------------------------------------------------------------------------


def test_an_agent_can_create_a_subagent_and_read_it_back() -> None:
    """The capability itself, end to end through the tool."""
    created = call(
        action="create",
        name=PROBE,
        description="Use when a probe needs a persisted delegated worker definition.",
        system_prompt="You verify one file exists and report its byte size.",
        tools="write_file,read_file",
        max_turns=4,
        timeout_seconds=60,
    )
    assert "Created managed subagent" in created, created
    # The reply must not imply the subagent can run -- that is the claim the
    # missing tool previously invited an agent to make.
    assert "does not prove the subagent can execute" in created, created

    stored = _get(PROBE)
    assert stored is not None, "the tool reported success but nothing was stored"
    assert stored.max_turns == 4
    assert stored.timeout_seconds == 60
    assert stored.tools == ["write_file", "read_file"]

    listed = json.loads(call(action="list"))
    assert PROBE in {r["name"] for r in listed["managed"]}
    assert listed["managed_count"] >= 1

    detail = json.loads(call(action="inspect", name=PROBE))
    assert detail["source"] == "managed"
    assert detail["editable"] is True
    assert detail["definition"]["system_prompt_chars"] > 0


def test_list_shows_builtins_so_a_near_duplicate_is_visible_first() -> None:
    """Nothing answered "does this already exist?" -- that is why the gap lasted."""
    listed = json.loads(call(action="list"))
    assert listed["builtin_count"] >= 8
    assert "general-purpose" in {r["name"] for r in listed["builtin"]}
    assert "nested" in listed["note"].lower() or "force-disallowed" in listed["note"]


def test_nested_delegation_is_force_denied_on_every_created_subagent() -> None:
    """A create cannot re-enable `task`, whatever it asks for."""
    call(
        action="create",
        name=PROBE,
        description="d",
        system_prompt="s",
        tools="write_file,task,ralph_loop",
    )
    stored = _get(PROBE)
    denied = set(stored.disallowed_tools)
    for required in REQUIRED_DISALLOWED_TOOLS:
        assert required in denied, f"{required} missing from the stored denial set"


# --- negative controls: the admin guards must survive the model path --------


@pytest.mark.parametrize(
    "kwargs,expect",
    [
        ({"name": "bad_name", "description": "d", "system_prompt": "s"}, "must match"),
        ({"name": PROBE, "description": "", "system_prompt": "s"}, "error"),
        ({"name": PROBE, "description": "d", "system_prompt": ""}, "error"),
        ({"name": PROBE, "description": "d", "system_prompt": "s", "max_turns": 0}, "error"),
        ({"name": PROBE, "description": "d", "system_prompt": "s", "timeout_seconds": 0}, "error"),
    ],
)
def test_the_name_pattern_and_bounds_still_refuse(kwargs: dict, expect: str) -> None:
    """Guards that hold on the admin route must hold on the model path too."""
    out = call(action="create", **kwargs).lower()
    assert "error" in out or expect in out, out
    assert _get(kwargs["name"]) is None


def test_a_builtin_name_cannot_be_shadowed() -> None:
    """Two definitions answering to one handle is the ambiguity the router refuses."""
    out = call(action="create", name="general-purpose", description="d", system_prompt="s")
    assert "builtin" in out.lower(), out


def test_creating_the_same_name_twice_is_refused_not_overwritten() -> None:
    call(action="create", name=PROBE, description="first", system_prompt="s")
    second = call(action="create", name=PROBE, description="second", system_prompt="s")
    assert "already exists" in second, second
    assert _get(PROBE).description == "first"


def test_update_requires_an_existing_managed_definition() -> None:
    out = call(action="update", name="never-created-probe", description="d")
    assert "nothing to update" in out, out
    # A builtin is not updatable through this tool either.
    assert "nothing to update" in call(action="update", name="general-purpose", description="d"), "builtin was editable"


def test_delete_reports_whether_anything_was_removed() -> None:
    assert "no managed subagent" in call(action="delete", name="never-created-probe")
    call(action="create", name=PROBE, description="d", system_prompt="s")
    assert "Deleted managed subagent" in call(action="delete", name=PROBE)
    assert _get(PROBE) is None


def test_actions_requiring_a_name_say_so_instead_of_guessing() -> None:
    for action in ("inspect", "create", "update", "delete"):
        assert "'name' is required" in call(action=action), action


def test_inspect_on_a_builtin_says_it_cannot_be_edited() -> None:
    detail = json.loads(call(action="inspect", name="general-purpose"))
    assert detail["editable"] is False
    assert "cannot be edited" in detail["note"]


def test_a_listing_elides_long_prompts_rather_than_flooding_the_context() -> None:
    """A list view that inlines eight multi-paragraph prompts is unreadable."""
    call(
        action="create",
        name=PROBE,
        description="d",
        system_prompt="x" * 900,
    )
    listed = json.loads(call(action="list"))
    row = next(r for r in listed["managed"] if r["name"] == PROBE)
    assert row["description"] == "d"
    detail = json.loads(call(action="inspect", name=PROBE))
    # `list` never carries the prompt at all; `inspect` reports its length.
    assert "system_prompt" not in row
    assert detail["definition"]["system_prompt_chars"] == 900


def test_the_tool_validates_through_the_persistence_model_not_a_hand_rolled_dict() -> None:
    """Structural: the two must not drift into different contracts."""
    d = ManagedSubagentDefinition(name=PROBE, description="d", system_prompt="s")
    assert set(REQUIRED_DISALLOWED_TOOLS) <= set(d.disallowed_tools)
    assert d.model_config.get("extra") == "forbid"
