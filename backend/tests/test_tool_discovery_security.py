"""Security invariants for discovery telemetry.

Two things must hold at once: the telemetry must never launder a denial into a
success, and adding telemetry must not weaken enforcement. Both are pinned here
against a real ``SkillToolPolicyMiddleware`` and a real
``DeferredToolPromotionAuditMiddleware``, never a stub.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool as as_tool
from pydantic import Field

from alpha.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware
from alpha.agents.middlewares.skill_tool_policy_middleware import SkillToolPolicyMiddleware
from alpha.agents.middlewares.tool_promotion_audit_middleware import DeferredToolPromotionAuditMiddleware
from alpha.agents.thread_state import ThreadState
from alpha.runtime.secret_context import SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY, write_slash_skill_source_path
from alpha.skills.types import Skill, SkillCategory
from alpha.tools import tool_discovery_metrics as metrics
from alpha.tools.builtins.tool_search import build_deferred_tool_setup
from alpha.tools.mcp_metadata import tag_mcp_tool

_SLASH_SOURCE_OWNER_TOKEN = "security-slash-source-owner"
_DENIED_MARKER = "TOP_SECRET_DENIED_PAYLOAD"
_EXECUTED: list[str] = []


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    home = tmp_path / "agent-workspace"
    home.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))
    _EXECUTED.clear()
    return home


@as_tool
def calc(expression: str) -> str:
    """Evaluate an arithmetic expression."""
    return "4"


@as_tool
def denied_lookup(query: str) -> str:
    """Run a lookup the active skill does not authorize."""
    _EXECUTED.append(query)
    return f"denied data {_DENIED_MARKER}"


class _StorageStub:
    def __init__(self, skills: list[Skill]):
        self._skills = skills

    def load_skills(self, *, enabled_only: bool = False) -> list[Skill]:
        return [skill for skill in self._skills if skill.enabled or not enabled_only]

    def get_container_root(self) -> str:
        return "/mnt/skills"


class _FakeModel(GenericFakeChatModel):
    bound_tool_names: list[list[str]] = Field(default_factory=list)

    def __init__(self, responses: list[AIMessage]):
        super().__init__(messages=iter(responses))
        self.bound_tool_names = []

    def bind_tools(self, tools, **kwargs):
        self.bound_tool_names.append([getattr(candidate, "name", "") for candidate in tools])
        return self


def _skill(name: str, allowed_tools: list[str]) -> Skill:
    skill_dir = Path(f"/tmp/skills/public/{name}")
    return Skill(
        name=name,
        description=f"Description for {name}",
        license="MIT",
        skill_dir=skill_dir,
        skill_file=skill_dir / "SKILL.md",
        relative_path=Path(name),
        category=SkillCategory.PUBLIC,
        allowed_tools=tuple(allowed_tools),
        enabled=True,
    )


def _build_graph(allowed_tools: list[str], responses: list[AIMessage], *, with_policy: bool = True, context: dict | None = None):
    setup = build_deferred_tool_setup([tag_mcp_tool(calc), tag_mcp_tool(denied_lookup)], enabled=True)
    run_context: dict = context if context is not None else {}
    middlewares = [DeferredToolPromotionAuditMiddleware(setup.deferred_names, setup.catalog_hash)]
    if with_policy:
        policy = SkillToolPolicyMiddleware(slash_source_owner_token=_SLASH_SOURCE_OWNER_TOKEN)
        policy._storage = lambda: _StorageStub([_skill("restricted", allowed_tools)])
        write_slash_skill_source_path(run_context, "/mnt/skills/public/restricted/SKILL.md", owner_token=_SLASH_SOURCE_OWNER_TOKEN)
        middlewares.append(policy)
    middlewares.append(DeferredToolFilterMiddleware(setup.deferred_names, setup.catalog_hash))
    model = _FakeModel(responses)
    graph = create_agent(model=model, tools=[calc, denied_lookup, setup.tool_search_tool], middleware=middlewares, state_schema=ThreadState)
    return graph, model, run_context


def _search(query: str, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "tool_search", "args": {"query": query}, "id": call_id, "type": "tool_call"}])


def _telemetry(context: dict) -> metrics.DiscoveryTelemetry:
    telemetry = context.get(metrics.TELEMETRY_CONTEXT_KEY)
    assert isinstance(telemetry, metrics.DiscoveryTelemetry)
    return telemetry


def test_denied_schema_never_reaches_the_model_or_the_promotion_metric():
    """The core security claim, end to end, with a real policy middleware."""
    graph, model, context = _build_graph(["calc"], [_search("select:denied_lookup", "s1"), AIMessage(content="done")])

    result = graph.invoke({"messages": [HumanMessage(content="use the denied lookup")]}, context=context)

    # Enforcement: the schema never became model-visible and never executed.
    assert all("denied_lookup" not in names for names in model.bound_tool_names)
    assert _EXECUTED == []
    assert result["promoted"]["names"] == []
    search_message = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "s1"][0]
    assert "denied_lookup" not in search_message.content
    assert _DENIED_MARKER not in search_message.content

    # Telemetry agrees with enforcement rather than with the proposal.
    snapshot = _telemetry(context).snapshot()
    assert snapshot["promotions_verified"] == 0
    assert snapshot["verified_names"] == []
    assert snapshot["outcomes"]["denied"] == 1


def test_denied_tool_execution_still_blocked_with_telemetry_present():
    graph, _model, context = _build_graph(
        ["calc"],
        [
            _search("select:denied_lookup", "s1"),
            AIMessage(content="", tool_calls=[{"name": "denied_lookup", "args": {"query": "secret"}, "id": "c1", "type": "tool_call"}]),
            AIMessage(content="done"),
        ],
    )

    result = graph.invoke({"messages": [HumanMessage(content="use then call the denied lookup")]}, context=context)

    assert _EXECUTED == []
    blocked = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "c1"][0]
    assert blocked.status == "error"
    assert "not allowed by the active skill policy" in blocked.content
    assert _telemetry(context).snapshot()["promotions_verified"] == 0


def test_telemetry_carries_no_secret_or_payload_material():
    """A metric is an aggregate; it must not become an exfiltration channel."""
    graph, _model, context = _build_graph(
        ["calc"],
        [
            _search(f"select:calc credential {_DENIED_MARKER}", "s1"),
            AIMessage(content=""),
        ],
    )
    # A query carrying the marker, plus a denied name, must not leak the marker
    # or any raw schema/query text through the recorded counters.
    graph.invoke({"messages": [HumanMessage(content="go")]}, context=context)

    telemetry = _telemetry(context)
    snapshot = telemetry.snapshot()
    serialized = json.dumps(snapshot, ensure_ascii=False, default=str)
    assert _DENIED_MARKER not in serialized
    assert "credential" not in serialized
    assert "select:" not in serialized
    assert SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY not in serialized
    # snapshot() must be JSON-serializable for an operator-facing surface.
    assert isinstance(snapshot["promotions_verified"], int)


def test_metric_only_ever_agrees_with_the_enforced_state():
    """Whatever the metric records must be a subset of what policy let through."""
    for allowed, proposed, expected in [
        (["calc"], "select:calc,denied_lookup", ["calc"]),
        (["calc"], "select:denied_lookup", []),
        (["calc", "denied_lookup"], "select:calc,denied_lookup", ["calc", "denied_lookup"]),
    ]:
        context: dict = {}
        graph, _model, _ctx = _build_graph(allowed, [_search(proposed, "s1"), AIMessage(content="done")], context=context)
        result = graph.invoke({"messages": [HumanMessage(content="go")]}, context=context)

        enforced = result["promoted"]["names"]
        recorded = _telemetry(context).snapshot()["verified_names"]
        assert enforced == expected
        assert recorded == expected
        assert _telemetry(context).snapshot()["promotions_verified"] == len(expected)


def test_audit_wrapper_and_telemetry_report_the_same_effective_names():
    """The existing outer audit wrapper and the new metric must not disagree."""
    recorder_calls: list[dict] = []

    class _Recorder:
        def record_middleware(self, **kwargs):
            recorder_calls.append(kwargs)

    context: dict = {"__run_journal": _Recorder()}
    graph, _model, _ctx = _build_graph(["calc"], [_search("select:calc,denied_lookup", "s1"), AIMessage(content="done")], context=context)

    graph.invoke({"messages": [HumanMessage(content="go")]}, context=context)

    audited = recorder_calls[0]["changes"]["tool_names"]
    snapshot = _telemetry(context).snapshot()
    assert audited == ["calc"]
    assert snapshot["verified_names"] == audited
    assert snapshot["promotions_verified"] == len(audited)


def test_policy_decision_stays_out_of_the_observable_snapshot():
    graph, _model, context = _build_graph(["calc"], [_search("select:calc", "s1"), AIMessage(content="done")])
    graph.invoke({"messages": [HumanMessage(content="go")]}, context=context)

    telemetry = _telemetry(context)
    serialized = json.dumps(telemetry.snapshot(), ensure_ascii=False, default=str)
    assert "owner_token" not in serialized
    assert "/mnt/skills" not in serialized
    assert telemetry.snapshot()["verified_names"] == ["calc"]


@pytest.mark.parametrize(
    ("context", "expected_reason"),
    [
        (None, "no_runtime_context"),
        ("not-a-dict", "no_runtime_context"),
        (17, "no_runtime_context"),
        ({}, "no_policy_decision"),
        ({"junk": object()}, "no_policy_decision"),
    ],
)
def test_malformed_runtime_context_degrades_to_unverified_instead_of_raising(context, expected_reason):
    """Telemetry must never be the thing that breaks a tool call."""
    verdict = metrics.evaluate_proposed_promotions(["calc"], context, deferred=frozenset({"calc"}))
    assert verdict.outcome == metrics.UNVERIFIED
    assert verdict.reason == expected_reason
    assert verdict.promoted == ()
