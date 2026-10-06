"""APEX — the executive control plane.

See ``README.md`` in this package for the doctrine and the "what this does not
own" table. The short version: APEX holds an autonomy contract and runs a
bounded executive cycle over engines that already exist; it is not an execution
path and not a second policy kernel.

Specification: ``docs/ALPHA_APEX_AUTOPILOT_MASTER_SPEC.md``.
Integration rationale: ``docs/APEX_INTEGRATION_MAP.md``.
Operations: ``docs/APEX_AUTOPILOT.md``.
"""

from __future__ import annotations

from alpha.apex.contract import (
    ApexBudget,
    ApexControls,
    AutonomyContract,
    AutonomyProfile,
    ContractViolation,
    default_contract,
    narrow_contract,
    profile_for,
)
from alpha.apex.executive import (
    ExecutiveDecision,
    ExecutiveResult,
    NextAction,
    run_cycle,
    select_next_action,
)
from alpha.apex.invariants import (
    INVARIANTS,
    InvariantCheck,
    InvariantReport,
    check_invariants,
    invariant_summary,
)
from alpha.apex.status import apex_status, contract_status, fleet_status
from alpha.apex.store import ApexSession, ApexSessionState, ApexStore, get_apex_store

__all__ = [
    "INVARIANTS",
    "ApexBudget",
    "ApexControls",
    "ApexSession",
    "ApexSessionState",
    "ApexStore",
    "AutonomyContract",
    "AutonomyProfile",
    "ContractViolation",
    "ExecutiveDecision",
    "ExecutiveResult",
    "InvariantCheck",
    "InvariantReport",
    "NextAction",
    "apex_status",
    "check_invariants",
    "contract_status",
    "default_contract",
    "fleet_status",
    "get_apex_store",
    "invariant_summary",
    "narrow_contract",
    "profile_for",
    "run_cycle",
    "select_next_action",
]
