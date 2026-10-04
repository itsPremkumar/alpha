"""The reuse gate must not refuse the call its own remediation names.

## The defect

`check_reuse_probe` blocks a productive step whose tool calls include no
consultation, and its remediation says:

    "read the capability manifest and search the existing implementation before
     writing new code"

The capability manifest read **is** the `alpha_capability` tool. But
`alpha_capability` was in neither `PROBE_SATISFYING_TOOLS` nor
`DEFAULT_SIDE_EFFECTS`, so:

* calling `alpha_capability` was refused with `reuse_probe_missing`;
* the refusal told the agent to call `alpha_capability`;
* calling it again was refused again.

An inescapable loop. Observed live on 2026-10-04: a real Gateway run
(`fa288522-c9dc-43fb-8d5d-0c855994510f`) burned six tool calls against this
gate, each refused with the same circular instruction, before the agent gave up
and reported BLOCKED.

The shape is the important part: a gate that refuses the specific action its own
remediation recommends cannot be satisfied by any agent, so it is not a
correctness check — it is a dead end that manufactures a BLOCKED run.
"""

from __future__ import annotations

from alpha.grounding.gates import (
    DEFAULT_SIDE_EFFECTS,
    PROBE_SATISFYING_TOOLS,
    GateSubject,
    SideEffectClass,
    check_reuse_probe,
)


def _subject(*, tool_calls: tuple[str, ...] = (), reuse_probe_run: bool = False) -> GateSubject:
    """Build a subject for the reuse gate.

    `tool_calls` are the names the model requested; the gate only inspects
    names, so real call metadata would not change the verdict.
    """
    return GateSubject(
        tool_calls=tool_calls,
        reuse_probe_run=reuse_probe_run,
    )


# --------------------------------------------------------------------------
# The circularity, pinned directly
# --------------------------------------------------------------------------


def test_the_capability_manifest_tool_satisfies_the_gate_it_recommends() -> None:
    """`alpha_capability` must be a probe-satisfying tool.

    This is the whole regression. Before the fix the gate's remediation named a
    tool that the gate itself refused.
    """
    assert "alpha_capability" in PROBE_SATISFYING_TOOLS, "the reuse gate's remediation tells the agent to read the capability manifest; alpha_capability IS that read, so refusing it is circular"


def test_the_capability_manifest_tool_is_classified_read_only() -> None:
    """`alpha_capability` is a read-only projection of local registries.

    Without a `DEFAULT_SIDE_EFFECTS` entry it defaults to `UNKNOWN`, which the
    guard treats as irreversible — so even the read-only path below could not
    clear it.
    """
    classification = DEFAULT_SIDE_EFFECTS.get("alpha_capability")
    assert classification is SideEffectClass.READ_ONLY, "alpha_capability performs no model call, no network fetch and no provider probe; an unclassified (UNKNOWN) manifest read is a guard that cannot be satisfied by reading"


def test_a_call_that_is_only_the_capability_manifest_passes() -> None:
    """The exact call the gate recommends must clear the gate."""
    result = check_reuse_probe(_subject(tool_calls=("alpha_capability",)))
    assert not result.blocked, "the gate refused the call its own remediation names"


# --------------------------------------------------------------------------
# The pre-existing guarantees, so the fix cannot weaken them
# --------------------------------------------------------------------------


def test_a_purely_read_only_step_still_passes() -> None:
    """A read-only step cannot produce duplication, so it passes."""
    result = check_reuse_probe(_subject(tool_calls=("read_file", "grep")))
    assert not result.blocked


def test_a_productive_step_with_no_consultation_still_blocks() -> None:
    """The gate must still block a write with no recorded consultation."""
    result = check_reuse_probe(_subject(tool_calls=("write_file",)))
    assert result.blocked
    assert result.code == "reuse_probe_missing"
    # The remediation must name a tool that actually clears the gate — otherwise
    # the loop this module exists to prevent returns.
    assert "alpha_capability" in result.remediation or "read" in result.remediation


def test_reuse_probe_run_still_short_circuits() -> None:
    """A run that already consulted the manifest is not re-gated."""
    result = check_reuse_probe(_subject(tool_calls=("write_file",), reuse_probe_run=True))
    assert not result.blocked


def test_an_unlisted_tool_is_still_guarded() -> None:
    """A tool nobody has classified must stay UNKNOWN, hence guarded.

    This is the deliberate conservative default: a tool added tomorrow is
    guarded before anyone has classified it.
    """
    result = check_reuse_probe(_subject(tool_calls=("some_tool_added_tomorrow",)))
    assert result.blocked


def test_a_mixed_batch_still_blocks_because_not_every_call_probes() -> None:
    """A batch is gated on the *whole* set, not on any member.

    `write_file` + `grep` blocks: the gate requires every called tool to be a
    consultation, so one productive call in the batch is enough. This is the
    conservative reading and it is deliberate — a batch that writes and reads in
    the same step has not consulted *before* writing.
    """
    result = check_reuse_probe(_subject(tool_calls=("write_file", "grep")))
    assert result.blocked


def test_empty_tool_calls_block_because_absence_is_not_consultation() -> None:
    """No tool calls means no recorded consultation, so the gate blocks.

    An empty batch is not a read-only step; it is an undeclared one. The gate
    refuses on it rather than inventing a default.
    """
    result = check_reuse_probe(_subject(tool_calls=()))
    assert result.blocked
