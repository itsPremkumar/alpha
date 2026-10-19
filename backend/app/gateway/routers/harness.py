"""HTTP API for Alpha's recursive harness improvement (Harness²-inspired).

The operator surface over ``alpha.harness_improvement`` — the subsystem the
``docs/ALPHA_HARNESS2_IMPLEMENTATION_GUIDE.md`` method is implemented in. It runs
improvement cycles, exposes the immutable bundle tree and its diffs, the
experience library, the promotion ladder and the budget ledger. The guide's
route list (§13.1) is adapted to Alpha's conventions rather than copied: reads
are authenticated, every mutation is admin-gated, and every refusal names a
reason a client can act on.

Four properties are load-bearing here:

* **The execution plane's absence is machine-readable, not only prose.** A
  production process wires no ``HarnessExecutor``, no ``HarnessEditor`` and no
  ``BaselineProvider`` — and then no run may execute anything. That is a
  deliberate posture with a reason, not an unfinished edge: an executor is the
  component that would run a model-driven task under a candidate harness, and
  binding it to Alpha's sandbox/subagent runtime is a separate trust decision
  (guide §12: isolation, deny-by-default network, redaction, budgets) that this
  router must not pretend to have made. So ``GET /status`` carries a
  ``harness_capability`` row (``state: absent``, ``smart: false``) naming the
  exact wiring a deployment would have to add, and it answers even while the
  feature is **disabled** — the client asking whether this plane can execute is
  precisely the client that should be told before it plans around it. A start
  attempt refuses with the same list rather than parking a run that could never
  progress.
* **A gate that did not run is not a gate that passed.** ``approve`` records an
  operator approval only when the *substantive* promotion gates already pass,
  and ``activate`` never force-skips a gate: the one reason an explicit operator
  action is meant to satisfy — ``automatic_global_promotion`` being off — is
  removed by re-evaluating under an explicit-operator policy, so every other
  unmet condition still refuses with its own words. The regression-suite gate is
  real: the Gateway runs no suite, so it stays unmeasured and refuses.
* **An unreadable store is a degraded state, never an empty ``200``.** A corrupt
  bundle manifest, run record or ledger answers ``503`` carrying the store's own
  reason; a *missing* file is genuinely empty, and the two stay
  distinguishable. Promotion/decision routes are absent-store-tolerant only
  where the answer is meaningful.
* **Store I/O never runs on the event loop.** Config resolution, listing scans
  and ledger reads go through ``asyncio.to_thread``. The run *driver* stays on
  the loop because it awaits the injected executor: its own durable writes are
  single small atomic JSON writes, and a real executor owns offloading its
  inference I/O.

**Scope.** This subsystem is installation-scoped: one configured store, one
active bundle. Every route therefore requires an authenticated caller, but the
payloads are *not* partitioned per user — an improvement run measures this
deployment's harness, not one person's data. The documents themselves are
single-process JSON/JSONL: atomic against a crash in one process, **never**
cross-process exactly-once.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from alpha.config import get_app_config
from alpha.harness_improvement import (
    CHECK_KINDS,
    DeliverableCheck,
    ExperienceMetadata,
    HarnessBundle,
    HarnessImprovementEngine,
    HarnessImprovementSettings,
    HarnessImprovementStore,
    ImprovementRun,
    ImprovementRunError,
    PromotionDecision,
    PromotionEvent,
    PromotionEvidence,
    PromotionState,
    StoreCorrupt,
    StoreUnreadable,
    TaskContract,
    activate,
    compute_task_fingerprint,
    diff_bundles,
    evaluate_promotion,
    is_stale,
    promotion_history,
    redact_text,
    reject_candidate,
    rollback,
    utc_now,
)
from alpha.harness_improvement.promotion import LADDER
from alpha.harness_improvement.state import TERMINAL_STAGES
from app.gateway.deps import get_current_user_from_request, require_admin_user

router = APIRouter(prefix="/api/harness", tags=["harness-improvement"])

#: A store that cannot be read answers with this status. It is the repo-wide
#: degraded state for "could not look" (``/api/capability-mesh`` and
#: ``/api/side-effects`` refuse the same way), and deliberately not a ``200``
#: with an empty body: an unreadable bundle tree must not render as an empty one.
_STORE_UNAVAILABLE = 503

#: The feature flag's refusal text names the setting, so a client can quote the
#: key an operator has to change instead of re-deriving it from a 403.
_DISABLED_DETAIL = (
    "Harness improvement is disabled (config.yaml -> harness_improvement.enabled: false). While disabled every route that would run, drive or advance a candidate refuses: starting, resuming, approving and activating. "
    "The stop-or-restore writes (cancelling a run, rejecting a candidate, rolling back the active bundle) stay reachable, because the kill switch must not trap an operator with work in flight or a bad active bundle; "
    "read-only posture and store views stay reachable."
)

#: Every store error the package can raise for a read is one answer here.
_STORE_ERRORS = (StoreUnreadable, StoreCorrupt)

_DURABILITY = "single-process JSON/JSONL under runtime_home()/harness_improvement; atomic per write, never cross-process exactly-once"

#: The wiring this process would need before a run could execute anything. Kept
#: as data (not prose) so the UI can render one row per missing collaborator and
#: a test can pin the list against the engine's actual injection points.
_MISSING_WIRING: tuple[dict[str, str], ...] = (
    {
        "wiring_point": "harness_improvement.executor (alpha.harness_improvement.executor.HarnessExecutor)",
        "why_absent": "the Gateway injects no executor; an improvement run therefore parks at BLOCKED naming this collaborator instead of faking a baseline, probe or successor execution",
    },
    {
        "wiring_point": "harness_improvement.editor (alpha.harness_improvement.executor.HarnessEditor)",
        "why_absent": "the Gateway injects no editor; with no executor there is no baseline evidence for an editor to propose against",
    },
    {
        "wiring_point": "harness_improvement.baseline_provider (alpha.harness_improvement.engine.BaselineProvider)",
        "why_absent": "no active bundle exists and no provider snapshots the editable harness surface, so a first run has no baseline to compare against; the engine refuses to invent one",
    },
    {
        "wiring_point": "harness_improvement.regression_suite (promotion evidence)",
        "why_absent": "the Gateway runs no regression suite, so with promotion.require_regression_suite: true (default) every promotion refuses naming the unmeasured suite rather than treating it as passed",
    },
)


# --------------------------------------------------------------------- models


class HarnessDeliverableCheck(BaseModel):
    """One deterministic acceptance check on a deliverable."""

    name: str = Field(..., min_length=1, max_length=200)
    path: str = Field(..., min_length=1, max_length=500)
    kind: str = Field(default="exists", description=f"one of: {', '.join(sorted(CHECK_KINDS))}")
    detail: str = Field(default="", max_length=2000)
    sections: list[str] = Field(default_factory=list)
    min_bytes: int = Field(default=1, ge=0)


class HarnessRunRequest(BaseModel):
    """A task contract for one improvement run (guide §7 step 0).

    ``task_fingerprint`` is deliberately not accepted from the client: the
    server computes the salted fingerprint from ``task``, so a caller cannot
    choose the identity a cross-task summary is keyed on.
    """

    task: str = Field(..., min_length=1, max_length=20000)
    outcome: str = Field(..., min_length=1, max_length=4000)
    deliverables: list[str] = Field(default_factory=list)
    checks: list[HarnessDeliverableCheck] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    domain_tags: list[str] = Field(default_factory=list)
    task_type: str = Field(default="general", max_length=100)
    uncertainties: list[str] = Field(default_factory=list)
    run_id: str | None = Field(default=None, max_length=128)


class HarnessCancelRequest(BaseModel):
    reason: str = Field(default="cancelled by operator", max_length=2000)


class HarnessApprovalRequest(BaseModel):
    """An operator approval for one candidate bundle.

    ``approvals`` names the *privileged-kind* approvals being granted
    (``scripts`` / ``middleware``); it can never satisfy a substantive gate,
    which is why there is no ``force`` field on this body.
    """

    reason: str = Field(default="", max_length=2000)
    approvals: list[str] = Field(default_factory=list)


class HarnessActivationRequest(BaseModel):
    reason: str = Field(default="", max_length=2000)


# --------------------------------------------------------------------- plumbing


def _refusal(status: int, code: str, detail: str, **extra: Any) -> JSONResponse:
    """One refusal shape: a string ``detail`` the frontend can render next to a
    machine-readable ``code`` (the same contract ``routers/skills.py`` keeps)."""
    return JSONResponse(status_code=status, content={"detail": detail, "code": code, **extra})


def _production_engine(app_config: Any) -> HarnessImprovementEngine:
    """The Gateway's wiring point for the engine's collaborators.

    It wires **none** of them, on purpose. ``HarnessExecutor``,
    ``HarnessEditor`` and ``BaselineProvider`` are the execution plane, and
    binding that plane to Alpha's sandbox/subagent runtime is a separate trust
    decision (isolation, network posture, credentials, budget) that this change
    does not make. The engine's own posture is the honest one: a run started
    with no executor parks at ``BLOCKED`` naming the missing collaborator, and
    with no baseline provider at all the run is refused up front instead of
    parking forever.
    """
    section = app_config.harness_improvement
    return HarnessImprovementEngine(
        store=HarnessImprovementStore(),
        settings=HarnessImprovementSettings.from_mapping(section.model_dump()),
    )


def _engine() -> HarnessImprovementEngine:
    """Build the engine from the live (hot-reloaded) config.

    Synchronous and config-reading, so callers reach it through
    ``asyncio.to_thread``: ``get_app_config()`` stats the config file on every
    call to detect edits, and that stat must not run on the request loop.
    """
    return _production_engine(get_app_config())


def _baseline_absent(engine: HarnessImprovementEngine) -> bool:
    """Whether a new run would have nothing to improve against.

    Mirrors the engine's own resolution order — an active pointer wins, then a
    wired ``baseline_provider`` may snapshot one — without creating a run
    record. The pre-check exists so the refusal carries the missing-wiring
    list before anything durable is written; a *corrupt* pointer still raises
    and answers as the store error it is, so "absent" is never folded together
    with "unreadable".
    """
    if engine.store.load_active() is not None:
        return False
    return engine.baseline_provider is None


def _settings() -> HarnessImprovementSettings:
    return HarnessImprovementSettings.from_mapping(get_app_config().harness_improvement.model_dump())


async def _enabled_engine() -> HarnessImprovementEngine | JSONResponse:
    """Resolve the engine, refusing while the feature is disabled."""
    app_config = await asyncio.to_thread(get_app_config)
    if not app_config.harness_improvement.enabled:
        return _refusal(_STORE_UNAVAILABLE, "harness_improvement_disabled", _DISABLED_DETAIL)
    return await asyncio.to_thread(_engine)


async def _admin_actor(request: Request, *, detail: str) -> str | JSONResponse:
    """Resolve the caller for a mutation, refusing a non-admin.

    Promotion decisions and run control are the writes that decide what harness
    this installation runs, so they are admin-gated exactly as
    ``routers/completion.py`` gates its mutations: a member cannot move the
    active pointer, and a PAT never carries admin capability.
    """
    await require_admin_user(request, detail=detail)
    user = getattr(request.state, "user", None)
    actor = str(getattr(user, "id", "") or "")
    if not actor:
        user = await get_current_user_from_request(request)
        actor = str(getattr(user, "id", "") or "")
    if not actor:
        return _refusal(401, "unattributed_caller", "authenticated caller carries no id; an improvement decision is never anonymous")
    return actor


def _store_error(exc: Exception) -> JSONResponse:
    return _refusal(_STORE_UNAVAILABLE, "harness_store_unreadable", str(exc))


def _redacted(content: str | None) -> tuple[str | None, list[dict[str, str]]]:
    """Redact a component body before it crosses the API.

    The harness surface is prompts, guidance and skill text — not credentials —
    but a candidate can quote whatever it saw, so the same scrubber the durable
    store uses runs on the way out and every redaction travels as a finding.
    """
    if content is None:
        return None, []
    clean, findings = redact_text(content)
    return clean, [finding.to_dict() for finding in findings]


# ------------------------------------------------------------------ projections


def _run_row(payload: dict[str, Any]) -> dict[str, Any]:
    """A bounded timeline row: identity, stage, counts — not the whole record."""
    stage = str(payload.get("stage", ""))
    validations = payload.get("validations") or []
    return {
        "run_id": payload.get("run_id", ""),
        "created_at": payload.get("created_at", ""),
        "updated_at": payload.get("updated_at", ""),
        "stage": stage,
        "mode": payload.get("mode", ""),
        "created_by": payload.get("created_by", ""),
        "task_fingerprint": (payload.get("task") or {}).get("task_fingerprint", ""),
        "task_type": (payload.get("task") or {}).get("task_type", ""),
        "outcome": (payload.get("task") or {}).get("outcome", ""),
        "base_bundle_id": payload.get("base_bundle_id", ""),
        "successor_bundle_id": payload.get("successor_bundle_id"),
        "blocked_reason": payload.get("blocked_reason", ""),
        "error": payload.get("error"),
        "cancel_requested": bool(payload.get("cancel_requested")),
        "resumable": stage not in {str(s) for s in TERMINAL_STAGES},
        "counts": {
            "checkpoints": len(payload.get("checkpoints") or {}),
            "proposals": len(payload.get("proposals") or []),
            "validations": len(validations),
            "accepted_validations": len([row for row in validations if isinstance(row, dict) and row.get("accepted")]),
            "probes": len(payload.get("probe_outcomes") or []),
            "contrasts": len(payload.get("contrasts") or []),
            "experiences": len(payload.get("experiences") or []),
        },
        "budget_state": payload.get("budget_state") or {},
        "notes": list(payload.get("notes") or []),
    }


def _bundle_row(bundle: HarnessBundle) -> dict[str, Any]:
    return {
        "bundle_id": bundle.bundle_id,
        "parent_bundle_id": bundle.parent_bundle_id,
        "created_at": bundle.created_at,
        "created_by": bundle.created_by,
        "agent_profile": bundle.agent_profile,
        "promotion_state": str(bundle.promotion_state),
        "component_count": len(bundle.components),
        "components": [ref.to_dict() for ref in bundle.components],
        "compatibility": {
            "alpha_runtime_version": bundle.alpha_runtime_version,
            "tool_schema_version": bundle.tool_schema_version,
            "policy_version": bundle.policy_version,
        },
        "provenance": {"source_run_id": bundle.source_run_id, "source_task_type": bundle.source_task_type},
    }


def _deliverable_verdict(run: ImprovementRun, bundle_id: str) -> bool | None:
    """``True``/``False`` only when the recorded deliverable check *ran*.

    ``NOT_RUN`` is not a pass: a deliverable whose validators never executed
    leaves the verdict ``None`` so the promotion gate reports "unmeasured"
    instead of reading an unverified artifact as verified.
    """
    deliverable = run.deliverable
    if not isinstance(deliverable, dict) or str(deliverable.get("bundle_id") or "") != bundle_id:
        return None
    selected = str(deliverable.get("selected") or "")
    key = {"baseline": "baseline_validators", "successor": "successor_validators"}.get(selected)
    rows = deliverable.get(key or "", None)
    if not isinstance(rows, list) or not rows:
        return None
    statuses = [str(row.get("status")) for row in rows if isinstance(row, dict)]
    if not statuses:
        return None
    if any(status == "failed" for status in statuses):
        return False
    if all(status == "passed" for status in statuses):
        return True
    return None


def _evidence_for_bundle(store: HarnessImprovementStore, bundle: HarnessBundle) -> tuple[PromotionEvidence, dict[str, Any]]:
    """Reconstruct a candidate's promotion evidence from its source run record.

    The evidence is read, never inferred: with no source run, a run that did not
    reach the relevant stage, or a check that never ran, the corresponding item
    stays unmeasured and the ladder refuses. Only the *identity* of the adopted
    candidates is derived from the composition, and that mapping is disclosed in
    the returned summary.
    """
    kinds = tuple(sorted({ref.kind for ref in bundle.components}, key=str))
    summary: dict[str, Any] = {
        "bundle_id": bundle.bundle_id,
        "source_run_id": bundle.source_run_id,
        "component_kinds": [str(kind) for kind in kinds],
        "proposal_ids": [],
        "basis": "none",
        "note": "",
    }
    payload = store.load_run(bundle.source_run_id) if bundle.source_run_id else None
    if payload is None:
        summary["note"] = "no source run record is stored for this bundle, so its validation, probe, contrast and deliverable evidence is unmeasured — not passed"
        return PromotionEvidence(component_kinds=kinds), summary

    run = ImprovementRun.from_dict(payload)
    validation_rows = [row for row in run.validations if isinstance(row, dict) and str(row.get("candidate_bundle_id") or "") == bundle.bundle_id]
    proposal_ids = {str(row.get("proposal_id")) for row in validation_rows}
    composition_ids: set[str] = set()
    composition = run.composition
    if composition is not None and composition.successor_bundle_id == bundle.bundle_id:
        composition_ids = {edit.candidate_id for edit in composition.adopted}
    related = proposal_ids | composition_ids
    if related and composition_ids and not proposal_ids:
        summary["basis"] = "composed_successor"
    elif proposal_ids:
        summary["basis"] = "candidate_bundle"
    summary["proposal_ids"] = sorted(related)

    if validation_rows:
        validation_clean: bool | None = all(bool(row.get("accepted")) for row in validation_rows)
    elif composition_ids:
        # The composer only adopts edits whose candidate validation was accepted
        # (the engine builds its composer inputs from accepted validations), so a
        # composed successor is statically validated by construction — and the
        # summary says the basis, rather than leaving the reader to assume it.
        validation_clean = True
        summary["note"] = "the composed successor's adopted edits all came from accepted candidate validations; no validation row names the composed bundle itself"
    else:
        validation_clean = None

    probes = [outcome for outcome in run.probe_outcomes if outcome.candidate_id in related or (outcome.candidate_id == "successor" and run.successor_bundle_id == bundle.bundle_id)]
    contrasts = tuple(contrast for contrast in run.contrasts if contrast.candidate_id in related)
    evidence = PromotionEvidence(
        validation_clean=validation_clean,
        probe_outcomes=len(probes),
        contrasts=contrasts,
        regression_suites=(),
        approvals=(),
        component_kinds=kinds,
        deliverable_verified=_deliverable_verdict(run, bundle.bundle_id),
    )
    return evidence, summary


def _recorded_approvals(store: HarnessImprovementStore, bundle_id: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Operator approvals already recorded for one bundle, from the promotion log."""
    listing = store.read_promotions()
    rows = [row for row in listing.items if isinstance(row, dict) and str(row.get("event")) == "approved" and str(row.get("bundle_id")) == bundle_id]
    granted = sorted({str(name) for row in rows for name in ((row.get("gates") or {}).get("granted") or [])})
    return granted, rows


def _decision_for(store: HarnessImprovementStore, bundle: HarnessBundle, policy: Any, *, target: PromotionState, with_approvals: bool) -> tuple[PromotionDecision, PromotionDecision, dict[str, Any]]:
    """``(as-configured decision, explicit-operator decision, evidence summary)``.

    The second decision exists because exactly one of the configured refusals is
    what an explicit operator action is *for*: with
    ``automatic_global_promotion`` disabled, ``evaluate_promotion`` refuses every
    global promotion by design. Re-evaluating under a policy with that flag
    turned on isolates that reason, so a refusal that survives both evaluations
    is a substantive gate — never something an operator click may skip.
    """
    evidence, summary = _evidence_for_bundle(store, bundle)
    if with_approvals:
        granted, _rows = _recorded_approvals(store, bundle.bundle_id)
        summary["recorded_approvals"] = granted
        evidence = replace(evidence, approvals=tuple(granted))
    configured = evaluate_promotion(bundle=bundle, evidence=evidence, policy=policy, target_state=target)
    explicit = evaluate_promotion(bundle=bundle, evidence=evidence, policy=replace(policy, automatic_global_promotion=True), target_state=target)
    return configured, explicit, summary


def _approval_view(store: HarnessImprovementStore, bundle: HarnessBundle, policy: Any, requested: tuple[str, ...]) -> dict[str, Any]:
    """Evaluate a candidate's ladder gates around one approval request.

    Three decisions are needed, and computing them together keeps the store
    reads in one thread hop:

    * ``pending`` — the explicit-operator policy (``automatic_global_promotion``
      on) evaluated with the *already recorded* approvals applied: its
      ``required_approvals`` are exactly the privileged kinds this request may
      name, so a request granting anything else is refused by name;
    * ``with_request`` — the same, with ``requested`` applied. The approval is
      recorded only when this decision is allowed, so an approval can never
      launder an unmeasured regression suite (or any other substantive gate)
      into a recorded pass;
    * ``configured`` — the operator's policy as written, for the response.
    """
    evidence, summary = _evidence_for_bundle(store, bundle)
    granted, _rows = _recorded_approvals(store, bundle.bundle_id)
    summary["recorded_approvals"] = granted
    explicit_policy = replace(policy, automatic_global_promotion=True)
    with_recorded = evaluate_promotion(bundle=bundle, evidence=replace(evidence, approvals=tuple(granted)), policy=explicit_policy, target_state=PromotionState.ACTIVE)
    pending = sorted(set(with_recorded.required_approvals))
    combined = tuple(sorted(set(granted) | set(requested)))
    with_request = evaluate_promotion(bundle=bundle, evidence=replace(evidence, approvals=combined), policy=explicit_policy, target_state=PromotionState.ACTIVE)
    configured = evaluate_promotion(bundle=bundle, evidence=replace(evidence, approvals=combined), policy=policy, target_state=PromotionState.ACTIVE)
    return {
        "granted": granted,
        "pending": pending,
        "unexpected": sorted(set(requested) - set(pending)),
        "with_request": with_request,
        "configured": configured,
        "evidence": summary,
    }


# ---------------------------------------------------------------------- routes


@router.get("/status")
async def get_status(_user: Any = Depends(get_current_user_from_request)) -> dict[str, Any]:
    """Posture: enabled, ceilings, the active bundle, store readability, wiring.

    Reachable while the feature is **disabled**, because a client deciding
    whether to plan around harness improvement should be told before it tries —
    and this is where it learns that this process wires no executor, no editor
    and no baseline provider, with the wiring point named per row.
    """

    def _payload() -> dict[str, Any]:
        app_config = get_app_config()
        section = app_config.harness_improvement
        settings = HarnessImprovementSettings.from_mapping(section.model_dump())
        store = HarnessImprovementStore()
        payload: dict[str, Any] = {
            "enabled": bool(section.enabled),
            "enabled_note": ("harness_improvement.enabled is true: the store, bundle tree, experience library and promotion ladder are reachable." if section.enabled else _DISABLED_DETAIL),
            "mode": section.mode,
            "settings": settings.to_dict(),
            "ceilings": settings.ceilings().to_dict(),
            "promotion_policy": settings.promotion_policy().to_dict(),
            "config_hash": settings.config_hash(),
            "harness_capability": {
                "capability": "harness_improvement_probe_execution",
                "state": "absent",
                "executes": False,
                "missing_wiring": [dict(row) for row in _MISSING_WIRING],
            },
            "durability": _DURABILITY,
            "store": {"root": str(store.root), "readable": True, "counts": {}, "unreadable": [], "reason": None},
            "active_bundle": None,
        }
        try:
            bundles = store.list_bundles()
            runs = store.list_runs(limit=500)
            experiences = store.list_experiences()
            pointer = store.load_active()
        except _STORE_ERRORS as exc:
            payload["store"].update({"readable": False, "reason": str(exc)})
            return payload
        payload["store"].update(
            {
                "counts": {
                    "bundles": len(bundles.items),
                    "runs": len(runs.items),
                    "experiences": len(experiences.items),
                },
                "unreadable": [*bundles.unreadable, *runs.unreadable, *experiences.unreadable],
            }
        )
        if pointer is not None:
            payload["active_bundle"] = pointer.to_dict()
            try:
                bundle = store.load_bundle(pointer.bundle_id)
            except _STORE_ERRORS as exc:
                payload["active_bundle"] = {**pointer.to_dict(), "manifest_error": str(exc)}
                return payload
            if bundle is None:
                payload["active_bundle"] = {**pointer.to_dict(), "manifest_error": f"bundle {pointer.bundle_id!r} is not stored"}
                return payload
            payload["active_bundle"] = {**pointer.to_dict(), "manifest": _bundle_row(bundle)}
        return payload

    return await asyncio.to_thread(_payload)


@router.get("/runs")
async def list_runs(
    limit: int = Query(default=50, ge=1, le=500),
    _user: Any = Depends(get_current_user_from_request),
) -> Any:
    """Newest-first run timeline rows. Rows that could not be read are named."""
    store = HarnessImprovementStore()
    try:
        listing = await asyncio.to_thread(store.list_runs, limit=limit)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    return {
        "runs": [_run_row(dict(row)) for row in listing.items],
        "unreadable": list(listing.unreadable),
        "complete": listing.complete,
        "durability": _DURABILITY,
    }


@router.post("/runs")
async def start_run(
    body: HarnessRunRequest,
    request: Request,
    _user: Any = Depends(get_current_user_from_request),
) -> Any:
    """Start one improvement run: qualify the task, snapshot, drive.

    The run is driven to its first stopping point before the response — with no
    executor wired that is the ``BLOCKED`` park naming the missing collaborator,
    so the response says exactly where the run stands. A deployment that wires a
    real executor drives long probes through ``POST /runs/{id}/resume`` (or a
    background caller) instead of holding one request open.
    """
    actor = await _admin_actor(request, detail="starting a harness improvement run requires an administrator")
    if isinstance(actor, JSONResponse):
        return actor
    invalid = sorted({check.kind for check in body.checks if check.kind not in CHECK_KINDS})
    if invalid:
        return _refusal(
            422,
            "invalid_check_kind",
            f"unknown deliverable check kind(s): {', '.join(invalid)}; an unknown check is NOT_RUN, never a pass, so it is refused here instead of silently never running",
            valid_kinds=sorted(CHECK_KINDS),
        )
    task = TaskContract(
        task=body.task,
        outcome=body.outcome,
        deliverables=tuple(body.deliverables),
        checks=tuple(
            DeliverableCheck(
                name=check.name,
                path=check.path,
                kind=check.kind,
                detail=check.detail,
                sections=tuple(check.sections),
                min_bytes=check.min_bytes,
            )
            for check in body.checks
        ),
        allowed_tools=tuple(body.allowed_tools),
        domain_tags=tuple(body.domain_tags),
        task_type=body.task_type or "general",
        uncertainties=tuple(body.uncertainties),
        task_fingerprint=compute_task_fingerprint(body.task),
    )
    engine = await _enabled_engine()
    if isinstance(engine, JSONResponse):
        return engine
    try:
        baseline_absent = await asyncio.to_thread(_baseline_absent, engine)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if baseline_absent:
        return _refusal(
            409,
            "harness_baseline_absent",
            "no active harness bundle exists and no baseline_provider is wired; refusing to invent a baseline",
            missing_wiring=[dict(row) for row in _MISSING_WIRING],
        )
    try:
        run = await engine.start(task=task, created_by=actor, run_id=body.run_id)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    except ImprovementRunError as exc:
        return _refusal(400, "harness_run_refused", str(exc))
    return {
        "run": run.to_dict(),
        "resumable": run.resumable,
        "next_stage": str(run.next_incomplete_stage()),
        "durability": _DURABILITY,
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str, _user: Any = Depends(get_current_user_from_request)) -> Any:
    """One run's full durable record plus its journal tail."""
    engine = await asyncio.to_thread(_engine)
    try:
        status = await asyncio.to_thread(engine.status, run_id)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if status is None:
        return _refusal(404, "harness_run_not_found", f"no improvement run {run_id!r} is recorded")
    return status


@router.get("/runs/{run_id}/events")
async def get_run_events(
    run_id: str,
    limit: int = Query(default=500, ge=1, le=2000),
    _user: Any = Depends(get_current_user_from_request),
) -> Any:
    """The append-only run journal, newest last.

    A torn final line is disclosed in ``unreadable`` rather than dropping the
    journal. A journal whose run record is gone is still returned, with
    ``run_record_present: false``, because deleting a record is not a reason to
    hide what was executed.
    """
    store = HarnessImprovementStore()
    try:
        record, events = await asyncio.to_thread(lambda: (store.load_run(run_id), store.read_events(run_id, limit=limit)))
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    rows, unreadable = events
    if record is None and not rows:
        return _refusal(404, "harness_run_not_found", f"no improvement run {run_id!r} is recorded")
    return {
        "run_id": run_id,
        "run_record_present": record is not None,
        "stage": str((record or {}).get("stage", "")),
        "events": rows,
        "unreadable": unreadable,
        "limit": limit,
    }


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    request: Request,
    _user: Any = Depends(get_current_user_from_request),
) -> Any:
    """Resume a parked or interrupted run from its last completed checkpoint."""
    actor = await _admin_actor(request, detail="resuming a harness improvement run requires an administrator")
    if isinstance(actor, JSONResponse):
        return actor
    engine = await _enabled_engine()
    if isinstance(engine, JSONResponse):
        return engine
    try:
        existing = await asyncio.to_thread(engine.status, run_id)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if existing is None:
        return _refusal(404, "harness_run_not_found", f"no improvement run {run_id!r} is recorded")
    try:
        run = await engine.resume(run_id)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    except ImprovementRunError as exc:
        return _refusal(409, "harness_run_not_resumable", str(exc))
    return {"run": run.to_dict(), "resumable": run.resumable, "next_stage": str(run.next_incomplete_stage()), "durability": _DURABILITY}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    request: Request,
    body: HarnessCancelRequest | None = None,
    _user: Any = Depends(get_current_user_from_request),
) -> Any:
    """Request cancellation, applied at the next stage boundary.

    Deliberately reachable while the feature is **disabled**, alongside reject
    and rollback: refusing to record a stop request would leave an operator
    unable to halt a run that a previous configuration started. Cancellation is
    a request, not a kill — the engine applies it at the next boundary, so a run
    that is parked stays parked until it is resumed, and the response says which
    happened.
    """
    actor = await _admin_actor(request, detail="cancelling a harness improvement run requires an administrator")
    if isinstance(actor, JSONResponse):
        return actor
    reason = (body.reason if body else "") or f"cancelled by {actor}"
    engine = await asyncio.to_thread(_engine)
    try:
        existing = await asyncio.to_thread(engine.status, run_id)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if existing is None:
        return _refusal(404, "harness_run_not_found", f"no improvement run {run_id!r} is recorded")
    try:
        run = await asyncio.to_thread(engine.cancel, run_id, reason=reason)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    except ImprovementRunError as exc:
        return _refusal(409, "harness_run_not_cancellable", str(exc))
    return {
        "run": run.to_dict(),
        "cancel_requested": run.cancel_requested,
        "applied_at_next_boundary": run.cancel_requested and not run.terminal,
        "note": "the request is durable; it is applied at the next stage boundary — resume the run to apply it now",
        "durability": _DURABILITY,
    }


@router.get("/bundles")
async def list_bundles(_user: Any = Depends(get_current_user_from_request)) -> Any:
    """The immutable bundle tree, newest-first rows plus the active pointer."""
    store = HarnessImprovementStore()
    try:
        listing = await asyncio.to_thread(store.list_bundles)
        pointer = await asyncio.to_thread(store.load_active)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    bundles = sorted(listing.items, key=lambda bundle: bundle.created_at, reverse=True)
    return {
        "bundles": [_bundle_row(bundle) for bundle in bundles],
        "unreadable": list(listing.unreadable),
        "complete": listing.complete,
        "active_bundle": pointer.to_dict() if pointer else None,
    }


@router.get("/bundles/{bundle_id}")
async def get_bundle(bundle_id: str, _user: Any = Depends(get_current_user_from_request)) -> Any:
    """One manifest plus its component bodies, redacted on the way out."""
    store = HarnessImprovementStore()
    try:
        bundle = await asyncio.to_thread(store.load_bundle, bundle_id)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if bundle is None:
        return _refusal(404, "harness_bundle_not_found", f"no harness bundle {bundle_id!r} is stored")

    def _components() -> tuple[list[dict[str, Any]], list[str]]:
        rows: list[dict[str, Any]] = []
        findings: list[str] = []
        for ref in bundle.components:
            content = store.get_blob(ref.sha256)
            clean, redactions = _redacted(content)
            findings.extend(f"{ref.component_id}: {finding['kind']}" for finding in redactions)
            rows.append({**ref.to_dict(), "content": clean, "missing": content is None})
        return rows, findings

    try:
        rows, findings = await asyncio.to_thread(_components)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    missing_note = "a missing component body means the manifest references a blob this store does not hold; the manifest itself is still valid"
    return {"bundle": _bundle_row(bundle), "components": rows, "redactions": findings, "note": missing_note if any(row["missing"] for row in rows) else ""}


@router.get("/bundles/{bundle_id}/diff")
async def get_bundle_diff(
    bundle_id: str,
    base: str = Query(default="", description="bundle to diff against; defaults to this bundle's parent"),
    include_contents: bool = Query(default=True),
    _user: Any = Depends(get_current_user_from_request),
) -> Any:
    """Component-level diff against the parent (or an explicit ``base``)."""
    store = HarnessImprovementStore()
    try:
        bundle = await asyncio.to_thread(store.load_bundle, bundle_id)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if bundle is None:
        return _refusal(404, "harness_bundle_not_found", f"no harness bundle {bundle_id!r} is stored")
    base_id = base or bundle.parent_bundle_id or ""
    if not base_id:
        return _refusal(409, "harness_bundle_has_no_parent", f"bundle {bundle_id!r} records no parent bundle; pass ?base=<bundle_id> to compare two bundles", bundle_id=bundle_id)
    try:
        base_bundle = await asyncio.to_thread(store.load_bundle, base_id)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if base_bundle is None:
        return _refusal(404, "harness_bundle_not_found", f"base bundle {base_id!r} is not stored")

    diff = diff_bundles(base_bundle, bundle)

    def _bodies() -> tuple[dict[str, Any], list[str]]:
        findings: list[str] = []
        bodies: dict[str, Any] = {"added": {}, "modified": {}}
        if not include_contents:
            return bodies, findings
        base_refs = {ref.component_id: ref for ref in base_bundle.components}
        for component_id in diff["added"]:
            ref = bundle.component(component_id)
            content = store.get_blob(ref.sha256) if ref else None
            clean, redactions = _redacted(content)
            bodies["added"][component_id] = clean
            findings.extend(f"{component_id}: {finding['kind']}" for finding in redactions)
        for row in diff["modified"]:
            component_id = str(row["component_id"])
            after = bundle.component(component_id)
            before = base_refs.get(component_id)
            clean_after, found_after = _redacted(store.get_blob(after.sha256) if after else None)
            clean_before, found_before = _redacted(store.get_blob(before.sha256) if before else None)
            bodies["modified"][component_id] = {"before": clean_before, "after": clean_after}
            findings.extend(f"{component_id}: {finding['kind']}" for finding in [*found_before, *found_after])
        return bodies, findings

    try:
        bodies, findings = await asyncio.to_thread(_bodies)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    return {
        "diff": diff,
        "contents": bodies,
        "redactions": findings,
        "note": "an empty diff with retained_baseline composition is a valid outcome: the composer is allowed to keep the baseline",
    }


@router.get("/experiences")
async def list_experiences(
    task_type: str = Query(default=""),
    scope: str = Query(default=""),
    status: str = Query(default=""),
    domain_tag: list[str] = Query(default_factory=list),
    limit: int = Query(default=100, ge=1, le=500),
    _user: Any = Depends(get_current_user_from_request),
) -> Any:
    """The experience library: every entry, with computed staleness per row.

    Staleness is *reported*, not applied: the retrieval path inside the engine
    filters stale entries out of a proposal's context, while this view shows
    them so an operator can see what has gone stale and why. Hiding them here
    would make the library unmaintainable.
    """
    store = HarnessImprovementStore()
    try:
        listing = await asyncio.to_thread(store.list_experiences)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    rows: list[dict[str, Any]] = []
    for row in listing.items:
        if not isinstance(row, dict):
            continue
        if task_type and str(row.get("task_type", "")) != task_type:
            continue
        if scope and str(row.get("scope", "")) != scope:
            continue
        if status and str(row.get("status", "")) != status:
            continue
        if domain_tag and not (set(domain_tag) & {str(tag) for tag in row.get("domain_tags", [])}):
            continue
        entry = ExperienceMetadata.from_dict(row)
        rows.append({**row, "staleness": list(is_stale(entry, now=utc_now()))})
    rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
    return {"experiences": rows[:limit], "unreadable": list(listing.unreadable), "complete": listing.complete, "total": len(rows), "limit": limit}


@router.get("/experiences/{experience_id}")
async def get_experience(experience_id: str, _user: Any = Depends(get_current_user_from_request)) -> Any:
    """One entry: metadata, its Markdown body, and computed staleness."""
    store = HarnessImprovementStore()
    try:
        loaded = await asyncio.to_thread(store.load_experience, experience_id)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if loaded is None:
        return _refusal(404, "harness_experience_not_found", f"no experience entry {experience_id!r} is stored")
    metadata, markdown = loaded
    entry = ExperienceMetadata.from_dict(metadata)
    clean, findings = _redacted(markdown)
    return {"metadata": metadata, "markdown": clean, "staleness": list(is_stale(entry, now=utc_now())), "redactions": findings}


@router.get("/promotions")
async def get_promotions(_user: Any = Depends(get_current_user_from_request)) -> Any:
    """The durable promotion chain plus the current pointer, read-only."""
    store = HarnessImprovementStore()
    try:
        history = await asyncio.to_thread(promotion_history, store)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    return {**history, "ladder": {str(source): str(target) for source, target in LADDER.items()}, "durability": _DURABILITY}


@router.post("/candidates/{bundle_id}/approve")
async def approve_candidate(
    bundle_id: str,
    body: HarnessApprovalRequest,
    request: Request,
    _user: Any = Depends(get_current_user_from_request),
) -> Any:
    """Record an operator approval — only when the substantive gates pass.

    Two refusals are deliberate. A candidate whose *substantive* gates are unmet
    is refused with every reason (nothing is recorded), so an approval can never
    launder an unmeasured regression suite into a recorded pass. And a request
    that grants more than the pending privileged-kind approvals is refused by
    name, so an approval means exactly what it says. Recording an approval
    *advances a candidate*, so it refuses while the feature is disabled; the
    stop-or-restore writes stay reachable.
    """
    actor = await _admin_actor(request, detail="approving a harness candidate requires an administrator")
    if isinstance(actor, JSONResponse):
        return actor
    engine = await _enabled_engine()
    if isinstance(engine, JSONResponse):
        return engine
    store = engine.store
    policy = engine.settings.promotion_policy()
    try:
        bundle = await asyncio.to_thread(store.load_bundle, bundle_id)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if bundle is None:
        return _refusal(404, "harness_bundle_not_found", f"no harness bundle {bundle_id!r} is stored")
    try:
        view = await asyncio.to_thread(_approval_view, store, bundle, policy, tuple(body.approvals))
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if view["unexpected"]:
        return _refusal(
            422,
            "harness_unexpected_approvals",
            f"this candidate needs no approval for {', '.join(view['unexpected'])}; pending privileged-kind approvals: {', '.join(view['pending']) or '(none)'}",
            pending=view["pending"],
        )
    if not view["with_request"].allowed:
        return _refusal(
            409,
            "harness_promotion_gates_unmet",
            "; ".join(view["with_request"].reasons),
            gates=view["with_request"].to_dict(),
            configured_gates=view["configured"].to_dict(),
            evidence=view["evidence"],
        )
    event = PromotionEvent(
        event="approved",
        bundle_id=bundle_id,
        previous_bundle_id=None,
        actor=actor,
        reason=body.reason or "operator approval recorded",
        gates={"decision": view["with_request"].to_dict(), "granted": list(body.approvals)},
    )
    try:
        await asyncio.to_thread(store.append_promotion, event.to_dict())
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    return {
        "approved": True,
        "bundle_id": bundle_id,
        "recorded": event.to_dict(),
        "gates": view["with_request"].to_dict(),
        "evidence": view["evidence"],
        "note": "approval is recorded, not applied: activation is a separate explicit step (POST /active/{bundle_id}/activate)",
    }


@router.post("/candidates/{bundle_id}/reject")
async def reject_bundle(
    bundle_id: str,
    body: HarnessApprovalRequest,
    request: Request,
    _user: Any = Depends(get_current_user_from_request),
) -> Any:
    """Record an operator rejection; the active pointer is untouched.

    Deliberately reachable while the feature is **disabled**, alongside cancel
    and rollback: refusing to record a stop would leave a candidate an operator
    decided against standing as an advanceable one, and the kill switch exists
    to stop work, not to freeze the records that describe it.
    """
    actor = await _admin_actor(request, detail="rejecting a harness candidate requires an administrator")
    if isinstance(actor, JSONResponse):
        return actor
    engine = await asyncio.to_thread(_engine)
    try:
        result = await asyncio.to_thread(reject_candidate, engine.store, bundle_id=bundle_id, actor=actor, reason=body.reason)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if not result.get("rejected"):
        return _refusal(404, "harness_bundle_not_found", str(result.get("reason")))
    return result


@router.post("/active/{bundle_id}/activate")
async def activate_bundle(
    bundle_id: str,
    body: HarnessActivationRequest,
    request: Request,
    _user: Any = Depends(get_current_user_from_request),
) -> Any:
    """Atomically point the active pointer at ``bundle_id``.

    Activation *advances a candidate onto the harness this installation runs*,
    so it refuses while the feature is disabled — the same gate as `start`,
    `resume` and `approve`. Its year-round counterpart, `rollback`, stays
    reachable so a bad activation can always be restored.

    The route never passes ``force``: the one gate reason an explicit operator
    action is *for* — ``automatic_global_promotion`` being off — is removed by
    re-evaluating under an explicit-operator policy, and every other unmet gate
    refuses with its own words. A candidate that was never approved still needs
    its privileged-kind approvals, and a candidate whose regression suite never
    ran is refused here too.
    """
    actor = await _admin_actor(request, detail="activating a harness bundle requires an administrator")
    if isinstance(actor, JSONResponse):
        return actor
    engine = await _enabled_engine()
    if isinstance(engine, JSONResponse):
        return engine
    store = engine.store
    policy = engine.settings.promotion_policy()
    try:
        bundle = await asyncio.to_thread(store.load_bundle, bundle_id)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if bundle is None:
        return _refusal(404, "harness_bundle_not_found", f"no harness bundle {bundle_id!r} is stored")
    configured, explicit, summary = await asyncio.to_thread(_decision_for, store, bundle, policy, target=PromotionState.ACTIVE, with_approvals=True)
    granted, approval_rows = await asyncio.to_thread(_recorded_approvals, store, bundle_id)
    approved_by_operator = bool(approval_rows)
    summary["recorded_approvals"] = granted
    summary["approved_by_operator"] = approved_by_operator
    if not configured.allowed and not (approved_by_operator and explicit.allowed):
        reasons = list(explicit.reasons)
        if explicit.allowed and not approved_by_operator:
            reasons.append(f"no operator approval is recorded for this candidate; record one with POST /api/harness/candidates/{bundle_id}/approve first")
        return _refusal(
            409,
            "harness_promotion_gates_unmet",
            "; ".join(reasons or configured.reasons),
            gates=explicit.to_dict(),
            configured_gates=configured.to_dict(),
            evidence=summary,
            approved_approvals=granted,
        )
    try:
        result = await asyncio.to_thread(
            partial(
                activate,
                store,
                bundle_id=bundle_id,
                actor=actor,
                reason=body.reason or "operator activation",
                decision=configured,
                approvals=tuple(granted),
                force=bool(not configured.allowed and explicit.allowed),
            )
        )
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if not result.get("activated"):
        return _refusal(409, "harness_activation_refused", str(result.get("reason")), gates=result.get("gates", {}))
    return {**result, "gates": configured.to_dict(), "evidence": summary, "note": "the previous known-good bundle is remembered on the pointer, so rollback is one step"}


@router.post("/active/{bundle_id}/rollback")
async def rollback_active(bundle_id: str, body: HarnessActivationRequest, request: Request, _user: Any = Depends(get_current_user_from_request)) -> Any:
    """Restore the previous known-good bundle of the active pointer.

    ``bundle_id`` must be the pointer's *current* bundle: the rollback target is
    the pointer's own ``previous_bundle_id``, and accepting an arbitrary id here
    would let a caller roll back from a bundle that is not active — a different
    operation than the one this route names. A rollback that cannot be performed
    (no prior bundle, prior bundle unreadable) refuses, because clearing the
    pointer would make a failure look like a rollback.

    Reachable while the feature is **disabled**, like cancel and reject: an
    operator who disables the subsystem after a bad activation must still be
    able to restore the prior known-good harness. The restore does not start or
    advance any candidate, which is what the kill switch refuses.
    """
    actor = await _admin_actor(request, detail="rolling back a harness bundle requires an administrator")
    if isinstance(actor, JSONResponse):
        return actor
    engine = await asyncio.to_thread(_engine)
    store = engine.store
    try:
        pointer = await asyncio.to_thread(store.load_active)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if pointer is None:
        return _refusal(409, "harness_rollback_refused", "no active bundle pointer exists, so there is nothing to roll back")
    if pointer.bundle_id != bundle_id:
        return _refusal(
            409,
            "harness_rollback_refused",
            f"bundle {bundle_id!r} is not the active bundle ({pointer.bundle_id!r} is); rollback restores the active pointer's previous bundle",
            active_bundle_id=pointer.bundle_id,
        )
    try:
        result = await asyncio.to_thread(rollback, store, actor=actor, reason=body.reason or "operator rollback")
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    if not result.get("rolled_back"):
        return _refusal(409, "harness_rollback_refused", str(result.get("reason")))
    return {**result, "note": "the restored bundle is the prior known-good harness recorded on the pointer before the activation"}


@router.get("/budgets")
async def get_budgets(
    run_id: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=2000),
    _user: Any = Depends(get_current_user_from_request),
) -> Any:
    """The measured spend ledger plus the ceilings it is measured against.

    ``ceilings`` are the operator's declared limits; the ledger rows are what was
    *measured*, and a dimension a provider did not report is left ``null`` there
    — a missing measurement is not a spend of zero.
    """
    settings = await asyncio.to_thread(_settings)
    store = HarnessImprovementStore()
    try:
        listing = await asyncio.to_thread(store.read_ledger, limit=limit)
    except _STORE_ERRORS as exc:
        return _store_error(exc)
    rows = [row for row in listing.items if isinstance(row, dict) and (not run_id or str(row.get("run_id", "")) == run_id)]
    return {
        "ceilings": settings.ceilings().to_dict(),
        "ledger": rows,
        "unreadable": list(listing.unreadable),
        "complete": listing.complete,
        "measured_model_calls": sum(int(row.get("model_calls", 0) or 0) for row in rows if isinstance(row, dict) and "model_calls" in row) or len(rows),
        "note": "cost and token totals are only comparable when every row reports them; an unreported dimension stays null rather than reading as zero",
        "durability": _DURABILITY,
    }


# ------------------------------------------------------------------ re-export

__all__ = ["router"]
