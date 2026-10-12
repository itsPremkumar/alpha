"""``alpha.tasks`` — the canonical task contract every engine projects onto.

This package is **additive**. It is the neutral vocabulary that lets a task be
described, budgeted, handed off and verified the same way whether the Dynamic
Workflow Engine, the swarm runtime or the graph plane produced it. It is not a
scheduler and it owns no lifecycle:

* ``RunManager`` remains the sole run-lifecycle owner;
* ``alpha.mission.acceptance`` remains the only producer of a terminal *passed*;
* ``alpha.errors.registry`` remains the sole owner of error codes;
* each engine keeps its own task model and its own scheduler — this package
  only gives them a common shape.

Modules:

* :mod:`alpha.tasks.contracts` — the contract models and the neutral status
  vocabulary (:class:`TaskContract`, :class:`TaskContractStatus`,
  :class:`TaskContractBudget`).
* :mod:`alpha.tasks.adapters` — the per-engine projections onto and back off
  the contract.

Contract: ``packages/harness/alpha/tasks/AGENTS.md``.
Tests: ``backend/tests/test_task_contract.py``.
"""

from alpha.tasks.adapters import (
    ENGINE_DWE,
    ENGINE_GRAPH,
    ENGINE_SWARM,
    KNOWN_ENGINES,
    graph_task_to_contract,
    native_status_for,
    status_map_for,
    swarm_task_to_contract,
    unmapped_statuses_for,
    workflow_node_to_contract,
)
from alpha.tasks.contracts import (
    CONTRACT_VERSION,
    SATISFIED_TASK_CONTRACT_STATUSES,
    TERMINAL_TASK_CONTRACT_STATUSES,
    UNBOUNDED,
    ContractValidationError,
    TaskContract,
    TaskContractBudget,
    TaskContractStatus,
    TaskContractType,
    UnmappableStatus,
)

__all__ = [
    "CONTRACT_VERSION",
    "ContractValidationError",
    "ENGINE_DWE",
    "ENGINE_GRAPH",
    "ENGINE_SWARM",
    "KNOWN_ENGINES",
    "SATISFIED_TASK_CONTRACT_STATUSES",
    "TERMINAL_TASK_CONTRACT_STATUSES",
    "TaskContract",
    "TaskContractBudget",
    "TaskContractStatus",
    "TaskContractType",
    "UNBOUNDED",
    "UnmappableStatus",
    "graph_task_to_contract",
    "native_status_for",
    "status_map_for",
    "swarm_task_to_contract",
    "unmapped_statuses_for",
    "workflow_node_to_contract",
]
