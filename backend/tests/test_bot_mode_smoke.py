"""Phase 0 smoke suite — make "Bot Mode broke" a red test, not a surprise.

Why this file exists
--------------------
The multi-agent build plan's Phase 0 gate requires smoke coverage for every
user-facing mode **before** execution paths are changed. Phases 2–7 alter how a
task is dispatched, budgeted, verified and isolated; without a floor under the
existing behaviour, every one of those phases could regress a working feature
and the only way to notice would be a user noticing.

What these tests are and are not
--------------------------------
They are **model-free and offline**. They do not assert that a model produces
good text — that is not a contract and would make the suite flaky. They assert
the *structural* contracts that Bot Mode and delegation depend on, which are
deterministic:

1. A run's acceptance criteria need **evidence**, not fluent prose.
2. Delegation is **single-level** — a child cannot spawn a grandchild.
3. A cancelled run is **terminal**, and the reason is machine-readable.
4. The run status vocabulary is the one every surface projects onto.
5. ``unverified`` is a real verdict, never silently ``met``.
6. A task contract round-trips between engines without losing its status.

Each of those is a place where a multi-agent change could quietly turn a
working feature into a plausible-looking lie.

``tests/test_run_acceptance_criteria.py`` covers ``verify_acceptance_criteria``
in depth (13 tests). This file is deliberately **not** a duplicate: it pins the
contract at the *integration boundary* — the shape a caller actually meets —
so a change to either side that breaks the join fails here.
"""

from __future__ import annotations

import pytest

from alpha.mission.acceptance import CriterionVerdict, EvidenceKind, assert_acceptance_passed
from alpha.runtime.runs.schemas import RunStatus
from alpha.runtime.runs.verification import verify_acceptance_criteria
from alpha.subagents.config import SubagentConfig
from alpha.tasks import TaskContract, TaskContractStatus, graph_task_to_contract

# --------------------------------------------------------------------------
# 1. Acceptance needs evidence, not prose
# --------------------------------------------------------------------------


class TestCompletionIsEvidenceBacked:
    """The single most load-bearing honesty rule in the repository.

    ``verify_acceptance_criteria`` is a **join**, not a provenance validator: it
    checks that each declared criterion has passing evidence of every
    ``required_evidence_kinds`` entry, and discloses what it could not
    attribute. Deciding whether an evidence item is *genuine* is the caller's
    job, enforced upstream in ``alpha.mission.acceptance.EvidenceRecord``. The
    join is what a multi-agent change would break, so it is what is pinned.
    """

    def test_no_declared_criteria_is_not_a_vacuous_pass(self) -> None:
        """No criteria means *nothing was verified* — not a trivial pass.

        ``verified`` is derived from a non-empty verdict list, so an empty plan
        can never read as complete.
        """
        result = verify_acceptance_criteria(None, None)
        assert result.verified is False
        assert result.verdicts == ()

    def test_a_criterion_with_no_evidence_does_not_pass(self) -> None:
        """The refusal names the *missing kind*, not just "failed".

        An operator seeing "Missing passing evidence: test_exit_report" knows
        what to produce; "verification failed" tells them nothing.
        """
        result = verify_acceptance_criteria([{"id": "c1", "required_evidence_kinds": ["test_exit_report"]}], [])
        assert result.verified is False
        assert result.verdicts[0].verified is False
        assert "Missing passing evidence: test_exit_report" in result.verdicts[0].reason

    def test_a_criterion_declaring_no_kinds_still_needs_evidence(self) -> None:
        """An empty ``required_evidence_kinds`` is "unstated", not "satisfied".

        With no kinds declared, a criterion is still a criterion: zero evidence
        must not pass it. The two refusal branches read differently, which is
        the point — they tell an operator different things.
        """
        result = verify_acceptance_criteria([{"id": "c1"}], [])
        assert result.verified is False
        assert result.verdicts[0].reason == "No passing evidence recorded"

    def test_evidence_of_the_wrong_kind_does_not_satisfy_a_criterion(self) -> None:
        """A criterion that names a kind is satisfied only by that kind.

        This is the failure a multi-agent change would introduce most easily:
        an agent reports "I checked the tests" as an ``observed_fact`` and the
        run verifies anyway.
        """
        result = verify_acceptance_criteria(
            [{"id": "c1", "required_evidence_kinds": ["test_exit_report"]}],
            [{"criterion_id": "c1", "kind": "observed_fact", "passed": True, "reference": "the agent said the tests pass"}],
        )
        assert result.verified is False
        assert "test_exit_report" in result.verdicts[0].reason

    def test_passing_evidence_of_the_required_kind_verifies(self) -> None:
        result = verify_acceptance_criteria(
            [{"id": "c1", "required_evidence_kinds": ["test_exit_report"]}],
            [{"criterion_id": "c1", "kind": "test_exit_report", "passed": True, "reference": "artifacts/pytest.json"}],
        )
        assert result.verified is True
        assert result.verdicts[0].criterion_id == "c1"
        assert result.verdicts[0].verified is True
        assert result.verdicts[0].evidence_references == ("artifacts/pytest.json",)

    def test_every_required_kind_must_be_present(self) -> None:
        """Two required kinds, one supplied: still a fail."""
        result = verify_acceptance_criteria(
            [{"id": "c1", "required_evidence_kinds": ["test_exit_report", "artifact_digest"]}],
            [{"criterion_id": "c1", "kind": "test_exit_report", "passed": True}],
        )
        assert result.verified is False
        assert "artifact_digest" in result.verdicts[0].reason

    def test_evidence_for_an_undeclared_criterion_is_disclosed(self) -> None:
        """Undeclared evidence must be visible, not silently absorbed.

        Absorbing it would let a run claim more coverage than it declared,
        which is how a criterion quietly stops being checked.
        """
        result = verify_acceptance_criteria(
            [{"id": "c1", "required_evidence_kinds": ["test_exit_report"]}],
            [
                {"criterion_id": "c1", "kind": "test_exit_report", "passed": True},
                {"criterion_id": "c_never_declared", "kind": "observed_fact", "passed": True},
            ],
        )
        assert result.verified is True
        assert result.notes
        assert "c_never_declared" in result.notes[0]

    def test_evidence_naming_no_criterion_is_disclosed(self) -> None:
        """An unattributed evidence row must not vanish."""
        result = verify_acceptance_criteria(
            [{"id": "c1", "required_evidence_kinds": ["test_exit_report"]}],
            [
                {"criterion_id": "c1", "kind": "test_exit_report", "passed": True},
                {"kind": "observed_fact", "passed": True},
            ],
        )
        assert len(result.notes) == 1
        assert "names no criterion" in result.notes[0]

    def test_a_criterion_without_an_id_is_refused(self) -> None:
        """A criterion nothing can be attributed to is a malformed plan."""
        with pytest.raises(ValueError):
            verify_acceptance_criteria([{"required_evidence_kinds": ["test_exit_report"]}], [])

    def test_invalid_required_kinds_are_refused(self) -> None:
        """A bare string instead of a sequence of kinds is a typo, not a default."""
        with pytest.raises(ValueError):
            verify_acceptance_criteria([{"id": "c1", "required_evidence_kinds": "test_exit_report"}], [])


# --------------------------------------------------------------------------
# 2. Delegation is single-level
# --------------------------------------------------------------------------


class TestDelegationBounds:
    """``docs`` records the rule: *the ordinary task path is single-level*.

    Nested delegation is not a missing feature — it is how the runtime keeps a
    subagent from spawning an unbounded tree of its own. A multi-agent change
    that quietly re-enables it would blow the operator's concurrency ceiling.
    """

    def test_a_child_cannot_delegate_further_by_default(self) -> None:
        config = SubagentConfig(name="child", description="a delegated child")
        assert "task" in config.disallowed_tools
        assert "ralph_loop" in config.disallowed_tools

    def test_a_child_is_bounded_in_turns_and_time(self) -> None:
        """Two independent ceilings; removing either makes a runaway possible."""
        config = SubagentConfig(name="child", description="a delegated child")
        assert config.max_turns > 0
        assert config.timeout_seconds > 0

    def test_a_child_inherits_tools_but_not_the_task_tool(self) -> None:
        """``inherit_all`` governs tools; the delegation ceiling is separate."""
        config = SubagentConfig(name="child", description="a delegated child", inherit_all=True)
        assert config.inherit_all is True
        assert "task" in config.disallowed_tools


# --------------------------------------------------------------------------
# 3. Cancellation is terminal and machine-readable
# --------------------------------------------------------------------------


class TestCancellationIsHonest:
    def test_the_status_vocabulary_is_the_one_surfaces_project_onto(self) -> None:
        """Every surface projects onto these six.

        APEX already learned this the hard way: a status that is terminal in
        ``RunStatus`` but absent from a smaller projection vocabulary lands in
        an ``else`` branch and a run is stored ``running`` forever. Pin the
        vocabulary so a new member cannot arrive unnoticed.
        """
        assert [s.value for s in RunStatus] == ["pending", "running", "success", "error", "timeout", "interrupted"]

    def test_timeout_is_terminal(self) -> None:
        """A timed-out run is finished, not merely slow.

        A viewer that renders ``timeout`` as still-running is the exact failure
        recorded in the APEX dispatcher notes.
        """
        assert RunStatus.timeout.value == "timeout"

    def test_interrupted_is_distinct_from_error(self) -> None:
        """A caller must be able to tell "stopped by the operator" from "crashed"."""
        assert RunStatus.interrupted is not RunStatus.error


# --------------------------------------------------------------------------
# 4. Unverified is a real verdict
# --------------------------------------------------------------------------


class TestUnverifiedIsReal:
    def test_unverified_is_a_distinct_verdict(self) -> None:
        """Collapsing it into ``MET`` is how unmeasured work reads as measured."""
        assert CriterionVerdict.UNVERIFIED.value == "unverified"
        assert CriterionVerdict.MET.value == "met"
        assert CriterionVerdict.NOT_MET.value == "not_met"
        assert len(set(CriterionVerdict)) == 3

    def test_evidence_kinds_are_the_documented_set(self) -> None:
        """The four kinds a collector may produce.

        A new kind is a contract change: a criterion declaring it can no longer
        be satisfied by existing evidence, which is the correct failure.
        """
        assert sorted(kind.value for kind in EvidenceKind) == ["artifact_digest", "observed_fact", "owner_approval", "test_exit_report"]

    def test_the_gate_refuses_a_missing_report(self) -> None:
        """``assert_acceptance_passed`` is the only writer of an acceptance.

        It refuses rather than warning, so a caller cannot proceed on a
        partially-satisfied report.
        """
        with pytest.raises(Exception):
            assert_acceptance_passed(None)


# --------------------------------------------------------------------------
# 5. Cross-engine task contracts stay lossless
# --------------------------------------------------------------------------
#
# The build plan's G2 is about seven schedulers sharing one vocabulary. This is
# the smoke-level check that the join still works after unrelated pipeline
# changes: a graph task projected onto the contract keeps its status, and a
# task that is NOT succeeded does not satisfy a downstream dependency.


class TestCrossEngineContractJoin:
    def _task(self, status: TaskContractStatus):
        from alpha.graph.ids import GoalId
        from alpha.graph.models import TaskNode
        from alpha.tasks.adapters import _canonical_status

        node = TaskNode(task_id="task_smoke", goal_id=GoalId("goal_smoke"), title="Smoke task", status=_canonical_status("graph", status))
        return graph_task_to_contract(node, run_id="run_smoke")

    @pytest.mark.parametrize("status", [TaskContractStatus.RUNNING, TaskContractStatus.BLOCKED, TaskContractStatus.FAILED])
    def test_a_non_successful_task_does_not_satisfy_its_dependents(self, status: TaskContractStatus) -> None:
        """The hole rule: downstream work must not run on a task that produced nothing."""
        assert self._task(status).satisfies_dependency is False

    def test_only_success_satisfies_a_dependency(self) -> None:
        assert self._task(TaskContractStatus.SUCCEEDED).satisfies_dependency is True

    def test_the_contract_still_names_the_engine_it_came_from(self) -> None:
        """Provenance travels with the task; otherwise a projection is anonymous."""
        contract = self._task(TaskContractStatus.RUNNING)
        assert contract.engine == "graph"
        assert contract.native_status == "running"

    def test_a_contract_is_immutable_so_two_engines_cannot_overwrite_it(self) -> None:
        contract = self._task(TaskContractStatus.PENDING)
        moved = contract.with_status(TaskContractStatus.RUNNING)
        assert contract.status is TaskContractStatus.PENDING
        assert moved.status is TaskContractStatus.RUNNING

    def test_a_contract_without_a_run_is_refused(self) -> None:
        """Attribution is not optional: an unowned task cannot be costed or recovered."""
        with pytest.raises(Exception):
            TaskContract(task_id="t", run_id="", title="orphan")
