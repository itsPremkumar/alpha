"""Experience-library maintenance (guide §16 Phase 7): bulk consolidation and expiry.

``build_experience`` writes one entry at a time and ``supersede_experience``
supersedes exactly one pair; nothing before the maintenance pass looks at the
*library as a whole*. These tests pin every deterministic rule of that pass:
what may be marked and why, what is unconditionally never touched, and that
applying maintenance rewrites metadata only — an entry is never deleted, so
the pointers keep every lesson retrievable after it leaves the current set.
"""

from __future__ import annotations

from typing import Any

import pytest

from alpha.harness_improvement.experiences import (
    CandidateScope,
    ExperienceMetadata,
    ExperienceStatus,
    load_experience,
    save_experience,
)
from alpha.harness_improvement.maintenance import (
    analyze_experiences,
    apply_maintenance,
    run_experience_maintenance,
)
from alpha.harness_improvement.store import HarnessImprovementStore


def _entry(
    experience_id: str,
    *,
    finding: str = "benefit",
    component_ids: tuple[str, ...] = ("tool_guidance/bash",),
    status: ExperienceStatus = ExperienceStatus.ACTIVE,
    created_at: str = "2026-01-01T00:00:00Z",
    expires_at: str | None = None,
    **overrides: Any,
) -> ExperienceMetadata:
    fields: dict[str, Any] = dict(
        experience_id=experience_id,
        title=f"entry {experience_id}",
        lesson=f"lesson {experience_id}",
        finding=finding,
        scope=CandidateScope.TASK_OR_DOMAIN,
        task_type="general",
        domain_tags=(),
        trigger_symptoms=(),
        applicable_conditions=(),
        not_applicable_to=(),
        component_ids=component_ids,
        component_kinds=(),
        evidence_refs=("run-1",),
        run_ids=("run-1",),
        benefit="cheaper check",
        counter_evidence="",
        status=status,
        created_at=created_at,
        last_reviewed_at=created_at,
        review_condition="",
        expires_at=expires_at,
        compatibility={},
    )
    fields.update(overrides)
    return ExperienceMetadata(**fields)


_NOW = "2026-10-19T00:00:00Z"


def _by_id(plan: Any, experience_id: str) -> Any:
    matches = [row for row in plan.actions if row.experience_id == experience_id]
    assert len(matches) <= 1, f"{experience_id} carries more than one action"
    return matches[0] if matches else None


# ------------------------------------------------------------------- duplicates


def test_a_duplicate_older_entry_is_superseded_by_the_newest_of_its_group() -> None:
    plan = analyze_experiences(
        (
            _entry("exp-old", created_at="2026-01-01T00:00:00Z"),
            _entry("exp-new", created_at="2026-06-01T00:00:00Z"),
        ),
        now=_NOW,
    )
    action = _by_id(plan, "exp-old")
    assert action is not None and action.action == "supersede"
    assert action.target_id == "exp-new"
    assert "duplicate" in action.reason
    assert _by_id(plan, "exp-new") is None, "the newest entry of a group is the keeper, never replaced"
    group = next(row for row in plan.duplicate_groups if row["kept_id"] == "exp-new")
    assert group["superseded_ids"] == ["exp-old"]
    assert group["component_ids"] == ["tool_guidance/bash"]
    assert plan.current == ("exp-new",)


def test_a_fully_terminal_group_leaves_its_current_member_alone() -> None:
    """The keeper candidate set is the *current* statuses; one surviving
    SUPERSEDED newer entry is reason for the group to take no action at all,
    never an invitation to supersede the ACTIVE older one by a dead pointer."""
    plan = analyze_experiences(
        (
            _entry("exp-old", created_at="2026-01-01T00:00:00Z", status=ExperienceStatus.ACTIVE),
            _entry("exp-new", created_at="2026-06-01T00:00:00Z", status=ExperienceStatus.SUPERSEDED, superseded_by="exp-other"),
        ),
        now=_NOW,
    )
    assert plan.actions == ()
    assert plan.current == ("exp-new", "exp-old"), "both are left in their recorded state; a pass touches only what it can justify"


def test_a_stale_keeper_candidate_ends_the_group_without_an_action() -> None:
    plan = analyze_experiences(
        (
            _entry("exp-old", created_at="2026-01-01T00:00:00Z", status=ExperienceStatus.ACTIVE),
            _entry("exp-new", created_at="2026-06-01T00:00:00Z", status=ExperienceStatus.STALE),
        ),
        now=_NOW,
    )
    assert plan.actions == ()
    row = plan.duplicate_groups[0]
    assert row["kept_id"] is None
    assert row["note"], "a group the pass declines to act on must say why"


def test_a_stale_older_entry_can_still_be_superseded_by_a_current_newer_one() -> None:
    plan = analyze_experiences(
        (
            _entry("exp-old", created_at="2026-01-01T00:00:00Z", status=ExperienceStatus.STALE),
            _entry("exp-new", created_at="2026-06-01T00:00:00Z", status=ExperienceStatus.ACTIVE),
        ),
        now=_NOW,
    )
    action = _by_id(plan, "exp-old")
    assert action is not None and action.action == "supersede" and action.target_id == "exp-new"


def test_entries_for_different_components_never_supersede_each_other() -> None:
    plan = analyze_experiences(
        (
            _entry("exp-a", component_ids=("tool_guidance/bash",)),
            _entry("exp-b", component_ids=("skills/checks",)),
        ),
        now=_NOW,
    )
    assert plan.actions == ()
    assert plan.duplicate_groups == ()
    assert plan.current == ("exp-a", "exp-b")


def test_general_lessons_without_component_identity_are_never_grouped() -> None:
    """An entry that names no components has no identity the pass can use; the
    conservative rule is to group nothing rather than guess what two general
    lessons share."""
    plan = analyze_experiences(
        (
            _entry("exp-a", component_ids=(), created_at="2026-01-01T00:00:00Z"),
            _entry("exp-b", component_ids=(), created_at="2026-06-01T00:00:00Z"),
        ),
        now=_NOW,
    )
    assert plan.actions == ()
    assert plan.duplicate_groups == ()


def test_duplicate_groups_are_finding_scoped_so_positive_and_negative_lessons_stay_separate() -> None:
    plan = analyze_experiences(
        (
            _entry("exp-pos", finding="benefit", created_at="2026-01-01T00:00:00Z"),
            _entry("exp-neg", finding="regression", created_at="2026-02-01T00:00:00Z"),
        ),
        now=_NOW,
    )
    assert plan.actions == ()
    assert plan.duplicate_groups == ()
    assert plan.current == ("exp-neg", "exp-pos")


# -------------------------------------------------------------------- conflicts


def test_opposing_findings_on_one_component_are_reported_not_mutated() -> None:
    """A benefit and a regression lesson about the same component are real
    information (it helped one task and hurt another); the pass surfaces the
    contradiction for a human instead of picking a winner it cannot prove."""
    plan = analyze_experiences(
        (
            _entry("exp-pos", finding="benefit", status=ExperienceStatus.PROPOSED),
            _entry("exp-neg", finding="regression", status=ExperienceStatus.PROPOSED),
        ),
        now=_NOW,
    )
    assert plan.actions == (), "a conflict alone never changes a record"
    assert len(plan.conflicts) == 1
    row = plan.conflicts[0]
    assert {row["left_id"], row["right_id"]} == {"exp-neg", "exp-pos"}
    assert row["component_ids"] == ["tool_guidance/bash"]
    assert "opposing" in row["reason"]
    assert plan.current == ("exp-neg", "exp-pos"), "both sides stay retrievable"


def test_a_superseded_duplicate_does_not_double_report_the_same_conflict() -> None:
    """The older duplicate of a benefit pair leaves the current set, so the
    contradiction that remains is the one between what survives."""
    plan = analyze_experiences(
        (
            _entry("exp-old", finding="benefit", created_at="2026-01-01T00:00:00Z"),
            _entry("exp-new", finding="benefit", created_at="2026-06-01T00:00:00Z"),
            _entry("exp-neg", finding="regression", created_at="2026-03-01T00:00:00Z"),
        ),
        now=_NOW,
    )
    assert len(plan.actions) == 1 and plan.actions[0].experience_id == "exp-old"
    assert len(plan.conflicts) == 1
    assert {plan.conflicts[0]["left_id"], plan.conflicts[0]["right_id"]} == {"exp-neg", "exp-new"}


# ----------------------------------------------------------------------- expiry


def test_an_entry_past_its_expiry_is_actions_to_stale_not_deleted() -> None:
    plan = analyze_experiences(
        (_entry("exp-expired", expires_at="2026-01-01T00:00:00Z"),),
        now=_NOW,
    )
    action = _by_id(plan, "exp-expired")
    assert action is not None and action.action == "stale" and action.target_id is None
    assert "expired at" in action.reason


def test_the_future_and_undated_and_clockless_are_left_in_place() -> None:
    entries = (
        _entry("exp-live", expires_at="2099-01-01T00:00:00Z"),
        _entry("exp-undated", expires_at=None, component_ids=("skills/checks",)),
    )
    plan = analyze_experiences(entries, now=_NOW)
    assert plan.actions == ()
    assert plan.current == ("exp-live", "exp-undated")
    clockless = analyze_experiences(entries, now=None)
    assert clockless.actions == (), "without a clock the pass does not decide expiry"
    assert analyze_experiences((_entry("exp-expired", expires_at="2026-01-01T00:00:00Z", status=ExperienceStatus.PROPOSED),), now=_NOW).actions


def test_terminal_statuses_are_never_actioned_even_when_expired() -> None:
    plan = analyze_experiences(
        (
            _entry("exp-gone", status=ExperienceStatus.SUPERSEDED, expires_at="2020-01-01T00:00:00Z", superseded_by="exp-x"),
            _entry("exp-retracted", status=ExperienceStatus.RETRACTED, expires_at="2020-01-01T00:00:00Z"),
        ),
        now=_NOW,
    )
    assert plan.actions == ()
    assert plan.current == ("exp-gone", "exp-retracted")


def test_supersede_outranks_expiry_for_the_same_entry() -> None:
    """An expired duplicate is superseded first: expiry is the weaker label and
    SUPERSEDED already implies staleness at retrieval."""
    plan = analyze_experiences(
        (
            _entry("exp-old", created_at="2026-01-01T00:00:00Z", expires_at="2026-02-01T00:00:00Z"),
            _entry("exp-new", created_at="2026-06-01T00:00:00Z"),
        ),
        now=_NOW,
    )
    assert [row.action for row in plan.actions if row.experience_id == "exp-old"] == ["supersede"]


def test_the_plan_is_deterministic_on_repeated_analysis() -> None:
    entries = (
        _entry("exp-b", created_at="2026-01-01T00:00:00Z"),
        _entry("exp-a", created_at="2026-06-01T00:00:00Z"),
        _entry("exp-c", finding="regression", status=ExperienceStatus.PROPOSED),
        _entry("exp-d", component_ids=("skills/checks",), expires_at="2026-01-01T00:00:00Z"),
    )
    first = analyze_experiences(entries, now=_NOW).to_dict()
    second = analyze_experiences(reversed(entries), now=_NOW).to_dict()
    assert first == second
    assert first["actions"] == sorted(first["actions"], key=lambda row: (row["experience_id"], row["action"]))


# ------------------------------------------------------------------- the store


def test_apply_false_reports_and_touches_nothing(tmp_path: Any) -> None:
    store = HarnessImprovementStore(tmp_path / "harness_improvement")
    older = _entry("exp-old", created_at="2026-01-01T00:00:00Z")
    newer = _entry("exp-new", created_at="2026-06-01T00:00:00Z")
    save_experience(store, newer)
    save_experience(store, older)
    report = run_experience_maintenance(store, apply=False, now=_NOW)
    assert report["apply"] is False
    assert report["applied"] == []
    assert report["reviewed"] == 2
    assert any(row["experience_id"] == "exp-old" for row in report["plan"]["actions"])
    assert load_experience(store, "exp-old") == older, "analysis must not rewrite the store"
    assert load_experience(store, "exp-new") == newer


def test_apply_marks_duplicates_and_expiry_without_deleting_anything(tmp_path: Any) -> None:
    store = HarnessImprovementStore(tmp_path / "harness_improvement")
    old_active = _entry("exp-old", created_at="2026-01-01T00:00:00Z", expires_at="2026-02-01T00:00:00Z")
    new_active = _entry("exp-new", created_at="2026-06-01T00:00:00Z")
    expired = _entry("exp-expired", component_ids=("skills/checks",), created_at="2026-03-01T00:00:00Z", expires_at="2026-04-01T00:00:00Z")
    for entry in (expired, old_active, new_active):
        save_experience(store, entry)

    report = run_experience_maintenance(store, apply=True, now=_NOW)
    assert report["apply"] is True
    applied_by_id = {row["experience_id"]: row for row in report["applied"]}
    assert applied_by_id["exp-old"]["status"] == "applied"
    assert applied_by_id["exp-expired"]["status"] == "applied"

    superseded = load_experience(store, "exp-old")
    keeper = load_experience(store, "exp-new")
    stale_marked = load_experience(store, "exp-expired")
    assert superseded is not None and keeper is not None and stale_marked is not None
    assert superseded.status is ExperienceStatus.SUPERSEDED and superseded.superseded_by == "exp-new"
    assert "exp-old" in keeper.supersedes
    assert stale_marked.status is ExperienceStatus.STALE
    assert stale_marked.last_reviewed_at >= _NOW
    assert any("expired at 2026-04-01T00:00:00Z" in note for note in stale_marked.maintenance_notes)

    directories = sorted(path.name for path in store.experiences_dir.iterdir() if path.is_dir())
    assert directories == ["exp-expired", "exp-new", "exp-old"], "maintenance rewrites metadata only; it never deletes an entry"


def test_an_unreadable_entry_is_reported_and_left_alone(tmp_path: Any) -> None:
    store = HarnessImprovementStore(tmp_path / "harness_improvement")
    save_experience(store, _entry("exp-new", created_at="2026-06-01T00:00:00Z"))
    bad_dir = store.experiences_dir / "exp-old"
    bad_dir.mkdir(parents=True)
    (bad_dir / "metadata.json").write_text("{not-json", encoding="utf-8")

    report = run_experience_maintenance(store, apply=True, now=_NOW)
    assert report["unreadable"] and "exp-old" in report["unreadable"][0]
    assert all(row["experience_id"] != "exp-old" for row in report["applied"]), "a row the store could not parse is never acted on"


def test_an_empty_store_is_an_empty_maintenance_pass_not_an_error(tmp_path: Any) -> None:
    store = HarnessImprovementStore(tmp_path / "harness_improvement")
    report = run_experience_maintenance(store, apply=True, now=_NOW)
    assert report["reviewed"] == 0
    assert report["applied"] == []
    assert report["plan"]["actions"] == []
    assert report["unreadable"] == []


def test_apply_reports_a_vanished_target_instead_of_raising(tmp_path: Any) -> None:
    """Between analysis and apply an entry can disappear in a concurrent
    process; the pass names the refusal per action and keeps going."""
    store = HarnessImprovementStore(tmp_path / "harness_improvement")
    plan = analyze_experiences(
        (
            _entry("exp-ghost", created_at="2026-01-01T00:00:00Z"),
            _entry("exp-keeper", created_at="2026-06-01T00:00:00Z"),
        ),
        now=_NOW,
    )
    assert len(plan.actions) == 1 and plan.actions[0].experience_id == "exp-ghost"
    rows = apply_maintenance(store, plan)
    assert len(rows) == 1
    assert rows[0]["experience_id"] == "exp-ghost"
    assert rows[0]["status"] == "refused" and "does not exist" in rows[0]["detail"]


def test_metadata_round_trips_maintenance_notes() -> None:
    entry = _entry("exp-a", maintenance_notes=("2026-10-19T00:00:00Z marked stale by library maintenance: expired at 2026-01-01T00:00:00Z",))
    rebuilt = ExperienceMetadata.from_dict(entry.to_dict())
    assert rebuilt.maintenance_notes == entry.maintenance_notes
    assert ExperienceMetadata.from_dict({k: v for k, v in entry.to_dict().items() if k != "maintenance_notes"}).maintenance_notes == (), "pre-maintenance metadata files keep reading"


@pytest.mark.parametrize(
    ("expires_at", "keeper_status", "expected"),
    (
        ("2026-01-01T00:00:00Z", ExperienceStatus.ACTIVE, "stale"),
        (None, ExperienceStatus.ACTIVE, None),
    ),
)
def test_expiry_requests_a_clock(expires_at: str | None, keeper_status: ExperienceStatus, expected: str | None) -> None:
    plan = analyze_experiences((_entry("exp-a", status=keeper_status, expires_at=expires_at),), now=_NOW)
    action = _by_id(plan, "exp-a")
    assert (action.action if action else None) == expected
