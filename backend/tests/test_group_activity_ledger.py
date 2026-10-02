"""The ledger: durability, the crash-event recorder, and reconcile-on-read."""

from __future__ import annotations

import pytest

from alpha.groups.activity import (
    ActivityLedger,
    RunEvidence,
    get_activity_ledger,
)


@pytest.fixture(autouse=True)
def _isolated_runtime_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path / ".alpha"))
    import alpha.groups.activity as activity_mod

    activity_mod._ledger = None
    activity_mod._ledger_path = None
    yield
    activity_mod._ledger = None
    activity_mod._ledger_path = None


class TestDurability:
    def test_a_recorded_crash_survives_a_restart(self, tmp_path):
        """A crash observed before a restart must still read as crashed after.

        This is the whole reason the ledger is file-backed: the operator who
        opens the room after a Gateway bounce needs to see what died, not an
        empty roster.
        """
        path = tmp_path / "activity.json"
        first = ActivityLedger(path)
        first.record_run_terminal("coder", run_id="r1", status="error", stop_reason="orphan_recovered", error="lease expired")
        second = ActivityLedger(path)
        row = second.raw_row("coder")
        assert row["run_status"] == "error"
        assert row["run_stop_reason"] == "orphan_recovered"

    def test_a_corrupt_store_starts_empty_instead_of_raising(self, tmp_path):
        """A display store must not take down the rooms it describes."""
        path = tmp_path / "activity.json"
        path.write_text("{not json", encoding="utf-8")
        ledger = ActivityLedger(path)
        assert ledger.raw_row("coder") is None
        assert ledger.room_activity("core", ["coder"]) is not None

    def test_singleton_rebuilds_when_runtime_home_moves(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALPHA_HOME", str(tmp_path / "one"))
        first = get_activity_ledger()
        first.record_heartbeat("coder", declared="working")
        monkeypatch.setenv("ALPHA_HOME", str(tmp_path / "two"))
        second = get_activity_ledger()
        assert second is not first
        assert second.raw_row("coder") is None


class TestTerminalEventIsolation:
    def test_a_late_terminal_event_for_a_previous_run_is_ignored(self):
        """An agent that already moved on must not have live work erased.

        Without this, a slow terminal event from run A arriving after run B
        started would blank B's `working` state and its detail — the display
        would flicker back to finished.
        """
        ledger = ActivityLedger()
        ledger.record_run_started("coder", run_id="a", detail="task A")
        ledger.record_run_started("coder", run_id="b", detail="task B")
        ledger.record_run_terminal("coder", run_id="a", status="success")
        row = ledger.raw_row("coder")
        assert row["run_id"] == "b"
        assert row["run_status"] == "running"

    def test_a_terminal_event_for_the_current_run_is_applied(self):
        ledger = ActivityLedger()
        ledger.record_run_started("coder", run_id="a")
        ledger.record_run_terminal("coder", run_id="a", status="success")
        assert ledger.raw_row("coder")["run_status"] == "success"

    def test_a_success_records_the_idle_declaration(self):
        """A real terminal success is the proof that turns working into idle."""
        ledger = ActivityLedger()
        ledger.record_run_started("coder", run_id="a")
        ledger.record_run_terminal("coder", run_id="a", status="success")
        assert ledger.raw_row("coder")["declared"] == "idle"


class TestHeartbeat:
    def test_a_fresh_heartbeat_reads_working(self):
        ledger = ActivityLedger()
        ledger.record_heartbeat("coder", declared="working", detail="refactoring the router")
        entry = ledger.resolve("coder", room_name="core")
        assert entry.activity == "working"
        assert entry.detail == "refactoring the router"

    def test_an_unknown_declared_state_is_refused(self):
        """A typo must not become a state word nothing can interpret."""
        with pytest.raises(ValueError):
            ActivityLedger().record_heartbeat("coder", declared="finishd")

    def test_claim_ids_are_carried_onto_the_record(self):
        ledger = ActivityLedger()
        ledger.record_heartbeat("coder", claim_ids=["cl-1", "cl-2"])
        assert ledger.resolve("coder").claim_ids == ["cl-1", "cl-2"]


class TestRoomOrder:
    def test_every_member_appears_in_the_rooms_own_order(self, tmp_path):
        """A sorted roster would reorder on every poll."""
        ledger = ActivityLedger(tmp_path / "a.json")
        members = ["tester", "architect", "coder"]
        assert [a.bot_name for a in ledger.room_activity("core", members)] == members

    def test_a_member_with_no_record_still_appears_as_unknown(self):
        """`unknown`, not omitted: "no record" and "finished" differ."""
        entry = ActivityLedger().room_activity("core", ["ghost"])[0]
        assert entry.activity == "unknown"

    def test_duplicate_members_are_collapsed(self):
        entries = ActivityLedger().room_activity("core", ["coder", "CODER", " coder "])
        assert [a.bot_name for a in entries] == ["coder"]


class TestCrashProjection:
    def test_a_supplied_run_fact_outranks_the_ledgers_own_record(self):
        """The store is the authority; the ledger copy is only a hint."""
        ledger = ActivityLedger()
        ledger.record_run_terminal("coder", run_id="r1", status="running")
        assert ledger.resolve("coder").activity in ("working", "unresponsive", "unknown")
        crashed = ledger.resolve("coder", run=RunEvidence(run_id="r1", status="error", stop_reason="orphan_recovered"))
        assert crashed.activity == "crashed"

    def test_a_crashed_agent_keeps_its_row_and_detail(self, tmp_path):
        """The user's requirement: the work must not simply disappear.

        A crashed agent that vanishes from the list is the failure being fixed,
        so the record must survive with its detail, run id and `since`.
        """
        ledger = ActivityLedger(tmp_path / "a.json")
        ledger.record_run_started("coder", run_id="r1", detail="migrating auth", room_name="core")
        ledger.record_run_terminal("coder", run_id="r1", status="error", stop_reason="gateway_shutdown")
        entry = ledger.resolve("coder", run=RunEvidence(run_id="r1", status="error", stop_reason="gateway_shutdown"))
        assert entry.activity == "crashed"
        assert entry.run_id == "r1"
        assert entry.since > 0

    def test_the_room_listing_keeps_a_crashed_member(self, tmp_path):
        ledger = ActivityLedger(tmp_path / "a.json")
        ledger.record_run_terminal("coder", run_id="r1", status="error", stop_reason="orphan_recovered")
        facts = {"coder": RunEvidence(run_id="r1", status="error", stop_reason="orphan_recovered")}
        entries = ledger.room_activity("core", ["coder", "other"], run_facts=facts)
        names = [a.bot_name for a in entries]
        assert "coder" in names
        assert entries[0].activity == "crashed"
        assert entries[1].activity != "crashed"
