"""Regression: the Sentinel analytics fold is honest about what it measured.

The aggregate is the number an operator reads off a dashboard, so the failures
it must not have are the ones that turn "nobody measured this" into a
reassuring value:

* a journal where no pass reported ``scanned`` must produce ``scanned_total =
  None`` — never ``0``, which would read as "the engine saw no signals";
* a pass that recorded no per-outcome rows is a *counted* fact
  (``passes_without_outcomes``), not an absence of signals;
* a duration is averaged only over the passes that reported one;
* a status this build does not name is preserved verbatim, never snapped to a
  known one;
* a cap is disclosed as a window, so a total can never be quoted without the
  population it came from.
"""

from __future__ import annotations

from alpha.runtime.sentinel.analytics import (
    VERDICT_DEFERRED,
    VERDICT_REPAIRED,
    VERDICT_REVERTED,
    VERDICT_UNMEASURED,
    VERDICT_UNREPAIRED,
    SentinelAnalytics,
    aggregate,
)


def _entry(recorded_at: str, *, outcomes: list[dict] | None = None, **report: object) -> dict:
    """One journal entry. Any key not passed is ABSENT, not zero.

    That distinction is the whole point of this module, so the fixture must not
    quietly supply ``duration_s: 0.5`` to a pass whose duration was never
    measured.
    """
    body: dict = {"summary": "s", "fixed": 0, "reverted": 0, "escalated": 0, "errors": []}
    body.update(report)
    if outcomes is not None:
        body["outcomes"] = outcomes
    return {"recorded_at": recorded_at, "trigger": "api", "auto_heal": False, "report": body}


def _bare_entry(recorded_at: str, **report: object) -> dict:
    """An entry carrying only what is passed — no trigger, no auto_heal."""
    return {"recorded_at": recorded_at, "report": dict(report)}


def _outcome(kind: str, status: str, fingerprint: str = "fp1") -> dict:
    return {"fingerprint": fingerprint, "kind": kind, "stage": "diagnose", "status": status, "detail": "", "commit": None, "verify": None}


def _bare_entry(recorded_at: str, **report: object) -> dict:
    """An entry carrying only what is passed — no trigger, no auto_heal."""
    return {"recorded_at": recorded_at, "report": dict(report)}


# -- empty / absent ---------------------------------------------------------


def test_empty_journal_folds_to_zero_passes_and_names_it() -> None:
    a = aggregate([])

    assert a.passes == 0
    assert a.scanned_total is None
    assert a.duration_mean_s is None
    assert a.kinds == ()
    assert a.repeats == ()
    assert a.first_recorded_at is None and a.last_recorded_at is None
    # A zero reading is only honest when the population is named.
    assert any("no passes were folded" in line for line in a.disclosures)


def test_unreported_scanned_stays_null_not_zero() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00")])  # no `scanned` key at all

    assert a.scanned_total is None
    assert a.scanned_reporting_passes == 0
    assert any("no pass reported a 'scanned' count" in line for line in a.disclosures)


def test_partially_reported_scanned_sums_only_reporters_and_says_so() -> None:
    a = aggregate(
        [
            _entry("2026-10-01T00:00:00+00:00", scanned=4),
            _entry("2026-10-01T00:01:00+00:00"),  # no scanned
            _entry("2026-10-01T00:02:00+00:00", scanned=6),
        ]
    )

    assert a.scanned_total == 10
    assert a.scanned_reporting_passes == 2
    assert any("summed over the 2 pass(es) that reported it" in line for line in a.disclosures)


def test_reported_zero_scanned_is_a_measured_zero() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00", scanned=0)])

    assert a.scanned_total == 0
    assert a.scanned_reporting_passes == 1


# -- per-kind roll-up -------------------------------------------------------


def test_kind_verdict_prefers_the_best_outcome_the_kind_ever_reached() -> None:
    a = aggregate(
        [
            _entry(
                "2026-10-01T00:00:00+00:00",
                outcomes=[
                    _outcome("missing_bom", "escalated"),
                    _outcome("missing_bom", "fixed", fingerprint="fp2"),
                ],
            ),
            _entry("2026-10-01T00:01:00+00:00", outcomes=[_outcome("missing_bom", "escalated", fingerprint="fp3")]),
        ]
    )

    (kind,) = a.kinds
    assert kind.kind == "missing_bom"
    assert kind.occurrences == 3
    assert kind.passes == 2
    assert kind.fixed == 1 and kind.escalated == 2
    assert kind.distinct_fingerprints == 3
    assert kind.verdict == VERDICT_REPAIRED
    assert kind.first_seen == "2026-10-01T00:00:00+00:00"
    assert kind.last_seen == "2026-10-01T00:01:00+00:00"


def test_kind_that_only_ever_escalated_is_unrepaired_not_repaired() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00", outcomes=[_outcome("syntax_error", "escalated")])])

    assert a.kinds[0].verdict == VERDICT_UNREPAIRED


def test_kind_that_only_reverted_is_reverted_not_a_failure_word() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00", outcomes=[_outcome("syntax_error", "reverted")])])

    assert a.kinds[0].verdict == VERDICT_REVERTED


def test_kind_that_only_skipped_is_deferred() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00", outcomes=[_outcome("syntax_error", "skipped")])])

    assert a.kinds[0].verdict == VERDICT_DEFERRED


def test_kinds_sort_worst_first_by_escalations_then_occurrences() -> None:
    a = aggregate(
        [
            _entry(
                "2026-10-01T00:00:00+00:00",
                outcomes=[
                    _outcome("quiet", "fixed"),
                    _outcome("loud", "escalated"),
                    _outcome("loud", "escalated", fingerprint="fp2"),
                    _outcome("mid", "escalated"),
                    _outcome("mid", "escalated", fingerprint="fp3"),
                ],
            )
        ]
    )

    assert [k.kind for k in a.kinds] == ["loud", "mid", "quiet"]


def test_unmeasured_kind_verdict_when_no_status_was_reported() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00", outcomes=[{"kind": "mystery", "detail": "no status field"}])])

    (kind,) = a.kinds
    assert kind.verdict == VERDICT_UNMEASURED
    assert kind.escalated == 0 and kind.fixed == 0 and kind.skipped == 0
    # No status was written, so there is no unrecognised word to preserve —
    # and the absence is still not one of the four named statuses.
    assert kind.status_missing == 1
    assert kind.other == 1
    assert kind.unknown_statuses == ()


def test_unknown_status_is_preserved_verbatim_never_snapped() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00", outcomes=[_outcome("x", "quarantined")])])

    (kind,) = a.kinds
    assert kind.unknown_statuses == ("quarantined",)
    assert a.status_counts == {"quarantined": 1}
    assert kind.other == 1
    assert kind.status_missing == 0


# -- counters that are rows vs counters that are measurements ----------------


def test_pass_without_outcomes_is_counted_separately_from_a_pass_with_none() -> None:
    # One pass carries an explicitly empty outcome list; the other omits the key.
    a = aggregate(
        [
            _entry("2026-10-01T00:00:00+00:00", outcomes=[]),
            _entry("2026-10-01T00:01:00+00:00"),
        ]
    )

    assert a.passes_with_outcomes == 1
    assert a.passes_without_outcomes == 1
    assert a.outcome_count == 0
    assert any("recorded no per-outcome rows" in line for line in a.disclosures)


def test_pass_with_no_outcomes_key_at_all_is_not_an_empty_pass() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00")])

    assert a.passes_without_outcomes == 1
    assert a.passes_with_outcomes == 0
    assert a.outcome_count == 0


def test_durations_average_only_passes_that_reported_one() -> None:
    first = _entry("2026-10-01T00:00:00+00:00", duration_s=2.0)
    second = _entry("2026-10-01T00:01:00+00:00")  # no duration key at all
    third = _entry("2026-10-01T00:02:00+00:00", duration_s=4.0)

    a = aggregate([first, second, third])

    assert a.duration_measured_passes == 2
    assert a.duration_min_s == 2.0
    assert a.duration_mean_s == 3.0
    assert a.duration_max_s == 4.0
    assert any("duration: measured over 2 of 3" in line for line in a.disclosures)


def test_no_pass_reported_duration_stays_null() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00", duration_s=None)])

    assert a.duration_mean_s is None
    assert a.duration_measured_passes == 0
    assert any("no pass reported a measured duration" in line for line in a.disclosures)


def test_negative_duration_is_not_a_measurement() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00", duration_s=-1.0)])

    assert a.duration_measured_passes == 0
    assert a.duration_mean_s is None


def test_boolean_scanned_is_not_a_count() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00", scanned=True)])

    assert a.scanned_total is None
    assert a.scanned_reporting_passes == 0


# -- repeats ----------------------------------------------------------------


def test_fingerprint_seen_in_two_passes_is_a_repeat() -> None:
    a = aggregate(
        [
            _entry("2026-10-01T00:00:00+00:00", outcomes=[_outcome("missing_bom", "escalated", fingerprint="fp-keep")]),
            _entry("2026-10-01T00:05:00+00:00", outcomes=[_outcome("missing_bom", "escalated", fingerprint="fp-keep")]),
            _entry("2026-10-01T00:06:00+00:00", outcomes=[_outcome("missing_bom", "fixed", fingerprint="fp-once")]),
        ]
    )

    assert [r.fingerprint for r in a.repeats] == ["fp-keep"]
    assert a.repeats[0].passes == 2
    assert a.repeats[0].kind == "missing_bom"
    assert a.distinct_fingerprints == 2


def test_fingerprint_repeated_inside_one_pass_is_not_a_repeat() -> None:
    a = aggregate(
        [
            _entry(
                "2026-10-01T00:00:00+00:00",
                outcomes=[
                    _outcome("missing_bom", "escalated", fingerprint="fp-a"),
                    _outcome("missing_bom", "escalated", fingerprint="fp-a"),
                ],
            )
        ]
    )

    assert a.repeats == ()


def test_repeat_borrows_its_kind_verdict_and_says_so() -> None:
    a = aggregate(
        [
            _entry("2026-10-01T00:00:00+00:00", outcomes=[_outcome("k", "fixed", fingerprint="fp-r")]),
            _entry("2026-10-01T00:01:00+00:00", outcomes=[_outcome("k", "fixed", fingerprint="fp-r")]),
        ]
    )

    assert a.repeats[0].verdict == VERDICT_REPAIRED


# -- mode split, triggers, errors -------------------------------------------


def test_auto_heal_split_is_null_when_no_pass_reported_the_flag() -> None:
    a = aggregate([_bare_entry("2026-10-01T00:00:00+00:00", outcomes=[])])

    assert a.auto_heal_passes is None
    assert a.observe_passes is None
    assert any("mode: no pass reported its auto_heal flag" in line for line in a.disclosures)


def test_auto_heal_split_counts_both_modes() -> None:
    a = aggregate(
        [
            _entry("2026-10-01T00:00:00+00:00"),
            {"recorded_at": "2026-10-01T00:01:00+00:00", "trigger": "supervisor-loop", "auto_heal": True, "report": {"outcomes": []}},
        ]
    )

    assert a.auto_heal_passes == 1
    assert a.observe_passes == 1
    assert a.trigger_counts == {"api": 1, "supervisor-loop": 1}


def test_entry_errors_are_carried_verbatim_and_bounded() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00", errors=[f"boom {i}" for i in range(60)])])

    assert a.error_total == 60
    assert len(a.errors) == 50
    assert a.errors[0] == "boom 0"


def test_non_string_errors_are_dropped_from_the_verbatim_list_but_still_counted() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00", errors=["real", None, 42])])

    assert a.error_total == 3
    assert a.errors == ("real",)


# -- shape failures and the cap --------------------------------------------


def test_malformed_entries_are_counted_not_silently_dropped() -> None:
    a = aggregate(["not an entry", _entry("2026-10-01T00:00:00+00:00"), {"recorded_at": "x"}])

    assert a.passes == 1
    assert a.malformed == 2
    assert any("2 entry(ies) were skipped" in line for line in a.disclosures)


def test_cap_is_disclosed_as_a_window_not_as_the_whole_journal() -> None:
    entries = [_entry(f"2026-10-01T00:0{i}:00+00:00", scanned=1) for i in range(5)]

    a = aggregate(entries[-2:], limit=2, total_on_disk=5)

    assert a.passes == 2
    assert a.cap == 2
    assert a.dropped_by_cap == 3
    assert a.scanned_total == 2
    assert any("3 older pass(es) were excluded" in line for line in a.disclosures)


def test_cap_without_a_total_on_disk_drops_nothing_silently() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00")], limit=1)

    assert a.dropped_by_cap == 0
    assert a.cap == 1


# -- serialization ----------------------------------------------------------


def test_to_dict_round_trips_every_field_with_null_preserved() -> None:
    a = aggregate([_entry("2026-10-01T00:00:00+00:00", duration_s=0.5, outcomes=[_outcome("missing_bom", "fixed")])])

    d = a.to_dict()

    assert isinstance(d["kinds"], list) and d["kinds"][0]["kind"] == "missing_bom"
    assert isinstance(d["disclosures"], list)
    assert d["scanned_total"] is None  # not reported by the fixture
    assert d["duration_mean_s"] == 0.5
    assert d["kinds"][0]["status_missing"] == 0


def test_frozen_dataclass_refuses_mutation() -> None:
    a = aggregate([])
    assert isinstance(a, SentinelAnalytics)
    try:
        a.passes = 5  # type: ignore[misc]
    except Exception as exc:  # frozen dataclasses raise FrozenInstanceError
        assert " FrozenInstanceError" in type(exc).__name__ or type(exc).__name__ == "FrozenInstanceError"
    else:  # pragma: no cover - only reachable if the dataclass stops being frozen
        raise AssertionError("SentinelAnalytics must stay frozen")
