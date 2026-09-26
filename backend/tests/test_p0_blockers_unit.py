"""P0 blocker regressions: the four unconditional defects that stopped a run.

Each test here failed before the corresponding fix and is written to fail again
if the fix is reverted. They are fast, in-process, and pin the *mechanism*; the
end-to-end proof that the shipped binary completes a real task lives in
``test_p0_live_binary_integration.py``.

Covered:

1. BLOCKER 1 - a ``ModelConfig`` field must not reach the provider constructor.
   (``test_model_factory_metadata_strip.py`` owns the strip-set invariant; this
   file pins the specific ``capabilities`` leak end-to-end through the request.)
2. BLOCKER 2 - a model declaring ``supports_thinking: false`` must work with
   zero configuration.
3. BLOCKER 3 - the SYNC graph path must not raise ``NotImplementedError`` for a
   middleware that defines the async hook. An async-path test would pass against
   the broken code, so every test in this class drives ``agent.stream()``.
4. BLOCKER 4 - non-ASCII frames must survive the real emit path on a
   cp1252-encoded stdout.
5. BLOCKER 5 - a failed run must emit an ``error`` frame with a code and make
   the CLI exit non-zero; a tool call must carry one identity.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from dataclasses import dataclass

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from alpha.client import StreamRunError
from alpha.tui import cli as cli_module

# ---------------------------------------------------------------------------
# BLOCKER 1 - ``capabilities`` leaked into the completion request
# ---------------------------------------------------------------------------


def _app_config_with_capabilities():
    from alpha.config.app_config import AppConfig
    from alpha.config.model_config import ModelConfig
    from alpha.config.sandbox_config import SandboxConfig

    model = ModelConfig(
        name="p0-probe",
        display_name="P0 probe",
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="p0-probe-1",
        api_key="test-key-not-a-secret",
        supports_thinking=False,
        supports_reasoning_effort=False,
        capabilities=["vision", "stt"],
    )
    return AppConfig(
        models=[model],
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
    )


def test_capabilities_never_reaches_the_completion_request(monkeypatch):
    """The reported ``TypeError`` reproduced, then shown gone.

    ``capabilities`` has ``default_factory=list`` so it is present in every
    ``model_dump()``; it used to be forwarded to the provider constructor, which
    langchain-openai diverts into ``model_kwargs`` and then spreads into every
    completion request. The HTTP transport is stubbed, so what is asserted is the
    exact JSON body that would have been sent -- the real failure mode, not a
    proxy for it.
    """
    import httpx
    from openai.types.chat import ChatCompletion
    from openai.types.chat.chat_completion import Choice
    from openai.types.chat.chat_completion_message import ChatCompletionMessage

    from alpha.models.factory import create_chat_model

    completion = ChatCompletion(
        id="chatcmpl-p0",
        choices=[Choice(finish_reason="stop", index=0, message=ChatCompletionMessage(role="assistant", content="ok"))],
        created=0,
        model="p0-probe-1",
        object="chat.completion",
    )
    sent: list[dict] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        sent.append(__import__("json").loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=completion.model_dump(exclude_none=True), request=request)

    transport = httpx.MockTransport(_handler)
    original_transport = httpx.HTTPTransport.handle_request

    def _patched(self, request):  # noqa: ANN001
        return transport.handle_request(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _patched, raising=True)

    model = create_chat_model(
        "p0-probe",
        thinking_enabled=False,
        attach_tracing=False,
        app_config=_app_config_with_capabilities(),
    )
    model.invoke([HumanMessage(content="hi")])

    assert len(sent) == 1, "the completion request never reached the transport"
    assert "capabilities" not in sent[0], f"capabilities leaked into the request payload: {sorted(sent[0])}"
    assert original_transport is not None


# ---------------------------------------------------------------------------
# BLOCKER 2 - ``thinking_enabled`` must not be True by default
# ---------------------------------------------------------------------------


def _non_thinking_config():
    from alpha.config.app_config import AppConfig
    from alpha.config.model_config import ModelConfig
    from alpha.config.sandbox_config import SandboxConfig

    model = ModelConfig(
        name="p0-nothink",
        display_name=None,
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="p0-nothink-1",
        api_key="test-key-not-a-secret",
        supports_thinking=False,
        supports_reasoning_effort=False,
    )
    return AppConfig(
        models=[model],
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
    )


def test_non_thinking_model_builds_with_zero_configuration():
    """``create_chat_model`` already defaulted to ``False``; the client did not.

    The client default was ``True``, so an unconfigured embedded client asked for
    thinking against a model that declares ``supports_thinking: false`` and the
    factory failed closed -- ~86s of agent assembly before the first model call.
    A ``supports_thinking: false`` model must build with no configuration at all.
    """
    from alpha.models.factory import create_chat_model

    model = create_chat_model(
        "p0-nothink",
        app_config=_non_thinking_config(),
        attach_tracing=False,
    )
    assert model is not None


def test_client_default_does_not_request_thinking(monkeypatch):
    """The client's default is ``False``, matching the factory and ``ModelConfig``.

    Pinned on the constructor default *and* on what the assembled runnable config
    carries, because ``_ensure_agent`` reads the latter and defaults it again.
    """
    import inspect

    from alpha.client import AgentWorkspaceClient

    default = inspect.signature(AgentWorkspaceClient.__init__).parameters["thinking_enabled"].default
    assert default is False, f"thinking_enabled defaults to {default!r}; a supports_thinking:false model cannot start"

    config = _non_thinking_config()
    monkeypatch.setattr("alpha.client.get_app_config", lambda: config)
    monkeypatch.setattr("alpha.client.reload_app_config", lambda *a, **k: None)
    monkeypatch.setattr(
        "alpha.client.configure_subagent_execution_capacity",
        lambda *a, **k: None,
    )

    client = AgentWorkspaceClient()
    runnable = client._get_runnable_config("t-p0")
    assert runnable["configurable"]["thinking_enabled"] is False


# ---------------------------------------------------------------------------
# BLOCKER 3 - the SYNC graph path
# ---------------------------------------------------------------------------


def _sync_stream_through(middlewares) -> list:
    """Drive the real synchronous ``create_agent(...).stream()`` path.

    Deliberately ``.stream()`` and never ``.astream()``: the shipped binary is a
    sync client, and an async-path test passes against code that is exactly as
    broken as the shipped code.
    """
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    model = GenericFakeChatModel(messages=iter([AIMessage(content="done")]))
    agent = create_agent(model=model, tools=[], middleware=middlewares)
    events = list(agent.stream({"messages": [HumanMessage(content="hi")]}, stream_mode="updates"))
    assert events, "the sync graph produced no events at all"
    return events


def test_autonomous_command_middleware_survives_the_sync_path():
    """An async-only ``awrap_model_call`` took down every synchronous run.

    ``create_agent`` wires the wrap hook whenever *either* variant is overridden
    and hands the graph a ``None`` for the missing one, so the sync ``stream()``
    raised ``NotImplementedError: Synchronous implementation of wrap_model_call
    is not available`` -- which is exactly what the real ``alpha --json`` run did
    after 117s and zero answers.
    """
    from alpha.agents.middlewares.autonomous_command_middleware import AutonomousCommandMiddleware

    events = _sync_stream_through([AutonomousCommandMiddleware()])
    assert events, "the sync graph produced no events"
    # The graph must actually have run the model to completion, not merely
    # avoided the NotImplementedError. ``stream_mode="updates"`` yields per-node
    # update dicts; the model node's output is where the AI message lands.
    model_outputs = [event["model"]["messages"] for event in events if isinstance(event, dict) and "model" in event]
    assert model_outputs, f"the model node never produced output on the sync path: {events}"
    assert any(isinstance(m, AIMessage) for m in model_outputs[-1])


def test_user_model_middleware_survives_the_sync_path():
    """``before_agent``/``after_agent`` must have sync variants too.

    Only overriding the ``a*`` variants makes ``create_agent`` build the node from
    the async hook and pass a ``None`` sync callable, which a sync run reports as
    ``TypeError: No synchronous function provided to "abefore_agent"``.
    """
    from alpha.agents.middlewares.user_model_middleware import UserModelMiddleware

    events = _sync_stream_through([UserModelMiddleware()])
    assert events


def test_both_middlewares_together_survive_the_sync_path():
    """The shipped configuration, in the shipped order, on the sync path."""
    from alpha.agents.middlewares.autonomous_command_middleware import AutonomousCommandMiddleware
    from alpha.agents.middlewares.user_model_middleware import UserModelMiddleware

    events = _sync_stream_through([AutonomousCommandMiddleware(), UserModelMiddleware()])
    assert events


# ---------------------------------------------------------------------------
# BLOCKER 4 - non-ASCII output must not kill the stream
# ---------------------------------------------------------------------------


_NON_ASCII_SAMPLES = [
    pytest.param("\U0001f43a", id="emoji"),
    pytest.param("你好，世界", id="cjk"),
    pytest.param("école", id="combining-mark"),
]


@pytest.mark.parametrize("text", _NON_ASCII_SAMPLES)
def test_non_ascii_frame_survives_a_cp1252_stdout(text, tmp_path):
    """Drive the *real* emit path with a cp1252 stdout, as Windows ships.

    A cp1252 stream cannot encode any of these characters, so the pre-fix code
    raised ``UnicodeEncodeError`` mid-stream on the first non-ASCII frame -- a
    ~39% crash probability per run. The frame must arrive byte-exact and still
    parse as NDJSON.
    """
    driver = tmp_path / "emit_driver.py"
    driver.write_text(
        "\n".join(
            [
                "import io, sys",
                "from alpha.tui import cli as c",
                "class E:",
                "    type = 'messages-tuple'",
                "    data = {'type': 'ai', 'content': sys.argv[2]}",
                "# A stdout that really is cp1252, not a pretend one. _configure_protocol_streams",
                "# must upgrade it in place, so emit through that same object.",
                "raw = io.TextIOWrapper(",
                "    io.BytesIO(), encoding='cp1252', errors='strict', newline='\\n')",
                "sys.stdout = raw",
                "c._configure_protocol_streams()",
                "c._emit_event(sys.stdout, E())",
                "sys.stdout.flush()",
                "sys.stdout.buffer.seek(0)",
                "sys.stderr.write(sys.stdout.buffer.read().decode('utf-8'))",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(driver), "x", text],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_repo_root_for_tests()),
    )
    assert result.returncode == 0, f"emit path raised on non-ASCII output: {result.stderr}"
    line = result.stderr.strip()
    frame = json.loads(line)
    assert frame["type"] == "messages-tuple"
    assert frame["data"]["content"] == text, "the payload was mangled in transit"


def test_configure_protocol_streams_upgrades_a_cp1252_stdout():
    """The reconfiguration itself, asserted on the stream's own attributes."""
    from alpha.tui.cli import PROTOCOL_STREAM_ENCODING, _configure_protocol_streams

    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", newline="\n")
    original = sys.stdout
    sys.stdout = stream
    try:
        _configure_protocol_streams()
        assert stream.encoding == PROTOCOL_STREAM_ENCODING
        # A code point cp1252 cannot represent must now be writable.
        stream.write("\U0001f43a")
        stream.flush()
    finally:
        sys.stdout = original
    assert "\U0001f43a" in stream.buffer.getvalue().decode("utf-8")


# ---------------------------------------------------------------------------
# BLOCKER 5 - the protocol must not lie about failure
# ---------------------------------------------------------------------------


@dataclass
class _FakeEvent:
    type: str
    data: dict


def _client_with_stream(chunks=None):
    """A client whose graph stream yields *chunks*, with no real model involved."""
    from unittest.mock import MagicMock

    from alpha.client import AgentWorkspaceClient

    client = AgentWorkspaceClient.__new__(AgentWorkspaceClient)
    client._app_config = MagicMock()
    agent = MagicMock()
    agent.stream.return_value = iter(chunks or [])
    client._agent = agent
    client._agent_config_key = object()
    client._agent_name = None
    client._environment = None
    client._checkpointer = None
    client._model_name = None
    client._thinking_enabled = False
    client._subagent_enabled = False
    client._plan_mode = False
    client._available_skills = None
    client._checkpoint_channel_mode = "full"
    client._checkpoint_snapshot_frequency = 10
    client._middlewares = []
    client._subagent_execution_capacity = 3
    client._ensure_agent = lambda *a, **k: None
    return client


_LLM_FALLBACK = {
    "agent_workspace_error_fallback": True,
    "error_type": "APIConnectionError",
    "error_reason": "transient",
    "error_detail": "connection reset by peer",
}


def test_llm_failure_emits_error_frame_and_raises_instead_of_end():
    """The reported lie: a failed run emitted a normal ``end`` and exited 0.

    The LLM error-handling middleware substitutes a placeholder ``AIMessage``
    stamped with ``agent_workspace_error_fallback``. That message is
    indistinguishable from an answer by content, so without reading the marker a
    failed run is indistinguishable from a successful one -- and this is exactly
    the case that shipped.
    """
    client = _client_with_stream(
        [
            ("messages", (AIMessage(content="LLM request failed: connection reset by peer", additional_kwargs=_LLM_FALLBACK), {})),
        ]
    )

    events = []
    with pytest.raises(StreamRunError) as excinfo:
        for event in client.stream("hello", thread_id="t-p0"):
            events.append(event)

    types = [e.type for e in events]
    assert "error" in types, f"no error frame emitted: {types}"
    assert "end" not in types, f"a failed run must not also report success: {types}"
    error = next(e for e in events if e.type == "error")
    assert error.data["code"] == "llm_error"
    assert error.data["correlation_id"]
    assert excinfo.value.code == "llm_error"


def test_successful_run_still_ends_with_end_and_no_error():
    """The success path is unchanged: a terminal ``end`` and no error frame."""
    client = _client_with_stream(
        [
            ("messages", (AIMessageChunk(content="hi", id="ai-1"), {})),
            ("values", {"messages": [HumanMessage(content="hi", id="h-1"), AIMessage(content="hi", id="ai-1")]}),
        ]
    )

    events = list(client.stream("hello", thread_id="t-p0"))
    types = [e.type for e in events]
    assert types[-1] == "end"
    assert "error" not in types
    assert events[-1].data["usage"]["total_tokens"] == 0


def test_graph_exception_emits_error_frame_with_a_stable_code():
    """An exception escaping the graph is reported, not swallowed."""
    from langgraph.errors import GraphRecursionError

    def _boom():
        raise GraphRecursionError("recursion limit reached")
        yield  # pragma: no cover - makes this a generator, as the graph stream is

    client = _client_with_stream(None)
    client._agent.stream.return_value = _boom()

    events = []
    with pytest.raises(StreamRunError) as excinfo:
        for event in client.stream("hello", thread_id="t-p0"):
            events.append(event)

    error = next(e for e in events if e.type == "error")
    assert error.data["code"] == "recursion_limit"
    assert excinfo.value.code == "recursion_limit"


def test_streamed_tool_call_arrives_as_one_event_with_one_identity():
    """Tool calls must not arrive as unjoinable fragment frames.

    The provider streams a tool call as a name+id frame followed by argument
    deltas carrying neither name nor id, so a consumer could not associate a tool
    result with the call that produced it. One call, one identity, one event.
    """
    client = _client_with_stream(
        [
            (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        id="ai-1",
                        tool_call_chunks=[{"name": "write_file", "args": "", "id": "call-abc", "index": 0}],
                    ),
                    {},
                ),
            ),
            (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        id="ai-1",
                        tool_call_chunks=[{"name": "", "args": '{"path": ', "id": None, "index": 0}],
                    ),
                    {},
                ),
            ),
            (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        id="ai-1",
                        tool_call_chunks=[{"name": "", "args": '"/tmp/x.txt"}', "id": None, "index": 0}],
                    ),
                    {},
                ),
            ),
        ]
    )

    tool_events = [e for e in client.stream("hello", thread_id="t-p0") if e.type == "messages-tuple" and e.data.get("tool_calls")]

    assert len(tool_events) == 1, f"expected exactly one tool-call event, got {len(tool_events)}"
    calls = tool_events[0].data["tool_calls"]
    assert len(calls) == 1
    call = calls[0]
    assert call["name"] == "write_file"
    assert call["id"] == "call-abc"
    assert call["args"] == {"path": "/tmp/x.txt"}


def test_tool_result_is_joinable_to_its_call():
    """The join a consumer actually performs: result ``tool_call_id`` -> call ``id``."""
    client = _client_with_stream(
        [
            (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        id="ai-1",
                        tool_call_chunks=[{"name": "write_file", "args": "", "id": "call-abc", "index": 0}],
                    ),
                    {},
                ),
            ),
            (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        id="ai-1",
                        tool_call_chunks=[{"name": "", "args": '{"path": "/tmp/x.txt"}', "id": None, "index": 0}],
                    ),
                    {},
                ),
            ),
            ("messages", (ToolMessage(content="ok", name="write_file", tool_call_id="call-abc", id="tool-1"), {})),
        ]
    )

    call_ids = set()
    result_ids = set()
    for event in client.stream("hello", thread_id="t-p0"):
        if event.type != "messages-tuple":
            continue
        for call in event.data.get("tool_calls") or []:
            if call.get("id"):
                call_ids.add(call["id"])
        if event.data.get("type") == "tool" and event.data.get("tool_call_id"):
            result_ids.add(event.data["tool_call_id"])

    assert call_ids == {"call-abc"}
    assert call_ids & result_ids == call_ids, "a tool result could not be joined to its call"


# ---------------------------------------------------------------------------
# BLOCKER 5 - the CLI must not exit 0 on a failed run
# ---------------------------------------------------------------------------


def _stub_session(monkeypatch, client):
    class _Session:
        def resolve_thread(self, plan):
            return "t-p0"

    session = _Session()
    session.client = client
    monkeypatch.setattr(cli_module, "_make_session", lambda: session)
    return session


def _run_json_with_events(monkeypatch, events, *, raises: BaseException | None = None):
    """Drive ``_run_json`` with a stubbed session, capturing its exit code."""
    from alpha.tui.cli import LaunchPlan, _run_json

    class _Client:
        def stream(self, *a, **k):
            if raises is not None:
                raise raises
            return iter(events)

    _stub_session(monkeypatch, _Client())
    plan = LaunchPlan(mode="json", message="hi", thread_id="t-p0")
    return _run_json(plan)


def test_cli_json_exits_zero_on_success(monkeypatch, capsys):
    events = [_FakeEvent("messages-tuple", {"type": "ai", "content": "ok"}), _FakeEvent("end", {"usage": {}})]
    code = _run_json_with_events(monkeypatch, events)
    assert code == 0


def test_cli_json_exits_non_zero_when_the_stream_raises(monkeypatch):
    code = _run_json_with_events(monkeypatch, [], raises=StreamRunError("llm_error", "boom", correlation_id="r1"))
    assert code == cli_module.RUN_FAILED_EXIT_CODE != 0


def test_cli_print_exits_non_zero_on_failure(monkeypatch):
    from alpha.tui.cli import LaunchPlan, _run_print

    class _Client:
        def chat(self, *a, **k):
            raise StreamRunError("llm_error", "boom", correlation_id="r1")

    _stub_session(monkeypatch, _Client())
    code = _run_print(LaunchPlan(mode="print", message="hi", thread_id="t-p0"))
    assert code == cli_module.RUN_FAILED_EXIT_CODE != 0


def test_protocol_stdout_isolates_inherited_fd_1(tmp_path):
    """Only protocol frames may reach stdout.

    A prior run produced 41 lines of which 34 were unparseable because a
    subprocess wrote to inherited stdout. Asserted end to end: the child really
    runs with a pipe on fd 1 (the production case), writes the frame, then writes
    two non-frame lines to fd 1 -- one via ``os.write`` (what a subprocess does)
    and one via ``print`` (what stray library code does). Neither may appear.
    """
    script = tmp_path / "isolate.py"
    script.write_text(
        "\n".join(
            [
                "import os, sys",
                "from alpha.tui import cli as c",
                "c._configure_protocol_streams()",
                "protocol, fd = c._open_protocol_stdout()",
                "try:",
                "    c._emit_event(protocol, type('E', (), {'type': 'end', 'data': {'usage': {}}})())",
                "    # Simulate a tool subprocess writing to inherited stdout, and a stray",
                "    # print in library code, neither of which may reach the caller's stdout.",
                "    os.write(1, b'this is not a protocol frame\\n')",
                "    sys.stdout.write('stray print also not a frame\\n')",
                "    sys.stdout.flush()",
                "finally:",
                "    protocol.flush()",
                "    c._restore_stdout(fd)",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_repo_root_for_tests()),
    )
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, "the protocol frame never reached stdout"
    for line in lines:
        frame = json.loads(line)  # raises if any non-frame line leaked
        assert set(frame) == {"type", "data"}
    assert len(lines) == 1, f"non-frame output leaked to stdout: {lines}"


def test_protocol_frames_follow_a_caller_redirected_stdout(monkeypatch, capsys):
    """The isolation must not steal output from a caller that redirected the stream.

    ``_open_protocol_stdout`` moves the frames onto a private duplicate of fd 1
    only when ``sys.stdout`` *is* fd 1. A host (or a test harness) that replaced
    the stream object has already decoupled the frames from fd 1, so the frames
    must stay on the caller's stream -- otherwise ``--json`` becomes uncapturable
    for every embedder, which is a regression in its own right.
    """
    import io

    buffer = io.StringIO()
    monkeypatch.setattr(cli_module.sys, "stdout", buffer)
    protocol, protocol_fd = cli_module._open_protocol_stdout()
    try:
        cli_module._emit_event(protocol, _FakeEvent("end", {"usage": {}}))
    finally:
        cli_module._restore_stdout(protocol_fd)

    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1, f"the caller's redirected stream received {lines!r}"
    assert json.loads(lines[0])["type"] == "end"


# ---------------------------------------------------------------------------
# doctor.py must not say Ready for a checkout that cannot run
# ---------------------------------------------------------------------------


def _import_doctor():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "doctor.py"
    spec = importlib.util.spec_from_file_location("p0_doctor", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_minimal_config(tmp_path, *, model: str = "p0-probe-1"):
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "config_version: 53",
                "models:",
                "  - name: p0-probe",
                "    display_name: P0 probe",
                "    description: probe",
                "    use: langchain_openai:ChatOpenAI",
                f"    model: {model}",
                "    base_url: https://example.invalid/v1",
                "    api_key: p0-not-a-real-key",
                "    supports_thinking: false",
                "    supports_reasoning_effort: false",
                "sandbox:",
                "  use: alpha.sandbox.local:LocalSandboxProvider",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def test_doctor_construction_check_fails_on_an_unbuildable_model(tmp_path, monkeypatch):
    """A model that cannot be built must be a FAIL, not a warning.

    Every pre-existing doctor check is static, which is why a checkout where all
    four P0 defects were present still reported ``Status: Ready``. This drives the
    real factory, so an unbuildable model surfaces here instead of 86 seconds into
    a user's first run.
    """
    doctor = _import_doctor()
    config = _write_minimal_config(tmp_path)

    def _boom(*args, **kwargs):
        raise ValueError("model does not support thinking")

    monkeypatch.setattr("alpha.models.factory.create_chat_model", _boom)
    results = doctor.check_models_construct(config)
    assert results, "the construction check produced no results at all"
    assert all(r.status == "fail" for r in results), [(r.label, r.status) for r in results]
    assert "does not support thinking" in results[0].detail


def test_doctor_construction_check_catches_a_diverted_metadata_key(tmp_path, monkeypatch):
    """The BLOCKER-1 class of defect, caught without spending a request.

    The provider client does not reject an unknown constructor kwarg -- it
    diverts it into ``model_kwargs`` and fails at request time, minutes later and
    only on the unlucky run that happens to call that model. Reading
    ``model_kwargs`` turns that into an immediate, free failure.
    """
    doctor = _import_doctor()
    config = _write_minimal_config(tmp_path)

    class _Leaky:
        """A client that has already swallowed Alpha metadata."""

        model_kwargs = {"capabilities": []}

    monkeypatch.setattr("alpha.models.factory.create_chat_model", lambda *a, **k: _Leaky())
    results = doctor.check_models_construct(config)
    assert [r.status for r in results] == ["fail"], [(r.label, r.status) for r in results]
    assert "capabilities" in results[0].detail


def test_doctor_construction_check_passes_for_a_healthy_model(tmp_path, monkeypatch):
    """The check must also be able to say OK, or it would always be a failure."""
    doctor = _import_doctor()
    config = _write_minimal_config(tmp_path)

    class _Healthy:
        model_kwargs: dict = {}

    monkeypatch.setattr("alpha.models.factory.create_chat_model", lambda *a, **k: _Healthy())
    results = doctor.check_models_construct(config)
    assert [r.status for r in results] == ["ok"], [(r.label, r.status, r.detail) for r in results]


def test_doctor_reachability_check_fails_when_the_provider_never_answers(tmp_path, monkeypatch):
    """Static checks cannot tell 'the key is set' from 'the key works'."""
    doctor = _import_doctor()
    config = _write_minimal_config(tmp_path)

    class _Dead:
        model_kwargs: dict = {}

        def with_config(self, *a, **k):
            return self

        def invoke(self, *a, **k):
            raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr("alpha.models.factory.create_chat_model", lambda *a, **k: _Dead())
    results = doctor.check_provider_reachable(config)
    assert [r.status for r in results] == ["fail"], [(r.label, r.status) for r in results]
    assert "ConnectionRefusedError" in results[0].detail


def test_doctor_reports_run_readiness_as_a_section(monkeypatch):
    """Run Readiness is wired into main(), and it is what can veto Ready.

    If these checks were computed and never rendered, the whole fix would be
    decorative: doctor would still print ``Status: Ready`` and still exit 0.
    """
    import inspect

    doctor = _import_doctor()
    source = inspect.getsource(doctor.main)
    assert "Run Readiness" in source, "the Run Readiness section is not rendered by main()"
    assert "check_models_construct" in source
    assert "check_provider_reachable" in source
    assert "readiness_failed" in source, "a readiness failure does not affect the summary"


def test_doctor_probe_timeout_is_bounded(monkeypatch):
    """A dead endpoint must report, not hang the health check."""
    doctor = _import_doctor()
    assert 0 < doctor._PROBE_TIMEOUT_SECONDS <= 120, (
        f"probe timeout {doctor._PROBE_TIMEOUT_SECONDS}s is not a bounded health check"
    )


def test_doctor_run_readiness_does_not_false_fail_on_a_healthy_checkout(tmp_path):
    """Both readiness checks must survive a cold import in one process.

    Found by running ``doctor.py`` for real: ``check_models_construct`` imported
    ``alpha.config.app_config`` before ``alpha.models.factory``, and resolving the
    factory submodule *through* the then-partially-initialised ``alpha.models``
    package raised ``cannot import name 'create_chat_model' from partially
    initialized module 'alpha.models'``. The check reported FAIL on a checkout
    that demonstrably works, which is worse than having no check: it teaches
    operators to ignore the one check that cannot lie.

    So the construction check is driven twice in a row from a cold import, and
    the *first* call -- the one that hits the import-order bug -- must not fail.
    Nothing is mocked: this is the real factory against a real config.
    """
    doctor = _import_doctor()
    config = _write_minimal_config(tmp_path)

    first = doctor.check_models_construct(config)
    assert [r.status for r in first] == ["ok"], [(r.label, r.status, r.detail) for r in first]
    # Second call in the same process: the package is fully imported now, so a
    # difference between the two would itself indicate import-order dependence.
    second = doctor.check_models_construct(config)
    assert [r.status for r in second] == ["ok"], [(r.label, r.status, r.detail) for r in second]


def test_doctor_reachability_imports_cleanly_too(tmp_path, monkeypatch):
    """Same import-order hazard, other function; caught without a network call."""
    doctor = _import_doctor()
    config = _write_minimal_config(tmp_path)
    monkeypatch.setenv(doctor.SKIP_NETWORK_ENV, "1")
    results = doctor.check_provider_reachable(config)
    # The env escape hatch short-circuits before any import, so assert the
    # import-order fix by disabling it and stubbing the client instead.
    monkeypatch.delenv(doctor.SKIP_NETWORK_ENV)
    monkeypatch.setattr("alpha.models.factory.create_chat_model", lambda *a, **k: _UnreachableClient())
    results = doctor.check_provider_reachable(config)
    assert results and all(r.status == "fail" for r in results), [(r.label, r.status, r.detail) for r in results]


class _UnreachableClient:
    """A built client whose provider never answers."""

    model_kwargs: dict = {}

    def with_config(self, *args, **kwargs):
        return self

    def invoke(self, *args, **kwargs):
        raise ConnectionRefusedError("connection refused")


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _repo_root_for_tests() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parents[2])
