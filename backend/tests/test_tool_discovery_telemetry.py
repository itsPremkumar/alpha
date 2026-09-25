"""Discovery telemetry must report only promotions it can prove.

Every test here fails against a metric that counts proposals, denied names, or
unmeasured calls as successes. The suite is hermetic: a per-test
``AGENT_WORKSPACE_HOME``, an injected skill-storage stub, a fake chat model, and
a per-test :class:`UniversalToolCatalog` -- no real BM25 corpus, no network, and
no wall-clock assertions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool as as_tool
from langgraph.types import Command
from pydantic import Field

from alpha.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware
from alpha.agents.middlewares.skill_tool_policy_middleware import SkillToolPolicyMiddleware
from alpha.agents.middlewares.tool_promotion_audit_middleware import DeferredToolPromotionAuditMiddleware
from alpha.agents.thread_state import ThreadState
from alpha.runtime.secret_context import SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY, write_slash_skill_source_path
from alpha.skills.types import Skill, SkillCategory
from alpha.tools import tool_discovery_metrics as metrics
from alpha.tools.builtins.tool_search import DeferredToolCatalog, build_deferred_tool_setup, build_tool_search_tool
from alpha.tools.builtins.tool_search_tool import catalog_tool_call
from alpha.tools.mcp_metadata import tag_mcp_tool
from alpha.tools.search.catalog import UniversalToolCatalog

_SLASH_SOURCE_OWNER_TOKEN = "telemetry-slash-source-owner"
_DEFERRED = frozenset({"calc", "denied_lookup"})


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    """Never read or write the developer's real agent workspace."""
    home = tmp_path / "agent-workspace"
    home.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))
    return home


# ── fakes ──


@as_tool
def calc(expression: str) -> str:
    """Evaluate an arithmetic expression."""
    return "4"


@as_tool
def denied_lookup(query: str) -> str:
    """Run a lookup the active skill does not authorize."""
    return "denied data"


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


def _search_call(query: str, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "tool_search", "args": {"query": query}, "id": call_id, "type": "tool_call"}])


def _run_search(query: str, *, allowed_tools: list[str] | None, with_policy: bool = True) -> tuple[dict, dict]:
    """Run one ``tool_search`` turn and return ``(result, run_context)``.

    ``allowed_tools=None`` builds no ``SkillToolPolicyMiddleware`` at all, which
    is the "never policy-checked" case the metric must not call a success.
    """
    setup = build_deferred_tool_setup([tag_mcp_tool(calc), tag_mcp_tool(denied_lookup)], enabled=True)
    context: dict = {}
    middlewares = [DeferredToolPromotionAuditMiddleware(setup.deferred_names, setup.catalog_hash)]
    if with_policy:
        policy = SkillToolPolicyMiddleware(slash_source_owner_token=_SLASH_SOURCE_OWNER_TOKEN)
        policy._storage = lambda: _StorageStub([_skill("restricted", allowed_tools or [])])
        write_slash_skill_source_path(context, "/mnt/skills/public/restricted/SKILL.md", owner_token=_SLASH_SOURCE_OWNER_TOKEN)
        middlewares.append(policy)
    middlewares.append(DeferredToolFilterMiddleware(setup.deferred_names, setup.catalog_hash))

    model = _FakeModel([_search_call(query, "search-call"), AIMessage(content="done")])
    graph = create_agent(model=model, tools=[calc, denied_lookup, setup.tool_search_tool], middleware=middlewares, state_schema=ThreadState)
    result = graph.invoke({"messages": [HumanMessage(content="use a deferred tool")]}, context=context)
    return result, context


def _telemetry(context: dict) -> metrics.DiscoveryTelemetry:
    telemetry = context.get(metrics.TELEMETRY_CONTEXT_KEY)
    assert isinstance(telemetry, metrics.DiscoveryTelemetry), "the deferred search did not record anything"
    return telemetry


def _well_formed_decision(**overrides) -> dict:
    decision = {
        "version": metrics.POLICY_DECISION_VERSION,
        "owner_token": "middleware-instance-token",
        "source": "slash",
        "active_paths": ["/mnt/skills/public/restricted/SKILL.md"],
        "allowed_names": ["calc", "tool_search"],
    }
    decision.update(overrides)
    return {SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY: decision}


# ── (d) the honest success path ──


def test_allowed_promotion_is_counted_exactly_once():
    result, context = _run_search("select:calc", allowed_tools=["calc"])

    # Enforcement really happened: the name survived policy into graph state.
    assert result["promoted"]["names"] == ["calc"]
    assert result["promoted"]["catalog_hash"]
    assert SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY in context

    snapshot = _telemetry(context).snapshot()
    assert snapshot["promotions_verified"] == 1
    assert snapshot["verified_names"] == ["calc"]
    assert snapshot["outcomes"]["promoted"] == 1
    assert snapshot["outcomes"]["denied"] == 0
    assert snapshot["outcomes"]["unverified"] == 0
    assert snapshot["reasons"]["policy_allowlist"] == 1


def test_repeated_search_of_the_same_tool_still_counts_each_real_promotion():
    """Two honest promotions of the same name are two recorded events."""
    setup = build_deferred_tool_setup([tag_mcp_tool(calc), tag_mcp_tool(denied_lookup)], enabled=True)
    context: dict = {}
    policy = SkillToolPolicyMiddleware(slash_source_owner_token=_SLASH_SOURCE_OWNER_TOKEN)
    policy._storage = lambda: _StorageStub([_skill("restricted", ["calc"])])
    write_slash_skill_source_path(context, "/mnt/skills/public/restricted/SKILL.md", owner_token=_SLASH_SOURCE_OWNER_TOKEN)
    model = _FakeModel(
        [
            _search_call("select:calc", "call-1"),
            _search_call("select:calc", "call-2"),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(
        model=model,
        tools=[calc, denied_lookup, setup.tool_search_tool],
        middleware=[policy, DeferredToolFilterMiddleware(setup.deferred_names, setup.catalog_hash)],
        state_schema=ThreadState,
    )
    graph.invoke({"messages": [HumanMessage(content="use the calculator")]}, context=context)

    snapshot = _telemetry(context).snapshot()
    assert snapshot["promotions_verified"] == 2
    assert snapshot["outcomes"]["promoted"] == 2


# ── (a) a real policy denial is not a promotion ──


def test_promotion_denied_by_real_policy_middleware_is_not_counted():
    result, context = _run_search("select:denied_lookup", allowed_tools=["calc"])

    # The denial is real and observable in state, not just in the metric.
    assert result["promoted"]["names"] == []

    snapshot = _telemetry(context).snapshot()
    assert snapshot["promotions_verified"] == 0
    assert snapshot["verified_names"] == []
    assert snapshot["outcomes"]["denied"] == 1
    assert snapshot["outcomes"]["promoted"] == 0


# ── (b) unknown is never a success ──


def test_absent_policy_decision_is_unverified_not_a_success():
    result, context = _run_search("select:calc", allowed_tools=None, with_policy=False)

    # Enforcement is real: the promotion landed in graph state.
    assert result["promoted"]["names"] == ["calc"]

    # No policy middleware ran, so nothing published a decision and no recorder
    # is reachable on the run context. "Unmeasurable" must degrade to
    # "claims nothing", never to a promotion.
    assert SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY not in context
    assert metrics.TELEMETRY_CONTEXT_KEY not in context
    assert metrics.current_telemetry() is None

    verdict = metrics.record_search(None, proposed=["calc"], context=context, deferred=_DEFERRED)
    assert verdict.outcome == metrics.UNVERIFIED
    assert verdict.reason == "no_policy_decision"
    assert verdict.promoted == ()


class _DecisionThief(AgentMiddleware):
    """Test-only: drop the decision between the policy middleware and the tool.

    Registered *after* ``SkillToolPolicyMiddleware`` so it runs inside that
    middleware's ``wrap_model_call`` wrapper -- i.e. after the decision was
    published and before the tool node executes. This is the only way to
    reproduce "a decision existed for the model call but is gone by the time
    the promotion is measured" without touching production code.
    """

    def wrap_model_call(self, request, handler):
        context = getattr(getattr(request, "runtime", None), "context", None)
        if isinstance(context, dict):
            context.pop(SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY, None)
        return handler(request)


def test_decision_missing_by_tool_time_is_recorded_unverified_end_to_end():
    """A promotion that reaches the tool with no decision counts as nothing."""
    setup = build_deferred_tool_setup([tag_mcp_tool(calc), tag_mcp_tool(denied_lookup)], enabled=True)
    context: dict = {}
    policy = SkillToolPolicyMiddleware(slash_source_owner_token=_SLASH_SOURCE_OWNER_TOKEN)
    policy._storage = lambda: _StorageStub([_skill("restricted", ["calc"])])
    write_slash_skill_source_path(context, "/mnt/skills/public/restricted/SKILL.md", owner_token=_SLASH_SOURCE_OWNER_TOKEN)
    model = _FakeModel([_search_call("select:calc", "s1"), AIMessage(content="done")])
    graph = create_agent(
        model=model,
        tools=[calc, denied_lookup, setup.tool_search_tool],
        # `_DecisionThief` must come after `policy` to run inside its wrapper.
        middleware=[policy, _DecisionThief(), DeferredToolFilterMiddleware(setup.deferred_names, setup.catalog_hash)],
        state_schema=ThreadState,
    )
    result = graph.invoke({"messages": [HumanMessage(content="use the calculator")]}, context=context)

    # The promotion itself is untouched by telemetry: still real in state.
    assert result["promoted"]["names"] == ["calc"]

    snapshot = _telemetry(context).snapshot()
    assert snapshot["promotions_verified"] == 0
    assert snapshot["verified_names"] == []
    assert snapshot["outcomes"]["promoted"] == 0
    assert snapshot["outcomes"]["unverified"] == 1
    assert snapshot["reasons"]["no_policy_decision"] == 1


@pytest.mark.parametrize(
    ("context_factory", "expected_reason"),
    [
        (lambda: {}, "no_policy_decision"),
        (lambda: {"unrelated": "value"}, "no_policy_decision"),
        (lambda: {SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY: "not-a-dict"}, "malformed_policy_decision"),
        (lambda: _well_formed_decision(allowed_names="calc"), "malformed_policy_decision"),
        (lambda: _well_formed_decision(allowed_names=["calc", 7]), "malformed_policy_decision"),
        (lambda: _well_formed_decision(active_paths="not-a-list"), "malformed_policy_decision"),
        (lambda: _well_formed_decision(active_paths=[7]), "malformed_policy_decision"),
        (lambda: _well_formed_decision(owner_token=""), "malformed_policy_decision"),
        (lambda: _well_formed_decision(owner_token=None), "malformed_policy_decision"),
        (lambda: _well_formed_decision(version=1), "foreign_policy_decision"),
        (lambda: _well_formed_decision(version="2"), "foreign_policy_decision"),
        (lambda: _well_formed_decision(source="some_other_middleware"), "foreign_policy_decision"),
        (lambda: _well_formed_decision(source=7), "foreign_policy_decision"),
    ],
)
def test_missing_foreign_or_malformed_decision_is_never_a_success(context_factory, expected_reason):
    verdict = metrics.evaluate_proposed_promotions(["calc"], context_factory(), deferred=_DEFERRED)

    assert verdict.outcome == metrics.UNVERIFIED
    assert verdict.reason == expected_reason
    assert verdict.promoted == ()


def test_non_dict_context_is_unverified():
    for context in (None, "context", 42, []):
        verdict = metrics.evaluate_proposed_promotions(["calc"], context, deferred=_DEFERRED)
        assert verdict.outcome == metrics.UNVERIFIED
        assert verdict.reason == "no_runtime_context"
        assert verdict.promoted == ()


def test_unverified_decision_cannot_inflate_the_recorded_metric():
    telemetry = metrics.DiscoveryTelemetry()
    unusable_contexts = [
        {},
        {"something_else": 1},
        _well_formed_decision(version=99),
        _well_formed_decision(allowed_names={"calc": True}),
    ]
    for context in unusable_contexts:
        metrics.record_search(telemetry, proposed=["calc"], context=context, deferred=_DEFERRED)

    snapshot = telemetry.snapshot()
    assert snapshot["promotions_verified"] == 0
    assert snapshot["outcomes"]["promoted"] == 0
    assert snapshot["outcomes"]["unverified"] == 4


# ── (c) an error result is an error result ──


class _ToolMessageHandler:
    """A handler whose ``invoke`` mimics a real LangChain tool returning a ToolMessage."""

    def __init__(self, message: ToolMessage):
        self._message = message

    def invoke(self, arguments):
        return self._message


@pytest.fixture
def catalog_context(monkeypatch):
    """Point ``catalog_tool_call`` at a per-test catalog with a per-run recorder."""
    catalog = UniversalToolCatalog()
    monkeypatch.setattr("alpha.tools.builtins.tool_search_tool.get_universal_catalog", lambda: catalog)
    context: dict = {}
    monkeypatch.setattr(metrics, "current_run_context", lambda: context)
    return catalog, context


def test_error_tool_message_is_not_counted_as_a_promotion(catalog_context):
    catalog, context = catalog_context
    catalog.register_tool(
        name="flaky",
        handler=_ToolMessageHandler(ToolMessage(content="upstream refused", tool_call_id="x", name="flaky", status="error")),
        description="Returns an error ToolMessage",
    )

    out = catalog_tool_call.invoke({"tool_name": "flaky", "arguments": {}})

    assert "upstream refused" in out
    snapshot = _telemetry(context).snapshot()
    assert snapshot["promotions_verified"] == 0
    assert snapshot["outcomes"]["promoted"] == 0
    assert snapshot["call_outcomes"]["error"] == 1
    assert snapshot["reasons"]["error_status"] == 1


def test_error_string_from_handler_is_not_counted_as_a_promotion(catalog_context):
    catalog, context = catalog_context
    catalog.register_tool(name="wordy", handler=lambda: "Error: quota exceeded", description="Returns an error string")

    out = catalog_tool_call.invoke({"tool_name": "wordy", "arguments": {}})

    assert out == "Error: quota exceeded"
    snapshot = _telemetry(context).snapshot()
    assert snapshot["promotions_verified"] == 0
    assert snapshot["outcomes"]["promoted"] == 0
    assert snapshot["call_outcomes"]["error"] == 1
    assert snapshot["reasons"]["error_result"] == 1


def test_raised_call_is_not_counted_as_a_promotion(catalog_context):
    catalog, context = catalog_context

    def _boom(**_kwargs):
        raise RuntimeError("backend unavailable")

    catalog.register_tool(name="broken", handler=_boom, description="Always raises")

    out = catalog_tool_call.invoke({"tool_name": "broken", "arguments": {}})

    assert "backend unavailable" in out
    snapshot = _telemetry(context).snapshot()
    assert snapshot["promotions_verified"] == 0
    assert snapshot["call_outcomes"]["error"] == 1
    assert snapshot["reasons"]["call_raised"] == 1


def test_unrecognised_tool_message_status_is_unverified_not_success():
    """`status` is a strict Literal today, so this pins the defensive branch.

    `model_construct` bypasses pydantic validation on purpose: if a future
    langchain release loosens the Literal, this branch becomes reachable and
    must not silently start reporting success.
    """
    odd = ToolMessage.model_construct(content="odd", tool_call_id="x", name="t", status="weird")
    outcome, reason = metrics.classify_catalog_result(odd)
    assert outcome == metrics.UNVERIFIED
    assert reason == "unverified_result"


def test_successful_catalog_call_is_still_recorded_as_a_promotion(catalog_context):
    catalog, context = catalog_context
    catalog.register_tool(name="ok_tool", handler=lambda: {"value": 1}, description="Succeeds")

    out = catalog_tool_call.invoke({"tool_name": "ok_tool", "arguments": {}})

    assert json.loads(out) == {"value": 1}
    snapshot = _telemetry(context).snapshot()
    # A catalog call is not a deferred-tool promotion, and the snapshot keeps
    # the two apart so `outcomes` can never imply a promotion happened.
    assert snapshot["promotions_verified"] == 0
    assert snapshot["outcomes"]["promoted"] == 0
    assert snapshot["calls"]["catalog_tool_call"] == 1
    assert snapshot["call_outcomes"]["promoted"] == 1
    assert snapshot["reasons"]["call_succeeded"] == 1


# ── (e) the metric cannot be inflated ──


def test_metric_cannot_be_inflated_by_repeated_denied_calls():
    """The headline: a denial that repeats must not move the success counter."""
    setup = build_deferred_tool_setup([tag_mcp_tool(calc), tag_mcp_tool(denied_lookup)], enabled=True)
    context: dict = {}
    policy = SkillToolPolicyMiddleware(slash_source_owner_token=_SLASH_SOURCE_OWNER_TOKEN)
    policy._storage = lambda: _StorageStub([_skill("restricted", ["calc"])])
    write_slash_skill_source_path(context, "/mnt/skills/public/restricted/SKILL.md", owner_token=_SLASH_SOURCE_OWNER_TOKEN)
    model = _FakeModel(
        [
            _search_call("select:denied_lookup", "d1"),
            _search_call("select:denied_lookup", "d2"),
            _search_call("select:denied_lookup", "d3"),
            _search_call("select:denied_lookup", "d4"),
            _search_call("select:denied_lookup", "d5"),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(
        model=model,
        tools=[calc, denied_lookup, setup.tool_search_tool],
        middleware=[policy, DeferredToolFilterMiddleware(setup.deferred_names, setup.catalog_hash)],
        state_schema=ThreadState,
    )
    result = graph.invoke({"messages": [HumanMessage(content="try the denied lookup repeatedly")]}, context=context)

    assert result["promoted"]["names"] == []
    snapshot = _telemetry(context).snapshot()
    assert snapshot["promotions_verified"] == 0
    assert snapshot["verified_names"] == []
    assert snapshot["outcomes"]["denied"] == 5
    assert snapshot["outcomes"]["promoted"] == 0


def test_partially_denied_search_counts_only_the_permitted_name():
    result, context = _run_search("select:calc,denied_lookup", allowed_tools=["calc"])

    assert result["promoted"]["names"] == ["calc"]

    snapshot = _telemetry(context).snapshot()
    # Two names were proposed, one survived: one promotion, not two.
    assert snapshot["promotions_verified"] == 1
    assert snapshot["verified_names"] == ["calc"]
    assert snapshot["outcomes"]["denied"] == 1


def test_no_match_search_is_its_own_outcome():
    result, context = _run_search("select:nothing_matches_this", allowed_tools=["calc"])

    assert result["promoted"]["names"] == []

    snapshot = _telemetry(context).snapshot()
    assert snapshot["promotions_verified"] == 0
    assert snapshot["outcomes"]["no_match"] == 1
    assert snapshot["outcomes"]["denied"] == 0
    assert snapshot["outcomes"]["unverified"] == 0


def test_names_outside_the_deferred_set_are_never_promotions():
    verdict = metrics.evaluate_proposed_promotions(["not_deferred"], _well_formed_decision(), deferred=_DEFERRED)
    assert verdict.outcome == metrics.NO_MATCH
    assert verdict.promoted == ()


# ── contract + bookkeeping ──


def test_policy_decision_contract_matches_the_enforcing_middleware():
    """The restated constants must track the code that enforces them."""
    from alpha.agents.middlewares import skill_tool_policy_middleware as enforcing

    assert metrics.POLICY_DECISION_VERSION == enforcing._POLICY_DECISION_VERSION
    assert metrics.POLICY_SOURCES == enforcing._POLICY_SOURCES
    assert metrics.SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY == SKILL_TOOL_POLICY_DECISION_CONTEXT_KEY


def test_telemetry_is_per_run_and_never_global():
    """Two runs must not observe each other's counters."""
    first_context: dict = {}
    second_context: dict = {}

    first = metrics.resolve_telemetry(first_context)
    second = metrics.resolve_telemetry(second_context)

    assert first is not second
    metrics.record_call(first, kind="catalog_tool_call", outcome=metrics.PROMOTED, reason="call_succeeded")
    assert first.snapshot()["calls"] == {"catalog_tool_call": 1}
    assert second.snapshot()["calls"] == {}


def test_recorder_is_created_on_first_use_and_reused():
    context: dict = {}
    first = metrics.resolve_telemetry(context)
    assert context[metrics.TELEMETRY_CONTEXT_KEY] is first
    assert metrics.resolve_telemetry(context) is first


def test_off_graph_invocation_records_nothing_rather_than_a_success():
    """No run context means unmeasurable, which must not become measurable."""
    assert metrics.current_run_context() is None
    assert metrics.current_telemetry() is None
    verdict = metrics.record_search(None, proposed=["calc"], context=None, deferred=_DEFERRED)
    assert verdict.outcome == metrics.UNVERIFIED
    assert verdict.promoted == ()


def test_snapshot_exposes_every_outcome_key():
    snapshot = metrics.DiscoveryTelemetry().snapshot()
    assert set(snapshot["outcomes"]) == set(metrics.OUTCOMES)
    assert set(snapshot["call_outcomes"]) == set(metrics.OUTCOMES)
    assert all(value == 0 for value in snapshot["outcomes"].values())
    assert all(value == 0 for value in snapshot["call_outcomes"].values())
    assert snapshot["promotions_verified"] == 0
    assert snapshot["verified_names"] == []


def test_direct_tool_invoke_off_graph_still_returns_its_command():
    """The metrics change must not alter the tool's own return value."""
    search_tool = build_tool_search_tool(DeferredToolCatalog((tag_mcp_tool(calc),)))

    out = search_tool.invoke({"type": "tool_call", "name": "tool_search", "args": {"query": "select:calc"}, "id": "tc1"})

    assert isinstance(out, Command)
    assert out.update["promoted"]["names"] == ["calc"]
