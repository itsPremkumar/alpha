"""Continuous resource monitor: stdlib-only readings plus autonomy advice.

The Gateway already exposes a snapshot (`GET /api/ops/resources`); this is
the harness-side evaluator that turns readings into decisions: how many
workers may run, which model class fits, and when to stand down.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from typing import Any, Literal

Recommendation = Literal["scale_up", "hold", "scale_down", "stand_down"]


@dataclass
class ResourceReading:
    cpu_percent: float | None = None
    mem_total_mb: float | None = None
    mem_available_mb: float | None = None
    disk_free_gb: float | None = None
    load_1m: float | None = None
    cpu_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    cpu_pct = round(min(100.0, (load_1m / cpu_count * 100.0)), 1) if load_1m is not None else None
    return ResourceReading(cpu_percent=cpu_pct, mem_total_mb=mem_total, mem_available_mb=mem_avail, disk_free_gb=disk_free, load_1m=load_1m, cpu_count=cpu_count)


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
            reasons.append("cpu load above 85%")
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
