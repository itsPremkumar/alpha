"""The derivation table is the contract, asserted row by row.

Every state in `groups/activity.py` is justified by an evidence row. A state
with no row here does not exist, and this file is where that is enforced. The
rows that matter most are the ones the old resolver could not express: a
finished agent and a dead agent must not look alike.
"""

from __future__ import annotations

import pytest

from alpha.groups.activity import (
    ACTIVITY_STATES,
    RunEvidence,
    derive_activity,
    tone_for,
)


@pytest.fixture(autouse=True)
def _isolated_runtime_home(tmp_path, monkeypatch):
    """Point `runtime_home()` at a temp dir and drop the ledger singletons.

    `get_activity_ledger()` is a process-wide singleton that rebuilds when
    `runtime_home()` moves, so relocating `ALPHA_HOME` is enough to isolate it.
    The bots registry is reset too because the derivation reads it: without this
    a developer's real roster would decide what `unknown` resolves to.
    """
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path / ".alpha"))
    import alpha.groups.activity as activity_mod
    import alpha.groups.claims as claims_mod

    activity_mod._ledger = None
    activity_mod._ledger_path = None
    claims_mod._claims = None
    claims_mod._claims_path = None
    try:
        from alpha.bots import registry as registry_mod

        registry_mod._registry = None
        registry_mod._registry_path = None
    except Exception:
        pass
    yield
    activity_mod._ledger = None
    activity_mod._ledger_path = None
    claims_mod._claims = None
    claims_mod._claims_path = None


def _crash_reason_vocabulary() -> frozenset[str]:
    """The real stop reasons, imported — never restated.

    If `RunManager` adds a fifth recoverable reason, a hardcoded copy here
    would keep passing while the production derivation silently stopped
    treating that reason as a crash.
    """
    from alpha.runtime.runs.manager import RECOVERABLE_RUN_STOP_REASONS

    return frozenset(RECOVERABLE_RUN_STOP_REASONS)


class TestCrashIsProvable:
    def test_every_recoverable_stop_reason_is_a_crash(self):
        """Each of RunManager's four named crash reasons yields `crashed`."""
        for reason in _crash_reason_vocabulary():
            evidence = RunEvidence(run_id="r1", status="error", stop_reason=reason, error="boom")
            assert derive_activity(bot_name="coder", now=1000.0, run=evidence).activity == "crashed", reason

    def test_terminal_failure_without_a_stop_reason_is_still_a_crash(self):
        for status in ("error", "timeout", "interrupted"):
            assert derive_activity(bot_name="coder", now=1000.0, run=RunEvidence(run_id="r1", status=status)).activity == "crashed"

    def test_a_stale_heartbeat_cannot_override_a_hard_crash(self):
        """Precedence: the store outranks a heartbeat, however old."""
        run = RunEvidence(run_id="r1", status="error", stop_reason="orphan_recovered")
        assert derive_activity(bot_name="coder", now=1000.0, run=run, last_heartbeat_at=1.0).activity == "crashed"

    def test_the_crash_detail_names_the_evidence(self):
        run = RunEvidence(run_id="r1", status="error", stop_reason="orphan_recovered", error="lease expired")
        detail = derive_activity(bot_name="coder", now=1000.0, run=run).detail
        assert "orphan_recovered" in detail
        assert "lease expired" in detail

    def test_crash_and_unresponsive_are_different_tones(self):
        """A UI cannot paint both red, or the distinction is wasted."""
        crashed = derive_activity(bot_name="c", now=1000.0, run=RunEvidence(run_id="r1", status="error", stop_reason="orphan_recovered"))
        silent = derive_activity(bot_name="c", now=1000.0, last_heartbeat_at=1.0)
        assert crashed.tone == "bad"
        assert silent.tone == "warn"
        assert crashed.tone != silent.tone


class TestIdleRequiresProof:
    def test_a_terminal_success_is_the_proof_that_idle_needs(self):
        assert derive_activity(bot_name="coder", now=1000.0, run=RunEvidence(run_id="r1", status="success")).activity == "idle"

    def test_silence_is_never_idle(self):
        """THE regression. A quiet agent used to read as `idle`.

        `idle` is what a *finished* agent looks like, so resolving silence to it
        rendered a crash and a clean finish identically.
        """
        for stamp, now in ((1.0, 1000.0), (800.0, 1000.0)):
            assert derive_activity(bot_name="coder", now=now, last_heartbeat_at=stamp).activity != "idle"

    def test_a_missing_heartbeat_is_unknown_not_idle(self):
        """No record at all is a different claim from a completed task."""
        assert derive_activity(bot_name="coder", now=1000.0).activity == "unknown"

    def test_an_unparseable_heartbeat_is_unknown_not_a_fresh_one(self):
        """A missing stamp must never become `time.time()`."""
        result = derive_activity(bot_name="coder", now=1000.0, last_heartbeat_at=None)
        assert result.activity == "unknown"
        assert result.last_heartbeat_at is None

    def test_an_absent_run_is_unresponsive_not_crashed(self):
        """The store having no row is a problem, not proof of a terminal state."""
        result = derive_activity(bot_name="coder", now=1000.0, run=RunEvidence(run_id="r1", absent=True))
        assert result.activity == "unresponsive"
        assert result.evidence.reason == "run_absent"


class TestUnresponsiveIsNotCrashed:
    @pytest.mark.parametrize("age", [200.0, 5000.0])
    def test_long_silence_reads_unresponsive(self, age):
        assert derive_activity(bot_name="coder", now=1000.0 + age, last_heartbeat_at=1000.0).activity == "unresponsive"

    @pytest.mark.parametrize("liveness", ["stalled", "dead"])
    def test_health_alone_yields_unresponsive(self, liveness):
        """`alpha.bots.health` owns healthy/stale/stalled/dead. It never says
        `crashed`, and neither may this layer — the two vocabularies are kept
        separate on purpose."""
        assert derive_activity(bot_name="coder", now=1000.0, health=liveness).activity == "unresponsive"

    def test_a_long_tool_call_inside_the_window_is_still_working(self):
        """Silence up to the window is normal; an agent mid-call goes quiet."""
        assert derive_activity(bot_name="coder", now=1000.0, last_heartbeat_at=950.0).activity == "working"


class TestAdministrativeLifecycle:
    @pytest.mark.parametrize("status", ["suspended", "archived"])
    def test_administrative_out_of_service_is_offline(self, status):
        result = derive_activity(bot_name="coder", now=1000.0, registry_status=status)
        assert result.activity == "offline"
        assert result.tone == "off"

    def test_a_suspended_agent_is_not_reported_as_crashed(self):
        """A suspended bot was told to stop. That is not a death."""
        run = RunEvidence(run_id="r1", status="error", stop_reason="orphan_recovered")
        assert derive_activity(bot_name="coder", now=1000.0, run=run, registry_status="suspended").activity == "offline"


class TestDeclaredStates:
    def test_a_fresh_blocked_declaration_is_blocked(self):
        assert derive_activity(bot_name="c", now=1000.0, last_heartbeat_at=950.0, declared="blocked").activity == "blocked"

    def test_a_fresh_idle_declaration_is_idle(self):
        assert derive_activity(bot_name="c", now=1000.0, last_heartbeat_at=950.0, declared="idle").activity == "idle"

    def test_a_fresh_declaration_without_work_reads_working(self):
        assert derive_activity(bot_name="c", now=1000.0, last_heartbeat_at=950.0, declared="working").activity == "working"

    def test_a_stale_blocked_declaration_stays_blocked(self):
        """A self-reported work state outranks an inferred liveness signal.

        An agent that said "I'm blocked on the schema" and then went quiet is
        still blocked. Reporting it `unresponsive` would let a liveness word
        overwrite the one thing a peer needed to know — the same mistake as
        resolving silence to `idle`, in a different vocabulary.
        """
        assert derive_activity(bot_name="c", now=1000.0, last_heartbeat_at=1.0, declared="blocked").activity == "blocked"

    def test_a_stale_idle_declaration_stays_idle(self):
        assert derive_activity(bot_name="c", now=1000.0, last_heartbeat_at=1.0, declared="idle").activity == "idle"

    def test_a_stale_working_declaration_does_not_survive_as_working(self):
        """`working` is the one declaration silence may retract.

        It claims live effort, so a stale one is precisely the case
        `unresponsive` exists for. `blocked` and `idle` claim the absence of
        effort, so silence confirms rather than contradicts them.
        """
        assert derive_activity(bot_name="c", now=1000.0, last_heartbeat_at=1.0, declared="working").activity == "unresponsive"


class TestEvidenceIsAlwaysPresent:
    def test_every_derived_state_carries_evidence(self):
        """Nothing renders a state this module cannot explain."""
        cases = [
            dict(run=RunEvidence(run_id="r1", status="error", stop_reason="orphan_recovered")),
            dict(run=RunEvidence(run_id="r1", status="success")),
            dict(run=RunEvidence(run_id="r1", absent=True)),
            dict(last_heartbeat_at=1.0),
            dict(last_heartbeat_at=950.0),
            dict(),
            dict(registry_status="suspended"),
            dict(health="dead"),
        ]
        for case in cases:
            result = derive_activity(bot_name="coder", now=1000.0, **case)
            assert result.evidence.reason, result.activity
            assert result.evidence.detail, result.activity

    def test_every_state_has_a_distinct_reason(self):
        """Two rows sharing a reason word would mean one of them is unreachable."""
        seen: dict[str, str] = {}
        cases = [
            ("crashed", dict(run=RunEvidence(run_id="r1", status="error", stop_reason="orphan_recovered"))),
            ("idle", dict(run=RunEvidence(run_id="r1", status="success"))),
            ("unresponsive", dict(run=RunEvidence(run_id="r1", absent=True))),
            ("working", dict(last_heartbeat_at=950.0)),
            ("offline", dict(registry_status="archived")),
            ("unknown", dict()),
        ]
        for expected, case in cases:
            result = derive_activity(bot_name="coder", now=1000.0, **case)
            assert result.activity == expected
            assert result.evidence.reason not in seen, f"{result.evidence.reason} shared with {seen.get(result.evidence.reason)}"
            seen[result.evidence.reason] = expected


class TestToneMapping:
    def test_unknown_enum_is_never_folded_into_healthy(self):
        """A future state must render as visibly-not-measured."""
        assert tone_for("some-future-state") == "unknown"
        assert tone_for("") == "unknown"

    @pytest.mark.parametrize("state", ACTIVITY_STATES)
    def test_every_declared_state_has_a_tone(self, state):
        assert tone_for(state) in ("busy", "ok", "warn", "bad", "off", "unknown")

    def test_crashed_and_unresponsive_never_share_a_tone(self):
        assert tone_for("crashed") != tone_for("unresponsive")


class TestAttributionDisclosure:
    def test_recorded_room_provenance_is_carried_on_the_record(self):
        from alpha.groups.activity import get_activity_ledger

        ledger = get_activity_ledger()
        ledger.bind_room("coder", room_name="core")
        assert ledger.resolve("coder", room_name="core").attributed_by == "explicit"

    def test_a_room_the_agent_was_not_bound_to_is_roster_match(self):
        """Disclosed inference, never presented as a binding."""
        from alpha.groups.activity import get_activity_ledger

        ledger = get_activity_ledger()
        ledger.bind_room("coder", room_name="core")
        assert ledger.resolve("coder", room_name="other").attributed_by == "roster_match"

    def test_an_agent_with_no_binding_is_never_explicit(self):
        from alpha.groups.activity import get_activity_ledger

        assert get_activity_ledger().resolve("stranger", room_name="core").attributed_by == "roster_match"
