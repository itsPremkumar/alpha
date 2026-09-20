"""Advanced host telemetry that fills the gaps the psutil baseline cannot see.

The base ``SystemMonitorService`` covers CPU, RAM, disk and network well through
``psutil``. Four things remain effectively blind — especially on Windows, where
``psutil.sensors_temperatures()`` returns nothing and only NVIDIA GPUs report
utilization:

* **GPU utilization** — ``nvidia-smi`` is NVIDIA-only; the WMI fallback returns
  names with no counters, so Intel/AMD/integrated GPUs show 0% forever.
* **Temperatures** — no CPU thermal reading at all on Windows.
* **Disk / ROM health** — partition usage only; no health status, media type or
  failure prediction.
* **Internet quality** — a single TCP connect gives one RTT. No packet loss, no
  DNS timing, no throughput, no public address.

Everything here is best-effort: each sampler is independently guarded, has a
timeout, caches anything expensive, and **never raises**. A missing tool or a
locked-down host degrades to ``None`` rather than breaking the vitals tick.

External calls are opt-in: public-IP lookup is cached for 10 minutes, and the
throughput probe is off by default because it deliberately consumes bandwidth.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import socket
import subprocess
import threading
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# --- tunables -------------------------------------------------------------
_GPU_UTIL_TTL_SECONDS = 10.0
_INTERNET_QUALITY_TTL_SECONDS = 15.0
_PUBLIC_IP_TTL_SECONDS = 600.0
_STORAGE_TTL_SECONDS = 120.0
_THERMAL_TTL_SECONDS = 30.0

_PROBE_TIMEOUT_SECONDS = 1.5
_PROBES_PER_HOST = 2
_INTERNET_PROBE_TARGETS: tuple[tuple[str, int, str], ...] = (
    ("1.1.1.1", 443, "cloudflare"),
    ("8.8.8.8", 443, "google"),
    ("208.67.222.222", 443, "opendns"),
)
_DNS_PROBE_HOST = "cloudflare.com"
_PUBLIC_IP_URL = "https://api.ipify.org?format=json"
# A small, stable, cache-friendly object used only to time sustained throughput.
_SPEEDTEST_URL = "https://speed.cloudflare.com/__down?bytes=1000000"

_IS_WINDOWS = platform.system() == "Windows"


def _now() -> float:
    return time.time()


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
    """Run a command without a console window; None on any failure."""
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": timeout}
    if _IS_WINDOWS:
        # Prevents a console flash when the Gateway runs without a terminal.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(cmd, **kwargs)  # type: ignore[call-arg]
    except Exception:
        logger.debug("Probe failed: %s", cmd[0], exc_info=True)
        return None


def _powershell(script: str, timeout: float = 8.0) -> str | None:
    result = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _opt_float(raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if text in ("", "N/A", "[N/A]", "None", "null"):
        return None
    try:
        return round(float(text), 1)
    except (TypeError, ValueError):
        return None


def _safe_percent(value: Any) -> float | None:
    result = _opt_float(value)
    if result is None or result != result:
        return None
    return max(0.0, min(100.0, result))


# ---------------------------------------------------------------------------
# GPU — cross-vendor
# ---------------------------------------------------------------------------

_gpu_cache: dict[str, Any] = {"at": 0.0, "value": None}
_gpu_lock = threading.Lock()


def _nvidia_gpus() -> list[dict[str, Any]]:
    result = _run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit,driver_version",
            "--format=csv,noheader,nounits",
        ],
        5.0,
    )
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return []
    gpus: list[dict[str, Any]] = []
    for line in result.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9:
            continue
        try:
            index = int(float(parts[0]))
        except ValueError:
            index = len(gpus)
        used = _opt_float(parts[3]) or 0.0
        total = _opt_float(parts[4]) or 0.0
        gpus.append(
            {
                "index": index,
                "name": parts[1] or "NVIDIA GPU",
                "vendor": "NVIDIA",
                "utilization_percent": _safe_percent(parts[2]),
                "memory_used_mb": _opt_float(parts[3]),
                "memory_total_mb": _opt_float(parts[4]),
                "memory_percent": _safe_percent(used / total * 100.0) if total > 0 else None,
                "temperature_c": _opt_float(parts[5]),
                "power_watts": _opt_float(parts[6]),
                "power_limit_watts": _opt_float(parts[7]),
                "driver_version": parts[8] if parts[8] not in ("", "N/A", "[N/A]") else None,
                "utilization_scope": "per_device",
                "source": "nvidia-smi",
            }
        )
    return gpus


def _rocm_gpus() -> list[dict[str, Any]]:
    result = _run(["rocm-smi", "--showid", "--showtemp", "--showuse", "--showmemuse", "--json"], 6.0)
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        payload = json.loads(result.stdout[result.stdout.find("{") :])
    except Exception:
        logger.debug("rocm-smi output was not JSON", exc_info=True)
        return []
    gpus: list[dict[str, Any]] = []
    for index, key in enumerate(sorted(k for k in payload if k.lower().startswith("card"))):
        entry = payload.get(key) or {}
        used = _opt_float(entry.get("GPU Memory Used (B)")) or 0.0
        total = _opt_float(entry.get("GPU Memory Total (B)")) or 0.0
        gpus.append(
            {
                "index": index,
                "name": str(entry.get("Card Series") or entry.get("Card Model") or "AMD GPU"),
                "vendor": "AMD",
                "utilization_percent": _safe_percent(entry.get("GPU use (%)")),
                "memory_used_mb": round(used / (1024 * 1024), 1) if used else None,
                "memory_total_mb": round(total / (1024 * 1024), 1) if total else None,
                "memory_percent": _safe_percent(used / total * 100.0) if total > 0 else None,
                "temperature_c": _opt_float(entry.get("Temperature (Sensor edge) (C)")),
                "power_watts": _opt_float(entry.get("Average Graphics Package Power (W)")),
                "power_limit_watts": None,
                "driver_version": None,
                "utilization_scope": "per_device",
                "source": "rocm-smi",
            }
        )
    return gpus


def _windows_gpu_utilization() -> float | None:
    """System-wide GPU utilization from the Windows GPU Engine counters.

    Engines cannot be reliably attributed to a specific adapter, so this is
    reported as a host-wide figure and flagged ``utilization_scope: "system"``.
    """
    if not _IS_WINDOWS:
        return None
    result = _run(["typeperf", r"\GPU Engine(*)\Utilization Percentage", "-sc", "1"], 8.0)
    if result is None or result.returncode != 0:
        return None
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    # Trailing banners ("Exiting, please wait...", "The command completed
    # successfully.") follow the data, so pick the last *CSV* line, not the
    # last line: data rows always open with a quoted timestamp column.
    data_row = next((ln for ln in reversed(lines) if ln.lstrip().startswith('"') and "," in ln), None)
    if data_row is None:
        return None
    values: list[float] = []
    for token in data_row.split(",")[1:]:  # skip the timestamp column
        value = _opt_float(token.strip().strip('"'))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return _safe_percent(max(values))


def _windows_video_controllers() -> list[dict[str, Any]]:
    """Per-adapter identity via CIM: name, driver, adapter memory, vendor."""
    if not _IS_WINDOWS:
        return []
    script = "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,AdapterRAM,VideoProcessor | ConvertTo-Json -Depth 3"
    raw = _powershell(script)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        logger.debug("VideoController query was not JSON", exc_info=True)
        return []
    if isinstance(payload, dict):
        payload = [payload]

    adapters: list[dict[str, Any]] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("Name") or "")
        if not name:
            continue
        upper = name.upper()
        vendor = "NVIDIA" if "NVIDIA" in upper else "AMD" if ("AMD" in upper or "RADEON" in upper) else "Intel" if "INTEL" in upper else ""
        adapter_ram = entry.get("AdapterRAM")
        # AdapterRAM is frequently wrong (>4GB wraps on older drivers).
        ram_mb = round(int(adapter_ram) / (1024 * 1024), 1) if isinstance(adapter_ram, int) and adapter_ram > 0 else None
        adapters.append(
            {
                "index": index,
                "name": name,
                "vendor": vendor,
                "utilization_percent": None,
                "memory_used_mb": None,
                "memory_total_mb": ram_mb,
                "memory_percent": None,
                "temperature_c": None,
                "power_watts": None,
                "power_limit_watts": None,
                "driver_version": str(entry.get("DriverVersion") or "") or None,
                "utilization_scope": None,
                "source": "cim",
            }
        )
    return adapters


def sample_gpus(force: bool = False) -> dict[str, Any]:
    """Best-effort cross-vendor GPU readout, cached for ``_GPU_UTIL_TTL_SECONDS``."""
    with _gpu_lock:
        cached = _gpu_cache.get("value")
        if not force and cached is not None and (_now() - float(_gpu_cache.get("at", 0.0))) < _GPU_UTIL_TTL_SECONDS:
            return cached

        gpus: list[dict[str, Any]] = []
        source = "none"
        for probe in (_nvidia_gpus, _rocm_gpus):
            gpus = probe()
            if gpus:
                source = gpus[0].get("source", "unknown")
                break

        system_utilization: float | None = None
        if not gpus:
            gpus = _windows_video_controllers()
            source = "cim" if gpus else "none"
            if gpus:
                system_utilization = _windows_gpu_utilization()
                if system_utilization is not None and len(gpus) == 1:
                    # Unambiguous: one adapter, so the host-wide figure is its figure.
                    gpus[0]["utilization_percent"] = system_utilization
                    gpus[0]["utilization_scope"] = "system"
                else:
                    # Several adapters: engine counters cannot be attributed to
                    # one of them, so none may claim the host-wide number.
                    for adapter in gpus:
                        adapter["utilization_scope"] = "unavailable"

        value = {
            "gpus": gpus,
            "count": len(gpus),
            "source": source,
            "system_utilization_percent": system_utilization,
            "utilization_supported": bool(system_utilization is not None or any(g.get("utilization_percent") is not None for g in gpus)),
            "checked_at": _now(),
        }
        _gpu_cache["value"] = value
        _gpu_cache["at"] = _now()
        return value


# ---------------------------------------------------------------------------
# Internet quality
# ---------------------------------------------------------------------------

_net_cache: dict[str, Any] = {"at": 0.0, "value": None}
_net_lock = threading.Lock()


def _tcp_rtt(host: str, port: int) -> tuple[bool, float | None]:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT_SECONDS):
            return True, round((time.monotonic() - started) * 1000.0, 1)
    except Exception:
        return False, None


def _dns_latency_ms(host: str, timeout: float = 2.0) -> float | None:
    """Resolve in a worker thread so a stalled resolver cannot block the tick."""
    result: dict[str, float | None] = {"value": None}

    def _resolve() -> None:
        started = time.monotonic()
        try:
            socket.getaddrinfo(host, None)
            result["value"] = round((time.monotonic() - started) * 1000.0, 1)
        except Exception:
            result["value"] = None

    worker = threading.Thread(target=_resolve, daemon=True)
    worker.start()
    worker.join(timeout)
    return result["value"]


def _public_ip() -> str | None:
    try:
        with urllib.request.urlopen(_PUBLIC_IP_URL, timeout=3.0) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return str(payload.get("ip")) if payload.get("ip") else None
    except Exception:
        logger.debug("Public IP lookup failed", exc_info=True)
        return None


def measure_download_mbps(url: str = _SPEEDTEST_URL, max_bytes: int = 4_000_000) -> float | None:
    """Time a sustained download. Off by default — it consumes real bandwidth."""
    started = time.monotonic()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "agent-workspace-monitor"})
        with urllib.request.urlopen(request, timeout=10.0) as response:
            total = 0
            while total < max_bytes:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
        elapsed = max(0.001, time.monotonic() - started)
        if total == 0:
            return None
        return round(total * 8 / 1_000_000.0 / elapsed, 2)
    except Exception:
        logger.debug("Download throughput probe failed", exc_info=True)
        return None


def sample_internet_quality(
    *,
    include_public_ip: bool = True,
    include_speedtest: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Multi-target reachability, packet loss, latency and DNS timing."""
    with _net_lock:
        cached = _net_cache.get("value")
        if not force and cached is not None and (_now() - float(_net_cache.get("at", 0.0))) < _INTERNET_QUALITY_TTL_SECONDS:
            return cached

        probes: list[dict[str, Any]] = []
        reachable_hosts = 0
        latencies: list[float] = []
        attempts = 0
        successes = 0

        for host, port, label in _INTERNET_PROBE_TARGETS:
            host_successes = 0
            host_latencies: list[float] = []
            for _ in range(_PROBES_PER_HOST):
                attempts += 1
                ok, rtt = _tcp_rtt(host, port)
                if ok:
                    host_successes += 1
                    successes += 1
                    if rtt is not None:
                        host_latencies.append(rtt)
                        latencies.append(rtt)
            if host_successes:
                reachable_hosts += 1
            probes.append(
                {
                    "label": label,
                    "host": host,
                    "port": port,
                    "reachable": host_successes > 0,
                    "attempts": _PROBES_PER_HOST,
                    "successes": host_successes,
                    "rtt_ms": round(sum(host_latencies) / len(host_latencies), 1) if host_latencies else None,
                    "min_rtt_ms": round(min(host_latencies), 1) if host_latencies else None,
                    "checked_at": _now(),
                }
            )

        loss_percent = round((attempts - successes) / attempts * 100.0, 1) if attempts else None
        best = min(latencies) if latencies else None
        avg = round(sum(latencies) / len(latencies), 1) if latencies else None

        value: dict[str, Any] = {
            "reachable": reachable_hosts > 0,
            "reachable_targets": reachable_hosts,
            "total_targets": len(_INTERNET_PROBE_TARGETS),
            "rtt_ms": best,
            "avg_rtt_ms": avg,
            "packet_loss_percent": loss_percent,
            "dns_latency_ms": _dns_latency_ms(_DNS_PROBE_HOST),
            "dns_host": _DNS_PROBE_HOST,
            "quality": _classify_connection(best, loss_percent, reachable_hosts),
            "probes": probes,
            "checked_at": _now(),
        }

        if include_public_ip:
            value["public_ip"] = _public_ip()
        if include_speedtest:
            value["download_mbps"] = measure_download_mbps()

        _net_cache["value"] = value
        _net_cache["at"] = _now()
        return value


def _classify_connection(rtt_ms: float | None, loss_percent: float | None, reachable: int) -> str:
    if reachable == 0:
        return "offline"
    if loss_percent is not None and loss_percent >= 25.0:
        return "unstable"
    if rtt_ms is None:
        return "unknown"
    if rtt_ms < 60 and (loss_percent or 0.0) == 0.0:
        return "excellent"
    if rtt_ms < 150:
        return "good"
    if rtt_ms < 400:
        return "fair"
    return "poor"


# ---------------------------------------------------------------------------
# Storage / ROM health
# ---------------------------------------------------------------------------

_storage_cache: dict[str, Any] = {"at": 0.0, "value": None}
_storage_lock = threading.Lock()


def _windows_physical_disks() -> list[dict[str, Any]]:
    if not _IS_WINDOWS:
        return []
    script = "Get-PhysicalDisk | Select-Object FriendlyName,HealthStatus,OperationalStatus,MediaType,BusType,Size,SerialNumber | ConvertTo-Json -Depth 3"
    raw = _powershell(script)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        logger.debug("Get-PhysicalDisk output was not JSON", exc_info=True)
        return []
    if isinstance(payload, dict):
        payload = [payload]
    disks: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        size = entry.get("Size")
        disks.append(
            {
                "name": str(entry.get("FriendlyName") or "Physical disk"),
                "health": str(entry.get("HealthStatus") or "Unknown"),
                "operational_status": str(entry.get("OperationalStatus") or "Unknown"),
                "media_type": str(entry.get("MediaType") or "Unspecified"),
                "bus_type": str(entry.get("BusType") or "Unknown"),
                "size_bytes": int(size) if isinstance(size, (int, float)) else None,
                "healthy": str(entry.get("HealthStatus") or "").lower() == "healthy",
                "source": "powershell",
            }
        )
    return disks


def _smartctl_disks() -> list[dict[str, Any]]:
    result = _run(["smartctl", "--scan", "-j"], 6.0)
    if result is None or result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout)
    except Exception:
        return []
    devices = payload.get("devices") or []
    disks: list[dict[str, Any]] = []
    for device in devices[:8]:
        name = device.get("name")
        if not name:
            continue
        info = _run(["smartctl", "-H", "-A", "-j", str(name)], 6.0)
        temperature = None
        if info is not None and info.returncode == 0:
            try:
                detail = json.loads(info.stdout)
                temperature = _opt_float((detail.get("temperature") or {}).get("current"))
            except Exception:
                temperature = None
        passed = None
        if info is not None and info.returncode == 0:
            try:
                passed = bool(json.loads(info.stdout).get("smart_status", {}).get("passed"))
            except Exception:
                passed = None
        disks.append(
            {
                "name": str(name),
                "health": "Healthy" if passed else ("Unknown" if passed is None else "At risk"),
                "operational_status": "Online",
                "media_type": "Unspecified",
                "bus_type": "Unknown",
                "size_bytes": None,
                "temperature_c": temperature,
                "healthy": bool(passed) if passed is not None else None,
                "source": "smartctl",
            }
        )
    return disks


def sample_storage_health(force: bool = False) -> dict[str, Any]:
    """Physical disk health, media type and aggregate capacity."""
    with _storage_lock:
        cached = _storage_cache.get("value")
        if not force and cached is not None and (_now() - float(_storage_cache.get("at", 0.0))) < _STORAGE_TTL_SECONDS:
            return cached

        disks: list[dict[str, Any]] = _windows_physical_disks()
        if not disks:
            disks = _smartctl_disks()

        total_bytes = sum(d.get("size_bytes") or 0 for d in disks) or None
        value = {
            "disks": disks,
            "count": len(disks),
            "total_bytes": total_bytes,
            "unhealthy": [d.get("name") for d in disks if d.get("healthy") is False],
            "supported": bool(disks),
            "checked_at": _now(),
        }
        _storage_cache["value"] = value
        _storage_cache["at"] = _now()
        return value


def sample_disk_performance(disks: list[dict[str, Any]], *, previous: dict[str, Any] | None, elapsed: float) -> dict[str, Any]:
    """Derive IOPS and per-disk throughput from counter deltas."""
    if not disks or previous is None or elapsed <= 0:
        return {"read_mbps": None, "write_mbps": None, "iops": None, "disks": []}

    prev_by_mount = {d.get("mount") or d.get("device") or d.get("name"): d for d in (previous.get("disks") or [])}
    read_bytes = write_bytes = reads = writes = 0
    per_disk: list[dict[str, Any]] = []

    for disk in disks:
        key = disk.get("mount") or disk.get("device") or disk.get("name")
        prev = prev_by_mount.get(key) or {}
        d_read = max(0, int(disk.get("read_bytes") or 0) - int(prev.get("read_bytes") or 0))
        d_write = max(0, int(disk.get("write_bytes") or 0) - int(prev.get("write_bytes") or 0))
        d_reads = max(0, int(disk.get("read_count") or 0) - int(prev.get("read_count") or 0))
        d_writes = max(0, int(disk.get("write_count") or 0) - int(prev.get("write_count") or 0))
        read_bytes += d_read
        write_bytes += d_write
        reads += d_reads
        writes += d_writes
        per_disk.append(
            {
                "name": key,
                "read_mbps": round(d_read / (1024 * 1024) / elapsed, 2),
                "write_mbps": round(d_write / (1024 * 1024) / elapsed, 2),
                "iops": round((d_reads + d_writes) / elapsed, 1),
            }
        )

    return {
        "read_mbps": round(read_bytes / (1024 * 1024) / elapsed, 2),
        "write_mbps": round(write_bytes / (1024 * 1024) / elapsed, 2),
        "iops": round((reads + writes) / elapsed, 1),
        "disks": per_disk,
    }


# ---------------------------------------------------------------------------
# CPU thermal
# ---------------------------------------------------------------------------

_thermal_cache: dict[str, Any] = {"at": 0.0, "value": None}
_thermal_lock = threading.Lock()


def _windows_cpu_temperature() -> tuple[float | None, str | None]:
    """ACPI thermal zone in deci-Kelvin, converted to Celsius.

    PowerShell/CIM is tried first: ``wmic.exe`` is deprecated on Windows 11 and
    is commonly blocked by endpoint policies, so it is only a last resort.
    """
    script = "$t = Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty CurrentTemperature; if ($t) { $t }"
    raw = _powershell(script)
    for token in re.findall(r"\d+", raw or ""):
        value = int(token)
        if 2000 < value < 5000:  # plausible deci-Kelvin range
            return round(value / 10.0 - 273.15, 1), "acpi-cim"

    result = _run(
        ["wmic", "/namespace:\\\\root\\wmi", "PATH", "MSAcpi_ThermalZoneTemperature", "get", "CurrentTemperature"],
        6.0,
    )
    if result is not None and result.returncode == 0:
        for token in re.findall(r"\d+", result.stdout):
            value = int(token)
            if 2000 < value < 5000:
                return round(value / 10.0 - 273.15, 1), "acpi-wmic"
    return None, None


def sample_cpu_thermal(force: bool = False) -> dict[str, Any]:
    """CPU package temperature, with a throttling hint.

    ``psutil`` reports no temperatures on Windows, so this falls back to the
    ACPI thermal zone; elsewhere it prefers ``psutil`` sensor readings.
    """
    with _thermal_lock:
        cached = _thermal_cache.get("value")
        if not force and cached is not None and (_now() - float(_thermal_cache.get("at", 0.0))) < _THERMAL_TTL_SECONDS:
            return cached

        temperature: float | None = None
        source: str | None = None

        try:
            import psutil  # noqa: PLC0415  (optional dependency)

            temps = psutil.sensors_temperatures() or {}
            for _name, entries in temps.items():
                for entry in entries:
                    label = str(getattr(entry, "label", "") or "").lower()
                    name_key = str(_name).lower()
                    if "cpu" in label or "core" in label or "package" in label or "cpu" in name_key or "coretemp" in name_key:
                        temperature = _opt_float(getattr(entry, "current", None))
                        source = "psutil"
                        break
                if temperature is not None:
                    break
        except Exception:
            logger.debug("psutil temperature read failed", exc_info=True)

        if temperature is None and _IS_WINDOWS:
            temperature, source = _windows_cpu_temperature()

        value = {
            "temperature_c": temperature,
            "source": source,
            "throttling": bool(temperature is not None and temperature >= 95.0),
            "supported": temperature is not None,
            "checked_at": _now(),
        }
        _thermal_cache["value"] = value
        _thermal_cache["at"] = _now()
        return value


def reset_caches() -> None:
    """Drop all cached samples — used by tests and by forced refreshes."""
    for cache in (_gpu_cache, _net_cache, _storage_cache, _thermal_cache):
        cache["value"] = None
        cache["at"] = 0.0


def enabled() -> bool:
    """Allow operators to switch advanced telemetry off entirely."""
    return os.getenv("AGENT_WORKSPACE_ADVANCED_MONITOR", "1").strip().lower() not in ("0", "false", "no", "off")
