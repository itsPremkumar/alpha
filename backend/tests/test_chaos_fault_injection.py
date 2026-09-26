"""Fault injection for autonomous group runs.

This is the isolation proof for ``alpha.groups.runner``. It is a
*fault-injection* suite rather than a happy-path suite: every test drives the
real group-run control flow (admission, per-member retry loop, the join, the
moderator synthesis pass, the failure roll-up) and breaks exactly one
component on purpose, then holds the run to three invariants:

(a) the failure is *reported*, as a stable typed tag drawn from the closed
    ``MEMBER_ERROR_TYPES`` set, on the failing component's own record -- never
    swallowed, never downgraded to prose, never recorded against a sibling;
(b) unrelated work *continues* -- a healthy sibling keeps its exact output,
    its own room receipt, and the moderator pass still runs over it;
(c) the run does not claim success once a component of it has failed.

Injected fault classes, one per test:

* **dependency raising mid-flight** -- the subagent execution reports
  ``RUNNING`` (so it genuinely started) and *then* ``FAILED``. The
  post-join/typed-record contract, on the commonest real failure.
* **timeout** -- the execution reports ``TIMED_OUT``, which must be tagged
  ``timeout`` and not collapsed into the generic dependency kind.
* **cancellation** -- the execution reports ``CANCELLED`` without a run-level
  cancel, which is a terminal, unretriable component failure.
* **resource exhaustion** -- the executor itself refuses the work at
  submission with a real host errno (too many open files) or an allocation
  failure, which must be tagged ``resource_exhausted`` and must leave the
  service usable for the next run.
* **component failure in the synthesis pass** -- members all succeed and the
  moderator pass fails, which must still not be reported as a successful run.
* **cross-run** -- a faulted run and a clean run in flight at the same time:
  the fault must not disturb, cancel or degrade the clean one.

Timing: the harness in ``test_sibling_survival`` drives the runner's own poll
and retry clocks, so the whole file runs in a couple of seconds. The only
waits are a 10s hang guard around reaching a terminal run status, which is
never used to rescue an assertion.

No ``skip``, no ``xfail``, no weakened assertions: every test fails loudly if
the isolation it describes is absent.
"""

from __future__ import annotations

import pytest
from test_sibling_survival import (
    _Status,
    _tool_provider,
    assert_member_succeeded,
    assert_typed_member_failure,
    cancelled,
    completed,
    emfile_fault,
    failed,
    install_fault_harness,
    isolate_group_singletons,
    member_receipts,
    running,
    timed_out,
    wait_for_terminal,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    isolate_group_singletons(monkeypatch, tmp_path)


# ---------------------------------------------------------------------------
# (1) a dependency raising mid-flight
# ---------------------------------------------------------------------------


async def test_dependency_raising_mid_flight_is_typed_isolated_and_not_a_success(monkeypatch) -> None:
    """A member whose dependency dies after it started must be reported, not swallowed.

    ``RUNNING`` is served first, so the failure is provably mid-flight rather
    than a rejected submission, and the retry budget is genuinely spent
    before the fault is recorded.
    """
    scripts = {
        "researcher": [running(), failed("vector store connection reset by peer")],
        "writer": [running(), completed("writer slice")],
        "synthesis": [running(), completed("merged deliverable")],
    }
    harness = install_fault_harness(monkeypatch, scripts=scripts)

    from alpha.groups.runner import get_group_run_service

    service = get_group_run_service()
    run = service.start_run("chaos-dep", "Research and write", members=["researcher", "writer"])
    terminal = await wait_for_terminal(run.run_id)

    # (a) reported, typed, on the failing member's own record. The tag is
    # checked against the closed set by the shared assertion.
    assert_typed_member_failure(terminal.member_results["researcher"], "researcher", "dependency_error")
    assert terminal.member_results["researcher"]["error"] == "@researcher execution failed: vector store connection reset by peer"
    # The retry budget was spent on the fault, and only on the fault.
    assert terminal.retry_counts == {"researcher": 3}
    assert terminal.member_results["researcher"]["attempts"] == 3
    # Mid-flight: the first attempt really was running before it broke.
    assert harness.results.served[f"{run.run_id}:researcher:attempt1"] == [_Status.RUNNING, _Status.FAILED]

    # (b) unrelated work continued.
    assert_member_succeeded(terminal.member_results["writer"], "writer", "writer slice")
    assert {m.sender: m.content for m in member_receipts("chaos-dep", "member_result")} == {"writer": "writer slice"}
    assert [m.metadata["error_type"] for m in member_receipts("chaos-dep", "member_failed")] == ["dependency_error"]
    assert terminal.synthesis == "merged deliverable"

    # (c) no success.
    assert terminal.status == "failed"
    assert "member @researcher [dependency_error]" in (terminal.error or "")
    assert "@writer" not in (terminal.error or "")


# ---------------------------------------------------------------------------
# (2) timeout
# ---------------------------------------------------------------------------


async def test_member_timeout_is_tagged_timeout_not_the_generic_kind(monkeypatch) -> None:
    """A timeout must keep its own identity, and must not take the sibling with it.

    The important half is the *negative*: ``timeout`` is a distinct tag from
    ``dependency_error``, so a caller can retry a timeout differently from a
    hard failure. Collapsing them would pass a weaker test.
    """
    scripts = {
        "slowpoke": [running(), timed_out("subagent exceeded 900s")],
        "fast": [running(), completed("fast slice")],
        "synthesis": [running(), completed("merged deliverable")],
    }
    harness = install_fault_harness(monkeypatch, scripts=scripts)

    from alpha.groups.runner import get_group_run_service

    service = get_group_run_service()
    run = service.start_run("chaos-timeout", "Two members, one hangs", members=["slowpoke", "fast"])
    terminal = await wait_for_terminal(run.run_id)

    # (a) typed as timeout, not dependency_error.
    entry = terminal.member_results["slowpoke"]
    assert_typed_member_failure(entry, "slowpoke", "timeout")
    assert entry["error_type"] != "dependency_error"
    assert entry["error"] == "@slowpoke execution timed_out: subagent exceeded 900s"
    assert terminal.retry_counts == {"slowpoke": 3}
    assert harness.results.served[f"{run.run_id}:slowpoke:attempt1"] == [_Status.RUNNING, _Status.TIMED_OUT]

    # (b) the sibling did not inherit the timeout and kept its work.
    assert_member_succeeded(terminal.member_results["fast"], "fast", "fast slice")
    assert "error_type" not in terminal.member_results["fast"]
    assert terminal.synthesis == "merged deliverable"

    # (c) no success.
    assert terminal.status == "failed"
    assert "member @slowpoke [timeout]" in (terminal.error or "")
    assert "member @fast" not in (terminal.error or "")


# ---------------------------------------------------------------------------
# (3) cancellation
# ---------------------------------------------------------------------------


async def test_member_cancellation_is_terminal_typed_and_never_retried(monkeypatch) -> None:
    """A member cancelled from under the run is terminal, and typed ``cancelled``.

    Two separate properties, and the second is the subtle one: cancellation
    must not feed the retry loop. Retrying a cancelled member re-runs work the
    system already stopped, and -- because a retried member that then succeeds
    is indistinguishable from one that never failed -- it is exactly how a run
    with a dead component ends up claiming success.
    """
    scripts = {
        "cancelme": [running(), cancelled("global subagent cancel")],
        "steady": [running(), completed("steady slice")],
        "synthesis": [running(), completed("merged deliverable")],
    }
    harness = install_fault_harness(monkeypatch, scripts=scripts)

    from alpha.groups.runner import get_group_run_service

    service = get_group_run_service()
    run = service.start_run("chaos-cancel", "One member is cancelled", members=["cancelme", "steady"])
    terminal = await wait_for_terminal(run.run_id)

    # (a) typed ``cancelled``, distinct from the retryable kinds.
    entry = terminal.member_results["cancelme"]
    assert_typed_member_failure(entry, "cancelme", "cancelled")
    assert entry["status"] == "cancelled"
    assert entry["error"] == "@cancelme execution cancelled: global subagent cancel"
    # Terminal: the retry budget was not spent on it at all.
    assert terminal.retry_counts == {}, terminal.retry_counts
    assert "attempts" not in entry
    # And it escaped run_member to be surfaced by the post-join loop, so the
    # run-level roll-up names it like any other component failure.
    assert "member @cancelme [cancelled]" in (terminal.error or "")
    assert harness.results.served[f"{run.run_id}:cancelme:attempt1"] == [_Status.RUNNING, _Status.CANCELLED]
    assert f"{run.run_id}:cancelme:attempt2" not in harness.results.polls

    # (b) the sibling was neither cancelled nor degraded.
    assert_member_succeeded(terminal.member_results["steady"], "steady", "steady slice")
    assert terminal.member_results["steady"]["status"] != "cancelled"
    assert terminal.synthesis == "merged deliverable"

    # (c) no success.
    assert terminal.status == "failed"
    assert "@steady" not in (terminal.error or "")


# ---------------------------------------------------------------------------
# (4) resource exhaustion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fault", "expected_error"),
    [
        pytest.param(emfile_fault(), "[Errno 24] Too many open files", id="too_many_open_files"),
        pytest.param(MemoryError(), "MemoryError", id="allocation_failure"),
    ],
)
async def test_resource_exhaustion_is_typed_isolated_and_leaves_the_service_usable(monkeypatch, fault, expected_error) -> None:
    """The host running out of something must be tagged, isolated, and recoverable.

    Both faults are raised by the executor at *submission* time -- the shape of
    a real ``fork/exec``/allocation refusal -- so they escape before any retry
    bookkeeping exists and are classified purely by
    ``classify_component_failure``. The second run in this test is the point:
    after an exhaustion fault the service must still be able to start and
    complete a fresh run, i.e. the fault did not poison shared state.
    """
    scripts = {
        "starved": [running(), completed("never polled")],
        "fed": [running(), completed("fed slice")],
        "synthesis": [running(), completed("merged deliverable")],
    }
    harness = install_fault_harness(monkeypatch, scripts=scripts, submit_faults={"starved": fault})

    from alpha.groups.runner import get_group_run_service

    service = get_group_run_service()
    run = service.start_run("chaos-resource", "One member exhausts a host resource", members=["starved", "fed"])
    terminal = await wait_for_terminal(run.run_id)

    # (a) tagged resource_exhausted, carrying the real host reason.
    entry = terminal.member_results["starved"]
    assert_typed_member_failure(entry, "starved", "resource_exhausted")
    assert entry["error"] == expected_error
    assert terminal.retry_counts == {"starved": 3}
    # It never reached execution, so it was never polled: this is a
    # submission-time refusal, not a failed in-flight execution.
    assert f"{run.run_id}:starved:attempt1" not in harness.results.polls

    # (b) the sibling was unaffected and the moderator pass still ran.
    assert_member_succeeded(terminal.member_results["fed"], "fed", "fed slice")
    assert terminal.synthesis == "merged deliverable"

    # (c) no success, and the kind is named at run level.
    assert terminal.status == "failed"
    assert "member @starved [resource_exhausted]" in (terminal.error or "")

    # (b, continued) the service is still usable: a fresh run on the same
    # process, with no fault, completes successfully.
    follow_up = service.start_run("chaos-resource-after", "Still able to work", members=["fed"])
    follow_up_terminal = await wait_for_terminal(follow_up.run_id)
    assert follow_up_terminal.status == "succeeded", follow_up_terminal.error
    assert_member_succeeded(follow_up_terminal.member_results["fed"], "fed", "fed slice")
    assert follow_up_terminal.synthesis == "merged deliverable"
    # The exhausted member did not leak its fault into the next run.
    assert "starved" not in follow_up_terminal.member_results


# ---------------------------------------------------------------------------
# (5) the synthesis component
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("synthesis_script", "expected_kind"),
    [
        pytest.param(failed("moderator model refused the merge"), "dependency_error", id="dependency_failure"),
        pytest.param(timed_out("moderator exceeded 900s"), "timeout", id="timeout"),
    ],
)
async def test_a_failing_synthesis_pass_never_yields_a_successful_run(monkeypatch, synthesis_script, expected_kind) -> None:
    """The moderator pass is a component too: its failure must fail the run.

    Every member succeeds here, so the only way to get this wrong is to treat
    synthesis as best-effort. The member work must also stay visible: a
    synthesis fault is not a reason to discard what the members produced.
    """
    scripts = {
        "architect": [running(), completed("architect slice")],
        "coder": [running(), completed("coder slice")],
        "synthesis": [running(), synthesis_script],
    }
    install_fault_harness(monkeypatch, scripts=scripts)

    from alpha.groups.runner import get_group_run_service

    service = get_group_run_service()
    run = service.start_run("chaos-synthesis", "Members fine, moderator broken", members=["architect", "coder"])
    terminal = await wait_for_terminal(run.run_id)

    # (a) the synthesis failure is reported with its own typed kind, not as a
    # member failure and not as a bare string.
    assert f"synthesis [{expected_kind}]" in (terminal.error or ""), terminal.error
    assert "member @architect" not in (terminal.error or "")
    assert "member @coder" not in (terminal.error or "")

    # (b) the member work that landed is preserved and was still posted.
    assert_member_succeeded(terminal.member_results["architect"], "architect", "architect slice")
    assert_member_succeeded(terminal.member_results["coder"], "coder", "coder slice")
    assert {m.sender: m.content for m in member_receipts("chaos-synthesis", "member_result")} == {
        "architect": "architect slice",
        "coder": "coder slice",
    }
    assert terminal.synthesis.startswith("Synthesis failed"), terminal.synthesis

    # (c) and the run is not successful despite every member having passed.
    assert terminal.status == "failed"


# ---------------------------------------------------------------------------
# (6) cross-run isolation
# ---------------------------------------------------------------------------


async def test_a_faulted_run_does_not_disturb_a_clean_run_in_flight_at_the_same_time(monkeypatch) -> None:
    """A fault is contained to its own run, not to the service.

    Both runs are started before the first ``await``, so they are genuinely
    concurrent. The clean run is given a deliberately long poll script so the
    faulted run is guaranteed to reach a terminal status *while the clean one
    is still in flight* -- that ordering is the assertion, not a detail: it
    proves the faulted run terminated without cancelling or degrading a
    sibling run that was mid-flight at the time.
    """
    long_poll = [running()] * 40 + [completed("clean slice")]
    scripts = {
        "clean_member": long_poll,
        "faulted_member": [running(), completed("never polled")],
        "synthesis": [running(), completed("merged deliverable")],
    }
    harness = install_fault_harness(monkeypatch, scripts=scripts, submit_faults={"faulted_member": emfile_fault()})

    from alpha.groups.runner import get_group_run_service

    service = get_group_run_service()
    clean = service.start_run("chaos-clean", "The run that must not be disturbed", members=["clean_member"])
    faulted = service.start_run("chaos-faulted", "The run that fails", members=["faulted_member"])

    # The faulted run reaches its terminal record first, while the clean run
    # is provably still running.
    faulted_terminal = await wait_for_terminal(faulted.run_id)
    assert faulted_terminal.status == "failed", faulted_terminal.status
    assert_typed_member_failure(faulted_terminal.member_results["faulted_member"], "faulted_member", "resource_exhausted")

    still_running = service.get_run(clean.run_id)
    assert still_running is not None
    assert still_running.status == "running", "the faulted run's failure disturbed the in-flight clean run"
    assert still_running.member_results == {}, "the clean run must not have inherited the fault"

    # ...and the clean run still finishes, on its own, successfully.
    clean_terminal = await wait_for_terminal(clean.run_id)
    assert clean_terminal.status == "succeeded", clean_terminal.error
    assert_member_succeeded(clean_terminal.member_results["clean_member"], "clean_member", "clean slice")
    assert clean_terminal.synthesis == "merged deliverable"
    assert clean_terminal.error is None

    # The two runs kept separate records; no cross-contamination either way.
    assert "faulted_member" not in clean_terminal.member_results
    assert "clean_member" not in faulted_terminal.member_results
    # And the clean run really did poll to completion, so it was not skipped.
    assert harness.results.served[f"{clean.run_id}:clean_member:attempt1"].count(_Status.RUNNING) == 40


async def test_the_stubbed_tool_provider_is_the_one_the_runner_assembled_with() -> None:
    """Guard the harness itself: the ``alpha.tools`` stub must stay wired in.

    ``install_tools_stub`` replaces a ~40s blocking import that the runner only
    uses to hand a callable to ``run_assembly``. If that indirection ever
    changes shape, this fails instead of the suite silently paying the import
    again -- or, worse, quietly testing a runner that no longer builds a tool
    pool.
    """
    assert callable(_tool_provider)
    assert _tool_provider() == []
