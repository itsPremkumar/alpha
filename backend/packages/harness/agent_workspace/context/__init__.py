"""Context Engine with Prefix-Preserving Compaction inspired by OpenClaw."""

from agent_workspace.context.engine import ContextEngine
from agent_workspace.context.projection import ContextProjection
from agent_workspace.context.watchdog import CompactionWatchdog

__all__ = ["ContextEngine", "ContextProjection", "CompactionWatchdog"]
