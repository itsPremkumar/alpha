"""Tests for capability gap detection and the temporary-specialist PLAN (plan §6).

Gap detection is honest classification: "no passport declares this
capability", "declared but UNMEASURED" (a gap in evidence, said precisely)
and "measured quality X below floor Y" are three different findings with
three different reasons. ``plan_specialist`` produces a PLAN and never an
action: it requires a capability-specific smoke check and a promotion gate,
and nothing here creates, installs or registers anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha.capability_mesh.gaps import (
    DEFAULT_PROMOTION_GATE,
    TOOL_CAPABILITY_PREFIX,
    CapabilityGap,
    GapKind,
    TemporarySpecialistPlan,
    detect_gaps,
    plan_specialist,
)
from alpha.capability_mesh.memory import CapabilityMemory, CapabilityRecord, OutcomeKind
from alpha.capability_mesh.models import CapabilityEstimate, CapabilityPassport, EntityKind, EvidenceLevel

PV = "p1"

_ids = iter(range(1, 1_000_000))


def _estimate(capability: str, *, quality: float | None = None, samples: int = 0, level: EvidenceLevel = EvidenceLevel.UNMEASURED) -> CapabilityEstimate:
    return CapabilityEstimate(capability=capability, task_category="*", quality_mean=quality, sample_count=samples, evidence_level=level, profile_version=PV if samples else "")


def _passport(entity_id: str = "agent:a", *, capabilities: tuple[CapabilityEstimate, ...] = (), required_tools: tuple[str, ...] = (), enabled: bool = True) -> CapabilityPassport:
    # Ensure capabilities is a tuple
    caps = tuple(capabilities) if capabilities else ()
    return CapabilityPassport(entity_id=entity_id, entity_type=EntityKind.AGENT, version="v1", capabilities=caps, required_tools=required_tools, enabled=enabled)


def _gap(capability: str = "code", kind: GapKind = GapKind.MODEL_GAP) -> CapabilityGap:
    return CapabilityGap(capability=capability, task_category="*", kind=kind, best_estimate=None, best_entity=None, reason="no passport declares this capability")


# ------------------------------------------------------------ CapabilityGap


def test_gap_refuses_an_empty_reason():
    with pytest.raises(ValueError, match="reason must be non-empty"):
        CapabilityGap(capability="code", task_category="*", kind=GapKind.MODEL_GAP, best_estimate=None, best_entity=None, reason="")


def test_gap_refuses_an_empty_capability():
    with pytest.raises(ValueError, match="capability must be non-empty"):
        CapabilityGap(capability="", task_category="*", kind=GapKind.MODEL_GAP, best_estimate=None, best_entity=None, reason="x")


# -------------------------------------------------------------- detect_gaps


def test_detect_gaps_requires_a_profile_version():
    with pytest.raises(ValueError, match="profile_version"):
        detect_gaps(("code",), [], profile_version="")


def test_undeclared_capability_is_a_model_gap():
    gaps = detect_gaps(("code",), [_passport("agent:a", capabilities=(_estimate("docs", quality=0.9, samples=5, level=EvidenceLevel.OBSERVED),))], profile_version=PV)
    assert len(gaps) == 1
    gap = gaps[0]
    assert (gap.capability, gap.kind, gap.best_estimate, gap.best_entity) == ("code", GapKind.MODEL_GAP, None, None)
    assert gap.reason == "no passport declares this capability"


def test_tool_shaped_capability_without_the_tool_is_a_tool_gap():
    passports = [_passport("agent:a", capabilities=(_estimate(f"{TOOL_CAPABILITY_PREFIX}shell"),))]
    gaps = detect_gaps((f"{TOOL_CAPABILITY_PREFIX}shell",), passports, profile_version=PV)
    assert gaps[0].kind is GapKind.TOOL_GAP


def test_tool_shaped_capability_with_a_tool_requirement_is_a_model_gap():
    # The tool exists in the fleet; what is missing is measured capability evidence.
    passports = [
        _passport("agent:a", capabilities=(_estimate(f"{TOOL_CAPABILITY_PREFIX}shell"),)),
        _passport("agent:b", required_tools=("shell",)),
    ]
    gaps = detect_gaps((f"{TOOL_CAPABILITY_PREFIX}shell",), passports, profile_version=PV)
    assert gaps[0].kind is GapKind.MODEL_GAP


def test_unmeasured_declaration_is_an_evidence_gap_with_a_precise_reason():
    passports = [_passport("agent:a", capabilities=(_estimate("code"),))]
    gaps = detect_gaps(("code",), passports, profile_version=PV)
    assert len(gaps) == 1
    assert gaps[0].reason.startswith("declared but UNMEASURED")
    assert gaps[0].best_entity == "agent:a"


def test_measured_quality_below_the_floor_names_both_numbers():
    passports = [_passport("agent:a", capabilities=(_estimate("code", quality=0.31, samples=5, level=EvidenceLevel.OBSERVED),))]
    gaps = detect_gaps(("code",), passports, profile_version=PV)
    assert len(gaps) == 1
    assert "measured quality 0.31 below floor 0.5" in gaps[0].reason
    assert gaps[0].best_entity == "agent:a"
    assert gaps[0].best_estimate.quality_mean == pytest.approx(0.31)


def test_measured_quality_above_the_floor_is_no_gap():
    passports = [_passport("agent:a", capabilities=(_estimate("code", quality=0.9, samples=5, level=EvidenceLevel.OBSERVED),))]
    assert detect_gaps(("code",), passports, profile_version=PV) == ()


def test_disabled_passports_do_not_cover_a_capability():
    est = _estimate("code", quality=0.9, samples=5, level=EvidenceLevel.OBSERVED)
    passports = [_passport("agent:a", capabilities=(est,), enabled=False)]
    gaps = detect_gaps(("code",), passports, profile_version=PV)
    assert gaps[0].reason == "no passport declares this capability"


def test_strong_memory_evidence_closes_the_gap(tmp_path: Path):
    passports = [_passport("agent:a", capabilities=(_estimate("code"),))]  # declared but unmeasured on the passport
    memory = CapabilityMemory(tmp_path)
    for index in range(4):
        memory.record(
            CapabilityRecord(
                record_id=f"r{next(_ids)}",
                task_fingerprint="fp_0011223344556677",
                task_category="*",
                entity_id="agent:a",
                capability="code",
                outcome=OutcomeKind.SELECTED,
                quality=0.95,
                profile_version=PV,
                recorded_at=f"2025-06-0{index + 1}T00:00:00+00:00",
            )
        )
    assert detect_gaps(("code",), passports, memory=memory, profile_version=PV) == ()


def test_caller_override_produces_the_reserved_gap_kinds():
    passports = [_passport("agent:a", capabilities=(_estimate("code"),))]
    gaps = detect_gaps(("code",), passports, profile_version=PV, kind_overrides={"code": GapKind.CONTEXT_GAP})
    assert gaps[0].kind is GapKind.CONTEXT_GAP
    assert gaps[0].reason.startswith("declared but UNMEASURED")  # the override changes classification, never the finding


def test_detect_gaps_returns_gaps_in_requirement_order():
    passports: list[CapabilityPassport] = []
    gaps = detect_gaps(("zeta", "alpha"), passports, profile_version=PV)
    assert [gap.capability for gap in gaps] == ["zeta", "alpha"]


# ------------------------------------------------------ TemporarySpecialistPlan


def test_specialist_plan_refuses_without_a_smoke_check():
    with pytest.raises(ValueError, match="smoke_check"):
        TemporarySpecialistPlan(capability="code", gap=GapKind.MODEL_GAP, base_entity=None, add_tools=(), add_skills=(), smoke_check="", promotion_gate=DEFAULT_PROMOTION_GATE)


def test_specialist_plan_refuses_without_a_promotion_gate():
    with pytest.raises(ValueError, match="promotion_gate"):
        TemporarySpecialistPlan(capability="code", gap=GapKind.MODEL_GAP, base_entity=None, add_tools=(), add_skills=(), smoke_check="run the checker", promotion_gate="")


def test_plan_specialist_is_a_plan_with_honest_defaults():
    gap = _gap(f"{TOOL_CAPABILITY_PREFIX}shell", GapKind.TOOL_GAP)
    plan = plan_specialist(gap, [], smoke_check="shell out to a scratch dir and diff the tree")
    assert plan.capability == f"{TOOL_CAPABILITY_PREFIX}shell"
    assert plan.gap is GapKind.TOOL_GAP
    assert plan.base_entity is None
    assert plan.add_tools == ("shell",)
    assert plan.add_skills == ()  # no skill is ever invented; there is no data source for one
    assert plan.retire_after_run is True
    assert plan.promotion_gate == DEFAULT_PROMOTION_GATE


def test_plan_specialist_picks_the_closest_base_entity():
    passports = [
        _passport("agent:a", capabilities=(_estimate("code", quality=0.31, samples=5, level=EvidenceLevel.OBSERVED),)),
        _passport("agent:b", capabilities=(_estimate("code", quality=0.45, samples=5, level=EvidenceLevel.OBSERVED),)),
        _passport("agent:c", capabilities=(_estimate("docs", quality=0.9, samples=5, level=EvidenceLevel.OBSERVED),)),
    ]
    plan = plan_specialist(_gap("code"), passports, smoke_check="the check")
    assert plan.base_entity == "agent:b"  # the closest existing evidence to build on


def test_plan_specialist_accepts_a_custom_promotion_gate():
    plan = plan_specialist(_gap("code"), [], smoke_check="the check", promotion_gate="a sandbox benchmark on the migration corpus")
    assert plan.promotion_gate == "a sandbox benchmark on the migration corpus"
    assert plan.retire_after_run is True


def test_specialist_plan_round_trips():
    plan = plan_specialist(_gap("code"), [], smoke_check="the check")
    assert TemporarySpecialistPlan.from_dict(plan.to_dict()) == plan
