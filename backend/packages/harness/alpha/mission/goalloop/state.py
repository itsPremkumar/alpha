"""Goal state and its durable home.

The whole point of a standing goal is that it outlives the turn that set it, so
state lives on disk through StoreKit's checked, atomically-published document
envelopes (:func:`alpha.persistence.storekit.write_document` /
:func:`~alpha.persistence.storekit.load_document`) rather than in a module
global. A restart, a ``/resume``, or a second process reading the same runtime
home all see the same goal, the same turn counter, the same subgoals, and the
same gates.

Files land under ``<runtime_home()>/mission/goals/<session>.json`` and
``runtime_home()`` honours ``AGENT_WORKSPACE_HOME``, which is what makes the
whole loop testable against a throwaway directory instead of the developer's
real workspace.

Every mutation goes through :meth:`GoalState.touch`, which bumps ``updated_at``
and re-saves, so the on-disk copy is never a half-applied transition.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from alpha.config.runtime_paths import runtime_home
from alpha.mission.goalloop.contract import CompletionContract
from alpha.mission.goalloop.gates import QualityGate

logger = logging.getLogger(__name__)

#: Schema id stamped on the persisted envelope. A document written by something
#: else under the same filename is refused rather than misread.
GOAL_SCHEMA_ID: Final[str] = "alpha.mission.goalloop.goal"
GOAL_FORMAT_VERSION: Final[int] = 1

#: The documented default continuation budget.
DEFAULT_MAX_TURNS: Final[int] = 20

#: Session key used when a caller has no thread/conversation id.
DEFAULT_SESSION_ID: Final[str] = "default"


class GoalStatus(StrEnum):
    """Lifecycle of a standing goal."""

    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    BLOCKED = "blocked"
    CLEARED = "cleared"

    @property
    def is_terminal(self) -> bool:
        return self in {GoalStatus.DONE, GoalStatus.CLEARED}

    @property
    def is_running(self) -> bool:
        return self is GoalStatus.ACTIVE


def new_goal_id() -> str:
    return f"goal-{uuid.uuid4().hex[:10]}"


@dataclass
class GoalState:
    """One standing goal for one session. Persisted as a single document."""

    session_id: str = DEFAULT_SESSION_ID
    goal_id: str = field(default_factory=new_goal_id)
    objective: str = ""
    contract: CompletionContract = field(default_factory=CompletionContract)
    subgoals: list[str] = field(default_factory=list)
    status: GoalStatus = GoalStatus.ACTIVE
    max_turns: int = DEFAULT_MAX_TURNS
    turns_used: int = 0
    gates: list[QualityGate] = field(default_factory=list)
    gate_failures: dict[int, int] = field(default_factory=dict)
    last_verdict: str = ""
    last_reason: str = ""
    pause_reason: str = ""
    wait_until: float = 0.0
    wait_reason: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------ invariants

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError("a goal needs a turn budget of at least 1")

    @property
    def budget_remaining(self) -> int:
        return max(0, self.max_turns - self.turns_used)

    @property
    def budget_exhausted(self) -> bool:
        return self.turns_used >= self.max_turns

    @property
    def headline(self) -> str:
        """One line naming the goal and its contract's outcome, for status output."""
        first = (self.objective or "").strip().splitlines()
        return first[0] if first else ""

    # ------------------------------------------------------------- mutation

    def add_subgoal(self, text: str) -> str:
        """Append one acceptance criterion without resetting the loop."""
        cleaned = " ".join((text or "").split())
        if not cleaned:
            raise ValueError("a subgoal needs text")
        self.subgoals.append(cleaned)
        self.touch()
        return cleaned

    def remove_subgoal(self, index: int) -> str:
        """Remove the 1-based *index* subgoal. Raises if it does not exist."""
        if index < 1 or index > len(self.subgoals):
            raise IndexError(f"no subgoal #{index}; the goal has {len(self.subgoals)}")
        removed = self.subgoals.pop(index - 1)
        self.touch()
        return removed

    def clear_subgoals(self) -> int:
        count = len(self.subgoals)
        self.subgoals.clear()
        self.touch()
        return count

    def add_gate(self, gate: QualityGate) -> QualityGate:
        if not gate.command.strip():
            raise ValueError("a gate needs a command")
        self.gates.append(gate)
        self.touch()
        return gate

    def remove_gate(self, index: int) -> QualityGate:
        if index < 1 or index > len(self.gates):
            raise IndexError(f"no gate #{index}; the goal has {len(self.gates)}")
        removed = self.gates.pop(index - 1)
        # Failure counters are positional; drop this one and renumber so a
        # later gate cannot inherit a dead gate's strike count.
        self.gate_failures = {
            (key - 1 if key > index else key): value for key, value in self.gate_failures.items() if key != index
        }
        self.touch()
        return removed

    def clear_gates(self) -> int:
        count = len(self.gates)
        self.gates.clear()
        self.gate_failures.clear()
        self.touch()
        return count

    def record_gate_outcome(self, index: int, passed: bool) -> int:
        """Track consecutive red boundaries for gate *index* (1-based).

        A pass resets the counter to zero, which is what makes "a gate whose
        input you just repaired passes on the next boundary" true rather than
        aspirational - there is no failure history to carry forward.
        """
        if passed:
            self.gate_failures.pop(index, None)
            return 0
        count = self.gate_failures.get(index, 0) + 1
        self.gate_failures[index] = count
        return count

    def consume_turn(self) -> int:
        """Count one continuation turn. Returns the new count."""
        self.turns_used += 1
        self.touch()
        return self.turns_used

    def pause(self, reason: str) -> GoalStatus:
        self.status = GoalStatus.PAUSED
        self.pause_reason = reason
        self.touch()
        return self.status

    def block(self, reason: str) -> GoalStatus:
        """A genuinely unachievable goal: paused with a reason, never done."""
        self.status = GoalStatus.BLOCKED
        self.pause_reason = reason
        self.last_verdict = "blocked"
        self.last_reason = reason
        self.touch()
        return self.status

    def complete(self, reason: str) -> GoalStatus:
        self.status = GoalStatus.DONE
        self.last_verdict = "done"
        self.last_reason = reason
        self.touch()
        return self.status

    def resume(self) -> GoalStatus:
        """Resume from a pause, resetting the turn counter to zero."""
        self.status = GoalStatus.ACTIVE
        self.pause_reason = ""
        self.wait_until = 0.0
        self.wait_reason = ""
        self.turns_used = 0
        self.touch()
        return self.status

    def park(self, reason: str, until: float) -> float:
        """Park the loop until an absolute deadline. Returns the deadline.

        The deadline is absolute, not a duration, so a restart cannot extend a
        park: whatever wall clock the operator comes back at, the barrier has
        already expired if it was going to. That is what stops a forgotten wait
        from wedging a goal forever.
        """
        self.wait_until = float(until)
        self.wait_reason = reason
        self.touch()
        return self.wait_until

    @property
    def wait_expired(self) -> bool:
        return self.wait_until > 0.0 and time.time() >= self.wait_until

    def clear(self) -> GoalStatus:
        self.status = GoalStatus.CLEARED
        self.pause_reason = ""
        self.wait_until = 0.0
        self.wait_reason = ""
        self.subgoals.clear()
        self.gates.clear()
        self.touch()
        return self.status

    def touch(self) -> None:
        self.updated_at = time.time()

    # --------------------------------------------------------- serialisation

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "goal_id": self.goal_id,
            "objective": self.objective,
            "contract": self.contract.to_dict(),
            "subgoals": list(self.subgoals),
            "status": self.status.value,
            "max_turns": self.max_turns,
            "turns_used": self.turns_used,
            "gates": [gate.to_dict() for gate in self.gates],
            "gate_failures": {str(key): value for key, value in self.gate_failures.items()},
            "last_verdict": self.last_verdict,
            "last_reason": self.last_reason,
            "pause_reason": self.pause_reason,
            "wait_until": self.wait_until,
            "wait_reason": self.wait_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> GoalState:
        try:
            status = GoalStatus(str(data.get("status", GoalStatus.ACTIVE.value)))
        except ValueError:
            status = GoalStatus.ACTIVE
        return cls(
            session_id=str(data.get("session_id", DEFAULT_SESSION_ID)),
            goal_id=str(data.get("goal_id") or new_goal_id()),
            objective=str(data.get("objective", "")),
            contract=CompletionContract.from_dict(data.get("contract", {}) or {}),
            subgoals=[str(item) for item in (data.get("subgoals") or [])],
            status=status,
            max_turns=int(data.get("max_turns", DEFAULT_MAX_TURNS) or DEFAULT_MAX_TURNS),
            turns_used=int(data.get("turns_used", 0) or 0),
            gates=[QualityGate.from_dict(dict(gate)) for gate in (data.get("gates") or [])],
            gate_failures={int(key): int(value) for key, value in (data.get("gate_failures") or {}).items()},
            last_verdict=str(data.get("last_verdict", "")),
            last_reason=str(data.get("last_reason", "")),
            pause_reason=str(data.get("pause_reason", "")),
            wait_until=float(data.get("wait_until", 0.0) or 0.0),
            wait_reason=str(data.get("wait_reason", "")),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        data = self.to_payload()
        data["budget_remaining"] = self.budget_remaining
        data["headline"] = self.headline
        return data


def goals_dir() -> Path:
    """``<runtime_home()>/mission/goals`` - created on first write."""
    return runtime_home() / "mission" / "goals"


def _safe_session_key(session_id: str) -> str:
    """Filesystem-safe key for a session id.

    Session ids come from callers (thread ids, chat ids), so they are not
    trusted to be path-safe. Anything outside a conservative alphabet is
    percent-escaped rather than stripped, so two different ids cannot collide
    onto one file.
    """
    raw = (session_id or DEFAULT_SESSION_ID).strip() or DEFAULT_SESSION_ID
    return "".join(char if (char.isalnum() or char in "-_.") else f"%{ord(char):02x}" for char in raw)


class GoalStore:
    """Durable, per-session goal storage backed by StoreKit documents.

    The in-process cache is a read cache only: :meth:`save` always writes
    through, and :meth:`load` re-reads from disk when the session is not
    cached, so a fresh process (or a fresh test) sees exactly what the previous
    one wrote.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory
        self._cache: dict[str, GoalState] = {}
        self._lock = threading.RLock()

    @property
    def directory(self) -> Path:
        return self._dir if self._dir is not None else goals_dir()

    def path_for(self, session_id: str) -> Path:
        return self.directory / f"{_safe_session_key(session_id)}.json"

    def save(self, state: GoalState) -> Path:
        """Publish *state* atomically and return the path written."""
        from alpha.persistence.storekit import write_document

        state.touch()
        path = self.path_for(state.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_document(
            path,
            state.to_payload(),
            format_version=GOAL_FORMAT_VERSION,
            schema_id=GOAL_SCHEMA_ID,
        )
        with self._lock:
            self._cache[state.session_id] = state
        return path

    def load(self, session_id: str) -> GoalState | None:
        """Read the goal for *session_id*, or ``None`` if there is none."""
        from alpha.persistence.storekit import load_document

        path = self.path_for(session_id)
        if not path.exists():
            with self._lock:
                return self._cache.get(session_id)
        result = load_document(path, expected_schema_id=GOAL_SCHEMA_ID)
        if result.status != "ok" or result.document is None:
            return None
        payload = result.document.payload
        if not isinstance(payload, dict):
            return None
        state = GoalState.from_payload(payload)
        with self._lock:
            self._cache[session_id] = state
        return state

    def delete(self, session_id: str) -> bool:
        """Remove the persisted goal for *session_id*. Returns True if one went.

        Used by ``/goal clear``: clearing a goal must make the next dispatch see
        no goal at all, not a tombstone it has to filter.
        """
        with self._lock:
            self._cache.pop(session_id, None)
        path = self.path_for(session_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.warning("Could not delete goal document %s: %s", path, exc)
            return False
        return True

    def clear_cache(self) -> None:
        """Drop the read cache so the next load re-reads from disk."""
        with self._lock:
            self._cache.clear()

    def sessions(self) -> Iterator[str]:
        directory = self.directory
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.json")):
            state = self.load(path.stem)
            if state is not None:
                yield state.session_id


_DEFAULT_STORE: GoalStore | None = None
_DEFAULT_STORE_LOCK = threading.Lock()


def get_goal_store() -> GoalStore:
    """Process-wide goal store."""
    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        if _DEFAULT_STORE is None:
            _DEFAULT_STORE = GoalStore()
        return _DEFAULT_STORE


def reset_goal_store() -> None:
    """Drop the process-wide store. Tests use this between cases."""
    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        _DEFAULT_STORE = None


__all__ = [
    "DEFAULT_MAX_TURNS",
    "DEFAULT_SESSION_ID",
    "GOAL_FORMAT_VERSION",
    "GOAL_SCHEMA_ID",
    "GoalState",
    "GoalStatus",
    "GoalStore",
    "get_goal_store",
    "goals_dir",
    "new_goal_id",
    "reset_goal_store",
]
