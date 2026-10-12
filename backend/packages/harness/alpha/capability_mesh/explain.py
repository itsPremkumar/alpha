"""Capability-aware explainability: bounded panels, never a leaderboard (plan §24, Feature W).

Feature W asks for a compact view a user can read, not a debug trace, and it
forbids one thing explicitly: **do not render a giant model leaderboard as the
primary UX**. So this module produces one :class:`ExplanationBundle` — a set of
small bounded panels whose primary question is *"why this agent for this
particular step?"* — and nothing in it ranks agents against each other.

Six rules decide the shape of this module:

* **Every panel is bounded, and truncation is visible.** ``MAX_VISIBLE_*``
  caps each panel and every dropped row is reported as a ``"…+N more"`` marker
  in :attr:`ExplanationBundle.honest_notes` — a panel is never silently
  thinned. A capability list truncated *inside* one item has no
  ``honest_notes`` to report into, so its last visible entry becomes the
  marker (see :func:`_bounded_capabilities`): a silent cut would render as
  "these are all of them".
* **A caption is derived, never hand-set.** :class:`ConfidenceView.caption`
  is computed from its ``evidence_level`` and ``sample_count`` on
  construction, so an unmeasured capability can only ever read
  ``"unmeasured (0 samples)"`` and never be smoothed into
  ``"well measured"``.
* **No score without its sample count.** Every caption names its count,
  because "well measured" over one sample is a different claim than over
  forty.
* **Unresolved conflicts stay visible.** A conflict with alternatives is
  reported as ``"left as alternatives"``; a conflict nobody resolved is
  ``"unresolved"``. Neither is quietly dropped to make the plan look clean.
* **``None`` means unknown, never zero.** :class:`ResourceView` carries ``None``
  for an unpriced estimate and an unknown remaining budget.
* **The learning controls are ADVERTISED, not implemented.** This package has
  no UI, no runtime and no route, so :class:`LearningControls` is a
  declaration of what a real surface must offer — never a claim that Alpha
  honours it today.

Nothing here reads a live run: :func:`explain_plan` is a pure projection over
a :class:`~alpha.capability_mesh.assignment.RoutingPlan` plus the optional
composition, memory and resource figures the caller supplies.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from alpha.capability_mesh.assignment import NO_ELIGIBLE_ENTITY, RoutingPlan
from alpha.capability_mesh.memory import CapabilityMemory
from alpha.capability_mesh.models import EvidenceLevel
from alpha.capability_mesh.synthesis import CompositionResult, ContributionSlot, SlotStatus

#: How many rows each panel may show before truncation is reported.
MAX_VISIBLE_STEPS = 8
MAX_VISIBLE_ASSIGNMENTS = 16
MAX_VISIBLE_SLOTS = 8
MAX_VISIBLE_CONFIDENCE = 12
MAX_VISIBLE_DISAGREEMENTS = 8
MAX_VISIBLE_FALLBACKS = 6
MAX_VISIBLE_HONEST_NOTES = 16

#: How many capabilities one panel item may list.
MAX_VISIBLE_CAPABILITIES = 8

#: The hard ceiling on one rendered reason string. A long ``score_explanation``
#: is a fit-score audit trail, not a UI line.
MAX_REASON_CHARS = 240

#: The marker a truncated panel reports in ``honest_notes``. A panel is never
#: silently thinned: the count of what was hidden is part of the message.
TRUNCATION_MARKER = "…"

#: The sample count at or above which a benchmarked capability may be captioned
#: "well measured". A documented constant, not a learned threshold.
WELL_MEASURED_MIN_SAMPLES = 10

#: The three captions a capability-confidence row may carry (plan §24).
CAPTION_WELL_MEASURED = "well measured"
CAPTION_LIMITED_HISTORY = "limited history"
CAPTION_UNMEASURED = "unmeasured"

#: The statuses a task-map row may carry.
TASK_STATUS_ROUTED = "routed"
TASK_STATUS_UNASSIGNED = "unassigned"

#: The two ways a disagreement may be resolved in the projection (plan §24).
RESOLUTION_LEFT_AS_ALTERNATIVES = "left as alternatives"
RESOLUTION_UNRESOLVED = "unresolved"


def _truncate(text: str, limit: int) -> str:
    """Bound one string, marking the cut instead of hiding it."""
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}{TRUNCATION_MARKER}"


def _bounded(rows: list[Any], limit: int, panel: str, notes: list[str]) -> tuple[Any, ...]:
    """Return at most ``limit`` rows, reporting every dropped one."""
    if len(rows) <= limit:
        return tuple(rows)
    hidden = len(rows) - limit
    notes.append(f"{panel}: {TRUNCATION_MARKER}+{hidden} more not shown (bounded at {limit})")
    return tuple(rows[:limit])


@dataclass(frozen=True)
class TaskMapItem:
    """One visible substep of the plan (plan §24's task map).

    ``title`` is the plan's microtask id: a routing plan records microtask
    IDS, not titles, and a title is never invented here. ``status`` is
    ``"routed"`` or ``"unassigned"``; ``capabilities`` lists the capabilities
    the step was routed for (plan-level, because the plan does not carry a
    per-step capability list).
    """

    microtask_id: str
    title: str
    status: str
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.microtask_id:
            raise ValueError("microtask_id must be non-empty")
        if not self.title:
            raise ValueError("title must be non-empty")
        if self.status not in (TASK_STATUS_ROUTED, TASK_STATUS_UNASSIGNED):
            raise ValueError(f"status must be one of {[TASK_STATUS_ROUTED, TASK_STATUS_UNASSIGNED]}, got {self.status!r}")
        object.__setattr__(self, "capabilities", _bounded_capabilities(self.capabilities))

    def to_dict(self) -> dict[str, Any]:
        return {"microtask_id": self.microtask_id, "title": self.title, "status": self.status, "capabilities": list(self.capabilities)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskMapItem:
        return cls(microtask_id=str(data["microtask_id"]), title=str(data["title"]), status=str(data["status"]), capabilities=tuple(str(capability) for capability in data.get("capabilities", ())))


@dataclass(frozen=True)
class SpecialistAssignmentView:
    """Who was chosen for one step, with the observable fit reason (plan §24).

    ``reason`` is the assignment's own ``score_explanation`` — the numbers the
    fit score actually produced — bounded to ``MAX_REASON_CHARS``. It is a
    step-level justification; it is never an overall agent ranking.
    """

    microtask_id: str
    passport_id: str
    role: str
    reason: str

    def __post_init__(self) -> None:
        for field_name in ("microtask_id", "passport_id", "role", "reason"):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be non-empty: an assignment row without its explanation is an assertion")
        object.__setattr__(self, "reason", _truncate(self.reason, MAX_REASON_CHARS))

    def to_dict(self) -> dict[str, Any]:
        return {"microtask_id": self.microtask_id, "passport_id": self.passport_id, "role": self.role, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpecialistAssignmentView:
        return cls(microtask_id=str(data["microtask_id"]), passport_id=str(data["passport_id"]), role=str(data["role"]), reason=str(data["reason"]))


@dataclass(frozen=True)
class ContributionMapView:
    """Which artifact each slot selected, who produced it, and how it verified (plan §24)."""

    slot_id: str
    selected_artifact_id: str | None
    entity_id: str | None
    verification_status: str
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.slot_id:
            raise ValueError("slot_id must be non-empty")
        if self.verification_status not in {status.value for status in SlotStatus}:
            allowed = ", ".join(status.value for status in SlotStatus)
            raise ValueError(f"verification_status must be one of: {allowed}, got {self.verification_status!r}")
        object.__setattr__(self, "capabilities", _bounded_capabilities(self.capabilities))

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "selected_artifact_id": self.selected_artifact_id,
            "entity_id": self.entity_id,
            "verification_status": self.verification_status,
            "capabilities": list(self.capabilities),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContributionMapView:
        return cls(
            slot_id=str(data["slot_id"]),
            selected_artifact_id=data.get("selected_artifact_id"),
            entity_id=data.get("entity_id"),
            verification_status=str(data["verification_status"]),
            capabilities=tuple(str(capability) for capability in data.get("capabilities", ())),
        )


@dataclass(frozen=True)
class ConfidenceView:
    """One capability-confidence row (plan §24).

    ``caption`` is DERIVED from ``evidence_level`` and ``sample_count`` in
    ``__post_init__`` and is not an input: an unmeasured capability can never
    be captioned anything but ``"unmeasured"``, no matter who asks.
    """

    capability: str
    entity_id: str
    evidence_level: EvidenceLevel
    sample_count: int
    # Derived from the two fields above; never an input, never serialised.
    caption: str = field(init=False, default="")

    def __post_init__(self) -> None:
        if not self.capability or not self.entity_id:
            raise ValueError("capability and entity_id must be non-empty")
        try:
            object.__setattr__(self, "evidence_level", EvidenceLevel(str(self.evidence_level)))
        except ValueError as error:
            allowed = ", ".join(level.value for level in EvidenceLevel)
            raise ValueError(f"unknown evidence_level {self.evidence_level!r}; expected one of: {allowed}") from error
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 0:
            raise ValueError(f"sample_count must be an int >= 0, got {self.sample_count!r}")
        object.__setattr__(self, "caption", _confidence_caption(self.evidence_level, self.sample_count))

    def to_dict(self) -> dict[str, Any]:
        # ``caption`` is deliberately absent: it is derived from the evidence
        # level and the sample count, never a claim a document makes.
        return {"capability": self.capability, "entity_id": self.entity_id, "evidence_level": self.evidence_level.value, "sample_count": self.sample_count}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfidenceView:
        # The constructor re-validates, so a hand-edited document cannot
        # bypass the honesty rules by skipping __post_init__.
        return cls(
            capability=str(data["capability"]),
            entity_id=str(data["entity_id"]),
            # Coerced (and validated with the friendly message) in __post_init__.
            evidence_level=data.get("evidence_level", EvidenceLevel.UNMEASURED),
            sample_count=int(data.get("sample_count", 0)),
        )


@dataclass(frozen=True)
class DisagreementView:
    """One important unresolved conflict and how the plan handled it (plan §24).

    ``resolution`` is ``"left as alternatives"`` when the composition kept
    named alternatives and ``"unresolved"`` when nothing resolved it. Both are
    first-class outcomes: a clean-looking panel that hides a conflict is the
    failure this module exists to prevent.
    """

    subject: str
    resolution: str

    def __post_init__(self) -> None:
        if not self.subject:
            raise ValueError("subject must be non-empty")
        if self.resolution not in (RESOLUTION_LEFT_AS_ALTERNATIVES, RESOLUTION_UNRESOLVED):
            raise ValueError(f"resolution must be one of {[RESOLUTION_LEFT_AS_ALTERNATIVES, RESOLUTION_UNRESOLVED]}, got {self.resolution!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "resolution": self.resolution}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DisagreementView:
        return cls(subject=str(data["subject"]), resolution=str(data["resolution"]))


@dataclass(frozen=True)
class ResourceView:
    """The resource meter: calls, estimated cost and remaining budget (plan §24).

    ``estimated_cost_usd`` is ``None`` when the run is unpriced and
    ``remaining_budget`` is ``None`` when no budget is known. Neither is ever
    a reassuring 0.0.
    """

    calls: int
    estimated_cost_usd: float | None = None
    remaining_budget: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.calls, bool) or not isinstance(self.calls, int) or self.calls < 0:
            raise ValueError(f"calls must be an int >= 0, got {self.calls!r}")
        for field_name in ("estimated_cost_usd", "remaining_budget"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{field_name} must be a finite number >= 0 or None, got {value!r}")
            object.__setattr__(self, field_name, float(value))

    def to_dict(self) -> dict[str, Any]:
        return {"calls": self.calls, "estimated_cost_usd": self.estimated_cost_usd, "remaining_budget": self.remaining_budget}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceView:
        return cls(calls=int(data.get("calls", 0)), estimated_cost_usd=data.get("estimated_cost_usd"), remaining_budget=data.get("remaining_budget"))


@dataclass(frozen=True)
class FallbackNotice:
    """What changed about the plan's assumptions, and why (plan §24).

    The mesh observes no live provider/locality/tool switch, so a notice is
    only ever produced from what its inputs disclose: a hard-gate refusal (a
    candidate was refused, so the assignment changed) or a plan note (a role
    was not filled). A runtime fallback nobody recorded cannot appear here.
    """

    what_changed: str
    why: str

    def __post_init__(self) -> None:
        for field_name in ("what_changed", "why"):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be non-empty: a notice without its cause is an assertion")

    def to_dict(self) -> dict[str, Any]:
        return {"what_changed": self.what_changed, "why": self.why}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FallbackNotice:
        return cls(what_changed=str(data["what_changed"]), why=str(data["why"]))


@dataclass(frozen=True)
class LearningControls:
    """The learning controls plan §24 asks a UI to offer.

    **These are ADVERTISED, not implemented.** This package has no UI, no HTTP
    route and no runtime that could honour them; the fields exist so the explainability contract names what a real surface must offer — a way to
    rate a result, a way to opt out of routing experiments, a way to disable cross-run performance memory, and a way to clear learned profile updates — and so nobody can claim Alpha already provides them.
    Every flag defaults to ``False``: no control is applied unless a caller sets one, and setting one here changes nothing in the running system.
    """

    rate_result: bool = False
    opt_out_routing_experiments: bool = False
    disable_cross_run_memory: bool = False
    clear_learned_profiles: bool = False

    def __post_init__(self) -> None:
        for field_name in ("rate_result", "opt_out_routing_experiments", "disable_cross_run_memory", "clear_learned_profiles"):
            value = getattr(self, field_name)
            if not isinstance(value, bool):
                raise ValueError(f"{field_name} must be a bool, got {value!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rate_result": self.rate_result,
            "opt_out_routing_experiments": self.opt_out_routing_experiments,
            "disable_cross_run_memory": self.disable_cross_run_memory,
            "clear_learned_profiles": self.clear_learned_profiles,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearningControls:
        return cls(
            rate_result=bool(data.get("rate_result", False)),
            opt_out_routing_experiments=bool(data.get("opt_out_routing_experiments", False)),
            disable_cross_run_memory=bool(data.get("disable_cross_run_memory", False)),
            clear_learned_profiles=bool(data.get("clear_learned_profiles", False)),
        )


@dataclass(frozen=True)
class ExplanationBundle:
    """One bounded, honest explanation of a routing plan (plan §24).

    One field per panel, plus ``honest_notes`` — which is where the
    truncation markers and every limit of the projection live. There is
    deliberately **no leaderboard field**: the primary explanation is why one
    entity was selected for one step, never how agents rank overall.
    """

    task_map: tuple[TaskMapItem, ...]
    specialist_assignments: tuple[SpecialistAssignmentView, ...]
    contribution_map: tuple[ContributionMapView, ...]
    capability_confidence: tuple[ConfidenceView, ...]
    disagreements: tuple[DisagreementView, ...]
    resource_meter: ResourceView
    fallback_notices: tuple[FallbackNotice, ...]
    learning_controls: LearningControls
    honest_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name, row_type in (
            ("task_map", TaskMapItem),
            ("specialist_assignments", SpecialistAssignmentView),
            ("contribution_map", ContributionMapView),
            ("capability_confidence", ConfidenceView),
            ("disagreements", DisagreementView),
            ("fallback_notices", FallbackNotice),
        ):
            rows = tuple(getattr(self, field_name))
            for row in rows:
                if not isinstance(row, row_type):
                    raise ValueError(f"{field_name} must contain {row_type.__name__} rows, got {row!r}")
            object.__setattr__(self, field_name, rows)
        if not isinstance(self.resource_meter, ResourceView):
            raise ValueError(f"resource_meter must be a ResourceView, got {self.resource_meter!r}")
        if not isinstance(self.learning_controls, LearningControls):
            raise ValueError(f"learning_controls must be a LearningControls, got {self.learning_controls!r}")
        notes = tuple(self.honest_notes)
        for note in notes:
            if not note:
                raise ValueError("honest_notes entries must be non-empty strings")
        object.__setattr__(self, "honest_notes", notes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_map": [row.to_dict() for row in self.task_map],
            "specialist_assignments": [row.to_dict() for row in self.specialist_assignments],
            "contribution_map": [row.to_dict() for row in self.contribution_map],
            "capability_confidence": [row.to_dict() for row in self.capability_confidence],
            "disagreements": [row.to_dict() for row in self.disagreements],
            "resource_meter": self.resource_meter.to_dict(),
            "fallback_notices": [row.to_dict() for row in self.fallback_notices],
            "learning_controls": self.learning_controls.to_dict(),
            "honest_notes": list(self.honest_notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExplanationBundle:
        return cls(
            task_map=tuple(TaskMapItem.from_dict(row) for row in data.get("task_map", ())),
            specialist_assignments=tuple(SpecialistAssignmentView.from_dict(row) for row in data.get("specialist_assignments", ())),
            contribution_map=tuple(ContributionMapView.from_dict(row) for row in data.get("contribution_map", ())),
            capability_confidence=tuple(ConfidenceView.from_dict(row) for row in data.get("capability_confidence", ())),
            disagreements=tuple(DisagreementView.from_dict(row) for row in data.get("disagreements", ())),
            resource_meter=ResourceView.from_dict(data["resource_meter"]),
            fallback_notices=tuple(FallbackNotice.from_dict(row) for row in data.get("fallback_notices", ())),
            learning_controls=LearningControls.from_dict(data.get("learning_controls", {})),
            honest_notes=tuple(str(note) for note in data.get("honest_notes", ())),
        )


# ------------------------------------------------------------------- helpers


def _bounded_capabilities(capabilities: Iterable[str]) -> tuple[str, ...]:
    """Capabilities are sorted, deduplicated and bounded — and say what they dropped.

    A panel that overflows writes its "…+N more" marker into
    ``honest_notes``, but a capability list truncated *inside* one item has no
    such place to report it. Cutting it silently would render as "these are all
    of the capabilities", which is exactly the over-claim this module refuses
    elsewhere, so the last visible entry becomes the marker itself and the cap
    is honoured at the same length.
    """
    cleaned = [str(capability) for capability in capabilities if capability]
    unique = list(dict.fromkeys(sorted(cleaned)))
    if len(unique) <= MAX_VISIBLE_CAPABILITIES:
        return tuple(unique)
    visible = unique[: MAX_VISIBLE_CAPABILITIES - 1]
    return (*visible, f"{TRUNCATION_MARKER}+{len(unique) - len(visible)} more")


def _confidence_caption(level: EvidenceLevel, sample_count: int) -> str:
    """The caption one evidence level and sample count earn — nothing smoother.

    UNMEASURED (or zero samples) is ``"unmeasured"``: an estimate nobody
    measured is never promoted by a neighbouring measured one. OUTCOME_VERIFIED
    and RECENTLY_REVALIDATED are outcome-backed, so they read
    ``"well measured"``; BENCHMARKED needs the sample count to earn it;
    everything else is ``"limited history"``.
    """
    if level is EvidenceLevel.UNMEASURED or sample_count < 1:
        phrase = CAPTION_UNMEASURED
    elif level in (EvidenceLevel.OUTCOME_VERIFIED, EvidenceLevel.RECENTLY_REVALIDATED) or (level is EvidenceLevel.BENCHMARKED and sample_count >= WELL_MEASURED_MIN_SAMPLES):
        phrase = CAPTION_WELL_MEASURED
    else:
        phrase = CAPTION_LIMITED_HISTORY
    return f"{phrase} ({sample_count} sample{'' if sample_count == 1 else 's'})"


#: A confidence row as a caller may supply it: ``(capability, entity_id,
#: evidence_level, sample_count)`` or a mapping with those keys.
ConfidenceInput = tuple[str, str, "EvidenceLevel | str", int] | Mapping[str, Any]


def _normalise_confidence_input(entry: ConfidenceInput) -> tuple[str, str, EvidenceLevel, int]:
    """Normalise one caller-supplied confidence row, refusing a bad shape."""
    if isinstance(entry, Mapping):
        try:
            return str(entry["capability"]), str(entry["entity_id"]), EvidenceLevel(str(entry.get("evidence_level", EvidenceLevel.UNMEASURED))), int(entry.get("sample_count", 0))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"confidence row {entry!r} must carry capability, entity_id, evidence_level and sample_count: {error}") from error
    if isinstance(entry, (tuple, list)) and len(entry) == 4:
        capability, entity_id, level, sample_count = entry
        try:
            return str(capability), str(entity_id), EvidenceLevel(str(level)), int(sample_count)
        except (TypeError, ValueError) as error:
            raise ValueError(f"confidence row {entry!r} must be (capability, entity_id, evidence_level, sample_count): {error}") from error
    raise ValueError(f"confidence row must be a 4-tuple (capability, entity_id, evidence_level, sample_count) or a mapping with those keys, got {entry!r}")


def _confidence_from_memory(plan: RoutingPlan, memory: CapabilityMemory, profile_version: str) -> list[ConfidenceView]:
    """Derive confidence rows from the plan's assignments against capability memory.

    Only the plan's assigned entity × the fingerprint's required capabilities
    is asked, and only under ``profile_version`` (the comparability rule):
    records measured under another rubric would be an incomparable number
    dressed as history.
    """
    rows: list[ConfidenceView] = []
    seen: set[tuple[str, str]] = set()
    for assignment in plan.assignments:
        for capability in plan.fingerprint.required_capabilities:
            key = (capability, assignment.passport_id)
            if key in seen:
                continue
            seen.add(key)
            estimate = memory.estimate_for(assignment.passport_id, capability, "*", profile_version=profile_version)
            rows.append(ConfidenceView(capability=capability, entity_id=assignment.passport_id, evidence_level=estimate.evidence_level, sample_count=estimate.sample_count))
    return rows


def _slot_entity_id(slot: ContributionSlot, composition: CompositionResult) -> str | None:
    """Who produced the artifact a slot selected, from the slot's own provenance."""
    for pair in slot.provenance:
        if pair and isinstance(pair, tuple) and len(pair) == 2 and isinstance(pair[0], str) and pair[0]:
            return pair[0]
    if slot.selected_artifact_id is not None:
        return composition.provenance_map.get(slot.selected_artifact_id)
    return None


def explain_plan(
    routing_plan: RoutingPlan,
    *,
    composition: CompositionResult | None = None,
    memory: CapabilityMemory | None = None,
    confidence_rows: Iterable[ConfidenceInput] = (),
    calls: int = 0,
    estimated_cost_usd: float | None = None,
    remaining_budget: float | None = None,
    profile_version: str,
) -> ExplanationBundle:
    """Project one routing plan into bounded, honest explanation panels (plan §24).

    ``routing_plan`` supplies the task map and the per-step specialist reasons
    (from each assignment's own ``score_explanation``). ``composition``, when
    given, supplies the contribution map and the disagreement rows — a
    conflict keeps ``"left as alternatives"`` while the composition carries
    alternatives, and an unresolved capability is ``"unresolved"``.
    ``confidence_rows`` is caller-supplied when the caller has measured rows;
    with ``memory`` and no rows, confidence is DERIVED from the plan's
    assignments through capability memory under ``profile_version``; with
    neither, the panel is empty and says so. ``calls``/``estimated_cost_usd``/
    ``remaining_budget`` fill the resource meter, with ``None`` meaning
    unknown rather than zero.

    The result never contains an overall agent ranking.
    """
    if not isinstance(routing_plan, RoutingPlan):
        raise ValueError(f"routing_plan must be a RoutingPlan, got {routing_plan!r}")
    if not profile_version:
        raise ValueError("explain_plan requires a non-empty profile_version (no score without its profile version)")
    if composition is not None and not isinstance(composition, CompositionResult):
        raise ValueError(f"composition must be a CompositionResult or None, got {composition!r}")
    if memory is not None and not isinstance(memory, CapabilityMemory):
        raise ValueError(f"memory must be a CapabilityMemory or None, got {memory!r}")
    if isinstance(calls, bool) or not isinstance(calls, int) or calls < 0:
        raise ValueError(f"calls must be an int >= 0, got {calls!r}")

    notes: list[str] = [
        f"bounded projection: each panel shows at most its MAX_VISIBLE_* rows and every truncation is reported here as '{TRUNCATION_MARKER}+N more', never silently",
        "no leaderboard is rendered: the primary explanation is why one entity was chosen for one step, never how agents rank overall (plan §24)",
        "learning controls are advertised, not implemented: this package has no UI, route or runtime that honours them",
        "task-map titles are microtask ids and task-map capabilities are plan-level: a routing plan carries ids, not titles or per-step capability lists, and neither is invented here",
    ]

    # ---------------------------------------------------------------- task map
    routed_ids = {assignment.microtask_id for assignment in routing_plan.assignments}
    unassigned_ids = {refusal.microtask_id for refusal in routing_plan.refusals if refusal.microtask_id and refusal.entity_id == NO_ELIGIBLE_ENTITY}
    plan_capabilities = tuple(routing_plan.fingerprint.required_capabilities)
    task_rows: list[TaskMapItem] = [TaskMapItem(microtask_id=microtask_id, title=microtask_id, status=TASK_STATUS_ROUTED, capabilities=plan_capabilities) for microtask_id in sorted(routed_ids)]
    task_rows.extend(TaskMapItem(microtask_id=microtask_id, title=microtask_id, status=TASK_STATUS_UNASSIGNED, capabilities=plan_capabilities) for microtask_id in sorted(unassigned_ids - routed_ids))
    task_map = _bounded(task_rows, MAX_VISIBLE_STEPS, "task map", notes)

    # ------------------------------------------------------ specialist steps
    assignment_rows: list[SpecialistAssignmentView] = [
        SpecialistAssignmentView(
            microtask_id=assignment.microtask_id,
            passport_id=assignment.passport_id,
            role=assignment.role.value,
            reason=" ".join(assignment.score_explanation),
        )
        for assignment in routing_plan.assignments
    ]
    specialist_assignments = _bounded(assignment_rows, MAX_VISIBLE_ASSIGNMENTS, "specialist assignments", notes)

    # -------------------------------------------------------- contribution map
    contribution_rows: list[ContributionMapView] = []
    if composition is not None:
        for slot in composition.slots:
            contribution_rows.append(
                ContributionMapView(
                    slot_id=slot.slot_id,
                    selected_artifact_id=slot.selected_artifact_id,
                    entity_id=_slot_entity_id(slot, composition),
                    verification_status=slot.verification_status.value,
                    capabilities=slot.capability_target,
                )
            )
    contribution_map = _bounded(contribution_rows, MAX_VISIBLE_SLOTS, "contribution map", notes)
    if composition is None:
        notes.append("no composition was supplied: the contribution map is empty because nothing was composed, not because every slot produced nothing")

    # ------------------------------------------------------ capability confidence
    supplied = [_normalise_confidence_input(entry) for entry in confidence_rows]
    confidence_views: list[ConfidenceView] = [ConfidenceView(capability=capability, entity_id=entity_id, evidence_level=level, sample_count=sample_count) for capability, entity_id, level, sample_count in supplied]
    if not confidence_views and memory is not None:
        confidence_views = _confidence_from_memory(routing_plan, memory, profile_version)
        notes.append(f"capability confidence is derived from capability memory for the plan's assigned entities × the fingerprint's required capabilities under profile_version '{profile_version}'")
    if not confidence_views:
        notes.append(f"capability confidence is empty: no confidence rows were supplied and no capability memory was given under profile_version '{profile_version}' — an unshown capability is unknown, not unmeasured-good")
    capability_confidence = _bounded(confidence_views, MAX_VISIBLE_CONFIDENCE, "capability confidence", notes)

    # ------------------------------------------------------------ disagreements
    disagreement_rows: list[DisagreementView] = []
    if composition is not None:
        resolution = RESOLUTION_LEFT_AS_ALTERNATIVES if composition.alternatives else RESOLUTION_UNRESOLVED
        for conflict in composition.conflicts:
            disagreement_rows.append(DisagreementView(subject=conflict, resolution=resolution))
        for capability in composition.unresolved_capabilities:
            disagreement_rows.append(DisagreementView(subject=f"capability '{capability}' was not covered by any selected contribution", resolution=RESOLUTION_UNRESOLVED))
    disagreements = _bounded(disagreement_rows, MAX_VISIBLE_DISAGREEMENTS, "disagreements", notes)

    # ------------------------------------------------------------ resource meter
    resource_meter = ResourceView(calls=calls, estimated_cost_usd=estimated_cost_usd, remaining_budget=remaining_budget)
    if estimated_cost_usd is None:
        notes.append("estimated cost is unknown (None): an unpriced plan is not a free plan, and no cost was supplied")

    # ----------------------------------------------------------- fallback notices
    fallback_rows: list[FallbackNotice] = []
    for refusal in routing_plan.refusals:
        if refusal.entity_id == NO_ELIGIBLE_ENTITY:
            continue  # an unassigned step is a task-map status, not a fallback switch
        scope = f" on microtask {refusal.microtask_id!r}" if refusal.microtask_id else ""
        fallback_rows.append(FallbackNotice(what_changed=f"candidate refused: {refusal.entity_id}{scope}", why="; ".join(refusal.unmet)))
    for note in routing_plan.notes:
        if note.startswith("microtask") and "left unassigned" in note:
            fallback_rows.append(FallbackNotice(what_changed="step left unassigned", why=note))
        elif note.startswith("microtask") and ("no challenger" in note or "no verifier" in note):
            fallback_rows.append(FallbackNotice(what_changed="role not filled", why=note))
    fallback_notices = _bounded(fallback_rows, MAX_VISIBLE_FALLBACKS, "fallback notices", notes)
    notes.append("fallback notices are projections of the routing plan's own refusals and notes: the mesh observes no live provider, locality, tool or quality switch, so a runtime fallback nobody recorded cannot appear here")

    # -------------------------------------------------------------- honest notes
    honest_notes = tuple(dict.fromkeys(notes))[:MAX_VISIBLE_HONEST_NOTES]
    return ExplanationBundle(
        task_map=task_map,
        specialist_assignments=specialist_assignments,
        contribution_map=contribution_map,
        capability_confidence=capability_confidence,
        disagreements=disagreements,
        resource_meter=resource_meter,
        fallback_notices=fallback_notices,
        learning_controls=LearningControls(),
        honest_notes=honest_notes,
    )


__all__ = [
    "CAPTION_LIMITED_HISTORY",
    "CAPTION_UNMEASURED",
    "CAPTION_WELL_MEASURED",
    "MAX_REASON_CHARS",
    "MAX_VISIBLE_ASSIGNMENTS",
    "MAX_VISIBLE_CAPABILITIES",
    "MAX_VISIBLE_CONFIDENCE",
    "MAX_VISIBLE_DISAGREEMENTS",
    "MAX_VISIBLE_FALLBACKS",
    "MAX_VISIBLE_HONEST_NOTES",
    "MAX_VISIBLE_SLOTS",
    "MAX_VISIBLE_STEPS",
    "RESOLUTION_LEFT_AS_ALTERNATIVES",
    "RESOLUTION_UNRESOLVED",
    "TASK_STATUS_ROUTED",
    "TASK_STATUS_UNASSIGNED",
    "TRUNCATION_MARKER",
    "WELL_MEASURED_MIN_SAMPLES",
    "ConfidenceInput",
    "ConfidenceView",
    "ContributionMapView",
    "DisagreementView",
    "ExplanationBundle",
    "FallbackNotice",
    "LearningControls",
    "ResourceView",
    "SpecialistAssignmentView",
    "TaskMapItem",
    "explain_plan",
]
