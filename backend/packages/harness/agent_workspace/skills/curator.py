"""Skill curator: background maintenance for agent-created skills.

Lifecycle: active -> stale -> archived (moved to ``.archive/``, recoverable).
Invariants: only agent-created skills are touched; never delete, only
archive; pinned skills bypass all auto-transitions; protected builtins are
exempt regardless of pins. Deterministic prune always runs; LLM
consolidation stays opt-in at the caller.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent_workspace.skills.usage import SkillUsageTracker

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_HOURS = 24 * 7.0
DEFAULT_MIN_IDLE_HOURS = 2.0

SkillState = Literal["active", "stale", "archived"]

#: Load-bearing built-ins the curator must NEVER archive/consolidate.
PROTECTED_BUILTIN_SKILLS: set[str] = set()


def is_protected_builtin(skill_name: str) -> bool:
    return skill_name in PROTECTED_BUILTIN_SKILLS


def _skills_root() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "skills"
    except Exception:
        return Path.cwd() / ".agent_workspace" / "skills"


@dataclass
class CuratorState:
    states: dict[str, SkillState] = field(default_factory=dict)
    pinned: list[str] = field(default_factory=list)
    last_run_at: float | None = None
    last_summary: str | None = None
    run_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CuratorState:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        states = {k: v for k, v in filtered.get("states", {}).items() if v in ("active", "stale", "archived")}
        filtered["states"] = states
        return cls(**filtered)


class SkillCurator:
    def __init__(self, skills_root: str | Path | None = None, usage: SkillUsageTracker | None = None):
        self.root = Path(skills_root).resolve() if skills_root else _skills_root()
        self.usage = usage or SkillUsageTracker(usage_file=self.root / ".usage.json")
        self._state_file = self.root / ".curator.json"
        self._lock = threading.Lock()
        self.state = self._load_state()

    def _load_state(self) -> CuratorState:
        if not self._state_file.exists():
            return CuratorState()
        try:
            return CuratorState.from_dict(json.loads(self._state_file.read_text(encoding="utf-8")))
        except Exception:
            logger.warning("Curator state load failed; starting fresh", exc_info=True)
            return CuratorState()

    def _save_state(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state.to_dict(), indent=2), encoding="utf-8")
            tmp.replace(self._state_file)
        except Exception:
            logger.warning("Curator state save failed", exc_info=True)

    def known_skills(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and not p.name.startswith(".") and (p / "SKILL.md").exists())

    def pin(self, skill_name: str) -> None:
        with self._lock:
            if skill_name not in self.state.pinned:
                self.state.pinned.append(skill_name)
            self._save_state()

    def unpin(self, skill_name: str) -> None:
        with self._lock:
            if skill_name in self.state.pinned:
                self.state.pinned.remove(skill_name)
            self._save_state()

    def apply_transitions(self, *, stale_after_days: float = 14.0, archive_after_days: float = 30.0, now: float | None = None, dry_run: bool = False) -> dict[str, list[str]]:
        """Deterministic prune: stale then archive. Dry-run computes without mutating."""
        moment = now if now is not None else time.time()
        changed: dict[str, list[str]] = {"staled": [], "archived": []}
        stale_cutoff = moment - stale_after_days * 86400.0
        archive_cutoff = moment - archive_after_days * 86400.0
        with self._lock:
            for name in self.known_skills():
                if name in self.state.pinned or is_protected_builtin(name):
                    continue
                stats = self.usage.stats(name)
                if stats is None or stats.created_by != "agent":
                    continue
                last = stats.last_used_at or 0.0
                current = self.state.states.get(name, "active")
                if current == "active" and last < stale_cutoff:
                    if not dry_run:
                        self.state.states[name] = "stale"
                    changed["staled"].append(name)
                elif current == "stale" and last < archive_cutoff:
                    if dry_run:
                        changed["archived"].append(name)
                    elif self._archive_skill(name):
                        self.state.states[name] = "archived"
                        changed["archived"].append(name)
            if not dry_run:
                self.state.last_run_at = moment
                self.state.run_count += 1
                self.state.last_summary = f"staled={len(changed['staled'])} archived={len(changed['archived'])}"
                self._save_state()
        return changed

    def _archive_skill(self, skill_name: str) -> bool:
        src = self.root / skill_name
        dest = self.root / ".archive" / skill_name
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(src), str(dest))
            return True
        except Exception:
            logger.warning("Skill archive failed for %s", skill_name, exc_info=True)
            return False

    def restore(self, skill_name: str) -> bool:
        """Recover an archived skill back to active."""
        src = self.root / ".archive" / skill_name
        dest = self.root / skill_name
        with self._lock:
            try:
                if not src.exists() or dest.exists():
                    return False
                shutil.move(str(src), str(dest))
                self.state.states[skill_name] = "active"
                self._save_state()
                return True
            except Exception:
                logger.warning("Skill restore failed for %s", skill_name, exc_info=True)
                return False

    def collect_skill_bodies(self, *, max_chars: int = 4000) -> dict[str, str]:
        """Read agent-created SKILL.md bodies for merge analysis (bounded)."""
        bodies: dict[str, str] = {}
        with self._lock:
            pinned = set(self.state.pinned)
        for name in self.known_skills():
            if name in pinned or is_protected_builtin(name):
                continue
            stats = self.usage.stats(name)
            if stats is None or stats.created_by != "agent":
                continue
            try:
                text = (self.root / name / "SKILL.md").read_text(encoding="utf-8")[:max_chars]
            except OSError:
                continue
            bodies[name] = text
        return bodies

    def suggest_merges(self, *, similarity: float = 0.55) -> list[list[str]]:
        return find_consolidation_candidates(self.collect_skill_bodies(), similarity=similarity)

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                "known": self.known_skills(),
                "states": dict(self.state.states),
                "pinned": list(self.state.pinned),
                "last_run_at": self.state.last_run_at,
                "last_summary": self.state.last_summary,
                "run_count": self.state.run_count,
            }


def run_curator_maintenance(*, stale_after_days: float = 14.0, archive_after_days: float = 30.0, dry_run: bool = False) -> dict[str, Any]:
    """One scheduled maintenance pass: gate on cadence, then prune.

    The single callable a scheduled task invokes (e.g. the ``skill-curator``
    blueprint). Returns the transition report; dry-run reports without
    mutating anything.
    """
    from agent_workspace.skills.usage import get_skill_usage_tracker

    curator = SkillCurator(usage=get_skill_usage_tracker())
    if not should_run_curator(curator.state.last_run_at):
        return {"skipped": True, "reason": "cadence interval has not elapsed", "report": curator.report()}
    changed = curator.apply_transitions(stale_after_days=stale_after_days, archive_after_days=archive_after_days, dry_run=dry_run)
    return {"skipped": False, "dry_run": dry_run, "transitions": changed, "report": curator.report()}


def should_run_curator(
    last_run_at: float | None,
    *,
    now: float | None = None,
    interval_hours: float = DEFAULT_INTERVAL_HOURS,
    idle_hours: float | None = None,
    min_idle_hours: float = DEFAULT_MIN_IDLE_HOURS,
    paused: bool = False,
) -> bool:
    """Inactivity-triggered scheduling: due interval + quiet machine + not paused."""
    if paused:
        return False
    moment = now if now is not None else time.time()
    if interval_hours <= 0:
        return True
    if last_run_at is not None and (moment - last_run_at) < interval_hours * 3600.0:
        return False
    if idle_hours is not None and idle_hours < min_idle_hours:
        return False
    return True


def curator_interval_hours() -> float:
    """Operator override via env; defaults to weekly."""
    try:
        return max(0.0, float(os.environ.get("AGENT_WORKSPACE_CURATOR_INTERVAL_HOURS", DEFAULT_INTERVAL_HOURS)))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_HOURS


def _tokens(text: str) -> set[str]:
    return {t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(t) > 2}


def find_consolidation_candidates(skills: dict[str, str], *, similarity: float = 0.55) -> list[list[str]]:
    """Group agent-skill bodies that overlap heavily (Jaccard over tokens).

    Returns groups of 2+ names worth one merge proposal each. Deterministic
    and dependency-free; the LLM rewrite itself stays caller opt-in.
    """
    names = sorted(skills)
    tokenized = {n: _tokens(skills[n]) for n in names}
    grouped: set[str] = set()
    out: list[list[str]] = []
    for i, a in enumerate(names):
        if a in grouped:
            continue
        group = [a]
        for b in names[i + 1 :]:
            if b in grouped:
                continue
            ta, tb = tokenized[a], tokenized[b]
            union = ta | tb
            score = (len(ta & tb) / len(union)) if union else 0.0
            if score >= similarity:
                group.append(b)
                grouped.add(b)
        if len(group) > 1:
            grouped.add(a)
            out.append(group)
    return out


def propose_consolidations(groups: list[list[str]], propose_fn: Callable[[str, str], Any]) -> list[Any]:
    """File one merge proposal per duplicate group via the caller's queue."""
    made: list[Any] = []
    for group in groups:
        title = f"Consolidate overlapping skills: {', '.join(group)}"
        body = (
            "These agent-created skills overlap heavily. Merge them into ONE skill: keep the best "
            "procedure steps from each, dedupe triggers, keep a single Verification section, and follow "
            "the house authoring bar. Archive the losers after the merged skill passes review.\n\nSkills:\n" + "\n".join(f"- {name}" for name in group)
        )
        made.append(propose_fn(title, body))
    return made
