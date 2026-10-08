"""The APEX agent factory — spec §10, §11, §16, §17, §28.

APEX may create temporary specialist agents, delegate to them, and reclaim them.
None of that is new machinery here: it composes three existing owners.

* **Records, leases and heartbeats** → ``alpha.subagents.lifecycle``.
  :class:`~alpha.subagents.lifecycle.SubagentLifecycleManager` already issues a
  lease on spawn, writes a disk checkpoint, refuses a spawn past
  ``DEFAULT_MAX_DEPTH``, and has ``recover_after_restart``. Reimplementing any of
  that would be a second lifecycle owner.
* **Delegation ceilings and cycle detection** → ``alpha.bots.delegation``.
  ``DelegationLimits`` / ``DelegationContext.check`` already answer depth,
  fanout, hops, cycle and budget, with stable refusal codes.
* **The ephemeral bot profile** → ``alpha.bots.ephemeral``.
  ``EphemeralBotManager.spawn_specialist`` synthesises a SOUL and registers a
  TTL lease without calling a model.

**What APEX adds is the decision layer**, which is what the spec actually asks
for and what none of the three holds: *whether a specialist is worth creating*,
*what role*, and *when the work is done so the agent can be terminated*.

**Three properties are load-bearing.**

1. **A specialist is only created when it earns its keep.** :meth:`should_specialize`
   applies spec §15's questions directly — specialization, independence, context
   isolation, expected benefit over cost, reviewer needed — and a task that
   answers "no" to all of them is executed directly. This is what stops APEX
   spawning twelve agents to rename a variable.

2. **Recursion is bounded twice.** The contract's ``max_delegation_depth`` is
   intersected with ``SubagentLifecycleManager``'s own ``DEFAULT_MAX_DEPTH``, so
   raising APEX's ceiling cannot escape the subagent manager's. Whichever is
   lower wins, and the refusal names both.

3. **Termination is real, not a status write.** :meth:`retire_agent` completes or
   cancels through the lifecycle manager and, when the agent came from the
   ephemeral pool, archives it. An agent left in ``RUNNING`` with no task is the
   orphan spec §102/§103 exists to prevent.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.apex.contract import AutonomyContract

logger = logging.getLogger(__name__)

__all__ = [
    "ApexAgentRole",
    "ApexAgentSpec",
    "AgentSpawnResult",
    "DelegationRefusal",
    "SpecializationVerdict",
    "assess_specialization",
    "resolve_delegation_limits",
]


#: The owner's terminal statuses, named here because
#: ``SubagentStatusEnum`` exposes no ``is_terminal()`` predicate. Deriving this
#: by trial (``getattr(status, "is_terminal", lambda: False)()``) looks like it
#: works and does not: the default swallows the question and returns False, so
#: a retirement would report itself verified without ever being checked.
TERMINAL_SUBAGENT_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "expired", "archived"})


class ApexAgentRole(StrEnum):
    """Spec §16's taxonomy, as templates rather than permanent agents.

    Kept as a StrEnum with free-form ``role`` strings still accepted, so a
    caller naming a role this build does not know is not refused — it is
    recorded verbatim and resolved downstream.
    """

    PLANNER = "planner"
    RESEARCHER = "researcher"
    FACT_CHECKER = "fact_checker"
    BROWSER_OPERATOR = "browser_operator"
    FRONTEND_ENGINEER = "frontend_engineer"
    BACKEND_ENGINEER = "backend_engineer"
    DATABASE_ENGINEER = "database_engineer"
    SYSTEMS_ENGINEER = "systems_engineer"
    TERMINAL_OPERATOR = "terminal_operator"
    GIT_OPERATOR = "git_operator"
    SECURITY_REVIEWER = "security_reviewer"
    CODE_REVIEWER = "code_reviewer"
    QA_ENGINEER = "qa_engineer"
    INTEGRATION_ENGINEER = "integration_engineer"
    PERFORMANCE_ENGINEER = "performance_engineer"
    UX_REVIEWER = "ux_reviewer"
    DOCUMENTATION_AGENT = "documentation_agent"
    RELEASE_AGENT = "release_agent"
    FAILURE_INVESTIGATOR = "failure_investigator"
    RECOVERY_AGENT = "recovery_agent"
    MONITORING_AGENT = "monitoring_agent"
    MEMORY_CURATOR = "memory_curator"
    MODEL_EVALUATOR = "model_evaluator"

    @classmethod
    def parse(cls, value: str) -> str:
        """Return a known role, or the caller's string verbatim.

        Refusing an unrecognised role would make APEX unable to name a
        specialist this build has not enumerated, which is a worse failure than
        recording an unfamiliar role for the host to interpret.
        """
        text = str(value or "").strip()
        try:
            return cls(text).value
        except ValueError:
            return text


#: Roles that may only ever be created with an isolated workspace, because
#: their work is a source mutation and two of them on one tree corrupt each
#: other (spec §20, §29).
_ISOLATED_WORKSPACE_ROLES: frozenset[str] = frozenset(
    {
        ApexAgentRole.FRONTEND_ENGINEER.value,
        ApexAgentRole.BACKEND_ENGINEER.value,
        ApexAgentRole.DATABASE_ENGINEER.value,
        ApexAgentRole.SYSTEMS_ENGINEER.value,
        ApexAgentRole.GIT_OPERATOR.value,
        ApexAgentRole.PERFORMANCE_ENGINEER.value,
    }
)

#: Roles whose output is a judgement rather than a mutation, so they may read a
#: shared workspace safely.
_REVIEW_ROLES: frozenset[str] = frozenset(
    {
        ApexAgentRole.SECURITY_REVIEWER.value,
        ApexAgentRole.CODE_REVIEWER.value,
        ApexAgentRole.FACT_CHECKER.value,
        ApexAgentRole.UX_REVIEWER.value,
    }
)


@dataclass
class ApexAgentSpec:
    """One specialist, per spec §10's required fields."""

    role: str
    objective: str
    goal_id: str = ""
    task_id: str = ""
    parent_agent_id: str = ""
    instructions: str = ""
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    memory_scope: str = "task"
    model: str = ""
    workspace: str = ""
    permissions: list[str] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    deadline: float | None = None
    success_criteria: list[str] = field(default_factory=list)
    verification_requirements: list[str] = field(default_factory=list)
    #: Spec §10's `expiration_policy`; ``task-complete`` is the only value the
    #: factory acts on, because a specialist with no automatic expiry is an
    #: orphan waiting to happen.
    expiration_policy: str = "task-complete"
    max_attempts: int = 2
    context_mode: str = "selective"
    timeout_seconds: int = 900

    @property
    def requires_isolated_workspace(self) -> bool:
        return self.role in _ISOLATED_WORKSPACE_ROLES

    @property
    def is_reviewer(self) -> bool:
        return self.role in _REVIEW_ROLES

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "objective": self.objective,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "parent_agent_id": self.parent_agent_id,
            "memory_scope": self.memory_scope,
            "model": self.model,
            "workspace": self.workspace,
            "permissions": list(self.permissions),
            "budget": dict(self.budget),
            "deadline": self.deadline,
            "success_criteria": list(self.success_criteria),
            "verification_requirements": list(self.verification_requirements),
            "expiration_policy": self.expiration_policy,
            "requires_isolated_workspace": self.requires_isolated_workspace,
            "is_reviewer": self.is_reviewer,
        }


@dataclass
class SpecializationVerdict:
    """Spec §15's agent-creation decision, as a recorded answer.

    Every question is answered with a *reason*, so "APEX spawned three agents"
    is explainable afterwards rather than only visible as a count.
    """

    should_specialize: bool
    reasons: list[str] = field(default_factory=list)
    #: The role APEX would create, or "" when it would execute directly.
    proposed_role: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_specialize": self.should_specialize,
            "reasons": list(self.reasons),
            "proposed_role": self.proposed_role,
        }


def _infer_role(objective: str, *, needs_review: bool = False) -> str:
    """Pick a role from objective keywords.

    Deterministic and model-free, matching spec §39's "delegate, don't execute
    the reasoning in the caller" only for the *routing* half. It records what it
    matched so a wrong pick is diagnosable rather than merely wrong.
    """
    text = str(objective or "").lower()
    if needs_review:
        return ApexAgentRole.CODE_REVIEWER.value
    if any(k in text for k in ("browser", "playwright", "selenium", "dom", "page load")):
        return ApexAgentRole.BROWSER_OPERATOR.value
    if any(k in text for k in ("security", "vulnerab", "cve", "secret", "credential")):
        return ApexAgentRole.SECURITY_REVIEWER.value
    if any(k in text for k in ("research", "investigate", "compare", "survey", "literature")):
        return ApexAgentRole.RESEARCHER.value
    if any(k in text for k in ("test", "qa", "regression", "coverage")):
        return ApexAgentRole.QA_ENGINEER.value
    if any(k in text for k in ("ui", "frontend", "css", "component", "react")):
        return ApexAgentRole.FRONTEND_ENGINEER.value
    if any(k in text for k in ("schema", "migration", "database", "sql", "index")):
        return ApexAgentRole.DATABASE_ENGINEER.value
    if any(k in text for k in ("api", "backend", "service", "endpoint", "server")):
        return ApexAgentRole.BACKEND_ENGINEER.value
    if any(k in text for k in ("deploy", "build", "pipeline", "ci", "release")):
        return ApexAgentRole.RELEASE_AGENT.value
    if any(k in text for k in ("document", "readme", "changelog", "guide")):
        return ApexAgentRole.DOCUMENTATION_AGENT.value
    if any(k in text for k in ("why", "root cause", "diagnose", "failure", "broken", "crash")):
        return ApexAgentRole.FAILURE_INVESTIGATOR.value
    if any(k in text for k in ("performance", "latency", "slow", "profil")):
        return ApexAgentRole.PERFORMANCE_ENGINEER.value
    return ApexAgentRole.SYSTEMS_ENGINEER.value


def assess_specialization(
    objective: str,
    *,
    independent_items: int = 1,
    context_isolation_helps: bool = False,
    estimated_cost_units: float = 1.0,
    expected_benefit_units: float = 1.0,
    needs_reviewer: bool = False,
) -> SpecializationVerdict:
    """Spec §15's five questions, answered explicitly.

    The cost/benefit test is a strict inequality on purpose: a specialization
    that cannot pay for itself is the failure mode spec §13 and §191 both warn
    about, and an equal-cost/equal-benefit case is not a reason to spend.
    """
    reasons: list[str] = []
    worth_it = True

    specialized = estimated_cost_units > 1.0 or len(str(objective).split()) > 12
    if specialized:
        reasons.append("specialization_useful")
    else:
        reasons.append("task_is_simple")
        worth_it = False

    if independent_items > 1:
        reasons.append(f"independent_work={independent_items}")
    else:
        reasons.append("no_independent_work")

    if context_isolation_helps:
        reasons.append("context_isolation_helps")
    else:
        reasons.append("context_isolation_not_needed")

    if expected_benefit_units > estimated_cost_units:
        reasons.append(f"benefit_exceeds_cost({expected_benefit_units}>{estimated_cost_units})")
    else:
        reasons.append(f"benefit_does_not_exceed_cost({expected_benefit_units}<={estimated_cost_units})")
        worth_it = False

    if needs_reviewer:
        reasons.append("independent_reviewer_required")
    else:
        reasons.append("no_reviewer_required")

    return SpecializationVerdict(
        should_specialize=worth_it,
        reasons=reasons,
        proposed_role=_infer_role(objective, needs_review=needs_reviewer) if worth_it else "",
    )


@dataclass
class DelegationRefusal:
    """A bounded delegation that was refused, with the real reason.

    ``code`` is a string rather than a bare message so a caller can branch on it
    the way ``alpha.bots.delegation.GUARD_REFUSAL_CODES`` already allows.
    """

    code: str
    detail: str
    source: str = "apex"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "source": self.source}


@dataclass
class AgentSpawnResult:
    """What a spawn actually produced.

    ``ok=False`` carries the refusal. There is no path that returns a
    half-created agent, because the two owners APEX composes both roll back on
    failure — a result claiming success here would be the fake-agent the spec
    forbids.
    """

    ok: bool
    agent_id: str = ""
    role: str = ""
    bot_name: str = ""
    refusal: DelegationRefusal | None = None
    lease_id: str = ""
    depth: int = 0
    isolated_workspace: bool = False
    warnings: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "agent_id": self.agent_id,
            "role": self.role,
            "bot_name": self.bot_name,
            "refusal": self.refusal.to_dict() if self.refusal else None,
            "lease_id": self.lease_id,
            "depth": self.depth,
            "isolated_workspace": self.isolated_workspace,
            "warnings": list(self.warnings),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def resolve_delegation_limits(contract: AutonomyContract) -> tuple[int, int, int]:
    """Intersect the contract's depth with the subagent manager's own ceiling.

    Returns ``(max_depth, max_children, max_total)``. The depth is
    ``min(contract, SubagentLifecycleManager.DEFAULT_MAX_DEPTH)`` because
    raising APEX's ceiling must not be a way *out* of the subagent manager's
    limit — whichever bound is lower wins, and this function is where that is
    decided rather than at three call sites.
    """
    from alpha.subagents.lifecycle import SubagentLifecycleManager

    manager_depth = int(getattr(SubagentLifecycleManager, "DEFAULT_MAX_DEPTH", 3))
    contract_depth = contract.budget.max_delegation_depth
    max_depth = manager_depth if contract_depth is None else min(int(contract_depth), manager_depth)
    # A zero budget is an explicit deny. Finite per-session delegation limits
    # intersect with hard engine admission caps.
    from alpha.config.subagents_config import MAX_CONCURRENT_SUBAGENT_CALLS, MAX_TOTAL_SUBAGENTS_PER_RUN

    parallel_limit = contract.budget.max_parallel_tasks
    active_limit = contract.budget.max_active_agents
    max_children = min(
        MAX_CONCURRENT_SUBAGENT_CALLS,
        parallel_limit if parallel_limit is not None else MAX_CONCURRENT_SUBAGENT_CALLS,
        active_limit if active_limit is not None else MAX_TOTAL_SUBAGENTS_PER_RUN,
    )
    max_total = min(active_limit if active_limit is not None else MAX_TOTAL_SUBAGENTS_PER_RUN, MAX_TOTAL_SUBAGENTS_PER_RUN)
    return max_depth, max_children, max_total


class ApexAgentFactory:
    """Creates and reclaims the temporary specialists APEX decides to need.

    Thin by design: it decides *whether* and *which role*, then hands the actual
    record, lease and profile to the owners above.
    """

    def __init__(self, contract: AutonomyContract, *, lifecycle: Any | None = None, ephemeral: Any | None = None) -> None:
        self._contract = contract
        self._lifecycle = lifecycle
        self._ephemeral = ephemeral
        #: Live agents this factory created, for termination and reporting.
        self._agents: dict[str, ApexAgentSpec] = {}

    # -- owner resolution (each fails closed) --------------------------------

    def _resolve_lifecycle(self) -> Any | None:
        if self._lifecycle is not None:
            return self._lifecycle
        try:
            from alpha.subagents.lifecycle import get_subagent_lifecycle_manager

            self._lifecycle = get_subagent_lifecycle_manager()
        except Exception as exc:
            logger.warning("APEX agent factory cannot reach the subagent lifecycle manager: %s", exc)
            self._lifecycle = None
        return self._lifecycle

    def _resolve_ephemeral(self) -> Any | None:
        if self._ephemeral is not None:
            return self._ephemeral
        try:
            from alpha.bots.ephemeral import get_ephemeral_manager

            self._ephemeral = get_ephemeral_manager()
        except Exception as exc:
            # Not fatal: the lifecycle record and lease are the load-bearing
            # parts, and an absent ephemeral pool must not block agent creation.
            logger.info("APEX agent factory has no ephemeral bot pool: %s", exc)
            self._ephemeral = None
        return self._ephemeral

    # -- spawning -------------------------------------------------------------

    def spawn(
        self,
        spec: ApexAgentSpec,
        *,
        parent_agent_id: str = "apex",
        depth: int = 1,
        ttl_seconds: int = 3600,
    ) -> AgentSpawnResult:
        """Create one specialist, or return the real refusal.

        Never reports a success it did not get: every step either produces a
        concrete id from an owner, or the result carries a refusal.
        """
        started = time.perf_counter()

        if not self._contract.enabled:
            return AgentSpawnResult(
                ok=False,
                role=spec.role,
                refusal=DelegationRefusal(
                    code="apex_profile_off",
                    detail="the active profile grants no agent creation authority",
                    source="apex.contract",
                ),
            )

        max_depth, max_children, max_total = resolve_delegation_limits(self._contract)
        if depth > max_depth:
            return AgentSpawnResult(
                ok=False,
                role=spec.role,
                depth=depth,
                refusal=DelegationRefusal(
                    code="delegation_depth_ceiling",
                    detail=(f"requested depth {depth} exceeds the effective ceiling {max_depth} (contract={self._contract.budget.max_delegation_depth}, subagent manager=SubagentLifecycleManager.DEFAULT_MAX_DEPTH)"),
                    source="apex.factory",
                ),
            )

        agent_capacity = min(max_children, max_total)
        if len(self._agents) >= agent_capacity:
            return AgentSpawnResult(
                ok=False,
                role=spec.role,
                refusal=DelegationRefusal(
                    code="agent_population_ceiling",
                    detail=f"{len(self._agents)} agents already live against the effective ceiling of {agent_capacity} (parallel={max_children}, active={max_total})",
                    source="apex.factory",
                ),
            )

        lifecycle = self._resolve_lifecycle()
        if lifecycle is None:
            return AgentSpawnResult(
                ok=False,
                role=spec.role,
                refusal=DelegationRefusal(
                    code="agent_runtime_unavailable",
                    detail="alpha.subagents.lifecycle is unreachable, so no lease could be issued",
                    source="apex.factory",
                ),
            )

        role = ApexAgentRole.parse(spec.role)
        # Spec §20: a mutating role gets an isolated workspace unless the caller
        # already supplied one. Two mutating agents on one tree is the exact
        # corruption the spec forbids.
        isolated = spec.requires_isolated_workspace and not spec.workspace
        workspace_mode = "isolated" if isolated else (spec.workspace and "shared" or "isolated")

        try:
            from alpha.subagents.lifecycle import SubagentContract

            record = lifecycle.spawn_subagent(
                parent_agent_id=parent_agent_id,
                contract=SubagentContract(
                    objective=spec.objective,
                    role=role,
                    instructions=spec.instructions,
                    model_override=spec.model or None,
                    skills=list(spec.skills),
                    tools=list(spec.tools),
                    workspace_mode=workspace_mode,
                    workspace_path=spec.workspace or None,
                    permissions=list(spec.permissions),
                    timeout_seconds=spec.timeout_seconds,
                    context_mode=spec.context_mode,
                    max_attempts=spec.max_attempts,
                ),
                parent_task_id=spec.task_id or None,
                depth=depth,
            )
        except ValueError as exc:
            # The manager's own ceilings raise ValueError; surface its refusal
            # verbatim rather than rewriting it into APEX's vocabulary.
            return AgentSpawnResult(
                ok=False,
                role=role,
                depth=depth,
                refusal=DelegationRefusal(code="delegation_refused_by_owner", detail=str(exc), source="alpha.subagents.lifecycle"),
            )
        except Exception as exc:
            return AgentSpawnResult(
                ok=False,
                role=role,
                depth=depth,
                refusal=DelegationRefusal(
                    code="agent_spawn_failed",
                    detail=f"{type(exc).__name__}: {exc}",
                    source="alpha.subagents.lifecycle",
                ),
            )

        agent_id = str(getattr(record, "subagent_id", "") or f"sub-{uuid.uuid4().hex[:8]}")
        lease = getattr(record, "lease", None)
        warnings: list[str] = []
        bot_name = ""

        ephemeral = self._resolve_ephemeral()
        if ephemeral is not None:
            try:
                profile = ephemeral.spawn_specialist(
                    spec.role,
                    spec.objective,
                    ttl_seconds=ttl_seconds,
                    parent_bot=parent_agent_id,
                    model=spec.model or None,
                )
                bot_name = str(getattr(profile, "name", "") or "")
            except Exception as exc:
                # The record and its lease already exist, so this is a warning
                # rather than a failed spawn — but it is disclosed, because a
                # specialist with no bot profile is a different thing from one
                # with it.
                warnings.append(f"ephemeral pool refused the bot profile: {type(exc).__name__}: {exc}")

        spec.role = role
        self._agents[agent_id] = spec
        return AgentSpawnResult(
            ok=True,
            agent_id=agent_id,
            role=role,
            bot_name=bot_name,
            lease_id=str(getattr(lease, "lease_id", "") or ""),
            depth=depth,
            isolated_workspace=isolated,
            warnings=warnings,
            elapsed_seconds=time.perf_counter() - started,
        )

    # -- lifecycle ------------------------------------------------------------

    def active(self) -> list[ApexAgentSpec]:
        """Specs for agents this factory created and has not retired."""
        return list(self._agents.values())

    def retire(self, agent_id: str, *, outcome: str = "task_complete", detail: str = "") -> dict[str, Any]:
        """Terminate one agent through its owner, then drop the spec.

        ``outcome`` is ``task_complete`` or ``cancelled``. Either way the record
        leaves ``RUNNING``: an agent left running with no task is the orphan
        spec §102 exists to prevent.
        """
        spec = self._agents.pop(agent_id, None)
        lifecycle = self._resolve_lifecycle()
        archived = False
        error = ""

        if lifecycle is not None:
            try:
                if outcome == "task_complete":
                    # The owner requires both fields; building it via keyword so
                    # a future required field fails loudly here rather than
                    # silently falling back to `cancel_subagent`.
                    from alpha.subagents.lifecycle import SubagentDeliverable

                    lifecycle.complete_subagent(
                        agent_id,
                        SubagentDeliverable(
                            status="completed",
                            summary=detail or (f"goal {spec.goal_id} satisfied" if spec else "task complete"),
                        ),
                    )
                else:
                    lifecycle.cancel_subagent(agent_id, reason=detail or "cancelled by executive")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.warning("APEX retire(%s) did not reach the owner: %s", agent_id, error)

        # Verify the retirement landed. A swallowed owner exception would leave
        # the agent RUNNING with no task — the orphan spec §102 exists to
        # prevent — while `retire` still returned a dict that looked finished.
        # Re-checking the owner's own state is what makes this a real
        # termination rather than a reported one.
        #
        # An agent this factory never created AND that the owner has no record
        # of is reported ``retired: False`` with a reason. "There is no record"
        # is not the same claim as "I retired it", and collapsing them lets a
        # caller count a successful teardown of work that never ran.
        retired = False
        if spec is None and lifecycle is not None:
            try:
                if lifecycle.get_subagent(agent_id) is None:
                    error = f"no agent {agent_id!r} is known to this factory or its owner; nothing was retired"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        elif lifecycle is not None and not error:
            try:
                record = lifecycle.get_subagent(agent_id)
                status = str(getattr(record.status, "value", record.status)) if record is not None else "gone"
                retired = record is None or status in TERMINAL_SUBAGENT_STATUSES
                if not retired:
                    error = f"agent {agent_id!r} is still '{status}' after retirement was requested"
            except Exception as exc:
                error = error or f"post-retire verification failed: {type(exc).__name__}: {exc}"

        ephemeral = self._resolve_ephemeral()
        bot_name = ""
        if spec is not None:
            bot_name = f"apex-{spec.role}-{agent_id[-6:]}"
        if ephemeral is not None and bot_name:
            try:
                ephemeral.archive_specialist(bot_name, reason=detail or outcome)
                archived = True
            except Exception as exc:
                error = error or f"ephemeral archive failed: {type(exc).__name__}: {exc}"

        return {
            "agent_id": agent_id,
            "outcome": outcome,
            #: True only after the owner's own record is observed terminal. A
            #: caller can therefore distinguish "retired" from "reported".
            "retired": retired,
            "archived": archived,
            "known_to_factory": spec is not None,
            "error": error,
        }

    def retire_all(self, *, outcome: str = "task_complete", detail: str = "") -> list[dict[str, Any]]:
        """Terminate every agent this factory created.

        Bounded by the number of tracked agents, so it cannot be an unbounded
        teardown; a factory with thousands of agents would be a different bug
        that this does not mask.
        """
        return [self.retire(agent_id, outcome=outcome, detail=detail) for agent_id in list(self._agents)]

    def reconcile(self) -> dict[str, Any]:
        """Ask the lifecycle owner about stalls and expired leases.

        Delegates to ``check_liveness_and_stalls`` rather than re-deriving
        staleness: the lease semantics live there, and a second copy would drift
        from it exactly when it matters (spec §28).
        """
        lifecycle = self._resolve_lifecycle()
        if lifecycle is None:
            return {"available": False, "reason": "alpha.subagents.lifecycle is unreachable"}
        try:
            report = lifecycle.check_liveness_and_stalls()
        except Exception as exc:
            return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
        return {"available": True, "report": report, "tracked_here": len(self._agents)}

    def recover_after_restart(self) -> dict[str, Any]:
        """Reconcile the owner's leases after a process restart (spec §29/§84)."""
        lifecycle = self._resolve_lifecycle()
        if lifecycle is None:
            return {"available": False, "reason": "alpha.subagents.lifecycle is unreachable"}
        try:
            return {"available": True, "recovery": lifecycle.recover_after_restart()}
        except Exception as exc:
            return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
