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


@dataclass
class RedirectRecord:
    """A recorded redirection: what triggered it, what was tried, where it went.

    AVO's supervisor *"reviews the overall evolutionary trajectory and steers the
    search toward several candidate optimization directions"*. Three things
    follow, and all three are fields here:

    * it operates on the **trajectory**, not the current step -- hence ``tried``
    * it re-opens the fan-out toward **several** directions, not one successor
    * the redirection is **recorded**, so "did anything intervene?" is answerable
    """

    trigger: str
    reasons: tuple[str, ...]
    directions: tuple[str, ...]
    tried: tuple[str, ...]
    sequence: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger,
            "reasons": list(self.reasons),
            "directions": list(self.directions),
            "direction_count": len(self.directions),
            "tried": list(self.tried),
            "sequence": self.sequence,
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
        #: Every redirect this supervisor has issued, in order. A redirect that
        #: is not recorded is indistinguishable from no redirect having happened,
        #: so the record is the mechanism and the directive is the payload.
        self.redirects: list[RedirectRecord] = []

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
        lineage: Any | None = None,
    ) -> tuple[bool, StrategicPivotDirective | None, str]:
        """
        Advanced observation interface detecting both Stalls and Oscillation Cycles.
        Returns: (needs_intervention, directive, diagnostic_message)

        ``lineage`` is optional. When supplied, the redirect's candidate
        directions are derived from what this lineage has already tried and
        rejected, rather than emitted as a fixed list -- which is the difference
        between steering a search and restarting it from the same place.
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
                directions = self._directions_for(lineage, minimum=2, trigger="oscillation")
                directive = StrategicPivotDirective(
                    directive_type="OSCILLATION_BREAK",
                    reason="Detected 2-cycle alternating oscillation between recent modifications.",
                    recommended_directions=list(directions),
                    taboo_patterns=[history_list[-1], history_list[-2]],
                    suggested_backtrack_target=backtrack_candidate,
                )
                self.last_directive = directive
                self._record_redirect("OSCILLATION_BREAK", (directive.reason,), directions, tuple(history_list))
                self.consecutive_stagnation = 0  # reset after intervention
                diag = f"CYCLE_DETECTED: {directive.reason} Forcing strategy pivot."
                logger.warning(diag)
                return True, directive, diag

        # Check Failure Mode 2: Exhaustion Stall
        if self.consecutive_stagnation >= self.max_no_improve:
            self.total_stagnation_events += 1
            reasons = (f"Search plateaued after {self.consecutive_stagnation} consecutive non-improving iterations.",)
            directions = self._directions_for(lineage, minimum=4, trigger="stall")
            directive = StrategicPivotDirective(
                directive_type="STAGNATION_PIVOT",
                reason=reasons[0],
                recommended_directions=list(directions),
                taboo_patterns=list(set(self.signature_history)),
                suggested_backtrack_target=backtrack_candidate,
            )
            self.last_directive = directive
            self._record_redirect("STAGNATION_PIVOT", reasons, directions, tuple(self.signature_history))
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

    # -- redirection ----------------------------------------------------
    #: Used when no lineage is available. Domain-agnostic on purpose: these are
    #: the shapes of change that have historically broken plateaus, not CUDA
    #: advice, so they are at least not wrong for a different domain.
    _GENERIC_DIRECTIONS: tuple[str, ...] = (
        "Revisit an earlier committed ancestor and branch from it rather than from the head.",
        "Replace a micro-refinement with a structural change to the module boundary.",
        "Reorder or overlap the stages that currently serialise against each other.",
        "Attack the dominant cost directly instead of tuning around it.",
    )

    def _directions_for(self, lineage: Any | None, *, minimum: int, trigger: str) -> tuple[str, ...]:
        """Candidate directions, derived from the trajectory when one is supplied.

        Returns at least ``minimum`` directions, always several. A stall must
        re-open the fan-out; a single forced successor is a different mechanism
        and is the one AVO explicitly does not use.
        """
        directions: list[str] = []
        if lineage is not None:
            trajectory = getattr(lineage, "rejected_attempts", []) or []
            for record in list(trajectory)[-3:]:
                reason = getattr(record, "rejection_reason", None)
                summary = str(getattr(record, "modification", "") or getattr(record, "hypothesis", ""))
                if reason:
                    directions.append(
                        f"'{summary[:80]}' was rejected for {reason}; approach the same surface differently rather than retrying it."
                    )
            head = lineage.get_head() if hasattr(lineage, "get_head") else None
            if head is not None:
                directions.append(
                    f"Branch from the best committed version ({head.version_id}, score {getattr(head, 'composite_score', 0.0):.6g}) rather than from the head of the failed line."
                )
        for generic in self._GENERIC_DIRECTIONS:
            if len(directions) >= max(minimum, 2):
                break
            if generic not in directions:
                directions.append(generic)
        if len(directions) < 2:  # pragma: no cover - the generic list is >= 2 by construction
            directions = list(self._GENERIC_DIRECTIONS[:2])
        assert trigger
        return tuple(directions)

    def _record_redirect(self, trigger: str, reasons: tuple[str, ...], directions: tuple[str, ...], tried: tuple[str, ...]) -> RedirectRecord:
        record = RedirectRecord(
            trigger=trigger,
            reasons=reasons,
            directions=directions,
            tried=tried,
            sequence=len(self.redirects) + 1,
        )
        self.redirects.append(record)
        logger.info(
            "AVO supervisor redirect #%d (%s) toward %d directions",
            record.sequence,
            trigger,
            len(record.directions),
        )
        return record

    def acknowledge_redirect(self, sequence: int, direction: str, outcome: str) -> dict[str, Any]:
        """Record that a redirect was actually taken, and what came of it.

        A supervisor that detects a stall and emits a directive nobody acts on is
        a detector, not a supervisor. This closes that loop in the record, and it
        is also how "a detector that kills the hardest tasks" becomes visible:
        ``outcome`` is free text precisely so an unproductive redirect is
        attributable.
        """
        for record in self.redirects:
            if record.sequence == sequence:
                return {"sequence": sequence, "direction": direction, "outcome": outcome, "recorded": True}
        return {"sequence": sequence, "direction": direction, "outcome": outcome, "recorded": False, "defect": "unknown redirect sequence"}

    def stats(self) -> dict[str, Any]:
        return {
            "max_no_improve": self.max_no_improve,
            "consecutive_stagnation": self.consecutive_stagnation,
            "total_improvements": self.total_improvements,
            "total_stagnation_events": self.total_stagnation_events,
            "total_cycle_events": self.total_cycle_events,
            "last_directive": self.last_directive.to_dict() if self.last_directive else None,
            "redirect_count": len(self.redirects),
            "redirects": [r.to_dict() for r in self.redirects],
            "intervention_mode": "redirect",
            "detector_bias": (
                "Counts consecutive non-improving iterations and has no notion of progress that does not "
                "commit. Long productive work that does not land a commit inside max_no_improve iterations "
                "is indistinguishable from a stall, so this supervisor fails toward FALSE POSITIVES: it "
                "redirects hardest tasks rather than killing them. That is the safer direction to fail in, "
                "because a redirect widens the fan-out and a kill does not."
            ),
        }
