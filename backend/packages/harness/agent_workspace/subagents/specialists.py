"""Specialist Sub-Agent Archetypes & Dynamic Auto-Role Generator.

Provides preconfigured archetype templates:
- CRITIC: Audits code, plans, and deliverable artifacts against quality standards.
- JUDGE: Evaluates and ranks multiple candidate solutions in debate/ensemble runs.
- RED_TEAM: Adversarial probe discovering edge cases, exploits, and boundary leaks.
- VERIFIER: Independent fact-checker validating citations, proof obligations, and tests.
- RESEARCHER: Deep inquiry and evidence gathering specialist.
- CODER: Git worktree and code implementation specialist.
- SECURITY_AUDITOR: Static code analysis, secret scanner, and dependency auditor.

Also provides `generate_dynamic_role()` to synthesize custom specialist contracts on demand.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from agent_workspace.subagents.lifecycle import SubagentContract


class SpecialistRoleArchetype(StrEnum):
    CRITIC = "critic"
    JUDGE = "judge"
    RED_TEAM = "red_team"
    VERIFIER = "verifier"
    RESEARCHER = "researcher"
    CODER = "coder"
    SECURITY_AUDITOR = "security_auditor"


@dataclass
class SpecialistTemplate:
    archetype: SpecialistRoleArchetype
    role_title: str
    description: str
    system_prompt: str
    recommended_skills: list[str] = field(default_factory=list)
    recommended_tools: list[str] = field(default_factory=list)
    workspace_mode: str = "isolated"
    model_tier: str = "fast"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["archetype"] = self.archetype.value
        return d


ARCHETYPE_REGISTRY: dict[SpecialistRoleArchetype, SpecialistTemplate] = {
    SpecialistRoleArchetype.CRITIC: SpecialistTemplate(
        archetype=SpecialistRoleArchetype.CRITIC,
        role_title="Artifact & Quality Critic",
        description="Rigorously critiques deliverables, identifying logical gaps, incomplete features, and deviations from specifications.",
        system_prompt=("You are a dedicated Quality Critic. Your mission is to identify deficiencies, unverified assumptions, code smells, and unmet criteria in the provided deliverables. Always produce constructive, actionable critique."),
        recommended_tools=["ast_grep_search", "trajectory_audit_tool"],
        model_tier="frontier",
    ),
    SpecialistRoleArchetype.JUDGE: SpecialistTemplate(
        archetype=SpecialistRoleArchetype.JUDGE,
        role_title="Consensus & Outcome Judge",
        description="Synthesizes parallel outputs from debating or ensemble workers, selects the superior solution, and resolves conflicts.",
        system_prompt=("You are an impartial Judge. Evaluate all presented arguments or candidate implementations. Weigh evidence, benchmark metrics, and adherence to requirements to declare the definitive winner."),
        recommended_tools=["evidence_matrix_tool", "moa_multi_model_reasoning"],
        model_tier="frontier",
    ),
    SpecialistRoleArchetype.RED_TEAM: SpecialistTemplate(
        archetype=SpecialistRoleArchetype.RED_TEAM,
        role_title="Adversarial Red-Team Specialist",
        description="Proactively stresses implementations with malformed inputs, edge cases, race conditions, and privilege escalation tests.",
        system_prompt=("You are an Adversarial Red-Team Specialist. Your job is to break the proposed solution. Probe for boundary condition failures, memory leaks, concurrency races, and injection vulnerabilities."),
        recommended_tools=["simulate_consequences", "verify_command_approval"],
        model_tier="frontier",
    ),
    SpecialistRoleArchetype.VERIFIER: SpecialistTemplate(
        archetype=SpecialistRoleArchetype.VERIFIER,
        role_title="Proof & Citation Verifier",
        description="Independently validates that claims are supported by authoritative sources and that code passes all test suites.",
        system_prompt=("You are an Independent Verifier. Zero unchecked assertions are permitted. Verify all proof obligations, run automated test suites, and cross-reference citations."),
        recommended_tools=["reproduce_and_verify", "evaluate_epistemic_claim"],
        model_tier="fast",
    ),
    SpecialistRoleArchetype.RESEARCHER: SpecialistTemplate(
        archetype=SpecialistRoleArchetype.RESEARCHER,
        role_title="Deep Research Specialist",
        description="Conducts multi-source investigation, literature analysis, and evidence gathering.",
        system_prompt=("You are a Deep Research Specialist. Systematically explore the topic, extract primary sources, synthesize findings, and preserve full provenance."),
        recommended_tools=["browser_navigate_and_inspect", "compile_five_pass_search"],
        model_tier="frontier",
    ),
    SpecialistRoleArchetype.CODER: SpecialistTemplate(
        archetype=SpecialistRoleArchetype.CODER,
        role_title="Software Implementation Specialist",
        description="Implements production-grade code, unit tests, and refactors within Git worktree isolation.",
        system_prompt=("You are a focused Software Implementation Specialist. Write clean, robust, well-tested code. Respect architectural boundaries and ensure zero lint or test failures."),
        recommended_tools=["hashline_edit", "python_repl_tool"],
        workspace_mode="git_worktree",
        model_tier="fast",
    ),
    SpecialistRoleArchetype.SECURITY_AUDITOR: SpecialistTemplate(
        archetype=SpecialistRoleArchetype.SECURITY_AUDITOR,
        role_title="Security & Vulnerability Auditor",
        description="Audits source code, configurations, dependencies, and network access for security risks.",
        system_prompt=("You are a Security Auditor. Inspect source files for hardcoded secrets, dangerous command execution, OWASP Top 10 vulnerabilities, and dependency CVEs."),
        recommended_tools=["astra_security_manage", "ast_grep_search"],
        model_tier="frontier",
    ),
}


def get_archetype_template(archetype: SpecialistRoleArchetype | str) -> SpecialistTemplate:
    """Retrieves the preconfigured template for an archetype."""
    if isinstance(archetype, str):
        try:
            archetype = SpecialistRoleArchetype(archetype.lower().strip())
        except ValueError:
            archetype = SpecialistRoleArchetype.RESEARCHER
    return ARCHETYPE_REGISTRY[archetype]


def generate_dynamic_role(objective: str) -> SubagentContract:
    """Analyzes a task objective and dynamically synthesizes a tailored SubagentContract."""
    obj_lower = objective.lower()

    # Match archetype keywords
    if any(k in obj_lower for k in ("critic", "critique", "review code", "audit design", "code smell")):
        template = ARCHETYPE_REGISTRY[SpecialistRoleArchetype.CRITIC]
    elif any(k in obj_lower for k in ("judge", "choose best", "compare debate", "ensemble consensus", "resolve conflict")):
        template = ARCHETYPE_REGISTRY[SpecialistRoleArchetype.JUDGE]
    elif any(k in obj_lower for k in ("red team", "exploit", "fuzz", "break", "stress test", "adversarial")):
        template = ARCHETYPE_REGISTRY[SpecialistRoleArchetype.RED_TEAM]
    elif any(k in obj_lower for k in ("verify", "validate", "check citation", "proof", "reproduce")):
        template = ARCHETYPE_REGISTRY[SpecialistRoleArchetype.VERIFIER]
    elif any(k in obj_lower for k in ("security", "cve", "leak", "secret", "vulnerability")):
        template = ARCHETYPE_REGISTRY[SpecialistRoleArchetype.SECURITY_AUDITOR]
    elif any(k in obj_lower for k in ("code", "build", "implement", "refactor", "bug", "endpoint")):
        template = ARCHETYPE_REGISTRY[SpecialistRoleArchetype.CODER]
    else:
        template = ARCHETYPE_REGISTRY[SpecialistRoleArchetype.RESEARCHER]

    return SubagentContract(
        objective=objective,
        role=template.archetype.value,
        instructions=f"{template.system_prompt}\n\nTask Goal: {objective}",
        tools=list(template.recommended_tools),
        skills=list(template.recommended_skills),
        workspace_mode=template.workspace_mode,
        timeout_seconds=600,
        lease_duration_seconds=60,
    )
