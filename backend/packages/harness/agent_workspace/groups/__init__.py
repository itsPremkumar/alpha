"""Multi-Agent Group Chat Engine for Alpha."""

from agent_workspace.groups.orchestration import GroupOrchestrator
from agent_workspace.groups.quorum import Proposal, QuorumEngine
from agent_workspace.groups.room import GroupMessage, GroupRoom, MessageIntent, OrchestrationMode
from agent_workspace.groups.service import GroupChatService, get_group_chat_service

__all__ = [
    "GroupMessage",
    "GroupRoom",
    "OrchestrationMode",
    "MessageIntent",
    "GroupOrchestrator",
    "QuorumEngine",
    "Proposal",
    "GroupChatService",
    "get_group_chat_service",
]
