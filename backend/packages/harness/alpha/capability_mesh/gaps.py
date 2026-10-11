"""Capability gap detection and temporary-specialist planning (plan §6).

Gap detection is honest classification: "no passport declares this
capability", "declared but UNMEASURED" (a gap in evidence, said precisely)
and "measured quality X below floor Y" are three different findings with
three different reasons. ``plan_specialist`` produces a PLAN and never an
action: it requires a capability-specific smoke check and a promotion gate,
and nothing here creates, installs or registers anything.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from alpha.capability_mesh.memory import CapabilityMemory
from alpha.capability_mesh.models import CapabilityEstimate, CapabilityPassport, EvidenceLevel

#: The quality floor below which a measured capability is considered a gap.
DEFAULT_QUALITY_FLOOR = 0.5

#: The promotion gate used when none is provided by the caller.
DEFAULT_PROMOTION_GATE = "a sandbox benchmark on the target corpus"

#: Prefix for tool-shaped capabilities (e.g. "tool:shell").
TOOL_CAPABILITY_PREFIX = "tool:"


class GapKind(StrEnum):
    """What kind of capability gap was detected (plan §6.1)."""

    MODEL_GAP = "model_gap"
    TOOL_GAP = "tool_gap"
    EVIDENCE_GAP = "evidence_gap"
    QUALITY_GAP = "quality_gap"
    CONTEXT_GAP = "context_gap"
    WORKFLOW_GAP = "workflow_gap"


@dataclass(frozen=True)
class CapabilityGap:
    """One detected capability gap (plan §6.1).

    ``best_estimate`` and ``best_entity`` are the closest existing evidence
    (may be None for a pure MODEL_GAP). ``reason`` is a precise, human-
    readable explanation that names numbers when applicable.
    """

    capability: str
    task_category: str
    kind: GapKind
    best_estimate: CapabilityEstimate | None
    best_entity: str | None
    reason: str

    def __post_init__(self) -> None:
        if not self.capability:
            raise ValueError("capability must be non-empty")
        if not self.task_category:
            raise ValueError("task_category must be non-empty")
        if not self.reason:
            raise ValueError("reason must be non-empty")
        if self.kind not in GapKind:
            allowed = ", ".join(k.value for k in GapKind)
            raise ValueError(f"unknown kind {self.kind!r}; expected one of: {allowed}")
        if self.best_estimate is not None and not isinstance(self.best_estimate, CapabilityEstimate):
            raise ValueError(f"best_estimate must be a CapabilityEstimate or None, got {self.best_estimate!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "task_category": self.task_category,
            "kind": self.kind.value,
            "best_estimate": self.best_estimate.to_dict() if self.best_estimate is not None else None,
            "best_entity": self.best_entity,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityGap:
        return cls(
            capability=str(data["capability"]),
            task_category=str(data["task_category"]),
            kind=GapKind(data["kind"]),
            best_estimate=CapabilityEstimate.from_dict(data["best_estimate"]) if data.get("best_estimate") else None,
            best_entity=data.get("best_entity"),
            reason=str(data["reason"]),
        )


@dataclass(frozen=True)
class TemporarySpecialistPlan:
    """A plan for a temporary specialist (plan §6.1/§6.2).

    This is a PLAN ONLY — it does not create, install, or register anything.
    It requires a capability-specific smoke check and a promotion gate.
    """

    capability: str
    gap: GapKind
    base_entity: str | None
    add_tools: tuple[str, ...]
    add_skills: tuple[str, ...]
    smoke_check: str
    promotion_gate: str
    retire_after_run: bool = True

    def __post_init__(self) -> None:
        if not self.capability:
            raise ValueError("capability must be non-empty")
        if not self.smoke_check:
            raise ValueError("smoke_check must be non-empty: a plan without a smoke check is not actionable")
        if not self.promotion_gate:
            raise ValueError("promotion_gate must be non-empty: a plan without a promotion gate has no path to production")
        if self.gap not in GapKind:
            allowed = ", ".join(k.value for k in GapKind)
            raise ValueError(f"unknown gap kind {self.gap!r}; expected one of: {allowed}")
        object.__setattr__(self, "add_tools", tuple(self.add_tools))
        object.__setattr__(self, "add_skills", tuple(self.add_skills))
        for tool in self.add_tools:
            if not tool:
                raise ValueError("add_tools entries must be non-empty strings")
        for skill in self.add_skills:
            if not skill:
                raise ValueError("add_skills entries must be non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "gap": self.gap.value,
            "base_entity": self.base_entity,
            "add_tools": list(self.add_tools),
            "add_skills": list(self.add_skills),
            "smoke_check": self.smoke_check,
            "promotion_gate": self.promotion_gate,
            "retire_after_run": self.retire_after_run,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemporarySpecialistPlan:
        return cls(
            capability=str(data["capability"]),
            gap=GapKind(data["gap"]),
            base_entity=data.get("base_entity"),
            add_tools=tuple(str(t) for t in data.get("add_tools", ())),
            add_skills=tuple(str(s) for s in data.get("add_skills", ())),
            smoke_check=str(data["smoke_check"]),
            promotion_gate=str(data["promotion_gate"]),
            retire_after_run=bool(data.get("retire_after_run", True)),
        )


def _has_strong_memory_evidence(
    memory: CapabilityMemory,
    entity_id: str,
    capability: str,
    profile_version: str,
    quality_floor: float,
) -> bool:
    """Check if memory has strong evidence (SELECTED/REPAIRED with quality >= floor)."""
    from alpha.capability_mesh.memory import OutcomeKind

    for record in memory.records(entity_id=entity_id):
        if record.capability != capability:
            continue
        if record.profile_version not in ("", profile_version):
            continue
        if record.outcome in (OutcomeKind.SELECTED, OutcomeKind.REPAIRED):
            if record.quality is not None and record.quality >= quality_floor:
                return True
    return False


def detect_gaps(
    required_capabilities: Iterable[str],
    passports: Iterable[CapabilityPassport],
    *,
    memory: CapabilityMemory | None = None,
    profile_version: str,
    quality_floor: float = DEFAULT_QUALITY_FLOOR,
    kind_overrides: dict[str, GapKind] | None = None,
) -> tuple[CapabilityGap, ...]:
    """Detect capability gaps for the required capabilities (plan §6.1).

    Returns a tuple of gaps in the same order as the required capabilities.
    A capability with no gap is omitted from the result.

    Rules:
    - Undeclared capability -> MODEL_GAP
    - Tool-shaped capability (prefixed with "tool:") without a passport
      declaring the tool -> TOOL_GAP; with a tool requirement -> MODEL_GAP
    - Declared but UNMEASURED -> EVIDENCE_GAP with precise reason
    - Measured quality below floor -> QUALITY_GAP naming both numbers
    - Measured quality at or above floor -> no gap
    - Disabled passports do not cover a capability
    - Strong memory evidence (SELECTED/REPAIRED records with quality >= floor)
      can close an EVIDENCE_GAP
    - ``kind_overrides`` lets the caller force a classification; the finding
      (best_estimate, best_entity, reason) remains honest
    """
    if not profile_version:
        raise ValueError("detect_gaps requires a non-empty profile_version (no score without its profile version)")

    required = tuple(dict.fromkeys(cap for cap in required_capabilities if cap))
    if not required:
        return ()

    roster = list(passports)
    for passport in roster:
        if not isinstance(passport, CapabilityPassport):
            raise ValueError(f"passports must contain CapabilityPassport rows, got {passport!r}")

    if memory is not None and not isinstance(memory, CapabilityMemory):
        raise ValueError(f"memory must be a CapabilityMemory or None, got {memory!r}")

    if isinstance(quality_floor, bool) or not isinstance(quality_floor, (int, float)) or not 0.0 <= float(quality_floor) <= 1.0:
        raise ValueError(f"quality_floor must be a finite number in [0, 1], got {quality_floor!r}")

    kind_overrides = kind_overrides or {}

    gaps: list[CapabilityGap] = []

    for capability in required:
        override_kind = kind_overrides.get(capability)

        # Check if this is a tool-shaped capability
        is_tool_capability = capability.startswith(TOOL_CAPABILITY_PREFIX)
        tool_name = capability.replace(TOOL_CAPABILITY_PREFIX, "") if is_tool_capability else None

        # Check if any enabled passport requires this tool
        tool_required_by_fleet = any(passport.enabled and tool_name in passport.required_tools for passport in roster) if is_tool_capability else False

        # For tool-shaped capabilities, the "declaration" is based on required_tools,
        # not on capability estimates. A capability estimate for "tool:shell" without
        # any passport requiring "shell" means the tool itself is not in the fleet.
        if is_tool_capability:
            if not tool_required_by_fleet:
                # No passport requires this tool -> TOOL_GAP (tool not in fleet)
                base_reason = f"no passport declares this capability and no tool requirement exists for {tool_name}"
                if override_kind:
                    gaps.append(
                        CapabilityGap(
                            capability=capability,
                            task_category="*",
                            kind=override_kind,
                            best_estimate=None,
                            best_entity=None,
                            reason=base_reason,
                        )
                    )
                else:
                    gaps.append(
                        CapabilityGap(
                            capability=capability,
                            task_category="*",
                            kind=GapKind.TOOL_GAP,
                            best_estimate=None,
                            best_entity=None,
                            reason=base_reason,
                        )
                    )
                continue
            # Tool is required by fleet -> MODEL_GAP (need measured capability evidence)
            # Fall through to find best passport with this capability
        # Find the best enabled passport for this capability
        best_passport: CapabilityPassport | None = None
        best_estimate: CapabilityEstimate | None = None
        best_quality = -1.0

        for passport in roster:
            if not passport.enabled:
                continue
            estimate = passport.estimate_for(capability)
            if estimate is None:
                continue
            # Prefer measured estimates
            if estimate.quality_mean is not None and estimate.quality_mean > best_quality:
                best_quality = estimate.quality_mean
                best_estimate = estimate
                best_passport = passport
            elif estimate.quality_mean is None and best_estimate is None:
                best_estimate = estimate
                best_passport = passport

        if override_kind:
            # Caller forces the classification; finding remains honest
            base_reason = _make_base_reason(capability, best_estimate, best_passport, is_tool_capability, tool_required_by_fleet, quality_floor)
            gaps.append(
                CapabilityGap(
                    capability=capability,
                    task_category="*",
                    kind=override_kind,
                    best_estimate=best_estimate,
                    best_entity=best_passport.entity_id if best_passport else None,
                    reason=base_reason,
                )
            )
            continue

        if best_passport is None:
            # No passport declares this capability
            if is_tool_capability and not tool_required_by_fleet:
                gaps.append(
                    CapabilityGap(
                        capability=capability,
                        task_category="*",
                        kind=GapKind.TOOL_GAP,
                        best_estimate=None,
                        best_entity=None,
                        reason=f"no passport declares this capability and no tool requirement exists for {tool_name}",
                    )
                )
            else:
                gaps.append(
                    CapabilityGap(
                        capability=capability,
                        task_category="*",
                        kind=GapKind.MODEL_GAP,
                        best_estimate=None,
                        best_entity=None,
                        reason="no passport declares this capability",
                    )
                )
            continue

        # For tool-shaped capabilities where the tool is required by the fleet,
        # an UNMEASURED estimate means we lack a model that can use the tool -> MODEL_GAP
        if is_tool_capability and tool_required_by_fleet and best_estimate is not None and best_estimate.evidence_level == EvidenceLevel.UNMEASURED:
            # Check memory first
            if memory is not None:
                strong_evidence = _has_strong_memory_evidence(memory, best_passport.entity_id, capability, profile_version, quality_floor)
                if strong_evidence:
                    continue  # Memory evidence closes the gap
            gaps.append(
                CapabilityGap(
                    capability=capability,
                    task_category="*",
                    kind=GapKind.MODEL_GAP,
                    best_estimate=best_estimate,
                    best_entity=best_passport.entity_id,
                    reason=f"tool '{tool_name}' is required by the fleet but capability is UNMEASURED (no measured evidence for using this tool)",
                )
            )
            continue

        # Check memory for strong evidence that could close the gap
        if memory is not None and best_estimate is not None and best_estimate.evidence_level == EvidenceLevel.UNMEASURED:
            strong_evidence = _has_strong_memory_evidence(memory, best_passport.entity_id, capability, profile_version, quality_floor)
            if strong_evidence:
                continue  # Memory evidence closes the gap

        if best_estimate.evidence_level == EvidenceLevel.UNMEASURED:
            gaps.append(
                CapabilityGap(
                    capability=capability,
                    task_category="*",
                    kind=GapKind.EVIDENCE_GAP,
                    best_estimate=best_estimate,
                    best_entity=best_passport.entity_id,
                    reason=f"declared but UNMEASURED (sample_count={best_estimate.sample_count}, evidence_level={best_estimate.evidence_level.value})",
                )
            )
            continue

        if best_estimate.quality_mean is not None and best_estimate.quality_mean < quality_floor:
            gaps.append(
                CapabilityGap(
                    capability=capability,
                    task_category="*",
                    kind=GapKind.QUALITY_GAP,
                    best_estimate=best_estimate,
                    best_entity=best_passport.entity_id,
                    reason=f"measured quality {best_estimate.quality_mean:.2f} below floor {quality_floor:.2f}",
                )
            )
            continue

        # No gap - measured quality meets or exceeds floor
        pass

    return tuple(gaps)


def _make_base_reason(
    capability: str,
    estimate: CapabilityEstimate | None,
    passport: CapabilityPassport | None,
    is_tool_capability: bool,
    tool_required_by_fleet: bool,
    quality_floor: float,
) -> str:
    """Generate the honest base reason for a gap, regardless of kind override."""
    if passport is None:
        if is_tool_capability and not tool_required_by_fleet:
            tool_name = capability.replace(TOOL_CAPABILITY_PREFIX, "")
            return f"no passport declares this capability and no tool requirement exists for {tool_name}"
        return "no passport declares this capability"
    if estimate is not None and estimate.evidence_level == EvidenceLevel.UNMEASURED:
        return f"declared but UNMEASURED (sample_count={estimate.sample_count}, evidence_level={estimate.evidence_level.value})"
    if estimate is not None and estimate.quality_mean is not None and estimate.quality_mean < quality_floor:
        return f"measured quality {estimate.quality_mean:.2f} below floor {quality_floor:.2f}"
    return "no gap detected"


def plan_specialist(
    gap: CapabilityGap,
    passports: Iterable[CapabilityPassport],
    *,
    smoke_check: str,
    promotion_gate: str = DEFAULT_PROMOTION_GATE,
) -> TemporarySpecialistPlan:
    """Create a temporary specialist plan for a gap (plan §6.1/§6.2).

    This produces a PLAN ONLY — no creation, installation, or registration.
    The plan requires a capability-specific smoke check and a promotion gate.
    """
    if not isinstance(gap, CapabilityGap):
        raise ValueError(f"plan_specialist expects a CapabilityGap, got {gap!r}")

    roster = list(passports)
    for passport in roster:
        if not isinstance(passport, CapabilityPassport):
            raise ValueError(f"passports must contain CapabilityPassport rows, got {passport!r}")

    # Determine base entity: the closest existing evidence to build on
    base_entity = None
    if gap.kind in (GapKind.EVIDENCE_GAP, GapKind.QUALITY_GAP) and gap.best_entity:
        base_entity = gap.best_entity
    elif gap.kind == GapKind.MODEL_GAP and roster:
        # Pick the passport with the highest measured quality FOR THE GAP CAPABILITY
        best_quality = -1.0
        for passport in roster:
            if not passport.enabled:
                continue
            estimate = passport.estimate_for(gap.capability)
            if estimate is not None and estimate.quality_mean is not None and estimate.quality_mean > best_quality:
                best_quality = estimate.quality_mean
                base_entity = passport.entity_id

    # Determine tools to add based on gap kind
    add_tools: list[str] = []
    if gap.kind == GapKind.TOOL_GAP:
        tool_name = gap.capability.replace(TOOL_CAPABILITY_PREFIX, "")
        if tool_name:
            add_tools.append(tool_name)

    # No skill is ever invented; there is no data source for one
    add_skills: list[str] = []

    return TemporarySpecialistPlan(
        capability=gap.capability,
        gap=gap.kind,
        base_entity=base_entity,
        add_tools=tuple(add_tools),
        add_skills=tuple(add_skills),
        smoke_check=smoke_check,
        promotion_gate=promotion_gate,
        retire_after_run=True,
    )


__all__ = [
    "DEFAULT_QUALITY_FLOOR",
    "DEFAULT_PROMOTION_GATE",
    "TOOL_CAPABILITY_PREFIX",
    "GapKind",
    "CapabilityGap",
    "TemporarySpecialistPlan",
    "detect_gaps",
    "plan_specialist",
]
