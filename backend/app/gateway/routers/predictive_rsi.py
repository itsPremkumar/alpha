"""Read-only operator surface for Predictive RSI (``alpha.rsi.predictive``).

Answers the two questions an operator actually has about this subsystem — *is it
working, and can I trust it?* — without adding a single mutating verb:

* ``GET /api/rsi/predictive/status`` — the service posture: mode, predictor
  identity and tier, the ledger's standing, and whether this deployment's mode
  may influence a selection.
* ``GET /api/rsi/predictive/calibration`` — whether the forecasts have held up:
  Brier score, calibration error, coverage, high-confidence failures.

Three properties are load-bearing, each because a plausible alternative produces
a specific lie:

* **An unreadable ledger is ``503``, never an empty ``200``.** A corrupt ledger
  and a ledger with no records drive opposite operator decisions and only one of
  them is true, so the route carries the store's own reason. A **missing** file is
  genuinely empty — absence is not corruption — so the two stay distinguishable.
* **Uncalibrated is reported as ``null``, not as a good score.** ``brier`` is
  ``None`` until a forecast has been scored against a genuinely measured
  outcome, and the payload says so in ``detail``. A client that renders ``null``
  as ``0`` is misusing it, but the server must not hand it a flattering number.
* **Nothing here mutates anything.** The route is read-only by construction: it
  constructs a service in ``observe_only`` and reads. It cannot admit a
  candidate, gate a promotion, or roll anything back — ``promotion_authority``
  travels as ``false`` in every payload so the answer is checkable rather than
  assumed.

**Store I/O never runs on the event loop.** Both handlers resolve everything
through ``asyncio.to_thread``, because a blocking read on the request loop stalls
every other run in the process.

**Scope.** The forecast ledger is installation-scoped and describes the
deployment's own self-improvement telemetry, not one person's data, so the routes
require an authenticated caller (exactly as ``routers/capability_mesh.py`` does)
but are not owner-partitioned. The ledger is single-process JSONL: atomic against
a crash in one process, **never** cross-process exactly-once.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from alpha.rsi.predictive import (
    DETERMINISTIC_BACKEND,
    ForecastLedger,
    PredictiveMode,
    PredictiveRSIService,
)
from app.gateway.deps import get_current_user_from_request

router = APIRouter(prefix="/api/rsi/predictive", tags=["rsi"])

#: A store that cannot be read answers with this status. It is the repo-wide
#: degraded state for "could not look" (``/api/side-effects`` and
#: ``/api/capability-mesh`` refuse the same way) and is deliberately not a ``200``
#: with an empty body: an unreadable ledger must not render as an idle one.
_STORE_UNAVAILABLE = 503

_LEDGER_UNREADABLE = "The Predictive RSI forecast ledger could not be read, so its calibration is unknown rather than good. This is 'could not look', not 'nothing recorded'."


def _service() -> PredictiveRSIService:
    """A read-only service bound to the default ledger path.

    ``observe_only`` is the only posture used here. It is provably inert —
    ``influenced_selection`` is ``False`` in every shipped mode — so exposing
    these numbers cannot let a caller act on them.
    """
    return PredictiveRSIService(backend=DETERMINISTIC_BACKEND, mode=PredictiveMode.OBSERVE_ONLY, ledger=ForecastLedger())


async def _status() -> dict[str, Any]:
    service = _service()
    ledger = ForecastLedger()
    if not ledger.readable:
        raise HTTPException(status_code=_STORE_UNAVAILABLE, detail=_LEDGER_UNREADABLE)
    return await asyncio.to_thread(partial(service.status))


async def _calibration() -> dict[str, Any]:
    service = _service()
    ledger = ForecastLedger()
    if not ledger.readable:
        raise HTTPException(status_code=_STORE_UNAVAILABLE, detail=_LEDGER_UNREADABLE)
    report = await asyncio.to_thread(partial(service.calibration_report))
    payload = report.to_dict()
    # The single most important distinction on this surface, stated where a
    # client cannot miss it: null means "not measured yet", not "measured as
    # zero" and not "measured as perfect".
    payload["calibration_known"] = report.brier is not None
    payload["promotion_authority"] = False
    payload["note"] = "A forecast is not a measurement: this subsystem cannot admit a candidate, gate a promotion or roll anything back. brier=null means no forecast has yet been scored against a genuinely measured outcome."
    return payload


@router.get("/status")
async def get_status(_user: Any = Depends(get_current_user_from_request)) -> dict[str, Any]:
    """Service posture: mode, predictor identity and tier, ledger standing.

    ``influences_selection`` is ``false`` in every shipped mode and travels with
    the payload, so "this forecast cannot change anything" is checkable rather
    than something the operator has to take on trust.
    """
    return await _status()


@router.get("/calibration")
async def get_calibration(_user: Any = Depends(get_current_user_from_request)) -> dict[str, Any]:
    """Whether the forecasts have held up against real measured outcomes.

    Reports ``brier``, ``calibration_error``, ``coverage`` and
    ``high_confidence_failures``. Every aggregate is ``null`` — with
    ``calibration_known: false`` — until a forecast has been scored against a
    genuinely measured outcome. An uncalibrated predictor is reported
    uncalibrated, never as perfectly calibrated.
    """
    return await _calibration()
