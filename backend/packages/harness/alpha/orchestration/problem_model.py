"""NVIDIA AVO Problem Model Compiler (Section 6 Specification).

Compiles human objectives into structured 12-factor task representations:
Never treat the raw user prompt as the complete state. Structure objectives,
constraints, entities, unknowns, risks, and empirical verification methods
before launching autonomous execution.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ProblemModel:
    """Structured 12-factor problem model anchoring autonomous execution."""

    objective: str
    desired_outcome: str
    task_type: str  # "coding", "research", "architecture", "optimization", "governance", "general"
    domain: str  # "backend", "frontend", "systems", "security", "data", "cross_domain"
    entities: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    known_facts: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    success_metrics: list[str] = field(default_factory=list)
    verification_methods: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        """Format as clean structured markdown for prompt context injection."""
        lines = [
            "# AVO Problem Model",
            f"**Objective**: {self.objective}",
            f"**Desired Outcome**: {self.desired_outcome}",
            f"**Task Type**: `{self.task_type}` | **Domain**: `{self.domain}`",
            "",
            "## Entities & Scope",
        ]
        for e in self.entities or ["None specified"]:
            lines.append(f"- {e}")

        lines.extend(["", "## Constraints & Assumptions"])
        for c in self.constraints or ["No explicit constraints"]:
            lines.append(f"- [Constraint] {c}")
        for a in self.assumptions:
            lines.append(f"- [Assumption] {a}")

        lines.extend(["", "## Risks & Identified Unknowns"])
        for r in self.risks or ["Low operational risk"]:
            lines.append(f"- [Risk] {r}")
        for u in self.unknowns:
            lines.append(f"- [Unknown] {u}")

        lines.extend(["", "## Success Metrics & Verification Methods"])
        for m in self.success_metrics or ["Completion of task requirements"]:
            lines.append(f"- [Metric] {m}")
        for v in self.verification_methods or ["Empirical test run"]:
            lines.append(f"- [Verification] {v}")

        lines.extend(["", "## Recommended Tooling"])
        lines.append(f"- Tools: {', '.join(self.required_tools or ['standard'])}")

        return "\n".join(lines)


class ProblemModelCompiler:
    """Analyzes raw goals and compiles structured ProblemModels."""

    @classmethod
    def compile(
        cls, goal: str, context: dict[str, Any] | None = None
    ) -> ProblemModel:
        text = goal.strip()
        text_lower = text.lower()

        # 1. Determine task type
        if any(w in text_lower for w in ["code", "bug", "fix", "refactor", "test", "implement", "class", "function", "api"]):
            task_type = "coding"
        elif any(w in text_lower for w in ["research", "investigate", "survey", "find", "search", "papers", "landscape"]):
            task_type = "research"
        elif any(w in text_lower for w in ["optimize", "performance", "throughput", "speedup", "latency", "benchmark"]):
            task_type = "optimization"
        elif any(w in text_lower for w in ["audit", "security", "invariant", "compliance", "policy", "review"]):
            task_type = "governance"
        elif any(w in text_lower for w in ["architect", "design", "system", "structure", "dag", "plan"]):
            task_type = "architecture"
        else:
            task_type = "general"

        # 2. Determine domain
        if any(w in text_lower for w in ["frontend", "ui", "react", "next.js", "css", "html", "tailwind"]):
            domain = "frontend"
        elif any(w in text_lower for w in ["backend", "fastapi", "python", "database", "sqlite", "server", "gateway"]):
            domain = "backend"
        elif any(w in text_lower for w in ["gpu", "cuda", "kernel", "memory", "driver", "system"]):
            domain = "systems"
        elif any(w in text_lower for w in ["auth", "token", "permission", "sandbox", "crypto"]):
            domain = "security"
        else:
            domain = "cross_domain"

        # 3. Extract entities (file extensions, named symbols, tokens with dots/slashes)
        entities: list[str] = []
        words = text.split()
        for w in words:
            clean = w.strip(".,;:()[]{}\"'`")
            if "." in clean or "/" in clean or "\\" in clean or "_" in clean or clean.isupper() and len(clean) > 2:
                if clean not in entities:
                    entities.append(clean)

        # 4. Formulate constraints & assumptions
        constraints: list[str] = [
            "Maintain backward compatibility with existing APIs",
            "Zero false completion (empirical verification mandatory)",
            "Non-destructive mutation with rollback safeguard",
        ]
        if "fast" in text_lower or "quick" in text_lower:
            constraints.append("Minimize latency and tool invocation overhead")

        assumptions: list[str] = [
            "Local codebase environment is accessible and dependency-ready",
            "Test suite is runnable and deterministic",
        ]

        # 5. Formulate risks
        risks: list[str] = []
        if task_type in ("coding", "optimization"):
            risks.append("Regression in existing passing test suites")
            risks.append("Unintended side-effects on concurrent modules")
        if task_type == "research":
            risks.append("Hallucination or unsourced claims")
        if not risks:
            risks.append("Unclear edge cases in user requirements")

        # 6. Success metrics & verification methods
        success_metrics = [
            "100% test pass rate on modified modules",
            "Deterministic build and typecheck pass without warnings",
        ]
        if task_type == "optimization":
            success_metrics.append("Measurable positive delta in throughput or execution duration")

        verification_methods = [
            "Run unit and integration test suite via auto_test_and_repair",
            "Inspect git diff and AST symbol integrity",
        ]
        if domain == "frontend":
            verification_methods.append("Run visual_verify_artifact on rendered UI components")

        # 7. Required tools
        tool_map = {
            "coding": ["generate_repo_map", "auto_test_and_repair", "manage_code_checkpoint", "read_file", "str_replace"],
            "optimization": ["run_nvidia_avo_step", "auto_test_and_repair", "manage_code_checkpoint"],
            "research": ["web_search", "web_fetch", "compile_five_pass_search"],
            "governance": ["consult_plan_gap_analysis", "review_plan_invariant_gate", "astra_security_manage"],
            "architecture": ["cognitive_plan", "workflow_dag_manage", "manage_mission_hierarchy"],
            "general": ["read_file", "ask_clarification"],
        }
        required_tools = tool_map.get(task_type, ["read_file"])

        return ProblemModel(
            objective=text,
            desired_outcome=f"Fully implemented, verified, and regression-free resolution of: {text[:80]}",
            task_type=task_type,
            domain=domain,
            entities=entities[:10],
            constraints=constraints,
            assumptions=assumptions,
            known_facts=[],
            unknowns=[],
            dependencies=[],
            risks=risks,
            success_metrics=success_metrics,
            verification_methods=verification_methods,
            required_tools=required_tools,
        )
