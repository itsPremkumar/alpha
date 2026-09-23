"""Dynamic Resource Assembler Engine.

Dynamically provisions and configures the complete execution environment for a goal:
- Specialized Bot / Subagent creation with tailored SOUL directives, model tiers, and memory scopes
- Bot Swarm / Project Workgroup orchestration setup
- Dynamic Skill Resolver (discovery via the real SkillsHub seam only). Skill
  *generation* is honestly disclosed as unavailable unless a generator is
  bound — nothing fabricated is ever registered into the live skills hub.
- Dynamic Tool & Plugin Selection (filtering the 116+ tool catalog to prevent context bloat)
- Dynamic MCP acquisition, sourced only from MCP specs *declared* in installed
  skill manifests (never fabricated); honest unavailability disclosure otherwise.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from alpha.bots.cloning import BotCloneEngine, get_bot_clone_engine
from alpha.bots.profile import BotProfile
from alpha.bots.registry import BotRegistry, get_bot_registry
from alpha.skills.hub.discovery import SkillsHub, get_skills_hub
from alpha.skills.mcp_lifecycle import SkillMcpLifecycleManager
from alpha.workflow.dynamic_decomposer import DynamicGoal
from alpha.workflow.dynamic_perception import DynamicExecutionTier

logger = logging.getLogger(__name__)


@dataclass
class SwarmWorkgroup:
    """Represents a dynamically assembled multi-agent swarm for collaborative execution."""

    swarm_id: str
    moderator_bot: str
    member_bots: list[str]
    orchestration_mode: str  # "parallel", "moderated", "quorum", "round_robin"
    shared_objective: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AssembledResources:
    """Encapsulates all dynamically provisioned resources ready for workflow execution."""

    goal_id: str
    bots: dict[str, dict[str, Any]] = field(default_factory=dict)
    swarm: SwarmWorkgroup | None = None
    skills: list[dict[str, Any]] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    model_tier: str = "deep_reasoning"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "bots": self.bots,
            "swarm": self.swarm.to_dict() if self.swarm else None,
            "skills": self.skills,
            "tools": self.tools,
            "mcp_servers": self.mcp_servers,
            "model_tier": self.model_tier,
            "metadata": self.metadata,
        }


class DynamicResourceAssembler:
    """Orchestrates runtime assembly of bots, swarms, skills, tools, and MCP servers."""

    # Built-in tool domain index for dynamic context-aware filtering
    DOMAIN_TOOLS: dict[str, list[str]] = {
        "engineering": [
            "read_file", "write_file", "edit_file", "view_file", "replace_file_content",
            "grep_search", "find_by_name", "list_dir", "run_command", "auto_test_and_repair",
            "manage_code_checkpoint", "reconcile_structural_ast_conflicts",
        ],
        "research": [
            "search_web", "read_url_content", "deep_research", "five_pass_search",
            "session_search", "query_knowledge_graph",
        ],
        "skills": [
            "synthesize_reusable_skill", "review_skill_package", "propose_skill",
            "executable_skill_tool", "skills_hub_manage",
        ],
        "bots": [
            "bot_roster_tool", "subagent_control", "a2a_tool", "group_chat_tool",
            "swarm_tool", "discipline_team_tool",
        ],
        "mcp": [
            "mcp_metadata", "request_secure_credential", "enterprise_security_manage",
        ],
        "verification": [
            "visual_verify_artifact", "trajectory_audit", "run_differential_regression_oracle",
            "run_task_evaluation_benchmark",
        ],
        "memory": [
            "cognitive_memory_tool", "consolidate_cognitive_memory", "recall_agent_memory",
            "manage_reflexion_memory", "query_contrastive_memory",
        ],
    }

    def __init__(
        self,
        bot_registry: BotRegistry | None = None,
        clone_engine: BotCloneEngine | None = None,
        skills_hub: SkillsHub | None = None,
        mcp_manager: SkillMcpLifecycleManager | None = None,
    ) -> None:
        self.bot_registry = bot_registry or get_bot_registry()
        self.clone_engine = clone_engine or get_bot_clone_engine()
        self.skills_hub = skills_hub or get_skills_hub()
        # None = no MCP registry seam bound. assemble() then reports an honest
        # "unavailable" instead of fabricating MCP server specs (DY-R3).
        self.mcp_manager = mcp_manager

    def assemble(self, goal: DynamicGoal, prompt: str) -> AssembledResources:
        """Assemble all resources synchronously or asynchronously."""
        intent = goal.intent

        # 1. Model Tier Selection
        if intent.execution_tier == DynamicExecutionTier.MULTIAGENT_SWARM:
            model_tier = "deep_reasoning"
        elif intent.execution_tier == DynamicExecutionTier.AUTONOMOUS_WORKFLOW:
            model_tier = "deep_reasoning"
        elif intent.execution_tier == DynamicExecutionTier.DEEP_REASONING:
            model_tier = "hybrid_cot"
        else:
            model_tier = "lightweight_fast"

        # 2. Dynamic Bot & SOUL Directive Creation
        provisioned_bots: dict[str, dict[str, Any]] = {}
        bot_names: list[str] = []

        # Determine roles based on goal tasks
        needed_roles: list[tuple[str, str, str]] = []  # (role_slug, display_name, description)
        for t in goal.tasks:
            if t.category == "research" and not any(r[0] == "lead_researcher" for r in needed_roles):
                needed_roles.append(("lead_researcher", "Research Specialist", "Deep technical literature and web intelligence"))
            elif t.category == "skills" and not any(r[0] == "skill_curator" for r in needed_roles):
                needed_roles.append(("skill_curator", "Skill Curator & Author", "Autonomous skill authoring and hub management"))
            elif t.category == "coding" and not any(r[0] == "code_specialist" for r in needed_roles):
                needed_roles.append(("code_specialist", "Core Systems Engineer", "Robust architecture, code synthesis, and refactoring"))
            elif t.category == "verification" and not any(r[0] == "qa_auditor" for r in needed_roles):
                needed_roles.append(("qa_auditor", "QA & Invariant Auditor", "Strict invariant verification, testing, and security scanning"))
            elif t.category == "learning" and not any(r[0] == "learning_engine" for r in needed_roles):
                needed_roles.append(("learning_engine", "Cognitive Memory & Evolution Engine", "Consolidation of session learnings and bot breeding"))

        if not needed_roles:
            needed_roles.append(("autonomous_agent", "Autonomous Agent", "General goal-driven execution"))

        for role_slug, role_title, role_desc in needed_roles:
            bot_name = f"bot_{role_slug}_{uuid.uuid4().hex[:4]}"
            soul_directive = self._generate_tailored_soul(
                bot_name=bot_name,
                role_title=role_title,
                role_desc=role_desc,
                domain=intent.primary_domain,
                goal_title=goal.title,
                raw_prompt=prompt,
            )

            # Assign domain-specific toolsets & memory scope
            bot_tools = self.DOMAIN_TOOLS.get(role_slug.split("_")[0], self.DOMAIN_TOOLS.get("engineering", []))

            # Synthesize Bot Profile
            profile = BotProfile(
                name=bot_name,
                display_name=f"{role_title} ({bot_name})",
                role=role_title,
                soul=soul_directive,
                model=model_tier,
                toolsets=bot_tools[:8],
                skills=[f"skill-{intent.primary_domain}"] if intent.primary_domain else [],
                avatar="🤖" if "engineer" in role_slug else "🔬" if "research" in role_slug else "🛡️",
                department=intent.primary_domain or "engineering",
                memory_scope=f"scope_{bot_name}",
                capabilities=[role_slug, intent.primary_domain],
                metadata={
                    "created_dynamically": True,
                    "goal_id": goal.goal_id,
                    "execution_tier": intent.execution_tier.value,
                    # SOUL directives are synthesized from templates in this
                    # layer — disclosed, never presented as generated wisdom.
                    "generation_method": "template",
                },
            )
            self.bot_registry.register(profile)
            provisioned_bots[bot_name] = profile.to_dict()
            bot_names.append(bot_name)

        # 3. Dynamic Bot Swarm / Workgroup Assembly
        moderator = bot_names[0] if bot_names else "lead_orchestrator"
        swarm_mode = "parallel" if intent.execution_tier == DynamicExecutionTier.MULTIAGENT_SWARM else "moderated"
        swarm = SwarmWorkgroup(
            swarm_id=f"swarm_{uuid.uuid4().hex[:6]}",
            moderator_bot=moderator,
            member_bots=bot_names,
            orchestration_mode=swarm_mode,
            shared_objective=goal.title,
            metadata={"domain": intent.primary_domain, "total_members": len(bot_names)},
        )

        # 4. Dynamic Skill Resolver — discovery only.
        #    Honesty (DY-R3): the previous version fabricated a hardcoded mock
        #    skill body and registered it into the live skills hub. No skill
        #    generator is bound in this layer, so generation is reported as
        #    unavailable and NOTHING is registered into the hub.
        active_skills: list[dict[str, Any]] = []
        skill_generation = "not requested"
        if intent.need_skill_download or intent.need_skill_creation:
            # 4a. Search in skills hub (real seam)
            discovered = self.skills_hub.search(intent.primary_domain)
            for pkg in discovered:
                active_skills.append({
                    "name": pkg.name,
                    "description": pkg.description,
                    "status": "discovered_in_hub",
                    "source": pkg.source,
                })

            # 4b. Authoring requested but no generator bound: honest skip.
            #     (No hardcoded body, no add_to_catalog() call.)
            if intent.need_skill_creation:
                skill_generation = "unavailable — no generator bound"

        # 5. Dynamic Tool Selection (context-efficient filtering)
        selected_tools: list[str] = []
        for domain in [intent.primary_domain] + intent.secondary_domains:
            if domain in self.DOMAIN_TOOLS:
                for t in self.DOMAIN_TOOLS[domain]:
                    if t not in selected_tools:
                        selected_tools.append(t)

        # Ensure essential general tools are always present
        for essential in ["read_file", "write_file", "replace_file_content", "run_command", "view_file"]:
            if essential not in selected_tools:
                selected_tools.append(essential)

        # 6. Dynamic MCP acquisition — only from REAL declared specs.
        #    Honesty (DY-R3): the previous version fabricated an MCP YAML spec
        #    and pushed it through the lifecycle manager. Specs are now read
        #    only from installed skill manifests (SKILL.md frontmatter); when
        #    no seam is bound or nothing is declared, we disclose that instead.
        active_mcp_servers: list[str] = []
        mcp_generation = "not requested"
        if intent.need_mcp_selection:
            if self.mcp_manager is None:
                mcp_generation = "unavailable — no MCP registry seam bound"
            else:
                manifests = self._declared_mcp_skill_manifests()
                if manifests is None:
                    mcp_generation = "unavailable — skills hub seam exposes no installed-skill manifests"
                else:
                    for skill_name, manifest in manifests:
                        active_mcp_servers.extend(
                            self.mcp_manager.acquire_for_skill(skill_name, manifest)
                        )
                    if active_mcp_servers:
                        mcp_generation = "declared_in_installed_skills"
                    else:
                        mcp_generation = "unavailable — no MCP server specs declared by installed skills"

        return AssembledResources(
            goal_id=goal.goal_id,
            bots=provisioned_bots,
            swarm=swarm,
            skills=active_skills,
            tools=selected_tools[:25],  # Cap toolset to preserve model context
            mcp_servers=active_mcp_servers,
            model_tier=model_tier,
            metadata={
                "total_bots": len(provisioned_bots),
                "total_tools": len(selected_tools),
                "total_skills": len(active_skills),
                # Honest disclosure fields (DY-R3): what was and was not
                # generated by this layer.
                "skill_generation": skill_generation,
                "mcp_generation": mcp_generation,
            },
        )

    def _declared_mcp_skill_manifests(self) -> list[tuple[str, str]] | None:
        """(skill_name, SKILL.md text) for every installed skill.

        This is the real source of MCP server declarations: only frontmatter
        actually present in these manifests may be acquired. Returns None when
        the bound skills-hub seam does not expose installed-skill manifests
        (callers must disclose that honestly instead of fabricating specs).
        """
        installed = getattr(self.skills_hub, "list_installed", None)
        skills_dir = getattr(self.skills_hub, "installed_skills_dir", None)
        if not callable(installed) or skills_dir is None:
            return None
        root = Path(skills_dir)
        manifests: list[tuple[str, str]] = []
        for entry in installed():
            name = (entry or {}).get("name")
            if not name:
                continue
            md_path = root / name / "SKILL.md"
            if md_path.is_file():
                manifests.append((name, md_path.read_text(encoding="utf-8")))
        return manifests

    def _generate_tailored_soul(
        self,
        bot_name: str,
        role_title: str,
        role_desc: str,
        domain: str,
        goal_title: str,
        raw_prompt: str,
    ) -> str:
        """Synthesize a complete, principled SOUL directive for a dynamic bot."""
        return (
            f"# SOUL DIRECTIVE: {role_title} ({bot_name})\n\n"
            f"## Identity & Role\n"
            f"You are **{bot_name}**, specialized as **{role_title}** for domain **{domain}**.\n"
            f"Your focus: {role_desc}.\n\n"
            f"## Primary Objective\n"
            f"Execute your assigned tasks in goal: '{goal_title}'.\n"
            f"User Context: {raw_prompt[:200]}\n\n"
            f"## Operating Principles\n"
            f"1. **Zero Guesswork**: Formulate hypotheses, examine codebase evidence, verify before marking complete.\n"
            f"2. **Strict Verification**: Every change must be backed by automated test coverage and invariant checks.\n"
            f"3. **Context Economy**: Use concise outputs and structured payloads to conserve token budget.\n"
            f"4. **Safe Rollback**: Respect write scopes and be prepared to execute saga compensation if errors occur.\n"
        )


_GLOBAL_ASSEMBLER: DynamicResourceAssembler | None = None


def get_dynamic_resource_assembler() -> DynamicResourceAssembler:
    global _GLOBAL_ASSEMBLER
    if _GLOBAL_ASSEMBLER is None:
        _GLOBAL_ASSEMBLER = DynamicResourceAssembler()
    return _GLOBAL_ASSEMBLER
