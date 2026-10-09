"""The router: deterministic admission of a task to the fast path or a governed run.

A-AVO is an expensive mode, not a universal mode. The router applies thresholds
and policy; the model may *recommend* a route, and the recommendation is one
advisory input (:attr:`~alpha.avo.contracts.TaskFeatures.autonomy_requested`)
that never substitutes for a condition.

Every condition the plan names is a rule here, and every rule that does not
hold lands in ``refused_reasons`` rather than silently defaulting to the fast
path — a task routed to the fast path *because* it failed a condition and a
task routed there because the router broke look identical otherwise.

Two properties are load-bearing:

* **Informational tasks never start a modifying workflow.** ``requires_mutation``
  is false and no evaluator exists means the fast path, whatever the
  complexity score says. "The agent has tools" is not a reason to use them.
* **The route carries its evidence.** A governed route records the rules that
  fired; a refusal records the ones that did not. Routing itself is auditable
  after the fact, not a decision that happened in someone's head.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import RouteDecision, TaskFeatures
from .profiles import UnknownProfile, budget_profile

__all__ = [
    "ESCALATION_RULES",
    "ROUTE_COMPLEXITY_FLOOR",
    "Router",
]

#: Below this complexity a task must fail a first attempt to justify the cost
#: of governed variation. The plan's "sufficiently complex **or** a first
#: controlled attempt failed" becomes one threshold plus one flag.
ROUTE_COMPLEXITY_FLOOR = 0.5

#: The exit progression, stated once so a caller cannot invent step 6.
ESCALATION_RULES: tuple[str, ...] = (
    "run_once_with_normal_verification",
    "route_failed_attempt_to_governed_run",
    "supervisor_proposes_causal_alternatives",
    "evaluate_against_shared_baseline",
    "stop_on_winner_limit_repeated_signature_or_human_input",
)


@dataclass
class Router:
    """Decide the route for one task. Stateless across tasks by design.

    The router never mutates anything: it reads :class:`TaskFeatures`, resolves
    the budget profile it names (so an unknown profile id is an exception at
    admission rather than a run with no ceiling), and returns a
    :class:`~alpha.avo.contracts.RouteDecision`.
    """

    #: Optional operator override: when ``False`` no task routes to a governed
    #: run however well it scores. A kill switch, not a threshold.
    governance_enabled: bool = True
    #: Features seen, for cheap introspection by callers/tests.
    seen: list[TaskFeatures] = field(default_factory=list)

    def route(self, features: TaskFeatures) -> RouteDecision:
        self.seen.append(features)
        matched: list[str] = []
        refused: list[str] = []

        # -- condition 0: budget profile resolves at all --------------------
        try:
            budget_profile(features.budget_profile_id)
        except UnknownProfile as exc:
            refused.append(f"budget_profile_unresolved: {exc.profile_id}")
            return RouteDecision(route="fast_path", matched_rules=matched, refused_reasons=refused, request=None)
        matched.append(f"budget_profile:{features.budget_profile_id}")

        if not self.governance_enabled:
            refused.append("governance_disabled_by_operator")
            return RouteDecision(route="fast_path", matched_rules=matched, refused_reasons=refused, request=None)

        # -- condition 1: bounded scope -------------------------------------
        # ``estimated_complexity`` is validated ``le=1.0`` by TaskFeatures, so
        # the contract is the ceiling: an over-complex task is refused there,
        # before a router ever sees it.
        if features.estimated_complexity >= ROUTE_COMPLEXITY_FLOOR:
            matched.append(f"complexity:{features.estimated_complexity:.2f}")
        elif features.prior_attempt_failed:
            matched.append("prior_attempt_failed")
        else:
            refused.append(f"complexity_below_floor:{features.estimated_complexity:.2f}<{ROUTE_COMPLEXITY_FLOOR:.2f} and no prior failed attempt")

        # -- condition 2: mutation is what the run is for -------------------
        if features.requires_mutation:
            matched.append("requires_mutation")
        else:
            refused.append("informational_task_never_starts_a_modifying_workflow")

        # -- condition 3: an objective or independently reviewable evaluator -
        if features.has_objective_evaluator:
            matched.append("objective_evaluator_present")
        else:
            refused.append("no_objective_evaluator")

        # -- condition 4: risk compatible with available approvals ----------
        if features.risk_level in ("high", "destructive"):
            refused.append(f"risk_level_{features.risk_level.value}_requires_human_admission")
        else:
            matched.append(f"risk_level:{features.risk_level.value}")

        # -- condition 5: budget available (profile ceilings exist) ---------
        # Resolution above is the admission-time half; a run that later
        # exhausts is the ledger's business, not the router's.
        matched.append("budget_available")

        # -- condition 6: autonomy was *requested* (a request, never a grant) -
        if features.autonomy_requested:
            matched.append("autonomy_requested_advisory")

        # -- condition 7: not prohibited by policy --------------------------
        # Prohibition lives in the profile's allow-list (the policy gate
        # enforces it per action); the router's half is that a governed run
        # needs a bounded working scope, which WorkspaceLocator validates.
        matched.append("bounded_working_scope")

        route = "fast_path" if refused else "avo"
        return RouteDecision(route=route, matched_rules=matched, refused_reasons=refused, request=None)

    # ------------------------------------------------------------------
    def escalation_step(self, *, attempt_failed: bool, governed_run_done: bool, stalled: bool) -> str:
        """The next step of the plan's escalation progression.

        Returns the *name* of the step; it does not execute anything. A caller
        that has already done steps 1-2 gets step 3, and so on, so "try again"
        loops are visible as a caller repeatedly reading the same step.
        """
        if not attempt_failed:
            return ESCALATION_RULES[0]
        if not governed_run_done:
            return ESCALATION_RULES[1]
        if not stalled:
            return ESCALATION_RULES[2]
        return ESCALATION_RULES[4]
