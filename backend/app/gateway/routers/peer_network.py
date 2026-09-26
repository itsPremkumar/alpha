"""Gateway API and public peering endpoints for Alpha-to-Alpha communication."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from alpha.peer_network import (
    ConversationCreateRequest,
    MessageCreateRequest,
    PairRequest,
    PeerEnvelope,
    PeerPairRequest,
    PeerPairResponse,
    PeerStatus,
    get_peer_network_service,
)
from alpha.peer_network.github import GitHubRendezvousError
from alpha.peer_network.transport import PeerTransportError
from app.gateway.authz import require_permission
from app.gateway.deps import require_admin_user

logger = logging.getLogger(__name__)
_MAX_PUBLIC_BODY_BYTES = 512 * 1024
router = APIRouter(prefix="/api/peer-network", tags=["peer-network"])
public_router = APIRouter(tags=["peer-network-public"])


class TrustBody(BaseModel):
    trust: str = Field(..., pattern="^(discovered|blocked)$")


class ReadBody(BaseModel):
    recipient_id: str | None = Field(default=None, max_length=128)


def _service():
    return get_peer_network_service()


def _check_public_body_size(request: Request) -> None:
    raw_length = request.headers.get("content-length")
    if not raw_length:
        return
    try:
        if int(raw_length) > _MAX_PUBLIC_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Peer network request exceeds the 512 KiB limit")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=f"Peer network record '{exc.args[0]}' not found")
    if isinstance(exc, PeerTransportError):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, GitHubRendezvousError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    logger.exception("Peer network request failed")
    return HTTPException(status_code=500, detail="Peer network operation failed")


async def _call(operation: Any):
    try:
        return await operation
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/status", response_model=PeerStatus, summary="Read peer network status")
@require_permission("threads", "read")
async def get_status() -> PeerStatus:
    return await _call(_service().status(include_pairing_code=True))


@router.get("/peers", summary="List discovered and paired Alpha peers")
@require_permission("threads", "read")
async def list_peers(skill: str | None = None, trust: str | None = None) -> dict[str, Any]:
    peers = await _call(_service().list_peers(skill=skill, trust=trust))
    return {"peers": peers, "count": len(peers)}


@router.post("/discover", summary="Broadcast a LAN discovery beacon")
@require_permission("threads", "read")
async def discover_peers() -> dict[str, Any]:
    peers = await _call(_service().discover())
    return {"peers": peers, "count": len(peers), "discovery": "local-first"}


@router.post("/github/publish", summary="Publish this Agent Card to the configured GitHub rendezvous")
@require_permission("threads", "write")
async def publish_github_card(request: Request) -> dict[str, Any]:
    await require_admin_user(request)
    result = await _call(_service().publish_github_card())
    return {"published": True, "result": result}


@router.post("/pair", summary="Pair with a manually addressed Alpha peer")
@require_permission("threads", "write")
async def pair_peer(body: PairRequest) -> dict[str, Any]:
    peer = await _call(_service().pair(body.endpoint, body.pairing_code, body.expected_agent_id))
    return {"status": "paired", "peer": peer}


@router.post("/pair/rotate", summary="Rotate this installation's pairing code")
@require_permission("threads", "write")
async def rotate_pairing_code() -> dict[str, Any]:
    code = await _call(_service().rotate_pairing_code())
    return {"status": "rotated", "pairing_code": code}


@router.patch("/peers/{agent_id}/trust", summary="Change peer trust state")
@require_permission("threads", "write")
async def set_peer_trust(agent_id: str, body: TrustBody) -> dict[str, Any]:
    peer = await _call(_service().set_trust(agent_id, body.trust))
    if peer is None:
        raise HTTPException(status_code=404, detail=f"Peer '{agent_id}' not found")
    return {"peer": peer}


@router.post("/conversations", status_code=201, summary="Create a typed peer conversation")
@require_permission("threads", "write")
async def create_conversation(body: ConversationCreateRequest) -> dict[str, Any]:
    return await _call(_service().create_conversation(body))


@router.get("/conversations", summary="List peer conversations")
@require_permission("threads", "read")
async def list_conversations() -> dict[str, Any]:
    conversations = await _call(_service().list_conversations())
    return {"conversations": conversations, "count": len(conversations)}


@router.get("/conversations/{conversation_id}", summary="Read a peer conversation")
@require_permission("threads", "read")
async def get_conversation(conversation_id: str) -> dict[str, Any]:
    conversation = await _call(_service().get_conversation(conversation_id))
    if conversation is None:
        raise HTTPException(status_code=404, detail=f"Conversation '{conversation_id}' not found")
    return conversation


@router.get("/conversations/{conversation_id}/messages", summary="Read bounded conversation history")
@require_permission("threads", "read")
async def get_conversation_messages(conversation_id: str, limit: int = 200) -> dict[str, Any]:
    messages = await _call(_service().get_messages(conversation_id, limit=max(1, min(limit, 1000))))
    return {"conversation_id": conversation_id, "messages": messages, "count": len(messages)}


@router.post("/conversations/{conversation_id}/messages", status_code=201, summary="Send into a conversation")
@require_permission("threads", "write")
async def send_conversation_message(conversation_id: str, body: MessageCreateRequest) -> dict[str, Any]:
    body.conversation_id = conversation_id
    return await _call(_service().send_message(body))


@router.post("/messages", status_code=201, summary="Send a direct, broadcast, or group message")
@require_permission("threads", "write")
async def send_message(body: MessageCreateRequest) -> dict[str, Any]:
    return await _call(_service().send_message(body))


@router.post("/messages/{message_id}/read", summary="Mark a peer message read")
@require_permission("threads", "write")
async def mark_message_read(message_id: str, body: ReadBody | None = None) -> dict[str, Any]:
    result = await _call(_service().mark_read(message_id))
    if result is None:
        raise HTTPException(status_code=404, detail=f"Message '{message_id}' not found")
    return {"message": result}


@router.get("/events", summary="Stream local peer-network events")
@require_permission("threads", "read")
async def stream_events(request: Request) -> StreamingResponse:
    service = _service()
    queue = service.subscribe()

    async def _events():
        try:
            yield f"event: ready\ndata: {json.dumps({'type': 'ready'})}\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            service.unsubscribe(queue)

    return StreamingResponse(_events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# The following endpoints are intentionally reachable without a browser session.
# Each mutating/remote endpoint performs its own pairing-token validation in the
# service; the Gateway auth/CSRF middleware must classify them as public first.
@public_router.get("/.well-known/agent-card.json", summary="A2A-style Alpha Agent Card")
async def public_agent_card() -> dict[str, Any]:
    return _service().card().to_dict()


@public_router.get("/.well-known/agent.json", include_in_schema=False)
async def public_agent_card_legacy_alias() -> dict[str, Any]:
    return _service().card().to_dict()


@public_router.get("/api/peer-network/card", include_in_schema=False)
async def public_agent_card_fallback() -> dict[str, Any]:
    return _service().card().to_dict()


@public_router.post("/api/peer-network/remote/pair", response_model=PeerPairResponse, summary="Accept an Alpha peer pairing")
async def remote_pair(request: Request, body: PeerPairRequest) -> PeerPairResponse:
    _check_public_body_size(request)
    return await _service().accept_pair(body)


@public_router.post("/api/peer-network/inbound/messages", summary="Receive a paired peer message")
async def inbound_message(request: Request, body: PeerEnvelope) -> dict[str, Any]:
    _check_public_body_size(request)
    token = request.headers.get("x-alpha-peer-token", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="X-Alpha-Peer-Token is required")
    try:
        return await _service().receive_remote(body, token)
    except PeerTransportError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise _http_error(exc) from exc


@public_router.websocket("/api/peer-network/ws")
async def peer_websocket(websocket: WebSocket) -> None:
    token = websocket.headers.get("x-alpha-peer-token", "").strip()
    if not token:
        await websocket.close(code=1008, reason="X-Alpha-Peer-Token is required")
        return
    service = _service()
    peer = await service.get_peer_by_token(token)
    if peer is None:
        await websocket.close(code=1008, reason="Peer token is not paired")
        return
    await websocket.accept()
    try:
        await websocket.send_json({"type": "connected", "peer_id": peer["agent_id"]})
        while True:
            try:
                raw = await websocket.receive_json()
            except (WebSocketDisconnect, ValueError):
                return
            try:
                envelope = PeerEnvelope.model_validate(raw)
                result = await service.receive_remote(envelope, token)
                await websocket.send_json({"type": "ack", "message_id": result["message_id"]})
            except PeerTransportError as exc:
                await websocket.send_json({"type": "error", "detail": str(exc)})
            except Exception as exc:
                await websocket.send_json({"type": "error", "detail": str(exc)})
    except WebSocketDisconnect:
        return


__all__ = ["public_router", "router"]
