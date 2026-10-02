"""Work claims: advisory, short-lived, non-refusing — and the crash seam.

The refusal behaviour lives in `projects/locks.py` and is unchanged. What is
tested here is the half that did not exist: a peer-visible declaration of
intent, an overlap that is actually reported, and a dead agent's work becoming
available rather than silently held.
"""

from __future__ import annotations

import pytest

from alpha.groups.claims import (
    CLAIM_INTENTS,
    CLAIM_KINDS,
    ClaimStore,
    detect_soft_conflicts,
    get_claim_store,
    normalise_subject,
    subjects_overlap,
)


@pytest.fixture(autouse=True)
def _isolated_runtime_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path / ".alpha"))
    import alpha.groups.claims as claims_mod

    claims_mod._claims = None
    claims_mod._claims_path = None
    yield
    claims_mod._claims = None
    claims_mod._claims_path = None


@pytest.fixture
def store(tmp_path) -> ClaimStore:
    return ClaimStore(tmp_path / "claims.json")


class TestNormalisation:
    @pytest.mark.parametrize("raw", ["./a.py", "a.py", "././a.py", "src/./a.py", "src\\a.py"])
    def test_the_same_file_has_one_spelling(self, raw):
        """Otherwise `"./a.py"` and `"a.py"` are two claims on one file, which
        is exactly the overlap this layer exists to report."""
        assert normalise_subject(raw) in ("a.py", "src/a.py")

    @pytest.mark.parametrize("raw", ["src/./api/routes.py", "src/api/../api/routes.py", "src\\api\\routes.py", "./src/api/routes.py"])
    def test_nested_paths_collapse(self, raw):
        assert normalise_subject(raw) == "src/api/routes.py"

    def test_windows_and_posix_separators_agree(self):
        """A peer naming the same file must not produce a different claim."""
        assert normalise_subject("src/api/routes.py") == normalise_subject("src\\api\\routes.py")

    def test_identifiers_are_not_mangled(self):
        """`posixpath.normpath` would eat a symbol that contains a dot."""
        assert normalise_subject("SomeClass.method", kind="symbol") == "SomeClass.method"
        assert normalise_subject("TASK-42", kind="task") == "TASK-42"

    def test_an_empty_subject_normalises_to_empty(self):
        assert normalise_subject("   ") == ""
        assert normalise_subject("./", kind="dir") == ""


class TestOverlapRules:
    def test_a_dir_claim_covers_what_is_beneath_it(self):
        assert subjects_overlap("dir", "src/api", "file", "src/api/routes.py")

    def test_coverage_works_in_both_directions(self):
        assert subjects_overlap("file", "src/api/routes.py", "dir", "src/api")

    def test_a_prefix_that_is_not_a_path_boundary_does_not_overlap(self):
        """`src/api` is a directory, `src/api.py` is a different file.

        A naive `startswith` would call these a conflict and train every agent
        to ignore the signal.
        """
        assert not subjects_overlap("dir", "src/api", "file", "src/api.py")

    def test_an_exact_match_overlaps_regardless_of_kind(self):
        assert subjects_overlap("symbol", "x", "file", "x")

    def test_unrelated_subjects_do_not_overlap(self):
        assert not subjects_overlap("file", "a.py", "file", "b.py")


class TestAdvisoryByDesign:
    def test_a_second_agent_may_claim_the_same_file(self, store):
        """Non-refusing is the whole point. A refusal belongs to locks.py."""
        first = store.claim("core", "coder", "file", "src/api.py")
        second = store.claim("core", "frontend", "file", "src/api.py")
        assert first.subject == second.subject
        assert first.holder != second.holder

    def test_re_claiming_the_same_subject_renews_rather_than_duplicates(self, store):
        """A long task must extend its own claim, not accumulate near-copies."""
        first = store.claim("core", "coder", "file", "./src/api.py")
        again = store.claim("core", "coder", "file", "src/api.py")
        assert again.claim_id == first.claim_id
        assert len(store.room_claims("core", live_only=True)) == 1

    def test_an_unknown_kind_or_intent_is_refused(self, store):
        with pytest.raises(ValueError):
            store.claim("core", "coder", "socket", "x")
        with pytest.raises(ValueError):
            store.claim("core", "coder", "file", "x", intent="vandalising")

    def test_an_empty_subject_is_refused(self, store):
        with pytest.raises(ValueError):
            store.claim("core", "coder", "file", "  ")

    def test_every_declared_kind_and_intent_is_accepted(self, store):
        for kind in CLAIM_KINDS:
            for intent in CLAIM_INTENTS:
                assert store.claim("core", "coder", kind, f"subj-{kind}-{intent}", intent=intent).kind == kind


class TestSoftConflicts:
    def test_two_agents_on_one_file_conflict(self, store):
        store.claim("core", "coder", "file", "src/api.py")
        store.claim("core", "frontend", "file", "src/api.py")
        found = detect_soft_conflicts(store.room_claims("core"))
        assert len(found) == 1
        assert set(found[0].holders) == {"coder", "frontend"}
        assert found[0].reclaimable is False

    def test_one_agent_holding_two_overlaps_is_not_a_conflict(self, store):
        """It is reasoning about a file it already owns. Flagging that is noise
        that would train agents to ignore the signal."""
        store.claim("core", "coder", "file", "src/api/routes.py")
        store.claim("core", "coder", "dir", "src/api")
        assert detect_soft_conflicts(store.room_claims("core")) == []

    def test_claims_in_different_rooms_do_not_conflict(self, store):
        """A nested group is a different room; its members may differ."""
        store.claim("core", "coder", "file", "src/api.py")
        store.claim("review", "frontend", "file", "src/api.py")
        assert detect_soft_conflicts(store.room_claims("core")) == []

    def test_a_released_claim_is_not_held(self, store):
        held = store.claim("core", "coder", "file", "src/api.py")
        store.claim("core", "frontend", "file", "src/api.py")
        store.release(held.claim_id, "coder")
        assert detect_soft_conflicts(store.room_claims("core")) == []

    def test_an_expired_claim_is_not_held(self, store):
        """A stale advisory that lingers is worse than none: peers route
        around work whose owner vanished hours ago."""
        store.claim("core", "coder", "file", "src/api.py", ttl_seconds=-1)
        store.claim("core", "frontend", "file", "src/api.py")
        assert detect_soft_conflicts(store.room_claims("core")) == []


class TestCrashSeam:
    def test_a_crashed_holders_claim_is_reported_as_reclaimable(self, store):
        store.claim("core", "coder", "file", "src/api/routes.py")
        store.claim("core", "frontend", "dir", "src/api")
        found = detect_soft_conflicts(store.room_claims("core"), holder_states={"coder": "crashed", "frontend": "working"})
        assert found[0].reclaimable is True
        assert found[0].dead_holder == "coder"
        assert "available to take" in found[0].detail

    def test_an_unresponsive_holder_is_not_reclaimable(self, store):
        """Silence is not proof of death. Taking live work away would hand it
        to a second agent while the first is mid-tool-call."""
        store.claim("core", "coder", "file", "src/api.py")
        store.claim("core", "frontend", "file", "src/api.py")
        found = detect_soft_conflicts(store.room_claims("core"), holder_states={"coder": "unresponsive", "frontend": "working"})
        assert found[0].reclaimable is False

    def test_orphaning_records_the_crash_evidence(self, store):
        claim = store.claim("core", "coder", "file", "src/api.py")
        moved = store.orphan_for_bot("core", "coder", evidence={"reason": "orphan_recovered", "run_id": "r1"})
        assert [m.claim_id for m in moved] == [claim.claim_id]
        assert store.get(claim.claim_id).state == "orphaned"
        assert store.get(claim.claim_id).orphan_evidence["reason"] == "orphan_recovered"

    def test_orphaning_leaves_a_live_agent_alone(self, store):
        store.claim("core", "coder", "file", "a.py")
        assert store.orphan_for_bot("core", "frontend") == []


class TestReclaim:
    def test_an_orphaned_claim_can_be_taken_over(self, store):
        claim = store.claim("core", "coder", "file", "src/api.py")
        store.orphan_for_bot("core", "coder", evidence={"reason": "orphan_recovered"})
        taken = store.reclaim(claim.claim_id, "reviewer")
        assert taken is not None
        assert taken.holder == "reviewer"
        assert taken.state == "active"
        assert taken.orphan_evidence is None

    def test_a_live_claim_cannot_be_stolen(self, store):
        """A silent steal is the failure this layer exists to prevent."""
        claim = store.claim("core", "coder", "file", "src/api.py")
        assert store.reclaim(claim.claim_id, "intruder") is None
        assert store.get(claim.claim_id).holder == "coder"

    def test_reclaiming_an_unknown_claim_returns_none(self, store):
        assert store.reclaim("cl-nope", "reviewer") is None


class TestOwnership:
    def test_another_bot_cannot_release_a_claim(self, store):
        claim = store.claim("core", "coder", "file", "src/api.py")
        assert store.release(claim.claim_id, "intruder") is False
        assert store.release(claim.claim_id, "coder") is True

    def test_the_supervisor_may_release_any_claim(self, store):
        """The same escape `LockManager` already offers."""
        claim = store.claim("core", "coder", "file", "src/api.py")
        assert store.release(claim.claim_id, "supervisor") is True

    def test_leaving_releases_every_claim_the_agent_held(self, store):
        store.claim("core", "coder", "file", "a.py")
        store.claim("core", "coder", "file", "b.py")
        assert store.release_for_bot("core", "coder") == 2
        assert store.room_claims("core", live_only=True) == []


class TestDurability:
    def test_claims_survive_a_restart(self, tmp_path):
        path = tmp_path / "claims.json"
        first = ClaimStore(path)
        first.claim("core", "coder", "file", "src/api.py", detail="adding a route")
        assert ClaimStore(path).room_claims("core")[0].detail == "adding a route"

    def test_a_corrupt_store_starts_empty_instead_of_raising(self, tmp_path):
        path = tmp_path / "claims.json"
        path.write_text("{not json", encoding="utf-8")
        assert ClaimStore(path).room_claims("core") == []

    def test_claims_expire_and_are_swept(self, store):
        store.claim("core", "coder", "file", "src/api.py", ttl_seconds=-1)
        assert store.sweep() == 1
        assert store.room_claims("core", live_only=True) == []

    def test_held_paths_are_derived_for_the_display(self, store):
        store.claim("core", "coder", "file", "src/api.py")
        store.claim("core", "coder", "file", "src/db.py")
        assert sorted(store.held_by_bot("core")["coder"]) == ["src/api.py", "src/db.py"]

    def test_one_agent_cannot_flood_the_room(self, store):
        for i in range(25):
            store.claim("core", "coder", "file", f"f{i}.py")
        with pytest.raises(ValueError):
            store.claim("core", "coder", "file", "one-too-many.py")

    def test_the_singleton_is_isolated_per_runtime_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALPHA_HOME", str(tmp_path / "one"))
        get_claim_store().claim("core", "coder", "file", "a.py")
        monkeypatch.setenv("ALPHA_HOME", str(tmp_path / "two"))
        assert get_claim_store().room_claims("core") == []
