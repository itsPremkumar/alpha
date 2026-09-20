"""Advanced host telemetry: cross-vendor GPU, internet quality, ROM health, thermals."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from app.gateway import system_monitor_extras as extras


@pytest.fixture(autouse=True)
def _clean_caches():
    extras.reset_caches()
    yield
    extras.reset_caches()


@pytest.fixture()
def _no_external(monkeypatch):
    """Keep tests hermetic: no real sockets, subprocesses or HTTP."""
    monkeypatch.setattr(extras, "_tcp_rtt", lambda host, port: (True, 10.0))
    monkeypatch.setattr(extras, "_dns_latency_ms", lambda host, timeout=2.0: 4.0)
    monkeypatch.setattr(extras, "_public_ip", lambda: None)
    monkeypatch.setattr(extras, "_nvidia_gpus", lambda: [])
    monkeypatch.setattr(extras, "_rocm_gpus", lambda: [])
    monkeypatch.setattr(extras, "_windows_video_controllers", lambda: [])
    monkeypatch.setattr(extras, "_windows_gpu_utilization", lambda: None)
    monkeypatch.setattr(extras, "_windows_physical_disks", lambda: [])
    monkeypatch.setattr(extras, "_smartctl_disks", lambda: [])


# -- helpers ---------------------------------------------------------------


def test_opt_float_rejects_placeholders():
    assert extras._opt_float("N/A") is None
    assert extras._opt_float("[N/A]") is None
    assert extras._opt_float("") is None
    assert extras._opt_float("42.5") == 42.5


def test_safe_percent_clamps_and_rejects_none():
    assert extras._safe_percent(150) == 100.0
    assert extras._safe_percent(-5) == 0.0
    assert extras._safe_percent(None) is None


def test_connection_classification():
    assert extras._classify_connection(None, None, 0) == "offline"
    assert extras._classify_connection(20.0, 0.0, 3) == "excellent"
    assert extras._classify_connection(100.0, 0.0, 3) == "good"
    assert extras._classify_connection(300.0, 0.0, 3) == "fair"
    assert extras._classify_connection(900.0, 0.0, 3) == "poor"
    assert extras._classify_connection(20.0, 40.0, 3) == "unstable"


def test_enabled_respects_env(monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ADVANCED_MONITOR", "0")
    assert extras.enabled() is False
    monkeypatch.setenv("AGENT_WORKSPACE_ADVANCED_MONITOR", "off")
    assert extras.enabled() is False
    monkeypatch.setenv("AGENT_WORKSPACE_ADVANCED_MONITOR", "1")
    assert extras.enabled() is True


# -- GPU -------------------------------------------------------------------


def test_gpu_prefers_nvidia_readout(monkeypatch, _no_external):
    monkeypatch.setattr(
        extras,
        "_nvidia_gpus",
        lambda: [{"index": 0, "name": "RTX 4070", "vendor": "NVIDIA", "utilization_percent": 42.0, "source": "nvidia-smi"}],
    )
    out = extras.sample_gpus(force=True)
    assert out["source"] == "nvidia-smi"
    assert out["count"] == 1
    assert out["utilization_supported"] is True


def test_gpu_falls_back_to_adapter_identity_with_system_utilization(monkeypatch, _no_external):
    """The whole point: Intel/AMD GPUs get utilization instead of a bare name."""
    monkeypatch.setattr(
        extras,
        "_windows_video_controllers",
        lambda: [
            {
                "index": 0,
                "name": "Intel(R) Iris(R) Xe Graphics",
                "vendor": "Intel",
                "utilization_percent": None,
                "memory_used_mb": None,
                "memory_total_mb": 2048.0,
                "memory_percent": None,
                "temperature_c": None,
                "power_watts": None,
                "power_limit_watts": None,
                "driver_version": "31.0.101",
                "utilization_scope": None,
                "source": "cim",
            }
        ],
    )
    monkeypatch.setattr(extras, "_windows_gpu_utilization", lambda: 37.5)

    out = extras.sample_gpus(force=True)
    gpu = out["gpus"][0]
    assert out["source"] == "cim"
    assert gpu["utilization_percent"] == 37.5
    # Honesty about scope: engine counters cannot be attributed to one adapter.
    assert gpu["utilization_scope"] == "system"
    assert out["utilization_supported"] is True


def test_gpu_does_not_attribute_system_utilization_to_one_of_several(monkeypatch, _no_external):
    """With several adapters, no single one may claim the host-wide figure."""

    def two_adapters() -> list[dict[str, Any]]:
        return [{"index": i, "name": f"Adapter {i}", "vendor": "", "utilization_percent": None, "source": "cim"} for i in range(2)]

    monkeypatch.setattr(extras, "_windows_video_controllers", two_adapters)
    monkeypatch.setattr(extras, "_windows_gpu_utilization", lambda: 12.0)

    out = extras.sample_gpus(force=True)
    assert all(g["utilization_percent"] is None for g in out["gpus"])
    assert all(g["utilization_scope"] == "unavailable" for g in out["gpus"])
    assert out["system_utilization_percent"] == 12.0
    assert out["utilization_supported"] is True


def test_gpu_absent_is_reported_cleanly(_no_external):
    out = extras.sample_gpus(force=True)
    assert out["gpus"] == []
    assert out["count"] == 0
    assert out["source"] == "none"
    assert out["utilization_supported"] is False
    assert out["system_utilization_percent"] is None


def test_service_surfaces_system_gpu_utilization(monkeypatch, _no_external):
    """End-to-end: the snapshot must expose the host-wide figure."""
    from app.gateway.system_monitor_service import SystemMonitorService

    def two_adapters() -> list[dict[str, Any]]:
        return [{"index": i, "name": f"Adapter {i}", "vendor": "", "utilization_percent": None, "source": "cim"} for i in range(2)]

    monkeypatch.setattr(extras, "_windows_video_controllers", two_adapters)
    monkeypatch.setattr(extras, "_windows_gpu_utilization", lambda: 44.0)

    snapshot = SystemMonitorService()._sample_once()
    assert snapshot["gpu_system_utilization_percent"] == 44.0
    assert snapshot["capabilities"]["gpu_utilization"] is True


def test_gpu_result_is_cached(monkeypatch, _no_external):
    calls = {"n": 0}

    def counting() -> list[dict[str, Any]]:
        calls["n"] += 1
        return []

    monkeypatch.setattr(extras, "_nvidia_gpus", counting)
    extras.sample_gpus(force=True)
    after_force = calls["n"]
    extras.sample_gpus()
    assert calls["n"] == after_force


# -- internet --------------------------------------------------------------


def test_internet_quality_aggregates_probes(monkeypatch, _no_external):
    sequence = iter([(True, 20.0), (True, 30.0), (False, None), (False, None), (True, 60.0), (True, 60.0)])
    monkeypatch.setattr(extras, "_tcp_rtt", lambda host, port: next(sequence))
    monkeypatch.setattr(extras, "_public_ip", lambda: "203.0.113.9")

    out = extras.sample_internet_quality(force=True)
    assert out["reachable"] is True
    assert out["reachable_targets"] == 2
    assert out["total_targets"] == len(extras._INTERNET_PROBE_TARGETS)
    assert out["packet_loss_percent"] == pytest.approx(33.3, abs=0.1)
    assert out["rtt_ms"] == 20.0
    assert out["avg_rtt_ms"] == pytest.approx(42.5, abs=0.1)
    assert out["public_ip"] == "203.0.113.9"
    assert out["dns_latency_ms"] == 4.0
    assert len(out["probes"]) == 3
    # Every legacy key the UI already reads must survive.
    for key in ("reachable", "rtt_ms", "probes"):
        assert key in out


def test_internet_offline_when_all_targets_fail(monkeypatch, _no_external):
    monkeypatch.setattr(extras, "_tcp_rtt", lambda host, port: (False, None))
    monkeypatch.setattr(extras, "_dns_latency_ms", lambda host, timeout=2.0: None)

    out = extras.sample_internet_quality(force=True)
    assert out["reachable"] is False
    assert out["reachable_targets"] == 0
    assert out["quality"] == "offline"
    assert out["packet_loss_percent"] == 100.0


def test_internet_speedtest_is_opt_in(monkeypatch, _no_external):
    def forbidden(url: str = "", max_bytes: int = 0) -> float:
        raise AssertionError("throughput probe must not run unless requested")

    monkeypatch.setattr(extras, "measure_download_mbps", forbidden)
    out = extras.sample_internet_quality(force=True)
    assert "download_mbps" not in out


def test_internet_quality_is_cached(monkeypatch, _no_external):
    calls = {"n": 0}

    def counting(host: str, port: int) -> tuple[bool, float | None]:
        calls["n"] += 1
        return True, 10.0

    monkeypatch.setattr(extras, "_tcp_rtt", counting)
    extras.sample_internet_quality(force=True)
    after_force = calls["n"]
    extras.sample_internet_quality()
    assert calls["n"] == after_force


# -- storage / ROM ---------------------------------------------------------


def test_storage_health_reports_healthy_disks(monkeypatch, _no_external):
    monkeypatch.setattr(
        extras,
        "_windows_physical_disks",
        lambda: [
            {"name": "NVMe SSD", "health": "Healthy", "healthy": True, "size_bytes": 512_000_000_000, "source": "powershell"},
            {"name": "Backup HDD", "health": "Healthy", "healthy": True, "size_bytes": 1_000_000_000, "source": "powershell"},
        ],
    )
    out = extras.sample_storage_health(force=True)
    assert out["count"] == 2
    assert out["unhealthy"] == []
    assert out["total_bytes"] == 513_000_000_000
    assert out["supported"] is True


def test_storage_health_flags_unhealthy_disks(monkeypatch, _no_external):
    monkeypatch.setattr(
        extras,
        "_smartctl_disks",
        lambda: [{"name": "/dev/sda", "health": "At risk", "healthy": False, "size_bytes": None, "source": "smartctl"}],
    )
    out = extras.sample_storage_health(force=True)
    assert out["unhealthy"] == ["/dev/sda"]
    assert out["total_bytes"] is None


def test_disk_performance_derives_iops_and_throughput():
    previous = {"disks": [{"mount": "C:", "read_bytes": 0, "write_bytes": 0, "read_count": 0, "write_count": 0}]}
    current = [{"mount": "C:", "read_bytes": 2 * 1024 * 1024, "write_bytes": 1024 * 1024, "read_count": 10, "write_count": 10}]
    out = extras.sample_disk_performance(current, previous=previous, elapsed=2.0)
    assert out["read_mbps"] == 1.0
    assert out["write_mbps"] == 0.5
    assert out["iops"] == 10.0
    assert out["disks"][0]["name"] == "C:"


def test_disk_performance_without_baseline_is_none():
    out = extras.sample_disk_performance([{"mount": "C:"}], previous=None, elapsed=2.0)
    assert out["read_mbps"] is None
    assert out["iops"] is None


# -- thermals --------------------------------------------------------------


class _FakeTemp:
    current = 55.0
    label = "Package id 0"
    high = None
    critical = None


class _FakePsutil:
    POWER_TIME_UNLIMITED = -1
    POWER_TIME_UNKNOWN = -2

    @staticmethod
    def sensors_temperatures() -> dict[str, list[Any]]:
        return {"coretemp": [_FakeTemp()]}


def test_cpu_thermal_reads_package_temperature(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", _FakePsutil)
    out = extras.sample_cpu_thermal(force=True)
    assert out["temperature_c"] == 55.0
    assert out["source"] == "psutil"
    assert out["throttling"] is False
    assert out["supported"] is True


def test_cpu_thermal_flags_throttling(monkeypatch):
    class Hot(_FakePsutil):
        @staticmethod
        def sensors_temperatures() -> dict[str, list[Any]]:
            class _Hot:
                current = 99.0
                label = "Package id 0"
                high = None
                critical = None

            return {"coretemp": [_Hot()]}

    monkeypatch.setitem(sys.modules, "psutil", Hot)
    out = extras.sample_cpu_thermal(force=True)
    assert out["temperature_c"] == 99.0
    assert out["throttling"] is True


def test_cpu_thermal_degrades_when_psutil_is_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(extras, "_IS_WINDOWS", False)
    out = extras.sample_cpu_thermal(force=True)
    assert out["temperature_c"] is None
    assert out["supported"] is False
    assert out["throttling"] is False
