"""Autonomous Dispatch Bridge: Bridges strategic MetaPlans to working execution subsystems.

Every dispatcher performs REAL work through its target subsystem and reports
only what actually happened:

- SWARM: real ``SwarmCoordinator`` create + first step; the artifact is the
  coordinator's actual checkpoint file.
- BOT_PROFILE: a real ``execute_handoff`` into the bot registry (status
  ``dispatched`` — the handoff being accepted is not task completion).
- MOA: a real ensemble deliberation through ``MasterDeliberationEngine``.
- DEEP_RESEARCH: a real ``DeepResearchEngine`` run over live search/fetch
  backends; counts come from the report that was actually produced.
- DEEP_THINK: one real extended-reflection model call; the trace file is the
  model's verbatim output.
- SUBAGENT: a real ``HierarchicalDelegationEngine.delegate`` call; status is
  mapped from the returned contract (``SUCCESS``/``PARTIAL_PROGRESS``/
  ``UNRECOVERABLE_ERROR`` → ``completed``/``partial``/``failed``).
- DIRECT_AGENT: one real model call; the summary is the verbatim output.

Historically six of these seven dispatchers returned a canned
``status="completed"`` with invented metrics (``sources_inspected: 12``,
``confidence_score: 0.98``, ``consensus_reached: True``) and artifact file
names that were never written. Now any exception is converted into an honest
``status="failed"`` result carrying the real error message, so callers never
see a fabricated success.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.deliberation import invocation as model_invocation
from alpha.planning.meta_planner import ExecutionParadigm, MetaPlan
from alpha.swarm.coordinator import get_swarm_coordinator
from alpha.swarm.models import SwarmMode

logger = logging.getLogger(__name__)

# System prompt for the DEEP_THINK dispatcher: a real extended-reflection
# call whose output is persisted verbatim as the reasoning trace.
_DEEP_THINK_SYSTEM = (
    "You are a meticulous deep-reasoning analyst. Work through the problem step by step: "
    "state assumptions explicitly, explore hypotheses, trace each inference to its basis, "
    "critique the weakest step, and finish with one clear final position. Show your full "
    "reasoning trace; never invent sources, measurements, or results you did not derive."
)


@dataclass
class DispatchResult:
    dispatch_id: str
    plan_id: str
    paradigm: str
    status: str  # 'dispatched', 'completed', 'partial', 'blocked_human_gate', 'failed'
    execution_id: str | None
    assigned_agents: list[str] = field(default_factory=list)
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _artifact_dir():
    """Writable directory for artifacts this bridge actually creates."""
    directory = runtime_home() / "plan_mode"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_artifact(name: str, content: str) -> str:
    """Persist a real artifact file and return its path."""
    path = _artifact_dir() / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def _run_async(coro):
    """Drive a coroutine from synchronous dispatch code, in any thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _model_call(system: str, user: str) -> tuple[str, str]:
    """One real chat call through the shared deliberation invocation seam.

    Returns ``(model_name, exact_output)``. Raises ``RuntimeError`` when no
    chat models are configured so dispatch fails honestly instead of
    fabricating output.
    """
    roster = model_invocation.configured_model_roster()
    if not roster:
        raise RuntimeError("No chat models configured; this dispatch cannot run.")
    model_name = roster[0]
    return model_name, model_invocation.invoke_model(model_name, system=system, user=user)


def _build_research_engine():
    """Construct the deep-research engine (live backends; raises honestly)."""
    from alpha.research.engine import DeepResearchEngine

    return DeepResearchEngine()


class AutonomousDispatchBridge:
    """Dispatches MetaPlans to their concrete execution subsystems."""

    @classmethod
    def dispatch(cls, plan: MetaPlan, async_mode: bool = False) -> DispatchResult:
        """Synchronously dispatches the plan or registers background dispatch.

        Any exception raised by a subsystem is returned as an honest
        ``status="failed"`` result (never an invented success).
        """
        dispatch_id = f"disp-{uuid.uuid4().hex[:8]}"
        paradigm = plan.decision.paradigm

        try:
            # 0. Safety Invariant: Gate High-Risk Tiers (R5/R6)
            if plan.decision.risk_tier in ("R5", "R6") or plan.status == "blocked":
                return DispatchResult(
                    dispatch_id=dispatch_id,
                    plan_id=plan.plan_id,
                    paradigm=paradigm.value,
                    status="blocked_human_gate",
                    execution_id=None,
                    assigned_agents=plan.decision.assigned_specialists,
                    summary=f"Execution blocked by Risk Gate ({plan.decision.risk_tier}). Human authorization required.",
                    artifacts=[],
                    details={
                        "risk_tier": plan.decision.risk_tier,
                        "reason": "Destructive or production-impacting operation detected; requires explicit signoff.",
                        "proof_obligations": plan.proof_obligations,
                    },
                )

            # 1. SWARM PARADIGM
            if paradigm == ExecutionParadigm.SWARM:
                return cls._dispatch_swarm(plan, dispatch_id, async_mode)

            # 2. BOT PROFILE PARADIGM
            if paradigm == ExecutionParadigm.BOT_PROFILE:
                return cls._dispatch_bot_profile(plan, dispatch_id)

            # 3. MIXTURE OF AGENTS (MoA) PARADIGM
            if paradigm == ExecutionParadigm.MOA:
                return cls._dispatch_moa(plan, dispatch_id)

            # 4. DEEP RESEARCH PARADIGM
            if paradigm == ExecutionParadigm.DEEP_RESEARCH:
                return cls._dispatch_deep_research(plan, dispatch_id)

            # 5. DEEP THINK PARADIGM
            if paradigm == ExecutionParadigm.DEEP_THINK:
                return cls._dispatch_deep_think(plan, dispatch_id)

            # 6. SUBAGENT PARADIGM
            if paradigm == ExecutionParadigm.SUBAGENT:
                return cls._dispatch_subagent(plan, dispatch_id)

            # 7. DIRECT AGENT PARADIGM (DEFAULT)
            return cls._dispatch_direct(plan, dispatch_id)
        except Exception as exc:
            logger.exception("Dispatch %s failed for plan %s", dispatch_id, plan.plan_id)
            return DispatchResult(
                dispatch_id=dispatch_id,
                plan_id=plan.plan_id,
                paradigm=paradigm.value,
                status="failed",
                execution_id=None,
                assigned_agents=plan.decision.assigned_specialists,
                summary=f"{type(exc).__name__}: {exc}",
                artifacts=[],
                details={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "risk_tier": plan.decision.risk_tier,
                },
            )

    @classmethod
    async def dispatch_async(cls, plan: MetaPlan) -> DispatchResult:
        """Async dispatch entry point for Gateway coroutines."""
        return await asyncio.to_thread(cls.dispatch, plan, True)

    # -------------------------------------------------------------------------
    # Internal Dispatchers
    # -------------------------------------------------------------------------

    @classmethod
    def _dispatch_swarm(cls, plan: MetaPlan, dispatch_id: str, async_mode: bool) -> DispatchResult:
        coordinator = get_swarm_coordinator()
        mode = plan.decision.swarm_mode or SwarmMode.AUTO

        # Extract items if mapped tasks exist
        items = []
        for t in plan.execution_waves:
            if "item" in t.objective.lower():
                items.append(t.objective)

        swarm_plan = coordinator.create_swarm(
            goal=plan.prompt,
            mode=mode,
            items=items if items else None,
            max_concurrency=8,
        )

        # Trigger initial execution step
        step_res = coordinator.step(swarm_plan.swarm_id)

        # create_swarm checkpoints the plan to storage_dir/{swarm_id}.json;
        # report that real file instead of the old invented "…_plan.json" name.
        checkpoint = coordinator.storage_dir / f"{swarm_plan.swarm_id}.json"

        return DispatchResult(
            dispatch_id=dispatch_id,
            plan_id=plan.plan_id,
            paradigm=ExecutionParadigm.SWARM.value,
            status="dispatched" if swarm_plan.status == "running" else swarm_plan.status,
            execution_id=swarm_plan.swarm_id,
            assigned_agents=plan.decision.assigned_specialists or ["swarm-worker"],
            summary=f"Autonomous Swarm spawned ({swarm_plan.swarm_id}) in mode '{mode.value}' with {len(swarm_plan.tasks)} DAG tasks.",
            artifacts=[str(checkpoint)] if checkpoint.exists() else [],
            details={
                "swarm_id": swarm_plan.swarm_id,
                "mode": mode.value,
                "initial_step": step_res,
                "estimated_speedup": plan.decision.estimated_speedup,
                "critical_path_seconds": swarm_plan.critical_path_seconds,
            },
        )

    @classmethod
    def _dispatch_bot_profile(cls, plan: MetaPlan, dispatch_id: str) -> DispatchResult:
        from alpha.bots.handoff import execute_handoff

        specialists = plan.decision.assigned_specialists or ["coder"]
        lead_specialist = specialists[0]
        sender = "architect" if lead_specialist != "architect" else "reviewer"

        # A real implementation contract: the compiled MetaPlan report.
        contract_path = _write_artifact("implementation_contract.md", plan.markdown_report)

        package = execute_handoff(
            task_id=plan.plan_id,
            from_bot=sender,
            to_bot=lead_specialist,
            objective=plan.prompt,
            context_summary=(
                f"Autonomous dispatch {dispatch_id} of plan {plan.plan_id} "
                f"(risk {plan.decision.risk_tier}, model tier {plan.decision.model_tier})."
            ),
            artifacts=[contract_path],
            acceptance_criteria=list(plan.proof_obligations),
            handoff_notes=f"workspace isolation: {plan.decision.workspace_isolation}",
        )

        # The handoff being accepted routes the work; the bot executes it
        # asynchronously, so this is "dispatched", not "completed".
        return DispatchResult(
            dispatch_id=dispatch_id,
            plan_id=plan.plan_id,
            paradigm=ExecutionParadigm.BOT_PROFILE.value,
            status="dispatched",
            execution_id=f"handoff-{package.handoff_id}",
            assigned_agents=specialists,
            summary=(
                f"Handoff {package.handoff_id} accepted: @{lead_specialist} now owns the objective "
                f"(from @{sender}); implementation contract written to {contract_path}."
            ),
            artifacts=[contract_path],
            details={
                "lead_bot": lead_specialist,
                "supporting_bots": specialists[1:],
                "from_bot": sender,
                "handoff_id": package.handoff_id,
                "handoff_status": package.status,
                "workspace_isolation": plan.decision.workspace_isolation,
                "model_tier": plan.decision.model_tier,
            },
        )

    @classmethod
    def _dispatch_moa(cls, plan: MetaPlan, dispatch_id: str) -> DispatchResult:
        from alpha.deliberation.engine import get_master_deliberation_engine
        from alpha.deliberation.models import DeliberationStrategy

        engine = get_master_deliberation_engine()
        result = engine.deliberate(
            prompt=plan.prompt,
            strategy=DeliberationStrategy.ENSEMBLE,
        )
        artifact_path = _write_artifact("moa_consensus_synthesis.md", result.final_answer)

        return DispatchResult(
            dispatch_id=dispatch_id,
            plan_id=plan.plan_id,
            paradigm=ExecutionParadigm.MOA.value,
            status="completed",
            execution_id=result.deliberation_id,
            assigned_agents=model_invocation.configured_model_roster(),
            summary=result.final_answer,
            artifacts=[artifact_path],
            details={
                "deliberation_id": result.deliberation_id,
                "strategy_used": result.strategy_used.value,
                "confidence_score": result.confidence_score,
                "consensus_percentage": result.consensus_percentage,
                "verification_status": result.verification_status,
                "verdict_rationale": result.verdict_rationale,
                "duration_seconds": result.duration_seconds,
            },
        )

    @classmethod
    def _dispatch_deep_research(cls, plan: MetaPlan, dispatch_id: str) -> DispatchResult:
        started = time.monotonic()
        engine = _build_research_engine()
        report = _run_async(
            engine.run_research(
                topic=plan.prompt,
                depth=3,
                max_sources=15,
                include_adversarial=True,
            )
        )
        report_path = _write_artifact("deep_research_report.md", report.markdown_content)
        citations_path = _write_artifact(
            "verified_citations.json",
            json.dumps(report.citations, indent=2, ensure_ascii=False),
        )
        duration = round(time.monotonic() - started, 3)

        # Every count below is derived from the report actually produced —
        # the old dispatcher hardcoded 12/8/0 regardless of reality.
        return DispatchResult(
            dispatch_id=dispatch_id,
            plan_id=plan.plan_id,
            paradigm=ExecutionParadigm.DEEP_RESEARCH.value,
            status="completed",
            execution_id=f"research-{uuid.uuid4().hex[:6]}",
            assigned_agents=[],
            summary=(
                f"Deep research completed for '{plan.prompt[:80]}': "
                f"{len(report.sources)} sources, {len(report.citations)} citations, "
                f"{len(report.contradictions)} contradictions in {duration}s."
            ),
            artifacts=[report_path, citations_path],
            details={
                "sources_inspected": len(report.sources),
                "citations_verified": len(report.citations),
                "contradictions_detected": len(report.contradictions),
                "depth": report.depth,
                "duration_seconds": duration,
            },
        )

    @classmethod
    def _dispatch_deep_think(cls, plan: MetaPlan, dispatch_id: str) -> DispatchResult:
        started = time.monotonic()
        model_name, text = _model_call(_DEEP_THINK_SYSTEM, plan.prompt)
        trace_path = _write_artifact("formal_reasoning_trace.md", text)
        duration = round(time.monotonic() - started, 3)

        # No invented reasoning_steps/self_critique_passed/confidence_score:
        # the trace file and model name are what actually exist.
        return DispatchResult(
            dispatch_id=dispatch_id,
            plan_id=plan.plan_id,
            paradigm=ExecutionParadigm.DEEP_THINK.value,
            status="completed",
            execution_id=f"think-{uuid.uuid4().hex[:6]}",
            assigned_agents=[model_name],
            summary=text,
            artifacts=[trace_path],
            details={"model": model_name, "duration_seconds": duration},
        )

    @classmethod
    def _dispatch_subagent(cls, plan: MetaPlan, dispatch_id: str) -> DispatchResult:
        from alpha.subagents.hierarchical_delegator import get_delegation_engine

        agent_type = (plan.decision.assigned_specialists or ["general"])[0]
        engine = get_delegation_engine()
        contract = engine.delegate(agent_type=agent_type, task_description=plan.prompt)
        contract_path = _write_artifact(
            "subagent_output.json",
            json.dumps(contract.to_dict(), indent=2, ensure_ascii=False, default=str),
        )

        contract_status = str(contract.status)
        status = {
            "SUCCESS": "completed",
            "PARTIAL_PROGRESS": "partial",
            "UNRECOVERABLE_ERROR": "failed",
        }.get(contract_status, "failed")

        return DispatchResult(
            dispatch_id=dispatch_id,
            plan_id=plan.plan_id,
            paradigm=ExecutionParadigm.SUBAGENT.value,
            status=status,
            execution_id=f"subagent-{contract.session_id}",
            assigned_agents=[contract.agent_type],
            summary=contract.executive_summary or f"Delegation finished with status {contract_status}.",
            artifacts=[contract_path],
            details={
                "session_id": contract.session_id,
                "agent_type": contract.agent_type,
                "contract_status": contract_status,
            },
        )

    @classmethod
    def _dispatch_direct(cls, plan: MetaPlan, dispatch_id: str) -> DispatchResult:
        started = time.monotonic()
        model_name, text = _model_call(
            "You are a direct, accurate assistant. Answer the objective precisely and completely.",
            plan.prompt,
        )
        duration = round(time.monotonic() - started, 3)

        return DispatchResult(
            dispatch_id=dispatch_id,
            plan_id=plan.plan_id,
            paradigm=ExecutionParadigm.DIRECT_AGENT.value,
            status="completed",
            execution_id=f"direct-{uuid.uuid4().hex[:6]}",
            assigned_agents=[model_name],
            summary=text,
            artifacts=[],
            details={"model": model_name, "duration_seconds": duration},
        )
