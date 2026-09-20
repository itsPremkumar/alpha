"""EventStreamLedger: Immutable append-only audit trail and session replay engine."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from alpha.events.stream.actions import Action
from alpha.events.stream.observations import Observation

logger = logging.getLogger(__name__)


class EventStreamLedger:
    """Audit ledger maintaining sequential Action and Observation streams for deterministic replays."""

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or f"session_{int(time.time())}"
        self._events: list[Action | Observation] = []
        self._action_map: dict[str, Action] = {}
        self._observation_map: dict[str, list[Observation]] = {}

    def append_action(self, action: Action) -> None:
        self._events.append(action)
        self._action_map[action.action_id] = action
        if action.action_id not in self._observation_map:
            self._observation_map[action.action_id] = []

    def append_observation(self, observation: Observation) -> None:
        self._events.append(observation)
        if observation.action_id and observation.action_id in self._observation_map:
            self._observation_map[observation.action_id].append(observation)

    def get_events(self) -> list[Action | Observation]:
        return list(self._events)

    def get_action(self, action_id: str) -> Action | None:
        return self._action_map.get(action_id)

    def get_observations_for_action(self, action_id: str) -> list[Observation]:
        return self._observation_map.get(action_id, [])

    def get_trajectory(self) -> list[tuple[Action, list[Observation]]]:
        """Return chronological pairs of (Action, [Observations])."""
        trajectory: list[tuple[Action, list[Observation]]] = []
        for action in self._action_map.values():
            obs_list = self._observation_map.get(action.action_id, [])
            trajectory.append((action, obs_list))
        return trajectory

    def replay_session(self) -> Iterator[dict[str, Any]]:
        """Yield structured chronological replay steps with duration, type, and payload."""
        start_time = self._events[0].timestamp if self._events else 0.0

        for idx, event in enumerate(self._events):
            is_action = isinstance(event, Action)
            relative_ms = (event.timestamp - start_time) * 1000.0 if start_time else 0.0

            yield {
                "step_index": idx,
                "event_category": "action" if is_action else "observation",
                "relative_offset_ms": round(relative_ms, 2),
                "payload": event.to_dict(),
            }

    def export_jsonl(self, filepath: str | Path) -> None:
        """Export ledger events to JSONL file."""
        fp = Path(filepath)
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            for event in self._events:
                is_action = isinstance(event, Action)
                record = {
                    "event_category": "action" if is_action else "observation",
                    "data": event.to_dict(),
                }
                f.write(json.dumps(record) + "\n")

    def summary_stats(self) -> dict[str, Any]:
        """Calculate statistics of actions, observations, errors, and critics."""
        actions_count = sum(1 for e in self._events if isinstance(e, Action))
        obs_count = sum(1 for e in self._events if isinstance(e, Observation))
        errors_count = sum(
            1 for e in self._events if isinstance(e, Observation) and e.observation_type.value == "error"
        )
        return {
            "session_id": self.session_id,
            "total_events": len(self._events),
            "total_actions": actions_count,
            "total_observations": obs_count,
            "total_errors": errors_count,
        }
