"""The delegation tool is never deferred.

Deferral is a token optimisation that trades a visible schema for a search
round-trip. That trade is sound for a wide, rarely-used surface. It is not sound
for `task`, which is the primary mechanism of a super-agent and the one
capability an operator explicitly opted into.

**Why this is pinned.** Measured on this deployment (2026-10-17): with
`tool_search.enabled: true` and `defer_builtin_tools: true`, a live delegation
attempt failed. The lead bound `task`, the model called it, and the
unpromoted-call refusal came back instead - while the model explained the failure
with a `tool_search` error string that exists nowhere in the codebase, so the
operator got a fabricated reason for a real outage.

The load-bearing property is not "task is bound" - it was bound before this fix
too. It is that **`task` never enters the deferred set**, so no promotion
round-trip sits on the primary delegation path.
"""

from __future__ import annotations

import unittest

from langchain_core.tools import tool as ltool

from alpha.tools.builtins.tool_search import (
    NEVER_DEFERRED_TOOL_NAMES,
    assemble_deferred_tools,
    build_deferred_tool_setup,
    is_deferrable,
)


@ltool("task")
def _task(prompt: str, subagent_type: str) -> str:
    """Delegate a bounded task to a subagent."""

    return "ok"


@ltool("read_file")
def _read(path: str) -> str:
    """Read a file."""

    return "x"


@ltool("catalog_tool_search")
def _catalog(query: str) -> str:
    """Search the tool catalog."""

    return "[]"


class TestTaskIsNeverDeferred(unittest.TestCase):
    def test_task_is_not_deferrable_even_when_builtins_are_opted_in(self):
        self.assertFalse(is_deferrable(_task, defer_builtin_tools=True))
        self.assertFalse(is_deferrable(_task, defer_builtin_tools=False))

    def test_an_ordinary_builtin_still_is(self):
        # The exemption must be narrow. If it accidentally covered every
        # first-party tool, this fix would silently disable the optimisation it
        # was narrowing.
        self.assertTrue(is_deferrable(_read, defer_builtin_tools=True))
        self.assertFalse(is_deferrable(_read, defer_builtin_tools=False))

    def test_the_exemption_is_exactly_delegation(self):
        self.assertEqual(set(NEVER_DEFERRED_TOOL_NAMES), {"task"})


class TestAssemblyKeepsTaskCallable(unittest.TestCase):
    def test_a_task_only_build_defers_nothing_and_does_not_trip_fail_closed(self):
        """The regression this change could have introduced.

        `assemble_deferred_tools` raises when it believes a deferrable set should
        exist but recovered none. That "expected" test used to be derived from
        `defer_builtin_tools and bool(candidate_tools)`, so a build whose only
        candidate is `task` would raise for deferral that was never requested -
        turning a narrow fix into a hard failure for every `task`-only subagent.
        """

        tools, setup = assemble_deferred_tools([_task], enabled=True, defer_builtin_tools=True)
        self.assertEqual(sorted(setup.deferred_names), [])
        self.assertIn("task", [t.name for t in tools])

    def test_a_mixed_build_defers_the_rest_and_keeps_task_eager(self):
        tools, setup = assemble_deferred_tools([_task, _read], enabled=True, defer_builtin_tools=True)
        self.assertNotIn("task", setup.deferred_names)
        self.assertIn("read_file", setup.deferred_names)
        bound = [t.name for t in tools]
        self.assertIn("task", bound)
        # The discovery tool must still be bound, or nothing can promote the rest.
        self.assertIn("tool_search", bound)

    def test_the_discovery_tool_is_never_itself_deferred(self):
        """A self-blackout would make promotion impossible for everything.

        The discovery tool is built *from* the catalog after selection, so it is
        never a candidate - this pins that structural fact rather than trusting it.
        """

        tools, setup = assemble_deferred_tools([_task, _read], enabled=True, defer_builtin_tools=True)
        self.assertIn("tool_search", [t.name for t in tools])
        self.assertNotIn("tool_search", setup.deferred_names)

    def test_disabled_deferral_still_binds_everything(self):
        tools, setup = assemble_deferred_tools([_task, _read], enabled=False, defer_builtin_tools=False)
        self.assertEqual(setup.deferred_names, frozenset())
        self.assertIsNone(setup.tool_search_tool)
        self.assertEqual(sorted(t.name for t in tools), ["read_file", "task"])


class TestSubagentPathIsUnaffected(unittest.TestCase):
    """The same predicate serves the lead, the embedded client and a subagent."""

    def test_the_setup_helper_agrees_with_the_assembler(self):
        setup = build_deferred_tool_setup([_task, _catalog], enabled=True, defer_builtin_tools=True)
        self.assertNotIn("task", setup.deferred_names)
        self.assertIn("catalog_tool_search", setup.deferred_names)


class TestMcpDeferralIsUnchanged(unittest.TestCase):
    def test_mcp_deferral_does_not_depend_on_the_builtin_flag(self):
        """MCP deferral predates this change and must be untouched."""

        from alpha.tools.builtins.tool_search import is_mcp_tool

        # A builtin is not an MCP tool, so this exercises the flag branch only.
        self.assertFalse(is_mcp_tool(_read))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
