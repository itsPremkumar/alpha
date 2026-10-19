"""The durable improvement-run record and its state machine (guide §6.3).

Two properties decide the design:

* **A transition is persisted before the next stage starts.** The engine writes
  the record, then appends the journal line, then does the work; a crash
  therefore resumes from the last *completed* checkpoint, and an interrupted
  stage is retried rather than assumed done.
* **A stage key makes retries idempotent.** ``stage_key(run_id, stage, inputs,
  config)`` is recorded when a stage completes; a retry that presents the same
  key is skipped, so a resumed run cannot double-spend a provider request or
  overwrite an artifact unnoticed (guide §6.3, §12.4).

``BLOCKED`` is the "parked, resumable" state — the state a run takes when a
collaborator is absent (no executor/editor wired) instead of pretending to run.
``FAILED``/``CANCELLED``/``BUDGET_EXHAUSTED`` are terminal and durable: they are
outcomes, not missing rows, and a later reader must be able to tell them apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from alpha.harness_improvement.models import (
    CompositionDecision,
    ContrastReport,
    HarnessProposal,
    ImprovementStage,
    ProbeOutcome,
    TaskContract,
    canonical_json,
    sha256_hex,
)
from alpha.harness_improvement.store import utc_now

#: Forward chain of the MVP state machine, in execution order.
STAGE_CHAIN: tuple[ImprovementStage, ...] = (
    ImprovementStage.QUEUED,
    ImprovementStage.SNAPSHOTTING,
    ImprovementStage.BASELINE_RUNNING,
    ImprovementStage.BASELINE_RECORDED,
    ImprovementStage.PROPOSING,
    ImprovementStage.CANDIDATES_VALIDATING,
    ImprovementStage.PROBING,
    ImprovementStage.CONTRASTING,
    ImprovementStage.COMPOSING,
    ImprovementStage.SUCCESSOR_RUNNING,
    ImprovementStage.VERIFYING,
    ImprovementStage.DELIVERABLE_SELECTED,
    ImprovementStage.REFLECTING,
    ImprovementStage.COMPLETED,
)

#: Off-ramps reachable from any non-terminal stage.
OFF_RAMP_STAGES: tuple[ImprovementStage, ...] = (
    ImprovementStage.BLOCKED,
    ImprovementStage.FAILED,
    ImprovementStage.CANCELLED,
    ImprovementStage.BUDGET_EXHAUSTED,
)

TERMINAL_STAGES: frozenset[ImprovementStage] = frozenset(
    {
        ImprovementStage.COMPLETED,
        ImprovementStage.FAILED,
        ImprovementStage.CANCELLED,
        ImprovementStage.BUDGET_EXHAUSTED,
    }
)

#: Explicit transition map. Built from the chain plus the off-ramps, then
#: hand-edited for the one legitimate non-linear edge: a BLOCKED run resumes by
#: re-entering the stage it parked at (or any earlier chain stage, because the
#: engine re-derives the next incomplete stage from the completion ledger).
#: The off-ramps are reachable from BLOCKED too, and that edge is load-bearing:
#: cancellation is *requested* while a run is parked (the only state it can be
#: observed in between calls) and applied when it is next driven, so
#: BLOCKED -> CANCELLED is the whole cancel path — without it the one mutation
#: the router deliberately keeps reachable while disabled could never apply.
TRANSITIONS: dict[ImprovementStage, frozenset[ImprovementStage]] = {stage: frozenset(set(OFF_RAMP_STAGES) | ({STAGE_CHAIN[index + 1]} if index + 1 < len(STAGE_CHAIN) else set())) for index, stage in enumerate(STAGE_CHAIN)}
TRANSITIONS[ImprovementStage.BLOCKED] = frozenset(STAGE_CHAIN) | frozenset(OFF_RAMP_STAGES)
for _terminal in TERMINAL_STAGES:
    TRANSITIONS[_terminal] = frozenset()


class InvalidTransition(RuntimeError):
    """Raised when a caller asks the run to move along an undeclared edge."""

    def __init__(self, current: ImprovementStage, requested: ImprovementStage) -> None:
        super().__init__(f"run cannot move from {current} to {requested}")
        self.current = current
        self.requested = requested


def stage_key(*, run_id: str, stage: ImprovementStage, inputs_hash: str, config_hash: str) -> str:
    """Deterministic idempotency key for one stage attempt (guide §6.3)."""
    payload = canonical_json({"run_id": run_id, "stage": str(stage), "inputs_hash": inputs_hash, "config_hash": config_hash})
    return f"sha256:{sha256_hex(payload)}"


@dataclass(slots=True)
class StageCheckpoint:
    """One completed stage, with the key that made it idempotent."""

    stage: ImprovementStage
    stage_key: str
    entered_at: str
    completed_at: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": str(self.stage),
            "stage_key": self.stage_key,
            "entered_at": self.entered_at,
            "completed_at": self.completed_at,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageCheckpoint:
        return cls(
            stage=ImprovementStage(data["stage"]),
            stage_key=str(data.get("stage_key", "")),
            entered_at=str(data.get("entered_at", "")),
            completed_at=str(data.get("completed_at", "")),
            reason=str(data.get("reason", "")),
        )


@dataclass(slots=True)
class StageEvent:
    """One journal/state transition."""

    stage: ImprovementStage
    at: str
    reason: str = ""
    event: str = "stage.changed"

    def to_dict(self) -> dict[str, Any]:
        return {"event": self.event, "stage": str(self.stage), "at": self.at, "reason": self.reason}


@dataclass(slots=True)
class ImprovementRun:
    """The durable run record: everything a restart needs to resume honestly.

    Mutable on purpose — the engine updates it stage by stage and persists it
    through :meth:`alpha.harness_improvement.store.HarnessImprovementStore.save_run`,
    which serializes a consistent snapshot per write.
    """

    run_id: str
    created_at: str
    updated_at: str
    stage: ImprovementStage
    mode: str
    config_hash: str
    task: TaskContract
    base_bundle_id: str
    created_by: str
    history: list[StageEvent] = field(default_factory=list)
    checkpoints: dict[str, StageCheckpoint] = field(default_factory=dict)
    proposals: list[HarnessProposal] = field(default_factory=list)
    validations: list[dict[str, Any]] = field(default_factory=list)
    probe_outcomes: list[ProbeOutcome] = field(default_factory=list)
    contrasts: list[ContrastReport] = field(default_factory=list)
    composition: CompositionDecision | None = None
    successor_bundle_id: str | None = None
    marginal_improvement: bool | None = None
    deliverable: dict[str, Any] | None = None
    experiences: list[str] = field(default_factory=list)
    budget_state: dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    cancel_reason: str = ""
    blocked_reason: str = ""
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    alpha_runtime_version: str = ""
    tool_schema_version: str = ""
    policy_version: str = ""
    source_task_type: str | None = None

    # -- transitions ---------------------------------------------------------
    @property
    def terminal(self) -> bool:
        return self.stage in TERMINAL_STAGES

    @property
    def resumable(self) -> bool:
        """A BLOCKED run can resume; a terminal run cannot."""
        return self.stage is ImprovementStage.BLOCKED or (self.stage not in TERMINAL_STAGES and self.stage is not ImprovementStage.COMPLETED)

    def can_transition(self, stage: ImprovementStage) -> bool:
        return stage in TRANSITIONS.get(self.stage, frozenset())

    def transition(self, stage: ImprovementStage, *, reason: str = "", event: str = "stage.changed") -> None:
        """Move the run to *stage*, recording why. Refuses an undeclared edge."""
        if not self.can_transition(stage):
            raise InvalidTransition(self.stage, stage)
        self.stage = stage
        self.updated_at = utc_now()
        if stage is ImprovementStage.BLOCKED:
            self.blocked_reason = reason
        self.history.append(StageEvent(stage=stage, at=self.updated_at, reason=reason, event=event))

    def completed_stage(self, stage: ImprovementStage, key: str) -> bool:
        """True when this exact stage attempt already completed (idempotency)."""
        checkpoint = self.checkpoints.get(str(stage))
        return checkpoint is not None and checkpoint.stage_key == key

    def mark_complete(self, stage: ImprovementStage, key: str, *, entered_at: str | None = None, reason: str = "") -> None:
        now = utc_now()
        self.checkpoints[str(stage)] = StageCheckpoint(stage=stage, stage_key=key, entered_at=entered_at or now, completed_at=now, reason=reason)
        self.updated_at = now

    def next_incomplete_stage(self) -> ImprovementStage:
        """The first chain stage with no checkpoint — where a resume picks up."""
        for stage in STAGE_CHAIN:
            if stage is ImprovementStage.QUEUED:
                continue
            if str(stage) not in self.checkpoints:
                return stage
        return ImprovementStage.COMPLETED

    def add_note(self, note: str) -> None:
        self.notes.append(note)

    # -- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stage": str(self.stage),
            "mode": self.mode,
            "config_hash": self.config_hash,
            "task": self.task.to_dict(),
            "base_bundle_id": self.base_bundle_id,
            "successor_bundle_id": self.successor_bundle_id,
            "marginal_improvement": self.marginal_improvement,
            "created_by": self.created_by,
            "history": [h.to_dict() for h in self.history],
            "checkpoints": {key: cp.to_dict() for key, cp in self.checkpoints.items()},
            "proposals": [p.to_dict() for p in self.proposals],
            "validations": self.validations,
            "probe_outcomes": [p.to_dict() for p in self.probe_outcomes],
            "contrasts": [c.to_dict() for c in self.contrasts],
            "composition": self.composition.to_dict() if self.composition else None,
            "deliverable": self.deliverable,
            "experiences": self.experiences,
            "budget_state": self.budget_state,
            "cancel_requested": self.cancel_requested,
            "cancel_reason": self.cancel_reason,
            "blocked_reason": self.blocked_reason,
            "error": self.error,
            "notes": self.notes,
            "compatibility": {
                "alpha_runtime_version": self.alpha_runtime_version,
                "tool_schema_version": self.tool_schema_version,
                "policy_version": self.policy_version,
            },
            "source_task_type": self.source_task_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImprovementRun:
        compatibility = data.get("compatibility", {})
        return cls(
            run_id=str(data["run_id"]),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            stage=ImprovementStage(data.get("stage", ImprovementStage.QUEUED)),
            mode=str(data.get("mode", "sequential")),
            config_hash=str(data.get("config_hash", "")),
            task=TaskContract.from_dict(data.get("task", {})),
            base_bundle_id=str(data.get("base_bundle_id", "")),
            successor_bundle_id=data.get("successor_bundle_id"),
            marginal_improvement=data.get("marginal_improvement"),
            created_by=str(data.get("created_by", "")),
            history=[StageEvent(stage=ImprovementStage(h["stage"]), at=str(h.get("at", "")), reason=str(h.get("reason", "")), event=str(h.get("event", "stage.changed"))) for h in data.get("history", [])],
            checkpoints={key: StageCheckpoint.from_dict(value) for key, value in data.get("checkpoints", {}).items()},
            proposals=[HarnessProposal.from_dict(p) for p in data.get("proposals", [])],
            validations=list(data.get("validations", [])),
            probe_outcomes=[ProbeOutcome.from_dict(p) for p in data.get("probe_outcomes", [])],
            contrasts=[ContrastReport.from_dict(c) for c in data.get("contrasts", [])],
            composition=CompositionDecision.from_dict(data["composition"]) if data.get("composition") else None,
            deliverable=data.get("deliverable"),
            experiences=[str(e) for e in data.get("experiences", [])],
            budget_state=dict(data.get("budget_state", {})),
            cancel_requested=bool(data.get("cancel_requested", False)),
            cancel_reason=str(data.get("cancel_reason", "")),
            blocked_reason=str(data.get("blocked_reason", "")),
            error=data.get("error"),
            notes=[str(n) for n in data.get("notes", [])],
            alpha_runtime_version=str(compatibility.get("alpha_runtime_version", "")),
            tool_schema_version=str(compatibility.get("tool_schema_version", "")),
            policy_version=str(compatibility.get("policy_version", "")),
            source_task_type=data.get("source_task_type"),
        )


def new_run(
    *,
    run_id: str,
    task: TaskContract,
    base_bundle_id: str,
    config_hash: str,
    created_by: str = "harness_improvement",
    mode: str = "sequential",
) -> ImprovementRun:
    """Create a freshly queued run record."""
    now = utc_now()
    return ImprovementRun(
        run_id=run_id,
        created_at=now,
        updated_at=now,
        stage=ImprovementStage.QUEUED,
        mode=mode,
        config_hash=config_hash,
        task=task,
        base_bundle_id=base_bundle_id,
        created_by=created_by,
        source_task_type=task.task_type if task.task_type else None,
    )
