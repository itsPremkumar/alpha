"""The error reporter's SSE leg: a coded report reaches the run that caused it.

`alpha.errors.report_error` fans one failure out to four places — log, SSE,
metric, recovery. Three of them are host-agnostic and fire from any process.
The **SSE** leg is not: publishing a frame means addressing one run's stream,
and only the Gateway owns the ``StreamBridge`` that holds those streams. The
harness therefore never formats the frame itself (`alpha.errors` hands the sink
a ready payload) and deliberately never imports ``app.gateway``; binding the
leg is the host's job. Until a host did that, the leg was unbound: every
report still reached the log, the metric and the recovery ledger, but the one
place a *watching client* looks for a failure never received one, and
``ErrorReporter.unbound_sse`` counted it as a process-global nobody read.

What this module owns
---------------------
One :func:`bind_error_reporter_sse_leg` call from the Gateway lifespan, which:

- publishes the report as an ``error`` frame on the run named by
  ``context.run_id`` (the field the run journal already stamps, and the only
  run identity a generic report can carry);
- renders it in the **run-stream error frame shape** clients already parse
  (``code`` + ``correlation_id`` at the top level), because the reporter's own
  payload nests both under ``error_*`` keys;
- counts every report it could not address on ``unbound_sse``;
- and treats an unbound payload as a *measurable* gap, never as a failure:
  a report with no run id has no stream to go to, and the log, metric and
  recovery legs still recorded it.

The honesty limit worth stating: this publishes to the stream that exists
**while a client is watching**. A failure reported for a run whose stream was
already cleaned up finds a full-or-nothing bridge (``publish`` is a no-op off
an empty subscriber set for the memory bridge; Redis behaves the same way) so
the frame is dropped and counted rather than buffered forever. That is the
same retention window every other stream consumer lives inside, and the run's
``run.error`` event plus this counter are the durable record.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from typing import Any

from fastapi import FastAPI

from alpha.errors import configure_error_reporter
from alpha.ops.metrics import get_metrics_registry
from alpha.runtime import StreamBridge

logger = logging.getLogger(__name__)

#: Counter for reports that named no run, so there was no stream to publish to.
UNBOUND_SSE_METRIC = "alpha_errors_sse_unbound_total"

#: The event name clients already listen for. ``alpha.errors`` names the same
#: one; keeping the literal here avoids importing the harness constant into a
#: value that is asserted on both sides.
ERROR_EVENT = "error"


def run_error_frame(payload: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Render one reported error as the run-stream ``error`` frame body.

    The reporter payload keys the code ``error_code`` and the family key
    ``error_correlation_id``; the SSE consumer (and every client written
    against it) reads ``code`` and ``correlation_id``. Publishing the payload
    verbatim would therefore deliver a frame whose identity fields nobody
    parses — the exact failure mode this leg was unbound through, one level
    down.

    Both client-visible keys are rendered from the **registry** definitions, so
    they are server-owned text, never tool output or provider prose. The
    reporter's own ``error_detail`` (the exception string) stays out: it is an
    operator field that reaches the log and the recovery ledger, and a client
    frame is not that surface.
    """
    context = payload.get("context") if isinstance(payload, dict) else None
    context_run_id = context.get("run_id") if isinstance(context, dict) else None
    frame: dict[str, Any] = {
        "type": ERROR_EVENT,
        "code": str(payload.get("error_code") or "UNKNOWN_ERROR"),
        "message": str(payload.get("error_message") or ""),
        "correlation_id": str(payload.get("error_correlation_id") or ""),
        "run_id": run_id,
        "source": "error_reporter",
    }
    # Carry the policy forward verbatim for operator surfaces (console, curl,
    # scripts) and keep the caller's own run id when it agrees with the
    # addressed one. A mismatch would mean the frame is filed against a run it
    # did not come from, so the addressed id wins and the disagreement is
    # dropped rather than shipped.
    for key in ("severity", "retryable", "recovery", "http_status", "trace_id"):
        value = payload.get(key)
        if value is not None:
            frame[key] = value
    if isinstance(context_run_id, str) and context_run_id:
        frame["reported_run_id"] = context_run_id
    return frame


def bind_error_reporter_sse_leg(app: FastAPI, bridge: StreamBridge, *, loop: asyncio.AbstractEventLoop) -> None:
    """Publish every reported run error onto that run's stream.

    Called from the lifespan once the bridge exists. The reporter is process
    global and ``configure_error_reporter`` rebinds rather than accumulating, so
    a subsystem restart re-binds instead of doubling every report's audience.
    """
    metrics = get_metrics_registry()
    unbound = metrics.counter(
        UNBOUND_SSE_METRIC,
        help="Error reports with no run id, so no SSE frame could be published to a run stream",
    )
    stats: dict[str, int] = {"published": 0, "unbound": 0, "loop_closed": 0}

    def _record_publish_failure(future: Future[Any]) -> None:
        exc = future.exception(timeout=0)
        if exc is None:
            return
        logger.warning("Failed to publish an error frame to a run stream; the report is still logged and counted", exc_info=exc)

    def sse_sink(payload: dict[str, Any]) -> None:
        context = payload.get("context") if isinstance(payload, dict) else None
        run_id = context.get("run_id") if isinstance(context, dict) else None
        if not isinstance(run_id, str) or not run_id.strip():
            # No run to address: this is not a lost report, it is a report with
            # no stream. Counted so an operator can tell "no failures" from
            # "failures that never named a run".
            stats["unbound"] += 1
            unbound.inc()
            logger.debug("Error report carried no run id; no SSE frame could be addressed")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(bridge.publish(run_id, ERROR_EVENT, run_error_frame(payload, run_id)), loop)
        except RuntimeError:
            # The serving loop is closed (shutdown). The other three legs
            # already recorded the report; counting keeps the gap visible.
            stats["loop_closed"] += 1
            unbound.inc()
            logger.debug("Error report arrived after the serving loop closed; no SSE frame was published")
            return
        future.add_done_callback(_record_publish_failure)
        stats["published"] += 1

    configure_error_reporter(sse_sink=sse_sink)
    # One live counter dict for an operator surface to read. The unbound total
    # is also a declared metric, and ``ErrorReporter.unbound_sse`` remains the
    # process-level source for "no sink at all"; this dict adds the per-Gateway
    # breakdown that neither of those can answer.
    app.state.error_reporter_sse = stats
    logger.info("Error reporter SSE leg bound to the run stream bridge")
