"""D/E/F-batch: Signal channel, STT worker, local probe, OpenAI compat, undo, context files, pairing, fallback evals."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.channels.message_bus import MessageBus
from app.channels.signal import SignalChannel, _chunk_text, _envelope_to_chat, _parse_envelopes


@pytest.fixture()
def bus():
    return MessageBus()


def test_signal_envelope_parsing():
    assert _parse_envelopes([{"source": "+1555", "dataMessage": {"message": "hi"}}])
    assert _parse_envelopes({"messages": []}) == []
    assert _parse_envelopes("garbage") == []
    assert _envelope_to_chat({"source": "+1555", "dataMessage": {"message": " hello "}}) == ("+1555", "dm:+1555", "hello")
    assert _envelope_to_chat({"source": "+1555", "dataMessage": {"groupInfo": {"groupId": "abc"}, "message": "hi"}}) == ("+1555", "group:abc", "hi")
    assert _envelope_to_chat({"source": "+1555", "dataMessage": {}}) is None
    assert _envelope_to_chat({}) is None
    assert _chunk_text("abc", limit=2) == ["ab", "c"]


def test_signal_registry_and_capabilities():
    from app.channels.manager import CHANNEL_CAPABILITIES
    from app.channels.service import _CHANNEL_CREDENTIAL_KEYS, _CHANNEL_REGISTRY

    assert _CHANNEL_REGISTRY["signal"] == "app.channels.signal:SignalChannel"
    assert _CHANNEL_CREDENTIAL_KEYS["signal"] == ["base_url", "number"]
    assert CHANNEL_CAPABILITIES["signal"] == {"supports_streaming": False}


@pytest.mark.asyncio
async def test_signal_refuses_without_number(bus):
    channel = SignalChannel(bus, {"base_url": "http://127.0.0.1:9"})
    await channel.start()
    assert channel.is_running is False


@pytest.mark.asyncio
async def test_signal_refuses_unreachable_wrapper(bus):
    channel = SignalChannel(bus, {"base_url": "http://127.0.0.1:9", "number": "+15550001111"})
    await channel.start()
    assert channel.is_running is False


class _FakeSignalHandler(BaseHTTPRequestHandler):
    received: list = []
    send_bodies: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/v1/about"):
            body = json.dumps({"versions": ["v1"]}).encode()
        elif self.path.startswith("/v1/receive/"):
            body = json.dumps([{"source": "+15550002222", "dataMessage": {"message": "hello bot"}}]).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        type(self).send_bodies.append(json.loads(self.rfile.read(length) or b"{}"))
        body = json.dumps({"timestamp": 1}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def fake_signal():
    _FakeSignalHandler.send_bodies = []
    server = HTTPServer(("127.0.0.1", 0), _FakeSignalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.mark.asyncio
async def test_signal_poll_and_send_against_fake_wrapper(bus, fake_signal):
    channel = SignalChannel(bus, {"base_url": fake_signal, "number": "+15550001111", "poll_interval_seconds": 60})
    await channel.start()
    assert channel.is_running is True
    try:
        parsed = channel.receive_poll_result_for_test([{"source": "+1", "dataMessage": {"message": "x"}}])
        assert parsed[0].chat_id == "dm:+1"

        from app.channels.message_bus import OutboundMessage

        await channel.send(OutboundMessage(channel_name="signal", chat_id="dm:+15550002222", thread_id="t", text="reply here"))
        assert _FakeSignalHandler.send_bodies
        sent = _FakeSignalHandler.send_bodies[-1]
        assert sent["recipients"] == ["+15550002222"] and sent["message"] == "reply here"
    finally:
        await channel.stop()
    assert channel.is_running is False


def test_stt_graceful_paths(tmp_path):
    from agent_workspace.media.stt import stt_available, transcribe_file

    missing = transcribe_file(tmp_path / "nope.wav")
    assert missing.ok is False and "not found" in missing.reason
    bad_ext = tmp_path / "note.txt"
    bad_ext.write_text("hi", encoding="utf-8")
    res = transcribe_file(bad_ext)
    assert res.ok is False and "unsupported" in res.reason
    assert isinstance(stt_available(), bool)


def test_local_probe_ssrf_guard():
    from agent_workspace.models.local import probe_openai_compatible

    refused = probe_openai_compatible("http://169.254.169.254/latest/meta-data/")
    assert refused.reachable is False and "SSRF" in refused.reason
    assert probe_openai_compatible("").reachable is False
    assert probe_openai_compatible("http://127.0.0.1:9").reachable is False


def test_local_probe_fake_server(fake_signal):
    from agent_workspace.models.local import probe_openai_compatible

    class _ModelsHandler(_FakeSignalHandler):
        def do_GET(self):
            body = json.dumps({"data": [{"id": "llama3.1"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), _ModelsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        health = probe_openai_compatible(f"http://127.0.0.1:{server.server_address[1]}")
        assert health.reachable is True and health.models == ["llama3.1"]
    finally:
        server.shutdown()


def test_openai_translation_helpers():
    from app.gateway.routers.openai_compat import (
        _last_assistant_text,
        openai_chat_response,
        openai_messages_to_run_input,
    )

    run_input = openai_messages_to_run_input([{"role": "system", "content": "Be nice."}, {"role": "user", "content": "Hi"}])
    assert run_input == {"messages": [{"role": "user", "content": "Be nice.\n\nHi"}]}
    assert openai_messages_to_run_input([{"role": "user", "content": "  "}]) == {"messages": []}
    values = {"messages": [{"type": "human", "content": "Hi"}, {"type": "ai", "content": "Hello!"}]}
    assert _last_assistant_text(values) == "Hello!"
    assert _last_assistant_text({}) == ""
    resp = openai_chat_response("gpt-x", "Hello!", thread_id="t1")
    assert resp["object"] == "chat.completion" and resp["choices"][0]["message"]["content"] == "Hello!"


def test_undo_target_finder():
    from app.gateway.routers.threads import _find_undo_target_ids

    def _msg(id, role):
        return {"id": id, "type": role, "content": "x"}

    assert _find_undo_target_ids([]) is None
    assert _find_undo_target_ids([_msg("u1", "human"), _msg("a1", "ai")]) is None
    two_turns = [_msg("u1", "human"), _msg("a1", "ai"), _msg("u2", "human"), _msg("a2", "ai"), _msg("a3", "ai")]
    assert _find_undo_target_ids(two_turns) == ["a1"]
    assert _find_undo_target_ids([_msg("u1", "human"), _msg("u2", "human"), _msg("a2", "ai")]) is None
    hidden = [_msg("u1", "human"), _msg("a1", "ai"), {"id": "h", "type": "human", "content": "x", "additional_kwargs": {"hide_from_ui": True}}, _msg("u2", "human"), _msg("a2", "ai")]
    assert _find_undo_target_ids(hidden) == ["a1"]


def test_context_files_loader(tmp_path):
    from agent_workspace.context_files import find_context_files, load_context_files

    repo = tmp_path / "repo"
    sub = repo / "pkg"
    sub.mkdir(parents=True)
    (repo / "AGENTS.md").write_text("# Rules\nBe kind.", encoding="utf-8")
    found = find_context_files(sub)
    assert any(p.name == "AGENTS.md" for p in found)
    excerpt = load_context_files(sub)
    assert "Be kind" in excerpt and "AGENTS.md" in excerpt
    assert load_context_files(tmp_path / "empty") == ""
    big = tmp_path / "big"
    big.mkdir()
    (big / "AGENTS.md").write_text("y" * 50000, encoding="utf-8")
    assert len(load_context_files(big, max_total_chars=1000)) <= 1050


def test_pairing_connect_code_flow():
    from app.channels.commands import KNOWN_CHANNEL_COMMANDS, extract_connect_code, is_known_channel_command

    assert extract_connect_code("/connect abc123") == "abc123"
    assert extract_connect_code("@bot /connect abc123") == "abc123"
    assert extract_connect_code("/connect") is None
    assert extract_connect_code("hello there") is None
    assert is_known_channel_command("/help me") is True
    assert is_known_channel_command("/approve yes") is True
    assert is_known_channel_command("just chatting") is False
    assert "/connect" not in KNOWN_CHANNEL_COMMANDS


def test_fallback_chain_fault_injection():
    from agent_workspace.models.fallback import FallbackChatModel, ModelFallbackExhaustedError

    class _Flaky:
        def __init__(self, behavior):
            self.behavior = behavior
            self.calls = 0

        def invoke(self, messages, stop=None):
            self.calls += 1
            if isinstance(self.behavior, BaseException):
                raise self.behavior
            from langchain_core.messages import AIMessage

            return AIMessage(content=self.behavior)

        def bind_tools(self, tools, tool_choice=None, **kwargs):
            return self

    class _Err500(Exception):
        status_code = 500

    first, second = _Flaky(_Err500("boom")), _Flaky("recovered")
    chain = FallbackChatModel(instances=[first, second], model_names=["m1", "m2"])
    answer = chain.invoke([{"role": "user", "content": "hi"}])
    assert answer.content == "recovered"
    assert chain.get_last_effective_model() == "m2"

    dead1, dead2 = _Flaky(_Err500("x")), _Flaky(_Err500("y"))
    exhausted = FallbackChatModel(instances=[dead1, dead2], model_names=["m1", "m2"])
    try:
        exhausted.invoke([{"role": "user", "content": "hi"}])
        raise AssertionError("expected exhaustion")
    except ModelFallbackExhaustedError as exc:
        assert "m1" in str(exc) and "m2" in str(exc)
