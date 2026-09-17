"""Autonomous one-prompt planner: raw user prompt -> fully decided execution plan.

The user writes a single prompt. This planner makes every planning-phase
decision by itself, deterministically and offline (no model calls):

- understands the prompt (Mission + Theory-of-Mind intent + reasoning tier)
- decides whether deep research is needed (and why)
- decides whether subagents are needed (and why)
- splits huge tasks into subtasks with dependencies and parallel waves
- assigns every subtask to an existing subagent profile + category, or
  drafts a new specialized profile spec when no builtin covers the work
- builds the Kanban board (cards, assignees, priorities, dependencies)
- selects skills, tool groups, and execution configuration automatically
- sets the final goal (acceptance criteria + proof obligations + risk tier)
- self-reviews the generated plan through the Hyperplan hostile gate

High-risk missions (R5/R6) and BLOCKED gates stay human-gated: autonomy is
"full" up to R4-reversible work and "gated" above it. The user only reads the
summary; the agent owns everything else.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_workspace.kanban.models import KanbanBoard, KanbanTask
from agent_workspace.mission.compiler import MissionCompiler
from agent_workspace.mission.models import Mission, RiskTier
from agent_workspace.planning.compiler import default_proof_obligations, plan_hash_for
from agent_workspace.planning.compiler.validity import PlanValidityMonitor
from agent_workspace.planning.compiler.waves import ExecutionWave, partition_execution_waves
from agent_workspace.planning.hyperplan import HyperplanPipeline
from agent_workspace.reasoning.governor import ReasoningGovernor
from agent_workspace.reasoning.tom.consultant import TheoryOfMindConsultant

_DOMAIN_PATTERNS: dict[str, re.Pattern] = {
    "research": re.compile(r"\b(research|survey|compare|investigat|literature|landscape|deep dive|state of|vendors?|pricing|options)\b", re.IGNORECASE),
    "coding": re.compile(r"\b(build|implement|refactor|fix|bug|code|coding|api|endpoint|function|class|feature|rewrite|debug|sdk)\b", re.IGNORECASE),
    "browser": re.compile(r"\b(browser|login|scrap|crawl|form fill|web flow|webpage|website interaction)\b", re.IGNORECASE),
    "data": re.compile(r"\b(dataset|dataframe|\bsql\b|etl|dashboard|chart|analytics|pipeline etl|transform data)\b", re.IGNORECASE),
    "design": re.compile(r"\b(design|ui\b|ux|frontend|mockup|wireframe|landing page|theme)\b", re.IGNORECASE),
    "deploy": re.compile(r"\b(deploy|release|publish|rollout|ci/?cd|staging|production rollout)\b", re.IGNORECASE),
    "docs": re.compile(r"\b(document|report|blog|guide|newsletter|ppt|presentation|paper|changelog)\b", re.IGNORECASE),
    "media": re.compile(r"\b(image|video|music|podcast|thumbnail|banner art)\b", re.IGNORECASE),
    "security": re.compile(r"\b(security|vuln|audit|pentest|\bauth\b|permissions|hardening)\b", re.IGNORECASE),
}

_DOMAIN_ORDER = ["research", "design", "coding", "data", "browser", "security", "deploy", "docs", "media"]

_DOMAIN_SKILLS: dict[str, list[str]] = {
    "research": ["deep-research"],
    "coding": ["project-cartographer"],
    "design": ["frontend-design", "web-design-guidelines"],
    "data": ["data-analysis"],
    "docs": ["code-documentation"],
    "media": ["image-generation"],
    "browser": [],
    "deploy": [],
    "security": [],
}

_DOMAIN_TOOL_GROUPS: dict[str, list[str]] = {
    "research": ["web"],
    "coding": ["file:read", "file:write", "bash"],
    "browser": ["browser", "web"],
    "data": ["file:read", "file:write", "bash"],
    "design": ["file:read", "file:write"],
    "deploy": ["bash", "file:read"],
    "docs": ["file:read", "file:write"],
    "media": ["file:write"],
    "security": ["file:read", "bash"],
}

_SPECIALIST_PROFILES: dict[str, dict[str, Any]] = {
    "frontend": {
        "name": "frontend-specialist",
        "description": "Builds UI surfaces with design-system fidelity; owns disjoint frontend scopes.",
        "skills": ["frontend-design", "web-design-guidelines"],
        "tools": None,
        "disallowed_tools": ["task"],
        "max_turns": 80,
        "category": "general",
    },
    "data": {
        "name": "data-specialist",
        "description": "Profiles, transforms, and validates datasets; publishes reproducible artifacts.",
        "skills": ["data-analysis"],
        "tools": None,
        "disallowed_tools": ["task"],
        "max_turns": 80,
        "category": "general",
    },
    "security": {
        "name": "security-reviewer",
        "description": "Audits diffs for vulnerabilities and secret leaks; never lands code itself.",
        "skills": [],
        "tools": ["read_file", "grep", "bash"],
        "disallowed_tools": ["task"],
        "max_turns": 60,
        "category": "general",
    },
    "research": {
        "name": "research-lead",
        "description": "Runs multi-source investigations with cited, contradiction-aware reports.",
        "skills": ["deep-research"],
        "tools": None,
        "disallowed_tools": ["task"],
        "max_turns": 100,
        "category": "research",
    },
}

_RESEARCH_SIGNAL_RE = re.compile(
    r"\b(compare|best|landscape|options|vendors?|pricing|threat|state of|unknown|unfamiliar|evaluate|alternatives)\b",
    re.IGNORECASE,
)
_MULTI_PART_RE = re.compile(r"\band\b", re.IGNORECASE)
_HUGE_HINT_RE = re.compile(r"\b(platform|system|end.to.end|full|complete|entire|migrate|redesign|overhaul|from scratch)\b", re.IGNORECASE)

# Heuristic per-card cost table (tokens + wall-clock). Documented estimates, not
# measurements: base tokens by domain, x1.25 for high/critical priority, ~45s
# per card turn-equivalent. Totals are checked against the run token budget.
_DOMAIN_TOKEN_BASE: dict[str, int] = {
    "research": 12000,
    "coding": 15000,
    "design": 12000,
    "data": 12000,
    "browser": 10000,
    "security": 6000,
    "docs": 6000,
    "media": 8000,
    "general": 6000,
}
_DOMAIN_SECONDS_BASE: dict[str, int] = {
    "research": 600,
    "coding": 900,
    "design": 600,
    "data": 600,
    "browser": 600,
    "security": 300,
    "docs": 300,
    "media": 300,
    "general": 300,
}

_ENV_WORD_RE = re.compile(r"\b(prod|production|staging|dev\b|development|local|docker|cloud|server)\b", re.IGNORECASE)
_METRIC_WORD_RE = re.compile(r"(\d+\s*(%|ms|s\b|x\b)|p95|p99|benchmark|sla)", re.IGNORECASE)
_VAGUE_QUALITY_RE = re.compile(r"\b(better|faster|improve|improvement|optimiz|clean ?up|nicer)\b", re.IGNORECASE)
_DUE_PHRASE_RE = re.compile(
    r"\bby\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|eod|end of (day|week)|next week|\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)
_URGENT_RE = re.compile(r"\b(urgent\w*|asap|immediately|right away|emergency|critical priority)\b", re.IGNORECASE)

_CONTROL_CHARS_RE = re.compile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f\\u200b-\\u200f\\u202a-\\u202e\\u2066-\\u2069\\ufeff]")
_WHITESPACE_RE = re.compile(r"\s+")


def safe_text(value: str, max_len: int) -> str:
    """Neutralize user-derived text for cards, prompts, and UI rendering.

    Strips control/format characters (incl. zero-width and bidi overrides),
    collapses whitespace, and caps length. Content is preserved; only
    non-printable smuggling and unbounded pastes are removed.
    """
    cleaned = _CONTROL_CHARS_RE.sub("", value or "")
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max(0, max_len - 1)].rstrip() + "…"
    return cleaned


def request_hash_for(raw_prompt: str) -> str:
    """Stable idempotency key for a raw prompt (case/whitespace-insensitive)."""
    import hashlib

    normalized = _WHITESPACE_RE.sub(" ", (raw_prompt or "").strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def detect_domains(prompt: str) -> list[str]:
    """Ordered domain hits for a raw prompt (may be empty for pure Q&A)."""
    return [d for d in _DOMAIN_ORDER if _DOMAIN_PATTERNS[d].search(prompt)]


@dataclass
class AutoSubtask:
    subtask_id: str
    title: str
    description: str
    domain: str
    category: str
    assignee: str
    needs_new_profile: bool = False
    profile_name: str | None = None
    skills: list[str] = field(default_factory=list)
    tool_groups: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    priority: str = "medium"
    dependencies: list[str] = field(default_factory=list)
    wave_index: int = 1
    tokens_estimate: int = 0
    seconds_estimate: int = 0

    def __post_init__(self) -> None:
        # Harden user-derived text at construction: bounded, printable, single-line.
        self.title = safe_text(self.title, 80)
        self.description = safe_text(self.description, 500)
        self.acceptance_criteria = [safe_text(a, 200) for a in (self.acceptance_criteria or [])]

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "title": self.title,
            "description": self.description,
            "domain": self.domain,
            "category": self.category,
            "assignee": self.assignee,
            "needs_new_profile": self.needs_new_profile,
            "profile_name": self.profile_name,
            "skills": list(self.skills),
            "tool_groups": list(self.tool_groups),
            "acceptance_criteria": list(self.acceptance_criteria),
            "priority": self.priority,
            "dependencies": list(self.dependencies),
            "wave_index": self.wave_index,
            "tokens_estimate": self.tokens_estimate,
            "seconds_estimate": self.seconds_estimate,
        }


@dataclass
class NewProfileSpec:
    name: str
    description: str
    system_prompt_outline: list[str] = field(default_factory=list)
    tools: list[str] | None = None
    disallowed_tools: list[str] = field(default_factory=lambda: ["task"])
    skills: list[str] = field(default_factory=list)
    model: str = "inherit"
    max_turns: int = 80
    timeout_seconds: int = 900
    category: str = "general"
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "system_prompt_outline": list(self.system_prompt_outline),
            "tools": list(self.tools) if self.tools is not None else None,
            "disallowed_tools": list(self.disallowed_tools),
            "skills": list(self.skills),
            "model": self.model,
            "max_turns": self.max_turns,
            "timeout_seconds": self.timeout_seconds,
            "category": self.category,
            "rationale": self.rationale,
        }


@dataclass
class AutonomousPlan:
    plan_id: str
    plan_hash: str
    raw_prompt: str
    goal_statement: str
    risk_tier: str
    reasoning_tier: str
    needs_deep_research: bool
    research_rationale: str
    needs_subagents: bool
    delegation_rationale: str
    subtasks: list[AutoSubtask] = field(default_factory=list)
    new_profiles: list[NewProfileSpec] = field(default_factory=list)
    kanban_board: dict[str, Any] = field(default_factory=dict)
    skills_to_use: list[str] = field(default_factory=list)
    tool_groups: list[str] = field(default_factory=list)
    execution_waves: list[dict[str, Any]] = field(default_factory=list)
    proof_obligations: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    execution_config: dict[str, Any] = field(default_factory=dict)
    hyperplan_status: str = "not_reviewed"
    hyperplan_summary: str = ""
    autonomy: str = "full"
    assumptions: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    cost_estimate: dict[str, Any] = field(default_factory=dict)
    capability_notes: list[str] = field(default_factory=list)
    llm_review: dict[str, Any] = field(default_factory=dict)
    kanban_persisted: bool = False
    revision: int = 1
    supersedes: str = ""
    request_hash: str = ""
    duplicate_of_board: str | None = None
    approval_request: dict[str, Any] = field(default_factory=dict)
    user_summary_markdown: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "raw_prompt": self.raw_prompt,
            "goal_statement": self.goal_statement,
            "risk_tier": self.risk_tier,
            "reasoning_tier": self.reasoning_tier,
            "needs_deep_research": self.needs_deep_research,
            "research_rationale": self.research_rationale,
            "needs_subagents": self.needs_subagents,
            "delegation_rationale": self.delegation_rationale,
            "subtasks": [s.to_dict() for s in self.subtasks],
            "new_profiles": [p.to_dict() for p in self.new_profiles],
            "kanban_board": self.kanban_board,
            "skills_to_use": list(self.skills_to_use),
            "tool_groups": list(self.tool_groups),
            "execution_waves": list(self.execution_waves),
            "proof_obligations": list(self.proof_obligations),
            "acceptance_criteria": list(self.acceptance_criteria),
            "execution_config": dict(self.execution_config),
            "hyperplan_status": self.hyperplan_status,
            "hyperplan_summary": self.hyperplan_summary,
            "autonomy": self.autonomy,
            "assumptions": list(self.assumptions),
            "unknowns": list(self.unknowns),
            "cost_estimate": dict(self.cost_estimate),
            "capability_notes": list(self.capability_notes),
            "llm_review": dict(self.llm_review),
            "kanban_persisted": self.kanban_persisted,
            "revision": self.revision,
            "supersedes": self.supersedes,
            "request_hash": self.request_hash,
            "duplicate_of_board": self.duplicate_of_board,
            "approval_request": dict(self.approval_request),
            "user_summary_markdown": self.user_summary_markdown,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AutonomousPlan:
        subtasks = [AutoSubtask(**{k: v for k, v in s.items() if k in AutoSubtask.__dataclass_fields__}) for s in data.get("subtasks", [])]
        profiles = [NewProfileSpec(**{k: v for k, v in p.items() if k in NewProfileSpec.__dataclass_fields__}) for p in data.get("new_profiles", [])]
        known = set(cls.__dataclass_fields__)
        kwargs = {k: v for k, v in data.items() if k in known and k not in ("subtasks", "new_profiles")}
        kwargs["subtasks"] = subtasks
        kwargs["new_profiles"] = profiles
        return cls(**kwargs)

    def save(self, path: Any) -> Any:
        """Persist the plan JSON to disk (round-trips through from_dict/load)."""
        import json
        from pathlib import Path

        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return dest

    @classmethod
    def load(cls, path: Any) -> AutonomousPlan:
        import json
        from pathlib import Path

        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


class AutonomousPlanner:
    """Turns one raw prompt into a fully decided, executable plan."""

    def __init__(self, max_subtasks: int = 8) -> None:
        self.max_subtasks = max(1, int(max_subtasks))

    def plan(
        self,
        raw_prompt: str,
        workspace_context: str | None = None,
        context_metadata: dict[str, Any] | None = None,
        max_subtasks: int | None = None,
        capabilities: dict[str, Any] | None = None,
        kanban_store: Any = None,
        token_budget: int | None = None,
        llm_reviewer: Callable[[str], str] | None = None,
        known_requests: dict[str, str] | None = None,
    ) -> AutonomousPlan:
        """Build the full autonomous plan.

        Args:
            raw_prompt: User's verbatim request.
            workspace_context: Optional workspace layout snippet for grounding.
            context_metadata: Optional repo/file/history hints.
            max_subtasks: Cap on Kanban work items.
            capabilities: Optional ``{"enabled_skills": [...]|None,
                "bash": bool|None, "browser": bool|None}``. ``None`` values mean
                "unknown" and skip that check (recorded, never blocking).
            kanban_store: Optional ``KanbanStore`` to persist the board into.
            token_budget: Optional run token budget the cost model is checked against.
            llm_reviewer: Optional ``fn(plan_markdown) -> "VERDICT\\nnote"`` hook
                for R4+ plans. Heuristic gate stays authoritative; hook failures
                are isolated and never block.
        """
        if not raw_prompt or not raw_prompt.strip():
            raise ValueError("raw_prompt must be a non-empty string")
        prompt = raw_prompt.strip()
        budget = max(1, int(max_subtasks or self.max_subtasks))
        req_hash = request_hash_for(prompt)
        duplicate_of_board = (known_requests or {}).get(req_hash)

        mission: Mission = MissionCompiler().compile(prompt)
        intent = TheoryOfMindConsultant().consult(
            task_description=prompt,
            workspace_context=workspace_context,
            context_metadata=context_metadata,
        )
        reasoning = ReasoningGovernor().evaluate(prompt)
        domains = detect_domains(prompt)
        huge = self._is_huge(prompt, domains)

        needs_research, research_rationale = self._decide_research(prompt, domains, huge)

        if self._is_trivial(prompt, domains, mission):
            subtasks = [self._single_quick_task(prompt, mission)]
        else:
            subtasks = self._decompose(prompt, domains, mission, needs_research, huge)
        subtasks = self._fit_budget(subtasks, budget)

        new_profiles = self._draft_profiles(subtasks, huge, mission)
        by_id = {s.subtask_id: s for s in subtasks}
        for sub in subtasks:
            if sub.needs_new_profile and sub.profile_name:
                sub.assignee = sub.profile_name

        dag = {s.subtask_id: list(s.dependencies) for s in subtasks}
        waves: list[ExecutionWave] = partition_execution_waves(dag)
        wave_of = {tid: w.wave_index for w in waves for tid in w.task_ids}
        for sub in subtasks:
            sub.wave_index = wave_of.get(sub.subtask_id, 1)

        needs_subagents, delegation_rationale = self._decide_delegation(subtasks, waves, huge)
        capability_notes, allowed_skills = self._apply_capabilities(subtasks, capabilities)
        if allowed_skills is not None:
            spec_by_name = {p.name: p for p in new_profiles}
            for sub in subtasks:
                spec = spec_by_name.get(sub.profile_name or "")
                if spec is not None:
                    spec.skills = [s for s in spec.skills if s in allowed_skills]
        skills = self._select_skills(prompt, domains, subtasks, allowed_skills)
        groups = self._select_tool_groups(subtasks)
        acceptance = self._acceptance(mission, subtasks)
        obligations = default_proof_obligations(mission.risk_tier.value.upper())
        autonomy = "gated" if mission.risk_tier in (RiskTier.R5, RiskTier.R6) else "full"
        assumptions, unknowns = self._derive_assumptions(prompt, domains, mission, intent)
        cost_estimate = self._estimate_cost(subtasks, token_budget)

        plan_id = f"autoplan_{uuid.uuid4().hex[:10]}"
        plan_hash = plan_hash_for(prompt, dag, "autonomous")
        board = self._build_board(plan_id, mission, subtasks)
        goal = self._goal_statement(mission, subtasks, new_profiles, autonomy)

        report = HyperplanPipeline().review_plan(
            mission.interpreted_intent[:80] or prompt[:80],
            self._render_plan_text(mission, subtasks, new_profiles, acceptance),
            strict_acceptance_required=False,
        )
        if report.is_blocked:
            autonomy = "gated"
        llm_review = self._run_llm_review(mission, acceptance, subtasks, llm_reviewer)
        if llm_review.get("verdict") == "REJECTED":
            autonomy = "gated"

        kanban_persisted = False
        if duplicate_of_board:
            capability_notes.append(f"Duplicate request — reusing board {duplicate_of_board}; no new board persisted.")
        elif kanban_store is not None:
            try:
                kanban_store.put_board(board)
                kanban_persisted = True
            except Exception as exc:  # noqa: BLE001 — persistence never breaks planning
                capability_notes.append(f"Kanban persistence failed ({exc}); board payload still in plan.")

        execution_config = {
            "reasoning_tier": reasoning.tier,
            "thinking_budget_tokens": reasoning.thinking_budget_tokens,
            "model": "inherit (category chains resolve at dispatch)",
            "autonomy": autonomy,
            "approval_policy": self._approval_policy(mission),
            "independent_waves_may_run_parallel": True,
            "estimated_total_tokens": cost_estimate["total_tokens"],
            "estimated_total_seconds": cost_estimate["total_seconds"],
            "budget_status": cost_estimate["budget_status"],
            "capabilities_validated": not any("unvalidated" in n or "unknown" in n for n in capability_notes),
            "schedule": self._parse_schedule(prompt),
        }
        approval_request = self._build_approval(
            autonomy=autonomy,
            mission=mission,
            goal=goal,
            plan_hash=plan_hash,
            gate_summary=report.gatekeeper_summary,
        )

        plan = AutonomousPlan(
            plan_id=plan_id,
            plan_hash=plan_hash,
            raw_prompt=prompt,
            goal_statement=goal,
            risk_tier=mission.risk_tier.value,
            reasoning_tier=reasoning.tier,
            needs_deep_research=needs_research,
            research_rationale=research_rationale,
            needs_subagents=needs_subagents,
            delegation_rationale=delegation_rationale,
            subtasks=subtasks,
            new_profiles=new_profiles,
            kanban_board=board.to_dict(),
            skills_to_use=skills,
            tool_groups=groups,
            execution_waves=[w.to_dict() for w in waves],
            proof_obligations=obligations,
            acceptance_criteria=acceptance,
            execution_config=execution_config,
            hyperplan_status=report.overall_status,
            hyperplan_summary=report.gatekeeper_summary,
            autonomy=autonomy,
            assumptions=assumptions,
            unknowns=unknowns,
            cost_estimate=cost_estimate,
            capability_notes=capability_notes,
            llm_review=llm_review,
            kanban_persisted=kanban_persisted,
            revision=1,
            supersedes="",
            request_hash=req_hash,
            duplicate_of_board=duplicate_of_board,
            approval_request=approval_request,
            user_summary_markdown="",
        )
        plan.user_summary_markdown = self._user_summary(plan, mission, intent)
        return plan

    # -- understanding ----------------------------------------------------

    def _is_huge(self, prompt: str, domains: list[str]) -> bool:
        score = 0
        if len(prompt) > 280:
            score += 1
        if len(domains) >= 3:
            score += 2
        elif len(domains) == 2:
            score += 1
        parts = len(_MULTI_PART_RE.findall(prompt)) + prompt.count(",") + prompt.count(";")
        if parts >= 3:
            score += 1
        if _HUGE_HINT_RE.search(prompt):
            score += 1
        return score >= 3

    def _is_trivial(self, prompt: str, domains: list[str], mission: Mission) -> bool:
        if mission.risk_tier in (RiskTier.R5, RiskTier.R6):
            return False
        markers = len(_MULTI_PART_RE.findall(prompt)) + prompt.count(",") + prompt.count(";")
        return len(prompt) < 120 and len(domains) <= 1 and markers == 0

    def _decide_research(self, prompt: str, domains: list[str], huge: bool):
        if "research" in domains:
            return True, "Prompt explicitly asks for research/investigation."
        if _RESEARCH_SIGNAL_RE.search(prompt):
            return True, "Prompt compares options or touches unknown ground — evidence first."
        if huge and ("coding" in domains or "deploy" in domains):
            return True, "Huge build — researching constraints and prior art before committing."
        return False, "No open unknowns; proceeding from workspace context and skills."

    # -- decomposition ----------------------------------------------------

    def _single_quick_task(self, prompt: str, mission: Mission) -> AutoSubtask:
        detected = detect_domains(prompt)
        return AutoSubtask(
            subtask_id="answer_direct",
            title=f"Handle directly: {prompt[:60]}",
            description=f"Single bounded task with no parallel benefit. {mission.interpreted_intent}",
            domain=detected[0] if detected else "general",
            category="quick",
            assignee="general-purpose",
            skills=[],
            tool_groups=["file:read", "file:write", "bash"],
            acceptance_criteria=[f"Deliver functional implementation satisfying: '{prompt[:80]}'"],
            priority="medium",
            dependencies=[],
        )

    def _decompose(self, prompt: str, domains: list[str], mission: Mission, needs_research: bool, huge: bool) -> list[AutoSubtask]:
        tasks: list[AutoSubtask] = []

        def add(
            sid: str,
            title: str,
            description: str,
            domain: str,
            category: str,
            assignee: str,
            skills: list[str],
            groups: list[str],
            acceptance: list[str],
            priority: str,
            deps: list[str],
            new_profile: bool = False,
            profile_key: str | None = None,
        ) -> None:
            tasks.append(
                AutoSubtask(
                    subtask_id=sid,
                    title=title,
                    description=description,
                    domain=domain,
                    category=category,
                    assignee=assignee,
                    needs_new_profile=new_profile,
                    profile_name=_SPECIALIST_PROFILES[profile_key]["name"] if profile_key else None,
                    skills=skills,
                    tool_groups=groups,
                    acceptance_criteria=acceptance,
                    priority=priority,
                    dependencies=deps,
                )
            )

        if needs_research:
            add(
                "research_evidence",
                "Research and evidence pack",
                "Multi-source investigation with cited, contradiction-aware findings.",
                "research",
                "research",
                "general-purpose",
                self._skills_for("research", prompt),
                ["web"],
                ["Evidence pack covers every open question with primary-source citations [citation:source](url)"],
                "medium",
                [],
            )
        research_dep = ["research_evidence"] if needs_research else []

        if "coding" in domains or "design" in domains or huge:
            add(
                "repo_map_design",
                "Repository map and execution design",
                "Map the repo, pin the change surface, and freeze the build order.",
                "coding",
                "general",
                "general-purpose",
                ["project-cartographer"],
                ["file:read"],
                ["Plan artifact names every file to touch and the verification command"],
                "medium",
                research_dep,
            )
        design_dep = ["repo_map_design"] if any(t.subtask_id == "repo_map_design" for t in tasks) else research_dep

        build_ids: list[str] = []
        if "design" in domains and "coding" in domains and huge:
            add(
                "build_frontend",
                "Build frontend surface",
                "Disjoint UI scope only; no backend or schema changes.",
                "design",
                "general",
                "general-purpose",
                self._skills_for("design", prompt),
                ["file:read", "file:write"],
                ["UI renders from the frozen contract with no placeholder TODOs"],
                "high",
                design_dep,
                new_profile=True,
                profile_key="frontend",
            )
            add(
                "build_backend",
                "Build backend and contracts",
                "Disjoint API/data scope only; no UI changes.",
                "coding",
                "general",
                "general-purpose",
                ["project-cartographer"],
                ["file:read", "file:write", "bash"],
                ["API satisfies the frozen contract; existing tests stay green"],
                "high",
                design_dep,
            )
            build_ids = ["build_frontend", "build_backend"]
        elif "coding" in domains:
            add(
                "build_core",
                "Implement the change",
                "Small verified increments inside the pinned change surface.",
                "coding",
                "general",
                "general-purpose",
                ["project-cartographer"],
                ["file:read", "file:write", "bash"],
                ["Change satisfies the request with zero regressions"],
                "high",
                design_dep,
            )
            build_ids = ["build_core"]
        if "data" in domains:
            add(
                "transform_data",
                "Transform and validate data",
                "Reproducible transform with row-count and validation deltas.",
                "data",
                "general",
                "general-purpose",
                self._skills_for("data", prompt),
                ["file:read", "file:write", "bash"],
                ["Published artifact ships with validation deltas"],
                "high",
                design_dep,
                new_profile=huge,
                profile_key="data" if huge else None,
            )
            build_ids.append("transform_data")
        if "browser" in domains:
            add(
                "browser_flow",
                "Drive the browser flow",
                "Isolated context, observe-verify after each step, no stray side effects.",
                "browser",
                "general",
                "general-purpose",
                [],
                ["browser", "web"],
                ["Flow completes with screenshots and extracted evidence"],
                "high",
                design_dep,
            )
            build_ids.append("browser_flow")

        verify_dep = build_ids or design_dep
        if build_ids or "docs" in domains or "media" in domains:
            if build_ids:
                add(
                    "run_tests",
                    "Verify with automated tests",
                    "Run the relevant suites and capture evidence.",
                    "coding",
                    "quick",
                    "bash",
                    [],
                    ["bash", "file:read"],
                    ["tests_passed:project suite"],
                    "high",
                    verify_dep,
                )
                verify_dep = ["run_tests"]
            if mission.risk_tier in (RiskTier.R4, RiskTier.R5, RiskTier.R6) or "security" in domains:
                add(
                    "security_review",
                    "Security and blast-radius review",
                    "Audit the diff for vulnerabilities, secret leaks, and scope escapes.",
                    "security",
                    "general",
                    "general-purpose",
                    [],
                    ["file:read", "bash"],
                    ["Review verdict recorded; no high-severity findings open"],
                    "high",
                    verify_dep,
                    new_profile=True,
                    profile_key="security",
                )
                verify_dep = ["security_review"]
            if "docs" in domains or "media" in domains or huge:
                add(
                    "docs_delivery",
                    "Docs and final delivery",
                    "Write user-facing docs and stage deliverables in outputs.",
                    "docs",
                    "quick",
                    "general-purpose",
                    self._skills_for("docs", prompt) + self._skills_for("media", prompt),
                    ["file:read", "file:write"],
                    ["file_written:/mnt/user-data/outputs/delivery.md"],
                    "medium",
                    verify_dep,
                )
        if not tasks:
            tasks.append(self._single_quick_task(prompt, mission))
        return tasks

    def _fit_budget(self, tasks: list[AutoSubtask], budget: int) -> list[AutoSubtask]:
        if len(tasks) <= budget:
            return tasks
        # Merge order: fold design into first build, fold security into tests.
        by_id = {t.subtask_id: t for t in tasks}
        if "repo_map_design" in by_id and len(by_id) > budget:
            design = by_id.pop("repo_map_design")
            for t in by_id.values():
                t.dependencies = [d for d in t.dependencies if d != "repo_map_design"]
                if t.subtask_id.startswith("build_") and "Repo map" not in t.description:
                    t.description = f"Repo map folded in ({design.title}). " + t.description
                    break
        if "security_review" in by_id and len(by_id) > budget:
            sec = by_id.pop("security_review")
            if "run_tests" in by_id:
                by_id["run_tests"].acceptance_criteria.extend(sec.acceptance_criteria)
        ordered = [t for t in tasks if t.subtask_id in by_id]
        # Re-point dangling deps to the surviving chain head.
        alive = {t.subtask_id for t in ordered}
        for t in ordered:
            t.dependencies = [d for d in t.dependencies if d in alive]
        return ordered[:budget]

    # -- assignment, resources, goal --------------------------------------

    def _draft_profiles(self, subtasks: list[AutoSubtask], huge: bool, mission: Mission) -> list[NewProfileSpec]:
        seen: dict[str, NewProfileSpec] = {}
        for sub in subtasks:
            if not sub.needs_new_profile or not sub.profile_name:
                continue
            key = next((k for k, v in _SPECIALIST_PROFILES.items() if v["name"] == sub.profile_name), None)
            if key is None or key in seen or len(seen) >= 3:
                continue
            template = _SPECIALIST_PROFILES[key]
            seen[key] = NewProfileSpec(
                name=template["name"],
                description=template["description"],
                system_prompt_outline=[
                    f"Role: {template['description']}",
                    f"Scope: own '{sub.title}' only; never touch sibling scopes.",
                    "Boundaries: reversible steps, verify before claiming, escalate destructive actions.",
                    f"Acceptance: {'; '.join(sub.acceptance_criteria[:2]) or 'lead verifies output'}",
                ],
                tools=template["tools"],
                disallowed_tools=list(template["disallowed_tools"]),
                skills=list(template["skills"] or sub.skills),
                max_turns=int(template["max_turns"]),
                category=str(template["category"]),
                rationale=f"No builtin covers {sub.domain} with skills {sub.skills or 'baseline'}; drafting a bounded specialist.",
            )
        return list(seen.values())

    def _skills_for(self, domain: str, prompt: str) -> list[str]:
        skills = list(_DOMAIN_SKILLS.get(domain, []))
        low = prompt.lower()
        if domain == "research":
            if "github" in low or "repo" in low:
                skills.append("github-deep-research")
            if any(w in low for w in ["paper", "literature", "academic", "study"]):
                skills.append("systematic-literature-review")
            if any(w in low for w in ["market", "company", "business", "competitor"]):
                skills.append("consulting-analysis")
        if domain == "data" and any(w in low for w in ["chart", "dashboard", "visual", "plot"]):
            skills.append("chart-visualization")
        if domain == "docs" and any(w in low for w in ["slide", "deck", "pitch"]):
            skills.append("ppt-generation")
        if domain == "media":
            if "video" in low:
                skills = ["video-generation"]
            elif "podcast" in low or "audio" in low:
                skills = ["podcast-generation"]
            elif "music" in low:
                skills = ["music-generation"]
        out: list[str] = []
        for s in skills:
            if s not in out:
                out.append(s)
        return out

    def _select_skills(
        self,
        prompt: str,
        domains: list[str],
        subtasks: list[AutoSubtask],
        allowed: set | None = None,
    ) -> list[str]:
        def keep(candidates: list[str]) -> list[str]:
            if allowed is None:
                return list(candidates)
            return [s for s in candidates if s in allowed]

        ordered: list[str] = []
        for sub in subtasks:
            for s in keep(sub.skills or self._skills_for(sub.domain, prompt)):
                if s not in ordered:
                    ordered.append(s)
        for d in domains:
            for s in keep(self._skills_for(d, prompt)):
                if s not in ordered:
                    ordered.append(s)
        return ordered

    def _select_tool_groups(self, subtasks: list[AutoSubtask]) -> list[str]:
        ordered: list[str] = []
        for sub in subtasks:
            for g in sub.tool_groups or _DOMAIN_TOOL_GROUPS.get(sub.domain, ["file:read"]):
                if g not in ordered:
                    ordered.append(g)
        return ordered or ["file:read"]

    def _decide_delegation(self, subtasks: list[AutoSubtask], waves: list[ExecutionWave], huge: bool):
        if len(subtasks) == 1:
            return False, "Single bounded item — direct execution beats delegation overhead."
        parallel = any(len(w.task_ids) > 1 for w in waves)
        if parallel:
            return True, f"{len(subtasks)} items across {len(waves)} waves with parallel scope — delegating."
        if huge:
            return True, "Huge task split for context isolation and bounded scopes."
        return True, "Multi-step work with clear handoff boundaries — delegating per item."

    def _acceptance(self, mission: Mission, subtasks: list[AutoSubtask]) -> list[str]:
        criteria = list(mission.acceptance_criteria)
        for sub in subtasks:
            for ac in sub.acceptance_criteria:
                if ac not in criteria:
                    criteria.append(f"[{sub.subtask_id}] {ac}")
        return criteria

    def _apply_capabilities(self, subtasks: list[AutoSubtask], capabilities: dict[str, Any] | None) -> tuple:
        """Validate planned skills/tools against real deployment capabilities.

        Unknown (None) capability values skip that check and are recorded —
        never blocking. Returns (notes, allowed_skills) where allowed_skills is
        None when skill availability is unknown (no filtering downstream).
        """
        notes: list[str] = []
        allowed: set | None = None
        if not capabilities:
            notes.append("Capabilities unvalidated (no deployment snapshot provided).")
            return notes, allowed
        enabled = capabilities.get("enabled_skills")
        if enabled is None:
            notes.append("Skill availability unknown — planned skills assumed installable.")
        else:
            allowed = set(enabled)
            available = set(enabled)
            for sub in subtasks:
                dropped = [s for s in sub.skills if s not in available]
                if dropped:
                    sub.skills = [s for s in sub.skills if s in available]
                    notes.append(f"[{sub.subtask_id}] skill fallback: {', '.join(dropped)} unavailable, using baseline tools.")
                    if sub.needs_new_profile and sub.profile_name:
                        notes.append(f"[{sub.subtask_id}] profile {sub.profile_name} needs skills {dropped} — install them or narrow its scope.")
        if capabilities.get("browser") is False:
            for sub in subtasks:
                if "browser" in sub.tool_groups:
                    sub.tool_groups = ["web" if g == "browser" else g for g in sub.tool_groups]
                    sub.description += " [Fallback: live browser unavailable — use web_fetch evidence instead.]"
            notes.append("Browser automation unavailable — browser scopes fall back to web_fetch evidence.")
        elif capabilities.get("browser") is None and any("browser" in s.tool_groups for s in subtasks):
            notes.append("Browser availability unknown — plan assumes the browser extra is installed.")
        if capabilities.get("bash") is False:
            for sub in subtasks:
                if sub.assignee == "bash":
                    sub.assignee = "general-purpose"
                    sub.category = "general"
                    notes.append(f"[{sub.subtask_id}] shell unavailable — reassigned bash work to general-purpose.")
            for sub in subtasks:
                sub.tool_groups = [g for g in sub.tool_groups if g != "bash"] or ["file:read"]
            notes.append("Shell execution unavailable — test/verify cards describe commands for the operator.")
        if not notes:
            notes.append("All planned skills and tool groups validated against deployment capabilities.")
        return notes, allowed

    def _estimate_cost(self, subtasks: list[AutoSubtask], token_budget: int | None) -> dict[str, Any]:
        """Heuristic per-card cost roll-up with an optional budget verdict."""
        per_card = []
        total_tokens = 0
        total_seconds = 0
        for sub in subtasks:
            tokens = _DOMAIN_TOKEN_BASE.get(sub.domain, _DOMAIN_TOKEN_BASE["general"])
            seconds = _DOMAIN_SECONDS_BASE.get(sub.domain, _DOMAIN_SECONDS_BASE["general"])
            if sub.priority in ("high", "critical"):
                tokens = int(tokens * 1.25)
            sub.tokens_estimate = tokens
            sub.seconds_estimate = seconds
            total_tokens += tokens
            total_seconds += seconds
            per_card.append({"subtask_id": sub.subtask_id, "tokens": tokens, "seconds": seconds})
        estimate: dict[str, Any] = {
            "per_card": per_card,
            "total_tokens": total_tokens,
            "total_seconds": total_seconds,
            "budget_status": "unknown",
            "budget_note": "No run token budget provided — totals are advisory.",
        }
        if token_budget is not None and token_budget > 0:
            if total_tokens <= token_budget:
                estimate["budget_status"] = "ok"
                estimate["budget_note"] = f"Estimated {total_tokens} tokens fit in budget {token_budget}."
            else:
                estimate["budget_status"] = "over"
                estimate["budget_note"] = f"Estimated {total_tokens} tokens exceed budget {token_budget} — cut scope, lower max_subtasks, or raise the budget before executing."
        return estimate

    def _derive_assumptions(self, prompt: str, domains: list[str], mission: Mission, intent: Any) -> tuple:
        """Explicit assumptions + open unknowns so the user can correct in one shot."""
        assumptions: list[str] = []
        unknowns: list[str] = []
        if "deadline" not in prompt.lower() and "asap" not in prompt.lower():
            assumptions.append("No deadline mentioned — steady, verification-first pace assumed.")
        if mission.risk_tier in (RiskTier.R4, RiskTier.R5, RiskTier.R6):
            assumptions.append("High-impact work — staging verification assumed before any production touch.")
        if any(d in domains for d in ("coding", "deploy", "browser")) and not _ENV_WORD_RE.search(prompt):
            unknowns.append("Target environment not specified (local / staging / production?).")
        if _VAGUE_QUALITY_RE.search(prompt) and not _METRIC_WORD_RE.search(prompt):
            unknowns.append("Success metric not specified (what number proves 'better'? faster by how much?).")
        confidence = float(getattr(intent, "confidence_score", 0.9) or 0.9)
        if confidence < 0.75:
            unknowns.append(f"Request is ambiguous (intent confidence {confidence}) — key choices were inferred, see assumptions.")
        if not unknowns:
            assumptions.append("Request is fully specified — no open unknowns.")
        return assumptions, unknowns

    def _run_llm_review(
        self,
        mission: Mission,
        acceptance: list[str],
        subtasks: list[AutoSubtask],
        llm_reviewer: Callable[[str], str] | None,
    ) -> dict[str, Any]:
        """Optional LLM reviewer pass for load-bearing plans (P2 hook).

        Contract: ``fn(plan_markdown) -> "VERDICT\\nnote"`` with VERDICT in
        APPROVED / NEEDS_REVISION / REJECTED. Heuristic gate stays
        authoritative; hook failures are isolated and recorded, never blocking.
        """
        high_stakes = mission.risk_tier in (RiskTier.R4, RiskTier.R5, RiskTier.R6)
        if llm_reviewer is None:
            return {
                "configured": False,
                "verdict": "SKIPPED",
                "note": "LLM review recommended for R4+ plans but no reviewer is configured." if high_stakes else "Heuristic gate sufficient for this risk tier.",
            }
        try:
            raw = (llm_reviewer(self._render_plan_text(mission, subtasks, [], acceptance)) or "").strip()
        except Exception as exc:  # noqa: BLE001 — hook isolation
            return {"configured": True, "verdict": "ERROR", "note": f"Reviewer failed: {exc}"}
        first, _, rest = raw.partition("\n")
        verdict = first.strip().upper()
        if verdict not in ("APPROVED", "NEEDS_REVISION", "REJECTED"):
            return {"configured": True, "verdict": "UNKNOWN", "note": raw[:500]}
        return {"configured": True, "verdict": verdict, "note": rest.strip()[:1000]}

    def _approval_policy(self, mission: Mission) -> dict[str, str]:
        tier = mission.risk_tier
        if tier in (RiskTier.R0, RiskTier.R1):
            return {"default": "auto-approve", "note": "read-only work, no side effects"}
        if tier == RiskTier.R2:
            return {"default": "auto-approve with audit", "note": "reversible local changes under git"}
        if tier == RiskTier.R3:
            return {"default": "auto with log", "note": "external low-impact calls are logged"}
        if tier == RiskTier.R4:
            return {"default": "proof-required", "note": "side effects need verification evidence first"}
        return {"default": "human-gate", "note": "destructive/strategic work waits for explicit approval"}

    def _parse_schedule(self, prompt: str) -> dict[str, Any]:
        """Deterministic schedule hints: explicit due phrase + urgency flag."""
        due = _DUE_PHRASE_RE.search(prompt)
        return {
            "due_phrase": due.group(0) if due else None,
            "urgent": bool(_URGENT_RE.search(prompt)),
        }

    def _build_approval(
        self,
        *,
        autonomy: str,
        mission: Mission,
        goal: str,
        plan_hash: str,
        gate_summary: str,
    ) -> dict[str, Any]:
        """Structured approval request the runtime can actually block on.

        Empty gate (``required: False``) for fully autonomous plans; otherwise
        an explicit question with options — never just prose.
        """
        if autonomy != "gated":
            return {"required": False}
        return {
            "required": True,
            "question": f"Plan {plan_hash} is {mission.risk_tier.value.upper()} / {gate_summary} Approve execution?",
            "context": goal,
            "options": ["approve and execute", "revise plan", "cancel"],
            "risk_tier": mission.risk_tier.value,
            "plan_hash": plan_hash,
        }

    def replan(
        self,
        plan: AutonomousPlan,
        *,
        done_ids: list[str] = (),
        failed_ids: list[str] = (),
        feedback: str = "",
        max_subtasks: int | None = None,
        capabilities: dict[str, Any] | None = None,
        kanban_store: Any = None,
        token_budget: int | None = None,
        llm_reviewer: Callable[[str], str] | None = None,
    ) -> AutonomousPlan:
        """Recompile after execution feedback, preserving completed work.

        Done cards are frozen verbatim (same ids, marked done on the board);
        failed cards are kept and annotated with the feedback; remaining scope
        is regenerated (feedback appended to the prompt) so new scope can enter
        without redoing finished work. Bumps revision, sets supersedes.
        """
        done = set(done_ids or ())
        failed = set(failed_ids or ())
        frozen = [s for s in plan.subtasks if s.subtask_id in done]
        annotated = []
        for s in plan.subtasks:
            if s.subtask_id in failed:
                note = f" [Rework note: {safe_text(feedback, 200)}]" if feedback else ""
                annotated.append(
                    AutoSubtask(
                        subtask_id=s.subtask_id,
                        title=s.title,
                        description=s.description + note,
                        domain=s.domain,
                        category=s.category,
                        assignee=s.assignee,
                        needs_new_profile=s.needs_new_profile,
                        profile_name=s.profile_name,
                        skills=list(s.skills),
                        tool_groups=list(s.tool_groups),
                        acceptance_criteria=list(s.acceptance_criteria),
                        priority="high" if feedback else s.priority,
                        dependencies=[d for d in s.dependencies if d in done or d in failed],
                        wave_index=s.wave_index,
                    )
                )
        regen_prompt = plan.raw_prompt + (f"\n\nReplan notes from execution: {feedback}" if feedback else "")
        fresh = self._decompose(
            regen_prompt,
            detect_domains(regen_prompt),
            MissionCompiler().compile(plan.raw_prompt),
            plan.needs_deep_research,
            len(plan.subtasks) >= 4,
        )
        protected = done | failed
        others = [s for s in fresh if s.subtask_id not in protected]
        merged = frozen + annotated + others
        budget = max(1, int(max_subtasks or self.max_subtasks or len(merged)))
        while len(merged) > budget:
            victim = next((s for s in reversed(merged) if s.subtask_id not in done and s.subtask_id not in failed), None)
            if victim is None:
                break
            merged = [s for s in merged if s.subtask_id != victim.subtask_id]
        alive = {s.subtask_id for s in merged}
        for s in merged:
            s.dependencies = [d for d in s.dependencies if d in alive]

        new_plan = self.plan(
            plan.raw_prompt,
            max_subtasks=budget,
            capabilities=capabilities,
            kanban_store=None,
            token_budget=token_budget,
            llm_reviewer=llm_reviewer,
        )
        # Splice the preserved cards back over the regenerated ones.
        reg_by_id = {s.subtask_id: s for s in new_plan.subtasks}
        for s in merged:
            if s.subtask_id in done or s.subtask_id in failed:
                reg_by_id[s.subtask_id] = s
        ordered = [s for s in merged if s.subtask_id in reg_by_id]
        ordered += [s for s in new_plan.subtasks if s.subtask_id not in {x.subtask_id for x in ordered}]
        ordered = ordered[:budget]
        alive = {s.subtask_id for s in ordered}
        for s in ordered:
            s.dependencies = [d for d in s.dependencies if d in alive]
        dag = {s.subtask_id: list(s.dependencies) for s in ordered}
        waves = partition_execution_waves(dag)
        wave_of = {tid: w.wave_index for w in waves for tid in w.task_ids}
        for s in ordered:
            s.wave_index = wave_of.get(s.subtask_id, 1)
        new_plan.subtasks = ordered
        new_plan.execution_waves = [w.to_dict() for w in waves]
        keep_mission = MissionCompiler().compile(plan.raw_prompt)
        new_plan.kanban_board = self._build_board(new_plan.plan_id, keep_mission, ordered, done).to_dict()
        new_plan.cost_estimate = self._estimate_cost(ordered, token_budget)
        new_plan.acceptance_criteria = self._acceptance(keep_mission, ordered)
        new_plan.revision = plan.revision + 1
        new_plan.supersedes = plan.plan_hash
        if kanban_store is not None and not new_plan.duplicate_of_board:
            try:
                kanban_store.put_board(self._build_board(new_plan.plan_id, keep_mission, ordered, done))
                new_plan.kanban_persisted = True
            except Exception:  # noqa: BLE001 — persistence never breaks planning
                pass
        return new_plan

    def _build_board(
        self,
        plan_id: str,
        mission: Mission,
        subtasks: list[AutoSubtask],
        done_ids: Any = (),
    ) -> KanbanBoard:
        board = KanbanBoard(
            board_id=f"board_{plan_id}",
            title=(mission.interpreted_intent[:60] or "Autonomous plan"),
        )
        done_set = set(done_ids or ())
        for sub in subtasks:
            board.tasks[sub.subtask_id] = KanbanTask(
                task_id=sub.subtask_id,
                board_id=board.board_id,
                title=sub.title,
                description=f"{sub.description}\nAcceptance: {'; '.join(sub.acceptance_criteria)}",
                column="done" if sub.subtask_id in done_set else "todo",
                priority=sub.priority,  # type: ignore[arg-type]
                assignee=sub.assignee,
                dependencies=list(sub.dependencies),
                metadata={
                    "domain": sub.domain,
                    "category": sub.category,
                    "skills": sub.skills,
                    "wave": sub.wave_index,
                    "profile": sub.profile_name,
                },
            )
        return board

    def _goal_statement(self, mission: Mission, subtasks: list[AutoSubtask], profiles: list[NewProfileSpec], autonomy: str) -> str:
        return f"{mission.desired_outcome} Done means {len(mission.acceptance_criteria)} mission criteria plus {len(subtasks)} work items complete ({len(profiles)} new specialist profiles), autonomy={autonomy}."

    def _render_plan_text(
        self,
        mission: Mission,
        subtasks: list[AutoSubtask],
        profiles: list[NewProfileSpec],
        acceptance: list[str],
    ) -> str:
        lines = [
            f"# Autonomous plan: {mission.interpreted_intent}",
            "",
            "## Prerequisites & Dependencies",
            "- Workspace access with file tooling; skills installed per card.",
            "- Model with tool calling; approval policy per risk tier.",
            "",
            "## Execution steps",
        ]
        for sub in subtasks:
            lines.append(f"- {sub.subtask_id}: {sub.title} (owner {sub.assignee}, deps {sub.dependencies or 'none'})")
        if profiles:
            lines.append("")
            lines.append("## Staffing")
            for p in profiles:
                lines.append(f"- New profile {p.name}: {p.description}")
        lines += ["", "## Verification plan & automated tests"]
        for ac in acceptance[:8]:
            lines.append(f"- Verify: {ac}")
        lines.append("- Run pytest on touched suites and capture evidence before claiming done.")
        return "\n".join(lines)

    def _user_summary(self, plan: AutonomousPlan, mission: Mission, intent: Any) -> str:
        lines = [
            "## Your plan is ready — nothing else needed from you",
            "",
            f"- **Understood:** {mission.interpreted_intent}",
            f"- **Risk:** `{plan.risk_tier.upper()}` · **Reasoning:** `{plan.reasoning_tier}` · **Autonomy:** `{plan.autonomy}` · **Intent confidence:** `{intent.confidence_score}`",
            f"- **Research:** {'yes — ' + plan.research_rationale if plan.needs_deep_research else 'no — ' + plan.research_rationale}",
            f"- **Subagents:** {'yes — ' + plan.delegation_rationale if plan.needs_subagents else 'no — ' + plan.delegation_rationale}",
            "",
            "### Work items (Kanban ready)",
        ]
        for sub in plan.subtasks:
            owner = sub.profile_name or sub.assignee
            new = " · *new profile*" if sub.needs_new_profile else ""
            lines.append(f"- `{sub.subtask_id}` (wave {sub.wave_index}, {sub.priority}) — {sub.title} → **{owner}**{new}")
        if plan.new_profiles:
            lines.append("")
            lines.append("### New specialist profiles drafted")
            for p in plan.new_profiles:
                lines.append(f"- **{p.name}** ({p.category}) — {p.description}")
        lines += [
            "",
            f"### Skills & tools auto-selected: {', '.join(plan.skills_to_use) or 'baseline only'}",
            f"Tool groups: {', '.join(plan.tool_groups)}",
        ]
        if plan.capability_notes:
            lines.append(f"Capability check: {'; '.join(plan.capability_notes[:3])}")
        cost = plan.cost_estimate
        if cost:
            lines.append(f"Cost estimate: ~{cost.get('total_tokens', 0)} tokens / ~{cost.get('total_seconds', 0)}s ({cost.get('budget_status', 'unknown')}). {cost.get('budget_note', '')}")
        if plan.assumptions or plan.unknowns:
            lines += ["", "### Assumptions & unknowns — correct me in one shot"]
            for a in plan.assumptions:
                lines.append(f"- Assumed: {a}")
            for u in plan.unknowns:
                lines.append(f"- Unknown: {u}")
        if plan.llm_review.get("configured"):
            lines += ["", f"### LLM review: `{plan.llm_review.get('verdict')}` — {plan.llm_review.get('note', '')}"]
        lines += [
            "",
            f"### Goal: {plan.goal_statement}",
            "",
            f"### Self-review gate: `{plan.hyperplan_status}` — {plan.hyperplan_summary}",
        ]
        if plan.hyperplan_status == "BLOCKED" or plan.autonomy == "gated":
            lines.append("Approval is required before execution starts.")
            question = plan.approval_request.get("question", "")
            options = plan.approval_request.get("options", [])
            if question:
                lines.append(f"> {question}")
            if options:
                lines.append(f"Options: {' / '.join(options)}")
        else:
            lines.append("Execution can start immediately — I own every step from here.")
        schedule = plan.execution_config.get("schedule", {})
        if schedule.get("urgent") or schedule.get("due_phrase"):
            lines.append(f"Schedule: urgent={schedule.get('urgent')}, due={schedule.get('due_phrase')}.")
        if plan.revision > 1:
            lines.append(f"Revision {plan.revision} (supersedes {plan.supersedes}).")
        if plan.duplicate_of_board:
            lines.append(f"Duplicate request — reusing board {plan.duplicate_of_board}.")
        return "\n".join(lines)


def review_wave(
    plan: AutonomousPlan,
    wave_task_results: dict[str, bool],
    cumulative_passed: int,
    cumulative_failed: int,
    total_tasks: int,
    consecutive_failures: int = 0,
    invariant_breach: bool = False,
) -> dict[str, Any]:
    """Wave-boundary hook: fold one wave's results into plan health.

    Returns the validity report plus executor-ready next steps: which cards to
    local-replan (failed ids), whether to halt for a global replan, and which
    wave comes next. Pure function — safe to call after every execution wave.
    """
    report = PlanValidityMonitor.evaluate_wave(
        wave_task_results,
        cumulative_passed,
        cumulative_failed,
        total_tasks,
        consecutive_failures=consecutive_failures,
        invariant_breach=invariant_breach,
    )
    done = set(wave_task_results)
    next_cards = [s.subtask_id for s in plan.subtasks if s.subtask_id not in done and all(d in done for d in s.dependencies)]
    return {
        "validity": report.to_dict(),
        "failed_cards": report.failed_task_ids,
        "next_cards": next_cards,
        "action": report.next_action,
        "halt": report.decision.value == "global_replan",
    }
