"""Dynamic Temporary Specialist Bot Lifecycle Engine.

Provisions on-demand, temporary domain specialist bots with custom SOUL instructions,
finite time-to-live (TTL) leases, and automatic archival upon task completion.
Keeps permanent bot rosters clean while providing unlimited specialized talent.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from agent_workspace.bots.profile import BotProfile
from agent_workspace.bots.registry import get_bot_registry
from agent_workspace.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ephemeral_storage_path() -> Path:
    return runtime_home() / "bots" / "ephemeral.json"


@dataclass
class EphemeralLease:
    """Tracking record for a temporary bot lease."""

    bot_name: str
    domain: str
    prompt_objective: str
    created_at: str
    expires_at: str
    ttl_seconds: int
    status: Literal["active", "archived", "expired"] = "active"
    archive_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EphemeralLease:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)

    @property
    def is_expired(self) -> bool:
        if self.status != "active":
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at)
            return datetime.now(UTC) > exp
        except Exception:
            return False

    @property
    def remaining_seconds(self) -> int:
        if self.status != "active":
            return 0
        try:
            exp = datetime.fromisoformat(self.expires_at)
            delta = exp - datetime.now(UTC)
            return max(0, int(delta.total_seconds()))
        except Exception:
            return 0


class EphemeralBotManager:
    """Manages spawning, leasing, monitoring, and archiving temporary specialist bots."""

    def __init__(self, storage_path: Path | None = None) -> None:
        self._path = storage_path or _ephemeral_storage_path()
        self._lock = threading.Lock()
        self._leases: dict[str, EphemeralLease] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("leases", []):
                lease = EphemeralLease.from_dict(item)
                self._leases[lease.bot_name] = lease
        except Exception:
            logger.warning("Failed to load ephemeral leases", exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            payload = {
                "version": 1,
                "leases": [l.to_dict() for l in self._leases.values()],
                "updated_at": _now(),
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self._path)
        except Exception:
            logger.warning("Failed to save ephemeral leases", exc_info=True)

    def spawn_specialist(
        self,
        domain: str,
        prompt_objective: str,
        *,
        ttl_seconds: int = 3600,
        bot_name: str | None = None,
        parent_bot: str | None = "architect",
        model: str | None = None,
    ) -> BotProfile:
        """Spawn and register a temporary specialist bot with a TTL lease."""
        clean_domain = re.sub(r"[^\w-]", "", domain.lower().replace(" ", "_"))
        unique_suffix = uuid.uuid4().hex[:6]
        name = bot_name or f"spec_{clean_domain}_{unique_suffix}"
        display_name = f"{domain.title()} Specialist"

        now_dt = datetime.now(UTC)
        expires_dt = now_dt + timedelta(seconds=ttl_seconds)

        specialist_soul = f"""# SOUL.md - {display_name} ({domain})

You are **{name}**, a temporary, hyper-specialized AI agent provisioned for domain: **{domain}**.

## Core Objective:
{prompt_objective.strip()}

## Operational Directives:
1. Focus exclusively on the domain objective with high precision.
2. Generate production-ready, verified code and analysis.
3. Once the objective is completed and verified, report completion for decommissioning.
4. Adhere strictly to project conventions, quality gates, and tool permission rings.
"""

        registry = get_bot_registry()
        bot = registry.get_or_create(
            name=name,
            display_name=display_name,
            role=f"Temporary {domain.title()} Specialist",
            soul=specialist_soul,
            department="specialist",
            reports_to=parent_bot,
            responsibilities=[f"Execute {domain} tasks", prompt_objective[:100]],
            capabilities=[clean_domain, "specialist"],
        )

        # Ensure model and metadata are set
        bot.metadata["is_ephemeral"] = True
        bot.metadata["domain"] = domain
        bot.metadata["expires_at"] = expires_dt.isoformat()
        bot.metadata["ttl_seconds"] = ttl_seconds
        if model:
            bot.model = model

        lease = EphemeralLease(
            bot_name=name,
            domain=domain,
            prompt_objective=prompt_objective,
            created_at=now_dt.isoformat(),
            expires_at=expires_dt.isoformat(),
            ttl_seconds=ttl_seconds,
            status="active",
        )

        with self._lock:
            self._leases[name] = lease
            self._save()

        logger.info("Spawned ephemeral specialist %s (TTL: %ds)", name, ttl_seconds)
        return bot

    def check_leases(self) -> list[str]:
        """Check all leases and mark expired specialists as archived."""
        expired_bots: list[str] = []
        registry = get_bot_registry()

        with self._lock:
            for lease in self._leases.values():
                if lease.status == "active" and lease.is_expired:
                    lease.status = "expired"
                    lease.archive_reason = "ttl_expired"
                    expired_bots.append(lease.bot_name)
                    registry.update_bot(lease.bot_name, status="archived")

            if expired_bots:
                self._save()

        return expired_bots

    def archive_specialist(self, bot_name: str, reason: str = "task_completed") -> bool:
        """Explicitly retire and archive an ephemeral specialist."""
        registry = get_bot_registry()
        clean = bot_name.lower().strip()

        with self._lock:
            lease = self._leases.get(clean)
            if lease:
                lease.status = "archived"
                lease.archive_reason = reason
                self._save()

            # Update registry status
            bot = registry.get_bot(clean)
            if bot:
                registry.update_bot(clean, status="archived")
                return True
            return lease is not None

    def list_active_specialists(self) -> list[dict[str, Any]]:
        """List active specialist bots and their remaining lease time."""
        self.check_leases()
        with self._lock:
            return [
                {
                    **lease.to_dict(),
                    "remaining_seconds": lease.remaining_seconds,
                }
                for lease in self._leases.values()
                if lease.status == "active"
            ]

    def get_lease(self, bot_name: str) -> EphemeralLease | None:
        with self._lock:
            return self._leases.get(bot_name.lower().strip())


_ephemeral_manager: EphemeralBotManager | None = None
_ephemeral_lock = threading.Lock()


def get_ephemeral_manager() -> EphemeralBotManager:
    global _ephemeral_manager
    with _ephemeral_lock:
        if _ephemeral_manager is None:
            _ephemeral_manager = EphemeralBotManager()
        return _ephemeral_manager
