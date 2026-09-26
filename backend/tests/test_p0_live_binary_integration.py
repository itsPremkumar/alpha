"""INTEGRATION TEST: the shipped binary must complete a real run, end to end.

This is an integration test, not a unit test. It launches the *installed
console script* (``backend/.venv/Scripts/alpha.exe``) as a subprocess, against the
*shipped* ``config.yaml`` and the real provider, and asserts on its real exit
code and its real stdout bytes. Nothing is mocked, no in-process shortcut is
taken, and the assertions are the acceptance criteria themselves:

* a task that genuinely succeeds exits **0**;
* a task that must fail exits **non-zero** and emits an ``error`` frame carrying
  a code;
* stdout is **100% parseable NDJSON** -- every line valid JSON, every type a
  known protocol type.

Why the subprocess and not the client API
------------------------------------------
Every one of the four P0 defects lived *between* the client and the process
boundary: the sync path, the stdout encoding, the exit code, the protocol
frames. An in-process test cannot see any of them. Asserting on the client's
return value would have passed against the shipped binary for all four.

Why it is opt-in
----------------
It calls a real paid API and takes minutes. It runs only under the repo's
existing live-test opt-in, ``AGENT_WORKSPACE_RUN_LIVE_TESTS=1``, and is marked
``live`` so ``make test`` (``pytest -m "not live"``) never picks it up. The
opt-in is load-bearing, not a formality: a live test that silently became a
permanent skip is how these four defects stayed invisible in the first place.

Run it with::

    AGENT_WORKSPACE_RUN_LIVE_TESTS=1 python -m pytest tests/test_p0_live_binary_integration.py -v -s
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

_LIVE_TEST_OPT_IN = "AGENT_WORKSPACE_RUN_LIVE_TESTS"

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

#: The installed entry point. Resolved rather than shelled out through PATH so the
#: test proves the *shipped* binary works, not whatever ``alpha`` happens to be
#: first on PATH.
ALPHA_EXE = BACKEND_ROOT / ".venv" / "Scripts" / "alpha.exe"
if not ALPHA_EXE.exists():  # pragma: no cover - non-Windows layout
    _posix = BACKEND_ROOT / ".venv" / "bin" / "alpha"
    ALPHA_EXE = _posix if _posix.exists() else ALPHA_EXE

#: The protocol's complete vocabulary. A line whose ``type`` is not in here means
#: something that is not the protocol reached stdout -- exactly the 34-of-41
#: unparseable lines a prior run produced.
KNOWN_EVENT_TYPES = frozenset({"values", "messages-tuple", "custom", "error", "end"})

#: Exit code reserved for a run that failed (``tui.cli.RUN_FAILED_EXIT_CODE``).
RUN_FAILED_EXIT_CODE = 1

#: Generous enough for a cold agent assembly plus several model round-trips on a
#: loaded machine, and short enough that a hang fails the test rather than the
#: job. A failing model is detected by the error frame, not by timing out, so
#: this bound is not load-bearing for correctness.
RUN_TIMEOUT_SECONDS = 900

_skip_reason: str | None = None
if os.environ.get("CI"):
    _skip_reason = "Live tests skipped in CI"
elif os.environ.get(_LIVE_TEST_OPT_IN) != "1":
    _skip_reason = f"Set {_LIVE_TEST_OPT_IN}=1 to run live tests with real external APIs"
elif not (REPO_ROOT / "config.yaml").exists():
    _skip_reason = "No config.yaml found — live tests require valid API credentials"
elif not ALPHA_EXE.exists():
    _skip_reason = f"Installed console script not found at {ALPHA_EXE}"

if _skip_reason:
    pytest.skip(_skip_reason, allow_module_level=True)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class BinaryRun:
    """The result of one real invocation of the shipped binary."""

    def __init__(self, completed: subprocess.CompletedProcess[str], elapsed: float) -> None:
        self.returncode = completed.returncode
        self.stdout = completed.stdout or ""
        self.stderr = completed.stderr or ""
        self.elapsed = elapsed

    @property
    def lines(self) -> list[str]:
        return [line for line in self.stdout.splitlines() if line.strip()]

    @property
    def frames(self) -> list[dict]:
        """Every stdout line, parsed. Raises on the first unparseable line.

        This is the 100%-parseability assertion: it is a property of the raw
        stdout text, not of anything the product reported about itself.
        """
        return [json.loads(line) for line in self.lines]

    @property
    def types(self) -> list[str]:
        return [frame.get("type") for frame in self.frames]

    def describe(self) -> str:
        return (
            f"\n--- exit code: {self.returncode}"
            f"\n--- elapsed: {self.elapsed:.1f}s"
            f"\n--- stdout lines: {len(self.lines)}"
            f"\n--- event types: {sorted(set(self.types))}"
            f"\n--- stderr tail:\n{self.stderr[-2000:]}"
        )


def _run_alpha(argv: list[str], *, task: str) -> BinaryRun:
    """Invoke the shipped binary once, capturing raw stdout bytes.

    ``text=True`` with an explicit UTF-8 encoding is deliberate: PowerShell's
    ``>`` redirection writes UTF-16, which would itself corrupt the stream and
    make the parseability assertion meaningless. A pipe in binary-decoded UTF-8
    is what a real consumer does.
    """
    import time

    env = dict(os.environ)
    # The agent writes thread state, artifacts and a checkpointer. Isolate them so
    # a live run cannot touch a developer's real workspace.
    env.setdefault("AGENT_WORKSPACE_HOME", str(Path(os.environ.get("TEMP", "/tmp")) / "ag_p0_live"))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    command = [str(ALPHA_EXE), *argv]
    started = time.monotonic()
    completed = subprocess.run(  # noqa: S603 - fixed executable, argv built here
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=RUN_TIMEOUT_SECONDS,
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
    )
    return BinaryRun(completed, time.monotonic() - started)


# ---------------------------------------------------------------------------
# A task that genuinely succeeds
# ---------------------------------------------------------------------------

#: Chosen to be (a) answerable from the model's own knowledge, so it does not
#: depend on a tool, a network fetch, or a search provider, and (b) short, so the
#: test costs one model round-trip rather than a long agent loop.
SUCCEEDING_TASK = "What is 17 * 23? Reply with only the number and nothing else."


@pytest.fixture(scope="module")
def successful_run() -> BinaryRun:
    return _run_alpha(["--json", "--recursion-limit", "250", SUCCEEDING_TASK], task=SUCCEEDING_TASK)


def test_a_real_task_completes_and_exits_zero(successful_run: BinaryRun):
    """The headline acceptance criterion: the shipped binary completes a run.

    Before the fixes this exited 0 *without answering* -- the run had failed
    internally and the protocol had no way to say so. So the exit code alone is
    not enough; the answer is asserted too, and so is the absence of an error
    frame.
    """
    assert successful_run.returncode == 0, f"a task that genuinely succeeds must exit 0{successful_run.describe()}"

    types = successful_run.types
    assert "error" not in types, f"a successful run emitted an error frame{successful_run.describe()}"
    assert types[-1] == "end", f"a successful run must terminate with 'end'{successful_run.describe()}"
    assert "end" in types, f"no terminal frame at all{successful_run.describe()}"


def test_successful_run_ndjson_is_100_percent_parseable(successful_run: BinaryRun):
    """Every stdout line is valid JSON of a known protocol type.

    The reported failure was 34 of 41 lines unparseable because a subprocess
    wrote to inherited stdout. Asserting per line (rather than a count) means one
    leaked line fails the test, which is the whole point.
    """
    lines = successful_run.lines
    assert lines, f"the run produced no stdout at all{successful_run.describe()}"

    unparseable: list[str] = []
    for index, line in enumerate(lines):
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            unparseable.append(f"line {index + 1}: {line[:200]!r}")
            continue
        assert isinstance(frame, dict), f"line {index + 1} is not a JSON object: {line[:200]!r}"
        assert frame.get("type") in KNOWN_EVENT_TYPES, (
            f"line {index + 1} has unknown type {frame.get('type')!r}; only protocol frames may reach stdout{successful_run.describe()}"
        )
        assert "data" in frame, f"line {index + 1} has no data field: {line[:200]!r}"

    assert not unparseable, f"stdout was not 100% parseable NDJSON: {unparseable[:5]}{successful_run.describe()}"


def test_successful_run_actually_produced_an_answer(successful_run: BinaryRun):
    """Non-empty AI text, so a silent run cannot masquerade as a success.

    17*23 = 391. Asserting the *content* catches a run that streams frames but
    never answers -- the exact shape of the shipped failure.
    """
    frames = successful_run.frames
    ai_text = "".join(
        frame["data"].get("content", "")
        for frame in frames
        if frame["type"] == "messages-tuple" and frame["data"].get("type") == "ai"
    ).strip()
    assert ai_text, f"the run produced no assistant text at all{successful_run.describe()}"
    assert "391" in ai_text, f"the model did not answer the question (got {ai_text[:200]!r}){successful_run.describe()}"


def test_successful_run_reports_token_usage(successful_run: BinaryRun):
    """The terminal ``end`` frame carries usage, so a caller can meter the run."""
    end_frames = [f for f in successful_run.frames if f["type"] == "end"]
    assert end_frames, f"no end frame{successful_run.describe()}"
    usage = end_frames[-1]["data"].get("usage")
    assert isinstance(usage, dict), f"end frame carries no usage: {end_frames[-1]!r}"
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        assert key in usage, f"usage is missing {key}: {usage!r}"


# ---------------------------------------------------------------------------
# A task that must fail
# ---------------------------------------------------------------------------

#: Points every model at a host that cannot be reached, through the shipped
#: config's own ``--model``-free surface: an unresolvable model name is rejected
#: by the factory before any request, which is the same code path a bad key or a
#: dead endpoint takes. Nothing here is mocked; the binary really does fail.
FAILING_TASK = "This task is guaranteed to fail; see the failing-model fixture."


@pytest.fixture(scope="module")
def failing_run(tmp_path_factory) -> BinaryRun:
    """A real run against a config whose provider endpoint cannot be reached.

    The failure is manufactured the way a real one happens -- an endpoint that
    does not answer -- rather than by an injected exception, so the exit code and
    the emitted frame are the ones a user would actually get.
    """
    import time

    workspace = tmp_path_factory.mktemp("ag_p0_failing")
    config = workspace / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "config_version: 53",
                "models:",
                "  - name: unreachable",
                "    display_name: Unreachable",
                "    description: deliberately broken endpoint",
                "    use: langchain_openai:ChatOpenAI",
                "    model: p0-unreachable-model",
                "    base_url: http://127.0.0.1:1/v1",
                "    api_key: p0-not-a-real-key",
                "    request_timeout: 5.0",
                "    max_retries: 0",
                "    supports_thinking: false",
                "    supports_reasoning_effort: false",
                "sandbox:",
                "  use: alpha.sandbox.local:LocalSandboxProvider",
            ]
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["AGENT_WORKSPACE_CONFIG_PATH"] = str(config)
    env["AGENT_WORKSPACE_HOME"] = str(workspace / "home")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    (workspace / "home").mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    completed = subprocess.run(  # noqa: S603 - fixed executable, argv built here
        [str(ALPHA_EXE), "--json", "--recursion-limit", "250", FAILING_TASK],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=RUN_TIMEOUT_SECONDS,
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
    )
    return BinaryRun(completed, time.monotonic() - started)


def test_a_failing_task_exits_non_zero(failing_run: BinaryRun):
    """A failed run must not exit 0.

    This is the defect in one assertion. The shipped protocol had no error event
    and the CLI returned 0 unconditionally, so a caller scripting ``alpha --json``
    could not tell a completed run from a dead one.
    """
    assert failing_run.returncode != 0, f"a run that failed must exit non-zero{failing_run.describe()}"
    assert failing_run.returncode == RUN_FAILED_EXIT_CODE, f"unexpected exit code{failing_run.describe()}"


def test_a_failing_run_emits_an_error_frame_with_a_code(failing_run: BinaryRun):
    """The failure is reported in the protocol, with a stable machine-readable code."""
    frames = failing_run.frames  # also asserts 100% parseability on the failing path
    error_frames = [f for f in frames if f["type"] == "error"]
    assert error_frames, f"a failed run emitted no error frame{failing_run.describe()}"

    error = error_frames[-1]
    assert error["data"].get("code"), f"the error frame carries no code: {error!r}"
    assert isinstance(error["data"]["code"], str)
    assert error["data"].get("message"), f"the error frame carries no message: {error!r}"
    assert error["data"].get("correlation_id"), f"the error frame carries no correlation id: {error!r}"


def test_a_failing_run_does_not_also_report_success(failing_run: BinaryRun):
    """``error`` and ``end`` are mutually exclusive terminal frames."""
    types = failing_run.types
    assert "error" in types, f"no error frame{failing_run.describe()}"
    assert "end" not in types, f"a failed run also reported success{failing_run.describe()}"


def test_failing_run_ndjson_is_also_100_percent_parseable(failing_run: BinaryRun):
    """Failure reporting must not itself produce unparseable output."""
    for line in failing_run.lines:
        frame = json.loads(line)  # raises with the offending line in the message
        assert frame.get("type") in KNOWN_EVENT_TYPES, f"unknown frame type in a failed run: {frame!r}"
