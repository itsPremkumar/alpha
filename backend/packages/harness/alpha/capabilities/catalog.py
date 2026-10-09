"""Catalogue of Alpha's optional (opt-in) subsystems.

Every entry here is a complete, tested implementation that used to have **no
production reference** — only its own test suite imported it. Declaring it here
makes it a first-class, loadable capability:

* the dotted target is a real production reference, so the orphan-module guard
  stops flagging the module;
* :func:`alpha.capabilities.registry.load_enabled_capabilities` imports and
  instantiates it when an operator opts in;
* :func:`alpha.capabilities.registry.status` reports it to
  ``GET /api/ops/integration-health`` and therefore to the UI.

Adding a subsystem: append a :class:`CapabilitySpec` — nothing else needs to
change (the loader, the status endpoint, the manifest and the UI all read this
catalogue).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CapabilitySpec(BaseModel):
    """One optional subsystem and how to reach it."""

    module: str = Field(description="Importable module path, e.g. ``alpha.mcp.gateway``.")
    target: str = Field(description="Symbol inside ``module``: a class or a factory function.")
    description: str = Field(description="Operator-facing one-liner.")
    kind: str = Field(default="engine", description="Grouping label for the UI (engine, middleware, guard, router, utility).")
    default_enabled: bool = Field(default=False, description="Whether the capability loads when the operator is silent.")

    @property
    def dotted_target(self) -> str:
        return f"{self.module}:{self.target}"


CAPABILITY_CATALOG: dict[str, CapabilitySpec] = {
    "company_os": CapabilitySpec(
        module="alpha.company_os.service",
        target="CompanyService",
        description="Durable multi-tenant Company OS: charter, org chart, workforce, portfolio and a bounded perpetual loop.",
        kind="engine",
    ),
    "company_os_orchestrator": CapabilitySpec(
        module="alpha.company_os.orchestrator",
        target="step",
        description="One bounded, model-free Company OS tick: gate, observe, plan, act, verify, ledger.",
        kind="engine",
    ),
    "teammate_mesh": CapabilitySpec(
        module="alpha.bots.teammate_mesh",
        target="AutonomousTeammateMesh",
        description="Autonomous teammate mesh & bot loop guard.",
        kind="guard",
    ),
    "micro_compaction": CapabilitySpec(
        module="alpha.context.micro_compaction",
        target="apply_micro_compaction",
        description="Micro-compaction of oversized tool output into rolling semantic receipts.",
        kind="utility",
    ),
    "moa_engine": CapabilitySpec(
        module="alpha.deliberation.moa_engine",
        target="MoAEngine",
        description="Mixture-of-Agents multi-model advisory engine.",
        kind="engine",
    ),
    "promptbreeder": CapabilitySpec(
        module="alpha.evolution.promptbreeder",
        target="PromptbreederEngine",
        description="Darwinian prompt-evolution engine for self-improving prompts.",
        kind="engine",
    ),
    "retrospective_engine": CapabilitySpec(
        module="alpha.evolution.retrospective_engine",
        target="RetrospectiveEngine",
        description="Autonomous retrospective & prompt/skill self-evolution engine.",
        kind="engine",
    ),
    "github_bridge": CapabilitySpec(
        module="alpha.integrations.github_bridge",
        target="GitHubWorkforceBridge",
        description="Bi-directional GitHub webhook bridge to the autonomous workforce.",
        kind="engine",
    ),
    "stop_guard": CapabilitySpec(
        module="alpha.kanban.stop_guard",
        target="build_stop_nudge",
        description="Kanban turn-end guard: end with a terminal call or keep going.",
        kind="guard",
    ),
    "autonomous_curator": CapabilitySpec(
        module="alpha.learning.autonomous_curator",
        target="AutonomousSkillCurator",
        description="Autonomous skill curator over the skill lifecycle.",
        kind="engine",
    ),
    "autonomous_learning_graph": CapabilitySpec(
        module="alpha.learning.autonomous_learning_graph",
        target="AutonomousLearningGraph",
        description="Autonomous learning graph over nodes and edges.",
        kind="engine",
    ),
    "memory_nudges": CapabilitySpec(
        module="alpha.learning.nudges",
        target="build_memory_nudge",
        description="Memory persistence nudges for long-running agents.",
        kind="utility",
    ),
    "mcp_gateway": CapabilitySpec(
        module="alpha.mcp.gateway",
        target="UniversalMCPGateway",
        description="Universal Model Context Protocol gateway (host & client).",
        kind="engine",
    ),
    "active_memory": CapabilitySpec(
        module="alpha.memory.active_memory",
        target="ActiveMemoryRouter",
        description="Active-memory two-tier escalation router.",
        kind="engine",
    ),
    "local_llm_failover": CapabilitySpec(
        module="alpha.models.local_llm_failover",
        target="LocalLLMFailoverRouter",
        description="Local LLM failover & hybrid cost router.",
        kind="router",
    ),
    "dag_orchestrator": CapabilitySpec(
        module="alpha.planning.dag_orchestrator",
        target="DynamicDagOrchestrator",
        description="Dynamic DAG workflow compiler and multi-wave orchestrator.",
        kind="engine",
    ),
    "message_converters": CapabilitySpec(
        module="alpha.runtime.converters",
        target="langchain_messages_to_openai",
        description="LangChain -> OpenAI Chat Completions message converters.",
        kind="utility",
    ),
    "lane_scheduler": CapabilitySpec(
        module="alpha.runtime.lane_scheduler",
        target="WriterFence",
        description="Multi-lane execution scheduler with transactional writer lease fence.",
        kind="engine",
    ),
    "canary_sandbox": CapabilitySpec(
        module="alpha.safety.canary_sandbox",
        target="ASTSafetyInvariantChecker",
        description="AST safety invariant checker and ephemeral canary sandboxing.",
        kind="guard",
    ),
    "net_policy": CapabilitySpec(
        module="alpha.safety.net_policy",
        target="NetworkPolicyGuard",
        description="Network policy & SSRF egress filtering guard.",
        kind="guard",
    ),
    "self_repo_guard": CapabilitySpec(
        module="alpha.safety.self_repo_guard",
        target="SelfRepoGuard",
        description="Self-repo mutation guard for the active runtime checkout.",
        kind="guard",
    ),
    "container_runner": CapabilitySpec(
        module="alpha.sandbox.container_runner",
        target="ContainerSandboxRunner",
        description="Containerized sandbox execution runner.",
        kind="engine",
    ),
    "mcp_lifecycle": CapabilitySpec(
        module="alpha.skills.mcp_lifecycle",
        target="SkillMcpLifecycleManager",
        description="Skill-embedded on-demand MCP lifecycle manager.",
        kind="engine",
    ),
    "yield_handoff": CapabilitySpec(
        module="alpha.subagents.yield_handoff",
        target="SubagentYieldRegistry",
        description="Subagent yield & settle handoff protocol registry.",
        kind="engine",
    ),
    "cnp_auction": CapabilitySpec(
        module="alpha.swarm.cnp_auction",
        target="ContractNetAuctionEngine",
        description="Contract Net Protocol multi-agent auction engine for large swarms.",
        kind="engine",
    ),
    "trajectory_compressor": CapabilitySpec(
        module="alpha.trajectory.trajectory_compressor",
        target="TrajectoryCompactor",
        description="Atomic trajectory compressor.",
        kind="utility",
    ),
    "sdlc_engine": CapabilitySpec(
        module="alpha.workflow.sdlc_engine",
        target="DocumentGatedSDLCEngine",
        description="Document-gated multi-agent SDLC engine.",
        kind="engine",
    ),
    "rsi_engine": CapabilitySpec(
        module="alpha.rsi.engine",
        target="RSIEngine",
        description="Recursive Self-Improvement closed-loop autonomous engine.",
        kind="engine",
    ),
    "adaptive_autonomy": CapabilitySpec(
        module="alpha.security.autonomy.policy_engine",
        target="AutonomyPolicyEngine",
        description="Adaptive autonomy 4-tier governance policy engine.",
        kind="guard",
    ),
    "dynamic_workflow_engine": CapabilitySpec(
        module="alpha.workflow.runtime",
        target="DynamicWorkflowEngine",
        description="Adaptive, durable dynamic workflow execution engine.",
        kind="engine",
    ),
    "bot_clone_engine": CapabilitySpec(
        module="alpha.bots.cloning",
        target="BotCloneEngine",
        description="Autonomous Bot cloning, specialist forking, and generational breeding engine.",
        kind="engine",
    ),
    "intent_goal_engine": CapabilitySpec(
        module="alpha.orchestration.intent",
        target="IntentGoalEngine",
        description="Universal prompt perception, slash-command auto-resolution & goal decomposition engine.",
        kind="engine",
    ),
    "system1_reflex": CapabilitySpec(
        module="alpha.system1.engine",
        target="System1Engine",
        description="Dual-process System 1 fast reflex decision harness (Jev cloud + local free CPU classifier).",
        kind="engine",
    ),
    "os_computer_use": CapabilitySpec(
        module="alpha.computer_use",
        target="LaptopController",
        description="Free local-first OS computer use: accessibility-tree grounding, guarded input dispatch, screenshots, sentinel safety.",
        kind="engine",
    ),
    "durable_workflow_state": CapabilitySpec(
        module="alpha.workflow.event_log",
        target="DurableEventLog",
        description="Append-only durable event log, run projections and graph-revision history for the DWE (kill-and-resume hydration).",
        kind="engine",
    ),
    "workflow_registry": CapabilitySpec(
        module="alpha.workflow.registry",
        target="WorkflowRegistry",
        description="Read-only discovery registries (capabilities, tools, skills, MCP servers, memory) behind one list/describe/health plane for dynamic-workflow planning.",
        kind="utility",
    ),
    # --- Runtime-correctness wave -----------------------------------------
    # These earn a production reference so `test_no_orphan_modules` can see them
    # wired. They are NOT enabled by default: a capability id only declares that
    # the module is importable and reachable, never that it performs work.
    "workflow_execution_primitives": CapabilitySpec(
        module="alpha.workflow.execution",
        target="execute_wave",
        description="Bounded workflow execution primitives: deadline-bounded node calls, a bounded wave executor, and per-run concurrency admission.",
        kind="utility",
    ),
    "workflow_observability": CapabilitySpec(
        module="alpha.workflow.observability",
        target="build_run_observability",
        description="Measured workflow observability projected from the event log: per-node timing, critical path, and wave shape.",
        kind="utility",
    ),
    "workflow_time_travel": CapabilitySpec(
        module="alpha.workflow.time_travel",
        target="fork_run",
        description="Workflow forking, time travel, and side-effect-free dry-run simulation over the append-only event log.",
        kind="utility",
    ),
    "workflow_templates": CapabilitySpec(
        module="alpha.workflow.templates",
        target="TemplateStore",
        description="Workflow template library with an evidence-gated draft -> verified -> promoted lifecycle.",
        kind="utility",
    ),
    "workflow_self_improvement": CapabilitySpec(
        module="alpha.workflow.self_improvement",
        target="suggest_improvements",
        description="Evidence-cited workflow improvement proposals; proposes only, never mutates a run or graph.",
        kind="utility",
    ),
    "workflow_domain_executors": CapabilitySpec(
        module="alpha.orchestrator.domain_executors",
        target="bind_domain_executors",
        description="Opt-in node executors that perform real work through the model factory, the guarded tool bridge, and the subagent executor.",
        kind="engine",
    ),
    # --- User-facing workspace layer --------------------------------------
    # Pure-stdlib, dependency-free modules. Registering each gives it a
    # production reference (so `test_no_orphan_modules` sees it wired) and
    # surfaces it on `/api/ops/integration-health`. None is enabled by default:
    # a capability id declares reachability, never that work happens.
    "routine_capture": CapabilitySpec(
        module="alpha.routines",
        target="RoutineStore",
        description="Demonstration-captured, parameterised, replayable routines with a durable store.",
        kind="engine",
    ),
    "connector_marketplace": CapabilitySpec(
        module="alpha.connectors",
        target="ConnectorCatalog",
        description="Curated connector marketplace catalog plus durable install/health state.",
        kind="utility",
    ),
    "egress_policy": CapabilitySpec(
        module="alpha.egress",
        target="EgressPolicy",
        description="Domain egress routing policy and browser-profile references (no secrets stored).",
        kind="guard",
    ),
    "work_modes": CapabilitySpec(
        module="alpha.modes",
        target="WorkMode",
        description="Ask/Plan/Craft/Coding work modes as an enforceable capability policy.",
        kind="guard",
    ),
    "expert_catalog": CapabilitySpec(
        module="alpha.experts",
        target="ExpertCatalog",
        description="Role-based Expert catalog and multi-expert Expert Group pipelines.",
        kind="utility",
    ),
    "automation_scheduler": CapabilitySpec(
        module="alpha.automations",
        target="AutomationStore",
        description="RRULE-style scheduled automations (once/daily/weekly/monthly/yearly) with a durable store.",
        kind="engine",
    ),
    "skill_marketplace": CapabilitySpec(
        module="alpha.skills_market",
        target="SkillMarketCatalog",
        description="Browsable Skill Marketplace listings plus durable install/enable state.",
        kind="utility",
    ),
    "agent_workspace": CapabilitySpec(
        module="alpha.workspace",
        target="AgentWorkspace",
        description="Unified workspace facade composing modes, experts, automations, connectors, egress, routines and the scorecard.",
        kind="engine",
    ),
    "capability_scorecard": CapabilitySpec(
        module="alpha.scorecard",
        target="scan",
        description="Frontier agent capability taxonomy scanner and coverage report.",
        kind="utility",
    ),
    "code_critic": CapabilitySpec(
        module="alpha.critique",
        target="critique_path",
        description="Deterministic AST code critic (8 rules) with a severity-ranked report.",
        kind="utility",
    ),
    # --- Grounding layer -------------------------------------------------
    # Claim ledger, solvability gate, capability manifest, and the per-step
    # deterministic-first gates. Wired on the agent path by
    # `agents/middlewares/grounding_middleware.py`, which the lead middleware
    # chain appends -- so this id declares reachability *and* has a live consumer.
    # Not default-enabled because manifest injection changes every prompt's shape;
    # the per-step gates are unconditional in code and stay on either way.
    "grounding": CapabilitySpec(
        module="alpha.grounding.service",
        target="GroundingService",
        description=(
            "Grounding layer: a live capability manifest the agent reads, a claim ledger with support status and "
            "blast radius, a solvability gate that names the way out, deterministic-first per-step verification "
            "gates, and a bounded effort brake."
        ),
        kind="engine",
    ),
    "grounding_manifest": CapabilitySpec(
        module="alpha.grounding.manifest",
        target="build_manifest",
        description=("Task-filtered capability manifest projected from live registries, gated by the static wired/unwired audit so a documented-but-uncalled subsystem is disclosed as unwired instead of advertised."),
        kind="utility",
    ),
    "mod_kernel": CapabilitySpec(
        module="alpha.mods",
        target="get_mod_kernel",
        description="Alpha Mod Kernel (AMK): ordered event middleware pipeline, sandboxed capability context, and autonomous execution governance spine.",
        kind="engine",
    ),
}


def capability_ids() -> list[str]:
    """All known capability ids, sorted."""
    return sorted(CAPABILITY_CATALOG)
