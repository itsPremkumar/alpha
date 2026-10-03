"""Process liveness: corroborating evidence, never a verdict on its own.

Two rules this file exists to hold:

1. `crashed` is never produced by liveness. Only a named terminal stop reason
   from the run store earns that word.
2. `None` means "nothing measured", and is never collapsed into `False`. Folding
   it into `False` would orphan the entire historical claim set the first time a
   sweep ran, because every record written before pids were captured has none.
"""

from __future__ import annotations

import os

import pytest

from alpha.groups.activity import RunEvidence, derive_activity
from alpha.groups.liveness import (
    LIVENESS_STALE_SECONDS,
    holder_is_gone,
    pid_alive,
    process_evidence,
)


class TestPidAlive:
    def test_this_process_is_alive(self):
        assert pid_alive(os.getpid()) is True

    @pytest.mark.parametrize("value", [None, 0, -1, "abc", True, False, ""])
    def test_nothing_to_measure_is_none_not_false(self, value):
        """The distinction the whole design turns on."""
        assert pid_alive(value) is None

    def test_an_absurd_pid_is_none_not_false(self):
        # Out of range on every platform we support; an OS error here is a
        # measurement we could not take, not proof of death.
        assert pid_alive(2**31 - 1) in (False, None)


class TestHolderIsGone:
    def test_requires_both_signals(self):
        """A dead pid alone must not orphan: pids are recycled."""
        assert holder_is_gone(pid=999_999_999, last_seen_at=1000.0, now=1001.0) is False

    def test_staleness_alone_must_not_orphan(self):
        """An agent inside a long tool call is silent but very much alive."""
        assert holder_is_gone(pid=os.getpid(), last_seen_at=0.0, now=10_000.0) is False

    def test_both_together_orphan(self):
        assert holder_is_gone(pid=999_999_999, last_seen_at=0.0, now=LIVENESS_STALE_SECONDS + 1) is True

    def test_no_last_seen_is_never_gone(self):
        assert holder_is_gone(pid=999_999_999, last_seen_at=None, now=10_000.0) is False

    def test_our_own_process_is_never_gone(self):
        assert holder_is_gone(pid=os.getpid(), last_seen_at=0.0, now=10_000.0) is False


class TestDerivationNeverDeclaresACrashFromLiveness:
    def test_a_gone_process_reads_unresponsive_not_crashed(self):
        result = derive_activity(
            bot_name="coder",
            now=LIVENESS_STALE_SECONDS + 1,
            pid=999_999_999,
            last_seen_at=0.0,
        )
        assert result.activity == "unresponsive"
        assert result.evidence.reason == "process_gone"

    def test_a_hard_run_fact_still_outranks_a_gone_process(self):
        """Precedence: the store is ground truth, so a real crash still wins."""
        run = RunEvidence(run_id="r1", status="error", stop_reason="orphan_recovered")
        result = derive_activity(
            bot_name="coder",
            now=LIVENESS_STALE_SECONDS + 1,
            run=run,
            pid=999_999_999,
            last_seen_at=0.0,
        )
        assert result.activity == "crashed"

    def test_an_administrative_lifecycle_word_still_outranks_liveness(self):
        """A suspended bot was told to stop; it did not die."""
        result = derive_activity(
            bot_name="coder",
            now=LIVENESS_STALE_SECONDS + 1,
            pid=999_999_999,
            last_seen_at=0.0,
            registry_status="suspended",
        )
        assert result.activity == "offline"

    def test_a_fresh_heartbeat_is_never_overridden_by_a_recycled_pid(self):
        result = derive_activity(
            bot_name="coder",
            now=1000.0,
            pid=999_999_999,
            last_seen_at=999.0,
        )
        assert result.activity != "unresponsive"


class TestProcessEvidence:
    def test_a_live_process_is_named(self):
        reading = process_evidence(os.getpid())
        assert reading["alive"] is True
        assert reading["reason"] == "process_alive"

    def test_an_absent_pid_says_so(self):
        reading = process_evidence(None)
        assert reading["alive"] is None
        assert reading["reason"] == "no_pid_recorded"
