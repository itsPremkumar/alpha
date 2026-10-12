"""HTTP API for the Alpha Capability Mesh.

The read and preview plane over ``alpha.capability_mesh``, mirroring the
``capability_mesh`` tool route-for-route so the two surfaces cannot disagree
about what a payload means. Both call ``CapabilityMeshService``; neither
re-derives an honesty rule the other already owns.

Three properties are load-bearing here:

* **An unreadable store is a degraded state, never an empty ``200``.**
  ``503`` carrying the store's own reason is the only honest answer to "the
  passport store is corrupt", because an empty list and a corrupt file drive
  opposite operator decisions and only one of them is true. A *missing* file is
  genuinely empty — absence is not corruption — so the two stay
  distinguishable.
* **Every planning route is a preview.** ``/route``, ``/team`` and
  ``/compose`` return ``preview: true`` and ``dispatched: false``; the package
  has no executor, so a plan is a proposal, never work that ran.
* **Store I/O never runs on the event loop.** Every handler resolves the config
  and does its filesystem work through ``asyncio.to_thread``, because a
  blocking read on the request loop stalls every other run in the process.

**Scope.** The mesh's directory is installation-scoped: one configured
directory holding the passports, memory, pair estimates and shadow comparisons
for this deployment's dispatchable configurations. Every route therefore
requires an authenticated caller — exactly as ``routers/arena.py`` does — but
the payloads are *not* partitioned per user, because a capability measurement
describes the installation's configuration rather than one person's data.
Nothing served here is per-user private. The documents themselves are
single-process JSON/JSONL: atomic against a crash in one process, **never**
cross-process exactly-once.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StrictBool

from alpha.capability_mesh.complementarity import PairMemoryStoreError
from alpha.capability_mesh.independence import IndependenceStoreError
from alpha.capability_mesh.memory import MemoryStoreError
from alpha.capability_mesh.shadow import ShadowStoreError
from alpha.capability_mesh.store import PassportStoreError
from alpha.capability_mesh.synthesis import ArtifactStoreError
from alpha.config import get_app_config
from app.gateway.deps import get_current_user_from_request

router = APIRouter(prefix="/api/capability-mesh", tags=["capability-mesh"])

#: A store that cannot be read answers with this status. It is the repo-wide
#: degraded state for "could not look" (``/api/side-effects`` and
#: ``/api/bots/working`` refuse the same way), and it is deliberately not a
#: ``200`` with an empty body: an unreadable ledger must not render as an idle
#: one.
_STORE_UNAVAILABLE = 503

_DISABLED_DETAIL = "The capability mesh is disabled (config.yaml -> capability_mesh.enabled: false). It is a read/preview plane: even enabled it dispatches nothing."

#: Every durable document the mesh owns can be unreadable, and all of them say
#: so with their own words.
_STORE_ERRORS = (PassportStoreError, MemoryStoreError, ShadowStoreError, IndependenceStoreError, ArtifactStoreError, PairMemoryStoreError)


# --------------------------------------------------------------------- models


class CapabilityMeshRouteRequest(BaseModel):
    task: str = Field(..., min_length=1)
    policy: str = ""
    profile_version: str = ""
    required_capabilities: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)


class CapabilityMeshTeamRequest(CapabilityMeshRouteRequest):
    budget_usd: float | None = Field(default=None, ge=0.0)
    max_team_size: int | None = Field(default=None, ge=1, le=64)


class CapabilityMeshComposeRequest(CapabilityMeshRouteRequest):
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class CapabilityMeshRecordRequest(BaseModel):
    entity_id: str = Field(..., min_length=1)
    capability: str = Field(..., min_length=1)
    outcome: str = Field(..., min_length=1)
    task_fingerprint: str = ""
    task_text: str = ""
    task_category: str = ""
    quality: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    # StrictBool, not bool: the mesh's own contract refuses a truthy string so
    # it can never become a silent pass, and a plain ``bool`` here would let
    # pydantic coerce ``"yes"`` into exactly that.
    passed_hard_gate: StrictBool | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0.0)
    failure_mode: str = ""
    slot: str = ""
    profile_version: str = ""
    record_id: str = ""


# --------------------------------------------------------------------- plumbing


async def _run(method: str, /, **kwargs) -> Any:
    """Resolve the config, build the service, run one method off the loop.

    ``get_app_config()`` stats the config file on every call to detect edits,
    so it is never read inline on a request loop; and every service method
    touches the filesystem, so it never runs on one either.

    ``ValueError`` is the mesh's own refusal vocabulary — an unknown policy, an
    out-of-bounds budget, a record its constructor rejects — and becomes a
    ``400`` carrying those words. Every store error becomes the degraded state
    with the store's reason attached, because "could not read" is never an
    empty answer.
    """
    from alpha.capability_mesh.config import capability_mesh_config
    from alpha.capability_mesh.service import CapabilityMeshService

    app_config = await asyncio.to_thread(get_app_config)
    config = capability_mesh_config(app_config)
    if not config.enabled:
        raise HTTPException(status_code=_STORE_UNAVAILABLE, detail=_DISABLED_DETAIL)
    service = CapabilityMeshService(config)
    try:
        return await asyncio.to_thread(partial(getattr(service, method), **kwargs))
    except _STORE_ERRORS as error:
        raise HTTPException(status_code=_STORE_UNAVAILABLE, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


# ---------------------------------------------------------------------- routes


@router.get("/profiles")
async def get_profiles(
    entity_id: str = Query(default=""),
    entity_type: str = Query(default=""),
    _user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """Capability passports with their evidence levels.

    A count without the level behind it is a number nobody can weigh, so both
    travel together and ``unmeasured`` is reported as ``unmeasured`` — never
    smoothed into a measurement.
    """
    return await _run("passports", entity_id=entity_id, entity_type=entity_type)


@router.get("/memory")
async def get_memory(
    entity_id: str = Query(default=""),
    capability: str = Query(default=""),
    task_category: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
    _user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """Recorded outcomes plus the conservative credit report.

    ``credit`` is a declared starting policy, not a measure of worth: an
    attempt nobody measured is still an attempt. A page that dropped rows says
    ``truncated: true`` rather than presenting a short list as the whole
    history.
    """
    return await _run("memory_report", entity_id=entity_id, capability=capability, task_category=task_category, limit=limit)


@router.get("/calibration")
async def get_calibration(
    profile_version: str = Query(default=""),
    _user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """Whether stated confidence means anything, under one rubric.

    Scoped to exactly one ``profile_version``: a v1 sample never averages into
    a v2 answer. Below the sample floor the verdict is ``None`` (unresolved),
    never "well calibrated", and a Brier score over nothing is ``None`` rather
    than a placeholder ``0.0``.
    """
    return await _run("calibration_report", profile_version=profile_version)


@router.get("/drift")
async def get_drift(
    profile_version: str = Query(default=""),
    _user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """Has performance moved. A pure reader: it reports and acts on nothing."""
    return await _run("drift_report", profile_version=profile_version)


@router.get("/policy")
async def get_policy(
    policy: str = Query(default=""),
    default_agents: int | None = Query(default=None, ge=1, le=64),
    _user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """The explicit decision-policy resolution table.

    A table, not intelligence: one inspectable row per policy, an unknown name
    refused with the valid ones listed, and bounds refused rather than clamped.
    The ``local`` row discloses that it declares an intent the mesh cannot
    enforce.
    """
    return await _run("policy_table", policy=policy, default_agents=default_agents)


@router.get("/independence")
async def get_independence(
    entity_a: str = Query(..., min_length=1),
    entity_b: str = Query(..., min_length=1),
    task_category: str = Query(default=""),
    profile_version: str = Query(default=""),
    _user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """Correlated-error awareness for one pair.

    ``known: false`` is a real answer — nothing was measured, which is neither
    independent nor dependent. Only ``model_family`` and ``model_provider`` may
    be marked shared by a documented weak prior.
    """
    return await _run("independence_report", entity_a=entity_a, entity_b=entity_b, task_category=task_category, profile_version=profile_version)


@router.get("/shadow")
async def get_shadow(
    profile_version: str = Query(default=""),
    _user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """The shadow router's self-evaluation. Predictions, never budget.

    Over zero comparisons ``agreement_rate`` is ``None``, never ``0.0``: there
    is no agreement to measure.
    """
    return await _run("shadow_report", profile_version=profile_version)


@router.post("/route")
async def post_route(
    body: CapabilityMeshRouteRequest,
    _user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """PREVIEW: fingerprint -> decompose -> route.

    Nothing is dispatched. The decomposition is the deterministic heuristic
    baseline and carries its own provenance disclosure.
    """
    return await _run(
        "route_preview",
        task=body.task,
        policy=body.policy,
        profile_version=body.profile_version,
        required_capabilities=tuple(body.required_capabilities),
        required_tools=tuple(body.required_tools),
    )


@router.post("/team")
async def post_team(
    body: CapabilityMeshTeamRequest,
    _user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """PREVIEW: the small team the required capabilities would select.

    Every member's reason carries its marginal number, ``stops`` says why
    selection ended, and ``total_predicted_cost`` is ``null`` whenever a member
    is unpriced — an unpriced member is never a free one.
    """
    return await _run(
        "team_preview",
        task=body.task,
        policy=body.policy,
        profile_version=body.profile_version,
        required_capabilities=tuple(body.required_capabilities),
        budget_usd=body.budget_usd,
        max_team_size=body.max_team_size,
    )


@router.post("/compose")
async def post_compose(
    body: CapabilityMeshComposeRequest,
    _user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """PREVIEW: best-per-slot composition over the supplied artifacts.

    Incompatible assumptions are never merged into a synthetic "best of both":
    ``composed_artifact`` stays absent while any slot conflicts, and every
    conflict carries the alternatives it would need. Supplied artifacts are
    staged in scratch storage and discarded — this route persists nothing.
    """
    return await _run(
        "compose_preview",
        task=body.task,
        artifacts=body.artifacts,
        policy=body.policy,
        profile_version=body.profile_version,
        required_capabilities=tuple(body.required_capabilities),
    )


@router.post("/records")
async def post_record(
    body: CapabilityMeshRecordRequest,
    _user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    """Append one measured outcome. The mesh's only write.

    ``CapabilityRecord`` is the only validator: a ``quality`` without a
    ``profile_version`` is refused by name rather than accepted and quietly
    downgraded, because the profile version is what makes a score comparable
    later. Raw task text is never stored — ``task_text`` is accepted only to
    derive the opaque fingerprint the memory contract asks for.
    """
    return await _run(
        "record",
        entity_id=body.entity_id,
        capability=body.capability,
        outcome=body.outcome,
        task_fingerprint=body.task_fingerprint,
        task_text=body.task_text,
        task_category=body.task_category,
        quality=body.quality,
        confidence=body.confidence,
        passed_hard_gate=body.passed_hard_gate,
        latency_ms=body.latency_ms,
        cost_usd=body.cost_usd,
        failure_mode=body.failure_mode,
        slot=body.slot,
        profile_version=body.profile_version,
        record_id=body.record_id,
    )


# ---------------------------------------------------------------- re-export for app.py

__all__ = ["router"]
