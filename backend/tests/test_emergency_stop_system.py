from pathlib import Path

from agent_workspace.runtime.estop import EmergencyStopManager
from agent_workspace.tools.builtins.estop_tool import emergency_stop_manage


def test_estop_lifecycle(tmp_path: Path):
    manager = EmergencyStopManager(root_dir=tmp_path)
    assert manager.is_engaged() is False

    # 1. Engage
    sentinel = manager.engage(reason="Security Incident Detected")
    assert sentinel.exists()
    assert manager.is_engaged() is True

    status = manager.get_status()
    assert status["is_engaged"] is True
    assert status["reason"] == "Security Incident Detected"

    # 2. Disengage
    res = manager.disengage()
    assert res is True
    assert manager.is_engaged() is False
    assert not sentinel.exists()


def test_emergency_stop_tool(tmp_path: Path, monkeypatch):
    manager = EmergencyStopManager(root_dir=tmp_path)
    monkeypatch.setattr("agent_workspace.runtime.estop.get_estop_manager", lambda: manager)
    monkeypatch.setattr("agent_workspace.tools.builtins.estop_tool.get_estop_manager", lambda: manager)

    # Check status
    stat_out = emergency_stop_manage.invoke({"action": "status"})
    assert '"is_engaged": false' in stat_out

    # Engage
    eng_out = emergency_stop_manage.invoke({"action": "engage", "reason": "Operator pause"})
    assert "ESTOP successfully engaged" in eng_out
    assert manager.is_engaged() is True

    # Disengage
    dis_out = emergency_stop_manage.invoke({"action": "disengage"})
    assert "ESTOP successfully disengaged" in dis_out
    assert manager.is_engaged() is False
