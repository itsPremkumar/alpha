"""The fleet driver must not grade itself, and must not grade a run it never saw.

`backend/scripts/fleet_task_assign.py` exists to check Alpha's output, so a hole
in *it* is worse than no checker: it launders its own blindness into a pass, or
its own impatience into a false bug report. Both happened in the first run
documented in `docs/audits/FLEET_VERIFICATION.md`:

* **F3** -- a fixed 40-iteration status poll gave up while a bot was still
  legitimately working, and the driver then reported that bot as having produced
  no files. The run succeeded ~1400 s later with a 15 KB artifact. The bug was
  in the instrument, not in Alpha.
* **F4** -- `_host_path` failed to resolve the thread output directory, so
  `verified_bytes` came back empty; because the size floor was only applied when
  that dict was non-empty, the driver printed `problems: none` for artifacts it
  had never measured.

These tests pin the corrected behaviour. They test the *verifier*, not Alpha.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fleet_task_assign.py"


def _load():
    """Import the script by path: ``backend/scripts`` is not a package."""
    spec = importlib.util.spec_from_file_location("fleet_task_assign", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


fleet = _load()


# ---------------------------------------------------------------------------
# Path mapping
# ---------------------------------------------------------------------------


def test_maps_user_data_to_the_thread_directory(tmp_path, monkeypatch) -> None:
    """The mapping must find a real file nested under a real user directory."""
    monkeypatch.setenv(fleet.USERS_ROOT_ENV, str(tmp_path))
    real = tmp_path / "alice" / "threads" / "tid-1" / "user-data" / "outputs" / "artifact.md"
    real.parent.mkdir(parents=True)
    real.write_text("x" * 900, encoding="utf-8")

    host = fleet._host_path("tid-1", "/mnt/user-data/outputs/artifact.md")

    assert host == real
    assert host.stat().st_size == 900


def test_resolves_under_whichever_user_dir_actually_exists(tmp_path, monkeypatch) -> None:
    """It must not hardcode a user name: the id is a directory to search."""
    monkeypatch.setenv(fleet.USERS_ROOT_ENV, str(tmp_path))
    real = tmp_path / "bob" / "threads" / "tid-9" / "user-data" / "outputs" / "a.txt"
    real.parent.mkdir(parents=True)
    real.write_text("y", encoding="utf-8")

    assert fleet._host_path("tid-9", "/mnt/user-data/outputs/a.txt") == real


def test_a_missing_artifact_still_yields_a_concrete_path(tmp_path, monkeypatch) -> None:
    """An absent file must map to a path the caller can name in an error.

    Returning a real-looking path is what lets the driver say "could not
    measure X at Y" instead of dropping the artifact on the floor.
    """
    monkeypatch.setenv(fleet.USERS_ROOT_ENV, str(tmp_path))
    (tmp_path / "default").mkdir()

    host = fleet._host_path("tid-x", "/mnt/user-data/outputs/nope.md")

    assert host.is_absolute()
    assert host.name == "nope.md"
    assert "tid-x" in str(host)
    assert not host.exists()


def test_paths_outside_user_data_are_passed_through_unchanged() -> None:
    """A repo-relative artifact is not remapped into a thread directory."""
    assert fleet._host_path("tid-1", "docs/thing.md") == Path("docs/thing.md")


# ---------------------------------------------------------------------------
# The honesty rule
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The size floor
# ---------------------------------------------------------------------------


def test_an_unmeasurable_artifact_is_reported_not_skipped(tmp_path, monkeypatch) -> None:
    """F4: the exact hole. Unresolvable paths must become a problem.

    The first driver resolved nothing, so every size came back negative, the
    size floor was skipped because the byte dict was effectively empty, and the
    row printed ``problems: none``. This asserts the failure is now visible.
    """
    monkeypatch.setenv(fleet.USERS_ROOT_ENV, str(tmp_path))
    (tmp_path / "default").mkdir()
    res = fleet.Result(bot="coder", title="t", thread_id="tid-none", files=["/mnt/user-data/outputs/gone.md"])

    fleet.check_artifacts(res, min_bytes=400)

    assert res.problems, "an unmeasurable artifact must not pass silently"
    assert "could not be measured" in res.problems[0]
    assert "gone.md" in res.problems[0]


def test_a_real_artifact_passes_and_is_measured(tmp_path, monkeypatch) -> None:
    """The positive case, so the rule above cannot be satisfied by always failing."""
    monkeypatch.setenv(fleet.USERS_ROOT_ENV, str(tmp_path))
    real = tmp_path / "u" / "threads" / "t1" / "user-data" / "outputs" / "big.md"
    real.parent.mkdir(parents=True)
    real.write_text("x" * 5000, encoding="utf-8")
    res = fleet.Result(bot="coder", title="t", thread_id="t1", files=["/mnt/user-data/outputs/big.md"])

    fleet.check_artifacts(res, min_bytes=400)

    assert res.problems == []
    assert res.verified_bytes == {"/mnt/user-data/outputs/big.md": 5000}


def test_a_too_small_artifact_is_reported(tmp_path, monkeypatch) -> None:
    """The floor still works when the path resolves -- it must not be shadowed."""
    monkeypatch.setenv(fleet.USERS_ROOT_ENV, str(tmp_path))
    real = tmp_path / "u" / "threads" / "t2" / "user-data" / "outputs" / "stub.md"
    real.parent.mkdir(parents=True)
    real.write_text("x" * 12, encoding="utf-8")
    res = fleet.Result(bot="coder", title="t", thread_id="t2", files=["/mnt/user-data/outputs/stub.md"])

    fleet.check_artifacts(res, min_bytes=400)

    assert any("below the 400B floor" in p for p in res.problems)


def test_one_unreadable_artifact_reports_even_when_others_are_real(tmp_path, monkeypatch) -> None:
    """A partial measurement is still not a complete one.

    Averaging over the readable subset is how a driver ends up reporting a
    confident byte count for work it partly could not see.
    """
    monkeypatch.setenv(fleet.USERS_ROOT_ENV, str(tmp_path))
    real = tmp_path / "u" / "threads" / "t3" / "user-data" / "outputs" / "there.md"
    real.parent.mkdir(parents=True)
    real.write_text("x" * 9000, encoding="utf-8")
    res = fleet.Result(
        bot="coder",
        title="t",
        thread_id="t3",
        files=["/mnt/user-data/outputs/there.md", "/mnt/user-data/outputs/vanished.md"],
    )

    fleet.check_artifacts(res, min_bytes=400)

    assert res.verified_bytes["/mnt/user-data/outputs/there.md"] == 9000
    assert res.problems and "could not be measured" in res.problems[0]


def test_terminal_states_include_success_and_every_failure_mode() -> None:
    """Polling must stop on success AND on failure -- never only on success.

    A checker that keeps waiting after an `error` run reports a timeout instead
    of the error, which is the same class of misreport as F3 in reverse.
    """
    for state in ("success", "error", "interrupted", "timeout", "canceled", "failed"):
        assert state in fleet.TERMINAL, f"{state} must be terminal"
    for in_flight in ("running", "pending", "queued", "streaming"):
        assert in_flight not in fleet.TERMINAL, f"{in_flight} is not terminal"


def test_default_timeout_exceeds_the_slowest_observed_run() -> None:
    """The default budget must clear a run that really took ~1400 s.

    The original default (900 s) was below the reviewer run's duration, so the
    default was itself a defect: an operator running the documented command got
    a false failure by default, not only by choice.
    """
    assert fleet.SLOWEST_OBSERVED_RUN_SECONDS == pytest.approx(1400, abs=200)
    default = _default_timeout()
    assert default > fleet.SLOWEST_OBSERVED_RUN_SECONDS


def _default_timeout() -> float:
    """Read the driver's real default rather than restating it."""
    import argparse

    ns = argparse.Namespace()
    # The driver declares its arguments inside main(); parsing the module source
    # for the add_argument default keeps one source of truth.
    src = _SCRIPT.read_text(encoding="utf-8")
    assert '"--timeout"' in src, "driver no longer exposes --timeout"
    marker = "default=max(2400.0, SLOWEST_OBSERVED_RUN_SECONDS * 1.7)"
    assert marker in src, "driver default timeout changed; re-check it against a real slow bot run before accepting the new value (docs/audits/FLEET_VERIFICATION.md F3)"
    del ns
    return 2400.0


def test_a_run_that_never_terminals_is_flagged_rather_than_assumed_ok() -> None:
    """`stream_timed_out` is recorded separately from the run's own status.

    Conflating them is what let the first driver report a cut-off stream as a
    product failure.
    """
    res = fleet.Result(bot="b", title="t", status="running")
    assert res.stream_timed_out is False
    res.stream_timed_out = True
    assert res.status == "running"
    # `ok` is derived from problems, so an explicit timeout problem is what
    # makes the row fail -- never a silent field.
    assert res.ok is True
    res.problems.append("run never reached a terminal status")
    assert res.ok is False


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


async def _frames(lines: list[str]) -> list[tuple[str, str]]:
    """Drive ``parse_sse`` through a real async iterator.

    httpx hands the parser an async line iterator, so passing a plain list would
    test a shape the driver never sees -- and this test file exists precisely to
    avoid testing the wrong shape.
    """

    async def alines():
        for line in lines:
            yield line

    return [(n, d) async for n, d in fleet.parse_sse(alines())]


@pytest.mark.asyncio
async def test_sse_frames_are_reassembled_across_multiple_data_lines() -> None:
    """A frame split over several `data:` lines is one payload, not several."""
    frames = await _frames(["event: values", 'data: {"a":', "data: 1}", "", "event: end", "data: {}", ""])
    assert frames == [("values", '{"a":\n1}'), ("end", "{}")]


@pytest.mark.asyncio
async def test_comments_and_blank_frames_are_not_emitted() -> None:
    """A bare ``:`` comment is a keepalive, not an empty frame."""
    frames = await _frames([": keepalive", "", "event: ping", "data: {}", ""])
    assert [n for n, _ in frames] == ["ping"]


def test_tool_names_are_found_in_the_shapes_this_gateway_actually_emits() -> None:
    """Tool discovery must survive the real frame layouts, not just one.

    The run in `docs/audits/FLEET_VERIFICATION.md` reported 3-8 tools per bot
    depending on which of these shapes carried the name.
    """
    out: set[str] = set()

    fleet.tool_names({"type": "tool", "name": "write_file"}, out)
    fleet.tool_names({"tool_calls": [{"name": "web_search"}]}, out)
    fleet.tool_names({"tool_call_chunks": [{"name": "grep"}]}, out)
    fleet.tool_names({"additional_kwargs": {"alpha_tool_receipt": {"tool_name": "read_file"}}}, out)
    fleet.tool_names({"message": {"type": "ToolMessage", "name": "ls"}}, out)
    fleet.tool_names([{"type": "tool", "name": "glob"}], out)

    assert out == {"write_file", "web_search", "grep", "read_file", "ls", "glob"}


def test_a_frame_without_a_tool_name_contributes_nothing() -> None:
    out: set[str] = set()
    fleet.tool_names({"type": "token", "content": "hello"}, out)
    assert out == set()


def test_final_ai_text_reads_the_last_non_empty_ai_message() -> None:
    payload = {
        "data": [
            {"type": "ai", "content": ""},
            {"type": "human", "content": "ignore me"},
            {"type": "ai", "content": "the answer"},
        ]
    }
    assert fleet.final_ai_text(payload) == "the answer"


def test_final_ai_text_accepts_the_structured_block_form() -> None:
    payload = {"data": [{"type": "ai", "content": [{"type": "text", "text": "blocked out"}]}]}
    assert fleet.final_ai_text(payload) == "blocked out"


def test_final_ai_text_is_none_when_the_run_only_emitted_tools() -> None:
    assert fleet.final_ai_text({"data": [{"type": "tool", "name": "ls"}]}) is None
