"""Candidate Artifact Contract + contribution extraction.

Every competitor's output is normalized into a structured artifact
with per-requirement coverage, so synthesis and judging read facts
rather than free prose.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CoverageStatus(str, Enum):
    COVERED = "covered"
    PARTIAL = "partial"
    MISSING = "missing"
    CONTRADICTORY = "contradictory"


@dataclass(frozen=True)
class RequirementCoverage:
    requirement_id: str
    status: CoverageStatus
    artifact_fragment: str = ""  # path:line or section reference
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "status": self.status.value,
            "artifact_fragment": self.artifact_fragment,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RequirementCoverage:
        return cls(
            requirement_id=data["requirement_id"],
            status=CoverageStatus(data["status"]),
            artifact_fragment=data.get("artifact_fragment", ""),
            notes=data.get("notes", ""),
        )


@dataclass(frozen=True)
class Contribution:
    """One reusable piece of the candidate's answer, scoped to a requirement."""

    requirement_id: str
    title: str
    body: str
    quality: int = 0  # 0-10 judge assessment of this fragment alone
    reusable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "title": self.title,
            "body": self.body,
            "quality": self.quality,
            "reusable": self.reusable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Contribution:
        return cls(
            requirement_id=data["requirement_id"],
            title=data["title"],
            body=data["body"],
            quality=int(data.get("quality", 0)),
            reusable=bool(data.get("reusable", True)),
        )


@dataclass
class CandidateArtifact:
    agent_id: str
    artifact_id: str
    run_id: str
    path: str
    summary: str = ""
    coverage: list[RequirementCoverage] = field(default_factory=list)
    contributions: list[Contribution] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def coverage_by_requirement(self) -> dict[str, CoverageStatus]:
        return {c.requirement_id: c.status for c in self.coverage}

    def uncovered(self, requirement_ids: list[str]) -> list[str]:
        covered = self.coverage_by_requirement()
        return [
            rid
            for rid in requirement_ids
            if covered.get(rid, CoverageStatus.MISSING) != CoverageStatus.COVERED
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "path": self.path,
            "summary": self.summary,
            "coverage": [c.to_dict() for c in self.coverage],
            "contributions": [c.to_dict() for c in self.contributions],
            "claims": self.claims,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateArtifact:
        return cls(
            agent_id=data["agent_id"],
            artifact_id=data["artifact_id"],
            run_id=data["run_id"],
            path=data["path"],
            summary=data.get("summary", ""),
            coverage=[RequirementCoverage.from_dict(c) for c in data.get("coverage", [])],
            contributions=[Contribution.from_dict(c) for c in data.get("contributions", [])],
            claims=list(data.get("claims", [])),
            created_at=data.get("created_at", time.time()),
        )


def best_contributions(
    artifacts: list[CandidateArtifact],
    requirement_ids: list[str],
) -> dict[str, Contribution]:
    """Pick the highest-quality reusable contribution per requirement.

    Deterministic: ties break on the lowest agent id so a resume
    produces the same synthesis as a fresh run.
    """
    best: dict[str, tuple[int, str, Contribution]] = {}
    for artifact in sorted(artifacts, key=lambda a: a.agent_id):
        for contrib in artifact.contributions:
            if not contrib.reusable or contrib.requirement_id not in requirement_ids:
                continue
            current = best.get(contrib.requirement_id)
            if current is None or contrib.quality > current[0]:
                best[contrib.requirement_id] = (contrib.quality, artifact.agent_id, contrib)
    return {rid: entry[2] for rid, entry in best.items()}


def synthesis_gaps(
    artifacts: list[CandidateArtifact],
    requirement_ids: list[str],
) -> list[str]:
    """Requirements no candidate covered - surfaced, never silently dropped."""
    covered: set[str] = set()
    for artifact in artifacts:
        for cov in artifact.coverage:
            if cov.status == CoverageStatus.COVERED:
                covered.add(cov.requirement_id)
    return [rid for rid in requirement_ids if rid not in covered]


__all__ = [
    "CoverageStatus",
    "RequirementCoverage",
    "Contribution",
    "CandidateArtifact",
    "best_contributions",
    "synthesis_gaps",
]
