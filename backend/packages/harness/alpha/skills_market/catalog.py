"""Skill Marketplace catalog: browsable listings.

This is the *marketplace* surface (discovery + install state), distinct from
:mod:`alpha.skills`, which loads and runs the actual ``SKILL.md`` packages. A
listing describes a skill that *can* be installed; the registry in
:mod:`alpha.skills_market.registry` records what the user installed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SkillListing:
    """One installable skill in the marketplace."""

    id: str
    name: str
    category: str
    description: str = ""
    tags: tuple[str, ...] = ()
    version: str = "1.0.0"
    author: str = ""
    source: str = "builtin"  # builtin | enterprise | community
    recommended: bool = False

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError("skill listing id must be a non-empty string")
        if not self.name:
            raise ValueError("skill listing name must be non-empty")
        if self.source not in {"builtin", "enterprise", "community"}:
            raise ValueError(f"unknown source {self.source!r}")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tags"] = list(self.tags)
        return d


#: A small first-party catalog mirroring the WorkBuddy marketplace categories.
BUILTIN_SKILLS: tuple[SkillListing, ...] = (
    SkillListing("excel-processing", "Excel Processing", "content", "Clean, analyse and chart spreadsheets.",
                 ("excel", "data"), source="builtin", recommended=True),
    SkillListing("ppt-generator", "PPT Generator", "content", "Generate presentation decks from an outline.",
                 ("ppt", "slides"), source="builtin", recommended=True),
    SkillListing("doc-generator", "Document Generator", "content", "Write and format Word documents.",
                 ("docx", "report"), source="builtin"),
    SkillListing("web-search", "Web Search", "research", "Search the web with recency filters.",
                 ("search", "web"), source="builtin", recommended=True),
    SkillListing("deep-research", "Deep Research", "research", "Multi-source research and synthesis.",
                 ("research", "report"), source="builtin"),
    SkillListing("pdf-tools", "PDF Toolkit", "productivity", "Read, split, merge and OCR PDFs.",
                 ("pdf", "ocr"), source="builtin"),
    SkillListing("image-generation", "Image Generation", "media", "Generate and edit images.",
                 ("image", "art"), source="builtin"),
    SkillListing("video-generation", "Video Generation", "media", "Generate short videos from text or images.",
                 ("video",), source="builtin"),
    SkillListing("calendar", "Calendar", "productivity", "Query availability and manage events.",
                 ("calendar", "google"), source="builtin"),
    SkillListing("drive", "Cloud Drive", "storage", "Read and write files in cloud storage.",
                 ("drive", "google"), source="builtin"),
    SkillListing("frontend-design", "Frontend Design", "engineering", "Build responsive, accessible UIs.",
                 ("web", "design"), source="builtin"),
    SkillListing("financial-research", "Financial Research", "finance", "Company and market research.",
                 ("finance", "research"), source="enterprise"),
)


class SkillMarketCatalog:
    """Read-only, searchable view over marketplace listings."""

    def __init__(self, listings: tuple[SkillListing, ...] | None = None) -> None:
        self._by_id: dict[str, SkillListing] = {s.id: s for s in (listings if listings is not None else BUILTIN_SKILLS)}

    def get(self, skill_id: str) -> SkillListing | None:
        return self._by_id.get((skill_id or "").strip().lower())

    def list(self, *, category: str | None = None, source: str | None = None) -> list[SkillListing]:
        items = list(self._by_id.values())
        if category:
            cat = category.strip().lower()
            items = [s for s in items if s.category == cat]
        if source:
            src = source.strip().lower()
            items = [s for s in items if s.source == src]
        return sorted(items, key=lambda s: s.name.lower())

    def search(self, query: str) -> list[SkillListing]:
        needle = (query or "").strip().lower()
        if not needle:
            return self.list()
        return sorted(
            (s for s in self._by_id.values()
             if needle in " ".join((s.id, s.name, s.category, s.description, *s.tags)).lower()),
            key=lambda s: s.name.lower(),
        )

    def recommended(self) -> list[SkillListing]:
        return sorted((s for s in self._by_id.values() if s.recommended), key=lambda s: s.name.lower())

    def categories(self) -> list[str]:
        return sorted({s.category for s in self._by_id.values()})


__all__ = ["BUILTIN_SKILLS", "SkillListing", "SkillMarketCatalog"]
