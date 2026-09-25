"""Two-way parity guards for the command and capability discovery planes.

The slash-command catalog (``alpha.commands.catalog``) is the public *name*
surface.  ``SlashCommandRegistry`` is the *executable* surface, and a catalog
row with no bound handler still answers ``execute()`` with
``status="success"`` and ``"Directive /help accepted [...]"``.  That default path
is a placeholder.  A placeholder nobody has classified is how a catalog quietly
overstates what the product can do, which is the exact hole the one-way version
of this test left open.

``IMPLEMENTED_COMMANDS`` is this repository's honest equivalent of the reviewed
branch's ``registered=True`` flag: the catalog rows the repository claims to
actually serve.  It is checked in *both* directions:

* a row declared here without a bound handler is a false claim -> red;
* a catalog row that grew a real handler without being declared here is an
  unclassified capability -> red, and declaring it also gives it its own
  execution assertion through the parametrised dispatch test.

The remaining catalog rows are placeholders.  They are counted rather than
listed, and a sample of them is executed so the placeholder path cannot change
shape unnoticed.  ``UNPUBLISHED_ALIASES`` and ``DUPLICATE_CATALOG_ROWS`` pin two
pre-existing catalog defects so neither can grow silently; they are inventories
of known debt, not endorsements, and the ability plane
(``alpha.capabilities.catalog``) is held to the same "must really exist" rule.
"""

from __future__ import annotations

import importlib
from collections import Counter

import pytest

from alpha.capabilities.catalog import CAPABILITY_CATALOG
from alpha.commands import backend_handlers  # noqa: F401 - binds the handlers
from alpha.commands.catalog import get_default_catalog_entries
from alpha.commands.registry import CommandExecutionResult, command_registry

# Catalog rows the repository claims to serve with a concrete handler.  Adding a
# row here is a claim; the tests below prove the claim and add its execution
# assertion.  Removing one without unbinding the handler is also red.
IMPLEMENTED_COMMANDS: frozenset[str] = frozenset(
    {
        "/boost",
        "/compact",
        "/compress",
        "/doctor",
        "/goal create",
        "/goal decompose",
        "/goal status",
        "/grill-me",
        "/learn",
        "/moa",
        "/mode",
        "/schedule",
        "/self-heal",
        "/skills create",
        "/teamwork-preview",
        "/usage",
    }
)

# Unique catalog rows (426) minus the 16 implemented ones: rows with no handler
# that fall through to the "Directive accepted" placeholder.  Pinned so a new
# catalog row cannot land as an unclassified claim.
PLACEHOLDER_ROW_COUNT = 410

# Placeholder rows executed per run to prove the fallback path still behaves as
# documented instead of quietly becoming a real handler.
PLACEHOLDER_PROBES: tuple[str, ...] = ("/help", "/status", "/goal")

# KNOWN DEFECT, pinned so it cannot grow: 21 handler bindings resolve to names
# the catalog never publishes, so they are reachable but undiscoverable.  The
# registry keys on the name, so a colon spelling and its space spelling are two
# bindings for one intent.
UNPUBLISHED_ALIASES: frozenset[str] = frozenset(
    {
        "/loop pause",
        "/loop resume",
        "/loop start",
        "/loop status",
        "/loop:pause",
        "/loop:resume",
        "/loop:start",
        "/loop:status",
        "/security review",
        "/security-review",
        "/skill create",
        "/skill list",
        "/skill test",
        "/skill:create",
        "/skill:list",
        "/skill:test",
        "/skills list",
        "/subagent list",
        "/subagent spawn",
        "/subagent:list",
        "/subagent:spawn",
    }
)

# KNOWN DEFECT, pinned so it cannot grow: the catalog lists 428 rows for 426
# unique commands, so the first row registered for a duplicated name is silently
# discarded by the registry's command-keyed dict.
DUPLICATE_CATALOG_ROWS: frozenset[str] = frozenset({"/learn", "/usage"})


def _canonical(command: str) -> str:
    """Collapse the registry's colon spelling to the catalog's space spelling."""
    return command.replace(":", " ", 1)


def _catalog_names() -> list[str]:
    """Unique catalog names, which is what the command-keyed registry stores."""
    seen: dict[str, None] = {}
    for row in get_default_catalog_entries():
        seen.setdefault(row[0], None)
    return list(seen)


def _production_handlers() -> dict[str, object]:
    """Handlers bound by production code, ignoring test-only registrations."""
    return {command: handler for command, handler in command_registry._handlers.items() if getattr(handler, "__module__", "").startswith("alpha.commands.")}


def _handler_backed_catalog_rows() -> set[str]:
    catalog = set(_catalog_names())
    return {_canonical(command) for command in _production_handlers() if _canonical(command) in catalog}


def _is_placeholder(result: CommandExecutionResult) -> bool:
    """Recognise the registry's default "directive accepted" success path."""
    return result.status == "success" and result.output.startswith("Directive ") and "accepted [" in result.output and "result" not in result.data


def test_production_handlers_are_bound() -> None:
    handlers = _production_handlers()
    assert handlers, "no production slash-command handlers were bound at import time"


def test_every_handler_binding_is_a_catalog_row_or_a_declared_alias() -> None:
    """Handler -> catalog: an undeclared non-catalog name is an undiscoverable leak."""
    catalog = set(_catalog_names())
    leaked_keys = sorted(command for command in _production_handlers() if command not in UNPUBLISHED_ALIASES and command not in catalog and _canonical(command) not in catalog)
    assert not leaked_keys, f"handlers are bound to names the catalog does not publish and that are not declared in UNPUBLISHED_ALIASES: {leaked_keys}"


def test_unpublished_alias_inventory_is_exact() -> None:
    """The pinned alias inventory must not drift in either direction."""
    catalog = set(_catalog_names())
    actual = {command for command in _production_handlers() if command not in catalog and _canonical(command) not in catalog}
    assert actual == set(UNPUBLISHED_ALIASES), (
        f"unpublished alias bindings changed. Add the new name to UNPUBLISHED_ALIASES deliberately, or publish it in the catalog. missing={sorted(set(UNPUBLISHED_ALIASES) - actual)} unexpected={sorted(actual - set(UNPUBLISHED_ALIASES))}"
    )


def test_every_declared_command_is_a_catalog_row_with_a_bound_handler() -> None:
    """Catalog -> handler, for every row the repository claims to serve."""
    catalog = set(_catalog_names())
    missing_from_catalog = sorted(IMPLEMENTED_COMMANDS - catalog)
    assert not missing_from_catalog, f"declared implemented commands are not catalog rows: {missing_from_catalog}"

    without_handler = sorted(command for command in IMPLEMENTED_COMMANDS if command not in command_registry._handlers)
    assert not without_handler, f"declared implemented catalog rows have no concrete handler and would silently return the directive-accepted placeholder: {without_handler}"


def test_every_handler_backed_catalog_row_is_declared_implemented() -> None:
    """The declaration cannot rot: a new concrete row must be classified."""
    undeclared = sorted(_handler_backed_catalog_rows() - IMPLEMENTED_COMMANDS)
    assert not undeclared, f"catalog rows gained a concrete handler but are not declared in IMPLEMENTED_COMMANDS, so they have no execution assertion: {undeclared}"


@pytest.mark.parametrize("command", sorted(IMPLEMENTED_COMMANDS))
def test_declared_command_dispatches_to_its_handler(command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """One execution assertion per concrete row: dispatch must reach a handler.

    The handler is replaced by a recorder so the assertion is about dispatch
    parity rather than about whatever a real handler does when executed.
    """
    calls: list[tuple[str, object]] = []

    def _recorder(args: str, context: object = None) -> CommandExecutionResult:
        calls.append((args, context))
        return CommandExecutionResult(
            status="success",
            command=command,
            output=f"handled {command}",
            data={"parity_probe": True},
        )

    assert command in command_registry._handlers, f"{command} lost its handler binding before the parity probe ran"
    monkeypatch.setitem(command_registry._handlers, command, _recorder)

    result = command_registry.execute(command)

    assert calls, f"{command} did not dispatch to its bound handler"
    assert not _is_placeholder(result), f"{command} still answers with the directive-accepted placeholder"
    assert result.data == {"parity_probe": True}


def test_placeholder_rows_are_exactly_the_rows_without_handlers() -> None:
    """The placeholder population is counted and cannot grow silently."""
    catalog = _catalog_names()
    handler_backed = _handler_backed_catalog_rows()
    placeholders = [row for row in catalog if row not in handler_backed]

    assert len(placeholders) == PLACEHOLDER_ROW_COUNT, (
        f"{len(catalog)} unique catalog rows, {len(handler_backed)} with "
        f"handlers, {len(placeholders)} directive-only placeholders (expected "
        f"{PLACEHOLDER_ROW_COUNT}). A new catalog row without a handler is a "
        "new claim about the product: give it a handler and declare it in "
        "IMPLEMENTED_COMMANDS, or move this count on purpose."
    )
    for row in IMPLEMENTED_COMMANDS:
        assert row not in placeholders, f"{row} is declared but is a placeholder"


@pytest.mark.parametrize("command", PLACEHOLDER_PROBES)
def test_placeholder_rows_still_take_the_disclosed_fallback_path(command: str) -> None:
    """A row without a handler is a documented placeholder, not a capability."""
    assert command not in command_registry._handlers

    result = command_registry.execute(command)

    assert _is_placeholder(result), f"{command} is catalogued without a handler but no longer returns the documented directive-accepted placeholder; declare it as implemented if that is intended"


def test_duplicate_catalog_rows_are_pinned() -> None:
    """Two catalog rows are declared twice; the pinned inventory must hold."""
    counts = Counter(row[0] for row in get_default_catalog_entries())
    duplicates = {name for name, count in counts.items() if count > 1}
    assert duplicates == set(DUPLICATE_CATALOG_ROWS), (
        "duplicated catalog rows changed. The registry keys on the command "
        "name, so the first row for a duplicated name is silently discarded. "
        f"missing={sorted(set(DUPLICATE_CATALOG_ROWS) - duplicates)} "
        f"unexpected={sorted(duplicates - set(DUPLICATE_CATALOG_ROWS))}"
    )


def test_every_capability_entry_maps_to_a_real_module_and_target() -> None:
    for capability_id, capability in CAPABILITY_CATALOG.items():
        module = importlib.import_module(capability.module)
        assert hasattr(module, capability.target), f"capability {capability_id!r} points at missing target {capability.module}:{capability.target}"
