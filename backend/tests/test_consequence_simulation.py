"""Tests for Consequence Simulator and Affordance Modeler."""

import json

from agent_workspace.consequence.affordance import AffordanceModel, EnvironmentAffordances
from agent_workspace.consequence.simulator import ConsequenceSimulator, SimulationReport
from agent_workspace.tools.builtins.consequence_tool import simulate_consequences


def test_affordance_model_probe(tmp_path):
    model = AffordanceModel()
    affordances = model.probe(str(tmp_path))

    assert isinstance(affordances, EnvironmentAffordances)
    assert affordances.is_writable is True
    assert "git" in affordances.available_binaries


def test_consequence_simulator_package_manifest(tmp_path):
    sim = ConsequenceSimulator()

    # Simulation on modifying pyproject.toml
    report = sim.simulate(
        action_name="write_to_file",
        parameters={"TargetFile": str(tmp_path / "pyproject.toml")},
        workspace_path=str(tmp_path),
    )
    assert isinstance(report, SimulationReport)
    assert report.blast_radius == "PACKAGE_DEPENDENCY"
    assert any("lockfile" in c for c in report.projected_state_changes)


def test_consequence_simulator_server_command(tmp_path):
    sim = ConsequenceSimulator()

    # Simulation on running foreground uvicorn server
    report = sim.simulate(
        action_name="run_command",
        parameters={"command": "uvicorn app:main --port 8000"},
        workspace_path=str(tmp_path),
    )
    assert any("foreground listening server" in w for w in report.warnings)


def test_simulate_consequences_tool(tmp_path):
    res_str = simulate_consequences.invoke({
        "action_name": "replace_file_content",
        "target_file": "alembic/versions/001_migration.py",
        "workspace_path": str(tmp_path),
    })
    data = json.loads(res_str)
    assert "blast_radius" in data
    assert "warnings" in data
    assert any("migration" in w.lower() for w in data["warnings"])
