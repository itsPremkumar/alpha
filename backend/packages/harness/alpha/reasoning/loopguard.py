"""Pure loop/thrash signals and an ordered intervention recommendation.

Alpha already owns live loop enforcement in
``alpha.agents.middlewares.loop_detection_middleware`` and fleet supervision in
``alpha.supervision.watchdog``.  This module does not import, instantiate, or
replace either subsystem.  It derives the plan's reasoning-state signals from
an event stream and returns a recommendation that the host may feed into the
existing owners.

Hard attempt/reflection/replan caps and cooldown make an unbounded retry loop
unrepresentable in the returned decision; they do not themselves stop a run.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alpha.reasoning.events import ReasoningEvent
from alpha.reasoning.models import ClampedModel

__all__ = [
    "LoopAnalysis",
    "LoopDecision",
    "LoopGuardCaps",
    "LoopIntervention",
    "LoopObservation",
    "LoopSignal",
    "choose_intervention",
    "detect_loop_signals",
    "evaluate_loopguard",
    "project_loop_observations",
]


class LoopSignal(StrEnum):
    SAME_ACTION = "same_action"
    SAME_TOOL_ARGS = "same_tool_args"
    SAME_ERROR = "same_error"
    SAME_HYPOTHESIS = "same_hypothesis"
    PLAN_UNCHANGED = "plan_unchanged"
    NO_NEW_EVIDENCE = "no_new_evidence"
    NO_VERIFICATION_IMPROVEMENT = "no_verification_improvement"


class LoopIntervention(StrEnum):
    WARNING = "warning"
    CHANGE_CONTEXT = "change_context"
    REQUEST_DISCRIMINATING_EVIDENCE = "request_discriminating_evidence"
    CHANGE_STRATEGY = "change_strategy"
    CHANGE_MODEL = "change_model"
    DELEGATE_CRITIC = "delegate_critic"
    TERMINATE = "terminate"
    BLOCK = "block"


class LoopGuardCaps(ClampedModel):
    enabled: bool = False
    signal_threshold: int = Field(default=2)
    max_attempts: int = Field(default=8)
    max_reflections: int = Field(default=3)
    max_replans: int = Field(default=3)
    cooldown_seconds: float = Field(default=2.0)
    terminal_signal_count: int = Field(default=7)
    block_on_exhaustion: bool = True

    @model_validator(mode="after")
    def _clamp_values(self) -> Self:
        self.clamp_field("signal_threshold", 2, 100, integer=True)
        self.clamp_field("max_attempts", 1, 1000, integer=True)
        self.clamp_field("max_reflections", 0, 1000, integer=True)
        self.clamp_field("max_replans", 0, 1000, integer=True)
        self.clamp_field("cooldown_seconds", 0.0, 3600.0)
        self.clamp_field("terminal_signal_count", 1, 7, integer=True)
        return self


class LoopObservation(ClampedModel):
    sequence: int = Field(ge=0)
    created_at: datetime
    action_signature: str | None = Field(default=None, max_length=512)
    tool_args_digest: str | None = Field(default=None, max_length=256)
    error_digest: str | None = Field(default=None, max_length=256)
    hypothesis_id: str | None = Field(default=None, max_length=128)
    plan_revision: str | None = Field(default=None, max_length=256)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=200)
    verification_score: float | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _clamp_score(self) -> LoopObservation:
        if self.verification_score is not None:
            self.clamp_field("verification_score", 0.0, 1.0)
        return self


class LoopAnalysis(BaseModel):
    disabled: bool
    signals: tuple[LoopSignal, ...] = ()
    repeat_counts: dict[LoopSignal, int] = Field(default_factory=dict)
    sample_size: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class LoopDecision(BaseModel):
    intervention: LoopIntervention | None
    analysis: LoopAnalysis
    terminal: bool
    cooldown_active: bool
    reason: str

    model_config = ConfigDict(extra="forbid", frozen=True)


def project_loop_observations(events: Sequence[ReasoningEvent]) -> tuple[LoopObservation, ...]:
    """Project typed events into the bounded signal stream used by this module."""

    ordered = sorted(events, key=lambda event: (event.created_at, event.event_id))
    observations: list[LoopObservation] = []
    for sequence, event in enumerate(ordered):
        action = event.action
        action_signature = event.action_signature or (action.tool_name if action is not None else None)
        if not any(
            (
                action_signature,
                event.tool_args_digest,
                event.error_digest,
                event.hypothesis_id,
                event.plan_revision,
                event.evidence_ids,
                event.verification_score is not None,
            )
        ):
            continue
        observations.append(
            LoopObservation(
                sequence=sequence,
                created_at=event.created_at,
                action_signature=action_signature,
                tool_args_digest=event.tool_args_digest,
                error_digest=event.error_digest,
                hypothesis_id=event.hypothesis_id,
                plan_revision=event.plan_revision,
                evidence_ids=event.evidence_ids,
                verification_score=event.verification_score,
            )
        )
    return tuple(observations)


def _max_repeat(values: Sequence[str | None]) -> int:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    return max(counts.values(), default=0)


def detect_loop_signals(
    observations: Sequence[LoopObservation],
    caps: LoopGuardCaps = LoopGuardCaps(),
) -> LoopAnalysis:
    """Derive pure loop signals over the most recent bounded window."""

    if not caps.enabled:
        return LoopAnalysis(disabled=True, sample_size=len(observations))
    window = list(observations[-max(3, caps.signal_threshold + 1) :])
    threshold = caps.signal_threshold
    signals: list[LoopSignal] = []
    counts: dict[LoopSignal, int] = {}

    def repeated(signal: LoopSignal, values: Sequence[str | None]) -> None:
        count = _max_repeat(values)
        counts[signal] = count
        if count >= threshold:
            signals.append(signal)

    repeated(LoopSignal.SAME_ACTION, [item.action_signature for item in window])
    repeated(LoopSignal.SAME_TOOL_ARGS, [item.tool_args_digest for item in window])
    repeated(LoopSignal.SAME_ERROR, [item.error_digest for item in window])
    repeated(LoopSignal.SAME_HYPOTHESIS, [item.hypothesis_id for item in window])

    plan_revisions = [item.plan_revision for item in window if item.plan_revision]
    counts[LoopSignal.PLAN_UNCHANGED] = len(plan_revisions)
    if len(plan_revisions) >= threshold and len(set(plan_revisions)) == 1:
        signals.append(LoopSignal.PLAN_UNCHANGED)

    evidence_sets = [frozenset(item.evidence_ids) for item in window]
    cumulative: set[str] = set()
    growth = False
    for evidence_set in evidence_sets:
        expanded = cumulative | evidence_set
        if expanded != cumulative:
            growth = True
        cumulative = expanded
    counts[LoopSignal.NO_NEW_EVIDENCE] = len(cumulative)
    if len(window) >= threshold + 1 and not growth:
        signals.append(LoopSignal.NO_NEW_EVIDENCE)

    scores = [item.verification_score for item in window if item.verification_score is not None]
    counts[LoopSignal.NO_VERIFICATION_IMPROVEMENT] = len(scores)
    if len(scores) >= threshold and all(later <= earlier for earlier, later in zip(scores, scores[1:])):
        signals.append(LoopSignal.NO_VERIFICATION_IMPROVEMENT)

    return LoopAnalysis(
        disabled=False,
        signals=tuple(dict.fromkeys(signals)),
        repeat_counts=counts,
        sample_size=len(window),
    )


def choose_intervention(signal_count: int, caps: LoopGuardCaps = LoopGuardCaps()) -> LoopIntervention:
    """Map signal count to the plan's ordered intervention ladder."""

    if signal_count <= 0:
        raise ValueError("signal_count must be positive")
    if signal_count >= caps.terminal_signal_count:
        return LoopIntervention.TERMINATE
    if signal_count >= 6:
        return LoopIntervention.DELEGATE_CRITIC
    if signal_count >= 5:
        return LoopIntervention.CHANGE_MODEL
    if signal_count >= 4:
        return LoopIntervention.CHANGE_STRATEGY
    if signal_count >= 3:
        return LoopIntervention.REQUEST_DISCRIMINATING_EVIDENCE
    if signal_count >= 2:
        return LoopIntervention.CHANGE_CONTEXT
    return LoopIntervention.WARNING


def evaluate_loopguard(
    observations: Sequence[LoopObservation],
    *,
    attempts: int,
    reflections: int,
    replans: int,
    caps: LoopGuardCaps = LoopGuardCaps(),
    now: float | None = None,
    last_intervention_at: float | None = None,
) -> LoopDecision:
    """Return an ordered, capped intervention recommendation."""

    analysis = detect_loop_signals(observations, caps)
    if analysis.disabled:
        return LoopDecision(
            intervention=None,
            analysis=analysis,
            terminal=False,
            cooldown_active=False,
            reason="loop-guard policy is default-off; existing loop detection remains authoritative",
        )
    if not analysis.signals:
        return LoopDecision(
            intervention=None,
            analysis=analysis,
            terminal=False,
            cooldown_active=False,
            reason="no loop signal fired",
        )

    exhausted = attempts >= caps.max_attempts or reflections >= caps.max_reflections or replans >= caps.max_replans
    if exhausted:
        intervention = LoopIntervention.BLOCK if caps.block_on_exhaustion else LoopIntervention.TERMINATE
        return LoopDecision(
            intervention=intervention,
            analysis=analysis,
            terminal=True,
            cooldown_active=False,
            reason=(f"hard cap reached (attempts={attempts}/{caps.max_attempts}, reflections={reflections}/{caps.max_reflections}, replans={replans}/{caps.max_replans})"),
        )

    intervention = choose_intervention(len(analysis.signals), caps)
    if intervention is LoopIntervention.TERMINATE:
        return LoopDecision(
            intervention=intervention,
            analysis=analysis,
            terminal=True,
            cooldown_active=False,
            reason="terminal signal threshold reached",
        )
    if last_intervention_at is not None and now is not None:
        elapsed = max(0.0, now - last_intervention_at)
        if elapsed < caps.cooldown_seconds:
            return LoopDecision(
                intervention=None,
                analysis=analysis,
                terminal=False,
                cooldown_active=True,
                reason=f"intervention cooldown active ({elapsed:.3f}s < {caps.cooldown_seconds:.3f}s)",
            )
    return LoopDecision(
        intervention=intervention,
        analysis=analysis,
        terminal=False,
        cooldown_active=False,
        reason=f"{len(analysis.signals)} loop signal(s) fired; selected {intervention.value}",
    )
