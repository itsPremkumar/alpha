"""Local-first Alpha-to-Alpha peer network.

The package is intentionally transport- and discovery-agnostic.  The Gateway
router owns HTTP/WebSocket exposure; this package owns identity, pairing,
persistence, discovery, delivery, and conversation semantics.
"""

from .github import GitHubRendezvous, GitHubRendezvousError
from .models import (
    PROTOCOL,
    PROTOCOL_VERSION,
    ConversationCreateRequest,
    MessageCreateRequest,
    PairRequest,
    PeerCard,
    PeerEnvelope,
    PeerPairRequest,
    PeerPairResponse,
    PeerStatus,
)
from .ratelimit import PairingThrottle, ThrottleDecision
from .service import PeerNetworkService, get_peer_network_service, shutdown_peer_network_service
from .storage import NETWORK_OWNER, PeerRegistryFullError
from .transcript import (
    MAX_TRANSCRIPT_ENTRIES,
    MAX_TURN_EVENTS,
    interleave,
    is_peer_run,
    public_peer_summary,
    runs_for_conversation,
    turn_detail,
)
from .transport import PeerNetworkDisabledError, PeerTransportError

__all__ = [
    "MAX_TRANSCRIPT_ENTRIES",
    "MAX_TURN_EVENTS",
    "NETWORK_OWNER",
    "ConversationCreateRequest",
    "GitHubRendezvous",
    "GitHubRendezvousError",
    "MessageCreateRequest",
    "PROTOCOL",
    "PROTOCOL_VERSION",
    "PairRequest",
    "PairingThrottle",
    "PeerCard",
    "PeerEnvelope",
    "PeerNetworkDisabledError",
    "PeerNetworkService",
    "PeerPairRequest",
    "PeerPairResponse",
    "PeerRegistryFullError",
    "PeerStatus",
    "PeerTransportError",
    "ThrottleDecision",
    "get_peer_network_service",
    "interleave",
    "is_peer_run",
    "public_peer_summary",
    "runs_for_conversation",
    "shutdown_peer_network_service",
    "turn_detail",
]
