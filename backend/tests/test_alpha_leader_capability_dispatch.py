"""The `alpha` default leader, capability dispatch, and bounded delegation.

Every test here is a claim about PRODUCTION behaviour, reached through the same
public entry points the runtime uses:

* :meth:`alpha.bots.registry.BotRegistry` construction — what a fresh install gets.
* :meth:`alpha.bots.capability_dispatch.CapabilityDispatcher.dispatch` — the
  dispatch path that :meth:`alpha.planning.bridge.AutonomousDispatchBridge.
  _dispatch_bot_profile` calls.
* :func:`alpha.bots.reassignment.reassign_after_failure` — the reassignment path.
* :func:`alpha.planning.bridge.AutonomousDispatchBridge.dispatch` — end to end.

The three components this task set out to wire up were all dead code before
this change: ``capability_tags`` was consulted only for leader election,
``match_bot_for_task`` had no production caller, and ``ContractNetAuctionEngine``
was imported only by its own test. Tests (b), (c) and the auction test below are
what keep them from going dead again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.bots.alpha_leader import (
    ALPHA_LEADER_NAME,
    ALPHA_LEADER_ROLE,
    ALPHA_LEADER_SOUL,
    DIRECTABLE_CAPABILITIES,
    LEADER_CAPABILITIES,
    MAX_DIRECTABLE_TAGS_PER_DISPATCH,
    NEVER_DIRECTED_CAPABILITIES,
    authority_boundary,
    ensure_alpha_leader,
    leader_may_direct,
    undirected_template_capabilities,
)
from alpha.bots.capability_dispatch import (
    DISPATCH_METHOD,
    DispatchOutcome,
    get_leader_dispatcher,
)
from alpha.bots.delegation import (
    REFUSAL_CYCLE,
    REFUSAL_DEPTH_CEILING,
    REFUSAL_FANOUT_CEILING,
    REFUSAL_HOP_CEILING,
    REFUSAL_TOKEN_BUDGET,
    ChildStatus,
    DelegationLimits,
    TreeStatus,
    child_report,
    root_context,
    run_bounded,
)
from alpha.bots.permissions import ToolPermissionGate
from alpha.bots.reassignment import (
    REASSIGN_SCOPE_ELSEWHERE,
    RETRY_SCOPE_IN_PLACE,
    ReassignmentAction,
    reassign_after_failure,
)
from alpha.bots.registry import BotRegistry
from alpha.bots.templates import BOT_TEMPLATES
from alpha.capabilities.eligibility import (
    eligible_candidates,
    match_capabilities,
    normalize_tag,
    normalize_tags,
    profile_capability_tags,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry(tmp_path: Path) -> BotRegistry:
    """A registry on an isolated roster file — no shared/global state."""
    return BotRegistry(storage_path=tmp_path / "roster.json")


@pytest.fixture()
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An isolated handoff ledger, reset around the test.

    ``AGENT_WORKSPACE_HOME`` is pinned to a unique directory so the global
    ledger never touches a developer's real runtime home.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))
    from alpha.runtime import escalation

    escalation.reset_ledger_caches()
    yield escalation.get_handoff_ledger()
    escalation.reset_ledger_caches()


def _ledger_entries(ledger, task_id: str | None = None) -> list:
    return ledger.entries(task_id=task_id) if task_id is not None else ledger.entries()


def _add_peer(registry: BotRegistry, name: str, capabilities: list[str], *, reputation: float = 0.5) -> None:
    """Register an extra live peer with a declared capability surface."""
    from alpha.bots.profile import BotProfile

    registry.register(
        BotProfile(
            name=name,
            display_name=name.capitalize(),
            role=f"{name} role",
            soul=f"{name} soul",
            capabilities=list(capabilities),
            reputation_score=reputation,
        )
    )


# ===========================================================================
# (a) A fresh install has `alpha` as the default leader, with no configuration
# ===========================================================================


def test_fresh_install_has_alpha_leader_with_no_configuration(tmp_path: Path) -> None:
    """A brand-new roster (empty dir, no config) already has a leader."""
    roster_path = tmp_path / "fresh" / "roster.json"
    assert not roster_path.exists()

    reg = BotRegistry(storage_path=roster_path)

    leader = reg.get_bot(ALPHA_LEADER_NAME)
    assert leader is not None, "a fresh install must have a leader with zero configuration"
    assert leader.role == ALPHA_LEADER_ROLE
    assert leader.department == "executive"
    assert leader.reports_to is None, "the leader is the root of the reporting chain"
    assert leader.soul == ALPHA_LEADER_SOUL
    assert set(LEADER_CAPABILITIES) <= set(leader.capabilities)
    assert leader.capabilities, "the leader must declare its own capabilities"
    assert roster_path.exists()


def test_alpha_leader_is_the_root_of_the_org_chart(registry: BotRegistry) -> None:
    """Nothing reports to the leader, so a cycle cannot be built through it."""
    leader = registry.get_bot(ALPHA_LEADER_NAME)
    assert leader is not None
    assert registry.get_subordinates(ALPHA_LEADER_NAME) == []


def test_alpha_is_registered_in_the_shared_template_catalog(registry: BotRegistry) -> None:
    """`get_or_create("alpha")` and the template catalog agree on the leader."""
    assert ALPHA_LEADER_NAME in BOT_TEMPLATES
    spec = BOT_TEMPLATES[ALPHA_LEADER_NAME]
    assert spec["role"] == ALPHA_LEADER_ROLE
    assert list(spec["capabilities"]) == list(LEADER_CAPABILITIES)
    assert spec["is_leader"] is True


def test_get_or_create_alpha_never_yields_the_generic_soul(registry: BotRegistry) -> None:
    """The provisioning path cannot create a leader with a generic SOUL."""
    bot = registry.get_or_create(ALPHA_LEADER_NAME)
    assert bot.soul == ALPHA_LEADER_SOUL
    assert bot.role == ALPHA_LEADER_ROLE


def test_ensure_alpha_leader_is_idempotent_and_preserves_operator_edits(registry: BotRegistry) -> None:
    registry.update_bot(ALPHA_LEADER_NAME, display_name="Operator Renamed", bump_version=False)
    again = ensure_alpha_leader(registry)
    assert again.display_name == "Operator Renamed"
    assert again.soul == ALPHA_LEADER_SOUL


def test_leader_prompts_describe_the_authority_boundary() -> None:
    """The SOUL and the system prompt both state the boundary in words."""
    assert "Never bypass a gate" in ALPHA_LEADER_SOUL
    assert "propose, dispatch, recall" in ALPHA_LEADER_SOUL
    from alpha.agents.lead_agent.prompt import _build_leader_dispatch_section

    section = _build_leader_dispatch_section()
    assert "<leader_dispatch>" in section
    assert "NEVER DIRECTABLE" in section
    assert "production_deploy" in section
    # Every directly-able tag is disclosed to the model, so the stated
    # boundary cannot silently disagree with the enforced one.
    for tag in ("sql", "react", "security_review", "code_generation"):
        assert f"`{tag}`" in section


# --- the boundary itself ----------------------------------------------------


def test_every_template_capability_is_inside_the_leader_allowlist() -> None:
    """A provisioned peer must be reachable, or the allowlist is a mis-specification."""
    assert undirected_template_capabilities() == ()


def test_leader_allowlist_covers_the_default_seed_roster(registry: BotRegistry) -> None:
    for bot in registry.list_bots(include_archived=False):
        if bot.name == ALPHA_LEADER_NAME:
            continue
        allowed, reason, blocking = leader_may_direct(profile_capability_tags(bot))
        assert allowed, f"@{bot.name} declares {sorted(profile_capability_tags(bot))} but the leader may not direct it: {reason} {blocking}"


def test_leader_may_direct_does_not_cover_irreversible_capabilities() -> None:
    for tag in NEVER_DIRECTED_CAPABILITIES:
        allowed, _reason, blocking = leader_may_direct([tag])
        assert not allowed, f"{tag} must never be directly routable by a leader"
        assert tag in blocking


def test_leader_may_direct_refuses_an_undeclared_capability() -> None:
    allowed, reason, blocking = leader_may_direct(["teleportation"])
    assert not allowed
    assert "allowlist" in reason
    assert blocking == ("teleportation",)


def test_leader_may_direct_refuses_an_over_wide_requirement() -> None:
    allowed, reason, _blocking = leader_may_direct([f"cap_{i}" for i in range(MAX_DIRECTABLE_TAGS_PER_DISPATCH + 1)])
    assert not allowed
    assert "at most" in reason


def test_leader_authority_boundary_grants_no_tool_and_lifts_no_gate() -> None:
    """The boundary is a ROUTING allowlist, not a superuser bypass."""
    boundary = authority_boundary()
    assert boundary["grants_tool_access"] is False
    assert boundary["bypasses_approval_gate"] is False
    assert boundary["bypasses_policy_layer"] is False
    assert boundary["bypasses_safety_enclave"] is False
    assert boundary["bypasses_sandbox"] is False
    assert boundary["authority"] == "propose_dispatch_recall"

    # And the code agrees: the leader's role ring denies every irreversible verb
    # and is not an `allow_all` ring.
    gate = ToolPermissionGate()
    for tool in ("write_to_file", "run_command", "git_push", "delete_file", "deploy_production", "drop_database"):
        allowed, _reason, requires_approval = gate.check_permission(ALPHA_LEADER_ROLE, tool)
        assert not allowed, f"the leader role must not be able to {tool}"
        assert not requires_approval
    for tool in ("message_agent", "view_file", "grep_search", "bot_roster"):
        allowed, _reason, _req = gate.check_permission(ALPHA_LEADER_ROLE, tool)
        assert allowed, f"the leader must be able to {tool}"


def test_leader_role_does_not_resolve_to_an_allow_all_ring() -> None:
    """A role string that fuzzy-matched `lead`/`admin` would hand over everything.

    Also pins that the leader resolves to its OWN explicit ring rather than
    silently falling through to the general-worker default: a ring that is
    skipped by the resolver (because it was flipped to ``allow_all``) and then
    falls through to the default still *denies* writes, so a deny-only assertion
    cannot tell the two apart. The leader-ring-only verbs are what can.
    """
    gate = ToolPermissionGate()
    ring = gate._resolve_ring(ALPHA_LEADER_ROLE)
    assert ring.allow_all is False
    # ``_resolve_ring`` lower-cases the role, so compare case-insensitively.
    assert ring.role_name.lower() == ALPHA_LEADER_ROLE.lower(), "the leader must resolve to its own ring, not the general-worker default"
    for tool in ("bot_roster", "cancel_batch", "ask_clarification"):
        assert tool in ring.allowed_tools, f"{tool} belongs to the leader ring and proves the ring is reached"
    # Sanity: an actually-privileged role still gets allow_all, so the assertion
    # above is testing this role and not a broken gate.
    assert gate._resolve_ring("lead").allow_all is True


def test_an_unrelated_role_still_gets_the_general_worker_default() -> None:
    """Control for the test above: the default ring is genuinely different."""
    gate = ToolPermissionGate()
    fallback = gate._resolve_ring("some brand new role nobody has heard of")
    assert "bot_roster" not in fallback.allowed_tools
    assert "view_file" in fallback.allowed_tools


# ===========================================================================
# Capability-tag vocabulary and the hard eligibility filter
# ===========================================================================


def test_capability_tags_normalise_separators_and_case() -> None:
    assert normalize_tag("  Code Generation ") == "code_generation"
    assert normalize_tag("code-generation") == "code_generation"
    assert normalize_tag("code/generation") == "code_generation"
    assert normalize_tag("") == ""
    assert normalize_tags(["SQL", "sql", " sql "]) == frozenset({"sql"})


def test_profile_capability_tags_include_skills_not_responsibilities() -> None:
    from alpha.bots.profile import BotProfile

    bot = BotProfile(
        name="probe",
        display_name="Probe",
        role="Probe",
        soul="probe",
        capabilities=["sql"],
        skills=["pytest"],
        responsibilities=["write reports"],
    )
    tags = profile_capability_tags(bot)
    assert "sql" in tags and "pytest" in tags
    assert "write reports" not in tags and "write_reports" not in tags


def test_eligibility_is_a_hard_filter_not_a_score(registry: BotRegistry) -> None:
    """An agent that does not cover the requirement is removed, not down-ranked."""
    profiles = registry.list_bots(include_archived=False)
    result = eligible_candidates(profiles, ["sql"])
    assert "data-analyst" in result.eligible
    assert "coder" not in result.eligible
    rejected = {item["bot"]: item for item in result.rejected}
    assert rejected["coder"]["code"] == "capability_mismatch"
    assert "sql" in rejected["coder"]["missing"]


def test_eligibility_reports_no_constraint_explicitly(registry: BotRegistry) -> None:
    result = eligible_candidates(registry.list_bots(include_archived=False), None)
    assert result.constrained is False
    assert "no capability tags declared" in result.reason


def test_match_capabilities_reports_coverage() -> None:
    from alpha.bots.profile import BotProfile

    bot = BotProfile(name="probe", display_name="P", role="P", soul="p", capabilities=["sql", "data_modeling"])
    match = match_capabilities(["sql", "react"], bot)
    assert match.eligible is False
    assert match.matched == frozenset({"sql"})
    assert match.missing == frozenset({"react"})
    assert match.coverage == 0.5


# ===========================================================================
# (b) A task is routed to the agent whose capability_tags match
# ===========================================================================


def test_dispatch_routes_to_the_capability_match_and_never_to_a_mismatch(registry: BotRegistry, ledger) -> None:
    dispatcher = get_leader_dispatcher("task-sql", registry=registry)
    decision = dispatcher.dispatch("task-sql", "Add an index to the orders table and explain the query plan", required_capability_tags=["sql"])

    assert decision.outcome is DispatchOutcome.DISPATCHED
    assert decision.target == "data-analyst", decision.reason
    assert "data-analyst" not in {item["bot"] for item in decision.rejected}
    rejected = {item["bot"]: item for item in decision.rejected}
    assert "coder" in rejected
    assert rejected["coder"]["code"] == "capability_mismatch"
    assert "sql" in rejected["coder"]["missing"]


def test_dispatch_refuses_when_nobody_declares_the_capability(registry: BotRegistry, ledger) -> None:
    """A directable capability no live agent declares must be an honest refusal.

    ``incident_response`` is on the leader's allowlist (it is the SRE template's
    capability) but SRE is not in the default seed roster, so nothing can take
    the work. Handing it to anybody would be the exact bug this task set out to
    fix.
    """
    dispatcher = get_leader_dispatcher("task-cookiecutter", registry=registry)
    decision = dispatcher.dispatch("task-cookiecutter", "Page the on-call for the outage", required_capability_tags=["incident_response"])

    assert decision.outcome is DispatchOutcome.REFUSED
    assert decision.target is None
    assert decision.refusal_code == "no_eligible_agent"
    assert "incident_response" in decision.reason
    # Every agent considered was rejected for the SAME reason: it does not
    # declare the capability. Nothing was excluded for an unrelated reason.
    rejected = {item["bot"]: item for item in decision.rejected}
    assert rejected, "a refusal must say who was rejected and why"
    assert all(item["code"] == "capability_mismatch" and "incident_response" in item["detail"] for item in rejected.values())


def test_dispatch_refuses_a_capability_outside_the_leader_allowlist(registry: BotRegistry, ledger) -> None:
    """An undeclared capability is refused by the AUTHORITY boundary, not rerouted."""
    dispatcher = get_leader_dispatcher("task-pastry", registry=registry)
    decision = dispatcher.dispatch("task-pastry", "Bake cookies", required_capability_tags=["pastry_hygiene"])
    assert decision.refusal_code == "capability_missing"
    assert decision.target is None


def test_dispatch_wired_the_contract_net_auction_for_real(registry: BotRegistry, ledger) -> None:
    """`ContractNetAuctionEngine` must be on the dispatch path, not just tested.

    Before this change the engine's only reference in the whole tree was its own
    test. Asserting only on the returned award is not enough — a hand-forged
    ``ContractAward`` would satisfy that — so this also asserts the engine's own
    side effects: its award table, the blackboard lease it takes, and the
    pheromone trace it deposits. Those exist only if ``conduct_auction`` really
    ran.
    """
    from alpha.blackboard.federated_blackboard import PheromoneType
    from alpha.swarm.cnp_auction import ContractNetAuctionEngine

    dispatcher = get_leader_dispatcher("task-auction", registry=registry)
    assert dispatcher.last_engine is None, "no auction has run yet"

    decision = dispatcher.dispatch("task-auction", "Tune the postgresql query planner", required_capability_tags=["sql"])

    engine = dispatcher.last_engine
    assert isinstance(engine, ContractNetAuctionEngine)

    award = decision.award
    assert award is not None, "the award must come from a real auction"
    assert award["contractor_agent_id"] == decision.target
    assert award["winning_bid_score"] > 0
    assert decision.bids, "the scored bids must be recorded"
    assert {bid["agent_id"] for bid in decision.bids} >= {"data-analyst"}
    # Only capability-eligible agents bid: the auction cannot re-introduce a
    # mismatched agent the filter removed.
    assert {bid["agent_id"] for bid in decision.bids} == {c["bot_name"] for c in decision.candidates}

    # Side effects that only a real conduct_auction() produces.
    assert len(engine.awards) == 1, "the engine's own award table must hold this award"
    assert list(engine.awards.values())[0].contractor_agent_id == decision.target
    assert award["lease_acquired"] is True
    lease = next(iter(engine.blackboard._leases.values()), None)
    assert lease is not None and lease.owner_agent_id == decision.target
    assert lease.resource_id == f"task:{list(engine.awards)[0]}"
    traces = engine.blackboard._traces.get("cluster:alpha.leader.dispatch", [])
    assert any(trace.trace_type is PheromoneType.ACTIVITY for trace in traces), "the auction must deposit its activity trace"


def test_dispatch_wired_match_bot_for_task_for_real(registry: BotRegistry, ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    """`match_bot_for_task` must be called by the dispatch path.

    It had NO production caller before this change; this test fails if the
    dispatch stops consulting it, which is how the declared-fit half of the
    recorded reason is produced.
    """
    import alpha.bots.capability_dispatch as cap_dispatch

    calls: list[str] = []
    real = cap_dispatch.match_bot_for_task

    def spy(*args, **kwargs):
        calls.append(str(args[0])[:60])
        return real(*args, **kwargs)

    monkeypatch.setattr(cap_dispatch, "match_bot_for_task", spy)
    dispatcher = get_leader_dispatcher("task-fit", registry=registry)
    decision = dispatcher.dispatch("task-fit", "Review the payment adapter for injection risk", required_capability_tags=["security_review"])

    assert calls, "the dispatch path must consult match_bot_for_task"
    assert decision.target == "reviewer"
    assert any("Skills matched" in r for c in decision.candidates for r in c["reasons"] if c["bot_name"] == "reviewer")


def test_dispatch_method_is_stamped_on_the_decision(registry: BotRegistry, ledger) -> None:
    decision = get_leader_dispatcher("task-m", registry=registry).dispatch("task-m", "Write release notes", required_capability_tags=["technical_writing"])
    assert decision.method == DISPATCH_METHOD


def test_dispatch_infers_the_requirement_from_the_task_text_not_the_proposed_owner(registry: BotRegistry, ledger) -> None:
    """A requirement taken from the proposed owner would be circular.

    ``coder`` is the proposed owner, so deriving the requirement from coder's own
    surface would make coder eligible by construction. The task TEXT is the
    non-circular source, and it is what excludes the coder here.
    """
    dispatcher = get_leader_dispatcher("task-circ", registry=registry)
    decision = dispatcher.dispatch("task-circ", "Add a composite index to the orders table", proposed_owners=["coder"])
    assert decision.target == "data-analyst"
    assert "sql" in decision.required_capability_tags
    assert "inferred from the task text" in decision.reason


# ===========================================================================
# (c) The choice and its reason are recorded
# ===========================================================================


def test_the_choice_and_its_reason_are_written_to_the_handoff_ledger(registry: BotRegistry, ledger) -> None:
    dispatcher = get_leader_dispatcher("task-ledger", registry=registry)
    decision = dispatcher.dispatch("task-ledger", "Rewrite the flaky auth test suite", required_capability_tags=["pytest"])

    assert decision.target == "tester"
    entries = _ledger_entries(ledger, "task-ledger")
    assert len(entries) == 1, "exactly one hop recorded for one dispatch"
    entry = entries[0]
    # The four mandatory fields.
    assert entry.from_ref == ALPHA_LEADER_NAME
    assert entry.to_ref == "tester"
    assert entry.reason
    assert entry.attempt == 1
    # The reason is the SAME string the caller received, so the record and the
    # explanation cannot drift.
    assert entry.reason == decision.reason
    assert "pytest" in entry.reason
    assert "tester" in entry.reason
    # And the structured evidence is on the entry.
    assert entry.details["method"] == DISPATCH_METHOD
    assert entry.details["required_capability_tags"] == ["pytest"]
    assert entry.details["matched_capability_tags"] == ["pytest"]
    assert "data-analyst" in {item["bot"] for item in entry.details["rejected"] if item["code"] == "capability_mismatch"}
    assert entry.details["award"]["contractor_agent_id"] == "tester"
    assert entry.seq is not None
    assert decision.ledger_seq == entry.seq


def test_a_refusal_is_also_recorded(registry: BotRegistry, ledger) -> None:
    decision = get_leader_dispatcher("task-refused", registry=registry).dispatch("task-refused", "Page the on-call for the outage", required_capability_tags=["incident_response"])
    assert not decision.ok
    entries = _ledger_entries(ledger, "task-refused")
    assert len(entries) == 1
    assert entries[0].to_ref == "(none)"
    assert entries[0].details["refused"] is True
    assert entries[0].reason == decision.refusal_code


def test_the_recorded_reason_names_the_capability_that_decided_it(registry: BotRegistry, ledger) -> None:
    decision = get_leader_dispatcher("task-why", registry=registry).dispatch("task-why", "Migrate the schema", required_capability_tags=["sql"])
    reason = decision.reason
    assert "@data-analyst" in reason
    assert "sql" in reason
    assert "auction award score" in reason


# ===========================================================================
# (d) The depth ceiling and cycle guard stop a runaway tree
# ===========================================================================


def test_depth_ceiling_stops_a_runaway_tree(registry: BotRegistry, ledger) -> None:
    limits = DelegationLimits(max_depth=2, max_fanout=4, max_hops=10)
    root = get_leader_dispatcher("deep", registry=registry, limits=limits)
    child = root.for_child("coder")
    grandchild = child.for_child("reviewer")

    assert child.context.depth == 1
    assert grandchild.context.depth == 2

    decision = grandchild.dispatch("deep-sub", "Refactor something", required_capability_tags=["code_audit"])
    assert decision.outcome is DispatchOutcome.REFUSED
    assert decision.refusal_code == REFUSAL_DEPTH_CEILING
    assert "ceiling of 2" in decision.reason
    # A stopped tree is a recorded stop, not a silent no-op.
    assert any(e.reason == REFUSAL_DEPTH_CEILING for e in _ledger_entries(ledger, "deep-sub"))


def test_fanout_ceiling_stops_a_runaway_fan_out(registry: BotRegistry, ledger) -> None:
    from dataclasses import replace

    limits = DelegationLimits(max_depth=3, max_fanout=2, max_hops=10)
    dispatcher = get_leader_dispatcher("wide", registry=registry, limits=limits)
    first = dispatcher.dispatch("wide-1", "One thing", required_capability_tags=["sql"])
    second = dispatcher.dispatch("wide-2", "Another thing", required_capability_tags=["pytest"])
    assert first.ok and second.ok
    # Charge the node's budget the way a real child report would.
    charged = dispatcher.context.charge(tokens=10, cost_usd=0.1)
    dispatcher._context = replace(charged, fanout_used=2)
    third = dispatcher.dispatch("wide-3", "A third thing", required_capability_tags=["react"])
    assert third.refusal_code == REFUSAL_FANOUT_CEILING


def test_delegation_context_cycle_guard_is_the_decision_point() -> None:
    """The guard on the context itself, independent of any dispatcher.

    An earlier version of the dispatcher re-implemented the lineage test inline,
    so weakening :meth:`DelegationContext.check_cycle` changed no observable
    behaviour — the guard was not load-bearing. These assertions pin the guard
    itself, and the dispatcher uses it as its only cycle decision.
    """
    context = root_context(ALPHA_LEADER_NAME, "t", limits=DelegationLimits(max_depth=9, max_hops=99, max_fanout=9))
    assert context.check_cycle("coder").allowed is True

    child = context.for_child("coder")
    verdict = child.check_cycle("alpha")
    assert verdict.allowed is False
    assert verdict.code == REFUSAL_CYCLE
    assert "already holds this task" in verdict.detail
    assert child.check_cycle("Coder").allowed is False, "the guard is case-insensitive: 'Coder' is the same agent as 'coder'"
    assert child.check_cycle("  CODER  ").allowed is False, "...and tolerant of surrounding whitespace"

    grandchild = child.for_child("reviewer")
    assert grandchild.check_cycle("coder").allowed is False, "every ancestor, not just the parent"
    assert grandchild.check_cycle("reviewer").allowed is False, "the current holder"

    # And it participates in the combined check, so a caller that uses
    # ``check(candidate)`` gets the same answer.
    combined = grandchild.check("coder")
    assert combined.allowed is False
    assert combined.code == REFUSAL_CYCLE


def test_cycle_guard_stops_a_task_ping_ponging_between_two_agents(registry: BotRegistry, ledger) -> None:
    """alpha -> coder -> alpha must be refused, not looped."""
    root = get_leader_dispatcher("pingpong", registry=registry, limits=DelegationLimits(max_depth=5, max_fanout=5, max_hops=50))
    coder = root.for_child("coder")
    assert coder.context.lineage == (ALPHA_LEADER_NAME, "coder")

    back_to_leader = coder.dispatch("pingpong", "hand it back", required_capability_tags=["task_dispatch"])
    assert back_to_leader.refusal_code == REFUSAL_CYCLE
    assert "already holds this task" in back_to_leader.reason
    assert "alpha" in back_to_leader.reason
    # And it is recorded as a refusal, so "why did nothing happen" is answerable.
    assert any(e.reason == REFUSAL_CYCLE for e in _ledger_entries(ledger, "pingpong"))


def test_hop_ceiling_stops_an_endless_reassignment_chain(registry: BotRegistry, ledger) -> None:
    from alpha.bots.capability_dispatch import CapabilityDispatcher

    limits = DelegationLimits(max_depth=9, max_fanout=9, max_hops=3)
    context = root_context(ALPHA_LEADER_NAME, "hoppy", limits=limits)
    # alpha -> coder -> reviewer -> tester is three hops; the fourth is refused.
    node = CapabilityDispatcher(context, registry=registry)
    assert node.context.hops == 0
    for expected_hops, agent in enumerate(["coder", "reviewer", "tester"], start=1):
        node = node.for_child(agent)
        assert node.context.hops == expected_hops
    refused = node.dispatch("hoppy", "one more", required_capability_tags=["code_audit"])
    assert refused.refusal_code == REFUSAL_HOP_CEILING


def test_token_budget_is_inherited_downward_and_stops_the_tree(registry: BotRegistry, ledger) -> None:
    from dataclasses import replace

    from alpha.bots.capability_dispatch import CapabilityDispatcher

    limits = DelegationLimits(max_depth=6, max_fanout=6, max_hops=20, token_budget=1000, child_token_share=0.5)
    root = get_leader_dispatcher("budget", registry=registry, limits=limits)
    assert root.context.tokens_remaining == 1000
    child = root.for_child("coder")
    assert child.context.token_budget == 500
    grandchild = child.for_child("reviewer")
    assert grandchild.context.token_budget == 250

    # A child that reports it burned the whole inherited budget.
    spent = replace(child.context, tokens_spent=child.context.token_budget)
    exhausted = CapabilityDispatcher(spent, registry=registry)
    decision = exhausted.dispatch("budget-sub", "anything", required_capability_tags=["code_generation"])
    assert decision.refusal_code == REFUSAL_TOKEN_BUDGET


def test_a_child_can_never_receive_more_than_its_parent_had_left() -> None:
    from dataclasses import replace

    limits = DelegationLimits(token_budget=100, cost_budget_usd=1.0, child_token_share=1.0)
    parent = root_context("alpha", "t", limits=limits, token_budget=100)
    nearly_spent = replace(parent, tokens_spent=95)
    child = nearly_spent.for_child("coder")
    assert child.token_budget <= 5


def test_leader_authority_refuses_a_dispatch_outside_the_allowlist(registry: BotRegistry, ledger) -> None:
    dispatcher = get_leader_dispatcher("teleport", registry=registry)
    decision = dispatcher.dispatch("teleport", "teleport the database", required_capability_tags=["teleportation"])
    assert decision.refusal_code == "capability_missing"
    assert "authority boundary" in decision.reason
    assert decision.target is None


# ===========================================================================
# (e) A failing child is reported to the parent as FAILED, never as success
# ===========================================================================


def test_a_raising_child_is_reported_as_failed(registry: BotRegistry, ledger) -> None:
    from alpha.bots.delegation import DelegationTree

    parent = DelegationTree(root_context(ALPHA_LEADER_NAME, "boom"))
    context = parent.child_context("coder")

    def boom(_objective: str):
        raise RuntimeError("worker died mid-task")

    report = run_bounded("coder", "do the thing", boom, parent=parent, context=context, child_id="c1")

    assert report.status is ChildStatus.FAILED
    assert report.ok is False
    assert parent.aggregate_status() is TreeStatus.FAILED
    assert parent.is_settled is False
    assert "worker died mid-task" in parent.failure_detail


def test_a_child_reporting_an_error_shape_is_failed(registry: BotRegistry, ledger) -> None:
    report = child_report("c1", "coder", {"status": "error", "error": "capability_missing: no such tool sqlx"})
    assert report.status is ChildStatus.FAILED
    assert report.ok is False
    assert report.reason == "capability_missing"


def test_a_child_returning_nothing_is_unresolved_not_successful(registry: BotRegistry, ledger) -> None:
    """Absence of a result is not a result. 'We did not hear back' != 'it worked'."""
    report = child_report("c1", "coder", None)
    assert report.status is ChildStatus.UNKNOWN_OUTCOME
    assert report.ok is False


def test_a_child_whose_own_child_failed_is_not_a_success(registry: BotRegistry, ledger) -> None:
    """Invariant 1 propagates to arbitrary depth."""
    from alpha.bots.delegation import DelegationTree

    grandchild_tree = DelegationTree(root_context("coder", "nested"))
    grandchild_ctx = grandchild_tree.child_context("reviewer")

    def boom(_objective: str):
        raise RuntimeError("deep failure")

    run_bounded("reviewer", "nested work", boom, parent=grandchild_tree, context=grandchild_ctx, child_id="g1")
    assert grandchild_tree.aggregate_status() is TreeStatus.FAILED

    report = child_report("c1", "coder", {"status": "ok", "summary": "all good"}, descendants=grandchild_tree)
    assert report.status is ChildStatus.FAILED, "a success-shaped child result must not mask a failed descendant"
    assert report.ok is False
    assert "deep failure" in report.detail


def test_a_task_is_not_complete_while_a_descendant_is_unresolved(registry: BotRegistry, ledger) -> None:
    """Invariant 2: pending descendants block completion."""
    from alpha.bots.delegation import DelegationTree

    tree = DelegationTree(root_context(ALPHA_LEADER_NAME, "pending"))
    tree.admit(tree.child_context("coder"), child_report("c1", "coder", "did the work"))
    assert tree.aggregate_status() is TreeStatus.COMPLETE

    tree.admit(tree.child_context("tester"), child_report("c2", "tester", None))
    assert tree.aggregate_status() is TreeStatus.UNRESOLVED
    assert tree.is_settled is False
    assert [item["agent"] for item in tree.unresolved()] == ["tester"]


def test_an_empty_tree_is_pending_never_complete(registry: BotRegistry, ledger) -> None:
    from alpha.bots.delegation import DelegationTree

    tree = DelegationTree(root_context(ALPHA_LEADER_NAME, "empty"))
    assert tree.aggregate_status() is TreeStatus.PENDING
    assert tree.is_settled is False


def test_dispatcher_exposes_its_bounded_subtree(registry: BotRegistry, ledger) -> None:
    dispatcher = get_leader_dispatcher("sub", registry=registry)
    assert dispatcher.tree.aggregate_status() is TreeStatus.PENDING
    assert dispatcher.tree.context.task_id == "sub"


# ===========================================================================
# (f) Capability mismatch reassigns elsewhere; transient failure retries in place
# ===========================================================================


def test_a_capability_mismatch_reassigns_to_a_different_capable_agent(registry: BotRegistry, ledger) -> None:
    # Two capable agents, so a reassignment has somewhere to go.
    _add_peer(registry, "db-backup", ["sql", "data_modeling"])

    dispatcher = get_leader_dispatcher("task-reassign", registry=registry)
    # `coder` cannot do it, so it is not even a candidate.
    incapable = dispatcher.dispatch("task-reassign", "Migrate the reporting schema", required_capability_tags=["sql"], candidate_names=["coder"])
    assert incapable.refusal_code == "no_eligible_agent"
    assert incapable.rejected[0]["bot"] == "coder"

    capable = dispatcher.dispatch("task-reassign", "Migrate the reporting schema", required_capability_tags=["sql"], candidate_names=["data-analyst"], attempt=1)
    assert capable.target == "data-analyst"

    outcome = reassign_after_failure(
        "task-reassign",
        "data-analyst",
        "capability_missing: no such tool pg_dump on this agent",
        objective="Migrate the reporting schema",
        required_capability_tags=["sql"],
        dispatcher=dispatcher,
        attempt=1,
        max_attempts=3,
        registry=registry,
    )
    # A capability gap is a CAPABILITY class, not a bad attempt, so the work
    # moves rather than burning the attempt budget on the same agent.
    assert outcome.reason == "capability_missing"
    assert outcome.reason_class == "capability"
    assert outcome.action is ReassignmentAction.REASSIGN
    assert outcome.reassigned is True
    assert outcome.target == "db-backup"
    assert outcome.scope == REASSIGN_SCOPE_ELSEWHERE
    assert "cannot do this work" in outcome.detail
    assert outcome.decision is not None and outcome.decision.ok
    # And the hop is in the ledger with the typed reason.
    hops = [e for e in _ledger_entries(ledger, "task-reassign") if e.to_ref != "(none)"]
    assert any(e.reason == "capability_missing" and e.to_ref == "db-backup" for e in hops)


def test_a_transient_failure_retries_in_place(registry: BotRegistry, ledger) -> None:
    dispatcher = get_leader_dispatcher("task-transient", registry=registry)
    dispatcher.dispatch("task-transient", "Write release notes", required_capability_tags=["technical_writing"], attempt=1)

    outcome = reassign_after_failure(
        "task-transient",
        "technical-writer",
        "429 rate limit exceeded, please retry",
        objective="Write release notes",
        required_capability_tags=["technical_writing"],
        dispatcher=dispatcher,
        attempt=1,
        max_attempts=3,
        registry=registry,
    )
    assert outcome.reason == "provider_rate_limit"
    assert outcome.reason_class == "transient"
    assert outcome.action is ReassignmentAction.RETRY_IN_PLACE
    assert outcome.retried_in_place is True
    assert outcome.reassigned is False
    assert outcome.target == "technical-writer"
    assert outcome.scope == RETRY_SCOPE_IN_PLACE


def test_a_crash_resumes_in_place(registry: BotRegistry, ledger) -> None:
    dispatcher = get_leader_dispatcher("task-crash", registry=registry)
    outcome = reassign_after_failure(
        "task-crash",
        "coder",
        "worker died: lease expired",
        objective="Implement the parser",
        required_capability_tags=["code_generation"],
        dispatcher=dispatcher,
        attempt=1,
        max_attempts=3,
        registry=registry,
    )
    assert outcome.reason == "worker_crash"
    assert outcome.reason_class == "crash"
    assert outcome.action is ReassignmentAction.RESUME_IN_PLACE
    assert outcome.retried_in_place is True


def test_an_exhausted_attempt_budget_escalates_instead_of_retrying(registry: BotRegistry, ledger) -> None:
    dispatcher = get_leader_dispatcher("task-exhaust", registry=registry)
    outcome = reassign_after_failure(
        "task-exhaust",
        "coder",
        "429 rate limit exceeded",
        objective="Implement the parser",
        required_capability_tags=["code_generation"],
        dispatcher=dispatcher,
        attempt=3,
        max_attempts=3,
        registry=registry,
    )
    assert outcome.reason == "attempts_exhausted"
    assert outcome.reason_class == "exhausted"
    assert outcome.action is ReassignmentAction.ESCALATE
    assert outcome.target is None


def test_a_capability_gap_still_escalates_once_attempts_are_spent(registry: BotRegistry, ledger) -> None:
    """The attempt ceiling is authoritative, whatever the class says."""
    dispatcher = get_leader_dispatcher("task-cap-exhaust", registry=registry)
    outcome = reassign_after_failure(
        "task-cap-exhaust",
        "coder",
        "capability_missing: unknown capability",
        objective="Do something",
        required_capability_tags=["code_generation"],
        dispatcher=dispatcher,
        attempt=2,
        max_attempts=2,
        registry=registry,
    )
    assert outcome.action is ReassignmentAction.ESCALATE
    assert outcome.reason == "attempts_exhausted"


def test_a_permanent_failure_escalates_immediately(registry: BotRegistry, ledger) -> None:
    dispatcher = get_leader_dispatcher("task-auth", registry=registry)
    outcome = reassign_after_failure(
        "task-auth",
        "coder",
        "401 unauthorized: invalid api key",
        objective="Implement the parser",
        required_capability_tags=["code_generation"],
        dispatcher=dispatcher,
        attempt=1,
        max_attempts=5,
        registry=registry,
    )
    assert outcome.reason == "provider_auth_or_access"
    assert outcome.action is ReassignmentAction.ESCALATE


def test_reassignment_uses_the_existing_taxonomy_rather_than_a_new_one(registry: BotRegistry, ledger) -> None:
    """Phase 4 must IMPORT `failure_reasons`, not fork it."""
    import inspect

    import alpha.bots.reassignment as reassignment

    source = inspect.getsource(reassignment)
    assert "from alpha.bots.failure_reasons import" in source
    # It must not redefine the vocabulary.
    assert "REASON_CLASSES" not in source
    assert "AUTO_RETRYABLE =" not in source
    assert "def classify_work_failure" not in source
    assert "def failure_class" not in source
    assert "def decide_failure" not in source


def test_reassignment_never_returns_the_agent_that_just_failed(registry: BotRegistry, ledger) -> None:
    """A reassignment back to the failed agent would be a silent no-op."""
    _add_peer(registry, "audit-two", ["code_audit", "security_review"])
    dispatcher = get_leader_dispatcher("task-same", registry=registry)
    outcome = reassign_after_failure(
        "task-same",
        "reviewer",
        "capability_missing: lacks the skill",
        objective="Audit the code",
        required_capability_tags=["code_audit"],
        dispatcher=dispatcher,
        attempt=1,
        max_attempts=3,
        registry=registry,
    )
    assert outcome.target != "reviewer"
    assert outcome.reassigned is True


def test_reassignment_with_no_other_capable_agent_is_unroutable_not_a_silent_success(registry: BotRegistry, ledger) -> None:
    """`reviewer` is the only `code_audit` agent; a reassignment must say so."""
    dispatcher = get_leader_dispatcher("task-lonely", registry=registry)
    outcome = reassign_after_failure(
        "task-lonely",
        "reviewer",
        "capability_missing: lacks the skill",
        objective="Audit the code",
        required_capability_tags=["code_audit"],
        dispatcher=dispatcher,
        attempt=1,
        max_attempts=3,
        registry=registry,
    )
    assert outcome.action is ReassignmentAction.UNROUTABLE
    assert outcome.reassigned is False
    assert outcome.target is None
    assert "no capable successor" in outcome.detail
    assert outcome.decision is not None and not outcome.decision.ok


# ===========================================================================
# End-to-end: the production dispatch bridge
# ===========================================================================


def test_autonomous_dispatch_bridge_routes_by_capability(registry: BotRegistry, ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real bridge call path selects by capability and records the reason."""
    import alpha.planning.bridge as bridge_mod

    monkeypatch.setattr("alpha.bots.registry.get_bot_registry", lambda *a, **k: registry)
    monkeypatch.setattr("alpha.bots.handoff.get_bot_registry", lambda *a, **k: registry)

    from alpha.planning.meta_planner import ExecutionParadigm, MetaPlan, MetaPlanDecision

    plan = MetaPlan(
        plan_id="plan-bridge",
        prompt="Add a composite index to the orders table",
        decision=MetaPlanDecision(
            paradigm=ExecutionParadigm.BOT_PROFILE,
            swarm_mode=None,
            reasoning_tier="t",
            workforce_type="permanent_bot",
            # The planner proposes a MIS-MATCHED specialist. The old code handed
            # the work to specialists[0] verbatim.
            assigned_specialists=["coder"],
        ),
        markdown_report="# contract",
    )

    result = bridge_mod.AutonomousDispatchBridge.dispatch(plan)

    assert result.status == "dispatched", result.summary
    assert result.details["lead_bot"] == "data-analyst", result.details["dispatch"]["reason"]
    assert result.details["proposed_bots"] == ["coder"]
    assert "sql" in result.details["dispatch"]["required_capability_tags"]
    assert "sql" in result.summary
    entries = _ledger_entries(ledger, "plan-bridge")
    assert entries and entries[-1].to_ref == "data-analyst"
    assert "sql" in entries[-1].reason


def test_autonomous_dispatch_bridge_reports_a_refusal_honestly(registry: BotRegistry, ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    """No capable agent must be a FAILED dispatch, not a hand-off to anyone."""
    import alpha.planning.bridge as bridge_mod

    monkeypatch.setattr("alpha.bots.registry.get_bot_registry", lambda *a, **k: registry)
    monkeypatch.setattr("alpha.bots.handoff.get_bot_registry", lambda *a, **k: registry)

    from alpha.planning.meta_planner import ExecutionParadigm, MetaPlan, MetaPlanDecision

    plan = MetaPlan(
        plan_id="plan-nowhere",
        prompt="Fix the production outage and write the postmortem",
        decision=MetaPlanDecision(
            paradigm=ExecutionParadigm.BOT_PROFILE,
            swarm_mode=None,
            reasoning_tier="t",
            workforce_type="permanent_bot",
            assigned_specialists=["sourdough-chef"],
        ),
        markdown_report="# contract",
    )

    result = bridge_mod.AutonomousDispatchBridge.dispatch(plan)

    assert result.status == "failed"
    assert result.details["lead_bot"] is None
    # The proposed owner is not on the roster, so the task text decides the
    # requirement — and "outage" is a capability no seeded bot declares.
    assert result.details["dispatch"]["refusal_code"] == "no_eligible_agent"
    assert "Capability dispatch refused" in result.summary


def test_roster_json_on_disk_carries_the_leader(registry: BotRegistry) -> None:
    """The default leader is persisted, so a restart keeps it."""
    data = json.loads(Path(registry.storage_path).read_text(encoding="utf-8"))
    names = {bot["name"] for bot in data["bots"]}
    assert ALPHA_LEADER_NAME in names
    leader = next(bot for bot in data["bots"] if bot["name"] == ALPHA_LEADER_NAME)
    assert leader["soul"] == ALPHA_LEADER_SOUL
    assert leader["metadata"]["authority"] == "propose_dispatch_recall"
    assert leader["metadata"]["directable_capabilities"] == sorted(DIRECTABLE_CAPABILITIES)


def test_a_reloaded_roster_still_has_the_leader(tmp_path: Path) -> None:
    path = tmp_path / "roster.json"
    BotRegistry(storage_path=path)
    reloaded = BotRegistry(storage_path=path)
    assert reloaded.get_bot(ALPHA_LEADER_NAME) is not None
