"""Autonomy truth, recovery, and activity contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.prebuilt import ToolRuntime

from alpha.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
from alpha.ops.autonomy_truth import (
    KNOWN_NON_CAPABILITIES,
    ActivityDigest,
    assess_autonomy,
    assess_capability,
    build_activity_digest,
    classify_failure,
)
from alpha.ops.recovery_brief import build_recovery_brief
from alpha.tools.builtins.autonomy_control_tool import autonomy_control
from alpha.tools.tools import BUILTIN_TOOLS


def _tool_runtime(context: dict | None = None) -> ToolRuntime:
    return ToolRuntime(
        state={},
        context=context or {},
        config={},
        stream_writer=lambda _: None,
        tool_call_id="autonomy-control-test",
        store=None,
    )


def _server_config():
    return SimpleNamespace(
        models=[SimpleNamespace(name="test-model", supports_vision=True)],
        get_model_config=lambda name: SimpleNamespace(supports_vision=True) if name == "test-model" else None,
        sandbox=SimpleNamespace(
            use="alpha.sandbox.local:LocalSandboxProvider",
            network=SimpleNamespace(mode="allowlist"),
        ),
        extensions=SimpleNamespace(get_enabled_mcp_servers=lambda: {"docs": object()}),
        skills=SimpleNamespace(get_skills_path=lambda: Path("skills")),
    )


def test_unknown_context_never_claims_autonomous_readiness() -> None:
    result = assess_autonomy({})

    assert result["status"] in {"unknown", "degraded"}
    assert result["ready"] is False
    assert result["capabilities"]
    assert result["next_actions"]


def test_capability_decisions_distinguish_available_degraded_and_blocked() -> None:
    context = {
        "current_model_observed": True,
        "tool_count": 12,
        "sandbox_provider": "local",
        "model_supports_vision": True,
        "network_policy": "allowlist",
        "enabled_mcp_servers": ["local-docs"],
        "public_skill_count": 4,
    }
    result = assess_autonomy(context)

    assert result["status"] == "ready"
    assert result["ready"] is True
    by_name = {item["capability"]: item for item in result["capabilities"]}
    assert by_name["sandbox_execution"]["status"] == "available"
    assert by_name["vision"]["status"] == "available"
    assert by_name["mcp_connectors"]["status"] == "available"

    approval = assess_capability("external_side_effect", {"approval_granted": True})
    assert approval.status == "degraded"
    assert approval.allowed is False
    assert approval.requires_approval is True

    unknown = assess_capability("teleport_the_computer", {})
    assert unknown.status == "unavailable"
    assert unknown.allowed is False
    assert "not a registered capability" in unknown.reason


def test_optional_capability_limits_do_not_block_the_core_offline_loop() -> None:
    result = assess_autonomy(
        {
            "current_model_observed": True,
            "tool_count": 1,
            "sandbox_provider": "none",
            "model_supports_vision": False,
            "network_policy": "isolated",
            "enabled_mcp_servers": [],
            "public_skill_count": 0,
        }
    )

    assert result["status"] == "ready"
    assert result["ready"] is True
    assert set(result["optional_limitations"]) == {
        "sandbox_execution",
        "vision",
        "network",
        "mcp_connectors",
        "public_skills",
    }


def test_known_non_capabilities_are_explicit_and_machine_readable() -> None:
    assert "unbounded_autonomy" in KNOWN_NON_CAPABILITIES
    assert "credential_inference" in KNOWN_NON_CAPABILITIES
    result = assess_capability("unbounded_autonomy", {})
    assert result.status == "unavailable"
    assert result.requires_approval is False
    assert result.next_action


@pytest.mark.parametrize(
    ("error", "category", "retryable"),
    [
        ("Permission denied by the sandbox", "permission", False),
        ("The container runtime is unavailable", "sandbox", True),
        ("ModuleNotFoundError: missing package", "dependency", False),
        ("401 invalid API key from provider", "provider", False),
        ("HTTP 429 rate limit", "provider", True),
        ("DNS name resolution timed out", "network", True),
        ("not enough memory to start worker", "resource", False),
        ("invalid user input", "input", False),
        ("an unexpected unknown failure", "unknown", False),
    ],
)
def test_failure_taxonomy_has_actionable_recovery(error: str, category: str, retryable: bool) -> None:
    result = classify_failure(error)

    assert result.category == category
    assert result.retryable is retryable
    assert result.user_message
    assert result.next_steps
    assert result.safe_summary
    assert "sk-" not in result.safe_summary


# These fixtures deliberately ARE credential-shaped -- proving that they get
# redacted is the whole point of test_failure_summary_redacts_common_secrets.
# GitHub push protection scans every push for contiguous credential strings,
# so assembling them from fragments keeps each runtime value byte-identical
# (the assertion below still sees the complete secret) while the source never
# contains a complete token. This keeps the repo pushable with no allowlist
# exception and protection fully enabled. Do NOT rejoin these into literals.
_GHP_CLASSIC_PAT = "ghp_" + "abcdefghijklmnopqrstuvwxyz" + "123456"
_GITHUB_FINE_GRAINED_PAT = "github_pat_" + "abcdefghijklmnopqrstuvwxyz" + "1234567890"
_AWS_ACCESS_KEY_ID = "AKIA" + "IOSFODNN7EXAMPLE"
_JWT_SAMPLE = "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTYifQ" + "." + "signature123"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-live-1234567890",
        _GHP_CLASSIC_PAT,
        _GITHUB_FINE_GRAINED_PAT,
        _AWS_ACCESS_KEY_ID,
        _JWT_SAMPLE,
        "https://alice:password@example.com/path",
    ],
)
def test_failure_summary_redacts_common_secrets(secret: str) -> None:
    result = classify_failure(f"Authorization Bearer {secret} failed: " + "x" * 10_000)

    assert secret not in result.safe_summary
    assert len(result.safe_summary) <= 500
    assert result.redacted is True


def test_activity_digest_summarizes_existing_events_without_echoing_secrets() -> None:
    events = [
        {"type": "tool_call", "tool": "read_file", "status": "success", "tool_input": {"path": "src/a.py"}},
        {"type": "tool_call", "tool": "write_file", "status": "success", "tool_input": {"path": "src/a.py"}},
        {"type": "tool_call", "tool": "bash", "status": "failure", "error": "pytest failed"},
        {"type": "approval_requested", "tool": "git:push", "status": "pending"},
        {"type": "usage", "input_tokens": 1200, "output_tokens": 300, "cost_usd": 0.012},
    ]
    digest = build_activity_digest(events)

    assert isinstance(digest, ActivityDigest)
    assert digest.step_count == 5
    assert digest.success_count == 2
    assert digest.failure_count == 1
    assert digest.approval_count == 1
    assert digest.edited_file_count == 1
    assert digest.input_tokens == 1200
    assert digest.output_tokens == 300
    assert digest.cost_usd == pytest.approx(0.012)
    assert "5 recorded steps" in digest.summary
    serialized = json.dumps(digest.to_dict())
    assert "pytest" not in serialized
    assert "src/a.py" not in serialized


def test_recovery_brief_is_bounded_and_does_not_echo_raw_history() -> None:
    state = {
        "task_notes": {
            "next": {
                "content": "Resume with token sk-live-1234567890 after approval",
                "source_ids": [],
                "authority": "model_report",
            }
        },
        "task_history": {
            "scope": "a" * 64,
            "batches": ["b" * 64],
            "omitted_records": 2,
            "status": "available",
        },
        "messages": [
            {"type": "tool", "name": "bash", "status": "error", "content": "raw output secret"},
            {"type": "ai", "tool_calls": [{"name": "read_file", "args": {"path": "/private/file"}}]},
        ],
    }

    brief = build_recovery_brief(state, max_chars=700)
    rendered = json.dumps(brief)

    assert brief["status"] == "available"
    assert brief["counts"] == {"notes": 1, "events": 2, "failures": 1, "approval_events": 0}
    assert "sk-live" not in rendered
    assert "raw output" not in rendered
    assert "/private/file" not in rendered
    assert brief["contains_raw_messages"] is False
    assert len(rendered) <= 900


def test_activity_digest_is_bounded_and_ignores_malformed_records() -> None:
    events = [{"type": "tool_call", "tool": "bash", "status": "success"} for _ in range(5000)]
    digest = build_activity_digest(events, max_events=100, max_chars=500)

    assert digest.step_count == 100
    assert len(digest.summary) <= 500
    assert build_activity_digest([None, "bad", {}, {"type": "tool_call"}]).step_count == 1


def test_tool_error_middleware_attaches_recovery_metadata_without_changing_visible_contract() -> None:
    middleware = ToolErrorHandlingMiddleware()
    request = SimpleNamespace(tool_call={"name": "web_search", "id": "tc-1"})

    message = middleware._build_error_message(request, ConnectionError("network down"))

    assert message.additional_kwargs["agent_workspace_autonomy_recovery"]["category"] == "network"
    assert message.additional_kwargs["agent_workspace_autonomy_recovery"]["retryable"] is True
    assert "Tool 'web_search' failed" in message.text
    assert "Continue with available context" in message.text


def test_autonomy_control_tool_is_registered_and_uses_server_runtime(monkeypatch) -> None:
    assert autonomy_control in BUILTIN_TOOLS
    monkeypatch.setattr(
        "alpha.ops.runtime_readiness.get_app_config",
        _server_config,
    )
    runtime = _tool_runtime({"model_name": "test-model"})

    readiness = autonomy_control.invoke({"runtime": runtime, "action": "readiness"})
    assert readiness["success"] is True
    assert readiness["action"] == "readiness"
    assert readiness["free_and_offline"] is True
    assert readiness["evidence_source"] == "server_runtime"
    assert readiness["ready"] is True

    failure = autonomy_control.invoke({"runtime": runtime, "action": "failure", "error": "permission denied"})
    assert failure["category"] == "permission"

    recovery = autonomy_control.invoke({"runtime": runtime, "action": "recovery"})
    assert recovery["success"] is True
    assert recovery["recovery"]["contains_raw_messages"] is False

    activity = autonomy_control.invoke(
        {
            "runtime": runtime,
            "action": "activity",
            "events_json": json.dumps([{"type": "tool_call", "tool": "bash", "status": "success"}]),
        }
    )
    assert activity["digest"]["step_count"] == 1

    limits = autonomy_control.invoke({"runtime": runtime, "action": "known_limits"})
    assert "unbounded_autonomy" in limits["non_capabilities"]


def test_autonomy_control_schema_does_not_accept_model_authored_readiness_claims() -> None:
    properties = autonomy_control.tool_call_schema.model_json_schema()["properties"]

    assert "runtime" not in properties
    assert "context_json" not in properties
    assert "approval_granted" not in properties


def test_autonomy_control_rejects_unbounded_or_invalid_json() -> None:
    runtime = _tool_runtime()
    result = autonomy_control.invoke({"runtime": runtime, "action": "activity", "events_json": "not-json"})
    assert result["success"] is False
    assert result["error"] == "invalid_json"

    oversized = autonomy_control.invoke({"runtime": runtime, "action": "activity", "events_json": "x" * 300_000})
    assert oversized["success"] is False
    assert oversized["error"] == "input_too_large"
