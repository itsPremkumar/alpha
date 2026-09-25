"""Closed, emit-only reasoning event taxonomy.

The plan's section 26 taxonomy is represented as serializable typed records.
This module does not import Alpha's in-process bus, the durable run-event store,
or the Gateway SSE layer.  Publication is a reported integration patch because
new durable run-event types must be added to the runtime catalog, constants,
JSON contract, documentation, and contract test together.

Action intents are untrusted proposals.  Only records stamped by the existing
runtime authority may claim approval, execution, verification, checkpointing, or
a terminal run state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alpha.reasoning.models import ClampedModel, ReasoningMode, VerificationStatus
from alpha.reasoning.policy import TaskClass
from alpha.reasoning.summary import assert_no_private_reasoning

__all__ = [
    "EmitterAuthority",
    "ReasoningActionIntent",
    "ReasoningEvent",
    "ReasoningEventStatus",
    "ReasoningEventType",
    "parse_event",
    "serialize_event",
]


class ReasoningEventType(StrEnum):
    STARTED = "started"
    POLICY_SELECTED = "policy.selected"
    PLAN_CREATED = "plan.created"
    STEP_STARTED = "step.started"
    HYPOTHESIS_CREATED = "hypothesis.created"
    ACTION_PROPOSED = "action.proposed"
    ACTION_APPROVED = "action.approved"
    ACTION_EXECUTED = "action.executed"
    OBSERVATION_RECEIVED = "observation.received"
    EVIDENCE_ADDED = "evidence.added"
    CONTRADICTION_DETECTED = "contradiction.detected"
    VERIFICATION_STARTED = "verification.started"
    VERIFICATION_COMPLETED = "verification.completed"
    REFLECTION_STARTED = "reflection.started"
    REFLECTION_COMPLETED = "reflection.completed"
    REPLAN_STARTED = "replan.started"
    REPLAN_COMPLETED = "replan.completed"
    BRANCH_CREATED = "branch.created"
    BRANCH_PRUNED = "branch.pruned"
    CONSENSUS_STARTED = "consensus.started"
    CONSENSUS_COMPLETED = "consensus.completed"
    CONTEXT_COMPACTED = "context.compacted"
    BUDGET_WARNING = "budget.warning"
    BUDGET_EXHAUSTED = "budget.exhausted"
    CHECKPOINT_CREATED = "checkpoint.created"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class ReasoningEventStatus(StrEnum):
    INFO = "info"
    STARTED = "started"
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTED = "executed"
    RECEIVED = "received"
    ADDED = "added"
    DETECTED = "detected"
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED = "rejected"
    WARNING = "warning"
    EXHAUSTED = "exhausted"
    CHECKPOINTED = "checkpointed"
    FAILED = "failed"
    BLOCKED = "blocked"


class EmitterAuthority(StrEnum):
    REASONING_PLANE = "reasoning_plane"
    EXISTING_RUNTIME = "existing_runtime"


class ReasoningActionIntent(BaseModel):
    """Untrusted action proposal; never an authorization or execution result."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str | None = Field(default=None, max_length=256)
    purpose: str = Field(min_length=1, max_length=2000)
    expected_observation: str = Field(min_length=1, max_length=2000)
    requires_approval: bool = False
    arguments_digest: str | None = Field(default=None, max_length=256)
    trust: Literal["untrusted"] = "untrusted"


_EXISTING_RUNTIME_ONLY = {
    ReasoningEventType.ACTION_APPROVED,
    ReasoningEventType.ACTION_EXECUTED,
    ReasoningEventType.VERIFICATION_COMPLETED,
    ReasoningEventType.CHECKPOINT_CREATED,
    ReasoningEventType.COMPLETED,
    ReasoningEventType.BLOCKED,
    ReasoningEventType.FAILED,
}


class ReasoningEvent(ClampedModel):
    """One serializable event record; publication remains a host responsibility."""

    event_id: str = Field(default_factory=lambda: f"reasoning-event-{uuid4().hex}", min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    event_type: ReasoningEventType
    status: ReasoningEventStatus = ReasoningEventStatus.INFO
    authority: EmitterAuthority = EmitterAuthority.REASONING_PLANE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    step_id: str | None = Field(default=None, max_length=128)
    agent_id: str | None = Field(default=None, max_length=128)
    model_id: str | None = Field(default=None, max_length=256)
    provider: str | None = Field(default=None, max_length=128)
    strategy: str | None = Field(default=None, max_length=128)
    reasoning_mode: ReasoningMode | None = None
    policy_class: TaskClass | None = None
    plan_id: str | None = Field(default=None, max_length=128)
    plan_step_ids: tuple[str, ...] = Field(default=(), max_length=200)
    hypothesis_id: str | None = Field(default=None, max_length=128)
    action: ReasoningActionIntent | None = None
    observation_id: str | None = Field(default=None, max_length=128)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=200)
    contradiction_id: str | None = Field(default=None, max_length=128)
    verification_id: str | None = Field(default=None, max_length=128)
    verification_status: VerificationStatus | None = None
    verification_score: float | None = None
    reflection_id: str | None = Field(default=None, max_length=128)
    replan_id: str | None = Field(default=None, max_length=128)
    branch_id: str | None = Field(default=None, max_length=128)
    consensus_id: str | None = Field(default=None, max_length=128)
    budget_dimension: str | None = Field(default=None, max_length=64)
    budget_remaining: float | None = None
    checkpoint_id: str | None = Field(default=None, max_length=256)
    state_checksum: str | None = Field(default=None, max_length=256)
    plan_revision: str | None = Field(default=None, max_length=256)
    action_signature: str | None = Field(default=None, max_length=512)
    tool_args_digest: str | None = Field(default=None, max_length=256)
    error_digest: str | None = Field(default=None, max_length=256)
    public_message: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _validate_contract(self) -> Self:
        if self.verification_score is not None:
            self.clamp_field("verification_score", 0.0, 1.0)
        if self.budget_remaining is not None:
            self.clamp_field("budget_remaining", 0.0, 1e12)
        if self.event_type in {ReasoningEventType.ACTION_PROPOSED, ReasoningEventType.ACTION_APPROVED, ReasoningEventType.ACTION_EXECUTED}:
            if self.action is None:
                raise ValueError(f"{self.event_type.value} requires an action intent")
        if self.event_type is ReasoningEventType.OBSERVATION_RECEIVED and not self.observation_id:
            raise ValueError("observation.received requires observation_id")
        if self.event_type is ReasoningEventType.EVIDENCE_ADDED and not self.evidence_ids:
            raise ValueError("evidence.added requires at least one evidence id")
        if self.event_type is ReasoningEventType.VERIFICATION_COMPLETED:
            if not self.verification_id or self.verification_status is None:
                raise ValueError("verification.completed requires verification_id and verification_status")
        if self.event_type in {ReasoningEventType.BUDGET_WARNING, ReasoningEventType.BUDGET_EXHAUSTED} and not self.budget_dimension:
            raise ValueError(f"{self.event_type.value} requires budget_dimension")
        if self.event_type is ReasoningEventType.CHECKPOINT_CREATED and not self.checkpoint_id:
            raise ValueError("checkpoint.created requires checkpoint_id")
        if self.event_type in _EXISTING_RUNTIME_ONLY and self.authority is not EmitterAuthority.EXISTING_RUNTIME:
            raise ValueError(f"{self.event_type.value} may only be emitted by the existing runtime authority")
        assert_no_private_reasoning(self)
        return self


def serialize_event(event: ReasoningEvent) -> str:
    """Serialize one record for a host publisher."""

    return event.model_dump_json()


def parse_event(payload: str) -> ReasoningEvent:
    """Validate a serialized record before host publication."""

    return ReasoningEvent.model_validate_json(payload)
