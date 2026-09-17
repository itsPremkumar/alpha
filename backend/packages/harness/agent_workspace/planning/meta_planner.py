"""Cognitive Meta-Planner: The 8-Dimensional Strategic Decision Engine.

Intercepts user prompts and determines:
1. Execution Paradigm (Deep Research vs Deep Think vs Swarm vs MoA vs Bot Profile vs Subagents vs Direct)
2. Swarm Strategy Mode (Map-Reduce, Debate, Ensemble, Coding Worktree, Hierarchical)
3. Reasoning Tier (Standard, Extended Reflection, Adversarial Audit)
4. Workforce Allocation (Permanent Hermes Bots vs Ephemeral Subagents vs Hybrid)
5. Compute & Model Routing (Frontier, Fast, Local, Verifier)
6. Workspace Isolation Backend (Shared, Git Worktree, Process Sandbox)
7. Risk Tier (R1 Safe Read-only to R6 High-risk Destructive)
8. Proof Obligations & Quality Gate Verification
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from agent_workspace.swarm.estimator import SwarmBenefitEstimator
from agent_workspace.swarm.models import SwarmMode


class ExecutionParadigm(StrEnum):
    DEEP_RESEARCH = "deep_research"
    DEEP_THINK = "deep_think"
    SWARM = "swarm"
    MOA = "moa"
    BOT_PROFILE = "bot_profile"
    SUBAGENT = "subagent"
    DIRECT_AGENT = "direct_agent"


@dataclass
class MetaPlanDecision:
    paradigm: ExecutionParadigm
    swarm_mode: SwarmMode | None
    reasoning_tier: str
    workforce_type: str  # 'permanent_bot', 'ephemeral_subagent', 'hybrid', 'single_agent'
    assigned_specialists: list[str] = field(default_factory=list)
    model_tier: str = "fast"  # 'frontier', 'fast', 'local', 'verifier'
    workspace_isolation: str = "shared"  # 'shared', 'git_worktree', 'sandbox'
    risk_tier: str = "R2"
    rationale: str = ""
    estimated_speedup: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["paradigm"] = self.paradigm.value
        d["swarm_mode"] = self.swarm_mode.value if self.swarm_mode else None
        return d


@dataclass
class MetaPlanTask:
    task_id: str
    wave: int
    objective: str
    assignee: str
    dependencies: list[str] = field(default_factory=list)
    worktree_path: str | None = None
    expected_artifact: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MetaPlan:
    plan_id: str
    prompt: str
    decision: MetaPlanDecision
    execution_waves: list[MetaPlanTask] = field(default_factory=list)
    proof_obligations: list[str] = field(default_factory=list)
    verification_command: str | None = None
    markdown_report: str = ""
    status: str = "ready"  # ready, executing, completed, blocked

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "prompt": self.prompt,
            "decision": self.decision.to_dict(),
            "execution_waves": [t.to_dict() for t in self.execution_waves],
            "proof_obligations": list(self.proof_obligations),
            "verification_command": self.verification_command,
            "markdown_report": self.markdown_report,
            "status": self.status,
        }


class CognitiveMetaPlanner:
    """Evaluates raw user prompts across all 8 strategic dimensions and compiles an actionable MetaPlan."""

    # Heuristic intent detectors
    RESEARCH_WORDS = ("research", "survey", "literature", "compare market", "landscape", "sources", "citations", "papers")
    THINK_WORDS = ("prove", "mathematical", "theorem", "complex logic", "formal reasoning", "causal", "puzzle", "algorithm design")
    MOA_WORDS = ("diverse perspectives", "cross-model", "brainstorm consensus", "multi-llm", "committee review")
    CODE_WORDS = ("build", "implement", "refactor", "bug", "endpoint", "architecture", "frontend", "backend", "full stack", "pr")
    RISK_HIGH_WORDS = ("drop table", "rm -rf", "delete database", "production deploy", "format disk", "wipe", "force push")

    @classmethod
    def evaluate_and_plan(
        cls,
        prompt: str,
        items: list[str] | None = None,
        max_concurrency: int = 8,
    ) -> MetaPlan:
        """Main entry point: analyzes prompt and produces the complete MetaPlan."""
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        prompt_lower = prompt.lower()

        # Dimension 7: Risk Tier
        if any(w in prompt_lower for w in cls.RISK_HIGH_WORDS):
            risk_tier = "R5"
        elif any(w in prompt_lower for w in ("deploy", "release", "modify production", "write api")):
            risk_tier = "R4"
        elif any(w in prompt_lower for w in ("refactor", "build", "implement", "create")):
            risk_tier = "R3"
        elif any(w in prompt_lower for w in ("test", "audit", "lint", "inspect")):
            risk_tier = "R2"
        else:
            risk_tier = "R1"

        # Dimension 1 & 2: Execution Paradigm & Swarm Strategy
        swarm_decision = SwarmBenefitEstimator.estimate(prompt, items=items, max_concurrency_limit=max_concurrency)

        if swarm_decision.should_swarm:
            paradigm = ExecutionParadigm.SWARM
            swarm_mode = swarm_decision.mode
            reasoning_tier = "standard"
            workforce_type = "hybrid"
            model_tier = "frontier" if swarm_mode in (SwarmMode.DEBATE, SwarmMode.CODING_WORKTREE) else "fast"
            workspace_isolation = "git_worktree" if swarm_mode == SwarmMode.CODING_WORKTREE else "shared"
            assigned_specialists = ["architect", "coder", "tester"] if "coding" in swarm_mode.value else ["researcher", "analyst"]
            rationale = swarm_decision.reason
            speedup = swarm_decision.estimated_speedup

        elif any(w in prompt_lower for w in cls.RESEARCH_WORDS) and ("deep" in prompt_lower or "investigate" in prompt_lower):
            paradigm = ExecutionParadigm.DEEP_RESEARCH
            swarm_mode = None
            reasoning_tier = "extended_reflection"
            workforce_type = "permanent_bot"
            assigned_specialists = ["researcher"]
            model_tier = "frontier"
            workspace_isolation = "shared"
            rationale = "In-depth multi-source discovery inquiry detected; routing to Deep Research with citation verification."
            speedup = 1.0

        elif any(w in prompt_lower for w in cls.THINK_WORDS):
            paradigm = ExecutionParadigm.DEEP_THINK
            swarm_mode = None
            reasoning_tier = "extended_reflection"
            workforce_type = "single_agent"
            assigned_specialists = []
            model_tier = "frontier"
            workspace_isolation = "shared"
            rationale = "High-complexity deductive or algorithmic prompt; routing to Deep Think extended reasoning."
            speedup = 1.0

        elif any(w in prompt_lower for w in cls.MOA_WORDS):
            paradigm = ExecutionParadigm.MOA
            swarm_mode = None
            reasoning_tier = "adversarial_audit"
            workforce_type = "hybrid"
            assigned_specialists = ["architect", "reviewer"]
            model_tier = "frontier"
            workspace_isolation = "shared"
            rationale = "Diverse multi-model consensus requested; routing to Mixture of Agents (MoA)."
            speedup = 1.0

        elif any(w in prompt_lower for w in cls.CODE_WORDS):
            paradigm = ExecutionParadigm.BOT_PROFILE
            swarm_mode = None
            reasoning_tier = "standard"
            workforce_type = "permanent_bot"
            assigned_specialists = ["coder", "tester"]
            model_tier = "fast"
            workspace_isolation = "git_worktree" if "refactor" in prompt_lower else "shared"
            rationale = "Software engineering task matching specialized developer bots (@coder, @tester)."
            speedup = 1.0

        else:
            paradigm = ExecutionParadigm.DIRECT_AGENT
            swarm_mode = None
            reasoning_tier = "standard"
            workforce_type = "single_agent"
            assigned_specialists = []
            model_tier = "fast"
            workspace_isolation = "shared"
            rationale = "Focused direct objective; routing to direct agent to eliminate coordination overhead."
            speedup = 1.0

        decision = MetaPlanDecision(
            paradigm=paradigm,
            swarm_mode=swarm_mode,
            reasoning_tier=reasoning_tier,
            workforce_type=workforce_type,
            assigned_specialists=assigned_specialists,
            model_tier=model_tier,
            workspace_isolation=workspace_isolation,
            risk_tier=risk_tier,
            rationale=rationale,
            estimated_speedup=speedup,
        )

        # Decompose Waves
        waves = cls._compile_execution_waves(prompt, decision, items)

        # Dimension 8: Proof Obligations
        proof_obligations = cls._compile_proof_obligations(prompt, decision)
        verification_cmd = "uv run pytest" if "coding" in str(swarm_mode) or paradigm == ExecutionParadigm.BOT_PROFILE else None

        # Compile Markdown Strategy Report
        report = cls._format_markdown_report(plan_id, prompt, decision, waves, proof_obligations)

        return MetaPlan(
            plan_id=plan_id,
            prompt=prompt,
            decision=decision,
            execution_waves=waves,
            proof_obligations=proof_obligations,
            verification_command=verification_cmd,
            markdown_report=report,
            status="blocked" if risk_tier in ("R5", "R6") else "ready",
        )

    @classmethod
    def _compile_execution_waves(
        cls,
        prompt: str,
        decision: MetaPlanDecision,
        items: list[str] | None,
    ) -> list[MetaPlanTask]:
        tasks: list[MetaPlanTask] = []

        if decision.paradigm == ExecutionParadigm.SWARM and decision.swarm_mode == SwarmMode.CODING_WORKTREE:
            tasks.extend(
                [
                    MetaPlanTask(
                        task_id="task-arch-spec",
                        wave=1,
                        objective=f"Architectural interface contracts: {prompt}",
                        assignee="architect",
                    ),
                    MetaPlanTask(
                        task_id="task-backend-impl",
                        wave=2,
                        objective=f"Backend logic implementation: {prompt}",
                        assignee="coder",
                        dependencies=["task-arch-spec"],
                        worktree_path=".worktrees/backend",
                        expected_artifact="backend.patch",
                    ),
                    MetaPlanTask(
                        task_id="task-frontend-impl",
                        wave=2,
                        objective=f"Frontend components: {prompt}",
                        assignee="frontend-specialist",
                        dependencies=["task-arch-spec"],
                        worktree_path=".worktrees/frontend",
                        expected_artifact="frontend.patch",
                    ),
                    MetaPlanTask(
                        task_id="task-verify-qa",
                        wave=3,
                        objective=f"Run full integration tests: {prompt}",
                        assignee="tester",
                        dependencies=["task-backend-impl", "task-frontend-impl"],
                        expected_artifact="test_report.md",
                    ),
                ]
            )
        elif decision.paradigm == ExecutionParadigm.SWARM and items and len(items) > 0:
            map_ids = []
            for idx, itm in enumerate(items, 1):
                tid = f"task-map-{idx}"
                map_ids.append(tid)
                tasks.append(
                    MetaPlanTask(
                        task_id=tid,
                        wave=1,
                        objective=f"Process target item ({idx}/{len(items)}): {itm}",
                        assignee="ephemeral-worker",
                    )
                )
            tasks.append(
                MetaPlanTask(
                    task_id="task-reduce",
                    wave=2,
                    objective=f"Synthesize consolidated report for: {prompt}",
                    assignee="architect",
                    dependencies=map_ids,
                    expected_artifact="consolidated_summary.md",
                )
            )
        else:
            # Single / Direct task
            tasks.append(
                MetaPlanTask(
                    task_id="task-core-01",
                    wave=1,
                    objective=prompt,
                    assignee=decision.assigned_specialists[0] if decision.assigned_specialists else "lead-agent",
                )
            )

        return tasks

    @classmethod
    def _compile_proof_obligations(cls, prompt: str, decision: MetaPlanDecision) -> list[str]:
        criteria = [f"Complete primary objective: {prompt[:80]}..."]
        if decision.workspace_isolation == "git_worktree":
            criteria.extend(["Zero git merge conflicts between branches", "All automated unit tests pass"])
        if decision.paradigm == ExecutionParadigm.DEEP_RESEARCH:
            criteria.extend(["All statements backed by verified citations", "Zero unresolved contradictions"])
        if decision.risk_tier in ("R3", "R4"):
            criteria.append("Clean diff audit with zero secret leaks")
        return criteria

    @classmethod
    def _format_markdown_report(
        cls,
        plan_id: str,
        prompt: str,
        decision: MetaPlanDecision,
        waves: list[MetaPlanTask],
        proof_obligations: list[str],
    ) -> str:
        lines = [
            f"# 🧠 Cognitive Plan: {prompt[:60]}",
            "",
            "## 1. 8-Dimensional Decision Matrix",
            f"- **1. Execution Paradigm**: `{decision.paradigm.value}`",
            f"- **2. Swarm Strategy**: `{decision.swarm_mode.value if decision.swarm_mode else 'N/A'}`",
            f"- **3. Reasoning Tier**: `{decision.reasoning_tier}`",
            f"- **4. Workforce Allocation**: `{decision.workforce_type}` (Specialists: {', '.join(decision.assigned_specialists) or 'None'})",
            f"- **5. Model Tier**: `{decision.model_tier}`",
            f"- **6. Workspace Isolation**: `{decision.workspace_isolation}`",
            f"- **7. Risk Tier**: `{decision.risk_tier}` ({'Auto-Authorized' if decision.risk_tier not in ('R5', 'R6') else 'Human Gate Required'})",
            f"- **8. Speedup Factor**: `{decision.estimated_speedup}x`",
            f"- **Strategic Rationale**: {decision.rationale}",
            "",
            "## 2. Work Breakdown & Execution Waves",
        ]

        # Group tasks by wave
        max_wave = max((t.wave for t in waves), default=1)
        for w in range(1, max_wave + 1):
            wave_tasks = [t for t in waves if t.wave == w]
            lines.append(f"### Wave {w} ({'Parallel' if len(wave_tasks) > 1 else 'Sequential'})")
            for t in wave_tasks:
                dep_str = f" (deps: {', '.join(t.dependencies)})" if t.dependencies else ""
                lines.append(f"- `[{t.task_id}]` @{t.assignee}: {t.objective}{dep_str}")

        lines.extend(["", "## 3. Proof Obligations (Quality Gate)"])
        for idx, po in enumerate(proof_obligations, 1):
            lines.append(f"{idx}. {po}")

        return "\n".join(lines)
