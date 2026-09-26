"""Phase 2: identity and addressing in chat.

Covers the required assertions (e) one bot / several bots / a role selector each
dispatch correctly and per-target isolated, (f) an unknown handle resolves to
nothing and says so, (g) monotonic sequence numbers survive a restart, plus the
in-channel slash-command permissions and the presence degradation.
"""

from __future__ import annotations

import asyncio

import pytest

from alpha.channels.mentions import (
    MAX_TARGETS_PER_MESSAGE,
    MentionResolutionError,
    normalise_handle,
    parse_mentions,
)
from alpha.channels.routing import (
    TARGET_FAILED,
    TARGET_OK,
    ChannelCommandRouter,
    CommandRequest,
    dispatch_resolution,
    parse_command,
)
from alpha.channels.transcript import (
    Presence,
    TranscriptError,
    TranscriptStore,
    console_safe,
    parse_iso,
    read_presence,
)

ROSTER = ["alice", "bob", "carol", "rev1", "rev2"]
ROLES = {"reviewers": ["rev1", "rev2"]}


# ------------------------------------------------------------------ (e) one
def test_e_mentioning_one_bot_dispatches_to_exactly_that_bot():
    resolution = parse_mentions("@alice please look at this", roster=ROSTER, roles=ROLES)
    assert resolution.ok
    assert resolution.resolved_handles == ("alice",)
    assert resolution.targets[0].via == "explicit-handle"


# -------------------------------------------------------------- (e) several
def test_e_mentioning_several_bots_dispatches_to_all_of_them():
    resolution = parse_mentions("@alice and @bob and @carol: input please", roster=ROSTER, roles=ROLES)
    assert resolution.ok
    assert resolution.resolved_handles == ("alice", "bob", "carol")
    assert len(resolution.targets) == 3


# ------------------------------------------------------------- (e) a role
def test_e_a_role_selector_dispatches_to_every_holder():
    resolution = parse_mentions("@role:reviewers review this", roster=ROSTER, roles=ROLES)
    assert resolution.ok
    assert resolution.resolved_handles == ("rev1", "rev2")
    assert resolution.targets[0].via == "role-selector"
    assert resolution.targets[0].kind == "role"


def test_e_mixed_bot_and_role_targets_are_all_reported():
    resolution = parse_mentions(
        "@alice and @role:reviewers: go", roster=ROSTER, roles=ROLES
    )
    assert resolution.ok
    assert set(resolution.resolved_handles) == {"alice", "rev1", "rev2"}
    assert {t.via for t in resolution.targets} == {"explicit-handle", "role-selector"}


# ------------------------------------------------------------ (e) isolated
def test_e_multi_target_dispatch_is_concurrent_and_per_target_isolated():
    """One target's failure must not cancel or degrade a healthy sibling."""
    started: list[str] = []
    finished: list[str] = []

    async def slow_good(target, body, resolution):
        started.append(target)
        await asyncio.sleep(0.05)
        finished.append(target)
        return f"{target} answered"

    async def bad(target, body, resolution):
        started.append(target)
        raise RuntimeError("this target is broken")

    async def other_good(target, body, resolution):
        started.append(target)
        return f"{target} answered too"

    handlers = {"alice": slow_good, "bob": bad, "carol": other_good}
    resolution = parse_mentions("@alice @bob @carol go", roster=ROSTER, roles=ROLES)

    report = asyncio.run(
        dispatch_resolution(
            resolution,
            lambda t, b, r: handlers[t](t, b, r),
            sender="user",
            room="test",
            record_ledger=False,
        )
    )

    # All three were started, so the dispatch was concurrent, not serial.
    assert set(started) == {"alice", "bob", "carol"}
    # The healthy targets completed despite the sibling's exception.
    assert "alice" in finished
    assert report.outcome_for("alice").status == TARGET_OK
    assert report.outcome_for("alice").output == "alice answered"
    assert report.outcome_for("carol").status == TARGET_OK
    # The failure is confined to its own outcome.
    assert report.outcome_for("bob").status == TARGET_FAILED
    assert "broken" in report.outcome_for("bob").error
    # And the report is honest about the partial delivery.
    assert report.ok is False
    assert report.delivered == ("alice", "carol")
    assert report.degraded == ("bob",)


def test_e_a_target_returning_none_is_still_a_successful_delivery():
    async def quiet(target, body, resolution):
        return None

    resolution = parse_mentions("@alice go", roster=ROSTER, roles=ROLES)
    report = asyncio.run(
        dispatch_resolution(resolution, quiet, record_ledger=False)
    )
    assert report.outcome_for("alice").status == TARGET_OK
    assert report.ok is True


def test_e_a_base_exception_in_a_target_is_contained():
    async def nasty(target, body, resolution):
        raise KeyboardInterrupt("a BaseException, not an Exception")

    resolution = parse_mentions("@alice go", roster=ROSTER, roles=ROLES)
    report = asyncio.run(dispatch_resolution(resolution, nasty, record_ledger=False))
    assert report.outcome_for("alice").status == TARGET_FAILED
    assert report.outcome_for("alice").error_type == "KeyboardInterrupt"


# ------------------------------------------------------------------- (f)
def test_f_an_unknown_handle_resolves_to_nothing_and_says_so():
    resolution = parse_mentions("@nobody are you there", roster=ROSTER, roles=ROLES)
    assert resolution.resolved_handles == ()
    assert resolution.targets == ()
    assert resolution.ok is False
    assert len(resolution.unresolved) == 1
    assert "unknown handle" in resolution.unresolved[0]["reason"]
    # The message says so in words a human can act on.
    assert "nobody" in resolution.human_line()
    assert "unknown handle" in resolution.human_line()


def test_f_an_unknown_handle_dispatches_to_nobody_at_all():
    called: list[str] = []

    async def handler(target, body, resolution):
        called.append(target)
        return "answered"

    resolution = parse_mentions("@ghost hello", roster=ROSTER, roles=ROLES)
    report = asyncio.run(
        dispatch_resolution(resolution, handler, record_ledger=False)
    )
    assert called == [], "an unknown handle reached a handler"
    assert report.outcomes == []
    assert report.ok is False
    assert len(report.unresolved) == 1


def test_f_a_near_miss_handle_resolves_to_nothing_rather_than_to_a_plausible_bot():
    """@rev must NOT reach rev1/rev2: a near match is the failure being prevented."""
    resolution = parse_mentions("@rev take a look", roster=ROSTER, roles=ROLES)
    assert resolution.resolved_handles == ()
    assert resolution.ok is False
    # Prefix, substring and plural forms are all refused.
    for probe in ("@rev", "@rev1x", "@al", "@alice2", "@car"):
        assert parse_mentions(probe, roster=ROSTER, roles=ROLES).resolved_handles == (), probe


def test_f_an_ambiguous_handle_resolves_to_nothing_and_says_so():
    """Two roster entries that fold to the same key are ambiguous, not a coin flip."""
    resolution = parse_mentions("@dup go", roster=["dup", "DUP", "alice"], roles={})
    assert resolution.resolved_handles == ()
    assert resolution.ok is False
    assert "ambiguous" in resolution.unresolved[0]["reason"]
    assert "DUP" in resolution.unresolved[0]["reason"]


def test_f_an_unknown_role_selector_resolves_to_nothing_and_says_so():
    resolution = parse_mentions("@role:interns go", roster=ROSTER, roles=ROLES)
    assert resolution.resolved_handles == ()
    assert "unknown role selector" in resolution.unresolved[0]["reason"]
    # The message names the roles that DO exist, so the fix is obvious.
    assert "reviewers" in resolution.unresolved[0]["reason"]


def test_f_a_role_naming_an_absent_member_is_refused_rather_than_partially_applied():
    resolution = parse_mentions("@role:mixed go", roster=ROSTER, roles={"mixed": ["alice", "ghost"]})
    assert resolution.resolved_handles == ()
    assert "not in this room" in resolution.unresolved[0]["reason"]
    assert "ghost" in resolution.unresolved[0]["reason"]


def test_f_a_bare_handle_is_never_treated_as_a_role():
    """Roles need the explicit prefix, so the two namespaces cannot be confused."""
    resolution = parse_mentions("@reviewers go", roster=ROSTER, roles=ROLES)
    assert resolution.resolved_handles == ()
    assert resolution.ok is False


def test_f_a_blank_role_is_a_configuration_error_not_a_silent_no_op():
    with pytest.raises(MentionResolutionError, match="no members"):
        parse_mentions("@role:empty go", roster=ROSTER, roles={"empty": []})


def test_f_fan_out_above_the_ceiling_is_refused():
    roster = [f"bot{i}" for i in range(MAX_TARGETS_PER_MESSAGE + 5)]
    resolution = parse_mentions("@all go", roster=roster, roles={}, sender="user")
    assert resolution.resolved_handles == ()
    assert "fan-out ceiling" in resolution.unresolved[0]["reason"]


def test_f_the_fan_out_selector_resolves_to_every_other_member():
    resolution = parse_mentions("@all go", roster=["alice", "bob"], roles={}, sender="alice")
    assert resolution.resolved_handles == ("bob",)
    assert resolution.targets[0].via == "all-selector"


def test_f_an_unknown_bare_token_is_never_fanned_out():
    """Only the documented aliases fan out; a typo addresses nobody."""
    for probe in ("@everybody", "@everyoneee", "@al", "@al l"):
        resolution = parse_mentions(f"{probe} go", roster=["alice", "bob"], roles={})
        if probe == "@al":
            assert resolution.resolved_handles == (), probe
        else:
            assert resolution.resolved_handles == (), probe
            assert resolution.ok is False, probe


def test_f_a_bot_named_all_cannot_be_shadowed_by_the_selector():
    """A roster entry literally named "all" is still reachable by explicit form."""
    resolution = parse_mentions("@bot:all go", roster=["all", "bob"], roles={})
    assert resolution.resolved_handles == ("all",)
    assert resolution.ok is True


# ------------------------------------------------------------------- (g)
def test_g_messages_carry_monotonic_sequence_numbers(tmp_path):
    store = TranscriptStore(tmp_path / "t.jsonl")
    seqs = [store.append("alice", f"message {i}").seq for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]
    assert [m.seq for m in store.read()] == [1, 2, 3, 4, 5]


def test_g_sequence_numbers_survive_a_restart_and_never_repeat(tmp_path):
    """(g) A restart must not renumber or reuse a sequence number."""
    path = tmp_path / "t.jsonl"
    first = TranscriptStore(path)
    for i in range(3):
        first.append("alice", f"before restart {i}")

    # Simulate a process restart: a brand new store over the same file.
    second = TranscriptStore(path)
    assert second.high_seq == 3
    new_message = second.append("bob", "after restart")
    assert new_message.seq == 4, "a restart reused or skipped a sequence number"

    everything = second.read()
    assert [m.seq for m in everything] == [1, 2, 3, 4]
    second.verify_ordered_and_gap_free()

    third = TranscriptStore(path)
    assert third.append("carol", "third process").seq == 5


def test_g_a_readable_transcript_records_author_and_timezone_explicit_time(tmp_path):
    store = TranscriptStore(tmp_path / "t.jsonl")
    message = store.append("alice", "hello", targets=("bob",))
    assert message.author == "alice"
    assert message.targets == ("bob",)
    parsed = parse_iso(message.wall_clock)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert message.wall_clock.endswith("Z")


def test_g_a_naive_timestamp_is_refused_rather_than_assumed_utc():
    with pytest.raises(ValueError, match="naive"):
        parse_iso("2026-01-01T12:00:00")


def test_g_an_unattributed_message_is_refused(tmp_path):
    store = TranscriptStore(tmp_path / "t.jsonl")
    with pytest.raises(TranscriptError, match="author"):
        store.append("   ", "orphan message")


def test_g_a_transcript_write_fault_is_never_reported_as_delivery(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path / "t.jsonl")
    store.append("alice", "first")

    real_open = open

    def broken_open(path, *args, **kwargs):
        if str(path).endswith("t.jsonl") and "a" in (args[0] if args else kwargs.get("mode", "")):
            raise OSError("simulated disk full")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", broken_open)
    with pytest.raises(TranscriptError, match="could not be read back|append failed"):
        store.append("bob", "this must not be reported as delivered")


def test_g_console_rendering_never_raises_on_any_encoding():
    """A cp1252 console must not be able to crash transcript rendering."""
    hostile = "text with non-ascii: \u2014 \u2500 \u00e9 \u4e2d\u6587 \U0001f600"
    for encoding in ("ascii", "cp1252", "utf-8", "latin-1"):
        rendered = console_safe(hostile, encoding=encoding)
        assert isinstance(rendered, str)
    # And the transcript renderer survives it.
    import tempfile
    from pathlib import Path

    store = TranscriptStore(Path(tempfile.mkdtemp()) / "t.jsonl")
    store.append("alice", hostile)
    assert isinstance(store.render(), str)


# ------------------------------------------------------------- presence
def test_presence_degrades_to_unknown_rather_than_to_idle():
    """A transport that cannot tell idle from disconnected must say UNKNOWN."""
    # No signal at all.
    assert read_presence(None, handle="alice").presence is Presence.UNKNOWN
    # A bare False from a transport that does not document idle reporting.
    unknown = read_presence(False, handle="alice", transport_reports_idle=False)
    assert unknown.presence is Presence.UNKNOWN
    assert "does not document idle reporting" in unknown.detail
    # Unrecognised tokens are unknown, not idle.
    assert read_presence("maybe", handle="alice").presence is Presence.UNKNOWN
    # Explicit typing is honoured.
    assert read_presence(True, handle="alice").presence is Presence.TYPING
    assert read_presence("typing", handle="alice").presence is Presence.TYPING
    # Idle is only reported when the transport actually documents it.
    assert read_presence("idle", handle="alice", transport_reports_idle=True).presence is Presence.IDLE


# ------------------------------------------------------------- slash commands
def test_a_slash_command_is_parsed_only_from_the_start_of_a_body():
    assert parse_command("/war-room build a plan") == ("war-room", "build a plan")
    assert parse_command("/status") == ("status", "")
    # Not a command: mid-sentence, or a path.
    assert parse_command("please /war-room now") is None
    assert parse_command("see /usr/local/bin") is None
    assert parse_command("") is None


def test_a_command_is_permission_checked_per_actor():
    router = ChannelCommandRouter({"status": ("view_file", False), "hire": ("hire_bot", True)})
    # A lead ring is allow_all, so this passes the role check.
    lead = CommandRequest("status", "", "alpha", "lead", "/status", "room")
    assert router.check(lead).allowed

    # A narrow role is refused a tool outside its ring.
    worker = CommandRequest("hire", "", "worker", "worker", "/hire x", "room")
    decision = router.check(worker)
    assert decision.allowed is False
    assert decision.refused_by == "permission"


def test_a_command_a_bot_may_issue_to_itself_it_may_not_issue_on_another_bot():
    """The asymmetry the requirement names, enforced in code."""
    router = ChannelCommandRouter({"status": ("view_file", False)})
    router.set_target_authority("bob", {"observe"})

    on_self = CommandRequest("status", "", "alice", "lead", "/status", "room")
    assert router.check(on_self).allowed is True

    # Same actor, same command, but aimed at somebody else.
    on_other = CommandRequest(
        "status", "", "alice", "lead", "/status", "room", on_behalf_of="bob"
    )
    decision = router.check(on_other)
    assert decision.allowed is False
    assert decision.refused_by == "target_authority"
    assert "may not issue it against" in decision.reason


def test_a_mutating_command_needs_the_target_to_hold_the_capability_itself():
    router = ChannelCommandRouter({"rescope": ("rescope_bot", True)})
    router.set_target_authority("weak", {"observe"})
    router.set_target_authority("strong", {"observe", "rescope_bot"})

    weak = CommandRequest("rescope", "", "lead", "lead", "/rescope", "r", on_behalf_of="weak")
    assert router.check(weak).allowed is False
    assert "does not hold" in router.check(weak).reason

    strong = CommandRequest("rescope", "", "lead", "lead", "/rescope", "r", on_behalf_of="strong")
    assert router.check(strong).allowed is True


def test_an_on_behalf_of_action_against_an_unknown_bot_is_refused():
    router = ChannelCommandRouter({"rescope": ("rescope_bot", True)})
    request = CommandRequest("rescope", "", "lead", "lead", "/rescope", "r", on_behalf_of="ghost")
    decision = router.check(request)
    assert decision.allowed is False
    assert "no recorded authority" in decision.reason


def test_a_command_aimed_at_a_protected_component_is_refused():
    router = ChannelCommandRouter({"archive": ("rescope_bot", True)})
    for target in (
        "alpha/bots/authority_ceiling.py",
        "alpha.bots.authority_ceiling",
        "kill_switch.py",
    ):
        request = CommandRequest(
            "archive", "", "lead", "lead", "/archive", "r", on_behalf_of=target
        )
        decision = router.check(request)
        assert decision.allowed is False, target
        assert decision.refused_by == "protected", target


def test_a_refused_command_never_calls_its_handler():
    router = ChannelCommandRouter({"hire": ("hire_bot", True)})
    called: list[str] = []

    def handler(request):
        called.append(request.name)
        return "should not happen"

    request = CommandRequest("hire", "", "worker", "worker", "/hire x", "room")
    outcome = asyncio.run(router.dispatch(request, handler, record_ledger=False))
    assert called == [], "a refused command reached its handler"
    assert outcome.ok is False
    assert outcome.decision.allowed is False


def test_an_unknown_command_is_refused_by_name():
    router = ChannelCommandRouter({})
    outcome = asyncio.run(
        router.dispatch(
            CommandRequest("nope", "", "lead", "lead", "/nope", "room"),
            record_ledger=False,
        )
    )
    assert outcome.ok is False
    assert outcome.decision.refused_by == "unknown"


# ------------------------------------------------------------- normalisation
def test_handle_normalisation_folds_case_but_not_punctuation():
    assert normalise_handle("  Alice  ") == "alice"
    assert normalise_handle("rev-1") == "rev-1"
    assert normalise_handle("rev_1") == "rev_1"
    assert normalise_handle("rev-1") != normalise_handle("rev_1")


def test_mention_capability_tags_are_derived_not_display_names():
    from alpha.channels.mentions import mention_capabilities

    assert mention_capabilities(["Alice", "bob", "alice"]) == (
        "mention:alice",
        "mention:bob",
    )
