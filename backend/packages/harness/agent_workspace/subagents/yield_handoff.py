"""Subagent Yield & Settle Handoff Protocol inspired by OpenClaw."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class SettleEvent:
    child_session_id: str
    status: Literal["completed", "failed", "cancelled"]
    result: Any = None
    error: str | None = None
    settled_at: float = field(default_factory=time.time)


@dataclass
class YieldState:
    parent_session_id: str
    child_session_ids: list[str]
    wait_condition: Literal["all", "any"] = "all"
    settled_events: dict[str, SettleEvent] = field(default_factory=dict)
    yielded_at: float = field(default_factory=time.time)
    resumed: bool = False

    @property
    def is_settled(self) -> bool:
        if self.wait_condition == "any":
            return len(self.settled_events) > 0
        return set(self.child_session_ids).issubset(set(self.settled_events.keys()))


class SubagentYieldRegistry:
    """Manages suspension of orchestrator sessions until child subagents settle."""

    def __init__(self):
        self._yields: dict[str, YieldState] = {}  # parent_session_id -> YieldState
        self._child_to_parent: dict[str, str] = {}  # child_session_id -> parent_session_id

    def register_yield(
        self,
        parent_session_id: str,
        child_session_ids: list[str],
        wait_condition: Literal["all", "any"] = "all",
    ) -> YieldState:
        """Suspend parent session until specified child sessions settle."""
        state = YieldState(
            parent_session_id=parent_session_id,
            child_session_ids=child_session_ids,
            wait_condition=wait_condition,
        )
        self._yields[parent_session_id] = state
        for cid in child_session_ids:
            self._child_to_parent[cid] = parent_session_id
        return state

    def is_parent_yielded(self, parent_session_id: str) -> bool:
        state = self._yields.get(parent_session_id)
        return state is not None and not state.resumed and not state.is_settled

    def record_settle(
        self,
        child_session_id: str,
        status: Literal["completed", "failed", "cancelled"],
        result: Any = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Record child session completion and check if parent orchestrator should wake."""
        event = SettleEvent(
            child_session_id=child_session_id,
            status=status,
            result=result,
            error=error,
        )

        parent_id = self._child_to_parent.get(child_session_id)
        if not parent_id or parent_id not in self._yields:
            return {"parent_woken": False, "parent_session_id": None}

        state = self._yields[parent_id]
        state.settled_events[child_session_id] = event

        if state.is_settled and not state.resumed:
            return {
                "parent_woken": True,
                "parent_session_id": parent_id,
                "resumption_payload": self.get_resumption_payload(parent_id),
            }

        return {
            "parent_woken": False,
            "parent_session_id": parent_id,
            "pending_children": [cid for cid in state.child_session_ids if cid not in state.settled_events],
        }

    def get_resumption_payload(self, parent_session_id: str) -> dict[str, Any] | None:
        """Construct the atomic handoff payload to wake the parent orchestrator."""
        state = self._yields.get(parent_session_id)
        if not state:
            return None

        state.resumed = True
        return {
            "parent_session_id": parent_session_id,
            "status": "resumed",
            "children_settled": len(state.settled_events),
            "total_children": len(state.child_session_ids),
            "results": {
                cid: {
                    "status": ev.status,
                    "result": ev.result,
                    "error": ev.error,
                    "settled_at": ev.settled_at,
                }
                for cid, ev in state.settled_events.items()
            },
        }

    def clear(self) -> None:
        self._yields.clear()
        self._child_to_parent.clear()


_global_yield_registry = SubagentYieldRegistry()


def get_yield_registry() -> SubagentYieldRegistry:
    return _global_yield_registry


def sessions_yield(
    parent_session_id: str,
    child_session_ids: list[str],
    wait_condition: Literal["all", "any"] = "all",
) -> dict[str, Any]:
    """Suspend parent orchestrator turn waiting for child sessions."""
    registry = get_yield_registry()
    state = registry.register_yield(parent_session_id, child_session_ids, wait_condition)
    return {
        "action": "yield",
        "parent_session_id": parent_session_id,
        "waiting_on": child_session_ids,
        "wait_condition": wait_condition,
    }


def sessions_settle(
    child_session_id: str,
    status: Literal["completed", "failed", "cancelled"],
    result: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Settle child session and wake parent if condition met."""
    registry = get_yield_registry()
    return registry.record_settle(child_session_id, status, result=result, error=error)
