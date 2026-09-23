"""Dynamic Prompt Ingestion & Intent Perception Engine.

Analyzes raw user prompts to dynamically determine:
- Intent classification (BUILD, RESEARCH, REFACTOR, DEBUG, EXPLORE, ORCHESTRATE, LEARN, BOOST)
- Domain classification (engineering, research, data, browser, design, devops, security, etc.)
- Complexity scoring and Execution Tier selection (FAST_DIRECT, DEEP_REASONING, AUTONOMOUS_WORKFLOW, MULTIAGENT_SWARM)
- Auto slash-command detection, validated at runtime against the real command
  catalog (alpha.commands.catalog). Only commands that actually exist there may
  be detected or suggested; anything else is dropped (see EMITTABLE_COMMANDS).
- Required dynamic capabilities (subagents, bot creation, skill authoring and download, tools, MCP, open-source search)
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def _catalog_commands() -> frozenset[str]:
    """Real slash-commands, read-only from alpha.commands.catalog.

    Returns an empty frozenset when the catalog cannot be imported: an
    unvalidatable command is never detected or suggested (honest ``None``).
    """
    try:
        from alpha.commands.catalog import get_default_catalog_entries
    except Exception:  # pragma: no cover - defensive: never emit unvalidated commands
        return frozenset()
    return frozenset(entry[0] for entry in get_default_catalog_entries())


class DynamicIntentType(StrEnum):
    BOOST = "boost"
    BUILD = "build"
    RESEARCH = "research"
    REFACTOR = "refactor"
    DEBUG = "debug"
    EXPLORE = "explore"
    LEARN = "learn"
    ORCHESTRATE = "orchestrate"
    GENERAL = "general"


class DynamicExecutionTier(StrEnum):
    FAST_DIRECT = "fast_direct"
    DEEP_REASONING = "deep_reasoning"
    AUTONOMOUS_WORKFLOW = "autonomous_workflow"
    MULTIAGENT_SWARM = "multiagent_swarm"


@dataclass
class PerceivedIntent:
    """Structured perception output representing understood intent, domains, and capabilities."""

    raw_prompt: str
    intent_type: DynamicIntentType
    primary_domain: str
    secondary_domains: list[str] = field(default_factory=list)
    complexity_score: float = 0.5  # 0.0 (trivial) to 1.0 (ultra-complex)
    execution_tier: DynamicExecutionTier = DynamicExecutionTier.AUTONOMOUS_WORKFLOW
    detected_slash_command: str | None = None
    # Always validated against alpha.commands.catalog before being set;
    # None means "no catalog-validated command could be suggested".
    suggested_slash_command: str | None = "/plan"

    # Capability requirements
    need_subagents: bool = True
    need_bot_creation: bool = True
    need_soul_tailoring: bool = True
    need_model_selection: bool = True
    need_skill_creation: bool = False
    need_skill_download: bool = False
    need_open_source_search: bool = False
    need_web_search: bool = False
    need_tool_selection: bool = True
    need_mcp_selection: bool = False
    need_memory_consolidation: bool = True

    # High-level objectives extracted
    extracted_goals: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DynamicPerceptionEngine:
    """Analyzes raw user input and context to perceive goals and plan dynamic execution."""

    # Every slash-command string this module may emit. Each key must exist in
    # alpha.commands.catalog; the static test test_dynamic_layer.py enforces it.
    EMITTABLE_COMMANDS: frozenset[str] = frozenset({
        "/plan",
        "/learn",
        "/fix",
        "/review",
        "/research",
        "/swarm",
    })

    # (Removed phantom "/boost": it does not exist in alpha.commands.catalog.
    #  "boost"/"dynamic" keywords still classify the intent as BOOST — an
    #  internal intent enum, not a slash-command.)
    SLASH_COMMAND_PATTERNS: dict[str, re.Pattern] = {
        "/plan": re.compile(r"^\s*/plan\b|(\b(plan|breakdown|decompose|roadmap)\b)", re.IGNORECASE),
        "/learn": re.compile(r"^\s*/learn\b|(\b(create skill|learn skill|author skill|download skill)\b)", re.IGNORECASE),
        "/fix": re.compile(r"^\s*/fix\b|(\b(fix|bug|repair|patch|resolve issue)\b)", re.IGNORECASE),
        "/review": re.compile(r"^\s*/review\b|(\b(review|audit|critique|check code)\b)", re.IGNORECASE),
        "/research": re.compile(r"^\s*/research\b|(\b(research|investigate|explore options|survey)\b)", re.IGNORECASE),
        "/swarm": re.compile(r"^\s*/swarm\b|(\b(swarm|multi-?agent|bot team|workgroup)\b)", re.IGNORECASE),
    }

    DOMAIN_KEYWORDS: dict[str, list[str]] = {
        "engineering": ["code", "implement", "build", "api", "function", "class", "refactor", "backend", "frontend", "architecture", "test"],
        "research": ["research", "survey", "compare", "literature", "investigate", "paper", "deep dive"],
        "open_source": ["open source", "github", "repository", "repo", "library", "package", "clone", "git"],
        "web": ["websearch", "web search", "google", "scrape", "browse", "url", "internet"],
        "skills": ["skill", "skills", "skill creation", "skill downloading", "hub", "authoring", "curator"],
        "bots": ["bot", "bots", "subagent", "subagents", "agent", "swarm", "soul", "profile", "persona", "clone"],
        "mcp": ["mcp", "model context protocol", "server", "tools", "external integration"],
        "data": ["data", "database", "sql", "analytics", "dataset", "etl", "metric"],
        "security": ["security", "audit", "auth", "permission", "vulnerability", "token"],
    }

    def perceive(self, prompt: str, context: dict[str, Any] | None = None) -> PerceivedIntent:
        """Parse raw prompt into structured dynamic perception."""
        cleaned = (prompt or "").strip()
        context = context or {}

        # 1. Detect slash commands (validated against the real catalog)
        catalog = _catalog_commands()
        detected_slash: str | None = None
        for cmd, pat in self.SLASH_COMMAND_PATTERNS.items():
            if cmd not in catalog:
                continue  # never detect a command the catalog does not define
            if pat.search(cleaned):
                detected_slash = cmd
                break

        # 2. Domain detection and scoring
        domain_scores: dict[str, int] = {}
        cleaned_lower = cleaned.lower()
        for domain, keywords in self.DOMAIN_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in cleaned_lower)
            if count > 0:
                domain_scores[domain] = count

        sorted_domains = sorted(domain_scores.keys(), key=lambda d: domain_scores[d], reverse=True)
        primary_domain = sorted_domains[0] if sorted_domains else "engineering"
        secondary_domains = sorted_domains[1:] if len(sorted_domains) > 1 else []

        # 3. Intent type determination
        # NOTE: "boost"/"dynamic" keywords classify the *intent* as BOOST.
        # This is an internal DynamicIntentType value, not a slash-command —
        # no command string is emitted here (the old /boost suggestion was a
        # phantom that never existed in alpha.commands.catalog).
        intent_type = DynamicIntentType.GENERAL
        if "boost" in cleaned_lower or "dynamic" in cleaned_lower:
            intent_type = DynamicIntentType.BOOST
        elif detected_slash == "/learn" or "skill" in cleaned_lower:
            intent_type = DynamicIntentType.LEARN
        elif detected_slash == "/fix" or "bug" in cleaned_lower or "fix" in cleaned_lower:
            intent_type = DynamicIntentType.DEBUG
        elif detected_slash == "/research" or "research" in cleaned_lower:
            intent_type = DynamicIntentType.RESEARCH
        elif any(k in cleaned_lower for k in ["build", "create", "implement", "develop"]):
            intent_type = DynamicIntentType.BUILD
        elif any(k in cleaned_lower for k in ["refactor", "optimize", "clean"]):
            intent_type = DynamicIntentType.REFACTOR
        elif any(k in cleaned_lower for k in ["swarm", "multi-agent", "orchestrate"]):
            intent_type = DynamicIntentType.ORCHESTRATE

        # 4. Complexity and Execution Tier
        length_factor = min(1.0, len(cleaned) / 500.0)
        domain_factor = min(1.0, len(domain_scores) / 5.0)
        multi_intent_factor = 0.3 if any(w in cleaned_lower for w in ["and", "also", "then", "after", "includes", "every"]) else 0.0
        complexity_score = min(1.0, round(0.3 * length_factor + 0.4 * domain_factor + 0.3 * multi_intent_factor + 0.2, 2))

        if complexity_score >= 0.75 or intent_type in (DynamicIntentType.BOOST, DynamicIntentType.ORCHESTRATE) or "swarm" in cleaned_lower:
            execution_tier = DynamicExecutionTier.MULTIAGENT_SWARM
        elif complexity_score >= 0.5:
            execution_tier = DynamicExecutionTier.AUTONOMOUS_WORKFLOW
        elif complexity_score >= 0.3:
            execution_tier = DynamicExecutionTier.DEEP_REASONING
        else:
            execution_tier = DynamicExecutionTier.FAST_DIRECT

        # 5. Dynamic capability flags
        need_open_source = bool("open source" in cleaned_lower or "github" in cleaned_lower or "repo" in cleaned_lower or intent_type == DynamicIntentType.BOOST)
        need_web_search = bool("websearch" in cleaned_lower or "web search" in cleaned_lower or "google" in cleaned_lower or "search" in cleaned_lower or intent_type == DynamicIntentType.BOOST)
        need_skill_creation = bool("skill creation" in cleaned_lower or "create skill" in cleaned_lower or "author" in cleaned_lower or intent_type == DynamicIntentType.BOOST)
        need_skill_download = bool("skill download" in cleaned_lower or "downloading" in cleaned_lower or intent_type == DynamicIntentType.BOOST)
        need_mcp = bool("mcp" in cleaned_lower or "protocol" in cleaned_lower or intent_type == DynamicIntentType.BOOST)
        need_subagents = bool(execution_tier in (DynamicExecutionTier.MULTIAGENT_SWARM, DynamicExecutionTier.AUTONOMOUS_WORKFLOW) or "subagent" in cleaned_lower or "bot" in cleaned_lower or intent_type == DynamicIntentType.BOOST)
        need_bot_creation = bool("bot" in cleaned_lower or "profile" in cleaned_lower or "soul" in cleaned_lower or intent_type == DynamicIntentType.BOOST)

        # 6. Extract goals/phases
        extracted_goals: list[str] = []
        if intent_type == DynamicIntentType.BOOST:
            extracted_goals = [
                "Perceive intent and select execution path",
                "Decompose tasks with verification criteria and saga compensation",
                "Assemble dynamic resources (bots, SOUL directives, skills, tools, MCP)",
                "Bridge execution to Dynamic Workflow Engine with DAG scheduling and replanning",
                "Consolidate learning and memory (episodic, strategy memory, evolutionary breeding)",
            ]
        else:
            sentences = [s.strip() for s in re.split(r"[.\n;]+", cleaned) if len(s.strip()) > 5]
            extracted_goals = sentences[:5] if sentences else [f"Execute task: {cleaned[:80]}"]

        # Suggestion: only catalog-validated commands, never a phantom.
        if detected_slash:
            suggested_slash: str | None = detected_slash
        elif execution_tier == DynamicExecutionTier.MULTIAGENT_SWARM and "/swarm" in catalog:
            suggested_slash = "/swarm"
        elif "/plan" in catalog:
            suggested_slash = "/plan"
        else:
            suggested_slash = None  # catalog unavailable — suggest nothing rather than an unvalidated command

        return PerceivedIntent(
            raw_prompt=cleaned,
            intent_type=intent_type,
            primary_domain=primary_domain,
            secondary_domains=secondary_domains,
            complexity_score=complexity_score,
            execution_tier=execution_tier,
            detected_slash_command=detected_slash,
            suggested_slash_command=suggested_slash,
            need_subagents=need_subagents,
            need_bot_creation=need_bot_creation,
            need_soul_tailoring=True,
            need_model_selection=True,
            need_skill_creation=need_skill_creation,
            need_skill_download=need_skill_download,
            need_open_source_search=need_open_source,
            need_web_search=need_web_search,
            need_tool_selection=True,
            need_mcp_selection=need_mcp,
            need_memory_consolidation=True,
            extracted_goals=extracted_goals,
            metadata=context,
        )


_GLOBAL_PERCEPTION_ENGINE: DynamicPerceptionEngine | None = None


def get_dynamic_perception_engine() -> DynamicPerceptionEngine:
    global _GLOBAL_PERCEPTION_ENGINE
    if _GLOBAL_PERCEPTION_ENGINE is None:
        _GLOBAL_PERCEPTION_ENGINE = DynamicPerceptionEngine()
    return _GLOBAL_PERCEPTION_ENGINE
