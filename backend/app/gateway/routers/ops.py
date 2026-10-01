"""Production operations endpoints (deployment metadata and runtime status).

Sits behind the default Gateway authentication like ``/api/features``: these
routes expose no secrets, only the build/runtime metadata operators need for
deploy verification, dashboards, and monitoring gates beyond ``/health`` and
``/health/ready`` (which stay public for orchestrator probes).

* ``GET /api/ops/version`` - service name plus the installed
  ``alpha-harness`` package version (the dist that carries the
  release version; legacy ``alpha`` / ``alpha`` names are tried as
  fallbacks, and ``"unknown"`` when package metadata is unavailable, e.g. an
  unpackaged source checkout).
* ``GET /api/ops/status`` - liveness plus process uptime, current UTC time,
  and whether the OpenAPI docs endpoints are enabled (expected ``false`` in
  production via ``GATEWAY_ENABLE_DOCS=false``).
* ``GET /api/ops/resources`` - stdlib-only host resource snapshot (CPU, memory,
  disk, load) for resource-aware autonomy and dashboards. Best-effort: fields
  the OS does not expose come back ``null`` instead of failing the probe. A
  probe that *fails* is a degraded read of the only surface autonomy and the
  operator dashboard reason about, so it is reported at ``warning`` (once per
  probe, with a running count at ``debug`` afterwards) rather than being
  invisible at the default level.
* ``GET /api/ops/runtime`` - the durable-runtime facts the process measures but
  does not otherwise surface: whether the **previous** shutdown finished, and
  the live connectivity reading with its durable parked-session count. Both
  blocks carry an explicit ``reported`` flag, so "not measured" never renders
  as a healthy value. See :mod:`app.gateway.ops_runtime`.
* ``GET /api/ops/network`` - the connectivity reading on its own, so a UI that
  only cares about the link does not have to parse the durable-runtime bundle to
  find it. Adds the measured per-endpoint round-trip (the "speed" figure), the
  ``allows_network_attempt`` reading (which is **true** for ``unknown``), and a
  ``retry`` block that discloses the automatic re-probe schedule rather than
  claiming a bare "retrying".
* ``POST /api/ops/network/recheck`` - force one immediate measurement, which is
  the manual "Retry" control. It goes through ``NetworkMonitor.recheck()``: the
  same serialized transition the background poll loop runs, so a click landing on
  the same tick as a poll cannot double-count hysteresis corroboration and
  publish a state the ladder has not seen twice. It deliberately does not restart
  or reschedule the loop - that loop is the only thing that can notice a recovery
  nobody asked about, and it never gives up.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import threading
import time
from datetime import UTC, datetime
from importlib import metadata

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.gateway.config import get_gateway_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ops"])

_PROCESS_START_MONOTONIC = time.monotonic()


def _resolve_gateway_version() -> str:
    """Return the installed harness package version, or "unknown" without metadata.

    ``alpha-harness`` is the installed dist carrying the release
    version (kept in lockstep by scripts/bump_version.sh); the legacy names
    remain as fallbacks for older installs.
    """
    for name in ("alpha-harness", "alpha", "alpha"):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return "unknown"


class VersionResponse(BaseModel):
    """Deployed Gateway build identity."""

    service: str = Field(..., description="Gateway service name")
    version: str = Field(..., description='Installed alpha-harness version (legacy alpha/alpha fallback), or "unknown" without package metadata')


class StatusResponse(BaseModel):
    """Gateway runtime status for operators and monitoring."""

    service: str = Field(..., description="Gateway service name")
    status: str = Field(..., description='Always "ok" when this endpoint responds')
    uptime_seconds: int = Field(..., description="Seconds since this Gateway process started")
    time_utc: str = Field(..., description="Current server time in ISO 8601 UTC")
    docs_enabled: bool = Field(..., description="Whether /docs, /redoc and /openapi.json are exposed (false in production)")


@router.get(
    "/ops/version",
    response_model=VersionResponse,
    summary="Gateway version",
    description="Return the deployed Gateway build identity for deploy verification.",
)
async def ops_version() -> VersionResponse:
    """Return the deployed Gateway build identity."""
    return VersionResponse(service="alpha-gateway", version=_resolve_gateway_version())


@router.get(
    "/ops/status",
    response_model=StatusResponse,
    summary="Gateway runtime status",
    description="Return process uptime and runtime flags for operators and monitoring.",
)
async def ops_status() -> StatusResponse:
    """Return Gateway runtime status."""
    return StatusResponse(
        service="alpha-gateway",
        status="ok",
        uptime_seconds=int(time.monotonic() - _PROCESS_START_MONOTONIC),
        time_utc=datetime.now(UTC).isoformat(),
        docs_enabled=get_gateway_config().enable_docs,
    )


class MemorySnapshot(BaseModel):
    """Host physical memory in MiB (null when the OS does not expose it)."""

    total_mb: int | None = Field(default=None, description="Total physical memory in MiB")
    available_mb: int | None = Field(default=None, description="Available physical memory in MiB")


class DiskSnapshot(BaseModel):
    """Filesystem capacity in MiB for the runtime data directory."""

    path: str = Field(..., description="Directory the disk figures were taken for")
    total_mb: int = Field(..., description="Total filesystem size in MiB")
    free_mb: int = Field(..., description="Free filesystem space in MiB")


class ResourcesResponse(BaseModel):
    """Best-effort host resource snapshot for autonomy and dashboards."""

    platform: str = Field(..., description="sys.platform of the Gateway host")
    cpu_count: int | None = Field(default=None, description="os.cpu_count() (null when indeterminable)")
    memory: MemorySnapshot = Field(default_factory=MemorySnapshot)
    disk: DiskSnapshot | None = Field(default=None, description="Null when disk figures are unavailable")
    load_average: list[float] | None = Field(default=None, description="1/5/15-minute load (Unix only, else null)")


#: Per-probe announcement latches. A host probe that keeps failing is a *state*,
#: not an event, so it is reported once at WARNING and then counted at DEBUG
#: instead of repeating an identical WARNING on every /api/ops/resources poll.
#: See ``_announce_probe_degradation``.
_PROBE_WARNED: set[str] = set()
_PROBE_SEEN: dict[str, int] = {}
_PROBE_LOCK = threading.Lock()


def _announce_probe_degradation(message: str, probe: str, *, exc_info: bool = True) -> None:
    """Report a degraded host probe at WARNING once, then at DEBUG with a count.

    Raising these off DEBUG is the point: ``/api/ops/resources`` is the only
    surface resource-aware autonomy and the operator dashboard read, so a
    silently-null memory or disk block is a monitoring failure that used to be
    invisible at the default level. The first occurrence carries the traceback;
    later ones carry the running count so a permanently broken host still shows
    exactly how long it has been broken.
    """
    with _PROBE_LOCK:
        _PROBE_SEEN[probe] = _PROBE_SEEN.get(probe, 0) + 1
        first = probe not in _PROBE_WARNED
        _PROBE_WARNED.add(probe)
        seen = _PROBE_SEEN[probe]
    if first:
        logger.warning("%s (occurrence 1; further occurrences counted at debug)", message, exc_info=exc_info)
    else:
        logger.debug("%s (occurrence %d)", message, seen)


def _memory_mb() -> MemorySnapshot:
    snapshot = MemorySnapshot()
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                snapshot.total_mb = int(status.ullTotalPhys // (1024 * 1024))
                snapshot.available_mb = int(status.ullAvailPhys // (1024 * 1024))
        else:
            page_size = os.sysconf("SC_PAGE_SIZE")
            pages = os.sysconf("SC_PHYS_PAGES")
            avail_pages = os.sysconf("SC_AVPHYS_PAGES")
            snapshot.total_mb = int(page_size * pages // (1024 * 1024))
            snapshot.available_mb = int(page_size * avail_pages // (1024 * 1024))
    except Exception:
        # WARNING, not DEBUG: this is a host-probe read, and a null memory block
        # in /api/ops/resources is what resource-aware autonomy and the operator
        # dashboard reason about. Announced once per process with a running
        # count, because the probe is re-run on every request and a locked-down
        # host would otherwise emit one identical WARNING per poll.
        _announce_probe_degradation("Host memory probe failed; reporting nulls", "memory")
    return snapshot


def _disk_snapshot() -> DiskSnapshot | None:
    try:
        from alpha.config.runtime_paths import runtime_home

        target = str(runtime_home())
        usage = shutil.disk_usage(target)
        return DiskSnapshot(
            path=target,
            total_mb=int(usage.total // (1024 * 1024)),
            free_mb=int(usage.free // (1024 * 1024)),
        )
    except Exception:
        _announce_probe_degradation("Disk probe failed; reporting null", "disk", exc_info=True)
        return None


@router.get(
    "/ops/resources",
    response_model=ResourcesResponse,
    summary="Host resource snapshot",
    description="Return CPU, memory, disk, and load figures for resource-aware autonomy. Unavailable fields are null; the probe never fails the request.",
)
async def ops_resources() -> ResourcesResponse:
    """Return a best-effort host resource snapshot."""
    try:
        load_average: list[float] | None = [float(v) for v in os.getloadavg()]
    except (AttributeError, OSError):
        load_average = None
    return ResourcesResponse(
        platform=sys.platform,
        cpu_count=os.cpu_count(),
        memory=_memory_mb(),
        disk=_disk_snapshot(),
        load_average=load_average,
    )


class AutonomyAdviceResponse(BaseModel):
    """How many workers may run and which model class fits right now."""

    recommendation: str = Field(..., description="scale_up|hold|scale_down|stand_down")
    max_workers: int = Field(..., description="Ceiling on parallel workers from current headroom")
    model_class: str = Field(..., description="strong|standard|light|none — which model tier fits")
    reasons: list[str] = Field(default_factory=list, description="Human-readable causes")
    reading: dict = Field(default_factory=dict, description="Raw resource reading behind the advice")


@router.get(
    "/ops/advice",
    response_model=AutonomyAdviceResponse,
    summary="Autonomy advice",
    description="Turn the resource snapshot into a worker/model decision: scale_up, hold, scale_down, or stand_down.",
)
async def ops_advice(current_workers: int = 1) -> AutonomyAdviceResponse:
    """Return resource-driven autonomy advice for the orchestrator."""
    import asyncio as _asyncio

    def _advise():
        from alpha.ops.monitor import advise, read_resources

        reading = read_resources()
        advice = advise(reading, current_workers=max(0, current_workers))
        return advice, reading

    advice, reading = await _asyncio.to_thread(_advise)
    return AutonomyAdviceResponse(recommendation=advice.recommendation, max_workers=advice.max_workers, model_class=advice.model_class, reasons=advice.reasons, reading=reading.to_dict())


# ---------------------------------------------------------------------------
# Durable runtime: the last drain, and live connectivity
# ---------------------------------------------------------------------------


class DrainStepSnapshot(BaseModel):
    """One shutdown phase and how it ended."""

    phase: str = Field(..., description="Shutdown phase name")
    status: str = Field(..., description="completed|skipped|failed|timed_out")
    detail: str = Field(default="", description="Why it ended that way, when the step said so")


class LastDrainResponse(BaseModel):
    """The previous process's ordered-shutdown outcome.

    ``reported`` is the field to branch on. ``is_clean`` is ``None`` whenever it
    is ``False``, because "we do not know how the last shutdown went" and "the
    last shutdown went cleanly" are opposite claims and a reader must not have
    to guess which one a bare ``true`` meant.
    """

    reported: bool = Field(..., description="Whether a drain report from a previous process was found and understood")
    reason: str = Field(default="", description="Machine-readable reason when reported is false")
    detail: str = Field(default="", description="Human-readable detail when reported is false")
    is_clean: bool | None = Field(default=None, description="True only when every registered drain step completed")
    emergency: bool | None = Field(default=None, description="Whether the emergency path was taken")
    total_seconds: float | None = Field(default=None, description="Measured drain duration in seconds")
    steps: list[DrainStepSnapshot] = Field(default_factory=list, description="Per-phase outcomes, in contract order")
    incomplete: list[str] = Field(default_factory=list, description="Phases that did not complete")
    recorded_at: str | None = Field(default=None, description="ISO 8601 UTC time the record was written")


class ParkedSessionsResponse(BaseModel):
    """Durable parked-session bookkeeping (migration 0026_network_waits)."""

    reported: bool = Field(..., description="Whether the parked-session registry answered this read")
    reason: str = Field(default="", description="Machine-readable reason when reported is false")
    detail: str = Field(default="", description="The server's own error text when the read failed")
    open_waits: int | None = Field(default=None, description="Sessions currently parked on connectivity")
    claimed: int | None = Field(default=None, description="Resume attempts claimed by this process")
    resumed: int | None = Field(default=None, description="Parks this process successfully handed back")
    gave_up: int | None = Field(default=None, description="Parks this process gave up on after exhausting the attempt budget")


class ConnectivityTargetResponse(BaseModel):
    """One reachability endpoint and the round-trip the last probe measured."""

    name: str = Field(..., description="Operator-declared target name from config.yaml -> network.targets")
    reachable: bool = Field(..., description="Whether the last probe completed a TCP connect to this target")
    latency_ms: float | None = Field(
        default=None,
        description="Measured connect round-trip in milliseconds, reported for unreachable targets too so a fast refusal is distinguishable from a black-holed route. Null when the probe produced no timing",
    )
    failure_kind: str = Field(default="", description="Classified transport failure for an unreachable target (timeout, dns_failure, connection_refused, network_unreachable)")
    detail: str = Field(default="", description="Mechanism-only failure note. Never a raw exception message, which can carry a URL with a query token")


class ConnectivityRetryResponse(BaseModel):
    """The automatic re-probe schedule, as the monitor itself decided it.

    This block is the answer to "is the backend still working on getting the
    link back?". It is deliberately a *reported* schedule rather than a promise:
    the poll loop never gives up, but it does slow down to a bounded backoff, so
    an operator is better served by the real drawn delay than by the bare word
    "retrying".
    """

    automatic: bool = Field(..., description="Whether the background poll loop is running in this process right now")
    retrying: bool = Field(
        ...,
        description="True only while the loop runs AND connectivity is not fully up: the process is actively re-probing for a recovery",
    )
    next_probe_seconds: float | None = Field(default=None, description="Seconds until the next automatic probe: the drawn, jittered delay the loop will actually wait")
    poll_interval_seconds: float | None = Field(default=None, description="Configured interval while connectivity is online")
    backoff_max_seconds: float | None = Field(default=None, description="Configured ceiling the offline backoff ladder grows toward")


class RecheckOutcome(BaseModel):
    """What one operator-triggered re-probe actually did."""

    performed: bool = Field(..., description="Whether a probe was executed. False is never a silent success")
    reason: str = Field(default="", description="Machine-readable reason when performed is false")
    detail: str = Field(default="", description="The server's own error text when the probe could not be run")
    changed: bool | None = Field(default=None, description="Whether the re-probe moved the published state")
    consecutive_agreeing: int | None = Field(default=None, description="Consecutive agreeing observations collected so far, against the hysteresis requirement")
    confirmations_required: int | None = Field(default=None, description="Consecutive observations required to publish a recovery (config.yaml -> network.online_after_consecutive)")


class NetworkResponse(BaseModel):
    """Live connectivity, with absence kept distinct from a reading."""

    reported: bool = Field(..., description="Whether this process has a running connectivity measurement")
    reason: str = Field(default="", description="Machine-readable reason when reported is false")
    state: str | None = Field(default=None, description="online|degraded|unknown|offline. Never rounded: unknown stays unknown")
    state_detail: str = Field(default="", description="The shared operator-facing one-liner for this state")
    allows_network_attempt: bool | None = Field(
        default=None,
        description="Whether a network operation may be attempted now. TRUE for unknown, because not knowing is not knowing the link is down. Null when nothing was measured",
    )
    latency_ms: float | None = Field(
        default=None,
        description="Mean measured round-trip over the REACHABLE targets, in milliseconds. Null when no target was reachable, never 0 — that would read as the fastest possible link",
    )
    monitoring: bool = Field(default=False, description="Whether the poll loop is running right now")
    observed_age_seconds: float | None = Field(
        default=None,
        description="Seconds since the last completed probe, asked of the monitor's own clock. Null when nothing has been measured. There is deliberately no absolute timestamp: the reading is stamped monotonic, so exposing it as a wall-clock time would be a confident wrong number",
    )
    targets: list[ConnectivityTargetResponse] = Field(default_factory=list, description="Per-endpoint reachability from the last probe")
    retry: ConnectivityRetryResponse = Field(
        default_factory=lambda: ConnectivityRetryResponse(automatic=False, retrying=False),
        description="The automatic re-probe schedule",
    )
    parked_durability: str = Field(default="unavailable", description="installed|unavailable — whether a park can be recorded durably")
    parked_sessions: ParkedSessionsResponse | None = Field(default=None, description="Durable parked-session counts, null when no registry is installed")
    last_observation: dict | None = Field(default=None, description="The most recent measured probe result, verbatim")


class ConnectivityResponse(NetworkResponse):
    """The connectivity surface on its own, plus disclosures a consumer must read.

    ``GET /api/ops/network`` and ``POST /api/ops/network/recheck`` answer this
    shape, so a UI that only cares about the link does not have to parse the
    durable-runtime bundle to find it.
    """

    recheck: RecheckOutcome | None = Field(default=None, description="Present on a recheck: whether a probe ran, and what it produced")
    notes: list[str] = Field(default_factory=list, description="Disclosures a consumer must not read past")


class RuntimeResponse(BaseModel):
    """Durable-runtime facts an operator needs and the process already knows."""

    last_drain: LastDrainResponse = Field(..., description="How the previous process's ordered shutdown ended")
    network: NetworkResponse = Field(..., description="Live connectivity and parked-session state")
    notes: list[str] = Field(default_factory=list, description="Disclosures a consumer must not read past")


# ---------------------------------------------------------------------------
# Connectivity: the operator surface for the durable-runtime monitor
# ---------------------------------------------------------------------------

#: Upper bound on one operator-triggered probe. ``TcpConnectivityProbe`` already
#: bounds every connect and the probe as a whole; this is the route-level ceiling
#: that guarantees an HTTP answer even against a probe implementation that does
#: not bound itself.
_RECHECK_TIMEOUT_SECONDS = 20.0

#: A reading older than this many poll intervals is disclosed as stale. The strip
#: must not present a five-minute-old `online` next to a live refresh cursor.
_STALE_POLL_INTERVALS = 3.0
_FALLBACK_POLL_INTERVAL_SECONDS = 15.0


async def _read_parked_sessions(wait_service: object, *, network_reported: bool) -> ParkedSessionsResponse | None:
    """Read the durable parked-session counters, or disclose why we could not.

    Reported, never raised. This is an operator surface, and turning a
    parked-session store outage into a 500 would hide the connectivity reading
    that is still perfectly good.
    """
    if wait_service is not None:
        try:
            status = await wait_service.status()  # type: ignore[attr-defined]
            return ParkedSessionsResponse(
                reported=True,
                reason="",
                open_waits=status.open_waits,
                claimed=status.claimed,
                resumed=status.resumed,
                gave_up=status.gave_up,
            )
        except Exception as exc:  # noqa: BLE001 - a disclosed degraded read, not a failed request
            logger.warning("parked-session status read failed; reporting it as unmeasured", exc_info=True)
            return ParkedSessionsResponse(reported=False, reason="the parked-session registry could not be read", detail=f"{type(exc).__name__}: {exc}")
    if network_reported:
        return ParkedSessionsResponse(
            reported=False,
            reason="parked-session durability is not installed on this deployment",
            detail="a memory database backend has nowhere durable to record a park, so no park can have been recorded",
        )
    return None


def _confirmations_required(monitor: object) -> int | None:
    """How many agreeing observations a recovery needs, straight from the config."""
    try:
        value = monitor.config.online_after_consecutive  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return None
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


async def _perform_recheck(monitor: object | None, configured: bool | None) -> RecheckOutcome:
    """Force one immediate measurement, or say plainly that none happened.

    It goes through ``monitor.recheck()``, which runs the *same* serialized
    transition the background poll loop runs. That is the point: a "Retry" button
    that called a different code path would be a second opinion about the link
    rather than another reading of it, and a click landing on the same tick as a
    poll could otherwise double-count hysteresis corroboration and publish a
    state the ladder has not seen twice.
    """
    from app.gateway.ops_runtime import REASON_MONITOR_ABSENT, REASON_MONITOR_DISABLED

    if monitor is None:
        if configured is False:
            return RecheckOutcome(
                performed=False,
                reason=REASON_MONITOR_DISABLED,
                detail="no connectivity probe exists in this process, so there is nothing to re-probe. Set network.enabled: true in config.yaml and restart.",
            )
        return RecheckOutcome(
            performed=False,
            reason=REASON_MONITOR_ABSENT,
            detail="connectivity monitoring is configured but no monitor is installed in this process, so there is nothing to re-probe.",
        )

    measure = getattr(monitor, "recheck", None) or getattr(monitor, "check_once", None)
    if not callable(measure):
        return RecheckOutcome(
            performed=False,
            reason="the installed monitor exposes no measurement API",
            detail=f"{type(monitor).__name__} has neither recheck() nor check_once()",
        )

    try:
        observation = await asyncio.wait_for(measure(), timeout=_RECHECK_TIMEOUT_SECONDS)
    except (TimeoutError, asyncio.CancelledError):
        # Never report a probe that did not answer as one that did. Cancelling is
        # safe here: `check_once` re-raises out of the probe before it touches
        # any state, so a timed-out recheck cannot half-apply a transition.
        raise
    except Exception as exc:  # noqa: BLE001 - a failed probe is a disclosed outcome, not a 500
        logger.warning("an operator-triggered connectivity recheck failed", exc_info=True)
        return RecheckOutcome(performed=False, reason="the connectivity probe could not be run", detail=f"{type(exc).__name__}: {exc}")

    return RecheckOutcome(
        performed=True,
        reason="",
        changed=bool(getattr(observation, "changed", None)),
        # The monitor's live counter, NOT ``observation.consecutive_agreeing``:
        # an observation reports the branch it took, which is 0 whenever the
        # probe itself ran, so reading it here would report "nothing outstanding"
        # for a recheck that is in fact waiting on three more confirmations.
        consecutive_agreeing=getattr(monitor, "pending_confirmations", None),
        confirmations_required=_confirmations_required(monitor),
    )


def _connectivity_notes(snapshot: dict, network: NetworkResponse, recheck: RecheckOutcome | None) -> list[str]:
    """Disclosures a consumer must not read past.

    Each one exists because a reasonable reader would otherwise take the compact
    payload for more than it says.
    """
    notes: list[str] = []
    if not network.reported:
        notes.append(f"reported is false ({snapshot['reason']}): connectivity is not being measured by this process, which is not the same as connectivity being fine.")
        if recheck is not None:
            notes.append(f"recheck.performed is false ({recheck.reason}): no probe ran, so nothing in this payload was re-measured.")
        return notes

    if network.state == "unknown":
        notes.append("state is 'unknown': the probe could not run or returned nothing. UNKNOWN is never rounded to offline, and it still permits a network attempt.")
    if network.state == "offline":
        notes.append("state is 'offline': a confirmed outage. Work needing the link is parked and resumes automatically once it returns; this process does not stop re-probing.")
    if network.latency_ms is None:
        notes.append("latency_ms is null: no reachable endpoint produced a round-trip time in the last probe. This is NOT 0 ms and NOT a fast link.")
    if network.retry.automatic is False:
        notes.append("retry.automatic is false: the background poll loop is not running, so nothing will re-probe on its own until the process restarts.")
    elif network.retry.retrying and network.retry.next_probe_seconds is not None:
        ceiling = network.retry.backoff_max_seconds
        ceiling_text = f", backing off toward {ceiling:.0f}s" if ceiling is not None else ""
        notes.append(f"retry.retrying is true: this process re-probes automatically in about {network.retry.next_probe_seconds:.0f}s{ceiling_text}. It keeps trying until the link returns and never gives up.")
    if network.observed_age_seconds is not None:
        interval = network.retry.poll_interval_seconds or _FALLBACK_POLL_INTERVAL_SECONDS
        if network.observed_age_seconds > _STALE_POLL_INTERVALS * interval:
            notes.append(f"observed_age_seconds is {network.observed_age_seconds:.0f}s: this reading is older than the poll cadence, so it is reported as stale rather than current.")
    if network.parked_durability == "unavailable":
        notes.append("parked_durability is 'unavailable': a session that parked on connectivity could not be recorded durably on this deployment.")
    if recheck is not None and recheck.performed and recheck.changed is False and recheck.consecutive_agreeing and recheck.confirmations_required:
        notes.append(f"recheck changed nothing: {recheck.consecutive_agreeing} of {recheck.confirmations_required} consecutive confirming observations collected. The hysteresis gate is working as designed, not stuck.")
    return notes


def _network_response(snapshot: dict, parked: ParkedSessionsResponse | None) -> NetworkResponse:
    """Build the connectivity block. One place, so the two surfaces cannot drift."""
    return NetworkResponse(
        reported=snapshot["reported"],
        reason=snapshot["reason"],
        state=snapshot["state"],
        state_detail=snapshot["state_detail"],
        allows_network_attempt=snapshot["allows_network_attempt"],
        latency_ms=snapshot["latency_ms"],
        monitoring=snapshot["monitoring"],
        observed_age_seconds=snapshot["observed_age_seconds"],
        targets=[ConnectivityTargetResponse(**target) for target in snapshot["targets"]],
        retry=ConnectivityRetryResponse(**snapshot["retry"]),
        parked_durability=snapshot["parked_durability"],
        parked_sessions=parked,
        last_observation=snapshot["last_observation"],
    )


async def _connectivity(request: Request, *, recheck: bool) -> ConnectivityResponse:
    """Project live connectivity, optionally forcing one fresh measurement first."""
    from app.gateway.ops_runtime import network_snapshot

    state = request.app.state
    monitor = getattr(state, "network_monitor", None)
    wait_service = getattr(state, "network_waits", None)
    # ``None`` means the attribute was never written (a process that never ran the
    # lifespan, or an extension host). The projection then derives it from the
    # monitor itself, which is the behaviour that predates the explicit flag.
    configured = getattr(state, "network_configured", None)

    outcome = await _perform_recheck(monitor, configured) if recheck else None
    snapshot = network_snapshot(monitor, wait_service, network_enabled=configured)
    network = _network_response(snapshot, await _read_parked_sessions(wait_service, network_reported=snapshot["reported"]))
    return ConnectivityResponse(
        **network.model_dump(),
        recheck=outcome,
        notes=_connectivity_notes(snapshot, network, outcome),
    )


@router.get(
    "/ops/network",
    response_model=ConnectivityResponse,
    summary="Live internet connectivity",
    description=(
        "Report the durable-runtime connectivity reading: the four-state link state, the measured round-trip per "
        "endpoint, whether a network operation may be attempted now, and the automatic re-probe schedule. Every block "
        "carries reported=false with a reason when the fact is unmeasured, so a missing measurement is never rendered "
        "as a healthy value."
    ),
)
async def ops_network(request: Request) -> ConnectivityResponse:
    """Return the live connectivity reading with its retry schedule."""
    return await _connectivity(request, recheck=False)


@router.post(
    "/ops/network/recheck",
    response_model=ConnectivityResponse,
    summary="Re-probe connectivity now",
    description=(
        "Force one immediate measurement through the same serialized transition the background poll loop uses, and "
        "return the resulting state. A recheck that did not move the state reports how many confirming observations "
        "are still outstanding, and a probe that did not run is reported as not run rather than as a quiet success."
    ),
)
async def ops_network_recheck(request: Request) -> ConnectivityResponse:
    """Force an immediate connectivity measurement."""
    return await _connectivity(request, recheck=True)


@router.get(
    "/ops/runtime",
    response_model=RuntimeResponse,
    summary="Durable-runtime facts: last drain and live connectivity",
    description=(
        "Report the previous process's shutdown outcome and this process's connectivity reading. Each block carries reported=false with a reason when the fact is unmeasured, so a missing measurement is never rendered as a healthy value."
    ),
)
async def ops_runtime(request: Request) -> RuntimeResponse:
    """Return the last recorded drain plus the live connectivity reading.

    Reads the monitor and the parked-session registry the Gateway lifespan
    already installed, plus the drain record written at the previous teardown.
    Deliberately does **not** read the side-effect ledger: it has no production
    writer, so a count there would always be zero and would read as "no unknown
    effects" rather than "nothing records effects". That gap is documented in
    `runtime/AGENTS.md` and tracked in `docs/WIRING_AUDIT.md` instead of being
    given a permanently-empty view.
    """
    from app.gateway.ops_runtime import network_snapshot, read_last_drain

    state = request.app.state
    monitor = getattr(state, "network_monitor", None)
    wait_service = getattr(state, "network_waits", None)
    configured = getattr(state, "network_configured", None)

    drain = read_last_drain()
    snapshot = network_snapshot(monitor, wait_service, network_enabled=configured)
    notes: list[str] = []

    parked = await _read_parked_sessions(wait_service, network_reported=snapshot["reported"])

    if not drain["reported"]:
        notes.append("last_drain.reported is false: this installation has no readable record of a previous shutdown. That is the state of a first boot, and it is not a claim that the last shutdown was clean.")
    if not snapshot["reported"]:
        notes.append(f"network.reported is false ({snapshot['reason']}): connectivity is not being measured by this process, which is not the same as connectivity being fine.")
    if snapshot["reported"] and snapshot["state"] == "unknown":
        notes.append("network.state is 'unknown': the probe could not run or returned nothing. UNKNOWN is never rounded to offline, and it still permits a network attempt.")
    if snapshot["parked_durability"] == "unavailable" and snapshot["reported"]:
        notes.append("network.parked_durability is 'unavailable': a session that parked on connectivity could not be recorded durably on this deployment.")

    return RuntimeResponse(
        last_drain=LastDrainResponse(**drain),
        network=_network_response(snapshot, parked),
        notes=notes,
    )
