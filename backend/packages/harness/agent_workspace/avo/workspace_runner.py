from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from agent_workspace.sandbox.env_policy import build_sandbox_env

from .knowledge import DomainKnowledgeBase
from .lineage import AVOLineage, VersionRecord
from .persistence import AVOPersistenceManager
from .scoring import EvaluationVector
from .supervisor import AVOSupervisor

_WORKSPACE_LOCK = threading.RLock()


class WorkspaceAVORunner:
    def __init__(
        self,
        lineage: AVOLineage | None = None,
        knowledge_base: DomainKnowledgeBase | None = None,
        supervisor: AVOSupervisor | None = None,
        persistence_mgr: AVOPersistenceManager | None = None,
        root_path: str | Path | None = None,
    ) -> None:
        self.root_path = Path(root_path).resolve() if root_path else Path.cwd().resolve()
        self.persistence_mgr = persistence_mgr or AVOPersistenceManager(self.root_path)
        self.lineage = lineage or self.persistence_mgr.load_lineage() or AVOLineage()
        self.knowledge_base = knowledge_base or self.persistence_mgr.load_knowledge_base() or DomainKnowledgeBase()
        self.supervisor = supervisor or AVOSupervisor()

    def _target(self, path: str) -> Path:
        target = Path(path)
        target = target if target.is_absolute() else self.root_path / target
        resolved = target.resolve(strict=True)
        if not resolved.is_relative_to(self.root_path) or not resolved.is_file():
            raise ValueError("Target must be a file within the workspace root")
        if target.absolute() != resolved or resolved.stat().st_nlink != 1:
            raise ValueError("Candidate target must not use links or traversal")
        return resolved

    def run_workspace_variation(
        self,
        target_file_path: str,
        candidate_code: str,
        hypothesis: str,
        modification: str,
        test_command: str | None = None,
        expected_metrics: dict[str, float] | None = None,
        parent_id: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        with _WORKSPACE_LOCK:
            return self._run_variation(target_file_path, candidate_code, hypothesis, modification, test_command, expected_metrics, parent_id, timeout_seconds)

    def _run_variation(self, target_file_path, candidate_code, hypothesis, modification, test_command, expected_metrics, parent_id, timeout_seconds) -> dict[str, Any]:
        response: dict[str, Any] = {
            "success": False,
            "committed": False,
            "rolled_back": False,
            "workspace_retained": False,
            "production_deployed": False,
            "commit_scope": "workspace_retention",
            "workspace_state": "unchanged",
            "rollback_scope": "target_file_only",
            "execution_scope": "local_process_not_security_sandbox",
            "concurrency_scope": "single_process",
            "persisted": False,
        }
        try:
            if expected_metrics:
                raise ValueError("expected_metrics cannot supply or override measured evaluation metrics")
            if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
                raise ValueError("timeout_seconds must be finite and between 0 and 300")
            target = self._target(target_file_path)
            baseline = target.read_bytes()
            baseline_text = baseline.decode("utf-8", errors="strict").replace("\r\n", "\n")
            command = shlex.split(test_command, posix=os.name != "nt") if test_command else [sys.executable, "-m", "pytest"]
            if os.name == "nt":
                command = [arg[1:-1] if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'" else arg for arg in command]
            if not command:
                raise ValueError("Evaluation command is empty")
            from agent_workspace.authz.sandbox_authz import authorize_sandbox_execution, safe_app_config

            authorize_sandbox_execution(context={}, app_config=safe_app_config())
            effective_parent = parent_id or self.lineage.head_id
            if effective_parent and effective_parent not in self.lineage.versions:
                raise ValueError("Unknown candidate parent")
            if parent_id and parent_id != self.lineage.head_id:
                raise ValueError("Workspace candidate must use the current lineage head")
            from agent_workspace.tools.builtins.code_agentic_core import manage_code_checkpoint

            rel_file = str(target.relative_to(self.root_path))
            checkpoint = json.loads(manage_code_checkpoint.invoke({"action": "create", "label": f"avo_pre_{time.time_ns()}", "target_files": [rel_file], "root_path": str(self.root_path)}))
            if not isinstance(checkpoint, dict) or checkpoint.get("status") != "created" or not isinstance(checkpoint.get("checkpoint_id"), str) or not checkpoint["checkpoint_id"]:
                raise ValueError("Successful checkpoint required before candidate write")
            if checkpoint.get("captured_files") != [rel_file] or checkpoint.get("captured_files_count") != 1:
                raise ValueError("Checkpoint did not capture the target file")
            checkpoint_id = checkpoint["checkpoint_id"]
            response["checkpoint_id"] = checkpoint_id
        except Exception as exc:
            response["error"] = str(exc)
            return response

        def rollback() -> None:
            response["workspace_state"] = "unknown"
            try:
                self._target(target_file_path)
                result = json.loads(manage_code_checkpoint.invoke({"action": "rollback", "checkpoint_id": checkpoint_id, "root_path": str(self.root_path)}))
                if not isinstance(result, dict) or result.get("status") != "rolled_back" or result.get("checkpoint_id") != checkpoint_id or rel_file not in result.get("restored_files", []):
                    raise RuntimeError("Checkpoint rollback was not successful")
                if self._target(target_file_path).read_text(encoding="utf-8").replace("\r\n", "\n") != baseline_text:
                    raise RuntimeError("Rollback content does not match baseline")
                response["rolled_back"] = True
                response["workspace_state"] = "baseline_restored"
            except Exception as exc:
                response["rollback_error"] = str(exc)

        try:
            if self._target(target_file_path).read_bytes() != baseline:
                raise RuntimeError("Target changed after checkpoint creation")
            target.write_text(candidate_code, encoding="utf-8", newline="")
            response["workspace_state"] = "candidate_written"
        except Exception as exc:
            response["error"] = f"Failed to write candidate: {exc}"
            rollback()
            return response

        start = time.monotonic()
        try:
            result = subprocess.run(command, shell=False, cwd=str(self.root_path), capture_output=True, text=True, timeout=timeout_seconds, stdin=subprocess.DEVNULL, env=build_sandbox_env())
            correctness = result.returncode == 0
            stdout, stderr = result.stdout, result.stderr
        except Exception as exc:
            correctness, stdout, stderr = False, "", str(exc)
        duration = time.monotonic() - start
        perf_score = max(0.01, round(1.0 / max(0.01, duration), 4))
        try:
            if self._target(target_file_path).read_bytes() != candidate_code.encode("utf-8"):
                correctness = False
                stderr += "\nTarget changed during evaluation"
            vector = EvaluationVector(metrics={"throughput": perf_score}, correctness=correctness, metadata={"duration_s": duration, "stdout_tail": stdout[-300:], "stderr_tail": stderr[-300:]})
            candidate = VersionRecord(
                parent_id=effective_parent,
                hypothesis=hypothesis,
                modification=modification,
                correctness=correctness,
                vector=vector,
                performance_score=perf_score,
                quality_score=1.0 if correctness else 0.0,
                diff_summary=modification,
                metadata={"target_file": str(target), "checkpoint_id": checkpoint_id, "commit_scope": "workspace_retention", "production_deployed": False},
            )
            retained = self.lineage.commit_candidate(candidate)
            response.update(
                committed=retained,
                workspace_retained=retained,
                version_id=candidate.version_id,
                parent_id=effective_parent,
                correctness=correctness,
                duration_seconds=round(duration, 3),
                performance_score=perf_score,
                rejection_reason=candidate.rejection_reason,
                stdout_snippet=stdout[-200:] or None,
                stderr_snippet=stderr[-200:] or None,
            )
            if not retained:
                rollback()
                self.knowledge_base.record_negative_lesson(attempt_hypothesis=hypothesis, failure_reason=candidate.rejection_reason or "Evaluation rejected")
            else:
                response["workspace_state"] = "candidate_retained"
                self.knowledge_base.record_positive_pattern(hypothesis=hypothesis, modification_summary=modification, measured_gain=f"throughput={perf_score:.4f} (duration={duration:.3f}s)")
            stagnated, directive, diag = self.supervisor.observe_step(improved=retained, signature=f"{modification[:30]}_{correctness}", backtrack_candidate=effective_parent)
            response.update(supervisor_intervention=stagnated, supervisor_directive=directive.to_dict() if directive else None, supervisor_status=diag)
            self.persistence_mgr.save_lineage(self.lineage)
            self.persistence_mgr.save_knowledge_base(self.knowledge_base)
            response["persisted"] = True
            response["success"] = retained
        except Exception as exc:
            response["error"] = str(exc)
            if not response["workspace_retained"] and not response["rolled_back"]:
                rollback()
        return response


_AVO_RUNNERS: dict[str, WorkspaceAVORunner] = {}


def get_avo_runner(project_id: str = "default") -> WorkspaceAVORunner:
    import re

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", project_id):
        raise ValueError("Invalid AVO project id")
    with _WORKSPACE_LOCK:
        base = Path(os.environ.get("AGENT_WORKSPACE_PROJECTS_DIR", ".agent_workspace_projects")).resolve()
        project = base / project_id
        if project.resolve() != project:
            raise ValueError("AVO project path escapes project root")
        key = str(project)
        if key not in _AVO_RUNNERS:
            project.mkdir(parents=True, exist_ok=True)
            _AVO_RUNNERS[key] = WorkspaceAVORunner(root_path=project)
        return _AVO_RUNNERS[key]
