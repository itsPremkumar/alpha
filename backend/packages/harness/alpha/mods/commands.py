"""Mod-registered commands — a ``/command`` that runs without a model turn.

Claude Code's ``$.command.register`` lets a mod add a slash command whose handler
runs *immediately*: no model turn, no tokens, and it works even while the agent
is still streaming. That is a different shape from a skill, which hands the model
instructions and waits for it to act.

Alpha's equivalent is this registry, owned by the mod kernel:

- a mod registers a name and a callable during ``session.start`` (or any event);
- dispatching ``command.run`` with that name **answers** the event directly,
  short-circuiting the rest of the pipeline exactly like ``EventOutcome.ANSWER``;
- resolution is by exact, normalized name. A mod may not shadow another mod's
  command, because two handlers for one name is a coin flip nobody can debug.

The registry is deliberately *not* the Gateway's
:class:`~alpha.commands.registry.SlashCommandRegistry`. That one owns the
catalog of operator-facing slash commands; this one owns what a **mod** can add
at runtime. :func:`alpha.mods.bindings.bind_mod_commands` is the single bridge
that projects mod commands into the Gateway catalog so they appear — and
dispatch — beside the rest. It is called from this registry's own mutation
path, not at import, because a mod registers when it *runs* rather than when
the process starts: a one-shot bind at startup would project nothing, and a
row left behind after ``unregister`` would keep advertising a command that no
longer exists.
"""

from __future__ import annotations

import inspect
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: A command name is a single lowercase token. Slashes and dots are refused so a
#: mod cannot register something that looks like a route or a subcommand family.
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")

#: Bounds on the handler's return: a command that prints a novel is a command
#: that floods a transcript.
MAX_OUTPUT_CHARS = 8000


class ModCommandError(RuntimeError):
    """A mod command registration or resolution was refused."""


@dataclass(frozen=True)
class ModCommand:
    """One command a mod contributed."""

    name: str
    mod_name: str
    description: str
    requires_approval: bool = False
    registered_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mod_name": self.mod_name,
            "description": self.description,
            "requires_approval": self.requires_approval,
            "registered_at": self.registered_at,
        }


def normalize_command_name(raw: str) -> str:
    """Normalize a requested command name, refusing anything unsafe.

    Raising here rather than coercing matters: silently lowercasing a name with a
    space or a slash would make a mod able to register ``goal status`` and
    collide with a real family row.
    """
    name = str(raw or "").strip().lstrip("/").lower()
    if not _NAME_PATTERN.match(name):
        raise ModCommandError(f"Invalid mod command name {raw!r}: use 1-48 lowercase letters, digits, '-' or '_'.")
    return name


class ModCommandRegistry:
    """Process-local registry of mod-contributed commands."""

    def __init__(self) -> None:
        self._commands: dict[str, ModCommand] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        mod_name: str,
        name: str,
        handler: Callable[..., Any],
        *,
        description: str = "",
        requires_approval: bool = False,
    ) -> ModCommand:
        """Register a command. Refuses a duplicate name owned by another mod."""
        if not callable(handler):
            raise ModCommandError(f"Mod '{mod_name}' command '{name}' needs a callable handler.")
        normalized = normalize_command_name(name)
        owner = str(mod_name or "").strip()
        if not owner:
            raise ModCommandError("A mod must be named to register a command; an anonymous caller cannot be audited.")
        with self._lock:
            existing = self._commands.get(normalized)
            if existing is not None and existing.mod_name != owner:
                raise ModCommandError(f"Command '{normalized}' is already registered by mod '{existing.mod_name}'; refusing to shadow it.")
            command = ModCommand(
                name=normalized,
                mod_name=owner,
                description=str(description or "")[:300],
                requires_approval=bool(requires_approval),
                registered_at=time.time(),
            )
            self._commands[normalized] = command
            self._handlers[normalized] = handler
        logger.info("Mod '%s' registered command '/%s'", owner, normalized)
        self._project_to_catalog()
        return command

    def _project_to_catalog(self) -> None:
        """Keep the Gateway catalog's projection of these commands in step.

        A mod command is reachable from the operator command plane — discovery
        *and* dispatch — only if that catalog knows about it, and it must stop
        being advertised the moment its mod withdraws it. The mapping itself
        lives in :mod:`alpha.mods.bindings`. A projection failure must not undo
        a successful registration (the command still works through this
        registry), so it is logged rather than raised.
        """
        try:
            from alpha.mods.bindings import bind_mod_commands

            bind_mod_commands([c.to_dict() for c in self.list_commands()])
        except Exception:  # noqa: BLE001 - a broken catalog must not break the kernel
            logger.warning("Could not project mod commands into the Gateway command catalog", exc_info=True)

    def unregister(self, mod_name: str, name: str) -> bool:
        """Remove a command; only its owning mod may remove it."""
        try:
            normalized = normalize_command_name(name)
        except ModCommandError:
            return False
        with self._lock:
            existing = self._commands.get(normalized)
            if existing is None or existing.mod_name != str(mod_name):
                return False
            self._commands.pop(normalized, None)
            self._handlers.pop(normalized, None)
        self._project_to_catalog()
        return True

    def unregister_mod(self, mod_name: str) -> int:
        """Drop every command owned by one mod; used when a mod is unregistered."""
        with self._lock:
            doomed = [name for name, cmd in self._commands.items() if cmd.mod_name == str(mod_name)]
            for name in doomed:
                self._commands.pop(name, None)
                self._handlers.pop(name, None)
        if doomed:
            self._project_to_catalog()
        return len(doomed)

    def resolve(self, name: str) -> tuple[ModCommand, Callable[..., Any]] | None:
        """Look up a command and its handler by exact normalized name."""
        try:
            normalized = normalize_command_name(name)
        except ModCommandError:
            return None
        with self._lock:
            command = self._commands.get(normalized)
            if command is None:
                return None
            return command, self._handlers[normalized]

    def list_commands(self, mod_name: str | None = None) -> list[ModCommand]:
        with self._lock:
            commands = list(self._commands.values())
        if mod_name is not None:
            commands = [c for c in commands if c.mod_name == str(mod_name)]
        return sorted(commands, key=lambda c: c.name)

    def clear(self) -> int:
        with self._lock:
            count = len(self._commands)
            self._commands.clear()
            self._handlers.clear()
        if count:
            self._project_to_catalog()
        return count


async def run_command(handler: Callable[..., Any], payload: dict[str, Any]) -> Any:
    """Invoke a command handler, tolerating sync and async callables.

    The handler is third-party-shaped code running inside the kernel, so a raise
    here must become a *reported* failure rather than an exception that unwinds
    the pipeline: a broken command is bad news the operator should read, not a
    crash in the dispatcher.
    """
    call = handler(payload) if _takes_payload(handler) else handler()
    if inspect.isawaitable(call):
        call = await call
    return call


def _takes_payload(handler: Callable[..., Any]) -> bool:
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return True
    for param in sig.parameters.values():
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD, param.VAR_POSITIONAL):
            return True
    return False
