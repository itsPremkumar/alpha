"""Dynamic Goal & Task Decomposer Engine.

Translates perceived user intent into a structured, dependency-ordered Goal DAG with:
- Structured task to-do list with explicit input/output contracts
- Automated verification criteria & verification commands
- Saga rollback compensation actions (for transactional rollback on failure)
- Dynamic execution wave partitioning (parallel waves)
- Retry and loop policies
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from alpha.workflow.dynamic_perception import PerceivedIntent
from alpha.workflow.models import LoopPolicy, NodeType, RetryPolicy

# ---------------------------------------------------------------------------
# NodeType mapping (DY-R3 remediation)
# ---------------------------------------------------------------------------
# The old code referenced a nonexistent NodeType.TASK (import-time
# AttributeError). There is no generic "task" member in
# alpha.workflow.models.NodeType; each synthesized task is mapped to the
# semantically correct real member instead:
#
#   task category   -> NodeType   rationale
#   --------------- +----------- + --------------------------------------------------------
#   research        -> TOOL      knowledge discovery run through search/read tools
#   skills          -> TOOL      skill authoring/validation run through skill tools
#   mcp             -> MCP       MCP server negotiation targets MCP infrastructure
#   bots            -> BOT       bot-profile provisioning (the bridge downgrades a
#                                 BOT node to AGENT when no assembled bot matches it)
#   coding          -> AGENT     free-form implementation requires an agent runner
#   verification    -> REVIEW    QA/invariant audit reviewing prior work
#   learning        -> AGENT     memory consolidation runs through an agent runner
#   (anything else) -> AGENT     generic runner-executed work unit
#
# Similarly RetryPolicy now uses the real field names: the old
# RetryPolicy(max_retries=2, backoff_factor=1.5) became
# RetryPolicy(max_attempts=3, backoff="exponential", initial_delay_seconds=1.5)
# (max_retries=2 => 3 total attempts; backoff_factor 1.5 is approximated by
# the supported "exponential" backoff with a 1.5s initial delay).
CATEGORY_NODE_TYPES: dict[str, NodeType] = {
    "research": NodeType.TOOL,
    "skills": NodeType.TOOL,
    "mcp": NodeType.MCP,
    "bots": NodeType.BOT,
    "coding": NodeType.AGENT,
    "verification": NodeType.REVIEW,
    "learning": NodeType.AGENT,
}


@dataclass
class SagaCompensation:
    """Represents a compensation action executed to roll back side effects upon task failure."""

    action_id: str
    target_task_id: str
    action_type: str  # e.g., "revert_git", "release_lease", "teardown_mcp", "clear_cache"
    description: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DynamicTaskItem:
    """A discrete executable work unit in the dynamic goal decomposition."""

    task_id: str
    title: str
    description: str
    category: str
    assigned_role: str
    depends_on: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    expected_outputs: list[str] = field(default_factory=list)
    verification_cmd: str | None = None
    verification_criteria: list[str] = field(default_factory=list)
    retry_policy: RetryPolicy = field(
        default_factory=lambda: RetryPolicy(
            max_attempts=3,  # was max_retries=2 (nonexistent field)
            backoff="exponential",  # was backoff_factor=1.5 (nonexistent field)
            initial_delay_seconds=1.5,
        )
    )
    loop_policy: LoopPolicy | None = None
    compensation: SagaCompensation | None = None
    node_type: NodeType = NodeType.AGENT  # was NodeType.TASK (nonexistent member); see CATEGORY_NODE_TYPES
    write_scope: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["retry_policy"] = self.retry_policy.model_dump()
        if self.loop_policy:
            d["loop_policy"] = self.loop_policy.model_dump()
        d["node_type"] = self.node_type.value
        return d


@dataclass
class DynamicGoal:
    """Top-level goal representation containing tasks, dependencies, waves, and acceptance criteria."""

    goal_id: str
    title: str
    description: str
    intent: PerceivedIntent
    tasks: list[DynamicTaskItem] = field(default_factory=list)
    execution_waves: list[list[str]] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    saga_compensations: list[SagaCompensation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "description": self.description,
            "intent": self.intent.to_dict(),
            "tasks": [t.to_dict() for t in self.tasks],
            "execution_waves": self.execution_waves,
            "acceptance_criteria": self.acceptance_criteria,
            "saga_compensations": [c.to_dict() for c in self.saga_compensations],
            "metadata": self.metadata,
        }

    def get_task(self, task_id: str) -> DynamicTaskItem | None:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None


class DynamicDecomposer:
    """Decomposes a perceived intent and prompt into a robust DAG of tasks with verification & saga rollbacks."""

    def decompose(self, intent: PerceivedIntent, prompt: str) -> DynamicGoal:
        goal_id = f"goal_{uuid.uuid4().hex[:8]}"
        tasks: list[DynamicTaskItem] = []
        compensations: list[SagaCompensation] = []

        # Step 1: Open Source & Research Phase (if requested or boost)
        prev_dep: list[str] = []
        if intent.need_open_source_search or intent.need_web_search:
            research_task_id = "task_01_research_discovery"
            tasks.append(
                DynamicTaskItem(
                    task_id=research_task_id,
                    title="Open Source & Web Knowledge Discovery",
                    description=f"Query open-source packages and technical web sources for domain '{intent.primary_domain}'",
                    category="research",
                    assigned_role="lead_researcher",
                    depends_on=[],
                    inputs={"query": prompt, "primary_domain": intent.primary_domain},
                    expected_outputs=["research_summary", "candidate_repos", "reference_patterns"],
                    verification_cmd="verify_research_coverage",
                    verification_criteria=["At least 2 relevant patterns or references identified", "No obsolete dependencies"],
                    node_type=CATEGORY_NODE_TYPES["research"],
                    write_scope=["research"],
                )
            )
            prev_dep = [research_task_id]

        # Step 2: Dynamic Skill Resolution & Authoring Phase
        if intent.need_skill_creation or intent.need_skill_download:
            skill_task_id = "task_02_skill_resolution"
            comp = SagaCompensation(
                action_id=f"comp_{skill_task_id}",
                target_task_id=skill_task_id,
                action_type="teardown_dynamic_skills",
                description="Unload and deregister dynamically authored skill drafts from active hub",
            )
            compensations.append(comp)
            tasks.append(
                DynamicTaskItem(
                    task_id=skill_task_id,
                    title="Skill Resolution & Dynamic Authoring",
                    description=f"Discover, download from hub, or author specialized skills for '{intent.primary_domain}'",
                    category="skills",
                    assigned_role="skill_curator",
                    depends_on=list(prev_dep),
                    inputs={"domain": intent.primary_domain, "intent": intent.intent_type.value},
                    expected_outputs=["active_skills", "authored_skill_drafts"],
                    verification_cmd="alpha.skills.authoring.validate_skill_draft",
                    verification_criteria=["Authored skills pass AST security scanner", "Skill frontmatter and verification commands validated"],
                    compensation=comp,
                    node_type=CATEGORY_NODE_TYPES["skills"],
                    write_scope=["skills"],
                )
            )
            prev_dep = [skill_task_id]

        # Step 3: Dynamic MCP Negotiation & Connection Phase
        if intent.need_mcp_selection:
            mcp_task_id = "task_03_mcp_negotiation"
            comp = SagaCompensation(
                action_id=f"comp_{mcp_task_id}",
                target_task_id=mcp_task_id,
                action_type="teardown_mcp",
                description="Terminate on-demand MCP servers and unbind environment endpoints",
            )
            compensations.append(comp)
            tasks.append(
                DynamicTaskItem(
                    task_id=mcp_task_id,
                    title="MCP Server Negotiation & On-Demand Acquisition",
                    description="Negotiate and spin up isolated Model Context Protocol (MCP) servers for required external integrations",
                    category="mcp",
                    assigned_role="mcp_coordinator",
                    depends_on=list(prev_dep),
                    inputs={"required_protocols": [intent.primary_domain, "system_tools"]},
                    expected_outputs=["active_mcp_servers", "negotiated_tools"],
                    verification_cmd="ping_mcp_servers",
                    verification_criteria=["All required MCP endpoints respond with healthy status within 5s"],
                    compensation=comp,
                    node_type=CATEGORY_NODE_TYPES["mcp"],
                    write_scope=["mcp"],
                )
            )
            prev_dep = [mcp_task_id]

        # Step 4: Dynamic Bot & Subagent Swarm Assembly Phase
        if intent.need_subagents or intent.need_bot_creation:
            bot_task_id = "task_04_bot_swarm_provisioning"
            comp = SagaCompensation(
                action_id=f"comp_{bot_task_id}",
                target_task_id=bot_task_id,
                action_type="release_lease",
                description="Release ephemeral bot profile leases and clear isolated scratchpads",
            )
            compensations.append(comp)
            tasks.append(
                DynamicTaskItem(
                    task_id=bot_task_id,
                    title="Dynamic Bot & Subagent Profile Creation",
                    description="Synthesize specialized Bot Profiles with tailored SOUL directives, model tiers, and memory scopes",
                    category="bots",
                    assigned_role="bot_cloner",
                    depends_on=list(prev_dep),
                    inputs={"domain": intent.primary_domain, "execution_tier": intent.execution_tier.value},
                    expected_outputs=["provisioned_bots", "swarm_membership", "soul_directives"],
                    verification_cmd="validate_bot_roster_health",
                    verification_criteria=["Bot profiles validated in registry with non-empty SOUL and isolated memory_scope"],
                    compensation=comp,
                    node_type=CATEGORY_NODE_TYPES["bots"],
                    write_scope=["bots"],
                )
            )
            prev_dep = [bot_task_id]

        # Step 5: Core Execution & Implementation Phase (Fan-out or Task DAG)
        core_task_id = "task_05_dynamic_implementation"
        comp_core = SagaCompensation(
            action_id=f"comp_{core_task_id}",
            target_task_id=core_task_id,
            action_type="revert_git",
            description="Revert uncommitted file changes and reset staging worktree",
        )
        compensations.append(comp_core)
        tasks.append(
            DynamicTaskItem(
                task_id=core_task_id,
                title="Goal-Driven Implementation & Execution",
                description=f"Execute core objective: {prompt[:120]}...",
                category="coding",
                assigned_role="lead_implementer",
                depends_on=list(prev_dep),
                inputs={"prompt": prompt, "domain": intent.primary_domain},
                expected_outputs=["modified_files", "execution_artifacts", "telemetry"],
                verification_cmd="pytest -q",
                verification_criteria=["All target functions/endpoints implemented", "No syntax or runtime regressions"],
                compensation=comp_core,
                node_type=CATEGORY_NODE_TYPES["coding"],
                write_scope=["code", "artifacts"],
            )
        )

        # Step 6: Automated Verification & Quality Gate
        qa_task_id = "task_06_verification_and_quality_gate"
        tasks.append(
            DynamicTaskItem(
                task_id=qa_task_id,
                title="Deep Verification & Invariant Audit",
                description="Verify code invariants, run test suite, and check zero orphan modules",
                category="verification",
                assigned_role="qa_auditor",
                depends_on=[core_task_id],
                inputs={"target_task": core_task_id},
                expected_outputs=["test_results", "quality_report", "verified_status"],
                verification_cmd="verify_test_suite_and_orphans",
                verification_criteria=["100% green tests", "Zero orphan modules detected", "No security or type errors"],
                node_type=CATEGORY_NODE_TYPES["verification"],
                write_scope=["qa"],
            )
        )

        # Step 7: Dynamic Learning & Memory Consolidation Phase
        if intent.need_memory_consolidation:
            learn_task_id = "task_07_memory_and_learning_consolidation"
            tasks.append(
                DynamicTaskItem(
                    task_id=learn_task_id,
                    title="Memory Consolidation & Evolutionary Bot Breeding",
                    description="Consolidate session learnings into cognitive memory, record metrics in StrategyMemory, and evolve successful bots",
                    category="learning",
                    assigned_role="learning_engine",
                    depends_on=[qa_task_id],
                    inputs={"execution_history": goal_id},
                    expected_outputs=["episodic_snapshot", "strategy_memory_stat", "evolved_bot_profiles"],
                    verification_cmd="verify_memory_persistence",
                    verification_criteria=["Memory snapshot persisted to user scope", "StrategyMemory updated with honesty doctrine"],
                    node_type=CATEGORY_NODE_TYPES["learning"],
                    write_scope=["memory", "rsi"],
                )
            )

        # Compute execution waves (topological level order)
        waves = self._compute_execution_waves(tasks)

        acceptance_criteria = [
            f"Goal '{intent.intent_type.value}' executed successfully across {len(tasks)} dynamic phases.",
            "All verification criteria satisfied across active execution waves.",
            "Zero orphan modules and zero unhandled exceptions.",
            "Episodic memory and strategy metrics consolidated.",
        ]

        return DynamicGoal(
            goal_id=goal_id,
            title=f"Dynamic Goal: {intent.intent_type.value.capitalize()} ({intent.primary_domain})",
            description=f"Autonomous goal pipeline for: {prompt[:100]}",
            intent=intent,
            tasks=tasks,
            execution_waves=waves,
            acceptance_criteria=acceptance_criteria,
            saga_compensations=compensations,
            metadata={"total_tasks": len(tasks), "execution_tier": intent.execution_tier.value},
        )

    def _compute_execution_waves(self, tasks: list[DynamicTaskItem]) -> list[list[str]]:
        """Calculate parallel execution waves using Kahn's topological sorting algorithm."""
        in_degree: dict[str, int] = {t.task_id: 0 for t in tasks}
        graph: dict[str, list[str]] = {t.task_id: [] for t in tasks}

        for t in tasks:
            for dep in t.depends_on:
                if dep in graph:
                    graph[dep].append(t.task_id)
                    in_degree[t.task_id] += 1

        waves: list[list[str]] = []
        current_wave = [tid for tid, deg in in_degree.items() if deg == 0]

        while current_wave:
            waves.append(sorted(current_wave))
            next_wave: list[str] = []
            for tid in current_wave:
                for child in graph.get(tid, []):
                    in_degree[child] -= 1
                    if in_degree[child] == 0:
                        next_wave.append(child)
            current_wave = next_wave

        return waves


_GLOBAL_DECOMPOSER: DynamicDecomposer | None = None


def get_dynamic_decomposer() -> DynamicDecomposer:
    global _GLOBAL_DECOMPOSER
    if _GLOBAL_DECOMPOSER is None:
        _GLOBAL_DECOMPOSER = DynamicDecomposer()
    return _GLOBAL_DECOMPOSER
