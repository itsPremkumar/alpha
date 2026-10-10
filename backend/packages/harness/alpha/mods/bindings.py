"""Bridge that projects mod commands into the Gateway's slash-command registry.

Kept separate from :mod:`alpha.mods.commands` because the two registries have
different owners: that one is the mod kernel's runtime surface (a mod adds and
withdraws a command while the Gateway serves), this one is the operator-facing
catalog. :func:`bind_mod_commands` is the single mapping between them, called
from the mod command registry's own registration lifecycle rather than at
import, so the harness never depends on the catalog's shape to *load* — only
when a mod actually contributes a command.

Three properties make the projection safe to keep in step at runtime:

- **A bound row is executable, not a catalogue stub.** The handler registered
  here dispatches back through the kernel's own registry, so ``has_handler``
  stays truthful: the discovery plane's "runnable" flag and the dispatch that
  follows it are the same fact. A row bound *without* a handler would advertise
  a capability that answers "nothing was executed".
- **A mod may not take a row it does not own.** A name already catalogued under
  another source — every core command included — is refused with a warning
  instead of overwritten, so registering ``goal`` cannot hand a mod the
  operator's ``/goal``.
- **The projection is pruned, never only appended.** A mod-sourced row the
  kernel no longer owns is removed, so withdrawing a command stops advertising
  it in the same act.

Approval stays with the catalog's own gate: ``requires_approval`` is carried
across, so a mod command that declares approval is blocked until a caller
supplies the same explicit grant a core command requires — never executed
unguarded, and never weakened into an unconditional run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

#: Metadata key marking a catalog row as projected from the mod kernel. Both the
#: shadow guard and the prune read it, so a row without it is never touched.
SOURCE_KEY = "source"
MOD_SOURCE = "alpha_mod_kernel"


def bind_mod_commands(mod_commands: list[dict]) -> int:
    """Refresh the catalog's projection of mod-contributed commands.

    ``mod_commands`` is the :meth:`AlphaModMiddleware.list_mod_commands` shape —
    plain dicts — so this function never imports the harness's command classes to
    build its argument. Returns how many rows it bound; rows withdrawn from the
    kernel are pruned beside them.

    Rebinding the same name is idempotent (the row and its handler are replaced
    together), so calling this after every registration change is safe.
    """
    from alpha.commands.registry import CommandCategory, SlashCommandDef, command_registry

    current: set[str] = set()
    bound = 0
    for row in mod_commands or []:
        name = str(row.get("name") or "").strip().lstrip("/").lower()
        if not name:
            continue
        command = f"/{name}"
        mod_name = str(row.get("mod_name") or "unknown")
        existing = command_registry.get(command)
        if existing is not None and (existing.metadata or {}).get(SOURCE_KEY) != MOD_SOURCE:
            logger.warning(
                "Refusing to project mod command %s from mod '%s': the catalog already owns that name. A mod may not shadow it.",
                command,
                mod_name,
            )
            continue
        current.add(command)
        command_def = SlashCommandDef(
            command=command,
            category=CommandCategory.OBSERVABILITY,
            description=f"[mod:{mod_name}] {str(row.get('description') or '')}".strip(),
            usage=command,
            is_core=False,
            is_autonomous_trigger=False,
            requires_approval=bool(row.get("requires_approval")),
            metadata={"mod_name": mod_name, SOURCE_KEY: MOD_SOURCE},
        )
        command_registry.register(command_def, handler=_make_handler(name))
        bound += 1
    pruned = _prune_projection(current)
    if bound or pruned:
        logger.info("Mod command projection refreshed: %d bound, %d pruned", bound, pruned)
    return bound


def _prune_projection(current: set[str]) -> int:
    """Remove projected rows the kernel no longer owns; never other rows."""
    from alpha.commands.registry import command_registry

    removed = 0
    for row in command_registry.list_commands():
        if (row.metadata or {}).get(SOURCE_KEY) != MOD_SOURCE or row.command in current:
            continue
        if command_registry.unregister(row.command):
            removed += 1
    return removed


def _make_handler(name: str) -> Callable[..., Any]:
    """Build the synchronous handler the catalog dispatches a projected row through.

    The catalog dispatches synchronously (the Gateway calls it through
    ``asyncio.to_thread``) while a mod's handler may be ``async``, so the two
    meet in :func:`_drive`.
    """
    from alpha.commands.registry import CommandExecutionResult

    command = f"/{name}"

    def _handler(args: Any, context: dict[str, Any] | None = None) -> CommandExecutionResult:
        from alpha.mods.kernel import get_mod_kernel
        from alpha.mods.middleware import run_mod_command

        # The catalog surface carries the argument string and the run context
        # it was invoked with; the mod handler owns how to read both.
        payload = {"args": args, "context": dict(context or {})}
        outcome = _drive(run_mod_command(get_mod_kernel(), name, payload))
        if outcome is None:
            # The row outlived its mod's registration. Reporting this as a
            # command that ran would be the lie the projection exists to avoid.
            return CommandExecutionResult(
                status="not_found",
                command=command,
                output=f"Mod command {command} is no longer registered by its mod; nothing was executed.",
                data={"executed": False, "source": MOD_SOURCE},
            )
        succeeded = outcome.get("status") == "success"
        return CommandExecutionResult(
            status="success" if succeeded else "error",
            command=command,
            output=str(outcome.get("output") or ""),
            data={
                "executed": succeeded,
                "mod_name": outcome.get("mod_name"),
                "source": MOD_SOURCE,
            },
        )

    return _handler


def _drive(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a mod command coroutine from the catalog's synchronous dispatch.

    A sync caller on a thread with no event loop takes the ordinary path. A
    caller that already owns a running loop cannot block on it without
    deadlocking, so the work moves to a short-lived thread with its own loop —
    the mod command still runs, and the caller's loop is never awaited from
    inside itself.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="alpha-mod-command") as pool:
        return pool.submit(asyncio.run, coro).result()
