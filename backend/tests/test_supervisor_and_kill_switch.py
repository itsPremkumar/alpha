"""Phase 5 and Phase 6: supervision, ceilings, kill switch, fail-closed.

Covers (n) the kill switch stops a running war room, plus the supervisor
invariants, the ENFORCED ceilings, inherited budgets, the cycle guard and the
loop detector's stated failure direction.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import alpha.bots  # noqa: F401  (resolves a pre-existing circular import)
from alpha.bots.delegation import (
    REFUSAL_CYCLE,
    REFUSAL_DEPTH_CEILING,
    REFUSAL_FANOUT_CEILING,
    REFUSAL_HOP_CEILING,
    ChildReport,
    ChildStatus,
    DelegationLimits,
    DelegationTree,
    TreeStatus,
    child_report,
    root_context,
)
from alpha.bots.events import OrgEventStore
from alpha.bots.failure_reasons import (
    ALL_REASONS,
    DELIVERY_TIMEOUT,
    FAILURE_CLASS_CAPABILITY,
    FAILURE_CLASS_TRANSIENT,
    WORKER_CRASH,
    classify_work_failure,
    decide_failure,
    failure_class,
)
from alpha.bots.kill_switch import set_global_kill_switch
from alpha.channels import ledger as channel_ledger
from alpha.groups.supervisor import (
    STEP_ESCALATE,
    STEP_RESCOPE,
    STEP_RETRY_IN_PLACE,
    STEP_UNROUTABLE,
    LoopDetector,
    Supervisor,
)
from alpha.groups.war_room import (
    RunStatus,
    WarRoom,
    build_default_config,
)


@pytest.fixture(autouse=True)
def _kill_switch_is_off():
    """Every test starts from a known kill-switch state."""
    set_global_kill_switch(False, "test setup")
    yield
    set_global_kill_switch(False, "test teardown")


_ROOT = None


def pytest_tmp_root():
    """A single shared scratch root for tests that need a real directory."""
    global _ROOT
    if _ROOT is None:
        import tempfile
        from pathlib import Path

        _ROOT = Path(tempfile.mkdtemp(prefix="ag_supervisor_"))
    return _ROOT


# =================================================================== (n)
def test_n_the_kill_switch_stops_a_running_war_room():
    """(n) The kill switch genuinely stops a war room mid-flight."""
    started = asyncio.Event()

    async def slow_participant(ctx):
        started.set()
        # Long enough that the switch is flipped while this is in flight.
        await asyncio.sleep(5)
        return "should never be counted"

    async def moderator(ctx):
        return "DECISION"

    async def scenario():
        config = build_default_config(
            "kill switch test", ["a", "b"], stage_timeout_seconds=30.0, stage_grace_seconds=1.0
        )
        room = WarRoom(
            config,
            participants={"a": slow_participant, "b": slow_participant},
            moderator=moderator,
            room="kill",
            root=pytest_tmp_root(),
            ledger_store=OrgEventStore(pytest_tmp_root() / "events.jsonl"),
            clock=time.monotonic,
        )
        task = asyncio.ensure_future(room.execute())
        await asyncio.wait_for(started.wait(), timeout=5)
        # Flip the switch while participants are running.
        set_global_kill_switch(True, "operator pressed the stop button")
        run = await asyncio.wait_for(task, timeout=20)
        return run, room

    run, room = asyncio.run(scenario())

    assert run.status == RunStatus.CANCELLED, run.failure_reason
    assert run.terminal, "a cancelled run must be terminal"
    assert "operator pressed the stop button" in (run.killed_by or "")
    # Nothing was published from the interrupted work.
    assert run.synthesis is None
    # And it is on the record.
    persisted = (room.dir / "run.json").read_text(encoding="utf-8")
    assert "cancelled" in persisted


def test_n_a_kill_switch_engaged_before_the_start_refuses_to_open():
    set_global_kill_switch(True, "already stopped")

    async def ok(ctx):
        return "a position"

    root = pytest_tmp_root()
    config = build_default_config("pre-killed", ["a", "b"], stage_timeout_seconds=1.0)
    room = WarRoom(
        config,
        participants={"a": ok, "b": ok},
        room="prekill",
        root=root,
        ledger_store=OrgEventStore(root / "events.jsonl"),
        clock=time.monotonic,
    )
    run = asyncio.run(room.execute())
    assert run.status == RunStatus.CANCELLED
    assert "before start" in run.failure_reason
    # No participant was ever dispatched, so there is nothing to have contributed.
    assert run.all_receipts() == []





def test_a_kill_switch_activation_is_recorded_on_the_ledger(tmp_path):
    """The activation lands in the ledger the store was given, read back from disk.

    The store is constructed over a path inside tmp_path and the assertion
    reads the FILE, not the in-memory deque. That matters: the whole point of
    the fail-closed bridge is that the in-memory view can look fine while the
    write never landed.
    """
    store = OrgEventStore(tmp_path / "events.jsonl")
    set_global_kill_switch(True, "testing the stop")
    try:
        entry = channel_ledger.note_kill_switch(
            "operator", target="fleet", active=True, reason="testing the stop", store=store
        )
    finally:
        set_global_kill_switch(False, "done")

    assert entry.seq > 0
    assert entry.event_type == "channel.kill_switch"
    assert entry.details["active"] is True
    # Present on disk, not merely in memory.
    assert str(store.log_path).startswith(str(tmp_path))
    assert store.log_path.exists()
    entries = channel_ledger.read_ledger(store=store)
    assert any(e.event_type == "channel.kill_switch" for e in entries)


def test_the_kill_switch_refuses_to_start_a_room_it_can_no_longer_read(monkeypatch):
    """A switch that raises is treated as ENGAGED, not as permission to proceed."""
    import alpha.bots.kill_switch as ks

    def boom():
        raise RuntimeError("switch state unavailable")

    monkeypatch.setattr(ks, "is_kill_switch_active", boom)
    root = pytest_tmp_root()

    async def ok(ctx):
        return "a position"

    room = WarRoom(
        build_default_config("switch unreadable", ["a", "b"], stage_timeout_seconds=1.0),
        participants={"a": ok, "b": ok},
        room="unreadable",
        root=root,
        ledger_store=OrgEventStore(root / "unreadable_events.jsonl"),
        clock=time.monotonic,
    )
    run = asyncio.run(room.execute())
    assert run.status == RunStatus.CANCELLED
    assert "unavailable" in run.failure_reason
    assert run.all_receipts() == []


# ============================================== supervisor invariants
def test_a_child_failure_is_never_visible_to_the_parent_as_success():
    """Invariant (a), proved against the existing helper AND independently."""
    # The helper refuses when given error TEXT. (It takes a string, not an
    # exception: the taxonomy classifies text, so the caller formats it.)
    report = child_report("t::a", "a", None, error="RuntimeError: blew up")
    assert report.status is ChildStatus.FAILED
    assert report.ok is False

    # It also refuses on an error-shaped RESULT with no error text.
    report = child_report("t::a", "a", {"error": "it failed"})
    assert report.status is ChildStatus.FAILED

    # A child that returned NOTHING at all is UNKNOWN_OUTCOME, never
    # optimistically read as success: "we did not hear back" is not "it worked".
    assert child_report("t::a", "a", None).status is ChildStatus.UNKNOWN_OUTCOME

    # A real success is COMPLETED, so the check is not vacuous.
    good = child_report("t::a", "a", {"answer": 42})
    assert good.status is ChildStatus.COMPLETED
    assert good.ok is True

    # FINDING, recorded rather than hidden: the existing helper treats an EMPTY
    # STRING result as COMPLETED. That is a real gap in `child_report`, not a
    # property this module can rely on. The war room does not use
    # `child_report` for its own empty-output check for exactly this reason: it
    # classifies EMPTY separately and never counts it as agreement. Asserted
    # here so the gap stays visible and is not mistaken for intended behaviour.
    assert child_report("t::a", "a", "").status is ChildStatus.COMPLETED


def test_a_task_is_not_complete_while_a_descendant_is_unresolved():
    """Invariant (b): unresolved is not success, and not a clean failure."""
    supervisor = Supervisor()

    # A child that failed, with no descendants of its own.
    supervisor.record_child("t1", "a", None, error="RuntimeError: blew up", descendants=None)
    verdict = supervisor.settle("t1")
    assert verdict.complete is False
    assert verdict.status == str(TreeStatus.FAILED)
    assert "a" in verdict.failed_children

    # A child with no result at all: unknown outcome, not success.
    supervisor2 = Supervisor()
    report = child_report("t2::b", "b", None)
    report.status = ChildStatus.UNKNOWN_OUTCOME
    supervisor2.tree_for("t2").admit(supervisor2.tree_for("t2").child_context("b"), report)
    verdict2 = supervisor2.settle("t2")
    assert verdict2.complete is False
    assert verdict2.status == str(TreeStatus.UNRESOLVED)
    assert "unknown outcome" in verdict2.reason

    # All children genuinely succeeded: now it completes.
    supervisor3 = Supervisor()
    supervisor3.record_child("t3", "a", {"ok": True}, descendants=None)
    supervisor3.record_child("t3", "b", {"ok": True}, descendants=None)
    verdict3 = supervisor3.settle("t3")
    assert verdict3.complete is True, verdict3.reason
    assert verdict3.status == str(TreeStatus.COMPLETE)


def test_a_child_whose_own_descendant_failed_is_not_a_success():
    """Invariant (a) propagates one level down."""
    supervisor = Supervisor()
    inner = DelegationTree(supervisor.context_for("t4"))
    inner.admit(
        inner.child_context("grandchild"),
        child_report("t4::a::g", "grandchild", None, error="RuntimeError: deep failure"),
    )
    supervisor.record_child("t4", "a", {"answer": "looks fine"}, descendants=inner)
    verdict = supervisor.settle("t4")
    assert verdict.complete is False
    assert "descendant" in verdict.reason or "failed" in verdict.reason


def test_a_child_whose_descendants_are_still_running_is_not_a_success():
    """Invariant (b) at depth: unsettled descendants block completion."""
    supervisor = Supervisor()
    inner = DelegationTree(supervisor.context_for("t5"))
    # Admit a report that is explicitly still pending by never settling it.
    inner.admit(
        inner.child_context("grandchild"),
        ChildReport(
            child_id="t5::a::g",
            agent="grandchild",
            status=ChildStatus.RUNNING,
            reason="unknown",
        ),
    )
    supervisor.record_child("t5", "a", {"answer": "looks fine"}, descendants=inner)
    verdict = supervisor.settle("t5")
    assert verdict.complete is False


def test_settle_refuses_a_contradictory_receipt():
    """Belt and braces: a receipt marked complete but carrying an error is refused.

    ``ChildReport``'s error field is ``detail``, so that is what is corrupted
    here, the way a bug elsewhere in the codebase would corrupt it.
    """
    supervisor = Supervisor()
    report = child_report("t::a", "a", {"answer": 1})
    assert report.status is ChildStatus.COMPLETED
    # Corrupt it: still marked completed, but now carrying an error.
    report.detail = "something went wrong"
    tree = supervisor.tree_for("t")
    tree.admit(tree.child_context("a"), report)
    verdict = supervisor.settle("t")
    assert verdict.complete is False
    assert verdict.status == "inconsistent"
    assert "contradictory receipt" in verdict.reason
    assert verdict.failed_children == ("a",)


# ============================================== ceilings are ENFORCED
def test_depth_fanout_and_hop_ceilings_are_enforced_not_merely_configured():
    limits = DelegationLimits(max_depth=2, max_fanout=2, max_hops=3)
    ctx = root_context("alpha", "t", limits=limits)

    # Depth 0 is fine; the ceiling bites as it is approached.
    assert ctx.check().allowed is True
    child = ctx.for_child("a")
    assert child.depth == 1
    assert child.check().allowed is True
    deeper = child.for_child("b")
    assert deeper.depth == 2
    verdict = deeper.check()
    assert verdict.allowed is False
    assert verdict.code == REFUSAL_DEPTH_CEILING

    # Fan-out is a per-node count.
    node = ctx.for_child("a")
    used = node
    for i in range(limits.max_fanout):
        used = used.charge()
    assert used.check().allowed is False
    assert used.check_fanout().code == REFUSAL_FANOUT_CEILING

    # Hops are whole-tree: they increment on every hand-off. The depth ceiling
    # is checked FIRST, so reaching the hop ceiling requires widening the depth
    # allowance enough for the hop count to be the binding constraint.
    hop_limits = DelegationLimits(max_depth=10, max_fanout=10, max_hops=3)
    hop_ctx = root_context("alpha", "t", limits=hop_limits)
    hops = hop_ctx
    for i in range(hop_limits.max_hops):
        hops = hops.for_child(f"n{i}")
    assert hops.hops == hop_limits.max_hops
    assert hops.check().code == REFUSAL_HOP_CEILING
    assert hops.check_depth().allowed is True, "depth should not be the binding constraint here"


def test_the_supervisor_refuses_a_step_that_would_exceed_a_ceiling():
    """A runaway tree is stopped at the supervisor, not merely described."""
    supervisor = Supervisor(limits=DelegationLimits(max_depth=1, max_fanout=1, max_hops=2))
    # Walk the context to its depth ceiling.
    ctx = supervisor.context_for("t")
    supervisor._contexts["t"] = ctx.for_child("a").for_child("b")
    decision = supervisor.next_step("t", "do work", required_capability_tags=["observe"])
    assert decision.dispatched is False
    assert decision.guard["allowed"] is False
    assert "delegation bound refused" in decision.reason


def test_budgets_are_inherited_so_depth_cannot_multiply_spend():
    """The child's budget is a share of the parent's REMAINDER, never more."""
    parent = root_context(
        "alpha", "t", limits=DelegationLimits(child_token_share=0.5), token_budget=1000
    )
    child = parent.for_child("a")
    assert child.token_budget == 500, "the child got the parent's whole budget"
    grandchild = child.for_child("b")
    assert grandchild.token_budget == 250, "budget multiplied with depth"
    assert grandchild.tokens_spent == 0, "a child inherited its parent's spend"
    # And a spend is capped by the remainder.
    assert child.tokens_remaining == 500
    spent = child.charge(tokens=10_000)
    assert spent.tokens_remaining == 0
    assert spent.check_budget().allowed is False


def test_the_cycle_guard_prevents_ping_pong():
    """A task must not bounce back to a bot that already held it."""
    ctx = root_context("alpha", "t").for_child("a").for_child("b")
    assert ctx.check_cycle("a").allowed is False
    assert ctx.check_cycle("a").code == REFUSAL_CYCLE
    assert ctx.check_cycle("A").allowed is False, "the cycle guard is case sensitive"
    # A genuinely new agent is allowed.
    assert ctx.check_cycle("c").allowed is True


# ============================================== the existing taxonomy
def test_reassignment_uses_the_existing_taxonomy_untouched():
    """Retry-in-place versus route-elsewhere, decided by the EXISTING taxonomy."""
    supervisor = Supervisor()

    # A TRANSIENT failure retries the SAME agent. `DELIVERY_TIMEOUT` is the
    # taxonomy's own transient-timeout code.
    #
    # FINDING about the existing taxonomy, recorded rather than papered over:
    # `classify_work_failure` is a TEXT classifier and the string
    # "delivery timeout" is not in its needle lists, so free text alone never
    # reaches the transient class. `decide_failure` DOES map an explicit
    # `DELIVERY_TIMEOUT` to `retry`. So a caller that knows the code must pass
    # it, which is why `on_failure` accepts `reason=`. This module does not
    # extend the taxonomy to fix that; it routes around it without weakening it.
    assert classify_work_failure("delivery timeout") == "unknown", (
        "the text classifier now recognises this phrase; the explicit-reason "
        "path is still correct but this note should be revisited"
    )
    timeout_decision = supervisor.on_failure(
        "t", "agent-a", "the relay never acknowledged the delivery", reason=DELIVERY_TIMEOUT
    )
    assert timeout_decision.step == STEP_RETRY_IN_PLACE
    assert timeout_decision.target == "agent-a"
    assert timeout_decision.reason_class == FAILURE_CLASS_TRANSIENT
    assert timeout_decision.failure_reason == DELIVERY_TIMEOUT

    # Without an explicit reason the text is classified, and an unrecognised
    # string fails closed to permanent rather than guessing transient.
    unknown_decision = supervisor.on_failure(
        "t", "agent-a", "something nobody has a name for", attempt=1, max_attempts=3
    )
    assert unknown_decision.reason_class == "permanent"
    assert "classified from" in unknown_decision.reason

    # A capability gap is not a bad attempt: route elsewhere.
    capability_decision = supervisor.on_failure(
        "t", "agent-a", "no such tool: the agent lacks the skill", attempt=1, max_attempts=3
    )
    assert capability_decision.step == STEP_RESCOPE, capability_decision.reason
    assert capability_decision.target is None
    assert capability_decision.reason_class == FAILURE_CLASS_CAPABILITY

    # The attempt ceiling is authoritative and outranks the class.
    exhausted = supervisor.on_failure(
        "t", "agent-a", "the relay never acknowledged", reason=DELIVERY_TIMEOUT, attempt=3, max_attempts=3
    )
    assert exhausted.step == STEP_ESCALATE
    assert "3/3" in exhausted.reason

    # And the taxonomy constants themselves are unchanged.
    assert len(ALL_REASONS) == 19
    assert failure_class("not_a_real_code") == "permanent", "an unknown code must fail closed"
    assert classify_work_failure("worker died") == WORKER_CRASH
    assert decide_failure("worker_crash", attempt=1, max_attempts=2).action == "resume"


def test_the_supervisor_refuses_to_guess_a_target_by_name():
    """Selection by name is how mismatched agents get work they cannot do."""
    supervisor = Supervisor()  # no dispatcher wired
    decision = supervisor.next_step("t", "do work", required_capability_tags=["observe"])
    assert decision.step == STEP_UNROUTABLE
    assert decision.target is None
    assert "by name" in decision.reason


def test_required_tags_come_from_the_caller_not_from_a_department():
    supervisor = Supervisor()
    explicit = supervisor.required_tags("anything", ["SQL", "observe", "sql"])
    assert explicit == ("observe", "sql")
    # With nothing explicit, the domain classifier supplies derived tags.
    derived = supervisor.required_tags("audit this python module for security bugs")
    assert derived, "no capability tags were derived from the objective"
    assert all(t.startswith(("domain:", "complexity:")) for t in derived)


def test_the_model_tier_decision_carries_the_routers_own_reason():
    supervisor = Supervisor()
    decision = supervisor.model_tier("write a migration", role="coder", complexity="high")
    assert "tier" in decision
    assert decision.get("reasoning"), "the model decision has no recorded reason"


# ============================================== loop detector
def test_the_loop_detector_catches_a_repeating_signature():
    detector = LoopDetector(max_repeats=3)
    # One observation is never enough to accuse anybody.
    looping, reason = detector.observe("t", {"state": "stuck"})
    assert looping is False, reason
    detector.observe("t", {"state": "stuck"})
    looping, reason = detector.observe("t", {"state": "stuck"})
    assert looping is True
    assert "stuck" in reason


def test_the_loop_detector_does_not_kill_productive_long_work():
    """Its stated failure direction: it fails toward ALLOWING hard work.

    An agent whose state keeps changing is never flagged, no matter how many
    observations there are. That is the deliberate trade: the alternative (a
    wall-clock kill) is what kills the hardest tasks.
    """
    detector = LoopDetector(max_repeats=3)
    for i in range(50):
        looping, _ = detector.observe("t", {"progress": i})
        assert looping is False, f"productive work was flagged at observation {i}"


def test_the_loop_detector_needs_more_than_one_observation_to_accuse():
    detector = LoopDetector(max_repeats=2)
    looping, _ = detector.observe("t", "same")
    assert looping is False
    with pytest.raises(ValueError):
        LoopDetector(max_repeats=1)


def test_forgetting_a_task_clears_its_loop_history():
    detector = LoopDetector(max_repeats=2)
    detector.observe("t", "same")
    detector.observe("t", "same")
    looping, _ = detector.observe("t", "same")
    assert looping is True
    detector.forget("t")
    looping, _ = detector.observe("t", "same")
    assert looping is False


# ============================================== ledger fail-closed
class BrokenSink(OrgEventStore):
    def __init__(self, log_path=None):
        super().__init__(log_path)

    def append_event(self, event_type, actor, *, target=None, details=None):
        import builtins

        real_open = builtins.open

        def fake_open(path, mode="r", *args, **kwargs):
            if str(path) == str(self.log_path) and "a" in mode:
                import io

                return io.StringIO()
            return real_open(path, mode, *args, **kwargs)

        builtins.open = fake_open
        try:
            return super().append_event(event_type, actor, target=target, details=details)
        finally:
            builtins.open = real_open


def test_a_ledger_write_failure_refuses_rather_than_degrading_to_allow(tmp_path):
    broken = BrokenSink(tmp_path / "events.jsonl")
    with pytest.raises(channel_ledger.LedgerUnavailable):
        channel_ledger.append(
            channel_ledger.EV_DISPATCH,
            actor="alpha",
            target="agent",
            reason="this must not be lost",
            store=broken,
        )


def test_a_dispatch_that_cannot_be_recorded_is_not_reported_as_delivered():
    from alpha.channels.mentions import parse_mentions
    from alpha.channels.routing import dispatch_resolution

    broken = BrokenSink(pytest_tmp_root() / "events.jsonl")

    async def handler(target, body, resolution):
        return "answered"

    resolution = parse_mentions("@alice go", roster=["alice"], roles={})
    with pytest.raises(channel_ledger.LedgerUnavailable):
        asyncio.run(
            dispatch_resolution(
                resolution, handler, sender="user", room="r", ledger_store=broken
            )
        )


def test_the_ledger_is_gap_free_across_mixed_event_kinds(tmp_path):
    """Messages, dispatches and stage transitions share ONE ordered sequence."""
    store = OrgEventStore(tmp_path / "events.jsonl")
    channel_ledger.note_message("alice", target="room", body_chars=10, seq=1, store=store)
    channel_ledger.note_mention(
        "alice", target="room", resolution_line="@bob->bob", ok=True, store=store
    )
    channel_ledger.note_stage("wrun_1", actor="war_room", stage="positions", previous="open", status="running", store=store)
    channel_ledger.note_dispatch(
        "alpha", target="bob", objective="do work", reason="capability match", store=store
    )
    channel_ledger.note_kill_switch("op", target="fleet", active=False, reason="test", store=store)
    channel_ledger.verify_ledger_ordered_and_gap_free(store=store)
    entries = channel_ledger.read_ledger(store=store)
    assert [e.seq for e in entries] == [1, 2, 3, 4, 5]
    # And it is human-readable, one line per entry.
    for entry in entries:
        line = entry.human_line()
        assert line.startswith(f"#{entry.seq}")
        assert entry.event_type in line
