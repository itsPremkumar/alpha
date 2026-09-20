"""Universal Agent Protocol Adapters (A2A, MCP Tasks)."""

from alpha.protocols.a2a import (
    A2ADelegationRequest,
    A2ADelegationResponse,
    A2AProtocolAdapter,
    AgentCapabilityCard,
)
from alpha.protocols.mcp_tasks import (
    MCPTaskManager,
    MCPTaskSpec,
    MCPTaskState,
    MCPTaskStatus,
)

__all__ = [
    "AgentCapabilityCard",
    "A2ADelegationRequest",
    "A2ADelegationResponse",
    "A2AProtocolAdapter",
    "MCPTaskState",
    "MCPTaskSpec",
    "MCPTaskStatus",
    "MCPTaskManager",
]
