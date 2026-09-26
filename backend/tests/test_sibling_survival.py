"""Sibling isolation for autonomous group runs, plus the fault harness it needs.

``GroupRunService._execute`` fans one objective out to every room member,
joins the members, then runs a moderator synthesis pass over whatever the
members produced. That join is the fault boundary of the whole feature: if a
member's failure escapes it, the *siblings* pay for it.

The join used to be::

    await asyncio.gather(*(run_member(m) for m in run.members))

so the first member to raise propagated straight out of ``_execute``. The
consequences were all silent, and all sibling-shaped:

* the moderator pass never ran, so the work that *did* land was never merged;
* the failing member got no record of its own -- ``member_results`` simply
  had no entry for it, and nothing anywhere carried a typed reason;
* ``_forget_task`` caught the escaped exception and stamped the run ``failed``
  with the raw exception text, which is the only trace a caller ever saw.

It is now ``return_exceptions=True`` followed by an explicit per-member
surfacing loop, so every escaping exception is turned into a typed
``error_type`` on *that* member's own record.

Every test below is written to fail against the old join. They do not assert
"the run failed"; they assert the three things the old join destroyed: the
surviving sibling's record and its exact output, the failing member's own
typed record, and the fact that the run still reached the synthesis phase.
A join that merely stops propagating would satisfy the first assertion and
fail the second and third.

``install_fault_harness`` is the shared fault-injection harness. It is
re-exported to ``test_chaos_fault_injection.py``, which drives the same
runner through the four fault classes.
"""

from __future__ import annotations

import asyncio
import errno
import importlib
import sys
from enum import Enum
from types import ModuleType, SimpleNamespace

import pytest

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# timing
# ---------------------------------------------------------------------------
#
# The runner polls background subagent results on a wall clock
# (``_POLL_SECONDS = 2.0``) and exponentially backs off between member
# retries (``_RETRY_BACKOFF_CAP = 10.0``). Those are the module globals the
# production code reads on every attempt, so patching them shrinks the
# *timeline* without changing the ordering, the retry count, or the number
# of polls a member gets. The full three-attempt fan-in below therefore
# executes in well under a second instead of ~10s.
_FAST_POLL_SECONDS = 0.01
_FAST_RETRY_BACKOFF_CAP = 0.01

#: A hang guard, not a correctness budget. The stubbed fan-in settles in
#: ~0.2s; this only exists so a run that never reaches a terminal status
#: fails loudly instead of wedging CI. It is never used to *rescue* a test.
_TERMINAL_WAIT_SECONDS = 10.0


# ---------------------------------------------------------------------------
# fault-injection harness
# ---------------------------------------------------------------------------


class _Status(Enum):
    """A real ``SubagentStatus`` stand-in.

    ``tests/conftest.py`` replaces ``alpha.subagents.executor`` in
    ``sys.modules`` with a MagicMock (the real module has a circular import),
    so ``SubagentStatus`` arrives as a bare mock class. The runner
    identity-compares statuses and reads ``.is_terminal``, so the stand-in
    must be a genuine enum with a real ``is_terminal``.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        return self in {
            type(self).COMPLETED,
            type(self).FAILED,
            type(self).CANCELLED,
            type(self).TIMED_OUT,
        }


# -- scripted background results -------------------------------------------
#
# The runner polls ``get_background_task_result(execution_id)`` on a loop
# until the status is terminal, so a script is a *list consumed one entry per
# poll*. ``[running(), failed(...)]`` therefore means "the execution really
# started, and then failed mid-flight on the second poll" -- which is what
# makes these mid-flight faults rather than submit-time rejections. The last
# entry repeats once the script is exhausted, so later retry attempts see
# the same terminal fault.


def running() -> SimpleNamespace:
    return SimpleNamespace(status=_Status.RUNNING, result=None, error=None)


def completed(output: str) -> SimpleNamespace:
    return SimpleNamespace(status=_Status.COMPLETED, result=output, error=None)


def failed(error: str) -> SimpleNamespace:
    return SimpleNamespace(status=_Status.FAILED, result=None, error=error)


def timed_out(error: str) -> SimpleNamespace:
    return SimpleNamespace(status=_Status.TIMED_OUT, result=None, error=error)


def cancelled(error: str) -> SimpleNamespace:
    return SimpleNamespace(status=_Status.CANCELLED, result=None, error=error)


def default_deliverable(member: str) -> SimpleNamespace:
    return completed(f"{member} deliverable")


class _ResultFactory:
    """Serves scripted background results and records what it served.

    ``served[execution_id]`` is the ordered list of statuses that member was
    actually handed, which is how a test proves a fault landed *mid-flight*
    (a ``RUNNING`` was served before the failure) rather than the execution
    never having started.
    """

    def __init__(self, scripts: dict[str, list[SimpleNamespace]], assembly_callables: list) -> None:
        self._scripts = dict(scripts)
        self._cursor: dict[str, int] = {}
        self.assembly_callables = assembly_callables
        self.polls: dict[str, int] = {}
        self.served: dict[str, list[_Status]] = {}

    def __call__(self, execution_id: str) -> SimpleNamespace:
        self.polls[execution_id] = self.polls.get(execution_id, 0) + 1
        member = _member_of(execution_id)
        steps = self._scripts.get(member) or [default_deliverable(member)]
        index = min(self._cursor.get(member, 0), len(steps) - 1)
        self._cursor[member] = self._cursor.get(member, 0) + 1
        step = steps[index]
        self.served.setdefault(execution_id, []).append(step.status)
        return step


class _ScriptedExecutor:
    """Stands in for ``SubagentExecutor``.

    ``submit_faults`` maps a member to an exception raised out of
    ``execute_async`` itself -- the shape of a real dependency that refuses
    the work at submission time (fork/exec ``EMFILE``, an allocation failure
    while importing a transport). Everything else submits cleanly and is
    resolved by the scripted result poller.
    """

    def __init__(self, submit_faults: dict[str, BaseException] | None = None, **_kwargs) -> None:
        self._submit_faults = submit_faults or {}

    def execute_async(self, prompt, task_id=None, **_kwargs):
        member = _member_of(task_id)
        fault = self._submit_faults.get(member)
        if fault is not None:
            raise fault
        return task_id


def _member_of(execution_id: str) -> str:
    """``grun_ab12:architect:attempt1`` -> ``architect``."""
    return execution_id.split(":")[1]


def attempt_id(run_id: str, member: str, attempt: int) -> str:
    return f"{run_id}:{member}:attempt{attempt}"


def _tool_provider() -> list:
    return []


def install_tools_stub(monkeypatch) -> ModuleType:
    """Stand in for the ``alpha.tools`` module the runner imports lazily.

    ``_execute`` does ``from alpha.tools import get_available_tools`` and hands
    the callable to ``run_assembly``. The real import drags in ~40s of
    blocking module loading *inside the run's own event loop*, to produce a
    value nothing in this path consumes (``run_assembly`` is replaced below,
    and the tool pool it would return is only handed to a stubbed executor).

    The stub keeps the wiring real -- the runner still resolves the attribute
    off ``alpha.tools`` and still passes the very callable through to the
    assembly call -- so ``harness.assembly_callables`` can prove the tool-pool
    step was exercised rather than skipped. Restored at teardown.
    """
    alpha_package = importlib.import_module("alpha")
    stub = ModuleType("alpha.tools")
    stub.get_available_tools = _tool_provider
    monkeypatch.setitem(sys.modules, "alpha.tools", stub)
    monkeypatch.setattr(alpha_package, "tools", stub, raising=False)
    return stub


def install_fault_harness(
    monkeypatch,
    *,
    scripts: dict[str, list[SimpleNamespace]] | None = None,
    submit_faults: dict[str, BaseException] | None = None,
) -> SimpleNamespace:
    """Neutralise the model/tool layers and drive the runner's own clock.

    Everything patched here is a *boundary* the runner reaches for lazily
    inside ``_execute`` (``from alpha.x import y`` on each call), so the real
    group-run control flow, retry loop, join and failure accounting are the
    code under test. Returns ``harness.results`` (the scripted background
    result factory, for poll-timeline assertions) and
    ``harness.assembly_callables`` (what ``run_assembly`` was handed).
    """
    import alpha.config as agent_workspace_config
    import alpha.groups.runner as runner
    import alpha.utils.assembly_io as assembly_io
    from alpha.subagents import config as subagent_config
    from alpha.subagents import executor as executor_mod

    monkeypatch.setattr(runner, "_POLL_SECONDS", _FAST_POLL_SECONDS)
    monkeypatch.setattr(runner, "_RETRY_BACKOFF_CAP", _FAST_RETRY_BACKOFF_CAP)

    # A truthy model layer gets past the no-models fail-fast gate without a
    # provider ever being contacted.
    monkeypatch.setattr(agent_workspace_config, "get_app_config", lambda: SimpleNamespace(models=["stub-model"]))
    monkeypatch.setattr(subagent_config, "resolve_subagent_model_name", lambda *args, **kwargs: "stub-model")

    assembly_callables: list = []

    async def _no_tools(get_available_tools, *_args, **_kwargs):
        assembly_callables.append(get_available_tools)
        return []

    monkeypatch.setattr(assembly_io, "run_assembly", _no_tools)
    install_tools_stub(monkeypatch)

    monkeypatch.setattr(executor_mod, "SubagentStatus", _Status)
    monkeypatch.setattr(executor_mod, "SubagentExecutor", lambda **kwargs: _ScriptedExecutor(submit_faults))
    results = _ResultFactory(scripts or {}, assembly_callables)
    monkeypatch.setattr(executor_mod, "get_background_task_result", results)
    return SimpleNamespace(results=results, assembly_callables=assembly_callables)


# ---------------------------------------------------------------------------
# assertions shared with the chaos suite
# ---------------------------------------------------------------------------


def isolate_group_singletons(monkeypatch, tmp_path) -> None:
    """Point every process-wide group/bot singleton at a private workspace."""
    import alpha.bots.registry as bot_reg
    import alpha.groups.runner as runner
    import alpha.groups.service as grp_svc

    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    monkeypatch.setattr(bot_reg, "_global_registry", None)
    monkeypatch.setattr(bot_reg, "_global_registry_path", None)
    monkeypatch.setattr(grp_svc, "_global_groups", None)
    monkeypatch.setattr(grp_svc, "_global_groups_path", None)
    monkeypatch.setattr(runner, "_global_runner", None)
    monkeypatch.setattr(runner, "_global_runner_path", None)


async def wait_for_terminal(run_id: str, *, timeout: float = _TERMINAL_WAIT_SECONDS):
    """Await the run's terminal record, or fail the test if it never lands."""
    from alpha.groups.runner import get_group_run_service

    async def _poll():
        while True:
            run = get_group_run_service().get_run(run_id)
            assert run is not None, f"group run {run_id} vanished from the service"
            if run.status != "running":
                return run
            await asyncio.sleep(_FAST_POLL_SECONDS)

    return await asyncio.wait_for(_poll(), timeout=timeout)


def assert_typed_member_failure(entry: dict, member: str, error_type: str) -> None:
    """Assert ``entry`` is a *typed* failure record, not prose or silence.

    The tag must come from the closed ``MEMBER_ERROR_TYPES`` set (so callers
    can branch on it), the failure must name its own member, and it must
    carry a human message. A missing ``error_type`` here is the exact
    signature of the pre-fix swallow.
    """
    from alpha.groups.runner import MEMBER_ERROR_TYPES

    assert isinstance(entry, dict), f"@{member} has no result record at all: {entry!r}"
    assert error_type in MEMBER_ERROR_TYPES, f"{error_type!r} is outside the closed set {sorted(MEMBER_ERROR_TYPES)}"
    assert entry.get("error_type") == error_type, f"@{member} expected {error_type!r}, got {entry!r}"
    assert entry.get("status") in {"failed", "cancelled"}, f"@{member} must be terminal, got {entry!r}"
    assert entry.get("error"), f"@{member} typed failure carries no message: {entry!r}"
    assert f"@{member}" in (entry.get("output") or ""), f"@{member} record does not name its own member: {entry!r}"


def assert_member_succeeded(entry: dict, member: str, output: str) -> None:
    """Assert a healthy sibling's record survived intact, byte for byte."""
    assert entry.get("status") == "done", f"@{member} should be done, got {entry!r}"
    assert entry.get("output") == output, f"@{member} output was corrupted: {entry!r}"
    assert "error" not in entry, f"@{member} picked up a failure it never had: {entry!r}"
    assert "error_type" not in entry, f"@{member} picked up a typed error it never had: {entry!r}"


def member_receipts(room_name: str, phase: str) -> list[SimpleNamespace]:
    """Room-log messages the run posted for one phase, oldest first."""
    from alpha.groups.service import get_group_chat_service

    room = get_group_chat_service().get_room(room_name)
    assert room is not None, f"room {room_name!r} was never created"
    return [m for m in room.log if m.metadata.get("phase") == phase]


def emfile_fault() -> OSError:
    return OSError(errno.EMFILE, "Too many open files")


# ---------------------------------------------------------------------------
# sibling isolation
# ---------------------------------------------------------------------------


async def test_escaping_member_exception_does_not_cancel_its_siblings(monkeypatch, tmp_path) -> None:
    """A member that raises *out of* ``run_member`` must not sink the fan-in.

    The injected fault is the purest form of the bug: ``bots.get_or_create``
    blows up for one member before any retry machinery exists to catch it,
    so the exception escapes ``run_member`` entirely and lands on the gather.

    Teeth against the old join: there, ``_execute`` re-raised, so
    (a) the failing member never got a record of its own -- ``member_results``
    had no entry for it at all,
    (b) the moderator pass never ran, leaving ``synthesis`` unset, and
    (c) ``_forget_task`` stamped the run with the bare exception text and
    no ``[kind]`` tag.
    """
    isolate_group_singletons(monkeypatch, tmp_path)
    scripts = {
        "survivor": [running(), completed("survivor slice")],
        "other": [running(), completed("other slice")],
        "boom": [running(), completed("never reached")],
        "synthesis": [running(), completed("merged deliverable")],
    }
    harness = install_fault_harness(monkeypatch, scripts=scripts)
    factory = harness.results

    from alpha.bots.registry import get_bot_registry
    from alpha.groups.runner import get_group_run_service

    service = get_group_run_service()
    # "survivor" leads the list, so it is the moderator: the synthesis pass
    # must be able to resolve it *after* the sibling fault.
    run = service.start_run("sib-escape", "Ship the fault isolation", members=["survivor", "other", "boom"])

    # Patched after start_run (which itself resolves every profile) and
    # before the first await, so the fault is guaranteed to land inside
    # run_member rather than during admission.
    registry = get_bot_registry()
    real_get_or_create = registry.get_or_create

    def _explode(name):
        if name == "boom":
            raise RuntimeError("bot profile store unreachable")
        return real_get_or_create(name)

    monkeypatch.setattr(registry, "get_or_create", _explode)

    terminal = await wait_for_terminal(run.run_id)

    # (b) unrelated work continues: both healthy siblings kept their exact
    # output, and the moderator pass still ran over it.
    assert_member_succeeded(terminal.member_results["survivor"], "survivor", "survivor slice")
    assert_member_succeeded(terminal.member_results["other"], "other", "other slice")
    assert terminal.synthesis == "merged deliverable", terminal.synthesis
    assert {m.sender: m.content for m in member_receipts("sib-escape", "member_result")} == {
        "survivor": "survivor slice",
        "other": "other slice",
    }

    # (a) the fault is reported as a typed error on the failing member's own
    # record -- not as a raw string on the run, and not on a sibling.
    assert_typed_member_failure(terminal.member_results["boom"], "boom", "dependency_error")
    assert terminal.member_results["boom"]["error"] == "bot profile store unreachable"

    # (c) the run does not claim success, and its error names the member and
    # the kind rather than leaking a bare traceback-ish string.
    assert terminal.status == "failed", terminal.status
    assert "member @boom [dependency_error]" in (terminal.error or ""), terminal.error
    assert "@survivor" not in (terminal.error or "")
    assert "@other" not in (terminal.error or "")

    # The faulted member never reached execution, so it was never polled --
    # proving the fault was a raise, not a failed poll.
    assert factory.polls.get(attempt_id(run.run_id, "boom", 1)) is None

    # The run really went through the tool-pool step (and the stubbed
    # ``alpha.tools`` import resolved), rather than the harness short-
    # circuiting past the assembly the way a real provider-less run would.
    assert harness.assembly_callables == [_tool_provider]


async def test_typed_member_failure_never_lands_on_a_sibling_record(monkeypatch, tmp_path) -> None:
    """Fault accounting is per member: no bleed in either direction.

    Two independent invariants in one run, because a single record cannot
    express "isolated" on its own:
      * the failing member owns the only typed error in ``member_results``;
      * the healthy sibling's entry has no failure keys at all, so the
        record a reader treats as authoritative is never a mix.
    """
    isolate_group_singletons(monkeypatch, tmp_path)
    scripts = {
        "steady": [running(), completed("steady slice")],
        "flaky": [running(), failed("upstream returned 503")],
        "synthesis": [running(), completed("merged deliverable")],
    }
    install_fault_harness(monkeypatch, scripts=scripts)

    from alpha.groups.runner import get_group_run_service

    service = get_group_run_service()
    run = service.start_run("sib-typed", "Deliver with a flaky dependency", members=["steady", "flaky"])
    terminal = await wait_for_terminal(run.run_id)

    assert_member_succeeded(terminal.member_results["steady"], "steady", "steady slice")
    assert_typed_member_failure(terminal.member_results["flaky"], "flaky", "dependency_error")
    assert terminal.member_results["flaky"]["error"] == "@flaky execution failed: upstream returned 503"

    # The two records are genuinely separate objects, not one shared dict.
    assert terminal.member_results["steady"] is not terminal.member_results["flaky"]
    assert set(terminal.member_results) == {"steady", "flaky"}

    assert terminal.status == "failed"
    assert (terminal.error or "").count("[") == 1, terminal.error


async def test_failed_member_leaves_the_other_members_and_synthesis_intact(monkeypatch, tmp_path) -> None:
    """One member of three failing must not degrade the other two, nor the run.

    A three-way fan-in with all three slots occupied, so this is the real
    concurrency shape and not a serialised one.
    """
    isolate_group_singletons(monkeypatch, tmp_path)
    scripts = {
        "architect": [running(), completed("architect slice")],
        "coder": [running(), completed("coder slice")],
        "flaky": [running(), failed("model provider refused the request")],
        "synthesis": [running(), completed("merged deliverable")],
    }
    factory = install_fault_harness(monkeypatch, scripts=scripts).results

    from alpha.groups.runner import get_group_run_service

    service = get_group_run_service()
    run = service.start_run(
        "sib-three",
        "Fan out to three specialists",
        members=["architect", "coder", "flaky"],
        max_parallel=3,
    )
    terminal = await wait_for_terminal(run.run_id)

    assert_member_succeeded(terminal.member_results["architect"], "architect", "architect slice")
    assert_member_succeeded(terminal.member_results["coder"], "coder", "coder slice")
    assert_typed_member_failure(terminal.member_results["flaky"], "flaky", "dependency_error")
    assert terminal.synthesis == "merged deliverable"

    # Every surviving member posted its own receipt, so the room log is a
    # complete and honest account of the run.
    survivors = {m.sender: m.content for m in member_receipts("sib-three", "member_result")}
    assert survivors == {"architect": "architect slice", "coder": "coder slice"}
    failure_receipts = member_receipts("sib-three", "member_failed")
    assert [m.metadata["error_type"] for m in failure_receipts] == ["dependency_error"]

    # The retry budget was actually spent on the faulted member and only on
    # it: 1 + max_retries(2) attempts, each of which really was in flight.
    assert terminal.retry_counts == {"flaky": 3}
    assert factory.served[attempt_id(run.run_id, "flaky", 1)] == [_Status.RUNNING, _Status.FAILED]
    assert attempt_id(run.run_id, "flaky", 4) not in factory.polls

    # The run does not claim success either.
    assert terminal.status == "failed", terminal.status
    assert "member @flaky [dependency_error]" in (terminal.error or "")


async def test_every_escaping_failure_is_surfaced_not_just_the_first(monkeypatch, tmp_path) -> None:
    """The post-join loop must cover *each* member, not the first exception.

    A join that captured a single failure (or that re-raised the first) would
    pass the one-fault tests above while still losing the second member's
    record. Two members fault here, both *escaping* ``run_member`` so the
    post-join loop is the only thing that can report them, and with two
    different classified kinds so per-member classification is proven too.

    Teeth against the old join: ``_execute`` re-raised the first escaping
    exception, so ``@broken_b`` never got a record at all, the moderator pass
    never ran, and the run carried only one component's raw text.
    """
    isolate_group_singletons(monkeypatch, tmp_path)
    scripts = {
        "survivor": [running(), completed("survivor slice")],
        "synthesis": [running(), completed("merged deliverable")],
    }
    harness = install_fault_harness(monkeypatch, scripts=scripts)

    from alpha.bots.registry import get_bot_registry
    from alpha.groups.runner import get_group_run_service

    service = get_group_run_service()
    # "survivor" leads, so it is also the moderator: the synthesis pass must
    # be able to resolve it after both sibling faults have landed.
    run = service.start_run(
        "sib-multi",
        "Two members hit different host faults",
        members=["survivor", "broken_a", "broken_b"],
        max_parallel=3,
    )

    registry = get_bot_registry()
    real_get_or_create = registry.get_or_create
    faults = {
        "broken_a": emfile_fault(),
        "broken_b": TimeoutError("bot profile store lookup exceeded its bound"),
    }

    def _explode(name):
        fault = faults.get(name)
        if fault is not None:
            raise fault
        return real_get_or_create(name)

    monkeypatch.setattr(registry, "get_or_create", _explode)

    terminal = await wait_for_terminal(run.run_id)

    # Two distinct kinds, each on its own member's own record.
    assert_typed_member_failure(terminal.member_results["broken_a"], "broken_a", "resource_exhausted")
    assert_typed_member_failure(terminal.member_results["broken_b"], "broken_b", "timeout")
    assert terminal.member_results["broken_a"]["error"] == "[Errno 24] Too many open files"
    assert terminal.member_results["broken_b"]["error"] == "bot profile store lookup exceeded its bound"

    # Neither fault reached execution, so neither was ever polled, and the
    # healthy sibling was never given a failure.
    assert harness.results.polls.get(attempt_id(run.run_id, "broken_a", 1)) is None
    assert harness.results.polls.get(attempt_id(run.run_id, "broken_b", 1)) is None
    assert_member_succeeded(terminal.member_results["survivor"], "survivor", "survivor slice")

    # Both faults are named separately, with their own kinds, in the
    # run-level error: a reader sees two components failed, not one.
    error = terminal.error or ""
    assert "member @broken_a [resource_exhausted]" in error, error
    assert "member @broken_b [timeout]" in error, error
    assert terminal.status == "failed"
    # And the run still reached the moderator pass over the survivor.
    assert terminal.synthesis == "merged deliverable"
