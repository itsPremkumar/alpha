"""Call-site proofs for the L1 typed working-memory pipeline.

Every test here is hermetic: LLM calls go through fakes, the store is rooted
in ``tmp_path``, and nothing globally enables memory (``memory.l1.enabled``
defaults to ``False``; tests construct explicit configs).

Coverage map:

* parser   -- closed status sets, fail-open behaviour, field repair.
* store    -- CRUD, hybrid recall, extraction cursors, corrupt-doc recovery.
* dedup    -- each decision (store / skip / update / merge) mutates the store
              the way the contract says.
* quota    -- record + credit limits block and are disclosed, never silent.
* cleaner  -- age sweep, record cap, pinned / -1 exemption.
* persona  -- threshold, LLM error, profile written.
* pipeline -- end-to-end run, honest report, cursor semantics, recall block.
* wiring   -- middleware capture + registration gates + prompt injection.
"""

from __future__ import annotations

import json
import time
from datetime import UTC
from typing import Any

import pytest

from alpha.agents.memory.l1.cleaner import sweep
from alpha.agents.memory.l1.dedup import apply_decisions, candidate_records
from alpha.agents.memory.l1.gates import l1_enabled
from alpha.agents.memory.l1.models import DedupOutcome, MemoryRecord
from alpha.agents.memory.l1.parser import (
    conflict_status_known,
    extraction_status_known,
    parse_dedup_response,
    parse_extraction_response,
)
from alpha.agents.memory.l1.paths import generation_log_path, quota_path, safe_segment
from alpha.agents.memory.l1.pipeline import (
    KNOWN_DEDUP_STATUSES,
    CaptureJob,
    L1Pipeline,
    filter_capture_messages,
    message_key,
    messages_to_prompt_dicts,
)
from alpha.agents.memory.l1.prompts import (
    format_batch_conflict_prompt,
    format_extraction_prompt,
    get_conflict_system_prompt,
    get_extraction_system_prompt,
)
from alpha.agents.memory.l1.provenance import append_run_entry, read_entries
from alpha.agents.memory.l1.quota import L1QuotaManager, calculate_credits, usage_from_response
from alpha.agents.memory.l1.store import L1RecordStore, hybrid_score, tokenize
from alpha.config.memory_config import L1MemoryConfig, MemoryConfig

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_config(tmp_path: Any, **l1_overrides: Any) -> MemoryConfig:
    """Explicit config with L1 enabled against an isolated store."""
    l1 = {"enabled": True, "storage_path": str(tmp_path / "l1"), "debounce_seconds": 0.0}
    l1.update(l1_overrides)
    return MemoryConfig.model_validate({"enabled": True, "l1": l1})


class FakeModel:
    """Canned LLM: extraction prompt -> extraction JSON, conflict prompt ->
    decisions JSON, anything else (persona) -> a Markdown profile."""

    def __init__(self, extraction: str, dedup: str | None = None, persona: str | None = None) -> None:
        self.extraction = extraction
        self.dedup = dedup
        self.persona = persona or "# Profile\n## Identity\nstub profile"
        self.prompts: list[str] = []

    def invoke(self, prompt: str, config: dict | None = None) -> str:
        self.prompts.append(prompt)
        if "memory extraction expert" in prompt:
            return self.extraction
        if "conflict detector" in prompt:
            return self.dedup if self.dedup is not None else "[]"
        return self.persona


class BrokenModel:
    """Raises on every call (LLM outage simulation)."""

    def invoke(self, prompt: str, config: dict | None = None) -> str:
        raise RuntimeError("model endpoint unreachable")


class Msg:
    """Minimal LangChain-like message."""

    def __init__(self, type_: str, content: str, id_: str | None = None, tool_calls: Any = None) -> None:
        self.type = type_
        self.content = content
        self.id = id_
        self.tool_calls = tool_calls
        self.additional_kwargs: dict[str, Any] = {}


EXTRACTION_OK = json.dumps(
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


TURNS = [
    Msg("human", "I'm planning a trip to Kyoto in April, and I hate early flights.", "m1"),
    Msg("ai", "Kyoto in April is lovely - let's plan it.", "m2"),
]


# ---------------------------------------------------------------------------
# gates + config
# ---------------------------------------------------------------------------


def test_l1_gate_requires_both_switches() -> None:
    both_on = MemoryConfig.model_validate({"enabled": True, "l1": {"enabled": True}})
    master_off = MemoryConfig.model_validate({"enabled": False, "l1": {"enabled": True}})
    l1_off = MemoryConfig.model_validate({"enabled": True, "l1": {"enabled": False}})

    assert l1_enabled(both_on) is True
    assert l1_enabled(master_off) is False
    assert l1_enabled(l1_off) is False


def test_l1_defaults_keep_feature_off_for_existing_suites() -> None:
    # The pydantic default must stay False so hermetic suites (which never
    # read config.yaml) keep byte-identical behavior.
    assert MemoryConfig().l1.enabled is False


def test_every_l1_config_key_has_a_reader() -> None:
    """No dormant knobs: each L1MemoryConfig field appears in a reader module."""
    import pathlib

    fields = set(L1MemoryConfig.model_fields)
    assert fields

    package_root = pathlib.Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha"
    blob = ""
    targets = [
        package_root / "agents" / "memory" / "l1",
        package_root / "agents" / "middlewares" / "l1_memory_middleware.py",
        package_root / "agents" / "lead_agent" / "prompt.py",
    ]
    for target in targets:
        files = sorted(target.glob("*.py")) if target.is_dir() else [target]
        for path in files:
            blob += path.read_text(encoding="utf-8")

    unread = sorted(field for field in fields if field not in blob)
    assert unread == [], f"L1 config keys with no reader: {unread}"


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------


def test_prompt_dialects_are_reachable() -> None:
    assert get_extraction_system_prompt("chat") != get_extraction_system_prompt("work")
    assert get_conflict_system_prompt("chat") != get_conflict_system_prompt("work")
    assert "work_fact" in get_extraction_system_prompt("work")
    assert "episodic" in get_extraction_system_prompt("chat")


def test_prompts_carry_attribution_and_english_adaptation_notice() -> None:
    from alpha.agents.memory.l1 import prompts

    doc = prompts.__doc__ or ""
    assert "TencentDB-Agent-Memory" in doc
    assert "MIT" in doc
    assert "English adaptation" in doc


def test_format_extraction_prompt_includes_messages_and_previous_scene() -> None:
    text = format_extraction_prompt(
        new_messages=[{"id": "m1", "role": "user", "content": "hello", "timestamp": "2026-01-01"}],
        background_messages=[{"id": "m0", "role": "assistant", "content": "hi"}],
        previous_scene_name="greeting",
    )
    assert "hello" in text
    assert "greeting" in text
    assert "m0" in text


def test_batch_conflict_prompt_builds_unified_pool() -> None:
    text = format_batch_conflict_prompt(
        [
            {
                "record": {
                    "record_id": "n1",
                    "content": "new fact",
                    "type": "persona",
                    "priority": 80,
                    "scene_name": "s",
                },
                "candidates": [
                    {
                        "id": "e1",
                        "content": "old fact",
                        "type": "persona",
                        "priority": 70,
                        "scene_name": "s",
                        "timestamps": [],
                    }
                ],
            }
        ]
    )
    assert "candidate pool" in text
    assert "e1" in text
    assert "n1" in text


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def test_parser_accepts_fenced_extraction_array_and_repairs_priority() -> None:
    raw = (
        '```json\n[{"scene_name": "s", "message_ids": ["a"], '
        '"memories": [{"content": "c", "type": "persona", "priority": "85"}]}]\n```'
    )
    outcome = parse_extraction_response(raw)
    assert outcome.status == "ok"
    assert extraction_status_known(outcome.status)
    assert outcome.memories[0].priority == 85


def test_parser_strips_prose_before_json() -> None:
    raw = 'Sure! Here is the result you asked for:\n["role", {"memories": [{"content": "kept", "type": "episodic", "priority": 70}]}]'
    outcome = parse_extraction_response(raw)
    assert outcome.status == "ok"
    assert outcome.memories[0].content == "kept"


def test_parser_reports_closed_statuses_and_never_raises() -> None:
    for raw, expected in [
        (None, "no_json"),
        ("", "no_json"),
        ("no json here at all", "parse_fail"),
        ('"just a string"', "not_array"),
        ("[]", "empty_scenes"),
    ]:
        outcome = parse_extraction_response(raw)
        assert outcome.status == expected, raw
        assert extraction_status_known(outcome.status)
        assert outcome.memories == []


def test_parser_unknown_type_falls_back_to_persona() -> None:
    raw = json.dumps([{"scene_name": "s", "memories": [{"content": "c", "type": "astronaut"}]}])
    outcome = parse_extraction_response(raw)
    assert outcome.memories[0].memory_type == "persona"


def test_dedup_parser_repairs_malformed_merge_to_store() -> None:
    # merge without merged_type must not drop the memory: falls back to store.
    raw = json.dumps([{"record_id": "n1", "action": "merge", "target_ids": ["e1"]}])
    outcome = parse_dedup_response(raw)
    assert outcome.status == "ok"
    assert outcome.decisions[0].action == "store"
    assert conflict_status_known(outcome.status)


def test_dedup_parser_handles_all_four_actions() -> None:
    raw = json.dumps(
        [
            {"record_id": "a", "action": "store"},
            {"record_id": "b", "action": "skip"},
            {
                "record_id": "c",
                "action": "update",
                "target_ids": ["e1"],
                "merged_content": "updated text",
                "merged_type": "persona",
                "merged_priority": 90,
                "merged_timestamps": ["2026-01-01"],
            },
            {
                "record_id": "d",
                "action": "merge",
                "target_ids": ["e1", "e2"],
                "merged_content": "merged text",
                "merged_type": "episodic",
                "merged_priority": 80,
            },
        ]
    )
    outcome = parse_dedup_response(raw)
    assert [d.action for d in outcome.decisions] == ["store", "skip", "update", "merge"]
    assert outcome.by_record_id()["c"].target_ids == ["e1"]


def test_dedup_parser_fail_open_statuses() -> None:
    for raw, expected in [
        (None, "no_json"),
        ("gibberish ]", "parse_fail"),
        ("{}", "not_array"),
        ("[]", "empty"),
    ]:
        outcome = parse_dedup_response(raw)
        assert outcome.status == expected, raw
        assert conflict_status_known(outcome.status)
        assert outcome.decisions == []
    assert "llm_error" in KNOWN_DEDUP_STATUSES


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def test_store_crud_and_persistence_roundtrip(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    record = MemoryRecord.create("The user likes pytest.", memory_type="persona", priority=80)
    assert store.put_records([record], user_id="u", agent_name="a") == 1
    assert store.count("u", "a") == 1

    fetched = store.get(record.id, user_id="u", agent_name="a")
    assert fetched is not None and fetched.content == record.content

    fresh = L1RecordStore(str(tmp_path))
    assert fresh.count("u", "a") == 1
    assert fresh.get(record.id, user_id="u", agent_name="a") is not None

    assert store.delete_records([record.id], user_id="u", agent_name="a") == 1
    assert store.count("u", "a") == 0


def test_store_hybrid_search_prefers_relevant_records(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    store.put_records(
        [
            MemoryRecord.create("Kyoto trip itinerary and hotel booking", memory_type="episodic"),
            MemoryRecord.create("The user's cat is named Mochi", memory_type="persona"),
        ],
        user_id="u",
        agent_name="a",
    )
    hits = store.search("kyoto trip", 2, user_id="u", agent_name="a")
    assert hits
    assert "Kyoto" in hits[0].content


def test_store_search_respects_memory_type_filter(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    store.put_records(
        [
            MemoryRecord.create("kyoto travel plan", memory_type="episodic"),
            MemoryRecord.create("kyoto preference note", memory_type="persona"),
        ],
        user_id="u",
        agent_name="a",
    )
    only_persona = store.search("kyoto", 5, user_id="u", agent_name="a", memory_type="persona")
    assert [r.type for r in only_persona] == ["persona"]


def test_store_hybrid_score_is_symmetric_in_query_tokens() -> None:
    record = MemoryRecord.create("deployment runbook for the billing service")
    corpus = [tokenize(record.content)]
    score = hybrid_score("billing runbook", record, corpus_tokens=corpus, index=0)
    assert score > 0.0
    assert hybrid_score("quantum zebra", record, corpus_tokens=corpus, index=0) == 0.0


def test_store_processed_cursor_is_bounded_and_persistent(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    keys = [f"k{i}" for i in range(600)]
    store.mark_processed("t1", keys, user_id="u", agent_name="a")
    stored = store.processed_keys("t1", user_id="u", agent_name="a")
    assert stored, "cursor must persist"
    assert len(stored) <= 500, "cursor must stay bounded"

    store.set_last_scene("t1", "planning", user_id="u", agent_name="a")
    assert store.last_scene("t1", user_id="u", agent_name="a") == "planning"


def test_store_recovers_from_corrupt_document_without_crash(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    store.put_records([MemoryRecord.create("keep me")], user_id="u", agent_name="a")
    path = store._path("u", "a")
    path.write_text("{not json", encoding="utf-8")

    recovered = L1RecordStore(str(tmp_path))
    assert recovered.count("u", "a") == 0, "corrupt doc restarts empty"
    backups = list(path.parent.glob(f"{path.name}.corrupt-*"))
    assert backups, "corrupt bytes must be preserved for forensics"


def test_candidates_for_dedup_returns_per_memory_recall(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    existing = MemoryRecord.create("Kyoto trip is booked for April", memory_type="episodic")
    store.put_records([existing], user_id="u", agent_name="a")
    matches = store.candidates_for_dedup(
        [{"record_id": "n1", "content": "kyoto trip booking"}], 3, user_id="u", agent_name="a"
    )
    assert matches["n1"] and matches["n1"][0].id == existing.id


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def test_paths_sanitize_segments_and_keep_layout(tmp_path) -> None:
    assert safe_segment("../evil") == "evil"
    assert safe_segment(None) == "__default__"
    root = tmp_path
    assert generation_log_path(root, "u", "2026-09-25").name == "2026-09-25.jsonl"
    assert quota_path(root, "u").name == "quota.json"


# ---------------------------------------------------------------------------
# quota
# ---------------------------------------------------------------------------


def test_quota_blocks_and_discloses_record_limit(tmp_path) -> None:
    quota = L1QuotaManager(
        user_id="u", memory_limit=2, credit_limit=100.0, storage_path=str(tmp_path)
    )
    assert quota.check_records(0, 2).allowed is True
    blocked = quota.check_records(2, 1)
    assert blocked.allowed is False
    assert blocked.reason == "memory_limit_exceeded"
    assert blocked.limit == 2.0


def test_quota_blocks_and_discloses_credit_limit(tmp_path) -> None:
    quota = L1QuotaManager(
        user_id="u", memory_limit=10, credit_limit=10.0, storage_path=str(tmp_path)
    )
    assert quota.check_credits().allowed is True
    quota.add_credits(12.5)
    blocked = quota.check_credits()
    assert blocked.allowed is False
    assert blocked.reason == "credit_limit_exceeded"

    # Usage survives a process restart (persisted under the L1 root).
    again = L1QuotaManager(
        user_id="u", memory_limit=10, credit_limit=10.0, storage_path=str(tmp_path)
    )
    assert again.credit_usage == pytest.approx(12.5)
    assert quota_path(tmp_path, "u").exists()


def test_credit_calculation_matches_source_rates() -> None:
    # input 1.0/1k, cache 0.2/1k, output 4.0/1k (source DEFAULT_RATES).
    assert calculate_credits(input_tokens=1000, output_tokens=1000) == pytest.approx(5.0)
    assert calculate_credits(input_tokens=2000, cache_tokens=1000, output_tokens=0) == pytest.approx(2.2)


def test_usage_from_response_reads_langchain_usage_metadata() -> None:
    class _Resp:
        usage_metadata = {"input_tokens": 1000, "output_tokens": 1000}

    assert usage_from_response(_Resp()) == pytest.approx(5.0)
    assert usage_from_response(object()) == 0.0, "unreported usage must be zero, not invented"


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


def test_provenance_appends_one_entry_per_run(tmp_path) -> None:
    append_run_entry(
        {"status": "succeeded", "stored": 2}, user_id="u", storage_path=str(tmp_path)
    )
    append_run_entry({"status": "failed", "error": "boom"}, user_id="u", storage_path=str(tmp_path))
    from datetime import datetime

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    entries = read_entries(day, user_id="u", storage_path=str(tmp_path))
    assert [e["status"] for e in entries] == ["succeeded", "failed"]
    assert entries[0]["ts"]
    assert generation_log_path(tmp_path, "u", day).exists()


def test_provenance_survives_unwritable_path(tmp_path) -> None:
    # A path that cannot be created must not raise (provenance is best-effort).
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    append_run_entry(
        {"status": "succeeded"},
        user_id="u",
        storage_path=str(blocker / "nested"),
    )  # parent is a file -> OSError inside, swallowed


# ---------------------------------------------------------------------------
# cleaner
# ---------------------------------------------------------------------------


def test_sweep_drops_expired_and_aged_records(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    now = 1_800_000_000.0
    expired = MemoryRecord.create("old ttl", ttl_days=1, now=now - 10 * 86400)
    aged = MemoryRecord.create("ancient", now=now - 400 * 86400)
    fresh = MemoryRecord.create("fresh", now=now)
    pinned = MemoryRecord.create("strict order", priority=-1, now=now - 400 * 86400)
    store.put_records([expired, aged, fresh, pinned], user_id="u", agent_name="a")

    report = sweep(
        store,
        user_id="u",
        agent_name="a",
        max_records=100,
        max_age_days=180,
        now=now,
    )
    assert report.expired == 1
    assert report.aged_out == 1, "pinned -1 record must survive the age sweep"
    remaining = {r.content for r in store.list_records("u", "a")}
    assert remaining == {"fresh", "strict order"}


def test_sweep_caps_record_count_lowest_priority_oldest_first(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    now = 1_800_000_000.0
    records = [
        MemoryRecord.create("high", priority=90, now=now - 100),
        MemoryRecord.create("mid", priority=50, now=now - 50),
        MemoryRecord.create("low", priority=20, now=now - 10),
        MemoryRecord.create("lower", priority=10, now=now - 60),
    ]
    store.put_records(records, user_id="u", agent_name="a")
    report = sweep(
        store, user_id="u", agent_name="a", max_records=2, max_age_days=10_000, now=now
    )
    assert report.capped == 2
    remaining = sorted(r.priority for r in store.list_records("u", "a"))
    assert remaining == [50, 90], "lowest priorities drop first"


# ---------------------------------------------------------------------------
# dedup application
# ---------------------------------------------------------------------------


def test_apply_decisions_store_and_skip(tmp_path) -> None:
    from alpha.agents.memory.l1.models import DedupDecision, ExtractedMemory

    store = L1RecordStore(str(tmp_path))
    candidates = candidate_records(
        [
            ExtractedMemory(content="new A", memory_type="persona", priority=80),
            ExtractedMemory(content="new B", memory_type="episodic", priority=70),
        ]
    )
    outcome = DedupOutcome(
        status="ok",
        decisions=[
            DedupDecision(record_id=candidates[0].id, action="store"),
            DedupDecision(record_id=candidates[1].id, action="skip"),
        ],
    )
    counts = apply_decisions(store, candidates, outcome, user_id="u", agent_name="a")
    assert counts == {"stored": 1, "skipped": 1, "updated": 0, "merged": 0}
    assert store.count("u", "a") == 1


def test_apply_decision_update_replaces_target_and_bumps_version(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    old = MemoryRecord.create("The user lives in Paris.", memory_type="persona", priority=70)
    store.put_records([old], user_id="u", agent_name="a")

    from alpha.agents.memory.l1.models import DedupDecision, ExtractedMemory

    candidates = candidate_records(
        [ExtractedMemory(content="The user moved to Lyon in 2026.", memory_type="persona", priority=85)]
    )
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
    counts = apply_decisions(store, candidates, outcome, user_id="u", agent_name="a")
    assert counts["updated"] == 1
    records = store.list_records("u", "a")
    assert len(records) == 1, "target removed, replacement lands"
    assert records[0].content == "The user moved to Lyon in 2026."
    assert records[0].priority == 88
    assert records[0].timestamps == ["2026-01-01"]


def test_apply_decision_merge_unions_timestamps_and_tags(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    first = MemoryRecord.create(
        "The user started podcasting in 2018.",
        memory_type="episodic",
        priority=70,
        timestamps=["2018-05-01"],
    )
    second = MemoryRecord.create(
        "The user has podcast production experience.",
        memory_type="persona",
        priority=75,
        timestamps=["2020-01-01"],
    )
    store.put_records([first, second], user_id="u", agent_name="a")

    from alpha.agents.memory.l1.models import DedupDecision, ExtractedMemory

    candidates = candidate_records(
        [ExtractedMemory(content="New podcast detail", memory_type="persona", priority=80)]
    )
    outcome = DedupOutcome(
        status="ok",
        decisions=[
            DedupDecision(
                record_id=candidates[0].id,
                action="merge",
                target_ids=[first.id, second.id],
                merged_content="The user has produced a podcast since 2018.",
                merged_type="persona",
                merged_priority=85,
            )
        ],
    )
    counts = apply_decisions(store, candidates, outcome, user_id="u", agent_name="a")
    assert counts["merged"] == 1
    records = store.list_records("u", "a")
    assert len(records) == 1
    assert records[0].content == "The user has produced a podcast since 2018."
    assert records[0].timestamps == ["2018-05-01", "2020-01-01"], "sorted union preserved"


def test_apply_decisions_fail_open_when_outcome_empty(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    from alpha.agents.memory.l1.models import ExtractedMemory

    candidates = candidate_records([ExtractedMemory(content="must not vanish", priority=70)])
    counts = apply_decisions(
        store, candidates, DedupOutcome(status="llm_error"), user_id="u", agent_name="a"
    )
    assert counts["stored"] == 1
    assert store.count("u", "a") == 1, "broken dedup must never drop content"


def test_apply_decision_merge_with_missing_target_still_stores(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    from alpha.agents.memory.l1.models import DedupDecision, ExtractedMemory

    candidates = candidate_records([ExtractedMemory(content="orphan merge", priority=70)])
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
    counts = apply_decisions(store, candidates, outcome, user_id="u", agent_name="a")
    assert counts["stored"] == 1
    assert store.count("u", "a") == 1


def test_l1_dedup_recall_feeds_unified_pool(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path))
    existing = MemoryRecord.create("Kyoto trip booked", memory_type="episodic")
    store.put_records([existing], user_id="u", agent_name="a")

    from alpha.agents.memory.l1.dedup import L1Dedup
    from alpha.agents.memory.l1.models import ExtractedMemory

    candidates = candidate_records(
        [ExtractedMemory(content="kyoto trip reservation", priority=80)]
    )
    model = FakeModel(
        extraction=EXTRACTION_OK,
        dedup=json.dumps(
            [{"record_id": candidates[0].id, "action": "store", "target_ids": []}]
        ),
    )
    dedup = L1Dedup(store, model, top_k=3, user_id="u", agent_name="a")
    outcome = dedup.detect(candidates, thread_id="t1")
    assert outcome.status == "ok"
    # The conflict prompt the model saw must contain the existing candidate.
    conflict_prompts = [p for p in model.prompts if "conflict detector" in p]
    assert conflict_prompts and existing.id in conflict_prompts[0]


# ---------------------------------------------------------------------------
# extractor
# ---------------------------------------------------------------------------


def test_extractor_returns_honest_llm_error_without_model() -> None:
    from alpha.agents.memory.l1.extractor import L1Extractor

    outcome = L1Extractor(model=None, model_name="definitely-missing-model").extract(
        [{"id": "m1", "role": "user", "content": "hi"}]
    )
    assert outcome.status == "llm_error"
    assert outcome.error == "no_model_configured"
    assert extraction_status_known(outcome.status)


def test_extractor_reports_llm_exception_instead_of_raising() -> None:
    from alpha.agents.memory.l1.extractor import L1Extractor

    outcome = L1Extractor(model=BrokenModel()).extract(
        [{"id": "m1", "role": "user", "content": "hi"}]
    )
    assert outcome.status == "llm_error"
    assert "unreachable" in outcome.error


def test_extractor_no_messages_is_empty_scenes() -> None:
    from alpha.agents.memory.l1.extractor import L1Extractor

    assert L1Extractor(model=FakeModel(EXTRACTION_OK)).extract([]).status == "empty_scenes"


# ---------------------------------------------------------------------------
# persona
# ---------------------------------------------------------------------------


def test_persona_below_threshold_makes_no_llm_call(tmp_path) -> None:
    from alpha.agents.memory.l1.persona import load_profile, synthesize

    store = L1RecordStore(str(tmp_path))
    store.put_records(
        [MemoryRecord.create("one persona fact", memory_type="persona", priority=80)],
        user_id="u",
        agent_name="a",
    )
    model = FakeModel(extraction=EXTRACTION_OK)
    report = synthesize(store, model, user_id="u", agent_name="a", min_memories=3)
    assert report.status == "below_threshold"
    assert model.prompts == []
    assert load_profile(store, user_id="u", agent_name="a") == ""


def test_persona_writes_profile_when_threshold_met(tmp_path) -> None:
    from alpha.agents.memory.l1.persona import load_profile, synthesize

    store = L1RecordStore(str(tmp_path))
    store.put_records(
        [
            MemoryRecord.create("likes tea", memory_type="persona", priority=80),
            MemoryRecord.create("works as a nurse", memory_type="persona", priority=90),
            MemoryRecord.create("lives with a sibling", memory_type="persona", priority=70),
        ],
        user_id="u",
        agent_name="a",
    )
    model = FakeModel(extraction=EXTRACTION_OK, persona="# Profile\n## Identity\nTea-drinking nurse.")
    report = synthesize(store, model, user_id="u", agent_name="a", min_memories=3)
    assert report.status == "updated"
    assert "Tea-drinking nurse" in load_profile(store, user_id="u", agent_name="a")


def test_persona_llm_error_keeps_existing_profile(tmp_path) -> None:
    from alpha.agents.memory.l1.persona import load_profile, save_profile, synthesize

    store = L1RecordStore(str(tmp_path))
    store.put_records(
        [
            MemoryRecord.create("likes tea", memory_type="persona", priority=80),
            MemoryRecord.create("works as a nurse", memory_type="persona", priority=90),
            MemoryRecord.create("lives with a sibling", memory_type="persona", priority=70),
        ],
        user_id="u",
        agent_name="a",
    )
    save_profile(store, "# Profile\nexisting", user_id="u", agent_name="a")
    report = synthesize(store, BrokenModel(), user_id="u", agent_name="a", min_memories=3)
    assert report.status == "llm_error"
    assert "existing" in load_profile(store, user_id="u", agent_name="a")


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------


def test_pipeline_end_to_end_run_reports_honestly(tmp_path) -> None:
    cfg = make_config(tmp_path, persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    model = FakeModel(extraction=EXTRACTION_OK)
    pipeline = L1Pipeline(config=cfg, store=store, model=model)

    report = pipeline.run_job(make_job(TURNS))
    assert report.status == "succeeded"
    assert report.extraction_status == "ok"
    assert report.dedup_status == "no_existing", "no existing records -> conflict call skipped"
    assert report.stored == 2
    assert store.count("u1", "lead") == 2

    records = store.list_records("u1", "lead")
    assert {r.type for r in records} == {"episodic", "persona"}
    scene_names = {r.scene_name for r in records}
    assert scene_names == {"planning the kyoto trip"}


def test_pipeline_skips_when_gate_off(tmp_path) -> None:
    cfg = make_config(tmp_path, enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=EXTRACTION_OK))
    report = pipeline.run_job(make_job(TURNS))
    assert report.status == "skipped"
    assert report.reason == "disabled"
    assert store.count("u1", "lead") == 0


def test_pipeline_second_run_with_same_messages_is_skipped(tmp_path) -> None:
    cfg = make_config(tmp_path, persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=EXTRACTION_OK))
    assert pipeline.run_job(make_job(TURNS)).status == "succeeded"
    again = pipeline.run_job(make_job(TURNS))
    assert again.status == "skipped"
    assert again.reason == "no_new_messages"


def test_pipeline_failed_extraction_does_not_advance_cursor(tmp_path) -> None:
    cfg = make_config(tmp_path, persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=BrokenModel())

    report = pipeline.run_job(make_job(TURNS))
    assert report.status == "failed"
    assert report.extraction_status == "llm_error"
    assert store.processed_keys("t1", user_id="u1", agent_name="lead") == set(), (
        "a failed run must be retried on the next turn"
    )

    # Recovery: a healthy model retries the SAME messages and succeeds.
    recovered = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=EXTRACTION_OK))
    assert recovered.run_job(make_job(TURNS)).status == "succeeded"
    assert store.processed_keys("t1", user_id="u1", agent_name="lead")


def test_pipeline_unparseable_extraction_is_a_failure_not_a_silent_zero(tmp_path) -> None:
    cfg = make_config(tmp_path, persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction="total nonsense"))
    report = pipeline.run_job(make_job(TURNS))
    assert report.status == "failed"
    assert report.extraction_status == "parse_fail"
    assert report.stored == 0
    assert store.count("u1", "lead") == 0


def test_pipeline_min_priority_filter_drops_weak_memories(tmp_path) -> None:
    cfg = make_config(tmp_path, min_priority=80, persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=EXTRACTION_OK))
    report = pipeline.run_job(make_job(TURNS))
    assert report.stored == 1, "priority-70 memory dropped by the 80 floor"
    assert store.list_records("u1", "lead")[0].priority == 85


def test_pipeline_keeps_negative_one_sentinel_below_floor(tmp_path) -> None:
    raw = json.dumps(
        [
            {
                "scene_name": "s",
                "message_ids": ["m1"],
                "memories": [
                    {"content": "Never auto-deploy on Fridays.", "type": "instruction", "priority": -1}
                ],
            }
        ]
    )
    cfg = make_config(tmp_path, min_priority=60, persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=raw))
    report = pipeline.run_job(make_job(TURNS))
    assert report.stored == 1, "-1 strict-order sentinel must survive min_priority filtering"


def test_pipeline_max_memories_per_run_cap(tmp_path) -> None:
    memories = [
        {"content": f"memory number {i}", "type": "episodic", "priority": 90 - i}
        for i in range(10)
    ]
    raw = json.dumps([{"scene_name": "s", "message_ids": ["m1"], "memories": memories}])
    cfg = make_config(tmp_path, max_memories_per_run=3, persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=raw))
    report = pipeline.run_job(make_job(TURNS))
    assert report.stored == 3
    assert store.count("u1", "lead") == 3


def test_pipeline_quota_credit_limit_blocks_before_llm_call(tmp_path) -> None:
    cfg = make_config(tmp_path, quota_credit_limit=1.0, persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    model = FakeModel(extraction=EXTRACTION_OK)
    pipeline = L1Pipeline(config=cfg, store=store, model=model)

    # Spend the credit budget directly.
    from alpha.agents.memory.l1.quota import L1QuotaManager

    L1QuotaManager(
        user_id="u1",
        memory_limit=cfg.l1.quota_memory_limit,
        credit_limit=1.0,
        storage_path=str(store.root),
    ).add_credits(5.0)

    report = pipeline.run_job(make_job(TURNS))
    assert report.status == "skipped"
    assert report.reason == "credit_limit_exceeded"
    assert report.quota_blocked is True
    assert model.prompts == [], "no LLM call may happen past the credit limit"


def test_pipeline_quota_record_limit_refusals_are_disclosed(tmp_path) -> None:
    cfg = make_config(tmp_path, quota_memory_limit=1, persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    store.put_records(
        [MemoryRecord.create("preexisting occupant", priority=90)],
        user_id="u1",
        agent_name="lead",
    )
    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=EXTRACTION_OK))
    report = pipeline.run_job(make_job(TURNS))
    assert report.quota_blocked is True
    assert report.refused == 2
    assert report.status == "skipped"
    assert report.reason == "memory_limit_exceeded"
    assert store.count("u1", "lead") == 1, "existing records untouched"


def test_pipeline_provenance_logs_every_outcome(tmp_path) -> None:
    from datetime import datetime

    day = datetime.now(UTC).strftime("%Y-%m-%d")

    ok_cfg = make_config(tmp_path, persona_enabled=False)
    store = L1RecordStore(ok_cfg.l1.storage_path)
    ok_pipeline = L1Pipeline(config=ok_cfg, store=store, model=FakeModel(extraction=EXTRACTION_OK))
    ok_pipeline.run_job(make_job(TURNS))

    fail_store = L1RecordStore(ok_cfg.l1.storage_path)
    fail_pipeline = L1Pipeline(config=ok_cfg, store=fail_store, model=BrokenModel())
    fail_pipeline.run_job(make_job(list(TURNS), thread_id="t2"))

    entries = read_entries(day, user_id="u1", storage_path=str(store.root))
    statuses = {e["status"] for e in entries}
    assert {"succeeded", "failed"} <= statuses
    succeeded = next(e for e in entries if e["status"] == "succeeded")
    assert succeeded["extraction_status"] == "ok"
    assert succeeded["thread_id"] == "t1"


def test_pipeline_dedup_conflict_run_applies_decisions(tmp_path) -> None:
    cfg = make_config(tmp_path, persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    store.put_records(
        [MemoryRecord.create("Kyoto trip booked for April", memory_type="episodic", priority=70)],
        user_id="u1",
        agent_name="lead",
    )
    dedup_reply = json.dumps(
        [
            {
                "record_id": "irrelevant",
                "action": "store",
                "target_ids": [],
            }
        ]
    )
    model = FakeModel(extraction=EXTRACTION_OK, dedup=dedup_reply)
    pipeline = L1Pipeline(config=cfg, store=store, model=model)
    report = pipeline.run_job(make_job(TURNS))

    assert report.dedup_status == "ok"
    assert report.stored + report.merged + report.updated == 2
    assert store.count("u1", "lead") == 3
    assert len(model.prompts) == 2, "extraction + conflict, persona off"


def test_pipeline_dedup_llm_error_fails_open_and_stores(tmp_path) -> None:
    cfg = make_config(tmp_path, persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    store.put_records(
        [MemoryRecord.create("Kyoto trip booked for April", memory_type="episodic")],
        user_id="u1",
        agent_name="lead",
    )

    class DedupBrokenModel:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, prompt: str, config: dict | None = None) -> str:
            self.calls += 1
            if "memory extraction expert" in prompt:
                return EXTRACTION_OK
            raise RuntimeError("dedup endpoint down")

    model = DedupBrokenModel()
    pipeline = L1Pipeline(config=cfg, store=store, model=model)
    report = pipeline.run_job(make_job(TURNS))
    assert report.dedup_status == "llm_error"
    assert report.stored == 2, "fail-open: nothing lost when dedup is down"
    assert store.count("u1", "lead") == 3


def test_pipeline_retention_sweep_runs_after_write(tmp_path) -> None:
    cfg = make_config(tmp_path, retention_max_records=1, persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=EXTRACTION_OK))
    report = pipeline.run_job(make_job(TURNS))
    assert report.retention.get("capped") == 1
    assert store.count("u1", "lead") == 1, "cap enforced after the run"


def test_pipeline_capture_is_gated_and_flush_runs_synchronously(tmp_path) -> None:
    # A long debounce keeps the debounce TIMER out of this test on purpose:
    # `capture()` arms `threading.Timer(delay, _drain)`, so with the suite's
    # default delay of 0.0 the timer thread races `flush()` and this assertion
    # tests whichever one the scheduler happened to favour. The timer path has
    # its own test below (test_pipeline_debounce_timer_drains_without_flush).
    cfg = make_config(tmp_path, persona_enabled=False, debounce_seconds=30.0)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=EXTRACTION_OK))

    assert pipeline.capture("t1", TURNS, user_id="u1", agent_name="lead") is True
    assert pipeline.flush() == 1, "flush drains the pending job without sleeping"
    assert store.count("u1", "lead") == 2

    gated = L1Pipeline(
        config=MemoryConfig.model_validate({"enabled": True, "l1": {"enabled": False}}),
        store=store,
        model=FakeModel(extraction=EXTRACTION_OK),
    )
    assert gated.capture("t1", TURNS, user_id="u1") is False
    assert gated.flush() == 0


def test_pipeline_debounce_timer_drains_without_flush(tmp_path) -> None:
    """The debounce timer must drain a captured turn on its own.

    This is the behaviour the synchronous-flush test deliberately does not
    exercise. At `debounce_seconds=0.0` the armed timer is the drainer, so the
    wait is bounded by a deadline and polls the store instead of sleeping a
    fixed amount and hoping.
    """
    cfg = make_config(tmp_path, persona_enabled=False, debounce_seconds=0.0)
    store = L1RecordStore(cfg.l1.storage_path)
    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=EXTRACTION_OK))

    assert pipeline.capture("t1", TURNS, user_id="u1", agent_name="lead") is True

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and store.count("u1", "lead") < 2:
        time.sleep(0.02)

    assert store.count("u1", "lead") == 2, "the debounce timer must drain the queued turn"
    assert pipeline.flush() == 0, "the timer already drained it, so nothing is left pending"


def test_pipeline_recall_block_lists_records_and_persona(tmp_path) -> None:
    from alpha.agents.memory.l1.persona import save_profile

    cfg = make_config(tmp_path)
    store = L1RecordStore(cfg.l1.storage_path)
    store.put_records(
        [MemoryRecord.create("The user prefers dark mode.", memory_type="persona", priority=88)],
        user_id="u1",
        agent_name="lead",
    )
    save_profile(store, "# Profile\n## Identity\nNight owl.", user_id="u1", agent_name="lead")

    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=EXTRACTION_OK))
    block = pipeline.recall(user_id="u1", agent_name="lead")
    assert "dark mode" in block
    assert "Night owl" in block
    assert block.startswith("### L1 working memory")


def test_pipeline_recall_is_empty_when_disabled(tmp_path) -> None:
    cfg = make_config(tmp_path, recall_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    store.put_records(
        [MemoryRecord.create("secret", priority=90)], user_id="u1", agent_name="lead"
    )
    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=EXTRACTION_OK))
    assert pipeline.recall(user_id="u1", agent_name="lead") == ""


def test_pipeline_recall_uses_hybrid_search_for_query(tmp_path) -> None:
    cfg = make_config(tmp_path)
    store = L1RecordStore(cfg.l1.storage_path)
    store.put_records(
        [
            MemoryRecord.create("Kyoto itinerary locked for April", priority=90),
            MemoryRecord.create("The user dislikes cilantro", priority=80),
        ],
        user_id="u1",
        agent_name="lead",
    )
    pipeline = L1Pipeline(config=cfg, store=store, model=FakeModel(extraction=EXTRACTION_OK))
    block = pipeline.recall(user_id="u1", agent_name="lead", query="kyoto itinerary")
    assert "Kyoto itinerary" in block
    assert "cilantro" not in block


def test_pipeline_work_mode_reaches_work_prompts(tmp_path) -> None:
    cfg = make_config(tmp_path, mode="work", persona_enabled=False)
    store = L1RecordStore(cfg.l1.storage_path)
    model = FakeModel(extraction=EXTRACTION_OK)
    pipeline = L1Pipeline(config=cfg, store=store, model=model)
    report = pipeline.run_job(make_job(TURNS, mode="work"))
    assert report.status == "succeeded"
    extraction_prompt = next(p for p in model.prompts if "memory extraction expert" in p)
    assert "work scene segmentation" in extraction_prompt


# ---------------------------------------------------------------------------
# message handling helpers
# ---------------------------------------------------------------------------


def test_filter_capture_messages_keeps_user_and_final_assistant_only() -> None:
    messages = [
        Msg("human", "real question", "a"),
        Msg("ai", "tool-ish reply", "b", tool_calls=[{"name": "search"}]),
        Msg("ai", "final reply", "c"),
        Msg("system", "system noise", "d"),
        Msg("human", "", "e"),
    ]
    kept = filter_capture_messages(messages)
    assert [m.id for m in kept] == ["a", "c"]


def test_filter_capture_messages_drops_framework_hidden_messages() -> None:
    hidden = Msg("human", "todo reminder payload", "x")
    hidden.additional_kwargs = {"hide_from_ui": True}
    assert filter_capture_messages([hidden]) == []


def test_message_key_prefers_id_and_falls_back_deterministically() -> None:
    msg = Msg("human", "hello")
    assert message_key(msg, 0) == message_key(Msg("human", "hello"), 0)
    assert message_key(Msg("human", "hello", "abc"), 0) == "abc"


def test_messages_to_prompt_dicts_shape() -> None:
    dicts = messages_to_prompt_dicts([Msg("human", "hi", "id1")])
    assert dicts == [{"id": "id1", "role": "user", "content": "hi", "timestamp": ""}]


# ---------------------------------------------------------------------------
# middleware wiring
# ---------------------------------------------------------------------------


class _Runtime:
    def __init__(self, **context: Any) -> None:
        self.context = context


def test_middleware_enqueues_when_l1_enabled(tmp_path, monkeypatch) -> None:
    from alpha.agents.middlewares import l1_memory_middleware as module

    cfg = make_config(tmp_path, persona_enabled=False)
    captured: list[tuple[str, list]] = []

    class _Pipeline:
        def capture(self, thread_id, messages, **kwargs):
            captured.append((thread_id, messages))
            return True

    monkeypatch.setattr(module, "get_memory_config", lambda: cfg)
    monkeypatch.setattr(module, "get_l1_pipeline", lambda: _Pipeline())

    middleware = module.L1MemoryMiddleware(agent_name="lead", memory_config=cfg)
    state = {"messages": TURNS}
    result = middleware.after_agent(state, _Runtime(thread_id="t1"))

    assert result is None
    assert captured and captured[0][0] == "t1"


def test_middleware_noop_when_l1_disabled(tmp_path, monkeypatch) -> None:
    from alpha.agents.middlewares import l1_memory_middleware as module

    cfg = MemoryConfig.model_validate({"enabled": True, "l1": {"enabled": False}})
    called: list[Any] = []

    monkeypatch.setattr(module, "get_memory_config", lambda: cfg)
    monkeypatch.setattr(module, "get_l1_pipeline", lambda: called.append(1))

    middleware = module.L1MemoryMiddleware(memory_config=cfg)
    assert middleware.after_agent({"messages": TURNS}, _Runtime(thread_id="t1")) is None
    assert called == []


def test_middleware_survives_capture_exception(tmp_path, monkeypatch) -> None:
    from alpha.agents.middlewares import l1_memory_middleware as module

    cfg = make_config(tmp_path)

    class _Boom:
        def capture(self, *args, **kwargs):
            raise RuntimeError("queue exploded")

    monkeypatch.setattr(module, "get_memory_config", lambda: cfg)
    monkeypatch.setattr(module, "get_l1_pipeline", lambda: _Boom())

    middleware = module.L1MemoryMiddleware(memory_config=cfg)
    # Must not raise: a broken capture never breaks the agent turn.
    assert middleware.after_agent({"messages": TURNS}, _Runtime(thread_id="t1")) is None


def test_lead_agent_registers_l1_middleware_only_when_enabled(tmp_path) -> None:
    """The registration site reads l1_enabled() before appending."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packages"
        / "harness"
        / "alpha"
        / "agents"
        / "lead_agent"
        / "agent.py"
    ).read_text(encoding="utf-8")
    assert "l1_memory_enabled(" in source
    assert "L1MemoryMiddleware(" in source

    factory = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packages"
        / "harness"
        / "alpha"
        / "agents"
        / "factory.py"
    ).read_text(encoding="utf-8")
    assert "l1_memory_enabled(" in factory
    assert "L1MemoryMiddleware(" in factory


def test_prompt_injection_reads_l1_recall(tmp_path) -> None:
    """``_get_memory_context`` must call the L1 recall seam behind the gate."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packages"
        / "harness"
        / "alpha"
        / "agents"
        / "lead_agent"
        / "prompt.py"
    ).read_text(encoding="utf-8")
    assert "l1_enabled(config)" in source
    assert "get_l1_pipeline()" in source
    assert ".recall(" in source


def test_prompt_injection_injects_l1_block_end_to_end(tmp_path, monkeypatch) -> None:
    """Real _get_memory_context, fake backend, real L1 store -> block appears."""
    from alpha.agents.lead_agent import prompt as prompt_module
    from alpha.agents.memory.l1.persona import save_profile

    cfg = make_config(tmp_path)
    store = L1RecordStore(cfg.l1.storage_path)
    store.put_records(
        [MemoryRecord.create("User prefers TypeScript.", memory_type="persona", priority=90)],
        user_id="u1",
        agent_name="lead",
    )
    save_profile(store, "# Profile\nTypeScript fan.", user_id="u1", agent_name="lead")

    class _Manager:
        def get_context(self, user_id=None, agent_name=None, thread_id=None):
            return ""

    monkeypatch.setattr("alpha.agents.memory.get_memory_manager", lambda: _Manager())
    monkeypatch.setattr(prompt_module, "_get_memory_context", prompt_module._get_memory_context)

    block = prompt_module._get_memory_context(
        "lead", app_config=type("C", (), {"memory": cfg})(), user_id="u1"
    )
    assert "TypeScript" in block
    assert block.strip().startswith("<memory>")


def test_prompt_injection_stays_empty_when_l1_off(tmp_path, monkeypatch) -> None:
    from alpha.agents.lead_agent import prompt as prompt_module

    cfg = MemoryConfig.model_validate({"enabled": True, "l1": {"enabled": False}})

    class _Manager:
        def get_context(self, user_id=None, agent_name=None, thread_id=None):
            return "backend content"

    monkeypatch.setattr("alpha.agents.memory.get_memory_manager", lambda: _Manager())
    block = prompt_module._get_memory_context(
        "lead", app_config=type("C", (), {"memory": cfg})(), user_id="u1"
    )
    assert "L1 working memory" not in block
