"""Regression pins for missions: the acceptance gate and the live feed.

Both defects here were confirmed by driving the shipped code directly, and each
test FAILS against the pre-fix behaviour:

- **A mission could reach its terminal "passed"/``completed`` state while no
  acceptance criterion had ever been evaluated.**  ``MissionStore.transition``
  was an unconditional status write, and ``Mission.acceptance`` /
  ``Mission.acceptance_criteria`` did not exist, so "completed" said nothing
  about whether the work met its own bar.  These tests pin that a terminal
  outcome now requires a report in which every criterion was measured and held.
- **Acceptance was BATCH and POST-HOC.**  There was no event trail at all
  (``MissionStore`` exposed only ``create``/``get``/``list``/``transition``/
  ``attach_thread``/``attach_artifact``), so nothing could show what a mission
  was doing *while* it did it.  These tests pin a durable, ordered, live event
  journal that survives a restart, plus an SSE route that streams it.

``alpha.mission.lifecycle``'s phase machine is pinned here too: a mission walks
draft -> assigned -> running -> verifying -> passed, and every illegal move is
refused with the real reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.mission.acceptance import (
    REASON_NO_CRITERIA,
    AcceptanceNotSatisfied,
    AcceptanceRegistry,
    assert_acceptance_passed,
    evaluate_acceptance,
    get_acceptance_registry,
    unevaluated_report,
)
from alpha.mission.lifecycle import (
    IllegalMissionTransition,
    MissionEventFeed,
    MissionLifecycle,
    MissionPhase,
)
from alpha.missions import MISSION_FEED, MissionStore

CRITERIA = ["file:report.md exists", "tests_passed:pytest -q"]


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """A store on its own JSON file, and a clean live feed per test."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    MISSION_FEED._events.clear()
    MISSION_FEED._seq.clear()
    MISSION_FEED._subscribers.clear()
    MissionLifecycle.drop("msn-lifecycle")
    yield tmp_path
    MissionLifecycle.drop("msn-lifecycle")


def _store(tmp_path: Path) -> MissionStore:
    return MissionStore(tmp_path / "missions" / "missions.json")


# ------------------------------------------------- acceptance is never faked


def test_a_criterion_without_measured_evidence_is_never_met():
    """UNVERIFIED is a real verdict, and it is not a pass."""
    report = unevaluated_report(CRITERIA)
    assert [c.verdict.value for c in report.criteria] == ["unverified", "unverified"]
    assert report.all_evaluated is False
    assert report.all_hold is False
    assert report.passed is False
    assert report.unevaluated == CRITERIA
    with pytest.raises(AcceptanceNotSatisfied) as excinfo:
        assert_acceptance_passed(report)
    assert REASON_NO_CRITERIA not in str(excinfo.value)
    assert "not all evaluated" in str(excinfo.value)


def test_an_empty_report_is_not_a_pass():
    """Vacuous truth is not acceptance: no criteria means nothing was proven."""
    empty = unevaluated_report([])
    assert empty.all_evaluated is False
    assert empty.passed is False
    with pytest.raises(AcceptanceNotSatisfied) as excinfo:
        assert_acceptance_passed(empty)
    assert str(excinfo.value) == REASON_NO_CRITERIA
    with pytest.raises(AcceptanceNotSatisfied):
        assert_acceptance_passed(None)


def test_a_criterion_cannot_be_silently_skipped():
    """Every criterion stays in the report; an unmatched evidence key is disclosed."""
    partial = evaluate_acceptance(CRITERIA, {CRITERIA[0]: True})
    assert [c.criterion for c in partial.criteria] == CRITERIA
    assert partial.unevaluated == [CRITERIA[1]]
    assert partial.all_evaluated is False

    typo = evaluate_acceptance(CRITERIA, {"file:report.md exist": True})
    assert typo.all_evaluated is False, "an unmatched evidence key must not count as coverage"
    assert any("match no criterion" in note for note in typo.notes)


def test_a_measured_failure_is_reported_as_a_failure():
    failing = evaluate_acceptance(CRITERIA, {CRITERIA[0]: True, CRITERIA[1]: False})
    assert failing.all_evaluated is True
    assert failing.all_hold is False
    assert failing.passed is False
    assert failing.not_met == [CRITERIA[1]]
    with pytest.raises(AcceptanceNotSatisfied) as excinfo:
        assert_acceptance_passed(failing)
    assert "did not hold" in str(excinfo.value) or "not met" in str(excinfo.value)


def test_a_broken_evaluator_decides_nothing():
    """A raising probe leaves its criterion UNVERIFIED instead of passing it."""

    def boom(criterion: str) -> bool:
        raise RuntimeError("probe exploded")

    registry = AcceptanceRegistry()
    registry.register("boom", boom)
    report = registry.evaluate(CRITERIA)
    assert report.all_evaluated is False
    assert report.passed is False
    assert any("probe exploded" in note for note in report.notes)


def test_a_registry_probe_that_cannot_decide_stays_unverified():
    registry = AcceptanceRegistry()
    registry.register("file_only", lambda c: True if c.startswith("file:") else None)
    report = registry.evaluate(CRITERIA)
    assert report.unevaluated == [CRITERIA[1]], "an undecidable criterion must not be reported as met"
    assert report.passed is False
    with pytest.raises(AcceptanceNotSatisfied):
        assert_acceptance_passed(report)


# --------------------------------------- the store refuses an unjustified end


def test_a_mission_cannot_complete_with_criteria_never_evaluated(tmp_path):
    """The headline gap: no evaluated criterion, no terminal completion."""
    store = _store(tmp_path)
    mission = store.create("u1", "ship the report", acceptance_criteria=CRITERIA)
    assert store.transition(mission.mission_id, "active") is not None

    refusal = store.completion_refusal(mission.mission_id)
    assert refusal is not None and "have not been evaluated yet: 2 pending" in refusal
    assert store.transition(mission.mission_id, "completed") is None
    assert store.get(mission.mission_id).status == "active", "a refused transition must not move the status"


def test_a_mission_with_no_criteria_cannot_be_justified_as_completed(tmp_path):
    store = _store(tmp_path)
    mission = store.create("u1", "a vague objective with no bar")
    store.transition(mission.mission_id, "active")
    refusal = store.completion_refusal(mission.mission_id)
    assert refusal is not None and "no acceptance criteria" in refusal
    assert store.transition(mission.mission_id, "completed") is None


def test_a_mission_completes_only_after_a_full_passing_report(tmp_path):
    """The whole path, and the refusals along the way, in order."""
    store = _store(tmp_path)
    mission = store.create("u1", "ship the report", acceptance_criteria=CRITERIA)
    store.transition(mission.mission_id, "active")

    # 1. one criterion measured false, the other untouched -> refused.
    store.record_acceptance(mission.mission_id, evaluate_acceptance(CRITERIA, {CRITERIA[0]: True}))
    assert store.transition(mission.mission_id, "completed") is None
    assert store.get(mission.mission_id).status == "active"

    # 2. a real pass -> allowed, and the report is durable.
    report = evaluate_acceptance(CRITERIA, {CRITERIA[0]: True, CRITERIA[1]: True})
    assert report.passed is True
    store.record_acceptance(mission.mission_id, report)
    assert store.completion_refusal(mission.mission_id) is None
    completed = store.transition(mission.mission_id, "completed")
    assert completed is not None and completed.status == "completed"

    # 3. the acceptance state and the criteria survive a restart.
    reloaded = _store(tmp_path).get(mission.mission_id)
    assert reloaded.status == "completed"
    assert reloaded.acceptance_criteria == CRITERIA
    assert reloaded.acceptance_report().passed is True


# ------------------------------------------------------------- the live feed


def test_every_mutation_is_journaled_in_order(tmp_path):
    """The trail from assignment through to observed behaviour."""
    store = _store(tmp_path)
    mission = store.create("u1", "ship it", acceptance_criteria=CRITERIA)
    store.transition(mission.mission_id, "active")
    store.attach_thread(mission.mission_id, "th-1")
    store.record_acceptance(mission.mission_id, evaluate_acceptance(CRITERIA, {CRITERIA[0]: True, CRITERIA[1]: True}))
    store.record_observation(mission.mission_id, "worker wrote report.md", path="report.md")
    store.attach_artifact(mission.mission_id, "report.md")
    store.transition(mission.mission_id, "completed")

    events = store.read_events(mission.mission_id)
    assert [e.event_type for e in events] == [
        "mission_created",
        "mission_transitioned",
        "thread_attached",
        "acceptance_evaluated",
        "observation",
        "artifact_attached",
        "mission_transitioned",
    ]
    assert [e.seq for e in events] == list(range(1, 8)), "events must be strictly ordered"
    assert all(e.payload.get("durable") is True for e in events)
    assert events[1].payload["to"] == "active"
    assert events[-1].payload["to"] == "completed"
    # The acceptance event carries the real verdict, not just a "done" flag.
    assert events[3].payload["passed"] is True
    assert events[3].payload["all_evaluated"] is True
    assert len(events[3].payload["report"]["criteria"]) == 2


def test_the_journal_survives_a_restart_and_reaches_the_live_feed(tmp_path):
    store = _store(tmp_path)
    mission = store.create("u1", "durable", acceptance_criteria=CRITERIA)
    store.transition(mission.mission_id, "active")
    before = len(store.read_events(mission.mission_id))
    assert before == 2

    # A fresh store over the same path is a restart.
    restarted = _store(tmp_path)
    assert len(restarted.read_events(mission.mission_id)) == before
    # ...and the live feed serves the same history a subscriber saw before it.
    assert [e.seq for e in MISSION_FEED.history(mission.mission_id)] == [1, 2]
    # Re-instantiating again must not duplicate the history.
    _store(tmp_path)
    assert [e.seq for e in MISSION_FEED.history(mission.mission_id)] == [1, 2]


def test_a_live_subscriber_receives_events_as_they_happen(tmp_path):
    """The gap being closed: a live view, not a post-hoc report.

    A live subscriber sees events from subscription onward — catching up on
    history is the SSE route's job (``after_seq`` replay), not the feed's.
    """
    store = _store(tmp_path)
    mission = store.create("u1", "live", acceptance_criteria=CRITERIA)
    received: list = []
    MISSION_FEED.subscribe(mission.mission_id, received.append)
    try:
        assert MISSION_FEED.subscriber_count(mission.mission_id) == 1
        store.transition(mission.mission_id, "active")
        store.record_observation(mission.mission_id, "step 1 done")
        store.record_observation(mission.mission_id, "step 2 done")
    finally:
        MISSION_FEED.unsubscribe(mission.mission_id, received.append)

    assert [e.event_type for e in received] == [
        "mission_transitioned",
        "observation",
        "observation",
    ]
    assert [e.payload.get("observation") for e in received if e.event_type == "observation"] == ["step 1 done", "step 2 done"]
    # Sequence numbers are continuous from the journal, so a consumer that
    # already replayed seq<=2 sees no gap and no duplicate.
    assert [e.seq for e in received] == [2, 3, 4]
    assert MISSION_FEED.subscriber_count(mission.mission_id) == 0


def test_a_broken_subscriber_cannot_stop_the_feed(tmp_path):
    store = _store(tmp_path)
    mission = store.create("u1", "resilient")
    seen: list = []

    def broken(event):
        raise RuntimeError("subscriber exploded")

    MISSION_FEED.subscribe(mission.mission_id, broken)
    MISSION_FEED.subscribe(mission.mission_id, seen.append)
    try:
        store.record_observation(mission.mission_id, "still delivered")
    finally:
        MISSION_FEED.unsubscribe(mission.mission_id, broken)
        MISSION_FEED.unsubscribe(mission.mission_id, seen.append)
    assert [e.payload.get("observation") for e in seen] == ["still delivered"]


def test_a_sse_subscriber_endpoint_exists_on_the_missions_router():
    """The live feed is reachable, not just implemented in the store."""
    from app.gateway.routers.missions import router

    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/api/missions/{mission_id}/events" in paths, "missions must expose a live event feed"
    assert "/api/missions/{mission_id}/events/history" in paths
    assert "/api/missions/{mission_id}/acceptance" in paths
    assert "/api/missions/{mission_id}/observations" in paths
    # The old batch-only surface is still there, additively.
    assert "/api/missions" in paths


def test_a_corrupt_journal_tail_is_reported_not_hidden(tmp_path):
    store = _store(tmp_path)
    mission = store.create("u1", "tail")
    store.transition(mission.mission_id, "active")
    path = store.events_path
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")
    assert len(store.read_events(mission.mission_id)) == 2, "the readable prefix is still served"
    _store(tmp_path)  # a restart must not explode on the corrupt tail


# --------------------------------------------------------------- lifecycle


def test_a_mission_walks_four_phases_to_passed():
    """draft -> assigned -> running -> verifying -> passed, five states."""
    feed = MissionEventFeed()
    lifecycle = MissionLifecycle.create(
        "msn-lifecycle",
        feed=feed,
        owner="u1",
        acceptance_criteria=CRITERIA,
    )
    assert lifecycle.phase is MissionPhase.DRAFT

    for target in (MissionPhase.ASSIGNED, MissionPhase.RUNNING):
        assert lifecycle.transition(target) is target
    lifecycle.observe("worker did the thing", thread_id="th-9")
    assert lifecycle.transition(MissionPhase.VERIFYING) is MissionPhase.VERIFYING

    lifecycle.evaluate({CRITERIA[0]: True, CRITERIA[1]: True})
    assert lifecycle.request_pass(reason="both criteria measured") is MissionPhase.PASSED
    assert lifecycle.is_terminal is True
    assert [e.event_type for e in lifecycle.history] == [
        "mission_created",
        "phase_changed",
        "phase_changed",
        "observation",
        "phase_changed",
        "acceptance_evaluated",
        "phase_changed",
    ]


def test_the_terminal_pass_is_gated_on_a_real_evaluation():
    feed = MissionEventFeed()
    lifecycle = MissionLifecycle.create(
        "msn-lifecycle",
        feed=feed,
        owner="u1",
        acceptance_criteria=CRITERIA,
    )

    # 1. The gated phase cannot be entered directly, bypassing the gate.
    with pytest.raises(IllegalMissionTransition) as excinfo:
        lifecycle.transition(MissionPhase.PASSED)
    assert "acceptance gate" in str(excinfo.value)

    for target in (MissionPhase.ASSIGNED, MissionPhase.RUNNING, MissionPhase.VERIFYING):
        lifecycle.transition(target)

    # 2. No report at all -> refused, and the phase does not move.
    with pytest.raises(AcceptanceNotSatisfied):
        lifecycle.request_pass()
    assert lifecycle.phase is MissionPhase.VERIFYING

    # 3. A report with an un-evaluated criterion -> refused.
    lifecycle.evaluate({CRITERIA[0]: True})
    with pytest.raises(AcceptanceNotSatisfied) as excinfo:
        lifecycle.request_pass()
    assert "not all evaluated" in str(excinfo.value)
    assert lifecycle.phase is MissionPhase.VERIFYING

    # 4. A measured failure -> refused.
    lifecycle.evaluate({CRITERIA[0]: False, CRITERIA[1]: True})
    with pytest.raises(AcceptanceNotSatisfied):
        lifecycle.request_pass()
    assert lifecycle.phase is MissionPhase.VERIFYING

    # 5. Only a full pass gets in.
    lifecycle.evaluate({CRITERIA[0]: True, CRITERIA[1]: True})
    assert lifecycle.request_pass() is MissionPhase.PASSED


def test_illegal_and_post_terminal_moves_are_refused():
    feed = MissionEventFeed()
    lifecycle = MissionLifecycle.create(
        "msn-lifecycle",
        feed=feed,
        owner="u1",
        acceptance_criteria=CRITERIA,
    )

    with pytest.raises(IllegalMissionTransition) as excinfo:
        lifecycle.transition(MissionPhase.RUNNING, reason="skipping ahead")
    assert "cannot move from 'draft' to 'running'" in str(excinfo.value)

    lifecycle.transition(MissionPhase.ASSIGNED)
    lifecycle.transition(MissionPhase.RUNNING)
    lifecycle.fail("worker crashed")
    assert lifecycle.phase is MissionPhase.FAILED
    with pytest.raises(IllegalMissionTransition) as excinfo:
        lifecycle.transition(MissionPhase.RUNNING)
    assert "terminal" in str(excinfo.value)


def test_a_lifecycle_can_be_restored_from_a_durable_trail():
    feed = MissionEventFeed()
    lifecycle = MissionLifecycle.create(
        "msn-lifecycle",
        feed=feed,
        owner="u1",
        acceptance_criteria=CRITERIA,
    )
    lifecycle.transition(MissionPhase.ASSIGNED)
    lifecycle.transition(MissionPhase.RUNNING)
    lifecycle.evaluate({CRITERIA[0]: True, CRITERIA[1]: True})
    lifecycle.request_pass()
    saved = [e.to_dict() for e in lifecycle.history]

    restored = MissionLifecycle(mission_id="msn-restored", feed=MissionEventFeed(), owner="u1")
    restored.restore([json.loads(json.dumps(e)) for e in saved])
    assert restored.phase is MissionPhase.PASSED
    assert restored.owner == "u1"
    assert restored.acceptance_criteria == CRITERIA
    assert restored.acceptance is not None and restored.acceptance.passed is True
    # Sequence numbering continues past the restored history, so a consumer
    # sees no gap and no reuse.
    assert restored.feed.next_seq("msn-restored") == max(e["seq"] for e in saved) + 1
