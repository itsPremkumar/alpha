"""Expert catalog: role-based agents and multi-expert pipelines.

WorkBuddy models two things that are distinct from a raw tool:

- an **Expert** — a role (prompt + skills + model hint) that adds domain
  experience to a task; and
- an **Expert Group** — several Experts wired into a pipeline, each owning a
  stage (e.g. content director -> copywriter -> video generator -> editor).

This module owns the read-only catalog (the declarations). Install/enable state
lives in :mod:`alpha.experts.registry`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Expert:
    """A role-based agent declaration."""

    id: str
    name: str
    category: str
    role: str
    description: str = ""
    skills: tuple[str, ...] = ()
    model_hint: str = ""
    version: str = "1.0.0"

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError("expert id must be a non-empty string")
        if not self.name:
            raise ValueError("expert name must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["skills"] = list(self.skills)
        return d


@dataclass(frozen=True)
class ExpertStep:
    """One stage of an expert-group pipeline."""

    expert_id: str
    stage: str

    def __post_init__(self) -> None:
        if not self.expert_id:
            raise ValueError("expert step needs an expert_id")
        if not self.stage:
            raise ValueError("expert step needs a stage")


@dataclass(frozen=True)
class ExpertGroup:
    """A pipeline of experts collaborating on one task."""

    id: str
    name: str
    description: str
    steps: tuple[ExpertStep, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("expert group id must be non-empty")
        if not self.steps:
            raise ValueError("an expert group needs at least one step")

    def expert_ids(self) -> list[str]:
        return [s.expert_id for s in self.steps]


#: A small first-party set mirroring the WorkBuddy expert categories.
BUILTIN_EXPERTS: tuple[Expert, ...] = (
    Expert("creative-director", "Creative Director", "content", "Leads creative direction and final review.",
           "Owns the creative vision and signs off on deliverables.", ("brainstorm", "review"), "frontier"),
    Expert("copywriter", "Copywriter", "content", "Writes persuasive, on-brand copy.",
           "Drafts copy for any channel.", ("writing", "seo"), "default"),
    Expert("researcher", "Researcher", "research", "Deep research and synthesis.",
           "Gathers and verifies facts across sources.", ("web-search", "deep-research"), "frontier"),
    Expert("data-analyst", "Data Analyst", "data", "Analyses data and builds visualisations.",
           "Turns spreadsheets into findings and charts.", ("data-analysis", "charting"), "default"),
    Expert("video-generator", "Video Generator", "media", "Generates and edits video.",
           "Produces video assets from briefs.", ("video-gen", "editing"), "default"),
    Expert("graphic-designer", "Graphic Designer", "media", "Produces image and layout assets.",
           "Creates visuals and layouts.", ("image-gen", "layout"), "default"),
    Expert("frontend-expert", "Frontend Expert", "engineering", "Builds web UI.",
           "Implements responsive, accessible frontends.", ("web-design", "coding"), "frontier"),
    Expert("legal-researcher", "Legal Researcher", "legal", "Researches statutes and cases.",
           "Answers legal questions from authoritative sources.", ("legal-db", "web-search"), "frontier"),
)


BUILTIN_EXPERT_GROUPS: tuple[ExpertGroup, ...] = (
    ExpertGroup("content-creation", "Content Creation Team",
                "A full content pipeline from concept to edited video.",
                (ExpertStep("creative-director", "direction"),
                 ExpertStep("copywriter", "copy"),
                 ExpertStep("graphic-designer", "visuals"),
                 ExpertStep("video-generator", "video"))),
    ExpertGroup("research-and-report", "Research & Report Team",
                "Research a topic and produce an analysed report.",
                (ExpertStep("researcher", "research"),
                 ExpertStep("data-analyst", "analysis"))),
)


class ExpertCatalog:
    """Read-only, searchable view over experts and expert groups."""

    def __init__(self, experts: tuple[Expert, ...] | None = None, groups: tuple[ExpertGroup, ...] | None = None) -> None:
        self._experts: dict[str, Expert] = {e.id: e for e in (experts if experts is not None else BUILTIN_EXPERTS)}
        self._groups: dict[str, ExpertGroup] = {g.id: g for g in (groups if groups is not None else BUILTIN_EXPERT_GROUPS)}

    # -- experts ----------------------------------------------------------
    def get_expert(self, expert_id: str) -> Expert | None:
        return self._experts.get((expert_id or "").strip().lower())

    def list_experts(self, *, category: str | None = None) -> list[Expert]:
        items = list(self._experts.values())
        if category:
            cat = category.strip().lower()
            items = [e for e in items if e.category == cat]
        return sorted(items, key=lambda e: e.name.lower())

    def search_experts(self, query: str) -> list[Expert]:
        needle = (query or "").strip().lower()
        if not needle:
            return self.list_experts()
        return sorted(
            (e for e in self._experts.values()
             if needle in " ".join((e.id, e.name, e.category, e.role, e.description, *e.skills)).lower()),
            key=lambda e: e.name.lower(),
        )

    # -- groups -----------------------------------------------------------
    def get_group(self, group_id: str) -> ExpertGroup | None:
        return self._groups.get((group_id or "").strip().lower())

    def list_groups(self) -> list[ExpertGroup]:
        return sorted(self._groups.values(), key=lambda g: g.name.lower())

    def group_members(self, group_id: str) -> list[Expert]:
        group = self.get_group(group_id)
        if group is None:
            return []
        return [self._experts[eid] for eid in group.expert_ids() if eid in self._experts]

    def validate_group(self, group_id: str) -> list[str]:
        """Return the expert ids a group references that are not in the catalog."""
        group = self.get_group(group_id)
        if group is None:
            raise KeyError(f"unknown expert group {group_id!r}")
        return [eid for eid in group.expert_ids() if eid not in self._experts]


__all__ = [
    "BUILTIN_EXPERT_GROUPS",
    "BUILTIN_EXPERTS",
    "Expert",
    "ExpertCatalog",
    "ExpertGroup",
    "ExpertStep",
]
