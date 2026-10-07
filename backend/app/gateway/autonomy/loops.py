"""Loop adapters: one tick per subsystem, safe by default.

Each adapter performs exactly ONE unit of work and must be safe to call
repeatedly. Adapters never raise (they return a dict summary); a tick that needs
a model/API key degrades to a no-op summary so a machine without credentials
still runs a complete, healthy system.
"""

from __future__ import annotations

import logging
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


def skill_curator_tick(*, dry_run: bool = True) -> dict[str, Any]:
    """One skill-curator prune pass. Dry-run by default; the flag decides."""
    from alpha.skills.curator import SkillCurator

    curator = SkillCurator()
    return curator.apply_transitions(dry_run=dry_run)


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
    * **It counts rather than hides.** ``skipped_terminal`` and
      ``cycles_blocked`` are both reported. "I ran one pass" and "three sessions
      refused to progress" are different operational facts, and a summary that
      only returned the first would hide the second.
    """
    try:
        from alpha.apex.contract import profile_for
        from alpha.apex.executive import run_cycle
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

    summary: dict[str, Any] = {
        "sessions": None,
        "cycles": 0,
        "skipped_terminal": 0,
        "cycles_blocked": 0,
        "decisions": {},
        "errors": [],
    }
    try:
        sessions = store.list(limit=200)
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    summary["sessions"] = len(sessions)
    for session in sessions:
        if session.is_terminal:
            summary["skipped_terminal"] += 1
            continue
        try:
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
