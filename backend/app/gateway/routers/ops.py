"""Production operations endpoints (deployment metadata and runtime status).

Sits behind the default Gateway authentication like ``/api/features``: these
routes expose no secrets, only the build/runtime metadata operators need for
deploy verification, dashboards, and monitoring gates beyond ``/health`` and
``/health/ready`` (which stay public for orchestrator probes).

* ``GET /api/ops/version`` - service name plus the installed
  ``agent-workspace-harness`` package version (the dist that carries the
  release version; legacy ``alpha`` / ``agent-workspace`` names are tried as
  fallbacks, and ``"unknown"`` when package metadata is unavailable, e.g. an
  unpackaged source checkout).
* ``GET /api/ops/status`` - liveness plus process uptime, current UTC time,
  and whether the OpenAPI docs endpoints are enabled (expected ``false`` in
  production via ``GATEWAY_ENABLE_DOCS=false``).
* ``GET /api/ops/resources`` - stdlib-only host resource snapshot (CPU, memory,
  disk, load) for resource-aware autonomy and dashboards. Best-effort: fields
  the OS does not expose come back ``null`` instead of failing the probe.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from importlib import metadata

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.gateway.config import get_gateway_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ops"])

_PROCESS_START_MONOTONIC = time.monotonic()


def _resolve_gateway_version() -> str:
    """Return the installed harness package version, or "unknown" without metadata.

    ``agent-workspace-harness`` is the installed dist carrying the release
    version (kept in lockstep by scripts/bump_version.sh); the legacy names
    remain as fallbacks for older installs.
    """
    for name in ("agent-workspace-harness", "alpha", "agent-workspace"):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return "unknown"


class VersionResponse(BaseModel):
    """Deployed Gateway build identity."""

    service: str = Field(..., description="Gateway service name")
    version: str = Field(..., description='Installed agent-workspace-harness version (legacy alpha/agent-workspace fallback), or "unknown" without package metadata')


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
    return VersionResponse(service="agent-workspace-gateway", version=_resolve_gateway_version())


@router.get(
    "/ops/status",
    response_model=StatusResponse,
    summary="Gateway runtime status",
    description="Return process uptime and runtime flags for operators and monitoring.",
)
async def ops_status() -> StatusResponse:
    """Return Gateway runtime status."""
    return StatusResponse(
        service="agent-workspace-gateway",
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
        logger.debug("Host memory probe failed; reporting nulls", exc_info=True)
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
        logger.debug("Disk probe failed; reporting null", exc_info=True)
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
