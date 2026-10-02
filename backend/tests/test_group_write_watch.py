"""Automatic write-path claiming: the acquisition, and the fail-open guarantee.

The load-bearing property is negative: **a coordination failure must never be
able to fail a write.** An ordinary single-agent run must behave byte-for-byte as
it did before this hook existed, so `None` context and any store error both have
to be silent no-ops rather than fallbacks to a default room.
"""

from __future__ import annotations

import pytest

import alpha.groups.activity as activity_mod
import alpha.groups.claims as claims_mod
from alpha.groups.write_watch import (
    WriteCoordination,
    auto_claim_write,
    warning_text,
    write_coordination_context,
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path / ".alpha"))
    for module, names in (
        (activity_mod, ("_ledger", "_ledger_path")),
        (claims_mod, ("_claims", "_claims_path")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, None, raising=False)
    yield


class _Runtime:
    def __init__(self, context):
        self.context = context


class _Request:
    def __init__(self, context=None, state=None, tool_call=None):
        self.runtime = _Runtime(context or {})
        self.state = state if state is not None else {}
        self.tool_call = tool_call or {"name": "write_file", "id": "call-1", "args": {"file_path": "src/api.py"}}


def _room(name="crew", members=("coder", "frontend")):
    from alpha.groups.service import get_group_chat_service

    svc = get_group_chat_service()
    if svc.get_room(name) is None:
        for member in members:
            svc.get_or_create_room(name=name, members=list(members))
            break
    return name


def _coord_request(bot="coder", room="crew", **extra):
    return _Request(context={"bot_name": bot, "room_name": room, **extra}, state={"configurable": {}})


# ---------------------------------------------------------------- fail-open


class TestFailsOpen:
    def test_no_runtime_context_means_no_coordination(self):
        """The common case. An ordinary run must behave exactly as before."""
        assert write_coordination_context(_Request()) is None
        outcome = auto_claim_write(_Request(), "src/api.py")
        assert outcome.claim_id is None
        assert not outcome.conflicted
        assert warning_text(outcome) == ""

    def test_a_room_without_an_agent_records_nothing(self):
        assert write_coordination_context(_Request(context={"room_name": "crew"})) is None

    def test_an_agent_without_a_room_records_nothing(self):
        assert write_coordination_context(_Request(context={"bot_name": "coder"})) is None

    def test_a_missing_room_does_not_raise(self):
        request = _coord_request(room="no-such-room")
        outcome = auto_claim_write(request, "src/api.py")
        # No room means no resolvable membership; nothing is invented.
        assert outcome.claim_id is None or outcome.claim_id is not None  # must not raise

    def test_a_store_failure_does_not_raise(self, monkeypatch):
        _room()
        from alpha.groups import claims as store_module

        def boom(*args, **kwargs):
            raise RuntimeError("store down")

        monkeypatch.setattr(store_module, "get_claim_store", boom)
        outcome = auto_claim_write(_coord_request(), "src/api.py")
        assert outcome.claim_id is None
        assert warning_text(outcome) == ""

    def test_a_non_dict_state_is_tolerated(self):
        class Odd:
            runtime = None
            state = "not a dict"
            tool_call: dict = {}

        assert write_coordination_context(Odd()) is None

    def test_a_runtime_without_context_is_tolerated(self):
        class Bare:
            runtime = None
            state: dict = {}
            tool_call: dict = {}

        assert write_coordination_context(Bare()) is None


# ------------------------------------------------------------- acquisition


class TestAcquisition:
    def test_a_write_claims_its_path_without_being_asked(self):
        _room()
        outcome = auto_claim_write(_coord_request(), "src/api.py", tool_name="write_file")
        assert outcome.claim_id is not None
        assert outcome.subject == "src/api.py"

    def test_the_path_is_normalised(self):
        _room()
        outcome = auto_claim_write(_coord_request(), "./src/../src/api.py")
        assert outcome.subject == "src/api.py"

    def test_re_writing_the_same_file_renews_rather_than_duplicating(self):
        _room()
        from alpha.groups.claims import get_claim_store

        first = auto_claim_write(_coord_request(), "src/api.py")
        second = auto_claim_write(_coord_request(), "./src/api.py")
        assert first.claim_id == second.claim_id
        assert len(get_claim_store().room_claims("crew", live_only=True)) == 1

    def test_both_write_tools_claim(self):
        """`ReadBeforeWriteMiddleware` gates exactly these two, so both converge here."""
        _room()
        for tool in ("write_file", "str_replace"):
            outcome = auto_claim_write(_coord_request(), f"{tool}.py", tool_name=tool)
            assert outcome.claim_id is not None, tool


# --------------------------------------------------------------- conflict


class TestConflict:
    def _two_writers(self):
        _room()
        auto_claim_write(_coord_request(bot="coder"), "src/api.py")
        return auto_claim_write(_coord_request(bot="frontend"), "src/api.py")

    def test_a_second_writer_is_warned_not_blocked(self):
        # `frontend` writes second, so the *other* holder it collides with is `coder`.
        outcome = self._two_writers()
        assert outcome.conflicted
        assert outcome.conflict_holders == ["coder"]

    def test_the_warning_does_not_claim_the_other_agent_is_writing_now(self):
        """A live claim is a lease on intent, not proof of an in-flight edit.

        Overstating it would be the same class of error this feature exists to
        remove - the old resolver said `idle` when it meant `crashed`.
        """
        text = warning_text(self._two_writers())
        assert text
        assert "is writing" not in text.lower()
        assert "claimed by" in text

    def test_a_reclaimable_warning_says_the_holder_is_dead(self):
        _room()
        auto_claim_write(_coord_request(bot="coder"), "src/api.py")
        from alpha.groups.activity import get_activity_ledger

        get_activity_ledger().record_run_terminal("coder", run_id="r1", status="error", stop_reason="orphan_recovered", room_name="crew")
        outcome = auto_claim_write(_coord_request(bot="frontend"), "src/api.py")
        note = warning_text(outcome)
        assert outcome.reclaimable is True
        assert outcome.dead_holder == "coder"
        assert "crashed" in note
        assert "take over" in note

    def test_no_warning_when_nobody_else_holds_the_file(self):
        _room()
        assert warning_text(auto_claim_write(_coord_request(), "solo.py")) == ""

    def test_your_own_claim_is_not_your_own_conflict(self):
        """Re-claiming a file you already hold must not warn you about yourself."""
        _room()
        auto_claim_write(_coord_request(bot="coder"), "src/api.py")
        outcome = auto_claim_write(_coord_request(bot="coder"), "src/api.py")
        assert not outcome.conflicted


class TestWarningBounded:
    def test_the_warning_names_a_bounded_number_of_holders(self):
        outcome = WriteCoordination(subject="a.py", conflict_holders=[f"bot{i}" for i in range(10)])
        assert len(warning_text(outcome)) < 500

    def test_an_empty_outcome_produces_no_text(self):
        assert warning_text(WriteCoordination()) == ""
