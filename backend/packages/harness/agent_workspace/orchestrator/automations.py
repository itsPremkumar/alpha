"""8. Unified automation scheduler (cron + heartbeat + event trigger).

Alpha ships scheduler/schedules (cron parsing) + app/scheduler service.
OpenClaw 2.0 unifies scheduled work under one name across agent/UI/CLI
with heartbeat monitoring + event (IMAP-style) triggers + non-interactive
runs (clarification disabled). This module is the additive unification
layer: one AutomationDefinition covers cron/interval/event kinds and
tracks queued->launching(lease)->running lifecycle already used by the
Gateway scheduler.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

AutomationKind = Literal["cron", "interval", "event"]
AutomationState = Literal["queued", "launching", "running", "succeeded", "failed"]


@dataclass
class AutomationDefinition:
    automation_id: str = field(default_factory=lambda: "auto_" + uuid.uuid4().hex[:8])
    name: str = ""
    kind: AutomationKind = "cron"
    cron: str = ""  # cron expression when kind == cron
    interval_seconds: float = 0  # when kind == interval
    event_source: str = ""  # e.g. "imap", "webhook", "channel" when kind == event
    agent_name: str = "lead_agent"
    non_interactive: bool = True  # scheduled runs disable clarification
    max_concurrent_runs: int = 1
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "automation_id": self.automation_id,
            "name": self.name,
            "kind": self.kind,
            "cron": self.cron,
            "interval_seconds": self.interval_seconds,
            "event_source": self.event_source,
            "agent_name": self.agent_name,
            "non_interactive": self.non_interactive,
            "enabled": self.enabled,
        }


@dataclass
class AutomationScheduler:
    """Process-local registry; durable execution stays with app scheduler."""

    _definitions: dict[str, AutomationDefinition] = field(default_factory=dict)
    _states: dict[str, AutomationState] = field(default_factory=dict)

    def register(self, definition: AutomationDefinition) -> AutomationDefinition:
        self._definitions[definition.automation_id] = definition
        self._states.setdefault(definition.automation_id, "queued")
        return definition

    def remove(self, automation_id: str) -> bool:
        existed = self._definitions.pop(automation_id, None) is not None
        self._states.pop(automation_id, None)
        return existed

    def list(self, enabled_only: bool = False) -> list[AutomationDefinition]:
        items = list(self._definitions.values())
        if enabled_only:
            items = [d for d in items if d.enabled]
        return items

    def mark(self, automation_id: str, state: AutomationState) -> None:
        if automation_id in self._definitions:
            self._states[automation_id] = state

    def run_context(self, automation_id: str) -> dict[str, Any]:
        """Non-interactive run context for the lead agent (no clarification)."""
        definition = self._definitions.get(automation_id)
        if definition is None:
            return {"non_interactive": True, "disable_clarification": True}
        return {
            "non_interactive": True,
            "disable_clarification": True,
            "agent_name": definition.agent_name,
            "automation_id": automation_id,
            "event_source": definition.event_source,
        }
