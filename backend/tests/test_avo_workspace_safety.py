import json
import subprocess
from types import SimpleNamespace

import pytest

from alpha.avo.persistence import AVOPersistenceManager
from alpha.avo.workspace_runner import WorkspaceAVORunner
from alpha.tools.builtins import code_agentic_core


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "calc.py"
    target.write_text("baseline\n", encoding="utf-8")
    monkeypatch.setattr(code_agentic_core, "_ACTIVE_CHECKPOINTS", {})
    return root, target, WorkspaceAVORunner(root_path=root)


def run(runner, **kwargs):
    return runner.run_workspace_variation("calc.py", "candidate\n", "hypothesis", "change", test_command="python -V", **kwargs)


@pytest.mark.parametrize("response", ["invalid", "{}", '{"status":"error"}', '{"status":"created","checkpoint_id":"x","captured_files":[]}'])
def test_checkpoint_failure_prevents_write(workspace, monkeypatch, response):
    _, target, runner = workspace
    monkeypatch.setattr(code_agentic_core, "manage_code_checkpoint", SimpleNamespace(invoke=lambda _: response))
    result = run(runner)
    assert result["success"] is False
    assert result["rolled_back"] is False
    assert target.read_text(encoding="utf-8") == "baseline\n"


def test_outside_target_rejected(workspace):
    root, _, runner = workspace
    outside = root.parent / "outside.py"
    outside.write_text("keep", encoding="utf-8")
    for path in (str(outside), "../outside.py"):
        result = runner.run_workspace_variation(path, "change", "h", "m")
        assert result["success"] is False
        assert result["rolled_back"] is False
    assert outside.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("response", ["invalid", '{"status":"error"}', '{"status":"rolled_back","checkpoint_id":"wrong","restored_files":["calc.py"]}'])
def test_rollback_failure_is_not_fabricated(workspace, monkeypatch, response):
    _, target, runner = workspace
    original = code_agentic_core.manage_code_checkpoint
    monkeypatch.setattr(code_agentic_core, "manage_code_checkpoint", SimpleNamespace(invoke=lambda args: original.invoke(args) if args["action"] == "create" else response))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="failed"))
    result = run(runner)
    assert result["rolled_back"] is False
    assert result["rollback_error"]
    assert result["workspace_state"] == "unknown"
    assert target.read_text(encoding="utf-8") == "candidate\n"


def test_rollback_claim_requires_restored_content(workspace, monkeypatch):
    _, _, runner = workspace
    original = code_agentic_core.manage_code_checkpoint

    def invoke(args):
        if args["action"] == "create":
            return original.invoke(args)
        return json.dumps({"status": "rolled_back", "checkpoint_id": args["checkpoint_id"], "restored_files": ["calc.py"]})

    monkeypatch.setattr(code_agentic_core, "manage_code_checkpoint", SimpleNamespace(invoke=invoke))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="failed"))
    assert run(runner)["rolled_back"] is False


def test_measured_metrics_cannot_be_overridden(workspace):
    _, target, runner = workspace
    result = run(runner, expected_metrics={"throughput": 999999.0})
    assert result["success"] is False
    assert "expected_metrics" in result["error"]
    assert target.read_text(encoding="utf-8") == "baseline\n"


def test_retention_not_deployment_and_scrubbed_execution(workspace, monkeypatch):
    _, target, runner = workspace
    monkeypatch.setenv("OPENAI_API_KEY", "not-for-tests")

    def execute(*args, **kwargs):
        assert kwargs["shell"] is False
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert 0 < kwargs["timeout"] <= 300
        return SimpleNamespace(returncode=0, stdout="passed", stderr="")

    monkeypatch.setattr(subprocess, "run", execute)
    result = run(runner)
    assert result["workspace_retained"] is True
    assert result["production_deployed"] is False
    assert result["commit_scope"] == "workspace_retention"
    assert result["workspace_state"] == "candidate_retained"
    assert target.read_text(encoding="utf-8") == "candidate\n"


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), 301])
def test_invalid_timeout_prevents_write(workspace, timeout):
    _, target, runner = workspace
    assert run(runner, timeout_seconds=timeout)["success"] is False
    assert target.read_text(encoding="utf-8") == "baseline\n"


def test_persistence_failure_reports_retained_workspace(workspace, monkeypatch):
    _, _, runner = workspace
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(runner.persistence_mgr, "save_lineage", fail)
    result = run(runner)
    assert result["success"] is False
    assert result["persisted"] is False
    assert result["workspace_retained"] is True
    assert result["production_deployed"] is False


@pytest.mark.parametrize("method", ["save_lineage", "load_lineage", "save_knowledge_base", "load_knowledge_base"])
def test_persistence_path_confined(tmp_path, method):
    from alpha.avo import AVOLineage, DomainKnowledgeBase

    manager = AVOPersistenceManager(tmp_path)
    args = [AVOLineage()] if method == "save_lineage" else [DomainKnowledgeBase()] if method == "save_knowledge_base" else []
    with pytest.raises(ValueError):
        getattr(manager, method)(*args, filename="../escape.json")
