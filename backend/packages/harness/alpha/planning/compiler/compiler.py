from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fallbacks import RecoveryFallbackTree
from .strategy import StrategyCandidate, generate_strategy_candidates
from .waves import ExecutionWave, partition_execution_waves, validate_task_dag


def infer_task_dag(goal: str) -> dict[str, list[str]]:
    """Infer a domain-appropriate default DAG when the caller supplies none.

    Keeps the historical coding DAG as the fallback so existing callers are
    unaffected, but returns research/browser/data/deploy shapes for matching
    goals instead of forcing every mission through a patch pipeline.
    """
    text = goal.lower()
    if re.search(r"\b(research|investigat|survey|compare|literature|landscape)\b", text):
        return {
            "define_questions": [],
            "collect_sources": ["define_questions"],
            "verify_sources": ["collect_sources"],
            "synthesize_report": ["verify_sources"],
        }
    if re.search(r"\b(browser|login|scrap|form|crawl|web flow)\b", text):
        return {
            "open_context": [],
            "navigate_observe": ["open_context"],
            "extract_verify": ["navigate_observe"],
            "close_context": ["extract_verify"],
        }
    if re.search(r"\b(csv|dataframe|\bsql\b|etl|pipeline|dataset)\b", text):
        return {
            "load_profile": [],
            "transform_validate": ["load_profile"],
            "analyze_verify": ["transform_validate"],
            "publish_artifact": ["analyze_verify"],
        }
    if re.search(r"\b(deploy|release|publish|rollout)\b", text):
        return {
            "build_verify": [],
            "stage_smoke": ["build_verify"],
            "prod_rollout_gated": ["stage_smoke"],
            "post_deploy_verify": ["prod_rollout_gated"],
        }
    return {
        "recon_environment": [],
        "synthesize_patch": ["recon_environment"],
        "run_unit_tests": ["synthesize_patch"],
        "verify_acceptance": ["run_unit_tests"],
    }


def default_proof_obligations(risk_tier: str) -> list[str]:
    """Verifiable proof obligations expressed as checkable handles."""
    obligations = [
        "all_pre_existing_tests_must_pass [tests_passed:<suite>]",
        "zero_unhandled_syntax_errors",
        "blast_radius_confined_to_workspace [file:<workspace> scoped]",
    ]
    if risk_tier not in ("R0", "R1"):
        obligations.extend(
            [
                "security_scan_clean [scan:skill_scan]",
                "rollback_plan_present [artifact:rollback_plan]",
            ]
        )
    return obligations


def plan_hash_for(goal: str, task_dag: dict[str, list[str]], strategy_name: str) -> str:
    digest = hashlib.sha256(json.dumps({"goal": goal, "dag": task_dag, "strategy": strategy_name}, sort_keys=True).encode()).hexdigest()
    return digest[:16]


@dataclass
class ExecutionPlanIR:
    """Immutable compiled execution intermediate representation emitted by P20."""

    plan_id: str
    goal: str
    risk_tier: str
    chosen_strategy: StrategyCandidate
    candidate_strategies: list[StrategyCandidate]
    execution_waves: list[ExecutionWave]
    recovery_tree: RecoveryFallbackTree
    proof_obligations: list[str] = field(default_factory=list)
    compiled_at: float = field(default_factory=time.time)
    plan_hash: str = ""
    task_dag: dict[str, list[str]] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    hyperplan_status: str = "not_reviewed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "risk_tier": self.risk_tier,
            "chosen_strategy": self.chosen_strategy.to_dict(),
            "candidate_strategies": [s.to_dict() for s in self.candidate_strategies],
            "execution_waves": [w.to_dict() for w in self.execution_waves],
            "recovery_tree": self.recovery_tree.to_dict(),
            "proof_obligations": self.proof_obligations,
            "compiled_at": self.compiled_at,
            "plan_hash": self.plan_hash,
            "task_dag": self.task_dag,
            "validation_errors": self.validation_errors,
            "hyperplan_status": self.hyperplan_status,
        }

    def to_evidence_dict(self) -> dict[str, Any]:
        """Compact evidence receipt for run_events / delivery ledger."""
        return {
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "goal": self.goal,
            "risk_tier": self.risk_tier,
            "chosen_strategy": self.chosen_strategy.name,
            "waves": len(self.execution_waves),
            "proof_obligations": self.proof_obligations,
            "hyperplan_status": self.hyperplan_status,
        }

    def save_artifact(self, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return dest


class CognitiveCompiler:
    """
    Hermes P0–P21 Pre-Execution Cognitive Compiler.
    Compiles a mission into an immutable, structured ExecutionPlanIR
    BEFORE committing any real tool calls or shell commands.
    """

    def __init__(self) -> None:
        pass

    def compile(
        self,
        goal: str,
        task_dag: dict[str, list[str]] | None = None,
        risk_tier: str = "R1",
        proof_obligations: list[str] | None = None,
        *,
        strict_validation: bool = True,
    ) -> ExecutionPlanIR:
        if not goal or not goal.strip():
            raise ValueError("goal must be a non-empty string")
        plan_id = f"plan_ir_{uuid.uuid4().hex[:10]}"

        # P8 & P9: Generate & evaluate Strategy Candidates (A, B, C)
        candidates = generate_strategy_candidates(goal=goal, risk_tier=risk_tier)
        # Select best scoring candidate
        chosen = max(candidates, key=lambda c: c.score())

        # P10, P11, P12: Goal Decomposition, Dependencies, and Parallel Execution Waves
        effective_dag = task_dag or infer_task_dag(goal)
        is_valid, errors = validate_task_dag(effective_dag)
        if not is_valid and strict_validation:
            raise ValueError(f"Invalid task_dag: {'; '.join(errors)}")
        waves = partition_execution_waves(effective_dag)

        # P19: Pre-computed Recovery Fallbacks
        recovery_tree = RecoveryFallbackTree(
            primary_strategy=f"Execute {chosen.name} via planned execution waves.",
            fallback_a="Switch to AST Isolated Worktree sandbox and alternate tool driver.",
            fallback_b="Gracefully degrade scope: generate non-breaking minimal shim and request verification.",
            escalation_threshold=2,
        )

        # Proof obligations as verifiable handles (file:/tests_passed:/artifact:)
        obligations = proof_obligations or default_proof_obligations(risk_tier)
        plan_hash = plan_hash_for(goal, effective_dag, chosen.name)

        return ExecutionPlanIR(
            plan_id=plan_id,
            goal=goal,
            risk_tier=risk_tier,
            chosen_strategy=chosen,
            candidate_strategies=candidates,
            execution_waves=waves,
            recovery_tree=recovery_tree,
            proof_obligations=obligations,
            plan_hash=plan_hash,
            task_dag=dict(effective_dag),
            validation_errors=[] if is_valid else list(errors),
        )

    def compile_with_gate(
        self,
        goal: str,
        plan_text: str,
        task_dag: dict[str, list[str]] | None = None,
        risk_tier: str = "R1",
        proof_obligations: list[str] | None = None,
        *,
        enforce: bool = True,
    ) -> tuple[ExecutionPlanIR, Any]:
        """Compile then run the Hyperplan hostile gate.

        Returns (ir, report). When enforce=True and the gate BLOCKs, raises
        RuntimeError instead of returning an executable plan — the caller must
        repair the plan and recompile. The plan hash pins execution (C3).
        """
        from alpha.planning.hyperplan import HyperplanPipeline

        report = HyperplanPipeline().review_plan(goal, plan_text)
        ir = self.compile(goal=goal, task_dag=task_dag, risk_tier=risk_tier, proof_obligations=proof_obligations)
        ir.hyperplan_status = report.overall_status
        if enforce and report.is_blocked:
            raise RuntimeError(f"Plan gate BLOCKED ({report.plan_hash}): {report.gatekeeper_summary}")
        return ir, report
