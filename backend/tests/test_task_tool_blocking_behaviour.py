"""Phase 1 measurement: does the model-facing `task` tool block its caller?

## The question this answers

`SUBAGENT_ADVANCED_IMPLEMENTATION_PLAN.md` Phase 1 asks whether `task` blocks
the parent's turn until the child finishes, or returns a handle so the parent
continues. That answer decides whether the next phase is a one-file wiring fix
or a real build, so it was left explicitly unverified in the master plan rather
than assumed.

## The measured answer (2026-10-17, `main`)

**Both, in different directions — and that is the useful part.**

* **Children run concurrently with each other.** `task_tool` submits through
  `executor.execute_async(prompt, task_id=tool_call_id)` and receives an
  `execution_id`, so the child occupies a real slot in the process-wide
  admission controller rather than running inline.
* **The parent's turn blocks.** The tool then `await`s
  `_await_subagent_terminal(...)`, polling the registry every
  `_SUBAGENT_POLL_INTERVAL_SECONDS` (5.0s) until the child reaches a terminal
  state. It returns no handle to the model mid-flight.

So the fan-out that matters already works, and the missing piece is ergonomics
(steering a running child, or continuing the turn) rather than parallelism.

## Why this is pinned rather than merely noted

Both halves are load-bearing and neither is self-evident from reading the call
site: the submit looks like it blocks, and the poll looks like it is polling
something other than a background run. A refactor that swapped `execute_async`
for the sync `execute` would silently make every fan-out serial *and* would still
pass a suite that only checked for "an await exists".

`TestBlockingContract` is therefore a **source pin**, and `TestPollerBoundedness`
is a real behavioural test of the poll loop: bounded by poll count, bounded by
wall clock, propagating an unreadable status instead of raising, and returning
promptly for a terminal child. A green suite that asserted the *wrong* contract
would be worse than none, so the pins name today's behaviour and its date.
"""

from __future__ import annotations

import asyncio
import importlib
import re
import unittest
from pathlib import Path
from unittest import mock

#: `alpha.tools.builtins` re-exports the TOOL as `task_tool`, so
#: `from alpha.tools.builtins import task_tool` binds the `StructuredTool`, not the
#: module (the facade-shadowing case `tools/AGENTS.md` documents).
#: `import_module` resolves the module from `sys.modules` and sidesteps it.
tt = importlib.import_module("alpha.tools.builtins.task_tool")

# `tests/conftest.py` stubs `alpha.subagents.executor` in `sys.modules` to break a
# circular import (documented in `backend/AGENTS.md`), so `SubagentStatus` is a
# MagicMock in this environment - `task_tool` captured the mock at import, and it
# has no members. Nothing here may depend on the real enum; every assertion below
# uses plain sentinels and patches `_is_subagent_terminal`, so the suite tests
# the poll loop's mechanics rather than which statuses exist.

_RUNNING = object()


def _result(status=_RUNNING, completed_at=None):
    """A stand-in for the registry's result object."""

    class _R:
        def __init__(self):
            self.status = status
            self.completed_at = completed_at

    return _R()


class TestBlockingContract(unittest.TestCase):
    """Pin the submit-then-poll shape of the model-facing `task` tool."""

    @classmethod
    def setUpClass(cls):
        backend = Path(__file__).resolve().parents[1]
        cls.source = (backend / "packages" / "harness" / "alpha" / "tools" / "builtins" / "task_tool.py").read_text(encoding="utf-8")

    def test_a_child_is_submitted_asynchronously_not_executed_inline(self):
        # The sync `execute()` wrapper holds its caller, so a fan-out built on it
        # would serialise. `execute_async` is what makes sibling children overlap.
        self.assertRegex(
            self.source,
            r"execution_id\s*=\s*executor\.execute_async\(",
            "task must submit through execute_async so children take real admission slots",
        )
        self.assertNotRegex(
            re.sub(r"def execute\(.*?\n\n", "", self.source, flags=re.S),
            r"result\s*=\s*executor\.execute\(",
            "task must not use the blocking executor wrapper; that would serialise every fan-out",
        )

    def test_the_tool_awaits_the_terminal_state_before_returning(self):
        # This is the half that makes `task` blocking for the parent today.
        self.assertRegex(
            self.source,
            r"await asyncio\.shield\(\s*_await_subagent_terminal\(",
            "task awaits the child's terminal state; this is why the parent turn blocks",
        )

    def test_polling_is_a_cadenced_wait_not_a_busy_loop(self):
        self.assertIn("await asyncio.sleep(", self.source)
        self.assertGreater(tt._SUBAGENT_POLL_INTERVAL_SECONDS, 0)


class TestPollerBoundedness(unittest.TestCase):
    """Real behaviour of `_await_subagent_terminal`."""

    def test_a_terminal_child_returns_without_sleeping(self):
        # `_is_subagent_terminal` cannot be exercised in this environment - it
        # builds its status set from `SubagentStatus`, which `conftest.py`
        # stubs - so it is patched and the poller's own short-circuit is what is
        # under test.
        done = _result()
        with (
            mock.patch.object(tt, "_peek_subagent_result", return_value=done),
            mock.patch.object(tt, "_is_subagent_terminal", return_value=True),
        ):
            out = asyncio.run(tt._await_subagent_terminal("exec-1", 100))
        self.assertIs(out, done)

    def test_a_vanished_registry_entry_returns_immediately(self):
        # Nothing left to wait for. Returning None lets the caller's own
        # not-found path decide, instead of polling a missing entry forever.
        with mock.patch.object(tt, "_peek_subagent_result", return_value=None):
            self.assertIsNone(asyncio.run(tt._await_subagent_terminal("exec-1", 100)))

    def test_the_wait_is_bounded_by_poll_count(self):
        # A child that never terminates must not hang the parent run. This is
        # the boundedness the cancellation unwind depends on.
        slept: list[float] = []

        async def _fake_sleep(seconds):
            slept.append(seconds)

        with (
            mock.patch.object(tt, "_peek_subagent_result", return_value=_result()),
            mock.patch.object(tt, "_is_subagent_terminal", return_value=False),
            mock.patch.object(tt.asyncio, "sleep", side_effect=_fake_sleep),
        ):
            out = asyncio.run(tt._await_subagent_terminal("exec-1", 3))
        self.assertIsNone(out, "an unfinished child must time out to None, never hang")
        self.assertEqual(len(slept), 2, "max_polls=3 means two sleeps before giving up")
        self.assertTrue(all(s == tt._SUBAGENT_POLL_INTERVAL_SECONDS for s in slept))

    def test_an_unreadable_status_propagates_instead_of_raising(self):
        # A broken status object must not abort the parent's unwind; the caller
        # re-raises its own original error.
        with mock.patch.object(tt, "_peek_subagent_result", return_value=tt._STATUS_UNREADABLE):
            out = asyncio.run(tt._await_subagent_terminal("exec-1", 100))
        self.assertIs(out, tt._STATUS_UNREADABLE)

    def test_grace_seconds_bounds_the_wait_by_wall_clock(self):
        # The generic-error unwind must re-raise promptly rather than stall for
        # the full execution timeout.
        with (
            mock.patch.object(tt, "_peek_subagent_result", return_value=_result()),
            mock.patch.object(tt, "_is_subagent_terminal", return_value=False),
            mock.patch.object(tt.asyncio, "sleep", side_effect=asyncio.sleep),
        ):
            out = asyncio.run(tt._await_subagent_terminal("exec-1", 1000, grace_seconds=0.0))
        self.assertIsNone(out, "an expired wall-clock grace must return None")

    def test_a_stamped_completion_ends_the_poll_regardless_of_status(self):
        # `_is_subagent_terminal` ORs a `completed_at` stamp onto the status
        # check, so a finished run is recognised even when its status string is
        # not one this build knows. Pinned as a source assertion because the
        # predicate cannot execute here (the status enum is stubbed), and a
        # behaviour test that skipped that would assert nothing.
        backend = Path(__file__).resolve().parents[1]
        source = (backend / "packages" / "harness" / "alpha" / "tools" / "builtins" / "task_tool.py").read_text(encoding="utf-8")
        body = source.split("def _is_subagent_terminal", 1)[1].split("\n\n", 1)[0]
        self.assertIn("completed_at", body, "a finished run must be recognised by its stamp, not only by status")


if __name__ == "__main__":
    unittest.main()
