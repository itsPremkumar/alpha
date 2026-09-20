"""Dynamic Bot Registry with Zero-Config Auto-Provisioning.

Manages active bot profiles with automatic provisioning when an unknown bot
is messaged or @mentioned.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from alpha.bots.profile import (
    BotProfile,
    _now,
    generate_default_soul,
    generate_sentinel_soul,
)
from alpha.bots.templates import BOT_STATUSES, get_template

logger = logging.getLogger(__name__)

_DEFAULT_BOT_DIR = "bots"

#: The Sentinel's scheduled repair pass, registered on provisioning so the loop
#: has a trigger rather than only being runnable by hand.
SENTINEL_OBSERVE_ROUTINE = "sentinel-observe"
SENTINEL_OBSERVE_SCHEDULE = "*/5 * * * *"


def _default_storage_path() -> Path:
    """Resolve the bot roster file under the writable runtime home.

    Uses AGENT_WORKSPACE_HOME (via runtime_home()) so Gateway, Electron, Docker,
    and embedded clients share one location instead of CWD at import time.
    Falls back to CWD only when runtime_home() is unusable.
    """
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / _DEFAULT_BOT_DIR / "roster.json"
    except Exception:
        return Path.cwd() / ".alpha" / _DEFAULT_BOT_DIR / "roster.json"


def _infer_role_from_name(name: str) -> str:
    """Infer sensible role and specialties based on name slug."""
    n = name.lower()
    if "arch" in n:
        return "System Architect & Technical Lead"
    elif "review" in n:
        return "Code & Quality Reviewer"
    elif "test" in n or "qa" in n:
        return "QA & Automated Verification Specialist"
    elif "sec" in n:
        return "Security & Vulnerability Analyst"
    elif "data" in n or "sql" in n:
        return "Data Engineer & Database Specialist"
    elif "front" in n or "ui" in n:
        return "Frontend & Design Specialist"
    elif "devops" in n or "ops" in n:
        return "DevOps & Infrastructure Engineer"
    elif "research" in n:
        return "Deep Researcher & Synthesis Specialist"
    elif "code" in n or "dev" in n:
        return "Software Engineer & Backend Developer"
    return "Autonomous Specialist Teammate"


class BotRegistry:
    """Thread-safe registry for autonomous Bot profiles with auto-provisioning."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._bots: dict[str, BotProfile] = {}
        self._lock = threading.Lock()
        self._load()

        # Ensure default foundational team exists
        self._ensure_default_roster()

    def _ensure_default_roster(self) -> None:
        # Default seed roster: the highest-ROI starter team, built from the
        # template catalog so display/role/skills stay in one place.
        seeds = [
            "architect",
            "coder",
            "reviewer",
            "tester",
            "researcher",
            "support",
            "data-analyst",
            "technical-writer",
        ]
        with self._lock:
            for slug in seeds:
                if slug in self._bots:
                    continue
                spec = get_template(slug)
                if spec is None:
                    continue
                # The Sentinel is the one bot permitted to change code and
                # commit unattended, so it gets a soul encoding the repair
                # loop's safety contract rather than the generic one.
                if slug == "sentinel":
                    soul = generate_sentinel_soul(slug)
                else:
                    soul = generate_default_soul(slug, spec["role"])
                bot = BotProfile(
                    name=slug,
                    display_name=spec["display"],
                    role=spec["role"],
                    soul=soul,
                    toolsets=list(spec.get("toolsets", [])) or ["all"],
                    skills=list(spec.get("skills", [])),
                    avatar=spec.get("avatar", ""),
                    department=spec.get("department", "engineering"),
                    reports_to=spec.get("reports_to"),
                    responsibilities=list(spec.get("responsibilities", [])),
                    capabilities=list(spec.get("capabilities", [])),
                )
                self._apply_sentinel_defaults(bot)
                self._bots[slug] = bot
            self._save()

    @staticmethod
    def _apply_sentinel_defaults(bot: "BotProfile") -> None:
        """Give the Sentinel its own memory namespace and its scheduled pass.

        Called whenever a Sentinel profile is created, by any path, so the bot
        never ends up as a half-configured shell: the repair loop needs a
        trigger, and its repair history should not mix into the shared pool.

        Idempotent — re-provisioning does not duplicate the routine.
        """
        if bot.name != "sentinel":
            return
        if not bot.memory_scope:
            bot.memory_scope = "sentinel"
        if bot.get_routine(SENTINEL_OBSERVE_ROUTINE) is None:
            bot.add_routine(
                SENTINEL_OBSERVE_ROUTINE,
                SENTINEL_OBSERVE_SCHEDULE,
                "sentinel.run_once",
                description="Scan for faults, repair bounded set, verify, commit",
            )

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("bots", []):
                profile = BotProfile.from_dict(item)
                self._bots[profile.name.lower()] = profile
        except Exception:
            logger.warning("Bot roster load failed; starting from defaults", exc_info=True)

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "bots": [b.to_dict() for b in self._bots.values()],
                "updated_at": _now(),
            }
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Bot roster save failed", exc_info=True)

    def get_bot(self, name: str) -> BotProfile | None:
        with self._lock:
            return self._bots.get(name.lower().strip())

    def get_or_create(
        self,
        name: str,
        display_name: str | None = None,
        role: str | None = None,
        soul: str | None = None,
        *,
        template: str | None = None,
        avatar: str | None = None,
        department: str | None = None,
        reports_to: str | None = None,
        responsibilities: list[str] | None = None,
        capabilities: list[str] | None = None,
        skills: list[str] | None = None,
        toolsets: list[str] | None = None,
        succession_fallback: str | None = None,
    ) -> BotProfile:
        """Fetch an existing bot or instantly auto-provision a new one.

        A ``template`` slug (see :mod:`alpha.bots.templates`) supplies the
        role/display/avatar/department/reports_to/skills/toolsets defaults for
        brand-new bots; explicit arguments always win over the template.
        Existing bots are returned untouched.
        """
        key = name.lower().strip()
        # Fall back to the bot's own name when no template was passed. Canonical
        # slugs (coder, reviewer, sentinel, ...) must resolve to their catalog
        # entry even without an explicit template=. Without this,
        # get_or_create("sentinel") silently produced a GENERIC bot: right name,
        # but a placeholder role, the default department and the generic SOUL
        # instead of the Sentinel safety contract. Found only by exercising the
        # real /api/bots/sentinel/ensure endpoint, not by unit tests.
        resolved_template = template or key
        spec = get_template(resolved_template)
        if template and spec is None:
            raise ValueError(f"Unknown bot template '{template}'.")
        with self._lock:
            if key in self._bots:
                return self._bots[key]

            # Auto-provision new bot on demand
            assigned_role = role or (spec["role"] if spec else None) or _infer_role_from_name(key)
            assigned_display = display_name or (spec["display"] if spec else None) or key.capitalize()
            # The Sentinel is the one bot allowed to change code and commit
            # unattended, so it must never receive the generic SOUL. Same rule
            # as _ensure_default_roster, applied here because get_or_create is
            # the path the /api/bots/{name}/ensure endpoint uses.
            if soul:
                assigned_soul = soul
            elif resolved_template == "sentinel":
                assigned_soul = generate_sentinel_soul(key)
            else:
                assigned_soul = generate_default_soul(key, assigned_role)
            assigned_avatar = avatar if avatar is not None else (spec["avatar"] if spec else "")
            assigned_dept = department or (spec.get("department") if spec else "engineering")
            assigned_reports = reports_to or (spec.get("reports_to") if spec else None)
            assigned_resps = responsibilities or (list(spec.get("responsibilities", [])) if spec else [])
            assigned_caps = capabilities or (list(spec.get("capabilities", [])) if spec else [])
            assigned_skills = skills if skills is not None else (list(spec.get("skills", [])) if spec else [])
            assigned_toolsets = toolsets if toolsets is not None else (list(spec.get("toolsets", [])) if spec else ["all"])

            bot = BotProfile(
                name=key,
                display_name=assigned_display,
                role=assigned_role,
                soul=assigned_soul,
                toolsets=assigned_toolsets,
                skills=assigned_skills,
                avatar=assigned_avatar,
                department=assigned_dept,
                reports_to=assigned_reports,
                responsibilities=assigned_resps,
                capabilities=assigned_caps,
                succession_fallback=succession_fallback,
            )
            self._apply_sentinel_defaults(bot)
            self._bots[key] = bot
            self._save()
            return bot

    def clone_bot(
        self,
        source: str,
        name: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        model: str | None = None,
        department: str | None = None,
        reports_to: str | None = None,
    ) -> BotProfile:
        """Create a bot from another profile (inventory #24).

        Copies configuration, skills, SOUL, and avatar — but never memory:
        clones start with a fresh identity. Raises ``KeyError`` when the
        source is missing and ``ValueError`` when the target name is taken.
        """
        source_key = source.lower().strip()
        key = name.lower().strip()
        with self._lock:
            template = self._bots.get(source_key)
            if template is None:
                raise KeyError(f"Bot '{source_key}' not found")
            if key in self._bots:
                raise ValueError(f"Bot '{key}' already exists")
            bot = BotProfile(
                name=key,
                display_name=display_name or f"{template.display_name} copy",
                role=role or template.role,
                soul=template.soul,
                model=model if model is not None else template.model,
                toolsets=list(template.toolsets),
                skills=list(template.skills),
                avatar=template.avatar,
                department=department or template.department,
                reports_to=reports_to or template.reports_to,
                responsibilities=list(template.responsibilities),
                capabilities=list(template.capabilities),
                succession_fallback=template.succession_fallback,
            )
            self._bots[key] = bot
            self._save()
            return bot

    def update_bot(
        self,
        name: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        soul: str | None = None,
        model: str | None = None,
        toolsets: list[str] | None = None,
        skills: list[str] | None = None,
        avatar: str | None = None,
        status: str | None = None,
        last_active: str | None = None,
        department: str | None = None,
        reports_to: str | None = None,
        responsibilities: list[str] | None = None,
        capabilities: list[str] | None = None,
        heartbeat: str | None = None,
        succession_fallback: str | None = None,
        reputation_score: float | None = None,
        task_stats: dict[str, Any] | None = None,
        routines: list[dict[str, Any]] | None = None,
        bump_version: bool = True,
    ) -> BotProfile | None:
        key = name.lower().strip()
        if status is not None and status not in BOT_STATUSES:
            raise ValueError(f"Invalid bot status '{status}'. Expected one of {list(BOT_STATUSES)}.")
        with self._lock:
            bot = self._bots.get(key)
            if not bot:
                return None
            if display_name is not None:
                bot.display_name = display_name
            if role is not None:
                bot.role = role
            if soul is not None:
                bot.soul = soul
            if model is not None:
                bot.model = model
            if toolsets is not None:
                bot.toolsets = toolsets
            if skills is not None:
                bot.skills = skills
            if avatar is not None:
                bot.avatar = avatar
            if status is not None:
                bot.status = status
            if last_active is not None:
                bot.last_active = last_active
            if department is not None:
                bot.department = department
            if reports_to is not None:
                bot.reports_to = reports_to
            if responsibilities is not None:
                bot.responsibilities = responsibilities
            if capabilities is not None:
                bot.capabilities = capabilities
            if heartbeat is not None:
                bot.heartbeat = heartbeat
            if succession_fallback is not None:
                bot.succession_fallback = succession_fallback
            if reputation_score is not None:
                bot.reputation_score = max(0.0, min(1.0, float(reputation_score)))
            if task_stats is not None:
                bot.task_stats = task_stats
            if routines is not None:
                bot.routines = routines
            if bump_version:
                bot.version += 1
            bot.updated_at = _now()
            self._save()
            return bot

    def add_routine(
        self,
        name: str,
        routine_name: str,
        schedule: str,
        action: str,
        *,
        enabled: bool = True,
        **extra: Any,
    ) -> dict[str, Any] | None:
        """Add or replace a routine on a bot. None when the bot does not exist."""
        key = name.lower().strip()
        with self._lock:
            bot = self._bots.get(key)
            if bot is None:
                return None
            routine = bot.add_routine(
                routine_name, schedule, action, enabled=enabled, **extra
            )
        self._save()
        return routine

    def remove_routine(self, name: str, routine_name: str) -> bool:
        """Remove a routine from a bot. False when either does not exist."""
        key = name.lower().strip()
        with self._lock:
            bot = self._bots.get(key)
            if bot is None:
                return False
            removed = bot.remove_routine(routine_name)
        if removed:
            self._save()
        return removed

    def list_bots(
        self,
        *,
        status: str | None = None,
        department: str | None = None,
        include_archived: bool = True,
    ) -> list[BotProfile]:
        """List bots.

        ``include_archived`` defaults to True to preserve historical behaviour.
        Callers that build a roster, org chart or routing table should pass
        False — several did not filter at all and would otherwise offer work to
        retired bots.
        """
        with self._lock:
            bots = list(self._bots.values())
        if not include_archived:
            bots = [b for b in bots if not b.is_retired]
        if status is not None:
            bots = [b for b in bots if b.status == status]
        if department is not None:
            bots = [b for b in bots if b.department.lower() == department.lower()]
        return bots

    def retire_bot(self, name: str) -> BotProfile | None:
        """Retire a bot by name (soft delete).

        The registry had no delete path at all, so bots were effectively
        immortal. Retirement archives the profile in place — history, stats and
        reputation are preserved so past runs stay auditable.

        Returns the retired profile, or None when no such bot exists.
        Retiring an already-retired bot is a no-op that still returns it.
        """
        key = name.lower().strip()
        with self._lock:
            bot = self._bots.get(key)
            if bot is None:
                return None
            bot.retire()
        self._save()
        return bot

    def get_by_department(self, department: str) -> list[BotProfile]:
        return self.list_bots(department=department)

    def get_subordinates(self, manager_name: str) -> list[BotProfile]:
        m = manager_name.lower().strip()
        with self._lock:
            return [b for b in self._bots.values() if (b.reports_to or "").lower() == m]


_global_registry: BotRegistry | None = None
_global_registry_path: str | None = None


def get_bot_registry(storage_path: str | Path | None = None) -> BotRegistry:
    """Return the process-wide bot registry.

    When an explicit storage_path is given and differs from the cached
    singleton's path, a fresh instance bound to that path is returned so
    Gateway requests (AGENT_WORKSPACE_HOME-aware) never reuse a stale CWD-bound
    registry created at import time.
    """
    global _global_registry, _global_registry_path
    if storage_path is not None:
        resolved = str(Path(storage_path).resolve())
        if _global_registry is None or _global_registry_path != resolved:
            _global_registry = BotRegistry(storage_path=resolved)
            _global_registry_path = resolved
        return _global_registry
    if _global_registry is None:
        _global_registry = BotRegistry()
        try:
            _global_registry_path = str(_global_registry.storage_path.resolve())
        except Exception:
            _global_registry_path = None
        return _global_registry
    # Re-resolve the default location: env (AGENT_WORKSPACE_HOME) may have been set
    # after import. If it moved, rebuild so we read/write the live location.
    try:
        live = str(_default_storage_path().resolve())
    except Exception:
        return _global_registry
    if _global_registry_path != live:
        _global_registry = BotRegistry()
        _global_registry_path = live
    return _global_registry
