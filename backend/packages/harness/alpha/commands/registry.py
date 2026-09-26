from __future__ import annotations

import difflib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: Commands whose blast radius is irreversible (they restrict capabilities or
#: otherwise change what the agent may do) and therefore must never be
#: dispatched from an unapproved caller.  Kept separate from the catalog row's
#: ``requires_approval`` flag so an alias binding cannot quietly lose the
#: protection: ``execute`` consults BOTH.
APPROVAL_REQUIRED_COMMANDS: frozenset[str] = frozenset(
    {
        "/security lockdown",
    }
)

#: How many sibling subcommands an unknown-subcommand message lists before it
#: truncates.  Enough to be useful, bounded so a huge family cannot flood a
#: model context.
_MAX_LISTED_SUGGESTIONS = 12

#: Context key a human caller sets to grant an approval-gated command.
APPROVAL_CONTEXT_KEY = "approved"


class CommandCategory(StrEnum):
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
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
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
    data: dict[str, Any] = field(default_factory=dict)
    autonomous_directives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "command": self.command,
            "output": self.output,
            "data": self.data,
            "autonomous_directives": self.autonomous_directives,
        }


@dataclass(frozen=True)
class CommandResolution:
    """How a command line resolved, and what it was ambiguous about.

    ``match_kind`` is one of ``none`` / ``two_word`` / ``one_word`` /
    ``raw_one_word``.  ``near_miss`` is set only when the line looked like
    ``<family> <sub> ...`` but ``<family> <sub>`` is not a registered command
    and the bare ``<family>`` row was matched instead.  That is the dangerous
    case: without ``near_miss`` the caller cannot tell "the user asked for
    /goal create" from "the user typed /goal creat and got /goal".
    """

    command_def: SlashCommandDef | None
    args: str
    match_kind: str = "none"
    near_miss: str | None = None
    siblings: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.command_def is not None


class SlashCommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, SlashCommandDef] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._register_default_catalog()

    def register(self, command_def: SlashCommandDef, handler: Callable[..., Any] | None = None) -> None:
        self._commands[command_def.command] = command_def
        if handler:
            self._handlers[command_def.command] = handler

    def get(self, command: str) -> SlashCommandDef | None:
        return self._commands.get(command)

    def has_handler(self, command: str) -> bool:
        """True when a concrete handler backs this command name."""
        return command in self._handlers

    def list_commands(self, category: CommandCategory | None = None, only_core: bool = False) -> list[SlashCommandDef]:
        res = list(self._commands.values())
        if category:
            res = [c for c in res if c.category == category]
        if only_core:
            res = [c for c in res if c.is_core]
        return sorted(res, key=lambda c: c.command)

    def get_categories(self) -> list[dict[str, Any]]:
        counts: dict[CommandCategory, int] = {}
        for c in self._commands.values():
            counts[c.category] = counts.get(c.category, 0) + 1
        return [{"category": cat.value, "count": counts.get(cat, 0)} for cat in CommandCategory]

    def search(self, query: str) -> list[SlashCommandDef]:
        q = query.lower().strip()
        if not q:
            return self.list_commands()
        return [c for c in self._commands.values() if q in c.command.lower() or q in c.description.lower() or q in c.category.value.lower()]

    def subcommands_of(self, command: str) -> list[str]:
        """Registered subcommands of a family row, sorted."""
        prefix = f"{command} "
        return sorted(name for name in self._commands if name.startswith(prefix))

    @staticmethod
    def _declares_arguments(command_def: SlashCommandDef) -> bool:
        """True when a row's published usage admits free text after the name.

        ``/plan <objective>`` takes arguments, so ``/plan fix the flaky test``
        is a legitimate invocation.  ``/goal`` (usage ``/goal``) does not, so a
        token after it can only be a mistyped subcommand.
        """
        usage = command_def.usage or ""
        return "<" in usage or "[" in usage

    def _suggest_siblings(self, parent: str, near_miss: str) -> tuple[list[str], list[str]]:
        """Split a family's subcommands into (closest to the typo, the rest)."""
        siblings = self.subcommands_of(parent)
        wanted = near_miss.lower()
        close = [s for s in difflib.get_close_matches(wanted, [s.split(" ", 1)[1] for s in siblings], n=3, cutoff=0.4)]
        ordered = [s for s in siblings if s.split(" ", 1)[1] in close]
        return ordered, [s for s in siblings if s not in ordered]

    def resolve(self, command_line: str) -> CommandResolution:
        """Resolve a command line to a row, an argument string and a match kind."""
        raw = command_line.strip()
        if not raw:
            return CommandResolution(command_def=None, args="", match_kind="none")
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
                return CommandResolution(cmd_def, args, "two_word")

        # Try one-word command match (e.g. "/goal" from "/goal create something")
        one_word = tokens[0]
        cmd_def = self.get(one_word)
        if cmd_def:
            args = normalized[len(one_word) :].strip()
            near_miss = args.split()[0] if args else None
            siblings = tuple(self.subcommands_of(one_word)) if near_miss else ()
            return CommandResolution(cmd_def, args, "one_word", near_miss, siblings)

        # Try original raw string first token
        raw_tokens = raw.split(maxsplit=1)
        raw_one = raw_tokens[0]
        cmd_def = self.get(raw_one)
        if cmd_def:
            args = raw_tokens[1] if len(raw_tokens) > 1 else ""
            return CommandResolution(cmd_def, args, "raw_one_word")

        return CommandResolution(command_def=None, args="", match_kind="none")

    def find_command(self, command_line: str) -> tuple[SlashCommandDef | None, str]:
        """Resolves a command line to its SlashCommandDef and remaining argument string."""
        resolution = self.resolve(command_line)
        return resolution.command_def, resolution.args

    def _approval_gate_result(self, cmd_def: SlashCommandDef) -> CommandExecutionResult:
        """Refuse to dispatch an approval-gated command to an unapproved caller."""
        unbacked = ""
        if not self.has_handler(cmd_def.command):
            unbacked = "\nNote: no handler is bound to this row, so approving it will not perform the action either."
        return CommandExecutionResult(
            status="approval_required",
            command=cmd_def.command,
            output=(
                f"{cmd_def.command} was NOT executed: it is approval-gated "
                f"({cmd_def.description}).\n"
                f"A human must approve it first — re-invoke with a context carrying "
                f"{APPROVAL_CONTEXT_KEY!r}=True (and an 'actor') to dispatch it deliberately."
                f"{unbacked}"
            ),
            data={
                "category": cmd_def.category.value,
                "requires_approval": True,
                "executed": False,
                "arguments": "",
                "has_handler": self.has_handler(cmd_def.command),
            },
        )

    @staticmethod
    def _run_with_deadline(fn: Callable[[], Any], timeout_seconds: float | None) -> tuple[Any, bool]:
        """Run *fn* on a worker thread, returning ``(value, timed_out)``.

        A plain daemon thread (not a pool) is used on purpose: a timed-out
        command leaves a thread behind, and a daemon thread cannot keep the
        interpreter alive at exit the way a pool's non-daemon workers do.
        """
        if timeout_seconds is None:
            return fn(), False
        box: dict[str, Any] = {}

        def runner() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
                box["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join(timeout_seconds)
        if thread.is_alive():
            return None, True
        if "error" in box:
            raise box["error"]
        return box.get("value"), False

    def execute(
        self,
        command_line: str,
        context: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandExecutionResult:
        raw = command_line.strip()
        if not raw:
            return CommandExecutionResult(
                status="error",
                command="",
                output="Empty command provided.",
            )

        resolution = self.resolve(raw)
        cmd_def = resolution.command_def
        if not cmd_def:
            cmd_name = raw.split()[0]
            return CommandExecutionResult(
                status="not_found",
                command=cmd_name,
                output=f"Unknown slash command: {cmd_name}. Type /help to see all available commands.",
            )

        # A near-miss subcommand must NOT silently fall back to the family row.
        # `/goal creat` resolving to `/goal` reports a real command as executed
        # when the caller asked for a different one, so it is refused instead.
        if resolution.match_kind == "one_word" and resolution.near_miss and resolution.siblings and not self._declares_arguments(cmd_def):
            close, rest = self._suggest_siblings(cmd_def.command, resolution.near_miss)
            listed = close + rest
            truncated = len(listed) > _MAX_LISTED_SUGGESTIONS
            listing = ", ".join(listed[:_MAX_LISTED_SUGGESTIONS])
            if truncated:
                listing += f", … (+{len(listed) - _MAX_LISTED_SUGGESTIONS} more)"
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            return CommandExecutionResult(
                status="not_found",
                command=cmd_def.command,
                output=(
                    f"Unknown subcommand '{resolution.near_miss}' for {cmd_def.command}; "
                    f"nothing was executed.{hint}\n"
                    f"Known subcommands of {cmd_def.command}: {listing}\n"
                    f"Published usage: {cmd_def.usage}"
                ),
                data={
                    "category": cmd_def.category.value,
                    "executed": False,
                    "unknown_subcommand": resolution.near_miss,
                    "known_subcommands": listed,
                },
            )

        if cmd_def.requires_approval or cmd_def.command in APPROVAL_REQUIRED_COMMANDS:
            approved = bool((context or {}).get(APPROVAL_CONTEXT_KEY) is True)
            if not approved:
                return self._approval_gate_result(cmd_def)

        def _dispatch() -> CommandExecutionResult:
            # Check if custom handler registered
            if cmd_def.command in self._handlers:
                handler = self._handlers[cmd_def.command]
                res = handler(resolution.args, context=context)
                if isinstance(res, CommandExecutionResult):
                    return res
                return CommandExecutionResult(
                    status="success",
                    command=cmd_def.command,
                    output=str(res),
                    data={"result": res},
                )

            # Default autonomous execution / intent parsing
            directives: list[str] = []
            if cmd_def.is_autonomous_trigger:
                directives.append(f"Execute autonomous directive for {cmd_def.command} ({cmd_def.category.value}) with args: {resolution.args}")

            return CommandExecutionResult(
                status="success",
                command=cmd_def.command,
                output=f"Directive {cmd_def.command} accepted [{cmd_def.category.value}]. {cmd_def.description}",
                data={
                    "category": cmd_def.category.value,
                    "arguments": resolution.args,
                    "is_core": cmd_def.is_core,
                    "is_autonomous_trigger": cmd_def.is_autonomous_trigger,
                    "requires_approval": cmd_def.requires_approval,
                },
                autonomous_directives=directives,
            )

        def _guarded_dispatch() -> CommandExecutionResult:
            try:
                return _dispatch()
            except BaseException as exc:  # noqa: BLE001 - a broken handler is a failed command, not a crash
                return CommandExecutionResult(
                    status="error",
                    command=cmd_def.command,
                    output=f"{cmd_def.command} failed: {type(exc).__name__}: {exc}",
                    data={"category": cmd_def.category.value, "executed": False, "error": f"{type(exc).__name__}: {exc}"},
                )

        if timeout_seconds is None:
            return _guarded_dispatch()
        try:
            value, timed_out = self._run_with_deadline(_guarded_dispatch, timeout_seconds)
        except BaseException as exc:  # noqa: BLE001 - re-raised defensively; _guarded_dispatch already traps handler errors
            return CommandExecutionResult(
                status="error",
                command=cmd_def.command,
                output=f"{cmd_def.command} failed: {type(exc).__name__}: {exc}",
                data={"category": cmd_def.category.value, "executed": False, "error": f"{type(exc).__name__}: {exc}"},
            )
        if timed_out:
            return CommandExecutionResult(
                status="timeout",
                command=cmd_def.command,
                output=(
                    f"{cmd_def.command} did not finish within {timeout_seconds:g}s and was abandoned; "
                    f"the result is unknown (it may still complete in the background)."
                ),
                data={
                    "category": cmd_def.category.value,
                    "executed": False,
                    "timed_out": True,
                    "timeout_seconds": timeout_seconds,
                },
            )
        return value

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
