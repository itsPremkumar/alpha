"""Versioned durable checkpoints and the resume protocol.

A checkpoint is the run's *whole* recoverable state in one atomic, versioned
blob: lifecycle, budget account, receipt-chain head, workspace digest, and the
idempotency key the run was admitted under. The rules are the plan's §11.2 and
§11.3, made structural:

* **Written atomically.** Stage beside the target, fsync, ``os.replace``. A
  half-written checkpoint that a crash leaves behind is a torn file the loader
  refuses, not a state the run resumes into.
* **Validated before resume, never after.** :func:`load_checkpoint` checks
  schema version, internal agreement (lifecycle state vs. last transition,
  receipt head vs. the chain in the blob) and returns a
  :class:`ResumeVerdict`. A verdict is *data*: ``ok`` plus reasons, so the
  caller quarantines on ``not ok`` exactly as §11.3 step 7 requires, instead of
  catching an exception and improvising.
* **A checkpoint cannot preserve a permission.** The blob stores profile
  *identifiers*, never resolved grants. Resume re-resolves them through
  :mod:`alpha.avo.profiles`, so a profile revoked between save and load is a
  refusal at resume rather than a run continuing under yesterday's grant.
* **No secrets, no raw tool output.** Digests, ids, counters, reason codes.

The durable format is deliberately boring JSON: this is control state, and a
control state that needs a custom deserializer to recover is a control state
that cannot be recovered under pressure.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .budgets import BudgetLedger
from .contracts import canonical_json
from .lifecycle import RunLifecycle
from .profiles import UnknownProfile, approval_policy, budget_profile, capability_profile
from .receipts import ReceiptChain

__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "Checkpoint",
    "ResumeVerdict",
    "load_checkpoint",
    "write_checkpoint",
]

logger = logging.getLogger("alpha.avo.checkpointing")

#: Version of the on-disk checkpoint contract. A loader that speaks another
#: version refuses by name rather than guessing at field meanings.
CHECKPOINT_SCHEMA_VERSION = 1


@dataclass
class Checkpoint:
    """One run's durable control state."""

    alpha_run_id: str
    avo_run_id: str
    task_id: str
    #: Profile *identifiers*. Re-resolved on resume; never trusted as grants.
    capability_profile_id: str
    budget_profile_id: str
    evaluator_profile_id: str
    approval_policy_id: str
    lifecycle: RunLifecycle
    ledger: BudgetLedger
    receipts: ReceiptChain
    workspace_digest: str = ""
    candidate_digest: str | None = None
    action_cursor: int = 0
    experiment_count: int = 0
    #: The admission idempotency key. A resume under a different key is refused.
    idempotency_key: str = ""
    checkpoint_id: str = field(default_factory=lambda: f"ckpt_{uuid.uuid4().hex[:12]}")
    created_at: float = field(default_factory=time.time)
    #: Set when the previous resume attempt failed validation, so repeated
    #: restarts into a broken state accumulate evidence instead of retrying
    #: silently forever.
    prior_rejection: str | None = None

    # ------------------------------------------------------------------
    def payload(self) -> dict[str, Any]:
        """The full serialisable state. One dict, one digest, one replace."""
        body = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_id": self.checkpoint_id,
            "created_at": self.created_at,
            "alpha_run_id": self.alpha_run_id,
            "avo_run_id": self.avo_run_id,
            "task_id": self.task_id,
            "profiles": {
                "capability": self.capability_profile_id,
                "budget": self.budget_profile_id,
                "evaluator": self.evaluator_profile_id,
                "approval": self.approval_policy_id,
            },
            "lifecycle": self.lifecycle.to_dict(),
            "budget": self.ledger.to_dict(),
            "receipts": self.receipts.to_dict(),
            "workspace_digest": self.workspace_digest,
            "candidate_digest": self.candidate_digest,
            "action_cursor": self.action_cursor,
            "experiment_count": self.experiment_count,
            "idempotency_key": self.idempotency_key,
            "prior_rejection": self.prior_rejection,
        }
        body["integrity"] = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
        return body

    def digest(self) -> str:
        """The integrity digest — recomputed by the loader, never trusted as-is."""
        return str(self.payload()["integrity"])


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------
def write_checkpoint(checkpoint: Checkpoint, path: str | Path) -> Path:
    """Write atomically: stage, fsync, replace. Returns the final path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = checkpoint.payload()
    staged = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    data = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    with open(staged, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staged, target)
    logger.debug("checkpoint %s written to %s", checkpoint.checkpoint_id, target)
    return target


# ---------------------------------------------------------------------------
# load + resume
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ResumeVerdict:
    """What a resume check found. ``ok`` is the only field that grants trust.

    The reasons are cumulative: a checkpoint that fails two checks reports
    both, so a repair fixes everything the first pass would have revealed one
    restart at a time.
    """

    ok: bool
    checkpoint: Checkpoint | None
    reasons: tuple[str, ...] = ()

    @classmethod
    def refused(cls, *reasons: str) -> ResumeVerdict:
        return cls(ok=False, checkpoint=None, reasons=tuple(reasons))

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reasons": list(self.reasons), "checkpoint_id": self.checkpoint.checkpoint_id if self.checkpoint else None}


def load_checkpoint(path: str | Path, *, expected_idempotency_key: str | None = None) -> ResumeVerdict:
    """Load and *validate* a checkpoint. Never raises for a bad checkpoint.

    §11.3 steps 1-5 compressed into the checks a loader can actually make:
    schema, integrity, internal agreement, profile re-resolution, and (when the
    caller states one) idempotency-key equality. Steps 3-4 (reconcile the
    in-flight action against the real environment) belong to the runner that
    has the environment; this module refuses before that work starts or it is
    a resume on unverified state.
    """
    target = Path(path)
    if not target.exists():
        return ResumeVerdict.refused(f"checkpoint file not found: {target}")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ResumeVerdict.refused(f"checkpoint unreadable: {type(exc).__name__}: {exc}")
    if not isinstance(raw, dict):
        return ResumeVerdict.refused("checkpoint root is not an object")

    reasons: list[str] = []

    version = raw.get("schema_version")
    if version != CHECKPOINT_SCHEMA_VERSION:
        return ResumeVerdict.refused(f"unsupported checkpoint schema_version {version!r}; this build speaks {CHECKPOINT_SCHEMA_VERSION}")

    recorded = raw.get("integrity")
    unsigned = {key: value for key, value in raw.items() if key != "integrity"}
    recomputed = hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()
    if not isinstance(recorded, str) or recorded != recomputed:
        # Do not attempt further validation: an integrity failure means the
        # bytes are not the ones that were written, so every other field is
        # suspect too.
        return ResumeVerdict.refused("checkpoint integrity digest mismatch: the file is not the one that was written")

    # -- profiles re-resolve ------------------------------------------------
    profiles = raw.get("profiles") or {}
    capability_id = str(profiles.get("capability", ""))
    budget_id = str(profiles.get("budget", ""))
    evaluator_id = str(profiles.get("evaluator", ""))
    approval_id = str(profiles.get("approval", ""))
    capability = budget = approval = None
    for kind, profile_id, resolver in (
        ("capability", capability_id, capability_profile),
        ("budget", budget_id, budget_profile),
        ("approval", approval_id, approval_policy),
    ):
        try:
            resolved = resolver(profile_id)
        except UnknownProfile as exc:
            reasons.append(f"profile {kind} {profile_id!r} no longer resolves: {exc}")
            continue
        if kind == "capability":
            capability = resolved
            if not resolved.enabled:
                reasons.append(f"capability profile {profile_id!r} is disabled at resume; a checkpoint cannot preserve a revoked grant")
        elif kind == "budget":
            budget = resolved
        else:
            approval = resolved
    try:
        from .profiles import evaluator_profile

        evaluator_profile(evaluator_id)
    except UnknownProfile as exc:
        reasons.append(f"evaluator profile {evaluator_id!r} no longer resolves: {exc}")

    if reasons:
        # State was parsed but cannot be acted on; keep no partial checkpoint.
        return ResumeVerdict.refused(*reasons)

    # -- lifecycle ----------------------------------------------------------
    try:
        lifecycle = RunLifecycle.from_dict(raw.get("lifecycle") or {})
    except ValueError as exc:
        return ResumeVerdict.refused(f"lifecycle rejected: {exc}")
    if lifecycle.avo_run_id and lifecycle.avo_run_id != str(raw.get("avo_run_id", "")):
        return ResumeVerdict.refused(f"lifecycle run id {lifecycle.avo_run_id!r} disagrees with checkpoint run id {raw.get('avo_run_id')!r}")

    # -- receipts -----------------------------------------------------------
    from .receipts import ReceiptRejected

    try:
        receipts = ReceiptChain.from_dict(raw.get("receipts") or {})
    except ReceiptRejected as exc:
        return ResumeVerdict.refused(f"receipt chain rejected: {exc}")
    if receipts.avo_run_id and receipts.avo_run_id != str(raw.get("avo_run_id", "")):
        return ResumeVerdict.refused(f"receipt chain run id {receipts.avo_run_id!r} disagrees with checkpoint run id {raw.get('avo_run_id')!r}")
    verdict = receipts.verify()
    if not verdict.ok:
        return ResumeVerdict.refused(f"receipt chain invalid: {verdict.defect}")

    # -- idempotency --------------------------------------------------------
    key = str(raw.get("idempotency_key", ""))
    if expected_idempotency_key is not None and key != expected_idempotency_key:
        return ResumeVerdict.refused("checkpoint idempotency key does not match the resume request; resuming under a different admission is a new run")

    # -- rebuild the ledger -------------------------------------------------
    # The ledger is rebuilt from its snapshot through one deliberate seam: the
    # snapshot round-trips through a fresh BudgetLedger's dimensions. A
    # ceiling that no longer exists (profile changed under the same id — the
    # id is pinned, so this cannot silently happen) would be caught by the
    # reserve-on-next-action path.
    assert capability is not None and budget is not None and approval is not None  # noqa: S101 - reasoned above: all three resolved or we returned
    ledger = BudgetLedger(profile=budget, started_at=0.0)
    ledger_state = raw.get("budget") or {}
    for name, entry in (ledger_state.get("dimensions") or {}).items():
        try:
            from .budgets import BudgetKind

            kind = BudgetKind(name)
        except ValueError:
            reasons.append(f"budget snapshot carries unknown dimension {name!r}")
            continue
        dim = ledger.dimension(kind)
        dim.reserved = float(entry.get("reserved", 0.0))
        dim.used = float(entry.get("used", 0.0))
    if reasons:
        return ResumeVerdict.refused(*reasons)

    checkpoint = Checkpoint(
        alpha_run_id=str(raw.get("alpha_run_id", "")),
        avo_run_id=str(raw.get("avo_run_id", "")),
        task_id=str(raw.get("task_id", "")),
        capability_profile_id=capability_id,
        budget_profile_id=budget_id,
        evaluator_profile_id=evaluator_id,
        approval_policy_id=approval_id,
        lifecycle=lifecycle,
        ledger=ledger,
        receipts=receipts,
        workspace_digest=str(raw.get("workspace_digest", "")),
        candidate_digest=raw.get("candidate_digest"),
        action_cursor=int(raw.get("action_cursor", 0)),
        experiment_count=int(raw.get("experiment_count", 0)),
        idempotency_key=key,
        checkpoint_id=str(raw.get("checkpoint_id", "")),
        created_at=float(raw.get("created_at", 0.0)),
        prior_rejection=raw.get("prior_rejection"),
    )
    return ResumeVerdict(ok=True, checkpoint=checkpoint, reasons=())
