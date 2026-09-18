"""Persistent Continual Harness State for Agent Workspace (inspired by Prime Agent).

The state model records supplemental prompt notes, memories, skill descriptors,
and reusable subagent configurations in session-local and global stores.
The immutable base system prompt and safety policies are never modified.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

HarnessKind = Literal["prompt", "memory", "skill", "subagent"]
HarnessScope = Literal["local", "global"]

_DEFAULT_FILE_NAME = "harness_state.json"
_DEFAULT_DIR_NAME = ".agent_workspace"
_KINDS: tuple[HarnessKind, ...] = ("prompt", "memory", "skill", "subagent")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(raw: str, fallback: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw.strip())
    normalized = "_".join(part for part in normalized.split("_") if part)
    return (normalized or fallback)[:80]


def get_default_global_dir() -> Path:
    env_dir = os.environ.get("AGENT_WORKSPACE_GLOBAL_HARNESS_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir.strip()).expanduser().resolve()
    return Path.home() / ".agent_workspace" / "harness"


@dataclass
class HarnessEntry:
    """A reusable prompt note, operational memory, skill, or subagent configuration."""

    id: str
    kind: HarnessKind
    title: str
    content: str
    path: str = "general"
    scope: HarnessScope = "local"
    reference: dict[str, Any] = field(default_factory=dict)
    arguments: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "agent"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    version: int = 1
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HarnessEntry:
        known_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class RefinementEvent:
    """A recorded online harness-refinement pass."""

    id: str
    trigger: str
    changes: list[str]
    evidence: str = ""
    outcome: str = ""
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RefinementEvent:
        known_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


class HarnessState:
    """CRUD store and prompt synthesizer for reset-free continual harness state."""

    def __init__(
        self,
        file_path: str | Path | None = None,
        *,
        in_memory: bool = False,
        scope: HarnessScope = "local",
    ):
        if in_memory:
            self.file_path: Path | None = None
        elif file_path:
            self.file_path = Path(file_path).expanduser().resolve()
        elif scope == "global":
            self.file_path = get_default_global_dir() / _DEFAULT_FILE_NAME
        else:
            self.file_path = Path.cwd() / _DEFAULT_DIR_NAME / _DEFAULT_FILE_NAME

        self.scope: HarnessScope = scope
        self.entries: dict[HarnessKind, dict[str, HarnessEntry]] = {kind: {} for kind in _KINDS}
        self.refinements: list[RefinementEvent] = []
        self._loaded_mtime: int | None = None
        self.load()

    def _disk_mtime(self) -> int | None:
        if self.file_path is None or not self.file_path.exists():
            return None
        try:
            return self.file_path.stat().st_mtime_ns
        except OSError:
            return None

    def sync_from_disk(self) -> None:
        """Reload if modified externally."""
        current_mtime = self._disk_mtime()
        if current_mtime is not None and current_mtime != self._loaded_mtime:
            self.load()

    def load(self) -> None:
        if self.file_path is None or not self.file_path.exists():
            return
        try:
            with open(self.file_path, encoding="utf-8") as f:
                data = json.load(f)

            self.entries = {kind: {} for kind in _KINDS}
            for kind in _KINDS:
                for item in data.get("entries", {}).get(kind, []):
                    entry = HarnessEntry.from_dict(item)
                    self.entries[kind][entry.id] = entry

            self.refinements = [
                RefinementEvent.from_dict(r) for r in data.get("refinements", [])
            ]
            self._loaded_mtime = self._disk_mtime()
        except Exception:
            pass

    def save(self) -> None:
        if self.file_path is None:
            return
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "scope": self.scope,
            "entries": {
                kind: [entry.to_dict() for entry in self.entries[kind].values()]
                for kind in _KINDS
            },
            "refinements": [r.to_dict() for r in self.refinements],
            "updated_at": _now(),
        }
        temp_file = self.file_path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        temp_file.replace(self.file_path)
        self._loaded_mtime = self._disk_mtime()

    def add_entry(
        self,
        kind: HarnessKind,
        title: str,
        content: str,
        *,
        entry_id: str | None = None,
        path: str = "general",
        reference: dict[str, Any] | None = None,
        arguments: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        source: str = "agent",
    ) -> HarnessEntry:
        if kind not in _KINDS:
            raise ValueError(f"Invalid harness kind: {kind}. Expected one of {_KINDS}")
        eid = entry_id or f"{kind}_{_slug(title, uuid4().hex[:8])}"
        entry = HarnessEntry(
            id=eid,
            kind=kind,
            title=title,
            content=content,
            path=path,
            scope=self.scope,
            reference=reference or {},
            arguments=arguments or {},
            metadata=metadata or {},
            source=source,
        )
        self.entries[kind][eid] = entry
        self.save()
        return entry

    def update_entry(
        self,
        entry_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> HarnessEntry | None:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None
        if title is not None:
            entry.title = title
        if content is not None:
            entry.content = content
        if metadata is not None:
            entry.metadata.update(metadata)
        if enabled is not None:
            entry.enabled = enabled
        entry.version += 1
        entry.updated_at = _now()
        self.save()
        return entry

    def get_entry(self, entry_id: str) -> HarnessEntry | None:
        for kind in _KINDS:
            if entry_id in self.entries[kind]:
                return self.entries[kind][entry_id]
        return None

    def remove_entry(self, entry_id: str) -> bool:
        for kind in _KINDS:
            if entry_id in self.entries[kind]:
                del self.entries[kind][entry_id]
                self.save()
                return True
        return False

    def list_entries(
        self,
        kind: HarnessKind | None = None,
        *,
        enabled_only: bool = True,
    ) -> list[HarnessEntry]:
        self.sync_from_disk()
        result: list[HarnessEntry] = []
        target_kinds = (kind,) if kind else _KINDS
        for k in target_kinds:
            for entry in self.entries[k].values():
                if not enabled_only or entry.enabled:
                    result.append(entry)
        return result

    def record_refinement(
        self,
        trigger: str,
        changes: list[str],
        evidence: str = "",
        outcome: str = "",
    ) -> RefinementEvent:
        event = RefinementEvent(
            id=f"refine_{uuid4().hex[:12]}",
            trigger=trigger,
            changes=changes,
            evidence=evidence,
            outcome=outcome,
        )
        self.refinements.append(event)
        self.save()
        return event

    def format_for_prompt(self) -> str:
        """Format active harness entries into a concise system prompt block."""
        self.sync_from_disk()
        sections: list[str] = []

        prompts = self.list_entries("prompt", enabled_only=True)
        if prompts:
            sections.append("### Learned Operating Directives & Prompt Notes:")
            for p in prompts:
                sections.append(f"- **{p.title}**: {p.content}")

        memories = self.list_entries("memory", enabled_only=True)
        if memories:
            sections.append("### Project-Specific Memories & Failure Rules:")
            for m in memories:
                sections.append(f"- **{m.title}**: {m.content}")

        skills = self.list_entries("skill", enabled_only=True)
        if skills:
            sections.append("### Specialized Executable Skills:")
            for s in skills:
                sections.append(f"- **{s.title}**: {s.content}")

        subagents = self.list_entries("subagent", enabled_only=True)
        if subagents:
            sections.append("### Refined Subagent Profiles:")
            for sub in subagents:
                sections.append(f"- **{sub.title}**: {sub.content}")

        if not sections:
            return ""

        header = "## Continual Harness Knowledge (Self-Refined State)"
        return f"{header}\n" + "\n".join(sections)
