"""Collaborative Living Specification Engine.

Maintains a single, unified source of truth for requirements, API contracts,
data models, and architectural boundaries across multi-agent collaborations.
Synchronizes dynamically to `projects/<project_id>/LIVING_SPEC.md` with section-level
bot authorship, version stamps, and lock protection.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_workspace.config.runtime_paths import runtime_home
from agent_workspace.projects.events import get_event_bus

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _spec_dir(project_id: str) -> Path:
    return runtime_home() / "projects" / project_id


@dataclass
class LivingSpecSection:
    """A distinct section in the project's living specification."""

    section_key: str
    title: str
    content: str
    last_author_bot: str = "architect"
    version: int = 1
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LivingSpecSection:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class LivingSpec:
    """The consolidated living specification document."""

    def __init__(self, project_id: str, title: str = "Project Living Specification"):
        self.project_id = project_id
        self.title = title
        self.sections: dict[str, LivingSpecSection] = {}
        self.updated_at = _now()

    def set_section(self, section: LivingSpecSection) -> None:
        self.sections[section.section_key] = section
        self.updated_at = _now()

    def render_markdown(self) -> str:
        """Render complete Markdown document suitable for repository commitment."""
        lines = [
            f"# {self.title}",
            f"\n> **Project**: `{self.project_id}` | **Last Updated**: {self.updated_at}",
            "\nThis document is the living single source of truth continuously maintained by the autonomous bot workforce.",
            "\n## Table of Contents",
        ]

        for s in self.sections.values():
            anchor = s.title.lower().replace(" ", "-").replace("&", "").replace("/", "")
            lines.append(f"- [{s.title}](#{anchor}) *(Last modified by @{s.last_author_bot}, v{s.version})*")

        for s in self.sections.values():
            lines.append(f"\n---\n\n## {s.title}")
            lines.append(f"*(Author: @{s.last_author_bot} | Version: {s.version} | Timestamp: {s.updated_at})*\n")
            lines.append(s.content.strip())

        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "title": self.title,
            "updated_at": self.updated_at,
            "sections": {k: s.to_dict() for k, s in self.sections.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LivingSpec:
        spec = cls(data.get("project_id", ""), data.get("title", "Project Living Specification"))
        spec.updated_at = data.get("updated_at", _now())
        for k, v in data.get("sections", {}).items():
            spec.sections[k] = LivingSpecSection.from_dict(v)
        return spec


class LivingSpecEngine:
    """Manages file persistence and bidirectional synchronization of the living specification."""

    def __init__(self, project_id: str, base_dir: Path | None = None):
        self.project_id = project_id
        self._root = base_dir or _spec_dir(project_id)
        self._json_path = self._root / "spec" / "spec.json"
        self._md_path = self._root / "LIVING_SPEC.md"
        self._lock = threading.Lock()
        self._spec = LivingSpec(project_id)
        self._load()

    def _load(self) -> None:
        if self._json_path.exists():
            try:
                with open(self._json_path, encoding="utf-8") as f:
                    self._spec = LivingSpec.from_dict(json.load(f))
                return
            except Exception:
                logger.warning("Failed to load living spec JSON for %s", self.project_id, exc_info=True)

        # Initialize default sections if new
        self._init_defaults()

    def _init_defaults(self) -> None:
        defaults = [
            ("overview", "1. System Overview & Objectives", "High-level functional overview and success metrics."),
            ("requirements", "2. Core Requirements & Acceptance Criteria", "- [ ] Primary user stories and acceptance criteria."),
            ("api_contracts", "3. API Interfaces & Data Contracts", "TypeScript interfaces and REST / JSON schema specifications."),
            ("data_models", "4. Data Models & State Architecture", "Database entities, relations, and caching strategies."),
            ("open_decisions", "5. Active Decisions & Open Questions", "Open architectural trade-offs pending team consensus."),
        ]
        for key, title, content in defaults:
            self._spec.sections[key] = LivingSpecSection(
                section_key=key,
                title=title,
                content=content,
                last_author_bot="architect",
            )
        self._save()

    def _save(self) -> None:
        try:
            self._json_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_json = self._json_path.with_suffix(".tmp")
            with open(tmp_json, "w", encoding="utf-8") as f:
                json.dump(self._spec.to_dict(), f, indent=2)
            tmp_json.replace(self._json_path)

            # Sync Markdown
            self._root.mkdir(parents=True, exist_ok=True)
            self._md_path.write_text(self._spec.render_markdown(), encoding="utf-8")
        except Exception:
            logger.warning("Failed to save living spec for %s", self.project_id, exc_info=True)

    def get_spec(self) -> LivingSpec:
        with self._lock:
            return self._spec

    def update_section(
        self,
        section_key: str,
        title: str,
        content: str,
        author_bot: str = "architect",
    ) -> LivingSpecSection:
        """Update an existing section or add a new one, bumping its version."""
        with self._lock:
            current = self._spec.sections.get(section_key)
            version = (current.version + 1) if current else 1
            section = LivingSpecSection(
                section_key=section_key,
                title=title,
                content=content,
                last_author_bot=author_bot,
                version=version,
                updated_at=_now(),
            )
            self._spec.set_section(section)
            self._save()

        get_event_bus(self.project_id).emit(
            "file_changed",
            author_bot,
            {"path": "LIVING_SPEC.md", "section": section_key, "version": version},
        )
        return section

    def get_section(self, section_key: str) -> LivingSpecSection | None:
        with self._lock:
            return self._spec.sections.get(section_key)

    def list_sections(self) -> list[LivingSpecSection]:
        with self._lock:
            return list(self._spec.sections.values())

    def get_markdown_path(self) -> Path:
        return self._md_path


_spec_engines: dict[str, LivingSpecEngine] = {}
_spec_lock = threading.Lock()


def get_living_spec_engine(project_id: str) -> LivingSpecEngine:
    with _spec_lock:
        eng = _spec_engines.get(project_id)
        if eng is None:
            eng = LivingSpecEngine(project_id)
            _spec_engines[project_id] = eng
        return eng
