"""Two-way parity guards for the command and capability discovery planes.

The slash-command catalog (``alpha.commands.catalog``) is the public *name*
surface.  ``SlashCommandRegistry`` is the *executable* surface, and a catalog
row with no bound handler does nothing.  It used to answer ``execute()`` with
``status="success"`` and ``"Directive /help accepted [...]"`` — a command that did
nothing, reported as having succeeded.  It now answers
``status="unimplemented"`` with ``data.executed is False``.

``IMPLEMENTED_COMMANDS`` is this repository's honest equivalent of the reviewed
branch's ``registered=True`` flag: the catalog rows the repository claims to
actually serve.  It is checked in *both* directions:

* a row declared here without a bound handler is a false claim -> red;
* a catalog row that grew a real handler without being declared here is an
  unclassified capability -> red, and declaring it also gives it its own
  execution assertion through the parametrised dispatch test.

The remaining catalog rows have no handler.  That is a real gap in the product
and this file deliberately does **not** pretend otherwise: it pins the gap's size
(``NO_HANDLER_ROW_COUNT``) so it cannot grow unnoticed, and pins that none of
those rows claims to have done anything
(``test_no_handler_row_reports_itself_as_unimplemented``, and
``test_command_honesty.py`` for the whole population).  The size is an inventory;
the honesty rule is the invariant.

An earlier revision pinned ``PLACEHOLDER_ROW_COUNT = 410`` and asserted that those
rows returned the directive-accepted success.  That test certified the defect, and
its count was itself wrong by three because the production-handler filter
excluded ``alpha.mission.goalloop.bindings``.  ``UNPUBLISHED_ALIASES`` and
``DUPLICATE_CATALOG_ROWS`` pin two further pre-existing catalog defects; they are
likewise inventories of known debt, not endorsements.  The ability plane
(``alpha.capabilities.catalog``) is held to the same "must really exist" rule.
"""

from __future__ import annotations

import importlib
from collections import Counter

import pytest

from alpha.capabilities.catalog import CAPABILITY_CATALOG
from alpha.commands import backend_handlers  # noqa: F401 - binds the handlers
from alpha.commands.catalog import get_default_catalog_entries
from alpha.commands.registry import UNIMPLEMENTED_STATUS, CommandExecutionResult, command_registry

# Catalog rows the repository claims to serve with a concrete handler.  Adding a
# row here is a claim; the tests below prove the claim and add its execution
# assertion.  Removing one without unbinding the handler is also red.
IMPLEMENTED_COMMANDS: frozenset[str] = frozenset(
    {
        # APEX autopilot: bound in alpha/apex/commands.py, published in
        # catalog.py. `/apex` alone is a read-only status read, never an
        # enable. The session-control verbs (pause..verify) act on the
        # active session for the conversation, never on a caller-supplied
        # row.
        "/apex",
        "/apex off",
        "/apex on",
        "/apex policy",
        "/apex status",
        "/apex approve",
        "/apex pause",
        "/apex reject",
        "/apex replan",
        "/apex resume",
        "/apex steer",
        "/apex stop",
        "/apex take-over",
        "/apex verify",
        "/boost",
        "/compact",
        "/compress",
        "/doctor",
        "/goal",
        "/goal clear",
        "/goal create",
        "/goal decompose",
        "/goal status",
        "/goal verify",
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

# Unique catalog rows with no bound handler of any kind.  Measured, not assumed:
# see ``test_no_handler_row_count_is_exact`` for the derivation, and
# ``docs/COMMAND_HONESTY.md`` for the measurement.
#
# This number is NOT an endorsement. It is the size of a known gap, pinned so it
# cannot grow unnoticed: a new catalog row without a handler must be noticed. The
# *honesty* of these rows is enforced separately and absolutely, by
# ``test_no_handler_row_reports_itself_as_unimplemented`` below and by
# ``test_command_honesty.py``, which cannot be satisfied by any count. The
# previous revision of this file pinned 410 and asserted that those rows answer
# ``status="success"`` with "Directive /help accepted [...]" — a test that
# certified the lie. 410 was also wrong: it excluded the 14 production handlers
# bound by ``alpha.mission.goalloop.bindings``.
NO_HANDLER_ROW_COUNT = 407
# The 14 ``/apex`` rows added for the autopilot are bound to real
# handlers, so they deliberately do NOT move this number. Shipping
# working commands is not a reason the known gap shrank; only closing
# handler-less rows may do that.

# Rows executed per run to prove the handler-less path behaves as documented
# instead of quietly becoming a real handler.
NO_HANDLER_PROBES: tuple[str, ...] = ("/help", "/status", "/plan")

# KNOWN DEFECT, pinned so it cannot grow: 32 handler bindings resolve to names
# the catalog never publishes, so they are reachable but undiscoverable.  The
# registry keys on the name, so a colon spelling and its space spelling are two
# bindings for one intent.  The previous count of 21 missed the 11 goal-loop
# aliases, for the same reason the row count was wrong.
UNPUBLISHED_ALIASES: frozenset[str] = frozenset(
    {
        # alpha.commands.backend_handlers
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
        # alpha.mission.goalloop.bindings. These were invisible to the previous
        # revision of this inventory, which filtered production handlers on
        # `alpha.commands.*` and so never saw the goal-loop bindings at all.
        "/goal draft",
        "/goal gate",
        "/goal gate add",
        "/goal gate clear",
        "/goal gate list",
        "/goal gate remove",
        "/goal show",
        "/goal step",
        "/subgoal",
        "/subgoal clear",
        "/subgoal remove",
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
    """Handlers bound by production code, ignoring test-only registrations.

    The module filter used to be ``alpha.commands.*``, which silently dropped the
    14 handlers bound by ``alpha.mission.goalloop.bindings`` — a different package
    that is still production code reached through ``alpha.commands.__init__``.
    Excluding them made three handler-backed catalog rows (``/goal``, ``/goal
    clear``, ``/goal verify``) look like no-ops, and inflated
    NO_HANDLER_ROW_COUNT by 3. Production is now "anything not defined in a test
    module".
    """
    return {command: handler for command, handler in command_registry._handlers.items() if not getattr(handler, "__module__", "").startswith("tests")}


def _handler_backed_catalog_rows() -> set[str]:
    catalog = set(_catalog_names())
    return {_canonical(command) for command in _production_handlers() if _canonical(command) in catalog}


def _no_handler_catalog_rows() -> list[str]:
    catalog = _catalog_names()
    handler_backed = _handler_backed_catalog_rows()
    return [row for row in catalog if row not in handler_backed]


def _reports_success_without_a_handler(result: CommandExecutionResult) -> bool:
    """The defect, as a predicate: a success status for a row with no handler.

    Deliberately structural rather than string-matching. The old predicate
    recognised the lie by its exact wording ("Directive ... accepted [...]"), so
    rewording the lie would have satisfied it.
    """
    return result.status in {"success", "ok"} and not command_registry.has_handler(result.command)


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
    assert not without_handler, f"declared implemented catalog rows have no concrete handler and would answer as unimplemented: {without_handler}"


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
    assert not _reports_success_without_a_handler(result), f"{command} still reports success without a handler"
    assert result.data == {"parity_probe": True}


def test_no_handler_row_count_is_exact() -> None:
    """The known-gap count is pinned so the gap cannot grow silently.

    This is an inventory of debt, not an approval: the rows it counts must also
    satisfy the honesty invariant in ``test_command_honesty.py``, which no count
    can satisfy.
    """
    catalog = _catalog_names()
    handler_backed = _handler_backed_catalog_rows()
    no_handler = _no_handler_catalog_rows()

    # 426 -> 431 -> 440: the fourteen `/apex` rows. Every one is
    # handler-backed, so the known-gap count above is unchanged.
    assert len(catalog) == 440, f"the unique catalog row count changed: {len(catalog)}"
    assert len(no_handler) == NO_HANDLER_ROW_COUNT, (
        f"{len(catalog)} unique catalog rows, {len(handler_backed)} with handlers, "
        f"{len(no_handler)} with none (expected {NO_HANDLER_ROW_COUNT}). A new catalog "
        "row without a handler is a new claim about the product: give it a handler and "
        "declare it in IMPLEMENTED_COMMANDS, or move this count on purpose."
    )
    for row in IMPLEMENTED_COMMANDS:
        assert row not in no_handler, f"{row} is declared implemented but has no handler"


@pytest.mark.parametrize("command", NO_HANDLER_PROBES)
def test_no_handler_row_reports_itself_as_unimplemented(command: str) -> None:
    """A row with no handler must say so in the payload, not in prose alone."""
    assert command not in command_registry._handlers

    result = command_registry.execute(command)

    assert result.status == UNIMPLEMENTED_STATUS
    # Distinguishable in the payload, so no consumer has to parse `output`.
    assert result.data["executed"] is False
    assert result.data["has_handler"] is False
    assert result.data["not_implemented"] is True


def test_every_no_handler_row_is_counted_and_disclosed() -> None:
    """The count and the population agree, for the whole catalog.

    Pinned on every row rather than a sample: a test that executes three probes
    cannot tell 407 honest rows from 406 honest ones and one liar.
    """
    catalog = set(_catalog_names())
    handler_backed = _handler_backed_catalog_rows()
    no_handler = _no_handler_catalog_rows()

    assert len(no_handler) == len(catalog) - len(handler_backed), "the no-handler set is not the catalog minus the handler-backed rows"
    assert no_handler, "every catalog row suddenly has a handler; retire NO_HANDLER_ROW_COUNT on purpose"
    for row in no_handler:
        assert row in catalog, f"{row} is counted as a no-op but is not a catalog row"


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
