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
    "slash_commands": CapabilitySpec(
        module="alpha.commands.registry",
        target="SlashCommandRegistry",
        description="Cataloged slash-command definitions and their executable handler registry.",
        kind="utility",
    ),
}


def capability_ids() -> list[str]:
    """All known capability ids, sorted."""
    return sorted(CAPABILITY_CATALOG)
