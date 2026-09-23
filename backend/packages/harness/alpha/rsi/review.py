"""RSI human review gate — the HITL review channel (plan WP-C2 slice C2b, feature #4).

WP-B1 (``alpha/rsi/workspace.py``) discloses ``review_required`` protected-path
results as *disclosure, not approval* because the candidate loop has no human
review channel.  This module is that channel: one candidate = one approval
ticket opened through the existing human-approval precedent
(``alpha/projects/approval_queue.ApprovalQueue`` — reused, not duplicated),
with an honest decision derived from the ticket's real resolution.

Scope disclosure (adapted from plan §3 WP-C2): the plan's automated
``review_candidate(candidate_id, evidence, *, risk) -> list[dict]`` composition
(hyperplan / adversary / council-role verdicts normalized to spec §21's
``{"reviewer","status","findings","required_changes","confidence"}`` shape) is
**not** part of this unit — this slice ships the human review gate only, per
the assigned scope.  That automated verdict composition is deferred, never
faked; this module therefore never serializes a ``confidence`` field at all.

Honest-failure contract (plan §3 WP-C2 / §5 guardrails):

- Decision states are exactly ``pending`` / ``approved`` / ``rejected``
  (``DECISION_STATES``).  A failed lookup — missing review record, missing or
  mismatched approval request, corrupt record, unknown status — yields
  ``pending``: fail-closed, never auto-approved.
- **No self-approval (§5.3):** this module has no function that approves or
  rejects anything.  ``open_review`` only *creates* a pending request via
  ``ApprovalQueue.request_approval``; the resolution must come from the human
  channel (``ApprovalQueue.resolve_request`` called by an operator/endpoint
  outside this module).  Tests AST-pin that ``resolve_request`` is never
  called here — candidates can never approve their own changes, and review
  channels are human-only.
- **No fabricated reviewer identity:** ``ReviewDecision.reviewer`` is copied
  verbatim from the approval record's ``resolved_by``; an absent human stays
  ``None``, and an "approved" record without a reviewer identity fails closed
  to ``pending``.
- **Complete evidence or refusal (§5.6):** approval is refused — the decision
  stays ``pending`` with the real defect text — unless every key in
  ``REQUIRED_EVIDENCE_KEYS`` is present, non-empty, and not *declared*
  ``simulated`` (simulated evidence can never gate) or ``unverified``
  (disclosed, not a pass).  Incomplete evidence is refused *before* a request
  is enqueued and re-checked at decision time.  A payload that declares no
  ``evidence_kind`` is accepted as present content for the human to judge —
  the kind is validated only when claimed, and no kind is ever invented.
- **Provenance:** every decision carries who/what/when — ``who`` is the
  verbatim reviewer (``None`` when absent), ``what`` identifies the candidate
  and risk class, ``when_opened`` comes from the injectable ``clock`` argument
  to ``open_review`` (deterministic in tests), ``when_decided`` is copied
  verbatim from the approval queue's ``resolved_at``.
- **No invented confidence scores:** neither records nor decisions serialize
  a ``confidence``/``score`` field — this gate records decisions, not numbers.
- **WP-B3 untouched:** this module never references the protected-path policy
  module or its rule constant; a ``deny`` there keeps failing closed even when
  this gate reports ``approved`` (pinned in tests).  The ``reviewed=True``
  flag a caller may derive from ``human_reviewed()`` can only ever satisfy
  ``review_required`` paths — it can never override a deny.

Adapted signatures (disclosed): plan lines 369-373 name ``review_candidate``
for the automated verdict list and leave the human channel implicit; this
unit instead exposes ``open_review`` / ``review_decision`` /
``human_reviewed`` over the real ``ApprovalQueue`` API.  ``rsi/state.py`` is
not imported: review decisions persist in their own record file and the
approval queue — no RSI cycle-state field is added.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.identity import atomic_write_json
from alpha.projects.approval_queue import ApprovalQueue
from alpha.rsi.lineage import EVIDENCE_KINDS, RsiLineageStore

logger = logging.getLogger(__name__)

__all__ = [
    "ACTION_TYPE",
    "BOT_NAME",
    "DECISION_STATES",
    "DEFAULT_PROJECT_ID",
    "MAX_REVIEW_EVIDENCE_CHARS",
    "REQUIRED_EVIDENCE_KEYS",
    "RISK_LEVELS",
    "ReviewDecision",
    "ReviewRecord",
    "evidence_defects",
    "human_reviewed",
    "load_review",
    "open_review",
    "review_decision",
    "review_record_path",
]

#: The only honest decision states (plan §3 WP-C2): a failed lookup or
#: missing evidence lands ``pending`` — never a fourth "approved-by-default".
DECISION_STATES: tuple[str, ...] = ("pending", "approved", "rejected")

#: Virtual project bucket that groups RSI review tickets in the shared
#: human-approval queue (one queue file per project under runtime_home).
DEFAULT_PROJECT_ID = "rsi"
BOT_NAME = "rsi_review"
ACTION_TYPE = "rsi_review"

#: Evidence a reviewer must be able to inspect before any approval can stand
#: (plan §3 WP-C2: reviewer inputs come from the evidence bundle, not from the
#: candidate's self-report).  Extensible per call via ``required_keys`` — but
#: never empty (an empty requirement list would make approval vacuous).
REQUIRED_EVIDENCE_KEYS: tuple[str, ...] = ("manifest", "baseline_metrics", "candidate_metrics", "holdout")

#: Risk class -> ``ApprovalQueue`` ``risk_level`` (its real
#: ``Literal["low","medium","high","critical"]``).  Only the *label* is
#: validated here; risk *policy* (auto-promote etc.) is WP-C2c's
#: ``promotion.py``, not this module's.
RISK_LEVELS: dict[str, str] = {"R0": "low", "R1": "low", "R2": "medium", "R3": "high", "R4": "high", "R5": "critical"}

#: Cap mirroring the WP-A1 archive convention: refuse oversized evidence with
#: the real numbers rather than silently truncating it.
MAX_REVIEW_EVIDENCE_CHARS = 65536

#: The only state *this module ever persists*: approvals live exclusively in
#: the human queue, so a record file claiming "approved" is corrupt by definition.
_RECORD_STATE = "pending"

#: Safe filename ids (mirrors the WP-A1 archive rule): no separators — a
#: candidate id can never escape the review-record directory.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _require_safe_id(candidate_id: Any) -> str:
    """Validate a candidate id for record-file use; real text on refusal."""
    if not isinstance(candidate_id, str) or not _SAFE_ID_RE.match(candidate_id):
        raise ValueError(f"Unsafe candidate_id {candidate_id!r}; review record file names must match {_SAFE_ID_RE.pattern} (no path separators).")
    return candidate_id


@dataclass
class ReviewRecord:
    """What a review records against: the candidate's durable identity + the
    evidence the human will judge + the approval ticket it opened (or the
    honest refusal that no ticket was opened)."""

    candidate_id: str
    risk: str
    project_id: str
    required_keys: list[str]
    evidence: dict[str, Any]
    opened_at: float
    state: str = _RECORD_STATE
    reason: str = ""
    request_id: str | None = None
    lineage_status: str | None = None
    lineage_payload_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Any) -> ReviewRecord:
        """Strictly validate a persisted record; ``ValueError`` names the real defect."""
        if not isinstance(payload, dict):
            raise ValueError(f"review record payload must be a JSON object, got {type(payload).__name__}")
        required = (
            "candidate_id",
            "risk",
            "project_id",
            "required_keys",
            "evidence",
            "opened_at",
            "state",
            "reason",
            "request_id",
            "lineage_status",
            "lineage_payload_hash",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError(f"review record missing field(s): {', '.join(missing)}")
        if payload["state"] != _RECORD_STATE:
            raise ValueError(
                f"review record state must be {_RECORD_STATE!r} — the only state this gate records; approvals live in the human queue, got {payload['state']!r}"
            )
        candidate_id = payload["candidate_id"]
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("review record candidate_id must be a non-empty string")
        for key in ("risk", "project_id", "reason"):
            if not isinstance(payload[key], str):
                raise ValueError(f"review record {key} must be a string, got {type(payload[key]).__name__}")
        if not payload["project_id"]:
            raise ValueError("review record project_id must be a non-empty string")
        keys = payload["required_keys"]
        if not isinstance(keys, list) or not keys or not all(isinstance(key, str) and key for key in keys):
            raise ValueError(f"review record required_keys must be a non-empty list of evidence names, got {keys!r}")
        if not isinstance(payload["evidence"], dict):
            raise ValueError(f"review record evidence must be a JSON object, got {type(payload['evidence']).__name__}")
        opened_at = payload["opened_at"]
        if isinstance(opened_at, bool) or not isinstance(opened_at, int | float):
            raise ValueError("review record opened_at must be a number")
        for key in ("request_id", "lineage_status", "lineage_payload_hash"):
            value = payload[key]
            if value is not None and not isinstance(value, str):
                raise ValueError(f"review record {key} must be a string or None, got {type(value).__name__}")
        return cls(
            candidate_id=candidate_id,
            risk=payload["risk"],
            project_id=payload["project_id"],
            required_keys=list(keys),
            evidence=dict(payload["evidence"]),
            opened_at=float(opened_at),
            state=payload["state"],
            reason=payload["reason"],
            request_id=payload["request_id"],
            lineage_status=payload["lineage_status"],
            lineage_payload_hash=payload["lineage_payload_hash"],
        )


@dataclass(frozen=True)
class ReviewDecision:
    """One honest review decision with who/what/when provenance.

    ``reviewer`` (and ``provenance["who"]``) is ``None`` whenever no human is
    recorded — an absent human stays absent, never a stand-in identity.
    """

    candidate_id: str
    state: str
    reason: str
    risk: str | None
    project_id: str | None
    request_id: str | None
    reviewer: str | None
    opened_at: float | None
    decided_at: str | None
    evidence_defects: tuple[str, ...]
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence_defects"] = list(self.evidence_defects)
        return payload


def evidence_defects(evidence: Any, required_keys: Sequence[str] = REQUIRED_EVIDENCE_KEYS) -> list[str]:
    """Real, per-key reasons the evidence is incomplete/unfit for approval.

    A payload's ``evidence_kind`` is validated only when claimed: declared
    ``simulated`` (can never gate, §5.6) and declared ``unverified``
    (disclosed, not a pass, §5.6) are defects, as is any label outside the
    ``EVIDENCE_KINDS`` whitelist.  Absent claims are not invented either way —
    the human reviewer judges bare payloads.
    """
    if not isinstance(evidence, Mapping):
        return [f"evidence must be a mapping of name -> payload, got {type(evidence).__name__}"]
    defects: list[str] = []
    for key in required_keys:
        if key not in evidence:
            defects.append(f"missing required evidence {key!r}")
            continue
        value = evidence[key]
        if value is None:
            defects.append(f"evidence {key!r} is null — no result recorded")
            continue
        if isinstance(value, str | list | dict) and not value:
            defects.append(f"evidence {key!r} is empty — nothing for a reviewer to inspect")
            continue
        if isinstance(value, Mapping):
            kind = value.get("evidence_kind")
            if kind is None:
                continue
            if kind not in EVIDENCE_KINDS:
                defects.append(f"evidence {key!r} carries evidence_kind {kind!r} outside the plan §5.6 whitelist {sorted(EVIDENCE_KINDS)}")
            elif kind == "simulated":
                defects.append(f"evidence {key!r} is evidence_kind='simulated' — simulated evidence can never gate (plan §5.6)")
            elif kind == "unverified":
                defects.append(f"evidence {key!r} is evidence_kind='unverified' — disclosed, not a pass (plan §5.6)")
    return defects


def review_record_path(candidate_id: str) -> Path:
    """Absolute path of the persisted review record (traversal-proof)."""
    _require_safe_id(candidate_id)
    return runtime_home() / "rsi" / "reviews" / f"{candidate_id}.json"


def _save_record(record: ReviewRecord) -> Path:
    """Atomically persist one review record; OSError propagates (real error, never a fake path)."""
    path = review_record_path(record.candidate_id)
    atomic_write_json(path, record.to_dict())
    return path


def load_review(candidate_id: str) -> ReviewRecord | None:
    """Load a persisted review record; ``None`` for missing/unsafe/corrupt.

    Corrupt storage degrades honestly (precedent: ``rsi/state.py``): it is
    never repaired or interpreted — the caller's decision then fails closed to
    ``pending``.
    """
    try:
        path = review_record_path(candidate_id)
    except ValueError:
        return None  # unsafe id: no lookup is possible, fail closed
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("RSI review record %s is unreadable (fail-closed): %s", path, exc)
        return None
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        logger.warning("RSI review record %s is corrupt; never repaired (fail-closed): %s", path, exc)
        return None
    try:
        return ReviewRecord.from_dict(payload)
    except ValueError as exc:
        logger.warning("RSI review record %s failed validation (fail-closed): %s", path, exc)
        return None


def open_review(
    candidate_id: str,
    evidence: Mapping[str, Any],
    *,
    risk: str,
    project_id: str = DEFAULT_PROJECT_ID,
    required_keys: Sequence[str] | None = None,
    clock: Callable[[], float] = time.time,
) -> ReviewRecord:
    """Open the human review channel for one candidate — never decide it.

    Returns the persisted ``ReviewRecord``.  The record stays ``pending``:

    - when the candidate has no lineage identity (failed lookup → fail-closed);
    - when the evidence is incomplete/declared-simulated/declared-unverified
      (refused with the real defect text — no approval request is enqueued);
    - after enqueueing: it awaits the human in the approval queue
      (``request_id`` set), which is the only path to ``approved``.

    Raises ``ValueError`` for entry defects (unsafe id, non-mapping evidence,
    unknown risk class, empty ``required_keys``, non-serializable or
    oversized evidence, a non-numeric clock) and ``ValueError`` when a prior
    review's request is still pending (one open review per candidate).
    """
    _require_safe_id(candidate_id)
    if not isinstance(evidence, Mapping):
        raise ValueError(f"evidence must be a mapping of name -> payload, got {type(evidence).__name__}")
    if risk not in RISK_LEVELS:
        raise ValueError(f"risk {risk!r} is not a known risk class; valid classes: {sorted(RISK_LEVELS)}")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError(f"project_id must be a non-empty string, got {project_id!r}")
    if isinstance(required_keys, str):  # a str is a Sequence[str] of chars — never a key list
        raise ValueError(f"required_keys must be a non-empty sequence of evidence names, got {required_keys!r}")
    keys = REQUIRED_EVIDENCE_KEYS if required_keys is None else tuple(required_keys)
    if not keys or not all(isinstance(key, str) and key for key in keys):
        raise ValueError(f"required_keys must be a non-empty sequence of evidence names, got {required_keys!r}")
    try:
        serialized = json.dumps(dict(evidence), ensure_ascii=False, sort_keys=True)
        evidence_payload: dict[str, Any] = json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"evidence for candidate {candidate_id!r} is not JSON-serializable: {exc}") from exc
    if len(serialized) > MAX_REVIEW_EVIDENCE_CHARS:
        raise ValueError(
            f"evidence for candidate {candidate_id!r} is {len(serialized)} chars, over the {MAX_REVIEW_EVIDENCE_CHARS}-char cap; refusing to record it"
        )
    opened_at = clock()
    if isinstance(opened_at, bool) or not isinstance(opened_at, int | float):
        raise ValueError(f"clock must return a Unix timestamp number, got {type(opened_at).__name__}")

    # One review at a time: a still-pending request blocks re-opening (it must
    # be resolved by a human first); a resolved/absent one allows a fresh review.
    existing = load_review(candidate_id)
    if existing is not None and existing.request_id is not None:
        existing_request = ApprovalQueue(existing.project_id).get_request(existing.request_id)
        if existing_request is not None and existing_request.status == "pending":
            raise ValueError(
                f"review already open for candidate {candidate_id!r}: approval request {existing.request_id} "
                f"is still pending in project {existing.project_id!r} (resolve it in the human channel before re-opening)"
            )

    record = ReviewRecord(
        candidate_id=candidate_id,
        risk=risk,
        project_id=project_id,
        required_keys=list(keys),
        evidence=evidence_payload,
        opened_at=float(opened_at),
    )

    lineage = RsiLineageStore().get(candidate_id)
    if lineage is None:
        # Failed lookup → pending/fail-closed: never auto-approved, never invented.
        record.reason = f"no review requested: candidate {candidate_id!r} has no RSI lineage record (lookup failed — fail-closed, never auto-approved)"
        _save_record(record)
        return record
    record.lineage_status = lineage.status
    record.lineage_payload_hash = lineage.payload_hash

    defects = evidence_defects(evidence_payload, keys)
    if defects:
        # Refused BEFORE the human channel is engaged: an approval ticket for
        # incomplete evidence must never exist in the first place.
        record.reason = "review refused: incomplete evidence — " + "; ".join(defects) + " (no approval request enqueued)"
        _save_record(record)
        return record

    request = ApprovalQueue(project_id).request_approval(
        bot_name=BOT_NAME,
        action_type=ACTION_TYPE,
        risk_level=RISK_LEVELS[risk],  # type: ignore[arg-type] — validated against RISK_LEVELS above
        details={
            "candidate_id": candidate_id,
            "risk": risk,
            "required_evidence_keys": list(keys),
            "lineage_status": lineage.status,
            "lineage_payload_hash": lineage.payload_hash,
        },
    )
    record.request_id = request.request_id
    record.reason = (
        f"awaiting human review: approval request {request.request_id} is pending in project {project_id!r}'s approval queue "
        "(this gate never resolves its own requests)"
    )
    _save_record(record)
    return record


def _decision(
    *,
    candidate_id: str,
    state: str,
    reason: str,
    risk: str | None,
    project_id: str | None,
    request_id: str | None,
    reviewer: str | None,
    opened_at: float | None,
    decided_at: str | None,
    defects: tuple[str, ...],
) -> ReviewDecision:
    """Assemble a decision with its who/what/when provenance (never invented)."""
    if state not in DECISION_STATES:
        raise ValueError(f"internal error: review decision state {state!r} is outside {DECISION_STATES}")
    what = f"rsi_review:{candidate_id}" if risk is None else f"rsi_review:{candidate_id}:risk={risk}"
    provenance: dict[str, Any] = {
        "who": reviewer,
        "what": what,
        "when_opened": opened_at,
        "when_decided": decided_at,
        "channel": None if project_id is None else f"alpha.projects.approval_queue:{project_id}",
    }
    return ReviewDecision(
        candidate_id=candidate_id,
        state=state,
        reason=reason,
        risk=risk,
        project_id=project_id,
        request_id=request_id,
        reviewer=reviewer,
        opened_at=opened_at,
        decided_at=decided_at,
        evidence_defects=defects,
        provenance=provenance,
    )


def review_decision(candidate_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> ReviewDecision:
    """Derive the honest decision for ``candidate_id`` — fail-closed on every gap.

    Reads the persisted record and the approval queue; it never writes a
    resolution.  ``approved`` requires ALL of: a record, a resolvable request
    on this review channel, queue status ``approved``, a non-blank verbatim
    reviewer identity, and complete non-simulated/non-unverified evidence —
    anything less lands ``pending`` (or ``rejected`` when a human rejected)
    with the real reason.
    """
    record = load_review(candidate_id)
    if record is None:
        return _decision(
            candidate_id=candidate_id,
            state="pending",
            reason=f"no review record for candidate {candidate_id!r} (lookup failed — fail-closed, never auto-approved)",
            risk=None,
            project_id=project_id,
            request_id=None,
            reviewer=None,
            opened_at=None,
            decided_at=None,
            defects=(),
        )
    defects = tuple(evidence_defects(record.evidence, record.required_keys))
    if record.request_id is None:
        return _decision(
            candidate_id=candidate_id,
            state="pending",
            reason=record.reason or "review was never requested (fail-closed, never auto-approved)",
            risk=record.risk,
            project_id=record.project_id,
            request_id=None,
            reviewer=None,
            opened_at=record.opened_at,
            decided_at=None,
            defects=defects,
        )
    request = ApprovalQueue(record.project_id).get_request(record.request_id)
    if request is None:
        return _decision(
            candidate_id=candidate_id,
            state="pending",
            reason=f"approval request {record.request_id!r} not found in project {record.project_id!r} approval queue (lookup failed — fail-closed, never auto-approved)",
            risk=record.risk,
            project_id=record.project_id,
            request_id=record.request_id,
            reviewer=None,
            opened_at=record.opened_at,
            decided_at=None,
            defects=defects,
        )
    details = request.details if isinstance(request.details, dict) else {}
    if request.action_type != ACTION_TYPE or details.get("candidate_id") != candidate_id:
        return _decision(
            candidate_id=candidate_id,
            state="pending",
            reason=(
                f"approval request {record.request_id!r} does not match this review channel "
                f"(action_type={request.action_type!r}, candidate={details.get('candidate_id')!r} — fail-closed, never auto-approved)"
            ),
            risk=record.risk,
            project_id=record.project_id,
            request_id=record.request_id,
            reviewer=None,
            opened_at=record.opened_at,
            decided_at=None,
            defects=defects,
        )
    if request.status == "pending":
        return _decision(
            candidate_id=candidate_id,
            state="pending",
            reason=f"awaiting human review: approval request {record.request_id} is pending — no human decision recorded yet",
            risk=record.risk,
            project_id=record.project_id,
            request_id=record.request_id,
            reviewer=None,
            opened_at=record.opened_at,
            decided_at=None,
            defects=defects,
        )
    reviewer = request.resolved_by if isinstance(request.resolved_by, str) and request.resolved_by.strip() else None
    if request.status == "approved":
        if reviewer is None:
            return _decision(
                candidate_id=candidate_id,
                state="pending",
                reason="approval record carries no reviewer identity (resolved_by is absent or blank) — an absent human stays absent (fail-closed, never auto-approved)",
                risk=record.risk,
                project_id=record.project_id,
                request_id=record.request_id,
                reviewer=None,
                opened_at=record.opened_at,
                decided_at=None,
                defects=defects,
            )
        if defects:
            return _decision(
                candidate_id=candidate_id,
                state="pending",
                reason="approval refused: incomplete evidence — " + "; ".join(defects),
                risk=record.risk,
                project_id=record.project_id,
                request_id=record.request_id,
                reviewer=reviewer,
                opened_at=record.opened_at,
                decided_at=request.resolved_at,
                defects=defects,
            )
        return _decision(
            candidate_id=candidate_id,
            state="approved",
            reason=f"human reviewer {reviewer!r} approved approval request {record.request_id}",
            risk=record.risk,
            project_id=record.project_id,
            request_id=record.request_id,
            reviewer=reviewer,
            opened_at=record.opened_at,
            decided_at=request.resolved_at,
            defects=defects,
        )
    if request.status == "rejected":
        comment = request.resolution_comment
        reason = comment if comment else f"human reviewer rejected approval request {record.request_id}"
        if reviewer is None:
            reason = f"{reason} (no reviewer identity recorded — reviewer unknown)"
        return _decision(
            candidate_id=candidate_id,
            state="rejected",
            reason=reason,
            risk=record.risk,
            project_id=record.project_id,
            request_id=record.request_id,
            reviewer=reviewer,
            opened_at=record.opened_at,
            decided_at=request.resolved_at,
            defects=defects,
        )
    if request.status == "timed_out":
        return _decision(
            candidate_id=candidate_id,
            state="pending",
            reason=f"approval request {record.request_id} timed out — no human decision recorded (fail-closed, still awaiting review)",
            risk=record.risk,
            project_id=record.project_id,
            request_id=record.request_id,
            reviewer=None,
            opened_at=record.opened_at,
            decided_at=None,
            defects=defects,
        )
    return _decision(
        candidate_id=candidate_id,
        state="pending",
        reason=f"approval request {record.request_id!r} has unrecognized status {request.status!r} (fail-closed, never auto-approved)",
        risk=record.risk,
        project_id=record.project_id,
        request_id=record.request_id,
        reviewer=None,
        opened_at=record.opened_at,
        decided_at=None,
        defects=defects,
    )


def human_reviewed(candidate_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> bool:
    """``True`` only when a human approved with complete evidence and identity.

    Callers may pass this as ``reviewed=True`` to the WP-B3 gate for
    ``review_required`` paths; it never overrides a ``deny`` (that gate fails
    closed regardless) and is never derived from candidate output.
    """
    return review_decision(candidate_id, project_id=project_id).state == "approved"
