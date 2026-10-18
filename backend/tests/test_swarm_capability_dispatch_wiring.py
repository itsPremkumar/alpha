"""1.9: swarm team composition assigns through the capability dispatcher.

The bar these tests hold, in the plan's own words: ``dispatch_task`` has a
**production caller**, and the swarm **no longer re-implements capability
matching**. Every test here fails against the pre-1.9 code -- ``team.py``
matched capabilities itself with ``match_capabilities``/``eligible_candidates``
and never touched the dispatcher -- and the two-tier semantics (a declared
requirement is strict, an inferred one is a ranked hint with a hard floor) are
asserted against the shared implementation the dispatcher actually runs.
"""

from __future__ import annotations

import pytest

from alpha.capabilities.eligibility import eligible_candidates
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode
from alpha.swarm.team import SpecialistProfile, assign_specialists, unregister_roster_provider


@pytest.fixture(autouse=True)
def _no_global_roster():
    """Never inherit a roster from another test: the registry is process-wide."""

    unregister_roster_provider()
    yield
    unregister_roster_provider()


@pytest.fixture
def roster():
    return [
        SpecialistProfile(name="researcher", role="Investigator", capabilities=["web_search", "technical_writing"]),
        SpecialistProfile(name="architect", role="System designer", capabilities=["system_design", "data_modeling"]),
        SpecialistProfile(name="coder", role="Implementer", capabilities=["code_generation", "python", "sql"]),
        SpecialistProfile(name="scribe", role="Documenter", capabilities=["technical_writing", "document_synthesis"]),
    ]


class _Entry:
    """Minimal ``BotRegistry``-shaped entry for direct dispatcher tests."""

    def __init__(self, profile: SpecialistProfile) -> None:
        self.name = profile.name
        self.display_name = profile.name
        self.status = profile.status
        self.role = profile.role
        self.department = "specialists"
        self.avatar = ""
        self.responsibilities: list[str] = []
        self.reputation_score = None
        self.capabilities = list(profile.capabilities)
        self.skills = list(profile.skills)


class _Registry:
    def __init__(self, profiles: list[SpecialistProfile]) -> None:
        self._entries = [_Entry(profile) for profile in profiles]

    def list_bots(self, include_archived: bool = False):
        return list(self._entries)

    def get_bot(self, name: str):
        return next((entry for entry in self._entries if entry.name.lower() == str(name or "").lower()), None)


def _plan(*tasks: SwarmTaskNode) -> SwarmPlan:
    plan = SwarmPlan(swarm_id="swm-wiring", goal="g", mode=SwarmMode.PARALLEL)
    for task in tasks:
        plan.tasks[task.task_id] = task
    return plan


def test_composition_calls_dispatch_task_once_per_task(roster, monkeypatch):
    """The production caller, measured: one dispatch call per task, with the
    roster, the caps and the two-tier requirement mode travelling with it."""

    import alpha.bots.capability_dispatch as dispatcher_module

    calls: list[dict[str, object]] = []
    real_dispatch = dispatcher_module.dispatch_task

    def _recording_dispatch(task_id, objective, **kwargs):
        calls.append({"task_id": task_id, "objective": objective, **kwargs})
        return real_dispatch(task_id, objective, **kwargs)

    monkeypatch.setattr(dispatcher_module, "dispatch_task", _recording_dispatch)

    plan = _plan(
        SwarmTaskNode(task_id="t1", objective="Implement a python service endpoint"),
        SwarmTaskNode(task_id="t2", objective="x", capability_tags=["sql", "react", "docker"]),
    )
    assignment = assign_specialists(plan, roster)

    assert [call["task_id"] for call in calls] == ["t1", "t2"], "every task goes through ONE dispatch call"
    inferred, declared = calls
    assert inferred["requirement_mode"] == "hint", "an inferred requirement is a ranked hint"
    assert declared["requirement_mode"] == "strict", "a declared requirement is strict"
    assert all(call["enforce_authority"] is False for call in calls), "an operator roster is not bounded by the leader's directable allowlist"
    assert all(call["registry"] is not None and hasattr(call["registry"], "list_bots") for call in calls), "the dispatcher runs over a BotRegistry-shaped view of the roster"
    assert inferred["required_capability_tags"] == ["backend", "code_generation", "python"]
    assert declared["required_capability_tags"] == ["docker", "react", "sql"]

    decisions = {item["task_id"]: item for item in assignment.decisions}
    assert decisions["t1"]["specialist"] == "coder"
    assert assignment.unassigned_count == 1 and assignment.unassigned[0]["task_id"] == "t2"
    # The refusal carries the dispatcher's own record, so the composition
    # reason is traceable to the dispatch attempt instead of being asserted.
    assert assignment.unassigned[0]["dispatch"]["refusal_code"] == "no_eligible_agent"
    assert assignment.unassigned[0]["dispatch"]["requirement_mode"] == "strict"
    assert decisions["t1"]["dispatch"]["outcome"] == "dispatched"
    assert decisions["t1"]["dispatch"]["requirement_mode"] == "hint"


def test_a_capped_specialist_is_excluded_not_dispatched_away(roster, monkeypatch):
    """The cap stays composition's business: it is passed to the dispatcher as
    an exclusion, so no second capability matcher has to re-filter around it."""

    import alpha.bots.capability_dispatch as dispatcher_module

    excludes: list[list[str]] = []
    real_dispatch = dispatcher_module.dispatch_task

    def _recording_dispatch(task_id, objective, **kwargs):
        excludes.append(list(kwargs.get("exclude") or []))
        return real_dispatch(task_id, objective, **kwargs)

    monkeypatch.setattr(dispatcher_module, "dispatch_task", _recording_dispatch)

    plan = _plan(*[SwarmTaskNode(task_id=f"t{index}", objective="Summarise the findings", capability_tags=["document_synthesis"]) for index in range(3)])
    assignment = assign_specialists(plan, roster, max_tasks_per_specialist=1)

    assert assignment.assigned_count == 1
    assert excludes == [[], ["scribe"], ["scribe"]], "the capped specialist travels as an exclusion"
    assert "cap" in assignment.unassigned[0]["reason"]
    assert assignment.unassigned[0]["capped_out"] == [{"name": "scribe", "cap": 1, "assigned": 1}]
    assert assignment.unassigned[0]["dispatch"]["refusal_code"] == "no_eligible_agent"


def test_the_hint_floor_keeps_partial_coverage_eligible():
    """The additive floor: strict needs every tag, the hint tier needs one, and
    covering none is still a refusal in both."""

    partial = SpecialistProfile(name="coder", capabilities=["code_generation", "python"])
    none = SpecialistProfile(name="tester", capabilities=["pytest"])

    strict = eligible_candidates([partial], ["backend", "code_generation"])
    hinted = eligible_candidates([partial], ["backend", "code_generation"], min_matches=1)
    hinted_zero = eligible_candidates([none], ["backend", "code_generation"], min_matches=1)

    assert strict.eligible == [], "a declared requirement keeps the strict conjunction"
    assert strict.rejected[0]["detail"].startswith("missing capability tags")
    assert hinted.eligible == ["coder"], "covering one hint keeps a candidate"
    assert hinted.rejected == []
    assert hinted_zero.eligible == [], "the floor is a floor, not a free pass"
    assert hinted_zero.rejected[0]["code"] == "capability_mismatch"


def test_dispatch_in_hint_mode_records_the_mode_and_the_matched_tags(roster):
    """The dispatcher's own record says which rule ran, and a partial match is
    reported as partial -- never as full coverage."""

    from alpha.bots.capability_dispatch import dispatch_task

    decision = dispatch_task(
        "t-hint",
        "Implement a python service endpoint",
        required_capability_tags=["backend", "code_generation", "python"],
        registry=_Registry(roster),
        requirement_mode="hint",
    )

    assert decision.ok and decision.target == "coder"
    assert decision.requirement_mode == "hint"
    assert decision.to_dict()["requirement_mode"] == "hint"
    assert set(decision.matched_capability_tags) == {"code_generation", "python"}
    assert decision.missing_capability_tags == ("backend",), "the unmatched hint stays visible in the decision"


def test_dispatch_rejects_an_unknown_requirement_mode(roster):
    from alpha.bots.capability_dispatch import dispatch_task

    with pytest.raises(ValueError, match="requirement_mode"):
        dispatch_task("t-bad-mode", "x", required_capability_tags=["python"], registry=_Registry(roster), requirement_mode="adjacent")
