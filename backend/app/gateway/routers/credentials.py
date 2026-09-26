"""API router for Out-of-Band Secure Credential Shield.

Handles secure modal submissions and status checks for credentials requested
by agents during execution runs.

**Authorization**: the vault behind these routes is a process-global singleton
keyed only by ``thread_id`` (``alpha.security.credential_vault`` has no user
dimension), so the ``thread_id`` in the request is the only thing standing
between two users. Every route therefore resolves the caller and enforces
thread ownership before touching the vault. Without that check any
authenticated user could enumerate another user's pending credential requests,
inject a value the foreign agent would then consume, and destroy the real
user's pending prompts.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from alpha.security.credential_vault import get_credential_vault
from alpha.utils.thread_id import ThreadId
from app.gateway.authz import require_thread_owner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


class CredentialSubmitRequest(BaseModel):
    """Payload for submitting a credential from the secure modal."""

    thread_id: str = Field(..., min_length=1, description="Target conversation or execution thread ID")
    key: str = Field(..., min_length=1, description="Environment variable key")
    value: str = Field(..., min_length=1, description="Secret token or password")
    ttl_seconds: float = Field(default=3600.0, ge=60.0, le=86400.0 * 7, description="Time to live in seconds")


@router.post("/submit", summary="Submit Credential Out-of-Band")
async def submit_credential(req: CredentialSubmitRequest, request: Request) -> dict[str, Any]:
    """Deposit a secret into the in-memory vault for a specific thread."""
    # Write-side capability: a value deposited here is injected into the target
    # thread's sandbox environment, so this must be the owner's own thread.
    await require_thread_owner(request, req.thread_id)
    vault = get_credential_vault()
    vault.deposit_credential(
        thread_id=req.thread_id,
        key=req.key,
        value=req.value,
        ttl_seconds=req.ttl_seconds,
    )
    logger.info("Credential '%s' deposited securely for thread '%s'", req.key, req.thread_id)
    return {
        "status": "deposited",
        "thread_id": req.thread_id,
        "key": req.key,
        "expires_in_seconds": req.ttl_seconds,
    }


@router.get("/pending", summary="List Pending Credential Requests")
async def list_pending_credentials(request: Request, thread_id: ThreadId) -> list[dict[str, Any]]:
    """Query unfulfilled credential prompts for a thread."""
    await require_thread_owner(request, thread_id)
    vault = get_credential_vault()
    return vault.list_pending(thread_id=thread_id)


@router.delete("/clear", summary="Purge Thread Credentials")
async def clear_credentials(request: Request, thread_id: ThreadId) -> dict[str, Any]:
    """Purge all secrets for a finished thread."""
    # Destructive: require the thread row to exist and be owned, so a deleted
    # thread cannot be re-targeted by another user through the missing-row path.
    await require_thread_owner(request, thread_id, require_existing=True)
    vault = get_credential_vault()
    vault.clear_thread(thread_id=thread_id)
    return {"status": "cleared", "thread_id": thread_id}
