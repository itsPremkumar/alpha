"""Surface the advertised-vs-wired verdict on ``GET /api/features``.

The OpenHuman-inspired roadmap's §36 rule, and its invariant 10, are the same
statement: *no feature is shown as operational without a backend capability
check.* This router is the surface the frontend reads to decide what to render,
so it is where an unwired capability would otherwise become a visible claim.

## What this adds, and what it deliberately does not

Each existing flag answers "is this configured and started?". That is a
**config** question, and it is answered correctly. What it cannot answer is the
question every honesty bug in this repository actually turned on: *is anything
outside the defining module calling it?* A stub with good tests and a valid
config flag answers "yes, available" on every existing check.

`advertised_capabilities` therefore reports the **wiring** verdict separately
from the flag, and the two are never merged into one boolean. A capability can be
enabled and unwired, which is precisely the state that reads as working.

## This must never block the event loop

This is the single most important property of this module, and it was the cause
of Alpha appearing to "crash continuously".

The wiring check is an AST walk over the harness and app packages. It is
measured at **358 s** on a full checkout before the shared-source-index fix, and
still tens of seconds after it. This router originally ran it *inline* the first
time ``GET /api/features`` was called, from inside an ``async def`` route - which
means it ran on the Gateway's asyncio event loop thread. The consequence was not
a slow endpoint; it was a total outage:

1. the frontend calls ``/api/features`` during bootstrap;
2. the event loop stops for the whole audit, so **every** other request -
   ``/health``, ``/health/ready``, all of ``/api/*`` - times out;
3. the launcher's 240 s readiness budget expires and it restarts the gateway;
4. the watchdog sees a gateway that never answers and escalates to a full stack
   restart;
5. the audit needs 358 s, so it is always killed before it can fill the
   per-process cache, and the next boot starts it again.

Steps 1-5 repeat forever. That is the restart loop, and it is why no crash
reason ever appeared in ``logs/gateway.err.log``: the process was never crashing,
it was busy.

So the contract here is:

* :func:`wiring_report` **returns what is cached and never computes**;
* :func:`start_wiring_audit` computes in one daemon thread, single-flight;
* the Gateway lifespan calls it at startup, so the work starts before the first
  request rather than because of it;
* an empty list is served until the first audit lands, which is honest - a
  capability that has not been verified is not advertised.

## Cost, stated plainly

An AST walk answers "is it connected", never "is it correct", so a cached answer
cannot go stale in a way that misreports health. The cache is process-local and
the audit is re-run on demand; source edits need a Gateway restart to be observed
either way, because the process is walking the tree it started with.

``ALPHA_CAPABILITY_WIRING_CACHE=0`` disables caching entirely (the audit then
runs in the background for every request, which is only sensible in a test).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from alpha.capabilities.honesty import WiringReport, audit_registered_claims

logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()
_CACHE: list[WiringReport] | None = None
#: "idle" -> "running" -> ("ready" | "failed")
_STATE: str = "idle"
_AUDIT_THREAD: threading.Thread | None = None
#: ``time.monotonic()`` of the last audit failure, so a broken tree cannot be
#: re-walked on every request (the old failure path did exactly that: it
#: returned ``[]`` *without* caching, so a single exception meant the full
#: multi-second walk ran again on every ``/api/features`` call).
_LAST_FAILURE_AT: float = 0.0
FAILURE_RETRY_SECONDS = 300.0


def _cache_enabled() -> bool:
    """The AST walk is cached unless an operator explicitly disables it."""
    return os.getenv("ALPHA_CAPABILITY_WIRING_CACHE", "1").strip() not in {"0", "false", "False"}


def _run_audit() -> None:
    """Worker-thread body: compute the reports and publish them."""
    global _CACHE, _STATE, _LAST_FAILURE_AT
    try:
        reports = audit_registered_claims()
    except Exception:
        # A wiring audit that itself fails must not take down the endpoint
        # the frontend uses to bootstrap. Report the failure honestly rather
        # than claiming capabilities are fine - and record the failure so the
        # retry is bounded instead of happening on every request.
        logger.warning("capability wiring audit failed", exc_info=True)
        with _CACHE_LOCK:
            _STATE = "failed"
            _LAST_FAILURE_AT = time.monotonic()
        return
    with _CACHE_LOCK:
        _CACHE = reports
        _STATE = "ready"
        _LAST_FAILURE_AT = 0.0
    logger.info("capability wiring audit complete: %d claim(s)", len(reports))


def start_wiring_audit() -> bool:
    """Kick the audit into a daemon thread if it is not already running.

    Single-flight: concurrent callers (a request racing the lifespan) start at
    most one worker, because the walk reads ~23 MB of source and two concurrent
    walks would double the CPU cost for no benefit.

    Returns ``True`` when a worker was started.
    """
    global _STATE, _AUDIT_THREAD
    if not _cache_enabled():
        return False
    with _CACHE_LOCK:
        if _STATE in {"running", "ready"}:
            return False
        if _STATE == "failed" and (time.monotonic() - _LAST_FAILURE_AT) < FAILURE_RETRY_SECONDS:
            return False
        _STATE = "running"
        thread = threading.Thread(target=_run_audit, name="capability-wiring-audit", daemon=True)
        _AUDIT_THREAD = thread
    thread.start()
    return True


def wiring_report(refresh: bool = False) -> list[WiringReport]:
    """Advertised-vs-wired verdicts, **never computed on the calling thread**.

    Returns the cached report, or ``[]`` while the first audit is still running
    (or after one failed). The empty list is the honest answer: nothing has been
    verified yet, so nothing is claimed.

    ``refresh=True`` discards the cache and requests a background recompute; it
    still returns immediately with whatever is already known.
    """
    global _CACHE, _STATE
    if refresh:
        with _CACHE_LOCK:
            _CACHE = None
            _STATE = "idle"
        start_wiring_audit()
    with _CACHE_LOCK:
        if _CACHE is not None:
            return _CACHE
    # Nothing cached yet: make sure a computation is under way, but do not wait
    # for it. This is the whole point of the module - a caller inside a request
    # handler must never be the reason the event loop stops.
    start_wiring_audit()
    return []


def audit_state() -> str:
    """``idle`` / ``running`` / ``ready`` / ``failed`` - for diagnostics."""
    with _CACHE_LOCK:
        return _STATE


def advertised_capabilities(refresh: bool = False) -> list[dict[str, Any]]:
    """The wiring verdict as a frontend-safe list.

    Deliberately not merged into any existing ``enabled`` flag. A consumer needs
    to be able to see "configured and started" and "wired" as two facts, because
    the combination that matters - enabled *and* unwired - is the one that
    looks like a working feature.
    """
    payload: list[dict[str, Any]] = []
    for report in wiring_report(refresh=refresh):
        payload.append(
            {
                "capability_id": report.capability_id,
                "wiring": report.state.value,
                "consumers": report.consumers,
                "detail": report.detail,
                # The reason is documentation, not a per-request cost; it is
                # included because an operator reading an unwired capability
                # needs to know which invariant broke, and re-deriving that from
                # the capability id is not possible.
                "reason": report.reason,
            }
        )
    return payload


def reset_for_tests() -> None:
    """Drop the process-local cache so a test starts from a known state."""
    global _CACHE, _STATE, _LAST_FAILURE_AT, _AUDIT_THREAD
    with _CACHE_LOCK:
        _CACHE = None
        _STATE = "idle"
        _LAST_FAILURE_AT = 0.0
        _AUDIT_THREAD = None
