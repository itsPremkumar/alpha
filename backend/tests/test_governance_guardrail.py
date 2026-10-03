"""Tests for the governance-backed guardrail provider.

Covers the enforcement half of tool governance: the operator's
per-tool policy is enforced at call time through the existing
GuardrailMiddleware seam, with the deliberately narrow authority
model — operator configuration is the only classification
authority at call time, a tool cannot widen itself, ask is
fail-closed without a confirmation authority, and a malformed
policy is a construction-time error, never a mid-run surprise.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from alpha.guardrails.governance import GovernanceGuardrailProvider
from alpha.guardrails.middleware import GuardrailMiddleware
from alpha.guardrails.provider import GuardrailRequest
from alpha.tools.governance import GovernanceError


class _FakeRuntime:
    def __init__(self, context: dict | None = None) -> None:
        # ``context or {}`` would replace an empty dict with a fresh one
        # and silently detach the middleware's writes from the caller.
        self.context = {} if context is None else context


def _make_tool_call_request(
    name: str = "bash",
    args: dict | None = None,
    call_id: str = "call_1",
    *,
    context: dict | None = None,
) -> MagicMock:
    req = MagicMock()
    req.tool_call = {"name": name, "args": args or {}, "id": call_id}
    req.runtime = _FakeRuntime(context)
    return req


def _request(name: str = "bash", *, is_internal: bool = False) -> GuardrailRequest:
    return GuardrailRequest(
        tool_name=name,
        tool_input={},
        is_internal=is_internal,
    )


# ---------------------------------------------------------------- decisions --


def test_unclassified_tool_is_allowed() -> None:
    provider = GovernanceGuardrailProvider()
    decision = provider.evaluate(_request("web_search"))
    assert decision.allow is True
    assert decision.metadata["governance"] == "unclassified"


def test_empty_registry_reports_unclassified() -> None:
    provider = GovernanceGuardrailProvider(tool_governance={})
    decision = provider.evaluate(_request("bash"))
    assert decision.allow is True
    assert decision.metadata["governance"] == "unclassified"


def test_block_entry_denies_with_governance_code() -> None:
    provider = GovernanceGuardrailProvider(
        tool_governance={
            "rm_tool": {
                "risk_class": "destructive",
                "reversibility": "irreversible",
                "confirmation": "block",
            }
        }
    )
    decision = provider.evaluate(_request("rm_tool"))
    assert decision.allow is False
    assert decision.policy_id == "alpha.tool-governance"
    assert decision.reasons[0].code == "governance.blocked"
    assert "destructive" in decision.reasons[0].message
    assert decision.metadata["risk_class"] == "destructive"
    assert decision.metadata["reversibility"] == "irreversible"
    assert decision.metadata["confirmation"] == "block"


def test_ask_entry_denies_without_internal_approval() -> None:
    provider = GovernanceGuardrailProvider(tool_governance={"bash": {"risk_class": "execute", "confirmation": "ask"}})
    decision = provider.evaluate(_request("bash", is_internal=False))
    assert decision.allow is False
    assert decision.reasons[0].code == "governance.requires_confirmation"


def test_ask_entry_allows_internal_dispatch() -> None:
    provider = GovernanceGuardrailProvider(tool_governance={"bash": {"risk_class": "execute", "confirmation": "ask"}})
    decision = provider.evaluate(_request("bash", is_internal=True))
    assert decision.allow is True
    assert decision.metadata["approved_by"] == "internal_dispatch"


def test_ask_allows_everyone_when_operator_opts_out_of_strict_rule() -> None:
    provider = GovernanceGuardrailProvider(
        tool_governance={"bash": {"risk_class": "execute", "confirmation": "ask"}},
        ask_requires_internal=False,
    )
    assert provider.evaluate(_request("bash", is_internal=False)).allow is True


def test_auto_entry_allows_and_records_the_governed_class() -> None:
    provider = GovernanceGuardrailProvider(
        tool_governance={
            "web_fetch": {
                "risk_class": "external",
                "side_effects": ["http_request"],
                "confirmation": "auto",
            }
        }
    )
    decision = provider.evaluate(_request("web_fetch"))
    assert decision.allow is True
    assert decision.metadata["governance"] == "classified"
    assert decision.metadata["risk_class"] == "external"
    assert decision.metadata["confirmation"] == "auto"


def test_aevaluate_matches_evaluate() -> None:
    provider = GovernanceGuardrailProvider(tool_governance={"bash": {"risk_class": "execute", "confirmation": "ask"}})
    request = _request("bash")
    sync_decision = provider.evaluate(request)
    async_decision = asyncio.run(provider.aevaluate(request))
    assert sync_decision.allow == async_decision.allow
    assert sync_decision.reasons[0].code == async_decision.reasons[0].code


def test_spec_overrides_the_top_level_section_per_tool() -> None:
    provider = GovernanceGuardrailProvider(
        tool_governance={"bash": {"risk_class": "execute", "confirmation": "auto"}},
        spec={"bash": {"risk_class": "execute", "confirmation": "block"}},
    )
    assert provider.evaluate(_request("bash")).allow is False


def test_provider_accepts_the_framework_hint() -> None:
    provider = GovernanceGuardrailProvider(framework="alpha")
    assert provider.evaluate(_request("read_file")).allow is True


def test_malformed_policy_fails_at_construction() -> None:
    with pytest.raises(GovernanceError):
        GovernanceGuardrailProvider(tool_governance={"bash": {"risk_class": "nope", "confirmation": "ask"}})


def test_release_policy_parameters_declares_the_governed_surface() -> None:
    provider = GovernanceGuardrailProvider(
        tool_governance={
            "bash": {"risk_class": "execute", "confirmation": "ask"},
            "rm_tool": {"risk_class": "destructive", "confirmation": "block"},
        }
    )
    parameters = provider.release_policy_parameters()
    assert parameters["ask_requires_internal"] is True
    governed = parameters["governed_tools"]
    assert set(governed) == {"bash", "rm_tool"}
    assert governed["bash"] == {"risk_class": "execute", "confirmation": "ask"}
    assert governed["rm_tool"] == {"risk_class": "destructive", "confirmation": "block"}


# ------------------------------------------------------------ middleware path --


def _handler(request: MagicMock) -> str:
    return "executed"


def test_middleware_denies_a_blocked_call_with_an_error_tool_message() -> None:
    provider = GovernanceGuardrailProvider(tool_governance={"bash": {"risk_class": "execute", "confirmation": "block"}})
    middleware = GuardrailMiddleware(provider, fail_closed=True)
    request = _make_tool_call_request("bash", {"command": "ls"})
    result = middleware.wrap_tool_call(request, _handler)
    assert result.status == "error"
    assert "governance.blocked" in result.content
    assert "bash" in result.content


def test_middleware_allows_an_unclassified_call_through_to_the_handler() -> None:
    provider = GovernanceGuardrailProvider()
    middleware = GuardrailMiddleware(provider, fail_closed=True)
    request = _make_tool_call_request("read_file", {"path": "a.txt"})
    assert middleware.wrap_tool_call(request, _handler) == "executed"


def test_middleware_denies_ask_calls_from_a_model_run() -> None:
    provider = GovernanceGuardrailProvider(tool_governance={"bash": {"risk_class": "execute", "confirmation": "ask"}})
    middleware = GuardrailMiddleware(provider, fail_closed=True)
    # A model-initiated call carries no internal flag in its context.
    request = _make_tool_call_request("bash", {"command": "ls"}, context={})
    result = middleware.wrap_tool_call(request, _handler)
    assert result.status == "error"
    assert "governance.requires_confirmation" in result.content


def test_middleware_async_path_matches() -> None:
    provider = GovernanceGuardrailProvider(tool_governance={"bash": {"risk_class": "execute", "confirmation": "block"}})
    middleware = GuardrailMiddleware(provider, fail_closed=True)
    request = _make_tool_call_request("bash", {"command": "ls"})

    async def handler(req: MagicMock) -> str:
        return "executed"

    async def run() -> Any:
        return await middleware.awrap_tool_call(request, handler)

    result = asyncio.run(run())
    assert result.status == "error"
    assert "governance.blocked" in result.content


def test_middleware_publishes_the_authorization_outcome() -> None:
    from alpha.authz.outcome import (
        AUTHORIZATION_OUTCOME_CONTEXT_KEY,
        AuthorizationOutcome,
    )

    provider = GovernanceGuardrailProvider(tool_governance={"bash": {"risk_class": "execute", "confirmation": "block"}})
    middleware = GuardrailMiddleware(provider, fail_closed=True)
    context: dict[str, Any] = {}
    request = _make_tool_call_request("bash", context=context)
    middleware.wrap_tool_call(request, _handler)
    outcome = context.get(AUTHORIZATION_OUTCOME_CONTEXT_KEY, {}).get(request.tool_call["id"])
    assert isinstance(outcome, AuthorizationOutcome)
    assert outcome.decision == "denied"
    assert "governance.blocked" in outcome.reason_codes


def test_middleware_records_the_guardrail_journal_event() -> None:
    class _Journal:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def record_middleware(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    provider = GovernanceGuardrailProvider(tool_governance={"bash": {"risk_class": "execute", "confirmation": "block"}})
    middleware = GuardrailMiddleware(provider, fail_closed=True)
    journal = _Journal()
    context: dict[str, Any] = {"__run_journal": journal}
    request = _make_tool_call_request("bash", context=context)
    middleware.wrap_tool_call(request, _handler)
    assert journal.calls, "a denied governance call must be audited"
    assert journal.calls[0]["action"] == "deny_tool_call"
    assert journal.calls[0]["changes"]["allow"] is False
    assert "governance.blocked" in journal.calls[0]["changes"]["reason_codes"]


def test_middleware_allows_internal_dispatch_for_ask_tools() -> None:
    provider = GovernanceGuardrailProvider(tool_governance={"bash": {"risk_class": "execute", "confirmation": "ask"}})
    middleware = GuardrailMiddleware(provider, fail_closed=True)
    context: dict[str, Any] = {"is_internal": True}
    request = _make_tool_call_request("bash", context=context)
    assert middleware.wrap_tool_call(request, _handler) == "executed"


# ------------------------------------------------------------ assembly path --


def _assembly_config(tool_governance: dict[str, Any] | None) -> Any:
    from alpha.config.app_config import AppConfig
    from alpha.config.guardrails_config import GuardrailProviderConfig, GuardrailsConfig
    from alpha.config.sandbox_config import SandboxConfig

    return AppConfig(
        models=[],
        sandbox=SandboxConfig(use="test"),
        guardrails=GuardrailsConfig(
            enabled=True,
            provider=GuardrailProviderConfig(
                use="alpha.guardrails.governance:GovernanceGuardrailProvider",
            ),
        ),
        tool_governance=tool_governance,
    )


def test_assembly_hands_the_top_level_section_to_the_provider() -> None:
    from alpha.agents.middlewares.tool_error_handling_middleware import (
        build_lead_runtime_middlewares,
    )
    from alpha.guardrails.middleware import GuardrailMiddleware

    config = _assembly_config({"bash": {"risk_class": "execute", "confirmation": "block"}})
    middlewares = build_lead_runtime_middlewares(app_config=config)
    guardrails = [m for m in middlewares if isinstance(m, GuardrailMiddleware)]
    assert len(guardrails) == 1
    provider = guardrails[0].provider
    assert isinstance(provider, GovernanceGuardrailProvider)
    # The operator section is live: bash is refused at call time.
    decision = provider.evaluate(_request("bash"))
    assert decision.allow is False
    assert decision.reasons[0].code == "governance.blocked"
    # Everything the operator did not classify still passes.
    assert provider.evaluate(_request("read_file")).allow is True


def test_malformed_top_level_section_fails_agent_assembly() -> None:
    from alpha.agents.middlewares.tool_error_handling_middleware import (
        build_lead_runtime_middlewares,
    )

    config = _assembly_config({"bash": {"risk_class": "nope", "confirmation": "ask"}})
    with pytest.raises(GovernanceError):
        build_lead_runtime_middlewares(app_config=config)
