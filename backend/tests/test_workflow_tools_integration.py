"""Integration tests for all Oh My OpenAgent (OmO / Sisyphus) Builtin Tools."""

import json
from pathlib import Path

from alpha.tools.builtins import (
    boulder_checkpoint_manage,
    hashline_edit,
    hashline_read,
    hyperplan_review_manage,
    workflow_dag_manage,
)
from alpha.tools.tools import BUILTIN_TOOLS


def test_omo_tools_registered_in_builtin_tools():
    tool_names = {t.name for t in BUILTIN_TOOLS}
    expected_tools = {
        "hashline_read",
        "hashline_edit",
        "workflow_dag_manage",
        "boulder_checkpoint_manage",
        "kibitzer_nudge_manage",
        "hyperplan_review_manage",
        "ast_grep_search",
        "ast_grep_rewrite",
    }
    for name in expected_tools:
        assert name in tool_names, f"Tool '{name}' missing from BUILTIN_TOOLS!"


def test_hashline_tools_lifecycle(tmp_path: Path, monkeypatch):
    """The hashline tools resolve paths through the sandbox mapping.

    They used to take a raw host path (``Path(file_path).exists()``), which is
    how they came to contradict ``write_file``/``read_file``/``ls`` about which
    paths exist. They now go through the same mapping as every other
    filesystem tool, so the file lives under the thread workspace and is
    addressed by its virtual path.
    """
    from types import SimpleNamespace

    from alpha.sandbox import tools as sandbox_tools
    from alpha.sandbox.local.local_sandbox import LocalSandbox

    thread_root = tmp_path / "thread"
    for sub in ("workspace", "uploads", "outputs"):
        (thread_root / sub).mkdir(parents=True, exist_ok=True)
    runtime = SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local:t1"},
            "thread_data": {
                "workspace_path": str(thread_root / "workspace"),
                "uploads_path": str(thread_root / "uploads"),
                "outputs_path": str(thread_root / "outputs"),
            },
        },
        context={"thread_id": "t1"},
    )
    monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime=None: LocalSandbox("t1"))
    monkeypatch.setattr(sandbox_tools, "ensure_thread_directories_exist", lambda runtime=None: None)

    virtual = "/mnt/user-data/workspace/hello.py"
    test_file = thread_root / "workspace" / "hello.py"
    test_file.write_text("line_one = 1\nline_two = 2\nline_three = 3\n", encoding="utf-8")

    # 1. Read with hashline tags
    tagged_output = hashline_read.func(runtime=runtime, file_path=virtual)
    assert "1#" in tagged_output
    assert "2#" in tagged_output
    assert "line_two = 2" in tagged_output
    assert "does not exist" not in tagged_output

    # Extract hash for line 2
    lines = tagged_output.splitlines()
    ref2 = lines[1].split("|")[0].strip()

    # 2. Edit line 2
    edit_res = hashline_edit.func(
        runtime=runtime,
        file_path=virtual,
        start_ref=ref2,
        end_ref=ref2,
        replacement="line_two = 200",
    )
    assert "Successfully updated" in edit_res
    assert "line_two = 200" in test_file.read_text(encoding="utf-8")


def test_workflow_dag_tool_lifecycle():
    res = workflow_dag_manage.invoke({
        "action": "create",
        "key": "test_wf",
        "name": "Integration Workflow",
    })
    assert "Created workflow" in res

    workflow_dag_manage.invoke({
        "action": "add_node",
        "key": "test_wf",
        "node_id": "step1",
        "prompt": "Run preliminary checks",
        "category": "quick",
    })

    workflow_dag_manage.invoke({
        "action": "add_node",
        "key": "test_wf",
        "node_id": "step2",
        "prompt": "Apply changes",
        "category": "deep",
        "depends_on": ["step1"],
    })

    waves_res = workflow_dag_manage.invoke({
        "action": "plan_waves",
        "key": "test_wf",
    })
    assert "Topological execution waves" in waves_res
    assert "step1" in waves_res

    # Try completing without evidence -> rejected
    reject_res = workflow_dag_manage.invoke({
        "action": "mark_completed",
        "key": "test_wf",
        "node_id": "step1",
    })
    assert "Rejected" in reject_res

    # Record evidence and complete
    workflow_dag_manage.invoke({
        "action": "record_evidence",
        "key": "test_wf",
        "node_id": "step1",
        "evidence": "Verification report: PASS",
    })
    complete_res = workflow_dag_manage.invoke({
        "action": "mark_completed",
        "key": "test_wf",
        "node_id": "step1",
    })
    assert "completed" in complete_res


def test_boulder_checkpoint_tool_lifecycle(tmp_path: Path):
    custom_path = str(tmp_path / "boulder.json")
    create_res = boulder_checkpoint_manage.invoke({
        "action": "create",
        "task": "Migrate Database",
        "checklist": ["backup", "migrate", "verify"],
        "custom_path": custom_path,
    })
    assert "Created Boulder checkpoint" in create_res

    status_res = boulder_checkpoint_manage.invoke({
        "action": "status",
        "custom_path": custom_path,
    })
    status_data = json.loads(status_res)
    assert status_data["top_level_task"] == "Migrate Database"
    assert len(status_data["checklist"]) == 3

    # Update step
    boulder_checkpoint_manage.invoke({
        "action": "update_step",
        "step_index": 0,
        "completed": True,
        "evidence": "Backup saved",
        "custom_path": custom_path,
    })
    status_updated = json.loads(boulder_checkpoint_manage.invoke({
        "action": "status",
        "custom_path": custom_path,
    }))
    assert status_updated["checklist"][0]["completed"] is True


def test_hyperplan_review_tool():
    res = hyperplan_review_manage.invoke({
        "plan_title": "Database Optimization",
        "plan_content": "Prerequisites: Postgres 16 running.\nArchitecture: Partition tables.\nSecurity: Zero plain credentials.\nVerification plan: Automated pytest tests.",
    })
    data = json.loads(res)
    assert data["overall_status"] == "APPROVED"
    assert len(data["verdicts"]) == 4
