"""AST node definitions for shell command representation and analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    COMMAND = "command"
    PIPELINE = "pipeline"
    SUBSHELL = "subshell"
    COMPOUND = "compound"
    REDIRECTION = "redirection"


@dataclass
class ASTNode:
    """Base node for shell syntax tree."""
    node_type: NodeType

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.node_type.value}


@dataclass
class RedirectionNode(ASTNode):
    """File redirection (>, >>, <, 2>&1)."""
    operator: str
    target: str

    def __init__(self, operator: str, target: str):
        super().__init__(NodeType.REDIRECTION)
        self.operator = operator
        self.target = target

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.node_type.value,
            "operator": self.operator,
            "target": self.target,
        }


@dataclass
class CommandNode(ASTNode):
    """A single command invocation with args, env vars, and redirections."""
    command: str
    args: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    redirections: list[RedirectionNode] = field(default_factory=list)
    subshells: list[ASTNode] = field(default_factory=list)

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
        redirections: list[RedirectionNode] | None = None,
        subshells: list[ASTNode] | None = None,
    ):
        super().__init__(NodeType.COMMAND)
        self.command = command
        self.args = args or []
        self.env_vars = env_vars or {}
        self.redirections = redirections or []
        self.subshells = subshells or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.node_type.value,
            "command": self.command,
            "args": self.args,
            "env_vars": self.env_vars,
            "redirections": [r.to_dict() for r in self.redirections],
            "subshells": [s.to_dict() for s in self.subshells],
        }


@dataclass
class PipelineNode(ASTNode):
    """A piped sequence of commands: cmd1 | cmd2 | cmd3."""
    stages: list[ASTNode] = field(default_factory=list)

    def __init__(self, stages: list[ASTNode] | None = None):
        super().__init__(NodeType.PIPELINE)
        self.stages = stages or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.node_type.value,
            "stages": [s.to_dict() for s in self.stages],
        }


@dataclass
class SubshellNode(ASTNode):
    """A subshell command: $(cmd) or `cmd`."""
    body: ASTNode

    def __init__(self, body: ASTNode):
        super().__init__(NodeType.SUBSHELL)
        self.body = body

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.node_type.value,
            "body": self.body.to_dict(),
        }


@dataclass
class CompoundNode(ASTNode):
    """A compound execution sequence: cmd1 && cmd2 || cmd3 ; cmd4."""
    operator: str  # "&&", "||", ";"
    left: ASTNode
    right: ASTNode

    def __init__(self, operator: str, left: ASTNode, right: ASTNode):
        super().__init__(NodeType.COMPOUND)
        self.operator = operator
        self.left = left
        self.right = right

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.node_type.value,
            "operator": self.operator,
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
        }
