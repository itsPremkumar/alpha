"""Trigger cross-process fencing: compare-and-set fire tokens.

A stale scheduler that wakes after another worker already fired must lose the
race rather than double-fire. These tests pin ``try_claim_fire``'s CAS
semantics, the token advance on every fire path, and the honest refusal reasons.
"""

from __future__ import annotations

from alpha.workflow.triggers import TriggerKind, TriggerStore, WorkflowTrigger


def _make_store() -> TriggerStore:
    return TriggerStore()


def _make_trigger(trigger_id: str = "t1") -> WorkflowTrigger:
    return WorkflowTrigger(
        trigger_id=trigger_id,
        workflow_id="wf_1",
        kind=TriggerKind.INTERVAL,
        interval_seconds=60.0,
        next_fire_at=1000.0,
    )


def test_try_claim_fire_succeeds_with_matching_token():
    store = _make_store()
    trigger = _make_trigger()
    store.add(trigger)

    claimed, result, reason = store.try_claim_fire("t1", expected_token=0)
    assert claimed
    assert result is not None
    assert result.fire_token == 1
    assert result.fire_count == 1
    assert reason == ""


def test_try_claim_fire_refuses_stale_token():
    store = _make_store()
    store.add(_make_trigger())

    # First worker fires with token 0 -> succeeds
    claimed1, _, _ = store.try_claim_fire("t1", expected_token=0)
    assert claimed1

    # Second worker still holds token 0 (stale) -> refused
    claimed2, result2, reason2 = store.try_claim_fire("t1", expected_token=0)
    assert not claimed2
    assert result2 is None
    assert "stale fire token 0" in reason2
    assert "current 1" in reason2


def test_try_claim_fire_refuses_unknown_trigger():
    store = _make_store()
    claimed, result, reason = store.try_claim_fire("missing", expected_token=0)
    assert not claimed
    assert result is None
    assert "not found" in reason


def test_fire_token_advances_on_record_fire():
    store = _make_store()
    store.add(_make_trigger())

    trigger = store.record_fire("t1")
    assert trigger.fire_token == 1
    assert trigger.fire_count == 1

    trigger = store.record_fire("t1")
    assert trigger.fire_token == 2
    assert trigger.fire_count == 2


def test_claim_then_record_fire_keeps_token_monotonic():
    store = _make_store()
    store.add(_make_trigger())

    store.try_claim_fire("t1", expected_token=0)
    trigger = store.record_fire("t1")
    assert trigger.fire_token == 2
    assert trigger.fire_count == 2

    # A reader holding token 2 can now claim the next occurrence
    claimed, result, _ = store.try_claim_fire("t1", expected_token=2)
    assert claimed
    assert result.fire_token == 3


def test_try_claim_fire_advances_next_fire_at():
    store = _make_store()
    store.add(_make_trigger())

    claimed, result, _ = store.try_claim_fire("t1", expected_token=0, fired_at=1000.0)
    assert claimed
    assert result is not None
    assert result.next_fire_at == 1060.0  # 1000 + interval 60


def test_try_claim_fire_respects_max_fires():
    store = _make_store()
    trigger = _make_trigger()
    trigger.max_fires = 1
    store.add(trigger)

    claimed, result, _ = store.try_claim_fire("t1", expected_token=0)
    assert claimed
    assert result is not None
    assert result.enabled is False
    assert "max_fires (1) reached" in result.disabled_reason

    # Even with the right token, a disabled trigger is not claimable via record path;
    # try_claim_fire itself still checks token, but enabled=False is the caller's gate.
    # Here the token advanced to 1, so a stale token-0 claim is refused.
    claimed2, _, reason2 = store.try_claim_fire("t1", expected_token=0)
    assert not claimed2
    assert "stale fire token 0" in reason2


def test_concurrent_claims_only_one_wins():
    """Two readers holding the same token: exactly one claim succeeds."""
    store = _make_store()
    store.add(_make_trigger())

    results = [store.try_claim_fire("t1", expected_token=0) for _ in range(5)]
    winners = [r for r in results if r[0]]
    losers = [r for r in results if not r[0]]
    assert len(winners) == 1
    assert len(losers) == 4
    for _, _, reason in losers:
        assert "stale fire token" in reason
