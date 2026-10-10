"""Built-in durable mission memory: the model-facing surface for long-horizon work.

NOTE: no ``from __future__ import annotations`` — LangChain's injected-argument
detection requires the concrete ``Runtime`` annotation object, exactly like the
sibling autonomy controls.

This tool lets an agent that is executing a multi-day objective *write down* the
state that keeps it coherent: freeze the objective and its constraints, decompose
it into verifiable milestones, append to a live status log, jot durable reasoning
into a scratchpad, and advance/verify milestones against a measured result. Every
write lands in the per-``(owner, thread)`` durable mission file that
:class:`~alpha.agents.middlewares.mission_memory_middleware.MissionMemoryMiddleware`
re-reads before every subsequent turn -- so the run re-anchors after a compaction
or a restart instead of drifting.

The tool records; it does not decide correctness. ``verify`` stores whatever the
caller measured (exit code, output, artifact) as the milestone's evidence; it
never runs a command and never infers a pass from prose. ``advance`` enforces the
structural stop-and-fix rule, refusing to move past an unverified or failed
milestone. ``status`` returns the same compact anchor the middleware injects, so
the model can confirm what the durable memory currently holds.

It is offline and deterministic: no model, provider, network or sandbox call.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from langchain.tools import tool

from alpha.config.paths import get_paths
from alpha.runtime.missions import (
    InvalidMilestonePlan,
    MilestonePlan,
    MilestoneVerdict,
    MissionManager,
    render_anchor,
    verify_milestone,
)
from alpha.runtime.user_context import resolve_runtime_user_id
from alpha.tools.types import Runtime

logger = logging.getLogger(__name__)

__all__ = ["mission_memory", "MAX_INPUT_CHARS"]

#: Bounds on any single text argument, mirroring the sibling truth-inspection
#: tools. Durable memory must not be a place to grow without limit.
MAX_INPUT_CHARS = 4000

_ACTIONS = ("status", "set_spec", "set_runbook", "set_plan", "log", "note", "verify", "advance")


def _resolve_scope(runtime: Runtime) -> tuple[str | None, str | None]:
    from langgraph.config import get_config

    context = getattr(runtime, "context", None) or {}
    thread = context.get("thread_id") or (get_config().get("configurable", {}) or {}).get("thread_id")
    try:
        owner = resolve_runtime_user_id(runtime)
    except Exception:
        owner = None
    return owner, (str(thread) if thread else None)


def _clean(text: str) -> str:
    return (text or "").strip()[:MAX_INPUT_CHARS]


def _collect_evidence(
    *,
    kind: str,
    source: str,
    artifact_root: str,
    criterion: str,
    detail: str,
    measured: bool,
) -> tuple[object | None, str | None]:
    """Collect a provenance-bearing evidence record, or say why none was measured.

    ``test_exit_report`` and ``artifact_digest`` read a *report the host already
    produced* through the sanctioned :mod:`alpha.mission.acceptance` collectors,
    which return ``None`` for a missing/unreadable/malformed source -- an
    ``UNVERIFIED`` outcome, never a guess. ``fact`` is a caller-observed fact and
    still requires a non-empty ``source`` naming what observed it. With no
    ``kind`` given this is an owner assertion recorded as an ``observed_fact``.

    Returns ``(record, None)`` on a measurement attempt and ``(None, error)``
    only for a malformed request (unknown kind, missing required path); a
    measured-but-None verdict is ``(None, None)`` -- the caller records
    ``unverified`` and leaves the milestone untouched.
    """
    from alpha.mission.acceptance import (
        EvidenceKind,
        EvidenceRecord,
        collect_artifact_digest,
        collect_test_exit_report,
    )

    if kind in ("", "fact", "observed_fact"):
        return EvidenceRecord(
            criterion=criterion,
            kind=EvidenceKind.OBSERVED_FACT,
            measured=bool(measured),
            source=source or "owner-assertion",
            detail=detail or "owner asserted a result without a measured source",
        ), None
    if kind == "test_exit_report":
        if not source:
            return None, "evidence_source_required"
        return collect_test_exit_report(source, criterion=criterion, scope=source), None
    if kind == "artifact_digest":
        if not source or not artifact_root:
            return None, "evidence_source_and_root_required"
        try:
            return collect_artifact_digest(artifact_root, source, criterion=criterion, scope=source), None
        except ValueError as exc:
            return None, str(exc)
    return None, f"unknown_evidence_kind:{kind}"


def _json_list(raw: str) -> list | None:
    if not raw.strip():
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list):
        return None
    return value


@tool("mission_memory", parse_docstring=True)
def mission_memory(
    runtime: Runtime,
    action: str = "status",
    objective: str = "",
    constraints_json: str = "[]",
    done_when_json: str = "[]",
    runbook: str = "",
    milestones_json: str = "[]",
    status: str = "",
    scratch: str = "",
    milestone_id: str = "",
    passed: bool = False,
    evidence: str = "",
    evidence_kind: str = "",
    evidence_source: str = "",
    artifact_root: str = "",
    criterion: str = "",
) -> dict[str, Any]:
    """Record/inspect the durable mission memory for an autonomous long-horizon run.

    Offline and deterministic: no model, provider, network or sandbox is called.
    ``status`` returns the same compact anchor injected before each turn. The
    other actions persist into the per-thread durable mission file so the run
    re-anchors after a compaction or a process restart. ``verify`` records a
    *measured* result and ``advance`` refuses to move past an unverified or
    failed milestone.

    Args:
        action: One of ``status``, ``set_spec``, ``set_runbook``, ``set_plan``, ``log``, ``note``, ``verify``, ``advance``.
        objective: For ``set_spec``: the frozen target. Keep it one testable sentence.
        constraints_json: For ``set_spec``: JSON array of hard-constraint strings that must not regress.
        done_when_json: For ``set_spec``: JSON array of the criteria that define done.
        runbook: For ``set_runbook``: how to operate (source of truth, scope discipline, when to validate).
        milestones_json: For ``set_plan``: JSON array of ``{"id","title","acceptance","validation"}`` objects.
        status: For ``log``: one line appended to the live status log.
        scratch: For ``note``: one line appended to the durable scratchpad.
        milestone_id: For ``verify``: the milestone id to record a result for.
        passed: For ``verify``: the outcome when ``evidence_kind`` is omitted (an owner assertion) or is ``fact``.
        evidence: For ``verify``: the measured detail, or the fact text for ``evidence_kind=fact``.
        evidence_kind: For ``verify``: ``test_exit_report`` (reads a JSON report with an integer ``exit_code``), ``artifact_digest`` (reads a file inside ``artifact_root``), or ``fact`` (a caller-observed fact).
            Omit for an owner assertion. A source that cannot be measured leaves the milestone ACTIVE (``unverified``), never a pass.
        evidence_source: For ``verify``: the report path, or the artifact path relative to ``artifact_root``.
        artifact_root: For ``verify``: the confined root ``evidence_source`` is read within (``artifact_digest`` only).
        criterion: For ``verify``: the criterion label recorded on the evidence; defaults to the milestone title.
    """

    normalized = (action or "status").strip().lower()
    if normalized not in _ACTIONS:
        return {"success": False, "error": "unknown_action", "allowed": list(_ACTIONS)}

    owner, thread = _resolve_scope(runtime)
    if not owner or not thread:
        return {"success": False, "error": "scope_unresolved"}

    from alpha.config import get_app_config

    cfg = getattr(get_app_config(), "mission_memory", None)
    manager = MissionManager(
        get_paths(),
        max_status_lines=int(getattr(cfg, "max_status_lines", 40)),
        max_scratch_entries=int(getattr(cfg, "max_scratch_entries", 60)),
    )
    stack = manager.load(owner, thread)

    try:
        if normalized == "status":
            pass  # fall through to the shared render below

        elif normalized == "set_spec":
            constraints = _json_list(constraints_json)
            done = _json_list(done_when_json)
            if constraints is None or done is None:
                return {"success": False, "error": "invalid_json"}
            stack = stack.with_spec(
                objective=_clean(objective),
                constraints=[str(c) for c in constraints],
                done_when=[str(c) for c in done],
            )

        elif normalized == "set_runbook":
            stack = stack.with_runbook(_clean(runbook))

        elif normalized == "set_plan":
            raw = _json_list(milestones_json)
            if raw is None:
                return {"success": False, "error": "invalid_json"}
            if not raw:
                return {"success": False, "error": "empty_plan"}
            plan = MilestonePlan.create(_clean(objective) or stack.spec_objective, raw)
            stack = stack.with_plan(plan)

        elif normalized == "log":
            stack = stack.log_status(_clean(status))
            if not stack.status_lines:
                return {"success": False, "error": "empty_status"}

        elif normalized == "note":
            stack = stack.note(_clean(scratch))
            if stack.scratchpad.count == 0:
                return {"success": False, "error": "empty_scratch"}

        elif normalized == "verify":
            if not stack.plan:
                return {"success": False, "error": "no_plan"}
            target = stack.plan.get(_clean(milestone_id))
            if target is None:
                return {"success": False, "error": "unknown_milestone"}
            record, collect_error = _collect_evidence(
                kind=_clean(evidence_kind).lower(),
                source=_clean(evidence_source),
                artifact_root=_clean(artifact_root),
                criterion=_clean(criterion) or target.title,
                detail=_clean(evidence),
                measured=bool(passed),
            )
            if collect_error:
                return {"success": False, "error": collect_error}
            updated_plan, verification = verify_milestone(stack.plan, milestone_id=_clean(milestone_id), record=record)
            if verification.verdict is MilestoneVerdict.UNVERIFIED:
                return {
                    "success": True,
                    "action": "verify",
                    "outcome": "unverified",
                    "milestone_id": _clean(milestone_id),
                    "note": verification.evidence,
                    "current_status": stack.plan.get(_clean(milestone_id)).status.value,
                }
            stack = stack.with_plan(updated_plan)

        elif normalized == "advance":
            if not stack.plan:
                return {"success": False, "error": "no_plan"}
            stack = stack.with_plan(stack.plan.advance())

    except InvalidMilestonePlan as exc:
        return {"success": False, "error": "invalid_plan", "reason": exc.reason}
    except ValueError as exc:
        return {"success": False, "error": "invalid_scope", "reason": str(exc)}

    stack = stack.touch(datetime.now(UTC).isoformat())

    try:
        manager.save(owner, thread, stack)
    except ValueError as exc:
        return {"success": False, "error": "persist_failed", "reason": str(exc)}

    plan = stack.plan
    result: dict[str, Any] = {
        "success": True,
        "action": normalized,
        "thread_id": thread,
        "objective": stack.spec_objective,
        "has_plan": plan is not None,
        "milestone_total": plan.total if plan else 0,
        "milestone_verified": plan.verified_count if plan else 0,
        "current_milestone": (plan.current.id if plan and plan.current else None),
        "plan_complete": bool(plan.complete) if plan else False,
        "status_lines": len(stack.status_lines),
        "scratch_notes": stack.scratchpad.count,
        "anchor": render_anchor(
            stack,
            status_tail=int(getattr(cfg, "status_tail", 4)),
            scratch_tail=int(getattr(cfg, "scratch_tail", 2)),
            max_chars=int(getattr(cfg, "max_anchor_chars", 4000)),
        ),
    }
    if normalized == "verify" and plan is not None:
        milestone = plan.get(_clean(milestone_id))
        if milestone is not None:
            result["verdict"] = milestone.status.value
            result["verified_evidence"] = milestone.evidence
    return result
