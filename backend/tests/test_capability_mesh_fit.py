"""Tests for context-aware fit scoring (plan §3.3): observable parts, hard gates first.

Hard constraints are gates, never penalties; an unmeasured capability is a
prior (0.25 coverage, 0.5 neutral quality), never evidence; the weights are
hand-checkable constants; recency is a multiplier with a stated reason; and
ranking keeps unmeasured priors behind measured fits on an equal score.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alpha.capability_mesh.decomposition import Microtask
from alpha.capability_mesh.fingerprint import Pressure, Risk, SituationFingerprint
from alpha.capability_mesh.fit import (
    COVERAGE_WEIGHT,
    NEUTRAL_QUALITY,
    QUALITY_WEIGHT,
    RECENCY_PENALTY,
    TOOL_WEIGHT,
    UNMEASURED_PRIOR,
    FitScore,
    HardConstraintRefusal,
    eligible_entities,
    fit_score,
    rank_entities,
)
from alpha.capability_mesh.memory import CapabilityMemory, CapabilityRecord, OutcomeKind
from alpha.capability_mesh.models import CapabilityEstimate, CapabilityPassport, EntityKind, EvidenceLevel

NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

_ids = iter(range(1, 1_000_000))


def _estimate(
    capability: str,
    *,
    quality: float | None = None,
    uncertainty: float | None = None,
    samples: int = 0,
    level: EvidenceLevel = EvidenceLevel.UNMEASURED,
    last: str | None = None,
) -> CapabilityEstimate:
    return CapabilityEstimate(capability=capability, task_category="*", quality_mean=quality, uncertainty=uncertainty, sample_count=samples, evidence_level=level, last_evaluated_at=last, profile_version="p1")


def _passport(entity_id: str = "agent:a", *, capabilities: tuple[CapabilityEstimate, ...] = (), required_tools: tuple[str, ...] = (), enabled: bool = True) -> CapabilityPassport:
    return CapabilityPassport(entity_id=entity_id, entity_type=EntityKind.AGENT, version="v1", capabilities=capabilities, required_tools=required_tools, enabled=enabled)


def _microtask(*, capabilities: tuple[str, ...] = (), tools: tuple[str, ...] = ()) -> Microtask:
    return Microtask(microtask_id="mt-1", title="t", summary="s", acceptance_checks=("the check passes",), required_capabilities=capabilities, required_tools=tools)


def _fingerprint(*, recency_required: bool = False) -> SituationFingerprint:
    return SituationFingerprint(task_type="general", risk_band=Risk.LOW, context_pressure=Pressure.LOW, recency_required=recency_required)


def _memory(tmp_path: Path, *, quality: float = 0.9, profile: str = "p1", count: int = 2) -> CapabilityMemory:
    memory = CapabilityMemory(tmp_path)
    for index in range(count):
        memory.record(
            CapabilityRecord(
                record_id=f"r{next(_ids)}",
                task_fingerprint="fp_0011223344556677",
                task_category="*",
                entity_id="agent:a",
                capability="code",
                outcome=OutcomeKind.SELECTED,
                quality=quality,
                profile_version=profile,
                recorded_at=f"2025-06-0{index + 1}T00:00:00+00:00",
            )
        )
    return memory


class TestHardGatesBeforeScore:
    def test_disabled_passport_is_refused_not_scored(self) -> None:
        eligible, refusals = eligible_entities(_microtask(), [_passport(enabled=False)])
        assert eligible == ()
        assert len(refusals) == 1
        assert "disabled" in refusals[0].unmet
        assert refusals[0].microtask_id == "mt-1"

    def test_missing_tool_is_refused_by_name(self) -> None:
        _, refusals = eligible_entities(_microtask(tools=("bash",)), [_passport()])
        assert refusals[0].unmet == ("missing tool:bash",)

    def test_tool_covered_by_required_tools_or_capabilities(self) -> None:
        by_tool = _passport(required_tools=("bash",))
        by_name = _passport(capabilities=(_estimate("bash"),))
        by_use_prefix = _passport(capabilities=(_estimate("use:bash"),))
        for passport in (by_tool, by_name, by_use_prefix):
            eligible, refusals = eligible_entities(_microtask(tools=("bash",)), [passport])
            assert eligible == (passport,)
            assert refusals == ()

    def test_great_fit_with_a_missing_tool_is_still_refused(self) -> None:
        # The plan invariant: no failed hard constraint can be overridden by
        # an aggregate fit score. This passport would score 0.825 — it is
        # refused anyway, and appears nowhere in the ranking.
        great = _passport(capabilities=(_estimate("code", quality=0.99, uncertainty=0.0, samples=5, level=EvidenceLevel.OBSERVED),), required_tools=())
        scores, refusals = rank_entities(_microtask(capabilities=("code",), tools=("bash",)), [great], profile_version="p1")
        assert scores == ()
        assert refusals[0].unmet == ("missing tool:bash",)

    def test_refusal_collects_every_unmet_reason(self) -> None:
        _, refusals = eligible_entities(_microtask(tools=("bash", "web")), [_passport(enabled=False)])
        assert refusals[0].unmet == ("disabled", "missing tool:bash", "missing tool:web")


class TestRefusals:
    def test_profile_version_is_required(self) -> None:
        with pytest.raises(ValueError, match="profile_version"):
            fit_score(_passport(), _microtask(), profile_version="")

    def test_fit_score_shape_is_validated(self) -> None:
        base = dict(entity_id="agent:a", fit_score=0.5, score_explanation=("x",), uncertainty=0.0, evidence_level=EvidenceLevel.UNMEASURED, estimated=True)
        with pytest.raises(ValueError, match="fit_score"):
            FitScore(**{**base, "fit_score": 1.5})  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="score_explanation must be non-empty"):
            FitScore(**{**base, "score_explanation": ()})  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="uncertainty"):
            FitScore(**{**base, "uncertainty": -0.1})  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="estimated"):
            FitScore(**{**base, "estimated": "yes"})  # type: ignore[arg-type]

    def test_refusal_shape_is_validated(self) -> None:
        with pytest.raises(ValueError, match="unmet must be non-empty"):
            HardConstraintRefusal(entity_id="agent:a", unmet=())
        with pytest.raises(ValueError, match="entity_id"):
            HardConstraintRefusal(entity_id="", unmet=("disabled",))


class TestWeightMath:
    def test_hand_computed_composite_with_a_prior(self) -> None:
        # coverage = (1.0 measured + 0.25 unmeasured) / 2 = 0.625
        # quality  = 0.5 neutral (no memory)
        # tool     = 1.0 (bash covered)
        # fit      = 0.5*0.625 + 0.35*0.5 + 0.15*1.0 = 0.6375
        passport = _passport(
            capabilities=(
                _estimate("code", quality=0.8, uncertainty=0.1, samples=5, level=EvidenceLevel.OBSERVED),
                _estimate("review"),
            ),
            required_tools=("bash",),
        )
        score = fit_score(passport, _microtask(capabilities=("code", "review"), tools=("bash",)), memory=None, profile_version="p1", fingerprint=_fingerprint())
        assert score.fit_score == pytest.approx(0.5 * 0.625 + 0.35 * 0.5 + 0.15 * 1.0)
        assert score.estimated is True  # one required capability is unmeasured: a prior, not a measurement
        assert score.evidence_level is EvidenceLevel.UNMEASURED
        assert score.uncertainty == 0.1
        # Every factor travels with its number.
        assert any(line.startswith("coverage 0.625") for line in score.score_explanation)
        assert any(line.startswith("quality 0.500") for line in score.score_explanation)
        assert any(line.startswith("tool margin 1.000") for line in score.score_explanation)
        assert any("evidence level unmeasured" in line for line in score.score_explanation)
        assert any(line.startswith("weighted fit") for line in score.score_explanation)

    def test_hand_computed_composite_fully_measured(self) -> None:
        # coverage = 1.0, quality = 0.5 neutral, tool = 1.0 -> 0.825
        passport = _passport(capabilities=(_estimate("code", quality=0.8, uncertainty=0.1, samples=5, level=EvidenceLevel.OBSERVED),))
        score = fit_score(passport, _microtask(capabilities=("code",)), memory=None, profile_version="p1")
        assert score.fit_score == pytest.approx(0.5 * 1.0 + 0.35 * NEUTRAL_QUALITY + 0.15 * 1.0)
        assert score.estimated is False
        assert score.evidence_level is EvidenceLevel.OBSERVED
        assert score.uncertainty == 0.1  # the estimate's own stated uncertainty

    def test_memory_quality_is_measured_not_neutral(self, tmp_path: Path) -> None:
        # memory: two SELECTED records of quality 0.9 under p1 -> quality 0.9;
        # the measured memory estimate also drives evidence (RECENTLY_REVALIDATED
        # at the injected clock), so coverage counts the capability as measured.
        # fit = 0.5*1.0 + 0.35*0.9 + 0.15*1.0 = 0.965
        memory = _memory(tmp_path)
        passport = _passport(capabilities=(_estimate("code"),))
        score = fit_score(passport, _microtask(capabilities=("code",)), memory=memory, profile_version="p1", fingerprint=_fingerprint(), now=NOW)
        assert score.fit_score == pytest.approx(0.5 * 1.0 + 0.35 * 0.9 + 0.15 * 1.0)
        assert score.evidence_level is EvidenceLevel.RECENTLY_REVALIDATED
        assert score.estimated is False
        assert score.uncertainty == 0.0  # zero spread across identical samples; absence of spread, not exactness

    def test_memory_is_scoped_to_the_profile_version(self, tmp_path: Path) -> None:
        # Records measured under rubric p1 must not leak into a p2 estimate:
        # the memory answers honest UNMEASURED, so coverage falls to the
        # 0.25 prior and quality to the 0.5 neutral -> 0.45.
        memory = _memory(tmp_path, profile="p1")
        passport = _passport(capabilities=(_estimate("code"),))
        score = fit_score(passport, _microtask(capabilities=("code",)), memory=memory, profile_version="p2", fingerprint=_fingerprint(), now=NOW)
        assert score.fit_score == pytest.approx(0.5 * UNMEASURED_PRIOR + 0.35 * NEUTRAL_QUALITY + 0.15 * 1.0)
        assert score.estimated is True

    def test_weights_are_pinned(self) -> None:
        assert COVERAGE_WEIGHT + QUALITY_WEIGHT + TOOL_WEIGHT == pytest.approx(1.0)


class TestRecencyPenalty:
    def test_stale_driving_estimate_is_penalised_with_a_reason(self) -> None:
        passport = _passport(capabilities=(_estimate("code", quality=0.8, uncertainty=0.1, samples=5, level=EvidenceLevel.OBSERVED, last="2020-01-01T00:00:00+00:00"),))
        score = fit_score(passport, _microtask(capabilities=("code",)), profile_version="p1", fingerprint=_fingerprint(recency_required=True), now=NOW)
        assert score.fit_score == pytest.approx((0.5 * 1.0 + 0.35 * NEUTRAL_QUALITY + 0.15 * 1.0) * RECENCY_PENALTY)
        assert any("recency required; no recent measurement" in line for line in score.score_explanation)

    def test_fresh_measurement_is_not_penalised(self) -> None:
        passport = _passport(capabilities=(_estimate("code", quality=0.8, uncertainty=0.1, samples=5, level=EvidenceLevel.OBSERVED, last="2025-06-14T00:00:00+00:00"),))
        score = fit_score(passport, _microtask(capabilities=("code",)), profile_version="p1", fingerprint=_fingerprint(recency_required=True), now=NOW)
        assert score.fit_score == pytest.approx(0.5 * 1.0 + 0.35 * NEUTRAL_QUALITY + 0.15 * 1.0)
        assert any("recency required; driving estimate measured" in line for line in score.score_explanation)

    def test_unmeasured_prior_never_satisfies_recency(self) -> None:
        # An UNMEASURED driving estimate has no timestamp at all: recency
        # demanded, recency unproven -> penalty, every time.
        passport = _passport(capabilities=(_estimate("code", quality=0.8, uncertainty=0.1, samples=5, level=EvidenceLevel.OBSERVED, last="2025-06-14T00:00:00+00:00"), _estimate("review")))
        score = fit_score(passport, _microtask(capabilities=("code", "review")), profile_version="p1", fingerprint=_fingerprint(recency_required=True), now=NOW)
        assert score.fit_score == pytest.approx(0.6375 * RECENCY_PENALTY)


class TestRanking:
    def test_ranked_by_score_desc(self) -> None:
        measured = _passport("agent:m", capabilities=(_estimate("code", quality=0.9, uncertainty=0.1, samples=5, level=EvidenceLevel.OBSERVED),))
        unmeasured = _passport("agent:u", capabilities=(_estimate("code"),))
        scores, _ = rank_entities(_microtask(capabilities=("code",)), [unmeasured, measured], profile_version="p1")
        assert [score.entity_id for score in scores] == ["agent:m", "agent:u"]
        assert scores[0].fit_score > scores[1].fit_score

    def test_unmeasured_prior_never_outranks_a_measured_fit_at_the_same_score(self) -> None:
        # No declared requirements -> both score 0.825 exactly. The measured
        # one must still lead EVEN THOUGH its stated uncertainty (0.5) is
        # higher than the prior's constant 0.1: estimated-after-measured is
        # the stronger tiebreak, or a prior could smuggle itself ahead.
        measured = _passport("agent:a", capabilities=(_estimate("code", quality=0.7, uncertainty=0.5, samples=1, level=EvidenceLevel.OBSERVED),))
        prior = _passport("agent:b", capabilities=())
        scores, _ = rank_entities(_microtask(), [prior, measured], profile_version="p1")
        assert scores[0].fit_score == pytest.approx(scores[1].fit_score)
        assert [score.entity_id for score in scores] == ["agent:a", "agent:b"]
        assert scores[0].estimated is False and scores[1].estimated is True

    def test_uncertainty_orders_within_the_same_evidence_class(self) -> None:
        sloppy = _passport("agent:a", capabilities=(_estimate("code", quality=0.7, uncertainty=0.5, samples=1, level=EvidenceLevel.OBSERVED),))
        tight = _passport("agent:z", capabilities=(_estimate("code", quality=0.7, uncertainty=0.0, samples=2, level=EvidenceLevel.OBSERVED),))
        scores, _ = rank_entities(_microtask(), [sloppy, tight], profile_version="p1")
        assert [score.entity_id for score in scores] == ["agent:z", "agent:a"]

    def test_entity_id_is_the_final_stable_tiebreak(self) -> None:
        first = _passport("agent:a", capabilities=(_estimate("code"),))
        second = _passport("agent:b", capabilities=(_estimate("code"),))
        scores, _ = rank_entities(_microtask(capabilities=("code",)), [second, first], profile_version="p1")
        assert [score.entity_id for score in scores] == ["agent:a", "agent:b"]

    def test_refusals_travel_back_untouched(self) -> None:
        disabled = _passport("agent:d", capabilities=(_estimate("code", quality=0.9, uncertainty=0.0, samples=5, level=EvidenceLevel.OBSERVED),), enabled=False)
        live = _passport("agent:l", capabilities=(_estimate("code"),))
        scores, refusals = rank_entities(_microtask(capabilities=("code",)), [disabled, live], profile_version="p1")
        assert [score.entity_id for score in scores] == ["agent:l"]
        assert [refusal.entity_id for refusal in refusals] == ["agent:d"]
