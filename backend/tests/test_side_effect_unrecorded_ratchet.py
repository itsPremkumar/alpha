"""The ratchet: the explicit set of irreversible effects that are STILL unrecorded.

Why this file exists
--------------------
`tests/test_side_effects_are_recorded.py` proves the ledger is wired at the sites
chosen for the first, narrow pass. It does **not** prove the rest of Alpha is
wired, and it must not pretend to: it is wired at one site, and the honest
statement of that is a list of the sites it is *not* wired at.

So this file is the other half. It names the remaining irreversible effects as
data, and asserts two things about that data:

* the set is not empty (a claim of full coverage fails here), and
* the set is small and explicit (a growing unaccounted surface fails here).

**This is a ratchet, not a lie, on two conditions that are enforced below:** the
set is a literal in this file rather than a computed scan (a computed scan would
silently absorb a new un-wired site, which is the opposite of a ratchet), and it
is asserted to be a strict, hand-reviewed subset of the irreversible effect
families this subsystem was scoped to cover — so a new family cannot be added to
the un-recorded list without also failing the coverage assertion that names it.

The point of the ratchet is the *direction* it turns. Removing a name from
``UNRECORDED_IRREVERSIBLE_EFFECTS`` is a claim: "this effect is now recorded".
That claim is checked by ``test_every_named_unrecorded_site_is_really_unwired``
below, which greps the named module for the recorder. Wire a site without
removing it from the list and that test fails. Remove a site without wiring it
and ``test_no_named_site_actually_calls_the_recorder`` fails. There is no edit
to this file that makes an unrecorded effect look recorded.

What is and is not claimed
--------------------------
Wired today: exactly one effect family — a durable MCP task *submit*
(`app/mcp_tasks/service.py::McpTaskService.submit`). See
`tests/test_side_effects_are_recorded.py` for the proof, including that the
bracket precedes the remote call, that a ledger outage does not fail the action,
and that a lost worker becomes ``UNKNOWN``.

Not wired, explicitly: everything in ``UNRECORDED_IRREVERSIBLE_EFFECTS`` below.
The run-level safety net is *not* nothing — ``SafeRunRecoveryService`` still
refuses to auto-replay a checkpoint whose pending node is a tool, MCP, shell,
browser, write/delete, payment, or unknown node, and records
``recovery_confirmation_required``. That is a real, tested, run-level guarantee
and this change does not weaken it. What it cannot do is name the *specific*
effect that needs confirming, which is what the ledger row is for.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

import pytest

from alpha.runtime.side_effects import side_effect_recorder_stats

#: The backend root, derived from this file so the paths below are checked
#: against a real tree rather than against whatever the test runner's cwd is.
_BACKEND: Final[Path] = Path(__file__).resolve().parents[1]


class UnrecordedIrreversibleEffect:
    """One irreversible effect family the ledger is not yet wired to."""

    __slots__ = ("effect", "module", "site", "why_it_matters")

    def __init__(self, *, effect: str, module: str, site: str, why_it_matters: str) -> None:
        self.effect = effect
        self.module = module
        self.site = site
        self.why_it_matters = why_it_matters

    def __repr__(self) -> str:  # pragma: no cover - failure output only
        return f"UnrecordedIrreversibleEffect({self.effect!r} at {self.module}:{self.site})"

    def source(self) -> str:
        path = _BACKEND / self.module
        assert path.is_file(), f"{self.module} no longer exists; the ratchet names a file that was moved or removed, so the un-recorded set is now wrong rather than merely incomplete: {self}"
        return path.read_text(encoding="utf-8")


#: Every irreversible effect this change did **not** wire, named individually.
#:
#: Order is stable and meaningful: the same order appears in the report, so a
#: reader can diff the two. Do not reorder for tidiness; append at the end.
UNRECORDED_IRREVERSIBLE_EFFECTS: Final[tuple[UnrecordedIrreversibleEffect, ...]] = (
    UnrecordedIrreversibleEffect(
        effect="sandbox.shell_command",
        module="packages/harness/alpha/sandbox/tools.py",
        site="bash_tool",
        why_it_matters="A process spawn is the widest irreversible surface in Alpha: it can push, deploy, charge, or delete, and the sandbox has no way to distinguish a read from a write before running it.",
    ),
    UnrecordedIrreversibleEffect(
        effect="mcp.synchronous_tool_call",
        module="packages/harness/alpha/mcp/tools.py",
        site="call_with_persistent_session",
        why_it_matters="The durable submit is recorded; the ordinary (non-background) MCP tool call is not, and a third-party MCP server can charge, write, or send for any tool name.",
    ),
    UnrecordedIrreversibleEffect(
        effect="workspace.file_write",
        module="packages/harness/alpha/sandbox/tools.py",
        site="write_file / str_replace",
        why_it_matters="A write is irreversible from the agent's point of view once the model has been told it happened, and `RunJournal`'s delivery receipt only *detects* a changed file after the fact rather than recording the attempt.",
    ),
    UnrecordedIrreversibleEffect(
        effect="git.publish",
        module="packages/harness/alpha/sandbox/tools.py",
        site="git_push / git_merge / git_rebase",
        why_it_matters="A push is the one effect whose duplicate is visible to a third party. `normalize_level` already classifies it HIGH_RISK; nothing calls that classification.",
    ),
    UnrecordedIrreversibleEffect(
        effect="host.computer_use",
        module="packages/harness/alpha/tools/builtins/os_computer_tool.py",
        site="os_computer_tool",
        why_it_matters="A click or keystroke on the host desktop is irreversible and leaves no artifact in the workspace for a reconciler to check.",
    ),
    UnrecordedIrreversibleEffect(
        effect="host.system_one_action",
        module="packages/harness/alpha/tools/builtins/computer_system_one_tool.py",
        site="computer_system_one_tool",
        why_it_matters="Same as the host computer-use tool, behind a different policy gate, so a single host-side effect can arrive through either door.",
    ),
    UnrecordedIrreversibleEffect(
        effect="im.outbound_message",
        module="app/channels/manager.py",
        site="ChannelManager outbound send",
        why_it_matters="A message sent to a person cannot be unsent, and it leaves Alpha entirely, so no local artifact distinguishes 'never sent' from 'sent, response lost'.",
    ),
    UnrecordedIrreversibleEffect(
        effect="self_update.source_mutation",
        module="packages/harness/alpha/evolution/update_engine.py",
        site="UpdateEngine.apply_now",
        why_it_matters="A fast-forwarded source checkout is a durable external mutation with its own backup ref and maintenance barrier, and it is deliberately not a tool call, so no other guard sees it.",
    ),
)


#: The effect families this subsystem was scoped to cover. A **literal**, not a
#: union of the two sets above, and that is the entire point.
#:
#: The first version of this file derived the inventory from
#: ``UNRECORDED_IRREVERSIBLE_EFFECTS``, which made the coverage assertion
#: self-consistent under deletion: dropping three un-recorded families removed
#: them from the inventory too, and the test passed. A ratchet that tightens
#: when you cut lines is not a ratchet. So the inventory is written out
#: independently, and removing a name from the ratchet without removing it from
#: here fails :func:`test_the_inventory_is_not_derived_from_the_ratchet`.
#:
#: This is still a hand-maintained list, which is a real limitation and is stated
#: rather than hidden: it can go stale. What it cannot do is go stale
#: *silently in the flattering direction*. A new irreversible effect nobody adds
#: here is invisible to this file -- that is the remaining gap, and the honest
#: description of this subsystem's coverage, not a claim of exhaustiveness.
SCOPED_IRREVERSIBLE_EFFECT_FAMILIES: Final[frozenset[str]] = frozenset(
    {
        # Wired by this change.
        "mcp.durable_task_submit",
        # Not wired. Each maps 1:1 onto an entry in the ratchet above.
        "mcp.synchronous_tool_call",
        "sandbox.shell_command",
        "workspace.file_write",
        "git.publish",
        "host.computer_use",
        "host.system_one_action",
        "im.outbound_message",
        "self_update.source_mutation",
    }
)

_WIRED_EFFECT_FAMILIES: Final[frozenset[str]] = frozenset({"mcp.durable_task_submit"})

_ALL_EFFECT_FAMILIES: Final[frozenset[str]] = _WIRED_EFFECT_FAMILIES | frozenset(entry.effect for entry in UNRECORDED_IRREVERSIBLE_EFFECTS)


class TestTheRatchetIsHonest:
    def test_the_ratchet_is_not_empty(self) -> None:
        """A claim of full coverage must fail here rather than pass quietly.

        The honest state of this change is *partial*. If somebody later wires the
        remainder and deletes this list wholesale, this is the test that stops
        the claim from being made without the evidence.
        """
        assert UNRECORDED_IRREVERSIBLE_EFFECTS, "the un-recorded list was emptied; either wire the remainder or say so in a test that proves it"

    def test_every_named_site_exists_and_names_its_module(self) -> None:
        """Each entry points at a real file, so the list cannot rot into fiction."""
        for entry in UNRECORDED_IRREVERSIBLE_EFFECTS:
            assert entry.module, entry
            assert entry.site, entry
            assert entry.why_it_matters, f"{entry.effect} has no recorded reason, so it is an unaccounted name rather than a tracked gap"
            assert entry.source(), f"{entry.module} is empty"

    def test_the_named_effects_are_distinct(self) -> None:
        names = [entry.effect for entry in UNRECORDED_IRREVERSIBLE_EFFECTS]
        assert len(names) == len(set(names)), f"duplicate effect names in the ratchet: {names}"

    def test_the_coverage_table_names_every_unrecorded_family(self) -> None:
        """Every un-recorded family is a declared family, and vice versa.

        This is what makes the list reviewable rather than a dumping ground: a
        new irreversible effect cannot join the ratchet without also appearing in
        the scoped inventory, and adding it to the inventory without wiring it
        fails here.
        """
        unrecorded = {entry.effect for entry in UNRECORDED_IRREVERSIBLE_EFFECTS}
        assert unrecorded <= _ALL_EFFECT_FAMILIES
        assert unrecorded == _ALL_EFFECT_FAMILIES - _WIRED_EFFECT_FAMILIES, "an effect family changed state (wired or newly scoped) without the ratchet being updated to say which"

    def test_the_inventory_is_not_derived_from_the_ratchet(self) -> None:
        """The assertion that makes this a ratchet rather than a self-description.

        ``SCOPED_IRREVERSIBLE_EFFECT_FAMILIES`` is a literal. If the two sets
        disagree, somebody changed one without the other -- which in the
        flattering direction means "an un-recorded effect stopped being listed".
        """
        assert _ALL_EFFECT_FAMILIES == SCOPED_IRREVERSIBLE_EFFECT_FAMILIES, (
            "the effect inventory and the un-recorded ratchet disagree. "
            f"only in the inventory: {sorted(SCOPED_IRREVERSIBLE_EFFECT_FAMILIES - _ALL_EFFECT_FAMILIES)}; "
            f"only in the ratchet: {sorted(_ALL_EFFECT_FAMILIES - SCOPED_IRREVERSIBLE_EFFECT_FAMILIES)}. "
            "An effect cannot leave the ratchet without also leaving the inventory, or the ratchet is just describing itself."
        )
        assert _WIRED_EFFECT_FAMILIES < SCOPED_IRREVERSIBLE_EFFECT_FAMILIES, "at least one effect family must actually be wired, or this subsystem does nothing"

    def test_the_unrecorded_set_is_small_and_bounded(self) -> None:
        """A ratchet that can absorb anything is not a ratchet.

        The bound is a number, not a shape: adding a ninth family requires
        editing this constant in the same change that adds the family, which is
        the reviewable moment.
        """
        assert len(UNRECORDED_IRREVERSIBLE_EFFECTS) <= 8, f"the un-recorded irreversible set grew past its bound: {[entry.effect for entry in UNRECORDED_IRREVERSIBLE_EFFECTS]}"


class TestTheRatchetCannotBeFaked:
    """These are the tests that make the list a measurement, not a claim."""

    def test_no_named_site_actually_calls_the_recorder(self) -> None:
        """If a named site is wired, the ratchet is lying. Fail instead.

        ``announce_effect`` is the only production entry point into the recorder
        (``SideEffectRecorder.announce`` is reached through it, and the accessor
        is only installed by the Gateway), so its absence is the absence of
        wiring. The alternative — searching for any of several spellings — would
        let a rename make the ratchet pass for the wrong reason.
        """
        for entry in UNRECORDED_IRREVERSIBLE_EFFECTS:
            body = entry.source()
            assert "announce_effect" not in body, (
                f"{entry.effect} is named as un-recorded in UNRECORDED_IRREVERSIBLE_EFFECTS but {entry.module} now calls announce_effect. "
                f"Remove it from the ratchet and say in the report that it is wired -- do not leave the list claiming a gap that no longer exists."
            )

    def test_the_wired_site_is_the_one_and_only_announce_call(self) -> None:
        """Production wiring is one site, and this pins that count.

        Without this, a future change could wire three more sites and never touch
        the ratchet: the ratchet only fails on a *named* site becoming wired, so
        something has to notice an *unnamed* one appearing. That is this test.
        """
        hits: list[str] = []
        for path in sorted(_BACKEND.rglob("*.py")):
            relative = path.relative_to(_BACKEND).as_posix()
            if relative.startswith(("packages/harness/alpha/runtime/side_effects/", "tests/")):
                continue
            body = path.read_text(encoding="utf-8")
            if "announce_effect" in body and "from alpha.runtime.side_effects import" in body:
                hits.append(relative)

        assert hits == ["app/mcp_tasks/service.py"], (
            f"the set of production sites calling announce_effect changed: {hits}. "
            "If a new site was wired on purpose, add it to _WIRED_EFFECT_FAMILIES and drop its entry from UNRECORDED_IRREVERSIBLE_EFFECTS. "
            "If one was wired by accident, this is the test that caught it."
        )

    def test_the_recorder_is_reachable_only_through_the_documented_accessor(self) -> None:
        """A second installer would be a second source of truth.

        ``set_side_effect_recorder`` is process state, so exactly one module may
        call it in production. This fails if an effect site tries to install its
        own ledger rather than resolving the one the Gateway installed.
        """
        installers: list[str] = []
        for path in sorted(_BACKEND.rglob("*.py")):
            relative = path.relative_to(_BACKEND).as_posix()
            if relative.startswith(("packages/harness/alpha/runtime/side_effects/", "tests/")):
                continue
            body = path.read_text(encoding="utf-8")
            if re.search(r"\bset_side_effect_recorder\s*\(", body) and not relative.endswith("app/gateway/deps.py"):
                installers.append(relative)
        assert installers == [], f"only app/gateway/deps.py may install the process-wide recorder; also found: {installers}"


class TestTheLedgerIsNotASecondLifecycleOwner:
    """The constraint that bounded the design, pinned so it cannot be eroded."""

    def test_the_recorder_never_resumes_or_cancels_a_run(self) -> None:
        """It writes rows. It decides nothing.

        ``RunManager`` is the sole run lifecycle owner and
        ``SafeRunRecoveryService`` is the only safe-continuation authority. A
        recorder that grew a resume path would be a second authority bypassing
        the side-effect gate it exists to serve.
        """
        from alpha.runtime.side_effects import recorder

        body = (Path(recorder.__file__)).read_text(encoding="utf-8")
        # Code only, never prose: the module docstring *has* to name the two
        # owners in order to disclaim them, and a substring scan over the whole
        # file would therefore fail on the very documentation that makes the
        # boundary honest. An AST scan sees imports, attribute access, and calls
        # -- an actual reference -- and ignores the string.
        tree = ast.parse(body)
        referenced: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    referenced.add(alias.name.split(".")[0])
        forbidden = ("SafeRunRecoveryService", "RunManager", "reconcile_orphaned", "start_run", "transition_recovery_stop_reason", "replay_run")
        for token in forbidden:
            assert token not in referenced, f"the recorder references {token!r} in code; it must record, never decide whether work continues"

    def test_the_wired_site_does_not_become_a_continuation_authority(self) -> None:
        """The submit path records an effect; it does not gain a resume path."""
        from app.mcp_tasks import service as mcp_task_service

        body = (Path(mcp_task_service.__file__)).read_text(encoding="utf-8")
        assert "SafeRunRecoveryService" not in body, "the MCP task service must not acquire a safe-continuation authority"
        assert "RunManager" not in body, "the MCP task service must not become a run lifecycle owner"

    def test_an_uncertain_outcome_is_never_settled_as_a_definite_failure(self) -> None:
        """The reason the bracket's exception path settles nothing.

        Recording "it raised" as ``FAILED`` for an irreversible effect is the
        optimistic guess the whole package refuses to make, so the test asserts
        the *behaviour* rather than trusting a comment: an entry whose effect
        raised is never ``FAILED`` and never ``COMPLETED`` before reclaim.
        """
        import asyncio
        import importlib.util
        from pathlib import Path as _Path

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from alpha.mcp.tasks import McpTaskDriverRegistry, TaskSnapshot, TaskStatus, TaskSubmission, TaskSubmitRequest
        from alpha.persistence.base import Base
        from alpha.persistence.side_effects import SqlSideEffectLedger
        from alpha.runtime.side_effects import SideEffectRecorder, set_side_effect_recorder
        from alpha.runtime.side_effects.statuses import SideEffectStatus
        from app.mcp_tasks.service import McpTaskService

        # The doubles are loaded from the sibling file by path rather than
        # imported as ``tests.<name>``: ``tests`` is not a package, and copying
        # them here would let the two copies drift -- which would be a ratchet
        # measuring a different service than the one under test.
        spec = importlib.util.spec_from_file_location("_side_effect_wired_doubles", _Path(__file__).with_name("test_side_effects_are_recorded.py"))
        assert spec is not None and spec.loader is not None
        doubles = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(doubles)

        engine = create_async_engine("sqlite+aiosqlite://")

        async def _create() -> None:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

        asyncio.run(_create())
        ledger = SqlSideEffectLedger(async_sessionmaker(engine, expire_on_commit=False))
        set_side_effect_recorder(SideEffectRecorder(ledger, lease_seconds=1.0))
        try:

            async def scenario() -> object:
                repo = doubles.FakeRepository()
                driver = doubles.FakeDriver(
                    submission=TaskSubmission(remote_task_id="r", snapshot=TaskSnapshot(status=TaskStatus.SUBMITTED)),
                    submit_error=RuntimeError("ambiguous"),
                )
                registry = McpTaskDriverRegistry()
                registry.register("fake", driver)
                service = McpTaskService(repository=repo, drivers=registry, poll_interval_seconds=5, lease_seconds=120, max_concurrent_polls=3)
                request = TaskSubmitRequest(
                    user_id="user-1",
                    thread_id="thread-1",
                    run_id="run-1",
                    tool_call_id="call-ambiguous",
                    server_name="reports",
                    task_name="Generate report",
                    arguments={"topic": "MCP"},
                )
                try:
                    await service.submit(driver_name="fake", request=request)
                except RuntimeError:
                    pass
                return await ledger.get("call-ambiguous")

            entry = asyncio.run(scenario())
            assert entry is not None
            assert entry.status is SideEffectStatus.IN_FLIGHT, f"an ambiguous effect must stay unaccounted, not be reported as {entry.status}"
        finally:
            set_side_effect_recorder(None)
            asyncio.run(engine.dispose())


class TestTheRecorderCountersAreProcessStateNotDurableState:
    def test_the_counters_are_not_a_substitute_for_the_ledger(self) -> None:
        """Stated so nobody reads the in-process record as durable.

        The ledger row is the durable record. The counters are a bounded,
        in-process diagnostic that exists only so a bookkeeping outage is
        *reported*; they are lost on restart by construction, and the class
        docstring says so.
        """
        from alpha.runtime.side_effects import recorder as recorder_module

        assert recorder_module.UNACCOUNTED_EFFECT_MEMORY > 0, "the in-process record must be bounded, or it becomes a second unbounded store"
        body = (Path(recorder_module.__file__)).read_text(encoding="utf-8")
        assert "not durable" in body.lower() or "not a substitute" in body.lower(), "the module must state that the in-process record is not durable"

    def test_stats_are_readable_with_no_ledger_installed(self) -> None:
        """The accessor always returns a recorder, never ``None``.

        ``None`` would make "bookkeeping is not installed" and "bookkeeping
        failed" indistinguishable, and only one of those is worth a counter.
        """
        from alpha.runtime.side_effects import get_side_effect_recorder, set_side_effect_recorder

        set_side_effect_recorder(None)
        assert get_side_effect_recorder() is not None
        assert get_side_effect_recorder().enabled is False
        stats = side_effect_recorder_stats()
        assert stats["announced"] == 0
        assert set(stats) >= {"announced", "unavailable", "settled", "unrecorded", "unaccountable", "recent_unaccounted"}


@pytest.mark.parametrize("entry", UNRECORDED_IRREVERSIBLE_EFFECTS, ids=lambda entry: entry.effect)
def test_each_unrecorded_effect_is_individually_pinned(entry: UnrecordedIrreversibleEffect) -> None:
    """One parametrised test per named gap, so a failure names the effect."""
    assert entry.effect in _ALL_EFFECT_FAMILIES
    assert "announce_effect" not in entry.source()
