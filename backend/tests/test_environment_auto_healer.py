"""Unit tests for Autonomous Environment Auto-Healing and Dependency Reconciler."""

import pytest

from alpha.runtime.environment_auto_healer import (
    CANONICAL_MODULE_MAP,
    DependencyConstraint,
    DependencySATSolver,
    EnvironmentAutoHealer,
    diagnose_and_heal_environment,
)


def test_dependency_sat_solver_compatible():
    solver = DependencySATSolver()

    constraints = [
        DependencyConstraint("requests", ">=", "2.25.0"),
        DependencyConstraint("requests", "<=", "2.31.0"),
    ]
    is_sat, spec, msg = solver.solve_constraints("requests", constraints)
    assert is_sat is True
    assert ">=2.25.0" in spec
    assert "<=2.31.0" in spec


def test_dependency_sat_solver_conflict_relaxation():
    solver = DependencySATSolver()

    # Incompatible exact version pins
    constraints = [
        DependencyConstraint("urllib3", "==", "1.26.15"),
        DependencyConstraint("urllib3", "==", "2.0.2"),
    ]
    is_sat, spec, msg = solver.solve_constraints("urllib3", constraints)
    assert is_sat is False
    assert spec is not None  # Generates relaxed constraint candidate
    assert "Conflict" in msg


def test_diagnose_error_logs_module_resolution():
    healer = EnvironmentAutoHealer()

    sample_logs = """Traceback (most recent call last):
  File "main.py", line 2, in <module>
    import yaml
ModuleNotFoundError: No module named 'yaml'
  File "vision.py", line 1, in <module>
    import cv2
ModuleNotFoundError: No module named 'cv2'
ImportError: libgomp.so.1: cannot open shared object file: No such file or directory
"""
    issues = healer.diagnose_error_logs(sample_logs)

    assert len(issues) == 3
    # Check yaml -> PyYAML mapping
    yaml_issue = next(i for i in issues if i.affected_entity == "yaml")
    assert yaml_issue.recommended_package == "PyYAML"

    # Check cv2 -> opencv-python mapping
    cv2_issue = next(i for i in issues if i.affected_entity == "cv2")
    assert cv2_issue.recommended_package == "opencv-python"

    # Check shared library issue
    so_issue = next(i for i in issues if i.issue_type == "missing_shared_lib")
    assert "libgomp.so.1" in so_issue.affected_entity


def test_manifest_parsing_and_healing(tmp_path):
    # Setup temporary project with requirements.txt
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("pydantic>=2.0.0\npytest>=8.0.0\n", encoding="utf-8")

    healer = EnvironmentAutoHealer(project_root=tmp_path)
    res = healer.diagnose_and_heal(auto_heal=True)

    assert res["success"] is True
    assert len(res["manifest_reports"]) >= 1
    assert "virtualenv_health" in res
    assert res["virtualenv_health"]["python_executable"] != ""


def test_diagnose_and_heal_environment_tool(tmp_path):
    # Test tool invocation with simulated error log
    simulated_log = "ModuleNotFoundError: No module named 'dotenv'\n"

    res = diagnose_and_heal_environment.invoke({
        "project_root": str(tmp_path),
        "auto_heal": True,
        "error_logs": simulated_log,
    })

    assert isinstance(res, dict)
    assert res["success"] is True
    assert res["data"]["diagnostics_count"] >= 1

    # Verify auto-heal suggested python-dotenv
    actions = res["data"]["healed_actions"]
    assert any(a.get("package") == "python-dotenv" for a in actions)


def test_cargo_toml_parsing(tmp_path):
    cargo_file = tmp_path / "Cargo.toml"
    cargo_content = """[package]
name = "enterprise-app"
version = "0.1.0"
edition = "2021"

[dependencies]
serde = "1.0.193"
tokio = { version = "1.35.0", features = ["full"] }

[dev-dependencies]
tempfile = "3.8"
"""
    cargo_file.write_text(cargo_content, encoding="utf-8")

    healer = EnvironmentAutoHealer(project_root=tmp_path)
    constraints = healer.parse_cargo_toml(cargo_file)

    assert len(constraints) >= 3
    serde = next(c for c in constraints if c.package_name == "serde")
    assert serde.version == "1.0.193"

    tokio = next(c for c in constraints if c.package_name == "tokio")
    assert tokio.version == "1.35.0"

    res = healer.diagnose_and_heal(manifest_types=["Cargo.toml"])
    assert res["success"] is True
    assert len(res["manifest_reports"]) == 1
    assert res["manifest_reports"][0]["manifest_type"] == "Cargo.toml"


def test_pyproject_toml_parsing(tmp_path):
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_content = """[project]
name = "sample-proj"
version = "0.1.0"
dependencies = [
    "httpx>=0.24.0",
    "pydantic>=2.5.0",
]

[project.optional-dependencies]
test = [
    "pytest>=8.0.0",
]
"""
    pyproject_file.write_text(pyproject_content, encoding="utf-8")

    healer = EnvironmentAutoHealer(project_root=tmp_path)
    constraints = healer.parse_pyproject_toml(pyproject_file)

    assert len(constraints) >= 3
    assert any(c.package_name == "httpx" and c.version == "0.24.0" for c in constraints)
    assert any(c.package_name == "pydantic" and c.version == "2.5.0" for c in constraints)
    assert any(c.package_name == "pytest" and c.version == "8.0.0" for c in constraints)
