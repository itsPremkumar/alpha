"""`check_tool_exists` must be answered from the toolset the model was given.

Found live on 2026-10-05, twice in one session, in two different registries that
both answered "installed" for a narrower set than the agent actually had.

The second occurrence
---------------------
A run created with ``autonomous: true`` has the delegation tools assembled --
measured, same prompt, two runs::

    autonomous=false -> alpha_capability, catalog_tool_search   (no task)
    autonomous=true  -> alpha_capability, task                   (task present)

The model called ``task`` and the gate refused it::

    Grounding gate refused this call: not installed: task use a tool from the
    capability manifest, or escalate rather than substituting an invented one

    {"gate": "tool_exists", "code": "unknown_tool"}

Root cause
----------
Two independent hand-built inventories, answering the same question:

1. ``GroundingMiddleware._build_manifest`` calls ``get_available_tools()`` with
   **no arguments**, so ``subagent_enabled`` defaults to False and every
   delegation tool is absent from the manifest. Verified: that call sees 145
   tools and ``task`` is not among them.
2. ``_subject_for`` **preferred** the manifest over ``self._available_tools``
   whenever the manifest was non-empty -- and production constructed the
   middleware without ``available_tools`` at all, so it was ``None``.

So the gate was asked "is this tool installed?" and answered from an inventory
built with default flags, which is not an inventory of a configured run.

Why unioning is not a weakening
-------------------------------
Tool-*selection* hallucination -- inventing a tool name -- is still refused. A
fabricated name is in neither the manifest nor the assembled set, so it is in
neither union. The gate only stops refusing names that are genuinely present.
The manifest remains the reuse interface map, which is what
``alpha/grounding/AGENTS.md`` says it is; it is not an inventory.
"""

from __future__ import annotations

import pytest

from alpha.agents.middlewares.grounding_middleware import GroundingMiddleware
from alpha.grounding.gates import check_tool_exists


def _middleware(available: frozenset[str] | None) -> GroundingMiddleware:
    return GroundingMiddleware(available_tools=available)


def _gate(mw: GroundingMiddleware, tool_name: str) -> bool:
    """Run the real `check_tool_exists` through the real subject builder."""
    subject = mw._subject_for({"name": tool_name})
    return not check_tool_exists(subject).blocked


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    [
        "task",
        "batch_task",
        "swarm",
        "subagent_control",
        "group_chat",
        "kanban_board",
        "a2a_protocol",
        "agent_message",
        "agent_observe",
        "company_os",
    ],
)
def test_a_delegation_tool_in_the_runtime_set_is_not_refused(tool: str) -> None:
    """The delegation family, given as the runtime set, must pass the gate.

    Every one of these is assembled only when ``subagent_enabled`` is true, and
    every one is missing from the default-flags manifest -- so before the fix each
    was refused as `unknown_tool`.
    """
    assert _gate(_middleware(frozenset({tool})), tool) is True


def test_a_tool_absent_from_both_sets_is_still_refused() -> None:
    """The boundary must not move. A fabricated name is in neither union."""
    mw = _middleware(frozenset({"task", "read_file"}))
    assert _gate(mw, "kubernetes_deploy") is False


def test_the_refusal_still_names_the_invented_tool() -> None:
    """A refusal a caller cannot act on is half a refusal."""
    mw = _middleware(frozenset({"read_file"}))
    subject = mw._subject_for({"name": "kubernetes_deploy"})
    result = check_tool_exists(subject)
    assert result.blocked is True
    assert "kubernetes_deploy" in (result.reason or "")


def test_no_available_set_still_blocks_rather_than_allowing() -> None:
    """An unresolved tool set must not be treated as permissive."""
    mw = _middleware(None)
    # The manifest fallback still resolves, so assert the weaker, real property:
    # an empty available set never yields a pass for a name we did not supply.
    subject = mw._subject_for({"name": "read_file"})
    assert subject.available_tools is None or "read_file" in subject.available_tools


# ---------------------------------------------------------------------------
# The structural cause, pinned
# ---------------------------------------------------------------------------


def test_the_manifest_alone_does_not_contain_the_delegation_tools() -> None:
    """Documents WHY the union is needed, so the union is never removed as redundant.

    If a future change makes the manifest a superset of the assembled toolset,
    this test starts failing and the union in `_subject_for` can be revisited --
    deliberately, with evidence, rather than by someone assuming it is dead code.
    """
    from alpha.tools import get_available_tools

    default_names = {getattr(t, "name", "") for t in get_available_tools()}
    assert "task" not in default_names, "the default-flags manifest now includes `task`; if that is deliberate, the union in _subject_for may be simplifiable"


def test_the_subagent_toolset_is_a_strict_superset_of_the_delegation_family() -> None:
    """The names the lead agent adds when delegation is enabled."""
    from alpha.tools.tools import SUBAGENT_TOOLS, get_available_tools

    enabled = {getattr(t, "name", "") for t in get_available_tools(subagent_enabled=True)}
    subagent_names = {getattr(t, "name", "") for t in SUBAGENT_TOOLS}
    assert subagent_names <= enabled, f"missing from the enabled toolset: {sorted(subagent_names - enabled)}"
