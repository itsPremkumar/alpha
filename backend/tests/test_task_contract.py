"""Contract tests for :mod:`alpha.tasks` — the canonical task contract.

What is pinned here, and why each one is load-bearing:

1. **Round-trip fidelity.** A task projected from any engine and projected back
   returns the engine's *own* status string, not a canonical guess. A lossy
   adapter would make the contract a place where information quietly disappears.
2. **Status maps are exhaustive.** Each adapter's map must cover **every**
   member of its engine's live enum. A gap turns an ordinary state change into
   a runtime refusal in the middle of a live run, which is the failure mode
   this package exists to remove.
3. **An unmapped status refuses, never defaults.** This is the single most
   important property: a projection that guesses ``pending`` turns an
   unfamiliar state into a task that looks ready to run.
4. **Bounds are refused, not clamped**, and ``0`` on a cost axis stays a real
   ceiling rather than becoming "unknown".
5. **The contract owns no lifecycle.** It must not import ``RunManager``, the
   acceptance gate or the error registry — a grep-level assertion, because the
   boundary is invisible at runtime and only shows up when somebody wires it
   in later.
"""

from __future__ import annotations

import pytest

from alpha.tasks import (
    CONTRACT_VERSION,
    SATISFIED_TASK_CONTRACT_STATUSES,
    UNBOUNDED,
    TaskContract,
    TaskContractBudget,
    TaskContractStatus,
    UnmappableStatus,
    graph_task_to_contract,
    native_status_for,
    status_map_for,
    swarm_task_to_contract,
    unmapped_statuses_for,
    workflow_node_to_contract,
)
from alpha.tasks.adapters import ENGINE_DWE, ENGINE_GRAPH, ENGINE_SWARM

RUN_ID = "run_test_1"


def _contract(**overrides) -> TaskContract:
    """A minimal valid contract; tests override one field at a time."""
    base = {
        "task_id": "task_a",
        "run_id": RUN_ID,
        "title": "Inspect authentication flow",
        "created_at": "2026-10-11T00:00:00Z",
    }
    base.update(overrides)
    return TaskContract(**base)


# --------------------------------------------------------------------------
# 1. Shape and attribution
# --------------------------------------------------------------------------


class TestShape:
    def test_minimal_contract_validates(self) -> None:
        contract = _contract()
        assert contract.task_id == "task_a"
        assert contract.run_id == RUN_ID
        assert contract.status is TaskContractStatus.PENDING

    def test_run_id_is_required(self) -> None:
        """A task with no owning run cannot be attributed, costed or recovered."""
        with pytest.raises(Exception) as excinfo:
            _contract(run_id="")
        assert "run_id" in str(excinfo.value)

    def test_schema_version_is_carried(self) -> None:
        assert _contract().schema_version == CONTRACT_VERSION

    def test_unknown_field_is_refused(self) -> None:
        """``extra="forbid"`` so a renamed field fails loudly at the boundary.

        Without it a typo'd field is dropped and the caller believes the value
        was recorded.
        """
        with pytest.raises(Exception):
            _contract(does_not_exist=1)


# --------------------------------------------------------------------------
# 2. Budgets: refuse, never clamp
# --------------------------------------------------------------------------


class TestBudget:
    def test_zero_attempts_is_refused_not_clamped(self) -> None:
        with pytest.raises(Exception) as excinfo:
            _contract(max_attempts=0)
        assert "refused rather than clamped" in str(excinfo.value)

    def test_zero_tool_calls_is_refused(self) -> None:
        with pytest.raises(Exception):
            TaskContractBudget(max_tool_calls=0)

    def test_negative_cost_is_refused(self) -> None:
        with pytest.raises(Exception):
            TaskContractBudget(max_cost_usd=-1.0)

    def test_zero_cost_is_a_real_ceiling(self) -> None:
        """``max_cost_usd=0`` means *paid calls are blocked*, not *unknown*.

        The source plan §5.2 is explicit about this, and it is the opposite of
        the "0 means unbounded" convention most code inherits.
        """
        budget = TaskContractBudget(max_cost_usd=0.0)
        assert budget.max_cost_usd == 0.0
        assert budget.max_cost_usd is not None

    def test_unbounded_is_explicit_and_distinct_from_none(self) -> None:
        explicit = TaskContractBudget(max_tool_calls=UNBOUNDED)
        unstated = TaskContractBudget(max_tool_calls=None)
        assert explicit.max_tool_calls == UNBOUNDED
        assert unstated.max_tool_calls is None
        assert explicit.max_tool_calls != unstated.max_tool_calls

    def test_bool_is_refused_as_a_number(self) -> None:
        """``True`` is an int in Python; a budget of ``True`` calls is a bug."""
        with pytest.raises(Exception):
            TaskContractBudget(max_model_calls=True)


# --------------------------------------------------------------------------
# 3. Derived state is derived, never declared
# --------------------------------------------------------------------------


class TestDerivedState:
    def test_terminal_statuses(self) -> None:
        for status in (TaskContractStatus.SUCCEEDED, TaskContractStatus.FAILED, TaskContractStatus.SKIPPED, TaskContractStatus.CANCELLED):
            assert _contract(status=status).is_terminal, status
        for status in (TaskContractStatus.PENDING, TaskContractStatus.RUNNING, TaskContractStatus.BLOCKED):
            assert not _contract(status=status).is_terminal, status

    def test_skipped_does_not_satisfy_a_dependency(self) -> None:
        """A skipped task produced nothing.

        Treating it as satisfied would run downstream work on a hole — the same
        rule ``alpha.graph`` states as ``SATISFIED_TASK_STATUSES = {SUCCEEDED}``.
        """
        assert _contract(status=TaskContractStatus.SKIPPED).satisfies_dependency is False
        assert _contract(status=TaskContractStatus.SUCCEEDED).satisfies_dependency is True
        assert SATISFIED_TASK_CONTRACT_STATUSES == frozenset({TaskContractStatus.SUCCEEDED})

    def test_blocked_needs_explicit_re_entry(self) -> None:
        assert _contract(status=TaskContractStatus.BLOCKED).is_blocked is True
        assert _contract(status=TaskContractStatus.PENDING).is_blocked is False

    def test_approval_is_derived_from_policy(self) -> None:
        """A contract cannot carry an approval flag that disagrees with its policy."""
        assert _contract(approval_policy="required").needs_approval is True
        assert _contract(approval_policy="not_required").needs_approval is False
        assert _contract(approval_policy="prohibited").needs_approval is False

    def test_status_change_returns_a_new_value(self) -> None:
        """The contract is frozen, so two engines cannot overwrite each other."""
        original = _contract()
        moved = original.with_status(TaskContractStatus.RUNNING, updated_at="2026-10-11T00:01:00Z")
        assert original.status is TaskContractStatus.PENDING
        assert moved.status is TaskContractStatus.RUNNING
        assert moved.updated_at == "2026-10-11T00:01:00Z"

    def test_attempt_increment_returns_a_new_value(self) -> None:
        original = _contract(attempt_count=1)
        assert original.with_attempt().attempt_count == 2
        assert original.attempt_count == 1


# --------------------------------------------------------------------------
# 4. Refusals on malformed contracts
# --------------------------------------------------------------------------


class TestRefusals:
    def test_self_dependency_is_refused(self) -> None:
        with pytest.raises(Exception) as excinfo:
            _contract(task_id="task_a", depends_on=["task_a"])
        assert "self-dependency" in str(excinfo.value)

    def test_duplicate_dependencies_are_deduplicated(self) -> None:
        contract = _contract(depends_on=["task_b", "task_b", "task_c"])
        assert contract.depends_on == ["task_b", "task_c"]

    def test_unknown_task_type_is_refused_not_defaulted(self) -> None:
        with pytest.raises(Exception) as excinfo:
            _contract(task_type="teleportation")
        assert "not part of the canonical vocabulary" in str(excinfo.value)

    def test_absent_created_at_is_unstated_not_zero(self) -> None:
        """Absent is a third state, distinct from a fabricated epoch."""
        contract = _contract(created_at=None)
        assert contract.created_at is None


# --------------------------------------------------------------------------
# 5. Status maps are exhaustive over each engine's live enum
# --------------------------------------------------------------------------


class TestStatusMapsAreExhaustive:
    def test_graph_map_covers_every_task_status(self) -> None:
        from alpha.graph.models import TaskStatus

        assert unmapped_statuses_for(ENGINE_GRAPH, list(TaskStatus)) == ()

    def test_workflow_map_covers_every_node_status(self) -> None:
        from alpha.workflow.models import NodeStatus

        assert unmapped_statuses_for(ENGINE_DWE, list(NodeStatus)) == ()

    def test_swarm_map_covers_every_documented_status_string(self) -> None:
        """``SwarmPlan.status`` is a bare string with a live value set.

        The inline comment on the field in ``swarm/models.py`` names the
        members and ``TERMINAL_SWARM_STATUSES``/``NON_TERMINAL_SWARM_STATUSES``
        enumerate them. The union of those two sets is the real vocabulary, so
        it is what the map is checked against — a value that exists only in
        prose is exactly the kind of gap this catches.
        """
        from alpha.swarm.models import NON_TERMINAL_SWARM_STATUSES, TERMINAL_SWARM_STATUSES

        documented = sorted(TERMINAL_SWARM_STATUSES | NON_TERMINAL_SWARM_STATUSES)
        assert unmapped_statuses_for(ENGINE_SWARM, documented) == ()

    def test_swarm_partial_success_maps_to_running(self) -> None:
        """``partial_success`` is the aggregator's partial verdict, never terminal.

        ``SwarmAggregator.aggregate`` writes ``partial_success`` when a
        completed task fails acceptance or carries declared-but-unverified
        criteria. Only RUNNING is honest: SUCCEEDED would fabricate success,
        FAILED a terminal failure — both erase exactly the disclosure this
        status exists to keep. ``native_status`` preserves the exact word.
        """

        table = status_map_for(ENGINE_SWARM)
        assert table["partial_success"] is TaskContractStatus.RUNNING
        assert table["partial_success"] is not TaskContractStatus.SUCCEEDED
        assert table["partial_success"] is not TaskContractStatus.FAILED

    def test_swarm_task_node_state_enum_is_also_covered(self) -> None:
        """``SwarmTaskNode`` carries *two* vocabularies; both must translate."""
        from alpha.swarm.models import TaskNodeState

        assert unmapped_statuses_for(ENGINE_SWARM, list(TaskNodeState)) == ()

    def test_every_canonical_status_has_a_reverse_mapping(self) -> None:
        from alpha.tasks.adapters import KNOWN_ENGINES

        for engine in KNOWN_ENGINES:
            for status in TaskContractStatus:
                assert native_status_for(engine, status)


# --------------------------------------------------------------------------
# 6. An unmapped status refuses, never defaults  ← the load-bearing property
# --------------------------------------------------------------------------


class TestUnmappedStatusRefuses:
    def test_unknown_engine_refuses(self) -> None:
        with pytest.raises(UnmappableStatus):
            status_map_for("telepathy")

    def test_unmapped_native_status_refuses_with_both_names(self) -> None:
        """The refusal must say which engine and which value.

        A bare "invalid status" would send an operator looking at the wrong
        subsystem.
        """
        from alpha.tasks.adapters import _canonical_status

        with pytest.raises(UnmappableStatus) as excinfo:
            _canonical_status(ENGINE_GRAPH, "quantum_superposition")
        message = str(excinfo.value)
        assert ENGINE_GRAPH in message
        assert "quantum_superposition" in message
        assert "alpha.tasks.adapters" in message

    def test_unmapped_status_is_not_pydantic_validation_error(self) -> None:
        """It is a distinct type so a caller cannot confuse it with a bad field."""
        assert issubclass(UnmappableStatus, ValueError)
        assert not issubclass(UnmappableStatus, TypeError)


# --------------------------------------------------------------------------
# 7. Round-trip fidelity through each real adapter
# --------------------------------------------------------------------------


class TestGraphAdapterRoundTrip:
    def _task(self, **overrides):
        from alpha.graph.ids import GoalId
        from alpha.graph.models import TaskNode

        base = {"goal_id": GoalId("goal_1"), "title": "Inspect auth flow", "description": "Find the defects"}
        base.update(overrides)
        return TaskNode(task_id="task_auth", **base)

    def test_round_trip_preserves_native_status(self) -> None:
        from alpha.graph.models import TaskStatus

        for status in TaskStatus:
            contract = graph_task_to_contract(self._task(status=status), run_id=RUN_ID)
            assert contract.native_status == status.value
            assert graph_task_to_contract(self._task(status=status), run_id=RUN_ID).status is contract.status

    def test_near_identity_for_graph(self) -> None:
        """The graph plane is closest to the contract, so almost nothing is lost."""
        contract = graph_task_to_contract(self._task(), run_id=RUN_ID, parent_task_id="task_root", acceptance_criteria=["cites file and line"])
        assert contract.task_id == "task_auth"
        assert contract.parent_task_id == "task_root"
        assert contract.engine == ENGINE_GRAPH
        assert contract.objective == "Find the defects"
        assert contract.acceptance_criteria == ["cites file and line"]
        assert contract.native_type == "tool"

    def test_high_risk_becomes_required_approval(self) -> None:
        """Approval is a dispatch decision, so it is derived, not stored twice."""
        from alpha.graph.models import RiskLevel

        contract = graph_task_to_contract(self._task(risk_level=RiskLevel.HIGH), run_id=RUN_ID)
        assert contract.needs_approval is True

    def test_prohibited_risk_is_not_an_approval_wait(self) -> None:
        """``PROHIBITED`` is deny-and-record, not "wait for a human"."""
        from alpha.graph.models import RiskLevel

        contract = graph_task_to_contract(self._task(risk_level=RiskLevel.PROHIBITED), run_id=RUN_ID)
        assert contract.needs_approval is False


class TestWorkflowAdapterRoundTrip:
    def _node(self, **overrides):
        from alpha.workflow.models import NodeType, WorkflowNode

        base = {"id": "node_1", "type": NodeType.AGENT, "prompt": "Research the auth flow"}
        base.update(overrides)
        return WorkflowNode(**base)

    def test_round_trip_preserves_native_status(self) -> None:
        from alpha.workflow.models import NodeStatus

        for status in NodeStatus:
            contract = workflow_node_to_contract(self._node(status=status), run_id=RUN_ID)
            assert contract.native_status == status.value

    def test_suspended_maps_to_blocked(self) -> None:
        """A parked run is blocked, not pending — otherwise it looks dispatchable."""
        from alpha.workflow.models import NodeStatus

        contract = workflow_node_to_contract(self._node(status=NodeStatus.SUSPENDED), run_id=RUN_ID)
        assert contract.status is TaskContractStatus.BLOCKED

    def test_aborted_maps_to_cancelled(self) -> None:
        from alpha.workflow.models import NodeStatus

        contract = workflow_node_to_contract(self._node(status=NodeStatus.ABORTED), run_id=RUN_ID)
        assert contract.status is TaskContractStatus.CANCELLED

    def test_token_budget_never_becomes_a_cost_budget(self) -> None:
        """The engine has a token count and no price.

        Filling ``max_cost_usd`` from it would be a fabricated cost figure,
        which is the exact failure the repository rejects elsewhere.
        """
        contract = workflow_node_to_contract(self._node(budget=5000), run_id=RUN_ID)
        assert contract.budget.max_tokens == 5000
        assert contract.budget.max_cost_usd is None

    def test_write_scope_becomes_the_conflict_scope(self) -> None:
        """``write_scope`` is what two concurrent tasks would race on."""
        contract = workflow_node_to_contract(self._node(write_scope=["src/auth/**"]), run_id=RUN_ID)
        assert contract.scope_paths == ["src/auth/**"]

    def test_native_node_type_is_preserved(self) -> None:
        """The neutral kind is coarse; the engine's kind survives alongside it."""
        from alpha.workflow.models import NodeType

        contract = workflow_node_to_contract(self._node(type=NodeType.SUBAGENT), run_id=RUN_ID)
        assert contract.native_type == "subagent"

    def test_declared_verification_criteria_are_read(self) -> None:
        node = self._node(config={"verification_criteria": [{"name": "pytest", "target": "unit"}]})
        contract = workflow_node_to_contract(node, run_id=RUN_ID)
        assert "name: pytest" in contract.acceptance_criteria


class TestSwarmAdapterRoundTrip:
    def _task(self, **overrides):
        from dataclasses import dataclass, field

        from alpha.swarm.models import TaskNodeState

        @dataclass
        class _Stub:
            task_id: str = "swarm_task_1"
            objective: str = "Reproduce the auth bug"
            dependencies: list = field(default_factory=list)
            assigned_worker: object = None
            worker_type: str = "ephemeral"
            model_override: object = None
            worktree_path: object = None
            input_artifacts: list = field(default_factory=list)
            output_artifacts: list = field(default_factory=lambda: ["patch.diff"])
            state: object = TaskNodeState.PENDING
            result_summary: object = None
            evidence: list = field(default_factory=list)
            lease_expires_at: object = None
            started_at: object = None
            completed_at: object = None
            duration_seconds: float = 0.0
            attempts: int = 0
            max_attempts: int = 3
            error_message: object = None
            lease_id: object = None
            lease_owner: object = None
            next_attempt_at: object = None
            context_refs: list = field(default_factory=list)
            capability_tags: list = field(default_factory=lambda: ["repository_read"])
            capability_source: str = "declared"
            parent_task_id: object = None
            priority: int = 0
            token_usage: dict = field(default_factory=dict)
            tool_calls: int = 0
            result_payload: dict = field(default_factory=dict)
            verification: dict = field(default_factory=dict)
            acceptance_criteria: list = field(default_factory=list)
            acceptance_status: str = "not_required"
            estimated_seconds: float = 15.0

        return _Stub(**overrides)

    def test_completed_maps_to_succeeded(self) -> None:
        """The vocabularies differ in exactly this word; the adapter owns it."""
        from alpha.swarm.models import TaskNodeState

        contract = swarm_task_to_contract(self._task(state=TaskNodeState.COMPLETED), run_id=RUN_ID)
        assert contract.status is TaskContractStatus.SUCCEEDED
        assert contract.native_status == "completed"

    def test_round_trip_preserves_native_status(self) -> None:
        from alpha.swarm.models import TaskNodeState

        for state in TaskNodeState:
            contract = swarm_task_to_contract(self._task(state=state), run_id=RUN_ID)
            assert contract.native_status == state.value

    def test_straggling_is_running_not_terminal(self) -> None:
        """A straggler is still in flight; calling it terminal would free a slot early."""
        from alpha.swarm.models import TaskNodeState

        contract = swarm_task_to_contract(self._task(state=TaskNodeState.STRAGGLING), run_id=RUN_ID)
        assert contract.is_terminal is False
        assert contract.status is TaskContractStatus.RUNNING

    def test_declared_capability_tags_survive(self) -> None:
        contract = swarm_task_to_contract(self._task(), run_id=RUN_ID)
        assert contract.eligible_capabilities == ["repository_read"]

    def test_output_artifacts_become_expected_artifacts(self) -> None:
        contract = swarm_task_to_contract(self._task(), run_id=RUN_ID)
        assert contract.expected_artifacts == ["patch.diff"]

    def test_worktree_path_becomes_the_write_scope(self) -> None:
        """This is how two coding tasks avoid racing on the same files."""
        contract = swarm_task_to_contract(self._task(worktree_path=".worktrees/agent-a"), run_id=RUN_ID)
        assert contract.scope_paths == [".worktrees/agent-a"]

    def test_zero_tool_calls_is_unstated_not_zero(self) -> None:
        """``tool_calls=0`` means "none recorded", not "ceiling of zero calls".

        Conflating them would produce a budget that refuses every call.
        """
        contract = swarm_task_to_contract(self._task(tool_calls=0), run_id=RUN_ID)
        assert contract.budget.max_tool_calls is None


# --------------------------------------------------------------------------
# 8. The package owns no lifecycle (grep-level boundary)
# --------------------------------------------------------------------------


class TestPackageBoundary:
    @pytest.mark.parametrize("forbidden", ["alpha.runtime.runs", "app.gateway", "alpha.errors.registry", "alpha.mission.acceptance"])
    def test_no_lifecycle_imports(self, forbidden: str) -> None:
        """The contract must not become a second run owner or a second verdict source.

        Asserted against the **parsed AST**, not a substring of the file: these
        modules are *named* in the package docstrings precisely to record who
        owns what, and a text grep cannot tell a docstring from an import. A
        boundary that only shows up at runtime is one somebody will cross later
        without noticing, so it is checked at the import level.
        """
        import ast
        import pathlib

        import alpha.tasks as pkg

        root = pathlib.Path(pkg.__file__).parent
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
                    imported.update(f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*")
            assert not any(name == forbidden or name.startswith(f"{forbidden}.") for name in imported), f"{path.name} imports {forbidden}; alpha.tasks owns no lifecycle"
