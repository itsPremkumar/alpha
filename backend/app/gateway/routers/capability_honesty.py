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

## Cost, stated plainly

The wiring check is an AST walk over the harness and app packages, which is not
free. It is therefore computed **once per process** and cached, behind
``ALPHA_CAPABILITY_WIRING_CACHE=0`` to disable. An AST walk answers "is it
connected", never "is it correct", so a cached answer cannot go stale in a way
that misreports health: the cache is invalidated by a restart, and source edits
require one anyway to take effect.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from alpha.capabilities.honesty import WiringReport, audit_registered_claims

logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()
_CACHE: list[WiringReport] | None = None


def _cache_enabled() -> bool:
    """The AST walk is cached unless an operator explicitly disables it."""
    return os.getenv("ALPHA_CAPABILITY_WIRING_CACHE", "1").strip() not in {"0", "false", "False"}


def wiring_report(refresh: bool = False) -> list[WiringReport]:
    """Advertised-vs-wired verdicts, computed once per process by default."""
    global _CACHE
    if not refresh and _CACHE is not None and _cache_enabled():
        return _CACHE
    with _CACHE_LOCK:
        if _CACHE is not None and not refresh and _cache_enabled():
            return _CACHE
        try:
            reports = audit_registered_claims()
        except Exception:
            # A wiring audit that itself fails must not take down the endpoint
            # the frontend uses to bootstrap. Report the failure honestly rather
            # than claiming capabilities are fine.
            logger.warning("capability wiring audit failed", exc_info=True)
            return []
        if _cache_enabled():
            _CACHE = reports
        return reports


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
