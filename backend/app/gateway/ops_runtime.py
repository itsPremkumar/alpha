"""Operator-visible durable-runtime facts: the last drain, and live connectivity.

Why this module exists
----------------------
Three things the durable-runtime layer *measures* were, at the time this was
written, computed and then thrown away:

* ``alpha.runtime.shutdown`` runs an eight-phase ``PlannedShutdown`` drain in
  the Gateway lifespan and produces an honest :class:`ShutdownReport` carrying
  ``is_clean``. The only consumer was a ``logger.warning`` at the very end of
  the teardown, so the one fact an operator debugging a stuck restart needs
  existed for less than a second and then only inside a logfile.
* ``alpha.runtime.network`` publishes a four-state connectivity reading
  (``ONLINE`` / ``DEGRADED`` / ``UNKNOWN`` / ``OFFLINE``) from a poll loop that
  runs for the life of the process. The monitor was stored on
  ``app.state.network_monitor`` and never read by a single production caller.
* ``NetworkWaitService.status()`` -- whose own docstring says "for an ops
  endpoint" -- had no production caller, so the durable parked-session registry
  (migration ``0026_network_waits``) was invisible.

This module is the seam that makes all three observable. It is deliberately
**additive**: it reads state other owners already hold and writes one
bookkeeping file. It is not a second lifecycle owner, it never cancels or
resumes a run, and ``SafeRunRecoveryService`` remains the only
safe-continuation authority.

Two rules that are the whole point of the file
----------------------------------------------

**1. Recording the drain must never fail the drain.** This is the same
fail-OPEN requirement the side-effect ledger documents: *a bookkeeping write
failure must not fail the user's action.* Here the "action" is the shutdown
itself, and the failure mode is concrete -- a read-only or full ``ALPHA_HOME``,
a Windows file lock, a disk error -- and it would otherwise be raised from a
teardown path, turning "I could not record that shutdown was incomplete" into
"the shutdown did not finish". So :func:`record_drain_report` returns a status
and never raises. The drain's own outcome is already in the report; losing the
record must not change it.

**2. Absence is never a value.** A missing monitor, a disabled network
config, an unreadable record, and a genuine ``OFFLINE`` reading are four
different claims. The payload distinguishes all four with an explicit
``reported`` flag plus a machine-readable ``reason``, so a reader cannot render
"we are not measuring connectivity" as "connectivity is fine". ``UNKNOWN`` is
reported verbatim and is never rounded to ``OFFLINE``, matching
``runtime/network/AGENTS.md`` rule 1.

Durability boundary
-------------------
``last_drain.json`` is a **single-process, atomic-replace** file under
``ALPHA_HOME``. It is restart-recoverable for one Gateway. It is not a shared
multi-worker ledger and makes no cross-process exactly-once claim: two Gateway
instances writing the same ``ALPHA_HOME`` would overwrite each other's record,
which is why the payload is treated as an operator hint about *this* installation
rather than a coordination primitive.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.utils.time import now_iso

logger = logging.getLogger(__name__)

__all__ = [
    "DRAIN_RECORD_FILENAME",
    "drain_record_path",
    "network_snapshot",
    "read_last_drain",
    "record_drain_report",
]

#: Relative to ``runtime_home()``. Versioned in the payload, not the filename,
#: so a future shape change is detectable rather than silently misread.
DRAIN_RECORD_SUBDIR = "runtime"
DRAIN_RECORD_FILENAME = "last_drain.json"
DRAIN_RECORD_VERSION = 1

# Reasons are a closed vocabulary, not free text, so a consumer can branch on
# them and a new reason is a visible change rather than a new string to guess at.
REASON_NO_RECORD = "no_drain_has_been_recorded_yet"
REASON_UNREADABLE = "the recorded drain could not be read"
REASON_MONITOR_DISABLED = "network monitoring is disabled by configuration"
REASON_MONITOR_ABSENT = "the network monitor is not running in this process"
REASON_RECORD_FAILED = "the drain report could not be recorded"


def drain_record_path(home: Path | None = None) -> Path:
    """Absolute path of the drain record. Injectable ``home`` for tests."""
    root = home if home is not None else runtime_home()
    return root / DRAIN_RECORD_SUBDIR / DRAIN_RECORD_FILENAME


def record_drain_report(report: Any, *, home: Path | None = None, recorded_at: str | None = None) -> dict[str, Any]:
    """Persist a :class:`~alpha.runtime.shutdown.ShutdownReport` for the next boot.

    **Never raises.** A failure here is logged and returned as
    ``{"recorded": False, "reason": ...}``; see rule 1 in the module docstring.
    The caller is a teardown path, and a bookkeeping failure must not become a
    shutdown failure.

    ``report`` is duck-typed on purpose: it is whatever ``drain.shutdown()``
    returned, and depending on the concrete class here would couple the Gateway
    to a harness dataclass for no behavioural gain. A malformed report is
    recorded as a failure rather than raising, for the same reason.
    """
    payload = {
        "version": DRAIN_RECORD_VERSION,
        "recorded_at": recorded_at or now_iso(),
        "is_clean": None,
        "emergency": None,
        "total_seconds": None,
        "steps": [],
        "incomplete": [],
    }
    try:
        payload["is_clean"] = bool(report.is_clean)
        payload["emergency"] = bool(report.emergency)
        payload["total_seconds"] = round(float(report.total_seconds), 3)
        payload["steps"] = [{"phase": phase.value, "status": status.value, "detail": detail} for phase, status, detail in report.steps]
        payload["incomplete"] = [phase.value for phase in report.incomplete()]
    except AttributeError as exc:
        # A report shape we do not understand is a recording failure, not a
        # reason to fail the shutdown that produced it.
        logger.warning("drain report could not be serialised (%s); recording the fact without it", exc)
        return {"recorded": False, "reason": f"unserialisable drain report: {type(exc).__name__}"}

    path = drain_record_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace: a crash mid-write must leave the *previous* record
        # intact rather than a truncated file that reads as "unreadable" and
        # silently hides the one prior incomplete drain.
        handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    except OSError as exc:
        logger.warning("drain report could not be written to %s: %s", path, exc)
        return {"recorded": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"recorded": True, "reason": "", "path": str(path)}


def read_last_drain(*, home: Path | None = None) -> dict[str, Any]:
    """Read the last recorded drain, distinguishing "none" from "unreadable".

    Never raises. The three outcomes are separate claims and are returned as
    such:

    * a valid record -> ``{"reported": True, ...}``
    * no record yet   -> ``{"reported": False, "reason": REASON_NO_RECORD, "is_clean": None}``
    * a broken record -> ``{"reported": False, "reason": REASON_UNREADABLE, "is_clean": None}``

    A record that parses but is not a known shape is *also* ``unreadable``
    rather than being partially believed, because half a drain report is not a
    measurement of a drain.
    """
    path = drain_record_path(home)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"reported": False, "reason": REASON_NO_RECORD, "is_clean": None}
    except OSError as exc:
        logger.warning("drain record %s could not be read: %s", path, exc)
        return {"reported": False, "reason": REASON_UNREADABLE, "detail": f"{type(exc).__name__}: {exc}", "is_clean": None}

    try:
        data = json.loads(raw)
    except ValueError as exc:
        logger.warning("drain record %s is not valid JSON: %s", path, exc)
        return {"reported": False, "reason": REASON_UNREADABLE, "detail": "the record is not valid JSON", "is_clean": None}

    if not isinstance(data, dict) or data.get("version") != DRAIN_RECORD_VERSION or not isinstance(data.get("is_clean"), bool):
        return {"reported": False, "reason": REASON_UNREADABLE, "detail": "the record is not a recognised drain report", "is_clean": None}
    return {
        "reported": True,
        "reason": "",
        "is_clean": data["is_clean"],
        "emergency": data.get("emergency"),
        "total_seconds": data.get("total_seconds"),
        "steps": data.get("steps") or [],
        "incomplete": data.get("incomplete") or [],
        "recorded_at": data.get("recorded_at"),
    }


def _finite_float(value: Any) -> float | None:
    """Return a finite float, or ``None``.

    ``None`` is the whole point of this helper. An unreachable endpoint has no
    round-trip time to report, and rounding that to ``0`` would render the worst
    possible result as the best possible one.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _project_targets(observation: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Per-endpoint reachability and measured latency, from the last observation.

    ``latency_ms`` is reported for unreachable targets too, and deliberately so:
    a fast refusal and a black-holed route are different faults, and an operator
    debugging "the internet is down" needs to tell them apart. The aggregate
    :func:`_mean_latency_ms` is the value that only ever uses reachable samples.
    """
    outcomes = (observation or {}).get("outcomes")
    if not isinstance(outcomes, list):
        return []
    targets: list[dict[str, Any]] = []
    for entry in outcomes:
        if not isinstance(entry, dict):
            continue
        target = {
            "name": str(entry.get("target") or "?"),
            "reachable": bool(entry.get("reachable")),
            "latency_ms": _finite_float(entry.get("elapsed_ms")),
            "failure_kind": str(entry.get("failure_kind") or ""),
            "detail": str(entry.get("detail") or ""),
        }
        targets.append(target)
    return targets


def _mean_latency_ms(targets: list[dict[str, Any]]) -> float | None:
    """Mean round-trip over the *reachable* targets, or ``None``.

    Reachable only. Averaging a reachable endpoint together with a timed-out one
    would produce a number describing neither, and reporting any latency while
    nothing was reachable at all would be the most misleading value available.
    """
    samples = [t["latency_ms"] for t in targets if t["reachable"] and t["latency_ms"] is not None]
    if not samples:
        return None
    return round(sum(samples) / len(samples), 1)


def _project_retry(monitor: Any, state: str | None, running: bool) -> dict[str, Any]:
    """The automatic-retry schedule, as the process itself decided it.

    ``retrying`` answers the one operator question this projection exists for:
    *is the backend still working on getting the link back?* It is true only
    while the loop is running **and** the link is not fully up, so a healthy host
    never claims to be retrying and a stopped loop never claims a retry that will
    not happen. ``next_probe_seconds`` is the monitor's own drawn and jittered
    delay, not an un-jittered ideal nobody sleeps on.
    """
    decision = None
    config = None
    try:
        decision = monitor.wait_decision()
    except Exception:  # noqa: BLE001 - an ops read must not raise into the request
        logger.warning("network monitor wait_decision() failed", exc_info=True)
    try:
        config = monitor.config
    except Exception:  # noqa: BLE001
        logger.warning("network monitor config could not be read", exc_info=True)

    next_probe = None
    if decision is not None:
        drawn = _finite_float(getattr(decision, "next_poll_seconds", None))
        next_probe = round(drawn, 3) if drawn is not None else None

    return {
        "automatic": bool(running),
        "retrying": bool(running and state is not None and state != "online"),
        "next_probe_seconds": next_probe,
        "poll_interval_seconds": _finite_float(getattr(config, "poll_interval_seconds", None)),
        "backoff_max_seconds": _finite_float(getattr(config, "backoff_max_seconds", None)),
    }


def _unreported(reason: str, *, wait_service: Any = None) -> dict[str, Any]:
    """The single shape for "connectivity is not being measured".

    Every disabled-monitor and absent-monitor case returns this with a different
    ``reason``. The key set matches the reported shape on purpose: a consumer must
    never have to branch on which keys exist to learn that nothing was measured.
    """
    return {
        "reported": False,
        "reason": reason,
        "detail": "",
        "state": None,
        "state_detail": "",
        "allows_network_attempt": None,
        "latency_ms": None,
        "monitoring": False,
        "observed_age_seconds": None,
        "last_observation": None,
        "targets": [],
        "retry": {
            "automatic": False,
            "retrying": False,
            "next_probe_seconds": None,
            "poll_interval_seconds": None,
            "backoff_max_seconds": None,
        },
        "parked_sessions": None,
        "parked_durability": "installed" if wait_service is not None else "unavailable",
    }


def network_snapshot(monitor: Any, wait_service: Any, *, network_enabled: bool | None = None) -> dict[str, Any]:
    """Project the live connectivity fact, with absence kept distinct from a value.

    ``monitor`` and ``wait_service`` are the objects the Gateway lifespan put on
    ``app.state``; either may be ``None``. Both are duck-typed so this stays a
    pure projection with no import-time dependency on the concrete classes, and
    so a test can pass a small stand-in.

    The parked-session read is *not* performed here: ``NetworkWaitService.status()``
    is async and touches a SQL store, so awaiting it belongs in the route, where
    a store outage can be caught and reported instead of turning this projection
    into a database call.
    """
    if network_enabled is None:
        network_enabled = monitor is not None

    if not network_enabled:
        return _unreported(REASON_MONITOR_DISABLED, wait_service=wait_service)
    if monitor is None:
        # Enabled but absent is a real deployment shape: the monitor failed to
        # start, or the process is shutting down. Saying "unknown" here would be
        # a claim about the network, which is exactly what we do not know.
        return _unreported(REASON_MONITOR_ABSENT, wait_service=wait_service)

    observation: dict[str, Any] | None = None
    try:
        last = monitor.last_observation()
    except Exception:  # noqa: BLE001 - an ops read must not raise into the request
        last = None
        logger.warning("network monitor last_observation() failed", exc_info=True)
    if last is not None:
        try:
            observation = last.to_dict()
        except Exception:  # noqa: BLE001
            logger.warning("network observation could not be serialised", exc_info=True)
            observation = None

    state: str | None = None
    state_detail = ""
    try:
        state_value = monitor.state
        state = state_value.value
        state_detail = str(getattr(state_value, "detail", "") or "")
    except Exception:  # noqa: BLE001
        logger.warning("network monitor state could not be read", exc_info=True)

    try:
        running = bool(getattr(monitor, "running", False))
    except Exception:  # noqa: BLE001
        running = False

    targets = _project_targets(observation)

    # ``allows_network_attempt`` is read from the monitor's own decision rather
    # than re-derived from the state string here, because the rule that matters
    # is not "is it online": ``UNKNOWN`` still permits an attempt, and a Gateway
    # that copied the vocabulary into this module would eventually disagree with
    # the harness about exactly that case.
    allows_attempt: bool | None = None
    try:
        allows_attempt = bool(monitor.wait_decision().admit_network_work)
    except Exception:  # noqa: BLE001
        logger.warning("network monitor wait_decision() could not be read", exc_info=True)

    # The age is asked of the monitor, never derived here. ``observed_at`` is
    # stamped from the monitor's injected clock (``SystemClock`` is
    # ``time.monotonic``), so subtracting wall time from it would produce a
    # plausible-looking wrong number -- and clamping that to 0 would render as
    # "measured just now", which is the most confident lie available.
    observed_age: float | None = None
    try:
        reported_age = monitor.observation_age_seconds()
    except Exception:  # noqa: BLE001
        logger.warning("network monitor observation_age_seconds() could not be read", exc_info=True)
    else:
        observed_age = round(reported_age, 3) if isinstance(reported_age, (int, float)) and not isinstance(reported_age, bool) else None

    return {
        "reported": True,
        "reason": "",
        "detail": "",
        "state": state,
        "state_detail": state_detail,
        "allows_network_attempt": allows_attempt,
        "latency_ms": _mean_latency_ms(targets),
        "monitoring": running,
        "observed_age_seconds": observed_age,
        "last_observation": observation,
        "targets": targets,
        "retry": _project_retry(monitor, state, running),
        "parked_sessions": None,
        "parked_durability": "installed" if wait_service is not None else "unavailable",
    }

