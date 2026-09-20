from __future__ import annotations

import collections
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("alpha.avo.supervisor")


@dataclass
class StrategicPivotDirective:
    """
    Structured intervention directive synthesized by the AVO Supervisor
    when progress stalls or enters an unproductive cycle.
    """
    directive_type: str  # "STAGNATION_PIVOT", "OSCILLATION_BREAK", "CORRECTNESS_BLOCKED"
    reason: str
    recommended_directions: list[str] = field(default_factory=list)
    taboo_patterns: list[str] = field(default_factory=list)
    suggested_backtrack_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "directive_type": self.directive_type,
            "reason": self.reason,
            "recommended_directions": self.recommended_directions,
            "taboo_patterns": self.taboo_patterns,
            "suggested_backtrack_target": self.suggested_backtrack_target,
        }


class AVOSupervisor:
    """
    Self-supervision and anti-stagnation watchdog for Autonomous AVO.
    Detects two primary long-horizon failure modes:
      1. Exhaustion Stalls: search plateau after max_no_improve non-improving iterations.
      2. Oscillation Cycles: repeated alternating edits or identical failure churn.
    Intervenes by issuing structured StrategicPivotDirectives.
    """

    def __init__(
        self,
        max_no_improve: int = 4,
        cycle_window_size: int = 6,
    ) -> None:
        self.max_no_improve = max_no_improve
        self.cycle_window_size = cycle_window_size
        self.consecutive_stagnation: int = 0
        self.total_improvements: int = 0
        self.total_stagnation_events: int = 0
        self.total_cycle_events: int = 0

        # Sliding history of candidate signatures for cycle detection
        self.signature_history: collections.deque[str] = collections.deque(maxlen=cycle_window_size)
        self.last_directive: StrategicPivotDirective | None = None

    def observe(self, improved: bool) -> tuple[bool, str]:
        """
        Legacy/Simple observation interface:
        Returns (is_stagnated, diagnostic_message).
        """
        stagnated, directive, diag = self.observe_step(improved=improved)
        return stagnated, diag

    def observe_step(
        self,
        improved: bool,
        signature: str | None = None,
        backtrack_candidate: str | None = None,
    ) -> tuple[bool, StrategicPivotDirective | None, str]:
        """
        Advanced observation interface detecting both Stalls and Oscillation Cycles.
        Returns: (needs_intervention, directive, diagnostic_message)
        """
        if signature:
            self.signature_history.append(signature)

        if improved:
            self.consecutive_stagnation = 0
            self.total_improvements += 1
            self.last_directive = None
            return False, None, "NOMINAL: Improvement committed to lineage."

        self.consecutive_stagnation += 1

        # Check Failure Mode 1: Oscillation Cycle Churn
        if len(self.signature_history) >= 4:
            history_list = list(self.signature_history)
            # Detect A-B-A-B oscillation
            if (
                history_list[-1] == history_list[-3]
                and history_list[-2] == history_list[-4]
                and history_list[-1] != history_list[-2]
            ):
                self.total_cycle_events += 1
                directive = StrategicPivotDirective(
                    directive_type="OSCILLATION_BREAK",
                    reason="Detected 2-cycle alternating oscillation between recent modifications.",
                    recommended_directions=[
                        "Abandon current alternating edits; explore an orthogonal architectural direction.",
                        "Revisit earlier committed ancestor and branch into alternative approach.",
                    ],
                    taboo_patterns=[history_list[-1], history_list[-2]],
                    suggested_backtrack_target=backtrack_candidate,
                )
                self.last_directive = directive
                self.consecutive_stagnation = 0  # reset after intervention
                diag = f"CYCLE_DETECTED: {directive.reason} Forcing strategy pivot."
                logger.warning(diag)
                return True, directive, diag

        # Check Failure Mode 2: Exhaustion Stall
        if self.consecutive_stagnation >= self.max_no_improve:
            self.total_stagnation_events += 1
            directive = StrategicPivotDirective(
                directive_type="STAGNATION_PIVOT",
                reason=(
                    f"Search plateaued after {self.consecutive_stagnation} consecutive "
                    "non-improving iterations."
                ),
                recommended_directions=[
                    "Branchless execution / speculative computation to remove divergence.",
                    "Pipeline overlap between compute stages and memory load latency.",
                    "Register rebalancing or occupancy tuning across specialized workers.",
                    "Structural algorithmic redesign rather than micro-tuning.",
                ],
                taboo_patterns=list(set(self.signature_history)),
                suggested_backtrack_target=backtrack_candidate,
            )
            self.last_directive = directive
            self.consecutive_stagnation = 0  # reset after triggering
            diag = (
                f"STAGNATION_DETECTED: Search plateaued after {self.max_no_improve} "
                "consecutive non-improving iterations. Forcing exploratory perturbation."
            )
            logger.warning(diag)
            return True, directive, diag

        return (
            False,
            None,
            f"MONITORING: Stagnation count at {self.consecutive_stagnation}/{self.max_no_improve}.",
        )

    def stats(self) -> dict[str, Any]:
        return {
            "max_no_improve": self.max_no_improve,
            "consecutive_stagnation": self.consecutive_stagnation,
            "total_improvements": self.total_improvements,
            "total_stagnation_events": self.total_stagnation_events,
            "total_cycle_events": self.total_cycle_events,
            "last_directive": self.last_directive.to_dict() if self.last_directive else None,
        }
