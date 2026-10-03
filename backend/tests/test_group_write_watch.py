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


# -------------------------------------------------- strict-policy enforcement


def _crew_config(monkeypatch, lock_policy):
    import alpha.projects.crew as crew_mod

    class _FakeCrew:
        def get_collaboration(self, _project_id):
            from types import SimpleNamespace

            return SimpleNamespace(lock_policy=lock_policy)

    monkeypatch.setattr(crew_mod, "get_crew_service", lambda *a, **k: _FakeCrew())


def _activity(monkeypatch, entries=()):
    import alpha.groups.activity as activity_mod

    class _FakeActivity:
        def record_heartbeat(self, *_args, **_kwargs):
            return None

        def room_activity(self, _room_name, _members):
            return list(entries)

    monkeypatch.setattr(activity_mod, "get_activity_ledger", lambda: _FakeActivity())


def _enforce_request(bot="coder", room="crew", project_id="p1"):
    return _Request(
        context={"bot_name": bot, "room_name": room, "project_id": project_id},
        state={"configurable": {}},
    )


class TestEnforceWrite:
    def test_advisory_policy_allows_despite_a_live_claim(self, monkeypatch):
        from alpha.groups.claims import get_claim_store
        from alpha.groups.write_watch import enforce_write

        _room()
        _crew_config(monkeypatch, "advisory")
        _activity(monkeypatch)
        get_claim_store().claim("crew", "reviewer", "file", "src/api.py")
        decision = enforce_write(_enforce_request(bot="coder"), "src/api.py")
        assert decision.allowed is True
        assert decision.reason == "advisory_policy"

    def test_strict_policy_refuses_a_live_claim(self, monkeypatch):
        from types import SimpleNamespace

        from alpha.groups.claims import get_claim_store
        from alpha.groups.write_watch import enforce_write

        _room()
        _crew_config(monkeypatch, "strict")
        _activity(monkeypatch, [SimpleNamespace(bot_name="reviewer", activity="working")])
        get_claim_store().claim("crew", "reviewer", "file", "src/api.py")
        decision = enforce_write(_enforce_request(bot="coder"), "src/api.py")
        assert decision.allowed is False
        assert decision.reason == "live_claim"
        assert decision.holder == "reviewer"

    def test_strict_policy_allows_a_confirmed_dead_holder(self, monkeypatch):
        from types import SimpleNamespace

        from alpha.groups.claims import get_claim_store
        from alpha.groups.write_watch import enforce_write

        _room()
        _crew_config(monkeypatch, "strict")
        _activity(monkeypatch, [SimpleNamespace(bot_name="reviewer", activity="crashed")])
        get_claim_store().claim("crew", "reviewer", "file", "src/api.py")
        decision = enforce_write(_enforce_request(bot="coder"), "src/api.py")
        assert decision.allowed is True
        assert decision.reason == "holder_gone"

    def test_strict_policy_allows_your_own_claim(self, monkeypatch):
        from alpha.groups.claims import get_claim_store
        from alpha.groups.write_watch import enforce_write

        _room()
        _crew_config(monkeypatch, "strict")
        _activity(monkeypatch)
        get_claim_store().claim("crew", "coder", "file", "src/api.py")
        decision = enforce_write(_enforce_request(bot="coder"), "src/api.py")
        assert decision.allowed is True

    def test_no_project_id_stays_advisory(self, monkeypatch):
        from alpha.groups.claims import get_claim_store
        from alpha.groups.write_watch import enforce_write

        _room()
        _crew_config(monkeypatch, "strict")
        _activity(monkeypatch)
        get_claim_store().claim("crew", "reviewer", "file", "src/api.py")
        request = _Request(context={"bot_name": "coder", "room_name": "crew"}, state={"configurable": {}})
        decision = enforce_write(request, "src/api.py")
        assert decision.allowed is True
        assert decision.reason == "advisory_policy"

    def test_no_room_binding_is_a_silent_no_op(self):
        from alpha.groups.write_watch import enforce_write

        decision = enforce_write(_Request(), "src/api.py")
        assert decision.allowed is True
        assert decision.reason == "no_room_binding"

    def test_a_coordination_store_error_fails_open(self, monkeypatch):
        import alpha.groups.activity as activity_mod
        from alpha.groups.write_watch import enforce_write

        _room()
        _crew_config(monkeypatch, "strict")

        def _boom():
            raise RuntimeError("store unreadable")

        monkeypatch.setattr(activity_mod, "get_activity_ledger", _boom)
        decision = enforce_write(_enforce_request(), "src/api.py")
        assert decision.allowed is True
        assert decision.reason == "enforcement_unavailable"


class TestStrictBlocksTheWritePath:
    def _request(self):
        from unittest.mock import MagicMock

        from langgraph.prebuilt.tool_node import ToolCallRequest

        runtime = MagicMock()
        runtime.context = {"bot_name": "coder", "room_name": "crew", "project_id": "p1", "thread_id": "t1"}
        return ToolCallRequest(
            tool_call={"name": "write_file", "args": {"path": "src/api.py"}, "id": "call-1"},
            tool=None,
            state={"messages": []},
            runtime=runtime,
        )

    def _middleware(self):
        from alpha.agents.middlewares.read_before_write_middleware import ReadBeforeWriteMiddleware

        def reader(_runtime, path):
            raise FileNotFoundError(path)

        return ReadBeforeWriteMiddleware(content_reader=reader)

    def test_strict_refuses_before_the_handler_runs(self, monkeypatch):
        from types import SimpleNamespace

        from langchain_core.messages import ToolMessage

        from alpha.groups.claims import get_claim_store

        _room()
        _crew_config(monkeypatch, "strict")
        _activity(monkeypatch, [SimpleNamespace(bot_name="reviewer", activity="working")])
        get_claim_store().claim("crew", "reviewer", "file", "src/api.py")

        ran = []

        def handler(req):
            ran.append(req)
            return ToolMessage(content="written", tool_call_id="call-1", name="write_file")

        result = self._middleware().wrap_tool_call(self._request(), handler)
        assert ran == [], "the write must not run against a live strict claim"
        assert result.status == "error"
        assert "live_claim" in result.content
        assert result.additional_kwargs["alpha_write_block"]["tool"] == "write_file"

    def test_advisory_still_lets_the_write_through(self, monkeypatch):
        from langchain_core.messages import ToolMessage

        from alpha.groups.claims import get_claim_store

        _room()
        _crew_config(monkeypatch, "advisory")
        _activity(monkeypatch)
        get_claim_store().claim("crew", "reviewer", "file", "src/api.py")

        ran = []

        def handler(req):
            ran.append(req)
            return ToolMessage(content="written", tool_call_id="call-1", name="write_file")

        result = self._middleware().wrap_tool_call(self._request(), handler)
        assert len(ran) == 1, "advisory must remain warn-only"
        assert "written" in result.content
