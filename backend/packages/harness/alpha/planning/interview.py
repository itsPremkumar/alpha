"""Plan interview mode: gap questions -> plan artifact -> review -> pinned approval.

The agent interviews the operator for missing requirements, drafts a plan
artifact, runs up to 3 read-only review rounds, then pins the approved plan
hash so execution cannot drift from what was agreed.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

MAX_REVIEW_ROUNDS = 3

PlanStatus = Literal["interviewing", "draft", "in_review", "approved", "rejected", "executing"]


@dataclass
class GapQuestion:
    question_id: str
    question: str
    why: str = ""
    answer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanArtifact:
    plan_id: str
    objective: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    review_rounds: int = 0
    reviews: list[dict[str, Any]] = field(default_factory=list)
    status: PlanStatus = "draft"
    plan_hash: str = ""
    created_at: float = field(default_factory=time.time)

    def compute_hash(self) -> str:
        canonical = json.dumps({"objective": self.objective, "steps": self.steps, "risks": self.risks}, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def derive_gap_questions(objective: str, *, known: dict[str, Any] | None = None) -> list[GapQuestion]:
    """Heuristic gap analysis: what must be known before planning is safe."""
    known = known or {}
    gaps: list[GapQuestion] = []

    def need(key: str, question: str, why: str) -> None:
        if not known.get(key):
            gaps.append(GapQuestion(question_id=f"gq-{uuid.uuid4().hex[:8]}", question=question, why=why))

    need("deliverables", "What exact deliverables prove this objective is done?", "Plans without verifiable outputs drift.")
    need("constraints", "What constraints (stack, budget, deadline) bind this work?", "Constraints change the viable strategy.")
    need("scope", "What is explicitly out of scope?", "Unbounded scope is the top cause of failed plans.")
    need("risk_tolerance", "Which actions need human approval (deploy, external sends, deletions)?", "Approval gates must be pinned before execution.")
    if any(w in objective.lower() for w in ("migrate", "refactor", "rewrite", "deploy", "production")):
        need("rollback", "What is the rollback plan if the change fails in production?", "Risky changes without rollback are rejected at review.")
    return gaps[:6]


def record_review(plan: PlanArtifact, reviewer: str, *, verdict: Literal["approve", "request_changes", "reject"], comments: str = "") -> PlanArtifact:
    if plan.status not in ("draft", "in_review"):
        raise ValueError(f"Plan {plan.plan_id} is {plan.status}; review closed.")
    if plan.review_rounds >= MAX_REVIEW_ROUNDS and verdict == "request_changes":
        plan.status = "rejected"
        plan.reviews.append({"reviewer": reviewer, "verdict": verdict, "comments": comments + " [round cap reached]", "at": time.time()})
        return plan
    plan.reviews.append({"reviewer": reviewer, "verdict": verdict, "comments": comments, "at": time.time()})
    if verdict == "approve":
        plan.status = "approved"
        plan.plan_hash = plan.compute_hash()
    elif verdict == "reject":
        plan.status = "rejected"
    else:
        plan.status = "in_review"
        plan.review_rounds += 1
    return plan


def new_plan(objective: str) -> PlanArtifact:
    return PlanArtifact(plan_id=f"pln-{uuid.uuid4().hex[:10]}", objective=objective)
