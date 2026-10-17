"""The canonical task contract every Alpha orchestration engine projects onto.

Why this exists
---------------
Alpha grew **seven** schedulers and **three** incompatible task vocabularies:

* ``alpha.workflow`` (``NodeStatus``): pending, ready, running, waiting,
  succeeded, failed, retrying, skipped, cancelled, suspended, compensating,
  aborted
* ``alpha.swarm`` (``SwarmPlan.status``): a **bare string** carrying
  planning, running, paused, aggregating, verifying, partial_success,
  completed, failed, cancelled, budget_exhausted, stalled
* ``alpha.graph`` (``TaskStatus``): pending, ready, queued, running,
  waiting_approval, blocked, succeeded, failed, cancelled, skipped

Nothing translates between them. A run started in one engine cannot be
observed, budgeted, resumed or verified in another, and the source plan's own
§31 warns against exactly this — *"multiple overlapping orchestration frameworks
running the same task"*.

This module is the **neutral projection target**, not a fourth vocabulary and
not a scheduler. It has no scheduler, no store, no clock and no I/O: it is the
shape an engine's own model is *translated into* so a task can be described,
handed off, budgeted and verified identically whichever engine ran it.

Four rules govern everything here
---------------------------------

1. **No engine vocabulary is re-declared.** ``alpha.tasks.contracts`` owns a
   deliberately narrow union vocabulary, and
   :mod:`alpha.tasks.adapters` holds the per-engine maps. Adding a status to an
   engine does not edit this file.
2. **An unmappable status is a refusal, never a default.** A projection that
   guesses ``pending`` for a status it does not recognise turns an unfamiliar
   state into a task that looks ready to run. :class:`UnmappableStatus` raises
   with the engine and the offending value named.
3. **Engine-specific vocabulary is reported, not erased.** A contract whose
   native status was ``compensating`` keeps that in
   :attr:`TaskContract.native_status`. Round-tripping a task therefore returns
   the engine it came from, so an adapter is lossless rather than lossy and
   pretending otherwise.
4. **This is not an engine's source of truth.** ``RunManager`` remains the sole
   run-lifecycle owner, ``alpha.mission.acceptance`` remains the only producer
   of a terminal *passed*, and ``alpha.errors.registry`` remains the sole owner
   of error codes. Nothing here writes a run, a verdict or an error code.

Bounds are **refused, not clamped**: ``max_attempts=0`` raises rather than
becoming 1, matching ``GraphBudget`` and the deep-agent channel ceiling. A
silently-adjusted limit is a limit the operator believes is in force and is
not.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "CONTRACT_VERSION",
    "ContractValidationError",
    "UnmappableStatus",
    "TaskContract",
    "TaskContractBudget",
    "TaskContractStatus",
    "TaskContractType",
    "TERMINAL_TASK_CONTRACT_STATUSES",
    "SATISFIED_TASK_CONTRACT_STATUSES",
    "UNBOUNDED",
]


class ContractValidationError(ValueError):
    """The contract was built with values the runtime must refuse to guess at.

    Separate from ``alpha.graph.models.ModelValidationError`` on purpose: this
    is the neutral package's own refusal, and a caller that catches the graph
    plane's error would otherwise be tempted to keep an invalid graph-plane
    task when only the projection was bad.
    """


class UnmappableStatus(ContractValidationError):
    """An engine reported a status the neutral vocabulary does not cover.

    Raised instead of defaulting, because the alternative is silent data loss
    on the one boundary whose entire job is to lose nothing.
    """

    def __init__(self, engine: str, value: object) -> None:
        self.engine = engine
        self.value = value
        super().__init__(f"{engine} reported task status {value!r}, which has no entry in the canonical contract vocabulary; add it to alpha.tasks.adapters rather than guessing a default")


#: The contract schema version. Bump when a field is removed or its meaning
#: changes; additive optional fields do not need a bump.
CONTRACT_VERSION: Final[int] = 1


class TaskContractStatus(StrEnum):
    """The neutral union of every engine's task status.

    Deliberately the **narrowest** set that all three engines can express, not
    their union. A union would let this file drift toward whichever engine grew
    last; a narrow common core is what makes a projection lossless.

    The three vocabularies really do differ — ``completed`` vs ``succeeded``,
    ``queued`` vs ``dispatched``, ``blocked`` (graph only) vs ``stalled``
    (swarm only) — which is exactly why
    :attr:`TaskContract.native_status` exists and why an unmapped value
    refuses rather than defaulting.
    """

    PENDING = "pending"
    READY = "ready"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


TERMINAL_TASK_CONTRACT_STATUSES: Final[frozenset[TaskContractStatus]] = frozenset({TaskContractStatus.SUCCEEDED, TaskContractStatus.FAILED, TaskContractStatus.SKIPPED, TaskContractStatus.CANCELLED})

#: Statuses that satisfy a prerequisite for readiness. ``SKIPPED`` is excluded
#: for the reason ``alpha.graph`` gives: a skipped task produced nothing, so
#: treating it as satisfied would run downstream work on a hole.
SATISFIED_TASK_CONTRACT_STATUSES: Final[frozenset[TaskContractStatus]] = frozenset({TaskContractStatus.SUCCEEDED})


class TaskContractType(StrEnum):
    """Neutral task kind.

    Reuses ``alpha.graph.models.TaskType``'s values rather than inventing a
    seventh set: ``code``/``research``/``review``/``test``/``tool`` are already
    the vocabulary three subsystems read. Engine-specific kinds are carried in
    :attr:`TaskContract.native_type` instead of widening this enum.
    """

    CODE = "code"
    RESEARCH = "research"
    REVIEW = "review"
    TEST = "test"
    TOOL = "tool"
    SYNTHESIS = "synthesis"
    BROWSER = "browser"
    APPROVAL = "approval"


#: Sentinel for "this axis is unbounded", distinct from ``0`` and from ``None``.
#:
#: The source plan is explicit that ``max_cost_usd: 0`` must mean *paid calls
#: are blocked*, never *unknown*. So this contract refuses ``0`` on every axis
#: and forces a caller to say which of the three things it means: unbounded,
#: zero-allowed, or simply not stated.
UNBOUNDED: Final[str] = "unbounded"


class TaskContractBudget(BaseModel):
    """Ceilings for one task. Every field is a ceiling, never a target.

    Two rules the rest of the repository already depends on:

    * **Bounds are refused, not clamped.** ``max_attempts=0`` raises. A limit
      nobody chose is not a limit.
    * **``None`` is not a number.** A caller that has not decided a ceiling
      passes ``None`` (unstated) or :data:`UNBOUNDED`. Treating a missing
      ceiling as a spendable one is how an unbudgeted run reaches a paid
      provider.

    The axes are typed ``int | Literal["unbounded"] | None`` rather than as a
    plain ``int`` because the string sentinel has to survive validation — a
    typed sentinel that Pydantic rejects would push every caller back to
    encoding "unbounded" as ``0`` or ``-1``, which is the ambiguity this
    contract exists to remove.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_model_calls: int | Literal["unbounded"] | None = Field(default=None, description="Ceiling on model calls for this task. None = unstated. UNBOUNDED = explicitly unbounded.")
    max_tool_calls: int | Literal["unbounded"] | None = Field(default=None, description="Ceiling on tool calls for this task. None = unstated. UNBOUNDED = explicitly unbounded.")
    max_tokens: int | Literal["unbounded"] | None = Field(default=None, description="Ceiling on total tokens for this task. None = unstated. UNBOUNDED = explicitly unbounded.")
    max_cost_usd: float | Literal["unbounded"] | None = Field(default=None, description="Ceiling in USD. 0.0 is a real ceiling meaning paid calls are blocked; it is never a stand-in for unknown.")
    max_attempts: int | None = Field(default=None, description="Ceiling on execution attempts. None = unstated.")
    max_wall_seconds: float | Literal["unbounded"] | None = Field(default=None, description="Ceiling on wall-clock time. None = unstated. UNBOUNDED = explicitly unbounded.")

    @field_validator("max_model_calls", "max_tool_calls", "max_tokens", "max_attempts", mode="before")
    @classmethod
    def _positive_int_or_unbounded(cls, value: Any, info: Any) -> Any:
        """Refuse a non-positive bound *before* Pydantic coerces it.

        ``mode="before"`` is load-bearing: an ``after`` validator never sees the
        caller's value, and a ``ge=1`` field constraint would replace the honest
        message with Pydantic's generic one — so ``max_attempts=0`` would still
        raise, but the caller would be told "greater than or equal to 1"
        instead of being told the bound is refused rather than clamped, and which
        alternative to use.
        """
        if value is None or value == UNBOUNDED:
            return value
        if isinstance(value, bool):
            raise ContractValidationError(f"{info.field_name}={value!r} is refused: bool is not a number of calls. Use an int, or None / {UNBOUNDED!r} to leave the axis unstated or unbounded.")
        if not isinstance(value, int):
            raise ContractValidationError(f"{info.field_name} must be a positive int, None (unstated) or {UNBOUNDED!r}, got {value!r}")
        if value <= 0:
            raise ContractValidationError(f"{info.field_name}={value!r} is refused rather than clamped: use {UNBOUNDED!r} for an explicit unbounded axis, or 1+ for a real ceiling")
        return value

    @field_validator("max_cost_usd", "max_wall_seconds", mode="before")
    @classmethod
    def _non_negative_number_or_unbounded(cls, value: Any, info: Any) -> Any:
        if value is None or value == UNBOUNDED:
            return value
        if isinstance(value, bool):
            raise ContractValidationError(f"{info.field_name}={value!r} is refused: bool is not a number. Use a number, or None / {UNBOUNDED!r}.")
        if not isinstance(value, (int, float)):
            raise ContractValidationError(f"{info.field_name} must be a number, None (unstated) or {UNBOUNDED!r}, got {value!r}")
        if value < 0:
            raise ContractValidationError(f"{info.field_name}={value!r} is negative, which is not a ceiling")
        return value


class TaskContract(BaseModel):
    """One unit of work, described the same way whichever engine runs it.

    Every field here is either **required for attribution** or explicitly
    **optional and honest about being optional**. A contract that omits the
    owner, the run or the acceptance criteria would let "agent sent a message"
    read as completion, which is the exact failure the source plan's §2 rule 4
    forbids.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=CONTRACT_VERSION, description="Contract schema version. Read by a consumer to decide whether it understands every field.")
    task_id: str = Field(min_length=1, description="Engine-local task identifier, unique within the run.")
    run_id: str = Field(min_length=1, description="Owning run. A task with no owning run cannot be attributed, costed or recovered.")
    parent_task_id: str | None = Field(default=None, description="Delegating task, or None for a root task.")
    title: str = Field(min_length=1, description="Short human-readable summary.")
    objective: str = Field(default="", description="What this task resolves. Empty is allowed for a mechanical node; it is not the same as a verified objective.")

    task_type: TaskContractType = Field(default=TaskContractType.TOOL, description="Neutral task kind.")
    native_type: str | None = Field(default=None, description="The engine's own kind string, preserved verbatim. The neutral enum is deliberately coarse, so this is what keeps a projection lossless.")
    status: TaskContractStatus = Field(default=TaskContractStatus.PENDING, description="Neutral status. The engine's own status is preserved in native_status.")
    engine: str = Field(default="unknown", min_length=1, description="Which engine produced this contract, so a projection can be routed back.")
    native_status: str | None = Field(default=None, description="The engine's own status string, preserved verbatim so the projection is lossless.")

    depends_on: list[str] = Field(default_factory=list, description="Prerequisite task ids within the same run.")
    acceptance_criteria: list[str] = Field(default_factory=list, description="What will be checked before this task counts as done. Empty means undeclared, not satisfied.")
    expected_artifacts: list[str] = Field(default_factory=list, description="Artifact ids or references this task is expected to produce.")
    scope_paths: list[str] = Field(default_factory=list, description="Filesystem paths this task may write. Two concurrent tasks with an overlapping path must be serialized or isolated.")
    eligible_capabilities: list[str] = Field(default_factory=list, description="Capability names an assignee must declare.")

    budget: TaskContractBudget = Field(default_factory=TaskContractBudget, description="Ceilings for this task.")
    priority: int = Field(default=50, ge=0, description="Higher runs first within a ready set.")
    attempt_count: int = Field(default=0, ge=0, description="Attempts already made.")
    max_attempts: int = Field(default=2, description="Attempt ceiling. Zero is refused, not clamped.")

    @field_validator("max_attempts", mode="before")
    @classmethod
    def _attempt_ceiling_is_refused_not_clamped(cls, value: Any) -> Any:
        """``ge=1`` is deliberately *not* used here.

        It would raise the same thing, but with Pydantic's wording, so a caller
        would learn only that 0 is too small and not that a zero ceiling is a
        contradiction — an unbounded axis is ``UNBOUNDED`` on the budget, and
        nothing else.
        """
        if isinstance(value, bool) or (value is not None and not isinstance(value, int)):
            raise ContractValidationError(f"max_attempts must be an int, got {value!r}")
        if isinstance(value, int) and value < 1:
            raise ContractValidationError(f"max_attempts={value!r} is refused rather than clamped: an attempt ceiling of {value!r} is not a limit anybody chose")
        return value

    risk_level: str = Field(default="low", description="Engine's risk level, carried as a string because the vocabularies differ.")
    approval_policy: str = Field(default="not_required", description="not_required | required | prohibited. Approval is a dispatch decision, so a task that needs one is parked before it runs.")
    deadline_at: str | None = Field(default=None, description="ISO-8601 deadline, or None when unstated.")
    idempotency_key: str | None = Field(default=None, description="Stable key so a repeated dispatch is recognisable as the same work.")
    created_at: str | None = Field(default=None, description="ISO-8601 creation time, or None when the source engine does not record one. Absent is unstated, never a zero time.")
    updated_at: str | None = Field(default=None, description="ISO-8601 last-write time, or None when unstated.")

    @field_validator("task_type", mode="before")
    @classmethod
    def _coerce_task_type(cls, value: Any) -> Any:
        """Accept an engine enum or its string value.

        The enum's own ``__str__`` is its value for a ``StrEnum``, so a value
        that already looks like a contract kind passes through untouched. A
        value that matches nothing raises here rather than defaulting to
        ``TOOL``.
        """
        if value is None:
            raise ContractValidationError("task_type is required; a task with an unstated kind must say so explicitly, not inherit TOOL")
        raw = value.value if isinstance(value, StrEnum) else value
        if isinstance(raw, str) and raw not in TaskContractType.__members__ and raw not in {member.value for member in TaskContractType}:
            raise ContractValidationError(f"task_type {raw!r} is not part of the canonical vocabulary; adapters map engine-specific kinds rather than widening this enum")
        return raw

    @model_validator(mode="after")
    def _self_dependency_is_refused(self) -> TaskContract:
        if self.task_id in self.depends_on:
            raise ContractValidationError(f"task {self.task_id} declares a self-dependency, which no ordering can satisfy")
        return self

    @model_validator(mode="after")
    def _duplicate_dependencies_are_dropped(self) -> TaskContract:
        if len(self.depends_on) == len(set(self.depends_on)):
            return self
        seen: set[str] = set()
        deduped: list[str] = []
        for dependency in self.depends_on:
            if dependency not in seen:
                seen.add(dependency)
                deduped.append(dependency)
        object.__setattr__(self, "depends_on", deduped)
        return self

    # -- derived, never declared ------------------------------------------
    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_CONTRACT_STATUSES

    @property
    def is_blocked(self) -> bool:
        """Whether this task is parked and needs an explicit re-entry.

        The counterpart to readiness in every engine: a blocked task picked up
        by default re-dispatches work a failure deliberately parked.
        """
        return self.status is TaskContractStatus.BLOCKED

    @property
    def satisfies_dependency(self) -> bool:
        """Whether this task's completion satisfies a downstream prerequisite."""
        return self.status in SATISFIED_TASK_CONTRACT_STATUSES

    @property
    def needs_approval(self) -> bool:
        """Whether the task must park for approval before dispatch.

        Derived from the policy string rather than stored, so a contract cannot
        carry an approval flag that disagrees with its policy.
        """
        return self.approval_policy == "required"

    def with_status(self, status: TaskContractStatus, *, native_status: str | None = None, updated_at: str | None = None) -> TaskContract:
        """Return a copy carrying ``status``.

        The contract is frozen, so a status change is a new value rather than a
        mutation — which is what lets two engines hold different views of the
        same task without either writing over the other.
        """
        return self.model_copy(update={"status": status, "native_status": native_status if native_status is not None else self.native_status, "updated_at": updated_at if updated_at is not None else self.updated_at})

    def with_attempt(self, *, updated_at: str | None = None) -> TaskContract:
        """Return a copy with ``attempt_count`` incremented."""
        return self.model_copy(update={"attempt_count": self.attempt_count + 1, "updated_at": updated_at if updated_at is not None else self.updated_at})
