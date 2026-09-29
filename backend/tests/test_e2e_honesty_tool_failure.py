"""Honesty 1/6 — a failing tool call is never recorded as a success.

The defect this pins
--------------------
A ``python_repl`` cell that raised ``ModuleNotFoundError`` was persisted with
``alpha_tool_meta = {"status": "success", ..., "recommended_next_action":
"continue"}`` — a success stamp on a body that literally said
``Error (ModuleNotFoundError): ...``.

How it was found
----------------
By driving the product and reading the durable record back, not by reading the
classifier. The agent transcript showed a REPL that kept failing; the run-event
store showed a tool that kept succeeding. The two were written by the same call
and nobody compared them.

Why a unit test could not have caught it
----------------------------------------
``agents/middlewares/tool_result_meta.py`` matched the literal string
``"Error:"``. The sandbox REPL does not emit that string: ``CellResult
.format_output()`` (``sandbox/repl/protocol.py:36``) emits
``f"Error ({self.error_name}): {self.error_value}"``. Every test in the repo fed
the classifier the *framework* convention, so the one convention the REPL
actually produces was never covered — and a test that only ever feeds
``"Error: ..."`` is a test of the shape the author imagined, not the shape the
product emits.

Why the wrong stamp is worse than a wrong label
-----------------------------------------------
``ToolProgressMiddleware`` reads ``alpha_tool_meta``, not the text. Its
anti-thrash state machine counts ``status in ("error", "partial_success")`` and
escalates ACTIVE -> WARNED -> BLOCKED. A stamp saying ``success``/``continue``
resets that counter to zero on every call, so the guard was blind to the entire
``python_repl`` tool family: a REPL that kept failing looked like a REPL that
kept succeeding. The tests below therefore drive the *real* tool, the *real*
classifier and the *real* guard, and assert the claim the product makes
(``alpha_tool_meta``) agrees with the durable body.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt import ToolRuntime

from alpha.agents.middlewares.tool_progress_middleware import ToolProgressMiddleware
from alpha.agents.middlewares.tool_result_meta import (
    TOOL_META_KEY,
    split_error_prefix,
)
from alpha.sandbox.repl.protocol import CellResult
from alpha.tools.builtins.python_repl_tool import python_repl_tool

#: The tool the anti-thrash guard tracks and does *not* exempt. ``python_repl``
#: is a real registered tool name (``tools/builtins/python_repl_tool.py``), and
#: ``ToolProgressMiddleware._exempt_tools`` defaults to
#: ``{ask_clarification, write_todos, present_files, task}`` — so the guard does
#: watch this family. That fact is asserted, not assumed; see
#: :func:`test_python_repl_is_not_an_exempt_tool`.
TOOL_NAME = "python_repl"

#: The framework convention, verbatim from ``tool_result_meta._ERROR_PREFIX``.
FRAMEWORK_ERROR_PREFIX = "Error:"


def _runtime() -> ToolRuntime:
    """A minimal ToolRuntime; ``runtime`` is an injected arg ToolNode fills in."""
    return ToolRuntime(
        state={},
        context={"thread_id": "honesty-tool-failure", "run_id": "run-1"},
        config={},
        stream_writer=lambda _: None,
        tool_call_id="tool-call-1",
        store=None,
    )


def _classify(content: str) -> dict:
    """Run the real classifier over a tool body and return its stamp.

    ``normalize_tool_message`` is exactly what
    ``ToolErrorHandlingMiddleware.awrap_tool_call`` calls (``tool_error_
    handling_middleware.py`` imports it and applies it to every tool result), so
    this is the production classification, not a re-implementation of it.
    """
    from alpha.agents.middlewares.tool_result_meta import normalize_tool_message

    message = ToolMessage(
        content=content,
        tool_call_id="tc-1",
        name=TOOL_NAME,
        additional_kwargs={},
    )
    normalize_tool_message(message)
    return dict(message.additional_kwargs[TOOL_META_KEY])


# ---------------------------------------------------------------------------
# The two error conventions that are actually in use
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_repl_error_body_is_not_stamped_as_a_success():
    """The regression, driven through the real tool.

    The body below is not a hand-written string: it is whatever
    ``python_repl_tool`` returned for a cell that really raised, executed by the
    real sandbox REPL. The historical bug was that ``"Error (Name): value"`` did
    not match the classifier's ``"Error:"`` prefix and fell through to the
    success default.
    """
    body = await python_repl_tool.ainvoke(
        {
            "code": "import alpha_module_that_does_not_exist_honesty_probe",
            "session_id": "honesty-repl-failure",
            "runtime": _runtime(),
        }
    )

    # Guard the premise: this really is the REPL's convention, not the
    # framework's. If the REPL ever changes shape, this test must fail loudly
    # rather than silently stop covering the case it was written for.
    assert body.startswith("Error ("), f"the REPL no longer emits 'Error (Name): value': {body[:120]!r}"
    assert not body.startswith(f"{FRAMEWORK_ERROR_PREFIX} "), "the REPL body no longer differs from the framework prefix"

    meta = _classify(body)
    assert meta["status"] == "error", f"a REPL cell that raised was stamped {meta!r}"
    assert meta["source"] == "tool_return"
    assert meta["error_type"] is not None


def test_the_framework_error_body_is_not_stamped_as_a_success():
    """The other convention, and the one the old test suite already covered."""
    meta = _classify(f"{FRAMEWORK_ERROR_PREFIX} File not found: /tmp/report.csv")
    assert meta["status"] == "error"
    assert meta["error_type"] == "not_found"
    assert meta["source"] == "tool_return"


def test_both_conventions_are_recognised_by_the_one_split_point():
    """`split_error_prefix` is the single place the two shapes are unified.

    `normalize_tool_message` and the exception path must not each re-derive the
    prefix, because the original defect was exactly that: one place matched
    ``"Error:"`` and the other did not exist.
    """
    assert split_error_prefix(f"{FRAMEWORK_ERROR_PREFIX} file not found") == "file not found"
    assert split_error_prefix("Error (ModuleNotFoundError): No module named 'x'") == "ModuleNotFoundError: No module named 'x'"
    # An empty exception class carries no information and must not render as ": x".
    assert split_error_prefix("Error (): boom") == "boom"
    # A body that merely *contains* the word is not an error marker.
    assert split_error_prefix("The report says: Error handling is documented") is None


def test_the_repl_dataclass_format_is_the_string_under_test():
    """Pin ``sandbox/repl/protocol.py`` as the producer of that shape.

    If the REPL's own formatter changes, the assertion above would be testing a
    string the product can no longer produce. This is a cheap, direct check of
    the real formatter rather than of a copy of it.
    """
    body = CellResult(
        status="error",
        error_name="NameError",
        error_value="name 'total' is not defined",
    ).format_output()
    assert body.startswith("Error (NameError): name 'total' is not defined")
    assert _classify(body)["status"] == "error"


# ---------------------------------------------------------------------------
# The claim must differ from the success case, or the anti-thrash guard is blind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_recommended_action_differs_from_the_success_case():
    """A failing call must never be told to ``continue``.

    This is the exact blindness the bug created: ``ToolProgressMiddleware``
    transitions on the *stamp*, so ``recommended_next_action == "continue"``
    beside a raised exception is indistinguishable, to the guard, from a
    healthy call.
    """
    failing = await python_repl_tool.ainvoke(
        {
            "code": "1 / 0",
            "session_id": "honesty-repl-failure",
            "runtime": _runtime(),
        }
    )
    assert failing.startswith("Error (ZeroDivisionError)"), failing[:120]

    succeeding = await python_repl_tool.ainvoke(
        {
            "code": "6 * 7",
            "session_id": "honesty-repl-failure",
            "runtime": _runtime(),
        }
    )
    assert "42" in succeeding

    failed_meta = _classify(failing)
    ok_meta = _classify(succeeding)

    assert ok_meta["status"] == "success", f"a real REPL success was mislabelled: {ok_meta!r}"
    assert ok_meta["recommended_next_action"] == "continue"

    assert failed_meta["status"] == "error"
    assert failed_meta["recommended_next_action"] != ok_meta["recommended_next_action"], "an exception and a clean result now share one recommendation, so the guard cannot tell them apart"


# ---------------------------------------------------------------------------
# The real guard, driven with the real classifier
# ---------------------------------------------------------------------------


def _request(runtime: ToolRuntime, tool_call_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": TOOL_NAME, "id": tool_call_id},
        runtime=runtime,
    )


def _driving_handler(body: str, stamp: dict | None = None):
    """A handler shaped like the production one: run the tool, then classify.

    ``ToolErrorHandlingMiddleware`` wraps the tool and applies
    ``normalize_tool_result`` to whatever it returns; that is the whole reason
    ``ToolProgressMiddleware`` can read a stamp at all. ``stamp`` overrides the
    classifier's verdict and exists only for the negative control below.
    """

    def handler(_request):
        message = ToolMessage(content=body, tool_call_id="tc-1", name=TOOL_NAME, additional_kwargs={})
        from alpha.agents.middlewares.tool_result_meta import normalize_tool_message

        normalize_tool_message(message)
        if stamp is not None:
            message.additional_kwargs = {TOOL_META_KEY: dict(stamp)}
        return message

    return handler


#: The exact stamp the historical bug wrote beside a raised REPL body. Quoted
#: from the defect description in ``tool_result_meta``'s own module comment, so
#: the control keeps describing the real failure even if that comment is edited.
HISTORICAL_BAD_STAMP = {
    "status": "success",
    "error_type": None,
    "recoverable_by_model": True,
    "recommended_next_action": "continue",
    "source": "content_analysis",
}


def test_python_repl_is_not_an_exempt_tool():
    """Premise check: the guard is supposed to be watching this family."""
    assert TOOL_NAME not in ToolProgressMiddleware()._exempt_tools


#: A real REPL cell whose body classifies as a *configuration* failure, i.e.
#: ``recoverable_by_model=False`` + ``recommended_next_action="stop"``. Written
#: as source the REPL really executes rather than as a canned string, so the
#: ``Error (Name): value`` shape is produced by ``sandbox/repl/protocol.py`` and
#: not by this file.
_UNRECOVERABLE_REPL_CELL = "raise RuntimeError('python_repl is not configured on this deployment')"


@pytest.mark.asyncio
async def test_repeated_repl_failures_warn_the_anti_thrash_guard():
    """End to end: real bodies -> real classifier -> real guard -> WARNED.

    A REPL that fails every call is the "anti-thrash" case by definition. If the
    classifier's stamp is what the guard reads, repeated failures must escalate
    the guard and raise ``consecutive_problems``. With the historical
    ``success``/``continue`` stamp they do not — see the control below, which
    is what makes this assertion mean something.
    """
    guard = ToolProgressMiddleware()  # production defaults: threshold 3, escalation 2
    runtime = _runtime()
    body = await python_repl_tool.ainvoke(
        {
            "code": "import alpha_module_that_does_not_exist_honesty_probe",
            "session_id": "honesty-repl-guard",
            "runtime": _runtime(),
        }
    )
    assert body.startswith("Error ("), body[:120]

    for index in range(5):
        guard.wrap_tool_call(_request(runtime, f"tc-{index}"), _driving_handler(body))

    state = guard._get_state("honesty-tool-failure", TOOL_NAME)
    assert state.phase == "warned", f"five consecutive REPL failures left the guard at {state.phase!r} (problems={state.consecutive_problems})"
    # The counter itself is the anti-thrash signal; it must have counted every
    # one of the five failures rather than resetting on each.
    assert state.consecutive_problems == 5, f"the guard counted {state.consecutive_problems} of 5 failures"


@pytest.mark.asyncio
async def test_an_unrecoverable_repl_failure_blocks_the_tool_immediately():
    """The stronger half of the same claim: a stop-grade failure blocks at once.

    ``ToolProgressMiddleware._assess_and_transition`` hard-blocks on
    ``recoverable_by_model=False`` and ``recommended_next_action="stop"`` before
    any stagnation counting. If the REPL convention bypassed the classifier
    (the historical defect), this could never happen for a REPL error — the
    stamp would say ``continue`` and the guard would see nothing to act on.
    """
    guard = ToolProgressMiddleware()
    runtime = _runtime()
    body = await python_repl_tool.ainvoke(
        {
            "code": _UNRECOVERABLE_REPL_CELL,
            "session_id": "honesty-repl-guard",
            "runtime": _runtime(),
        }
    )
    assert body.startswith("Error (RuntimeError)"), body[:160]

    meta = _classify(body)
    assert meta["status"] == "error", meta
    assert meta["recoverable_by_model"] is False, meta
    assert meta["recommended_next_action"] == "stop", meta

    guard.wrap_tool_call(_request(runtime, "tc-0"), _driving_handler(body))
    assert guard._get_state("honesty-tool-failure", TOOL_NAME).phase == "blocked"
    assert guard._get_block_reason(runtime, TOOL_NAME), "a blocked guard must be able to say why"


def test_the_historical_stamp_leaves_the_anti_thrash_guard_blind():
    """The negative control: this is what the bug looked like from the outside.

    Identical bodies, identical guard, only the stamp differs. With the
    historical ``success``/``continue`` stamp the guard never escalates — which
    is the proof that the passing tests above are measuring the classifier and
    not merely the guard's own arithmetic.
    """
    guard = ToolProgressMiddleware()
    runtime = _runtime()
    failing_body = "Error (ModuleNotFoundError): No module named 'pandas'"

    for index in range(5):
        guard.wrap_tool_call(_request(runtime, f"tc-{index}"), _driving_handler(failing_body, stamp=HISTORICAL_BAD_STAMP))

    state = guard._get_state("honesty-tool-failure", TOOL_NAME)
    assert state.phase == "active", f"the control no longer demonstrates the defect (phase={state.phase!r}); the real stamp must differ from the historical one for the other tests to mean anything"
    assert state.consecutive_problems == 0


def test_a_successful_repl_call_resets_the_guard():
    """A real success must clear the counter — the guard is not simply 'fail = bad'."""
    guard = ToolProgressMiddleware()
    runtime = _runtime()
    failing = "Error (ZeroDivisionError): division by zero"

    for index in range(3):
        guard.wrap_tool_call(_request(runtime, f"tc-{index}"), _driving_handler(failing))
    assert guard._get_state("honesty-tool-failure", TOOL_NAME).consecutive_problems == 3

    guard.wrap_tool_call(_request(runtime, "tc-ok"), _driving_handler("[result]\n42"))
    state = guard._get_state("honesty-tool-failure", TOOL_NAME)
    assert state.phase == "active"
    assert state.consecutive_problems == 0
