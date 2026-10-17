"""Priority-ordered admission for the shared subagent execution pool.

## Why this exists

`SubagentExecutionCapacity` is one process-wide pool shared by every mechanism
that runs through `SubagentExecutor` - ordinary `task`, durable `batch_task`
items, Arena competitors and GRO candidate lanes. It admitted in strict FIFO, so
a cheap background batch queued first would be served before a run an operator was
actively waiting on, with no way to express the difference.

Priority ordering adds that expression. It is deliberately inert until a caller
opts in: `slot()` with no argument uses priority `0.0` for every waiter, and
`min` on `(-priority, sequence)` then selects the longest-waiting entry first, so
the observable behaviour is identical to the previous FIFO queue.

## What is NOT changed

Every boundedness rule is untouched and re-asserted here: `max_running`,
`max_queued`, the `reject` policy, `queue_timeout_seconds`, and the
cancellation/timeout transfer nuance where a slot release and an expiry race -
in that race the timed-out caller owns the transferred slot and must release it,
or the running count leaks downward and the pool over-admits.

These are behavioural tests using `asyncio.Event` for synchronisation. No sleeps
are used to infer ordering, so a loaded host cannot turn a real regression into a
passing run.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from alpha.config.subagent_runtime_config import SubagentRuntimeConfig
from alpha.subagents.capacity import (
    SubagentCapacityRejected,
    SubagentCapacityTimeout,
    SubagentExecutionCapacity,
)


def _capacity(**overrides) -> SubagentExecutionCapacity:
    config = SubagentRuntimeConfig(**overrides)
    return SubagentExecutionCapacity(config)


class TestPriorityOrdering(unittest.TestCase):
    def test_equal_priorities_are_still_served_fifo(self):
        """The safety property: opting in changes nothing until priorities differ."""

        async def scenario():
            cap = _capacity(max_running=1, max_queued=8, queue_timeout_seconds=5)
            order: list[str] = []

            async with cap.slot():
                # Three waiters arrive in a known order, all at the default priority.
                first = asyncio.create_task(_enter(cap, "low", order, 0.0))
                await _settled()
                second = asyncio.create_task(_enter(cap, "mid", order, 0.0))
                await _settled()
                third = asyncio.create_task(_enter(cap, "high", order, 0.0))
                await _settled()
            await asyncio.gather(first, second, third)
            return order

        self.assertEqual(asyncio.run(scenario()), ["low", "mid", "high"])

    def test_a_higher_priority_waiter_is_served_first(self):
        """The property the change exists for: foreground overtakes background."""

        async def scenario():
            cap = _capacity(max_running=1, max_queued=8, queue_timeout_seconds=5)
            order: list[str] = []

            async with cap.slot():
                # The background job queues FIRST, so FIFO would serve it first.
                background = asyncio.create_task(_enter(cap, "background", order, 0.0))
                await _settled()
                foreground = asyncio.create_task(_enter(cap, "foreground", order, 10.0))
                await _settled()
            await asyncio.gather(background, foreground)
            return order

        self.assertEqual(asyncio.run(scenario()), ["foreground", "background"])

    def test_priority_inverts_arrival_order_completely(self):
        async def scenario():
            cap = _capacity(max_running=1, max_queued=8, queue_timeout_seconds=5)
            order: list[str] = []

            async with cap.slot():
                tasks = []
                for name, priority in (("a", 1.0), ("b", 5.0), ("c", 3.0)):
                    tasks.append(asyncio.create_task(_enter(cap, name, order, priority)))
                    await _settled()
            await asyncio.gather(*tasks)
            return order

        self.assertEqual(asyncio.run(scenario()), ["b", "c", "a"])

    def test_a_preemption_is_counted_for_the_snapshot(self):
        """Contention has to be observable, or it is only felt."""

        async def scenario():
            cap = _capacity(max_running=1, max_queued=8, queue_timeout_seconds=5)
            order: list[str] = []

            async with cap.slot():
                background = asyncio.create_task(_enter(cap, "background", order, 0.0))
                await _settled()
                foreground = asyncio.create_task(_enter(cap, "foreground", order, 10.0))
                await _settled()
                before = cap.snapshot().priority_preemptions
            await asyncio.gather(background, foreground)
            return before, cap.snapshot().priority_preemptions

        before, after = asyncio.run(scenario())
        self.assertEqual(before, 0, "no slot has been released yet")
        self.assertEqual(after, 1, "serving the background waiter first would be the preemption")


class TestBoundednessPreserved(unittest.TestCase):
    def test_max_running_is_still_enforced(self):
        async def scenario():
            cap = _capacity(max_running=1, max_queued=8, queue_timeout_seconds=5)
            snapshot = None
            async with cap.slot():
                snapshot = cap.snapshot()
            return snapshot

        snapshot = asyncio.run(scenario())
        self.assertEqual(snapshot.running, 1)
        self.assertEqual(snapshot.max_running, 1)

    def test_max_queued_is_still_enforced(self):
        async def scenario():
            cap = _capacity(max_running=1, max_queued=1, queue_timeout_seconds=5)
            async with cap.slot():
                asyncio.create_task(_enter(cap, "one", [], 0.0))
                await _settled()
                try:
                    await _enter(cap, "two", [], 0.0)
                except SubagentCapacityRejected as exc:
                    return str(exc)
            return None

        message = asyncio.run(scenario())
        self.assertIsNotNone(message, "a second waiter over max_queued must be rejected")
        self.assertIn("capacity is full", message)

    def test_reject_policy_still_refuses_immediately(self):
        async def scenario():
            cap = _capacity(max_running=1, max_queued=8, admission_policy="reject", queue_timeout_seconds=5)
            async with cap.slot():
                try:
                    await _enter(cap, "x", [], 0.0)
                except SubagentCapacityRejected as exc:
                    return str(exc)
            return None

        self.assertIn("capacity is full", asyncio.run(scenario()))

    def test_a_waiter_that_times_out_releases_nothing_and_is_not_served_later(self):
        """A timed-out waiter must not occupy the queue, and must not be counted."""

        async def scenario():
            # The config floor for `queue_timeout_seconds` is 1, so this genuinely
            # waits a second rather than stubbing the timeout.
            cap = _capacity(max_running=1, max_queued=8, queue_timeout_seconds=1)
            order: list[str] = []
            async with cap.slot():
                with self.assertRaises(SubagentCapacityTimeout):
                    await _enter(cap, "expired", order, 0.0)
                queued_during = cap.snapshot().queued
            return order, queued_during

        order, queued = asyncio.run(scenario())
        self.assertEqual(order, [], "a timed-out waiter must never be admitted")
        self.assertEqual(queued, 0, "a timed-out waiter must leave the queue depth clean")

    # The transfer nuance - a slot handed to a waiter at the very moment it timed
    # out is still owned by that waiter and must be released - is deliberately NOT
    # re-tested here. It is already pinned by
    # `tests/test_subagent_execution_capacity.py::test_capacity_timeout_removes_waiter_and_releases_slot`
    # and `::test_capacity_cancelled_waiter_does_not_leak_queue_or_slot`, and a
    # hand-rolled version of a race is the kind of test that passes without proving
    # anything. Those two cases still pass against the priority implementation.


class TestExecutorForwardsPriority(unittest.TestCase):
    """The bands only matter if a call site actually supplies one.

    These are **source pins**, and deliberately so. Driving `SubagentExecutor` to
    the admission line means running the real agent graph, which in this
    environment is mocked; a test built on that proves the graph ran, not that
    the right keyword was passed. The claim under test is an argument shape at one
    call site, which is exactly what `tests/test_workflow_wave_peak_in_flight.py`
    already pins this way.
    """

    @classmethod
    def setUpClass(cls):
        backend = Path(__file__).resolve().parents[1]
        cls.executor = (backend / "packages" / "harness" / "alpha" / "subagents" / "executor.py").read_text(encoding="utf-8")

    def test_a_non_default_priority_is_passed_as_a_keyword(self):
        self.assertIn(
            '{"priority": self.admission_priority}',
            self.executor,
            "a priority must reach slot() by keyword, so a caller-supplied controller's positional signature is not disturbed",
        )

    def test_the_zero_priority_path_passes_no_keyword(self):
        # This is the compatibility guarantee: `execution_capacity` is a
        # documented extension point, and every zero-argument `slot()` in the
        # tree (including the doubles in test_subagent_executor.py) must keep
        # working. Passing unconditionally broke them with
        # "_ImmediateCapacity.slot() takes 1 positional argument but 2 were given".
        self.assertRegex(
            self.executor,
            r'\{\} if not self\.admission_priority else \{"priority": self\.admission_priority\}',
            "the default admission call must keep the original zero-argument arity",
        )

    def test_the_bands_are_ordered(self):
        from alpha.subagents.capacity import (
            PRIORITY_BACKGROUND,
            PRIORITY_FOREGROUND,
            PRIORITY_OPERATOR,
        )

        # Only the relative order is meaningful, but the spacing is wide so an
        # intermediate band can be added without renumbering.
        self.assertLess(PRIORITY_BACKGROUND, PRIORITY_FOREGROUND)
        self.assertLess(PRIORITY_FOREGROUND, PRIORITY_OPERATOR)

    def test_the_adopted_call_sites_opted_in(self):
        """Foreground and operator work must actually declare its band.

        An unnamed band at a call site is the defect these constants exist to
        prevent, so both spellings that reach the constructor are accepted:
        ``admission_priority=PRIORITY_X`` (direct call) and
        ``"admission_priority": PRIORITY_X`` (a kwargs dict handed to the
        constructor). What is rejected is the file mentioning the band at all
        without binding it to the parameter.
        """

        backend = Path(__file__).resolve().parents[1]
        harness = backend / "packages" / "harness" / "alpha"
        for relative, expected in (
            # The parent turn blocks on an ordinary `task` and on a hierarchical
            # child, and on nothing else in these two files.
            ("tools/builtins/task_tool.py", "PRIORITY_FOREGROUND"),
            ("subagents/deep_runner.py", "PRIORITY_FOREGROUND"),
            # An operator explicitly asked for a tournament and for a
            # candidate lane.
            ("tools/builtins/arena_tool.py", "PRIORITY_OPERATOR"),
            ("gro/adapters.py", "PRIORITY_OPERATOR"),
        ):
            with self.subTest(file=relative):
                source = (harness / relative).read_text(encoding="utf-8")
                self.assertTrue(
                    f"admission_priority={expected}" in source or f'"admission_priority": {expected}' in source,
                    f"{relative} must bind the executor's admission_priority to {expected}",
                )

    def test_the_background_band_is_never_passed_explicitly(self):
        """Background is the default, so writing it out would be noise.

        A call site naming `PRIORITY_BACKGROUND` is saying "I checked and this is
        background" - which is fine as documentation but must not become a
        reason to add the keyword. Keeping it implicit is what guarantees the
        default path stays the zero-argument `slot()` call.
        """

        backend = Path(__file__).resolve().parents[1]
        harness = backend / "packages" / "harness" / "alpha"
        offenders = []
        for path in harness.rglob("*.py"):
            source = path.read_text(encoding="utf-8", errors="replace")
            if "PRIORITY_BACKGROUND" in source and "admission_priority" in source and "capacity.py" not in path.name and "test" not in path.parts:
                offenders.append(path.relative_to(harness).as_posix())
        self.assertEqual(offenders, [], "PRIORITY_BACKGROUND must stay implicit (the executor default)")


class TestSnapshotContract(unittest.TestCase):
    def test_the_new_snapshot_field_defaults_for_old_constructors(self):
        """`priority_preemptions` is additive, so a positional construction still works."""

        from alpha.subagents.capacity import SubagentCapacitySnapshot

        snapshot = SubagentCapacitySnapshot(
            max_running=1,
            running=0,
            max_queued=4,
            queued=0,
            admission_policy="queue",
        )
        self.assertEqual(snapshot.priority_preemptions, 0)


async def _enter(capacity: SubagentExecutionCapacity, name: str, order: list[str], priority: float) -> None:
    async with capacity.slot(priority):
        order.append(name)


async def _settled() -> None:
    """Let queued tasks reach their first await."""
    for _ in range(3):
        await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
