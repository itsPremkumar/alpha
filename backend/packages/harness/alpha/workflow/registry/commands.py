"""Slash-command registry — read-only view of the master command catalog.

Source of truth: ``alpha.commands.command_registry`` — the process-wide
:class:`~alpha.commands.registry.SlashCommandRegistry`, the same object
``GET /api/commands`` reads. Over 400 rows across ~28 families, declared in
``alpha/commands/catalog.py``.

Why it belongs on the selection plane
-------------------------------------
A command is not a tool: the model reaches it through
``execute_slash_command_tool``, and only ``commands`` that actually have a bound
handler run. So "418 commands registered" and "418 commands usable" are two very
different claims, and collapsing them is exactly how an agent ends up telling a
user it can ``/foo`` and then reporting success on a row that resolved to nothing.

Honesty contract
----------------
* ``availability="available"`` = **a handler is bound**. ``has_handler`` is the
  measured fact, and it is the only thing that distinguishes a dispatchable
  command from a catalog entry.
* ``availability="unavailable"`` = the row exists with no handler, and ``reason``
  says exactly that — plus that ``execute`` returns
  :data:`~alpha.commands.registry.UNIMPLEMENTED_STATUS`, never ``success``. That
  distinction is already enforced by the registry module; this projection only
  refuses to hide it.
* ``requires_approval`` is carried in the descriptor address, because a command the
  caller cannot authorise is a different capability from one it can.
* ``health`` stays ``unverified``: a bound handler has not been executed here.
* ``version`` stays ``None``.
* ``authority`` is developer code, because the catalog is shipped source — except
  for rows an operator registers at runtime, which the registry cannot distinguish,
  so the honest authority is the shipped catalog plus runtime registration.

This registry performs no I/O beyond building the catalog on first import.
"""

from __future__ import annotations

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    RegistryHealth,
    RegistryUnavailable,
)

_SOURCE = "alpha.commands.catalog:get_default_catalog_entries"
_AUTHORITY = "developer code (shipped catalog) + runtime command registration"

_NO_HANDLER_REASON = "catalog row has no bound handler; execute_slash_command_tool returns status='unimplemented' for it, never success"


def _load_registry() -> object:
    try:
        from alpha.commands import command_registry
    except Exception as exc:  # pragma: no cover - production-wired import
        raise RegistryUnavailable(f"{type(exc).__name__}: {exc}") from exc
    if command_registry is None:  # pragma: no cover - defensive
        raise RegistryUnavailable("alpha.commands.command_registry resolved to None")
    return command_registry


class CommandRegistry:
    """list/describe/health over the slash command catalog."""

    name = "commands"

    def list(self) -> list[CapabilityDescriptor]:
        registry = _load_registry()
        try:
            rows = list(registry.list_commands())
        except Exception as exc:
            raise RegistryUnavailable(f"command catalog could not be listed: {type(exc).__name__}: {exc}") from exc
        return [self._describe_row(registry, row) for row in rows]

    def describe(self, entry_id: str) -> CapabilityDescriptor | None:
        registry = _load_registry()
        try:
            row = registry.get(entry_id)
        except Exception as exc:
            raise RegistryUnavailable(f"command catalog could not be read: {type(exc).__name__}: {exc}") from exc
        if row is None:
            return None
        return self._describe_row(registry, row)

    def health(self) -> RegistryHealth:
        try:
            descriptors = self.list()
        except Exception as exc:
            return RegistryHealth(
                registry=self.name,
                status="unavailable",
                count=None,
                error=f"{type(exc).__name__}: {exc}",
                evidence_kind="measured",
            )
        dispatchable = sum(1 for descriptor in descriptors if descriptor.availability == "available")
        return RegistryHealth(
            registry=self.name,
            status="ok",
            count=len(descriptors),
            error=None if dispatchable == len(descriptors) else f"{len(descriptors) - dispatchable} row(s) have no bound handler",
            evidence_kind="measured",
        )

    @staticmethod
    def _describe_row(registry: object, row: object) -> CapabilityDescriptor:
        command = str(getattr(row, "command", "") or "")
        category = getattr(getattr(row, "category", None), "value", "") or str(getattr(row, "category", "") or "")
        usage = str(getattr(row, "usage", "") or "") or command
        requires_approval = bool(getattr(row, "requires_approval", False))
        address = f"{_SOURCE} -> {command or '(unnamed)'} [{category or 'uncategorised'}] usage={usage}"
        if requires_approval:
            address = f"{address} requires_approval=true"

        if not command:
            return CapabilityDescriptor(
                id="",
                kind="slash_command",
                availability="unavailable",
                source=_SOURCE,
                version=None,
                health="unverified",
                authority=_AUTHORITY,
                evidence_kind="measured",
                reason="catalog row has no command name; the shipped catalog is malformed",
            )

        try:
            has_handler = bool(registry.has_handler(command))
        except Exception:  # noqa: BLE001 - a probe failure is not a handler
            has_handler = False

        return CapabilityDescriptor(
            id=command,
            kind="slash_command",
            availability="available" if has_handler else "unavailable",
            source=address,
            version=None,
            health="unverified",
            authority=_AUTHORITY,
            evidence_kind="measured",
            reason=None if has_handler else _NO_HANDLER_REASON,
        )


__all__ = ["CommandRegistry"]
