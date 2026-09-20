"""API router for Out-of-Band Secure Credential Shield.

Handles secure modal submissions and status checks for credentials requested
by agents during execution runs.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from alpha.security.credential_vault import get_credential_vault
from alpha.utils.thread_id import ThreadId

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


class CredentialSubmitRequest(BaseModel):
    """Payload for submitting a credential from the secure modal."""

    thread_id: str = Field(..., min_length=1, description="Target conversation or execution thread ID")
    key: str = Field(..., min_length=1, description="Environment variable key")
    value: str = Field(..., min_length=1, description="Secret token or password")
    ttl_seconds: float = Field(default=3600.0, ge=60.0, le=86400.0 * 7, description="Time to live in seconds")


@router.post("/submit", summary="Submit Credential Out-of-Band")
def submit_credential(req: CredentialSubmitRequest) -> dict[str, Any]:
    """Deposit a secret into the in-memory vault for a specific thread."""
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
def list_pending_credentials(thread_id: ThreadId) -> list[dict[str, Any]]:
    """Query unfulfilled credential prompts for a thread."""
    vault = get_credential_vault()
    return vault.list_pending(thread_id=thread_id)


@router.delete("/clear", summary="Purge Thread Credentials")
def clear_credentials(thread_id: ThreadId) -> dict[str, Any]:
    """Purge all secrets for a finished thread."""
    vault = get_credential_vault()
    vault.clear_thread(thread_id=thread_id)
    return {"status": "cleared", "thread_id": thread_id}
