"""Which L1 records did this turn actually COMMIT? (the observation channel)

The central capture seam needs the survivors of ``apply_decisions`` so it can
feed the other memory types without re-deriving anything (re-deriving would
re-index records dedup had just merged away, which resurface downstream as
duplicate or phantom memories). These tests pin the contract that answers that
question, per dedup outcome:

* new / skip / update / merge / stale target / partial write,
* the cross-type merge rule (survivor reported, absorbed id never reported),
* a multi-record conflict pool,
* cost: no second full store read, and no read at all when nothing was written,
* no behaviour change: the counts contract, the stored state, the
  ``RunReport`` payload and the provenance entry are all pinned unchanged.

Every test is hermetic: an explicit ``MemoryConfig`` rooted in ``tmp_path``, a
scripted LLM fake, and hand-built store fakes for the failure paths. No timer,
no sleeping, no network, no gateway import.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import pytest

from alpha.agents.memory.l1.dedup import apply_decisions, candidate_records
from alpha.agents.memory.l1.models import (
    DedupDecision,
    DedupOutcome,
    ExtractedMemory,
    MemoryRecord,
    RunReport,
)
from alpha.agents.memory.l1.pipeline import (
    CaptureJob,
    L1Pipeline,
    PersistedRunReport,
)
from alpha.agents.memory.l1.provenance import read_entries
from alpha.agents.memory.l1.store import L1RecordStore
from alpha.config.memory_config import MemoryConfig

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

#: Record ids are ``mem_<24 hex>_<8 hex>``. The conflict prompt prints each
#: NEW memory as ``### New memory #N (record_id: <id>)``, so a scripted model
#: can decide for ids it did not get to choose (they are generated per run).
NEW_MEMORY_ID_RE = re.compile(r"### New memory #\d+ \(record_id: (mem_[0-9a-f_]+)\)")


def make_config(tmp_path: Any, **l1_overrides: Any) -> MemoryConfig:
    """Explicit config with L1 enabled against an isolated store."""
    l1 = {
        "enabled": True,
        "storage_path": str(tmp_path / "l1"),
        "debounce_seconds": 0.0,
        "persona_enabled": False,
    }
    l1.update(l1_overrides)
    return MemoryConfig.model_validate({"enabled": True, "l1": l1})


class Msg:
    """Minimal LangChain-like message."""

    def __init__(self, type_: str, content: str, id_: str | None = None) -> None:
        self.type = type_
        self.content = content
        self.id = id_
        self.tool_calls = None
        self.additional_kwargs: dict[str, Any] = {}


TURNS = [
    Msg("human", "I'm planning a trip to Kyoto in April, and I hate early flights.", "m1"),
    Msg("ai", "Kyoto in April is lovely - let's plan it.", "m2"),
]

EXTRACTION_TWO = json.dumps(
    [
        {
            "scene_name": "planning the kyoto trip",
            "message_ids": ["m1", "m2"],
            "memories": [
                {
                    "content": "The user plans to visit Kyoto in April 2026.",
                    "type": "episodic",
                    "priority": 85,
                    "source_message_ids": ["m1"],
                    "metadata": {},
                },
                {
                    "content": "The user prefers window seats on flights.",
                    "type": "persona",
                    "priority": 70,
                    "source_message_ids": ["m2"],
                    "metadata": {},
                },
            ],
        }
    ]
)

EXTRACTION_NONE = json.dumps([{"scene_name": "small talk", "message_ids": ["m1"], "memories": []}])


class ScriptedModel:
    """Extraction reply + a conflict reply computed from the prompt itself.

    ``decide`` receives the conflict prompt and returns the decision JSON, so
    a test can bind decisions to the generated candidate ids instead of
    hard-coding ids it cannot know ahead of the run.
    """

    def __init__(self, extraction: str, decide: Any = None) -> None:
        self.extraction = extraction
        self.decide = decide
        self.conflict_prompts: list[str] = []

    def invoke(self, prompt: str, config: dict | None = None) -> str:
        if "memory extraction expert" in prompt:
            return self.extraction
        if "conflict detector" in prompt:
            self.conflict_prompts.append(prompt)
            return "" if self.decide is None else self.decide(prompt)
        return "# Profile\nstub profile"


def new_memory_ids(prompt: str) -> list[str]:
    """Candidate record ids in the order the conflict prompt presents them."""
    return NEW_MEMORY_ID_RE.findall(prompt)


def make_job(messages: list[Any], **overrides: Any) -> CaptureJob:
    job = CaptureJob(
        thread_id="t1",
        user_id="u1",
        agent_name="lead",
        mode="chat",
        trace_id=None,
        messages=messages,
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


class CountingStore(L1RecordStore):
    """The real store plus per-method call counters (proves read cost)."""

    def __init__(self, storage_path: str | None = None) -> None:
        super().__init__(storage_path)
        self.calls: Counter[str] = Counter()

    def list_records(self, *args: Any, **kwargs: Any) -> list[MemoryRecord]:
        self.calls["list_records"] += 1
        return super().list_records(*args, **kwargs)

    def get(self, *args: Any, **kwargs: Any) -> MemoryRecord | None:
        self.calls["get"] += 1
        return super().get(*args, **kwargs)

    def put_records(self, *args: Any, **kwargs: Any) -> int:
        self.calls["put_records"] += 1
        return super().put_records(*args, **kwargs)

    def delete_records(self, *args: Any, **kwargs: Any) -> int:
        self.calls["delete_records"] += 1
        return super().delete_records(*args, **kwargs)


class PartialCommitStore(L1RecordStore):
    """Store whose ``put_records`` commits only the first ``commit`` records.

    The real store writes a whole batch atomically, so a partial commit cannot
    be produced without a fake: a network/registry sink that accepted only
    part of the batch is the production shape this pins. ``fail_after`` makes
    the write raise *after* the partial commit.
    """

    def __init__(self, storage_path: str | None = None, *, commit: int = 1, fail_after: bool = False) -> None:
        super().__init__(storage_path)
        self.commit = commit
        self.fail_after = fail_after
        self.submitted = 0

    def put_records(self, records: list[MemoryRecord], **kwargs: Any) -> int:
        self.submitted += len(records)
        added = super().put_records(records[: self.commit], **kwargs)
        if self.fail_after:
            raise OSError("L1 document write failed")
        return added


class ExplodingGetStore(L1RecordStore):
    """Store whose confirmation read always fails."""

    def get(self, *args: Any, **kwargs: Any) -> MemoryRecord | None:
        raise RuntimeError("read path is down")


def store_ids(store: L1RecordStore, user_id: str = "u", agent_name: str = "a") -> set[str]:
    return {record.id for record in store.list_records(user_id, agent_name)}


# ---------------------------------------------------------------------------
# (a) a turn that writes N new records reports exactly those N ids
# ---------------------------------------------------------------------------


def test_new_records_report_exactly_the_ids_the_store_holds(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    candidates = candidate_records(
        [
            ExtractedMemory(content="new A", memory_type="persona", priority=80),
            ExtractedMemory(content="new B", memory_type="episodic", priority=70),
            ExtractedMemory(content="new C", memory_type="instruction", priority=90),
        ]
    )
    persisted: list[str] = []

    counts = apply_decisions(store, candidates, DedupOutcome(status="ok"), user_id="u", agent_name="a", persisted_ids=persisted)

    assert counts == {"stored": 3, "skipped": 0, "updated": 0, "merged": 0}
    assert persisted == [c.id for c in candidates]
    assert set(persisted) == store_ids(store), "ids must match what the store actually contains"
    assert len(persisted) == 3


def test_run_report_persisted_ids_match_the_store_after_a_pipeline_run(tmp_path) -> None:
    cfg = make_config(tmp_path)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=ScriptedModel(EXTRACTION_TWO))

    report = pipeline.run_job(make_job(TURNS))

    assert report.status == "succeeded"
    assert report.dedup_status == "no_existing"
    assert report.stored == 2, "the counts contract is unchanged"
    assert len(report.persisted_record_ids) == report.stored == 2
    assert set(report.persisted_record_ids) == store_ids(store, "u1", "lead")
    for record_id in report.persisted_record_ids:
        assert store.get(record_id, user_id="u1", agent_name="lead") is not None


# ---------------------------------------------------------------------------
# (b) every candidate skipped -> empty set, never the candidates
# ---------------------------------------------------------------------------


def test_all_skipped_candidates_report_an_empty_set(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    candidates = candidate_records(
        [
            ExtractedMemory(content="dup A", memory_type="persona", priority=80),
            ExtractedMemory(content="dup B", memory_type="episodic", priority=70),
        ]
    )
    outcome = DedupOutcome(
        status="ok",
        decisions=[
            DedupDecision(record_id=candidates[0].id, action="skip"),
            DedupDecision(record_id=candidates[1].id, action="skip"),
        ],
    )
    persisted: list[str] = ["stale-id-from-a-previous-run"]

    counts = apply_decisions(store, candidates, outcome, user_id="u", agent_name="a", persisted_ids=persisted)

    assert counts == {"stored": 0, "skipped": 2, "updated": 0, "merged": 0}
    assert persisted == [], "a skipped candidate is not a record, and a reused sink is cleared"
    assert store_ids(store) == set()


def test_empty_turn_reports_no_persisted_ids(tmp_path) -> None:
    cfg = make_config(tmp_path)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=ScriptedModel(EXTRACTION_NONE))

    report = pipeline.run_job(make_job(TURNS))

    assert report.status == "succeeded"
    assert report.stored == 0
    assert report.persisted_record_ids == ()


def test_gate_off_turn_reports_no_persisted_ids(tmp_path) -> None:
    cfg = make_config(tmp_path, enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=ScriptedModel(EXTRACTION_TWO))

    report = pipeline.run_job(make_job(TURNS))

    assert report.status == "skipped"
    assert report.reason == "disabled"
    assert report.persisted_record_ids == ()


# ---------------------------------------------------------------------------
# (c) an update reports the surviving id exactly once
# ---------------------------------------------------------------------------


def test_update_reports_the_surviving_id_exactly_once(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    old = MemoryRecord.create("The user lives in Paris.", memory_type="persona", priority=70)
    store.put_records([old], user_id="u", agent_name="a")
    candidates = candidate_records([ExtractedMemory(content="The user moved to Lyon in 2026.", memory_type="persona", priority=85)])
    outcome = DedupOutcome(
        status="ok",
        decisions=[
            DedupDecision(
                record_id=candidates[0].id,
                action="update",
                target_ids=[old.id],
                merged_content="The user moved to Lyon in 2026.",
                merged_type="persona",
                merged_priority=88,
                merged_timestamps=["2026-01-01"],
            )
        ],
    )
    persisted: list[str] = []

    counts = apply_decisions(store, candidates, outcome, user_id="u", agent_name="a", persisted_ids=persisted)

    assert counts["updated"] == 1
    assert persisted == [candidates[0].id], "the survivor is the candidate's id, reported once"
    assert persisted.count(candidates[0].id) == 1
    assert old.id not in persisted, "the replaced target is not a survivor"
    assert store_ids(store) == set(persisted)
    survivor = store.get(candidates[0].id, user_id="u", agent_name="a")
    assert survivor is not None and survivor.priority == 88


# ---------------------------------------------------------------------------
# (d) cross-type merge: only the survivor; the absorbed id is never reported
# ---------------------------------------------------------------------------


def test_cross_type_merge_reports_only_the_survivor(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    episodic = MemoryRecord.create(
        "The user started podcasting in 2018.",
        memory_type="episodic",
        priority=70,
        timestamps=["2018-05-01"],
    )
    persona = MemoryRecord.create(
        "The user has podcast production experience.",
        memory_type="persona",
        priority=75,
        timestamps=["2020-01-01"],
    )
    store.put_records([episodic, persona], user_id="u", agent_name="a")
    candidates = candidate_records([ExtractedMemory(content="New podcast detail", memory_type="persona", priority=80)])
    outcome = DedupOutcome(
        status="ok",
        decisions=[
            DedupDecision(
                record_id=candidates[0].id,
                action="merge",
                target_ids=[episodic.id, persona.id],
                merged_content="The user has produced a podcast since 2018.",
                merged_type="persona",
                merged_priority=85,
            )
        ],
    )
    persisted: list[str] = []

    counts = apply_decisions(store, candidates, outcome, user_id="u", agent_name="a", persisted_ids=persisted)

    assert counts["merged"] == 1
    assert persisted == [candidates[0].id]
    assert episodic.id not in persisted, "an absorbed id must never reach the capture seam"
    assert persona.id not in persisted, "a cross-type absorbed id must never reach the capture seam"
    assert store_ids(store) == set(persisted), "the absorbed records no longer exist"
    survivor = store.get(candidates[0].id, user_id="u", agent_name="a")
    assert survivor is not None
    assert survivor.content == "The user has produced a podcast since 2018."
    assert survivor.timestamps == ["2018-05-01", "2020-01-01"]


def test_merge_absorbed_id_rule_is_documented_in_the_code() -> None:
    """The rule the capture seam depends on must live in the source, not only
    in a test: which id survives, and what happens to the absorbed id."""
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "agents" / "memory" / "l1" / "dedup.py").read_text(encoding="utf-8")
    assert "Merge rule" in source
    assert "SURVIVOR is the candidate's id" in source
    assert "ABSORBED" in source
    assert "never reported" in source


def test_pipeline_merge_reports_the_survivor_and_not_the_absorbed_record(tmp_path) -> None:
    cfg = make_config(tmp_path)
    store = L1RecordStore(cfg.l1.storage_path)
    absorbed = MemoryRecord.create("Kyoto trip booked for April 2026", memory_type="episodic", priority=70)
    store.put_records([absorbed], user_id="u1", agent_name="lead")

    def decide(prompt: str) -> str:
        new_ids = new_memory_ids(prompt)
        return json.dumps(
            [
                {
                    "record_id": new_ids[0],
                    "action": "merge",
                    "target_ids": [absorbed.id],
                    "merged_content": "The user will visit Kyoto in April 2026.",
                    "merged_type": "episodic",
                    "merged_priority": 88,
                }
            ]
        )

    model = ScriptedModel(EXTRACTION_TWO, decide)
    pipeline = L1Pipeline(config=cfg, store=store, model=model)
    report = pipeline.run_job(make_job(TURNS))

    assert report.dedup_status == "ok"
    assert report.merged == 1
    assert report.stored == 1, "the candidate the model did not judge is stored as before"
    assert model.conflict_prompts and absorbed.id in model.conflict_prompts[0]
    judged, unjudged = new_memory_ids(model.conflict_prompts[0])
    assert report.persisted_record_ids == (judged, unjudged)
    assert absorbed.id not in report.persisted_record_ids
    assert set(report.persisted_record_ids) == store_ids(store, "u1", "lead")
    survivor = store.get(judged, user_id="u1", agent_name="lead")
    assert survivor is not None
    assert survivor.content == "The user will visit Kyoto in April 2026."


def test_stale_merge_target_reports_the_candidate_under_its_own_id(tmp_path) -> None:
    """A merge whose targets are all gone degrades to ``store``; the unknown
    target ids must not leak into the reported set."""
    store = L1RecordStore(str(tmp_path))
    candidates = candidate_records([ExtractedMemory(content="orphan merge", memory_type="persona", priority=70)])
    outcome = DedupOutcome(
        status="ok",
        decisions=[
            DedupDecision(
                record_id=candidates[0].id,
                action="merge",
                target_ids=["does-not-exist"],
                merged_content="orphan merge merged",
                merged_type="persona",
                merged_priority=71,
            )
        ],
    )
    persisted: list[str] = []

    counts = apply_decisions(store, candidates, outcome, user_id="u", agent_name="a", persisted_ids=persisted)

    assert counts["stored"] == 1
    assert persisted == [candidates[0].id]
    assert "does-not-exist" not in persisted


# ---------------------------------------------------------------------------
# multi-record conflict pool
# ---------------------------------------------------------------------------


def test_conflict_pool_reports_each_survivor_once_and_nothing_absorbed(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    target_update = MemoryRecord.create("The user lives in Paris.", memory_type="persona", priority=70)
    target_merge_a = MemoryRecord.create("The user started podcasting in 2018.", memory_type="episodic", priority=70)
    target_merge_b = MemoryRecord.create("The user runs a podcast.", memory_type="persona", priority=75)
    bystander = MemoryRecord.create("The user dislikes cilantro.", memory_type="persona", priority=60)
    store.put_records([target_update, target_merge_a, target_merge_b, bystander], user_id="u", agent_name="a")
    candidates = candidate_records(
        [
            ExtractedMemory(content="Moved to Lyon", memory_type="persona", priority=88),
            ExtractedMemory(content="Podcast since 2018", memory_type="persona", priority=86),
            ExtractedMemory(content="Already known preference", memory_type="persona", priority=60),
            ExtractedMemory(content="Brand new fact", memory_type="episodic", priority=84),
        ]
    )
    outcome = DedupOutcome(
        status="ok",
        decisions=[
            DedupDecision(
                record_id=candidates[0].id,
                action="update",
                target_ids=[target_update.id],
                merged_content="Moved to Lyon",
                merged_type="persona",
                merged_priority=88,
            ),
            DedupDecision(
                record_id=candidates[1].id,
                action="merge",
                target_ids=[target_merge_a.id, target_merge_b.id],
                merged_content="Podcast since 2018",
                merged_type="persona",
                merged_priority=86,
            ),
            DedupDecision(record_id=candidates[2].id, action="skip"),
            # candidates[3] has no decision at all -> plain store
        ],
    )
    persisted: list[str] = []

    counts = apply_decisions(store, candidates, outcome, user_id="u", agent_name="a", persisted_ids=persisted)

    assert counts == {"stored": 1, "skipped": 1, "updated": 1, "merged": 1}
    assert persisted == [candidates[0].id, candidates[1].id, candidates[3].id]
    assert len(persisted) == len(set(persisted)), "each survivor is reported exactly once"
    for absorbed in (target_update.id, target_merge_a.id, target_merge_b.id):
        assert absorbed not in persisted
    assert candidates[2].id not in persisted
    # The report is this run's DELTA, not the whole scope: the untouched
    # bystander stays in the store and stays out of the report.
    assert set(persisted) == store_ids(store) - {bystander.id}
    assert bystander.id in store_ids(store)


def test_merge_target_written_in_the_same_run_is_reported_not_deleted(tmp_path) -> None:
    """The replacement guard keeps a target that is also being written, and the
    channel reports what the store really holds rather than judging it."""
    store = L1RecordStore(str(tmp_path))
    candidates = candidate_records(
        [
            ExtractedMemory(content="primary takes the other in", memory_type="persona", priority=80),
            ExtractedMemory(content="secondary", memory_type="episodic", priority=70),
        ]
    )
    outcome = DedupOutcome(
        status="ok",
        decisions=[
            DedupDecision(
                record_id=candidates[0].id,
                action="merge",
                target_ids=[candidates[1].id],
                merged_content="primary takes the other in",
                merged_type="persona",
                merged_priority=80,
            )
        ],
    )
    persisted: list[str] = []

    apply_decisions(store, candidates, outcome, user_id="u", agent_name="a", persisted_ids=persisted)

    assert persisted == [candidates[0].id, candidates[1].id]
    assert set(persisted) == store_ids(store)


# ---------------------------------------------------------------------------
# (e) partial / failed write reports only what was committed
# ---------------------------------------------------------------------------


def test_partial_write_reports_only_the_committed_records(tmp_path) -> None:
    store = PartialCommitStore(str(tmp_path), commit=1)
    candidates = candidate_records(
        [
            ExtractedMemory(content="lands", memory_type="persona", priority=80),
            ExtractedMemory(content="lost", memory_type="episodic", priority=70),
        ]
    )
    persisted: list[str] = []

    counts = apply_decisions(store, candidates, DedupOutcome(status="ok"), user_id="u", agent_name="a", persisted_ids=persisted)

    assert store.submitted == 2, "two records were attempted"
    assert counts["stored"] == 2, "the counts contract is untouched by the sink"
    assert persisted == [candidates[0].id], "only the record the store actually holds"
    assert set(persisted) == store_ids(store)
    assert store.get(candidates[1].id, user_id="u", agent_name="a") is None


def test_failed_write_reports_the_partial_truth_and_still_raises(tmp_path) -> None:
    store = PartialCommitStore(str(tmp_path), commit=1, fail_after=True)
    candidates = candidate_records(
        [
            ExtractedMemory(content="lands", memory_type="persona", priority=80),
            ExtractedMemory(content="lost", memory_type="episodic", priority=70),
        ]
    )
    persisted: list[str] = []

    with pytest.raises(OSError, match="document write failed"):
        apply_decisions(store, candidates, DedupOutcome(status="ok"), user_id="u", agent_name="a", persisted_ids=persisted)

    assert persisted == [candidates[0].id], "the sink holds the partial reality"
    assert set(persisted) == store_ids(store)


def test_confirmation_read_failure_degrades_to_absent_without_raising(tmp_path) -> None:
    store = ExplodingGetStore(str(tmp_path))
    candidates = candidate_records([ExtractedMemory(content="never confirmed", priority=80)])
    persisted: list[str] = []

    counts = apply_decisions(store, candidates, DedupOutcome(status="ok"), user_id="u", agent_name="a", persisted_ids=persisted)

    assert counts["stored"] == 1
    assert persisted == [], "an unconfirmable record is reported as absent, never guessed"
    assert store_ids(store) == {candidates[0].id}, "the record really is stored"


# ---------------------------------------------------------------------------
# (f) cost: no second full read, and no read at all when nothing was written
# ---------------------------------------------------------------------------


def test_confirmation_costs_one_lookup_per_written_record_and_no_full_read(tmp_path) -> None:
    store = CountingStore(str(tmp_path))
    candidates = candidate_records([ExtractedMemory(content=f"fact {i}", memory_type="persona", priority=80) for i in range(4)])
    persisted: list[str] = []

    apply_decisions(store, candidates, DedupOutcome(status="ok"), user_id="u", agent_name="a", persisted_ids=persisted)

    assert len(persisted) == 4
    assert store.calls["get"] == 4, "one confirmation per written record"
    assert store.calls["list_records"] == 1, "no second full read of the scope"
    assert store.calls["put_records"] == 1, "no second write pass"
    assert store.calls["delete_records"] == 0


def test_a_turn_that_writes_nothing_performs_no_confirmation_read(tmp_path) -> None:
    store = CountingStore(str(tmp_path))
    candidates = candidate_records(
        [
            ExtractedMemory(content="dup A", memory_type="persona", priority=80),
            ExtractedMemory(content="dup B", memory_type="episodic", priority=70),
        ]
    )
    outcome = DedupOutcome(
        status="ok",
        decisions=[DedupDecision(record_id=c.id, action="skip") for c in candidates],
    )
    persisted: list[str] = []

    apply_decisions(store, candidates, outcome, user_id="u", agent_name="a", persisted_ids=persisted)

    assert persisted == []
    assert store.calls["get"] == 0
    assert store.calls["put_records"] == 0
    assert store.calls["list_records"] == 1


def test_no_sink_supplied_costs_exactly_what_it_used_to(tmp_path) -> None:
    """The default call path must be byte-for-byte the old cost: a caller that
    does not ask pays no read at all."""
    store = CountingStore(str(tmp_path))
    candidates = candidate_records(
        [
            ExtractedMemory(content="new A", memory_type="persona", priority=80),
            ExtractedMemory(content="new B", memory_type="episodic", priority=70),
        ]
    )

    counts = apply_decisions(store, candidates, DedupOutcome(status="ok"), user_id="u", agent_name="a")

    assert counts == {"stored": 2, "skipped": 0, "updated": 0, "merged": 0}
    assert store.calls["get"] == 0, "the observation channel must not run uninvited"
    assert store.calls["list_records"] == 1
    assert store.calls["put_records"] == 1


def test_empty_candidate_turn_reads_nothing(tmp_path) -> None:
    store = CountingStore(str(tmp_path))
    persisted: list[str] = ["stale"]

    counts = apply_decisions(store, [], DedupOutcome(status="empty"), user_id="u", agent_name="a", persisted_ids=persisted)

    assert counts == {"stored": 0, "skipped": 0, "updated": 0, "merged": 0}
    assert persisted == []
    assert not store.calls, "an empty turn touches the store not at all"


def test_empty_pipeline_turn_makes_no_extra_store_read(tmp_path) -> None:
    cfg = make_config(tmp_path)
    store = CountingStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=ScriptedModel(EXTRACTION_NONE))

    report = pipeline.run_job(make_job(TURNS))

    assert report.stored == 0
    assert report.persisted_record_ids == ()
    assert store.calls["get"] == 0, "nothing was written, so nothing is confirmed"
    assert store.calls["put_records"] == 0


# ---------------------------------------------------------------------------
# (g) nothing else changed
# ---------------------------------------------------------------------------


def test_base_run_report_contract_is_unchanged() -> None:
    """``RunReport`` itself is untouched; the channel is an additive subclass."""
    assert "persisted_record_ids" not in RunReport.__dataclass_fields__
    assert set(RunReport(status="succeeded").to_dict()) == {
        "status",
        "reason",
        "error",
        "stored",
        "skipped",
        "updated",
        "merged",
        "quota_blocked",
        "refused",
        "credits_used",
        "extraction_status",
        "dedup_status",
        "retention",
        "persona_status",
        "duration_ms",
    }


def test_run_job_returns_a_backwards_compatible_report(tmp_path) -> None:
    cfg = make_config(tmp_path)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=ScriptedModel(EXTRACTION_TWO))

    report = pipeline.run_job(make_job(TURNS))

    assert isinstance(report, RunReport), "existing callers keep their isinstance check"
    assert isinstance(report, PersistedRunReport)
    assert report.ok is True
    assert report.duration_ms >= 0


def test_persisted_ids_are_not_written_into_the_provenance_entry(tmp_path) -> None:
    """The generation log is a provenance artifact: widening it would change
    provenance output, so the ids stay on the returned object only."""
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    cfg = make_config(tmp_path)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=ScriptedModel(EXTRACTION_TWO))

    report = pipeline.run_job(make_job(TURNS))
    entries = read_entries(day, user_id="u1", storage_path=str(store.root))

    assert report.persisted_record_ids, "the report carries the ids"
    assert len(entries) == 1
    entry = entries[0]
    assert "persisted_record_ids" not in entry
    assert set(entry) == set(RunReport(status="succeeded").to_dict()) | {
        "ts",
        "component",
        "thread_id",
        "user_id",
        "agent_name",
        "mode",
        "trace_id",
    }
    assert entry["stored"] == 2


def test_persisted_ids_do_not_leak_between_runs(tmp_path) -> None:
    """Each run reports only its own ids; a later turn that writes nothing
    reports nothing rather than repeating the previous turn's survivors."""
    cfg = make_config(tmp_path)
    store = L1RecordStore(cfg.l1.storage_path)
    first = L1Pipeline(config=cfg, store=store, model=ScriptedModel(EXTRACTION_TWO))
    first_report = first.run_job(make_job(TURNS))
    assert len(first_report.persisted_record_ids) == 2

    empty_turn = [
        Msg("human", "Thanks, that is all I needed today.", "m3"),
        Msg("ai", "Any time.", "m4"),
    ]
    second = L1Pipeline(config=cfg, store=store, model=ScriptedModel(EXTRACTION_NONE))
    second_report = second.run_job(make_job(empty_turn, thread_id="t2"))

    assert second_report.status == "succeeded"
    assert second_report.persisted_record_ids == ()
    assert set(first_report.persisted_record_ids) == store_ids(store, "u1", "lead")


def test_strict_order_sentinel_record_is_reported(tmp_path) -> None:
    """The ``-1`` strict-instruction sentinel survives min-priority filtering
    and is a committed record like any other, so it is reported — and because
    the strict-order sort puts it first, it is committed first."""
    raw = json.dumps(
        [
            {
                "scene_name": "s",
                "message_ids": ["m1"],
                "memories": [
                    {"content": "Never auto-deploy on Fridays.", "type": "instruction", "priority": -1},
                    {"content": "The user plans to visit Kyoto in April 2026.", "type": "episodic", "priority": 85},
                ],
            }
        ]
    )
    cfg = make_config(tmp_path, min_priority=60)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=ScriptedModel(raw))

    report = pipeline.run_job(make_job(TURNS))

    stored = store.list_records("u1", "lead")
    strict = [record for record in stored if record.priority == -1]
    other = [record for record in stored if record.priority != -1]
    assert len(strict) == 1 and len(other) == 1
    assert report.persisted_record_ids == (strict[0].id, other[0].id), "strict order commits first"
    assert set(report.persisted_record_ids) == {record.id for record in stored}
