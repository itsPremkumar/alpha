"""Unit tests for the sandboxed CapabilityContext ($) and capability namespaces."""

import pytest

from alpha.mods.context import CapabilityContext
from alpha.mods.kernel import ModKernel
from alpha.mods.types import CorrelationContext


@pytest.fixture
def kernel():
    return ModKernel()


def test_capability_permission_enforcement(kernel):
    # Context with no granted capabilities
    empty_ctx = CapabilityContext("unprivileged", set(), kernel)

    with pytest.raises(PermissionError) as exc_tools:
        _ = empty_ctx.tools
    assert "tools:read" in str(exc_tools.value)

    with pytest.raises(PermissionError) as exc_models:
        _ = empty_ctx.models
    assert "models:complete" in str(exc_models.value)

    with pytest.raises(PermissionError) as exc_evidence:
        _ = empty_ctx.evidence
    assert "evidence:record" in str(exc_evidence.value)

    with pytest.raises(PermissionError) as exc_estop:
        _ = empty_ctx.estop
    assert "estop:control" in str(exc_estop.value)

    with pytest.raises(PermissionError) as exc_clock:
        _ = empty_ctx.clock
    assert "clock:schedule" in str(exc_clock.value)

    with pytest.raises(PermissionError) as exc_storage:
        _ = empty_ctx.storage
    assert "storage:write" in str(exc_storage.value)

    with pytest.raises(PermissionError) as exc_ui:
        _ = empty_ctx.ui
    assert "ui:render" in str(exc_ui.value)


def test_estop_state_read_failure_fails_closed(monkeypatch, kernel):
    from alpha.mods.context import EstopCapability

    def unavailable(*args, **kwargs):
        raise OSError("control store unavailable")

    monkeypatch.setattr("alpha.runtime.estop.get_estop_manager", unavailable)
    capability = EstopCapability(kernel, "test")

    assert capability.is_engaged() is True
    assert capability.status()["is_engaged"] is True


def test_granted_capabilities_access(kernel):
    all_caps = {
        "tools:read",
        "models:complete",
        "evidence:record",
        "estop:control",
        "clock:schedule",
        "storage:write",
        "ui:render",
    }
    ctx = CapabilityContext("privileged", all_caps, kernel)

    assert ctx.tools is not None
    assert ctx.models is not None
    assert ctx.evidence is not None
    assert ctx.estop is not None
    assert ctx.clock is not None
    assert ctx.storage is not None
    assert ctx.ui is not None


@pytest.mark.asyncio
async def test_tool_capability_dispatch(kernel):
    async def sample_tool(path: str):
        return f"content of {path}"

    kernel.register_tool_executor("read_file", sample_tool)

    ctx = CapabilityContext("mod", {"tools:read", "tools:execute"}, kernel)
    res = await ctx.tools.dispatch("read_file", {"path": "test.txt"})
    assert res == "content of test.txt"
    assert "read_file" in ctx.tools.list_tools()


@pytest.mark.asyncio
async def test_tool_capability_refuses_unregistered_runtime_tool(kernel):
    ctx = CapabilityContext("mod", {"tools:read", "tools:execute"}, kernel)

    with pytest.raises(KeyError, match="no explicitly registered mod executor"):
        await ctx.tools.dispatch("read_file", {"path": "test.txt"})


@pytest.mark.asyncio
async def test_tool_capability_requires_execute_grant(kernel):
    kernel.register_tool_executor("read_file", lambda **args: "ok")
    ctx = CapabilityContext("mod", {"tools:read"}, kernel)

    with pytest.raises(PermissionError, match="tools:execute"):
        await ctx.tools.dispatch("read_file", {"path": "test.txt"})


@pytest.mark.asyncio
async def test_model_capability_sidecar_triage(kernel):
    async def mock_llm(prompt, **kwargs):
        if "unmatched" in prompt.lower():
            return "I cannot determine a category"
        if "classify" in prompt.lower():
            return "security"
        return "sidecar response"

    kernel.set_mock_model_provider(mock_llm)

    ctx = CapabilityContext("mod", {"models:complete"}, kernel)
    resp = await ctx.models.complete("Check intent")
    assert resp == "sidecar response"

    cat = await ctx.models.classify("Analyze security risk", ["perf", "security", "style"])
    assert cat == "security"
    assert await ctx.models.classify("unmatched text", ["perf", "security"]) == ""


@pytest.mark.asyncio
async def test_model_capability_uses_configured_model_and_token_cap(monkeypatch, kernel):
    from types import SimpleNamespace

    calls = {}

    class FakeModel:
        async def ainvoke(self, prompt):
            calls["prompt"] = prompt
            return SimpleNamespace(content="real sidecar result")

    app_config = SimpleNamespace(
        default_model_name="default-model",
        models=[],
        get_model_config=lambda name: object() if name == "sidecar-model" else None,
    )

    def fake_create_chat_model(**kwargs):
        calls["model_kwargs"] = kwargs
        return FakeModel()

    monkeypatch.setattr("alpha.config.app_config.get_app_config", lambda: app_config)
    monkeypatch.setattr("alpha.models.create_chat_model", fake_create_chat_model)

    ctx = CapabilityContext("mod", {"models:complete"}, kernel)
    result = await ctx.models.complete("Classify this", model_profile="sidecar-model", max_tokens=9000)

    assert result == "real sidecar result"
    assert calls["prompt"] == "Classify this"
    assert calls["model_kwargs"]["name"] == "sidecar-model"
    assert calls["model_kwargs"]["model_overrides"] == {"max_tokens": 8192}
    assert calls["model_kwargs"]["attach_tracing"] is False
    assert await ctx.models.classify("unmatched text", ["perf", "security"]) == ""


def test_evidence_capability_ledger(kernel):
    ctx = CapabilityContext("mod", {"evidence:record"}, kernel)

    corr = CorrelationContext.create(run_id="run_101", task_id="task_202")
    rcpt_id = ctx.evidence.record(
        {"exit_code": 0, "test_count": 42, "summary": "pytest passed"},
        correlation=corr,
    )
    assert rcpt_id is not None

    entry = ctx.evidence.get(rcpt_id)
    assert entry is not None
    assert entry["exit_code"] == 0
    assert entry["test_count"] == 42

    corr_matches = ctx.evidence.get_by_correlation(corr)
    assert len(corr_matches) == 1
    assert corr_matches[0]["id"] == rcpt_id


def test_storage_capability_key_value(kernel):
    ctx = CapabilityContext("mod", {"storage:write"}, kernel)

    assert ctx.storage.get("k1") is None
    ctx.storage.set("k1", {"foo": "bar"})
    assert ctx.storage.get("k1") == {"foo": "bar"}
    assert "k1" in ctx.storage.keys()

    deleted = ctx.storage.delete("k1")
    assert deleted is True
    assert ctx.storage.get("k1") is None


def test_ui_capability_cards(kernel):
    ctx = CapabilityContext("mod", {"ui:render"}, kernel)

    card_id = ctx.ui.render_card(
        {
            "title": "Approval Required",
            "description": "High risk action",
        }
    )
    assert card_id is not None

    cards = ctx.ui.get_rendered_cards()
    assert len(cards) == 1
    assert cards[0]["title"] == "Approval Required"


@pytest.mark.asyncio
async def test_clock_capability_scheduling(kernel):
    ctx = CapabilityContext("mod", {"clock:schedule"}, kernel)

    timer_id = ctx.clock.after(10.0, "deferred.event")
    assert timer_id is not None

    cancelled = ctx.clock.cancel_timer(timer_id)
    assert cancelled is True


def test_dollar_syntax_alias(kernel):
    all_caps = {
        "tools:read",
        "models:complete",
        "evidence:record",
        "estop:control",
        "clock:schedule",
        "storage:write",
        "ui:render",
    }
    ctx = CapabilityContext("mod", all_caps, kernel)
    # The dollar property is the valid-Python alias for `$`, allowing ctx.dollar.tools
    assert ctx.dollar is ctx
    assert ctx.dollar.tools is not None
    assert ctx.dollar.models is not None
    assert ctx.dollar.evidence is not None
    assert ctx.dollar.estop is not None
    assert ctx.dollar.clock is not None
    assert ctx.dollar.storage is not None
    assert ctx.dollar.ui is not None


@pytest.mark.asyncio
async def test_clock_capability_recurring_every(kernel):
    ctx = CapabilityContext("mod", {"clock:schedule"}, kernel)

    timer_id = ctx.clock.every(10.0, "heartbeat.tick", max_iterations=3)
    assert timer_id is not None
    active = ctx.clock.list_timers()
    assert timer_id in active

    cancelled = ctx.clock.cancel_timer(timer_id)
    assert cancelled is True
    assert timer_id not in ctx.clock.list_timers()


def test_storage_and_ui_cleanup(kernel):
    ctx = CapabilityContext("mod", {"storage:write", "ui:render"}, kernel)

    ctx.storage.set("k1", "v1")
    ctx.storage.set("k2", "v2")
    assert len(ctx.storage.keys()) == 2
    ctx.storage.clear()
    assert len(ctx.storage.keys()) == 0

    ctx.ui.render_card({"title": "Test Card"})
    assert len(ctx.ui.get_rendered_cards()) == 1
    ctx.ui.clear_cards()
    assert len(ctx.ui.get_rendered_cards()) == 0
