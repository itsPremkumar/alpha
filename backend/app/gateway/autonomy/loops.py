"""Loop adapters: one tick per subsystem, safe by default.

Each adapter performs exactly ONE unit of work and must be safe to call
repeatedly. Adapters never raise (they return a dict summary); a tick that needs
a model/API key degrades to a no-op summary so a machine without credentials
still runs a complete, healthy system.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SyncTick = Callable[[], dict[str, Any]]


def _resolve_project_root() -> Path:
    """Repository root for repo-scoped loops (sentinel scans, etc.).

    Structural, not cwd-relative. This used to call
    ``alpha.config.runtime_paths.project_root()``, which resolves the *process
    working directory* -- and the documented launch command is
    ``cd backend && uvicorn app.gateway.app:app``, so it answered ``backend/``.
    Every ``.ps1`` file in this repository lives at the repository root
    (``start.ps1``, ``installer/``, ``recovery/``, ``scripts/``); there are none
    under ``backend/``. The PowerShell sentinel therefore scanned a tree with
    zero PowerShell scripts, found nothing, and reported a healthy empty result
    while costing ~18s to walk the virtualenv it found instead.
    """
    from alpha.config.runtime_paths import repository_root

    try:
        return repository_root()
    except Exception:
        # backend/app/gateway/autonomy/loops.py -> repo root is four levels up.
        return Path(__file__).resolve().parents[4]


def _persist_sentinel_report(report: dict[str, Any], *, trigger: str, auto_heal: bool) -> dict[str, Any]:
    """Durably journal one Sentinel pass — the single choke point for history.

    Every ``sentinel_tick`` call (supervisor loop, manual API pass, CLI-driven
    call) lands here, so the report history cannot diverge by call site.

    The pass has already run when this is called, so a write failure cannot
    fail closed retroactively: it is logged at ERROR and returned as a typed
    disclosure instead of being dropped (mirrors the job-memory honesty rule —
    no fake entry is ever written in its place).
    """
    try:
        from alpha.runtime.sentinel.report_store import default_sentinel_report_store

        store = default_sentinel_report_store()
        store.append(
            {
                "recorded_at": datetime.now(UTC).isoformat(),
                "trigger": trigger,
                "auto_heal": bool(auto_heal),
                "report": report,
            }
        )
    except Exception as exc:  # noqa: BLE001 - disclosure, never a silent drop
        reason = f"{type(exc).__name__}: {exc}"
        logger.error("Sentinel report not persisted: %s", reason)
        return {"persisted": False, "error": reason}
    return {"persisted": True, "store": store.path}


def sentinel_tick(*, repo_root: Path | None = None, auto_heal: bool = False, trigger: str = "supervisor-loop") -> dict[str, Any]:
    """One sentinel observe pass.

    Safe default: observes and reports; escalates unknown kinds.
    When auto_heal is True, wires default verified repair and verification routines.

    The resulting report is journaled by ``alpha.runtime.sentinel.report_store``
    and the returned dict carries the real report fields plus a ``persistence``
    disclosure (``{"persisted": bool, "store"|"error": ...}``). ``trigger`` names
    the caller (``sentinel_tick``'s only in-process caller that omits it is the
    supervisor loop, hence the default).
    """
    from alpha.runtime.sentinel.runner import (
        SentinelRunner,
        make_default_fix_fns,
        make_default_verification_commands,
    )

    root = repo_root or _resolve_project_root()
    fix_fns = make_default_fix_fns(root) if auto_heal else None
    verification_commands = make_default_verification_commands() if auto_heal else None
    runner = SentinelRunner(
        repo_root=root,
        fix_fns=fix_fns,
        verification_commands=verification_commands,
    )
    report = runner.run_once()
    payload = report.to_dict() if hasattr(report, "to_dict") else {"summary": str(report)}
    payload["persistence"] = _persist_sentinel_report(payload, trigger=trigger, auto_heal=auto_heal)
    return payload


def perpetual_tick() -> dict[str, Any]:
    """One perpetual-daemon heartbeat (discovery / consolidation / stagnation)."""
    from alpha.perpetual.daemon import PerpetualDaemon

    daemon = PerpetualDaemon(project_id="default")
    return daemon.step_heartbeat()


def review_queue_tick() -> dict[str, Any]:
    """Observe the deferred review queue WITHOUT draining it.

    Draining pops snapshots and requires a handler; doing that from a background
    loop would silently discard learning-fork reviews. This tick only reports how
    many reviews are pending so the signal is visible and can trigger the bus.
    """
    from alpha.learning.review_queue import get_review_queue

    queue = get_review_queue()
    pending = queue.pending()
    return {"pending": len(pending), "session_ids": [entry.session_id for entry in pending]}


def mission_tick(*, max_scopes: int = 200) -> dict[str, Any]:
    """Fail-closed mission watchdog: park stuck/blocked missions, never loop.

    Model-free. Scans durable missions, applies the mission loop brake
    (:func:`alpha.runtime.missions.decide_mission`) to each, and *tightens only*:
    a mission the brake says to park is recorded as ``blocked`` with the reason,
    so the brake holds even if the agent session that owned it died mid-run or a
    process restarted. It never dispatches work and never un-parks -- ``RunManager``
    and the APEX dispatcher stay the only execution authorities, so this is a
    safety net, not a second executor. A single unreadable mission never stalls
    the pass, and the scan is bounded.
    """
    from alpha.config.paths import get_paths
    from alpha.runtime.missions import MissionManager, decide_mission

    try:
        manager = MissionManager(get_paths())
        scopes = manager.scan_active(max_scopes=max_scopes)
    except Exception as exc:  # fail-open: an unreadable store is "no missions", not a fault
        return {"scanned": 0, "error": f"{type(exc).__name__}: {exc}"}

    counts = {"continue": 0, "done": 0, "park": 0}
    parked: list[dict[str, str]] = []
    for owner, thread_id, stack in scopes:
        try:
            decision = decide_mission(stack)
        except Exception:
            continue
        action = decision.action.value
        counts[action] = counts.get(action, 0) + 1
        # Tighten only: record the brake's reason, but never overwrite an
        # operator's existing block note and never un-park or dispatch.
        if decision.should_park and not stack.blocked.strip():
            try:
                manager.save(owner, thread_id, stack.block(decision.reason))
                parked.append({"owner": owner, "thread": thread_id, "reason": decision.reason})
            except (OSError, ValueError):
                continue
    return {
        "scanned": len(scopes),
        "continue": counts["continue"],
        "done": counts["done"],
        "park": counts["park"],
        "newly_parked": len(parked),
        "parked": parked[:20],
    }


def skill_curator_tick(*, dry_run: bool = True) -> dict[str, Any]:
    """One skill-curator prune pass. Dry-run by default; the flag decides."""
    from alpha.skills.curator import SkillCurator

    curator = SkillCurator()
    return curator.apply_transitions(dry_run=dry_run)


def memory_upkeep_tick(*, trials: int = 5) -> dict[str, Any]:
    """One autonomous skill/memory upkeep pass.

    Owner-scoped and model-free by default: it walks every owner's cognitive
    memory, ingests nothing (a loop has no work outcomes of its own — those come
    from the caller that ran the work), reconciles each skill's lifecycle against
    its measured evidence, replays what worked, and runs the four proof gates. A
    ``degraded`` verification makes the pass report and apply nothing, which is
    what makes this safe to run unattended.

    Ingestion stays a caller responsibility on purpose: a background loop has no
    way to know whether a recall was actually useful, and inventing that verdict
    would feed the lifecycle fabricated evidence.
    """
    from alpha.memory.cognitive.engine import _owner_systems, _owner_systems_lock

    passed = blocked = 0
    reports: list[dict[str, Any]] = []
    with _owner_systems_lock:
        systems = list(_owner_systems.values())
    for system in systems:
        try:
            report = system.run_upkeep(trials=trials)
        except Exception as exc:  # noqa: BLE001 - one owner must not stop the sweep
            logger.warning("memory upkeep failed for one owner system: %s", exc, exc_info=True)
            continue
        reports.append(report)
        if report.get("blocked_by_verification"):
            blocked += 1
        else:
            passed += 1
    return {"owners_swept": len(systems), "applied": passed, "blocked_by_verification": blocked, "reports": reports}


def enterprise_heartbeat_tick() -> dict[str, Any]:
    """One enterprise heartbeat cycle."""
    from alpha.enterprise.heartbeat import get_enterprise_heartbeat_coordinator

    coordinator = get_enterprise_heartbeat_coordinator()
    return coordinator.step_heartbeat_cycle()


def swarm_status_tick() -> dict[str, Any]:
    """Telemetry-only swarm tick: report running swarm tasks without starting any.

    Swarms are started on demand by the swarm tool/HTTP surface; a periodic loop
    must not spawn agent work on its own.
    """
    try:
        from alpha.swarm.runner import AsyncSwarmRunner  # noqa: F401

        # Instantiating the runner requires a coordinator; report absence honestly
        # instead of fabricating one.
        return {"active_swarms": "on-demand-only", "note": "swarms start via swarm_tool/HTTP"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"error": type(exc).__name__}


def workflow_triggers_tick(*, max_fires: int = 5) -> dict[str, Any]:
    """Fire due dynamic-workflow triggers: one bounded pass, never a cron loop.

    The trigger STORE owns *what should fire when*; this adapter is the firing
    seam the existing supervisor rides, which is what keeps the documented
    "ride the scheduler, do not become a second cron owner" contract true — it
    is a tick inside the ONE loop owner, gated under
    ``config.yaml -> autonomy.loops.workflow_triggers`` (absent = disabled), so
    a deployment that declares nothing spawns no task at all.

    Starting the run is the WHOLE tick. Execution stays with the host that owns
    the run (the kernel's per-run claim), so this loop can never turn into a
    second executor, and every fire goes through the same
    ``fire_trigger_on_engine`` seam the ``POST /api/workflows/triggers/{id}/fire``
    route uses — the two firing paths cannot drift apart.

    A refused fire (unregistered workflow, a store error) is REPORTED, not
    retried here: the schedule stays due, and the refusal is visible in the
    tick summary the supervisor publishes.
    """
    try:
        from alpha.workflow.triggers import fire_trigger_on_engine
        from app.gateway.routers.workflows import get_workflow_engine, get_workflow_trigger_store

        engine = get_workflow_engine()
        store = get_workflow_trigger_store()
        due = store.due_triggers(limit=max_fires)
        outcomes = [fire_trigger_on_engine(engine, store, trigger.trigger_id).to_dict() for trigger in due]
        fired = [item for item in outcomes if item.get("fired")]
        refused = [item for item in outcomes if not item.get("fired")]
        return {
            "due": len(due),
            "fired": fired,
            "refused": refused,
            "note": "each fire starts a run through the shared engine; execution is owned by the host that claims the run",
        }
    except Exception as exc:  # noqa: BLE001 - one failed pass must not park the loop
        logger.warning("workflow trigger pass failed: %s", exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


def free_models_sync_tick() -> dict[str, Any]:
    """Daily sync for keyless free LLM models (safe, non-breaking)."""
    try:
        from alpha.models.free_router import get_free_router

        router = get_free_router()
        return router.sync_daily_models(force_probe=True)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Daily free models sync failed: %s", exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


def company_operations_tick() -> dict[str, Any]:
    """Advance every Company OS loop that is currently eligible.

    One tick per company, in a bounded sweep. Companies whose own gate refuses —
    paused, over budget, no progress, disabled, or with the kill switch engaged —
    are **counted and reported**, not silently skipped: "the loop did nothing"
    and "three companies are refusing to run" are different operational facts.

    Each company is isolated, so one company's failure cannot stop the others and
    cannot park this loop. The adapter is model-free by construction, which is why
    a per-tick sweep is safe to run on a thread.
    """
    try:
        from alpha.company_os.service import get_company_service
        from alpha.company_os.store import CompanyNotFound
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Company OS unavailable for the autonomy tick: %s", exc)
        return {"error": f"{type(exc).__name__}: {exc}"}

    try:
        service = get_company_service()
        owner_ids = service.store.list_owner_ids()
    except Exception as exc:
        logger.warning("Could not enumerate company owners for the autonomy tick: %s", exc)
        return {"error": f"{type(exc).__name__}: {exc}"}

    summary: dict[str, Any] = {
        "owners_scanned": len(owner_ids),
        "companies_advanced": 0,
        "companies_refused": 0,
        "companies_failed": 0,
        "outcomes": {},
        "refusals": [],
        "errors": [],
    }

    for owner_id in owner_ids:
        try:
            companies = service.list_companies(owner_id)
        except Exception as exc:
            summary["companies_failed"] += 1
            summary["errors"].append({"owner": owner_id, "error": f"{type(exc).__name__}: {exc}"})
            continue

        for company in companies:
            company_id = company.company_id
            # A disabled loop is reported as "not enabled", not as a refusal: the
            # operator turned it off, which is not a fault.
            if not company.loop_policy.enabled:
                continue
            try:
                view = service.run_tick(company_id, owner_id)
            except CompanyNotFound:
                # Deleted between listing and tick; nothing to report.
                continue
            except Exception as exc:
                summary["companies_failed"] += 1
                summary["errors"].append({"company_id": company_id, "error": f"{type(exc).__name__}: {exc}"})
                logger.warning("Company tick failed for %s: %s", company_id, exc)
                continue

            record = view.record
            outcome = record.outcome.value
            summary["outcomes"][outcome] = summary["outcomes"].get(outcome, 0) + 1
            if outcome == "idle":
                # Idle is the healthy no-op; it is not counted as an advance.
                continue
            summary["companies_advanced"] += 1
            if outcome in ("paused", "budget_exhausted", "no_progress"):
                summary["companies_refused"] += 1
                summary["refusals"].append({"company_id": company_id, "outcome": outcome, "reason": record.reason})

    return summary


def apex_tick() -> dict[str, Any]:
    """One APEX executive pass over every non-terminal session.

    Registered through :class:`AutonomySupervisor` like every other loop, so
    "off unless ``config.yaml`` enables it" and the restart-budget-then-park
    behaviour apply unchanged — the supervisor is the single owner of background
    loops and this adapter adds nothing beside it.

    Two properties are load-bearing:

    * **Adapters never raise.** A tick returns a dict summary; a subsystem that
      cannot be reached degrades to a disclosed row rather than killing the pass.
    * **It counts rather than hides.** ``skipped_terminal``,
      ``skipped_mode_off``, and ``cycles_blocked`` are reported. A missing or
      unreadable per-scope mode never grants a tick, and a summary discloses
      how many sessions were intentionally left untouched.
    """
    try:
        from alpha.apex.contract import contract_from_snapshot, profile_for
        from alpha.apex.executive import run_cycle
        from alpha.apex.mode import DEFAULT_SCOPE, get_apex_mode_store
        from alpha.apex.store import get_apex_store
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("APEX unavailable for the autonomy tick: %s", exc)
        return {"error": f"{type(exc).__name__}: {exc}"}

    try:
        store = get_apex_store()
    except Exception as exc:
        logger.warning("APEX store unavailable for the autonomy tick: %s", exc)
        return {"error": f"{type(exc).__name__}: {exc}"}

    if store.is_degraded:
        # An unreadable store is reported, not treated as "no sessions". The
        # difference decides whether a reader repairs the file or relaxes.
        return {
            "error": "apex_store_unreadable",
            "store_error": store.load_error,
            "sessions": None,
            "cycles": 0,
        }

    try:
        mode_store = get_apex_mode_store()
    except Exception as exc:
        logger.warning("APEX mode store unavailable for the autonomy tick: %s", exc)
        return {"error": f"apex_mode_store_unavailable:{type(exc).__name__}:{exc}", "sessions": None, "cycles": 0}

    if mode_store.is_degraded:
        return {
            "error": "apex_mode_store_unreadable",
            "store_error": mode_store.load_error,
            "sessions": None,
            "cycles": 0,
        }

    summary: dict[str, Any] = {
        "sessions": None,
        "cycles": 0,
        "skipped_terminal": 0,
        "skipped_mode_off": 0,
        "skipped_profile_mismatch": 0,
        "cycles_blocked": 0,
        "decisions": {},
        "errors": [],
    }
    try:
        sessions = (session for page in store.iter_pages(page_size=200) for session in page)
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    summary["sessions"] = 0
    session_iterator = iter(sessions)
    while True:
        try:
            session = next(session_iterator)
        except StopIteration:
            break
        except Exception as exc:
            summary["error"] = f"{type(exc).__name__}: {exc}"
            return summary
        summary["sessions"] += 1
        if session.is_terminal:
            summary["skipped_terminal"] += 1
            continue
        scope_key = session.thread_id or session.owner or DEFAULT_SCOPE
        mode = mode_store.for_scope(scope_key)
        if not mode.enabled:
            summary["skipped_mode_off"] += 1
            continue
        try:
            contract = mode.contract()
            if mode.profile != session.profile:
                summary["skipped_profile_mismatch"] += 1
                continue
            if session.contract_snapshot is not None:
                contract = contract_from_snapshot(session.contract_snapshot, expected_digest=session.contract_digest)
            else:
                # Legacy rows predate persisted snapshots; only the canonical
                # profile contract can be reconstructed safely for them.
                contract = profile_for(session.profile, mission_id=session.mission_id)
            result = run_cycle(store, session.session_id, contract)
        except Exception as exc:
            summary["errors"].append({"session_id": session.session_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        summary["cycles"] += 1
        action = result.decision.action.value
        summary["decisions"][action] = summary["decisions"].get(action, 0) + 1
        if result.decision.blocked:
            summary["cycles_blocked"] += 1

    return summary


#: RunManager statuses this adapter reads as a finished, successful turn.
_APEX_RUN_SUCCESS_STATUSES = frozenset({"completed", "success"})

#: RunManager statuses this adapter reads as a terminal failure. ``failed`` and
#: ``cancelled`` are the pre-RunManager spellings still carried by older session
#: rows; the live enum spells them ``error`` and ``interrupted``.
_APEX_RUN_FAILED_STATUSES = frozenset({"error", "failed", "interrupted", "cancelled", "timeout"})

#: RunManager terminal statuses that have no twin in the dispatch-state
#: vocabulary ``ApexStore.record_run_status`` understands. ``timeout`` is
#: terminal everywhere else in the runtime -- ``_TERMINAL_RUN_STATUSES`` in
#: ``services.py``, the durable ``SessionState`` machine, edit-replay
#: visibility, peer recovery -- so recording it verbatim here leaves the
#: session in ``running`` forever, a state nothing can move it out of, while
#: every tick summary claims live work that RunManager already terminalized.
_APEX_PROJECTED_RUN_STATUSES = {"timeout": "error"}


def _observed_run_status(run: Any) -> str:
    """Read one RunManager status; ``"unknown"`` is honest, a name is not.

    The previous form nested ``getattr`` twice, so a record carrying
    ``status=None`` fell through to ``str(None)``: a run whose status could not
    be read was reported as having the status ``"none"``, and every downstream
    bucket read that as a live one.
    """
    raw = getattr(run, "status", None)
    value = getattr(raw, "value", raw)
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return "unknown"


def _projected_dispatch_status(status: str) -> str:
    """Map an observed RunManager status onto the store's dispatch vocabulary."""
    return _APEX_PROJECTED_RUN_STATUSES.get(status, status)


async def apex_execution_tick(app: Any, *, session_id: str | None = None) -> dict[str, Any]:
    """Dispatch and observe APEX objectives through the Gateway run owner.

    This host adapter is deliberately app-bound and async: the shared
    ``start_run`` service creates every run and ``RunManager`` remains its sole
    lifecycle owner. A durable dispatch generation is reused after a crash,
    and terminal runs stop here as awaiting verification rather than being
    reported complete or silently re-run.
    """
    from alpha.apex.contract import contract_from_snapshot, profile_for
    from alpha.apex.executive import run_cycle
    from alpha.apex.mode import DEFAULT_SCOPE, get_apex_mode_store
    from alpha.apex.store import ApexSessionState, get_apex_store
    from app.gateway.autonomy.supervisor import _fleet_admits_tick
    from app.gateway.services import launch_apex_session_run

    if not _fleet_admits_tick("apex"):
        return {"sessions": 0, "error": "fleet_control_refused", "dispatched": 0}

    summary: dict[str, Any] = {
        "sessions": None,
        "dispatched": 0,
        "running": 0,
        "awaiting_verification": 0,
        "completed": 0,
        "replanned": 0,
        "approval_requeued": 0,
        "blocked": 0,
        "failed": 0,
        "budget_exhausted": 0,
        "skipped_terminal": 0,
        "skipped_mode_off": 0,
        "skipped_profile_mismatch": 0,
        "unobserved": 0,
        "errors": [],
    }
    store = get_apex_store()
    mode_store = get_apex_mode_store()
    if store.is_degraded or mode_store.is_degraded:
        return {
            **summary,
            "error": "apex_store_unreadable" if store.is_degraded else "apex_mode_store_unreadable",
            "sessions": None,
        }

    run_manager = getattr(getattr(app, "state", None), "run_manager", None)
    event_store = getattr(getattr(app, "state", None), "run_event_store", None)
    if run_manager is None:
        return {**summary, "error": "run_manager_unavailable", "sessions": 0}

    if session_id is not None:
        selected = store.get(session_id)
        sessions = iter([selected] if selected is not None else [])
    else:
        sessions = (session for page in store.iter_pages(page_size=200) for session in page)
    summary["sessions"] = 0
    session_iterator = iter(sessions)
    while True:
        try:
            session = next(session_iterator)
        except StopIteration:
            break
        except Exception as exc:
            summary["errors"].append({"error": f"session_scan_failed: {type(exc).__name__}: {exc}"})
            logger.warning("APEX session scan failed: %s", exc)
            break
        summary["sessions"] += 1
        if session.is_terminal:
            summary["skipped_terminal"] += 1
            continue
        generation: int | None = None
        try:
            if session.run_id:
                run = await run_manager.get(session.run_id, user_id=session.owner, raise_on_store_error=True)
                if run is None:
                    # The link is durable and the record is not. Parking the
                    # dispatch here would invent a terminal state nobody
                    # measured, and counting it as ``running`` would claim work
                    # that cannot be observed, so it is disclosed durably and
                    # in its own bucket instead of vanishing with the tick.
                    summary["unobserved"] += 1
                    summary["errors"].append({"session_id": session.session_id, "run_id": session.run_id, "error": "linked RunManager record is unavailable; dispatch remains parked"})
                    store.emit(session.session_id, "run.record_unavailable", run_id=session.run_id)
                    continue
                status = _observed_run_status(run)
                if status == "unknown":
                    summary["unobserved"] += 1
                    summary["errors"].append({"session_id": session.session_id, "run_id": session.run_id, "error": "linked RunManager record carries no readable status; dispatch remains parked"})
                    store.emit(session.session_id, "run.status_unreadable", run_id=session.run_id)
                    continue
                if event_store is not None:
                    cursor = int(session.usage.event_cursors.get(session.run_id, 0))
                    for _ in range(20):
                        events = await event_store.list_events(
                            session.thread_id,
                            session.run_id,
                            event_types=["llm.ai.response", "subagent.end"],
                            limit=200,
                            after_seq=cursor,
                        )
                        if not events:
                            break
                        for event in events:
                            if not isinstance(event, dict):
                                continue
                            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
                            content = event.get("content") if isinstance(event.get("content"), dict) else {}
                            event_type = str(event.get("event_type", event.get("type", "")))
                            usage_data = metadata.get("usage") if event_type == "llm.ai.response" else content.get("usage")
                            usage_data = usage_data if isinstance(usage_data, dict) else {}
                            seq = int(event.get("seq", 0) or 0)
                            if seq <= cursor:
                                continue
                            store.record_run_usage_event(
                                session.session_id,
                                run_id=session.run_id,
                                seq=seq,
                                input_tokens=usage_data.get("input_tokens"),
                                output_tokens=usage_data.get("output_tokens"),
                                llm_call=event_type == "llm.ai.response",
                            )
                            cursor = seq
                        if len(events) < 200:
                            break
                    # Keep cumulative counters as a fallback when event rows
                    # omit usage. ApexStore chooses one source per run and
                    # refuses to add snapshots after measured event usage, so
                    # polling this on every tick cannot double-count.
                    store.record_run_usage(
                        session.session_id,
                        run_id=session.run_id,
                        input_tokens=int(getattr(run, "total_input_tokens", 0) or 0),
                        output_tokens=int(getattr(run, "total_output_tokens", 0) or 0),
                        llm_calls=int(getattr(run, "llm_call_count", 0) or 0),
                    )
                else:
                    store.record_run_usage(
                        session.session_id,
                        run_id=session.run_id,
                        input_tokens=int(getattr(run, "total_input_tokens", 0) or 0),
                        output_tokens=int(getattr(run, "total_output_tokens", 0) or 0),
                        llm_calls=int(getattr(run, "llm_call_count", 0) or 0),
                    )

                # Enforce the persisted session contract at the host boundary.
                # APEX can request interruption, but RunManager remains the
                # sole owner of the actual run lifecycle.
                contract = contract_from_snapshot(session.contract_snapshot, expected_digest=session.contract_digest) if session.contract_snapshot is not None else profile_for(session.profile, mission_id=session.mission_id)
                started_at = session.dispatch_started_at or session.usage.started_at
                runtime_limit = contract.budget.max_runtime_minutes
                latest = store.get(session.session_id)
                measured_tokens = latest.usage.total_tokens if latest is not None else None
                token_limit = contract.budget.max_total_tokens
                token_exhausted = token_limit is not None and measured_tokens is not None and measured_tokens >= token_limit
                runtime_exhausted = runtime_limit is not None and time.time() - started_at >= runtime_limit * 60
                if status in {"pending", "running", "queued"} and token_exhausted:
                    await run_manager.cancel(session.run_id, action="interrupt")
                    store.emit(
                        session.session_id,
                        "budget.tokens_exhausted",
                        run_id=session.run_id,
                        measured_tokens=measured_tokens,
                        limit=token_limit,
                    )
                    summary["budget_exhausted"] += 1
                    observed = await run_manager.get(session.run_id, user_id=session.owner, raise_on_store_error=True)
                    if observed is not None:
                        status = _observed_run_status(observed)
                elif status in {"pending", "running", "queued"} and runtime_exhausted:
                    await run_manager.cancel(session.run_id, action="interrupt")
                    # The twin token ceiling journals its own interrupt. A run
                    # stopped for elapsed time must leave the same evidence, or
                    # the only record of why the objective ended is the generic
                    # failure bucket below -- an operator reading the journal
                    # sees a failure with no cause, and a status projection
                    # showing zero budget exhaustion.
                    store.emit(
                        session.session_id,
                        "budget.runtime_exhausted",
                        run_id=session.run_id,
                        elapsed_minutes=round((time.time() - started_at) / 60.0, 3),
                        limit_minutes=runtime_limit,
                    )
                    summary["budget_exhausted"] += 1
                    observed = await run_manager.get(session.run_id, user_id=session.owner, raise_on_store_error=True)
                    if observed is not None:
                        status = _observed_run_status(observed)
                projected = _projected_dispatch_status(status)
                store.record_run_status(session.session_id, run_id=session.run_id, status=projected)
                if projected != status:
                    # Keep the real RunManager outcome in the durable journal:
                    # the dispatch projection has no ``timeout`` state, so the
                    # recorded one is the failure class and the observed name
                    # travels here.
                    store.emit(
                        session.session_id,
                        "run.status_projected",
                        run_id=session.run_id,
                        observed_status=status,
                        projected_status=projected,
                    )
                if status in _APEX_RUN_SUCCESS_STATUSES:
                    latest = store.get(session.session_id)
                    if latest is not None and latest.state is ApexSessionState.ACTIVE and latest.acceptance is None and latest.acceptance_criteria:
                        from alpha.mission.acceptance import get_acceptance_registry

                        registry = get_acceptance_registry()
                        if registry.names():
                            report = await asyncio.to_thread(registry.evaluate, list(latest.acceptance_criteria))
                            if report.all_evaluated:
                                stored = store.update(session.session_id, acceptance=report.to_dict())
                                if stored is None:
                                    raise RuntimeError("APEX session disappeared before acceptance could be stored")
                                store.emit(
                                    session.session_id,
                                    "acceptance.registry_report_submitted",
                                    report=report.to_dict(),
                                    run_id=session.run_id,
                                )
                                latest = stored
                    # An operator may approve an exact tool call after the
                    # RunManager turn that requested it has already ended.
                    # Requeue only when that approval remains unused and no
                    # measured acceptance report is available; otherwise the
                    # normal acceptance gate owns the next transition.
                    latest = store.get(session.session_id)
                    if latest is not None and status in _APEX_RUN_SUCCESS_STATUSES and store.requeue_approved_tool_action(session.session_id, run_id=session.run_id):
                        summary["approval_requeued"] += 1
                        continue
                    # A report may already have been persisted before a prior
                    # process stopped. Re-run the deterministic executive gate
                    # so a crash after report storage cannot strand the goal.
                    if latest is not None and latest.state is ApexSessionState.ACTIVE and latest.acceptance is not None:
                        contract = contract_from_snapshot(latest.contract_snapshot, expected_digest=latest.contract_digest) if latest.contract_snapshot is not None else profile_for(latest.profile, mission_id=latest.mission_id)
                        run_cycle(store, latest.session_id, contract)
                        updated = store.get(latest.session_id)
                        if updated is not None and updated.state is ApexSessionState.COMPLETED:
                            summary["completed"] += 1
                        elif updated is not None and updated.state is ApexSessionState.BLOCKED:
                            summary["blocked"] += 1
                        elif updated is not None and not updated.run_id and updated.dispatch_state == "idle":
                            summary["replanned"] += 1
                        else:
                            summary["awaiting_verification"] += 1
                    elif latest is not None and latest.state is ApexSessionState.BLOCKED:
                        summary["blocked"] += 1
                    else:
                        summary["awaiting_verification"] += 1
                elif status in _APEX_RUN_FAILED_STATUSES:
                    summary["failed"] += 1
                else:
                    summary["running"] += 1
                continue

            scope_key = session.thread_id or session.owner or DEFAULT_SCOPE
            mode = mode_store.for_scope(scope_key)
            if not mode.enabled:
                summary["skipped_mode_off"] += 1
                continue
            if mode.profile != session.profile:
                summary["skipped_profile_mismatch"] += 1
                continue

            if session.dispatch_state in {"awaiting_verification", "failed"}:
                if session.dispatch_state == "awaiting_verification":
                    summary["awaiting_verification"] += 1
                else:
                    summary["failed"] += 1
                continue

            if session.state is not ApexSessionState.ACTIVE:
                if session.state is not ApexSessionState.IDLE:
                    continue
                contract = contract_from_snapshot(session.contract_snapshot, expected_digest=session.contract_digest) if session.contract_snapshot is not None else profile_for(session.profile, mission_id=session.mission_id)
                # Record the executive's current decision before the host
                # adapter acts on it, preserving the decision/execution seam.
                result = run_cycle(store, session.session_id, contract)
                if result.decision.blocked or result.decision.action.value == "none":
                    continue
                store.update(session.session_id, mission_id=session.mission_id or session.session_id)
                session = store.set_state(session.session_id, ApexSessionState.ACTIVE, reason="host adapter admitted objective") or session
            elif not session.mission_id:
                store.update(session.session_id, mission_id=session.session_id)

            # The executive's dispatch decision is journaled before the host
            # starts the run. A blocked or no-op decision never reaches this
            # side-effect boundary.
            contract = contract_from_snapshot(session.contract_snapshot, expected_digest=session.contract_digest) if session.contract_snapshot is not None else profile_for(session.profile, mission_id=session.mission_id)
            decision = run_cycle(store, session.session_id, contract).decision
            if decision.blocked or decision.action.value != "dispatch":
                continue

            if contract.budget.max_total_tokens == 0:
                generation = store.claim_dispatch(session.session_id)
                if generation is not None:
                    reason = "APEX token budget is zero; no model run was admitted."
                    if store.record_dispatch_failure(session.session_id, generation=generation, reason=reason):
                        summary["failed"] += 1
                        summary["budget_exhausted"] += 1
                continue

            generation = store.claim_dispatch(session.session_id)
            if generation is None:
                continue
            run = await launch_apex_session_run(app=app, session=session, generation=generation)
            admitted_run_id = str(getattr(run, "run_id", "") or "")
            if store.record_dispatch_run(
                session.session_id,
                generation=generation,
                run_id=admitted_run_id,
                status=_observed_run_status(run),
            ):
                summary["dispatched"] += 1
            else:
                # A run was admitted but the APEX row refused the link. Leaving
                # the session in ``starting`` is not a recovery posture: its
                # generation is already consumed, so every later tick re-claims
                # the *same* generation and re-enters admission for a session
                # that will never link -- a stuck "starting" session and one
                # redundant admission attempt per tick, forever. Park it instead
                # and name the run so the operator can inspect the idempotent
                # record. ``record_dispatch_failure`` is itself a
                # compare-and-set that only lands while the dispatch is still
                # ``starting`` and unlinked, so a concurrent observer that
                # already moved this row is never overwritten by this path.
                reason = f"run was admitted but the session dispatch link changed (run_id={admitted_run_id or 'unknown'}, generation={generation}); inspect the idempotent run record"
                parked = store.record_dispatch_failure(session.session_id, generation=generation, reason=reason)
                summary["errors"].append(
                    {
                        "session_id": session.session_id,
                        "run_id": admitted_run_id,
                        "error": reason,
                        "parked": parked,
                    }
                )
                if parked:
                    summary["failed"] += 1
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            if generation is not None:
                # Parking this dispatch writes through the same store
                # transaction that just failed, so it can fail too. An
                # unguarded raise here escapes the adapter: the remaining
                # sessions of this pass are never examined, and the supervisor
                # counts a raising adapter against its restart budget until it
                # parks the APEX loop for every session.
                try:
                    parked = store.record_dispatch_failure(session.session_id, generation=generation, reason=reason)
                except Exception as park_exc:  # noqa: BLE001 - one session must not abort the pass
                    parked = False
                    logger.error(
                        "APEX dispatch failure for %s could not be parked: %s",
                        session.session_id,
                        f"{type(park_exc).__name__}: {park_exc}",
                    )
                if parked:
                    summary["failed"] += 1
                else:
                    summary["errors"].append(
                        {
                            "session_id": session.session_id,
                            "error": reason,
                            "park_error": "dispatch failure could not be persisted; inspect the session row",
                        }
                    )
            summary["errors"].append({"session_id": session.session_id, "error": reason})
            logger.warning("APEX host dispatch failed for %s: %s", session.session_id, exc)

    return summary


def self_update_tick() -> dict[str, Any]:
    """Run the opt-in source-update check/apply policy.

    This adapter is deliberately safe when the policy file is absent or
    disabled: it performs no network call and no subprocess.  The actual
    mutation is handed to a detached transaction by ``UpdateEngine`` so the
    Gateway process never rewrites its own source tree.
    """
    try:
        from alpha.evolution.update_engine import get_update_engine
        from alpha.evolution.update_policy import load_update_policy

        policy = load_update_policy()
        if not policy.enabled:
            return {"state": "DISABLED", "checked": False, "reason": "auto-update policy is disabled"}
        engine = get_update_engine()
        if engine.policy != policy:
            # Keep the Gateway-installed idle callback while applying an
            # operator policy edit; replacing the singleton would lose it.
            engine.set_policy(policy)
        return engine.check_and_maybe_apply()
    except Exception as exc:  # adapters must not crash the autonomy supervisor
        from alpha.evolution.update_state import redact_update_text

        reason = redact_update_text(f"{type(exc).__name__}: {exc}")
        logger.warning("Self-update tick failed closed: %s", reason)
        return {"state": "CHECK_FAILED", "error": reason}
