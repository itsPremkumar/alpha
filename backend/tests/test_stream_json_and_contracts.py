"""Phase 8 tests: machine-readable output, contract typing, prompt stability.

The contract tests are written to *fail* on drift rather than to describe the
current shape, so they keep biting after the change that added them.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from alpha.streamjson.emitter import (
    ENVELOPE_FIELDS,
    SCHEMA_VERSION,
    TERMINAL_FIELDS,
    UnknownFrameField,
    UnaccountedField,
    audit_frame,
    audit_stream,
    parse_ndjson,
)
from alpha.streamjson.emitter import StreamJsonEmitter
from alpha.wire_contracts import bootstrap, registered
from alpha.wire_contracts.generate import DEFAULT_MANIFEST_PATH, DEFAULT_TS_PATH, build
from alpha.wire_contracts.registry import (
    contract_fields,
    required_fields,
    render_manifest,
    render_typescript,
    require,
)


# ===========================================================================
# (r) stream-JSON output is machine-parseable and every field is accounted for
# ===========================================================================
@pytest.fixture()
def emitter() -> StreamJsonEmitter:
    return StreamJsonEmitter(run_id="run-1", thread_id="thread-1", model="test-model")


def test_every_frame_is_accounted_for(emitter):
    emitter.run_started(tools=["read_file", "write_file"], started_at=1.0)
    emitter.message_delta(message_id="m1", delta="hel")
    emitter.message_delta(message_id="m1", delta="lo")
    emitter.tool_call(tool_call_id="t1", name="read_file", arguments={"path": "a"})
    emitter.tool_result(
        tool_call_id="t1", name="read_file", ok=True, duration_ms=12.3456, summary="ok"
    )
    emitter.run_finished(status="ok", usage={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})

    assert len(emitter.frames) == 6
    for frame in emitter.frames:
        report = audit_frame(frame)
        assert report == {"unknown_type": [], "missing": [], "unexpected": []}, frame


def test_the_emitter_refuses_an_undeclared_field(emitter):
    with pytest.raises(UnaccountedField):
        emitter._emit("message.delta", {"run_id": "run-1", "message_id": "m", "delta": "x", "surprise": 1})


def test_the_emitter_refuses_a_missing_contract_field(emitter):
    with pytest.raises(UnaccountedField) as excinfo:
        emitter._emit("tool.result", {"run_id": "run-1", "tool_call_id": "t", "name": "n"})
    assert "missing contract fields" in str(excinfo.value)


def test_the_emitter_refuses_an_undeclared_frame_type(emitter):
    with pytest.raises(UnknownFrameField):
        emitter._emit("run.teleported", {"run_id": "run-1"})


def test_the_emitter_refuses_contract_excluded_fields(emitter):
    for field in ("api_key", "token", "secret", "password", "authorization"):
        with pytest.raises(UnaccountedField) as excinfo:
            emitter._emit("tool.call", {"run_id": "r", "tool_call_id": "t", "name": "n", "arguments": {}, field: "x"})
        assert field in str(excinfo.value)


def test_output_is_ndjson_and_round_trips(emitter):
    sink = io.StringIO()
    emitter.sink = sink
    emitter.run_started(tools=["read_file"], started_at=1.0)
    emitter.message_delta(message_id="m1", delta="héllo →")
    emitter.run_finished(status="ok")

    text = sink.getvalue()
    assert text.endswith("\n")
    assert text.count("\n") == 3
    frames = list(parse_ndjson(text))
    assert [f["type"] for f in frames] == ["run.started", "message.delta", "run.finished"]
    assert frames[1]["delta"] == "héllo →"
    assert all(f["v"] == SCHEMA_VERSION for f in frames)
    assert all(f["seq"] == i + 1 for i, f in enumerate(frames))
    assert not audit_stream(text)[0]["missing"]


def test_a_malformed_line_is_an_error_not_a_skipped_frame():
    with pytest.raises(ValueError) as excinfo:
        list(parse_ndjson('{"type":"error"}\nnot json\n'))
    assert "line 2" in str(excinfo.value)
    with pytest.raises(ValueError):
        list(parse_ndjson("[1,2,3]\n"))


def test_seq_is_declared_on_every_frame_type():
    """``seq`` is emitted on every frame, so it belongs in every contract."""
    for frame_type, fields in TERMINAL_FIELDS.items():
        assert "seq" in fields, frame_type
        assert ENVELOPE_FIELDS <= fields, frame_type


def test_audit_reports_the_exact_missing_and_unexpected_keys():
    report = audit_frame({"v": 1, "type": "error", "run_id": "r", "code": "c", "message": "m", "bogus": 1})
    assert report["unexpected"] == ["bogus"]
    assert "correlation_id" in report["missing"]
    assert audit_frame({"type": "nope"})["unknown_type"] == ["nope"]


def test_error_and_finished_frames_are_terminal_and_distinct(emitter):
    emitter.error(code="llm_error", message="boom", correlation_id="c-1")
    emitter.run_finished(status="error", stop_reason="llm_error")
    types = [f["type"] for f in emitter.frames]
    assert types == ["error", "run.finished"]
    assert emitter.frames[0]["correlation_id"] == "c-1"
    assert emitter.frames[1]["stop_reason"] == "llm_error"


def test_usage_is_always_present_on_a_finished_frame(emitter):
    frame = emitter.run_finished(status="ok")
    assert set(frame["usage"]) == {"input_tokens", "output_tokens", "total_tokens"}


# ===========================================================================
# the wire contract registry
# ===========================================================================
def test_registry_bootstrap_is_idempotent():
    first = registered()
    second = bootstrap()
    assert {c.name for c in first} == {c.name for c in second}
    assert len(second) == len(set(c.name for c in second))


def test_generated_typescript_is_current():
    """A field added to a model without regenerating is a failing test."""
    typescript, manifest = build()
    assert DEFAULT_TS_PATH.exists(), f"{DEFAULT_TS_PATH} has not been generated"
    assert DEFAULT_TS_PATH.read_text(encoding="utf-8") == typescript, (
        "the generated TypeScript is stale; run "
        "`python -m alpha.wire_contracts.generate --write`"
    )
    assert DEFAULT_MANIFEST_PATH.exists()
    assert DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8") == manifest


def test_every_contract_field_appears_in_the_generated_typescript():
    typescript = render_typescript(registered())
    for contract in registered():
        assert f"export interface {contract.name}" in typescript, contract.name
        for field in contract_fields(contract):
            assert f'"{field}"' in typescript or f" {field}?:" in typescript or f" {field}:" in typescript, (
                contract.name,
                field,
            )


def test_required_versus_optional_is_preserved_in_typescript():
    typescript = render_typescript(registered())
    # ``label`` is optional on SecretHandle; ``handle`` is required.
    assert "label?:" in typescript
    assert "handle:" in typescript
    for contract in registered():
        required = required_fields(contract)
        assert required, contract.name
        assert not required & {"label"}


def test_no_contract_carries_a_field_a_consumer_could_mistake_for_a_secret():
    for contract in registered():
        for field in contract_fields(contract):
            lowered = field.lower()
            assert not any(
                token in lowered for token in ("secret", "password", "token", "api_key", "credential")
            ), f"{contract.name}.{field}"


def test_secret_handle_contract_exactly_matches_the_python_shape():
    from alpha.security.vault import SecretHandle

    contract = require("SecretHandle")
    handle = SecretHandle(
        operation="http_request", target="https://x", owner="u-1", expires_at=1.0
    )
    assert contract_fields(contract) == set(handle.to_dict().keys())


def test_manifest_is_machine_readable_and_complete():
    manifest = render_manifest(registered())
    assert manifest["generator_version"] >= 1
    assert set(manifest["contracts"]) == {c.name for c in registered()}
    for name, entry in manifest["contracts"].items():
        assert entry["fields"], name
        assert set(entry["required"]) <= set(entry["fields"]), name
    json.dumps(manifest)


def test_registring_a_contract_twice_replaces_it():
    from alpha.wire_contracts.registry import register

    before = len(registered())
    contract = require("SecretHandle")
    register(contract)
    assert len(registered()) == before


# ===========================================================================
# (s) a surface switch does not change the system prompt or the toolset
# ===========================================================================
def test_system_prompt_is_byte_stable_across_repeated_assembly():
    from alpha.agents.lead_agent.prompt import apply_prompt_template

    first = apply_prompt_template({})
    second = apply_prompt_template({})
    assert first == second
    assert isinstance(first, str) and len(first) > 1000


def test_system_prompt_template_has_no_unstable_component():
    """No cwd, clock or thread id may appear in the prompt itself.

    A surface switch that rebuilt the prompt from a timestamp or a working
    directory would bust the provider's prompt cache on every switch.
    """
    from alpha.agents.lead_agent import prompt as prompt_module

    template = prompt_module.SYSTEM_PROMPT_TEMPLATE
    for forbidden in ("{cwd}", "{today}", "{now}", "{timestamp}", "{thread_id}", "{date}"):
        assert forbidden not in template, forbidden
    assert "os.getcwd" not in template
    assert "datetime.now" not in template
    assert "time.time" not in template


def test_tool_assembly_is_stable_for_the_same_inputs():
    """The same assembly inputs must produce the same tool name set."""
    from alpha.tools.tools import get_available_tools

    first = [t.name for t in get_available_tools()]
    second = [t.name for t in get_available_tools()]
    assert first == second
    assert len(first) > 100, len(first)


def test_a_surface_switch_does_not_rebuild_the_agent():
    """``_agent_config_key`` is the cache key; a surface switch must not alter it."""
    import inspect

    from alpha.client import AgentWorkspaceClient

    source = inspect.getsource(AgentWorkspaceClient._ensure_agent)
    key_fields = [
        token
        for token in (
            "model_name",
            "thinking_enabled",
            "is_plan_mode",
            "subagent_enabled",
            "agent_name",
            "effective_user_id",
        )
    ]
    for field in key_fields:
        assert field in source, field
    for surface_only in ("surface", "is_tui", "display_mode", "cli_mode"):
        assert surface_only not in source, surface_only
