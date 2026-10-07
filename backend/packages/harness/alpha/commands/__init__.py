from .autonomous_engine import (
    AutonomousCommandEngine,
    AutonomousDetectionResult,
    LifecyclePhase,
    TriggerRule,
    autonomous_command_engine,
)
from .registry import CommandCategory, CommandExecutionResult, SlashCommandDef, command_registry
from . import backend_handlers

# Standing goals: /goal, /goal step, /goal gate ..., /subgoal. Imported for its
# binding side effect, exactly like backend_handlers above - the handlers are
# registered on the process-wide command_registry, which the gateway reaches
# through routers/commands.py -> command_registry.execute.
from alpha.mission.goalloop import bindings as goal_bindings  # noqa: F401

# APEX autopilot: /apex, /apex on, /apex off, /apex status, /apex policy.
# Imported for the same binding side effect. Deliberately imported *after*
# backend_handlers: register() replaces a handler rather than stacking a
# second one, so the order cannot produce two live handlers for one command.
from alpha.apex import commands as apex_commands  # noqa: F401
