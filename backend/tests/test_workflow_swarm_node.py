"""SWARM node: the swarm paradigm inside the DWE graph model (research item 2).

The deep-research matrix refused ``swarm`` because a static fan-out would
misrepresent a dynamic swarm. This suite pins the construct that replaced the
refusal:

- **aggregation policy** — ``FIRST_SUCCESS`` / ``QUORUM`` / ``ALL`` / ``ANY``
  decide the verdict, and the verdict is FROZEN once decided (a straggler
  landing late updates the measured totals but never flips a decision the
  caller already acted on);
- **real cost ledger** — per-member tokens/cost come from the runner's own
  result; an unpriced swarm reports ``total_cost_usd: None``, never a
  reassuring ``0.0``;
- **honest unbound state** — no member runner means every member fails with
  the real reason; no member result is ever fabricated;
- **engine integration** — ``_handle_swarm`` on the structural path charges
  member tokens through the run budget gate (the one path must not spend
  outside the budget) and journals the ledger into ``run.state``.
"""

from __future__ import annotations

import threading

import pytest

import alpha.workflow.runtime as runtime_module
from alpha.workflow.models import (
    NodeType,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
)
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.swarm.execution import (
    MAX_SWARM_CONCURRENCY,
    SwarmConfig,
    execute_swarm_node,
)
from alpha.workflow.swarm.ledger import (
    AggregationPolicy,
    SwarmLedger,
    SwarmMember,
)


def _ledger(policy: AggregationPolicy, quorum: int = 1, *member_ids: str) -> SwarmLedger:
    ledger = SwarmLedger(aggregation=policy, quorum=quorum)
    for member_id in member_ids or ("a", "b", "c"):
        ledger.add_member(SwarmMember(member_id=member_id))
    return ledger


# ------------------------------------------------------------- ledger policies


def test_first_success_completes_and_skips_the_pending_members():
    ledger = _ledger(AggregationPolicy.FIRST_SUCCESS, 1, "a", "b", "c")

    ledger.complete_member("a", output="done", tokens_used=5)

    assert ledger.should_stop() is True
    state = ledger.state
    assert state.completed is True and state.succeeded is True
    assert state.member("b").status.value == "skipped"
    assert state.member("c").status.value == "skipped"
    assert "before this member was needed" in state.member("b").failure_reason


def test_first_success_never_completes_succeeding_when_every_member_fails():
    ledger = _ledger(AggregationPolicy.FIRST_SUCCESS, 1, "a", "b")

    ledger.fail_member("a", "boom")
    assert ledger.state.completed is False  # b is still pending

    ledger.fail_member("b", "boom")
    assert ledger.state.completed is True
    assert ledger.state.succeeded is False


def test_quorum_waits_for_the_declared_count_then_skips_the_rest():
    ledger = _ledger(AggregationPolicy.QUORUM, 2, "a", "b", "c")

    ledger.complete_member("a")
    assert ledger.state.completed is False  # 1 of the 2 needed

    ledger.complete_member("b")
    assert ledger.state.completed is True
    assert ledger.state.succeeded is True
    assert ledger.state.member("c").status.value == "skipped"


def test_quorum_below_the_declared_count_is_a_failure_not_a_timeout():
    ledger = _ledger(AggregationPolicy.QUORUM, 3, "a", "b", "c")

    ledger.complete_member("a")
    ledger.fail_member("b", "boom")
    ledger.complete_member("c")

    assert ledger.state.completed is True
    assert ledger.state.succeeded is False  # 2 < 3 once everybody finished


def test_all_needs_every_member_and_one_failure_fails_the_swarm():
    ledger = _ledger(AggregationPolicy.ALL, 1, "a", "b", "c")

    ledger.complete_member("a")
    ledger.complete_member("b")
    assert ledger.state.completed is False  # c still pending

    ledger.fail_member("c", "boom")
    assert ledger.state.completed is True
    assert ledger.state.succeeded is False


def test_any_takes_the_first_finished_outcome_even_a_failure():
    ledger = _ledger(AggregationPolicy.ANY, 1, "a", "b")

    ledger.fail_member("a", "boom")

    # Decided immediately by the first finished member — ANY is not a slow ALL.
    assert ledger.state.completed is True
    assert ledger.state.succeeded is False
    assert ledger.state.member("b").status.value == "skipped"


def test_a_straggler_cannot_flip_a_decided_verdict_but_still_costs_money():
    """The frozen-verdict invariant: totals update, the decision does not."""
    ledger = _ledger(AggregationPolicy.ANY, 1, "a", "b")

    ledger.fail_member("a", "boom")
    assert ledger.state.succeeded is False

    ledger.complete_member("b", output="late but real", tokens_used=5, cost_usd=0.25)

    assert ledger.state.completed is True
    assert ledger.state.succeeded is False, "a late success must not retroactively succeed a failed swarm"
    # Its spend was real, so the measured totals still carry it.
    assert ledger.state.total_tokens == 5
    assert ledger.state.total_cost_usd == 0.25


def test_cost_totals_are_measured_and_none_when_unpriced():
    priced = _ledger(AggregationPolicy.ALL, 1, "a", "b")
    priced.complete_member("a", tokens_used=10, cost_usd=0.5)
    priced.complete_member("b", tokens_used=5)  # priced nothing: honest None contribution
    assert priced.state.total_tokens == 15
    assert priced.state.total_cost_usd == 0.5

    unpriced = _ledger(AggregationPolicy.ALL, 1, "a")
    unpriced.complete_member("a", tokens_used=7)
    assert unpriced.state.total_cost_usd is None, "no member reported a price — None, never 0.0"


def test_skip_never_erases_an_already_finished_member():
    ledger = _ledger(AggregationPolicy.ALL, 1, "a")
    ledger.complete_member("a", output="done")

    ledger.skip_member("a", "would have erased the result")

    assert ledger.state.member("a").status.value == "succeeded"
    assert ledger.state.member("a").output == "done"


# ------------------------------------------------------------ config parsing


def test_swarm_config_parses_members_policy_and_clamps_the_pool():
    node = WorkflowNode(
        id="sw",
        type=NodeType.SWARM,
        config={
            "members": [{"id": "a", "prompt": "p"}, {"id": "b"}],
            "aggregation": "quorum",
            "quorum": "2",
            "max_concurrency": 999,
        },
    )
    cfg = SwarmConfig.from_node(node)

    assert cfg.members == [{"id": "a", "prompt": "p"}, {"id": "b"}]
    assert cfg.aggregation is AggregationPolicy.QUORUM
    assert cfg.quorum == 2
    assert cfg.max_concurrency == MAX_SWARM_CONCURRENCY, "a typo'd pool size is clamped, not a thread bomb"


def test_swarm_config_falls_back_rather_than_raising_mid_run():
    node = WorkflowNode(
        id="sw",
        type=NodeType.SWARM,
        config={
            "members": ["not-a-dict", {"id": "a"}, 7],
            "aggregation": "vibes",
            "quorum": "two",
            "max_concurrency": "lots",
        },
    )
    cfg = SwarmConfig.from_node(node)

    assert cfg.members == [{"id": "a"}], "malformed member entries are dropped, not executed"
    assert cfg.aggregation is AggregationPolicy.ALL, "unknown policy falls back to wait-for-everyone"
    assert cfg.quorum == 1
    assert cfg.max_concurrency == 4


# --------------------------------------------------------------- execution


def _node(members: list[dict], **config) -> WorkflowNode:
    return WorkflowNode(id="sw", type=NodeType.SWARM, prompt="fan out", config={"members": members, **config})


def _run() -> object:
    return object()  # execute_swarm_node only threads it through as a call seam


def test_a_swarm_without_members_is_refused_not_fabricated():
    outcome = execute_swarm_node(_node([]), _run(), SwarmConfig.from_node(_node([])), lambda *_: {})

    assert outcome.succeeded is False
    assert outcome.reason == "swarm node has no members configured"
    assert outcome.ledger == {}


def test_an_unbound_runner_fails_every_member_with_the_real_reason():
    node = _node([{"id": "a"}, {"id": "b"}, {"id": "c"}], aggregation="all")
    cfg = SwarmConfig.from_node(node)

    outcome = execute_swarm_node(node, _run(), cfg, None)

    assert outcome.succeeded is False
    joined = " | ".join(outcome.evidence)
    assert "no swarm member runner is bound" in joined
    assert all(m["status"] == "failed" for m in outcome.ledger["members"])
    assert outcome.reason.startswith("swarm aggregation all not satisfied")


def test_first_success_stops_dispatching_after_the_first_win():
    """Sequential dispatch (max_concurrency=1): the runner is called ONCE."""
    node = _node([{"id": "a"}, {"id": "b"}, {"id": "c"}], aggregation="first_success", max_concurrency=1)
    cfg = SwarmConfig.from_node(node)
    calls: list[str] = []

    def runner(member_id: str, prompt: str) -> dict:
        calls.append(member_id)
        return {"status": "completed", "output": f"done {member_id}"}

    outcome = execute_swarm_node(node, _run(), cfg, runner)

    assert outcome.succeeded is True
    assert calls == ["a"], "FIRST_SUCCESS must not keep spending after the win"
    statuses = {m["member_id"]: m["status"] for m in outcome.ledger["members"]}
    assert statuses == {"a": "succeeded", "b": "skipped", "c": "skipped"}


def test_a_runner_exception_is_a_member_failure_not_a_crash():
    node = _node([{"id": "boom"}, {"id": "ok"}], aggregation="all", max_concurrency=1)
    cfg = SwarmConfig.from_node(node)

    def runner(member_id: str, prompt: str) -> dict:
        if member_id == "boom":
            raise RuntimeError("kaboom")
        return {"status": "completed", "output": "fine"}

    outcome = execute_swarm_node(node, _run(), cfg, runner)

    assert outcome.succeeded is False
    joined = " | ".join(outcome.evidence)
    assert "member boom failed" in joined
    assert "RuntimeError: kaboom" in joined, "the failure carries the exception text, not an opaque failed"
    assert any(m["member_id"] == "ok" and m["status"] == "succeeded" for m in outcome.ledger["members"])


def test_retry_budget_is_respected_then_reported_as_exhaustion():
    node = _node([{"id": "flaky", "max_attempts": 3}], aggregation="all", max_concurrency=1)
    cfg = SwarmConfig.from_node(node)
    attempts: list[str] = []

    def flaky(member_id: str, prompt: str) -> dict:
        attempts.append(member_id)
        if len(attempts) < 3:
            return {"status": "failed", "output": "transient"}
        return {"status": "completed", "output": "eventually"}

    outcome = execute_swarm_node(node, _run(), cfg, flaky)

    assert outcome.succeeded is True
    assert len(attempts) == 3
    assert outcome.ledger["members"][0]["attempts"] == 3

    # And the same budget honestly reports exhaustion when it never succeeds.
    dead = _node([{"id": "dead", "max_attempts": 2}], aggregation="all", max_concurrency=1)
    spent: list[str] = []

    def never(member_id: str, prompt: str) -> dict:
        spent.append(member_id)
        return {"status": "failed", "output": "still broken"}

    outcome = execute_swarm_node(dead, _run(), SwarmConfig.from_node(dead), never)
    assert outcome.succeeded is False
    assert len(spent) == 2
    assert "exhausted 2 attempt(s): still broken" in " | ".join(outcome.evidence)


def test_the_cost_ledger_accumulates_real_runner_usage():
    node = _node([{"id": "a"}, {"id": "b"}, {"id": "c"}], aggregation="all", max_concurrency=1)
    cfg = SwarmConfig.from_node(node)

    def runner(member_id: str, prompt: str) -> dict:
        if member_id == "a":
            return {"status": "completed", "output": "a", "tokens_used": 10, "cost_usd": 0.5}
        if member_id == "b":
            return {"status": "completed", "output": "b", "tokens_used": 5, "evidence": ["b: measured"]}
        return {"status": "completed", "output": "c", "tokens_used": 1, "cost_usd": 0.25}

    outcome = execute_swarm_node(node, _run(), cfg, runner)

    assert outcome.succeeded is True
    assert outcome.ledger["total_tokens"] == 16
    assert outcome.ledger["total_cost_usd"] == 0.75
    assert sorted(outcome.outputs) == ["a", "b", "c"]
    assert "b: measured" in outcome.evidence


# --------------------------------------------------------- engine integration


def _swarm_engine(name: str, members: list[dict], *, budget: int | None = None, **config):
    dwe = DynamicWorkflowEngine()
    node = _node(members, **config)
    graph = WorkflowGraph(version=1, nodes={"sw": node}, edges=[])
    dwe.register_definition(WorkflowDefinition(id=name, name=name, graph=graph, budget=budget))
    run = dwe.start_run(name)
    return dwe, node, run


def test_swarm_node_succeeds_through_the_engine_at_quorum():
    dwe, node, run = _swarm_engine(
        "wf_swarm_quorum",
        [{"id": "alpha", "prompt": "a"}, {"id": "beta", "prompt": "b"}, {"id": "gamma", "prompt": "c"}],
        aggregation="quorum",
        quorum=2,
    )
    calls: list[str] = []
    lock = threading.Lock()

    def runner(member_node, run):
        with lock:
            calls.append(member_node.id)
        if member_node.id.endswith(":beta"):
            return {"status": "failed", "output": "boom"}
        # The member inherits the swarm's executor but its config is EMPTY —
        # a runner re-reading config must not see the member list.
        assert member_node.config == {}
        return {
            "status": "completed",
            "output": f"done {member_node.id}",
            "evidence": [f"member {member_node.id} ok"],
            "tokens_used": 3,
        }

    dwe.execute_step(run.run_id, node_runner=runner)
    run = dwe.get_run(run.run_id)

    assert node.status.value == "succeeded"
    assert sorted(calls) == ["sw:alpha", "sw:beta", "sw:gamma"]
    ledger = run.state["sw_ledger"]
    assert ledger["completed"] is True and ledger["succeeded"] is True
    assert ledger["total_tokens"] == 6
    assert run.state["sw_result"] == ["done sw:alpha", "done sw:gamma"]
    joined = " | ".join(node.evidence)
    assert "swarm quorum: 2/3 members succeeded" in joined
    assert "member beta failed: exhausted 1 attempt(s): boom" in joined
    assert "member sw:alpha ok" in joined


def test_swarm_without_a_runner_fails_honestly_through_the_engine(monkeypatch):
    monkeypatch.setattr(runtime_module, "_NODE_RUNNER", None)  # no module seam fallback
    dwe, node, run = _swarm_engine(
        "wf_swarm_unbound",
        [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        aggregation="all",
    )

    dwe.execute_step(run.run_id)  # no node_runner at all
    run = dwe.get_run(run.run_id)

    assert node.status.value == "failed"
    assert "sw" in run.failed_nodes
    joined = " | ".join(node.evidence)
    assert "no swarm member runner is bound" in joined
    assert "swarm all: 0/3 members succeeded" in joined


def test_swarm_member_tokens_are_charged_through_the_run_budget_gate():
    dwe, node, run = _swarm_engine(
        "wf_swarm_budget",
        [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        aggregation="all",
        max_concurrency=1,
        budget=5,
    )

    def runner(member_node, member_run):
        return {"status": "completed", "output": "x", "tokens_used": 100}

    dwe.execute_step(run.run_id, node_runner=runner)
    run = dwe.get_run(run.run_id)

    # 3 members x 100 tokens vs a budget of 5: a swarm must not be the one
    # execution path that spends outside the run's budget.
    assert run.tokens_consumed == 300
    assert node.status.value == "failed"
    assert "sw" in run.failed_nodes
    reasons = [str(e.payload.get("reason", "")) for e in dwe.events.get_events(run.run_id)]
    assert any("Workflow token budget exhausted." in reason for reason in reasons)


def test_swarm_node_is_expressible_by_the_mode_mapper():
    """The paradigm maps to a real SWARM node — the old refusal is gone."""
    from alpha.orchestrator.mode_mapper import (
        EXPRESSIBLE_PARADIGMS,
        NON_EXPRESSIBLE_REASONS,
        build_paradigm_definition,
    )

    assert "swarm" in EXPRESSIBLE_PARADIGMS
    assert "swarm" not in NON_EXPRESSIBLE_REASONS

    mapping = build_paradigm_definition("swarm", mode="normal", prompt="go wild")
    assert mapping.expressible is True
    assert mapping.node_kinds == ("swarm",)
    assert mapping.archetype == "swarm"
    node = mapping.definition.graph.nodes["swarm"]
    assert node.type == NodeType.SWARM
    assert len(node.config["members"]) >= 2


@pytest.mark.parametrize("policy", list(AggregationPolicy))
def test_every_policy_has_a_deterministic_terminal_state(policy):
    """No policy can leave a finished swarm undecided — a run must not hang."""
    ledger = _ledger(policy, 2, "a", "b", "c")
    ledger.fail_member("a", "boom")
    ledger.fail_member("b", "boom")
    ledger.fail_member("c", "boom")

    assert ledger.state.completed is True
    assert ledger.state.succeeded is False
