"""Phase 1: the war room runs staged, clocked, bounded deliberation.

Covers the required assertions (a) staged/clocked/bounded run to synthesis,
(b) a wedged participant ends the run in a typed timeout, (c) empty synthesis
FAILS, (d) one member's failure does not degrade a healthy sibling, and the
transcript invariants.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from alpha.bots.events import OrgEventStore
from alpha.channels.transcript import TranscriptStore
from alpha.groups.war_room import (
    MemberStatus,
    RunStatus,
    WarRoom,
    WarRoomConfig,
    build_default_config,
)


@pytest.fixture
def env(tmp_path):
    return {"root": tmp_path, "store": OrgEventStore(tmp_path / "events.jsonl")}


def make_room(env, participants, *, policy="majority", timeout=1.0, grace=0.3, moderator=None, room="wr"):
    config = build_default_config(
        "should we ship the war room",
        list(participants),
        stage_timeout_seconds=timeout,
        stage_grace_seconds=grace,
        quorum_policy=policy,
    )
    impls = {name: fn for name, fn in participants.items()} if isinstance(participants, dict) else {}
    return WarRoom(
        config,
        participants=impls,
        moderator=moderator,
        room=room,
        root=env["root"],
        ledger_store=env["store"],
        clock=time.monotonic,
    )


# ---------------------------------------------------------------- (a) + (d)
def test_a_runs_staged_clocked_bounded_deliberation_to_a_synthesis(env):
    """(a) The room opens, advances on a clock, and reaches a real synthesis."""
    order: list[str] = []

    async def alice(ctx):
        order.append(f"alice:{ctx.stage.name}")
        return f"alice says ship it because of {ctx.topic}"

    async def bob(ctx):
        order.append(f"bob:{ctx.stage.name}")
        return "bob agrees, with caveats"

    async def moderator(ctx):
        order.append("moderator")
        return "DECISION: ship behind a flag"

    room = make_room(
        env,
        {"alice": alice, "bob": bob},
        moderator=moderator,
    )
    run = asyncio.run(room.execute())

    assert run.status == RunStatus.SUCCEEDED, run.failure_reason
    assert run.synthesis == "DECISION: ship behind a flag"
    # Every stage ran, in order, and the collecting stages came before synthesis.
    assert [s.name for s in run.stages] == ["positions", "cross_exam", "synthesis"]
    assert order.index("alice:positions") < order.index("alice:cross_exam") < order.index("moderator")
    # Each collecting stage really gathered both members, with timing recorded.
    for stage in run.stages[:2]:
        assert {r.participant for r in stage.receipts} == {"alice", "bob"}
        assert all(r.duration_ms >= 0 for r in stage.receipts)


# ------------------------------------------------------------------- (a) quorum
@pytest.mark.parametrize(
    ("policy", "expected_required"),
    [("all", 3), ("any", 1), ("majority", 2), ("supermajority", 3)],
)
def test_quorum_policy_is_explicit_and_reported(env, policy, expected_required):
    """The room REPORTS which policy it used, and enforces that policy's count."""
    config = build_default_config(
        "policy check", ["a", "b", "c"], quorum_policy=policy
    )
    assert config.required_votes() == expected_required

    async def ok(ctx):
        return "a position"

    async def moderator(ctx):
        return "synthesis"

    room = WarRoom(
        config,
        participants={"a": ok, "b": ok, "c": ok},
        moderator=moderator,
        root=env["root"],
        ledger_store=env["store"],
        clock=time.monotonic,
        room=f"pol-{policy}",
    )
    run = asyncio.run(room.execute())
    assert run.final_quorum is not None
    assert run.final_quorum.policy == policy
    assert run.final_quorum.required_votes == expected_required
    assert run.final_quorum.eligible_voters == 3


def test_quorum_uses_the_quorum_engine_and_records_the_proposal(env):
    """Quorum is decided by the reachable QuorumEngine, not a parallel tally."""
    from alpha.groups.quorum import QuorumEngine

    engine = QuorumEngine()

    async def ok(ctx):
        return "a position"

    async def moderator(ctx):
        return "synthesis"

    config = build_default_config("engine check", ["a", "b"], quorum_policy="majority")
    room = WarRoom(
        config,
        participants={"a": ok, "b": ok},
        moderator=moderator,
        room="eng",
        root=env["root"],
        ledger_store=env["store"],
        clock=time.monotonic,
        engine=engine,
    )
    run = asyncio.run(room.execute())
    proposal_id = run.final_quorum.proposal_id
    assert proposal_id, "no QuorumEngine proposal was recorded"
    proposal = engine.get_proposal(proposal_id)
    assert proposal is not None, "QuorumEngine has no record of the run's vote"
    assert set(proposal.votes) == {"a", "b"}


# ---------------------------------------------------------------------- (b)
def test_b_wedged_participant_ends_the_run_in_a_typed_timeout(env):
    """(b) A wedged participant must NOT hold the room open forever."""
    started = time.monotonic()

    async def healthy(ctx):
        return "I delivered"

    async def wedged(ctx):
        await asyncio.sleep(3600)  # never returns
        return "unreachable"

    async def moderator(ctx):
        return "synthesis"

    # quorum=all, so the wedge alone must fail the stage.
    room = make_room(
        env,
        {"healthy": healthy, "wedged": wedged},
        policy="all",
        timeout=0.5,
        grace=0.2,
        moderator=moderator,
    )
    run = asyncio.run(room.execute())
    elapsed = time.monotonic() - started

    assert run.status == RunStatus.TIMEOUT, run.failure_reason
    assert run.status != "running", "the run must never be left in a non-terminal state"
    assert run.terminal is not False, "TIMEOUT must be terminal"
    # It actually bounded the wait rather than hanging.
    assert elapsed < 20, f"the run waited {elapsed:.1f}s, so it did not bound the wedge"
    # The wedge is recorded as a timeout in its own receipt, by name.
    wedge_receipts = [r for r in run.all_receipts() if r.participant == "wedged"]
    assert wedge_receipts, "the wedged participant has no receipt at all"
    assert all(r.status == MemberStatus.TIMEOUT for r in wedge_receipts)
    assert run.failure_reason, "a timeout run must carry a named reason"


def test_b_timeout_is_reported_in_the_quorum_decision(env):
    """A timeout still reports the policy and the tally behind it."""
    async def healthy(ctx):
        return "delivered"

    async def wedged(ctx):
        await asyncio.sleep(3600)

    async def moderator(ctx):
        return "synthesis"

    room = make_room(
        env, {"healthy": healthy, "wedged": wedged}, policy="all", timeout=0.4, grace=0.2, moderator=moderator
    )
    run = asyncio.run(room.execute())
    assert run.final_quorum is not None
    assert run.final_quorum.passed is False
    assert run.final_quorum.agree == 1
    assert run.final_quorum.disagree == 1
    assert "all" in run.final_quorum.human_line()


# ---------------------------------------------------------------------- (c)
def test_c_empty_synthesis_fails_instead_of_reporting_success(env):
    """(c) NEVER publish synthesis="" with status="succeeded"."""
    async def ok(ctx):
        return "a real position"

    async def empty_moderator(ctx):
        return "   \n\t  "  # whitespace only: an empty deliverable

    room = make_room(env, {"a": ok, "b": ok}, moderator=empty_moderator)
    run = asyncio.run(room.execute())

    assert run.status == RunStatus.FAILED
    assert run.synthesis is None, "an empty synthesis must not be published as a value"
    assert run.status != RunStatus.SUCCEEDED
    assert "no deliverable" in run.failure_reason
    # And the persisted record agrees with the in-memory one.
    persisted = json.loads((room.dir / "run.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["synthesis"] is None
    assert persisted["failure_reason"], "a failed run must persist its reason"


def test_c_synthesis_returning_none_also_fails(env):
    async def ok(ctx):
        return "a real position"

    async def none_moderator(ctx):
        return None

    room = make_room(env, {"a": ok, "b": ok}, moderator=none_moderator)
    run = asyncio.run(room.execute())
    assert run.status == RunStatus.FAILED
    assert run.synthesis is None


def test_c_a_synthesis_that_raises_fails_loudly(env):
    async def ok(ctx):
        return "a real position"

    async def broken_moderator(ctx):
        raise RuntimeError("moderator exploded")

    room = make_room(env, {"a": ok, "b": ok}, moderator=broken_moderator)
    run = asyncio.run(room.execute())
    assert run.status == RunStatus.FAILED
    assert run.synthesis is None
    assert "moderator exploded" in (run.error or "")


# ---------------------------------------------------------------------- (d)
def test_d_one_members_failure_does_not_degrade_a_healthy_sibling(env):
    """(d) A failure NEVER cancels or degrades a healthy sibling's contribution."""
    delivered: list[str] = []

    async def healthy_one(ctx):
        delivered.append(f"one:{ctx.stage.name}")
        return "one delivered fully"

    async def explodes(ctx):
        raise RuntimeError("this member is broken")

    async def healthy_two(ctx):
        delivered.append(f"two:{ctx.stage.name}")
        return "two delivered fully"

    async def moderator(ctx):
        # The moderator must SEE both healthy siblings, by handle and in full.
        return "SYNTH:" + "|".join(f"{name}" for name in sorted(ctx.previous_outputs))

    room = make_room(
        env,
        {"one": healthy_one, "explodes": explodes, "two": healthy_two},
        policy="any",
        moderator=moderator,
    )
    run = asyncio.run(room.execute())

    # The moderator is handed each healthy sibling's FULL text, keyed by handle,
    # on BOTH collecting stages. That is the real isolation assertion: the
    # failure cannot have degraded what the healthy siblings produced.
    for stage in run.stages[:2]:
        by_name = {r.participant: r for r in stage.receipts}
        assert by_name["one"].status == MemberStatus.CONTRIBUTED
        assert by_name["one"].output == "one delivered fully"
        assert by_name["two"].status == MemberStatus.CONTRIBUTED
        assert by_name["two"].output == "two delivered fully"
        assert by_name["explodes"].status == MemberStatus.FAILED
        # The failure is in its OWN receipt, not smeared across the others.
        assert "explodes" not in by_name["one"].error
        assert by_name["one"].error == ""

    # The moderator received the siblings' real, complete output.
    assert run.synthesis == "SYNTH:one|two", "a sibling's contribution was degraded"
    # A partial run is reported as partial, never as a clean success.
    assert run.status == RunStatus.PARTIAL
    assert "did not deliver" in run.failure_reason


def test_d_an_empty_contribution_is_not_counted_as_agreement(env):
    """A member that returns nothing is EMPTY, never agreement."""

    async def talks(ctx):
        return "a real position"

    async def says_nothing(ctx):
        return ""

    async def moderator(ctx):
        return "synthesis"

    # quorum_policy="any" is deliberate: it IS satisfied by the one real
    # contribution, so the run succeeds and the stage does NOT fail on policy.
    # What is being proved here is narrower and more interesting: an empty
    # contribution must never be counted as a delivery, and must never be
    # laundered into "this member agreed".
    room = make_room(env, {"talks": talks, "quiet": says_nothing}, policy="any", moderator=moderator)
    run = asyncio.run(room.execute())

    empties = [r for r in run.all_receipts() if r.participant == "quiet"]
    assert empties, "the empty member has no receipt at all"
    assert all(r.status == MemberStatus.EMPTY for r in empties)
    assert all(r.output == "" for r in empties)
    # The empty output did NOT become agreement: only the real one did.
    # final_quorum is the LAST stage that voted, so its counts are per-stage.
    assert run.final_quorum.agree == 1, "an empty output was counted as agreement"
    assert run.final_quorum.disagree == 0
    assert run.final_quorum.amend == 1, "an empty output should be an amend, not an agree"
    # Every stage recorded the empty member as EMPTY, never as agreement.
    for stage in run.stages[:2]:
        assert stage.quorum.agree == 1
        assert stage.quorum.amend == 1
    # And it is reported as a degraded run, not a clean one.
    assert run.status == RunStatus.PARTIAL
    assert "did not deliver" in run.failure_reason


def test_d_an_empty_contribution_cannot_satisfy_an_all_quorum(env):
    """A stricter policy is not satisfiable by silence."""

    async def talks(ctx):
        return "a real position"

    async def says_nothing(ctx):
        return ""

    async def moderator(ctx):
        return "synthesis"

    room = make_room(env, {"talks": talks, "quiet": says_nothing}, policy="all", moderator=moderator)
    run = asyncio.run(room.execute())

    # ALL quorum demands two deliveries; an empty contribution is not one, so
    # the stage cannot reach quorum and the run is FAILED, not TIMEOUT: nothing
    # wedged, the room simply did not get enough real contributions.
    assert run.status == RunStatus.FAILED, "an empty contribution must not satisfy an ALL quorum"
    empties = [r for r in run.all_receipts() if r.participant == "quiet"]
    assert empties and all(r.status == MemberStatus.EMPTY for r in empties)
    assert run.final_quorum.agree == 1, "an empty output was counted as agreement"
    assert run.final_quorum.passed is False


# ------------------------------------------------------------- transcript
def test_transcript_is_durable_gap_free_and_replayable(env):
    """Per-message attribution and timing, durable and replayable."""
    async def ok(ctx):
        return "a position"

    async def moderator(ctx):
        return "DECISION"

    room = make_room(env, {"a": ok, "b": ok}, moderator=moderator)
    asyncio.run(room.execute())

    room.verify_transcript()  # raises if not gap-free
    messages = room.transcript.read()
    assert [m.seq for m in messages] == list(range(1, len(messages) + 1))
    # Attribution: every participant's contribution is attributed to them.
    authors = {m.author for m in messages}
    assert {"a", "b", "war_room"} <= authors
    # Timing: every message carries a timezone-explicit timestamp.
    from alpha.channels.transcript import parse_iso

    for message in messages:
        parsed = parse_iso(message.wall_clock)
        assert parsed.tzinfo is not None
    replay = room.replay()
    assert "DECISION" in replay
    assert "a position" in replay


def test_a_transcript_fault_does_not_rewrite_a_delivered_member_as_failed(env, monkeypatch):
    """A transcript fault must NOT be able to rewrite an already-delivered member."""
    async def ok(ctx):
        return "a position"

    async def moderator(ctx):
        return "DECISION"

    room = make_room(env, {"a": ok, "b": ok}, moderator=moderator)

    # Break the transcript AFTER the receipts are decided, in memory only.
    from alpha.channels.transcript import TranscriptError

    real_append = TranscriptStore.append
    state = {"calls": 0}

    def flaky(self, *args, **kwargs):
        state["calls"] += 1
        if state["calls"] > 2:
            raise TranscriptError("simulated disk fault")
        return real_append(self, *args, **kwargs)

    monkeypatch.setattr(TranscriptStore, "append", flaky)
    run = asyncio.run(room.execute())

    assert run.transcript_errors, "the transcript fault was swallowed entirely"
    # The delivered members are still recorded as delivered.
    delivered = [r for r in run.all_receipts() if r.participant == "a"]
    assert delivered, "no receipt for the healthy member at all"
    assert all(r.status == MemberStatus.CONTRIBUTED for r in delivered)
    assert all(r.output == "a position" for r in delivered)
    # And the run does not claim a clean success while its record is broken.
    assert run.status != RunStatus.SUCCEEDED


def test_a_lost_failure_receipt_is_never_swallowed(env, monkeypatch):
    """A failure receipt that cannot be persisted is escalated, not dropped."""
    from alpha.channels.transcript import TranscriptError

    async def ok(ctx):
        return "a position"

    async def explodes(ctx):
        raise RuntimeError("member is broken")

    async def moderator(ctx):
        return "DECISION"

    room = make_room(env, {"ok": ok, "broken": explodes}, policy="any", moderator=moderator)

    real_append = TranscriptStore.append

    def fail_on_receipt(self, author, body, *, kind="chat", **kwargs):
        if kind.startswith("receipt:"):
            raise TranscriptError("simulated receipt loss")
        return real_append(self, author, body, kind=kind, **kwargs)

    monkeypatch.setattr(TranscriptStore, "append", fail_on_receipt)
    run = asyncio.run(room.execute())

    assert run.receipt_loss, "a lost failure receipt was swallowed"
    assert any("broken" in entry for entry in run.receipt_loss)
    # An incomplete record cannot be reported as a clean success.
    assert run.status != RunStatus.SUCCEEDED
    assert "receipt" in run.failure_reason


# ------------------------------------------------------------- config guards
def test_a_room_cannot_open_without_a_topic_or_participants():
    with pytest.raises(ValueError, match="topic"):
        WarRoomConfig(topic="  ", participants=("a", "b"), stages=())
    with pytest.raises(ValueError, match="participants"):
        WarRoomConfig(topic="t", participants=("solo",), stages=())


def test_a_stage_budget_is_clamped_so_a_config_typo_cannot_park_a_room_open():
    from alpha.channels.timing import MAX_STAGE_TIMEOUT_SECONDS, StageBudget

    budget = StageBudget(name="s", timeout_seconds=10**9, grace_seconds=1)
    assert budget.timeout_seconds == MAX_STAGE_TIMEOUT_SECONDS
    with pytest.raises(ValueError):
        StageBudget(name="s", timeout_seconds=0)
    with pytest.raises(ValueError):
        StageBudget(name="s", timeout_seconds=1, grace_seconds=-1)
