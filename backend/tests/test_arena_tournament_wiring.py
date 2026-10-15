"""The arena tournament report: proof the four advanced primitives are cited.

``task_contract.py``, ``candidate.py``, ``evidence.py`` and ``synthesis.py`` were
exported, documented and unit-tested but **no executor ever called them**. A
completed tournament produced no requirement coverage, no claim ledger and no
merge, and three separate documents said so — an admission the gap was known.

These tests drive the real :class:`ArenaExecutor` through a real bracket on the
scripted runner seam (no model calls, exactly like the rest of ``test_arena_*``)
and assert the finished run document cites all four. They are the end-to-end
evidence for that wiring; without them the claim rests on reading the executor.

The honesty properties are asserted too, because a report that exists but
over-claims is worse than no report:

* coverage is **measured** from each competitor's own solution file, so a
  competitor that wrote nothing about the requirement lands in ``missing``;
* every claim is ``UNVERIFIED`` — the arena performs no retrieval, so nothing may
  be recorded as ``SUPPORTED``;
* the synthesis is a **plan** (``executed: false``);
* a report that could not be assembled says so, which is a different statement
  from a report that assembled and found nothing.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest.mock

import pytest

from alpha.arena import ArenaStatus, bracket
from alpha.arena.executor import ArenaExecutor
from alpha.arena.modes import IMPLEMENTED_MODES, REFUSED_MODES, RunMode, describe_modes
from alpha.arena.runner import NO_OUTPUT, ArenaJob, ArenaJobResult, ScriptedArenaRunner
from alpha.arena.store import ArenaStore
from alpha.arena.tournament import assemble_tournament_report

#: A task whose requirement keywords are decidable from the artifacts alone.
TASK = "Build the parser and satisfy every acceptance criterion the reviewer names"

_GOOD_SOLUTION = (
    "# Parser\n\n"
    "## Approach\n\n"
    "This parser satisfies the request by tokenising the input once and then\n"
    "building an explicit state table. It satisfies the boundary cases too:\n"
    "empty input, unicode, and a trailing delimiter with no value.\n\n"
    "## Why this satisfies the criteria\n\n"
    "The reviewer can satisfy themselves that each criterion has a test.\n"
)
#: Names no requirement keyword, so its coverage must not read as `covered`.
_EMPTY_SOLUTION = "# Parser\n\nNo notes were produced for this competitor.\n"


def _script(solutions: dict[str, str] | None = None) -> ScriptedArenaRunner:
    """A deterministic runner whose spawns write chosen solution bodies."""
    bodies = solutions or {}

    def script(job: ArenaJob) -> ArenaJobResult:
        if job.kind == "spawn":
            return ArenaJobResult(ok=True, text=bodies.get(job.agent_id, _GOOD_SOLUTION))
        if job.kind == "attack":
            return ArenaJobResult(ok=True, text="ATTACK 1 [MINOR] nits\nWhere: line 2\nProblem: unclear")
        if job.kind == "defend":
            return ArenaJobResult(ok=True, text="ATTACK 1: CONCEDE. fixed\nWROTE " + job.reply_hint)
        if job.kind == "judge":
            return ArenaJobResult(
                ok=True,
                text=(
                    '{"scores": {"a": {"correctness": 9, "completeness": 8, "specificity": 7, "robustness": 8, "clarity": 8, "fatal": false}, '
                    '"b": {"correctness": 5, "completeness": 4, "specificity": 4, "robustness": 4, "clarity": 5, "fatal": false}}, '
                    '"winner": "a", "reason": "a is better"}'
                ),
            )
        if job.kind == "final":
            return ArenaJobResult(ok=True, text="VERDICT: PASS\nREASON: passes")
        return ArenaJobResult(ok=False, text="", error=NO_OUTPUT)

    return ScriptedArenaRunner(script)


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ArenaStore(tmp)


async def _run(store: ArenaStore, *, agents: int = 2, task: str = TASK, runner: ScriptedArenaRunner | None = None) -> dict:
    """Drive a complete tournament and return the finished run document."""
    from alpha.arena.cards import deal_cards, load_deck

    state = bracket.new_run(owner_id="test", task=task, seed="7", agents_n=agents, wave=2)
    deck = load_deck()
    cards = deal_cards(agents, "7", deck)
    bracket.set_cards(state, dict(zip(bracket.agent_ids(agents), cards, strict=True)))
    store.save(state)
    return await ArenaExecutor(store, runner or _script(), wave=2, max_judge_attempts=1).execute(state)


class TestPerWavePersistenceIsIntact:
    """A regression guard for a bug this change nearly introduced.

    Adding the report to ``execute()`` de-indented the per-wave
    ``self._store.save(state)`` by one level, so it ran once after the whole
    bracket instead of once per wave. Every arena test still passed: none of them
    crashes mid-run, so none of them could see it. The per-wave save is what the
    executor's resumability claim rests on — "a Gateway restart loses at most one
    wave of work" — so it is pinned here rather than left to inspection.
    """

    @pytest.mark.asyncio
    async def test_the_store_is_saved_after_every_wave(self, store: ArenaStore) -> None:
        state = bracket.new_run(owner_id="test", task=TASK, seed="7", agents_n=4, wave=2)
        from alpha.arena.cards import deal_cards, load_deck

        cards = deal_cards(4, "7", load_deck())
        bracket.set_cards(state, dict(zip(bracket.agent_ids(4), cards, strict=True)))

        saves: list[dict] = []
        original = store.save

        def counting_save(document: dict) -> None:
            saves.append(json.loads(json.dumps(document)))
            original(document)

        store.save = counting_save  # type: ignore[method-assign]
        finished = await ArenaExecutor(store, _script(), wave=2, max_judge_attempts=1).execute(state)

        assert finished["status"] == ArenaStatus.COMPLETED.value
        # 4 agents, wave 2 => two spawn waves, then two rounds of match work.
        # Anything at or below one save per completed wave is the regression.
        assert len(saves) >= 4, f"expected a save per wave, saw {len(saves)}"
        # Every save except the last must predate the final state, or the
        # intermediate waves were never actually persisted.
        intermediate = saves[:-1]
        assert any(doc.get("rounds") for doc in intermediate) or any(doc.get("solutions") for doc in intermediate), "no intermediate save captured partial progress"


class TestTheReportIsWired:
    @pytest.mark.asyncio
    async def test_a_completed_run_cites_all_four_primitives(self, store: ArenaStore) -> None:
        """The gap this file exists to close.

        Every one of the four modules had tests and no caller. After the bracket
        reaches a terminal state the run document must carry a ``tournament``
        block with the contract, the candidate artifacts, the evidence ledger and
        the synthesis — so a live run finally cites them.
        """
        state = await _run(store)

        assert state["status"] == ArenaStatus.COMPLETED.value
        report = state["tournament"]
        assert report["assembled"] is True
        assert report["assembled_by"] == "alpha.arena.tournament:assemble_tournament_report"

        contract = report["task_contract"]
        assert contract["original_request"] == TASK
        assert contract["requirements"], "the contract must carry at least the derived requirement"

        assert report["candidate_count"] == 2
        assert set(report["coverage"]) == {"agent-1", "agent-2"}

        assert report["evidence"]["run_id"] == state["run_id"]
        assert "counts" in report["evidence"]

        assert report["synthesis"]["run_id"] == state["run_id"]
        assert report["synthesis"]["executed"] is False

    @pytest.mark.asyncio
    async def test_the_report_reaches_the_service_reads(self, store: ArenaStore) -> None:
        """A report nobody can read is not a report.

        ``ArenaService.status`` and ``.winner`` are what the tool and the HTTP
        router project. Before this wiring the four primitives were reachable
        only by opening the run document by hand, which is exactly why they read
        as unwired.
        """
        from alpha.arena.config import ArenaConfig
        from alpha.arena.service import ArenaService

        state = await _run(store)
        service = ArenaService(store, config=ArenaConfig(reasoning_bank_seed=False))

        assert service.status(state["run_id"], "test")["tournament"]["assembled"] is True
        assert service.winner(state["run_id"], "test")["tournament"]["evidence"]["gate_tripped"] is False


class TestCoverageIsMeasured:
    @pytest.mark.asyncio
    async def test_two_artifacts_get_two_different_readings(self, store: ArenaStore) -> None:
        """A status comes from each artifact, not from the judge.

        One competitor writes a solution naming the requirement's keywords, the
        other writes nothing relevant. The report must score them differently —
        one global "covered" would be a fabricated reading of two different
        artifacts.
        """
        state = await _run(store, runner=_script({"agent-1": _GOOD_SOLUTION, "agent-2": _EMPTY_SOLUTION}))
        coverage = state["tournament"]["coverage"]
        statuses = {agent: next(iter(rows.values())) for agent, rows in coverage.items()}

        assert statuses["agent-1"] == "covered", "the substantive solution names the requirement's keywords"
        assert statuses["agent-2"] != "covered", "an artifact that never mentions the requirement cannot read as covered"
        assert statuses["agent-2"] in {"partial", "missing"}

    @pytest.mark.asyncio
    async def test_an_unreadable_artifact_is_named_not_dropped(self, store: ArenaStore) -> None:
        """A competitor whose solution file vanished is a disclosed fact.

        Reading it as empty content would claim "this competitor said nothing
        about the requirement", which is a different statement from "we could not
        read what it said".
        """
        state = await _run(store)
        winner = state["champion"]
        store.work_path(state["run_id"], f"{winner}-solution.md").unlink(missing_ok=True)

        executor = ArenaExecutor(store, _script(), wave=2, max_judge_attempts=1)
        executor._assemble_report(state)  # noqa: SLF001 - exercising the re-assembly path directly

        unreadable = state["tournament"]["unreadable_artifacts"]
        assert unreadable, "a vanished solution file must be reported by name"
        assert any(winner in reason for reason in unreadable)


class TestContributionAttribution:
    @pytest.mark.asyncio
    async def test_a_competitor_is_scored_on_its_own_side_only(self, store: ArenaStore) -> None:
        """``scores["a"]`` is a *label*, not a competitor.

        The judge scores label A and label B, and the executor deliberately
        swaps which physical competitor those labels carry (``pair_order`` /
        ``judge_presentation.swapped``) to defeat position bias. A report that
        read ``scores["a"]`` unconditionally would therefore credit a competitor
        with its opponent's rubric total — the same attribution error the judge's
        own blinding exists to prevent, reintroduced one layer up.
        """
        state = await _run(store, runner=_script(), agents=2)
        report = state["tournament"]
        assert report["assembled"] is True

        by_agent = {row["agent_id"]: row for row in report["candidates"]}
        assert set(by_agent) == {"agent-1", "agent-2"}

        match = state["rounds"][0]["matches"][0]
        scores = match["verdict"]["scores"]
        for agent_id, artifact in by_agent.items():
            side = "a" if match["a"] == agent_id else "b"
            qualities = {row["quality"] for row in artifact["contributions"]}
            assert qualities == {round(float(scores[side]["total"]))}, f"{agent_id} was scored from the wrong side: {qualities} vs {scores[side]['total']}"

    @pytest.mark.asyncio
    async def test_an_unscored_side_is_zero_rather_than_the_opponents_total(self, store: ArenaStore) -> None:
        """A competitor that reached no judged match has no rubric total.

        Reporting the opponent's would be the same bug wearing a different hat,
        so the unscored value is ``0`` and the contribution stays non-reusable.
        """
        state = await _run(store, runner=_script(), agents=2)
        # Drop every verdict, as if no judge had ever returned a parsable block.
        for round_record in state["rounds"]:
            for match in round_record["matches"]:
                match.pop("verdict", None)

        report = assemble_tournament_report(state, lambda agent_id: store.work_path(state["run_id"], f"{agent_id}-solution.md"))

        assert report["assembled"] is True
        for artifact in report["candidates"]:
            assert {row["quality"] for row in artifact["contributions"]} == {0}
            assert all(row["reusable"] is False for row in artifact["contributions"])


class TestEvidenceHonesty:
    @pytest.mark.asyncio
    async def test_no_claim_is_ever_supported(self, store: ArenaStore) -> None:
        """The arena performs no retrieval, so ``SUPPORTED`` would be invented.

        Judges score solutions against a rubric; nothing in the bracket checks a
        claim against an external source. A ``SUPPORTED`` verdict here would be
        exactly the fabricated evidence the ledger's own contract forbids, so the
        wiring records every claim ``UNVERIFIED`` and the gate stays untripped.
        """
        state = await _run(store)
        evidence = state["tournament"]["evidence"]

        assert evidence["counts"]["total"] > 0, "the fixtures produce solution text with claim-shaped lines"
        verdicts = {check["verdict"] for check in evidence["checks"]}
        assert "SUPPORTED" not in verdicts, "the arena runs no retrieval step; a SUPPORTED claim would be fabricated"
        assert verdicts == {"UNVERIFIED"}
        assert evidence["gate_tripped"] is False, "an unverified claim must never fail a candidate"
        assert evidence["support_ratio"] is None, "a support ratio over zero checked claims is unknown, not 0.0"


class TestSynthesisIsAPlan:
    @pytest.mark.asyncio
    async def test_the_merge_plan_is_not_executed(self, store: ArenaStore) -> None:
        """``synthesize`` returns sections; nothing here writes them anywhere.

        A merged document nobody merged is the exact fabrication the primitive's
        own docstring forbids, so the block says so explicitly and a reader cannot
        mistake a plan for an applied patch.
        """
        state = await _run(store)
        synthesis = state["tournament"]["synthesis"]

        assert synthesis["executed"] is False
        assert "executed_note" in synthesis
        assert synthesis["sources"], "the synthesis names which competitors contributed"


class TestTheReportNeverOverClaims:
    @pytest.mark.asyncio
    async def test_a_budget_exhausted_run_reports_no_champion_honestly(self, store: ArenaStore) -> None:
        """A stopped run says there is nothing to report, rather than nothing.

        Budget exhaustion lands before the bracket finished, so there is no
        champion and no final verdict. The block is present and explicitly
        ``assembled: false`` — an absent key would be indistinguishable from a
        run that predates the wiring, and an assembled-but-empty report would
        read as "no requirement was left uncovered".
        """
        state = bracket.new_run(owner_id="test", task=TASK, seed="7", agents_n=2, wave=2, max_subagent_calls=0)
        store.save(state)
        finished = await ArenaExecutor(store, _script(), wave=2, max_judge_attempts=1).execute(state)

        assert finished["status"] == "budget_exhausted"
        report = finished["tournament"]
        assert report["assembled"] is False
        assert "budget_exhausted" in report["reason"]
        assert "never reached a champion" in report["note"]
        assert "candidate_count" not in report, "a report that did not assemble must not carry an empty measurement set"

    @pytest.mark.asyncio
    async def test_a_report_that_cannot_be_assembled_says_so(self, store: ArenaStore, monkeypatch: pytest.MonkeyPatch) -> None:
        """A degraded report is distinguishable from a clean one.

        ``assembled: false`` with the real reason is the only honest answer when
        assembly itself fails: without it, a reader sees an empty ``gaps`` list
        and concludes nothing was left uncovered, when in fact nothing was
        measured. The completed tournament itself is still completed — a report
        failure must not retroactively fail real work.
        """
        state = await _run(store)
        assert state["tournament"]["assembled"] is True

        # Drive the degraded branch directly: the builder's own try/except is the
        # code under test, and reaching it through a second full tournament would
        # exercise the fixture rather than the branch.
        executor = ArenaExecutor(store, _script(), wave=2, max_judge_attempts=1)
        monkeypatch.setattr(
            "alpha.arena.executor.assemble_tournament_report",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("workspace vanished mid-report")),
        )
        executor._assemble_report(state)  # noqa: SLF001

        report = state["tournament"]
        assert report["assembled"] is False
        assert "RuntimeError: workspace vanished mid-report" in report["reason"]
        assert "no coverage number or evidence verdict may be quoted" in report["note"]
        assert state["status"] == ArenaStatus.COMPLETED.value, "the tournament itself is unaffected"

    @pytest.mark.asyncio
    async def test_the_report_is_built_off_the_event_loop(self, store: ArenaStore) -> None:
        """A synchronous read of every solution file cannot run on the loop.

        An arena run is driven from a Gateway route, so a report built inline
        would stall every other run in the process for the length of N artifact
        reads. The blocker guard that would catch it lives in
        ``tests/blocking_io/`` and does not cover this route, so it is pinned
        here instead.
        """
        state = await _run(store, runner=_script({"agent-1": _GOOD_SOLUTION, "agent-2": _EMPTY_SOLUTION}))
        executor = ArenaExecutor(store, _script(), wave=2, max_judge_attempts=1)

        loops: list[str] = []

        def record_thread(_self, _state: dict) -> dict:
            loops.append(threading.current_thread().name)
            return {"assembled": True}

        with unittest.mock.patch.object(ArenaExecutor, "_build_report_block", record_thread):
            await executor._finish(state)  # noqa: SLF001

        assert loops, "the report builder never ran"
        assert loops[0] != threading.current_thread().name, "the report was assembled inline on the event loop"


class TestModeVocabularyIsHonest:
    def test_refused_modes_are_reported_as_refused(self) -> None:
        """``describe_modes`` must not advertise a mode that raises on start.

        ``compare`` and ``synthesize`` are declared by the plan and refused by
        name. The ``profiles`` action renders this table to the model, so a row
        without ``implemented`` / ``refusal_reason`` would present a run that
        raises as something the engine can do.
        """
        rows = {row["mode"]: row for row in describe_modes()}

        assert rows["decide"]["implemented"] is True
        assert rows["plan"]["implemented"] is True
        for mode in ("compare", "synthesize"):
            assert rows[mode]["implemented"] is False, f"{mode} is refused by name and must not read as available"
            assert rows[mode]["refusal_reason"], f"{mode} needs the reason it is refused"
            assert "NOT IMPLEMENTED" in rows[mode]["description"]

        assert IMPLEMENTED_MODES == frozenset({RunMode.DECIDE, RunMode.PLAN})
        assert set(REFUSED_MODES) == {RunMode.COMPARE, RunMode.SYNTHESIZE}

    def test_the_service_still_refuses_the_modes_the_table_reports(self) -> None:
        """The reporting half and the enforcement half must agree.

        ``describe_modes`` and ``service._check_mode`` are two descriptions of one
        fact. A mode reported as refused but no longer raising means the table is
        stale; one reported as implemented but raising means the table is a lie.
        """
        from alpha.arena.service import _check_mode

        for mode in REFUSED_MODES:
            with pytest.raises(ValueError, match="declared but not implemented"):
                _check_mode(mode.value, start=False)
        assert _check_mode(RunMode.DECIDE.value, start=False) == RunMode.DECIDE.value


class TestReportIsSerializable:
    @pytest.mark.asyncio
    async def test_the_report_round_trips_through_the_store_document(self, store: ArenaStore) -> None:
        """The block is part of the durable run document, so it must be JSON.

        The store writes ``state`` as one JSON object; a report carrying a
        dataclass, a set or a Path would make every subsequent save of the run
        fail — after the tournament had already completed and been reported as
        successful.
        """
        state = await _run(store)
        store.save(state)
        loaded = store.load(state["run_id"], "test")

        assert loaded["tournament"]["assembled"] is True
        assert json.loads(json.dumps(loaded["tournament"]))["assembled"] is True
