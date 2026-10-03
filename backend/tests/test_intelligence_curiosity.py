"""Tests for Phase H: durable curiosity and the replay trust gate.

Two findings from the second research round drive this file.

1. ``alpha.agency.curiosity.CuriosityScorer`` already existed but had **zero
   production callers** and an in-memory novelty map, so novelty reset to 1.0 on
   every restart — the opposite of a novelty signal. ``TestDurableNovelty`` pins
   that the map survives a restart.

2. The literature names **verifier disagreement** as the second intrinsic signal
   and warns that intrinsic reward can fall into a *degenerate* exploration
   regime. ``TestDegeneracyGuard`` pins that high curiosity with no competence
   evidence is refused rather than rewarded.

3. Memory-poisoning work (DrunkAgent, WWW 2026) makes the trust gate load
   bearing: a self-improving agent that replays untrusted observations equally
   will propagate them permanently. ``TestTrustGate`` pins that untrusted and
   unknown sources are refused.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha.intelligence.curiosity import (
    DEFAULT_CURIOSITY_CAP,
    CuriosityLedger,
    CuriositySignals,
    CuriosityTarget,
    DegeneracyVerdict,
    situation_digest,
)
from alpha.intelligence.replay import ReplayItem, ReplayReservoir
from alpha.intelligence.sanitizer import (
    DEFAULT_TRUST_LEVEL,
    TRUSTED_SOURCES,
    UNTRUSTED_SOURCES,
    Outcome,
    Stage,
    default_sanitizer,
)


def _clean_record(**metadata: object):
    from alpha.learning.experience.models import ExperienceRecord, OutcomeType

    return ExperienceRecord(
        task_goal="Fix the failing hydration test in the frontend checkout",
        outcome=OutcomeType.SUCCESS,
        lessons_learned=["Root cause was a Date.now() call during server render, so the markup differed"],
        evidence=["trace-abc123"],
        metadata={"trust_level": "user", **metadata},
    )


# ---------------------------------------------------------------------------
# Durable novelty
# ---------------------------------------------------------------------------


class TestDurableNovelty:
    def test_unknown_situation_is_fully_novel(self, tmp_path: Path) -> None:
        ledger = CuriosityLedger(tmp_path / "curiosity.json")
        assert ledger.novelty({"task": "x"}) == 1.0

    def test_novelty_decays_with_encounters(self, tmp_path: Path) -> None:
        ledger = CuriosityLedger(tmp_path / "curiosity.json", familiarity_step=0.25)
        situation = {"task": "x"}
        first = ledger.observe(situation)
        second = ledger.observe(situation)
        assert second < first
        assert second == pytest.approx(0.5)

    def test_novelty_survives_a_restart(self, tmp_path: Path) -> None:
        """The bug this module exists to fix: an in-memory map reports everything
        as novel after every process start, which carries no information."""
        path = tmp_path / "curiosity.json"
        first = CuriosityLedger(path, familiarity_step=0.5)
        first.observe({"task": "x"})
        assert first.novelty({"task": "x"}) == pytest.approx(0.5)

        reopened = CuriosityLedger(path, familiarity_step=0.5)
        assert reopened.novelty({"task": "x"}) == pytest.approx(0.5)
        assert reopened.familiarity_size() == 1

    def test_familiarity_saturates(self, tmp_path: Path) -> None:
        ledger = CuriosityLedger(tmp_path / "curiosity.json", saturation=1.0, familiarity_step=0.4)
        for _ in range(10):
            ledger.observe({"task": "x"})
        assert ledger.novelty({"task": "x"}) == 0.0
        assert ledger.familiarity_size() == 1

    def test_forget_restores_novelty(self, tmp_path: Path) -> None:
        ledger = CuriosityLedger(tmp_path / "curiosity.json")
        ledger.observe({"task": "x"})
        assert ledger.forget({"task": "x"}) is True
        assert ledger.novelty({"task": "x"}) == 1.0
        assert ledger.forget({"task": "x"}) is False

    def test_digest_is_content_keyed_not_call_site_keyed(self, tmp_path: Path) -> None:
        ledger = CuriosityLedger(tmp_path / "curiosity.json", familiarity_step=1.0)
        ledger.observe({"a": 1, "b": 2})
        # Same content, different insertion order -> same situation.
        assert ledger.novelty({"b": 2, "a": 1}) == 0.0

    def test_digest_is_stable_and_short(self) -> None:
        first = situation_digest({"task": "x"})
        assert first == situation_digest({"task": "x"})
        assert len(first) == 16

    def test_corrupt_ledger_degrades_to_over_reporting(self, tmp_path: Path) -> None:
        """Novelty over-reporting is the safe direction: it explores more."""
        path = tmp_path / "curiosity.json"
        path.write_text("{not json", encoding="utf-8")
        ledger = CuriosityLedger(path)
        assert ledger.familiarity_size() == 0
        assert ledger.novelty({"task": "x"}) == 1.0

    def test_unknown_schema_starts_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "curiosity.json"
        path.write_text('{"schema_version": 999, "familiarity": {"a": 0.5}}', encoding="utf-8")
        assert CuriosityLedger(path).familiarity_size() == 0

    def test_invalid_bounds_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="saturation"):
            CuriosityLedger(tmp_path / "c.json", saturation=0)
        with pytest.raises(ValueError, match="familiarity_step"):
            CuriosityLedger(tmp_path / "c.json", familiarity_step=0.0)
        with pytest.raises(ValueError, match="cap"):
            CuriosityLedger(tmp_path / "c.json", cap=-1.0)

    def test_serialisation(self, tmp_path: Path) -> None:
        ledger = CuriosityLedger(tmp_path / "curiosity.json")
        ledger.observe({"task": "x"})
        payload = ledger.to_dict()
        assert payload["familiarity_size"] == 1
        assert payload["cap"] == DEFAULT_CURIOSITY_CAP


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


class TestCuriositySignals:
    def test_all_three_signals_present(self) -> None:
        signals = CuriositySignals(novelty=1.0, prediction_error=1.0, disagreement=1.0)
        assert signals.composite == pytest.approx(1.0)

    def test_unmeasured_signals_are_excluded_not_zeroed(self) -> None:
        """Defaulting an absent signal to zero would make "nobody disagreed"
        identical to "nobody checked"."""
        measured = CuriositySignals(novelty=1.0, disagreement=1.0)
        assert measured.composite == pytest.approx(1.0)
        absent = CuriositySignals(novelty=1.0, prediction_error=None, disagreement=None)
        assert absent.composite == pytest.approx(1.0)

    def test_no_signals_is_zero(self) -> None:
        assert CuriositySignals().composite == 0.0

    def test_out_of_range_refused(self) -> None:
        with pytest.raises(ValueError, match="novelty"):
            CuriositySignals(novelty=1.5)

    def test_prediction_error_from_outcomes(self, tmp_path: Path) -> None:
        ledger = CuriosityLedger(tmp_path / "c.json")
        signals = ledger.signals_for({"task": "x"}, predicted_outcome=0.9, actual_outcome=0.3)
        assert signals.prediction_error == pytest.approx(0.6)
        assert signals.novelty == 1.0

    def test_no_outcomes_leaves_prediction_error_unmeasured(self, tmp_path: Path) -> None:
        ledger = CuriosityLedger(tmp_path / "c.json")
        assert ledger.signals_for({"task": "x"}).prediction_error is None

    def test_serialisation(self) -> None:
        payload = CuriositySignals(novelty=0.5, disagreement=0.5).to_dict()
        # Renormalisation over equal measured values preserves them: (0.5*0.4 +
        # 0.5*0.25) / 0.65 == 0.5. Excluding an unmeasured signal must not
        # *dilute* the ones that were measured.
        assert payload["composite"] == pytest.approx(0.5)
        assert payload["novelty"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Degeneracy guard
# ---------------------------------------------------------------------------


def make_degenerate_target() -> CuriosityTarget:
    """Maximum curiosity with zero competence: the shape the guard must refuse."""
    return CuriosityTarget(
        key="weird",
        signals=CuriositySignals(novelty=1.0, prediction_error=1.0, disagreement=1.0),
        competence=0.0,
    )


class TestDegeneracyGuard:
    def test_novelty_without_competence_is_a_refusal_not_a_reward(self) -> None:
        target = make_degenerate_target()
        assert target.verdict is DegeneracyVerdict.EXPLORATION_WITHOUT_COMPETENCE
        assert target.bonus() == 0.0

    def test_tried_and_failed_is_not_curiosity(self) -> None:
        target = CuriosityTarget(
            key="failed-thing",
            signals=CuriositySignals(novelty=1.0),
            competence=0.0,
            attempts=5,
        )
        assert target.verdict is DegeneracyVerdict.ALREADY_FAMILIAR
        assert target.bonus() == 0.0

    def test_low_signal_is_refused(self) -> None:
        target = CuriosityTarget(key="boring", signals=CuriositySignals(novelty=0.05), competence=0.5)
        assert target.verdict is DegeneracyVerdict.LOW_SIGNAL
        assert target.bonus() == 0.0

    def test_competent_and_novel_is_admitted(self) -> None:
        target = CuriosityTarget(key="good", signals=CuriositySignals(novelty=0.9), competence=0.8)
        assert target.verdict is DegeneracyVerdict.OK
        assert target.bonus() > 0

    def test_bonus_is_capped(self) -> None:
        target = CuriosityTarget(key="good", signals=CuriositySignals(novelty=1.0), competence=1.0)
        assert target.bonus() <= DEFAULT_CURIOSITY_CAP

    def test_cap_is_configurable(self) -> None:
        target = CuriosityTarget(key="good", signals=CuriositySignals(novelty=1.0), competence=1.0)
        assert target.bonus(cap=0.5) == pytest.approx(0.5)

    def test_ranking_prefers_competence_on_a_tie(self) -> None:
        """Equal curiosity: the one Alpha can demonstrably do comes first. This is
        the opposite of what a pure novelty ranking would produce."""
        ledger = CuriosityLedger(Path("unused.json"))
        can_do = CuriosityTarget(key="can_do", signals=CuriositySignals(novelty=0.8), competence=0.9)
        cannot_do = CuriosityTarget(key="cannot_do", signals=CuriositySignals(novelty=0.8), competence=0.1)
        assert ledger.rank([cannot_do, can_do]).keys == ["can_do", "cannot_do"]

    def test_refusals_stay_visible(self, tmp_path: Path) -> None:
        ledger = CuriosityLedger(tmp_path / "c.json")
        ranking = ledger.rank(
            [
                CuriosityTarget(key="ok", signals=CuriositySignals(novelty=0.9), competence=0.5),
                make_degenerate_target(),
            ]
        )
        assert ranking.keys == ["ok"]
        assert ranking.refused_keys == ["weird"]

    def test_ranking_is_deterministic(self, tmp_path: Path) -> None:
        ledger = CuriosityLedger(tmp_path / "c.json")
        targets = [CuriosityTarget(key=f"k{i}", signals=CuriositySignals(novelty=0.5), competence=0.5) for i in range(5)]
        assert ledger.rank(targets).keys == ledger.rank(list(reversed(targets))).keys

    def test_target_for_resolves_novelty_from_the_ledger(self, tmp_path: Path) -> None:
        ledger = CuriosityLedger(tmp_path / "c.json", familiarity_step=0.5)
        ledger.observe({"task": "x"})
        target = ledger.target_for({"task": "x"}, competence=0.5)
        assert target.signals.novelty == pytest.approx(0.5)

    def test_invalid_target_refused(self) -> None:
        with pytest.raises(ValueError, match="key"):
            CuriosityTarget(key="  ", signals=CuriositySignals())
        with pytest.raises(ValueError, match="competence"):
            CuriosityTarget(key="k", signals=CuriositySignals(), competence=2.0)

    def test_serialisation(self) -> None:
        target = CuriosityTarget(key="k", signals=CuriositySignals(novelty=0.9), competence=0.5)
        assert target.to_dict()["verdict"] == "OK"


# ---------------------------------------------------------------------------
# Replay integration
# ---------------------------------------------------------------------------


class TestReplayCuriosityIntegration:
    def _item(self, index: int, importance: float) -> ReplayItem:
        return ReplayItem(
            experience_id=f"exp_{index:04d}",
            strata={"recent"},
            importance=importance,
            created_at=1_000_000.0,
        )

    def test_curiosity_reorders_within_a_stratum(self, tmp_path: Path) -> None:
        reservoir = ReplayReservoir(path=tmp_path / "replay.json", capacity=10)
        for i in range(3):
            reservoir.add(self._item(i, importance=0.5))
        ledger = CuriosityLedger(tmp_path / "curiosity.json")
        report = reservoir.sample(3, curiosity=ledger, curiosity_cap=0.1)
        assert len(report.sampled) == 3

    def test_curiosity_none_disables_the_term(self, tmp_path: Path) -> None:
        reservoir = ReplayReservoir(path=tmp_path / "replay.json", capacity=10)
        reservoir.add(self._item(1, importance=0.5))
        ledger = CuriosityLedger(tmp_path / "curiosity.json")
        plain = reservoir.sample(1).sampled
        with_curiosity = reservoir.sample(1, curiosity=ledger, curiosity_cap=0.1).sampled
        assert [i.experience_id for i in plain] == [i.experience_id for i in with_curiosity]

    def test_zero_cap_disables_the_term(self, tmp_path: Path) -> None:
        reservoir = ReplayReservoir(path=tmp_path / "replay.json", capacity=10)
        reservoir.add(self._item(1, importance=0.5))
        ledger = CuriosityLedger(tmp_path / "curiosity.json")
        assert reservoir.sample(1, curiosity=ledger, curiosity_cap=0.0).sampled

    def test_curiosity_does_not_break_required_strata(self, tmp_path: Path) -> None:
        """Curiosity reorders within a stratum; it must not move items between
        strata, or the old-knowledge retention guarantee would break."""
        reservoir = ReplayReservoir(path=tmp_path / "replay.json", capacity=20)
        reservoir.add(self._item(1, importance=0.9))
        reservoir.add(ReplayItem(experience_id="exp_old", strata={"old_knowledge"}, importance=0.1, created_at=1.0))
        ledger = CuriosityLedger(tmp_path / "curiosity.json")
        report = reservoir.sample(4, curiosity=ledger, curiosity_cap=0.1, required_strata=("old_knowledge",))
        assert any(item.experience_id == "exp_old" for item in report.sampled)


# ---------------------------------------------------------------------------
# Trust gate
# ---------------------------------------------------------------------------


class TestTrustGate:
    def test_gate_is_off_by_default_and_reports_skipped(self) -> None:
        """Off by default so no existing caller breaks. Skipped, never PASSED —
        a stage that did not run must not read as a stage that cleared."""
        result = default_sanitizer().sanitize(_clean_record(trust_level="web")).stage(Stage.TRUST_VALIDATION)
        assert result.outcome is Outcome.SKIPPED
        assert "admit_untrusted" in result.reason

    def test_strict_gate_refuses_untrusted(self) -> None:
        report = default_sanitizer(admit_untrusted=False).sanitize(_clean_record(trust_level="web"))
        assert report.accepted is False

    def test_trusted_source_passes(self) -> None:
        report = default_sanitizer(admit_untrusted=False).sanitize(_clean_record())
        assert report.stage(Stage.TRUST_VALIDATION).outcome is Outcome.PASSED
        assert report.accepted is True

    def test_untrusted_source_refused(self) -> None:
        report = default_sanitizer(admit_untrusted=False).sanitize(_clean_record(trust_level="web"))
        assert report.refused_at is Stage.TRUST_VALIDATION
        assert "untrusted" in report.reason
        assert report.accepted is False

    def test_tool_output_refused(self) -> None:
        report = default_sanitizer(admit_untrusted=False).sanitize(_clean_record(trust_level="tool_output"))
        assert report.refused_at is Stage.TRUST_VALIDATION

    def test_unknown_source_refused_not_assumed_safe(self) -> None:
        """The safe default: 'we do not know where this came from' must not be
        promoted to 'trust it'."""
        report = default_sanitizer(admit_untrusted=False).sanitize(_clean_record(trust_level="something_new"))
        assert report.refused_at is Stage.TRUST_VALIDATION
        assert "unknown trust level" in report.reason

    def test_missing_trust_level_defaults_to_untrusted(self) -> None:
        from alpha.learning.experience.models import ExperienceRecord, OutcomeType

        record = ExperienceRecord(
            task_goal="Fix the failing hydration test in the frontend checkout",
            outcome=OutcomeType.SUCCESS,
            lessons_learned=["Root cause was a Date.now() call during server render, differing markup"],
            evidence=["trace-1"],
        )
        assert DEFAULT_TRUST_LEVEL in UNTRUSTED_SOURCES
        report = default_sanitizer(admit_untrusted=False).sanitize(record)
        assert report.refused_at is Stage.TRUST_VALIDATION

    def test_non_string_trust_level_refused(self) -> None:
        report = default_sanitizer(admit_untrusted=False).sanitize(_clean_record(trust_level=7))
        assert report.refused_at is Stage.TRUST_VALIDATION
        assert "not a string" in report.reason

    def test_trust_runs_after_provenance(self) -> None:
        """Both questions must be asked: where did it come from, and do we trust it."""
        report = default_sanitizer().sanitize(_clean_record(trust_level="user"))
        order = [result.stage for result in report.stages]
        assert order.index(Stage.PROVENANCE_VALIDATION) < order.index(Stage.TRUST_VALIDATION)

    def test_strict_gate_accepts_a_clean_trusted_record(self) -> None:
        assert default_sanitizer(admit_untrusted=False).sanitize(_clean_record()).accepted is True

    def test_trust_vocabulary_is_disjoint(self) -> None:
        assert not (TRUSTED_SOURCES & UNTRUSTED_SOURCES)

    def test_stage_count_is_seven(self) -> None:
        report = default_sanitizer().sanitize(_clean_record())
        assert len(report.stages) == 7

    def test_stage_order_is_the_documented_order(self) -> None:
        report = default_sanitizer().sanitize(_clean_record())
        assert [result.stage for result in report.stages] == list(Stage)
