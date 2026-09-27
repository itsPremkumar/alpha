"""The `swarm` tool's strategy, telemetry, and trace actions.

These are new *actions* on the existing tool rather than new tools, so
`BUILTIN_TOOLS` and the capability counts stay put.  What is pinned here is the
shape of each reply:

* ``strategy`` reports the choice the plan actually ran under — never a fresh
  re-derivation that could disagree with the recorded one — and degrades to an
  explicit "not recorded" for a plan that predates automatic selection;
* ``telemetry`` carries the signal set with its basis, so a caller can see what
  a number was derived from;
* ``trace`` reads and writes advisory context only: a deposit is bounded and
  owner-scoped, and an empty list says so rather than looking like an error.
"""

from __future__ import annotations

import json
import re
import typing

import pytest

import alpha.swarm.coordinator as coord_mod
from alpha.swarm.models import SwarmPlan
from alpha.tools.builtins.swarm_tool import swarm_tool


@pytest.fixture(autouse=True)
def _isolated_coordinator(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    coord_mod._GLOBAL_COORDINATOR = None
    yield
    coord_mod._GLOBAL_COORDINATOR = None


def _spawn(goal: str = "Compare React vs Vue for an enterprise dashboard", **kwargs) -> str:
    """Spawn through the tool and return the swarm id it reports."""

    response = swarm_tool.invoke({"action": "spawn", "goal": goal, **kwargs})
    assert "Autonomous Swarm Successfully Spawned" in response, response
    match = re.search(r"Swarm ID\*\*:\s*`([^`]+)`", response)
    assert match is not None, response
    return match.group(1)


def test_strategy_reports_the_recorded_choice_not_a_recomputation():
    sid = _spawn(mode="auto")
    raw = swarm_tool.invoke({"action": "strategy", "swarm_id": sid})
    payload = json.loads(raw)

    assert payload["swarm_id"] == sid
    # The declared mode is what the caller asked for; the effective mode is
    # what automatic selection resolved it to.  They are separate fields so an
    # `auto` request is never reported as having been run verbatim.
    assert payload["declared_mode"] == "auto"
    assert payload["effective_mode"] in {"debate", "ensemble", "map_reduce", "scatter_gather", "hierarchical", "parallel", "coding_worktree"}
    assert payload["tier"] in {"direct", "simple", "adapt", "medium", "full"}
    assert payload["source"] in {"explicit", "topology", "heuristic", "fallback"}
    assert isinstance(payload["candidates"], list)
    assert payload["rationale"]


def test_strategy_honors_an_explicit_mode_without_reinterpreting_it():
    sid = _spawn(mode="debate")
    payload = json.loads(swarm_tool.invoke({"action": "strategy", "swarm_id": sid}))

    # An explicit topology is a requirement, so the resolver must not move it.
    assert payload["declared_mode"] == "debate"
    assert payload["effective_mode"] == "debate"
    assert payload["source"] == "explicit"


def test_strategy_reports_nothing_recorded_instead_of_inventing_one():
    coordinator = coord_mod.get_swarm_coordinator()
    sid = "swm-unrecorded"
    # A plan that never went through automatic selection (an imported or
    # pre-upgrade checkpoint) must not be given a strategy it does not have.
    coordinator._swarms[sid] = SwarmPlan(swarm_id=sid, goal="legacy plan")

    response = swarm_tool.invoke({"action": "strategy", "swarm_id": sid})
    assert "**Recorded**: `no`" in response
    assert "**Declared mode**: `auto`" in response


def test_strategy_action_requires_a_swarm_id():
    assert swarm_tool.invoke({"action": "strategy"}) == "Error: 'swarm_id' parameter is required."


def test_unknown_swarm_fails_closed_for_the_new_actions():
    for action in ("strategy", "telemetry", "trace", "leader"):
        response = swarm_tool.invoke({"action": action, "swarm_id": "swm-missing"})
        assert "not found or is not visible" in response, (action, response)


def test_leader_action_requires_a_swarm_id_instead_of_crashing():
    # `leader` dereferences the plan, so it belongs in the swarm_id guard set.
    assert swarm_tool.invoke({"action": "leader"}) == "Error: 'swarm_id' parameter is required."


def test_telemetry_action_surfaces_signals_with_their_basis():
    sid = _spawn()
    response = swarm_tool.invoke({"action": "telemetry", "swarm_id": sid})

    assert f"### Swarm Telemetry: `{sid}`" in response
    assert "**Risk signals**:" in response
    assert "**Basis**: derived from persisted plan state; no model call and no estimated token conversion" in response
    # The full signal set follows as a fenced JSON block.
    block = response.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    payload = json.loads(block)
    assert set(payload) >= {"budget_pressure", "loop_indicator", "tool_instability", "information_gain", "coordination_overhead", "risk_signals"}
    assert payload["risk_signals"] == []


def test_trace_action_deposits_then_ranks():
    sid = _spawn()

    deposit = swarm_tool.invoke(
        {
            "action": "trace",
            "swarm_id": sid,
            "topic": "port-map",
            "message": "gateway listens on 8001",
            "task_id": "worker-a",
        }
    )
    assert "Trace deposited" in deposit, deposit
    assert "at strength `1.0`" in deposit

    listed = swarm_tool.invoke({"action": "trace", "swarm_id": sid})
    traces = json.loads(listed)
    assert len(traces) == 1
    assert traces[0]["key"] == "port-map"
    assert traces[0]["payload"] == "gateway listens on 8001"
    assert traces[0]["provenance"] == ["worker-a"]


def test_trace_deposit_from_a_second_worker_corroborates():
    sid = _spawn()
    for worker in ("worker-a", "worker-b"):
        swarm_tool.invoke({"action": "trace", "swarm_id": sid, "topic": "cache", "message": "use ./tmp", "task_id": worker})

    traces = json.loads(swarm_tool.invoke({"action": "trace", "swarm_id": sid}))
    assert len(traces) == 1
    # Two independent witnesses, sub-linear: 1 + 0.5 = 1.5, never 2.0.
    assert traces[0]["strength"] == pytest.approx(1.5)
    assert traces[0]["deposits"] == 2
    assert traces[0]["provenance"] == ["worker-a", "worker-b"]


def test_trace_listing_reports_empty_honestly():
    sid = _spawn()
    response = swarm_tool.invoke({"action": "trace", "swarm_id": sid})
    assert "No stigmergic traces recorded" in response
    assert "never gate a decision" in response


def test_trace_deposit_rejects_an_empty_key():
    sid = _spawn()
    response = swarm_tool.invoke({"action": "trace", "swarm_id": sid, "topic": "  ", "message": "payload"})
    assert response.startswith("Error: trace rejected:")


def test_trace_listing_can_filter_by_category():
    sid = _spawn()
    swarm_tool.invoke({"action": "trace", "swarm_id": sid, "topic": "a", "message": "x", "reason": "dead_end"})
    swarm_tool.invoke({"action": "trace", "swarm_id": sid, "topic": "b", "message": "y", "reason": "discovery"})

    only_dead_ends = json.loads(swarm_tool.invoke({"action": "trace", "swarm_id": sid, "reason": "dead_end"}))
    assert [trace["key"] for trace in only_dead_ends] == ["a"]
    assert all(trace["category"] == "dead_end" for trace in only_dead_ends)

    everything = json.loads(swarm_tool.invoke({"action": "trace", "swarm_id": sid}))
    assert {trace["key"] for trace in everything} == {"a", "b"}


def test_every_declared_action_is_implemented_by_the_dispatcher():
    """The `Literal` and the dispatcher must not drift apart.

    A declared action nobody handles falls through to "Unknown action", so the
    two lists are checked against each other rather than trusted.
    """

    hints = typing.get_type_hints(swarm_tool.func)
    declared = set(typing.get_args(hints["action"]))
    assert {"strategy", "telemetry", "trace"} <= declared

    sid = _spawn()
    for action in sorted(declared):
        response = swarm_tool.invoke({"action": action, "swarm_id": sid, "goal": "some goal", "items_json": '["one"]'})
        assert f"Unknown action '{action}'" not in response, (action, response)
