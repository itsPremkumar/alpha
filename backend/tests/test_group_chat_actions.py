"""The ``group_chat`` action set must be closed and fully dispatched.

Modelled on the ``war_room`` invariant in ``test_war_room_honesty.py``: an
``action`` that exists in the ``Literal`` but has no branch in the dispatcher
returns "unknown action" for a *schema-valid* call. The model is told the tool
offers it, the tool then refuses it, and the failure looks like a bug in the
model rather than in the schema.

Scoped to ``group_chat`` on purpose. A repository-wide AST sweep is tempting
but wrong: several builtins dispatch through shapes this check does not model
(``deliberate`` is the ``Literal``'s own default and is reached by falling out
of the chain; ``code_agentic_core`` dispatches through a handler map), so a
generic sweep would flag correct pre-existing code and train everyone to
ignore it.
"""

from __future__ import annotations

import importlib
import inspect

# `alpha.tools.builtins.group_chat_tool` resolves to the *tool object*, not the
# module, because the decorator rebinds the module-level name. `importlib` by
# dotted name gets the module itself.
group_chat_tool_mod = importlib.import_module("alpha.tools.builtins.group_chat_tool")

TOOL_FUNC = group_chat_tool_mod.group_chat_tool.func

EXPECTED_ACTIONS = (
    "send",
    "create",
    "list",
    "history",
    "propose_vote",
    "cast_vote",
    "tally_vote",
    "status",
    "claim",
    "release",
)


def test_every_advertised_action_is_handled_in_the_dispatcher() -> None:
    """Each name appears in the closed Literal *and* in the dispatcher body.

    The Literal renders with single quotes and the dispatcher uses double, so
    a bare substring test would be wrong for one of the two. Accept either.
    """
    annotation = str(inspect.signature(TOOL_FUNC).parameters["action"].annotation)
    source = inspect.getsource(TOOL_FUNC)
    for action in EXPECTED_ACTIONS:
        quoted = ("'" + action + "'") in annotation or ('"' + action + '"') in annotation
        assert quoted, f"{action} is not in the closed action Literal: {annotation}"
        assert '"' + action + '"' in source, f"{action} is advertised but never handled in the dispatcher"


def test_status_and_claim_do_not_need_a_claim_id_or_subject_to_be_advertised() -> None:
    """`status`/`claim`/`release` take their own params, not `proposal_id`.

    A shared-parameter design would make `status` require a proposal id, which
    is exactly the kind of schema friction that makes a model skip a useful
    read. The new actions carry `subject` / `claim_id` instead.
    """
    params = inspect.signature(TOOL_FUNC).parameters
    assert "subject" in params
    assert "claim_id" in params
    assert params["subject"].default == ""
    assert params["claim_id"].default == ""


def test_claim_rejects_an_empty_subject_rather_than_claiming_nothing() -> None:
    payload = group_chat_tool_mod.group_chat_tool.invoke({"action": "claim", "room_name": "nope", "subject": "  "})
    assert "subject" in payload.lower()
