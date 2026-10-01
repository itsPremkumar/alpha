"""Multi-Agent Group Chat Engine for Alpha."""

from alpha.groups.orchestration import GroupOrchestrator
from alpha.groups.presence import MemberPresence, PresenceState, resolve_room_presence
from alpha.groups.quorum import Proposal, QuorumEngine
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
from alpha.groups.room import REACTION_EMOJI, VALID_INTENTS, GroupMessage, GroupRoom, MessageIntent, OrchestrationMode
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
