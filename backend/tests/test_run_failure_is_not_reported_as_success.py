"""A failed run and a failed tool call must never read as success.

Both defects here were found by assigning a real task to the agent and reading
what the server actually persisted, not by reading code.

**1. ``POST /threads/{id}/runs/wait`` reported a dead run as an empty success.**
A run that died at the LangGraph recursion limit — after 200s, 363k input
tokens, and 52k tokens re-sent on every one of seven turns — returned HTTP 200
with a body of exactly `{}`. No `status`, no `error`, no `run_id`. The endpoint
builds its reply from the final checkpoint whenever one exists, and an errored
run can still have a checkpoint row, so the projection came back empty and the
terminal state was dropped. A client could not distinguish that from a
successful, empty conversation.

**2. Every failed tool call was persisted with ``status: "success"``.**
The run's event feed recorded, verbatim:

    seq=4  read_file  STATUS='success'  :: Error: File not found: /mnt/user-data/workspace/README.md
    seq=9  glob       STATUS='success'  :: Error: Permission denied: /mnt/user-data
    seq=13 glob       STATUS='success'  :: Error: Permission denied: /mnt

`ToolMessage.status` is LangChain's field and defaults to `"success"`. Alpha's
file/glob/permission tools report failure by *returning* an error string rather
than raising, so the run can continue — which left the feed claiming three
succeeded calls that had all failed, contradicting what the model was told.
`ToolErrorHandlingMiddleware` had already classified each one and stamped the
verdict into `additional_kwargs["alpha_tool_meta"]`; only the persisted
projection ignored it.
"""

from __future__ import annotations

import pytest
from langchain.agents.middleware import ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.journal import (
    RunJournal,
    _build_history_seed_events,
    _with_honest_tool_status,
)


def _make_tool_call_request(tool_name: str = "read_file", tool_call_id: str = "call_01abc"):
    """Create a simple object with the fields the middleware accesses."""

    class MockRequest:
        def __init__(self, tool_name: str, tool_call_id: str):
            self.tool_call = {"name": tool_name, "id": tool_call_id, "args": {}}
            self.id = tool_call_id
            self.name = tool_name

    return MockRequest(tool_name, tool_call_id)


def _tool_message_with_meta(*, content: str, meta_status: str, langchain_status: str = "success") -> ToolMessage:
    """A ToolMessage shaped like the ones the real tool middlewares produce."""
    return ToolMessage(
        content=content,
        tool_call_id="call_01abc",
        name="read_file",
        status=langchain_status,
        additional_kwargs={"alpha_tool_meta": {"status": meta_status, "source": "tool_return"}},
    )


class TestHonestToolStatus:
    def test_an_error_string_is_not_persisted_as_success(self) -> None:
        message = _tool_message_with_meta(content="Error: File not found: /mnt/user-data/workspace/README.md", meta_status="error")
        payload = _with_honest_tool_status(message.model_dump())
        assert payload["status"] == "error", "a failed tool call must not be recorded as a success"

    def test_permission_denied_is_not_persisted_as_success(self) -> None:
        message = _tool_message_with_meta(content="Error: Permission denied: /mnt", meta_status="error")
        assert _with_honest_tool_status(message.model_dump())["status"] == "error"

    def test_a_genuine_success_is_untouched(self) -> None:
        message = _tool_message_with_meta(content="# Alpha\n", meta_status="success")
        assert _with_honest_tool_status(message.model_dump())["status"] == "success"

    def test_a_declared_error_is_never_upgraded_to_success(self) -> None:
        """The correction is one-directional; a real error must survive it."""
        message = _tool_message_with_meta(content="Error: boom", meta_status="error", langchain_status="error")
        assert _with_honest_tool_status(message.model_dump())["status"] == "error"

    def test_an_unstamped_message_is_left_exactly_as_the_provider_described_it(self) -> None:
        message = ToolMessage(content="Error: File not found", tool_call_id="c1", name="read_file")
        assert _with_honest_tool_status(message.model_dump())["status"] == "success"

    def test_partial_success_is_reported_as_such(self) -> None:
        message = _tool_message_with_meta(content="found 3 of 10", meta_status="partial_success")
        assert _with_honest_tool_status(message.model_dump())["status"] == "partial_success"

    def test_a_malformed_stamp_is_ignored_rather_than_crashing(self) -> None:
        for stamp in (None, "not-a-dict", [], "weird-status", 42):
            message = ToolMessage(
                content="x",
                tool_call_id="c1",
                name="read_file",
                additional_kwargs={"alpha_tool_meta": stamp},
            )
            assert _with_honest_tool_status(message.model_dump())["status"] == "success"

    def test_the_original_payload_is_not_mutated_in_place(self) -> None:
        """The caller keeps using the dump; a shared-mutation surprise here would
        silently change the checkpoint projection too."""
        message = _tool_message_with_meta(content="Error: nope", meta_status="error")
        original = message.model_dump()
        _with_honest_tool_status(original)
        assert original["status"] == "success"


class TestJournalPersistsTheHonestStatus:
    def test_the_live_journal_event_carries_the_failure(self) -> None:
        journal = RunJournal(run_id="r1", thread_id="t1", event_store=MemoryRunEventStore())
        captured: list[dict] = []
        journal._put = lambda **kwargs: captured.append(kwargs)  # type: ignore[method-assign]
        journal._record_message_summary = lambda *a, **k: None  # type: ignore[method-assign]

        journal._persist_tool_result_message(_tool_message_with_meta(content="Error: File not found", meta_status="error"))

        assert captured, "the tool result was not persisted at all"
        event = captured[0]
        assert event["event_type"] == "llm.tool.result"
        assert event["content"]["status"] == "error", "the persisted event still claims success"


class TestSeededRowsMatchJournaledOnes:
    def test_a_seeded_failed_tool_call_is_also_corrected(self) -> None:
        """Branch seeding writes the same event type, so it needs the same rule."""
        events = _build_history_seed_events(
            [
                HumanMessage(content="do it", id="m1"),
                AIMessage(content="", id="m2"),
                _tool_message_with_meta(content="Error: File not found", meta_status="error"),
            ],
            thread_id="t1",
            run_id_prefix="branch-seed-0",
            seed_metadata={},
        )
        tool_events = [e for e in events if e["event_type"] == "llm.tool.result"]
        assert tool_events, "no seeded tool-result event was produced"
        assert tool_events[0]["content"]["status"] == "error"


class TestWaitRunAlwaysCarriesTheTerminalStatus:
    """`/runs/wait` must not answer a dead run with an empty object.

    The endpoint is covered end to end elsewhere; this pins the property that was
    actually broken, against the handler's own response builder, so a future
    refactor cannot drop the status again.
    """

    def test_the_projection_never_replaces_a_missing_status(self) -> None:
        import inspect

        from app.gateway.routers import thread_runs

        source = inspect.getsource(thread_runs.wait_run)
        assert 'payload.setdefault("status"' in source, "wait_run must carry the terminal status alongside the checkpoint projection; without it an errored run is reported as an empty success"
        assert 'payload.setdefault("error"' in source, "wait_run must carry the terminal error alongside the projection"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Error: File not found: /x", "error"),
        ("Error: Permission denied: /mnt", "error"),
        ("No files matched under /x", "success"),  # not an error string; left alone
        ("", "success"),
    ],
)
def test_realistic_tool_outputs(content: str, expected: str) -> None:
    """The exact strings from the failing run, so the rule is not hypothetical."""
    message = _tool_message_with_meta(content=content, meta_status="error" if content.startswith("Error:") else "success")
    assert _with_honest_tool_status(message.model_dump())["status"] == expected


class TestRealMiddlewareToJournalPath:
    """Test the REAL path: ToolErrorHandlingMiddleware -> RunJournal.

    This tests whether the alpha_tool_meta stamp survives from the middleware
    classification all the way through to the journal's persisted event.
    """

    @pytest.mark.anyio
    async def test_tool_error_handling_middleware_stamp_reaches_journal(self):
        """The full integration path: middleware classifies -> journal persists.

        This exercises the REAL code path, not a hand-constructed fixture.
        """
        from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
        from alpha.config.app_config import AppConfig

        # Create the real middleware
        middleware = ToolErrorHandlingMiddleware(app_config=None)

        # Create a handler that returns a ToolMessage with error content
        # (simulating a tool that returns an error string instead of raising)
        def handler(request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content="Error: File not found: /mnt/user-data/workspace/README.md",
                tool_call_id=request.id,
                name=request.name,
                status="success",  # LangChain default - the bug we're fixing
            )

        request = _make_tool_call_request("read_file", "call_real_001")

        # Process through the REAL middleware
        result = middleware.wrap_tool_call(request, handler)

        # Verify the middleware stamped alpha_tool_meta
        assert isinstance(result, ToolMessage)
        meta = result.additional_kwargs.get("alpha_tool_meta")
        assert meta is not None, "ToolErrorHandlingMiddleware must stamp alpha_tool_meta"
        assert meta["status"] == "error", f"middleware classified as {meta['status']}, expected error"
        assert meta["source"] == "tool_return"

        # Now pass through the REAL journal path
        store = MemoryRunEventStore()
        journal = RunJournal(run_id="r1", thread_id="t1", event_store=store)

        # This is what on_tool_end does internally
        journal._persist_tool_result_message(result)
        await journal.flush()

        # Check what actually landed in the event store
        events = await store.list_events("t1", "r1")
        tool_events = [e for e in events if e["event_type"] == "llm.tool.result"]
        assert tool_events, "no tool-result event was persisted"

        event = tool_events[0]
        # The critical assertion: the persisted event must have status="error"
        assert event["content"]["status"] == "error", f"Persisted event has status={event['content']['status']}, expected error. The alpha_tool_meta stamp was lost in transit!"

    @pytest.mark.anyio
    async def test_tool_error_handling_middleware_stamp_reaches_journal_permission_denied(self):
        """Same test for permission denied error."""
        from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware

        middleware = ToolErrorHandlingMiddleware(app_config=None)

        def handler(request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content="Error: Permission denied: /mnt/user-data",
                tool_call_id=request.id,
                name=request.name,
                status="success",
            )

        request = _make_tool_call_request("glob", "call_real_002")
        result = middleware.wrap_tool_call(request, handler)

        meta = result.additional_kwargs.get("alpha_tool_meta")
        assert meta is not None
        assert meta["status"] == "error"

        store = MemoryRunEventStore()
        journal = RunJournal(run_id="r2", thread_id="t2", event_store=store)
        journal._persist_tool_result_message(result)
        await journal.flush()

        events = await store.list_events("t2", "r2")
        tool_events = [e for e in events if e["event_type"] == "llm.tool.result"]
        assert tool_events
        assert tool_events[0]["content"]["status"] == "error"

    @pytest.mark.anyio
    async def test_tool_error_handling_middleware_stamp_reaches_journal_glob_no_results(self):
        """Test 'no results found' classification (partial_success)."""
        from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware

        middleware = ToolErrorHandlingMiddleware(app_config=None)

        def handler(request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content="No results found for pattern",
                tool_call_id=request.id,
                name=request.name,
                status="success",
            )

        request = _make_tool_call_request("glob", "call_real_003")
        result = middleware.wrap_tool_call(request, handler)

        meta = result.additional_kwargs.get("alpha_tool_meta")
        assert meta is not None
        # "no results found" -> partial_success
        assert meta["status"] == "partial_success"

        store = MemoryRunEventStore()
        journal = RunJournal(run_id="r3", thread_id="t3", event_store=store)
        journal._persist_tool_result_message(result)
        await journal.flush()

        events = await store.list_events("t3", "r3")
        tool_events = [e for e in events if e["event_type"] == "llm.tool.result"]
        assert tool_events
        # partial_success should be preserved (not downgraded to success)
        assert tool_events[0]["content"]["status"] == "partial_success"


class TestClassifierBashExitCode:
    """Test the classifier's handling of bash exit codes.

    The issue: bash commands that fail append 'Exit Code: N' to their output,
    but the classifier only checks for 'Error:' prefix. So a failed bash command
    like 'ls /nonexistent' produces:
        ls: cannot access '/nonexistent': No such file or directory
        Exit Code: 1
    And this is classified as SUCCESS because it doesn't start with 'Error:'.
    """

    @pytest.mark.anyio
    async def test_bash_nonzero_exit_is_classified_as_error(self):
        """A bash command with nonzero exit code should be classified as error.

        This tests the CLASSIFIER defect: the classifier doesn't recognize
        the 'Exit Code: N' marker that sandbox providers append.
        """
        from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware

        middleware = ToolErrorHandlingMiddleware(app_config=None)

        # Simulate a bash tool output with nonzero exit code
        # This is what the bash tool actually returns
        def handler(request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content="ls: cannot access '/nonexistent': No such file or directory\nExit Code: 1",
                tool_call_id=request.id,
                name=request.name,
                status="success",
            )

        request = _make_tool_call_request("bash", "call_bash_001")
        result = middleware.wrap_tool_call(request, handler)

        meta = result.additional_kwargs.get("alpha_tool_meta")
        assert meta is not None, "middleware must stamp alpha_tool_meta"
        # THIS IS THE BUG: currently returns 'success' but should be 'error'
        print(f"Actual classification: {meta['status']}")
        # The test documents the current (buggy) behavior
        # After fix, this should be 'error'
        assert meta["status"] == "error", f"Bash nonzero exit should be classified as error, got {meta['status']}"

    @pytest.mark.anyio
    async def test_bash_zero_exit_is_classified_as_success(self):
        """A bash command with zero exit code should be classified as success."""
        from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware

        middleware = ToolErrorHandlingMiddleware(app_config=None)

        def handler(request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content="file1.txt\nfile2.txt\nExit Code: 0",
                tool_call_id=request.id,
                name=request.name,
                status="success",
            )

        request = _make_tool_call_request("bash", "call_bash_002")
        result = middleware.wrap_tool_call(request, handler)

        meta = result.additional_kwargs.get("alpha_tool_meta")
        assert meta is not None
        assert meta["status"] == "success"

    @pytest.mark.anyio
    async def test_bash_tool_nonzero_exit_is_classified_as_error(self):
        """Test that 'bash_tool' (alternative name) also gets exit code classification."""
        from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware

        middleware = ToolErrorHandlingMiddleware(app_config=None)

        def handler(request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content="Command failed\nExit Code: 2",
                tool_call_id=request.id,
                name=request.name,
                status="success",
            )

        # Test with bash_tool name
        request = _make_tool_call_request("bash_tool", "call_bash_004")
        result = middleware.wrap_tool_call(request, handler)

        meta = result.additional_kwargs.get("alpha_tool_meta")
        assert meta is not None
        assert meta["status"] == "error"

    @pytest.mark.anyio
    async def test_bash_signal_exit_is_classified_as_error(self):
        """A bash command killed by signal (e.g., Exit Code: -9) should be error."""
        from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware

        middleware = ToolErrorHandlingMiddleware(app_config=None)

        def handler(request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content="Killed\nExit Code: -9",
                tool_call_id=request.id,
                name=request.name,
                status="success",
            )

        request = _make_tool_call_request("bash", "call_bash_003")
        result = middleware.wrap_tool_call(request, handler)

        meta = result.additional_kwargs.get("alpha_tool_meta")
        assert meta is not None
        assert meta["status"] == "error"
