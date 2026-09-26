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

from alpha.sandbox.env_policy import build_sandbox_env

from .commit_gate import CommitGate, InvariantOracle, PromotionDecision
from .evidence import InvariantEvidence, VerificationReceipt, classify_change, code_digest, record_score
from .knowledge import DomainKnowledgeBase
from .lineage import AVOLineage, VersionRecord
from .persistence import AVOPersistenceManager
from .scorer_authority import GateRuleSet, ScorerProposalLedger, assert_candidate_target_permitted
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
        actor: str = "server",
        allow_declared_behaviour_change: bool = True,
    ) -> None:
        self.root_path = Path(root_path).resolve() if root_path else Path.cwd().resolve()
        self.persistence_mgr = persistence_mgr or AVOPersistenceManager(self.root_path)
        self.lineage = lineage or self.persistence_mgr.load_lineage() or AVOLineage()
        self.knowledge_base = knowledge_base or self.persistence_mgr.load_knowledge_base() or DomainKnowledgeBase()
        self.supervisor = supervisor or AVOSupervisor()
        #: Who authored the candidates this runner is judging. Recorded on every
        #: decision so the lineage says who asked, not just what happened.
        self.actor = actor
        #: The server-owned gate. This runner is the server: it runs the oracle
        #: itself and therefore the only component that can mint a verification
        #: receipt. Every candidate that reaches a file goes through this gate.
        #:
        #: ``allow_declared_behaviour_change`` is a deliberate, scoped concession
        #: to this surface's existing contract: a candidate here may change
        #: behaviour and the supplied oracle certifies the new behaviour
        #: (``test_avo_architecture.py::test_workspace_avo_runner_success_and_rollback``
        #: asserts exactly that). Imposing the strict policy would break a landed
        #: guarantee. The concession is neither free nor silent -- every such
        #: commit is stamped ``behaviour_changed_by_candidate`` and counted under
        #: ``caveats.behaviour_changed_commits``, so a run leaning on it cannot be
        #: mistaken for an invariant-verified one. The strict policy remains the
        #: default on :class:`CommitGate` itself.
        self.gate = CommitGate(
            self.lineage,
            rules=GateRuleSet(allow_declared_behaviour_change=allow_declared_behaviour_change),
            invariant_oracle=InvariantOracle(),
        )
        #: Scorer/gate change requests are recorded and refused, never installed.
        self.scorer_proposals = ScorerProposalLedger()
        self.last_decision: PromotionDecision | None = None

    def _target(self, path: str) -> Path:
        target = Path(path)
        target = target if target.is_absolute() else self.root_path / target
        resolved = target.resolve(strict=True)
        if not resolved.is_relative_to(self.root_path) or not resolved.is_file():
            raise ValueError("Target must be a file within the workspace root")
        if target.absolute() != resolved or resolved.stat().st_nlink != 1:
            raise ValueError("Candidate target must not use links or traversal")
        self._assert_target_is_not_the_gate(resolved)
        return resolved

    def _assert_target_is_not_the_gate(self, target: Path) -> None:
        """Refuse a candidate that tries to write the scorer or the gate.

        The workspace root already confines a candidate to its own project
        directory, so in practice this cannot fire for a candidate confined to
        that root. It is here anyway, and it is called on the write path, because
        a self-modifying harness that *can* reach its own scoring surface is the
        one failure mode the rest of this package cannot route around.
        """
        assert_candidate_target_permitted(target)

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
        task_id: str = "default",
    ) -> dict[str, Any]:
        with _WORKSPACE_LOCK:
            return self._run_variation(target_file_path, candidate_code, hypothesis, modification, test_command, expected_metrics, parent_id, timeout_seconds, task_id)

    def _run_variation(
        self,
        target_file_path,
        candidate_code,
        hypothesis,
        modification,
        test_command,
        expected_metrics,
        parent_id,
        timeout_seconds,
        task_id="default",
    ) -> dict[str, Any]:
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
            from alpha.authz.sandbox_authz import authorize_sandbox_execution, safe_app_config

            authorize_sandbox_execution(context={}, app_config=safe_app_config())
            effective_parent = parent_id or self.lineage.head_id
            if effective_parent and effective_parent not in self.lineage.versions:
                raise ValueError("Unknown candidate parent")
            if parent_id and parent_id != self.lineage.head_id:
                raise ValueError("Workspace candidate must use the current lineage head")
            from alpha.tools.builtins.code_agentic_core import manage_code_checkpoint

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
            exit_code = result.returncode
            correctness = exit_code == 0
            stdout, stderr = result.stdout, result.stderr
        except Exception as exc:
            exit_code, correctness, stdout, stderr = -1, False, "", str(exc)
        duration = time.monotonic() - start
        perf_score = max(0.01, round(1.0 / max(0.01, duration), 4))
        digest = code_digest(candidate_code)
        try:
            if self._target(target_file_path).read_bytes() != candidate_code.encode("utf-8"):
                correctness = False
                exit_code = exit_code if exit_code != 0 else 1
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
                task_id=task_id,
                metadata={
                    "target_file": str(target),
                    "checkpoint_id": checkpoint_id,
                    "commit_scope": "workspace_retention",
                    "production_deployed": False,
                    "code": candidate_code,
                    "change_kind": classify_change(baseline_text, candidate_code).value,
                },
            )
            # The server mints the correctness receipt: this runner is the only
            # component that actually executed the oracle, so it is the only
            # component whose verdict is evidence rather than an assertion.
            receipt = VerificationReceipt(
                receipt_id=f"vr_{digest[:12]}",
                target_digest=digest,
                oracle=" ".join(command)[:300],
                exit_code=exit_code,
                passed=correctness,
                minted_at=time.time(),
                detail=f"evaluation took {duration:.3f}s",
            )
            # ...and the invariant half of the gate, which AVO does not have.
            invariant = self._check_invariants(baseline_text, candidate_code, target, digest)
            decision = self.gate.promote(
                candidate,
                receipt=receipt,
                invariant=invariant,
                target_digest=digest,
                author=self.actor,
            )
            self.last_decision = decision
            retained = decision.committed
            response.update(
                committed=retained,
                workspace_retained=retained,
                version_id=candidate.version_id,
                parent_id=effective_parent,
                correctness=correctness,
                duration_seconds=round(duration, 3),
                performance_score=perf_score,
                rejection_reason=decision.reason,
                rejection_category=decision.category.value if decision.category else None,
                gate_version=decision.gate_version,
                gate_fingerprint=decision.gate_fingerprint,
                invariant_scope=decision.invariant_scope,
                invariant_regressions=invariant.regressions,
                invariant_detail=invariant.detail,
                task_id=task_id,
                stdout_snippet=stdout[-200:] or None,
                stderr_snippet=stderr[-200:] or None,
            )
            if not retained:
                rollback()
                self.knowledge_base.record_negative_lesson(attempt_hypothesis=hypothesis, failure_reason=decision.reason)
            else:
                response["workspace_state"] = "candidate_retained"
                self.knowledge_base.record_positive_pattern(hypothesis=hypothesis, modification_summary=modification, measured_gain=f"throughput={perf_score:.4f} (duration={duration:.3f}s)")
            stagnated, directive, diag = self.supervisor.observe_step(improved=retained, signature=f"{modification[:30]}_{correctness}", backtrack_candidate=effective_parent, lineage=self.lineage)
            response.update(supervisor_intervention=stagnated, supervisor_directive=directive.to_dict() if directive else None, supervisor_status=diag)
            self.persistence_mgr.save_lineage(self.lineage)
            self.persistence_mgr.save_knowledge_base(self.knowledge_base)
            response["chain_intact"] = self.lineage.verify_chain().ok
            response["persisted"] = True
            response["success"] = retained
        except Exception as exc:
            response["error"] = str(exc)
            if not response["workspace_retained"] and not response["rolled_back"]:
                rollback()
        return response

    def _check_invariants(self, baseline_text: str, candidate_code: str, target: Path, digest: str) -> InvariantEvidence:
        """Run the invariant half of the gate over this candidate.

        The entrypoint is discovered from the target's own public functions and
        the input schema is inferred from that function's own annotations. The
        candidate does not choose either: both are determined by the server from
        the baseline, and a candidate that hid a parameter's type would only be
        losing the fuzzer's boundary coverage, not gaining anything.

        When no entrypoint can be found, or the target is not parseable Python,
        the scope is reported as ``not_applicable`` **with the reason** rather than
        as a pass -- and commits made under that scope are counted separately by
        :func:`alpha.avo.honesty.build_honesty_report`, so the run cannot claim
        invariant coverage it does not have.
        """
        entrypoint = self._entrypoint_for(baseline_text, candidate_code)
        if entrypoint is None:
            return InvariantEvidence(
                source="differential_invariant_fuzzer",
                target_digest=digest,
                scope="not_applicable",
                detail=(
                    f"no shared public function between baseline and candidate at {target.name}; "
                    "the differential invariant suite has no entrypoint to exercise"
                ),
            )
        return self.gate.invariant_oracle.check(
            baseline_code=baseline_text,
            candidate_code=candidate_code,
            entrypoint=entrypoint,
            target_digest=digest,
            input_schema=self._input_schema_for(baseline_text, entrypoint),
        )

    @staticmethod
    def _input_schema_for(source: str, entrypoint: str) -> dict[str, str]:
        """Map the entrypoint's parameters to type hints, from the BASELINE source.

        Read from the baseline rather than the candidate on purpose: a candidate
        that is trying to change behaviour might also be trying to change how it
        is inspected. The server's view of the contract is the one that was in
        force before the change.
        """
        import ast

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == entrypoint:
                schema: dict[str, str] = {}
                for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                    schema[arg.arg] = ast.unparse(arg.annotation) if arg.annotation is not None else ""
                return schema
        return {}

    @staticmethod
    def _entrypoint_for(baseline_text: str, candidate_code: str) -> str | None:
        """The first public function both revisions define.

        Arguments are fine: the differential fuzzer inspects the signature and
        synthesises boundary values for each parameter. The candidate does not
        choose the entrypoint -- it is whatever the two revisions share, and it
        is discovered by the server, not supplied by the agent.
        """
        import ast

        def public_names(source: str) -> list[str]:
            try:
                tree = ast.parse(source)
            except SyntaxError:
                return []
            return [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]

        before = public_names(baseline_text)
        if not before:
            return None
        for name in before:
            if name in public_names(candidate_code):
                return name
        return None

    def honesty(self) -> Any:
        """The run's honesty report: denominators, change kinds, plateau status."""
        from .honesty import CommitStep, build_honesty_report

        steps = [
            CommitStep(
                version_id=record.version_id,
                kind=str(record.metadata.get("change_kind") or "refinement"),
                score=record_score(record),
                previous_best=0.0,
                best_committed_id=self.lineage.head_id,
                invariant_scope=str(record.metadata.get("invariant_scope") or "not_run"),
                author=str(record.metadata.get("author") or "unknown"),
            )
            for record in sorted(self.lineage.versions.values(), key=lambda r: r.created_at)
        ]
        running: list[float] = []
        for step in steps:
            step.previous_best = running[-1] if running else 0.0
            running.append(step.score)
        unverified = sum(1 for s in steps if s.invariant_scope not in ("measured", "behaviour_changed_by_candidate"))
        behaviour_changed = sum(1 for s in steps if s.invariant_scope == "behaviour_changed_by_candidate")
        report = build_honesty_report(
            total_explored=len(self.lineage.versions) + len(self.lineage.rejected_attempts),
            committed_steps=steps,
            rejection_counts=self.lineage.rejection_breakdown(),
            unverified_invariant_commits=unverified,
            behaviour_changed_commits=behaviour_changed,
            scorer_change_refusals=len(self.scorer_proposals.proposals),
            redirects=len(getattr(self.supervisor, "redirects", []) or []),
        )
        return report


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
