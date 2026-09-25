"""Parity guards for the existing command and capability discovery planes.

The command catalog is the public name surface, while ``SlashCommandRegistry``
is the executable handler surface.  These tests keep the two honest: every
concrete handler has a catalog name (colon spellings normalize to the same
command), and every catalog definition points at a capability whose module and
target really exist.
"""

from __future__ import annotations

import importlib

from alpha.capabilities.catalog import CAPABILITY_CATALOG
from alpha.commands import backend_handlers  # noqa: F401 - binds the concrete handlers
from alpha.commands.catalog import get_default_catalog_entries
from alpha.commands.registry import COMMAND_CAPABILITY_ID, command_registry


def _canonical(command: str) -> str:
    """Collapse the registry's colon spelling to the catalog's space spelling."""
    return command.replace(":", " ", 1)


def _production_handler_commands() -> list[str]:
    """Ignore test/extension registrations left on the process-global registry."""
    return [command for command in command_registry.handler_commands() if getattr(command_registry._handlers.get(command), "__module__", "").startswith("alpha.commands.")]


def test_every_concrete_command_handler_has_a_catalog_definition() -> None:
    catalog_names = {row[0] for row in get_default_catalog_entries()}
    concrete_names = {_canonical(command) for command in _production_handler_commands()}

    missing = sorted(concrete_names - catalog_names)
    assert not missing, f"implemented command handlers are absent from the catalog: {missing}"

    for command in sorted(concrete_names):
        definition = command_registry.get(command)
        assert definition is not None, f"catalog name {command!r} does not resolve in the registry"
        assert definition.registered is True, f"catalog command {command!r} is not honestly registered"
        assert any(_canonical(bound) == command for bound in _production_handler_commands()), f"catalog command {command!r} has no concrete handler"


def test_compatibility_aliases_are_honestly_marked_when_not_catalog_rows() -> None:
    catalog_names = {row[0] for row in get_default_catalog_entries()}
    aliases = [command for command in _production_handler_commands() if command not in catalog_names]

    for alias in aliases:
        definition = command_registry.get(alias)
        assert definition is not None
        assert definition.registered is False, f"non-catalog compatibility alias {alias!r} must not claim catalog registration"
        assert _canonical(alias) in catalog_names


def test_every_catalog_entry_maps_to_a_real_capability() -> None:
    for row in get_default_catalog_entries():
        command = row[0]
        definition = command_registry.get(command)
        assert definition is not None, f"catalog command {command!r} is missing from the registry"
        assert definition.capability == COMMAND_CAPABILITY_ID
        assert definition.registered is True

        capability_id = definition.capability
        capability = CAPABILITY_CATALOG.get(capability_id)
        assert capability is not None, f"{command!r} points at missing capability {capability_id!r}"
        module = importlib.import_module(capability.module)
        assert hasattr(module, capability.target), f"capability {capability_id!r} target {capability.module}:{capability.target} is not real"
