"""Continuous resource monitor: real CPU utilization plus autonomy advice.

The Gateway already exposes a snapshot (`GET /api/ops/resources`); this is
the harness-side evaluator that turns readings into decisions: how many
workers may run, which model class fits, and when to stand down.

CPU utilization is measured with ``psutil.cpu_percent(interval=None)``
(cross-platform, including Windows), which compares against psutil's
module-level cached sample from the previous call — i.e. a real delta, never
a load average scaled into a percent. The 1-minute load average is still
reported separately as ``load_1m``. Where the value cannot be measured (no
psutil, unsupported host, or the first call that only establishes the
baseline), ``cpu_percent`` is ``None`` and ``cpu_percent_basis`` discloses
why. Memory/disk/load readings remain stdlib-only.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from typing import Any, Literal

Recommendation = Literal["scale_up", "hold", "scale_down", "stand_down"]

# Disclosure strings for the cpu_percent field (see _read_cpu_percent).
_CPU_BASIS_WARMING = "warming up: first psutil sample only establishes the baseline; no utilization reading yet"
_CPU_BASIS_NO_PSUTIL = "unavailable: psutil is not installed; CPU utilization cannot be measured on this host"
_CPU_BASIS_MEASURE_FAILED = "unavailable: psutil.cpu_percent failed on this host"
_CPU_BASIS_REAL = "psutil.cpu_percent: real CPU utilization since the previous sample"

# Module-level: whether this process has taken (and discarded) the priming sample.
_CPU_PRIMED = False


@dataclass
class ResourceReading:
    cpu_percent: float | None = None
    cpu_percent_basis: str | None = None
    mem_total_mb: float | None = None
    mem_available_mb: float | None = None
    disk_free_gb: float | None = None
    load_1m: float | None = None
    cpu_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_cpu_percent() -> tuple[float | None, str]:
    """Return (real CPU utilization percent or None, basis disclosure).

    ``None`` means "not measured" — warm-up, missing psutil, or a host where
    the call fails. Load-average math is NEVER used as a percent here.
    """
    global _CPU_PRIMED
    try:
        import psutil
    except Exception:
        return None, _CPU_BASIS_NO_PSUTIL
    try:
        pct = float(psutil.cpu_percent(interval=None))
    except Exception:
        return None, _CPU_BASIS_MEASURE_FAILED
    if not _CPU_PRIMED:
        # psutil's first sample compares against process start, not a prior
        # interval: it is meaningless. Prime it and disclose the warm-up.
        _CPU_PRIMED = True
        return None, _CPU_BASIS_WARMING
    return round(min(100.0, max(0.0, pct)), 1), _CPU_BASIS_REAL


def read_resources() -> ResourceReading:
    cpu_count = os.cpu_count() or 1
    load_1m: float | None = None
    try:
        load_1m = os.getloadavg()[0]
    except (AttributeError, OSError):
        load_1m = None
    mem_total = mem_avail = None
    try:
        if os.path.exists("/proc/meminfo"):
            info: dict[str, float] = {}
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0].endswith(":"):
                        info[parts[0][:-1]] = float(parts[1]) / 1024.0
            mem_total = info.get("MemTotal")
            mem_avail = info.get("MemAvailable", info.get("MemFree"))
    except OSError:
        mem_total = mem_avail = None
    try:
        disk_free = shutil.disk_usage(os.sep).free / (1024.0**3)
    except OSError:
        disk_free = None
    cpu_pct, cpu_basis = _read_cpu_percent()
    return ResourceReading(cpu_percent=cpu_pct, cpu_percent_basis=cpu_basis, mem_total_mb=mem_total, mem_available_mb=mem_avail, disk_free_gb=disk_free, load_1m=load_1m, cpu_count=cpu_count)


@dataclass
class AutonomyAdvice:
    recommendation: Recommendation
    max_workers: int
    model_class: str
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def advise(reading: ResourceReading, *, current_workers: int = 1) -> AutonomyAdvice:
    reasons: list[str] = []
    max_workers = 4
    model_class = "standard"
    recommendation: Recommendation = "hold"

    mem_low = reading.mem_available_mb is not None and reading.mem_total_mb and reading.mem_available_mb / reading.mem_total_mb < 0.15
    cpu_hot = reading.cpu_percent is not None and reading.cpu_percent > 85.0
    disk_critical = reading.disk_free_gb is not None and reading.disk_free_gb < 1.0

    if disk_critical:
        return AutonomyAdvice(recommendation="stand_down", max_workers=0, model_class="none", reasons=["disk free below 1 GiB; refuse new work until space is reclaimed"])
    if mem_low or cpu_hot:
        if mem_low:
            reasons.append("available memory below 15%")
        if cpu_hot:
            reasons.append("cpu utilization above 85%")
        return AutonomyAdvice(recommendation="scale_down", max_workers=1, model_class="light", reasons=reasons)

    mem_ok = reading.mem_available_mb is not None and reading.mem_total_mb and reading.mem_available_mb / reading.mem_total_mb > 0.5
    cpu_idle = reading.cpu_percent is not None and reading.cpu_percent < 40.0
    if (mem_ok or reading.mem_available_mb is None) and (cpu_idle or reading.cpu_percent is None):
        if current_workers < 4:
            recommendation = "scale_up"
            reasons.append("headroom available for more parallel workers")
        max_workers = 8 if (reading.cpu_count or 1) >= 8 else 4
        model_class = "strong"
    else:
        reasons.append("within normal operating band")
    return AutonomyAdvice(recommendation=recommendation, max_workers=max_workers, model_class=model_class, reasons=reasons)
