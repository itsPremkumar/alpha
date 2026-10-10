# APEX autopilot: /apex, /apex on, /apex off, /apex status, /apex policy.
# Imported for the same binding side effect. Deliberately imported *after*
# backend_handlers: register() replaces a handler rather than stacking a
# second one, so the order cannot produce two live handlers for one command.
from alpha.apex import commands as apex_commands  # noqa: F401

# Durable mission memory: /mission, /mission set. Imported for the same binding
# side effect as apex/goalloop above; the handlers resolve a server-trusted
# owner (never the client-supplied context) and address the thread from it.
from alpha.mission import commands as mission_commands  # noqa: F401

# Standing goals: /goal, /goal step, /goal gate ..., /subgoal. Imported for its
# binding side effect, exactly like backend_handlers above - the handlers are
# registered on the process-wide command_registry, which the gateway reaches
# through routers/commands.py -> command_registry.execute.
from alpha.mission.goalloop import bindings as goal_bindings  # noqa: F401

from . import backend_handlers  # noqa: F401 - registers subsystem command handlers
from .autonomous_engine import (
    AutonomousCommandEngine,
    AutonomousDetectionResult,
    LifecyclePhase,
    TriggerRule,
    autonomous_command_engine,
)
from .registry import CommandCategory, CommandExecutionResult, SlashCommandDef, command_registry

__all__ = [
    "AutonomousCommandEngine",
    "AutonomousDetectionResult",
    "CommandCategory",
    "CommandExecutionResult",
    "LifecyclePhase",
    "SlashCommandDef",
    "TriggerRule",
    "autonomous_command_engine",
    "command_registry",
]
