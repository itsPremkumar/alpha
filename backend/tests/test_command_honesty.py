"""A slash command that does nothing must not report that it succeeded.

The defect
----------
``SlashCommandRegistry._dispatch`` had a fall-through for any catalog row with no
bound handler. It returned::

    status = "success"
    output  = "Directive /judge accepted [communication]. Runs independent judge ..."

Nothing ran. An operator who typed ``/judge``, and a model that called it through
``execute_slash_command``, were both told the command had been accepted. Measured
over the catalog, that was 406 rows reporting success for doing nothing, and the
count was pinned as an expectation by ``test_discovery_plane_parity.py``, which
made the suite certify the lie.

What this file holds
--------------------
The invariant, stated once and checked over the whole catalog rather than a
sample: **no row reports success without a handler bound to it.** No count can
satisfy this. If the count changes, the count test moves and this one does not.

The measured facts the invariant rests on are recorded in
``docs/COMMAND_HONESTY.md`` and in ``test_discovery_plane_parity.py``.
"""

from __future__ import annotations

import pytest

from alpha.commands import backend_handlers  # noqa: F401 - binds the handlers
from alpha.commands.catalog import get_default_catalog_entries
from alpha.commands.registry import UNIMPLEMENTED_STATUS, command_registry


def _catalog_names() -> list[str]:
    """Unique catalog names, which is what the command-keyed registry stores."""
    seen: dict[str, None] = {}
    for row in get_default_catalog_entries():
        seen.setdefault(row[0], None)
    return list(seen)


def _no_handler_rows() -> list[str]:
    """Catalog rows with no bound handler, under any production module."""
    canonical = {c.replace(":", " ", 1): c for c in command_registry._handlers}
    return [row for row in _catalog_names() if row not in canonical]


def _handler_backed_rows() -> list[str]:
    canonical = set(c.replace(":", " ", 1) for c in command_registry._handlers)
    return [row for row in _catalog_names() if row in canonical]


# ── the invariant ───────────────────────────────────────────────────────


def test_the_no_handler_population_is_not_empty() -> None:
    """Precondition. If this fails the other tests here are vacuous.

    It also means a future change that gives every row a handler has to delete
    this file on purpose rather than by accident.
    """
    assert len(_no_handler_rows()) > 0, "every catalog row now has a handler; this file's premise is gone"
    assert len(_handler_backed_rows()) > 0, "no catalog row has a handler; the registry is entirely inert"


@pytest.mark.parametrize("row", _no_handler_rows())
def test_no_handler_row_never_reports_success(row: str) -> None:
    """The core invariant, one assertion per catalog row.

    Parametrised over the whole population instead of a sample of probes: a
    sample cannot distinguish 407 honest rows from 406 honest rows and one liar,
    which is precisely the failure this test exists to prevent.
    """
    assert not command_registry.has_handler(row), f"{row} gained a handler and no longer belongs in this set"

    result = command_registry.execute(row)

    assert result.status not in {"success", "ok"}, f"{row} has no handler but reported status={result.status!r}. output={result.output[:160]!r}"


@pytest.mark.parametrize("row", _no_handler_rows())
def test_no_handler_row_discloses_that_nothing_executed(row: str) -> None:
    """The answer must be machine-readable, not only readable prose.

    Rows that are *also* approval-gated answer ``approval_required`` instead: the
    gate is the more specific fact and it is checked first, so it is still an
    honest refusal to do anything. Both are covered by the invariant above; this
    test only pins the shape of the payload.
    """
    result = command_registry.execute(row)

    assert result.status in {UNIMPLEMENTED_STATUS, "approval_required"}, f"{row} answered {result.status!r}"
    assert result.data["executed"] is False, f"{row} must say it did not execute"
    if result.status == UNIMPLEMENTED_STATUS:
        assert result.data["has_handler"] is False
        assert result.data["not_implemented"] is True


def test_the_unimplemented_status_is_distinct_from_every_other_status() -> None:
    """A consumer switching on status must be able to tell these apart.

    Guards against the failure mode where `unimplemented` is introduced as an
    alias of `success` for compatibility, which would reintroduce the lie in the
    one field that is actually read.
    """
    other_statuses = {"success", "ok", "error", "not_found", "approval_required", "timeout"}
    assert UNIMPLEMENTED_STATUS not in other_statuses, "the no-handler status collides with a status that means something ran"


def test_a_handler_backed_row_still_reports_success() -> None:
    """The guard above must not be satisfiable by making every row non-success."""
    result = command_registry.execute("/doctor")

    assert result.status == "success", result.output
    assert result.data.get("not_implemented") is not True
    assert "no handler" not in result.output.lower()


def test_an_unknown_command_still_reports_not_found() -> None:
    """Distinct from both success and unimplemented: it was never recognised."""
    result = command_registry.execute("/definitely-not-a-real-command")

    assert result.status == "not_found"
    assert result.status != UNIMPLEMENTED_STATUS


def test_the_approval_gate_still_outranks_the_unimplemented_status() -> None:
    """/security lockdown has no handler AND is approval-gated.

    It must answer `approval_required`, not `unimplemented`: the two facts are
    different and the caller acts on them differently.
    """
    result = command_registry.execute("/security lockdown")

    assert result.status == "approval_required"
    assert result.data["executed"] is False


# ── the false claim, stated positively ──────────────────────────────────


@pytest.mark.parametrize("row", ["/judge", "/help", "/status", "/plan"])
def test_a_named_placeholder_no_longer_claims_the_command_was_accepted(row: str) -> None:
    """/judge is the row the audit named. It is an ordinary no-op, not a special one.

    The old wording asserted acceptance. Nothing may restore a phrase that tells
    a caller work was taken on when none was.
    """
    result = command_registry.execute(row)

    assert "accepted" not in result.output.lower(), f"{row} still claims its directive was accepted"
    assert "directive" not in result.output.lower(), f"{row} still speaks in the old directive-accepted voice"
    assert "nothing was executed" in result.output


def test_no_catalog_description_is_echoed_back_as_if_it_were_a_result() -> None:
    """The old output ended with the row's description, reading as an outcome.

    A description of what a command would do must not be returned by a
    dispatch that did nothing, because that is indistinguishable from a
    dispatch that did.
    """
    for row in ("/judge", "/swarm", "/memory"):
        description = command_registry.get(row).description
        result = command_registry.execute(row)
        # The description may legitimately be quoted as *documentation*, but it
        # must not be the whole message; the refusal has to lead.
        assert result.output != description
        assert result.output.startswith(f"{row} is a catalogued command")


# ── consumers ───────────────────────────────────────────────────────────


def test_the_gateway_verdict_says_no_handler_rather_than_success() -> None:
    from app.gateway.routers.commands import _verdict_for

    for row in ("/judge", "/help"):
        verdict = _verdict_for(command_registry.execute(row).to_dict())
        assert verdict == "not_executed_no_handler", f"{row} reads as {verdict!r} over HTTP"
        assert verdict != "succeeded"


def test_the_gateway_verdict_still_reports_a_real_handler_as_succeeded() -> None:
    from app.gateway.routers.commands import _verdict_for

    assert _verdict_for(command_registry.execute("/doctor").to_dict()) == "succeeded"


def test_the_model_facing_tool_hands_the_request_back_to_the_model() -> None:
    """The caller asymmetry, made explicit.

    A model calling `/judge` can do the work itself, so its tool must say so.
    A human operator posting `/judge` gets the HTTP verdict instead, which does
    not promise anyone will pick it up. One status, two truthful renderings.
    """
    from alpha.tools.builtins.autonomous_command_tool import execute_slash_command_tool

    out = execute_slash_command_tool.invoke({"command_line": "/judge"})

    verdict = next(line for line in out.splitlines() if line.startswith("VERDICT: "))
    assert "NOT EXECUTED" in verdict, verdict
    assert "nothing ran" in verdict, verdict
    # The model-facing half of the asymmetry: the work is handed back to it.
    assert "do it directly with the available tools" in verdict, verdict


def test_the_model_facing_tool_never_reports_succeeded_for_a_no_op() -> None:
    from alpha.tools.builtins.autonomous_command_tool import execute_slash_command_tool

    for row in ("/judge", "/memory", "/swarm"):
        out = execute_slash_command_tool.invoke({"command_line": row})
        assert "VERDICT: SUCCEEDED" not in out, f"{row} reads as SUCCEEDED to the model\n{out}"


# ── the guard must be able to bite ──────────────────────────────────────


def _violates_invariant(result) -> bool:
    """The per-row invariant, as a single callable.

    Deliberately the same predicate the suite applies to every catalog row. If a
    future edit weakens this function, the per-row tests weaken with it and the
    bite-proof below stops meaning anything.
    """
    return result.status in {"success", "ok"} and not command_registry.has_handler(result.command)


def test_the_invariant_is_satisfied_by_the_real_registry() -> None:
    """Precondition for the bite-proof: the shipped registry passes."""
    for row in _no_handler_rows():
        assert not _violates_invariant(command_registry.execute(row)), row


def test_the_invariant_bites_a_registry_that_reintroduces_the_defect() -> None:
    """Proof the guard is load-bearing rather than vacuously green.

    Builds the original fall-through exactly as it was — ``status="success"`` and
    the directive-accepted echo, with no handler bound — and asserts the same
    predicate rejects it. This is what a "the invariant holds" test would look
    like if the predicate were decorative.

    The mutant is a standalone object, so the process-wide registry is untouched.
    """
    from alpha.commands.registry import CommandExecutionResult

    # The original defect, verbatim in shape.
    lying_result = CommandExecutionResult(
        status="success",
        command="/judge",
        output="Directive /judge accepted [communication]. Runs independent judge over competing answers",
        data={"category": "communication", "arguments": "", "is_core": False},
    )

    assert not command_registry.has_handler("/judge"), "precondition: /judge really has no handler"
    assert _violates_invariant(lying_result) is True, "the invariant failed to catch a success reported for a row with no handler; the per-row tests cannot be trusted if this passes"


def test_the_invariant_would_fail_on_the_original_registry_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end bite proof: put the defect back into the real dispatch path.

    The no-handler branch is a closure inside ``execute``, so the honest way to
    reintroduce the original defect is to patch the one name it reads:
    ``UNIMPLEMENTED_STATUS`` is looked up in the module namespace at call time,
    so rebinding it to ``"success"`` restores exactly the old behaviour — the
    fall-through answers ``status="success"`` for a row with no handler, through
    the production code path, with no other edit.

    Then the verbatim body of ``test_no_handler_row_never_reports_success`` is run
    and is required to fail. If it does not raise here, it would not raise there.
    """
    from alpha.commands import registry as registry_mod

    # Precondition: the status really is resolved from the module globals at call
    # time (it is not captured in a default argument or a closure cell), which is
    # what makes this a mutation of the production path rather than a separate
    # imitation of it.
    assert registry_mod.SlashCommandRegistry.execute.__module__ == "alpha.commands.registry"
    assert "UNIMPLEMENTED_STATUS" not in registry_mod.SlashCommandRegistry.execute.__code__.co_freevars
    assert "UNIMPLEMENTED_STATUS" not in (registry_mod.SlashCommandRegistry.execute.__defaults__ or ())

    monkeypatch.setattr(registry_mod, "UNIMPLEMENTED_STATUS", "success", raising=True)

    result = command_registry.execute("/judge")

    assert result.status == "success", "precondition: the mutation really did reintroduce the lie"
    assert not command_registry.has_handler("/judge"), "precondition: /judge still has no handler"

    # This is the body of test_no_handler_row_never_reports_success for one row.
    with pytest.raises(AssertionError, match="has no handler but reported status"):
        assert result.status not in {"success", "ok"}, f"{'/judge'} has no handler but reported status={result.status!r}. output={result.output[:160]!r}"

    # NOTE, recorded here because it was measured rather than assumed: the
    # `data["executed"] is False` disclosure SURVIVES this mutation, because
    # `executed` is a separate literal in the payload rather than a value derived
    # from the status. Two independent fields therefore disagree under a partial
    # regression, and the status is the one the suite treats as authoritative.
    # That is why test_no_handler_row_never_reports_success keys on the status and
    # not on `executed`.
