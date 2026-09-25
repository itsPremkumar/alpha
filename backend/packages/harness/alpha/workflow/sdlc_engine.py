"""Document-Gated Multi-Agent SDLC Engine.

Autonomous SDLC pipeline: Product Manager (PRD) -> Architect (UML) -> Project Manager (Tasks) -> Coder -> QA.
Enforces formal document quality gates at each transition with 100% autonomous execution (zero blocking human-in-the-loop).
"""

from __future__ import annotations

import ast
import json
import logging
import py_compile
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)


class SDLCStage(StrEnum):
    PRD = "prd"
    ARCHITECTURE = "architecture"
    PROJECT_PLAN = "project_plan"
    IMPLEMENTATION = "implementation"
    QA_VERIFICATION = "qa_verification"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DocumentArtifact:
    artifact_id: str
    stage: SDLCStage
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    is_valid: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "stage": self.stage.value,
            "title": self.title,
            "content": self.content,
            "metadata": self.metadata,
            "validation_errors": self.validation_errors,
            "is_valid": self.is_valid,
        }


# --- Autonomous Specialized Agents ---


class ProductManagerAgent:
    """Generates and refines Product Requirements Documents (PRD)."""

    def generate_prd(self, requirements: str, project_name: str = "AlphaProject") -> DocumentArtifact:
        content = f"""# Product Requirements Document (PRD): {project_name}

## 1. Executive Summary & Problem Statement
{requirements}

## 2. Target Users & Personas
- Software Engineers, System Architects, DevOps Specialists

## 3. Functional Requirements (MoSCoW Prioritization)
### Must Have
- Autonomous execution conforming to specified requirements
- Comprehensive unit and integration test coverage
- Strict zero-regression safety bounds

### Should Have
- Clean modular API interfaces and typed contracts
- Fast deterministic verification

### Could Have
- Detailed telemetry and trace visualization

### Won't Have
- Blocking human-in-the-loop manual confirmation gates

## 4. Acceptance Criteria & Success Metrics
1. 100% automated test pass rate with zero regressions.
2. Complete documentation and type safety compliance.
"""
        artifact = DocumentArtifact(
            artifact_id=f"prd-{uuid.uuid4().hex[:8]}",
            stage=SDLCStage.PRD,
            title=f"PRD: {project_name}",
            content=content,
            metadata={"project_name": project_name},
        )
        return self.validate_gate(artifact)

    def validate_gate(self, artifact: DocumentArtifact) -> DocumentArtifact:
        errors = []
        c = artifact.content
        if "## 1. Executive Summary" not in c and "Problem Statement" not in c:
            errors.append("PRD missing Executive Summary / Problem Statement section.")
        if "Functional Requirements" not in c:
            errors.append("PRD missing Functional Requirements section.")
        if "Acceptance Criteria" not in c:
            errors.append("PRD missing Acceptance Criteria.")
        artifact.validation_errors = errors
        artifact.is_valid = len(errors) == 0
        return artifact


class ArchitectAgent:
    """Generates system designs, UML diagrams, and API contracts."""

    def design_architecture(self, prd_artifact: DocumentArtifact) -> DocumentArtifact:
        content = """# Architectural System Design & UML Specifications

## 1. Architectural Style & Component Topology
- Microservices & Modular Package Layering
- Decoupled event bus and stateless REST Gateway
- Pure deterministic domain logic

## 2. Mermaid UML Class Diagram
```mermaid
classDiagram
    class SDLCController {
        +run_pipeline(requirements: str)
        +validate_gate(artifact: DocumentArtifact)
    }
    class DocumentArtifact {
        +str artifact_id
        +SDLCStage stage
        +bool is_valid
    }
    SDLCController --> DocumentArtifact : produces & gates
```

## 3. Data Schemas & API Contracts
- JSON-RPC 2.0 and REST OpenAPI 3.1 contracts
- Typed Pydantic data models for payload validation

## 4. Non-Functional Requirements & Security
- Zero-trust RBAC validation
"""
        artifact = DocumentArtifact(
            artifact_id=f"arch-{uuid.uuid4().hex[:8]}",
            stage=SDLCStage.ARCHITECTURE,
            title="System Architecture & UML Design",
            content=content,
            metadata={"upstream_prd_id": prd_artifact.artifact_id},
        )
        return self.validate_gate(artifact)

    def validate_gate(self, artifact: DocumentArtifact) -> DocumentArtifact:
        errors = []
        c = artifact.content
        if "Mermaid" not in c and "classDiagram" not in c and "Component" not in c:
            errors.append("Architecture artifact missing UML or Component diagram.")
        if "API Contracts" not in c and "Data Schemas" not in c:
            errors.append("Architecture artifact missing API Contracts or Data Schemas.")
        artifact.validation_errors = errors
        artifact.is_valid = len(errors) == 0
        return artifact


class ProjectManagerAgent:
    """Breaks down architecture and PRD into a Work Breakdown Structure (WBS) and Task DAG."""

    def create_project_plan(
        self,
        prd_artifact: DocumentArtifact,
        arch_artifact: DocumentArtifact,
    ) -> DocumentArtifact:
        tasks = [
            {"task_id": "T1", "name": "Core Domain Models", "dependencies": [], "definition_of_done": "Models with validation"},
            {"task_id": "T2", "name": "Service Logic & Engine", "dependencies": ["T1"], "definition_of_done": "Pure logic implementation"},
            {"task_id": "T3", "name": "API Handlers & Transport", "dependencies": ["T2"], "definition_of_done": "REST & JSON-RPC endpoints"},
            {"task_id": "T4", "name": "Automated Test Suite", "dependencies": ["T3"], "definition_of_done": "100% pytest test pass"},
        ]
        content = f"""# Work Breakdown Structure & Task DAG

## 1. Implementation Task Breakdown
{json.dumps(tasks, indent=2)}

## 2. Critical Path & Dependencies
- T1 -> T2 -> T3 -> T4

## 3. Risk Mitigation
- Automated canary regression gates verify each step.
"""
        artifact = DocumentArtifact(
            artifact_id=f"plan-{uuid.uuid4().hex[:8]}",
            stage=SDLCStage.PROJECT_PLAN,
            title="WBS & Task DAG",
            content=content,
            metadata={"tasks": tasks},
        )
        return self.validate_gate(artifact)

    def validate_gate(self, artifact: DocumentArtifact) -> DocumentArtifact:
        errors = []
        tasks = artifact.metadata.get("tasks", [])
        if not tasks or len(tasks) < 2:
            errors.append("Task plan must specify at least two decomposed implementation tasks.")
        artifact.validation_errors = errors
        artifact.is_valid = len(errors) == 0
        return artifact


class CoderAgent:
    """Materialize a small, real implementation artifact.

    The default implementation is deliberately a bounded local scaffold, not
    a claim that an arbitrary product was implemented.  It writes the generated
    file beneath the runtime home, compiles it, and records the actual path and
    compiler result.  A host can replace this agent with a sandboxed executor.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else None

    def implement_code(
        self,
        plan_artifact: DocumentArtifact,
        arch_artifact: DocumentArtifact,
    ) -> DocumentArtifact:
        artifact_id = f"code-{uuid.uuid4().hex[:8]}"
        root = (self.root or (runtime_home() / "sdlc" / artifact_id)).resolve()
        relative = Path("core/module.py")
        path = root / relative
        source = "def process_data(data: dict) -> dict:\n    return {'status': 'processed', 'input': data}\n"
        evidence: list[str] = []
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
            py_compile.compile(str(path), doraise=True)
            evidence.append(f"wrote {path}")
            evidence.append(f"compiled {path} with py_compile")
        except Exception as exc:
            evidence.append(f"write/compile failed: {type(exc).__name__}: {exc}")

        code_files = {str(relative): source} if path.is_file() else {}
        content = f"""# Code Implementation Deliverable

## Files Materialized:
{json.dumps(list(code_files.keys()), indent=2)}

## Implementation Scope:
Bounded local scaffold matching the generated contracts. This artifact does
not claim to implement requirements outside the materialized file.

## Verification Evidence:
{json.dumps(evidence, indent=2)}
"""
        artifact = DocumentArtifact(
            artifact_id=artifact_id,
            stage=SDLCStage.IMPLEMENTATION,
            title="Source Code Implementation",
            content=content,
            metadata={
                "files": code_files,
                "root_path": str(root),
                "evidence": evidence,
                "execution_mode": "local_bounded_scaffold",
            },
        )
        return self.validate_gate(artifact)

    def validate_gate(self, artifact: DocumentArtifact) -> DocumentArtifact:
        errors = []
        files = artifact.metadata.get("files", {})
        if not files:
            errors.append("Coder artifact contains no materialized source files.")
        root = Path(str(artifact.metadata.get("root_path", "")))
        for relative in files:
            if not (root / relative).is_file():
                errors.append(f"claimed source file is missing: {relative}")
        if not artifact.metadata.get("evidence"):
            errors.append("Coder artifact has no independent write/compile evidence.")
        artifact.validation_errors = errors
        artifact.is_valid = not errors
        return artifact


class QAAgent:
    """Run a real, bounded syntax/materialization gate for the scaffold."""

    def __init__(self, verifier: Any | None = None) -> None:
        self.verifier = verifier

    def verify_and_test(
        self,
        code_artifact: DocumentArtifact,
        prd_artifact: DocumentArtifact,
    ) -> DocumentArtifact:
        test_results: dict[str, Any] = {
            "total_tests": 0,
            "passed": 0,
            "failed": 0,
            "coverage_percent": None,
            "test_command": None,
        }
        evidence: list[str] = []
        files = code_artifact.metadata.get("files", {})
        root = Path(str(code_artifact.metadata.get("root_path", "")))
        for relative, source in files.items():
            test_results["total_tests"] += 1
            path = root / relative
            try:
                ast.parse(source, filename=str(path))
                py_compile.compile(str(path), doraise=True)
                test_results["passed"] += 1
                evidence.append(f"AST/compile passed: {path}")
            except Exception as exc:
                test_results["failed"] += 1
                evidence.append(f"AST/compile failed: {path}: {type(exc).__name__}: {exc}")

        # A host may inject a sandboxed test runner.  It must return measured
        # counts; arbitrary shell commands are never run by this library.
        if self.verifier is not None:
            measured = self.verifier(code_artifact=code_artifact, prd_artifact=prd_artifact)
            if isinstance(measured, dict):
                test_results.update(measured)
                evidence.append("host verifier returned measured results")
            else:
                test_results["failed"] += 1
                evidence.append(f"host verifier returned invalid result: {measured!r}")

        content = f"""# QA Verification & Test Report

## 1. Measured Verification
- Checks: {test_results["total_tests"]}
- Passed: {test_results["passed"]}
- Failed: {test_results["failed"]}
- Coverage: {test_results["coverage_percent"] if test_results["coverage_percent"] is not None else "not measured"}

## Evidence
{json.dumps(evidence, indent=2)}

## Gate Decision
{"VERIFICATION PASSED for the measured checks." if test_results["failed"] == 0 and test_results["passed"] > 0 else "VERIFICATION FAILED or INCOMPLETE."}
"""
        artifact = DocumentArtifact(
            artifact_id=f"qa-{uuid.uuid4().hex[:8]}",
            stage=SDLCStage.QA_VERIFICATION,
            title="QA Verification Report",
            content=content,
            metadata={"test_results": test_results, "evidence": evidence},
        )
        return self.validate_gate(artifact)

    def validate_gate(self, artifact: DocumentArtifact) -> DocumentArtifact:
        errors = []
        res = artifact.metadata.get("test_results", {})
        if res.get("failed", 1) > 0:
            errors.append("QA Verification failed: failing checks present.")
        if res.get("passed", 0) <= 0:
            errors.append("QA Verification incomplete: zero passing checks.")
        if not artifact.metadata.get("evidence"):
            errors.append("QA Verification has no independent evidence.")
        artifact.validation_errors = errors
        artifact.is_valid = not errors
        return artifact


# --- Document-Gated SDLC Engine Controller ---


@dataclass
class SDLCExecutionResult:
    success: bool
    final_stage: SDLCStage
    artifacts: dict[str, DocumentArtifact] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "final_stage": self.final_stage.value,
            "artifacts": {k: v.to_dict() for k, v in self.artifacts.items()},
            "log": self.log,
        }


class DocumentGatedSDLCEngine:
    """Orchestrates autonomous multi-agent software engineering through document quality gates."""

    def __init__(self) -> None:
        self.pm = ProductManagerAgent()
        self.architect = ArchitectAgent()
        self.project_mgr = ProjectManagerAgent()
        self.coder = CoderAgent()
        self.qa = QAAgent()

    def run_pipeline(
        self,
        requirements: str,
        project_name: str = "AlphaSDLC",
        max_revision_rounds: int = 2,
    ) -> SDLCExecutionResult:
        """Executes the complete autonomous SDLC cycle without blocking human-in-the-loop gates."""
        artifacts: dict[str, DocumentArtifact] = {}
        logs: list[str] = []

        logs.append(f"Starting Document-Gated SDLC for project '{project_name}'")

        # 1. Product Manager Stage (PRD)
        prd = self.pm.generate_prd(requirements=requirements, project_name=project_name)
        if not prd.is_valid:
            logs.append(f"Gate 1 (PRD) failed: {prd.validation_errors}")
            return SDLCExecutionResult(success=False, final_stage=SDLCStage.PRD, artifacts=artifacts, log=logs)
        artifacts["prd"] = prd
        logs.append("Gate 1 (PRD) passed successfully.")

        # 2. Architect Stage (System Architecture & UML)
        arch = self.architect.design_architecture(prd_artifact=prd)
        if not arch.is_valid:
            logs.append(f"Gate 2 (Architecture) failed: {arch.validation_errors}")
            return SDLCExecutionResult(success=False, final_stage=SDLCStage.ARCHITECTURE, artifacts=artifacts, log=logs)
        artifacts["architecture"] = arch
        logs.append("Gate 2 (Architecture) passed successfully.")

        # 3. Project Manager Stage (WBS & Tasks)
        plan = self.project_mgr.create_project_plan(prd_artifact=prd, arch_artifact=arch)
        if not plan.is_valid:
            logs.append(f"Gate 3 (Plan) failed: {plan.validation_errors}")
            return SDLCExecutionResult(success=False, final_stage=SDLCStage.PROJECT_PLAN, artifacts=artifacts, log=logs)
        artifacts["project_plan"] = plan
        logs.append("Gate 3 (Plan) passed successfully.")

        # 4. Coder Stage (Implementation)
        code = self.coder.implement_code(plan_artifact=plan, arch_artifact=arch)
        if not code.is_valid:
            logs.append(f"Gate 4 (Code) failed: {code.validation_errors}")
            return SDLCExecutionResult(success=False, final_stage=SDLCStage.IMPLEMENTATION, artifacts=artifacts, log=logs)
        artifacts["implementation"] = code
        logs.append("Gate 4 (Code) passed successfully.")

        # 5. QA Stage (Verification & Tests)
        qa_rep = self.qa.verify_and_test(code_artifact=code, prd_artifact=prd)
        if not qa_rep.is_valid:
            logs.append(f"Gate 5 (QA) failed: {qa_rep.validation_errors}")
            return SDLCExecutionResult(success=False, final_stage=SDLCStage.QA_VERIFICATION, artifacts=artifacts, log=logs)
        artifacts["qa_report"] = qa_rep
        logs.append("Gate 5 (QA) passed successfully.")

        logs.append("All SDLC document gates cleared autonomously. Pipeline completed.")
        return SDLCExecutionResult(
            success=True,
            final_stage=SDLCStage.COMPLETED,
            artifacts=artifacts,
            log=logs,
        )
