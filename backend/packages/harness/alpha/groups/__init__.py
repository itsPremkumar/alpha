"""Multi-Agent Group Chat Engine for Alpha."""

from alpha.groups.activity import (
    ACTIVITY_STATES,
    ActivityEvidence,
    ActivityLedger,
    ActivityState,
    ActivityTone,
    AgentActivity,
    RunEvidence,
    derive_activity,
    get_activity_ledger,
    tone_for,
)
from alpha.groups.claims import (
    CLAIM_INTENTS,
    CLAIM_KINDS,
    ClaimStore,
    SoftConflict,
    WorkClaim,
    detect_soft_conflicts,
    get_claim_store,
    normalise_subject,
)
from alpha.groups.orchestration import GroupOrchestrator
from alpha.groups.presence import MemberPresence, PresenceState, resolve_room_presence
from alpha.groups.quorum import Proposal, QuorumEngine
from alpha.groups.room import REACTION_EMOJI, VALID_INTENTS, GroupMessage, GroupRoom, MessageIntent, OrchestrationMode
from alpha.groups.roster import (
    GroupRoster,
    MembershipRule,
    ResolvedRoster,
    RosterError,
    resolve_roster,
    resolve_rule,
    validate_rule,
)
from alpha.groups.scope import (
    MAX_DEPTH,
    GroupScope,
    ScopeError,
    ancestors_of,
    children_of,
    descendants_of,
    plan_relay,
    recompute_all,
    validate_state,
)
from alpha.groups.service import GroupChatService, get_group_chat_service

__all__ = [
    "GroupMessage",
    "GroupRoom",
    "OrchestrationMode",
    "MessageIntent",
    "VALID_INTENTS",
    "REACTION_EMOJI",
    "GroupOrchestrator",
    "QuorumEngine",
    "Proposal",
    "GroupChatService",
    "get_group_chat_service",
    "MemberPresence",
    "PresenceState",
    "resolve_room_presence",
    # Activity (crash-honest live status)
    "ACTIVITY_STATES",
    "ActivityEvidence",
    "ActivityLedger",
    "ActivityState",
    "ActivityTone",
    "AgentActivity",
    "RunEvidence",
    "derive_activity",
    "get_activity_ledger",
    "tone_for",
    # Work claims (advisory coordination)
    "CLAIM_INTENTS",
    "CLAIM_KINDS",
    "ClaimStore",
    "SoftConflict",
    "WorkClaim",
    "detect_soft_conflicts",
    "get_claim_store",
    "normalise_subject",
    # Nesting / scope
    "GroupScope",
    "ScopeError",
    "MAX_DEPTH",
    "children_of",
    "ancestors_of",
    "descendants_of",
    "recompute_all",
    "plan_relay",
    "validate_state",
    # Roster
    "GroupRoster",
    "MembershipRule",
    "ResolvedRoster",
    "RosterError",
    "resolve_roster",
    "resolve_rule",
    "validate_rule",
]
