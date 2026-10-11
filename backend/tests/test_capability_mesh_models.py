"""Tests for the capability mesh value types: evidence levels, estimates, passports.

Everything here is offline and pure: no store, no network, no model calls.
The evidence-level matrix is the contract later phases (routing, team
formation) will rank candidates with, so every branch — including the
boundaries at sample_count 0/1/10, staleness, and unparseable timestamps —
is pinned explicitly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from alpha.capability_mesh.models import (
    EVIDENCE_RANK,
    CapabilityEstimate,
    CapabilityPassport,
    EntityKind,
    EvidenceLevel,
    derive_evidence_level,
    evidence_rank,
)

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


class TestEvidenceLevelOrdering:
    def test_rank_is_declaration_order_not_string_order(self) -> None:
        """A StrEnum compares by string, so ranking must never use ``min()``.

        ``"unmeasured" > "recently_revalidated"`` lexicographically, which
        would invert every worst-level aggregation if the enum were compared
        directly. The explicit rank table is the only ordering.
        """
        assert EVIDENCE_RANK[EvidenceLevel.UNMEASURED] < EVIDENCE_RANK[EvidenceLevel.OBSERVED]
        assert EVIDENCE_RANK[EvidenceLevel.OBSERVED] < EVIDENCE_RANK[EvidenceLevel.BENCHMARKED]
        assert EVIDENCE_RANK[EvidenceLevel.BENCHMARKED] < EVIDENCE_RANK[EvidenceLevel.OUTCOME_VERIFIED]
        assert EVIDENCE_RANK[EvidenceLevel.OUTCOME_VERIFIED] < EVIDENCE_RANK[EvidenceLevel.RECENTLY_REVALIDATED]

    def test_string_comparison_is_not_the_rank(self) -> None:
        # Documents WHY evidence_rank() exists: naive string ordering disagrees.
        assert "unmeasured" > "recently_revalidated"
        assert evidence_rank(EvidenceLevel.UNMEASURED) < evidence_rank(EvidenceLevel.RECENTLY_REVALIDATED)

    def test_values_match_the_plan_vocabulary(self) -> None:
        assert [level.value for level in EvidenceLevel] == [
            "unmeasured",
            "observed",
            "benchmarked",
            "outcome_verified",
            "recently_revalidated",
        ]


class TestDeriveEvidenceLevel:
    def test_zero_samples_is_always_unmeasured(self) -> None:
        assert derive_evidence_level(0, has_outcome=False, last_evaluated_at=None, now=NOW) is EvidenceLevel.UNMEASURED

    def test_zero_samples_with_outcome_is_still_unmeasured(self) -> None:
        """No observations means no evidence, whatever the story around it."""
        level = derive_evidence_level(0, has_outcome=True, last_evaluated_at=_iso(NOW), now=NOW)
        assert level is EvidenceLevel.UNMEASURED

    def test_negative_samples_is_unmeasured(self) -> None:
        assert derive_evidence_level(-3, has_outcome=False, last_evaluated_at=None, now=NOW) is EvidenceLevel.UNMEASURED

    def test_one_sample_without_outcome_is_observed(self) -> None:
        assert derive_evidence_level(1, has_outcome=False, last_evaluated_at=None, now=NOW) is EvidenceLevel.OBSERVED

    def test_nine_samples_without_outcome_is_observed(self) -> None:
        assert derive_evidence_level(9, has_outcome=False, last_evaluated_at=None, now=NOW) is EvidenceLevel.OBSERVED

    def test_ten_samples_without_outcome_is_benchmarked(self) -> None:
        assert derive_evidence_level(10, has_outcome=False, last_evaluated_at=None, now=NOW) is EvidenceLevel.BENCHMARKED

    def test_outcome_beats_the_benchmark_threshold(self) -> None:
        assert derive_evidence_level(10, has_outcome=True, last_evaluated_at=_iso(NOW), now=NOW) is EvidenceLevel.RECENTLY_REVALIDATED

    def test_recent_outcome_is_recently_revalidated(self) -> None:
        level = derive_evidence_level(3, has_outcome=True, last_evaluated_at=_iso(NOW - timedelta(days=1)), now=NOW)
        assert level is EvidenceLevel.RECENTLY_REVALIDATED

    def test_timestamp_exactly_at_the_window_edge_is_within(self) -> None:
        level = derive_evidence_level(3, has_outcome=True, last_evaluated_at=_iso(NOW - timedelta(days=30)), now=NOW)
        assert level is EvidenceLevel.RECENTLY_REVALIDATED

    def test_stale_outcome_is_outcome_verified(self) -> None:
        level = derive_evidence_level(3, has_outcome=True, last_evaluated_at=_iso(NOW - timedelta(days=30, seconds=1)), now=NOW)
        assert level is EvidenceLevel.OUTCOME_VERIFIED

    def test_outcome_without_timestamp_is_outcome_verified(self) -> None:
        level = derive_evidence_level(3, has_outcome=True, last_evaluated_at=None, now=NOW)
        assert level is EvidenceLevel.OUTCOME_VERIFIED

    def test_unparseable_timestamp_never_upgrades(self) -> None:
        """A timestamp nobody can read is stale, not recent."""
        level = derive_evidence_level(3, has_outcome=True, last_evaluated_at="not-a-timestamp", now=NOW)
        assert level is EvidenceLevel.OUTCOME_VERIFIED

    def test_unparseable_timestamp_does_not_block_the_lower_levels(self) -> None:
        level = derive_evidence_level(3, has_outcome=False, last_evaluated_at="not-a-timestamp", now=NOW)
        assert level is EvidenceLevel.OBSERVED

    def test_z_suffix_timestamp_parses(self) -> None:
        zulu = _iso(NOW - timedelta(hours=2)).replace("+00:00", "Z")
        level = derive_evidence_level(3, has_outcome=True, last_evaluated_at=zulu, now=NOW)
        assert level is EvidenceLevel.RECENTLY_REVALIDATED

    def test_naive_timestamp_is_read_as_utc(self) -> None:
        level = derive_evidence_level(3, has_outcome=True, last_evaluated_at="2026-01-15T11:00:00", now=NOW)
        assert level is EvidenceLevel.RECENTLY_REVALIDATED

    def test_future_dated_timestamp_is_not_recent(self) -> None:
        """A measurement dated in the future cannot prove recent revalidation."""
        level = derive_evidence_level(3, has_outcome=True, last_evaluated_at=_iso(NOW + timedelta(days=60)), now=NOW)
        assert level is EvidenceLevel.OUTCOME_VERIFIED

    def test_custom_window_is_honoured(self) -> None:
        level = derive_evidence_level(
            3,
            has_outcome=True,
            last_evaluated_at=_iso(NOW - timedelta(days=5)),
            now=NOW,
            revalidation_window=timedelta(days=7),
        )
        assert level is EvidenceLevel.RECENTLY_REVALIDATED

    def test_defaults_to_wall_clock_now(self) -> None:
        """Without ``now`` the rule still runs; only freshness is time-relative."""
        recent = _iso(datetime.now(UTC))
        assert derive_evidence_level(3, has_outcome=True, last_evaluated_at=recent) is EvidenceLevel.RECENTLY_REVALIDATED
        ancient = _iso(datetime(2000, 1, 1, tzinfo=UTC))
        assert derive_evidence_level(3, has_outcome=True, last_evaluated_at=ancient) is EvidenceLevel.OUTCOME_VERIFIED


def _estimate(**overrides: object) -> CapabilityEstimate:
    base: dict[str, object] = {
        "capability": "implement",
        "task_category": "coding",
        "quality_mean": 0.8,
        "uncertainty": 0.1,
        "sample_count": 4,
        "evidence_level": EvidenceLevel.OBSERVED,
        "last_evaluated_at": "2026-01-01T00:00:00+00:00",
        "profile_version": "p1",
    }
    base.update(overrides)
    return CapabilityEstimate(**base)  # type: ignore[arg-type]


class TestCapabilityEstimateRefusals:
    def test_quality_mean_without_samples_is_refused(self) -> None:
        with pytest.raises(ValueError, match="sample_count"):
            _estimate(quality_mean=0.9, sample_count=0)

    def test_uncertainty_without_samples_is_refused(self) -> None:
        with pytest.raises(ValueError, match="sample_count"):
            _estimate(uncertainty=0.2, sample_count=0)

    def test_measured_evidence_without_samples_is_refused(self) -> None:
        with pytest.raises(ValueError, match="sample_count"):
            _estimate(evidence_level=EvidenceLevel.BENCHMARKED, sample_count=0, quality_mean=None, uncertainty=None)

    def test_outcome_verified_without_profile_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="profile_version"):
            _estimate(evidence_level=EvidenceLevel.OUTCOME_VERIFIED, profile_version="")

    def test_recently_revalidated_without_profile_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="profile_version"):
            _estimate(evidence_level=EvidenceLevel.RECENTLY_REVALIDATED, profile_version="")

    def test_negative_sample_count_is_refused(self) -> None:
        with pytest.raises(ValueError, match="sample_count"):
            _estimate(sample_count=-1, quality_mean=None, uncertainty=None, evidence_level=EvidenceLevel.UNMEASURED)

    def test_negative_uncertainty_is_refused(self) -> None:
        with pytest.raises(ValueError, match="uncertainty"):
            _estimate(uncertainty=-0.1)

    def test_non_finite_quality_mean_is_refused(self) -> None:
        with pytest.raises(ValueError, match="quality_mean"):
            _estimate(quality_mean=float("inf"))

    def test_nan_quality_mean_is_refused(self) -> None:
        with pytest.raises(ValueError, match="quality_mean"):
            _estimate(quality_mean=float("nan"))

    def test_non_finite_uncertainty_is_refused(self) -> None:
        with pytest.raises(ValueError, match="uncertainty"):
            _estimate(uncertainty=float("inf"))

    def test_unknown_evidence_level_is_refused(self) -> None:
        with pytest.raises(ValueError, match="evidence_level"):
            _estimate(evidence_level="vibes")

    def test_valid_measured_estimate_constructs(self) -> None:
        estimate = _estimate()
        assert estimate.sample_count == 4
        assert estimate.evidence_level is EvidenceLevel.OBSERVED

    def test_unmeasured_estimate_needs_no_statistics(self) -> None:
        estimate = CapabilityEstimate(capability="implement", task_category="*")
        assert estimate.evidence_level is EvidenceLevel.UNMEASURED
        assert estimate.sample_count == 0
        assert estimate.quality_mean is None
        assert estimate.uncertainty is None

    def test_outcome_verified_with_profile_version_constructs(self) -> None:
        estimate = _estimate(evidence_level=EvidenceLevel.OUTCOME_VERIFIED)
        assert estimate.profile_version == "p1"

    def test_zero_uncertainty_is_allowed(self) -> None:
        estimate = _estimate(uncertainty=0.0)
        assert estimate.uncertainty == 0.0


class TestCapabilityEstimateSerialization:
    def test_roundtrip(self) -> None:
        estimate = _estimate()
        restored = CapabilityEstimate.from_dict(estimate.to_dict())
        assert restored == estimate

    def test_to_dict_is_json_safe(self) -> None:
        payload = _estimate().to_dict()
        assert json.loads(json.dumps(payload))["evidence_level"] == "observed"

    def test_from_dict_coerces_types(self) -> None:
        restored = CapabilityEstimate.from_dict(
            {
                "capability": "review",
                "task_category": "code_review",
                "quality_mean": 1,
                "sample_count": "2",
                "evidence_level": "observed",
                "profile_version": "p2",
            }
        )
        assert restored.quality_mean == 1.0
        assert restored.sample_count == 2

    def test_from_dict_revalidates(self) -> None:
        """A hand-edited file cannot smuggle a fabricated statistic past the constructor."""
        with pytest.raises(ValueError, match="sample_count"):
            CapabilityEstimate.from_dict({"capability": "x", "task_category": "*", "quality_mean": 0.9, "sample_count": 0})


def _passport(*estimates: CapabilityEstimate, **overrides: object) -> CapabilityPassport:
    base: dict[str, object] = {
        "entity_id": "agent:tester",
        "entity_type": EntityKind.AGENT,
        "version": "v1",
        "capabilities": estimates,
    }
    base.update(overrides)
    return CapabilityPassport(**base)  # type: ignore[arg-type]


class TestCapabilityPassportConstruction:
    def test_string_entity_type_is_coerced(self) -> None:
        passport = _passport(entity_type="tool")
        assert passport.entity_type is EntityKind.TOOL

    def test_unknown_entity_type_is_refused(self) -> None:
        with pytest.raises(ValueError, match="entity_type"):
            _passport(entity_type="spaceship")

    def test_sequence_inputs_are_coerced_to_tuples(self) -> None:
        passport = _passport([], capabilities=[])
        assert passport.capabilities == ()
        listed = _passport(entity_id="agent:a2", required_tools=["bash"], known_failure_modes=["flaky"])
        assert listed.required_tools == ("bash",)
        assert listed.known_failure_modes == ("flaky",)

    def test_blank_entity_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="entity_id"):
            _passport(entity_id="")

    def test_entity_kind_vocabulary(self) -> None:
        assert [kind.value for kind in EntityKind] == ["agent", "model_config", "tool", "skill_bundle"]


class TestEstimateFor:
    def test_exact_category_beats_wildcard(self) -> None:
        wildcard = CapabilityEstimate(capability="implement", task_category="*")
        coding = CapabilityEstimate(capability="implement", task_category="coding", sample_count=1, evidence_level=EvidenceLevel.OBSERVED)
        passport = _passport(wildcard, coding)
        assert passport.estimate_for("implement", "coding") is coding

    def test_wildcard_falls_back_when_category_has_no_entry(self) -> None:
        wildcard = CapabilityEstimate(capability="implement", task_category="*")
        coding = CapabilityEstimate(capability="implement", task_category="coding", sample_count=1, evidence_level=EvidenceLevel.OBSERVED)
        passport = _passport(wildcard, coding)
        assert passport.estimate_for("implement", "research") is wildcard

    def test_absent_capability_returns_none(self) -> None:
        passport = _passport(CapabilityEstimate(capability="implement", task_category="*"))
        assert passport.estimate_for("debug") is None

    def test_explicit_category_without_wildcard_or_match_returns_none(self) -> None:
        """Refusing to answer a question the passport has no entry for."""
        coding = CapabilityEstimate(capability="implement", task_category="coding", sample_count=1, evidence_level=EvidenceLevel.OBSERVED)
        passport = _passport(coding)
        assert passport.estimate_for("implement", "research") is None

    def test_no_category_prefers_wildcard_then_first_entry(self) -> None:
        wildcard = CapabilityEstimate(capability="implement", task_category="*")
        coding = CapabilityEstimate(capability="implement", task_category="coding", sample_count=1, evidence_level=EvidenceLevel.OBSERVED)
        assert _passport(wildcard, coding).estimate_for("implement") is wildcard
        assert _passport(coding, wildcard).estimate_for("implement") is wildcard

    def test_no_category_with_only_specific_entries_returns_the_first(self) -> None:
        coding = CapabilityEstimate(capability="implement", task_category="coding", sample_count=1, evidence_level=EvidenceLevel.OBSERVED)
        other = CapabilityEstimate(capability="implement", task_category="data", sample_count=2, evidence_level=EvidenceLevel.BENCHMARKED)
        assert _passport(coding, other).estimate_for("implement") is coding

    def test_empty_passport_returns_none(self) -> None:
        assert _passport().estimate_for("implement") is None


class TestOverallEvidenceLevel:
    def test_empty_passport_is_unmeasured(self) -> None:
        assert _passport().overall_evidence_level() is EvidenceLevel.UNMEASURED

    def test_empty_for_unknown_capability_is_unmeasured(self) -> None:
        passport = _passport(CapabilityEstimate(capability="implement", task_category="*"))
        assert passport.overall_evidence_level("debug") is EvidenceLevel.UNMEASURED

    def test_worst_level_wins(self) -> None:
        verified = _estimate(evidence_level=EvidenceLevel.OUTCOME_VERIFIED)
        observed = _estimate(evidence_level=EvidenceLevel.OBSERVED)
        revalidated = _estimate(evidence_level=EvidenceLevel.RECENTLY_REVALIDATED)
        passport = _passport(verified, observed, revalidated)
        assert passport.overall_evidence_level() is EvidenceLevel.OBSERVED

    def test_capability_filter_selects_only_matching_estimates(self) -> None:
        observed = _estimate(capability="implement")
        verified = _estimate(capability="review", evidence_level=EvidenceLevel.OUTCOME_VERIFIED)
        passport = _passport(observed, verified)
        assert passport.overall_evidence_level("review") is EvidenceLevel.OUTCOME_VERIFIED
        assert passport.overall_evidence_level("implement") is EvidenceLevel.OBSERVED

    def test_worst_is_unmeasured_when_any_estimate_is_unmeasured(self) -> None:
        unmeasured = CapabilityEstimate(capability="implement", task_category="*")
        verified = _estimate(evidence_level=EvidenceLevel.OUTCOME_VERIFIED)
        assert _passport(unmeasured, verified).overall_evidence_level() is EvidenceLevel.UNMEASURED

    def test_all_revalidated_aggregates_to_revalidated(self) -> None:
        first = _estimate(evidence_level=EvidenceLevel.RECENTLY_REVALIDATED)
        second = _estimate(capability="review", evidence_level=EvidenceLevel.RECENTLY_REVALIDATED)
        assert _passport(first, second).overall_evidence_level() is EvidenceLevel.RECENTLY_REVALIDATED


class TestPassportSummary:
    def test_shape(self) -> None:
        estimate = _estimate()
        passport = _passport(
            estimate,
            required_tools=["bash"],
            known_failure_modes=["hallucinates API args"],
            enabled=False,
        )
        summary = passport.summary()
        assert summary["entity_id"] == "agent:tester"
        assert summary["entity_type"] == "agent"
        assert summary["version"] == "v1"
        assert summary["enabled"] is False
        assert summary["known_failure_modes"] == ["hallucinates API args"]
        assert summary["required_tools"] == ["bash"]
        assert summary["capabilities"] == [
            {
                "capability": "implement",
                "task_category": "coding",
                "evidence_level": "observed",
                "sample_count": 4,
                "quality_mean": 0.8,
            }
        ]

    def test_json_serializable(self) -> None:
        passport = _passport(_estimate(), required_tools=["bash"], known_failure_modes=["flaky"])
        payload = json.loads(json.dumps(passport.summary()))
        assert payload["entity_type"] == "agent"

    def test_empty_passport_summary_is_compact(self) -> None:
        summary = _passport().summary()
        assert summary["capabilities"] == []
        assert json.dumps(summary)


class TestPassportSerialization:
    def test_roundtrip(self) -> None:
        passport = _passport(
            _estimate(),
            required_tools=["bash"],
            known_failure_modes=["flaky"],
            model_provider="openrouter",
            model_id="some/model",
            model_family="example",
        )
        restored = CapabilityPassport.from_dict(passport.to_dict())
        assert restored == passport

    def test_to_dict_is_json_safe(self) -> None:
        passport = _passport(_estimate())
        assert json.loads(json.dumps(passport.to_dict()))["entity_type"] == "agent"

    def test_from_dict_revalidates_estimates(self) -> None:
        payload = _passport().to_dict()
        payload["capabilities"] = [{"capability": "x", "task_category": "*", "quality_mean": 0.5, "sample_count": 0}]
        with pytest.raises(ValueError, match="sample_count"):
            CapabilityPassport.from_dict(payload)

    def test_from_dict_rejects_unknown_entity_type(self) -> None:
        with pytest.raises(ValueError, match="entity_type"):
            CapabilityPassport.from_dict({"entity_id": "x", "entity_type": "spaceship", "version": "v1"})
