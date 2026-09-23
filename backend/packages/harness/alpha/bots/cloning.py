"""Advanced Bot Cloning, Forking, and Evolutionary Breeding Engine.

Enables dynamic workflows and agents to clone, fork, and mutate Bot Profiles at runtime:
- EXACT_COPY: Duplicates a bot with an isolated memory namespace and scratchpad.
- SPECIALIST_FORK: Clones base bot with task-specific SOUL directives, domain skills, and finite TTL leases.
- ENHANCED_MUTATION: Evolves bot capabilities, model tier, and behavioral instructions based on execution telemetry.
"""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum
from typing import Any

from alpha.bots.ephemeral import EphemeralBotManager, get_ephemeral_manager
from alpha.bots.profile import BotProfile
from alpha.bots.registry import BotRegistry, get_bot_registry

logger = logging.getLogger(__name__)


class CloneMode(StrEnum):
    EXACT_COPY = "exact_copy"
    SPECIALIST_FORK = "specialist_fork"
    ENHANCED_MUTATION = "enhanced_mutation"


class BotCloneEngine:
    """Manages runtime cloning, specialist forking, and generational breeding of bots."""

    def __init__(
        self,
        registry: BotRegistry | None = None,
        ephemeral_mgr: EphemeralBotManager | None = None,
    ) -> None:
        self.registry = registry or get_bot_registry()
        self.ephemeral_mgr = ephemeral_mgr or get_ephemeral_manager()

    def clone_bot(
        self,
        source_name: str,
        target_name: str | None = None,
        mode: CloneMode = CloneMode.SPECIALIST_FORK,
        specialist_directive: str | None = None,
        skills_to_add: list[str] | None = None,
        tools_to_add: list[str] | None = None,
        model_override: str | None = None,
        department: str | None = None,
        ttl_seconds: int = 3600,
    ) -> BotProfile:
        """Clone or fork a bot profile with inherited lineage and specialized directives."""
        source_profile = self.registry.get_bot(source_name)
        if not source_profile:
            # Check builtin templates
            from alpha.bots.templates import BOT_TEMPLATES, generate_default_soul

            tmpl = BOT_TEMPLATES.get(source_name)
            if tmpl:
                source_profile = BotProfile(
                    name=source_name,
                    display_name=tmpl.get("display", source_name),
                    role=tmpl.get("role", "Specialist"),
                    soul=generate_default_soul(source_name, tmpl.get("role", "Specialist")),
                    department=tmpl.get("department", "engineering"),
                    capabilities=tmpl.get("capabilities", []),
                )
            else:
                raise KeyError(f"Source bot profile '{source_name}' not found.")

        unique_suffix = uuid.uuid4().hex[:6]
        new_name = target_name or f"{source_name}_clone_{unique_suffix}"

        # Build SOUL
        new_soul = source_profile.soul
        if specialist_directive:
            new_soul += f"\n\n### SPECIALIST MISSION DIRECTIVE ({new_name})\n{specialist_directive}\n"

        # Merge toolsets and skills (preserve insertion order)
        merged_tools = list(dict.fromkeys(source_profile.toolsets + (tools_to_add or [])))
        merged_skills = list(dict.fromkeys(source_profile.skills + (skills_to_add or [])))
        merged_caps = list(dict.fromkeys(source_profile.capabilities + (skills_to_add or [])))

        # Lineage metadata
        prior_lineage = source_profile.metadata.get("lineage", [])
        lineage = list(prior_lineage) + [source_name]

        cloned_profile = BotProfile(
            name=new_name,
            display_name=f"{source_profile.display_name} (Fork: {new_name})",
            role=f"{source_profile.role} [Specialist]",
            soul=new_soul,
            model=model_override or source_profile.model,
            toolsets=merged_tools,
            skills=merged_skills,
            avatar=source_profile.avatar or "🤖",
            department=department or source_profile.department,
            reports_to=source_profile.name,
            capabilities=merged_caps,
            version=source_profile.version + (1 if mode == CloneMode.ENHANCED_MUTATION else 0),
            memory_scope=new_name,
            metadata={
                **source_profile.metadata,
                "cloned_from": source_name,
                "clone_mode": mode.value,
                "lineage": lineage,
            },
        )

        # Ephemeral lease tracking (TTL > 0 only).
        #
        # The real API is `EphemeralBotManager.register_lease` (aliased at class
        # scope as `provision`), whose signature is:
        #   register_lease(bot_name, domain="engineering", prompt_objective="",
        #                  ttl_seconds=3600, **kwargs)
        # The previous call site passed `name_override=`/`profile_override=`
        # (silently absorbed by **kwargs) while omitting the required positional
        # `bot_name`, so EVERY clone raised TypeError and the lease was never
        # created - the failure was downgraded to a warning-only log line.
        # Mapping onto the real parameters:
        #   - intended name override  -> `bot_name` (register_lease keys the
        #     lease by the lower-cased bot name; the clone profile itself is
        #     registered in the bot registry below),
        #   - intended profile override -> no such parameter exists and none is
        #     needed: register_lease only stores lease metadata (bot_name,
        #     domain, objective, TTL) and never reads/writes bot profiles, so
        #     there is nothing profile-shaped it could override.
        if ttl_seconds > 0:
            try:
                lease = self.ephemeral_mgr.provision(
                    bot_name=new_name,
                    domain=source_profile.department,
                    prompt_objective=specialist_directive or f"Cloned from {source_name}",
                    ttl_seconds=ttl_seconds,
                )
                lease_status = (
                    f"active: lease={lease.bot_name} "
                    f"ttl_seconds={lease.ttl_seconds} expires_at={lease.expires_at}"
                )
            except Exception as e:
                # Honest failure surface: the real exception text goes to the
                # caller (payload) and to the logs at ERROR with traceback.
                # Never swallowed into a warning-only path again.
                lease_status = f"failed: {type(e).__name__}: {e}"
                logger.error(
                    "Could not register ephemeral lease for clone '%s': %s",
                    new_name,
                    lease_status,
                    exc_info=True,
                )
        else:
            lease_status = f"none: ttl_seconds=0, no ephemeral lease requested for '{new_name}'"

        # Additive payload key: the clone result carries an honest lease status
        # alongside the pre-existing lineage metadata (cloned_from, clone_mode,
        # lineage). Values: "active: ...", "failed: <real error>", "none: ...".
        cloned_profile.metadata["lease_status"] = lease_status

        # Register in active bot registry last, so the persisted profile
        # already carries its lease_status metadata.
        self.registry.register(cloned_profile)

        logger.info(f"Bot '{source_name}' successfully cloned into '{new_name}' (mode: {mode.value})")
        return cloned_profile

    def evolve_bot(
        self,
        source_name: str,
        performance_delta: dict[str, Any],
        improvement_directive: str,
        promoted_skills: list[str] | None = None,
    ) -> BotProfile:
        """Breed a generation-advanced version of a bot based on positive evaluation metrics."""
        source_profile = self.registry.get_bot(source_name)
        if not source_profile:
            raise KeyError(f"Bot '{source_name}' not found for evolution.")

        new_version = source_profile.version + 1
        new_name = f"{source_name}_v{new_version}"

        evolution_soul = (
            source_profile.soul
            + f"\n\n### EVOLUTIONARY DIRECTIVE (Generation {new_version})\n"
            + f"{improvement_directive}\n"
            + f"Performance telemetry baseline: {performance_delta}\n"
        )

        merged_skills = list(set(source_profile.skills + (promoted_skills or [])))

        evolved_profile = BotProfile(
            name=new_name,
            display_name=f"{source_profile.display_name} v{new_version}",
            role=source_profile.role,
            soul=evolution_soul,
            model=source_profile.model,
            toolsets=list(source_profile.toolsets),
            skills=merged_skills,
            avatar=source_profile.avatar or "🧬",
            department=source_profile.department,
            reports_to=source_profile.reports_to,
            capabilities=list(source_profile.capabilities),
            version=new_version,
            # Reputation is INHERITED, never bumped by evolution: the caller's
            # performance_delta is an unverified claim (free-form dict), and
            # reputation moves only through observed task outcomes
            # (alpha.bots.performance — success boost / failure penalty).
            reputation_score=source_profile.reputation_score,
            metadata={
                **source_profile.metadata,
                "evolved_from": source_name,
                "generation": new_version,
                "performance_delta": performance_delta,
                "reputation_basis": f"inherited from {source_name}; moves only via observed task outcomes (alpha.bots.performance)",
            },
        )

        self.registry.register(evolved_profile)
        logger.info(f"Bot '{source_name}' evolved to '{new_name}' (Gen {new_version})")
        return evolved_profile


_GLOBAL_CLONE_ENGINE: BotCloneEngine | None = None


def get_bot_clone_engine() -> BotCloneEngine:
    global _GLOBAL_CLONE_ENGINE
    if _GLOBAL_CLONE_ENGINE is None:
        _GLOBAL_CLONE_ENGINE = BotCloneEngine()
    return _GLOBAL_CLONE_ENGINE
