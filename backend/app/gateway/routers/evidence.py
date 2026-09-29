"""Evidence + action-ledger REST surface (wave P6).

Exposes the two packages that P3 wired to real call sites, so the recorded
evidence and the action receipts are visible instead of only living on disk:

* ``GET /api/evidence/records``        — observation/artifact/benchmark/trace records
* ``GET /api/evidence/candidates``     — candidate -> evaluation -> promotion chains
* ``GET /api/action-ledger/intents``   — recorded action intents (pending/closed)
* ``GET /api/action-ledger/receipts``  — action receipts with their real outcomes

Honesty contract:

* ``owner_id`` is REQUIRED on every route: both stores are owner-scoped and the
  API never guesses a caller identity or merges owners.
* A corrupt/unreadable store fails closed with its real reason (file, schema or
  line detail from the store) — never an empty list that would read as "nothing
  was ever recorded".
* Empty IS reported as empty (with the owner echoed) when the store is
  genuinely readable and has no rows for that owner.
* **Every timestamp is ISO 8601 on the wire.** The stored models declare
  ``created_at`` / ``promoted_at`` / ``started_at`` / ``completed_at`` as
  ``time.time()`` floats, and these four routes used to serialise the dataclass
  ``__dict__`` verbatim, so a raw epoch went out next to every other Gateway
  route that speaks ISO. ``coerce_iso`` runs at this boundary only; the stores
  on disk keep the float and ``EvidenceRecord``/``ActionReceipt`` still load
  every existing record.

Wire-shape contract (why these are explicit projections, not ``__dict__``):

* ``record.__dict__`` published dataclass *internals* as the HTTP contract.
  ``EvidenceRecord.tags`` is normalised to a ``tuple`` by ``__post_init__``, so
  the tuple leaked out and JSON turned it into an array anyway; more
  importantly, any field added to the dataclass silently became a public API
  field, and ``__post_init__``'s sanitisation became the documented response.
  An explicit projection says exactly what is published and stops both.
* ``tags`` / ``evidence_ids`` are emitted as JSON arrays, which is what the
  tuple already serialised to, so this is not a wire break — it is the same
  shape stated instead of inherited.

Mount seam (applied by the lead in ``app.py``, same as the autonomy router):
``app.include_router(evidence.router)``. Auth is untouched: these routes sit
behind the gateway's default ``AuthMiddleware`` with no route-level decorators.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from alpha.utils.time import coerce_iso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evidence", "action-ledger"])


def _evidence_store():
    from alpha.evidence.store import default_evidence_store

    return default_evidence_store()


def _action_ledger():
    from alpha.ledger.store import default_action_ledger

    return default_action_ledger()


def _ts(value: object) -> str:
    """A stored epoch float as ISO 8601.

    Unlike the nullable task timestamps in the swarm plane, every field routed
    through here is a REQUIRED stamp: ``EvidenceRecord.created_at``,
    ``Promotion.promoted_at`` and ``ActionReceipt.started_at`` are validated by
    ``validate_timestamp`` (non-negative, finite) at construction and the store
    writes ``time.time()``. There is no "never ran" state on any of them, so
    there is no sentinel to translate — and inventing one would be worse than
    the honest reading of what was recorded.
    """
    return coerce_iso(value)


def _record_to_dict(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "owner_id": record.owner_id,
        "kind": record.kind,
        "ref": record.ref,
        "summary": record.summary,
        "created_at": _ts(record.created_at),
        "tags": list(record.tags),
    }


def _candidate_to_dict(candidate: Any) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "owner_id": candidate.owner_id,
        "title": candidate.title,
        "evidence_ids": list(candidate.evidence_ids),
        "status": candidate.status,
    }


def _evaluation_to_dict(evaluation: Any) -> dict[str, Any]:
    return {
        "id": evaluation.id,
        "candidate_id": evaluation.candidate_id,
        "score": evaluation.score,
        "verdict": evaluation.verdict,
        "rationale": evaluation.rationale,
        "evaluator": evaluation.evaluator,
        "created_at": _ts(evaluation.created_at),
    }


def _promotion_to_dict(promotion: Any) -> dict[str, Any]:
    return {
        "candidate_id": promotion.candidate_id,
        "evaluation_id": promotion.evaluation_id,
        "promoted_at": _ts(promotion.promoted_at),
    }


def _intent_to_dict(intent: Any) -> dict[str, Any]:
    return {
        "id": intent.id,
        "owner_id": intent.owner_id,
        "tool_name": intent.tool_name,
        # Stored and published as a digest; the raw arguments never leave the
        # ledger, and this projection cannot widen that.
        "arguments_digest": intent.arguments_digest,
        "created_at": _ts(intent.created_at),
        "thread_id": intent.thread_id,
        "status": intent.status,
    }


def _receipt_to_dict(receipt: Any) -> dict[str, Any]:
    return {
        "intent_id": receipt.intent_id,
        "outcome": receipt.outcome,
        "started_at": _ts(receipt.started_at),
        "completed_at": _ts(receipt.completed_at),
        "error_category": receipt.error_category,
        "exit_ref": receipt.exit_ref,
    }


@router.get(
    "/api/evidence/records",
    summary="Evidence records for an owner",
    description=("Real evidence records (observation / artifact / benchmark / trace) recorded by the runtime for one owner. Fail-closed: an unreadable store answers 500 with the store's real reason instead of an empty list."),
)
async def list_evidence_records(
    owner_id: str = Query(..., min_length=1, description="Owner the records belong to (required — owners are never merged)"),
    kind: str | None = Query(None, description="Optional exact kind filter: artifact | benchmark | trace | observation"),
) -> dict:
    try:
        records = _evidence_store().list_evidence(owner_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    if kind is not None:
        records = [record for record in records if record.kind == kind]
    return {
        "owner_id": owner_id,
        "kind_filter": kind,
        "count": len(records),
        "records": [_record_to_dict(record) for record in records],
        "note": "records are exactly what the runtime recorded; nothing is synthesised or back-filled here",
    }


@router.get(
    "/api/evidence/candidates",
    summary="Evidence-backed candidates for an owner",
    description="Candidate → evaluation → promotion chains that cite recorded evidence ids.",
)
async def list_evidence_candidates(
    owner_id: str = Query(..., min_length=1, description="Owner the candidates belong to"),
) -> dict:
    try:
        store = _evidence_store()
        candidates = store.list_candidates(owner_id)
        evaluations = store.list_evaluations(owner_id)
        promotions = store.list_promotions(owner_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {
        "owner_id": owner_id,
        "candidates": [_candidate_to_dict(candidate) for candidate in candidates],
        "evaluations": [_evaluation_to_dict(evaluation) for evaluation in evaluations],
        "promotions": [_promotion_to_dict(promotion) for promotion in promotions],
        "counts": {"candidates": len(candidates), "evaluations": len(evaluations), "promotions": len(promotions)},
    }


@router.get(
    "/api/action-ledger/intents",
    summary="Action intents for an owner",
    description="Every recorded action intent (tool name, argument digest, thread, status) newest first.",
)
async def list_action_intents(
    owner_id: str = Query(..., min_length=1, description="Owner the intents belong to"),
    tool_name: str | None = Query(None, description="Optional exact tool-name filter"),
    thread_id: str | None = Query(None, description="Optional exact thread-id filter"),
    status: str | None = Query(None, description="Optional status filter (pending | succeeded | failed | denied)"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    try:
        intents = _action_ledger().list_intents(
            owner_id,
            tool_name=tool_name,
            thread_id=thread_id,
            status=status,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {
        "owner_id": owner_id,
        "count": len(intents),
        "intents": [_intent_to_dict(intent) for intent in intents],
    }


@router.get(
    "/api/action-ledger/receipts",
    summary="Action receipts for an owner",
    description=("Recorded action receipts with their real outcome, measured start/completion times, error category and exit reference. An unreadable ledger fails closed with its real reason."),
)
async def list_action_receipts(
    owner_id: str = Query(..., min_length=1, description="Owner the receipts belong to"),
    tool_name: str | None = Query(None, description="Optional exact tool-name filter"),
    thread_id: str | None = Query(None, description="Optional exact thread-id filter"),
    outcome: str | None = Query(None, description="Optional outcome filter: succeeded | failed | denied"),
    intent_id: str | None = Query(None, description="Optional exact intent id"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    try:
        receipts = _action_ledger().list_receipts(
            owner_id,
            intent_id=intent_id,
            outcome=outcome,
            tool_name=tool_name,
            thread_id=thread_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {
        "owner_id": owner_id,
        "count": len(receipts),
        "receipts": [_receipt_to_dict(receipt) for receipt in receipts],
    }
