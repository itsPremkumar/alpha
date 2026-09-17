"""Typed Action event definitions for OpenHands-style EventStream architecture."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ActionType(str, Enum):
    CMD_RUN = "cmd_run"
    FILE_EDIT = "file_edit"
    BROWSE = "browse"
    CRITIC = "critic"
    MESSAGE = "message"
    AGENT_FINISH = "agent_finish"


@dataclass
class Action:
    """Base class for all agent-initiated actions."""
    action_type: ActionType
    thought: str = ""
    action_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action_type"] = self.action_type.value
        return data


@dataclass
class CmdRunAction(Action):
    """Execution of a shell command."""
    command: str = ""
    is_background: bool = False
    cwd: str | None = None

    def __init__(
        self,
        command: str,
        thought: str = "",
        is_background: bool = False,
        cwd: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(action_type=ActionType.CMD_RUN, thought=thought, **kwargs)
        self.command = command
        self.is_background = is_background
        self.cwd = cwd


@dataclass
class FileEditAction(Action):
    """File creation, deletion, or replacement."""
    path: str = ""
    content: str | None = None
    old_str: str | None = None
    new_str: str | None = None
    mode: str = "write"  # "write", "replace", "append", "delete"

    def __init__(
        self,
        path: str,
        content: str | None = None,
        old_str: str | None = None,
        new_str: str | None = None,
        mode: str = "write",
        thought: str = "",
        **kwargs: Any,
    ):
        super().__init__(action_type=ActionType.FILE_EDIT, thought=thought, **kwargs)
        self.path = path
        self.content = content
        self.old_str = old_str
        self.new_str = new_str
        self.mode = mode


@dataclass
class CriticAction(Action):
    """Evaluation by a quality or completion critic."""
    critic_name: str = ""
    target_action_id: str | None = None

    def __init__(
        self,
        critic_name: str,
        target_action_id: str | None = None,
        thought: str = "",
        **kwargs: Any,
    ):
        super().__init__(action_type=ActionType.CRITIC, thought=thought, **kwargs)
        self.critic_name = critic_name
        self.target_action_id = target_action_id


@dataclass
class AgentFinishAction(Action):
    """Agent declaration of task completion."""
    final_thought: str = ""
    deliverables: list[str] = field(default_factory=list)

    def __init__(
        self,
        final_thought: str = "",
        deliverables: list[str] | None = None,
        **kwargs: Any,
    ):
        super().__init__(action_type=ActionType.AGENT_FINISH, thought=final_thought, **kwargs)
        self.final_thought = final_thought
        self.deliverables = deliverables or []
