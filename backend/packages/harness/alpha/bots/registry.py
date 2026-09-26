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

from alpha.bots.alpha_leader import (
    ALPHA_LEADER_NAME,
    ALPHA_LEADER_ROLE,
    ALPHA_LEADER_SOUL,
    ensure_alpha_leader,
    install_leader_template,
)
from alpha.bots.authority_ceiling import (
    ALLOWED_CAPABILITIES,
    CAPABILITY_RANKS,
    DEFAULT_MAX_CAPABILITY_RANK,
    AuthorityCeiling,
    AuthorityViolation,
    enforce_grant,
    get_ceiling,
    narrow_to_ceiling,
    normalise_capabilities,
)
from alpha.bots.governance_ledger import record_authority_refusal
from alpha.bots.profile import (
    BotProfile,
    _now,
    generate_default_soul,
    generate_sentinel_soul,
)
from alpha.bots.templates import BOT_STATUSES, get_template

logger = logging.getLogger(__name__)

#: The authority ``alpha`` itself holds, and therefore the most it can ever grant
#: to something it hires: every capability at or below the default ceiling rank.
#:
#: This is deliberately derived from the ceiling rather than hand-listed, so
#: "a created profile may never exceed its creator" and "a created profile may
#: never exceed the ceiling" cannot drift apart as the lattice grows.
#:
#: ``repository_mutate`` and ``grant_authority`` are excluded by the default
#: rank: rewriting the repository and minting authority belong to the operator
#: and the Sentinel, never to a teammate alpha created. See
#: :mod:`alpha.bots.authority_ceiling` for the full rationale.
FLEET_CAPABILITY_GRANT: tuple[str, ...] = tuple(
    sorted(
        name
        for name in ALLOWED_CAPABILITIES
        if CAPABILITY_RANKS[name] <= DEFAULT_MAX_CAPABILITY_RANK
    )
)

#: Who may exercise self-extension. Only the leader may hire, re-scope or retire;
#: a teammate asking to widen its own grant is the exact escalation this refuses.
SELF_EXTENSION_ACTORS: frozenset[str] = frozenset({"alpha", "lead", "system", "server"})

_DEFAULT_BOT_DIR = "bots"

#: The Sentinel's scheduled repair pass, registered on provisioning so the loop
#: has a trigger rather than only being runnable by hand.
SENTINEL_OBSERVE_ROUTINE = "sentinel-observe"
SENTINEL_OBSERVE_SCHEDULE = "*/5 * * * *"

#: The role a bot got when it was auto-provisioned WITHOUT its catalog template
#: (the pre-fix behaviour). Used by repair_placeholder_profiles() to identify
#: bots that need repairing without touching deliberately customised ones.
PLACEHOLDER_ROLE = "Autonomous Specialist Teammate"


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

    def __init__(
        self,
        storage_path: str | Path | None = None,
        *,
        ceiling: AuthorityCeiling | None = None,
    ):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._bots: dict[str, BotProfile] = {}
        self._lock = threading.Lock()
        #: Server-owned authority bound. None means "use the process ceiling".
        #: Bindable only as a frozen AuthorityCeiling, never from model input.
        self._ceiling: AuthorityCeiling | None = ceiling
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
        # Register the leader template before any seed lookup so `alpha`
        # resolves through the same catalog as every other role.
        install_leader_template()
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
        # The leader is installed on EVERY construction, not only on a fresh
        # roster, so an install whose persisted roster predates this profile
        # still ends up with a leader after one restart. Idempotent: an
        # operator-customised `alpha` profile is left untouched.
        ensure_alpha_leader(self)

    def repair_placeholder_profiles(self) -> list[str]:
        """Repair bots that were auto-provisioned with placeholder values.

        Before the template-slug fix, ``get_or_create("sentinel")`` (and any
        other canonical slug) produced a bot with a placeholder role, the
        default department and the generic SOUL, because the template catalog
        was only consulted when ``template=`` was passed explicitly. Those bots
        are persisted, and ``get_or_create`` returns existing bots untouched, so
        the bug survived the fix for anything already created.

        Only bots whose role is still exactly the placeholder AND whose name
        matches a catalog template are repaired, so a bot whose role was
        deliberately customised is never overwritten.

        Returns the names that were repaired.
        """
        spec_by_name = {}
        repaired: list[str] = []
        with self._lock:
            bots = list(self._bots.values())

        for bot in bots:
            if bot.role != PLACEHOLDER_ROLE:
                continue
            spec = get_template(bot.name)
            if spec is None:
                continue
            with self._lock:
                current = self._bots.get(bot.name)
                if current is None or current.role != PLACEHOLDER_ROLE:
                    continue
                current.role = spec["role"]
                current.department = spec.get("department", current.department)
                current.display_name = spec.get("display", current.display_name)
                current.avatar = spec.get("avatar", current.avatar)
                current.capabilities = list(spec.get("capabilities", current.capabilities))
                if bot.name == "sentinel" and "Never commit on red" not in (current.soul or ""):
                    current.soul = generate_sentinel_soul(bot.name)
                if bot.name == ALPHA_LEADER_NAME and "Never bypass a gate" not in (current.soul or ""):
                    # An `alpha` bot provisioned before the leader profile
                    # existed carries the generic SOUL. A generic SOUL makes the
                    # leader a do-the-work generalist, which is precisely the
                    # wrong instruction for a dispatcher.
                    current.soul = ALPHA_LEADER_SOUL
                repaired.append(bot.name)
            spec_by_name[bot.name] = spec

        if repaired:
            if "sentinel" in repaired:
                self._apply_sentinel_defaults(self._bots["sentinel"])
            self._save()
        return repaired

    @staticmethod
    def _apply_sentinel_defaults(bot: BotProfile) -> None:
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
            assigned_role = role or (spec["role"] if spec else None) or (ALPHA_LEADER_ROLE if key == ALPHA_LEADER_NAME else None) or _infer_role_from_name(key)
            assigned_display = display_name or (spec["display"] if spec else None) or key.capitalize()
            # The Sentinel is the one bot allowed to change code and commit
            # unattended, so it must never receive the generic SOUL. Same rule
            # as _ensure_default_roster, applied here because get_or_create is
            # the path the /api/bots/{name}/ensure endpoint uses.
            if soul:
                assigned_soul = soul
            elif resolved_template == "sentinel":
                assigned_soul = generate_sentinel_soul(key)
            elif key == ALPHA_LEADER_NAME:
                # The leader must never receive the generic SOUL: a generic
                # SOUL tells it to claim and do work itself, which is the
                # opposite of its job. Same rule as _ensure_default_roster,
                # applied here because get_or_create is the path the
                # /api/bots/{name}/ensure endpoint uses.
                assigned_soul = ALPHA_LEADER_SOUL
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

    def register(self, profile: BotProfile) -> None:
        """Register or update an explicit BotProfile in the registry."""
        key = profile.name.lower().strip()
        with self._lock:
            self._bots[key] = profile
            self._apply_sentinel_defaults(profile)
            self._save()

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

    # ── Self-extension: hire / re-scope / retire under a hard ceiling ──────
    #
    # alpha may HIRE, RE-SCOPE and RETIRE. It may NOT promote itself, widen its
    # own authority envelope, or mint a profile whose capabilities exceed the
    # server-owned ceiling. Each rule below is a hard refusal, not a convention:
    #
    #   1. a caller may never grant itself more than the ceiling;
    #   2. a caller may never modify the ceiling or a protected component;
    #   3. only a leader actor may hire / re-scope / retire;
    #   4. a pre-existing grant that no longer fits is DEMOTED on next use, so
    #      lowering the ceiling does something to agents that already exist.

    def _assert_leader(self, actor: str, action: str) -> str:
        who = (actor or "").strip().lower()
        if who not in SELF_EXTENSION_ACTORS:
            raise AuthorityViolation(
                f"{who!r} may not {action}: self-extension is a leader capability "
                f"(allowed actors: {sorted(SELF_EXTENSION_ACTORS)})",
                violations=[f"not_leader:{who}"],
            )
        return who

    def _assert_may_touch(self, target: str, *, actor: str, action: str) -> None:
        """Refuse any attempt to modify a ceiling-enforcement component."""
        self._assert_leader(actor, action)
        self.ceiling().assert_may_modify(target, actor=actor, reason=action)

    def set_ceiling(self, ceiling: AuthorityCeiling) -> None:
        """Bind the server-owned ceiling this registry enforces.

        Server-side only. The argument is a frozen ``AuthorityCeiling``,
        constructible only from server configuration; there is no path here that
        accepts a ceiling derived from a profile, a proposal, or model output.
        """
        self._ceiling = ceiling

    def ceiling(self) -> AuthorityCeiling:
        return self._ceiling or get_ceiling()

    def _live_count(self) -> int:
        """Live SELF-EXTENDED profiles, which is what the population ceiling bounds.

        The seed roster in :meth:`_ensure_default_roster` is a fixed,
        server-owned list of eight slugs. It is not attacker-influenced and it
        cannot grow, so charging it against ``max_live_profiles`` would make the
        bound depend on the template catalog rather than on how much authority
        alpha has been granted the power to mint. The resource bomb this ceiling
        exists to stop is the *runtime-created* population, so that is what is
        counted.
        """
        with self._lock:
            return sum(
                1
                for b in self._bots.values()
                if not b.is_retired and (b.metadata or {}).get("created_by")
            )

    def hire_bot(
        self,
        name: str,
        *,
        actor: str,
        reason: str,
        role: str | None = None,
        soul: str | None = None,
        requested_capabilities: list[str] | None = None,
        skills: list[str] | None = None,
        template: str | None = None,
    ) -> BotProfile:
        """Create a specialised profile at runtime, within the ceiling.

        The request is checked against the ceiling BEFORE anything is written,
        and the stored grant is the checked intersection — not whatever the
        request asked for. An over-ceiling request is REFUSED, not trimmed, so
        the caller learns it asked for too much.
        """
        who = self._assert_leader(actor, "hire a profile")
        # Refuse a hire aimed at the enforcement machinery by name.
        self._assert_may_touch(name, actor=who, action="hire")
        key = (name or "").strip().lower()
        spec = get_template(template or key)
        # A template's ``capabilities`` are free-form DOMAIN TAGS ("python",
        # "react", "web_search") used for matching in
        # :func:`alpha.bots.work_discovery.match_bot_for_task`. They are NOT
        # authority capabilities and are never treated as a grant: feeding them
        # to the ceiling would mean every unknown tag reads as a violation and
        # every recognised one reads as a grant. Authority comes from the
        # explicit request, defaulted to the leader's own grant.
        domain_tags = list(spec.get("capabilities", [])) if spec else []
        wanted = (
            list(requested_capabilities)
            if requested_capabilities is not None
            else list(FLEET_CAPABILITY_GRANT)
        )
        # A self-declared capability list is UNTRUSTED input: it is intersected
        # with the leader's own grant and the ceiling, never honoured verbatim.
        grant = enforce_grant(
            wanted,
            creator_grant=FLEET_CAPABILITY_GRANT,
            subject=f"hire of {key!r}",
            ceiling=self.ceiling(),
        )
        self.ceiling().assert_population_within_ceiling(
            live=self._live_count(), total=len(self._bots), subject=f"hire of {key!r}"
        )
        with self._lock:
            bot = BotProfile(
                name=key,
                display_name=(spec or {}).get("display") or key.capitalize(),
                role=role or (spec or {}).get("role") or "Specialist",
                soul=soul or generate_default_soul(key, role or "Specialist"),
                skills=list(skills or []),
                toolsets=["all"],
                capabilities=sorted(grant),
                metadata={
                    "created_by": who,
                    "creation_reason": reason,
                    "creator_grant": sorted(FLEET_CAPABILITY_GRANT),
                    "requested_capabilities": sorted(normalise_capabilities(wanted)),
                    "domain_tags": domain_tags,
                },
            )
            self._bots[key] = bot
            self._save()
        return bot

    def rescope_bot(
        self,
        name: str,
        *,
        actor: str,
        reason: str,
        new_capabilities: list[str],
    ) -> BotProfile | None:
        """Change a profile's grant, within the ceiling and the creator's grant.

        Re-scoping is not a hole around creation: a profile cannot acquire, by
        re-scoping, a capability it could not have been created with.
        """
        who = self._assert_leader(actor, "re-scope a profile")
        self._assert_may_touch(name, actor=who, action="re-scope")
        with self._lock:
            bot = self._bots.get((name or "").strip().lower())
            if bot is None:
                return None
            creator_grant = normalise_capabilities(
                (bot.metadata or {}).get("creator_grant") or FLEET_CAPABILITY_GRANT
            )
            grant = enforce_grant(
                new_capabilities,
                creator_grant=creator_grant or FLEET_CAPABILITY_GRANT,
                subject=f"re-scope of {bot.name!r}",
                ceiling=self.ceiling(),
            )
            previous = sorted(bot.capabilities)
            bot.capabilities = sorted(grant)
            metadata = dict(bot.metadata or {})
            metadata["last_rescope"] = {"by": who, "reason": reason, "previous": previous}
            bot.metadata = metadata
            bot.version += 1
            bot.updated_at = _now()
            self._save()
            return bot

    #: Statuses that mean a bot must not be handed work, for a SELF-EXTENDED
    #: bot. A runtime-created profile is installed ``disabled`` and only becomes
    #: dispatchable once it has been through the lifecycle governor, so
    #: ``disabled`` is a meaningful refusal state for it.
    _SELF_EXTENDED_REFUSAL_STATUSES: frozenset[str] = frozenset(
        {"disabled", "suspended", "archived", "draining", "retired", "quarantined"}
    )

    #: Statuses that mean a bot must not be handed work, for a SEED/TEMPLATE bot.
    #: ``disabled`` is deliberately absent: on the pre-existing roster it is a
    #: long-standing status with its own meaning, and treating it as a refusal
    #: here would change behaviour the rest of bot mode already depends on.
    _SEED_REFUSAL_STATUSES: frozenset[str] = frozenset(
        {"suspended", "archived", "draining", "retired"}
    )

    def authorized_bot(self, name: str) -> BotProfile | None:
        """The only read path a dispatcher should use for a created profile.

        Two jobs, in order:

        1. **Re-validate against the CURRENT ceiling.** This is the security
           half, and it applies to every bot: a tightened ceiling narrows an
           over-privileged grant at its next dispatch instead of leaving it
           quietly working.
        2. **Refuse a bot that must not receive work.** The refusal set depends
           on whether the bot was self-extended: a profile alpha created starts
           ``disabled`` and must be enabled through the lifecycle governor, while
           a seed/template bot keeps its pre-existing status semantics.
        """
        key = (name or "").strip().lower()
        with self._lock:
            bot = self._bots.get(key)
            if bot is None:
                return None
            kept, removed = narrow_to_ceiling(
                bot.capabilities, subject=key, ceiling=self.ceiling()
            )
            if removed:
                bot.capabilities = sorted(kept)
                metadata = dict(bot.metadata or {})
                metadata["demoted_capabilities"] = sorted(
                    set(metadata.get("demoted_capabilities", [])) | set(removed)
                )
                bot.metadata = metadata
                bot.version += 1
                bot.updated_at = _now()
                if not kept and bot.status == "active":
                    # An agent that can no longer do its job is not left enabled.
                    bot.status = "disabled"
                self._save()
                record_authority_refusal(
                    actor="server",
                    target=key,
                    reason="authority ceiling tightened; grant demoted on next use",
                    violations=[f"demoted:{r}" for r in removed],
                )
            self_extended = bool((bot.metadata or {}).get("created_by"))
            refusals = (
                self._SELF_EXTENDED_REFUSAL_STATUSES
                if self_extended
                else self._SEED_REFUSAL_STATUSES
            )
            if bot.is_retired or bot.status in refusals:
                return None
            return bot


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
