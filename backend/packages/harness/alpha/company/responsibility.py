"""Persistent Responsibility Engine: Survival of duties beyond individual worker lifecycles."""

from __future__ import annotations

import logging
import time

from alpha.company.models import ResponsibilityBinding

logger = logging.getLogger(__name__)


class ResponsibilityEngine:
    """Manages enduring organizational responsibilities with automated failover to backup agents."""

    def __init__(self):
        # responsibility_id -> ResponsibilityBinding
        self._bindings: dict[str, ResponsibilityBinding] = {}
        # Audit log of failover events
        self._failover_history: list[dict[str, str | float]] = []

    def register_responsibility(
        self,
        name: str,
        department: str,
        primary_bot_name: str,
        backup_bot_name: str,
        recovery_bot_name: str,
    ) -> ResponsibilityBinding:
        """Registers a critical duty with primary, backup, and recovery worker bindings."""
        binding = ResponsibilityBinding(
            name=name,
            department=department,
            primary_bot_name=primary_bot_name,
            backup_bot_name=backup_bot_name,
            recovery_bot_name=recovery_bot_name,
            active_bot_name=primary_bot_name,
            status="active",
        )
        self._bindings[binding.responsibility_id] = binding
        logger.info(f"Registered responsibility '{name}' [Primary: {primary_bot_name}, Backup: {backup_bot_name}]")
        return binding

    def trigger_failover(self, responsibility_id: str, reason: str = "Primary worker unresponsive") -> ResponsibilityBinding:
        """Automatically transitions active duty to backup or recovery worker when primary fails."""
        binding = self._bindings.get(responsibility_id)
        if not binding:
            raise KeyError(f"Responsibility '{responsibility_id}' not found.")

        former_active = binding.active_bot_name
        if binding.active_bot_name == binding.primary_bot_name:
            binding.active_bot_name = binding.backup_bot_name
            binding.status = "failed_over"
        elif binding.active_bot_name == binding.backup_bot_name:
            binding.active_bot_name = binding.recovery_bot_name
            binding.status = "degraded"
        else:
            # Already on recovery agent
            binding.status = "critical"

        binding.last_health_check = time.time()
        record = {
            "responsibility_id": responsibility_id,
            "responsibility_name": binding.name,
            "former_active": former_active,
            "new_active": binding.active_bot_name,
            "status": binding.status,
            "reason": reason,
            "timestamp": time.time(),
        }
        self._failover_history.append(record)
        logger.warning(f"Responsibility failover triggered for '{binding.name}': {former_active} -> {binding.active_bot_name} ({reason})")
        return binding

    def restore_primary(self, responsibility_id: str) -> ResponsibilityBinding:
        """Restores duty back to primary worker once healthy."""
        binding = self._bindings.get(responsibility_id)
        if not binding:
            raise KeyError(f"Responsibility '{responsibility_id}' not found.")

        binding.active_bot_name = binding.primary_bot_name
        binding.status = "active"
        binding.last_health_check = time.time()
        return binding

    def get_binding(self, responsibility_id: str) -> ResponsibilityBinding | None:
        return self._bindings.get(responsibility_id)

    def list_bindings(self, department: str | None = None) -> list[ResponsibilityBinding]:
        bindings = list(self._bindings.values())
        if department:
            bindings = [b for b in bindings if b.department.lower() == department.lower()]
        return bindings

    list_responsibilities = list_bindings

    def get_bindings_for_bot(self, bot_name: str) -> list[ResponsibilityBinding]:
        clean = bot_name.strip().lower()
        return [b for b in self._bindings.values() if b.primary_bot_name.lower() == clean or b.active_bot_name.lower() == clean]

    def get_failover_history(self) -> list[dict[str, str | float]]:
        return list(self._failover_history)
