"""Lifecycle and policy owner for the Alpha peer network."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import os
import secrets
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .discovery import MdnsDiscovery, UdpDiscovery
from .github import GitHubRendezvous, GitHubRendezvousError
from .identity import LocalIdentity, prime_advertised_host
from .models import (
    MESSAGE_KIND_VALUES,
    ConversationCreateRequest,
    MessageCreateRequest,
    PeerCard,
    PeerEnvelope,
    PeerPairRequest,
    PeerPairResponse,
    PeerStatus,
    utc_now,
    validate_agent_id,
)
from .ratelimit import (
    ATTEMPTS_CEILING,
    DEFAULT_BASE_LOCKOUT_SECONDS,
    DEFAULT_BREAKER_COOLDOWN_SECONDS,
    DEFAULT_BREAKER_FAILURES,
    DEFAULT_GLOBAL_MAX_ATTEMPTS,
    DEFAULT_GLOBAL_WINDOW_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_FAILURES,
    DEFAULT_MAX_LOCKOUT_SECONDS,
    DEFAULT_MAX_TRACKED_KEYS,
    DEFAULT_WINDOW_SECONDS,
    FAILURES_CEILING,
    GLOBAL_ATTEMPTS_CEILING,
    LOCKOUT_CEILING_SECONDS,
    LOCKOUT_FLOOR_SECONDS,
    TRACKED_KEYS_CEILING,
    WINDOW_CEILING_SECONDS,
    WINDOW_FLOOR_SECONDS,
    PairingThrottle,
    ThrottleDecision,
)
from .storage import (
    DEFAULT_MAX_PEERS,
    MAX_PEERS_CEILING,
    NETWORK_OWNER,
    PeerNetworkStore,
    PeerRegistryFullError,
    token_digest,
)
from .transport import PeerNetworkDisabledError, PeerTransport, PeerTransportError, validate_endpoint

logger = logging.getLogger(__name__)

_DEFAULT_HOME = "peer_network"
_MAX_PARTICIPANTS = 50
_MAX_PAYLOAD_BYTES = 256 * 1024
_MAX_RECIPIENTS = 50

# The peer network is an *inbound* plane: `remote/pair`, `inbound/messages`, and
# `api/peer-network/ws` are mounted without a browser session, and UDP/mDNS
# discovery puts this installation on the LAN whether or not anyone asks. That
# makes "on" a production exposure decision, so the safe default is OFF and an
# operator opts in with ALPHA_PEER_NETWORK_ENABLED=1. See the module docstring
# of `ratelimit.py` for the ingress throttle that bounds the open plane.
_DEFAULT_ENABLED = False


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(os.getenv(name, str(default))), maximum))
    except ValueError:
        return default


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(float(os.getenv(name, str(default))), maximum))
    except ValueError:
        return default


def _runtime_home() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / _DEFAULT_HOME
    except Exception:
        return Path.cwd() / ".agent-workspace" / _DEFAULT_HOME


def _normalize_local_agent_id(service: PeerNetworkService, value: str | None) -> str:
    if value is None or value.strip().casefold() in {"", "operator", "local", "self"}:
        return service.identity.agent_id
    return validate_agent_id(value)


def _resolve_outbound_sender(
    service: PeerNetworkService,
    request: MessageCreateRequest,
    trusted_sender_id: str | None,
) -> str:
    """Resolve a server-owned sender; public callers cannot spoof an agent id."""

    if trusted_sender_id is not None:
        return validate_agent_id(trusted_sender_id)
    supplied = (request.sender_id or "").strip()
    if supplied and supplied.casefold() not in {"operator", "local", "self"}:
        raise ValueError("sender_id is server-owned; omit it or use the local Alpha identity")
    return service.identity.agent_id


def _conversation_id_for(participants: Iterable[str], mode: str) -> str:
    normalized = sorted(set(participants))
    digest = hashlib.sha256(f"{mode}|{','.join(normalized)}".encode()).hexdigest()[:24]
    return f"conv_{mode}_{digest}"


class PeerNetworkService:
    """Own discovery, pairing, delivery, and conversation state.

    The service is deliberately installation-scoped.  A single Alpha process
    normally serves one local operator; using one network owner keeps messages
    from a paired remote instance visible after a restart and avoids trusting a
    client-supplied owner field.  A future multi-tenant deployment can move the
    store's owner key without changing the wire protocol.
    """

    def __init__(
        self,
        home: str | Path | None = None,
        *,
        enabled: bool | None = None,
        discovery_port: int | None = None,
        discovery_interval_seconds: float | None = None,
        transport_timeout_seconds: float | None = None,
        version: str | None = None,
        max_peers: int | None = None,
        pairing_throttle: PairingThrottle | None = None,
    ):
        self.home = Path(home) if home is not None else _runtime_home()
        self.home.mkdir(parents=True, exist_ok=True)
        # Safe by default: an inbound plane is opt-in, not opt-out.
        self.enabled = _env_bool("ALPHA_PEER_NETWORK_ENABLED", _DEFAULT_ENABLED) if enabled is None else enabled
        self.identity = LocalIdentity.load_or_create(self.home, version=version)
        self.max_peers = _env_int(
            "ALPHA_PEER_NETWORK_MAX_PEERS",
            DEFAULT_MAX_PEERS,
            minimum=1,
            maximum=MAX_PEERS_CEILING,
        ) if max_peers is None else max(1, min(int(max_peers), MAX_PEERS_CEILING))
        self.store = PeerNetworkStore(self.home / "network.sqlite3")
        self.pairing_throttle = pairing_throttle if pairing_throttle is not None else self._build_pairing_throttle()
        self.transport = PeerTransport(timeout_seconds=transport_timeout_seconds if transport_timeout_seconds is not None else float(os.getenv("ALPHA_PEER_NETWORK_TIMEOUT_SECONDS", "8")))
        self.udp = UdpDiscovery(
            self,
            port=discovery_port if discovery_port is not None else _env_int("ALPHA_PEER_NETWORK_DISCOVERY_PORT", 8743, minimum=1, maximum=65535),
            interval_seconds=discovery_interval_seconds if discovery_interval_seconds is not None else float(os.getenv("ALPHA_PEER_NETWORK_DISCOVERY_INTERVAL_SECONDS", "8")),
            bind_host=os.getenv("ALPHA_PEER_NETWORK_BIND_HOST", "0.0.0.0"),
        )
        self.mdns = MdnsDiscovery(self, port=_env_int("ALPHA_PEER_NETWORK_HTTP_PORT", 8001, minimum=1, maximum=65535))
        self.github = GitHubRendezvous.from_env()
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._retry_task: asyncio.Task[None] | None = None
        self._started = False
        self._stop = asyncio.Event()
        self._last_error: str | None = None
        self._store_closed = False

    @staticmethod
    def _build_pairing_throttle() -> PairingThrottle:
        """Build the ingress throttle from operator config, hard-ceilinged.

        Every knob can tighten the throttle and none of them can raise it past
        the ceilings in ``ratelimit``: a setting that can switch off the control
        that protects an unauthenticated mutation is a bypass, not a setting.
        """

        return PairingThrottle(
            max_attempts=_env_int(
                "ALPHA_PEER_NETWORK_PAIR_MAX_ATTEMPTS",
                DEFAULT_MAX_ATTEMPTS,
                minimum=1,
                maximum=ATTEMPTS_CEILING,
            ),
            window_seconds=_env_float(
                "ALPHA_PEER_NETWORK_PAIR_WINDOW_SECONDS",
                DEFAULT_WINDOW_SECONDS,
                minimum=WINDOW_FLOOR_SECONDS,
                maximum=WINDOW_CEILING_SECONDS,
            ),
            max_failures=_env_int(
                "ALPHA_PEER_NETWORK_PAIR_MAX_FAILURES",
                DEFAULT_MAX_FAILURES,
                minimum=1,
                maximum=FAILURES_CEILING,
            ),
            base_lockout_seconds=_env_float(
                "ALPHA_PEER_NETWORK_PAIR_LOCKOUT_SECONDS",
                DEFAULT_BASE_LOCKOUT_SECONDS,
                minimum=LOCKOUT_FLOOR_SECONDS,
                maximum=LOCKOUT_CEILING_SECONDS,
            ),
            max_lockout_seconds=_env_float(
                "ALPHA_PEER_NETWORK_PAIR_MAX_LOCKOUT_SECONDS",
                DEFAULT_MAX_LOCKOUT_SECONDS,
                minimum=LOCKOUT_FLOOR_SECONDS,
                maximum=LOCKOUT_CEILING_SECONDS,
            ),
            global_max_attempts=_env_int(
                "ALPHA_PEER_NETWORK_PAIR_GLOBAL_MAX_ATTEMPTS",
                DEFAULT_GLOBAL_MAX_ATTEMPTS,
                minimum=1,
                maximum=GLOBAL_ATTEMPTS_CEILING,
            ),
            global_window_seconds=_env_float(
                "ALPHA_PEER_NETWORK_PAIR_GLOBAL_WINDOW_SECONDS",
                DEFAULT_GLOBAL_WINDOW_SECONDS,
                minimum=WINDOW_FLOOR_SECONDS,
                maximum=WINDOW_CEILING_SECONDS,
            ),
            breaker_failures=_env_int(
                "ALPHA_PEER_NETWORK_PAIR_BREAKER_FAILURES",
                DEFAULT_BREAKER_FAILURES,
                minimum=1,
                maximum=FAILURES_CEILING,
            ),
            breaker_cooldown_seconds=_env_float(
                "ALPHA_PEER_NETWORK_PAIR_BREAKER_COOLDOWN_SECONDS",
                DEFAULT_BREAKER_COOLDOWN_SECONDS,
                minimum=LOCKOUT_FLOOR_SECONDS,
                maximum=LOCKOUT_CEILING_SECONDS,
            ),
            max_tracked_keys=_env_int(
                "ALPHA_PEER_NETWORK_PAIR_MAX_TRACKED_KEYS",
                DEFAULT_MAX_TRACKED_KEYS,
                minimum=1,
                maximum=TRACKED_KEYS_CEILING,
            ),
        )

    def card(self) -> PeerCard:
        return self.identity.card()

    def public_identity(self, *, include_pairing_code: bool = False) -> dict[str, Any]:
        return self.identity.public_dict(include_pairing_code=include_pairing_code)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        # Resolve the advertised host before anything can ask for a card: the
        # public Agent Card routes and the UDP beacon both build one, and the
        # lookup is a blocking resolver call. Priming here keeps the very first
        # unauthenticated request off the resolver.
        with contextlib.suppress(Exception):
            await asyncio.to_thread(prime_advertised_host)
        if self.enabled:
            await self.udp.start()
            # mDNS is optional; its provider owns its own socket lifecycle.
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.mdns.start)
            self._retry_task = asyncio.create_task(self._retry_loop(), name="alpha-peer-delivery-retry")
        self._publish("network.started", self._status_snapshot())

    async def stop(self) -> None:
        self._stop.set()
        if self._retry_task:
            self._retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._retry_task
            self._retry_task = None
        await self.udp.stop()
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self.mdns.stop)
        for queue in tuple(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait({"type": "network.stopped", "at": utc_now()})
        self._subscribers.clear()
        if not self._store_closed:
            self.store.close()
            self._store_closed = True
        self._started = False

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def _publish(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        event = {"type": event_type, "at": utc_now(), "data": payload or {}}
        for queue in tuple(self._subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    async def _run_store(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)

    def observe_discovery(self, payload: dict[str, Any], *, source: str) -> PeerCard | None:
        """Validate and persist a discovery beacon.

        Discovery is untrusted.  It never authenticates a peer or grants a
        conversation capability; pairing is a separate explicit operation.
        """

        if payload.get("agent_id") == self.identity.agent_id:
            return None
        if payload.get("protocol") not in {None, "alpha-a2a"}:
            return None
        if payload.get("protocol_version") not in {None, "1.0"}:
            return None
        try:
            card = PeerCard.model_validate(payload)
            validate_endpoint(card.url, allowed_schemes=("http", "https"))
            if card.websocket_url:
                validate_endpoint(card.websocket_url, allowed_schemes=("ws", "wss"))
        except (ValueError, TypeError) as exc:
            logger.debug("Ignoring malformed peer discovery from %s: %s", source, exc)
            return None

        def _save() -> dict[str, Any] | None:
            try:
                return self.store.upsert_peer(
                    card=card.to_dict(),
                    source=source,
                    trust="discovered",
                    max_peers=self.max_peers,
                )
            except PeerRegistryFullError as exc:
                # The registry is bounded on purpose: an unauthenticated beacon
                # that arrives when the ceiling is reached is dropped, never
                # traded for the loss of a paired peer.
                logger.debug("Not retaining discovery beacon from %s: %s", source, exc)
                return None

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(asyncio.to_thread(_save))
        except RuntimeError:
            _save()
        return card

    async def discover(self) -> list[dict[str, Any]]:
        """Trigger local discovery and return the current server-owned view."""

        if self.enabled:
            await self.udp.broadcast_once()
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.mdns.start)
        if self.enabled and self.github.configured:
            try:
                cards = await asyncio.wait_for(self.github.list_cards(), timeout=20)
                for card in cards:
                    self.observe_discovery(card.to_dict(), source="github")
            except (GitHubRendezvousError, TimeoutError) as exc:
                logger.info("GitHub peer rendezvous unavailable: %s", exc)
        peers = await self.list_peers()
        self._publish("discovery.completed", {"count": len(peers)})
        return peers

    async def list_peers(self, *, skill: str | None = None, trust: str | None = None) -> list[dict[str, Any]]:
        return await self._run_store(self.store.list_peers, skill=skill, trust=trust)

    async def get_peer(self, agent_id: str, *, include_secret: bool = False) -> dict[str, Any] | None:
        return await self._run_store(self.store.get_peer, agent_id, include_secret=include_secret)

    async def get_peer_by_token(self, token: str, agent_id: str | None = None) -> dict[str, Any] | None:
        """Resolve a peer token for the inbound plane, or ``None``.

        ``None`` is the refusal the WebSocket handshake turns into a 1008
        close, so a closed plane, an unknown token, and a blocked peer are all
        indistinguishable to an unauthenticated caller.
        """

        if not self.enabled:
            return None
        return await self._run_store(self.store.find_peer_by_token, token, agent_id=agent_id)

    async def set_trust(self, agent_id: str, trust: str) -> dict[str, Any] | None:
        if trust == "paired":
            raise ValueError("Use the explicit pairing flow; trust updates cannot grant credentials")
        if trust not in {"discovered", "blocked"}:
            raise ValueError("trust must be discovered or blocked")
        result = await self._run_store(self.store.set_peer_trust, agent_id, trust)
        self._publish("peer.trust_changed", {"agent_id": agent_id, "trust": trust})
        return result

    async def rotate_pairing_code(self) -> str:
        code = await self._run_store(self.identity.rotate_pairing_code)
        self._publish("pairing.rotated", {"rotated": True})
        return code

    async def pair(self, endpoint: str, pairing_code: str, expected_agent_id: str | None = None) -> dict[str, Any]:
        """Pair with a remote card using an out-of-band shared code.

        The code is sent to the remote pair route, which validates it against
        its own identity.  The same code becomes the local bearer credential;
        it is never placed in the Agent Card or a discovery beacon.
        """

        validate_endpoint(endpoint, allowed_schemes=("http", "https"))
        if expected_agent_id:
            expected_agent_id = validate_agent_id(expected_agent_id)
        if len(pairing_code) < 16:
            raise ValueError("Pairing code must contain at least 16 characters")
        card = await self.transport.fetch_card(endpoint)
        if expected_agent_id and card.agent_id != expected_agent_id:
            raise ValueError(f"Peer identity mismatch: expected {expected_agent_id}, received {card.agent_id}")
        request = PeerPairRequest(card=self.card(), pairing_code=pairing_code)
        response = await self.transport.pair(endpoint, request)
        if response.get("accepted") is not True:
            raise PeerTransportError(str(response.get("message") or "Remote peer rejected pairing"))
        remote_card = response.get("peer")
        if not isinstance(remote_card, dict):
            remote_card = card.to_dict()
        try:
            remote = PeerCard.model_validate(remote_card)
        except ValueError as exc:
            raise PeerTransportError("Remote peer returned an invalid Agent Card after pairing") from exc
        saved = await self._run_store(
            self.store.upsert_peer,
            card=remote.to_dict(),
            source="manual-pair",
            trust="paired",
            outbound_token=pairing_code,
            paired=True,
        )
        self._publish("peer.paired", {"agent_id": remote.agent_id, "transport": "pairing"})
        return saved

    async def publish_github_card(self) -> dict[str, Any]:
        if not self.github.writable:
            raise ValueError("GitHub rendezvous writes require repository configuration and a token")
        return await self.github.publish_card(self.card())

    def _pairing_card_problem(self, card: PeerCard) -> str | None:
        """Return why a remote pairing card is unacceptable, or ``None``.

        A pairing card arrives unauthenticated.  It is validated exactly like a
        discovery beacon — and, unlike a beacon, it becomes a *paired* row with
        a stored credential — plus one rule a beacon does not need: a peer may
        not claim this installation's own agent id.  Without that check a caller
        holding the code could register a second "us" pointing at an endpoint it
        controls and win the local agent id in the peer table.
        """

        if card.agent_id == self.identity.agent_id:
            return "a peer cannot claim this installation's agent id"
        try:
            validate_endpoint(card.url, allowed_schemes=("http", "https"))
            if card.websocket_url:
                validate_endpoint(card.websocket_url, allowed_schemes=("ws", "wss"))
        except (PeerTransportError, ValueError) as exc:
            return str(exc)
        return None

    def _throttled_response(self, decision: ThrottleDecision) -> PeerPairResponse:
        logger.info(
            "Refused an Alpha peer pairing attempt (%s); retry in %.1fs",
            decision.reason,
            decision.retry_after_seconds,
        )
        self._publish(
            "pairing.throttled",
            {"reason": decision.reason, "retry_after_seconds": round(decision.retry_after_seconds, 3)},
        )
        return PeerPairResponse(
            accepted=False,
            peer=None,
            message=decision.as_message(),
            retry_after_seconds=max(1, int(math.ceil(decision.retry_after_seconds))),
        )

    def _rejected_response(self, message: str) -> PeerPairResponse:
        return PeerPairResponse(accepted=False, peer=None, message=message)

    async def accept_pair(self, request: PeerPairRequest) -> PeerPairResponse:
        """Validate, throttle, and register a remote pairing request.

        This is the unauthenticated mutating ingress.  The order below is the
        security order, and every step is a refusal rather than a partial
        mutation:

        1. the plane must be enabled (off by default),
        2. the bounded throttle must admit the attempt — a throttle that cannot
           answer refuses, it never fails open,
        3. the card must be a well-formed, non-self-claiming, reachable-looking
           Agent Card,
        4. the pairing code must match in constant time,
        5. the bounded peer registry must have room.

        Steps 3-5 each count as a failed attempt, so a flood of well-formed
        cards with wrong codes is bounded by the same budget as a flood of
        malformed ones.
        """

        if not self.enabled:
            self._publish("pairing.refused", {"reason": "peer network disabled"})
            return self._rejected_response("Alpha peer network is disabled; the operator opted out of the inbound plane")
        key = request.card.agent_id
        try:
            decision = self.pairing_throttle.check(key)
        except Exception:
            # Fail closed: a throttle that cannot decide must not become an
            # unlimited unauthenticated write path.
            logger.exception("Alpha peer pairing throttle failed; refusing the attempt")
            return self._rejected_response("Pairing is temporarily unavailable")
        if decision is not None:
            return self._throttled_response(decision)

        def _refuse(message: str) -> PeerPairResponse:
            self.pairing_throttle.record_failure(key)
            return self._rejected_response(message)

        card = request.card
        problem = self._pairing_card_problem(card)
        if problem is not None:
            return _refuse(f"Pairing rejected: {problem}")
        if not secrets.compare_digest(token_digest(request.pairing_code), token_digest(self.identity.pairing_code)):
            return _refuse("Pairing code rejected")
        try:
            await self._run_store(
                self.store.upsert_peer,
                card=card.to_dict(),
                source="remote-pair",
                trust="paired",
                outbound_token=request.pairing_code,
                paired=True,
                max_peers=self.max_peers,
            )
        except PeerRegistryFullError as exc:
            return _refuse(f"Pairing rejected: {exc}")
        self.pairing_throttle.record_success(key)
        self._publish("peer.paired", {"agent_id": card.agent_id, "transport": "remote"})
        return PeerPairResponse(accepted=True, peer=self.card(), message="Peer paired", paired_at=utc_now())

    def _normalize_participants(self, values: Iterable[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = _normalize_local_agent_id(self, value)
            if normalized not in result:
                result.append(normalized)
        if not result:
            raise ValueError("At least one participant is required")
        if len(result) > _MAX_PARTICIPANTS:
            raise ValueError(f"A conversation supports at most {_MAX_PARTICIPANTS} participants")
        return result

    def _validate_mode(self, mode: str, participants: list[str]) -> str:
        if mode not in {"direct", "one_to_many", "many_to_one", "many_to_many", "broadcast", "inbox"}:
            raise ValueError("Unsupported conversation mode")
        if mode == "direct" and len(participants) != 2:
            raise ValueError("Direct conversations require exactly two participants")
        if mode != "inbox" and len(participants) < 2:
            raise ValueError("Group conversation modes require at least two participants")
        return mode

    async def create_conversation(self, request: ConversationCreateRequest) -> dict[str, Any]:
        participants = self._normalize_participants(request.participants)
        # The local installation is always a participant in a locally-created
        # session. This makes a UI selection of one remote peer a real direct
        # conversation and prevents the sender from being omitted on restart.
        if self.identity.agent_id not in participants:
            participants.insert(0, self.identity.agent_id)
        mode = self._validate_mode(request.mode, participants)
        conversation_id = _conversation_id_for(participants, mode)
        existing = await self._run_store(self.store.get_conversation, conversation_id)
        if existing:
            return existing
        result = await self._run_store(
            self.store.create_conversation,
            conversation_id=conversation_id,
            mode=mode,
            title=request.title.strip() or "Alpha peer conversation",
            participants=participants,
            metadata=request.metadata,
        )
        self._publish("conversation.created", result)
        return result

    async def list_conversations(self) -> list[dict[str, Any]]:
        return await self._run_store(self.store.list_conversations)

    async def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        return await self._run_store(self.store.get_conversation, conversation_id)

    async def get_messages(self, conversation_id: str, limit: int = 200) -> list[dict[str, Any]]:
        if await self.get_conversation(conversation_id) is None:
            raise KeyError(conversation_id)
        return await self._run_store(self.store.list_messages, conversation_id, limit=limit)

    async def mark_read(self, message_id: str) -> dict[str, Any] | None:
        result = await self._run_store(self.store.get_message, message_id)
        if result is None:
            return None
        await self._run_store(self.store.mark_message_read, message_id, recipient_id=self.identity.agent_id)
        self._publish("message.read", {"message_id": message_id})
        return await self._run_store(self.store.get_message, message_id)

    async def send_message(
        self,
        request: MessageCreateRequest,
        *,
        trusted_sender_id: str | None = None,
    ) -> dict[str, Any]:
        sender = _resolve_outbound_sender(self, request, trusted_sender_id)
        if request.kind.strip().lower() not in MESSAGE_KIND_VALUES:
            raise ValueError("Unsupported message kind")
        if len(request.text.encode("utf-8")) > 20000:
            raise ValueError("Message text exceeds 20,000 UTF-8 bytes")
        if len(json.dumps(request.payload, ensure_ascii=False).encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise ValueError("Message payload exceeds 256 KiB")

        conversation: dict[str, Any] | None = None
        if request.conversation_id:
            conversation = await self.get_conversation(request.conversation_id)
            if conversation is None:
                raise KeyError(request.conversation_id)
            participants = list(conversation["participants"])
            if sender not in participants:
                if trusted_sender_id is None:
                    raise ValueError("sender is not a participant in this conversation")
                await self._run_store(self.store.add_participant, conversation["conversation_id"], sender)
                participants.append(sender)
            recipients = self._normalize_participants(request.recipients) if request.recipients else [p for p in participants if p != sender]
            if any(recipient not in participants for recipient in recipients):
                raise ValueError("All recipients must be participants in the selected conversation")
        else:
            recipients = self._normalize_participants(request.recipients) if request.recipients else []
            if not recipients:
                raise ValueError("recipients are required when conversation_id is omitted")
            participants = list(dict.fromkeys([sender, *recipients]))
            mode = request.mode or ("direct" if len(participants) == 2 else "many_to_many")
            mode = self._validate_mode(mode, participants)
            conversation_id = _conversation_id_for(participants, mode)
            conversation = await self.get_conversation(conversation_id)
            if conversation is None:
                conversation = await self._run_store(
                    self.store.create_conversation,
                    conversation_id=conversation_id,
                    mode=mode,
                    title=(request.title or "Alpha peer conversation").strip(),
                    participants=participants,
                )

        recipients = list(dict.fromkeys(recipient for recipient in recipients if recipient != sender))
        if not recipients:
            recipients = [participant for participant in conversation["participants"] if participant != sender]
        if not recipients:
            raise ValueError("A message must have at least one recipient other than its sender")
        if len(recipients) > _MAX_RECIPIENTS:
            raise ValueError("Too many recipients")

        envelope = PeerEnvelope(
            kind=request.kind,
            sender_id=sender,
            recipients=recipients,
            conversation_id=conversation["conversation_id"],
            text=request.text,
            payload=request.payload,
            idempotency_key=request.idempotency_key,
        )
        message = await self._run_store(
            self.store.add_message,
            message_id=envelope.id,
            conversation_id=conversation["conversation_id"],
            sender_id=sender,
            recipients=recipients,
            kind=envelope.kind,
            text=envelope.text,
            payload=envelope.payload,
            direction="outbound",
            status="queued",
            remote_id=envelope.id,
            idempotency_key=envelope.idempotency_key,
        )
        if request.idempotency_key and message.get("idempotency_key") == request.idempotency_key and message.get("message_id") != envelope.id:
            return message
        for recipient in recipients:
            if recipient == self.identity.agent_id:
                await self._run_store(self.store.update_delivery, envelope.id, recipient, status="delivered", transport="local")
                continue
            peer = await self.get_peer(recipient, include_secret=True)
            if not peer or peer.get("trust") != "paired" or not peer.get("url"):
                await self._run_store(
                    self.store.update_delivery,
                    envelope.id,
                    recipient,
                    status="queued",
                    transport="offline",
                    error="Peer is not paired or has no reachable endpoint",
                )
                continue
            try:
                result = await self.transport.send(peer, envelope, str(peer.get("outbound_token") or ""))
                await self._run_store(self.store.update_delivery, envelope.id, recipient, status="delivered", transport=result.transport)
            except PeerTransportError as exc:
                await self._run_store(self.store.update_delivery, envelope.id, recipient, status="queued", transport="offline", error=str(exc))
        final_message = await self._run_store(self.store.get_message, envelope.id)
        self._publish("message.outbound", final_message)
        return final_message  # type: ignore[return-value]

    async def receive_remote(self, envelope: PeerEnvelope, token: str) -> dict[str, Any]:
        """Accept one envelope from a paired peer on the inbound plane.

        ``PeerNetworkDisabledError`` is a ``PeerTransportError``, which is the
        refusal the public ingress route already renders as 401, so a closed
        plane can never look like a delivery.
        """

        if not self.enabled:
            raise PeerNetworkDisabledError("Alpha peer network is disabled; the operator opted out of the inbound plane")
        peer = await self._run_store(self.store.find_peer_by_token, token, agent_id=envelope.sender_id)
        if peer is None or peer.get("agent_id") != envelope.sender_id:
            raise PeerTransportError("Peer token does not match the sender")
        if envelope.protocol != "alpha-a2a" or envelope.protocol_version != "1.0":
            raise PeerTransportError("Unsupported peer protocol version")
        if self.identity.agent_id not in envelope.recipients:
            raise PeerTransportError("Message is not addressed to this Alpha")
        existing_message = await self._run_store(self.store.get_message, envelope.id)
        if existing_message is not None:
            return existing_message
        if len(envelope.text.encode("utf-8")) > 20000 or len(json.dumps(envelope.payload, ensure_ascii=False).encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise PeerTransportError("Message exceeds the peer network size limit")

        conversation = None
        if envelope.conversation_id:
            conversation = await self.get_conversation(envelope.conversation_id)
        if conversation is None:
            # Unaddressed remote mail lands in one installation inbox. This is
            # what makes many-to-one useful: independently paired senders do
            # not need to know a private local conversation id. A sender that
            # has a pre-created shared group can still send its id explicitly.
            conversation_id = _conversation_id_for([self.identity.agent_id], "inbox")
            conversation = await self.get_conversation(conversation_id)
            if conversation is None:
                conversation = await self._run_store(
                    self.store.create_conversation,
                    conversation_id=conversation_id,
                    mode="inbox",
                    title="Alpha peer inbox",
                    participants=[self.identity.agent_id, envelope.sender_id],
                    metadata={"remote": True},
                )
            elif envelope.sender_id not in conversation["participants"]:
                # The inbox is a single installation-wide conversation, so every
                # distinct remote sender would otherwise grow it without bound
                # and past the documented participant limit. Cap it here, where
                # the limit lives, instead of trusting the repository.
                if len(conversation["participants"]) >= _MAX_PARTICIPANTS:
                    raise PeerTransportError(
                        f"The peer inbox already holds the maximum of {_MAX_PARTICIPANTS} participants"
                    )
                await self._run_store(self.store.add_participant, conversation_id, envelope.sender_id)
                conversation = await self.get_conversation(conversation_id)
                if conversation is None:
                    raise PeerTransportError("Peer inbox could not be updated")
        # A remote conversation id is not an authority to cross the local
        # installation boundary; verify the sender/local participant.
        if envelope.sender_id not in conversation["participants"] or self.identity.agent_id not in conversation["participants"]:
            raise PeerTransportError("Conversation participants do not authorize this message")
        message = await self._run_store(
            self.store.add_message,
            message_id=envelope.id,
            conversation_id=conversation["conversation_id"],
            sender_id=envelope.sender_id,
            recipients=envelope.recipients,
            kind=envelope.kind,
            text=envelope.text,
            payload=envelope.payload,
            direction="inbound",
            status="delivered",
            remote_id=envelope.id,
            delivery_status="delivered",
            delivery_recipients=[self.identity.agent_id],
        )
        self._publish("message.inbound", message)
        return message  # type: ignore[return-value]

    async def retry_pending(self) -> int:
        pending = await self._run_store(self.store.pending_messages)
        attempted = 0
        for message in pending:
            for delivery in message.get("deliveries", []):
                if delivery.get("status") != "queued":
                    continue
                recipient = delivery.get("recipient_id")
                if not recipient or recipient == self.identity.agent_id:
                    continue
                peer = await self.get_peer(recipient, include_secret=True)
                if not peer or peer.get("trust") != "paired":
                    continue
                envelope = PeerEnvelope(
                    id=message["message_id"],
                    kind=message["kind"],
                    sender_id=message["sender_id"],
                    recipients=message["recipients"],
                    conversation_id=message["conversation_id"],
                    text=message["text"],
                    payload=message["payload"],
                    created_at=message["created_at"],
                    idempotency_key=message.get("idempotency_key"),
                )
                try:
                    result = await self.transport.send(peer, envelope, str(peer.get("outbound_token") or ""))
                    await self._run_store(self.store.update_delivery, message["message_id"], recipient, status="delivered", transport=result.transport)
                    attempted += 1
                except PeerTransportError as exc:
                    await self._run_store(self.store.update_delivery, message["message_id"], recipient, status="queued", transport="offline", error=str(exc))
        return attempted

    async def _retry_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=15)
            except TimeoutError:
                try:
                    await self.retry_pending()
                except Exception:
                    logger.warning("Alpha peer delivery retry failed", exc_info=True)

    def _status_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "identity": self.public_identity(),
            "discovery": {
                "udp": {
                    "enabled": self.enabled,
                    "running": self.udp.running,
                    "port": self.udp.port,
                    "last_error": self.udp.last_error,
                },
                "mdns": {
                    "available": self.mdns.available,
                    "running": self.mdns.running,
                    "last_error": self.mdns.last_error,
                },
                "github": self.github.status(),
                "libp2p": {
                    "available": False,
                    "running": False,
                    "last_error": "No maintained Python libp2p implementation is bundled; use the HTTP/WebSocket direct path or provide an external bridge.",
                },
            },
            "transports": {
                "http": {"enabled": True, "free": True, "server_required": False},
                "websocket": {"enabled": True, "free": True, "server_required": False},
                "sqlite": {"enabled": True, "free": True, "server_required": False},
            },
            "persistence": {"backend": "sqlite", "path": str(self.store.path), "durable": True},
            "limits": {
                "max_participants": _MAX_PARTICIPANTS,
                "max_recipients": _MAX_RECIPIENTS,
                "max_text_bytes": 20000,
                "max_payload_bytes": _MAX_PAYLOAD_BYTES,
                "max_peers": self.max_peers,
                "pair_max_attempts": self.pairing_throttle.max_attempts,
                "pair_window_seconds": int(self.pairing_throttle.window_seconds),
                "pair_global_max_attempts": self.pairing_throttle.global_max_attempts,
                "pair_global_window_seconds": int(self.pairing_throttle.global_window_seconds),
            },
            # Throttle policy and counters for the unauthenticated pairing
            # route. Credential-free by construction: attempt/failure counts
            # only, never a code, a token, or a throttle key.
            "pairing": {
                "enabled": self.enabled,
                "ingress": "open" if self.enabled else "closed",
                "throttle": self.pairing_throttle.snapshot(),
            },
        }

    async def status(self, *, include_pairing_code: bool = False) -> PeerStatus:
        snapshot = self._status_snapshot()
        if include_pairing_code:
            snapshot["pairing_code"] = self.identity.pairing_code
        return PeerStatus.model_validate(snapshot)


_service: PeerNetworkService | None = None
_service_path: str | None = None


def get_peer_network_service(home: str | Path | None = None) -> PeerNetworkService:
    """Return the process-wide service, rebuilding when runtime home changes."""

    global _service, _service_path
    resolved = str(Path(home).resolve()) if home is not None else str(_runtime_home().resolve())
    if _service is None or _service_path != resolved or _service._store_closed:
        if _service is not None and not _service._store_closed:
            with contextlib.suppress(Exception):
                _service.store.close()
        _service = PeerNetworkService(resolved)
        _service_path = resolved
    return _service


async def shutdown_peer_network_service() -> None:
    global _service, _service_path
    if _service is not None:
        await _service.stop()
    _service = None
    _service_path = None


__all__ = [
    "NETWORK_OWNER",
    "PairingThrottle",
    "PeerNetworkService",
    "get_peer_network_service",
    "shutdown_peer_network_service",
]
