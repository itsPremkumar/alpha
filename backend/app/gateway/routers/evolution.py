"""Bounded evolution API: propose, benchmark, gate, promote, roll back."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/api/evolution", tags=["evolution"])


class ProposeRequest(BaseModel):
    surface: str = Field(..., min_length=1, max_length=32)
    target: str = Field(..., min_length=1, max_length=200)
    payload: dict = Field(default_factory=dict)
    parent_id: str | None = None


class BenchmarkReport(BaseModel):
    benchmark: dict = Field(default_factory=dict)


class GateRequest(BaseModel):
    baseline: dict = Field(default_factory=dict)
    human_approved: bool = False
    #: Opt-in, mirroring alpha.evolution.engine.gate's default: omitting this
    #: field must never grant autonomy. Omission ⇒ gated on human approval.
    autonomous_mode: bool = Field(
        default=False,
        description="Opt-in autonomous gating. Defaults to False; when False, promotion requires human_approved=True (matching the evolution engine's safe default). Omission never grants autonomy.",
    )


class UpdateApplyRequest(BaseModel):
    """Admin-only handoff options for a source update.

    The client cannot provide a URL, ref, commit, or archive. The server uses
    the candidate that its own GitHub/release verification persisted.
    """

    model_config = ConfigDict(extra="forbid")

    force: bool = Field(
        default=False,
        description="Confirm an attended apply while unattended auto_apply is off; it never bypasses canApply, clean-worktree, ancestry, or health safety checks.",
    )


class UpdateSkipRequest(BaseModel):
    """Admin-only version skip request."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(..., min_length=1, max_length=100)


async def _require_update_admin(request: Request) -> None:
    # Synthetic/internal callers are not update administrators.  In
    # particular, AGENT_WORKSPACE_AUTH_DISABLED must not turn a local bypass
    # into unattended source mutation capability.
    from app.gateway.auth_disabled import AUTH_SOURCE_AUTH_DISABLED, AUTH_SOURCE_INTERNAL, AUTH_SOURCE_PAT

    if getattr(request.state, "auth_source", None) in {AUTH_SOURCE_AUTH_DISABLED, AUTH_SOURCE_INTERNAL, AUTH_SOURCE_PAT}:
        raise HTTPException(status_code=403, detail="A real interactive administrator session is required for source update operations.")
    from app.gateway.deps import require_admin_user

    await require_admin_user(request, detail="Administrator role required for source update operations.")


@router.post("/candidates", status_code=201)
async def propose_candidate(body: ProposeRequest) -> dict:
    def _do():
        from alpha.evolution import get_evolution_engine

        return get_evolution_engine().propose(body.surface, body.target, body.payload, parent_id=body.parent_id).to_dict()

    try:
        return await asyncio.to_thread(_do)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/candidates/{candidate_id}/benchmark")
async def record_benchmark(candidate_id: str, body: BenchmarkReport) -> dict:
    def _do():
        from alpha.evolution import get_evolution_engine

        cand = get_evolution_engine().record_benchmark(candidate_id, body.benchmark)
        return cand.to_dict() if cand else None

    result = await asyncio.to_thread(_do)
    if result is None:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    return result


@router.post("/candidates/{candidate_id}/gate")
async def gate_candidate(candidate_id: str, body: GateRequest) -> dict:
    def _do():
        from alpha.evolution import get_evolution_engine

        return get_evolution_engine().gate(
            candidate_id,
            body.baseline,
            human_approved=body.human_approved,
            autonomous_mode=body.autonomous_mode,
        )

    promoted, reason = await asyncio.to_thread(_do)
    return {"promoted": promoted, "reason": reason}


@router.post("/candidates/{candidate_id}/rollback")
async def rollback_candidate(candidate_id: str, reason: str = "") -> dict:
    def _do():
        from alpha.evolution import get_evolution_engine

        return get_evolution_engine().rollback(candidate_id, reason)

    return {"rolled_back": await asyncio.to_thread(_do)}


@router.get("/ledger")
async def evolution_ledger(limit: int = 100) -> dict:
    def _do():
        from alpha.evolution import get_evolution_engine

        return get_evolution_engine().ledger(limit=min(limit, 500))

    return {"events": await asyncio.to_thread(_do)}


@router.get("/identity")
async def evolution_identity() -> dict:
    """Runtime identity: canonical repository, version, commit, capabilities.

    Behind the default AuthMiddleware like every /api route (no auth changes
    here); the heavy assembly (disk + git subprocess) runs in a worker thread.
    """

    def _do():
        from alpha.evolution.identity import get_runtime_identity

        return get_runtime_identity()

    try:
        return await asyncio.to_thread(_do)
    except RuntimeError as exc:
        # Honest local failure (missing manifest, unwritable runtime home):
        # carry the real message in-body instead of an opaque 500 traceback.
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/update-check")
async def evolution_update_check() -> dict:
    """Run a GitHub latest-release check (spec section 22/45 subset).

    Failures answer in-body as an honest CHECK_FAILED state with the real
    error message — never a fabricated success, never a 500.
    """

    def _do():
        from alpha.evolution.release_check import check_for_update
        from alpha.evolution.update_policy import load_update_policy

        # Keep the original Phase-1 response contract for a disabled policy
        # (manual metadata checks remain useful even though no mutation is
        # possible).  Once the operator enables the policy, use the same
        # engine that persists the immutable candidate consumed by Apply.
        try:
            policy = load_update_policy()
        except Exception as exc:
            from alpha.evolution.release_check import record_check_failure

            return record_check_failure(f"update policy is invalid: {exc}")
        if policy.enabled:
            from alpha.evolution.update_engine import get_update_engine

            return get_update_engine().check(force=True)
        return check_for_update()

    try:
        return await asyncio.to_thread(_do)
    except Exception as exc:
        # check_for_update() already converts upstream failures into a
        # CHECK_FAILED state; this net only fires for escaped local errors so
        # the in-body contract still holds.
        from alpha.evolution.release_check import record_check_failure

        return await asyncio.to_thread(record_check_failure, str(exc) or repr(exc))


@router.get("/update-state")
async def evolution_update_state() -> dict:
    """Persisted update state only — no network access on this path."""

    def _do():
        from alpha.evolution.release_check import load_update_state
        from alpha.evolution.update_state import safe_state_snapshot

        return safe_state_snapshot(load_update_state())

    return await asyncio.to_thread(_do)


@router.post("/update-apply", status_code=202, summary="Queue a guarded source update", description="Admin-only. Uses the server-verified candidate; no client-supplied ref or URL is accepted.")
async def evolution_update_apply(request: Request, body: UpdateApplyRequest | None = None) -> dict:
    """Queue a detached update transaction and return its transaction id."""
    await _require_update_admin(request)

    def _do() -> dict:
        from alpha.evolution.update_engine import get_update_engine

        return get_update_engine().request_apply(force=bool(body and body.force))

    result = await asyncio.to_thread(_do)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return result


@router.post("/update-skip", summary="Skip one verified update version", description="Admin-only. The version is recorded locally; no source or remote state is changed.")
async def evolution_update_skip(request: Request, body: UpdateSkipRequest) -> dict:
    """Persist a bounded operator skip for a semantic version."""
    await _require_update_admin(request)

    def _do() -> dict:
        from alpha.evolution.update_engine import get_update_engine

        return get_update_engine().skip_version(body.version)

    result = await asyncio.to_thread(_do)
    if not result.get("ok"):
        raise HTTPException(status_code=422 if body.version else 409, detail=result)
    return result


@router.post("/update-recover", summary="Recover an interrupted source update", description="Admin-only. Restore the persisted backup ref and verify the previous checkout.")
async def evolution_update_recover(request: Request) -> dict:
    """Recover an update that stopped after staging or during health checks."""
    await _require_update_admin(request)

    def _do():
        from alpha.evolution.update_engine import get_update_engine

        return get_update_engine().recover_incomplete().public_dict()

    result = await asyncio.to_thread(_do)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return result
