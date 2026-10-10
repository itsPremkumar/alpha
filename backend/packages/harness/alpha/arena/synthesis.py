"""Synthesis: combine the strongest valid contributions.

The bracket picks a champion; synthesis answers the harder question
"what is the best *answer*", built from every candidate's best
fragments. Two deliverables:

* **answer synthesis** - merge the top contribution per requirement
  into one document, reporting gaps rather than papering over them;
* **code synthesis** - a merge planner over patch contribution
  records, which never executes a merge itself.

Code synthesis is a *plan*, not an action: overlapping edits to the
same region are flagged for the operator, and the plan says so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from alpha.arena.candidate import (
    CandidateArtifact,
    Contribution,
    best_contributions,
    synthesis_gaps,
)


class MergeStatus(str, Enum):
    APPLY = "apply"          # non-overlapping, safe to apply as-is
    CONFLICT = "conflict"    # two contributions touch the same region
    MANUAL = "manual"        # needs human review regardless


@dataclass(frozen=True)
class PatchContribution:
    """One candidate's edit to one region of the codebase."""

    agent_id: str
    path: str
    start_line: int
    end_line: int
    patch: str
    requirement_id: str = ""
    quality: int = 0

    def overlaps(self, other: PatchContribution) -> bool:
        if self.path != other.path:
            return False
        return not (self.end_line < other.start_line or other.end_line < self.start_line)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "patch": self.patch,
            "requirement_id": self.requirement_id,
            "quality": self.quality,
        }


@dataclass(frozen=True)
class MergeItem:
    patch: PatchContribution
    status: MergeStatus
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch": self.patch.to_dict(),
            "status": self.status.value,
            "reason": self.reason,
        }


@dataclass
class MergePlan:
    items: list[MergeItem] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @property
    def conflicts(self) -> list[MergeItem]:
        return [i for i in self.items if i.status is MergeStatus.CONFLICT]

    @property
    def applyable(self) -> list[MergeItem]:
        return [i for i in self.items if i.status is MergeStatus.APPLY]

    @property
    def is_clean(self) -> bool:
        return not self.conflicts

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [i.to_dict() for i in self.items],
            "conflicts": len(self.conflicts),
            "applyable": len(self.applyable),
            "clean": self.is_clean,
            "created_at": self.created_at,
        }


def plan_merge(patches: list[PatchContribution]) -> MergePlan:
    """Order patches by quality per region and flag overlaps.

    Highest-quality patch for a region wins; every other patch
    touching that region becomes a CONFLICT. Nothing is executed -
    this is a plan for an operator or a separate, explicitly-approved
    step.
    """
    ordered = sorted(patches, key=lambda p: (-p.quality, p.agent_id, p.path, p.start_line))
    accepted: list[PatchContribution] = []
    plan = MergePlan()
    for patch in ordered:
        clashing = next((a for a in accepted if a.overlaps(patch)), None)
        if clashing is None:
            accepted.append(patch)
            plan.items.append(MergeItem(patch=patch, status=MergeStatus.APPLY))
        else:
            plan.items.append(
                MergeItem(
                    patch=patch,
                    status=MergeStatus.CONFLICT,
                    reason=(
                        f"overlaps {clashing.agent_id} on {clashing.path}:"
                        f"{clashing.start_line}-{clashing.end_line}"
                    ),
                )
            )
    # Present in file order for readability.
    plan.items.sort(key=lambda i: (i.patch.path, i.patch.start_line, i.patch.agent_id))
    return plan


@dataclass
class SynthesisResult:
    run_id: str
    sections: list[dict[str, str]] = field(default_factory=list)  # requirement_id, body, source
    gaps: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @property
    def is_complete(self) -> bool:
        return not self.gaps

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "sections": self.sections,
            "gaps": self.gaps,
            "sources": self.sources,
            "complete": self.is_complete,
            "created_at": self.created_at,
        }


def synthesize(
    run_id: str,
    artifacts: list[CandidateArtifact],
    requirement_ids: list[str],
    fallback_text: str = "",
) -> SynthesisResult:
    """Merge the best contribution per requirement.

    Requirements no candidate covered land in ``gaps`` with the
    fallback text - the result is never presented as complete when
    it is not.
    """
    winners = best_contributions(artifacts, requirement_ids)
    gaps = synthesis_gaps(artifacts, requirement_ids)
    sections: list[dict[str, str]] = []
    for rid in requirement_ids:
        contrib = winners.get(rid)
        if contrib is None:
            continue
        sections.append(
            {
                "requirement_id": rid,
                "title": contrib.title,
                "body": contrib.body,
                "quality": str(contrib.quality),
            }
        )
    if gaps and fallback_text:
        sections.append(
            {
                "requirement_id": "GAPS",
                "title": "Uncovered requirements",
                "body": fallback_text + "\n" + "\n".join(f"- {g}" for g in gaps),
                "quality": "0",
            }
        )
    return SynthesisResult(
        run_id=run_id,
        sections=sections,
        gaps=gaps,
        sources=sorted({a.agent_id for a in artifacts}),
    )


def render_markdown(result: SynthesisResult) -> str:
    lines = [f"# Synthesis for {result.run_id}", ""]
    for section in result.sections:
        lines.append(f"## {section['title']} ({section['requirement_id']})")
        lines.append("")
        lines.append(section["body"])
        lines.append("")
    if result.gaps:
        lines.append("## Gaps")
        lines.append("")
        lines.extend(f"- {g}" for g in result.gaps)
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "MergeStatus",
    "PatchContribution",
    "MergeItem",
    "MergePlan",
    "plan_merge",
    "SynthesisResult",
    "synthesize",
    "render_markdown",
]
