"""Task-DAG state machine, evidence freshness, scheduling and ownership.

These are the §18 unit rows that make "returned is not verified" and "not run
is not passed" mechanical rather than a matter of prose:

* illegal state transitions and status invariants;
* evidence freshness/invalidation after a file or contract change;
* scheduler readiness and dependency-cycle rejection;
* ownership conflict matching and lease expiry.
"""

from __future__ import annotations

import pytest

from alpha.completion.contract import RequirementInput, build_contract
from alpha.completion.errors import ContractInvalid, IllegalTransition, PathEscape
from alpha.completion.evidence import (
    current_passing,
    definition_digest,
    evaluate_freshness,
    input_fingerprints,
    record_evidence,
    refresh_freshness,
)
from alpha.completion.gates import CheckResult
from alpha.completion.models import (
    CheckOutcome,
    CompletionScope,
    Expectation,
    Freshness,
    Gate,
    GateKind,
    GateStatus,
    ScopeBudget,
    ScopeStatus,
    TaskNode,
    TaskState,
)
from alpha.completion.ownership import claim_paths, conflicts_between, detect_conflicts, normalize_paths, sweep_expired
from alpha.completion.policy import CompletionSettings, settings_from_config
from alpha.completion.scheduler import assert_acyclic, detect_cycle, promote_ready, scheduling_view, verdict_for
from alpha.completion.state_machine import (
    SCOPE_TRANSITIONS,
    TASK_TRANSITIONS,
    assert_scope_transition,
    assert_task_transition,
    can_transition_scope,
    can_transition_task,
    is_terminal_scope,
    is_terminal_task,
)
from alpha.completion.verifier import run_gate
from alpha.config.completion_config import CompletionConfig


def make_settings(tmp_path, **overrides) -> CompletionSettings:
    payload = {
        "enabled": True,
        "mode": "enforce",
        "workspace_root": str(tmp_path),
        "store_path": str(tmp_path / "state.json"),
        "ownership_lease_seconds": 60.0,
    }
    payload.update(overrides)
    return settings_from_config(CompletionConfig(**payload))


def make_gate(gid: str = "g1", task_id: str = "t1", **overrides) -> Gate:
    payload = {
        "gate_id": gid,
        "task_id": task_id,
        "title": "report exists",
        "kind": GateKind.FILE,
        "check_ref": "file.exists",
        "parameters": {"path": "report.md"},
        "expectation": Expectation(required_markers=["# Report"]),
    }
    payload.update(overrides)
    return Gate(**payload)


def make_scope(**overrides) -> CompletionScope:
    payload = {
        "scope_id": "sc-1",
        "owner_id": "user-1",
        "request_snapshot": "Write the report and publish it",
        "contract": build_contract(
            contract_id="c1",
            mode="strict",
            requirements=[
                RequirementInput(
                    requirement_id="r1",
                    intent="Write the report and publish it",
                    observable="report.md exists",
                    task_id="t1",
                    gate_ids=["g1"],
                )
            ],
        ),
        "tasks": {"t1": TaskNode(task_id="t1", title="Write the report", gate_ids=["g1"])},
        "gates": {"g1": make_gate()},
        "budget": ScopeBudget(max_tasks=10, max_depth=4, max_concurrency=2, max_gate_runs=50, wall_clock_seconds=3600.0),
    }
    payload.update(overrides)
    return CompletionScope(**payload)


class TestScopeTransitions:
    def test_legal_edges_are_accepted(self):
        assert can_transition_scope(ScopeStatus.PLANNING, ScopeStatus.RUNNING) is True
        assert can_transition_scope(ScopeStatus.RUNNING, ScopeStatus.VERIFYING) is True
        assert can_transition_scope(ScopeStatus.VERIFYING, ScopeStatus.COMPLETE) is True

    def test_terminal_scopes_have_no_outgoing_edges(self):
        for terminal in (ScopeStatus.COMPLETE, ScopeStatus.CANCELLED):
            assert is_terminal_scope(terminal) is True
            assert SCOPE_TRANSITIONS.get(terminal, set()) == set()
            for target in ScopeStatus:
                assert can_transition_scope(terminal, target) is False

    def test_a_scope_cannot_jump_from_planning_to_complete(self):
        # The whole point of the guard: nothing reaches `complete` without
        # passing through running/verifying.
        assert can_transition_scope(ScopeStatus.PLANNING, ScopeStatus.COMPLETE) is False
        with pytest.raises(IllegalTransition):
            assert_scope_transition(ScopeStatus.PLANNING, ScopeStatus.COMPLETE)

    def test_an_illegal_transition_names_both_states(self):
        with pytest.raises(IllegalTransition) as exc:
            assert_scope_transition(ScopeStatus.CANCELLED, ScopeStatus.RUNNING)
        message = str(exc.value)
        assert "cancelled" in message and "running" in message

    def test_every_scope_status_is_reachable_from_some_edge(self):
        targets = {target for edges in SCOPE_TRANSITIONS.values() for target in edges}
        assert {status for status in ScopeStatus if not is_terminal_scope(status)} <= targets | {ScopeStatus.PLANNING}


class TestTaskTransitions:
    def test_returned_is_not_verified(self):
        assert can_transition_task(TaskState.RETURNED, TaskState.VERIFIED) is False
        with pytest.raises(IllegalTransition):
            assert_task_transition(TaskState.RETURNED, TaskState.VERIFIED)

    def test_a_task_must_be_returned_before_it_can_be_verified(self):
        # in_flight -> verified would let a worker's own claim stand as proof.
        assert can_transition_task(TaskState.IN_FLIGHT, TaskState.VERIFIED) is False
        assert can_transition_task(TaskState.VERIFYING, TaskState.VERIFIED) is True
        assert can_transition_task(TaskState.RETURNED, TaskState.VERIFYING) is True

    def test_abandoned_is_terminal(self):
        assert is_terminal_task(TaskState.ABANDONED) is True
        assert TASK_TRANSITIONS.get(TaskState.ABANDONED, set()) == set()

    def test_a_failed_task_can_be_retried_but_not_verified_directly(self):
        assert can_transition_task(TaskState.FAILED, TaskState.READY) is True
        assert can_transition_task(TaskState.FAILED, TaskState.VERIFIED) is False

    def test_ready_may_be_parked_back_to_waiting(self):
        assert can_transition_task(TaskState.READY, TaskState.WAITING) is True


class TestEvidenceFreshness:
    def test_passing_evidence_starts_current(self, tmp_path):
        settings = make_settings(tmp_path)
        (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
        scope = make_scope()
        gate = scope.gates["g1"]
        evidence = record_evidence(scope, gate, CheckResult(CheckOutcome.PASSED), settings, now=1000.0)
        assert evidence.freshness is Freshness.CURRENT
        assert current_passing(scope, gate, settings, now=1001.0) is evidence

    def test_editing_the_gated_file_makes_the_evidence_stale(self, tmp_path):
        settings = make_settings(tmp_path)
        path = tmp_path / "report.md"
        path.write_text("# Report\n", encoding="utf-8")
        scope = make_scope()
        gate = scope.gates["g1"]
        evidence = record_evidence(scope, gate, CheckResult(CheckOutcome.PASSED), settings, now=1000.0)

        path.write_text("# Report\nchanged after the check\n", encoding="utf-8")
        import os

        os.utime(path, (2000, 2000))
        freshness, reason = evaluate_freshness(evidence, gate, settings, now=2001.0)
        assert freshness is Freshness.STALE
        assert reason
        assert current_passing(scope, gate, settings, now=2001.0) is None

    def test_changing_the_gate_definition_invalidates_prior_evidence(self, tmp_path):
        settings = make_settings(tmp_path)
        (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
        scope = make_scope()
        gate = scope.gates["g1"]
        evidence = record_evidence(scope, gate, CheckResult(CheckOutcome.PASSED), settings, now=1000.0)

        gate.expectation = Expectation(required_markers=["# Report", "# Extra"])
        freshness, reason = evaluate_freshness(evidence, gate, settings, now=1001.0)
        assert freshness is Freshness.STALE
        assert "definition" in reason or "digest" in reason

    def test_refresh_marks_the_stale_gate_and_names_it(self, tmp_path):
        settings = make_settings(tmp_path)
        path = tmp_path / "report.md"
        path.write_text("# Report\n", encoding="utf-8")
        scope = make_scope()
        # Through the real gate runner, so the gate reaches PASSED first —
        # only a green gate can later be *demoted*, which is the property.
        run_gate(scope, scope.gates["g1"], settings, now=1000.0)
        assert scope.gates["g1"].status is GateStatus.PASSED

        path.write_text("changed\n", encoding="utf-8")
        import os

        os.utime(path, (2000, 2000))
        stale = refresh_freshness(scope, settings, now=2001.0)
        # The *evidence* ids are what change; the gate is then moved back to
        # stale because nothing current passes it any more.
        assert stale == ["ev-g1-1"]
        assert scope.gates["g1"].status is GateStatus.STALE
        assert scope.evidence["ev-g1-1"].invalidation_reason
        assert current_passing(scope, scope.gates["g1"], settings, now=2001.0) is None

    def test_failed_evidence_is_never_current_passing(self, tmp_path):
        settings = make_settings(tmp_path)
        (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
        scope = make_scope()
        gate = scope.gates["g1"]
        record_evidence(scope, gate, CheckResult(CheckOutcome.FAILED, reason="missing"), settings, now=1000.0)
        assert current_passing(scope, gate, settings, now=1001.0) is None

    def test_a_fingerprint_covers_the_file_and_the_expectation(self, tmp_path):
        settings = make_settings(tmp_path)
        (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
        fingerprints = input_fingerprints(make_gate(), settings)
        # Keys are the declared workspace-relative path exactly as written, so
        # a rename or an escape never silently matches an old fingerprint.
        assert "report.md" in fingerprints
        assert all(len(value) == 64 for value in fingerprints.values())

    def test_the_definition_digest_tracks_the_policy_snapshot(self, tmp_path):
        gate = make_gate()
        assert definition_digest(gate, make_settings(tmp_path, gate_timeout_seconds=5.0)) != definition_digest(gate, make_settings(tmp_path, gate_timeout_seconds=6.0))


class TestScheduling:
    def test_a_task_with_unmet_dependencies_is_not_ready(self, tmp_path):
        scope = make_scope(
            tasks={
                "t1": TaskNode(task_id="t1", title="A", gate_ids=["g1"]),
                "t2": TaskNode(task_id="t2", title="B", gate_ids=[], depends_on=["t1"]),
            }
        )
        verdict = verdict_for(scope, scope.tasks["t2"])
        assert verdict.ready is False
        assert "t1" in verdict.reason

    def test_a_task_becomes_ready_once_its_dependency_verifies(self, tmp_path):
        scope = make_scope(
            tasks={
                "t1": TaskNode(task_id="t1", title="A", gate_ids=["g1"], state=TaskState.VERIFIED),
                "t2": TaskNode(task_id="t2", title="B", gate_ids=[], depends_on=["t1"]),
            }
        )
        assert verdict_for(scope, scope.tasks["t2"]).ready is True

    def test_a_dependency_cycle_is_detected_by_id(self, tmp_path):
        scope = make_scope(
            tasks={
                "t1": TaskNode(task_id="t1", title="A", gate_ids=["g1"], depends_on=["t2"]),
                "t2": TaskNode(task_id="t2", title="B", gate_ids=[], depends_on=["t1"]),
            }
        )
        cycle = detect_cycle(scope.tasks)
        assert cycle
        assert set(cycle) <= {"t1", "t2"}
        with pytest.raises(ContractInvalid):
            assert_acyclic(scope.tasks)

    def test_a_self_dependency_is_a_cycle(self, tmp_path):
        scope = make_scope(tasks={"t1": TaskNode(task_id="t1", title="A", gate_ids=["g1"], depends_on=["t1"])})
        assert detect_cycle(scope.tasks)
        with pytest.raises(ContractInvalid):
            assert_acyclic(scope.tasks)

    def test_promote_ready_moves_waiting_tasks_whose_dependencies_met(self, tmp_path):
        scope = make_scope(
            tasks={
                "t1": TaskNode(task_id="t1", title="A", gate_ids=["g1"], state=TaskState.VERIFIED),
                "t2": TaskNode(task_id="t2", title="B", gate_ids=[], depends_on=["t1"], state=TaskState.WAITING),
            }
        )
        moved = promote_ready(scope, now=1000.0)
        assert moved == ["t2"]
        assert scope.tasks["t2"].state is TaskState.READY
        # Idempotent: a second pass promotes nothing.
        assert promote_ready(scope, now=1001.0) == []

    def test_the_view_respects_the_declared_limit(self, tmp_path):
        scope = make_scope(
            tasks={
                "t1": TaskNode(task_id="t1", title="A", gate_ids=["g1"]),
                "t2": TaskNode(task_id="t2", title="B", gate_ids=[]),
            }
        )
        view = scheduling_view(scope, limit=1)
        assert len(view.dispatchable) == 1


class TestOwnership:
    def test_paths_are_normalised_to_the_workspace(self, tmp_path):
        settings = make_settings(tmp_path)
        assert normalize_paths(["./a/b.md", "a/b.md"], settings) == ["a/b.md"]

    def test_an_escaping_claim_is_refused(self, tmp_path):
        settings = make_settings(tmp_path)
        with pytest.raises(PathEscape):
            normalize_paths(["../outside.md"], settings)

    def test_overlapping_claims_are_detected(self, tmp_path):
        settings = make_settings(tmp_path)
        scope = make_scope()
        task_a = scope.tasks["t1"]
        claim_paths(scope, task_a, ["src/module.py"], settings, now=1000.0)
        detail = detect_conflicts(["src/module.py"], scope.ownership, scope_id="sc-1", exclude_task="t2", now=1001.0)
        assert detail
        assert detail[0].conflicts_with_task == "t1"

    def test_a_task_is_not_in_conflict_with_its_own_claim(self, tmp_path):
        settings = make_settings(tmp_path)
        scope = make_scope()
        claim_paths(scope, scope.tasks["t1"], ["src/module.py"], settings, now=1000.0)
        assert detect_conflicts(["src/module.py"], scope.ownership, scope_id="sc-1", exclude_task="t1", now=1001.0) == []

    def test_an_expired_claim_is_swept(self, tmp_path):
        settings = make_settings(tmp_path, ownership_lease_seconds=60.0)
        scope = make_scope()
        claim_paths(scope, scope.tasks["t1"], ["src/module.py"], settings, now=1000.0)
        assert sweep_expired(scope, now=1030.0) == []
        released = sweep_expired(scope, now=1061.0)
        assert [claim.task_id for claim in released] == ["t1"]
        assert scope.ownership == []

    def test_prefix_claims_conflict_only_at_a_boundary(self):
        # A directory claim covers its subtree. A *sibling* file whose name
        # merely starts with the same characters is not inside it, so
        # "src/app.py" must not block "src/app".
        assert conflicts_between("src/app", "src/app/main.py") is True
        assert conflicts_between("src/app/main.py", "src/app") is True
        assert conflicts_between("src/app.py", "src/app") is False
        assert conflicts_between("src/app.py", "src/other.py") is False
        assert conflicts_between("src/app.py", "src/app.py") is True
