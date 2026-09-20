"""Continuous host system monitor for the Alpha operations dashboard.

Runs as a lightweight background thread inside the Gateway process, sampling
host resources with ``psutil`` every few seconds and retaining a rolling
history for trend display. The FastAPI router in
``app.gateway.routers.system_monitor`` serves the latest snapshot, recent
history, processes, network interfaces, capabilities, and active alerts from
this service's in-memory state.

Design notes:

* ``psutil`` is optional: when it is not installed the service still starts,
  logs a warning once, and reports zero/empty readings so the Gateway boot
  never fails because of monitoring.
* Sampling failures are contained per-tick: one bad sample never stops the
  loop and never takes down the Gateway (fail-restart-contained).
* Slow host calls are kept off the sampling tick. ``psutil.disk_partitions``
  can block for 20+ seconds on machines with stale network mounts, so the
  partition table is refreshed in the background with a TTL and the tick
  reuses the last-known table. GPU identity is cached for the same reason.
  The sampler thread therefore stays on its ~5s cadence.
* No secret-bearing data is ever collected: process command lines,
  environments, executable paths, and usernames are deliberately excluded.
* All state access is guarded by a single lock; the collector thread is a
  daemon so interpreter shutdown never hangs on it.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import platform
import socket
import socket as _socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any

from app.gateway import system_monitor_extras as _extras

logger = logging.getLogger(__name__)

try:  # Optional dependency - degrade gracefully when absent.
    import psutil as _psutil
except Exception:  # ImportError and platform-specific load failures.
    _psutil = None  # type: ignore[assignment]
    logger.warning("psutil is not installed; the system monitor will report empty readings. Install it with `pip install psutil` to enable host metrics.")

# Sampling cadence and retention: 5s ticks x 720 samples ~= 60 minutes.
DEFAULT_INTERVAL_SECONDS = 5.0
DEFAULT_HISTORY_LIMIT = 720

# Alert thresholds (percent).
RAM_HIGH_PERCENT = 80.0
RAM_CRITICAL_PERCENT = 90.0
DISK_LOW_PERCENT = 90.0
DISK_CRITICAL_PERCENT = 95.0
CPU_HIGH_PERCENT = 90.0
# Advanced telemetry thresholds (see system_monitor_extras.py).
GPU_HOT_CELSIUS = 85.0
CPU_THROTTLE_CELSIUS = 95.0

# Reliable host for the internet reachability probe (Cloudflare DNS).
_INTERNET_PROBE_HOST = "1.1.1.1"
_INTERNET_PROBE_PORT = 53
_INTERNET_PROBE_TIMEOUT_SECONDS = 2.0

# Disk partition table refresh TTL. Refreshing re-stats every mount, which can
# hang on stale network drives, so it happens at most this often and never
# blocks the sampling tick (see _refresh_disks_if_stale).
_DISK_REFRESH_TTL_SECONDS = 120.0
# Per-mount timeout (seconds) for usage stats inside a refresh.
_DISK_MOUNT_TIMEOUT_SECONDS = 6.0
# A mount that fails this many consecutive refreshes is skipped until it
# succeeds again (avoids re-hanging on dead network drives every refresh).
_DISK_MOUNT_MAX_ERRORS = 3

# GPU readout cache TTL (utilization changes; identity does not).
_GPU_REFRESH_TTL_SECONDS = 30.0

# Top-process cache TTL. A full process scan costs tens of seconds on a
# loaded Windows host (~0.1s per process handle), so scans refresh in the
# background and the endpoint serves the last-known ranking instantly.
_PROCESS_REFRESH_TTL_SECONDS = 15.0
# Hard CPU-time budget (seconds) for a cold synchronous scan: the first
# dashboard load gets partial-but-fast data instead of a hung request.
_PROCESS_COLD_SCAN_BUDGET_SECONDS = 5.0
# Bound the scan itself on busy hosts.
_PROCESS_SCAN_CAP = 512

# Pseudo-filesystems that raise on usage() or carry no signal.
_IGNORED_FSTYPES = frozenset({"", "squashfs", "tmpfs", "devtmpfs", "overlay", "proc", "sysfs", "cgroup", "cgroup2"})


def _bytes_to_mb(value: Any) -> float:
    try:
        return round(float(value) / (1024 * 1024), 1)
    except (TypeError, ValueError):
        return 0.0


def _safe_percent(value: Any) -> float:
    try:
        result = round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0
    if result != result:  # NaN guard
        return 0.0
    return max(0.0, min(100.0, result))


def _check_internet() -> dict[str, Any]:
    """Probe internet reachability with a short TCP connect; never raises."""
    checked_at = time.time()
    started = time.monotonic()
    try:
        with _socket.create_connection(
            (_INTERNET_PROBE_HOST, _INTERNET_PROBE_PORT),
            timeout=_INTERNET_PROBE_TIMEOUT_SECONDS,
        ):
            rtt_ms = round((time.monotonic() - started) * 1000.0, 1)
            probe = {"host": _INTERNET_PROBE_HOST, "reachable": True, "rtt_ms": rtt_ms, "checked_at": checked_at}
            return {"reachable": True, "rtt_ms": rtt_ms, "host": _INTERNET_PROBE_HOST, "probes": [probe]}
    except Exception:
        probe = {"host": _INTERNET_PROBE_HOST, "reachable": False, "rtt_ms": None, "checked_at": checked_at}
        return {"reachable": False, "rtt_ms": None, "host": _INTERNET_PROBE_HOST, "probes": [probe]}


def _query_nvidia_gpus() -> list[dict[str, Any]]:
    """Read all NVIDIA GPUs via nvidia-smi; empty list when unavailable."""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    if out.returncode != 0 or not out.stdout.strip():
        return []

    def _opt_float(raw: str) -> float | None:
        raw = raw.strip()
        if raw in ("", "N/A", "[N/A]"):
            return None
        try:
            return round(float(raw), 1)
        except ValueError:
            return None

    gpus: list[dict[str, Any]] = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9:
            continue
        try:
            index = int(float(parts[0]))
        except ValueError:
            index = len(gpus)
        mem_used = _opt_float(parts[3]) or 0.0
        mem_total = _opt_float(parts[4]) or 0.0
        gpus.append(
            {
                "index": index,
                "name": parts[1] or "NVIDIA GPU",
                "vendor": "NVIDIA",
                "utilization_percent": _opt_float(parts[2]),
                "memory_used_mb": _opt_float(parts[3]),
                "memory_total_mb": _opt_float(parts[4]),
                "memory_percent": _safe_percent(mem_used / mem_total * 100.0) if mem_total > 0 else None,
                "temperature_c": _opt_float(parts[5]),
                "power_watts": _opt_float(parts[6]),
                "power_limit_watts": _opt_float(parts[7]),
                "driver_version": parts[8] if parts[8] not in ("", "N/A", "[N/A]") else None,
                "compute_version": None,
                "source": "nvidia-smi",
            }
        )
    return gpus


def _query_wmi_gpu_names() -> list[dict[str, Any]]:
    """Windows fallback: video-controller names (no utilization counters)."""
    if platform.system() != "Windows":
        return []
    try:
        query = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get", "name"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception:
        return []
    if query.returncode != 0:
        return []
    names = [ln.strip() for ln in query.stdout.splitlines()[1:] if ln.strip() and ln.strip().lower() != "name"]
    return [
        {
            "index": i,
            "name": name,
            "vendor": "",
            "utilization_percent": None,
            "memory_used_mb": None,
            "memory_total_mb": None,
            "memory_percent": None,
            "temperature_c": None,
            "power_watts": None,
            "power_limit_watts": None,
            "driver_version": None,
            "compute_version": None,
            "source": "wmi",
        }
        for i, name in enumerate(names)
    ]


class SystemMonitorService:
    """Background sampler retaining the latest host snapshot plus history."""

    def __init__(
        self,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> None:
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._history: deque[dict[str, Any]] = deque(maxlen=max(0, int(history_limit)) or DEFAULT_HISTORY_LIMIT)
        self._latest: dict[str, Any] | None = None
        self._alerts: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._boot_monotonic = time.monotonic()
        self._service_started_wall = time.time()
        self._prev_net: tuple[float, float] | None = None
        self._prev_net_time: float | None = None
        # Disk cache: last-known table + refresh bookkeeping.
        self._disk_cache: list[dict[str, Any]] = []
        self._disk_cache_time: float = 0.0
        self._disk_refresh_lock = threading.Lock()
        self._disk_mount_errors: dict[str, int] = {}
        # GPU cache.
        self._gpu_cache: list[dict[str, Any]] = []
        self._gpu_cache_time: float = 0.0
        # Advanced telemetry deltas (disk IOPS needs the previous counter set).
        self._prev_disks: dict[str, Any] | None = None
        self._prev_advanced_time: float = 0.0
        # Host-wide GPU utilization when per-adapter attribution is impossible.
        self._gpu_system_utilization: float | None = None
        # Top-process cache (served instantly; refreshed in the background).
        self._process_cache: dict[str, Any] | None = None
        self._process_cache_time: float = 0.0
        self._process_refresh_lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        # No synchronous first sample: _collect() can block for tens of seconds
        # on machines with stale network mounts, and Gateway startup must never
        # wait on monitoring. The sampler thread takes the first sample
        # immediately; readers degrade to an empty snapshot until then.
        self._thread = threading.Thread(
            name="alpha-system-monitor",
            target=self._loop,
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "System monitor started (interval=%.1fs, history=%d)",
            self.interval_seconds,
            self._history.maxlen,
        )

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=min(5.0, self.interval_seconds + 1.0))

    # -- reads ----------------------------------------------------------

    def get_vitals(self) -> dict[str, Any]:
        with self._lock:
            latest = dict(self._latest) if self._latest else None
            alerts = [dict(a) for a in self._alerts]
        if latest is None:
            latest = self._empty_snapshot(reason="no samples collected yet")
        latest = dict(latest)
        latest["alerts"] = alerts
        return latest

    def get_history(self, minutes: float = 5.0) -> list[dict[str, Any]]:
        try:
            window = max(0.5, float(minutes))
        except (TypeError, ValueError):
            window = 5.0
        cutoff = time.time() - window * 60.0
        with self._lock:
            points = [dict(p) for p in self._history if float(p.get("timestamp", 0) or 0) >= cutoff]
        return points

    def get_alerts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(a) for a in self._alerts]

    def get_processes(self, limit: int = 20, sort: str = "cpu") -> dict[str, Any]:
        """Top-process summary from cache; never raises, never blocks long.

        A full scan is too slow to run per request, so the endpoint serves the
        last-known ranking instantly. A stale cache triggers a background
        refresh; a cold cache (first call) gets one budget-bounded synchronous
        scan so the first dashboard load shows partial-but-fast data.
        """
        limit = max(1, min(100, int(limit) if isinstance(limit, int) else 20))
        if sort not in ("cpu", "memory"):
            sort = "cpu"
        empty = {"total": 0, "running": 0, "sleeping": 0, "stopped": 0, "top_cpu": [], "top_memory": []}
        if _psutil is None:
            return empty
        now = time.time()
        with self._lock:
            cached = self._process_cache
            cache_age = now - self._process_cache_time
        if cached is None:
            # Cold cache: one bounded synchronous scan for fast first paint.
            # An empty scan (transient failure under load) is not cached, so
            # the next caller retries instead of pinning an empty ranking.
            scanned = self._scan_processes(budget_seconds=_PROCESS_COLD_SCAN_BUDGET_SECONDS)
            if scanned.get("total"):
                with self._lock:
                    self._process_cache = scanned
                    self._process_cache_time = time.time()
                cached = scanned
            else:
                self._trigger_process_refresh()
                return dict(empty)
        elif cache_age > _PROCESS_REFRESH_TTL_SECONDS:
            self._trigger_process_refresh()
        return {
            "total": cached.get("total", 0),
            "running": cached.get("running", 0),
            "sleeping": cached.get("sleeping", 0),
            "stopped": cached.get("stopped", 0),
            "top_cpu": list(cached.get("top_cpu", []))[:limit],
            "top_memory": list(cached.get("top_memory", []))[:limit],
        }

    def _trigger_process_refresh(self) -> None:
        """Start a background process-scan refresh when none is running."""
        if not self._process_refresh_lock.acquire(blocking=False):
            return
        worker = threading.Thread(
            name="alpha-system-monitor-procs",
            target=self._refresh_processes_guarded,
            daemon=True,
        )
        try:
            worker.start()
        except Exception:
            self._process_refresh_lock.release()
            logger.debug("Process refresh thread failed to start", exc_info=True)

    def _refresh_processes_guarded(self) -> None:
        """Background process-scan wrapper that always releases its lock."""
        try:
            fresh = self._scan_processes(budget_seconds=None)
            with self._lock:
                self._process_cache = fresh
                self._process_cache_time = time.time()
        except Exception:
            logger.debug("Background process scan failed", exc_info=True)
        finally:
            self._process_refresh_lock.release()

    def _scan_processes(self, budget_seconds: float | None) -> dict[str, Any]:
        """Scan processes into a ranked summary; never raises.

        ``budget_seconds`` bounds a cold synchronous scan (checked every few
        processes); ``None`` means scan to completion for background refresh.
        """
        empty = {"total": 0, "running": 0, "sleeping": 0, "stopped": 0, "top_cpu": [], "top_memory": []}
        if _psutil is None:
            return empty
        deadline = time.monotonic() + budget_seconds if budget_seconds is not None else None
        try:
            procs: list[dict[str, Any]] = []
            counts = {"running": 0, "sleeping": 0, "stopped": 0}
            for index, proc in enumerate(_psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_percent", "memory_info", "num_threads", "create_time"])):
                if deadline is not None and index % 16 == 0 and time.monotonic() > deadline:
                    logger.debug("Cold process scan hit its %.1fs budget at %d processes", budget_seconds, len(procs))
                    break
                try:
                    info = proc.info
                except (_psutil.NoSuchProcess, _psutil.AccessDenied, _psutil.ZombieProcess):
                    continue
                # NOTE: command lines, environments, executable paths, and
                # usernames are deliberately excluded - telemetry only.
                mem_info = info.get("memory_info")
                try:
                    rss = mem_info.size if hasattr(mem_info, "size") else (mem_info or [0])[0]
                    mem_mb = _bytes_to_mb(rss)
                except Exception:
                    mem_mb = 0.0
                try:
                    cpu_pct = float(info.get("cpu_percent") or 0.0)
                except (TypeError, ValueError):
                    cpu_pct = 0.0
                try:
                    mem_pct = float(info.get("memory_percent") or 0.0)
                except (TypeError, ValueError):
                    mem_pct = 0.0
                status = str(info.get("status") or "")
                if status in counts:
                    counts[status] += 1
                threads = info.get("num_threads")
                try:
                    threads = int(threads) if threads is not None else None
                except (TypeError, ValueError):
                    threads = None
                created = info.get("create_time")
                try:
                    created = float(created) if created is not None else None
                except (TypeError, ValueError):
                    created = None
                procs.append(
                    {
                        "pid": int(info.get("pid") or 0),
                        "name": str(info.get("name") or "unknown"),
                        "status": status,
                        "cpu_percent": round(cpu_pct, 1),
                        "memory_percent": round(mem_pct, 1),
                        "memory_mb": mem_mb,
                        "threads": threads,
                        "create_time": created,
                    }
                )
                if len(procs) >= _PROCESS_SCAN_CAP:  # bound the scan on busy hosts
                    break
            return {
                "total": len(procs),
                "running": counts["running"],
                "sleeping": counts["sleeping"],
                "stopped": counts["stopped"],
                "top_cpu": sorted(procs, key=lambda p: p["cpu_percent"], reverse=True),
                "top_memory": sorted(procs, key=lambda p: p["memory_percent"], reverse=True),
            }
        except Exception:
            logger.debug("Process scan failed", exc_info=True)
            return empty

    def get_network_interfaces(self) -> list[dict[str, Any]]:
        """On-demand per-interface state and counters; never raises."""
        if _psutil is None:
            return []
        try:
            stats = _psutil.net_if_stats() or {}
            addrs = _psutil.net_if_addrs() or {}
            counters = (_psutil.net_io_counters(pernic=True) or {}) if hasattr(_psutil, "net_io_counters") else {}
        except Exception:
            logger.debug("Interface stats failed", exc_info=True)
            return []
        interfaces: list[dict[str, Any]] = []
        for name in sorted(set(stats) | set(addrs)):
            stat = stats.get(name)
            counter = counters.get(name)
            ipv4: list[str] = []
            ipv6: list[str] = []
            for addr in addrs.get(name, []):
                family = str(getattr(addr, "family", ""))
                if "AF_INET6" in family or ":" in str(getattr(addr, "address", "")):
                    if getattr(addr, "address", None):
                        ipv6.append(str(addr.address))
                elif getattr(addr, "address", None):
                    ipv4.append(str(addr.address))
            interfaces.append(
                {
                    "name": name,
                    "is_up": bool(getattr(stat, "isup", False)) if stat is not None else False,
                    "speed_mbps": float(stat.speed) if stat is not None and getattr(stat, "speed", 0) else None,
                    "mtu": int(stat.mtu) if stat is not None and getattr(stat, "mtu", None) else None,
                    "ipv4": ipv4,
                    "ipv6": ipv6,
                    "bytes_sent": int(getattr(counter, "bytes_sent", 0)) if counter is not None else 0,
                    "bytes_recv": int(getattr(counter, "bytes_recv", 0)) if counter is not None else 0,
                    "packets_sent": int(getattr(counter, "packets_sent", 0)) if counter is not None else 0,
                    "packets_recv": int(getattr(counter, "packets_recv", 0)) if counter is not None else 0,
                    "errors_in": int(getattr(counter, "errin", 0)) if counter is not None else 0,
                    "errors_out": int(getattr(counter, "errout", 0)) if counter is not None else 0,
                    "drops_in": int(getattr(counter, "dropin", 0)) if counter is not None else 0,
                    "drops_out": int(getattr(counter, "dropout", 0)) if counter is not None else 0,
                }
            )
        return interfaces

    def get_capabilities(self) -> dict[str, bool]:
        """Report which monitoring capabilities this host supports."""
        has_psutil = _psutil is not None
        nvidia = bool(self._gpu_cache and any(g.get("source") == "nvidia-smi" for g in self._gpu_cache))
        if not nvidia:
            # Cheap presence check without a full query.
            try:
                probe = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=5)
                nvidia = probe.returncode == 0 and bool(probe.stdout.strip())
            except Exception:
                nvidia = False
        return {
            "psutil": has_psutil,
            "cpu": has_psutil,
            "memory": has_psutil,
            "disk": True,  # stdlib fallback via shutil covers the basics
            "disk_io": has_psutil,
            "network": has_psutil,
            "network_interfaces": has_psutil,
            "gpu": nvidia or any(g.get("source") == "wmi" for g in self._gpu_cache),
            "nvidia_gpu": nvidia,
            "sensors": has_psutil,
            "battery": has_psutil,
            "process_monitoring": has_psutil,
            "load_average": hasattr(os, "getloadavg"),
            # Advanced telemetry (app/gateway/system_monitor_extras.py).
            "advanced": _extras.enabled(),
            "gpu_utilization": self._gpu_utilization_supported(),
            "gpu_amd": any(g.get("vendor") == "AMD" for g in self._gpu_cache),
            "internet_quality": _extras.enabled(),
            "storage_health": self._storage_health_supported(),
            "cpu_thermal": self._cpu_thermal_supported(),
        }

    def _gpu_utilization_supported(self) -> bool:
        if not _extras.enabled():
            return any(g.get("utilization_percent") is not None for g in self._gpu_cache)
        try:
            return bool(_extras.sample_gpus().get("utilization_supported"))
        except Exception:
            return False

    def _storage_health_supported(self) -> bool:
        if not _extras.enabled():
            return False
        try:
            return bool(_extras.sample_storage_health().get("supported"))
        except Exception:
            return False

    def _cpu_thermal_supported(self) -> bool:
        if not _extras.enabled():
            return False
        try:
            return bool(_extras.sample_cpu_thermal().get("supported"))
        except Exception:
            return False

    # -- sampler internals ----------------------------------------------

    def _loop(self) -> None:
        # Sample immediately, then on cadence.
        try:
            self._sample_once()
        except Exception:
            logger.exception("System monitor sample failed (non-fatal)")
        while not self._stop.wait(self.interval_seconds):
            try:
                self._sample_once()
            except Exception:
                # One bad tick must never kill the loop or the Gateway.
                logger.exception("System monitor sample failed (non-fatal)")

    def _sample_once(self) -> dict[str, Any]:
        snapshot = self._collect()
        alerts = self._evaluate_alerts(snapshot)
        snapshot["health"] = "critical" if any(a["severity"] == "critical" for a in alerts) else ("warning" if alerts else ("unknown" if not snapshot.get("psutil_available") else "healthy"))
        snapshot["alerts"] = alerts
        with self._lock:
            self._latest = snapshot
            self._history.append(self._compact_history_point(snapshot))
            self._alerts = alerts
        return snapshot

    @staticmethod
    def _compact_history_point(snapshot: dict[str, Any]) -> dict[str, Any]:
        """Store a compact trend point (not the full snapshot)."""
        ram = snapshot.get("memory", {}).get("ram", {}) if isinstance(snapshot.get("memory"), dict) else snapshot.get("ram", {})
        swap = snapshot.get("memory", {}).get("swap", {}) if isinstance(snapshot.get("memory"), dict) else {}
        cpu = snapshot.get("cpu", {})
        network = snapshot.get("network", {})
        gpus = snapshot.get("gpus", [])
        gpu = gpus[0] if gpus else None
        internet = snapshot.get("internet", {})
        disks = snapshot.get("disks", [])
        return {
            "timestamp": snapshot.get("timestamp", time.time()),
            "ram": {"percent": _safe_percent(ram.get("percent"))},
            "swap": {"percent": _safe_percent(swap.get("percent"))},
            "cpu": {"percent": _safe_percent(cpu.get("percent"))},
            "network": {
                "upload_mbps": float(network.get("upload_mbps", network.get("up_mbps", 0.0)) or 0.0),
                "download_mbps": float(network.get("download_mbps", network.get("down_mbps", 0.0)) or 0.0),
            },
            "gpu": (
                {
                    "utilization_percent": gpu.get("utilization_percent"),
                    "memory_percent": gpu.get("memory_percent"),
                }
                if isinstance(gpu, dict)
                else None
            ),
            "internet": {"reachable": bool(internet.get("reachable")), "rtt_ms": internet.get("rtt_ms")},
            "disks": [{"mount": d.get("mount", "?"), "percent": _safe_percent(d.get("percent"))} for d in disks if isinstance(d, dict)],
        }

    def _collect(self) -> dict[str, Any]:
        now = time.time()
        ram = {"total_mb": 0.0, "used_mb": 0.0, "available_mb": 0.0, "free_mb": 0.0, "percent": 0.0}
        swap = {"total_mb": 0.0, "used_mb": 0.0, "free_mb": 0.0, "percent": 0.0}
        cpu: dict[str, Any] = {
            "percent": 0.0,
            "cores": 0,
            "physical_cores": None,
            "frequency_mhz": None,
            "min_frequency_mhz": None,
            "max_frequency_mhz": None,
            "load": None,
            "per_core": [],
        }
        network: dict[str, Any] = {
            "bytes_sent": 0,
            "bytes_recv": 0,
            "packets_sent": 0,
            "packets_recv": 0,
            "errors_in": 0,
            "errors_out": 0,
            "drops_in": 0,
            "drops_out": 0,
            "upload_mbps": 0.0,
            "download_mbps": 0.0,
            "up_mbps": 0.0,
            "down_mbps": 0.0,
            "interfaces": [],
        }

        if _psutil is not None:
            try:
                mem = _psutil.virtual_memory()
                ram = {
                    "total_mb": _bytes_to_mb(mem.total),
                    "used_mb": _bytes_to_mb(mem.used),
                    "available_mb": _bytes_to_mb(getattr(mem, "available", 0)),
                    "free_mb": _bytes_to_mb(getattr(mem, "available", getattr(mem, "free", 0))),
                    "percent": _safe_percent(mem.percent),
                }
            except Exception:
                logger.debug("RAM sample failed", exc_info=True)
            try:
                sw = _psutil.swap_memory()
                swap = {
                    "total_mb": _bytes_to_mb(sw.total),
                    "used_mb": _bytes_to_mb(sw.used),
                    "free_mb": _bytes_to_mb(sw.free),
                    "percent": _safe_percent(sw.percent),
                }
            except Exception:
                logger.debug("Swap sample failed", exc_info=True)
            cpu = self._sample_cpu()
            network = self._sample_network(now)
        else:
            # psutil missing: still report at least the current-drive disk usage
            # via stdlib so the dashboard is not entirely blank.
            self._disk_cache = self._stdlib_disk_fallback()
            self._disk_cache_time = now

        disks = self._disks_for_tick(now)
        gpus = self._gpus_for_tick(now)
        internet = self._sample_internet()
        advanced = self._sample_advanced(now, disks)
        if advanced.get("cpu_thermal") and cpu is not None:
            cpu["temperature_c"] = advanced["cpu_thermal"].get("temperature_c")
            cpu["thermal_throttling"] = advanced["cpu_thermal"].get("throttling")
        battery = self._sample_battery()
        sensors = self._sample_sensors()
        system = self._sample_host(now)
        runtime = self._sample_runtime()
        alpha = self._sample_alpha(now)

        snapshot: dict[str, Any] = {
            "timestamp": time.time(),
            "health": "unknown",
            "memory": {"ram": ram, "swap": swap},
            # Legacy alias: RAM stays reachable as a top-level primary metric.
            "ram": ram,
            "disks": disks,
            "cpu": cpu,
            "gpus": gpus,
            # Legacy alias: first GPU or None.
            "gpu": gpus[0] if gpus else None,
            # Host-wide GPU utilization, used when no single adapter can claim it.
            "gpu_system_utilization_percent": self._gpu_system_utilization,
            "network": network,
            "internet": internet,
            "system": system,
            "runtime": runtime,
            "alpha": alpha,
            "battery": battery,
            "sensors": sensors,
            "cpu_thermal": advanced.get("cpu_thermal"),
            "storage": advanced.get("storage"),
            "disk_performance": advanced.get("disk_performance"),
            "processes": None,  # on-demand via GET /api/system/processes
            "capabilities": self.get_capabilities(),
            "psutil_available": _psutil is not None,
        }
        return snapshot

    # -- advanced telemetry (cross-vendor GPU, internet quality, ROM health) --

    def _sample_internet(self) -> dict[str, Any]:
        """Multi-target internet quality, keeping the legacy key contract."""
        if not _extras.enabled():
            return _check_internet()
        try:
            quality = _extras.sample_internet_quality()
        except Exception:
            logger.debug("Advanced internet sampling failed", exc_info=True)
            return _check_internet()
        probes = quality.get("probes") or []
        return {
            "reachable": bool(quality.get("reachable")),
            "rtt_ms": quality.get("rtt_ms"),
            "host": probes[0].get("host", _INTERNET_PROBE_HOST) if probes else _INTERNET_PROBE_HOST,
            "probes": probes,
            "quality": quality.get("quality"),
            "avg_rtt_ms": quality.get("avg_rtt_ms"),
            "packet_loss_percent": quality.get("packet_loss_percent"),
            "dns_latency_ms": quality.get("dns_latency_ms"),
            "public_ip": quality.get("public_ip"),
            "reachable_targets": quality.get("reachable_targets"),
            "total_targets": quality.get("total_targets"),
            "checked_at": quality.get("checked_at"),
        }

    def _sample_advanced(self, now: float, disks: list[dict[str, Any]]) -> dict[str, Any]:
        """CPU thermal, physical-disk health and disk throughput in one pass."""
        empty = {"cpu_thermal": None, "storage": None, "disk_performance": None}
        if not _extras.enabled():
            return empty
        try:
            elapsed = (now - self._prev_advanced_time) if self._prev_advanced_time else 0.0
            performance = _extras.sample_disk_performance(disks, previous=self._prev_disks, elapsed=elapsed) if elapsed > 0 and self._prev_disks is not None else {"read_mbps": None, "write_mbps": None, "iops": None, "disks": []}
            self._prev_disks = {"disks": disks}
            self._prev_advanced_time = now
            return {
                "cpu_thermal": _extras.sample_cpu_thermal(),
                "storage": _extras.sample_storage_health(),
                "disk_performance": performance,
            }
        except Exception:
            logger.debug("Advanced telemetry sampling failed", exc_info=True)
            return empty

    # -- per-area samplers (each failure-contained) ----------------------

    def _sample_cpu(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "percent": 0.0,
            "cores": 0,
            "physical_cores": None,
            "frequency_mhz": None,
            "min_frequency_mhz": None,
            "max_frequency_mhz": None,
            "load": None,
            "per_core": [],
        }
        if _psutil is None:
            return result
        try:
            result["percent"] = _safe_percent(_psutil.cpu_percent(interval=None))
        except Exception:
            logger.debug("CPU percent failed", exc_info=True)
        try:
            result["cores"] = int(_psutil.cpu_count(logical=True) or 0)
            physical = _psutil.cpu_count(logical=False)
            result["physical_cores"] = int(physical) if physical else None
        except Exception:
            logger.debug("CPU count failed", exc_info=True)
        try:
            freq = _psutil.cpu_freq()
            if freq and freq.current:
                result["frequency_mhz"] = round(float(freq.current), 1)
                result["min_frequency_mhz"] = round(float(freq.min), 1) if freq.min else None
                result["max_frequency_mhz"] = round(float(freq.max), 1) if freq.max else None
        except Exception:
            logger.debug("CPU frequency failed", exc_info=True)
        try:
            load = os.getloadavg()  # Unix only; absent on Windows.
            result["load"] = {
                "one_minute": round(float(load[0]), 2),
                "five_minutes": round(float(load[1]), 2),
                "fifteen_minutes": round(float(load[2]), 2),
            }
        except (AttributeError, OSError):
            result["load"] = None
        try:
            per_cpu = _psutil.cpu_percent(interval=None, percpu=True) or []
            try:
                per_freq = _psutil.cpu_freq(percpu=True) or []
            except Exception:
                per_freq = []
            cores = []
            for i, pct in enumerate(per_cpu):
                freq_mhz = None
                if i < len(per_freq) and getattr(per_freq[i], "current", None):
                    freq_mhz = round(float(per_freq[i].current), 1)
                cores.append({"core": i, "percent": _safe_percent(pct), "frequency_mhz": freq_mhz})
            result["per_core"] = cores
        except Exception:
            logger.debug("Per-core CPU failed", exc_info=True)
        # Legacy alias for older readers.
        result["freq_mhz"] = result["frequency_mhz"]
        return result

    def _sample_network(self, now: float) -> dict[str, Any]:
        result: dict[str, Any] = {
            "bytes_sent": 0,
            "bytes_recv": 0,
            "packets_sent": 0,
            "packets_recv": 0,
            "errors_in": 0,
            "errors_out": 0,
            "drops_in": 0,
            "drops_out": 0,
            "upload_mbps": 0.0,
            "download_mbps": 0.0,
            "up_mbps": 0.0,
            "down_mbps": 0.0,
            "interfaces": [],
        }
        if _psutil is None:
            return result
        try:
            counters = _psutil.net_io_counters()
            sent = int(counters.bytes_sent)
            recv = int(counters.bytes_recv)
            up_mbps = down_mbps = 0.0
            if self._prev_net is not None and self._prev_net_time is not None:
                elapsed = max(0.1, now - self._prev_net_time)
                up_mbps = round(max(0.0, (sent - self._prev_net[0]) * 8.0 / 1_000_000.0 / elapsed), 3)
                down_mbps = round(max(0.0, (recv - self._prev_net[1]) * 8.0 / 1_000_000.0 / elapsed), 3)
            self._prev_net = (float(sent), float(recv))
            self._prev_net_time = now
            result.update(
                {
                    "bytes_sent": sent,
                    "bytes_recv": recv,
                    "packets_sent": int(getattr(counters, "packets_sent", 0)),
                    "packets_recv": int(getattr(counters, "packets_recv", 0)),
                    "errors_in": int(getattr(counters, "errin", 0)),
                    "errors_out": int(getattr(counters, "errout", 0)),
                    "drops_in": int(getattr(counters, "dropin", 0)),
                    "drops_out": int(getattr(counters, "dropout", 0)),
                    "upload_mbps": up_mbps,
                    "download_mbps": down_mbps,
                    "up_mbps": up_mbps,
                    "down_mbps": down_mbps,
                }
            )
        except Exception:
            logger.debug("Network sample failed", exc_info=True)
        return result

    def _sample_battery(self) -> dict[str, Any] | None:
        if _psutil is None or not hasattr(_psutil, "sensors_battery"):
            return None
        try:
            battery = _psutil.sensors_battery()
        except Exception:
            logger.debug("Battery sample failed", exc_info=True)
            return None
        if battery is None:
            return {"available": False, "percent": None, "plugged_in": None, "seconds_left": None, "power_plugged": None}
        return {
            "available": True,
            "percent": _safe_percent(battery.percent),
            "plugged_in": bool(battery.power_plugged),
            "seconds_left": int(battery.secsleft) if battery.secsleft not in (None, _psutil.POWER_TIME_UNLIMITED, _psutil.POWER_TIME_UNKNOWN) else None,
            "power_plugged": bool(battery.power_plugged),
        }

    def _sample_sensors(self) -> dict[str, Any] | None:
        if _psutil is None:
            return None
        snapshot: dict[str, Any] = {"temperatures": [], "fans": [], "battery": []}
        try:
            temps = _psutil.sensors_temperatures() or {} if hasattr(_psutil, "sensors_temperatures") else {}
            for name, entries in temps.items():
                for entry in entries:
                    snapshot["temperatures"].append(
                        {
                            "name": str(name),
                            "label": str(getattr(entry, "label", "") or name),
                            "value": float(entry.current) if getattr(entry, "current", None) is not None else None,
                            "unit": "C",
                            "high": float(entry.high) if getattr(entry, "high", None) is not None else None,
                            "critical": float(entry.critical) if getattr(entry, "critical", None) is not None else None,
                        }
                    )
        except Exception:
            logger.debug("Temperature sensors failed", exc_info=True)
        try:
            fans = _psutil.sensors_fans() or {} if hasattr(_psutil, "sensors_fans") else {}
            for name, entries in fans.items():
                for entry in entries:
                    snapshot["fans"].append(
                        {
                            "name": str(name),
                            "label": str(getattr(entry, "label", "") or name),
                            "value": float(entry.current) if getattr(entry, "current", None) is not None else None,
                            "unit": "RPM",
                            "high": None,
                            "critical": None,
                        }
                    )
        except Exception:
            logger.debug("Fan sensors failed", exc_info=True)
        return snapshot

    def _sample_host(self, now: float) -> dict[str, Any]:
        try:
            boot = float(_psutil.boot_time()) if _psutil is not None else now - (time.monotonic() - self._boot_monotonic)
        except Exception:
            boot = now - (time.monotonic() - self._boot_monotonic)
        try:
            timezone: str | None = datetime.now().astimezone().tzname()
        except Exception:
            timezone = None
        return {
            "hostname": socket.gethostname(),
            "fqdn": socket.getfqdn(),
            "os": platform.system(),
            "os_version": platform.version(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "kernel": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "boot_time": boot,
            "uptime_seconds": max(0, int(now - boot)),
            "timezone": timezone,
        }

    def _sample_runtime(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "executable": sys.executable,
            "pid": os.getpid(),
            "parent_pid": os.getppid(),
            "process_cpu_percent": None,
            "process_memory_mb": None,
            "process_memory_percent": None,
            "process_threads": None,
        }
        if _psutil is not None:
            try:
                proc = _psutil.Process()
                with proc.oneshot():
                    info["process_cpu_percent"] = round(float(proc.cpu_percent(interval=None)), 1)
                    info["process_memory_mb"] = _bytes_to_mb(proc.memory_info().rss)
                    info["process_memory_percent"] = round(float(proc.memory_percent()), 2)
                    info["process_threads"] = int(proc.num_threads())
            except Exception:
                logger.debug("Runtime process stats failed", exc_info=True)
        return info

    def _sample_alpha(self, now: float) -> dict[str, Any]:
        try:
            from importlib import metadata as _metadata

            version: str | None = _metadata.version("agent-workspace")
        except Exception:
            version = None
        return {
            "service_name": "alpha",
            "version": version,
            "environment": os.environ.get("AGENT_WORKSPACE_ENV"),
            "pid": os.getpid(),
            "started_at": self._service_started_wall,
            "uptime_seconds": round(now - self._service_started_wall, 1),
            "event_loop": "asyncio",
            "worker_count": 1,
            "active_tasks": None,
            "queued_tasks": None,
            "active_agents": None,
            "connected_agents": None,
            "api_requests_total": None,
            "api_errors_total": None,
        }

    # -- disk cache (hang-safe) ------------------------------------------

    @staticmethod
    def _stdlib_disk_fallback() -> list[dict[str, Any]]:
        """Minimal disk readout without psutil (current drive only)."""
        import shutil

        try:
            usage = shutil.disk_usage(".")
            total = float(usage.total) or 1.0
            return [
                {
                    "mount": ".",
                    "device": "",
                    "filesystem": "",
                    "total_mb": _bytes_to_mb(usage.total),
                    "used_mb": _bytes_to_mb(usage.used),
                    "free_mb": _bytes_to_mb(usage.free),
                    "percent": _safe_percent(usage.used / total * 100.0),
                    "read_only": None,
                    "io": None,
                }
            ]
        except Exception:
            return []

    def _disks_for_tick(self, now: float) -> list[dict[str, Any]]:
        """Return last-known disks; kick a background refresh when stale."""
        stale = (now - self._disk_cache_time) > _DISK_REFRESH_TTL_SECONDS
        if stale and self._disk_refresh_lock.acquire(blocking=False):
            worker = threading.Thread(
                name="alpha-system-monitor-disks",
                target=self._refresh_disk_cache_guarded,
                daemon=True,
            )
            try:
                worker.start()
            except Exception:
                self._disk_refresh_lock.release()
                logger.debug("Disk refresh thread failed to start", exc_info=True)
        with self._lock:
            return [dict(d) for d in self._disk_cache]

    def _refresh_disk_cache_guarded(self) -> None:
        """Refresh wrapper that always releases the refresh lock."""
        try:
            self._refresh_disk_cache()
        except Exception:
            logger.debug("Disk refresh failed", exc_info=True)
        finally:
            self._disk_refresh_lock.release()

    def _refresh_disk_cache(self) -> None:
        """Re-enumerate mounts and usage; per-mount timeouts apply."""
        if _psutil is None:
            self._disk_cache = self._stdlib_disk_fallback()
            self._disk_cache_time = time.time()
            return
        try:
            parts = _psutil.disk_partitions(all=False) or []
        except Exception:
            logger.debug("Disk partition enumeration failed", exc_info=True)
            return
        candidates: list[tuple[str, str, str, bool]] = []
        seen: set[str] = set()
        for part in parts:
            mount = getattr(part, "mountpoint", "") or ""
            if not mount or mount in seen:
                continue
            if getattr(part, "fstype", "") in _IGNORED_FSTYPES:
                continue
            if self._disk_mount_errors.get(mount, 0) >= _DISK_MOUNT_MAX_ERRORS:
                continue
            seen.add(mount)
            try:
                read_only = "ro" in str(getattr(part, "opts", "")).split(",")
            except Exception:
                read_only = False
            candidates.append((mount, getattr(part, "device", "") or "", getattr(part, "fstype", "") or "", read_only))
        try:
            disk_io = _psutil.disk_io_counters(perdisk=False)
        except Exception:
            disk_io = None
        io_snapshot = None
        if disk_io is not None:
            try:
                io_snapshot = {
                    "read_bytes": int(disk_io.read_bytes),
                    "write_bytes": int(disk_io.write_bytes),
                    "read_count": int(disk_io.read_count),
                    "write_count": int(disk_io.write_count),
                }
            except Exception:
                io_snapshot = None

        def _usage_for(mount: str) -> dict[str, Any] | None:
            try:
                usage = _psutil.disk_usage(mount)
            except Exception:
                return None
            total = float(usage.total) or 1.0
            return {
                "total_mb": _bytes_to_mb(usage.total),
                "used_mb": _bytes_to_mb(usage.used),
                "free_mb": _bytes_to_mb(usage.free),
                "percent": _safe_percent(usage.used / total * 100.0),
            }

        disks: list[dict[str, Any]] = []
        if candidates:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(candidates)), thread_name_prefix="alpha-disk") as pool:
                futures = {pool.submit(_usage_for, mount): (mount, device, fstype, read_only) for mount, device, fstype, read_only in candidates}
                for future, (mount, device, fstype, read_only) in futures.items():
                    try:
                        usage = future.result(timeout=_DISK_MOUNT_TIMEOUT_SECONDS)
                    except concurrent.futures.TimeoutError:
                        logger.debug("Disk usage timed out for %s; backing off", mount)
                        self._disk_mount_errors[mount] = self._disk_mount_errors.get(mount, 0) + 1
                        continue
                    except Exception:
                        self._disk_mount_errors[mount] = self._disk_mount_errors.get(mount, 0) + 1
                        continue
                    if usage is None:
                        self._disk_mount_errors[mount] = self._disk_mount_errors.get(mount, 0) + 1
                        continue
                    self._disk_mount_errors.pop(mount, None)
                    disks.append(
                        {
                            "mount": mount,
                            "device": device,
                            "filesystem": fstype,
                            "read_only": read_only,
                            "io": io_snapshot,
                            **usage,
                        }
                    )
        if disks:
            with self._lock:
                self._disk_cache = disks
                self._disk_cache_time = time.time()
        elif not self._disk_cache:
            fallback = self._stdlib_disk_fallback()
            if fallback:
                with self._lock:
                    self._disk_cache = fallback
                    self._disk_cache_time = time.time()

    def _gpus_for_tick(self, now: float) -> list[dict[str, Any]]:
        if (now - self._gpu_cache_time) < _GPU_REFRESH_TTL_SECONDS and self._gpu_cache_time > 0:
            with self._lock:
                return [dict(g) for g in self._gpu_cache]
        gpus = _query_nvidia_gpus() or _query_wmi_gpu_names()
        # Cross-vendor upgrade. The baseline probes leave Intel, AMD and
        # integrated GPUs with names but no counters, so they read 0% forever.
        # Prefer the advanced readout only when it genuinely says more.
        if _extras.enabled():
            try:
                advanced_gpu = _extras.sample_gpus()
                self._gpu_system_utilization = advanced_gpu.get("system_utilization_percent")
                if self._richer_gpu_set(advanced_gpu, gpus):
                    gpus = advanced_gpu.get("gpus") or []
            except Exception:
                logger.debug("Advanced GPU sampling failed", exc_info=True)
        with self._lock:
            self._gpu_cache = gpus
            self._gpu_cache_time = now
            return [dict(g) for g in gpus]

    @staticmethod
    def _richer_gpu_set(advanced: dict[str, Any], current: list[dict[str, Any]]) -> bool:
        """True when the advanced probe adds information the baseline lacks."""
        candidates = advanced.get("gpus") or []
        if not candidates:
            return False
        if not current:
            return True
        current_has_util = any(g.get("utilization_percent") is not None for g in current)
        return bool(advanced.get("utilization_supported") and not current_has_util)

    # -- alerts ----------------------------------------------------------

    @staticmethod
    def _empty_snapshot(reason: str) -> dict[str, Any]:
        return {
            "timestamp": time.time(),
            "health": "unknown",
            "memory": {
                "ram": {"total_mb": 0.0, "used_mb": 0.0, "available_mb": 0.0, "free_mb": 0.0, "percent": 0.0},
                "swap": {"total_mb": 0.0, "used_mb": 0.0, "free_mb": 0.0, "percent": 0.0},
            },
            "ram": {"total_mb": 0.0, "used_mb": 0.0, "available_mb": 0.0, "free_mb": 0.0, "percent": 0.0},
            "disks": [],
            "cpu": {
                "percent": 0.0,
                "cores": 0,
                "physical_cores": None,
                "frequency_mhz": None,
                "min_frequency_mhz": None,
                "max_frequency_mhz": None,
                "freq_mhz": None,
                "load": None,
                "per_core": [],
            },
            "gpus": [],
            "gpu": None,
            "gpu_system_utilization_percent": None,
            "network": {
                "bytes_sent": 0,
                "bytes_recv": 0,
                "packets_sent": 0,
                "packets_recv": 0,
                "errors_in": 0,
                "errors_out": 0,
                "drops_in": 0,
                "drops_out": 0,
                "upload_mbps": 0.0,
                "download_mbps": 0.0,
                "up_mbps": 0.0,
                "down_mbps": 0.0,
                "interfaces": [],
            },
            "internet": {"reachable": False, "rtt_ms": None, "host": _INTERNET_PROBE_HOST, "probes": []},
            "system": {
                "hostname": socket.gethostname(),
                "fqdn": None,
                "os": platform.system(),
                "os_version": platform.version(),
                "os_release": platform.release(),
                "architecture": platform.machine(),
                "kernel": None,
                "machine": platform.machine(),
                "processor": platform.processor(),
                "boot_time": None,
                "uptime_seconds": 0,
                "timezone": None,
            },
            "runtime": {
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "executable": sys.executable,
                "pid": os.getpid(),
                "parent_pid": os.getppid(),
                "process_cpu_percent": None,
                "process_memory_mb": None,
                "process_memory_percent": None,
                "process_threads": None,
            },
            "alpha": None,
            "battery": None,
            "sensors": None,
            "cpu_thermal": None,
            "storage": None,
            "disk_performance": None,
            "processes": None,
            "capabilities": {
                "psutil": _psutil is not None,
                "cpu": False,
                "memory": False,
                "disk": True,
                "disk_io": False,
                "network": False,
                "network_interfaces": False,
                "gpu": False,
                "nvidia_gpu": False,
                "sensors": False,
                "battery": False,
                "process_monitoring": False,
                "load_average": hasattr(os, "getloadavg"),
            },
            "psutil_available": _psutil is not None,
            "note": reason,
        }

    @staticmethod
    def _evaluate_alerts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        now = time.time()
        alerts: list[dict[str, Any]] = []
        memory = snapshot.get("memory") if isinstance(snapshot.get("memory"), dict) else {}
        ram = (memory or {}).get("ram", {}) if isinstance(memory, dict) else snapshot.get("ram", {})
        ram_pct = float(ram.get("percent") or 0.0)
        if ram_pct >= RAM_CRITICAL_PERCENT:
            alerts.append(
                {
                    "key": "ram_critical",
                    "severity": "critical",
                    "category": "memory",
                    "message": f"RAM critically high at {ram_pct:.1f}% - close workloads or add memory.",
                    "value": ram_pct,
                    "threshold": RAM_CRITICAL_PERCENT,
                    "timestamp": now,
                }
            )
        elif ram_pct >= RAM_HIGH_PERCENT:
            alerts.append(
                {
                    "key": "ram_high",
                    "severity": "warning",
                    "category": "memory",
                    "message": f"RAM usage high at {ram_pct:.1f}% of total.",
                    "value": ram_pct,
                    "threshold": RAM_HIGH_PERCENT,
                    "timestamp": now,
                }
            )
        for disk in snapshot.get("disks") or []:
            pct = float(disk.get("percent") or 0.0)
            mount = disk.get("mount") or "disk"
            if pct >= DISK_CRITICAL_PERCENT:
                alerts.append(
                    {
                        "key": f"disk_critical:{mount}",
                        "severity": "critical",
                        "category": "disk",
                        "message": f"Disk {mount} critically full at {pct:.1f}% - free space now.",
                        "value": pct,
                        "threshold": DISK_CRITICAL_PERCENT,
                        "timestamp": now,
                    }
                )
            elif pct >= DISK_LOW_PERCENT:
                alerts.append(
                    {
                        "key": f"disk_low:{mount}",
                        "severity": "warning",
                        "category": "disk",
                        "message": f"Disk {mount} running low at {pct:.1f}% used.",
                        "value": pct,
                        "threshold": DISK_LOW_PERCENT,
                        "timestamp": now,
                    }
                )
        cpu_pct = float((snapshot.get("cpu") or {}).get("percent") or 0.0)
        if cpu_pct >= CPU_HIGH_PERCENT:
            alerts.append(
                {
                    "key": "cpu_high",
                    "severity": "warning",
                    "category": "cpu",
                    "message": f"CPU load high at {cpu_pct:.1f}%.",
                    "value": cpu_pct,
                    "threshold": CPU_HIGH_PERCENT,
                    "timestamp": now,
                }
            )
        internet = snapshot.get("internet") or {}
        if not internet.get("reachable"):
            alerts.append(
                {
                    "key": "internet_down",
                    "severity": "critical",
                    "category": "network",
                    "message": "No internet connectivity (probe to 1.1.1.1:53 failed).",
                    "value": None,
                    "threshold": None,
                    "timestamp": now,
                }
            )
        elif (internet.get("packet_loss_percent") or 0.0) >= 25.0:
            alerts.append(
                {
                    "key": "internet_unstable",
                    "severity": "warning",
                    "category": "network",
                    "message": f"Internet is unstable ({internet.get('packet_loss_percent')}% packet loss).",
                    "value": internet.get("packet_loss_percent"),
                    "threshold": 25.0,
                    "timestamp": now,
                }
            )
        for gpu in snapshot.get("gpus") or []:
            if not isinstance(gpu, dict):
                continue
            temperature = gpu.get("temperature_c")
            if temperature is not None and temperature >= GPU_HOT_CELSIUS:
                alerts.append(
                    {
                        "key": "gpu_hot",
                        "severity": "warning",
                        "category": "gpu",
                        "message": f"GPU {gpu.get('name') or '?'} is running hot ({temperature} C).",
                        "value": temperature,
                        "threshold": GPU_HOT_CELSIUS,
                        "timestamp": now,
                    }
                )
        thermal = snapshot.get("cpu_thermal") or {}
        if thermal.get("throttling"):
            alerts.append(
                {
                    "key": "cpu_throttling",
                    "severity": "critical",
                    "category": "cpu",
                    "message": f"CPU is thermally throttling ({thermal.get('temperature_c')} C).",
                    "value": thermal.get("temperature_c"),
                    "threshold": 95.0,
                    "timestamp": now,
                }
            )
        for name in (snapshot.get("storage") or {}).get("unhealthy") or []:
            alerts.append(
                {
                    "key": "disk_unhealthy",
                    "severity": "critical",
                    "category": "disk",
                    "message": f"Physical disk '{name}' reports a non-healthy status.",
                    "value": None,
                    "threshold": None,
                    "timestamp": now,
                }
            )
        return alerts


# Process-wide singleton: one sampler per Gateway worker.
_service: SystemMonitorService | None = None
_service_lock = threading.Lock()


def get_system_monitor() -> SystemMonitorService:
    """Return the process singleton, creating (but not starting) it on demand."""
    global _service
    with _service_lock:
        if _service is None:
            _service = SystemMonitorService()
        return _service


def start_system_monitor(interval_seconds: float = DEFAULT_INTERVAL_SECONDS) -> SystemMonitorService:
    """Start the singleton sampler; safe to call repeatedly (idempotent)."""
    service = get_system_monitor()
    try:
        if service.interval_seconds != float(interval_seconds):
            service.interval_seconds = max(1.0, float(interval_seconds))
    except (TypeError, ValueError):
        pass
    service.start()
    return service


def stop_system_monitor() -> None:
    """Stop the singleton sampler if it is running (idempotent)."""
    global _service
    with _service_lock:
        service, _service = _service, None
    if service is not None:
        try:
            service.stop()
        except Exception:
            logger.exception("System monitor shutdown failed (non-fatal)")
