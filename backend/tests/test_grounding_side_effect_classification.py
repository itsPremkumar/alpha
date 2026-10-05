"""The side-effect gate must not be decided by whether a model used the word.

`alpha.grounding.gates.DEFAULT_SIDE_EFFECTS` is a hand-maintained table. An
unlisted tool defaults to `SideEffectClass.UNKNOWN`, and `check_side_effect`
enforces UNKNOWN identically to IRREVERSIBLE. So a tool's usability depends on
whether the model happened to name it in its plan prose -- because the gate has
a second branch (the plan-naming check) that a run can fall through to.

That branchability was observed live, not reasoned about. Driving real
orchestration probes on 2026-10-05:

* one run delegating to ``deep-code-reviewer`` was refused --
  ``irreversible or unclassified call(s) without confirmation:
  delegate_to_deep_agent``;
* another run delegating to ``deep-security`` through the *same tool* went
  through.

The verdict was not the bug. The nondeterminism was: two runs, one tool, two
outcomes, decided by prose the gate cannot verify. This file pins the delegation
family so it cannot rot a fifth time -- ``ls``, ``PROBE_SATISFYING_TOOLS``,
``hashline_read`` and ``catalog_tool_search`` were each patched here one at a
time, which is the pattern that produced 115 unclassified builtin tools.

The measured size of the remaining gap is asserted at the bottom. It is a
*disclosure*, not an endorsement: nothing here claims an unclassified tool is
safe, and the table is not mass-filled because guessing `read_only` for
`request_secure_credential` or `reversible_delete` would be a real security
regression. See ``docs/audits/ORCHESTRATION_PROBES.md``.
"""

from __future__ import annotations

from alpha.grounding.gates import (
    DEFAULT_SIDE_EFFECTS,
    GateSubject,
    SideEffectClass,
    check_side_effect,
)
from alpha.tools.tools import BUILTIN_TOOLS


def _subject(**kw) -> GateSubject:
    return GateSubject(**kw)


# ---------------------------------------------------------------------------
# The delegation family
# ---------------------------------------------------------------------------

#: Delegation and its introspection half. Each is either the isolated-child
#: equivalent of `task`, its durable-batch form, the bounded retry loop over it,
#: or a read of the registry that decides all three.
DELEGATION = {
    "task": SideEffectClass.REVERSIBLE_WRITE,
    "delegate_to_deep_agent": SideEffectClass.REVERSIBLE_WRITE,
    "batch_task": SideEffectClass.REVERSIBLE_WRITE,
    "ralph_loop": SideEffectClass.REVERSIBLE_WRITE,
    "await_task_event": SideEffectClass.READ_ONLY,
    "list_available_deep_agents": SideEffectClass.READ_ONLY,
    "inspect_deep_agent_telemetry": SideEffectClass.READ_ONLY,
}


def test_every_delegation_tool_is_classified() -> None:
    """The regression this file exists for.

    An unclassified delegation tool defaults to UNKNOWN, which the gate refuses
    as an unclassified irreversible call -- so the feature simply does not work,
    and the failure is reported to the user as a refusal with no indication that
    a maintenance table was the cause.
    """
    unclassified = sorted(name for name in DELEGATION if name not in DEFAULT_SIDE_EFFECTS)
    assert not unclassified, f"delegation tools absent from DEFAULT_SIDE_EFFECTS: {unclassified}. Each defaults to UNKNOWN and is refused as an unclassified irreversible call."


def test_delegation_tools_keep_their_declared_class() -> None:
    """A future edit must not quietly reclassify delegation as read-only.

    These tools spawn a child agent with file tools. `READ_ONLY` would be a
    lie to the gate, and `IRREVERSIBLE` would make delegation unusable -- the
    class is `REVERSIBLE_WRITE` because that is exactly what `task` has always
    been, and a family split would reintroduce the same inconsistency.
    """
    for name, expected in DELEGATION.items():
        assert DEFAULT_SIDE_EFFECTS[name] is expected, f"{name} was reclassified"


def test_a_named_delegation_call_passes_the_gate() -> None:
    """End to end through the gate, not just a table lookup.

    The refusal string this replaces was
    ``unconfirmed_side_effect``; asserting on the table alone would still pass if
    `check_side_effect` grew a second reason to refuse it.
    """
    result = check_side_effect(_subject(tool_calls={"delegate_to_deep_agent"}, planned_tools={"delegate_to_deep_agent"}))
    assert result.blocked is False, f"delegation is still refused: {result.reason}"


def test_delegation_passes_even_with_forbid_unconfirmed_side_effects() -> None:
    """The strict branch must agree with the lenient one.

    This is the exact inconsistency the live runs hit: the same tool was refused
    on one path and allowed on the other. A classification that only works on
    the lenient branch is a coin flip, not a control.
    """
    result = check_side_effect(
        _subject(
            tool_calls={"delegate_to_deep_agent", "ralph_loop", "batch_task"},
            forbid_unconfirmed_side_effects=True,
            planned_tools=set(),
        )
    )
    assert result.blocked is False, f"strict branch still refuses delegation: {result.reason}"


def test_the_strict_branch_still_refuses_a_genuinely_irreversible_call() -> None:
    """The fix must not weaken the boundary it was fixing.

    Classifying the delegation family is only safe if `bash` is still refused.
    """
    result = check_side_effect(_subject(tool_calls={"bash"}, forbid_unconfirmed_side_effects=True, planned_tools=set()))
    assert result.blocked is True
    assert "bash" in (result.detail or {}).get("irreversible", [])


def test_an_unclassified_tool_is_still_refused_rather_than_assumed_read_only() -> None:
    """The fail-closed default survives this change.

    115 builtin tools are unclassified and this file does not pretend otherwise.
    What it pins is that the *default* stays UNKNOWN, so the remaining gap is a
    disclosed gap and not a silently permittable one.
    """
    assert "some_tool_that_does_not_exist_yet" not in DEFAULT_SIDE_EFFECTS
    result = check_side_effect(_subject(tool_calls={"some_tool_that_does_not_exist_yet"}, forbid_unconfirmed_side_effects=True))
    assert result.blocked is True
    assert (result.detail or {}).get("unclassified") == ["some_tool_that_does_not_exist_yet"]


# ---------------------------------------------------------------------------
# The measured size of the remaining gap
# ---------------------------------------------------------------------------


def test_unclassified_tool_count_is_disclosed_not_silently_large() -> None:
    """Pin the current number so the gap stays visible as it changes.

    This is a disclosure assertion, not a target: it fails loudly when the count
    moves in EITHER direction, so a fix that classifies tools and a regression
    that adds unclassified ones both show up. The assert message carries the
    list, because "115" alone does not tell a reviewer which tools.
    """
    names = sorted({getattr(t, "name", str(t)) for t in BUILTIN_TOOLS})
    missing = [n for n in names if n not in DEFAULT_SIDE_EFFECTS]
    assert len(missing) == 110, (
        f"{len(missing)} of {len(names)} builtin tools have no side-effect classification "
        f"({len(names) - len(missing)} classified). If this moved because tools were "
        f"classified, update docs/audits/ORCHESTRATION_PROBES.md. Newly unclassified: "
        f"{missing[:20]}"
    )
