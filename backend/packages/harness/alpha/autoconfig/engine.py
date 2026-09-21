"""Autonomous Self-Configuration Engine.

Analyzes user intent, estimates complexity, detects domain and risks, and
automatically synthesizes optimal runtime configurations (models, reasoning budgets,
tool whitelists, swarm topologies, and execution guardrails) without human setup.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from alpha.autoconfig.models import (
    ComplexityLevel,
    GoalAnalysis,
    ModelTier,
    OperatingMode,
    RuntimeTuningUpdate,
    SelfConfigProfile,
    TopologyType,
)

logger = logging.getLogger(__name__)

#: Call-site label recorded in the System One decision log (see evaluation/system_one_calibration.py).
SITE = "autoconfig"


class SelfConfigurationEngine:
    """Orchestrates autonomous intent decomposition and dynamic harness self-configuration."""

    def __init__(self, project_id: str = "default"):
        self.project_id = project_id
        self._active_profile: SelfConfigProfile | None = None
        self._analysis_history: list[GoalAnalysis] = []

    def _system_one_signals(self, goal: str, context: dict[str, Any]) -> dict[str, Any]:
        """Ask a System One model for domain / complexity / risk, else return {}.

        Returns {} whenever System One is disabled, unreachable, or not
        confident — the caller then keeps its heuristic values. System One
        answers all three questions in a single parallel request, so this costs
        roughly one fast call rather than three.
        """
        try:
            import asyncio

            from alpha.models.system_one import ChoiceQuestion, ScoreQuestion, get_system_one_client

            client = get_system_one_client()
            cfg = client.config
            if not cfg.enabled or not cfg.enable_goal_analysis or not client.is_available():
                return {}

            async def _query() -> dict[str, Any]:
                result = await client.evaluate(
                    {"goal": goal[:8000], "context": context or {}},
                    {
                        "domain": ChoiceQuestion(
                            instructions="Which domain does this goal belong to?",
                            criteria={
                                "coding": "Writing, debugging, refactoring or testing software.",
                                "deep_research": "Investigating, surveying or comparing information.",
                                "security_audit": "Auditing security, permissions, vulnerabilities or exploits.",
                                "architecture": "Designing or evolving systems, frameworks or engines.",
                                "autonomous_company": "Running or growing a business autonomously.",
                                "general": "None of the above fit well.",
                            },
                        ),
                        "complexity": ChoiceQuestion(
                            instructions="How complex is this goal for a single strong AI agent?",
                            criteria={
                                "trivial": "One immediate action.",
                                "simple": "A couple of clear steps.",
                                "moderate": "Several steps with some planning.",
                                "complex": "Many interdependent steps needing coordination.",
                                "research_frontier": "Open-ended, self-improving or frontier-level ambition.",
                            },
                        ),
                        "risk": ScoreQuestion(
                            instructions="How much damage could this goal do if executed wrongly?",
                            criteria=["Harmless", "Recoverable rework", "Affects production, money or security", "Irreversible or destructive"],
                        ),
                    },
                    site=SITE,
                )
                if result is None:
                    return {}
                out: dict[str, Any] = {}
                threshold = cfg.min_confidence
                domain_answer = result.get("domain")
                if domain_answer is not None and domain_answer.meets(threshold):
                    out["domain"] = str(domain_answer.value)
                complexity_answer = result.get("complexity")
                if complexity_answer is not None and complexity_answer.meets(threshold):
                    out["complexity"] = str(complexity_answer.value)
                risk_answer = result.get("risk")
                if risk_answer is not None and risk_answer.meets(threshold):
                    # Score is 0..3; normalise onto the 0..1 heuristic scale.
                    out["risk_score"] = max(0.0, min(1.0, float(risk_answer.value) / 3.0))
                return out

            # analyze_goal is sync; it runs on worker threads where no loop is
            # active. Fall back to {} rather than blocking a live loop.
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(_query())
            return {}
        except Exception:
            logger.debug("System One goal analysis unavailable; using heuristics.", exc_info=True)
            return {}

    def analyze_goal(self, goal: str, context: dict[str, Any] | None = None) -> GoalAnalysis:
        """Parse, classify, and decompose goal intent to determine optimal agent parameters."""
        ctx = context or {}
        g_lower = goal.lower()

        # 0. System One (Jev) pre-pass: typed, calibrated domain/complexity/risk
        # judgements. Empty dict means "no confident answer" -> heuristics run.
        s1 = self._system_one_signals(goal, ctx)

        # 1. Domain Detection
        coding_signals = ["code", "python", "test", "debug", "refactor", "api", "function", "backend", "frontend", "git", "bug", "patch"]
        research_signals = ["research", "survey", "compare", "paper", "investigate", "literature", "deep research", "market"]
        security_signals = ["security", "vulnerability", "auth", "exploit", "cve", "audit", "permission", "sandbox"]
        arch_signals = ["architecture", "harness", "framework", "rsi", "evolution", "lineage", "engine", "epistemic"]
        company_signals = ["company", "business", "product", "autonomous operation", "never stop", "perpetual"]

        domain_scores = {
            "coding": sum(1 for s in coding_signals if s in g_lower),
            "deep_research": sum(1 for s in research_signals if s in g_lower),
            "security_audit": sum(1 for s in security_signals if s in g_lower),
            "architecture": sum(1 for s in arch_signals if s in g_lower),
            "autonomous_company": sum(1 for s in company_signals if s in g_lower),
        }
        domain = s1.get("domain") or (max(domain_scores, key=domain_scores.get) if any(domain_scores.values()) else "general")

        # 2. Complexity Assessment
        conjunctions = len(re.findall(r"\b(and also|furthermore|as well as|moreover|in addition|additionally)\b", g_lower))
        verbs = len(re.findall(r"\b(build|create|implement|verify|test|refactor|benchmark|optimize|deploy|replicate)\b", g_lower))
        word_count = len(goal.split())
        
        frontier_keywords = ["asi", "never stop", "self-improving", "self replication", "meta-compiler", "perpetual", "world class"]
        has_frontier = any(k in g_lower for k in frontier_keywords)

        complexity_score = (conjunctions * 2) + verbs + (word_count // 15)
        if has_frontier or complexity_score >= 12:
            complexity = ComplexityLevel.RESEARCH_FRONTIER
        elif complexity_score >= 8:
            complexity = ComplexityLevel.COMPLEX
        elif complexity_score >= 4:
            complexity = ComplexityLevel.MODERATE
        elif complexity_score >= 2:
            complexity = ComplexityLevel.SIMPLE
        else:
            complexity = ComplexityLevel.TRIVIAL
        # System One override when it was confident enough to answer.
        s1_complexity = s1.get("complexity")
        if s1_complexity:
            try:
                complexity = ComplexityLevel(s1_complexity)
            except ValueError:
                logger.debug("System One returned unknown complexity '%s'; keeping heuristic.", s1_complexity)

        # 3. Risk Scoring (0.0 - 1.0)
        risk_signals = ["delete", "rm ", "drop ", "overwrite", "root", "sudo", "bypass", "credentials", "token", "kill"]
        risk_hits = sum(1 for r in risk_signals if r in g_lower)
        risk_score = s1.get("risk_score") if "risk_score" in s1 else min(1.0, 0.1 + (risk_hits * 0.25))

        # 4. Mode and Model Tier Selection
        if has_frontier or "never stop" in g_lower:
            suggested_mode = OperatingMode.AUTONOMOUS
            recommended_model_tier = ModelTier.REASONING_FRONTIER
            recommended_topology = TopologyType.HIERARCHICAL
            estimated_turns = 80
            reasoning_budget = 16384
            reflection_frequency = 1
            compaction_threshold = 60000
        elif domain == "autonomous_company":
            suggested_mode = OperatingMode.COMPANY
            recommended_model_tier = ModelTier.REASONING_FRONTIER
            recommended_topology = TopologyType.HIERARCHICAL
            estimated_turns = 60
            reasoning_budget = 12288
            reflection_frequency = 2
            compaction_threshold = 55000
        elif domain == "coding":
            suggested_mode = OperatingMode.CODING
            recommended_model_tier = ModelTier.REASONING_FRONTIER if complexity in [ComplexityLevel.COMPLEX, ComplexityLevel.RESEARCH_FRONTIER] else ModelTier.STANDARD
            recommended_topology = TopologyType.HIERARCHICAL if complexity != ComplexityLevel.SIMPLE else TopologyType.SOLO
            estimated_turns = 40
            reasoning_budget = 8192
            reflection_frequency = 2
            compaction_threshold = 50000
        elif domain == "deep_research":
            suggested_mode = OperatingMode.RESEARCH
            recommended_model_tier = ModelTier.STANDARD
            recommended_topology = TopologyType.PARALLEL_MESH
            estimated_turns = 35
            reasoning_budget = 6144
            reflection_frequency = 3
            compaction_threshold = 45000
        elif complexity in [ComplexityLevel.COMPLEX, ComplexityLevel.RESEARCH_FRONTIER]:
            suggested_mode = OperatingMode.SWARM
            recommended_model_tier = ModelTier.MULTI_MODEL_ENSEMBLE if risk_score > 0.4 else ModelTier.REASONING_FRONTIER
            recommended_topology = TopologyType.DEBATE_COUNCIL
            estimated_turns = 50
            reasoning_budget = 8192
            reflection_frequency = 2
            compaction_threshold = 50000
        elif complexity == ComplexityLevel.MODERATE:
            suggested_mode = OperatingMode.PLAN
            recommended_model_tier = ModelTier.STANDARD
            recommended_topology = TopologyType.SOLO
            estimated_turns = 20
            reasoning_budget = 4096
            reflection_frequency = 3
            compaction_threshold = 40000
        else:
            suggested_mode = OperatingMode.DIRECT
            recommended_model_tier = ModelTier.FAST_LOCAL
            recommended_topology = TopologyType.SOLO
            estimated_turns = 10
            reasoning_budget = 1024
            reflection_frequency = 5
            compaction_threshold = 30000

        # 5. Tool Discovery and Capability Mapping
        capabilities: list[str] = ["core_reasoning"]
        tool_whitelist: list[str] = ["filesystem_editor", "ripgrep_search"]

        if domain in ["coding", "architecture", "autonomous_company"] or has_frontier:
            capabilities.extend(["code_synthesis", "ast_refactor", "test_verification"])
            tool_whitelist.extend(["code_executor", "git_worktree", "pytest_runner"])
        
        if domain in ["deep_research", "autonomous_company"] or has_frontier:
            capabilities.extend(["web_retrieval", "document_synthesis"])
            tool_whitelist.extend(["web_research", "browser_controller"])

        if complexity in [ComplexityLevel.COMPLEX, ComplexityLevel.RESEARCH_FRONTIER] or has_frontier:
            capabilities.extend(["epistemic_belief_tracking", "closed_loop_rsi", "genetic_variation"])
            tool_whitelist.extend(["epistemic_belief_graph", "rsi_engine", "avo_optimizer", "trajectory_replayer"])

        # Intent summary
        intent = f"Execute {domain.replace('_', ' ')} with {complexity.value} depth under {suggested_mode.value} paradigm"

        mitigations = []
        if risk_score > 0.3:
            mitigations.append("Enforce pre-commit test assertions and workspace change sandboxing.")
        if complexity == ComplexityLevel.RESEARCH_FRONTIER:
            mitigations.append("Activate Epistemic Belief Engine to verify critical assumptions before destructive actions.")

        analysis = GoalAnalysis(
            raw_goal=goal,
            intent=intent,
            domain=domain,
            complexity=complexity,
            risk_score=risk_score,
            estimated_turns=estimated_turns,
            suggested_mode=suggested_mode,
            recommended_model_tier=recommended_model_tier,
            recommended_topology=recommended_topology,
            required_capabilities=capabilities,
            tool_whitelist=list(dict.fromkeys(tool_whitelist)),
            reasoning_budget_tokens=reasoning_budget,
            reflection_frequency=reflection_frequency,
            context_compaction_threshold=compaction_threshold,
            ambiguities=["Implicit dependencies on external credentials" if risk_score > 0.4 else "None identified"],
            mitigation_strategies=mitigations,
        )

        self._analysis_history.append(analysis)
        return analysis

    def synthesize_profile(self, analysis: GoalAnalysis, project_id: str | None = None) -> SelfConfigProfile:
        """Derive complete runnable SelfConfigProfile from an intent analysis."""
        pid = project_id or self.project_id
        
        # Primary model mapping based on tier
        model_map = {
            ModelTier.FAST_LOCAL: ("claude-3-5-haiku", "gpt-4o-mini"),
            ModelTier.STANDARD: ("claude-3-5-sonnet", "gpt-4o"),
            ModelTier.REASONING_FRONTIER: ("claude-3-7-sonnet-thinking", "o3-mini"),
            ModelTier.MULTI_MODEL_ENSEMBLE: ("claude-3-7-sonnet-thinking", "deepseek-r1"),
        }
        primary, fallback = model_map.get(analysis.recommended_model_tier, ("claude-3-7-sonnet-thinking", "gpt-4o"))

        swarm_roles = []
        if analysis.recommended_topology == TopologyType.HIERARCHICAL:
            swarm_roles = ["executive_commander", "specialist_engineer", "independent_critic"]
        elif analysis.recommended_topology == TopologyType.DEBATE_COUNCIL:
            swarm_roles = ["proponent_agent", "opponent_critic", "arbiter_judge"]
        elif analysis.recommended_topology == TopologyType.PARALLEL_MESH:
            swarm_roles = ["worker_alpha", "worker_beta", "aggregator"]
        else:
            swarm_roles = ["autonomous_solo_worker"]

        thought_depth = "deep" if analysis.complexity in [ComplexityLevel.COMPLEX, ComplexityLevel.RESEARCH_FRONTIER] else "standard"

        profile = SelfConfigProfile(
            project_id=pid,
            goal=analysis.raw_goal,
            operating_mode=analysis.suggested_mode,
            model_tier=analysis.recommended_model_tier,
            primary_model=primary,
            fallback_model=fallback,
            active_tools=list(analysis.tool_whitelist),
            reasoning_budget_tokens=analysis.reasoning_budget_tokens,
            thought_depth=thought_depth,
            max_turns=analysis.estimated_turns,
            context_compaction_threshold=analysis.context_compaction_threshold,
            loop_detection_limit=3,
            topology=analysis.recommended_topology,
            swarm_roles=swarm_roles,
            autonomous_pivots_enabled=True,
            guardrails={
                "max_file_deletions": 1 if analysis.risk_score > 0.5 else 3,
                "require_test_verification": True,
                "allow_network_search": "web_research" in analysis.tool_whitelist,
                "risk_score": analysis.risk_score,
            },
        )

        self._active_profile = profile
        return profile

    def tune_profile(self, updates: RuntimeTuningUpdate) -> SelfConfigProfile:
        """Apply dynamic in-flight parameter tuning to the active profile."""
        if not self._active_profile:
            self._active_profile = SelfConfigProfile(project_id=self.project_id)

        p = self._active_profile
        if updates.reasoning_budget_tokens is not None:
            p.reasoning_budget_tokens = updates.reasoning_budget_tokens
        if updates.context_compaction_threshold is not None:
            p.context_compaction_threshold = updates.context_compaction_threshold
        if updates.loop_detection_limit is not None:
            p.loop_detection_limit = updates.loop_detection_limit
        if updates.primary_model:
            p.primary_model = updates.primary_model
        if updates.operating_mode:
            try:
                p.operating_mode = OperatingMode(updates.operating_mode)
            except ValueError:
                pass
        if updates.thought_depth:
            p.thought_depth = updates.thought_depth
        if updates.extra_tools:
            for t in updates.extra_tools:
                if t not in p.active_tools:
                    p.active_tools.append(t)
        if updates.disabled_tools:
            p.active_tools = [t for t in p.active_tools if t not in updates.disabled_tools]

        p.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return p

    def get_profile(self) -> SelfConfigProfile:
        """Return the current active self-configuration profile."""
        if not self._active_profile:
            self._active_profile = SelfConfigProfile(project_id=self.project_id)
        return self._active_profile

    def get_status(self) -> dict[str, Any]:
        """Telemetry status of the self-configuration engine."""
        prof = self.get_profile()
        return {
            "project_id": self.project_id,
            "active_profile": prof.to_dict(),
            "analyses_performed": len(self._analysis_history),
            "last_analysis": self._analysis_history[-1].to_dict() if self._analysis_history else None,
        }


_ENGINES: dict[str, SelfConfigurationEngine] = {}


def get_self_config_engine(project_id: str = "default") -> SelfConfigurationEngine:
    """Project-scoped singleton accessor for SelfConfigurationEngine."""
    if project_id not in _ENGINES:
        _ENGINES[project_id] = SelfConfigurationEngine(project_id)
    return _ENGINES[project_id]
