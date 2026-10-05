"""The subagent write boundary must refuse a field it does not understand.

Found live on 2026-10-05. `ManagedSubagentDefinition` -- the persistence model --
already declared ``model_config = ConfigDict(extra="forbid")``, but the **Gateway
request models** are separate classes that did not. Measured against the shipped
code before the fix::

    create extra = UNSET
    update extra = UNSET
    persistence extra = forbid

    r = ManagedSubagentCreateRequest(name=..., description=..., system_prompt=...,
                                     max_turn=5)      # note: max_turn
    r.max_turns            -> 50                   # the default, not 5
    hasattr(r, "max_turn")  -> False               # the typo was dropped

And over HTTP, ``POST /api/subagents`` answered **201 Created** with a subagent
carrying ``max_turns=50``. The operator asked for 5 turns, was told they had
configured it, and got the default.

Why this is more than tidiness
-----------------------------
Every other guard on this route behaves correctly -- the name pattern, the
builtin-collision refusal, the unknown-model refusal, ``ge=1`` bounds -- and each
was negative-controlled. This one alone failed open, and it is the only guard a
*typo* can reach: nobody misspells `name` and calls that a feature.

It is also the repo's own documented failure shape. `IMPROVEMENT_PLAN.md` names it
-- "a claim that is more specific than the evidence behind it" -- and states the
rule this test exists to enforce: *for every fix, pin the consumer, not the
declaration*. Here the strictness was **declared** on the persistence model and
never reached the request boundary that consumes it, so a green test asserting
`extra == "forbid"` on the definition model would have passed while the API
silently discarded the field.

These tests assert on the *request* models, because that is the boundary the
defect lived on. Asserting on `ManagedSubagentDefinition` again is the mistake
that hid it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from alpha.persistence.managed_subagents.base import ManagedSubagentDefinition
from app.gateway.routers.subagents import (
    ManagedSubagentCreateRequest,
    ManagedSubagentUpdateRequest,
)

BASE = {"name": "probe-extra", "description": "d", "system_prompt": "s"}


def test_the_persistence_model_is_still_strict() -> None:
    """The layer that was already correct, kept correct."""
    assert ManagedSubagentDefinition.model_config.get("extra") == "forbid"


@pytest.mark.parametrize("model", [ManagedSubagentCreateRequest, ManagedSubagentUpdateRequest])
def test_the_request_models_are_strict_about_unknown_fields(model) -> None:
    """The defect: these two had `extra` unset while the definition had `forbid`."""
    assert model.model_config.get("extra") == "forbid", f"{model.__name__} accepts unknown fields, so a typo'd knob is dropped with a success code instead of refused"


def test_a_misspelled_knob_is_refused_on_create() -> None:
    """The exact live input: asked for `max_turn`, which does not exist."""
    with pytest.raises(ValidationError) as exc:
        ManagedSubagentCreateRequest(**BASE, max_turn=5)
    # The message must name the offending field, or the caller cannot fix it.
    assert "max_turn" in str(exc.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_turn", 5),
        ("timeout_second", 60),
        ("tool", ["write_file"]),  # singular of `tools`
        ("disallowed_tool", []),  # singular of `disallowed_tools`
        ("enable", True),  # `enabled`
        ("systemPrompt", "s"),  # camelCase of a snake_case field
        ("systen_prompt", "s"),  # transposition, the most human typo there is
    ],
)
def test_every_plausible_typo_is_refused(field: str, value: object) -> None:
    """A near-miss is the whole risk surface; `enable` is not a field name."""
    with pytest.raises(ValidationError):
        ManagedSubagentCreateRequest(**BASE, **{field: value})


def test_an_unknown_field_is_refused_on_update_too() -> None:
    """A dropped update field reports 200 and changes nothing."""
    with pytest.raises(ValidationError):
        ManagedSubagentUpdateRequest(max_turn=5)


def test_a_valid_request_is_unaffected() -> None:
    """Strictness must not reject the legitimate payload it is meant to police."""
    r = ManagedSubagentCreateRequest(**BASE, max_turns=5, timeout_seconds=60, tools=["read_file"])
    assert r.max_turns == 5
    assert r.timeout_seconds == 60
    assert r.tools == ["read_file"]


def test_known_optional_fields_are_all_still_accepted() -> None:
    """Every documented field must survive, or `forbid` becomes an outage."""
    r = ManagedSubagentCreateRequest(
        **BASE,
        display_name="Probe",
        disallowed_tools=["task"],
        skills=["s"],
        model="inherit",
        max_turns=9,
        timeout_seconds=30,
        enabled=False,
    )
    assert (r.display_name, r.disallowed_tools, r.skills) == ("Probe", ["task"], ["s"])
    assert (r.model, r.max_turns, r.timeout_seconds, r.enabled) == ("inherit", 9, 30, False)


def test_the_forced_disallowed_set_is_still_applied_not_silently_overridable() -> None:
    """`forbid` on an unknown field must not weaken the no-nested-loop guard."""
    from alpha.persistence.managed_subagents.base import REQUIRED_DISALLOWED_TOOLS

    d = ManagedSubagentDefinition(**BASE)
    for required in REQUIRED_DISALLOWED_TOOLS:
        assert required in set(d.disallowed_tools), f"{required} dropped from the required set"
