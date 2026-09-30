"""Shared fixtures for the verification-loop suites.

The controller depends on exactly two things from the outside — a
``CommandExecutor`` and a surface reader — so both are substituted here rather
than mocked in per test. The substitute executor still produces a *sealed*
:class:`RecordedExecution` through the production
:func:`alpha.verification.execution.record_execution`; nothing in the test suite
forges one, which is the same constraint production lives under.

That constraint is the point. A test that could hand the controller a
hand-built "12 passed" transcript would be testing a controller that accepts
transcripts, so the fixture makes that impossible rather than merely unused.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alpha.verification.execution import RecordedExecution, record_execution  # noqa: E402

#: A pytest-shaped failing tail. The controller must hand *this* to the repair,
#: so tests assert on its exact presence rather than on a paraphrase.
FAILING_OUTPUT = """=================================== FAILURES ===================================
_________________________________ test_subtracts _____________________________________

    def test_subtracts():
>       assert compute(5, 3) == 3
E       assert 2 == 3

tests/test_math.py:9: AssertionError
=========================== short test summary info ============================
FAILED tests/test_math.py::test_subtracts - assert 2 == 3
========================= 1 failed, 4 passed in 0.12s =========================
Exit Code: 1"""

PASSING_OUTPUT = """============================= test session starts ==============================
collected 5 items

tests/test_math.py .....                                                      [100%]

============================= 5 passed in 0.12s ==============================
"""

#: A command the acceptance binder's shell-structure matcher accepts. Chosen to
#: be boring on purpose: the point of these tests is the controller, not the
#: matcher's edge cases (which ``test_acceptance_checks.py`` owns).
VERIFY_COMMAND = "pytest tests/test_math.py"

BASELINE_TEST_FILE = """def test_adds():
    assert compute(2, 3) == 5


def test_subtracts():
    assert compute(5, 3) == 2
"""


def make_execution(
    *,
    output: str,
    status: str = "error",
    command: str = VERIFY_COMMAND,
    shell_persistent: bool | None = False,
    tool_call_id: str = "call-1",
) -> RecordedExecution:
    """A sealed recorded execution, exactly as a real executor would produce one."""
    return record_execution(
        command=command,
        tool_call_id=tool_call_id,
        tool_name="bash",
        output=output,
        status=status,
        shell_persistent=shell_persistent,
    )


class ScriptedExecutor:
    """An executor that replays a fixed list of recorded executions.

    Stands in for the *sandbox*, not for the *recording*: every value it returns
    went through the production seal. It records how many times it was called,
    which is what the budget test asserts against.
    """

    def __init__(self, outputs: Iterable[str], *, statuses: Iterable[str] | None = None) -> None:
        self._outputs = list(outputs)
        self._statuses = list(statuses) if statuses is not None else None
        self.calls: list[str] = []
        self.exhausted = False

    async def execute(self, command: str) -> RecordedExecution:
        self.calls.append(command)
        if not self._outputs:
            self.exhausted = True
            raise RuntimeError("scripted executor ran out of recordings")
        index = min(len(self.calls) - 1, len(self._outputs) - 1)
        output = self._outputs[index]
        status = self._statuses[index] if self._statuses and index < len(self._statuses) else "error"
        return make_execution(output=output, status=status, command=command, tool_call_id=f"call-{len(self.calls)}")


def reading_file(initial: str) -> Callable[[str], str | None]:
    """A surface reader over one in-memory file whose content a test can change.

    The before/after captures therefore see genuinely different text on either
    side of a repair, which is what makes the weakening comparison a real
    comparison rather than two reads of the same string.
    """
    state = {"text": initial}

    def read(path: str) -> str | None:
        return state["text"] if path.endswith("test_math.py") else None

    read.__dict__["state"] = state  # type: ignore[attr-defined]

    def set_text(value: str) -> None:
        state["text"] = value

    read.set_text = set_text  # type: ignore[attr-defined]
    return read


@pytest.fixture()
def test_file_path() -> str:
    """A workspace-relative test path the guard recognises as a test file."""
    return "tests/test_math.py"
