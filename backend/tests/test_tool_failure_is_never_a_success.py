"""A failing tool must never be recorded as a success.

The bug this file pins was found by driving the real agent, not by reading code.
A live `python_repl` call failed with::

    Error (ModuleNotFoundError): No module named 'bash'
    Traceback (most recent call last):
      ...

and the durable record beside that body said::

    {"status": "success", "error_type": null,
     "recoverable_by_model": true,
     "recommended_next_action": "continue",
     "source": "content_analysis"}

Root cause: `tool_result_meta` matched the literal prefix ``"Error:"``, while
`sandbox/repl/protocol.py` formats ``f"Error ({self.error_name}): {self.error_value}"``
— a space and a parenthesised exception class before the colon. So
``"Error (ModuleNotFoundError): ...".startswith("Error:")`` was False, the
classifier fell through every branch, and the success default stamped a
green result onto an exception.

Why it mattered beyond the label: ``ToolProgressMiddleware`` reads
``alpha_tool_meta`` (not the text) for stagnation accounting, so the anti-thrash
guard was blind to the entire ``python_repl`` family. A REPL failing repeatedly
measured as a REPL succeeding repeatedly.

The REPL's own message format is NOT changed here: ``Error (Name): value`` is
what the model reads, and other consumers may match on it.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import ToolMessage

from alpha.agents.middlewares.tool_result_meta import (
    TOOL_META_KEY,
    normalize_tool_result,
    split_error_prefix,
)

REPL_BODY = (
    "Error (ModuleNotFoundError): No module named 'bash'\n"
    "Traceback (most recent call last):\n"
    '  File "session.py", line 562, in _execute_body\n'
    "    exec(body_code, self.namespace)\n"
)


def _meta(body: str, *, status: str = "success", name: str = "python_repl") -> dict:
    message = ToolMessage(content=body, tool_call_id="call-1", name=name, status=status)
    stamped = normalize_tool_result(message)
    return dict(stamped.additional_kwargs.get(TOOL_META_KEY) or {})


def test_the_repl_error_shape_is_recognised_not_missed():
    """The regression itself: this exact body used to stamp a success."""
    assert split_error_prefix(REPL_BODY) is not None, "the REPL's error shape must be recognised"
    meta = _meta(REPL_BODY)
    assert meta["status"] == "error", f"a ModuleNotFoundError was recorded as {meta['status']!r}"
    assert meta["source"] == "tool_return"


def test_the_framework_error_shape_still_works():
    # The pre-existing convention must not regress while the other one is added.
    meta = _meta("Error: File not found: /mnt/user-data/workspace/nope.txt", name="read_file")
    assert meta["status"] == "error"
    assert meta["error_type"] == "not_found"


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        # Every REPL exception shape must classify, not just one.
        ("Error (ModuleNotFoundError): No module named 'bash'", "error"),
        ("Error (PermissionError): access denied", "error"),
        ("Error (SyntaxError): invalid syntax", "error"),
        ("Error (ZeroDivisionError): division by zero", "error"),
        ("Error (ValueError): bad input", "error"),
        # An unnamed parenthesis carries no information; the plain shape is used.
        ("Error (): still an error", "error"),
        # Not an error at all.
        ("The file has 5 lines.", "success"),
        ("", "success"),
        ("ok", "success"),
    ],
)
def test_both_conventions_classify(body, expected_status):
    assert _meta(body)["status"] == expected_status, body


def test_a_failing_repl_is_actionable_rather_than_continue():
    """The success default said "continue"; a failure must ask for a change.

    This is the property `ToolProgressMiddleware` consumes, so getting it wrong
    blinds the stagnation guard for the whole tool family.
    """
    failure = _meta(REPL_BODY)
    success = _meta("The file has 5 lines.")
    assert failure["recommended_next_action"] != success["recommended_next_action"], (
        "a failure must not be told to continue exactly as a success is"
    )
    assert failure["recommended_next_action"] in {"try_alternative", "rewrite_query", "stop"}


def test_the_exception_name_survives_for_keyword_classification():
    # The name is what lets a permission error be told apart from a syntax one.
    assert split_error_prefix(REPL_BODY).startswith("ModuleNotFoundError:")
    assert split_error_prefix("Error: File not found") == "File not found"
    assert split_error_prefix("Error (): body only") == "body only"


def test_the_message_body_is_not_rewritten():
    """Only the stamp changes; the model still reads the original text."""
    message = ToolMessage(content=REPL_BODY, tool_call_id="call-1", name="python_repl", status="success")
    stamped = normalize_tool_result(message)
    assert stamped.content == REPL_BODY
    assert "Traceback" in stamped.content, "the diagnostic must reach the model, not be stripped"


def test_an_explicit_error_status_is_still_honoured_without_a_marker():
    meta = _meta("something went wrong somewhere", status="error")
    assert meta["status"] == "error"
