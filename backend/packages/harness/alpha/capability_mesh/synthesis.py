"""Best-per-step composition with an integration compiler (Feature F + U).

This module turns a routing plan and its artifacts into a composed deliverable.
The central honesty rule (plan §7 + §22): **incompatible assumptions are never
merged into a synthetic "best of both" answer**. When a conflict remains
unresolved, ``composed_artifact`` is ``None`` and ``alternatives`` contains
named options describing the divergent assumptions and the conditions under
which each would be chosen.

Everything here is a crude, conservative heuristic by design:
- ``extract_assumptions`` is keyword-based and incomplete.
- ``detect_conflicts`` compares assumption strings and flags obvious
  contradictions; it misses subtle ones (a false COMPATIBLE is possible).
- ``resolve_slots`` picks the highest-scoring measurable candidate; when
  evidence is thin the choice is marked speculative.

Later phases can swap in model-driven extraction and semantic conflict
detection; this baseline establishes the contract and the honesty boundaries.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.capability_mesh.assignment import RoutingPlan
from alpha.capability_mesh.models import EvidenceLevel, evidence_rank
from alpha.capability_mesh.policy import DecisionPolicy

# --------------------------------------------------------------------------- enums


class SlotStatus(StrEnum):
    """Verification status of a contribution slot (plan §7.1)."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    NOT_APPLICABLE = "not_applicable"


class CompatibilityStatus(StrEnum):
    """Compatibility between slots (plan §7.1)."""

    UNCHECKED = "unchecked"
    COMPATIBLE = "compatible"
    CONFLICT = "conflict"
    ALTERNATIVE = "alternative"


# ---------------------------------------------------------------------------- datatypes


def _coerce(enum_cls: type[StrEnum], value: Any, field: str) -> StrEnum:
    """Coerce ``value`` into ``enum_cls`` or refuse naming the allowed values."""
    try:
        return enum_cls(str(value))
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum_cls)
        raise ValueError(f"unknown {field} {value!r}; expected one of: {allowed}") from error


@dataclass(frozen=True)
class ContributionSlot:
    """One output slot in the composed deliverable (plan §7.1)."""

    slot_id: str
    requirement_id: str
    capability_target: tuple[str, ...]
    selected_artifact_id: str | None = None
    dependency_slots: tuple[str, ...] = ()
    verification_status: SlotStatus = SlotStatus.PENDING
    compatibility_status: CompatibilityStatus = CompatibilityStatus.UNCHECKED
    candidate_artifacts: tuple[str, ...] = ()
    provenance: tuple[tuple[str, str], ...] = ()
    assumptions: tuple[str, ...] = ()
    version_context: str | None = None
    confidence: float | None = None
    limitations: tuple[str, ...] = ()
    conflict_summary: str | None = None
    selected_reason: str | None = None

    def __post_init__(self) -> None:
        # Coerced (and validated with the friendly message) in __post_init__:
        # detect_conflicts()/resolve_slots() rebuild slots from to_dict()
        # values, which carry the enum *strings*, and a hand-edited document
        # must not smuggle a raw string past the door either.
        object.__setattr__(self, "verification_status", _coerce(SlotStatus, self.verification_status, "verification_status"))
        object.__setattr__(
            self,
            "compatibility_status",
            _coerce(CompatibilityStatus, self.compatibility_status, "compatibility_status"),
        )
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        if self.selected_artifact_id is not None and self.selected_reason is None:
            raise ValueError("selected_artifact_id set requires selected_reason")
        if (
            self.compatibility_status
            in (
                CompatibilityStatus.CONFLICT,
                CompatibilityStatus.ALTERNATIVE,
            )
            and self.conflict_summary is None
        ):
            raise ValueError(f"compatibility_status={self.compatibility_status.value} requires conflict_summary")

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "requirement_id": self.requirement_id,
            "capability_target": list(self.capability_target),
            "selected_artifact_id": self.selected_artifact_id,
            "dependency_slots": list(self.dependency_slots),
            "verification_status": self.verification_status.value,
            "compatibility_status": self.compatibility_status.value,
            "candidate_artifacts": list(self.candidate_artifacts),
            "provenance": [list(p) for p in self.provenance],
            "assumptions": list(self.assumptions),
            "version_context": self.version_context,
            "confidence": self.confidence,
            "limitations": list(self.limitations),
            "conflict_summary": self.conflict_summary,
            "selected_reason": self.selected_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContributionSlot:
        return cls(
            slot_id=str(data["slot_id"]),
            requirement_id=str(data["requirement_id"]),
            capability_target=tuple(str(c) for c in data.get("capability_target", ())),
            selected_artifact_id=data.get("selected_artifact_id"),
            dependency_slots=tuple(str(s) for s in data.get("dependency_slots", ())),
            verification_status=SlotStatus(data.get("verification_status", "pending")),
            compatibility_status=CompatibilityStatus(data.get("compatibility_status", "unchecked")),
            candidate_artifacts=tuple(str(a) for a in data.get("candidate_artifacts", ())),
            provenance=tuple(tuple(str(x) for x in p) for p in data.get("provenance", ())),
            assumptions=tuple(str(a) for a in data.get("assumptions", ())),
            version_context=data.get("version_context"),
            confidence=(None if data.get("confidence") is None else float(data["confidence"])),
            limitations=tuple(str(item) for item in data.get("limitations", ())),
            conflict_summary=data.get("conflict_summary"),
            selected_reason=data.get("selected_reason"),
        )


@dataclass(frozen=True)
class ArtifactRecord:
    """One artifact produced by an entity for a microtask."""

    artifact_id: str
    entity_id: str
    microtask_id: str
    content: str
    mime_type: str
    capability_coverage: tuple[str, ...]
    evidence_level: EvidenceLevel
    quality: float | None
    passed_hard_gate: bool | None
    recorded_at: str

    def __post_init__(self) -> None:
        if self.quality is not None and not (0.0 <= self.quality <= 1.0):
            raise ValueError(f"quality must be in [0, 1], got {self.quality}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "entity_id": self.entity_id,
            "microtask_id": self.microtask_id,
            "content": self.content,
            "mime_type": self.mime_type,
            "capability_coverage": list(self.capability_coverage),
            "evidence_level": self.evidence_level.value,
            "quality": self.quality,
            "passed_hard_gate": self.passed_hard_gate,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactRecord:
        return cls(
            artifact_id=str(data["artifact_id"]),
            entity_id=str(data["entity_id"]),
            microtask_id=str(data["microtask_id"]),
            content=str(data["content"]),
            mime_type=str(data["mime_type"]),
            capability_coverage=tuple(str(c) for c in data.get("capability_coverage", ())),
            evidence_level=EvidenceLevel(data.get("evidence_level", "unmeasured")),
            quality=(None if data.get("quality") is None else float(data["quality"])),
            passed_hard_gate=data.get("passed_hard_gate"),
            recorded_at=str(data["recorded_at"]),
        )


@dataclass(frozen=True)
class CompositionResult:
    """Result of composing slots into a deliverable (plan §7 + §22)."""

    slots: tuple[ContributionSlot, ...]
    composed_artifact: str | None
    alternatives: tuple[str, ...]
    conflicts: tuple[str, ...]
    provenance_map: dict[str, str]
    unresolved_capabilities: tuple[str, ...]
    profile_version: str

    def __post_init__(self) -> None:
        if self.profile_version == "":
            raise ValueError("profile_version must be non-empty")
        has_conflict = any(s.compatibility_status == CompatibilityStatus.CONFLICT for s in self.slots)
        if has_conflict and self.composed_artifact is not None:
            raise ValueError("composed_artifact must be None when any slot has CONFLICT")
        if has_conflict and not self.alternatives:
            raise ValueError("alternatives must contain at least one entry per CONFLICT")
        if has_conflict and not self.conflicts:
            raise ValueError("conflicts must name every CONFLICT")

    def to_dict(self) -> dict[str, Any]:
        return {
            "slots": [s.to_dict() for s in self.slots],
            "composed_artifact": self.composed_artifact,
            "alternatives": list(self.alternatives),
            "conflicts": list(self.conflicts),
            "provenance_map": dict(self.provenance_map),
            "unresolved_capabilities": list(self.unresolved_capabilities),
            "profile_version": self.profile_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompositionResult:
        return cls(
            slots=tuple(ContributionSlot.from_dict(s) for s in data.get("slots", ())),
            composed_artifact=data.get("composed_artifact"),
            alternatives=tuple(str(a) for a in data.get("alternatives", ())),
            conflicts=tuple(str(c) for c in data.get("conflicts", ())),
            provenance_map=dict(data.get("provenance_map", {})),
            unresolved_capabilities=tuple(str(c) for c in data.get("unresolved_capabilities", ())),
            profile_version=str(data["profile_version"]),
        )


# -------------------------------------------------------------------------- artifact store


class ArtifactStoreError(RuntimeError):
    """The artifact store is unusable or its document is corrupt."""


class ArtifactStore:
    """In-memory + durable artifact mapping (artifact_id -> ArtifactRecord)."""

    FILE_NAME = "artifacts.json"

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)

    @property
    def path(self) -> Path:
        return self._directory / self.FILE_NAME

    def _atomic_write(self, document: dict[str, Any]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def put(self, record: ArtifactRecord) -> None:
        doc = self._load()
        doc[record.artifact_id] = record.to_dict()
        self._atomic_write(doc)

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        doc = self._load()
        if artifact_id not in doc:
            return None
        return ArtifactRecord.from_dict(doc[artifact_id])

    def for_slot(self, slot_id: str) -> list[ArtifactRecord]:
        """Return all artifacts whose microtask_id == slot_id (since slot_id == microtask_id)."""
        doc = self._load()
        return [ArtifactRecord.from_dict(v) for v in doc.values() if v.get("microtask_id") == slot_id]

    def for_entity(self, entity_id: str) -> list[ArtifactRecord]:
        doc = self._load()
        return [ArtifactRecord.from_dict(v) for v in doc.values() if v.get("entity_id") == entity_id]

    def _load(self) -> dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as handle:
                doc = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactStoreError(f"artifact store '{self.path}' is unreadable: {error}")
        if not isinstance(doc, dict):
            raise ArtifactStoreError(f"artifact store '{self.path}' is not an object")
        return doc


# -------------------------------------------------------------------- constants


ASSUMPTION_PATTERNS = (
    "assume",
    "given",
    "requires",
    "expects",
    "if ",
    "provided that",
    "depends on",
    "presume",
    "suppose",
    "on the condition",
)
CONTEXT_WINDOW = 200
QUALITY_FLOOR = 0.5


# ------------------------------------------------------------------- assumption extraction


def extract_assumptions(artifact: ArtifactRecord, capability: str) -> tuple[str, ...]:
    """Crude deterministic assumption extractor (honest about its limits)."""
    text = artifact.content.lower()
    cap = capability.lower()
    found: set[str] = set()
    for pattern in ASSUMPTION_PATTERNS:
        start = 0
        while True:
            idx = text.find(pattern, start)
            if idx == -1:
                break
            # Check if capability keyword appears nearby
            window_start = max(0, idx - CONTEXT_WINDOW)
            window_end = min(len(text), idx + len(pattern) + CONTEXT_WINDOW)
            window = text[window_start:window_end]
            if cap in window:
                snippet = window.strip()
                if len(snippet) > 200:
                    snippet = snippet[:200] + "…"
                found.add(snippet)
            start = idx + len(pattern)
    if not found:
        if artifact.evidence_level == EvidenceLevel.UNMEASURED:
            return ("[assumptions unknown — unmeasured capability]",)
        return ()
    return tuple(sorted(found))


# ---------------------------------------------------------------- slot building


def build_slots(
    routing_plan: RoutingPlan,
    artifacts: ArtifactStore,
    *,
    profile_version: str,
    evidence_floor: EvidenceLevel = EvidenceLevel.OBSERVED,
) -> tuple[ContributionSlot, ...]:
    """One slot per microtask from the routing plan."""
    if profile_version == "":
        raise ValueError("profile_version must be non-empty")

    slots: list[ContributionSlot] = []
    for assignment in routing_plan.assignments:
        microtask_id = assignment.microtask_id
        entity_id = assignment.passport_id
        candidate_arts = artifacts.for_entity(entity_id)
        candidate_ids = tuple(a.artifact_id for a in candidate_arts)

        # Determine verification status from the best artifact
        best_evidence = EvidenceLevel.UNMEASURED
        best_quality: float | None = None
        best_passed_gate: bool | None = None
        for art in candidate_arts:
            if evidence_rank(art.evidence_level) > evidence_rank(best_evidence):
                best_evidence = art.evidence_level
            if art.quality is not None and (best_quality is None or art.quality > best_quality):
                best_quality = art.quality
            if art.passed_hard_gate is True:
                best_passed_gate = True

        if candidate_ids:
            if evidence_rank(best_evidence) >= evidence_rank(evidence_floor) and best_passed_gate is not False and (best_quality is None or best_quality >= QUALITY_FLOOR):
                vstatus = SlotStatus.PASSED
            elif best_evidence != EvidenceLevel.UNMEASURED and (best_quality is not None and best_quality < QUALITY_FLOOR or best_passed_gate is False):
                vstatus = SlotStatus.FAILED
            elif best_evidence != EvidenceLevel.UNMEASURED:
                vstatus = SlotStatus.UNCERTAIN
            else:
                vstatus = SlotStatus.UNCERTAIN
        else:
            vstatus = SlotStatus.NOT_APPLICABLE

        # Extract assumptions from each candidate
        all_assumptions: set[str] = set()
        for art in candidate_arts:
            for cap in assignment.expected_outputs:
                all_assumptions.update(extract_assumptions(art, cap))

        slot = ContributionSlot(
            slot_id=microtask_id,
            requirement_id=microtask_id,
            capability_target=tuple(assignment.expected_outputs),
            candidate_artifacts=candidate_ids,
            provenance=((entity_id, assignment.role.value),),
            assumptions=tuple(sorted(all_assumptions)),
            verification_status=vstatus,
            compatibility_status=CompatibilityStatus.UNCHECKED,
        )
        slots.append(slot)

    return tuple(slots)


# --------------------------------------------------------------- conflict detection


def _contradicts(a: str, b: str) -> bool:
    """Heuristic: do two assumption strings contradict?"""
    # "not X" vs "X"
    if a.startswith("not ") and a[4:] in b:
        return True
    if b.startswith("not ") and b[4:] in a:
        return True
    # "requires X" vs "excludes X"
    if a.startswith("requires ") and b.startswith("excludes "):
        if a[9:] == b[9:]:
            return True
    if b.startswith("requires ") and a.startswith("excludes "):
        if b[9:] == a[9:]:
            return True
    # tool exclusivity: "use:foo" vs "use:bar" when both are tool capabilities
    if a.startswith("use:") and b.startswith("use:") and a != b:
        return True
    return False


def detect_conflicts(
    slots: tuple[ContributionSlot, ...],
) -> tuple[ContributionSlot, ...]:
    """Compare slots pairwise; mark CONFLICT / ALTERNATIVE / COMPATIBLE."""
    if len(slots) < 2:
        return slots

    updated = list(slots)
    for i, s1 in enumerate(updated):
        if s1.verification_status not in (SlotStatus.PASSED, SlotStatus.UNCERTAIN):
            continue
        for j, s2 in enumerate(updated):
            if i >= j:
                continue
            if s2.verification_status not in (SlotStatus.PASSED, SlotStatus.UNCERTAIN):
                continue

            conflict_pair = None
            for a1 in s1.assumptions:
                for a2 in s2.assumptions:
                    if _contradicts(a1, a2):
                        conflict_pair = (a1, a2)
                        break
                if conflict_pair:
                    break

            if conflict_pair:
                a1, a2 = conflict_pair
                summary = f"Contradiction: {a1} vs {a2}"
                updated[i] = ContributionSlot(
                    **{
                        **s1.to_dict(),
                        "compatibility_status": CompatibilityStatus.CONFLICT,
                        "conflict_summary": summary,
                    }
                )
                updated[j] = ContributionSlot(
                    **{
                        **s2.to_dict(),
                        "compatibility_status": CompatibilityStatus.CONFLICT,
                        "conflict_summary": summary,
                    }
                )
            elif s1.assumptions and s2.assumptions:
                # Different assumptions but no direct contradiction -> ALTERNATIVE
                if s1.assumptions != s2.assumptions:
                    summary = "Divergent assumptions; preserved as alternatives"
                    if updated[i].compatibility_status == CompatibilityStatus.UNCHECKED:
                        updated[i] = ContributionSlot(
                            **{
                                **updated[i].to_dict(),
                                "compatibility_status": CompatibilityStatus.ALTERNATIVE,
                                "conflict_summary": summary,
                            }
                        )
                    if updated[j].compatibility_status == CompatibilityStatus.UNCHECKED:
                        updated[j] = ContributionSlot(
                            **{
                                **updated[j].to_dict(),
                                "compatibility_status": CompatibilityStatus.ALTERNATIVE,
                                "conflict_summary": summary,
                            }
                        )
            else:
                if updated[i].compatibility_status == CompatibilityStatus.UNCHECKED:
                    updated[i] = ContributionSlot(
                        **{
                            **updated[i].to_dict(),
                            "compatibility_status": CompatibilityStatus.COMPATIBLE,
                        }
                    )
                if updated[j].compatibility_status == CompatibilityStatus.UNCHECKED:
                    updated[j] = ContributionSlot(
                        **{
                            **updated[j].to_dict(),
                            "compatibility_status": CompatibilityStatus.COMPATIBLE,
                        }
                    )

    return tuple(updated)


# ---------------------------------------------------------------- slot resolution


def _score_artifact(art: ArtifactRecord) -> float:
    """Quality * evidence_rank; UNMEASURED gets a low prior."""
    rank = {
        EvidenceLevel.UNMEASURED: 0,
        EvidenceLevel.OBSERVED: 1,
        EvidenceLevel.BENCHMARKED: 2,
        EvidenceLevel.OUTCOME_VERIFIED: 3,
        EvidenceLevel.RECENTLY_REVALIDATED: 4,
    }.get(art.evidence_level, 0)
    q = art.quality if art.quality is not None else 0.5
    return q * (0.25 if rank == 0 else rank / 4.0)


def resolve_slots(
    slots: tuple[ContributionSlot, ...],
    *,
    policy: DecisionPolicy | None = None,
    artifacts: ArtifactStore | None = None,
    profile_version: str,
) -> CompositionResult:
    """Greedy resolution; build alternatives for conflicts; assemble when clean."""
    if profile_version == "":
        raise ValueError("profile_version must be non-empty")

    # Pick best artifact per slot
    chosen: dict[str, str] = {}  # slot_id -> artifact_id
    reasons: dict[str, str] = {}
    all_conflicts: list[str] = []
    all_alternatives: list[str] = []

    for slot in slots:
        if slot.compatibility_status == CompatibilityStatus.CONFLICT:
            all_conflicts.append(f"Slot {slot.slot_id}: {slot.conflict_summary or 'contradictory assumptions'}")
            # Pick the better candidate
            best_art = None
            best_score = -1.0
            alt_arts: list[str] = []
            if artifacts is not None:
                for aid in slot.candidate_artifacts:
                    art = artifacts.get(aid)
                    if art is None:
                        continue
                    score = _score_artifact(art)
                    if score > best_score:
                        if best_art is not None:
                            alt_arts.append(best_art)
                        best_art = aid
                        best_score = score
                    else:
                        alt_arts.append(aid)
            if best_art is not None:
                chosen[slot.slot_id] = best_art
                reasons[slot.slot_id] = f"highest score {best_score:.3f}"
                for alt in alt_arts:
                    all_alternatives.append(f"Slot {slot.slot_id}: Option (rejected) — {alt}")
            # Also add the conflicting pair as an alternative description
            all_alternatives.append(f"Slot {slot.slot_id}: Conflict — {slot.conflict_summary}")
        elif slot.compatibility_status == CompatibilityStatus.ALTERNATIVE:
            all_alternatives.append(f"Slot {slot.slot_id}: Alternatives — " + " | ".join(slot.assumptions[:3]) + ("…" if len(slot.assumptions) > 3 else ""))
            # Pick one arbitrarily (first)
            if slot.candidate_artifacts:
                chosen[slot.slot_id] = slot.candidate_artifacts[0]
                reasons[slot.slot_id] = "first alternative (policy: first)"
        else:
            # COMPATIBLE or UNCHECKED -> pick best
            best_art = None
            best_score = -1.0
            if artifacts is not None:
                for aid in slot.candidate_artifacts:
                    art = artifacts.get(aid)
                    if art is None:
                        continue
                    score = _score_artifact(art)
                    if score > best_score:
                        best_art = aid
                        best_score = score
            if best_art is not None:
                chosen[slot.slot_id] = best_art
                reasons[slot.slot_id] = f"best score {best_score:.3f}"

    # Build composed artifact only if NO conflicts remain
    composed_artifact = None
    if not all_conflicts:
        parts: list[str] = []
        # Order by dependency (topological sort would be ideal; for now by slot_id)
        for slot in sorted(slots, key=lambda s: s.slot_id):
            aid = chosen.get(slot.slot_id)
            if aid and artifacts is not None:
                art = artifacts.get(aid)
                if art:
                    parts.append(art.content)
        if parts:
            composed_artifact = "\n---\n".join(parts)

    # Provenance map
    provenance_map: dict[str, str] = {}
    for slot in slots:
        for aid in slot.candidate_artifacts:
            if artifacts is not None:
                art = artifacts.get(aid)
                if art:
                    provenance_map[aid] = art.entity_id

    # Unresolved capabilities
    unresolved: list[str] = []
    for slot in slots:
        if slot.verification_status == SlotStatus.NOT_APPLICABLE:
            continue
        if slot.slot_id not in chosen:
            unresolved.extend(slot.capability_target)

    return CompositionResult(
        slots=tuple(
            ContributionSlot(
                **{
                    **slot.to_dict(),
                    "selected_artifact_id": chosen.get(slot.slot_id),
                    "selected_reason": reasons.get(slot.slot_id),
                }
            )
            for slot in slots
        ),
        composed_artifact=composed_artifact,
        alternatives=tuple(all_alternatives),
        conflicts=tuple(all_conflicts),
        provenance_map=provenance_map,
        unresolved_capabilities=tuple(unresolved),
        profile_version=profile_version,
    )


def compose_deliverable(
    routing_plan: RoutingPlan,
    artifacts: ArtifactStore,
    *,
    profile_version: str,
    evidence_floor: EvidenceLevel = EvidenceLevel.OBSERVED,
    policy: DecisionPolicy | None = None,
) -> CompositionResult:
    """One-call API: build slots -> detect conflicts -> resolve."""
    slots = build_slots(routing_plan, artifacts, profile_version=profile_version, evidence_floor=evidence_floor)
    slots = detect_conflicts(slots)
    return resolve_slots(slots, policy=policy, artifacts=artifacts, profile_version=profile_version)


__all__ = [
    "ASSUMPTION_PATTERNS",
    "CONTEXT_WINDOW",
    "QUALITY_FLOOR",
    "ArtifactRecord",
    "ArtifactStore",
    "ArtifactStoreError",
    "CompatibilityStatus",
    "CompositionResult",
    "ContributionSlot",
    "SlotStatus",
    "compose_deliverable",
    "build_slots",
    "detect_conflicts",
    "extract_assumptions",
    "resolve_slots",
]
