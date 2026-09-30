"""The swarm benefit estimator's ``reason`` must not contradict its own numbers.

``SwarmBenefitEstimator.estimate`` publishes ``estimated_serial_seconds`` and
``estimated_parallel_seconds`` in the same payload as the prose ``reason`` that
``POST /api/swarms/evaluate`` hands back to a caller (and to the ``swarm`` model's
``evaluate`` action). When those two disagree the surface is reporting a cause
it never measured.

The concrete regression: the batch branch refuses a swarm on
``item_count >= 3 and net_benefit > 10.0`` but hardcoded a single explanation for
every refusal. A 2-item batch is refused on the item-count half of the gate while
its own arithmetic shows the parallel path is 16.2s *faster* than serial, so the
shipped string asserted the opposite of the numbers printed beside it. Observed
live from ``POST /api/swarms/evaluate``::

    {"should_swarm": false, ..., "estimated_serial_seconds": 40.0,
     "estimated_parallel_seconds": 23.8, "estimated_speedup": 1.0,
     "reason": "Batch count of 2 is too small to overcome swarm spawn and merge overhead."}

Contract under test: a refusal reason may not claim the overhead was not overcome
when ``estimated_parallel_seconds < estimated_serial_seconds``. The decision
itself (``should_swarm``, ``mode``, and every number) is unchanged by this - only
the explanation is required to stop fabricating one.
"""

from __future__ import annotations

import pytest

from alpha.swarm.estimator import SwarmBenefitEstimator

# Phrases that assert the swarm costs more than it saves. If the payload says
# otherwise, the sentence is a fabricated reason.
_OVERHEAD_NOT_OVERCOME_PHRASES = (
    "too small to overcome",
    "exceeds expected parallel gains",
    "coordination overhead exceeds",
)


def _overhead_claim(reason: str) -> str | None:
    lowered = reason.lower()
    for phrase in _OVERHEAD_NOT_OVERCOME_PHRASES:
        if phrase in lowered:
            return phrase
    return None


@pytest.mark.parametrize("item_count", [2, 3, 4, 7, 12, 25])
def test_reason_never_claims_overhead_was_not_overcome_when_it_was(item_count: int) -> None:
    """Every batch size: the prose may not deny a speedup the payload reports."""

    decision = SwarmBenefitEstimator.estimate("Process the batch", items=[f"item-{n}" for n in range(item_count)])

    parallel_is_faster = decision.estimated_parallel_seconds < decision.estimated_serial_seconds
    phrase = _overhead_claim(decision.reason)
    assert not (parallel_is_faster and phrase), (
        f"item_count={item_count}: reason claims {phrase!r} but the same payload reports "
        f"serial={decision.estimated_serial_seconds}s vs parallel={decision.estimated_parallel_seconds}s "
        f"(a {decision.estimated_serial_seconds / max(decision.estimated_parallel_seconds, 1.0):.2f}x saving). "
        f"reason={decision.reason!r}"
    )


def test_two_item_batch_refusal_names_the_actual_gate() -> None:
    """A 2-item batch is refused on batch size, not on measured overhead.

    This is the exact shape observed live: ``should_swarm=False`` with a parallel
    estimate well under the serial one.
    """

    decision = SwarmBenefitEstimator.estimate("Reply with the capital of each country", items=["France", "Japan"])

    assert decision.should_swarm is False
    # The decision is unchanged by the fix - only the explanation is corrected.
    assert decision.mode.value == "map_reduce"
    assert decision.recommended_workers == 1
    assert decision.estimated_speedup == 1.0
    # Measured arithmetic, straight from the module's own constants.
    assert decision.estimated_serial_seconds == 40.0
    assert decision.estimated_parallel_seconds == pytest.approx(23.8)
    assert decision.estimated_parallel_seconds < decision.estimated_serial_seconds
    # ...so the refusal must be attributable to the batch-size half of the gate.
    assert "too small to overcome" not in decision.reason.lower()
    assert "2" in decision.reason


def test_default_refusal_still_reports_neutral_serial_and_parallel() -> None:
    """The no-batch branch is honest already and must stay that way.

    It reports parallel == serial, so "overhead exceeds gains" is consistent there.
    This pins the fix did not leak into the branch that needed no change.
    """

    decision = SwarmBenefitEstimator.estimate("Fix a small typo in README.md")

    assert decision.should_swarm is False
    assert decision.estimated_serial_seconds == decision.estimated_parallel_seconds
    assert _overhead_claim(decision.reason) is not None


def test_positive_refusal_is_still_measurable() -> None:
    """A refusal must still report the measured saving rather than hiding it.

    ``estimated_speedup``/``recommended_workers`` stay floored at the single-agent
    recommendation (the decision the caller is being told to act on), but the two
    duration fields are the raw measurement and must keep showing it.
    """

    decision = SwarmBenefitEstimator.estimate("Process the batch", items=["a", "b"])

    assert decision.should_swarm is False
    saving = decision.estimated_serial_seconds - decision.estimated_parallel_seconds
    assert saving > 0.0
    assert str(round(saving)) in decision.reason or f"{saving:.0f}" in decision.reason
