"""Evidence-based completion: no evidence means no completion.

A task may move to DONE only with attached proof. The gate is a pure check
so UI, API, and agents enforce exactly the same rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EvidenceKind = Literal["commit", "tests_passed", "lint", "typecheck", "security", "integration", "manual", "artifact"]

REQUIRED_FOR_CODE = ("commit", "tests_passed", "lint")
REQUIRED_FOR_DEPLOY = ("commit", "tests_passed", "security", "integration")


@dataclass
class Evidence:
    kind: str
    reference: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "reference": self.reference, "detail": self.detail}


@dataclass
class GateResult:
    passed: bool
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "missing": self.missing}


def check_completion(evidence: list[Evidence | dict[str, Any]], *, task_kind: str = "code") -> GateResult:
    kinds = {e.kind if isinstance(e, Evidence) else str(e.get("kind", "")) for e in evidence}
    required = REQUIRED_FOR_DEPLOY if task_kind == "deploy" else REQUIRED_FOR_CODE
    missing = [r for r in required if r not in kinds]
    return GateResult(passed=not missing, missing=missing)


def evidence_from_receipts(receipts: list[dict[str, Any]]) -> list[Evidence]:
    out: list[Evidence] = []
    for r in receipts:
        kind = str(r.get("kind", "") or r.get("type", ""))
        ref = str(r.get("commit") or r.get("reference") or r.get("report") or r.get("path") or "")
        if kind and ref:
            out.append(Evidence(kind=kind, reference=ref, detail=str(r.get("detail", ""))))
    return out
