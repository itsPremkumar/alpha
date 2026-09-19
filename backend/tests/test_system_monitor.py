"""Unit tests for the host system monitor (service + router)."""

import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.gateway.routers.system_monitor as router_module
import app.gateway.system_monitor_service as service_module
from app.gateway.routers import system_monitor
from app.gateway.system_monitor_service import SystemMonitorService


def _sample(*, ram_percent=42.0, disk_percent=50.0, internet=True):
    ram = {
        "total_mb": 16384.0,
        "used_mb": 6881.0,
        "available_mb": 9503.0,
        "free_mb": 9503.0,
        "percent": ram_percent,
    }
    return {
        "timestamp": time.time(),
        "health": "healthy",
        "memory": {
            "ram": ram,
            "swap": {"total_mb": 4096.0, "used_mb": 100.0, "free_mb": 3996.0, "percent": 2.4},
        },
        "ram": ram,
        "disks": [
            {
                "mount": "C:\\",
                "device": "C:",
                "filesystem": "NTFS",
                "total_mb": 500000.0,
                "used_mb": 250000.0,
                "free_mb": 250000.0,
                "percent": disk_percent,
                "read_only": False,
                "io": None,
            }
        ],
        "cpu": {
            "percent": 12.5,
            "cores": 8,
            "physical_cores": 4,
            "frequency_mhz": 3200.0,
            "min_frequency_mhz": 800.0,
            "max_frequency_mhz": 4200.0,
            "freq_mhz": 3200.0,
            "load": None,
            "per_core": [],
        },
        "gpus": [],
        "gpu": None,
        "network": {
            "bytes_sent": 1000,
            "bytes_recv": 2000,
            "packets_sent": 10,
            "packets_recv": 20,
            "errors_in": 0,
            "errors_out": 0,
            "drops_in": 0,
            "drops_out": 0,
            "upload_mbps": 1.5,
            "download_mbps": 9.25,
            "up_mbps": 1.5,
            "down_mbps": 9.25,
            "interfaces": [],
        },
        "internet": {
            "reachable": internet,
            "rtt_ms": 11.2 if internet else None,
            "host": "1.1.1.1",
            "probes": [],
        },
        "system": {
            "hostname": "alpha",
            "fqdn": "alpha.local",
            "os": "Windows",
            "os_version": "10",
            "os_release": "10",
            "architecture": "AMD64",
            "kernel": None,
            "machine": "AMD64",
            "processor": "Intel64",
            "boot_time": time.time() - 3600,
            "uptime_seconds": 3600,
            "timezone": "UTC",
        },
        "runtime": {
            "python_version": "3.12.0",
            "python_implementation": "CPython",
            "executable": "python",
            "pid": 1234,
            "parent_pid": 1,
            "process_cpu_percent": 1.0,
            "process_memory_mb": 100.0,
            "process_memory_percent": 0.6,
            "process_threads": 8,
        },
        "alpha": {"service_name": "alpha", "pid": 1234},
        "battery": None,
        "sensors": {"temperatures": [], "fans": [], "battery": []},
        "processes": None,
        "capabilities": {
            "psutil": True,
            "cpu": True,
            "memory": True,
            "disk": True,
            "disk_io": True,
            "network": True,
            "network_interfaces": True,
            "gpu": False,
            "nvidia_gpu": False,
            "sensors": True,
            "battery": True,
            "process_monitoring": True,
            "load_average": False,
        },
        "psutil_available": True,
    }


def _compact(timestamp: float, ram_percent: float = 42.0) -> dict:
    return {
        "timestamp": timestamp,
        "ram": {"percent": ram_percent},
        "swap": {"percent": 2.0},
        "cpu": {"percent": 12.0},
        "network": {"upload_mbps": 1.0, "download_mbps": 9.0},
        "gpu": None,
        "internet": {"reachable": True, "rtt_ms": 11.0},
        "disks": [{"mount": "C:\\", "percent": 50.0}],
    }


def test_alert_thresholds_ram() -> None:
    assert SystemMonitorService._evaluate_alerts(_sample(ram_percent=50.0)) == []
    high = SystemMonitorService._evaluate_alerts(_sample(ram_percent=85.0))
    assert [a["key"] for a in high] == ["ram_high"]
    assert high[0]["severity"] == "warning"
    assert high[0]["category"] == "memory"
    critical = SystemMonitorService._evaluate_alerts(_sample(ram_percent=95.0))
    assert [a["key"] for a in critical] == ["ram_critical"]
    assert critical[0]["severity"] == "critical"


def test_alert_thresholds_disk_cpu_and_internet() -> None:
    low = SystemMonitorService._evaluate_alerts(_sample(disk_percent=92.0))
    assert any(a["key"] == "disk_low:C:\\" and a["severity"] == "warning" for a in low)
    critical = SystemMonitorService._evaluate_alerts(_sample(disk_percent=97.0))
    assert any(a["key"] == "disk_critical:C:\\" and a["severity"] == "critical" for a in critical)
    offline = SystemMonitorService._evaluate_alerts(_sample(internet=False))
    assert any(a["key"] == "internet_down" and a["severity"] == "critical" for a in offline)


def test_health_reflects_worst_alert() -> None:
    service = SystemMonitorService()
    service._latest = _sample(ram_percent=50.0)
    service._alerts = []
    assert service.get_vitals()["health"] in ("healthy", "unknown")
    service._alerts = SystemMonitorService._evaluate_alerts(_sample(ram_percent=95.0))
    service._latest = _sample(ram_percent=95.0)
    service._latest["health"] = "critical"
    assert service.get_vitals()["health"] == "critical"


def test_service_collects_without_psutil(monkeypatch) -> None:
    monkeypatch.setattr(service_module, "_psutil", None)
    monkeypatch.setattr(service_module, "_check_internet", lambda: {"reachable": True, "rtt_ms": 1.0, "host": "1.1.1.1", "probes": []})
    monkeypatch.setattr(service_module, "_query_nvidia_gpus", lambda: [])
    monkeypatch.setattr(service_module, "_query_wmi_gpu_names", lambda: [])
    service = SystemMonitorService()
    snapshot = service._collect()
    assert set(snapshot) >= {"timestamp", "memory", "disks", "cpu", "gpus", "network", "internet", "system"}
    assert snapshot["memory"]["ram"]["percent"] == 0.0
    # get_vitals degrades to an empty snapshot before the first sample.
    vitals = service.get_vitals()
    assert vitals["memory"]["ram"]["percent"] == 0.0
    assert vitals["health"] == "unknown"


def test_service_history_window_filters_old_samples() -> None:
    service = SystemMonitorService(history_limit=10)
    now = time.time()
    service._history.append(_compact(now - 3600))  # one hour old
    service._history.append(_compact(now))  # fresh
    assert len(service.get_history(minutes=5)) == 1
    assert len(service.get_history(minutes=90)) == 2


def test_compact_history_point_shape() -> None:
    point = SystemMonitorService._compact_history_point(_sample())
    assert point["ram"]["percent"] == 42.0
    assert point["network"]["download_mbps"] == 9.25
    assert point["disks"] == [{"mount": "C:\\", "percent": 50.0}]


def test_get_processes_serves_cache_and_slices(monkeypatch) -> None:
    rows = [
        {"pid": i, "name": f"proc-{i}", "status": "running", "cpu_percent": float(i), "memory_percent": 1.0, "memory_mb": 10.0, "threads": 1, "create_time": None}
        for i in range(5)
    ]
    calls = {"n": 0}

    def _fake_scan(self, *, budget_seconds):
        calls["n"] += 1
        return {"total": 5, "running": 5, "sleeping": 0, "stopped": 0, "top_cpu": list(rows), "top_memory": list(rows)}

    monkeypatch.setattr(SystemMonitorService, "_scan_processes", _fake_scan)
    service = SystemMonitorService()
    first = service.get_processes(limit=3)
    second = service.get_processes(limit=2)
    assert calls["n"] == 1  # second call served from cache, no rescan
    assert [p["pid"] for p in first["top_cpu"]] == [0, 1, 2]
    assert [p["pid"] for p in second["top_cpu"]] == [0, 1]
    assert first["total"] == 5


def _client(monkeypatch) -> TestClient:
    now = time.time()
    fake = SimpleNamespace(
        get_vitals=lambda: {**_sample(), "alerts": []},
        get_history=lambda minutes=5.0: [_compact(now)],
        get_alerts=lambda: [],
        get_processes=lambda limit=20, sort="cpu": {
            "total": 1,
            "running": 1,
            "sleeping": 0,
            "stopped": 0,
            "top_cpu": [{"pid": 1, "name": "alpha", "cpu_percent": 5.0, "memory_percent": 1.0, "memory_mb": 10.0}],
            "top_memory": [],
        },
        get_network_interfaces=lambda: [{"name": "Ethernet", "is_up": True}],
        get_capabilities=lambda: _sample()["capabilities"],
    )
    monkeypatch.setattr(router_module, "get_system_monitor", lambda: fake)
    app = FastAPI()
    app.include_router(system_monitor.router)
    return TestClient(app)


def test_vitals_endpoint_returns_ram_primary(monkeypatch) -> None:
    with _client(monkeypatch) as client:
        response = client.get("/api/system/vitals")
    assert response.status_code == 200
    payload = response.json()
    assert payload["memory"]["ram"]["total_mb"] == 16384.0
    assert payload["memory"]["ram"]["percent"] == 42.0
    assert payload["disks"][0]["mount"] == "C:\\"
    assert payload["cpu"]["cores"] == 8
    assert payload["gpus"] == []
    assert payload["internet"]["reachable"] is True
    assert payload["system"]["hostname"] == "alpha"
    assert isinstance(payload["alerts"], list)


def test_history_endpoint_defaults_to_five_minutes(monkeypatch) -> None:
    with _client(monkeypatch) as client:
        response = client.get("/api/system/history")
    assert response.status_code == 200
    payload = response.json()
    assert payload["minutes"] == 5.0
    assert len(payload["points"]) == 1
    assert payload["points"][0]["ram_percent"] == 42.0


def test_alerts_endpoint(monkeypatch) -> None:
    with _client(monkeypatch) as client:
        response = client.get("/api/system/alerts")
    assert response.status_code == 200
    assert response.json()["alerts"] == []


def test_processes_endpoint(monkeypatch) -> None:
    with _client(monkeypatch) as client:
        response = client.get("/api/system/processes?limit=5&sort=cpu")
    assert response.status_code == 200
    payload = response.json()
    assert payload["processes"]["total"] == 1
    assert payload["processes"]["top_cpu"][0]["name"] == "alpha"
    # Command lines must never leak through the API.
    assert "cmdline" not in payload["processes"]["top_cpu"][0]


def test_network_interfaces_endpoint(monkeypatch) -> None:
    with _client(monkeypatch) as client:
        response = client.get("/api/system/network/interfaces")
    assert response.status_code == 200
    assert response.json()["interfaces"][0]["name"] == "Ethernet"


def test_capabilities_endpoint(monkeypatch) -> None:
    with _client(monkeypatch) as client:
        response = client.get("/api/system/capabilities")
    assert response.status_code == 200
    assert response.json()["capabilities"]["memory"] is True
