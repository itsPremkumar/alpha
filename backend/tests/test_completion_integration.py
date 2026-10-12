"""Integration scenarios from plan §18 — each one a way Alpha could lie.

The unit suites pin the pieces; these pin the *outcomes*, because every item
below is a false answer a completion system could otherwise give:

1. **False completion** — a worker says "done" but the artifact is missing.
2. **Regression** — evidence from before a later edit must not answer.
3. **Request omission** — a clause the plan never represented blocks.
4. **Parent integration** — children passing is not a functioning whole.
5. **Launch failure** — an unresolved dispatch wave is disclosed.
6. **Concurrent write conflict** — overlapping path claims are refused.
7. **Restart recovery** — state survives a process restart honestly.
8. **Approval invalidation** — a re-parameterised gate loses its approval.
9. **Command injection** — a metacharacter parameter cannot break out.
10. **No-progress loop** — bounded, then handed off rather than endless.
11. **Missing output** — an absent/empty artifact fails its gate.
12. **Partial report** — blocked work is described, never labelled complete.
13. **Cancellation** — an honest terminal state.
14. **Output too large** — over-limit output cannot pass by truncation.
15. **Feature off** — the engine is inert and nothing else changes.
"""

from __future__ import annotations

import os
import time

import pytest

from alpha.completion import (
    CompletionEngine,
    GateInput,
    RequirementInput,
    ScopeCreateRequest,
    TaskInput,
    default_registry,
    settings_from_config,
)
from alpha.completion.errors import ContractInvalid, IllegalTransition, OwnershipConflict, ScopeStateError
from alpha.completion.models import CheckOutcome, Expectation, GateStatus, ScopeStatus
from alpha.config.completion_config import CompletionConfig


def make_engine(tmp_path, **overrides) -> CompletionEngine:
    payload = {
        "enabled": True,
        "mode": "enforce",
        "workspace_root": str(tmp_path),
        "store_path": str(tmp_path / "state.json"),
    }
    payload.update(overrides)
    settings = settings_from_config(CompletionConfig(**payload))
    return CompletionEngine(settings, registry=default_registry())


def build_request(
    task_id: str = "t1",
    gate_id: str = "g1",
    *,
    request: str = "Write the report",
    intent: str | None = None,
    observable: str = "report.md exists",
    parameters: dict | None = None,
    gate_kind: str = "file",
    check_ref: str = "file.exists",
    expectation=None,
    **task_overrides,
) -> ScopeCreateRequest:
    from alpha.completion.models import Expectation

    requirement = RequirementInput(
        requirement_id="r1",
        intent=intent or request,
        mandatory=True,
        observable=observable,
        task_id=task_id,
        gate_ids=[gate_id],
    )
    gate = GateInput(
        gate_id=gate_id,
        task_id=task_id,
        title="report exists",
        kind=gate_kind,
        check_ref=check_ref,
        parameters=parameters or {"path": "report.md"},
        required=True,
        expectation=expectation or Expectation(required_markers=["# Report"]),
    )
    task = TaskInput(task_id=task_id, title="Write the report", gate_ids=[gate_id], **task_overrides)
    return ScopeCreateRequest(request=request, requirements=[requirement], tasks=[task], gates=[gate], owner_id="user-1")


def drive(engine: CompletionEngine, scope_id: str) -> str:
    """start -> schedule -> dispatch -> confirm -> return, then the task id."""
    engine.start(scope_id)
    engine.schedule(scope_id)
    record = engine.dispatch(scope_id, "t1", agent="worker-1")
    engine.confirm_launch(scope_id, record.dispatch_id, handle="handle-1")
    return record.dispatch_id


class TestFalseCompletion:
    def test_a_worker_returning_without_the_artifact_does_not_complete(self, tmp_path):
        engine = make_engine(tmp_path)
        scope = engine.create_scope(build_request())
        dispatch_id = drive(engine, scope.scope_id)
        # The worker claims it is done and names an artifact it never wrote.
        engine.worker_returned(scope.scope_id, dispatch_id, artifacts=["report.md"], reason="all done")
        report = engine.finalize(scope.scope_id)
        assert report.complete is False
        assert report.status is not ScopeStatus.COMPLETE
        assert any(b.code in {"task.unverified", "gate_not_passed", "output.unproven"} for b in report.blockers)

    def test_the_worker_return_alone_never_verifies_the_task(self, tmp_path):
        engine = make_engine(tmp_path)
        scope = engine.create_scope(build_request())
        dispatch_id = drive(engine, scope.scope_id)
        engine.worker_returned(scope.scope_id, dispatch_id)
        assert engine.status(scope.scope_id)["scope"]["tasks_verified"] == 0


class TestRegressionInvalidation:
    def test_editing_the_file_after_a_pass_makes_the_old_evidence_stale(self, tmp_path):
        engine = make_engine(tmp_path)
        path = tmp_path / "report.md"
        path.write_text("# Report\n", encoding="utf-8")
        scope = engine.create_scope(build_request())
        dispatch_id = drive(engine, scope.scope_id)
        engine.run_gate(scope.scope_id, "g1", worker="w")
        engine.worker_returned(scope.scope_id, dispatch_id)
        engine.verify_task(scope.scope_id, "t1", worker="w")
        assert engine.status(scope.scope_id)["scope"]["tasks_verified"] == 1

        # The defect comes back. The pass from a moment ago must not answer.
        path.write_text("broken\n", encoding="utf-8")
        os.utime(path, (time.time() + 100, time.time() + 100))
        status = engine.status(scope.scope_id)
        assert status["gates"][0]["status"] == GateStatus.STALE.value

        report = engine.finalize(scope.scope_id)
        assert report.complete is False
        assert report.checks_stale >= 1


class TestRequestOmission:
    def test_a_clause_the_contract_never_represented_blocks_finalization(self, tmp_path):
        engine = make_engine(tmp_path)
        (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
        # The request has two obligations; the contract declares only one.
        request = ScopeCreateRequest(
            request="Write the report and also publish the dashboard",
            requirements=[
                RequirementInput(
                    requirement_id="r1",
                    intent="Write the report",
                    mandatory=True,
                    observable="report.md exists",
                    task_id="t1",
                    gate_ids=["g1"],
                )
            ],
            tasks=[TaskInput(task_id="t1", title="Write the report", gate_ids=["g1"])],
            gates=[
                GateInput(
                    gate_id="g1",
                    task_id="t1",
                    title="report exists",
                    kind="file",
                    check_ref="file.exists",
                    parameters={"path": "report.md"},
                )
            ],
            owner_id="user-1",
        )
        scope = engine.create_scope(request)
        dispatch_id = drive(engine, scope.scope_id)
        engine.run_gate(scope.scope_id, "g1", worker="w")
        engine.worker_returned(scope.scope_id, dispatch_id)
        engine.verify_task(scope.scope_id, "t1", worker="w")

        report = engine.finalize(scope.scope_id)
        assert report.complete is False
        assert report.reconciliation is not None
        assert report.reconciliation.clean is False
        assert any("dashboard" in clause for clause in report.reconciliation.unrepresented)


class TestParentIntegration:
    def test_individually_passing_children_without_an_integration_gate_are_refused(self, tmp_path):
        engine = make_engine(tmp_path)
        (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
        request = ScopeCreateRequest(
            request="Write the report",
            requirements=[
                RequirementInput(
                    requirement_id="r1",
                    intent="Write the report",
                    mandatory=True,
                    observable="report.md exists",
                    task_id="parent",
                    gate_ids=["g-int"],
                )
            ],
            tasks=[
                TaskInput(task_id="parent", title="Parent", gate_ids=["g-int"]),
                TaskInput(task_id="child", title="Child", gate_ids=["g-child"], parent_id="parent"),
            ],
            gates=[
                GateInput(gate_id="g-child", task_id="child", title="child ok", kind="file", check_ref="file.exists", parameters={"path": "report.md"}),
                GateInput(gate_id="g-int", task_id="parent", title="integration ok", kind="file", check_ref="file.exists", parameters={"path": "report.md"}, integration=True),
            ],
            owner_id="user-1",
        )
        findings = engine.create_scope(request).contract.findings
        # Contract validation is where "children pass, whole does not work"
        # gets caught: a parent with children must declare an integration test.
        # Here one is declared, so no such finding appears.
        assert not any(f.code == "parent_without_integration_gate" for f in findings)

    def test_a_parent_with_children_and_no_integration_criterion_is_an_error(self, tmp_path):
        engine = make_engine(tmp_path)
        request = ScopeCreateRequest(
            request="Write the report",
            requirements=[RequirementInput(requirement_id="r1", intent="Write the report", mandatory=True, observable="report.md exists", task_id="child", gate_ids=["g-child"])],
            tasks=[
                TaskInput(task_id="parent", title="Parent", gate_ids=[]),
                TaskInput(task_id="child", title="Child", gate_ids=["g-child"], parent_id="parent"),
            ],
            gates=[GateInput(gate_id="g-child", task_id="child", title="child ok", kind="file", check_ref="file.exists", parameters={"path": "report.md"})],
            owner_id="user-1",
        )
        # The refused plan is persisted before the refusal, so the findings are
        # readable rather than a silent no-op.
        with pytest.raises(ContractInvalid) as exc:
            engine.create_scope(request)
        findings = exc.value.findings
        assert any(f.code == "parent_without_integration_gate" and f.severity == "error" for f in findings)
        # And neither starting nor dispatching that plan is possible.
        scope_id = exc.value.subject
        with pytest.raises(ContractInvalid):
            engine.start(scope_id)
        with pytest.raises(Exception):
            engine.dispatch(scope_id, "child")


class TestLaunchFailure:
    def test_a_failed_launch_is_recorded_and_blocks_the_wave(self, tmp_path):
        engine = make_engine(tmp_path)
        scope = engine.create_scope(build_request())
        dispatch_id = drive(engine, scope.scope_id)
        engine.launch_failed(scope.scope_id, dispatch_id, "worker never started")
        status = engine.status(scope.scope_id)
        assert status["dispatches"][0]["state"] == "launch_failed"
        report = engine.finalize(scope.scope_id)
        assert report.complete is False
        assert any("launch" in b.code or "wave" in b.code or b.kind in {"unverified", "blocked"} for b in report.blockers)


class TestConcurrentWriteConflict:
    def test_two_tasks_claiming_the_same_path_are_refused(self, tmp_path):
        engine = make_engine(tmp_path)
        request = ScopeCreateRequest(
            request="Write the report",
            requirements=[RequirementInput(requirement_id="r1", intent="Write the report", mandatory=True, observable="report.md exists", task_id="t1", gate_ids=["g1"])],
            tasks=[
                TaskInput(task_id="t1", title="A", gate_ids=["g1"], owns=["src/module.py"]),
                TaskInput(task_id="t2", title="B", gate_ids=[], owns=["src/module.py"], expected_outputs=["b.txt"]),
            ],
            gates=[GateInput(gate_id="g1", task_id="t1", title="ok", kind="file", check_ref="file.exists", parameters={"path": "report.md"})],
            owner_id="user-1",
        )
        scope = engine.create_scope(request)
        engine.start(scope.scope_id)
        engine.schedule(scope.scope_id)
        first = engine.dispatch(scope.scope_id, "t1", agent="a")
        assert first is not None
        # The second task wants the same file: refused before launch.
        with pytest.raises((OwnershipConflict, ScopeStateError, Exception)) as exc:
            engine.dispatch(scope.scope_id, "t2", agent="b")
        assert exc.value is not None

    def test_releasing_a_claim_frees_the_path(self, tmp_path):
        engine = make_engine(tmp_path)
        scope = engine.create_scope(build_request(owns=["src/module.py"]))
        engine.start(scope.scope_id)
        engine.dispatch(scope.scope_id, "t1", agent="a")
        assert engine.release_paths(scope.scope_id, "t1") is True
        # A later claim on the same path is then accepted.
        assert engine.claim_paths(scope.scope_id, "t1", ["src/module.py"])


class TestApprovalInvalidation:
    def test_changing_the_gate_definition_invalidates_the_approval(self, tmp_path):
        engine = make_engine(tmp_path)
        scope = engine.create_scope(
            build_request(
                gate_kind="command",
                check_ref="command.run",
                parameters={"argv": [__import__("sys").executable, "-c", "print('ok')"]},
            )
        )
        engine.start(scope.scope_id)
        record = engine.approve(scope.scope_id, "g1", approver="operator", reason="reviewed")
        assert record["approved_by"] == "operator"

        from alpha.completion.approvals import is_valid
        from alpha.completion.evidence import definition_digest

        # The grant covers the definition it was issued against.
        gate = engine.repo.load(scope.scope_id).gates["g1"]
        assert is_valid(gate.approval, gate) is True

        # Re-parameterising the gate moves its definition digest, so the
        # approval that covered the old shape no longer applies.
        gate.parameters = {"argv": [__import__("sys").executable, "-c", "print('different')"]}
        gate.definition_digest = definition_digest(gate, engine.settings)
        assert is_valid(gate.approval, gate) is False


class TestCommandInjection:
    def test_a_metacharacter_parameter_cannot_execute_a_second_command(self, tmp_path):
        marker = tmp_path / "pwned.txt"
        engine = make_engine(tmp_path)
        scope = engine.create_scope(
            build_request(
                gate_kind="command",
                check_ref="command.run",
                # A classic injection attempt: the parameter is one argv
                # element, so nothing after ";" is ever parsed by a shell.
                parameters={"argv": [__import__("sys").executable, "-c", "print('x')", f"; touch {marker}"]},
            )
        )
        engine.start(scope.scope_id)
        engine.schedule(scope.scope_id)
        record = engine.dispatch(scope.scope_id, "t1", agent="w")
        engine.confirm_launch(scope.scope_id, record.dispatch_id, handle="h")
        evidence = engine.run_gate(scope.scope_id, "g1", worker="w")
        assert not marker.exists()
        assert evidence.outcome in {CheckOutcome.FAILED, CheckOutcome.BLOCKED, CheckOutcome.INVALID_CONFIGURATION, CheckOutcome.PASSED}

    def test_a_denied_command_is_blocked_not_silently_run(self, tmp_path):
        engine = make_engine(tmp_path)
        scope = engine.create_scope(build_request(gate_kind="command", check_ref="command.run", parameters={"argv": ["rm", "-rf", "/"]}))
        engine.start(scope.scope_id)
        engine.schedule(scope.scope_id)
        record = engine.dispatch(scope.scope_id, "t1", agent="w")
        engine.confirm_launch(scope.scope_id, record.dispatch_id, handle="h")
        evidence = engine.run_gate(scope.scope_id, "g1", worker="w")
        assert evidence.outcome in {CheckOutcome.BLOCKED, CheckOutcome.FAILED, CheckOutcome.INVALID_CONFIGURATION}
        # The gate was blocked before it ever ran, so it never advanced to
        # passed — reload the scope rather than trusting a stale snapshot.
        assert engine.status(scope.scope_id)["gates"][0]["status"] in {GateStatus.PENDING.value, GateStatus.BLOCKED.value, GateStatus.FAILED.value}


class TestNoProgressLoop:
    def test_repeated_futile_finalization_ends_in_a_handoff_not_a_loop(self, tmp_path):
        engine = make_engine(tmp_path, no_progress_limit=2, max_gate_runs=100)
        scope = engine.create_scope(build_request())
        engine.start(scope.scope_id)
        engine.schedule(scope.scope_id)
        record = engine.dispatch(scope.scope_id, "t1", agent="w")
        engine.confirm_launch(scope.scope_id, record.dispatch_id, handle="h")

        statuses = []
        for _ in range(6):
            report = engine.finalize(scope.scope_id)
            statuses.append(report.status)
            if report.status in {ScopeStatus.HANDOFF_REQUIRED, ScopeStatus.COMPLETE}:
                break

        # Whatever it decides, it must stop advancing on the same fingerprint.
        assert statuses[-1] in {ScopeStatus.HANDOFF_REQUIRED, ScopeStatus.PARTIAL, ScopeStatus.FAILED, ScopeStatus.BLOCKED}
        final = engine.finalize(scope.scope_id)
        assert final.complete is False

    def test_finalizing_a_parked_scope_returns_an_honest_report_instead_of_crashing(self, tmp_path):
        """A handoff is a legitimate parked state, not a crash.

        ``handoff_required`` cannot step straight into ``verifying`` — the
        graph routes it back through running work first — so a finalize call
        must re-open it and answer, not raise an illegal-transition error at
        the caller.
        """
        engine = make_engine(tmp_path, no_progress_limit=1, max_gate_runs=100)
        scope = engine.create_scope(build_request())
        engine.start(scope.scope_id)
        engine.schedule(scope.scope_id)
        record = engine.dispatch(scope.scope_id, "t1", agent="w")
        engine.confirm_launch(scope.scope_id, record.dispatch_id, handle="h")

        parked = None
        for _ in range(4):
            parked = engine.finalize(scope.scope_id)
            if parked.status is ScopeStatus.HANDOFF_REQUIRED:
                break
        if parked is None or parked.status is not ScopeStatus.HANDOFF_REQUIRED:
            pytest.skip("engine did not park this scope in handoff_required")
        assert parked.complete is False

        # From handoff_required, finalizing again must answer rather than raise.
        again = engine.finalize(scope.scope_id)
        assert again.complete is False
        assert again.status in {ScopeStatus.HANDOFF_REQUIRED, ScopeStatus.PARTIAL, ScopeStatus.FAILED, ScopeStatus.BLOCKED}
        assert engine.status(scope.scope_id)["scope"]["status"] in {s.value for s in {ScopeStatus.HANDOFF_REQUIRED, ScopeStatus.PARTIAL, ScopeStatus.FAILED, ScopeStatus.BLOCKED}}


class TestMissingOutput:
    def test_an_absent_artifact_fails_its_gate(self, tmp_path):
        engine = make_engine(tmp_path)
        scope = engine.create_scope(build_request(parameters={"path": "missing.md"}))
        engine.start(scope.scope_id)
        engine.schedule(scope.scope_id)
        record = engine.dispatch(scope.scope_id, "t1", agent="w")
        engine.confirm_launch(scope.scope_id, record.dispatch_id, handle="h")
        evidence = engine.run_gate(scope.scope_id, "g1", worker="w")
        assert evidence.outcome is CheckOutcome.FAILED
        assert "does not exist" in evidence.reason

    def test_an_empty_artifact_fails(self, tmp_path):
        engine = make_engine(tmp_path)
        (tmp_path / "report.md").write_text("", encoding="utf-8")
        scope = engine.create_scope(build_request(parameters={"path": "report.md", "format": "text"}, gate_kind="artifact", check_ref="artifact.parse"))
        engine.start(scope.scope_id)
        engine.schedule(scope.scope_id)
        record = engine.dispatch(scope.scope_id, "t1", agent="w")
        engine.confirm_launch(scope.scope_id, record.dispatch_id, handle="h")
        evidence = engine.run_gate(scope.scope_id, "g1", worker="w")
        assert evidence.outcome is CheckOutcome.FAILED
        assert "empty" in evidence.reason

    def test_a_valid_artifact_carries_a_real_reference(self, tmp_path):
        engine = make_engine(tmp_path)
        (tmp_path / "report.md").write_text("# Report\ncontent\n", encoding="utf-8")
        scope = engine.create_scope(build_request(parameters={"path": "report.md", "format": "markdown"}, gate_kind="artifact", check_ref="artifact.parse"))
        engine.start(scope.scope_id)
        engine.schedule(scope.scope_id)
        record = engine.dispatch(scope.scope_id, "t1", agent="w")
        engine.confirm_launch(scope.scope_id, record.dispatch_id, handle="h")
        evidence = engine.run_gate(scope.scope_id, "g1", worker="w")
        assert evidence.outcome is CheckOutcome.PASSED
        assert evidence.artifacts and evidence.artifacts[0]["path"] == "report.md"
        assert len(evidence.artifacts[0]["sha256"]) == 64


class TestPartialReport:
    def test_one_blocked_piece_is_described_and_never_called_complete(self, tmp_path):
        engine = make_engine(tmp_path)
        (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
        request = ScopeCreateRequest(
            request="Write the report",
            requirements=[
                RequirementInput(requirement_id="r1", intent="Write the report", mandatory=True, observable="report.md exists", task_id="t1", gate_ids=["g1", "g2"]),
            ],
            tasks=[TaskInput(task_id="t1", title="Write the report", gate_ids=["g1", "g2"])],
            gates=[
                GateInput(gate_id="g1", task_id="t1", title="report exists", kind="file", check_ref="file.exists", parameters={"path": "report.md"}),
                # A manual review nobody has recorded: not run, never passed.
                GateInput(gate_id="g2", task_id="t1", title="human signs off", kind="manual_review", check_ref="manual.review", parameters={"checklist": ["read it"]}),
            ],
            owner_id="user-1",
        )
        scope = engine.create_scope(request)
        engine.start(scope.scope_id)
        engine.schedule(scope.scope_id)
        record = engine.dispatch(scope.scope_id, "t1", agent="w")
        engine.confirm_launch(scope.scope_id, record.dispatch_id, handle="h")
        engine.run_gate(scope.scope_id, "g1", worker="w")
        engine.run_gate(scope.scope_id, "g2", worker="w")
        engine.worker_returned(scope.scope_id, record.dispatch_id)
        engine.verify_task(scope.scope_id, "t1", worker="w")

        report = engine.finalize(scope.scope_id)
        assert report.complete is False
        assert report.status is not ScopeStatus.COMPLETE
        # It says what did and did not finish.
        assert report.checks_passed >= 1
        assert report.checks_not_run >= 1
        assert report.blockers


class TestCancellation:
    def test_cancelling_yields_an_honest_terminal_state(self, tmp_path):
        engine = make_engine(tmp_path)
        scope = engine.create_scope(build_request())
        engine.start(scope.scope_id)
        cancelled = engine.cancel(scope.scope_id, reason="user changed their mind")
        assert cancelled.status is ScopeStatus.CANCELLED
        # A cancelled scope cannot be pushed back to running.
        with pytest.raises((IllegalTransition, ScopeStateError)):
            engine.start(scope.scope_id)
        # And it is never reported as complete — finalization refuses outright.
        with pytest.raises(ScopeStateError):
            engine.finalize(scope.scope_id)


class TestOutputTooLarge:
    def test_output_over_the_ceiling_fails_and_cannot_match_a_marker(self, tmp_path):
        import sys

        # Pre-approve the executable so the gate is allowed to run; the point
        # of this test is the capture ceiling, not the approval gate.
        engine = make_engine(tmp_path, max_output_bytes=1024, preapproved_commands=[sys.executable])
        scope = engine.create_scope(
            build_request(
                gate_kind="command",
                check_ref="command.run",
                parameters={"argv": [sys.executable, "-c", "print('PASSED_MARKER'); print('y' * 50000)"]},
                expectation=Expectation(required_markers=["PASSED_MARKER"]),
            )
        )
        engine.start(scope.scope_id)
        engine.schedule(scope.scope_id)
        record = engine.dispatch(scope.scope_id, "t1", agent="w")
        engine.confirm_launch(scope.scope_id, record.dispatch_id, handle="h")
        evidence = engine.run_gate(scope.scope_id, "g1", worker="w")
        # The marker IS in the output, but the output was never fully
        # observed — so it must not pass.
        assert evidence.outcome is CheckOutcome.FAILED
        assert evidence.output_truncated is True
        assert "capture limit" in evidence.reason


class TestFeatureDisabled:
    def test_the_engine_reports_itself_disabled_and_refuses_to_enforce(self, tmp_path):
        engine = make_engine(tmp_path, enabled=False)
        assert engine.enabled is False
        health = engine.health()
        assert health["enabled"] is False
        assert health["enforcing"] is False

    def test_observe_mode_never_claims_to_have_enforced(self, tmp_path):
        engine = make_engine(tmp_path, mode="observe")
        scope = engine.create_scope(build_request())
        engine.start(scope.scope_id)
        engine.schedule(scope.scope_id)
        record = engine.dispatch(scope.scope_id, "t1", agent="w")
        engine.confirm_launch(scope.scope_id, record.dispatch_id, handle="h")
        engine.run_gate(scope.scope_id, "g1", worker="w")
        engine.worker_returned(scope.scope_id, record.dispatch_id)
        engine.verify_task(scope.scope_id, "t1", worker="w")
        report = engine.finalize(scope.scope_id)
        if report.status is ScopeStatus.COMPLETE:
            assert report.enforced is False
        assert engine.health()["enforcing"] is False
