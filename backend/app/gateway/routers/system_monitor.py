"""System observability endpoints for the Alpha operations dashboard.

Exposes host-level telemetry collected by ``system_monitor_service``.

Endpoints
---------
GET /api/system/vitals
    Complete latest host snapshot.

GET /api/system/history
    Rolling time-series data for dashboard charts.

GET /api/system/alerts
    Current host health alerts.

GET /api/system/processes
    Top processes by CPU/memory.

GET /api/system/network/interfaces
    Per-interface network state and counters.

GET /api/system/capabilities
    Detect which host-monitoring capabilities are available.

The API intentionally exposes operational telemetry only. No environment
variables, credentials, tokens, command-line arguments, file contents,
process command lines, or other secrets are returned.

Authentication
--------------
Authentication is provided by the global AuthMiddleware for ``/api/*``.
No additional permission decorator is required here.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.gateway.system_monitor_service import get_system_monitor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------


class CpuCoreSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    core: int
    percent: float
    frequency_mhz: float | None = None


class CpuLoadSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    one_minute: float | None = None
    five_minutes: float | None = None
    fifteen_minutes: float | None = None


class CpuSnapshot(BaseModel):
    """Processor utilization and topology."""

    model_config = ConfigDict(extra="ignore")

    percent: float = Field(..., ge=0, description="Overall CPU utilization")
    cores: int = Field(..., ge=0, description="Logical CPU count")
    physical_cores: int | None = Field(
        default=None,
        ge=0,
        description="Physical CPU core count",
    )
    frequency_mhz: float | None = None
    min_frequency_mhz: float | None = None
    max_frequency_mhz: float | None = None
    load: CpuLoadSnapshot | None = None
    per_core: list[CpuCoreSnapshot] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class RamSnapshot(BaseModel):
    """Physical RAM — the primary dashboard metric."""

    model_config = ConfigDict(extra="ignore")

    total_mb: float
    used_mb: float
    available_mb: float
    free_mb: float
    percent: float


class SwapSnapshot(BaseModel):
    """Virtual memory / page file."""

    model_config = ConfigDict(extra="ignore")

    total_mb: float
    used_mb: float
    free_mb: float
    percent: float


class MemorySnapshot(BaseModel):
    ram: RamSnapshot
    swap: SwapSnapshot


# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------


class DiskIoSnapshot(BaseModel):
    read_bytes: int = 0
    write_bytes: int = 0
    read_count: int = 0
    write_count: int = 0


class DiskSnapshot(BaseModel):
    """Mounted filesystem."""

    model_config = ConfigDict(extra="ignore")

    mount: str
    device: str = ""
    filesystem: str = ""
    total_mb: float
    used_mb: float
    free_mb: float
    percent: float
    read_only: bool | None = None
    io: DiskIoSnapshot | None = None


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------


class GpuSnapshot(BaseModel):
    """GPU telemetry."""

    model_config = ConfigDict(extra="ignore")

    index: int = 0
    name: str
    vendor: str = ""
    utilization_percent: float | None = None
    memory_used_mb: float | None = None
    memory_total_mb: float | None = None
    memory_percent: float | None = None
    temperature_c: float | None = None
    power_watts: float | None = None
    power_limit_watts: float | None = None
    driver_version: str | None = None
    compute_version: str | None = None
    source: str = ""


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


class NetworkInterfaceSnapshot(BaseModel):
    """One network interface."""

    model_config = ConfigDict(extra="ignore")

    name: str
    is_up: bool
    speed_mbps: float | None = None
    mtu: int | None = None

    ipv4: list[str] = Field(default_factory=list)
    ipv6: list[str] = Field(default_factory=list)

    bytes_sent: int = 0
    bytes_recv: int = 0
    packets_sent: int = 0
    packets_recv: int = 0
    errors_in: int = 0
    errors_out: int = 0
    drops_in: int = 0
    drops_out: int = 0


class NetworkSnapshot(BaseModel):
    """Aggregate and per-interface network statistics."""

    bytes_sent: int
    bytes_recv: int
    packets_sent: int = 0
    packets_recv: int = 0

    errors_in: int = 0
    errors_out: int = 0
    drops_in: int = 0
    drops_out: int = 0

    upload_mbps: float
    download_mbps: float

    interfaces: list[NetworkInterfaceSnapshot] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internet
# ---------------------------------------------------------------------------


class InternetProbeSnapshot(BaseModel):
    host: str
    reachable: bool
    rtt_ms: float | None = None
    checked_at: float


class InternetSnapshot(BaseModel):
    reachable: bool
    rtt_ms: float | None = None
    host: str
    probes: list[InternetProbeSnapshot] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Battery / power
# ---------------------------------------------------------------------------


class BatterySnapshot(BaseModel):
    available: bool = False
    percent: float | None = None
    plugged_in: bool | None = None
    seconds_left: int | None = None
    power_plugged: bool | None = None


# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------


class SensorReading(BaseModel):
    name: str
    label: str
    value: float | None = None
    unit: str
    high: float | None = None
    critical: float | None = None


class SensorsSnapshot(BaseModel):
    temperatures: list[SensorReading] = Field(default_factory=list)
    fans: list[SensorReading] = Field(default_factory=list)
    battery: list[SensorReading] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------


class ProcessSnapshot(BaseModel):
    """Safe process information.

    Deliberately excludes command lines, environment variables, usernames,
    executable paths, and other potentially sensitive process metadata.
    """

    pid: int
    name: str
    status: str = ""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_mb: float = 0.0
    threads: int | None = None
    create_time: float | None = None


class ProcessSummary(BaseModel):
    total: int = 0
    running: int = 0
    sleeping: int = 0
    stopped: int = 0
    top_cpu: list[ProcessSnapshot] = Field(default_factory=list)
    top_memory: list[ProcessSnapshot] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Host information
# ---------------------------------------------------------------------------


class HostInfo(BaseModel):
    hostname: str
    fqdn: str | None = None

    os: str
    os_version: str
    os_release: str | None = None
    architecture: str

    kernel: str | None = None
    machine: str | None = None
    processor: str | None = None

    boot_time: float | None = None
    uptime_seconds: int

    timezone: str | None = None


class RuntimeInfo(BaseModel):
    """Alpha runtime information."""

    python_version: str
    python_implementation: str
    executable: str | None = None

    pid: int
    parent_pid: int | None = None

    process_cpu_percent: float | None = None
    process_memory_mb: float | None = None
    process_memory_percent: float | None = None
    process_threads: int | None = None


# ---------------------------------------------------------------------------
# Alpha-specific information
# ---------------------------------------------------------------------------


class AlphaRuntimeSnapshot(BaseModel):
    """Runtime state useful for the Alpha operations dashboard."""

    service_name: str = "alpha"
    version: str | None = None
    environment: str | None = None

    pid: int
    started_at: float | None = None
    uptime_seconds: float | None = None

    event_loop: str | None = None
    worker_count: int | None = None

    active_tasks: int | None = None
    queued_tasks: int | None = None
    active_agents: int | None = None
    connected_agents: int | None = None

    # These are counters/health figures, not secrets.
    api_requests_total: int | None = None
    api_errors_total: int | None = None


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class SystemCapabilities(BaseModel):
    psutil: bool
    cpu: bool
    memory: bool
    disk: bool
    disk_io: bool
    network: bool
    network_interfaces: bool
    gpu: bool
    nvidia_gpu: bool
    sensors: bool
    battery: bool
    process_monitoring: bool
    load_average: bool


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


class SystemAlert(BaseModel):
    key: str
    severity: str
    category: str = "system"
    message: str
    value: float | None = None
    threshold: float | None = None
    timestamp: float | None = None


# ---------------------------------------------------------------------------
# Complete vitals response
# ---------------------------------------------------------------------------


class SystemVitalsResponse(BaseModel):
    timestamp: float

    health: str = Field(
        default="unknown",
        description="Overall host state: healthy|warning|critical|unknown",
    )

    memory: MemorySnapshot
    disks: list[DiskSnapshot] = Field(default_factory=list)
    cpu: CpuSnapshot
    gpus: list[GpuSnapshot] = Field(default_factory=list)
    network: NetworkSnapshot
    internet: InternetSnapshot

    system: HostInfo
    runtime: RuntimeInfo
    alpha: AlphaRuntimeSnapshot | None = None

    battery: BatterySnapshot | None = None
    sensors: SensorsSnapshot | None = None
    processes: ProcessSummary | None = None

    capabilities: SystemCapabilities
    psutil_available: bool

    alerts: list[SystemAlert] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


class SystemHistoryPoint(BaseModel):
    timestamp: float

    ram_percent: float = 0.0
    swap_percent: float = 0.0
    cpu_percent: float = 0.0

    disk_percent: float = 0.0

    upload_mbps: float = 0.0
    download_mbps: float = 0.0

    gpu_percent: float | None = None
    gpu_memory_percent: float | None = None

    internet_reachable: bool | None = None
    internet_rtt_ms: float | None = None


class SystemHistoryResponse(BaseModel):
    minutes: float
    points: list[SystemHistoryPoint] = Field(default_factory=list)


class SystemAlertsResponse(BaseModel):
    timestamp: float
    alerts: list[SystemAlert] = Field(default_factory=list)


class ProcessesResponse(BaseModel):
    timestamp: float
    processes: ProcessSummary


class NetworkInterfacesResponse(BaseModel):
    timestamp: float
    interfaces: list[NetworkInterfaceSnapshot] = Field(default_factory=list)


class CapabilitiesResponse(BaseModel):
    timestamp: float
    capabilities: SystemCapabilities


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/vitals",
    response_model=SystemVitalsResponse,
    summary="Complete host system snapshot",
)
async def system_vitals() -> SystemVitalsResponse:
    """Return the complete latest system snapshot.

    The monitor service remains the source of truth. The router only
    normalizes the response into the public API contract.
    """
    try:
        raw = get_system_monitor().get_vitals()
        return SystemVitalsResponse(**raw)
    except Exception:
        logger.exception("Failed to retrieve system vitals")
        raise HTTPException(
            status_code=503,
            detail="System monitor temporarily unavailable",
        )


@router.get(
    "/history",
    response_model=SystemHistoryResponse,
    summary="System resource history",
)
async def system_history(
    minutes: float = Query(
        default=5.0,
        ge=0.5,
        le=60.0,
        description="History window in minutes",
    ),
) -> SystemHistoryResponse:
    """Return compact time-series data suitable for dashboard charts."""
    try:
        raw_points = get_system_monitor().get_history(minutes=minutes)
    except Exception:
        logger.exception("Failed to retrieve system history")
        raise HTTPException(
            status_code=503,
            detail="System history temporarily unavailable",
        )

    points: list[SystemHistoryPoint] = []

    for point in raw_points:
        ram = point.get("ram") or {}
        swap = point.get("swap") or {}
        cpu = point.get("cpu") or {}
        network = point.get("network") or {}
        gpu = point.get("gpu") or {}
        internet = point.get("internet") or {}

        disks = point.get("disks") or []
        disk_percent = 0.0

        if disks:
            disk_percent = max(
                (_safe_float(d.get("percent")) for d in disks if isinstance(d, dict)),
                default=0.0,
            )

        points.append(
            SystemHistoryPoint(
                timestamp=_safe_float(point.get("timestamp")),
                ram_percent=_safe_float(ram.get("percent")),
                swap_percent=_safe_float(swap.get("percent")),
                cpu_percent=_safe_float(cpu.get("percent")),
                disk_percent=disk_percent,
                upload_mbps=_safe_float(network.get("upload_mbps", network.get("up_mbps"))),
                download_mbps=_safe_float(network.get("download_mbps", network.get("down_mbps"))),
                gpu_percent=(_safe_float(gpu.get("utilization_percent")) if gpu else None),
                gpu_memory_percent=(_safe_float(gpu.get("memory_percent")) if gpu else None),
                internet_reachable=(bool(internet.get("reachable")) if internet else None),
                internet_rtt_ms=(_safe_float(internet.get("rtt_ms")) if internet.get("rtt_ms") is not None else None),
            )
        )

    return SystemHistoryResponse(
        minutes=minutes,
        points=points,
    )


@router.get(
    "/alerts",
    response_model=SystemAlertsResponse,
    summary="Current system alerts",
)
async def system_alerts() -> SystemAlertsResponse:
    """Return active system health alerts."""
    try:
        alerts = get_system_monitor().get_alerts()
    except Exception:
        logger.exception("Failed to retrieve system alerts")
        raise HTTPException(
            status_code=503,
            detail="System alerts temporarily unavailable",
        )

    return SystemAlertsResponse(
        timestamp=time.time(),
        alerts=alerts or [],
    )


@router.get(
    "/processes",
    response_model=ProcessesResponse,
    summary="Top host processes",
)
async def system_processes(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of processes per ranking",
    ),
    sort: str = Query(
        default="cpu",
        pattern="^(cpu|memory)$",
        description="Primary process ranking",
    ),
) -> ProcessesResponse:
    """Return safe top-process information.

    The underlying monitor omits command lines, environment variables,
    credentials, executable paths, and other sensitive process metadata.
    """
    monitor = get_system_monitor()

    try:
        data = monitor.get_processes(
            limit=limit,
            sort=sort,
        )
    except AttributeError:
        # Older monitor implementations can degrade gracefully.
        data = {
            "total": 0,
            "running": 0,
            "sleeping": 0,
            "stopped": 0,
            "top_cpu": [],
            "top_memory": [],
        }
    except Exception:
        logger.exception("Failed to retrieve process information")
        raise HTTPException(
            status_code=503,
            detail="Process monitor temporarily unavailable",
        )

    return ProcessesResponse(
        timestamp=time.time(),
        processes=ProcessSummary(**data),
    )


@router.get(
    "/network/interfaces",
    response_model=NetworkInterfacesResponse,
    summary="Network interface telemetry",
)
async def network_interfaces() -> NetworkInterfacesResponse:
    """Return per-interface network state and counters."""
    monitor = get_system_monitor()

    try:
        interfaces = monitor.get_network_interfaces()
    except AttributeError:
        interfaces = []
    except Exception:
        logger.exception("Failed to retrieve network interfaces")
        raise HTTPException(
            status_code=503,
            detail="Network monitor temporarily unavailable",
        )

    return NetworkInterfacesResponse(
        timestamp=time.time(),
        interfaces=interfaces or [],
    )


@router.get(
    "/capabilities",
    response_model=CapabilitiesResponse,
    summary="Available system monitoring capabilities",
)
async def system_capabilities() -> CapabilitiesResponse:
    """Report which optional system-monitoring capabilities are available."""
    monitor = get_system_monitor()

    try:
        capabilities = monitor.get_capabilities()
    except AttributeError:
        # Conservative fallback for older service implementations.
        try:
            import psutil  # noqa: F401

            psutil_available = True
        except ImportError:
            psutil_available = False

        capabilities = SystemCapabilities(
            psutil=psutil_available,
            cpu=psutil_available,
            memory=psutil_available,
            disk=psutil_available,
            disk_io=psutil_available,
            network=psutil_available,
            network_interfaces=psutil_available,
            gpu=False,
            nvidia_gpu=False,
            sensors=psutil_available,
            battery=psutil_available,
            process_monitoring=psutil_available,
            load_average=hasattr(os, "getloadavg"),
        ).model_dump()

    except Exception:
        logger.exception("Failed to retrieve monitor capabilities")
        raise HTTPException(
            status_code=503,
            detail="System capabilities temporarily unavailable",
        )

    return CapabilitiesResponse(
        timestamp=time.time(),
        capabilities=SystemCapabilities(**capabilities),
    )
