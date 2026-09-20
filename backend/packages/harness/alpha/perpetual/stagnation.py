"""Stagnation & Deadlock Watchdog: Detects repetitive loops and applies Keel-style recovery interventions."""

from __future__ import annotations

import logging
from typing import Any

from alpha.perpetual.models import StagnationIncident

logger = logging.getLogger(__name__)


class StagnationRecoveryWatchdog:
    """Monitors running execution trajectories to detect semantic stalls and execute strategic pivots."""

    def __init__(self, loop_threshold: int = 3):
        self.loop_threshold = loop_threshold
        self._action_history: list[str] = []
        self._incidents: list[StagnationIncident] = []

    def record_action(self, action_signature: str) -> None:
        """Record an executed action signature to the sliding history."""
        self._action_history.append(action_signature)
        if len(self._action_history) > 50:
            self._action_history.pop(0)

    def record_failure(self, failure_signature: str) -> None:
        """Record an execution failure or error signature to track stagnation."""
        self.record_action(f"failure_{failure_signature}")

    def check_stagnation(self) -> StagnationIncident | None:
        """Analyze recent action patterns for repetition or deadlock signatures."""
        if len(self._action_history) < self.loop_threshold:
            return None

        # Check for consecutive repetition of any action in recent history
        for i in range(len(self._action_history) - self.loop_threshold + 1):
            window = self._action_history[i : i + self.loop_threshold]
            if len(set(window)) == 1:
                stalled_action = window[0]
                recovery_action = self._determine_intervention(stalled_action)

                incident = StagnationIncident(
                    signature=f"repeated_action_{stalled_action[:32]}",
                    repeated_action=stalled_action,
                    consecutive_failures=self.loop_threshold,
                    recovery_action_taken=recovery_action,
                    resolved=True,
                )
                self._incidents.append(incident)
                logger.warning(
                    "Stagnation detected: action '%s' repeated %d times. Intervention applied: %s",
                    stalled_action,
                    self.loop_threshold,
                    recovery_action,
                )
                # Clear stalled action occurrences to prevent re-triggering on the same stall
                self._action_history = [a for a in self._action_history if a != stalled_action]
                return incident

        return None

    def _determine_intervention(self, stalled_action: str) -> str:
        """Choose the optimal Keel-inspired perturbation intervention."""
        act_lower = stalled_action.lower()
        if "test" in act_lower or "fail" in act_lower or "error" in act_lower or "timeout" in act_lower:
            return "backtrack_to_last_checkpoint_and_mutate_hypothesis"
        elif "tool" in act_lower or "api" in act_lower:
            return "inject_negative_constraint_forbid_repeated_tool_args"
        else:
            return "escalate_model_tier_and_reset_working_memory"

    def get_incidents(self) -> list[StagnationIncident]:
        return list(self._incidents)
