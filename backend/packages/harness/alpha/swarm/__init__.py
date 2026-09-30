"""Autonomous Agent Swarm Subsystem for Alpha 2.0.

Provides critical-path-optimized swarm decomposition, dependency DAGs,
hybrid workforce scheduling (Permanent Specialist Bots + Ephemeral Subagents),
background async execution, tri-tier memory, rate-limit governance,
autonomous triggers, and automated succession incident recovery.
"""

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.cnp_auction import (
    ContractAward,
    ContractNetAuctionEngine,
    LeaderCandidate,
    LeaderElection,
    SwarmWorkerAgent,
    elect_leader,
)
from alpha.swarm.communication import (
    SwarmMessage,
    SwarmMessageBus,
    SwarmMessageValidationError,
    SwarmSubscription,
)
from alpha.swarm.consensus import (
    ConsensusPolicy,
    ConsensusResult,
    ConsensusVote,
    VoteStance,
    evaluate_consensus,
)
from alpha.swarm.coordinator import SwarmCoordinator, get_swarm_coordinator
from alpha.swarm.decomposer import SwarmTaskDecomposer
from alpha.swarm.deliberation import (
    DeliberationPolicy,
    DeliberationReport,
    rounds_from_evidence,
    run_deliberation,
)
from alpha.swarm.estimator import SwarmBenefitEstimator
from alpha.swarm.governor import SwarmResourceGovernor, get_swarm_resource_governor
from alpha.swarm.incidents import (
    SwarmIncident,
    SwarmIncidentManager,
    get_swarm_incident_manager,
)
from alpha.swarm.memory import SwarmMemoryManager, get_swarm_memory_manager
from alpha.swarm.models import (
    SwarmBudget,
    SwarmDecision,
    SwarmEvent,
    SwarmMode,
    SwarmPlan,
    SwarmTaskLease,
    SwarmTaskNode,
    TaskNodeState,
    is_terminal_swarm_status,
)
from alpha.swarm.planner import (
    PlanCandidate,
    PlanScore,
    build_candidate,
    score_plan,
    select_best_candidate,
)
from alpha.swarm.reflection import SwarmReflection, SwarmReflector
from alpha.swarm.runner import AsyncSwarmRunner
from alpha.swarm.scheduler import SwarmPlanValidationError, SwarmScheduler
from alpha.swarm.stigmergy import StigmergicTrace, StigmergicTraceStore, TraceCategory
from alpha.swarm.strategy import ComplexityTier, StrategyResolution, resolve_strategy, tier_for
from alpha.swarm.team import (
    SPECIALIST_WORKER_TYPE,
    RosterConflictError,
    SpecialistProfile,
    TeamAssignment,
    assign_specialists,
    build_team_report,
    register_roster_provider,
    render_team_report_markdown,
    resolve_roster,
    unregister_roster_provider,
)
from alpha.swarm.telemetry import BudgetPressure, SwarmTelemetry
from alpha.swarm.topology import DagFeatures, TopologyRoute, compute_dag_features, route_topology
from alpha.swarm.triggers import AutonomousWorkTrigger
from alpha.swarm.watchdog import SwarmWatchdog
from alpha.swarm.worker import (
    CodingWorktreeWorker,
    EphemeralSubagentWorker,
    SpecialistBotWorker,
    SpecialistSubagentWorker,
    SwarmWorkerBackend,
)

__all__ = [
    "AsyncSwarmRunner",
    "AutonomousWorkTrigger",
    "BudgetPressure",
    "CodingWorktreeWorker",
    "ComplexityTier",
    "ContractAward",
    "ContractNetAuctionEngine",
    "ConsensusPolicy",
    "ConsensusResult",
    "ConsensusVote",
    "DagFeatures",
    "DeliberationPolicy",
    "DeliberationReport",
    "EphemeralSubagentWorker",
    "LeaderCandidate",
    "LeaderElection",
    "PlanCandidate",
    "PlanScore",
    "RosterConflictError",
    "SpecialistBotWorker",
    "SpecialistProfile",
    "SpecialistSubagentWorker",
    "StigmergicTrace",
    "StigmergicTraceStore",
    "StrategyResolution",
    "SwarmAggregator",
    "SwarmBudget",
    "SwarmCoordinator",
    "SwarmDecision",
    "SwarmEvent",
    "SwarmIncident",
    "SwarmIncidentManager",
    "SwarmMemoryManager",
    "SwarmMessage",
    "SwarmMessageBus",
    "SwarmMessageValidationError",
    "SwarmMode",
    "SwarmPlan",
    "SwarmPlanValidationError",
    "SwarmReflection",
    "SwarmReflector",
    "SwarmResourceGovernor",
    "SwarmScheduler",
    "SwarmSubscription",
    "SwarmTaskDecomposer",
    "SwarmTaskLease",
    "SwarmTaskNode",
    "SwarmTelemetry",
    "SwarmWatchdog",
    "SwarmWorkerAgent",
    "SwarmWorkerBackend",
    "SPECIALIST_WORKER_TYPE",
    "TaskNodeState",
    "TeamAssignment",
    "TopologyRoute",
    "TraceCategory",
    "VoteStance",
    "SwarmBenefitEstimator",
    "assign_specialists",
    "build_candidate",
    "build_team_report",
    "compute_dag_features",
    "elect_leader",
    "evaluate_consensus",
    "get_swarm_coordinator",
    "get_swarm_incident_manager",
    "get_swarm_memory_manager",
    "get_swarm_resource_governor",
    "is_terminal_swarm_status",
    "register_roster_provider",
    "render_team_report_markdown",
    "resolve_roster",
    "resolve_strategy",
    "route_topology",
    "rounds_from_evidence",
    "run_deliberation",
    "score_plan",
    "select_best_candidate",
    "tier_for",
    "unregister_roster_provider",
]
