"""L1 staleness propagation into derived stores (Hy-Memory adoption slice #1).

Design: a dedup ``update``/``merge`` supersession removes the target record in
real time, but derived stores that indexed that record's content (the entity
mention index) accumulate with no reversal pass — the StateMem-style "state
drift" failure mode: the derived artifact survives the supersession of the
fact it was extracted from. This slice adds a deterministic, LLM-free MARKING
pass (never a deletion): when the gate ``memory.l1.staleness_propagation`` is
on, the pipeline walks the recorded mention lineage of every superseded
record, names the dependent entities in a bounded, id-only generation-log
entry, and counts them in ``RunReport.stale_dependents``.

Properties pinned here:

* default OFF — the gate is false and every existing ``apply_decisions``
  caller keeps byte-identical behaviour (no journal, count 0);
* the supersession ids are observed, never guessed — ``apply_decisions``
  fills the optional out-parameter only with ids the store CONFIRMED removed;
* marking is id-only and bounded; no record content enters the journal;
* failure of the entity layer can never fail an L1 run;
* ``RunReport.stale_dependents`` is an additive report field.

Every test is hermetic: an explicit ``MemoryConfig`` rooted in ``tmp_path``,
a scripted LLM fake, and an injected entity-memory fake. No timer, no
sleeping, no network, no gateway import.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from alpha.agents.memory.l1.dedup import apply_decisions
from alpha.agents.memory.l1.models import DedupDecision, DedupOutcome, MemoryRecord, RunReport
from alpha.agents.memory.l1.pipeline import CaptureJob, L1Pipeline
from alpha.agents.memory.l1.propagation import (
    DEPENDENT_LIMIT,
    StaleDependency,
    append_staleness_entry,
    collect_entity_dependents,
    staleness_journal_path,
)
from alpha.agents.memory.l1.store import L1RecordStore
from alpha.config.memory_config import L1MemoryConfig, MemoryConfig


def make_config(tmp_path: Any, **l1_overrides: Any) -> MemoryConfig:
    """Explicit config with L1 enabled against an isolated store."""
    l1 = {"enabled": True, "storage_path": str(tmp_path / "l1"), "debounce_seconds": 0.0, "persona_enabled": False}
    l1.update(l1_overrides)
    return MemoryConfig.model_validate({"enabled": True, "l1": l1})


def _update_case(content: str, targets: list[str]) -> tuple[list[MemoryRecord], DedupOutcome]:
    candidate = MemoryRecord.create(content, memory_type="persona")
    outcome = DedupOutcome(
        status="ok",
        decisions=[
            DedupDecision(
                record_id=candidate.id,
                action="update" if len(targets) <= 1 else "merge",
                target_ids=list(targets),
                merged_content=content,
                merged_type="persona",
            )
        ],
    )
    return [candidate], outcome


class FakeEntityMemory:
    """Duck-typed EntityMemory: ``entities_for_record`` only."""

    def __init__(self, by_record: dict[str, list[str]]) -> None:
        self._by_record = by_record

    def entities_for_record(self, record_id: str, *, user_id: str, include_unpromoted: bool = False) -> list[str]:
        return list(self._by_record.get(record_id, []))


# ---------------------------------------------------------------------------
# gate + report contract
# ---------------------------------------------------------------------------


def test_staleness_propagation_gate_defaults_off() -> None:
    assert L1MemoryConfig().staleness_propagation is False
    assert MemoryConfig().l1.staleness_propagation is False


def test_run_report_has_stale_dependents_key() -> None:
    report = RunReport(status="succeeded")
    assert report.stale_dependents == 0
    assert "stale_dependents" in report.to_dict()


# ---------------------------------------------------------------------------
# superseded-id observation channel
# ---------------------------------------------------------------------------


def test_apply_decisions_reports_confirmed_superseded_ids(tmp_path) -> None:
    """An update that deleted its target reports the target id."""
    store = L1RecordStore(str(tmp_path / "store"))
    old = MemoryRecord.create("Ravi prefers tea for the morning briefing.", memory_type="persona")
    store.put_records([old], user_id="u1", agent_name="agent")
    candidates, outcome = _update_case("Ravi prefers coffee for the morning briefing.", [old.id])

    superseded: list[str] = []
    apply_decisions(store, candidates, outcome, user_id="u1", agent_name="agent", superseded_ids=superseded)
    assert superseded == [old.id], "only ids the store confirmed removed"


def test_apply_decisions_reports_all_merged_targets(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path / "store"))
    first = MemoryRecord.create("Deploy grid uses region A.", memory_type="work_fact")
    second = MemoryRecord.create("Deploy grid uses region B.", memory_type="work_fact")
    store.put_records([first, second], user_id="u1", agent_name="agent")
    candidates, outcome = _update_case("Deploy grid uses region A and B.", [first.id, second.id])

    superseded: list[str] = []
    counts = apply_decisions(store, candidates, outcome, user_id="u1", agent_name="agent", superseded_ids=superseded)
    assert counts["merged"] == 1
    assert sorted(superseded) == sorted([first.id, second.id])


def test_apply_decisions_reports_nothing_by_default(tmp_path) -> None:
    """The out-parameter is opt-in: absent = no change; empty-run = empty."""
    store = L1RecordStore(str(tmp_path / "store"))
    old = MemoryRecord.create("Ravi prefers tea.", memory_type="persona")
    store.put_records([old], user_id="u1", agent_name="agent")
    candidates, outcome = _update_case("Ravi prefers coffee.", [old.id])

    counts = apply_decisions(store, candidates, outcome, user_id="u1", agent_name="agent")
    assert counts["updated"] == 1

    clean: list[str] = []
    apply_decisions(
        store,
        [MemoryRecord.create("Fresh unrelated note.", memory_type="episodic")],
        DedupOutcome(status="empty"),
        user_id="u1",
        agent_name="agent",
        superseded_ids=clean,
    )
    assert clean == []


def test_superseded_ids_never_claim_an_id_written_in_the_same_run(tmp_path) -> None:
    """A target written in the same run survives; the store confirms it."""
    store = L1RecordStore(str(tmp_path / "store"))
    old = MemoryRecord.create("Kiran reviews code on Thursdays.", memory_type="persona")
    store.put_records([old], user_id="u1", agent_name="agent")
    candidate = MemoryRecord.create(
        "Kiran reviews code on Fridays; the review day moved from last cycle.",
        memory_type="persona",
    )
    surrogate = MemoryRecord.create("Surrogate note for the same merge.", memory_type="episodic")
    outcome = DedupOutcome(
        status="ok",
        decisions=[
            DedupDecision(
                record_id=surrogate.id,
                action="update",
                target_ids=[old.id, candidate.id],
                merged_content="Surrogate note for the same merge.",
                merged_type="episodic",
            )
        ],
    )
    superseded: list[str] = []
    counts = apply_decisions(
        store,
        [candidate, surrogate],
        outcome,
        user_id="u1",
        agent_name="agent",
        superseded_ids=superseded,
    )
    assert counts["updated"] == 1
    assert candidate.id not in superseded
    assert old.id in superseded


# ---------------------------------------------------------------------------
# dependents collector
# ---------------------------------------------------------------------------


def test_collect_entity_dependents_walks_the_mention_lineage() -> None:
    memory = FakeEntityMemory({"rec-old": ["ent-alpha", "ent-beta"], "rec-other": ["ent-gamma"]})
    dependents = collect_entity_dependents(memory, user_id="u1", superseded_ids=["rec-old"])
    assert sorted(d.id for d in dependents) == ["ent-alpha", "ent-beta"]
    assert all(d.kind == "entity" for d in dependents)
    assert all(d.record_id == "rec-old" for d in dependents)


def test_collect_entity_dependents_is_bounded() -> None:
    memory = FakeEntityMemory({"rec-old": [f"ent-{index:02d}" for index in range(DEPENDENT_LIMIT + 10)]})
    dependents = collect_entity_dependents(memory, user_id="u1", superseded_ids=["rec-old"])
    assert len(dependents) == DEPENDENT_LIMIT


def test_collect_entity_dependents_dedupes_shared_entities() -> None:
    class OverlapMemory(FakeEntityMemory):
        def entities_for_record(self, record_id: str, *, user_id: str, include_unpromoted: bool = False) -> list[str]:
            if record_id in ("rec-a", "rec-b"):
                return ["ent-shared"]
            return []

    dependents = collect_entity_dependents(OverlapMemory({}), user_id="u1", superseded_ids=["rec-a", "rec-b"])
    assert [(d.kind, d.id) for d in dependents] == [("entity", "ent-shared")]


def test_collect_entity_dependents_never_raises() -> None:
    class ExplodingMemory:
        def entities_for_record(self, record_id: str, *, user_id: str, include_unpromoted: bool = False) -> list[str]:
            raise RuntimeError("entity layer blew up")

    dependents = collect_entity_dependents(ExplodingMemory(), user_id="u1", superseded_ids=["rec-old"])
    assert dependents == []


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------


def test_staleness_journal_entry_is_bounded_and_id_only(tmp_path) -> None:
    storage = str(tmp_path / "l1")
    append_staleness_entry(
        user_id="u1",
        superseded_record_ids=["rec-old"],
        dependents=[
            StaleDependency(kind="entity", id="ent-alpha", record_id="rec-old"),
            StaleDependency(kind="entity", id="ent-beta", record_id="rec-old"),
        ],
        storage_path=storage,
        truncated=False,
    )
    path = staleness_journal_path(storage, "u1")
    assert path.exists()
    line = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()][-1]
    entry = json.loads(line)
    assert entry["component"] == "l1_staleness"
    assert entry["superseded_record_ids"] == ["rec-old"]
    assert entry["stale_dependents"] == [
        {"kind": "entity", "id": "ent-alpha", "record_id": "rec-old"},
        {"kind": "entity", "id": "ent-beta", "record_id": "rec-old"},
    ]
    assert entry["truncated"] is False


def test_staleness_journal_append_never_raises(tmp_path) -> None:
    """A journal path obstructed by a directory is logged, not raised."""
    storage = str(tmp_path / "l1")
    path = staleness_journal_path(storage, "u3")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    path.mkdir()
    append_staleness_entry(
        user_id="u3",
        superseded_record_ids=["rec-y"],
        dependents=[],
        storage_path=storage,
        truncated=False,
    )


def test_staleness_journal_entry_carries_the_day_stamp(tmp_path) -> None:
    storage = str(tmp_path / "l1")
    stamp = datetime(2026, 10, 17, tzinfo=UTC).timestamp()
    append_staleness_entry(
        user_id="u1",
        superseded_record_ids=["rec-old"],
        dependents=[],
        storage_path=storage,
        truncated=False,
        now=stamp,
    )
    path = staleness_journal_path(storage, "u1", now=stamp)
    assert path.name == "2026-10-17.jsonl"


def test_staleness_journal_entry_discloses_truncation(tmp_path) -> None:
    storage = str(tmp_path / "l1")
    append_staleness_entry(
        user_id="u1",
        superseded_record_ids=[],
        dependents=[StaleDependency(kind="entity", id="ent-cap", record_id="rec-old")],
        storage_path=storage,
        truncated=True,
    )
    line = staleness_journal_path(storage, "u1").read_text(encoding="utf-8").splitlines()[-1]
    assert json.loads(line)["truncated"] is True


# ---------------------------------------------------------------------------
# pipeline wiring
# ---------------------------------------------------------------------------


class ScriptedUpdateModel:
    """Extraction reply + an update decision computed from the conflict prompt.

    The decision fires on the FIRST related candidate id the prompt presents
    for the first new memory (the seeded review-day record ranks first).
    """

    EXTRACTION = json.dumps(
        [
            {
                "scene_name": "review cadence",
                "message_ids": ["m1"],
                "memories": [
                    {
                        "content": "Ravi reviews code on Fridays at nine.",
                        "type": "persona",
                        "priority": 85,
                        "source_message_ids": ["m1"],
                        "metadata": {},
                    }
                ],
            }
        ]
    )

    def __init__(self) -> None:
        self.conflict_prompts: list[str] = []

    def invoke(self, prompt: str, config: dict[str, Any] | None = None) -> str:
        if "memory extraction expert" in prompt:
            return self.EXTRACTION
        if "conflict detector" in prompt:
            self.conflict_prompts.append(prompt)
            marker = "[Related candidate ids]"
            tail = prompt.split(marker, 1)[1].split("\n", 1)[0].strip()
            related = json.loads(tail)
            return json.dumps(
                [
                    {
                        "record_id": related and prompt.split("record_id: ", 1)[1].split(")", 1)[0],
                        "action": "update",
                        "target_ids": [related[0]],
                        "merged_content": "Ravi reviews code on Fridays at nine.",
                        "merged_type": "persona",
                    }
                ]
            )
        return "# Profile\nstub profile"


def _seeded_store(tmp_path: Any) -> tuple[L1RecordStore, MemoryRecord, MemoryRecord]:
    """Store with the to-be-replaced Thursday review record + two fillers."""
    store = L1RecordStore(str(tmp_path / "seed"))
    target = MemoryRecord.create("Ravi reviews code on Thursdays at nine.", memory_type="persona")
    filler_a = MemoryRecord.create("Planning the workshop agenda materials.", memory_type="episodic")
    filler_b = MemoryRecord.create("Shipping the release notes document.", memory_type="work_fact")
    store.put_records([target, filler_a, filler_b], user_id="u1", agent_name="agent")
    return store, target, filler_a


def _update_job() -> CaptureJob:
    return CaptureJob(
        thread_id="t1",
        user_id="u1",
        agent_name="agent",
        mode="chat",
        trace_id=None,
        messages=[message("human", "Ravi reviews code on Fridays now, at nine.")],
    )


class Msg:
    def __init__(self, type_: str, content: str, id_: str = "x") -> None:
        self.type = type_
        self.content = content
        self.id = id_
        self.tool_calls = None
        self.additional_kwargs: dict[str, Any] = {}


def message(type_: str, content: str) -> Msg:
    return Msg(type_, content)


def test_pipeline_marks_entity_dependents_when_gate_is_on(tmp_path) -> None:
    cfg = make_config(tmp_path, staleness_propagation=True)
    store, target, _filler = _seeded_store(tmp_path)
    fake = FakeEntityMemory({target.id: ["ent-person", "ent-schedule"]})
    pipeline = L1Pipeline(config=cfg, store=store, model=ScriptedUpdateModel(), entity_memory=fake)

    report = pipeline.run_job(_update_job())

    assert report.ok, report.error
    assert report.updated == 1
    assert report.stale_dependents == 2
    path = staleness_journal_path(str(store.root), "u1")
    line = path.read_text(encoding="utf-8").splitlines()[-1]
    entry = json.loads(line)
    assert entry["superseded_record_ids"] == [target.id]
    assert {item["id"] for item in entry["stale_dependents"]} == {"ent-person", "ent-schedule"}


def test_pipeline_without_gate_marks_nothing(tmp_path) -> None:
    cfg = make_config(tmp_path)  # gate off (default), same forced update
    store, target, _filler = _seeded_store(tmp_path)
    fake = FakeEntityMemory({target.id: ["ent-person"]})
    pipeline = L1Pipeline(config=cfg, store=store, model=ScriptedUpdateModel(), entity_memory=fake)

    report = pipeline.run_job(_update_job())

    assert report.ok, report.error
    assert report.updated == 1
    assert report.stale_dependents == 0
    assert not staleness_journal_path(str(store.root), "u1").exists()


def test_pipeline_entity_layer_failure_never_fails_the_run(tmp_path) -> None:
    class ExplodingEntityMemory:
        def entities_for_record(self, record_id: str, *, user_id: str, include_unpromoted: bool = False) -> list[str]:
            raise RuntimeError("entity layer blew up")

    cfg = make_config(tmp_path, staleness_propagation=True)
    store, _target, _filler = _seeded_store(tmp_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=ScriptedUpdateModel(), entity_memory=ExplodingEntityMemory())

    report = pipeline.run_job(_update_job())

    assert report.ok, report.error
    assert report.stale_dependents == 0


def test_pipeline_without_entity_memory_marks_nothing(tmp_path) -> None:
    """No entity seam at all (entities disabled): propagation is a no-op."""
    cfg = make_config(tmp_path, staleness_propagation=True)
    store, _target, _filler = _seeded_store(tmp_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=ScriptedUpdateModel(), entity_memory=None)

    report = pipeline.run_job(_update_job())

    assert report.ok, report.error
    assert report.stale_dependents == 0
    assert not staleness_journal_path(str(store.root), "u1").exists()
