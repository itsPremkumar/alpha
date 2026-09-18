"""Autonomous Mutation Testing and Invariant Fuzzing Engine.

Injects synthetic AST mutations to evaluate test suite kill scores and executes
property-based invariant fuzzing to uncover edge cases and state anomalies.
"""

from __future__ import annotations

import ast
import copy
import random
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


class MutantType(str, Enum):
    COMPARISON_INVERSION = "comparison_inversion"
    BOOLEAN_NEGATION = "boolean_negation"
    BOUNDARY_OFFSET = "boundary_offset"
    RETURN_ZEROING = "return_zeroing"


@dataclass
class MutantCandidate:
    mutant_id: str
    mutant_type: MutantType
    original_code: str
    mutated_code: str
    line_number: int
    description: str


@dataclass
class MutationAuditReport:
    total_mutants: int
    killed_mutants: int
    survived_mutants: int
    kill_score: float
    details: list[dict[str, Any]]


class ASTMutantInjector(ast.NodeTransformer):
    """Transforms Python AST nodes into syntactically valid mutated variants."""

    def __init__(self):
        self.mutants: list[tuple[ast.AST, MutantType, int, str]] = []

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        # Invert comparisons: < to <=, == to !=, > to >=
        for op in node.ops:
            if isinstance(op, ast.Lt):
                node.ops = [ast.LtE()]
                self.mutants.append((node, MutantType.COMPARISON_INVERSION, getattr(node, "lineno", 1), "Mutated '<' to '<='"))
            elif isinstance(op, ast.Eq):
                node.ops = [ast.NotEq()]
                self.mutants.append((node, MutantType.COMPARISON_INVERSION, getattr(node, "lineno", 1), "Mutated '==' to '!='"))
            elif isinstance(op, ast.Gt):
                node.ops = [ast.GtE()]
                self.mutants.append((node, MutantType.COMPARISON_INVERSION, getattr(node, "lineno", 1), "Mutated '>' to '>='"))
        return node


class MutationTestingEngine:
    """Executes mutation testing against test suites."""

    def generate_mutants(self, source_code: str) -> list[MutantCandidate]:
        candidates = []
        try:
            tree = ast.parse(source_code)
        except Exception:
            return []

        # 1. Comparison mutations
        lines = source_code.splitlines()
        for idx, line in enumerate(lines, 1):
            if " < " in line:
                mut = source_code.replace(" < ", " <= ", 1)
                candidates.append(
                    MutantCandidate(
                        mutant_id=f"mut_{len(candidates)+1}",
                        mutant_type=MutantType.COMPARISON_INVERSION,
                        original_code=source_code,
                        mutated_code=mut,
                        line_number=idx,
                        description="Inverted '<' to '<='",
                    )
                )
            if " == " in line:
                mut = source_code.replace(" == ", " != ", 1)
                candidates.append(
                    MutantCandidate(
                        mutant_id=f"mut_{len(candidates)+1}",
                        mutant_type=MutantType.COMPARISON_INVERSION,
                        original_code=source_code,
                        mutated_code=mut,
                        line_number=idx,
                        description="Inverted '==' to '!='",
                    )
                )
            if "return " in line and "return None" not in line:
                mut = source_code.replace(line, "    return None", 1)
                candidates.append(
                    MutantCandidate(
                        mutant_id=f"mut_{len(candidates)+1}",
                        mutant_type=MutantType.RETURN_ZEROING,
                        original_code=source_code,
                        mutated_code=mut,
                        line_number=idx,
                        description="Zeroed return value to None",
                    )
                )

        return candidates

    def run_mutation_audit(
        self,
        source_code: str,
        test_runner: Callable[[str], bool],
    ) -> MutationAuditReport:
        """Run mutation test audit. Returns score based on killed mutants."""
        mutants = self.generate_mutants(source_code)
        if not mutants:
            return MutationAuditReport(
                total_mutants=0,
                killed_mutants=0,
                survived_mutants=0,
                kill_score=1.0,
                details=[],
            )

        killed = 0
        survived = 0
        details = []

        for m in mutants:
            # If test passes on mutant, the mutant SURVIVED (test failed to catch it)
            # If test fails on mutant, the mutant was KILLED (test caught the bug)
            passed = test_runner(m.mutated_code)
            if passed:
                survived += 1
                status = "survived"
            else:
                killed += 1
                status = "killed"

            details.append({
                "mutant_id": m.mutant_id,
                "type": m.mutant_type.value,
                "line": m.line_number,
                "status": status,
                "description": m.description,
            })

        total = killed + survived
        score = (killed / total) if total > 0 else 1.0
        return MutationAuditReport(
            total_mutants=total,
            killed_mutants=killed,
            survived_mutants=survived,
            kill_score=round(score, 3),
            details=details,
        )


class PropertyInvariantFuzzer:
    """Fuzzes function invariants across boundary distributions."""

    @staticmethod
    def fuzz_invariants(
        fn: Callable[[Any], Any],
        invariant_check: Callable[[Any, Any], bool],
        input_generator: Callable[[], Any],
        trials: int = 50,
    ) -> dict[str, Any]:
        """Fuzz function with random inputs and assert invariant holding."""
        passed_trials = 0
        violations = []

        for i in range(trials):
            val = input_generator()
            try:
                res = fn(val)
                if invariant_check(val, res):
                    passed_trials += 1
                else:
                    violations.append({"input": str(val), "output": str(res), "error": "Invariant violated"})
            except Exception as e:
                violations.append({"input": str(val), "error": f"Exception: {e}"})

        return {
            "total_trials": trials,
            "passed_trials": passed_trials,
            "success_rate": round(passed_trials / trials, 3),
            "violations": violations[:5],
        }
