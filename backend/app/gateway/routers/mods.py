"""Gateway API Router for the Alpha Mod Kernel.

Read-only operator surface over the ordered middleware chain: what mods are
installed, in what order they run, what they have decided, what they are
holding for approval, and what commands they contributed. Nothing here
registers, mutates, or reorders a mod — the kernel's registration path and its
guards own that — and nothing here executes a tool on a mod's behalf.

Route order is load-bearing, for the same Starlette reason the skills and
workflow collection routes precede their catch-alls: the single-segment
collection routes below are declared before ``GET /api/mods/{mod_name}``, or
``GET /api/mods/chain`` is read as an id and 404s.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from alpha.mods.approvals import get_hold_store
from alpha.mods.audit import AuditLedgerMod
from alpha.mods.kernel import ModKernel, get_mod_kernel
from alpha.mods.middleware import list_mod_commands, run_mod_command
from app.gateway.deps import require_admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mods", tags=["mods"])

_ADMIN_AUDIT_DETAIL = "Admin privileges are required to read the mod audit ledger."
_ADMIN_HOLDS_DETAIL = "Admin privileges are required to review or decide held actions."
_ADMIN_STATE_DETAIL = "Admin privileges are required to read mod state."


def _kernel() -> ModKernel:
    return get_mod_kernel()


def _audit_mod(kernel: ModKernel) -> AuditLedgerMod | None:
    mod = kernel.get_mod("audit_ledger")
    return mod if isinstance(mod, AuditLedgerMod) else None


async def _require_authenticated(request: Request) -> None:
    """Fail closed when no principal resolved onto the request."""
    if getattr(request.state, "user", None) is None:
        raise HTTPException(status_code=401, detail="authentication required")


class HoldDecisionRequest(BaseModel):
    reason: str = Field(default="", max_length=2000, description="Why this decision: recorded beside the hold for audit.")


class ModCommandRunRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict, description="Arguments passed to the mod's handler.")


class ImpactPreviewRequest(BaseModel):
    tool_name: str = Field(..., min_length=1, description="Tool that would be invoked.")
    tool_args: dict[str, Any] = Field(default_factory=dict, description="Arguments the tool would receive.")


# ---------------------------------------------------------------------------
# Fleet introspection
# ---------------------------------------------------------------------------


@router.get("")
async def list_mods(request: Request) -> dict[str, Any]:
    """The installed fleet, its ordered control chain, and per-mod descriptions.

    ``describe_mods`` reports what the kernel already knows — subscription set,
    granted capabilities, declared manifest, and the state keys each mod has
    actually touched. Nothing here executes a mod.
    """
    await _require_authenticated(request)
    kernel = _kernel()
    return {
        "total": len(kernel.list_mods()),
        "chain": kernel.control_chain(),
        "mods": kernel.describe_mods(),
    }


@router.get("/chain")
async def get_control_chain(request: Request) -> dict[str, Any]:
    """The ordered chain as ``dispatch`` runs it — sorted by priority, never by registration time.

    This is the auditable answer to "who runs first, and can anything outrank
    the safety triad?".
    """
    await _require_authenticated(request)
    chain = _kernel().control_chain()
    return {"total": len(chain), "chain": chain}


@router.get("/ui-cards")
async def list_ui_cards(request: Request, mod_name: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    """Cards mods rendered for an operator (hold previews, feedback prompts).

    Admin-scoped: cards carry the paths and reasons of actions in flight, and
    the kernel's card store has no per-owner dimension to scope a member read
    against — a member read would show every user's held actions.
    """
    await require_admin_user(request, detail="Admin privileges are required to read mod UI cards.")
    cards = _kernel().list_ui_cards(mod_name=mod_name, limit=limit)
    return {"total": len(cards), "cards": cards}


# ---------------------------------------------------------------------------
# Audit ledger
# ---------------------------------------------------------------------------


@router.get("/audit")
async def get_audit(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    event_name: str | None = Query(default=None),
    mod_name: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
) -> dict[str, Any]:
    """Recent audit records plus aggregate counters over the retained window.

    Admin-scoped: entries carry redacted tool arguments and outcomes of every
    user's dispatches. An absent audit mod is ``503`` with the real reason —
    "could not look" and "looked and found nothing" lead to opposite decisions,
    so an empty list is never returned for an unreadable source.
    """
    await require_admin_user(request, detail=_ADMIN_AUDIT_DETAIL)
    kernel = _kernel()
    audit = _audit_mod(kernel)
    if audit is None:
        raise HTTPException(status_code=503, detail="audit_ledger mod is not registered in this kernel; no audit records could be read")
    entries = await asyncio.to_thread(audit.get_entries, limit=limit, event_name=event_name, mod_name=mod_name, outcome=outcome)
    return {"total": len(entries), "entries": entries, "stats": audit.stats()}


@router.get("/audit/export")
async def export_audit(request: Request, limit: int = Query(default=1000, ge=1, le=10000)) -> dict[str, Any]:
    """The retained audit window as JSONL, for an external sink."""
    await require_admin_user(request, detail=_ADMIN_AUDIT_DETAIL)
    audit = _audit_mod(_kernel())
    if audit is None:
        raise HTTPException(status_code=503, detail="audit_ledger mod is not registered in this kernel; no audit records could be read")
    text = await asyncio.to_thread(audit.export_jsonl, limit=limit)
    lines = text.splitlines() if text else []
    return {"total": len(lines), "jsonl": text}


# ---------------------------------------------------------------------------
# Held actions (the operator approval surface)
# ---------------------------------------------------------------------------


@router.get("/holds")
async def list_holds(request: Request, decision: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    """Held actions awaiting (or carrying) an operator decision.

    The store is durable under ``runtime_home()`` but single-process: a decision
    taken here is not coordinated across Gateway workers.
    """
    await require_admin_user(request, detail=_ADMIN_HOLDS_DETAIL)
    store = get_hold_store()
    records = await asyncio.to_thread(lambda: store.list_holds(decision=decision, limit=limit))
    return {
        "total": len(records),
        "holds": [r.to_dict() for r in records],
        "store_file": str(store.store_file),
    }


@router.post("/holds/{hold_id}/approve")
async def approve_hold(hold_id: str, payload: HoldDecisionRequest, request: Request) -> dict[str, Any]:
    """Approve one held action. Admin-only, and the operator identity is the
    authenticated caller — never a body-supplied name, which would let a client
    attribute a decision to somebody else."""
    await require_admin_user(request, detail=_ADMIN_HOLDS_DETAIL)
    user = getattr(request.state, "user", None)
    operator = str(getattr(user, "id", "") or "unknown")
    store = get_hold_store()
    record = await asyncio.to_thread(lambda: store.approve(hold_id, operator=operator, reason=payload.reason))
    if record is None:
        raise HTTPException(status_code=404, detail=f"hold '{hold_id}' not found")
    return {"hold": record.to_dict()}


@router.post("/holds/{hold_id}/reject")
async def reject_hold(hold_id: str, payload: HoldDecisionRequest, request: Request) -> dict[str, Any]:
    """Reject one held action, recording the authenticated operator and reason."""
    await require_admin_user(request, detail=_ADMIN_HOLDS_DETAIL)
    user = getattr(request.state, "user", None)
    operator = str(getattr(user, "id", "") or "unknown")
    store = get_hold_store()
    record = await asyncio.to_thread(lambda: store.reject(hold_id, operator=operator, reason=payload.reason))
    if record is None:
        raise HTTPException(status_code=404, detail=f"hold '{hold_id}' not found")
    return {"hold": record.to_dict()}


# ---------------------------------------------------------------------------
# Mod state and commands
# ---------------------------------------------------------------------------


@router.get("/state")
async def get_mod_state(request: Request) -> dict[str, Any]:
    """A snapshot of every mod's namespaced state.

    Admin-scoped: a mod's state is its own bookkeeping and may carry paths,
    counters, or identifiers from any user's runs; there is no owner dimension
    to scope a member read against.
    """
    await require_admin_user(request, detail=_ADMIN_STATE_DETAIL)
    kernel = _kernel()
    store = kernel.state
    return {
        "total_keys": await asyncio.to_thread(store.total_keys),
        "mods": {name: await asyncio.to_thread(store.snapshot, name) for name in await asyncio.to_thread(store.mod_names)},
    }


@router.get("/commands")
async def get_mod_commands(request: Request) -> dict[str, Any]:
    """Commands mods contributed — each runs with no model turn and no tokens."""
    await _require_authenticated(request)
    commands = await asyncio.to_thread(list_mod_commands, _kernel())
    return {"total": len(commands), "commands": commands}


@router.post("/commands/{command_name}")
async def post_mod_command(command_name: str, payload: ModCommandRunRequest, request: Request) -> dict[str, Any]:
    """Run one mod-registered command immediately.

    An unknown command is ``404`` so the caller can fall through to the ordinary
    command registry rather than being told an error. A command declaring
    ``requires_approval`` is refused here with ``409``: this route has no
    approval flow, and executing an approval-gated command from an unguarded
    HTTP call would defeat the flag the registering mod set.
    """
    await _require_authenticated(request)
    kernel = _kernel()
    resolved = kernel.commands.resolve(command_name)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"no mod owns command '{command_name}'")
    command_def, _handler = resolved
    if command_def.requires_approval:
        raise HTTPException(status_code=409, detail=f"mod command '/{command_def.name}' declares requires_approval; this route has no approval flow")
    result = await run_mod_command(kernel, command_name, payload.payload)
    if result is None:  # pragma: no cover - resolve above already proved ownership
        raise HTTPException(status_code=404, detail=f"no mod owns command '{command_name}'")
    return result


# ---------------------------------------------------------------------------
# Impact preview (never executes anything)
# ---------------------------------------------------------------------------


@router.post("/preview")
async def post_impact_preview(payload: ImpactPreviewRequest, request: Request) -> dict[str, Any]:
    """A bounded, read-only estimate of what a tool call would touch.

    This never executes the tool. ``measurable: false`` means the preview could
    not be computed — it is not a claim that nothing is at stake.
    """
    await _require_authenticated(request)
    from alpha.mods.preview import preview_impact

    preview = await asyncio.to_thread(preview_impact, payload.tool_name, payload.tool_args)
    return preview.to_dict()


# ---------------------------------------------------------------------------
# Single-mod describe (declared last: catch-all)
# ---------------------------------------------------------------------------


@router.get("/{mod_name}")
async def get_mod(mod_name: str, request: Request) -> dict[str, Any]:
    """One mod's review-facing description, or 404 naming the absent mod."""
    await _require_authenticated(request)
    kernel = _kernel()
    for entry in kernel.describe_mods():
        if entry.get("name") == mod_name:
            return entry
    raise HTTPException(status_code=404, detail=f"mod '{mod_name}' is not registered")
