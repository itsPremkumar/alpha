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


class PlanVersion(_Record):
    contract_id: NonEmptyString
    version: Annotated[int, Field(ge=1)]
    content: dict[str, JsonValue]
    status: PlanStatus


class TaskAttempt(_Record):
    plan_id: NonEmptyString
    intent: NonEmptyString
    status: AttemptStatus
