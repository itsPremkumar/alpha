from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class CommandCategory(str, Enum):
    CORE = "core"
    MISSION = "mission"
    PLANNING = "planning"
    EXECUTION = "execution"
    SWARM = "swarm"
    AGENT = "agent"
    BACKGROUND = "background"
    RESEARCH = "research"
    MEMORY = "memory"
    CONTEXT = "context"
    SKILLS = "skills"
    MODEL = "model"
    TOOLS = "tools"
    VERIFICATION = "verification"
    EVIDENCE = "evidence"
    CODING = "coding"
    BROWSER = "browser"
    RSI = "rsi"
    EVOLUTION = "evolution"
    AUTONOMOUS_OPS = "autonomous_ops"
    SECURITY = "security"
    RUNTIME = "runtime"
    OBSERVABILITY = "observability"
    SESSION = "session"
    COLLABORATION = "collaboration"
    COMMUNICATION = "communication"
    ARTIFACT = "artifact"
    WORLD_MODEL = "world_model"


@dataclass(frozen=True)
class SlashCommandDef:
    command: str
    category: CommandCategory
    description: str
    usage: str
    is_core: bool = False
    is_autonomous_trigger: bool = False
    requires_approval: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "category": self.category.value,
            "description": self.description,
            "usage": self.usage,
            "is_core": self.is_core,
            "is_autonomous_trigger": self.is_autonomous_trigger,
            "requires_approval": self.requires_approval,
            "metadata": self.metadata,
        }


@dataclass
class CommandExecutionResult:
    status: str
    command: str
    output: str
    data: Dict[str, Any] = field(default_factory=dict)
    autonomous_directives: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "command": self.command,
            "output": self.output,
            "data": self.data,
            "autonomous_directives": self.autonomous_directives,
        }


class SlashCommandRegistry:
    def __init__(self) -> None:
        self._commands: Dict[str, SlashCommandDef] = {}
        self._handlers: Dict[str, Callable[..., Any]] = {}
        self._register_default_catalog()

    def register(self, command_def: SlashCommandDef, handler: Optional[Callable[..., Any]] = None) -> None:
        self._commands[command_def.command] = command_def
        if handler:
            self._handlers[command_def.command] = handler

    def get(self, command: str) -> Optional[SlashCommandDef]:
        return self._commands.get(command)

    def list_commands(self, category: Optional[CommandCategory] = None, only_core: bool = False) -> List[SlashCommandDef]:
        res = list(self._commands.values())
        if category:
            res = [c for c in res if c.category == category]
        if only_core:
            res = [c for c in res if c.is_core]
        return sorted(res, key=lambda c: c.command)

    def get_categories(self) -> List[Dict[str, Any]]:
        counts: Dict[CommandCategory, int] = {}
        for c in self._commands.values():
            counts[c.category] = counts.get(c.category, 0) + 1
        return [{"category": cat.value, "count": counts.get(cat, 0)} for cat in CommandCategory]

    def search(self, query: str) -> List[SlashCommandDef]:
        q = query.lower().strip()
        if not q:
            return self.list_commands()
        return [c for c in self._commands.values() if q in c.command.lower() or q in c.description.lower() or q in c.category.value.lower()]

    def find_command(self, command_line: str) -> tuple[Optional[SlashCommandDef], str]:
        """Resolves a command line to its SlashCommandDef and remaining argument string."""
        raw = command_line.strip()
        if not raw:
            return None, ""
        # Normalize colon to space for lookup if colon exists in command prefix
        # e.g. /goal:status -> /goal status
        normalized = raw
        if ":" in raw.split()[0]:
            first_tok = raw.split()[0]
            rest_tok = raw[len(first_tok) :].strip()
            normalized = first_tok.replace(":", " ") + (" " + rest_tok if rest_tok else "")

        # Try exact two-word command match first (e.g. "/goal status" from "/goal status arg1 arg2")
        tokens = normalized.split(maxsplit=2)
        if len(tokens) >= 2:
            two_word = f"{tokens[0]} {tokens[1]}"
            cmd_def = self.get(two_word)
            if cmd_def:
                args = tokens[2] if len(tokens) > 2 else ""
                return cmd_def, args

        # Try one-word command match (e.g. "/goal" from "/goal create something")
        one_word = tokens[0]
        cmd_def = self.get(one_word)
        if cmd_def:
            args = normalized[len(one_word) :].strip()
            return cmd_def, args

        # Try original raw string first token
        raw_tokens = raw.split(maxsplit=1)
        raw_one = raw_tokens[0]
        cmd_def = self.get(raw_one)
        if cmd_def:
            args = raw_tokens[1] if len(raw_tokens) > 1 else ""
            return cmd_def, args

        return None, ""

    def execute(self, command_line: str, context: Optional[Dict[str, Any]] = None) -> CommandExecutionResult:
        raw = command_line.strip()
        if not raw:
            return CommandExecutionResult(
                status="error",
                command="",
                output="Empty command provided.",
            )

        cmd_def, args_str = self.find_command(raw)
        if not cmd_def:
            cmd_name = raw.split()[0]
            return CommandExecutionResult(
                status="not_found",
                command=cmd_name,
                output=f"Unknown slash command: {cmd_name}. Type /help to see all available commands.",
            )

        # Check if custom handler registered
        if cmd_def.command in self._handlers:
            handler = self._handlers[cmd_def.command]
            res = handler(args_str, context=context)
            if isinstance(res, CommandExecutionResult):
                return res
            return CommandExecutionResult(
                status="success",
                command=cmd_def.command,
                output=str(res),
                data={"result": res},
            )

        # Default autonomous execution / intent parsing
        directives: List[str] = []
        if cmd_def.is_autonomous_trigger:
            directives.append(f"Execute autonomous directive for {cmd_def.command} ({cmd_def.category.value}) with args: {args_str}")

        return CommandExecutionResult(
            status="success",
            command=cmd_def.command,
            output=f"Directive {cmd_def.command} accepted [{cmd_def.category.value}]. {cmd_def.description}",
            data={
                "category": cmd_def.category.value,
                "arguments": args_str,
                "is_core": cmd_def.is_core,
                "is_autonomous_trigger": cmd_def.is_autonomous_trigger,
                "requires_approval": cmd_def.requires_approval,
            },
            autonomous_directives=directives,
        )

    def _register_default_catalog(self) -> None:
        from .catalog import get_default_catalog_entries

        for item in get_default_catalog_entries():
            cmd, cat, desc, usage, is_core = item[0], item[1], item[2], item[3], item[4]
            is_auto = item[5] if len(item) > 5 else False
            req_app = item[6] if len(item) > 6 else False
            self.register(
                SlashCommandDef(
                    command=cmd,
                    category=cat,
                    description=desc,
                    usage=usage,
                    is_core=is_core,
                    is_autonomous_trigger=is_auto,
                    requires_approval=req_app,
                )
            )


command_registry = SlashCommandRegistry()
