"""Durable Task State Machine.

Inspired by Chapter 22 of the Master Architecture Blueprint:
CREATED -> ANALYZING -> PLANNING -> READY -> RUNNING
  ├── WAITING_FOR_TOOL
  ├── WAITING_FOR_AGENT
  ├── WAITING_FOR_APPROVAL
  ├── BLOCKED
  └── RECOVERING
-> VERIFYING -> COMPLETED / FAILED / CANCELLED / EXPIRED
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    CREATED = "created"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    WAITING_FOR_AGENT = "waiting_for_agent"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    BLOCKED = "blocked"
    RECOVERING = "recovering"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal state machine transition is attempted."""
    pass


# Legal state transition graph mapping source_state -> set of valid target_states
VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.CREATED: {
        TaskState.ANALYZING,
        TaskState.CANCELLED,
    },
    TaskState.ANALYZING: {
        TaskState.PLANNING,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.PLANNING: {
        TaskState.READY,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.READY: {
        TaskState.RUNNING,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
    },
    TaskState.RUNNING: {
        TaskState.WAITING_FOR_TOOL,
        TaskState.WAITING_FOR_AGENT,
        TaskState.WAITING_FOR_APPROVAL,
        TaskState.BLOCKED,
        TaskState.RECOVERING,
        TaskState.VERIFYING,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.WAITING_FOR_TOOL: {
        TaskState.RUNNING,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.EXPIRED,
    },
    TaskState.WAITING_FOR_AGENT: {
        TaskState.RUNNING,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.EXPIRED,
    },
    TaskState.WAITING_FOR_APPROVAL: {
        TaskState.RUNNING,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.EXPIRED,
    },
    TaskState.BLOCKED: {
        TaskState.READY,
        TaskState.RUNNING,
        TaskState.RECOVERING,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.RECOVERING: {
        TaskState.RUNNING,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.VERIFYING: {
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.RECOVERING,
        TaskState.RUNNING,
    },
    # Terminal states do not transition anywhere
    TaskState.COMPLETED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
    TaskState.EXPIRED: set(),
}

TERMINAL_STATES = {
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELLED,
    TaskState.EXPIRED,
}

WAITING_STATES = {
    TaskState.WAITING_FOR_TOOL,
    TaskState.WAITING_FOR_AGENT,
    TaskState.WAITING_FOR_APPROVAL,
    TaskState.BLOCKED,
}


@dataclass
class StateTransitionRecord:
    from_state: TaskState
    to_state: TaskState
    timestamp: float = field(default_factory=time.time)
    reason: str = ""
    actor: str = "orchestrator"

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "timestamp": self.timestamp,
            "reason": self.reason,
            "actor": self.actor,
        }


class TaskStateMachine:
    """Manages legal lifecycle transitions and audits history for an autonomous task."""

    def __init__(self, task_id: str, initial_state: TaskState = TaskState.CREATED):
        self.task_id: str = task_id
        self._current_state: TaskState = initial_state
        self._history: list[StateTransitionRecord] = [
            StateTransitionRecord(
                from_state=initial_state,
                to_state=initial_state,
                reason="Task instantiated",
                actor="system",
            )
        ]

    @property
    def current_state(self) -> TaskState:
        return self._current_state

    @property
    def history(self) -> list[StateTransitionRecord]:
        return list(self._history)

    @property
    def is_terminal(self) -> bool:
        return self._current_state in TERMINAL_STATES

    @property
    def is_waiting(self) -> bool:
        return self._current_state in WAITING_STATES

    def can_transition_to(self, target: TaskState) -> bool:
        valid_targets = VALID_TRANSITIONS.get(self._current_state, set())
        return target in valid_targets

    def transition_to(
        self,
        target_state: TaskState,
        reason: str = "",
        actor: str = "orchestrator",
    ) -> TaskState:
        """Attempt to transition to target_state. Raises InvalidStateTransitionError if illegal."""
        if target_state == self._current_state:
            return self._current_state

        if not self.can_transition_to(target_state):
            raise InvalidStateTransitionError(
                f"Illegal state transition for task '{self.task_id}': "
                f"Cannot move from '{self._current_state.value}' to '{target_state.value}'. "
                f"Valid next states: {[s.value for s in VALID_TRANSITIONS.get(self._current_state, set())]}"
            )

        record = StateTransitionRecord(
            from_state=self._current_state,
            to_state=target_state,
            timestamp=time.time(),
            reason=reason,
            actor=actor,
        )
        self._history.append(record)
        self._current_state = target_state
        return self._current_state

    def reset_for_retry(self, reason: str = "Task retried after failure", actor: str = "recovery_agent") -> TaskState:
        """Allow an explicit recovery reset from FAILED or BLOCKED back to READY."""
        if self._current_state not in (TaskState.FAILED, TaskState.BLOCKED):
            raise InvalidStateTransitionError(
                f"Cannot reset task '{self.task_id}' from state '{self._current_state.value}'. Only FAILED or BLOCKED can be reset."
            )

        record = StateTransitionRecord(
            from_state=self._current_state,
            to_state=TaskState.READY,
            timestamp=time.time(),
            reason=reason,
            actor=actor,
        )
        self._history.append(record)
        self._current_state = TaskState.READY
        return self._current_state

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "current_state": self._current_state.value,
            "is_terminal": self.is_terminal,
            "is_waiting": self.is_waiting,
            "history": [r.to_dict() for r in self._history],
        }
