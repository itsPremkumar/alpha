"""Models and interfaces for Trigger-based MicroAgents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TriggerContext:
    """Context information used to evaluate whether a MicroAgent should trigger."""
    query: str = ""
    file_paths: list[str] = field(default_factory=list)
    task_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseTrigger(ABC):
    """Abstract base class for all micro-agent triggers."""

    trigger_type: str = "base"

    @abstractmethod
    def should_trigger(self, context: TriggerContext) -> bool:
        """Return True if condition is satisfied."""
        pass


@dataclass
class MicroAgent:
    """A specialized domain micro-agent with triggering conditions and prompt content."""
    agent_id: str
    name: str
    content: str
    triggers: list[BaseTrigger] = field(default_factory=list)
    priority: int = 10
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def matches(self, context: TriggerContext) -> bool:
        if not self.enabled:
            return False
        if not self.triggers:
            return False
        # If any trigger fires, the micro-agent activates
        return any(t.should_trigger(context) for t in self.triggers)
