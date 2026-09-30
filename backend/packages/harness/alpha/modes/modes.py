"""Working modes: Ask / Plan / Craft (+ Coding).

WorkBuddy's central UX idea is that a task is created in one of a small set of
modes whose capability increases from read-only Q&A (Ask) to planning (Plan) to
full execution (Craft). This module makes that explicit and *enforceable*: each
mode declares what it may do, and a run can be checked against its mode before
it acts. It is a policy object, not a UI concern — the mode is the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class WorkMode(IntEnum):
    """Capability tiers, ascending. The integer value is the capability rank."""

    ASK = 0
    PLAN = 1
    CRAFT = 2
    CODING = 3  # a Craft specialization for repositories and code


@dataclass(frozen=True)
class ModeCapabilities:
    """What a mode is permitted to do."""

    can_read_files: bool
    can_write_files: bool
    can_execute_tools: bool
    can_run_commands: bool
    can_delegate: bool
    produces_artifacts: bool
    description: str


MODE_CAPABILITIES: dict[WorkMode, ModeCapabilities] = {
    WorkMode.ASK: ModeCapabilities(
        can_read_files=True, can_write_files=False, can_execute_tools=True, can_run_commands=False,
        can_delegate=False, produces_artifacts=False,
        description="Answer questions and read context; no side effects.",
    ),
    WorkMode.PLAN: ModeCapabilities(
        can_read_files=True, can_write_files=False, can_execute_tools=True, can_run_commands=False,
        can_delegate=True, produces_artifacts=True,
        description="Produce a plan/design; no side effects on the workspace.",
    ),
    WorkMode.CRAFT: ModeCapabilities(
        can_read_files=True, can_write_files=True, can_execute_tools=True, can_run_commands=True,
        can_delegate=True, produces_artifacts=True,
        description="Execute the task and produce deliverables.",
    ),
    WorkMode.CODING: ModeCapabilities(
        can_read_files=True, can_write_files=True, can_execute_tools=True, can_run_commands=True,
        can_delegate=True, produces_artifacts=True,
        description="Execute code changes in a repository.",
    ),
}

#: Action -> the capability attribute it requires.
_ACTION_CAPABILITY: dict[str, str] = {
    "read_file": "can_read_files",
    "write_file": "can_write_files",
    "execute_tool": "can_execute_tools",
    "run_command": "can_run_commands",
    "delegate": "can_delegate",
    "produce_artifact": "produces_artifacts",
}

_ALIASES: dict[str, WorkMode] = {
    "ask": WorkMode.ASK,
    "chat": WorkMode.ASK,
    "plan": WorkMode.PLAN,
    "craft": WorkMode.CRAFT,
    "agent": WorkMode.CRAFT,
    "coding": WorkMode.CODING,
    "code": WorkMode.CODING,
}


class ModeError(RuntimeError):
    """Base class for mode failures."""


class UnknownModeError(ModeError, ValueError):
    """A mode name was not recognised."""


class ModeViolation(ModeError):
    """An action was attempted that the active mode does not permit."""


def parse_mode(value: WorkMode | str) -> WorkMode:
    """Normalise a mode name or enum to :class:`WorkMode`."""
    if isinstance(value, WorkMode):
        return value
    key = str(value or "").strip().lower()
    if key not in _ALIASES:
        raise UnknownModeError(f"unknown work mode {value!r}; expected one of {sorted(_ALIASES)}")
    return _ALIASES[key]


def capabilities_for(mode: WorkMode | str) -> ModeCapabilities:
    """Return the capability set for *mode*."""
    return MODE_CAPABILITIES[parse_mode(mode)]


def can_escalate(src: WorkMode | str, dst: WorkMode | str) -> bool:
    """True when moving from *src* to *dst* is an increase in capability."""
    return parse_mode(dst) > parse_mode(src)


def assert_allowed(mode: WorkMode | str, action: str) -> None:
    """Raise :class:`ModeViolation` if *action* is not permitted in *mode*."""
    resolved = parse_mode(mode)
    cap_attr = _ACTION_CAPABILITY.get(action)
    if cap_attr is None:
        raise ModeError(f"unknown action {action!r}; expected one of {sorted(_ACTION_CAPABILITY)}")
    if not getattr(MODE_CAPABILITIES[resolved], cap_attr):
        raise ModeViolation(f"mode {resolved.name} does not permit action {action!r}: {MODE_CAPABILITIES[resolved].description}")


def is_allowed(mode: WorkMode | str, action: str) -> bool:
    """Return whether *action* is permitted in *mode* (no raise)."""
    try:
        assert_allowed(mode, action)
    except ModeViolation:
        return False
    return True


__all__ = [
    "MODE_CAPABILITIES",
    "ModeCapabilities",
    "ModeError",
    "ModeViolation",
    "UnknownModeError",
    "WorkMode",
    "assert_allowed",
    "can_escalate",
    "capabilities_for",
    "is_allowed",
    "parse_mode",
]
