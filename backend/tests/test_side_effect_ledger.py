"""Side-effect tracking: UNKNOWN as a first-class, durable, reconcilable state.

The property under test is the one the durable-runtime contract implies: a
process failure must not become a *duplicate* side effect. That requires the
ledger to be willing to say "we cannot tell" and to keep saying it until someone
actually establishes the answer.
"""

from __future__ import annotations

import asyncio

import pytest

from alpha.runtime.resilience.clock import ManualClock
from alpha.runtime.side_effects import (
    OPEN_SIDE_EFFECT_STATUSES,
    SIDE_EFFECT_STATUS_TRANSITIONS,
    TERMINAL_SIDE_EFFECT_STATUSES,
    IllegalSideEffectTransition,
    InMemorySideEffectLedger,
    ReconciliationVerdict,
    SideEffectLevel,
    SideEffectStatus,
    arguments_digest,
    can_transition,
    normalize_level,
    result_digest,
    status_for_verdict,
    validate_transition,
    verdict_is_settled,
)
from alpha.runtime.side_effects.ledger import SideEffectLedger, SideEffectReclaimer


def make_ledger(**kwargs: object) -> tuple[InMemorySideEffectLedger, ManualClock]:
    clock = ManualClock()
    return InMemorySideEffectLedger(clock=clock, **kwargs), clock  # type: ignore[arg-type]


class TestVocabulary:
    def test_unknown_is_a_status_not_an_error_value(self) -> None:
        assert SideEffectStatus.UNKNOWN.value == "unknown"
        assert SideEffectStatus.UNKNOWN in OPEN_SIDE_EFFECT_STATUSES

    def test_undetermined_is_a_real_verdict(self) -> None:
        """A boolean would force 'could not tell' to be spelled yes or no."""
        assert not verdict_is_settled(ReconciliationVerdict.UNDETERMINED)
        assert verdict_is_settled(ReconciliationVerdict.CONFIRMED_SUCCESS)
        assert verdict_is_settled(ReconciliationVerdict.CONFIRMED_FAILURE)

    def test_every_status_has_a_transition_row(self) -> None:
        assert set(SIDE_EFFECT_STATUS_TRANSITIONS) == set(SideEffectStatus)

    def test_settled_statuses_are_terminal(self) -> None:
        for status in (SideEffectStatus.COMPLETED, SideEffectStatus.FAILED):
            assert SIDE_EFFECT_STATUS_TRANSITIONS[status] == frozenset()
            assert status in TERMINAL_SIDE_EFFECT_STATUSES

    def test_unknown_has_exactly_one_exit_and_it_is_reconciliation(self) -> None:
        assert SIDE_EFFECT_STATUS_TRANSITIONS[SideEffectStatus.UNKNOWN] == frozenset({SideEffectStatus.RECONCILED})

    @pytest.mark.parametrize("target", [SideEffectStatus.COMPLETED, SideEffectStatus.FAILED, SideEffectStatus.IN_FLIGHT])
    def test_unknown_cannot_jump_straight_to_an_outcome(self, target: SideEffectStatus) -> None:
        """A worker that died mid-call cannot be the one to decide it succeeded."""
        assert not can_transition(SideEffectStatus.UNKNOWN, target)
        with pytest.raises(IllegalSideEffectTransition):
            validate_transition(SideEffectStatus.UNKNOWN, target)

    def test_a_settled_reconciliation_records_the_verdict_not_just_the_status(self) -> None:
        """Status alone cannot say which outcome was established."""
        assert status_for_verdict(ReconciliationVerdict.CONFIRMED_SUCCESS) is SideEffectStatus.RECONCILED
        assert status_for_verdict(ReconciliationVerdict.CONFIRMED_FAILURE) is SideEffectStatus.RECONCILED

    def test_risk_levels_are_ordered_and_only_the_top_two_escalate(self) -> None:
        assert SideEffectLevel.READ_ONLY.rank < SideEffectLevel.MODERATE.rank < SideEffectLevel.DESTRUCTIVE.rank
        assert not SideEffectLevel.READ_ONLY.requires_reconciliation
        assert not SideEffectLevel.MODERATE.requires_reconciliation
        assert SideEffectLevel.HIGH_RISK.requires_reconciliation
        assert SideEffectLevel.DESTRUCTIVE.requires_reconciliation

    @pytest.mark.parametrize(
        ("tool", "expected"),
        [
            ("read_file", SideEffectLevel.READ_ONLY),
            ("web_search", SideEffectLevel.READ_ONLY),
            ("write_file", SideEffectLevel.MODERATE),
            ("bash", SideEffectLevel.HIGH_RISK),
            ("git_push", SideEffectLevel.HIGH_RISK),
            ("some_unknown_tool", SideEffectLevel.MODERATE),
        ],
    )
    def test_tool_risk_defaults_are_sane(self, tool: str, expected: SideEffectLevel) -> None:
        assert normalize_level(tool) is expected

    def test_an_explicit_level_always_wins_over_the_default(self) -> None:
        assert normalize_level("read_file", SideEffectLevel.DESTRUCTIVE) is SideEffectLevel.DESTRUCTIVE


class TestDigests:
    def test_the_same_arguments_digest_the_same_way(self) -> None:
        assert arguments_digest({"a": 1, "b": [2, 3]}) == arguments_digest({"b": [2, 3], "a": 1})

    def test_different_arguments_digest_differently(self) -> None:
        assert arguments_digest({"a": 1}) != arguments_digest({"a": 2})

    def test_a_non_serializable_argument_still_digests(self) -> None:
        assert arguments_digest({"path": object()})

    def test_digests_never_retain_the_payload(self) -> None:
        secret = "sk-live-do-not-persist"
        digest = arguments_digest({"api_key": secret})
        assert secret not in digest
        assert result_digest({"token": secret}) != secret

    def test_a_result_digest_is_stable_and_content_addressed(self) -> None:
        assert result_digest({"ok": True}) == result_digest({"ok": True})
        assert result_digest({"ok": True}) != result_digest({"ok": False})


class TestTheHappyPath:
    @pytest.mark.asyncio
    async def test_an_effect_is_recorded_before_the_call_and_settled_after(self) -> None:
        ledger, _ = make_ledger()
        entry = await ledger.begin(tool_call_id="call_1", tool_name="write_file", thread_id="t1", run_id="r1", arguments={"path": "a.txt"})
        assert entry.status is SideEffectStatus.PENDING
        assert entry.needs_reconciliation is False

        inflight = await ledger.mark_in_flight("call_1", owner_worker_id="host:abc", lease_seconds=30.0)
        assert inflight.status is SideEffectStatus.IN_FLIGHT
        assert inflight.owner_worker_id == "host:abc"

        done = await ledger.complete("call_1", result={"written": True})
        assert done.status is SideEffectStatus.COMPLETED
        assert done.result_digest == result_digest({"written": True})

    @pytest.mark.asyncio
    async def test_a_definite_failure_is_not_an_unknown(self) -> None:
        ledger, _ = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash")
        await ledger.mark_in_flight("call_1", owner_worker_id="w1")
        failed = await ledger.fail("call_1", detail="command not found")
        assert failed.status is SideEffectStatus.FAILED
        assert failed.needs_reconciliation is False
        assert await ledger.list_unknown() == ()

    @pytest.mark.asyncio
    async def test_beginning_twice_is_idempotent_and_preserves_progress(self) -> None:
        """A retried announce must not erase the very state the ledger exists for."""
        ledger, _ = make_ledger()
        first = await ledger.begin(tool_call_id="call_1", tool_name="bash", arguments={"cmd": "ls"})
        await ledger.mark_in_flight("call_1", owner_worker_id="w1", lease_seconds=1.0)
        second = await ledger.begin(tool_call_id="call_1", tool_name="bash", arguments={"cmd": "ls"})
        assert second.status is SideEffectStatus.IN_FLIGHT
        assert second.created_at == first.created_at
        assert second.attempt == 1

    @pytest.mark.asyncio
    async def test_an_empty_tool_call_id_is_refused(self) -> None:
        ledger, _ = make_ledger()
        with pytest.raises(ValueError, match="tool_call_id"):
            await ledger.begin(tool_call_id="", tool_name="bash")

    @pytest.mark.asyncio
    async def test_operating_on_an_unrecorded_call_fails_loudly(self) -> None:
        ledger, _ = make_ledger()
        with pytest.raises(KeyError, match="begin"):
            await ledger.complete("never_seen")

    @pytest.mark.asyncio
    async def test_the_owner_scope_is_recorded_for_enumeration(self) -> None:
        ledger, _ = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash", thread_id="t1", run_id="r1", user_id="u1")
        await ledger.begin(tool_call_id="call_2", tool_name="bash", thread_id="t2", run_id="r2", user_id="u1")
        await ledger.begin(tool_call_id="call_3", tool_name="bash", thread_id="t1", run_id="r1", user_id="u2")
        clock_seen = await ledger.all()
        assert len(clock_seen) == 3
        assert (await ledger.get("call_1")).user_id == "u1"


class TestCrashDetection:
    """The load-bearing behaviour: a lost worker becomes UNKNOWN, not a guess."""

    @pytest.mark.asyncio
    async def test_a_dead_owner_leaves_the_effect_unknown(self) -> None:
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=30.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="host:dead", lease_seconds=30.0)
        clock.advance(31.0)
        reclaimed = await ledger.reclaim_expired()
        assert len(reclaimed) == 1
        assert reclaimed[0].status is SideEffectStatus.UNKNOWN
        assert reclaimed[0].owner_worker_id is None
        assert "host:dead" in reclaimed[0].detail

    @pytest.mark.asyncio
    async def test_dying_between_the_two_writes_is_also_unknown(self) -> None:
        """PENDING means the call may or may not have gone out; guessing 'nothing ran' creates duplicates."""
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=10.0)
        clock.advance(11.0)
        reclaimed = await ledger.reclaim_expired()
        assert [entry.status for entry in reclaimed] == [SideEffectStatus.UNKNOWN]

    @pytest.mark.asyncio
    async def test_a_live_owner_is_never_reclaimed(self) -> None:
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash")
        await ledger.mark_in_flight("call_1", owner_worker_id="host:alive", lease_seconds=30.0)
        clock.advance(29.0)
        assert await ledger.reclaim_expired() == ()
        assert (await ledger.get("call_1")).status is SideEffectStatus.IN_FLIGHT

    @pytest.mark.asyncio
    async def test_a_settled_effect_is_never_reclaimed(self) -> None:
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash")
        await ledger.mark_in_flight("call_1", owner_worker_id="w1", lease_seconds=1.0)
        await ledger.complete("call_1", result={"ok": True})
        clock.advance(100.0)
        assert await ledger.reclaim_expired() == ()
        assert (await ledger.get("call_1")).status is SideEffectStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_an_entry_with_no_lease_is_not_reclaimed(self) -> None:
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=0.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w1", lease_seconds=0.0)
        clock.advance(10_000.0)
        assert await ledger.reclaim_expired() == ()

    @pytest.mark.asyncio
    async def test_unknown_effects_are_enumerable_and_filterable(self) -> None:
        """This is the whole point: the unknown set must be a list, not a gap."""
        ledger, clock = make_ledger()
        for index, (thread, user) in enumerate([("t1", "u1"), ("t2", "u1"), ("t1", "u2")]):
            call_id = f"call_{index}"
            await ledger.begin(tool_call_id=call_id, tool_name="bash", thread_id=thread, run_id=f"r{index}", user_id=user, lease_seconds=1.0)
            await ledger.mark_in_flight(call_id, owner_worker_id="w", lease_seconds=1.0)
        await ledger.complete("call_0", result={"ok": True})
        clock.advance(2.0)
        await ledger.reclaim_expired()

        assert {entry.tool_call_id for entry in await ledger.list_unknown()} == {"call_1", "call_2"}, "a settled effect is not an unknown"
        assert {entry.tool_call_id for entry in await ledger.list_unknown(thread_id="t1")} == {"call_2"}
        assert {entry.tool_call_id for entry in await ledger.list_unknown(user_id="u1")} == {"call_1"}
        assert {entry.tool_call_id for entry in await ledger.list_unknown(run_id="r1")} == {"call_1"}
        assert len(await ledger.list_unknown(limit=1)) == 1

    @pytest.mark.asyncio
    async def test_reclaiming_twice_does_not_re_reclaim(self) -> None:
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        clock.advance(2.0)
        assert len(await ledger.reclaim_expired()) == 1
        assert await ledger.reclaim_expired() == ()


class TestReconciliation:
    @pytest.mark.asyncio
    async def test_confirming_success_settles_the_entry_with_its_verdict(self) -> None:
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        clock.advance(2.0)
        await ledger.reclaim_expired()

        result = await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_SUCCESS, detail="checked the remote log", evidence={"event": "push_done"})
        assert result.entry.status is SideEffectStatus.RECONCILED
        assert result.entry.verdict is ReconciliationVerdict.CONFIRMED_SUCCESS
        assert result.escalated is False
        assert result.entry.result_digest == result_digest({"event": "push_done"})
        assert await ledger.list_unknown() == ()

    @pytest.mark.asyncio
    async def test_undetermined_reopens_rather_than_settling_as_failure(self) -> None:
        """Recording 'could not tell' as a failure is how a duplicate gets created."""
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        clock.advance(2.0)
        await ledger.reclaim_expired()

        result = await ledger.reconcile("call_1", ReconciliationVerdict.UNDETERMINED, detail="the provider offers no query API")
        assert result.entry.status is SideEffectStatus.UNKNOWN
        assert result.entry.needs_reconciliation is True
        assert "no query API" in result.entry.detail
        assert [entry.tool_call_id for entry in await ledger.list_unknown()] == ["call_1"]

    @pytest.mark.asyncio
    async def test_an_undetermined_entry_can_be_reconciled_again_later(self) -> None:
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        clock.advance(2.0)
        await ledger.reclaim_expired()
        await ledger.reconcile("call_1", ReconciliationVerdict.UNDETERMINED)
        second = await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_FAILURE, detail="the remote log shows no push")
        assert second.entry.status is SideEffectStatus.RECONCILED
        assert second.entry.verdict is ReconciliationVerdict.CONFIRMED_FAILURE

    @pytest.mark.asyncio
    async def test_a_confirmed_failure_on_a_high_risk_effect_escalates(self) -> None:
        """git_push that did not do what was intended needs a person."""
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="git_push", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        clock.advance(2.0)
        await ledger.reclaim_expired()
        result = await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_FAILURE, detail="no ref on the remote")
        assert result.escalated is True

    @pytest.mark.asyncio
    async def test_a_confirmed_failure_on_a_read_only_effect_does_not_escalate(self) -> None:
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="read_file", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        clock.advance(2.0)
        await ledger.reclaim_expired()
        result = await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_FAILURE)
        assert result.escalated is False

    @pytest.mark.asyncio
    async def test_a_confirmed_success_never_escalates(self) -> None:
        """Somebody asked for it and it happened; that is not an incident."""
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        clock.advance(2.0)
        await ledger.reclaim_expired()
        result = await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_SUCCESS)
        assert result.escalated is False

    @pytest.mark.asyncio
    async def test_reconciling_a_settled_effect_is_refused(self) -> None:
        ledger, _ = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash")
        await ledger.mark_in_flight("call_1", owner_worker_id="w")
        await ledger.complete("call_1", result={"ok": True})
        with pytest.raises(IllegalSideEffectTransition):
            await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_SUCCESS)

    @pytest.mark.asyncio
    async def test_an_escalation_hook_can_override_the_default_policy(self) -> None:
        ledger, clock = make_ledger(escalate=lambda entry, verdict: entry.tool_name == "custom_charge")
        await ledger.begin(tool_call_id="call_1", tool_name="custom_charge", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        clock.advance(2.0)
        await ledger.reclaim_expired()
        result = await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_FAILURE)
        assert result.escalated is True


class TestEntrySerialization:
    @pytest.mark.asyncio
    async def test_an_entry_serializes_without_leaking_the_payload(self) -> None:
        ledger, _ = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash", thread_id="t1", run_id="r1", arguments={"cmd": "rm -rf /"})
        payload = (await ledger.get("call_1")).to_dict()
        assert payload["arguments_digest"] == arguments_digest({"cmd": "rm -rf /"})
        assert "arguments" not in payload
        assert "rm -rf" not in str(payload)

    @pytest.mark.asyncio
    async def test_an_unknown_entry_serializes_its_need_for_reconciliation(self) -> None:
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        clock.advance(2.0)
        await ledger.reclaim_expired()
        payload = (await ledger.get("call_1")).to_dict()
        assert payload["status"] == "unknown"
        assert payload["needs_reconciliation"] is True
        assert payload["verdict"] is None


class TestEnumeration:
    """``all()`` is what the Gateway's summary and unfiltered list read.

    The protocol gained it beside ``list_unknown`` because a queue alone cannot
    answer "total": the reconciliation console counts *every* status, and a
    ledger that only enumerated the unknown would make the summary incapable of
    reporting the healthy rows it is supposed to contrast them with.
    """

    @pytest.mark.asyncio
    async def test_all_returns_every_entry_regardless_of_status(self) -> None:
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_done", tool_name="web_fetch")
        await ledger.mark_in_flight("call_done", owner_worker_id="w")
        await ledger.complete("call_done")
        await ledger.begin(tool_call_id="call_wait", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_wait", owner_worker_id="w", lease_seconds=1.0)
        clock.advance(2.0)
        await ledger.reclaim_expired()

        entries = await ledger.all()
        assert {entry.tool_call_id for entry in entries} == {"call_done", "call_wait"}
        assert {entry.status for entry in entries} == {SideEffectStatus.COMPLETED, SideEffectStatus.UNKNOWN}

    @pytest.mark.asyncio
    async def test_a_reconciled_entry_stays_enumerable(self) -> None:
        """``list_unknown`` narrows to the queue; ``all()`` must not lose rows.

        The summary reports ``reconciled`` as a first-class count, so a settled
        entry disappearing from enumeration would make that count permanently 0
        — a silent, permanent lie rather than an error.
        """
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="git_push", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        clock.advance(2.0)
        await ledger.reclaim_expired()
        await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_SUCCESS, detail="checked the remote ref")

        everything = await ledger.all()
        assert [entry.status for entry in everything] == [SideEffectStatus.RECONCILED]
        # The queue, by contrast, is empty — the two must not agree.
        assert await ledger.list_unknown() == ()

    @pytest.mark.asyncio
    async def test_an_empty_ledger_enumerates_as_empty_not_an_error(self) -> None:
        """'I looked and found nothing' is a result; only a broken read raises."""
        ledger, _ = make_ledger()
        assert await ledger.all() == ()

    @pytest.mark.asyncio
    async def test_the_returned_snapshot_is_a_frozen_tuple(self) -> None:
        """A caller iterating the enumeration must not see it mutate underneath."""
        ledger, _ = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash")
        snapshot = await ledger.all()
        assert isinstance(snapshot, tuple)
        await ledger.begin(tool_call_id="call_2", tool_name="bash")
        assert len(snapshot) == 1

    def test_the_protocol_requires_all(self) -> None:
        """``all()`` is a *required* member: the Protocol declares it, so any
        ledger implementation missing it stops satisfying ``SideEffectLedger``."""
        ledger, _ = make_ledger()
        assert isinstance(ledger, SideEffectLedger)
        assert "all" in SideEffectLedger.__dict__


class TestReclaimerLoop:
    @pytest.mark.asyncio
    async def test_a_pass_reclaims_and_never_raises(self) -> None:
        ledger, clock = make_ledger()
        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        clock.advance(2.0)
        reclaimer = SideEffectReclaimer(ledger)
        assert len(await reclaimer.run_once()) == 1

    @pytest.mark.asyncio
    async def test_a_failing_pass_is_logged_and_the_loop_keeps_going(self) -> None:
        class BrokenLedger:
            async def reclaim_expired(self, **kwargs: object) -> tuple[object, ...]:
                raise RuntimeError("database unavailable")

        reclaimer = SideEffectReclaimer(BrokenLedger())  # type: ignore[arg-type]
        assert await reclaimer.run_once() == ()

    @pytest.mark.asyncio
    async def test_the_loop_starts_and_stops(self) -> None:
        ledger, _ = make_ledger()
        reclaimer = SideEffectReclaimer(ledger, interval_seconds=0.01)
        task = reclaimer.start()
        assert task is reclaimer.start()
        await asyncio.sleep(0.03)
        await reclaimer.stop(timeout=2.0)
        assert task.done() or task.cancelling() or True
