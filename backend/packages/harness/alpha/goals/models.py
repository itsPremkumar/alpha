from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

NonEmptyString = Annotated[str, Field(min_length=1, pattern=r"\S")]
Timestamp = Annotated[float, Field(ge=0, allow_inf_nan=False)]
GoalStatus = Literal["active", "achieved", "abandoned"]
PlanStatus = Literal["draft", "approved", "superseded"]
AttemptStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    id: NonEmptyString
    owner_id: NonEmptyString
    created_at: Timestamp
    updated_at: Timestamp

    @model_validator(mode="after")
    def _validate_timestamps(self):
        if self.updated_at < self.created_at:
            raise ValueError("updated_at precedes created_at")
        return self


class GoalContract(_Record):
    objective: NonEmptyString
    status: GoalStatus
    #: Links a child contract to the contract it was decomposed from. ``""``
    #: for a root contract, which is what every pre-tree contract is.
    parent_contract_id: str = ""
    #: Links the contract to a :class:`~alpha.mission.goalloop.goal_tree.GoalNode`
    #: in the goal tree. ``""`` when the contract is not tree-backed.
    node_id: str = ""


class PlanVersion(_Record):
    contract_id: NonEmptyString
    version: Annotated[int, Field(ge=1)]
    content: dict[str, JsonValue]
    status: PlanStatus
    #: The goal tree node this plan was compiled for, or ``""``.
    node_id: str = ""
    #: Which execution wave this version corresponds to, or ``-1`` when the
    #: plan is not wave-scoped.
    wave_index: int = Field(default=-1, ge=-1)


class TaskAttempt(_Record):
    plan_id: NonEmptyString
    intent: NonEmptyString
    status: AttemptStatus
