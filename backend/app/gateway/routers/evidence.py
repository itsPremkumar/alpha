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

Mount seam (applied by the lead in ``app.py``, same as the autonomy router):
``app.include_router(evidence.router)``. Auth is untouched: these routes sit
behind the gateway's default ``AuthMiddleware`` with no route-level decorators.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evidence", "action-ledger"])


def _evidence_store():
    from alpha.evidence.store import default_evidence_store

    return default_evidence_store()


def _action_ledger():
    from alpha.ledger.store import default_action_ledger

    return default_action_ledger()


@router.get(
    "/api/evidence/records",
    summary="Evidence records for an owner",
    description=(
        "Real evidence records (observation / artifact / benchmark / trace) recorded by the "
        "runtime for one owner. Fail-closed: an unreadable store answers 500 with the store's "
        "real reason instead of an empty list."
    ),
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
        "records": [record.__dict__ if hasattr(record, "__dict__") else record for record in records],
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
        "candidates": [getattr(candidate, "__dict__", candidate) for candidate in candidates],
        "evaluations": [getattr(evaluation, "__dict__", evaluation) for evaluation in evaluations],
        "promotions": [getattr(promotion, "__dict__", promotion) for promotion in promotions],
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
        "intents": [getattr(intent, "__dict__", intent) for intent in intents],
    }


@router.get(
    "/api/action-ledger/receipts",
    summary="Action receipts for an owner",
    description=(
        "Recorded action receipts with their real outcome, measured start/completion times, "
        "error category and exit reference. An unreadable ledger fails closed with its real reason."
    ),
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
        "receipts": [getattr(receipt, "__dict__", receipt) for receipt in receipts],
    }
