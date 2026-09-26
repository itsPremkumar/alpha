"""Built-in reversible, server-approved file quarantine tool."""

# NOTE: no ``from __future__ import annotations`` — LangChain's injected-argument
# detection requires the concrete ``Runtime`` annotation object.

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from langchain.tools import tool

from alpha.config.paths import get_paths
from alpha.projects.approval_queue import ApprovalRequest, get_approval_queue
from alpha.runtime.user_context import resolve_runtime_user_id
from alpha.safety.reversible_delete import ReversibleDeleteService
from alpha.tools.types import Runtime

_EXECUTE_ACTION = "reversible_delete_execute"
_RESTORE_ACTION = "reversible_delete_restore"
_PROJECT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _validated_project_id(value: str) -> str:
    if not isinstance(value, str) or _PROJECT_ID_RE.fullmatch(value) is None:
        raise ValueError("project_id must be a storage-safe identifier")
    return value


def _runtime_workspace(runtime: Runtime) -> Path:
    """Resolve the authenticated user's current thread workspace server-side."""

    context = getattr(runtime, "context", None)
    context = context if isinstance(context, dict) else {}
    thread_id = context.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        config = getattr(runtime, "config", None)
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("server thread context is required")

    user_id = resolve_runtime_user_id(runtime)
    expected = get_paths().sandbox_work_dir(thread_id, user_id=user_id)
    if expected.is_symlink():
        raise ValueError("thread workspace cannot be a symlink")
    expected_resolved = expected.resolve(strict=False)

    state = getattr(runtime, "state", None)
    thread_data = state.get("thread_data") if isinstance(state, dict) else None
    observed = thread_data.get("workspace_path") if isinstance(thread_data, dict) else None
    if isinstance(observed, str) and observed:
        if Path(observed).resolve(strict=False) != expected_resolved:
            raise ValueError("runtime workspace does not match authenticated thread workspace")

    expected.mkdir(parents=True, exist_ok=True)
    return expected_resolved


def _paths(raw: str) -> list[str]:
    if len(raw) > 64_000:
        raise ValueError("paths input is too large")
    value = json.loads(raw or "[]")
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError("paths_json must be a JSON array of non-empty strings")
    return value


def _plan_digest(service: ReversibleDeleteService, plan_id: str) -> str:
    plan = service.get_plan(plan_id)
    if plan is None:
        raise KeyError("delete plan not found")
    canonical = json.dumps(
        {
            "root": plan.root,
            "targets": [target.to_dict() for target in plan.targets],
            "max_files": plan.max_files,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _approval_summary(request: ApprovalRequest) -> dict[str, Any]:
    return {
        "request_id": request.request_id,
        "project_id": request.project_id,
        "action_type": request.action_type,
        "status": request.status,
        "resolved_by": request.resolved_by,
        "resolved_at": request.resolved_at,
    }


def _matching_approval(
    service: ReversibleDeleteService,
    project_id: str,
    plan_id: str,
) -> ApprovalRequest:
    plan = service.get_plan(plan_id)
    if plan is None:
        raise KeyError("delete plan not found")
    digest = _plan_digest(service, plan_id)
    for request in reversed(get_approval_queue(project_id).list_requests()):
        if request.action_type != _EXECUTE_ACTION:
            continue
        details = request.details
        if request.status in {"pending", "approved"} and details.get("plan_id") == plan_id and details.get("root") == plan.root and details.get("targets_digest") == digest:
            return request
    raise KeyError("matching approval request not found")


def _verify_approval(
    service: ReversibleDeleteService,
    project_id: str,
    approval_request_id: str,
    *,
    action_type: str,
    plan_id: str = "",
    receipt_id: str = "",
) -> tuple[ApprovalRequest | None, str]:
    if not project_id or not approval_request_id:
        return None, "approval_required"
    request = get_approval_queue(project_id).get_request(approval_request_id)
    if request is None:
        return None, "approval_required"
    if request.status == "pending":
        return request, "waiting_approval"
    if request.status in {"rejected", "timed_out"}:
        return request, "approval_rejected"
    if request.status != "approved":
        return request, "approval_rejected"
    if not request.resolved_by or not request.resolved_at:
        return request, "approval_mismatch"
    if request.project_id != project_id or request.action_type != action_type:
        return request, "approval_mismatch"
    details = request.details
    if action_type == _EXECUTE_ACTION:
        if not plan_id or details.get("plan_id") != plan_id:
            return request, "approval_mismatch"
        if details.get("root") != str(service.root):
            return request, "approval_mismatch"
        if details.get("targets_digest") != _plan_digest(service, plan_id):
            return request, "approval_mismatch"
    elif action_type == _RESTORE_ACTION:
        receipt = service.get_receipt(receipt_id)
        if receipt is None:
            return None, "not_found"
        if details.get("receipt_id") != receipt_id or details.get("root") != service.root:
            return request, "approval_mismatch"
    return request, "approved"


@tool("reversible_delete", parse_docstring=True)
def reversible_delete(
    runtime: Runtime,
    action: str = "plan",
    paths_json: str = "[]",
    plan_id: str = "",
    receipt_id: str = "",
    project_id: str = "",
    approval_request_id: str = "",
) -> dict[str, Any]:
    """Plan, request approval for, execute, or restore reversible file quarantine.

    This free, local safety boundary never hard-deletes data. ``plan`` creates a
    read-only target dossier (apart from internal plan metadata). ``request_approval``
    creates a human-gated project request bound to the exact root, plan, and target
    fingerprints. A human must resolve it through the authenticated project approval
    API before ``execute`` can move targets into the quarantine vault. ``restore``
    uses a separate approval request bound to the receipt and refuses to overwrite
    recreated paths. The hidden runtime resolves the authenticated user's
    current thread workspace; model-supplied roots are not accepted. Paths are
    root-confined; symlinks, traversal, protected roots, oversized trees, and
    batches over ten targets fail closed.

    Args:
        action: One of ``plan``, ``request_approval``, ``execute``, ``request_restore_approval``, ``restore``, or ``status``.
        paths_json: JSON array of relative target paths for ``plan``.
        plan_id: Plan identifier for execute approval and execution.
        receipt_id: Receipt identifier for restore approval and restoration.
        project_id: Existing project used for the human approval queue.
        approval_request_id: Server-issued approval request identifier.
    """

    normalized = (action or "plan").strip().lower()
    try:
        service = ReversibleDeleteService(_runtime_workspace(runtime))
        if project_id:
            project_id = _validated_project_id(project_id)
        if normalized == "plan":
            plan = service.plan(_paths(paths_json))
            return {
                "success": True,
                "action": normalized,
                "free_and_offline": True,
                "plan": plan.to_dict(),
                "plan_digest": _plan_digest(service, plan.plan_id),
            }
        if normalized == "request_approval":
            if not project_id or not plan_id:
                return {
                    "success": False,
                    "action": normalized,
                    "status": "approval_required",
                    "error": "project_id and plan_id are required",
                    "free_and_offline": True,
                }
            try:
                request = _matching_approval(service, project_id, plan_id)
            except KeyError:
                plan = service.get_plan(plan_id)
                if plan is None:
                    raise
                request = get_approval_queue(project_id).request_approval(
                    "lead_agent",
                    _EXECUTE_ACTION,
                    risk_level="high",
                    details={
                        "plan_id": plan.plan_id,
                        "root": plan.root,
                        "targets_digest": _plan_digest(service, plan_id),
                        "target_paths": [target.path for target in plan.targets],
                        "target_count": plan.target_count,
                    },
                    diff_preview=(f"Quarantine {plan.target_count} target(s) from {plan.root}: " + ", ".join(target.path for target in plan.targets)),
                )
            return {
                "success": True,
                "action": normalized,
                "status": "waiting_approval",
                "free_and_offline": True,
                "approval": _approval_summary(request),
            }
        if normalized == "execute":
            if not project_id or not plan_id or not approval_request_id:
                return {
                    "success": False,
                    "action": normalized,
                    "status": "approval_required",
                    "error": "project_id, plan_id, and approval_request_id are required",
                    "free_and_offline": True,
                }
            approval, approval_status = _verify_approval(
                service,
                project_id,
                approval_request_id,
                action_type=_EXECUTE_ACTION,
                plan_id=plan_id,
            )
            if approval_status != "approved":
                return {
                    "success": False,
                    "action": normalized,
                    "status": approval_status,
                    "approval": _approval_summary(approval) if approval is not None else None,
                    "free_and_offline": True,
                }
            receipt = service.execute(
                plan_id,
                approved=True,
                approval_reference=f"project:{project_id}:{approval_request_id}",
            )
            return {
                "success": receipt.status == "quarantined",
                "action": normalized,
                "free_and_offline": True,
                **receipt.to_dict(),
                "approval": _approval_summary(approval),
            }
        if normalized == "request_restore_approval":
            receipt = service.get_receipt(receipt_id)
            if not project_id or receipt is None:
                return {
                    "success": False,
                    "action": normalized,
                    "status": "approval_required",
                    "error": "project_id and a valid receipt_id are required",
                    "free_and_offline": True,
                }
            request = get_approval_queue(project_id).request_approval(
                "lead_agent",
                _RESTORE_ACTION,
                risk_level="medium",
                details={
                    "receipt_id": receipt_id,
                    "root": service.root,
                    "target_paths": [entry.original_path for entry in receipt.entries],
                },
                diff_preview=(f"Restore {len(receipt.entries)} quarantined target(s) to {service.root}: " + ", ".join(entry.original_path for entry in receipt.entries)),
            )
            return {
                "success": True,
                "action": normalized,
                "status": "waiting_approval",
                "free_and_offline": True,
                "approval": _approval_summary(request),
            }
        if normalized == "restore":
            if not project_id or not receipt_id or not approval_request_id:
                return {
                    "success": False,
                    "action": normalized,
                    "status": "approval_required",
                    "error": "project_id, receipt_id, and approval_request_id are required",
                    "free_and_offline": True,
                }
            approval, approval_status = _verify_approval(
                service,
                project_id,
                approval_request_id,
                action_type=_RESTORE_ACTION,
                receipt_id=receipt_id,
            )
            if approval_status != "approved":
                return {
                    "success": False,
                    "action": normalized,
                    "status": approval_status,
                    "approval": _approval_summary(approval) if approval is not None else None,
                    "free_and_offline": True,
                }
            receipt = service.restore(
                receipt_id,
                approved=True,
                approval_reference=f"project:{project_id}:{approval_request_id}",
            )
            return {
                "success": receipt.status == "restored",
                "action": normalized,
                "free_and_offline": True,
                **receipt.to_dict(),
                "approval": _approval_summary(approval),
            }
        if normalized == "status":
            return {"success": True, "action": normalized, "free_and_offline": True, **service.status()}
        return {
            "success": False,
            "action": normalized,
            "error": "unsupported_action",
            "supported_actions": [
                "plan",
                "request_approval",
                "execute",
                "request_restore_approval",
                "restore",
                "status",
            ],
            "free_and_offline": True,
        }
    except (ValueError, FileNotFoundError) as exc:
        return {
            "success": False,
            "action": normalized,
            "error": "invalid_delete_request",
            "detail": str(exc),
            "free_and_offline": True,
        }
    except KeyError as exc:
        return {
            "success": False,
            "action": normalized,
            "error": "not_found",
            "detail": str(exc),
            "free_and_offline": True,
        }
    except Exception as exc:  # pragma: no cover - defensive model-tool boundary
        return {
            "success": False,
            "action": normalized,
            "error": "reversible_delete_unavailable",
            "detail": f"{type(exc).__name__}: {exc}",
            "free_and_offline": True,
        }


__all__ = ["reversible_delete"]
