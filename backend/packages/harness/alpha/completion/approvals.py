"""Approvals bound to a gate's *definition*, never to a gate's name.

An approval is permission to execute one specific, already-reviewed check
shape. The binding is the definition digest (``evidence.definition_digest``),
so re-parameterising the gate, changing its working directory, bumping the
check implementation or moving policy all invalidate it automatically — the
thing the plan calls "approval invalidation", achieved without a revocation
job because the digest simply no longer matches.

Three rules:

* **``deny`` is not approvable.** The command policy's ``deny`` verdict is
  final; asking a human to click past it would make the policy decorative.
* **Approval is not a sandbox.** Granting permission lets a command *run*; it
  restricts nothing about what that command can do with the process's ambient
  OS permissions. The record says who approved exactly what, and that is all.
* **No secrets in a grant.** The record carries the approver, the timestamp,
  the digest and a reason — never the command's output, environment or
  parameters verbatim (the digest already commits to them).
"""

from __future__ import annotations

import time

from .errors import ApprovalRefused
from .gates import command_permission
from .models import ApprovalPolicy, ApprovalRecord, Gate
from .policy import CompletionSettings

#: Approval records live beside the store document, keyed by gate id. They are
#: deliberately *not* embedded in the gate: a gate can be re-created with the
#: same id and the stale grant must not travel with it.


def is_valid(approval: ApprovalRecord | None, gate: Gate) -> bool:
    """True only when the grant covers the gate's *current* definition."""
    if approval is None:
        return False
    if not gate.definition_digest:
        # The gate has never been digested, so nothing can be bound to it.
        return False
    return approval.definition_digest == gate.definition_digest


def request_approval(gate: Gate, *, now: float | None = None) -> dict[str, object]:
    """Describe what an approval would cover, so the UI can ask honestly."""
    return {
        "gate_id": gate.gate_id,
        "title": gate.title,
        "definition_digest": gate.definition_digest,
        "risk_tier": gate.risk_tier.value,
        "approval_policy": gate.approval_policy.value,
        "requested_at": time.time() if now is None else now,
        "reason": _approval_reason(gate),
    }


def grant(gate: Gate, approver: str, *, reason: str = "", now: float | None = None) -> ApprovalRecord:
    """Approve the gate's current definition.

    Raises :class:`~alpha.completion.errors.ApprovalRefused` for a gate the
    policy never permits, for a gate whose definition has not been digested,
    or for an empty approver — an unidentified approver is not an approval.
    """
    if not approver or not str(approver).strip():
        raise ApprovalRefused(f"gate {gate.gate_id!r} requires an identified approver", subject=gate.gate_id)
    if gate.approval_policy is ApprovalPolicy.DENIED:
        raise ApprovalRefused(f"gate {gate.gate_id!r} declares approval_policy=denied; it can never be approved", subject=gate.gate_id)
    if not gate.definition_digest:
        raise ApprovalRefused(
            f"gate {gate.gate_id!r} has no definition digest; digest it before approving so the grant binds to something",
            subject=gate.gate_id,
        )
    record = ApprovalRecord(
        approved_by=str(approver).strip(),
        approved_at=time.time() if now is None else now,
        definition_digest=gate.definition_digest,
        reason=reason or _approval_reason(gate),
    )
    gate.approval = record
    return record


def revoke(gate: Gate, *, reason: str = "") -> bool:
    """Drop the grant. Returns whether one existed."""
    existed = gate.approval is not None
    gate.approval = None
    if reason:
        gate.last_failure = f"approval revoked: {reason}"
    return existed


def effective_approval(gate: Gate, approvals: dict[str, dict]) -> ApprovalRecord | None:
    """The stored grant for ``gate``, only if it still matches its digest.

    A stale grant is *not* deleted here (history is evidence too) — it is
    simply not honoured, and ``grant`` overwrites it on the next approval.
    """
    stored = approvals.get(gate.gate_id)
    if not stored:
        return None
    try:
        record = ApprovalRecord.model_validate(stored)
    except Exception:  # noqa: BLE001 - a malformed grant is an absent grant, never a permissive one
        return None
    return record if is_valid(record, gate) else None


def gate_is_permitted(gate: Gate, settings: CompletionSettings, approvals: dict[str, dict]) -> tuple[bool, str]:
    """Whether this gate may execute right now, and why not if it may not.

    Command-shaped gates are judged by the shared command policy; the answer
    is ``allow``, ``ask`` (needs a live approval) or ``deny`` (final).
    """
    if gate.check_ref in {"file.exists", "artifact.parse", "manual.review"}:
        return True, "check does not execute anything"
    if gate.check_ref == "api.request":
        if not settings.allow_network_checks:
            return False, "network checks are disabled (completion.allow_network_checks)"
        return True, "network checks enabled"
    argv = _argv_for(gate)
    if argv is None:
        return False, "gate declares no runnable argv"
    verdict, reason = command_permission(argv, settings)
    if verdict == "allow":
        return True, reason
    if verdict == "deny":
        return False, f"command policy denies this check: {reason}"
    if is_valid(effective_approval(gate, approvals), gate):
        return True, f"approved by {gate.approval.approved_by} for this definition digest"
    return False, f"approval required ({verdict}): {reason}"


def _approval_reason(gate: Gate) -> str:
    if gate.check_ref == "api.request":
        return "network probe"
    argv = _argv_for(gate)
    if argv is None:
        return f"gate {gate.gate_id} ({gate.check_ref})"
    return " ".join(str(part) for part in argv)[:500]


def _argv_for(gate: Gate) -> list[str] | None:
    params = gate.parameters
    if gate.check_ref == "pytest.run":
        targets = params.get("targets", [])
        if isinstance(targets, str):
            targets = [targets]
        return ["pytest", *[str(t) for t in targets]]
    argv = params.get("argv")
    if isinstance(argv, str):
        import shlex

        argv = shlex.split(argv)
    if isinstance(argv, list) and argv:
        return [str(part) for part in argv]
    if gate.check_ref == "api.request":
        url = str(params.get("url", ""))
        if url.startswith(("http://", "https://")):
            return [url.split("/")[0] + "//" + url.split("/")[2]]
    return None


__all__ = [
    "effective_approval",
    "gate_is_permitted",
    "grant",
    "is_valid",
    "request_approval",
    "revoke",
]
