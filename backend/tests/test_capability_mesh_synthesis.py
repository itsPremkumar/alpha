"""Tests for capability_mesh.synthesis — composition compiler (Feature F + U)."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from alpha.capability_mesh.assignment import MicrotaskAssignment, Role, RoutingPlan
from alpha.capability_mesh.fingerprint import Pressure, Risk, SituationFingerprint
from alpha.capability_mesh.models import (
    CapabilityEstimate,
    CapabilityPassport,
    EntityKind,
    EvidenceLevel,
)
from alpha.capability_mesh.synthesis import (
    ArtifactRecord,
    ArtifactStore,
    CompatibilityStatus,
    CompositionResult,
    ContributionSlot,
    SlotStatus,
    _contradicts,
    build_slots,
    compose_deliverable,
    detect_conflicts,
    extract_assumptions,
    resolve_slots,
)


def _passport(entity_id: str, caps: tuple[str, ...] = ()) -> CapabilityPassport:
    return CapabilityPassport(
        entity_id=entity_id,
        entity_type=EntityKind.AGENT,
        version="1",
        capabilities=tuple(CapabilityEstimate(capability=c, task_category="*", evidence_level=EvidenceLevel.OBSERVED, sample_count=5, quality_mean=0.8, profile_version="v1") for c in caps),
    )


def _artifact(
    artifact_id: str,
    entity_id: str,
    microtask_id: str,
    content: str,
    *,
    evidence_level: EvidenceLevel = EvidenceLevel.OBSERVED,
    quality: float = 0.8,
    passed_hard_gate: bool | None = True,
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        entity_id=entity_id,
        microtask_id=microtask_id,
        content=content,
        mime_type="text/plain",
        capability_coverage=("test",),
        evidence_level=evidence_level,
        quality=quality,
        passed_hard_gate=passed_hard_gate,
        recorded_at=datetime.now(UTC).isoformat(),
    )


def _store(tmp: Path) -> ArtifactStore:
    return ArtifactStore(tmp)


class TestExtractAssumptions:
    def test_no_assumptions_unmeasured_returns_honest_placeholder(self) -> None:
        art = _artifact("a1", "e1", "mt1", "no assumptions here", evidence_level=EvidenceLevel.UNMEASURED)
        assumptions = extract_assumptions(art, "test")
        assert assumptions == ("[assumptions unknown — unmeasured capability]",)

    def test_no_assumptions_measured_returns_empty(self) -> None:
        art = _artifact("a1", "e1", "mt1", "no assumptions here", evidence_level=EvidenceLevel.OBSERVED)
        assumptions = extract_assumptions(art, "test")
        assert assumptions == ()

    def test_extracts_assumption_patterns(self) -> None:
        art = _artifact(
            "a1",
            "e1",
            "mt1",
            "We assume the test input is valid JSON and given the schema is fixed.",
            evidence_level=EvidenceLevel.OBSERVED,
        )
        assumptions = extract_assumptions(art, "test")
        assert len(assumptions) >= 1
        # Should contain the assumption-like phrases
        joined = " ".join(assumptions)
        assert "assume" in joined.lower() or "given" in joined.lower()


class TestContradicts:
    def test_not_x_vs_x(self) -> None:
        assert _contradicts("not valid", "valid") is True
        assert _contradicts("valid", "not valid") is True

    def test_requires_vs_excludes(self) -> None:
        assert _contradicts("requires python", "excludes python") is True
        assert _contradicts("excludes python", "requires python") is True

    def test_different_tool_use(self) -> None:
        assert _contradicts("use:foo", "use:bar") is True

    def test_no_contradiction(self) -> None:
        assert _contradicts("requires python", "requires tests") is False
        assert _contradicts("assume valid", "given schema") is False


class TestBuildSlots:
    def test_builds_one_slot_per_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "content"))
            store.put(_artifact("a2", "e2", "mt2", "content"))

            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(
                    MicrotaskAssignment(
                        microtask_id="mt1",
                        passport_id="e1",
                        role=Role.PRIMARY,
                        fit_score=0.9,
                        score_explanation=(),
                        expected_outputs=("cap1",),
                        acceptance_checks=("check1",),
                    ),
                    MicrotaskAssignment(
                        microtask_id="mt2",
                        passport_id="e2",
                        role=Role.PRIMARY,
                        fit_score=0.7,
                        score_explanation=(),
                        expected_outputs=("cap2",),
                        acceptance_checks=("check2",),
                    ),
                ),
            )
            slots = build_slots(plan, store, profile_version="v1")
            assert len(slots) == 2
            assert slots[0].slot_id == "mt1"
            assert slots[1].slot_id == "mt2"

    def test_verification_status_passed_when_evidence_sufficient(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "content", quality=0.8))

            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(
                    MicrotaskAssignment(
                        microtask_id="mt1",
                        passport_id="e1",
                        role=Role.PRIMARY,
                        fit_score=0.9,
                        score_explanation=(),
                        expected_outputs=("cap1",),
                        acceptance_checks=("check1",),
                    ),
                ),
            )
            slots = build_slots(plan, store, profile_version="v1", evidence_floor=EvidenceLevel.OBSERVED)
            assert slots[0].verification_status == SlotStatus.PASSED

    def test_verification_status_failed_when_quality_below_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "content", quality=0.3))

            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(
                    MicrotaskAssignment(
                        microtask_id="mt1",
                        passport_id="e1",
                        role=Role.PRIMARY,
                        fit_score=0.9,
                        score_explanation=(),
                        expected_outputs=("cap1",),
                        acceptance_checks=("check1",),
                    ),
                ),
            )
            slots = build_slots(plan, store, profile_version="v1", evidence_floor=EvidenceLevel.OBSERVED)
            assert slots[0].verification_status == SlotStatus.FAILED

    def test_verification_status_not_applicable_when_no_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            # No artifacts put

            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(
                    MicrotaskAssignment(
                        microtask_id="mt1",
                        passport_id="e1",
                        role=Role.PRIMARY,
                        fit_score=0.9,
                        score_explanation=(),
                        expected_outputs=("cap1",),
                        acceptance_checks=("check1",),
                    ),
                ),
            )
            slots = build_slots(plan, store, profile_version="v1")
            assert slots[0].verification_status == SlotStatus.NOT_APPLICABLE


class TestDetectConflicts:
    def test_conflict_on_contradictory_assumptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "use:foo depends on cap1", evidence_level=EvidenceLevel.OBSERVED))
            store.put(_artifact("a2", "e2", "mt2", "use:bar depends on cap2", evidence_level=EvidenceLevel.OBSERVED))

            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(
                    MicrotaskAssignment("mt1", "e1", Role.PRIMARY, 0.9, (), ("cap1",), ("check1",)),
                    MicrotaskAssignment("mt2", "e2", Role.PRIMARY, 0.9, (), ("cap2",), ("check2",)),
                ),
            )
            slots = build_slots(plan, store, profile_version="v1")
            slots = detect_conflicts(slots)
            # Both should be CONFLICT
            assert slots[0].compatibility_status == CompatibilityStatus.CONFLICT
            assert slots[1].compatibility_status == CompatibilityStatus.CONFLICT
            assert slots[0].conflict_summary is not None

    def test_alternative_on_divergent_assumptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "assume python for cap1", evidence_level=EvidenceLevel.OBSERVED))
            store.put(_artifact("a2", "e2", "mt2", "assume javascript for cap2", evidence_level=EvidenceLevel.OBSERVED))

            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(
                    MicrotaskAssignment("mt1", "e1", Role.PRIMARY, 0.9, (), ("cap1",), ("check1",)),
                    MicrotaskAssignment("mt2", "e2", Role.PRIMARY, 0.9, (), ("cap2",), ("check2",)),
                ),
            )
            slots = build_slots(plan, store, profile_version="v1")
            slots = detect_conflicts(slots)
            # Should be ALTERNATIVE (divergent but not contradictory)
            assert slots[0].compatibility_status == CompatibilityStatus.ALTERNATIVE
            assert slots[1].compatibility_status == CompatibilityStatus.ALTERNATIVE

    def test_compatible_when_no_assumptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "content", evidence_level=EvidenceLevel.OBSERVED))
            store.put(_artifact("a2", "e2", "mt2", "content", evidence_level=EvidenceLevel.OBSERVED))

            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(
                    MicrotaskAssignment("mt1", "e1", Role.PRIMARY, 0.9, (), ("cap1",), ("check1",)),
                    MicrotaskAssignment("mt2", "e2", Role.PRIMARY, 0.9, (), ("cap2",), ("check2",)),
                ),
            )
            slots = build_slots(plan, store, profile_version="v1")
            slots = detect_conflicts(slots)
            assert slots[0].compatibility_status == CompatibilityStatus.COMPATIBLE
            assert slots[1].compatibility_status == CompatibilityStatus.COMPATIBLE


class TestResolveSlots:
    def test_resolves_to_best_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "content A", quality=0.9))
            store.put(_artifact("a2", "e1", "mt1", "content B", quality=0.6))

            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(MicrotaskAssignment("mt1", "e1", Role.PRIMARY, 0.9, (), ("cap1",), ("check1",)),),
            )
            slots = build_slots(plan, store, profile_version="v1")
            slots = detect_conflicts(slots)
            result = resolve_slots(slots, artifacts=store, profile_version="v1")
            assert result.slots[0].selected_artifact_id == "a1"
            assert result.composed_artifact is not None
            assert "content A" in result.composed_artifact

    def test_composed_artifact_none_when_conflict_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "use:foo depends on cap1", evidence_level=EvidenceLevel.OBSERVED))
            store.put(_artifact("a2", "e2", "mt2", "use:bar depends on cap2", evidence_level=EvidenceLevel.OBSERVED))

            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(
                    MicrotaskAssignment("mt1", "e1", Role.PRIMARY, 0.9, (), ("cap1",), ("check1",)),
                    MicrotaskAssignment("mt2", "e2", Role.PRIMARY, 0.9, (), ("cap2",), ("check2",)),
                ),
            )
            slots = build_slots(plan, store, profile_version="v1")
            slots = detect_conflicts(slots)
            result = resolve_slots(slots, artifacts=store, profile_version="v1")
            assert result.composed_artifact is None
            assert len(result.alternatives) >= 1
            assert len(result.conflicts) >= 1

    def test_alternatives_contain_at_least_one_per_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "use:foo depends on cap1", evidence_level=EvidenceLevel.OBSERVED))
            store.put(_artifact("a2", "e2", "mt2", "use:bar depends on cap2", evidence_level=EvidenceLevel.OBSERVED))

            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(
                    MicrotaskAssignment("mt1", "e1", Role.PRIMARY, 0.9, (), ("cap1",), ("check1",)),
                    MicrotaskAssignment("mt2", "e2", Role.PRIMARY, 0.9, (), ("cap2",), ("check2",)),
                ),
            )
            slots = build_slots(plan, store, profile_version="v1")
            slots = detect_conflicts(slots)
            result = resolve_slots(slots, artifacts=store, profile_version="v1")
            # One conflict -> at least one alternative entry
            assert len(result.alternatives) >= 1
            assert any("Conflict" in alt or "Option" in alt for alt in result.alternatives)


class TestComposeDeliverable:
    def test_one_call_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "content A", quality=0.9))
            store.put(_artifact("a2", "e2", "mt2", "content B", quality=0.8))

            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(
                    MicrotaskAssignment("mt1", "e1", Role.PRIMARY, 0.9, (), ("cap1",), ("check1",)),
                    MicrotaskAssignment("mt2", "e2", Role.PRIMARY, 0.8, (), ("cap2",), ("check2",)),
                ),
            )
            result = compose_deliverable(plan, store, profile_version="v1")
            assert isinstance(result, CompositionResult)
            assert result.composed_artifact is not None
            assert "content A" in result.composed_artifact
            assert "content B" in result.composed_artifact

    def test_profile_version_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(MicrotaskAssignment("mt1", "e1", Role.PRIMARY, 0.9, (), ("cap1",), ("check1",)),),
            )
            try:
                compose_deliverable(plan, store, profile_version="")
                assert False, "Expected ValueError"
            except ValueError as e:
                assert "profile_version" in str(e)


class TestArtifactStore:
    def test_put_get_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            art = _artifact("a1", "e1", "mt1", "content")
            store.put(art)
            retrieved = store.get("a1")
            assert retrieved is not None
            assert retrieved.artifact_id == "a1"
            assert retrieved.content == "content"

    def test_for_slot_filters_by_microtask_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "content"))
            store.put(_artifact("a2", "e1", "mt2", "other"))
            arts = store.for_slot("mt1")
            assert len(arts) == 1
            assert arts[0].artifact_id == "a1"

    def test_for_entity_filters_by_entity_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "content"))
            store.put(_artifact("a2", "e2", "mt1", "content"))
            arts = store.for_entity("e1")
            assert len(arts) == 1
            assert arts[0].entity_id == "e1"

    def test_corrupt_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ArtifactStore.FILE_NAME
            path.write_text("not json")
            store = _store(Path(tmp))
            try:
                store.get("a1")
                assert False, "Expected ArtifactStoreError"
            except Exception as e:
                assert "unreadable" in str(e)


class TestCompositionResultHonesty:
    def test_refuses_composed_artifact_when_conflict_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "content", quality=0.9))

            # Build a result with a conflict but a composed artifact -> should fail
            slot = ContributionSlot(
                slot_id="mt1",
                requirement_id="mt1",
                capability_target=("cap1",),
                verification_status=SlotStatus.PASSED,
                compatibility_status=CompatibilityStatus.CONFLICT,
                conflict_summary="test conflict",
                candidate_artifacts=("a1",),
            )
            try:
                CompositionResult(
                    slots=(slot,),
                    composed_artifact="should fail",
                    alternatives=("alt",),
                    conflicts=("conflict",),
                    provenance_map={},
                    unresolved_capabilities=(),
                    profile_version="v1",
                )
                assert False, "Expected ValueError"
            except ValueError as e:
                assert "composed_artifact must be None" in str(e)

    def test_refuses_missing_alternatives_when_conflict(self) -> None:
        slot = ContributionSlot(
            slot_id="mt1",
            requirement_id="mt1",
            capability_target=("cap1",),
            verification_status=SlotStatus.PASSED,
            compatibility_status=CompatibilityStatus.CONFLICT,
            conflict_summary="test conflict",
            candidate_artifacts=("a1",),
        )
        try:
            CompositionResult(
                slots=(slot,),
                composed_artifact=None,
                alternatives=(),  # empty!
                conflicts=("conflict",),
                provenance_map={},
                unresolved_capabilities=(),
                profile_version="v1",
            )
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "alternatives must contain at least one entry" in str(e)


class TestProvenanceMap:
    def test_provenance_map_built_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(Path(tmp))
            store.put(_artifact("a1", "e1", "mt1", "content"))
            store.put(_artifact("a2", "e2", "mt2", "content"))

            fp = SituationFingerprint(task_type="test", risk_band=Risk.LOW, context_pressure=Pressure.LOW)
            plan = RoutingPlan(
                fingerprint=fp,
                assignments=(
                    MicrotaskAssignment("mt1", "e1", Role.PRIMARY, 0.9, (), ("cap1",), ("check1",)),
                    MicrotaskAssignment("mt2", "e2", Role.PRIMARY, 0.9, (), ("cap2",), ("check2",)),
                ),
            )
            result = compose_deliverable(plan, store, profile_version="v1")
            assert result.provenance_map.get("a1") == "e1"
            assert result.provenance_map.get("a2") == "e2"
