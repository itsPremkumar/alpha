"""Reversible, approval-gated deletion contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langgraph.prebuilt import ToolRuntime

from alpha.config.paths import Paths
from alpha.projects.approval_queue import ApprovalQueue
from alpha.safety.reversible_delete import MAX_METADATA_BYTES, ReversibleDeleteService
from alpha.tools.builtins.reversible_delete_tool import reversible_delete
from alpha.tools.tools import BUILTIN_TOOLS


def _service(root: Path) -> ReversibleDeleteService:
    return ReversibleDeleteService(root, max_files=3)


def _tool_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ToolRuntime, Path]:
    paths = Paths(base_dir=tmp_path / "state")
    monkeypatch.setattr(
        "alpha.tools.builtins.reversible_delete_tool.get_paths",
        lambda: paths,
    )
    context = {"thread_id": "thread-1", "user_id": "user-1"}
    root = paths.sandbox_work_dir("thread-1", user_id="user-1")
    runtime = ToolRuntime(
        state={"thread_data": {"workspace_path": str(root)}},
        context=context,
        config={},
        stream_writer=lambda _: None,
        tool_call_id="reversible-delete-test",
        store=None,
    )
    return runtime, root


def test_plan_is_read_only_and_dossier_is_bounded(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "b.txt").write_text("beta", encoding="utf-8")
    service = _service(tmp_path)

    plan = service.plan(["a.txt", "folder"])

    assert plan.approval_required is True
    assert plan.target_count == 2
    assert [target.path for target in plan.targets] == ["a.txt", "folder"]
    assert (tmp_path / "a.txt").exists()
    assert (tmp_path / "folder" / "b.txt").exists()
    assert "a.txt" in json.dumps(plan.to_dict())


def test_plan_rejects_traversal_symlinks_and_batch_overflow(tmp_path: Path) -> None:
    (tmp_path / "safe.txt").write_text("safe", encoding="utf-8")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="outside root"):
        service.plan(["../outside.txt"])
    with pytest.raises(ValueError, match="root itself"):
        service.plan(["."])
    with pytest.raises(ValueError, match="batch"):
        service.plan(["safe.txt", "safe.txt", "safe.txt", "safe.txt"])

    link = tmp_path / "link"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        return
    with pytest.raises(ValueError, match="symlink"):
        service.plan(["link"])


def test_execute_requires_approval_and_restore_returns_content(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("keep me", encoding="utf-8")
    service = _service(tmp_path)
    plan = service.plan(["notes.txt"])

    refused = service.execute(plan, approved=False)
    assert refused.status == "approval_required"
    assert source.exists()

    receipt = service.execute(plan, approved=True, approval_reference="user:test")
    assert receipt.status == "quarantined"
    assert not source.exists()
    assert receipt.entries[0].sha256

    restored = service.restore(receipt.receipt_id, approved=True, approval_reference="user:test")
    assert restored.status == "restored"
    assert source.read_text(encoding="utf-8") == "keep me"
    assert service.get_receipt(receipt.receipt_id).status == "restored"


def test_execute_fails_closed_when_target_changes_after_plan(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("before", encoding="utf-8")
    service = _service(tmp_path)
    plan = service.plan(["notes.txt"])
    source.write_text("after", encoding="utf-8")

    result = service.execute(plan, approved=True, approval_reference="user:test")

    assert result.status == "target_changed"
    assert source.read_text(encoding="utf-8") == "after"
    assert not any(item.is_dir() for item in service.quarantine_root.iterdir())


def test_tampered_plan_metadata_is_ignored_before_execution(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    outside = tmp_path.parent / "outside.txt"
    source.write_text("inside", encoding="utf-8")
    outside.write_text("outside", encoding="utf-8")
    service = _service(tmp_path)
    plan = service.plan(["notes.txt"])
    metadata = json.loads(service._plans_path.read_text(encoding="utf-8"))
    metadata["plans"][plan.plan_id]["targets"][0]["path"] = "../outside.txt"
    service._plans_path.write_text(json.dumps(metadata), encoding="utf-8")

    reloaded = _service(tmp_path)

    assert reloaded.get_plan(plan.plan_id) is None


def test_tampered_receipt_metadata_is_ignored_before_restore(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("inside", encoding="utf-8")
    service = _service(tmp_path)
    plan = service.plan(["notes.txt"])
    receipt = service.execute(plan, approved=True, approval_reference="trusted:test")
    metadata = json.loads(service._receipts_path.read_text(encoding="utf-8"))
    metadata["receipts"][receipt.receipt_id]["entries"][0]["original_path"] = "../outside.txt"
    service._receipts_path.write_text(json.dumps(metadata), encoding="utf-8")

    reloaded = _service(tmp_path)

    assert reloaded.get_receipt(receipt.receipt_id) is None


def test_oversized_quarantine_metadata_is_ignored(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._plans_path.write_bytes(b" " * (MAX_METADATA_BYTES + 1))

    reloaded = _service(tmp_path)

    assert reloaded._plans == {}


def test_quarantine_vault_rejects_workspace_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / ".alpha").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable on this host")

    with pytest.raises(ValueError, match="symlink"):
        ReversibleDeleteService(tmp_path)


def test_reversible_delete_tool_schema_and_runtime_reject_model_supplied_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    properties = reversible_delete.tool_call_schema.model_json_schema()["properties"]
    assert "runtime" not in properties
    assert "root" not in properties

    runtime, _workspace = _tool_runtime(tmp_path, monkeypatch)
    runtime.state["thread_data"]["workspace_path"] = str(tmp_path / "outside")
    (tmp_path / "outside").mkdir()
    result = reversible_delete.invoke({"runtime": runtime, "action": "plan", "paths_json": '["outside"]'})

    assert result["success"] is False
    assert result["error"] == "invalid_delete_request"
    assert "does not match authenticated thread workspace" in result["detail"]


def test_reversible_delete_tool_rejects_unsafe_project_id_before_queue_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, workspace = _tool_runtime(tmp_path, monkeypatch)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "data.txt").write_text("data", encoding="utf-8")
    plan = reversible_delete.invoke({"runtime": runtime, "action": "plan", "paths_json": '["data.txt"]'})["plan"]
    monkeypatch.setattr(
        "alpha.tools.builtins.reversible_delete_tool.get_approval_queue",
        lambda project_id: (_ for _ in ()).throw(AssertionError("unsafe project id reached queue")),
    )

    result = reversible_delete.invoke(
        {
            "runtime": runtime,
            "action": "request_approval",
            "plan_id": plan["plan_id"],
            "project_id": "../escape",
        }
    )

    assert result["success"] is False
    assert result["error"] == "invalid_delete_request"
    assert "storage-safe" in result["detail"]


def test_reversible_delete_tool_uses_server_resolved_project_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, workspace = _tool_runtime(tmp_path, monkeypatch)
    workspace.mkdir(parents=True, exist_ok=True)
    source = workspace / "data.txt"
    source.write_text("data", encoding="utf-8")
    assert reversible_delete in BUILTIN_TOOLS

    plan = reversible_delete.invoke({"runtime": runtime, "action": "plan", "paths_json": '["data.txt"]'})
    assert plan["success"] is True
    assert plan["plan"]["approval_required"] is True
    assert source.exists()

    queue = ApprovalQueue("project-1", storage_path=tmp_path / "approvals.json")
    monkeypatch.setattr(
        "alpha.tools.builtins.reversible_delete_tool.get_approval_queue",
        lambda project_id: queue,
    )
    approval = reversible_delete.invoke(
        {
            "runtime": runtime,
            "action": "request_approval",
            "plan_id": plan["plan"]["plan_id"],
            "project_id": "project-1",
        }
    )
    assert approval["success"] is True
    assert approval["approval"]["status"] == "pending"
    assert source.exists()

    refused = reversible_delete.invoke(
        {
            "runtime": runtime,
            "action": "execute",
            "plan_id": plan["plan"]["plan_id"],
            "project_id": "project-1",
            "approval_request_id": approval["approval"]["request_id"],
        }
    )
    assert refused["status"] == "waiting_approval"
    assert source.exists()

    queue.resolve_request(
        approval["approval"]["request_id"],
        approved=True,
        resolved_by="project-owner",
    )
    receipt = reversible_delete.invoke(
        {
            "runtime": runtime,
            "action": "execute",
            "plan_id": plan["plan"]["plan_id"],
            "project_id": "project-1",
            "approval_request_id": approval["approval"]["request_id"],
        }
    )
    assert receipt["status"] == "quarantined"
    assert receipt["approval"]["resolved_by"] == "project-owner"
    assert not source.exists()

    restore_approval = reversible_delete.invoke(
        {
            "runtime": runtime,
            "action": "request_restore_approval",
            "receipt_id": receipt["receipt_id"],
            "project_id": "project-1",
        }
    )
    queue.resolve_request(
        restore_approval["approval"]["request_id"],
        approved=True,
        resolved_by="project-owner",
    )
    restored = reversible_delete.invoke(
        {
            "runtime": runtime,
            "action": "restore",
            "receipt_id": receipt["receipt_id"],
            "project_id": "project-1",
            "approval_request_id": restore_approval["approval"]["request_id"],
        }
    )
    assert restored["status"] == "restored"
    assert source.read_text(encoding="utf-8") == "data"


def test_reversible_delete_tool_rejects_approval_for_another_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, workspace = _tool_runtime(tmp_path, monkeypatch)
    workspace.mkdir(parents=True, exist_ok=True)
    first = workspace / "first.txt"
    second = workspace / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    queue = ApprovalQueue("project-1", storage_path=tmp_path / "approvals.json")
    monkeypatch.setattr(
        "alpha.tools.builtins.reversible_delete_tool.get_approval_queue",
        lambda project_id: queue,
    )

    first_plan = reversible_delete.invoke({"runtime": runtime, "action": "plan", "paths_json": '["first.txt"]'})["plan"]
    second_plan = reversible_delete.invoke({"runtime": runtime, "action": "plan", "paths_json": '["second.txt"]'})["plan"]
    approval = reversible_delete.invoke(
        {
            "runtime": runtime,
            "action": "request_approval",
            "plan_id": first_plan["plan_id"],
            "project_id": "project-1",
        }
    )["approval"]
    queue.resolve_request(approval["request_id"], approved=True, resolved_by="owner")

    result = reversible_delete.invoke(
        {
            "runtime": runtime,
            "action": "execute",
            "plan_id": second_plan["plan_id"],
            "project_id": "project-1",
            "approval_request_id": approval["request_id"],
        }
    )

    assert result["status"] == "approval_mismatch"
    assert first.exists()
    assert second.exists()
