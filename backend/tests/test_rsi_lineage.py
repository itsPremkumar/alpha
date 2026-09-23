"""WP-A1 tests: RSI lineage store + durable candidate archive.

Honesty pins (plan §3 WP-A1, §5.6):
- one real JSONL event per record/transition; ``ancestry()`` walks the real
  parent chain and never invents an ancestor (unknown parent → ``null``);
- corrupt ``lineage.jsonl`` lines are skipped with an honest counted warning
  while surviving records still load, and the rebuilt snapshot contains only
  those honest records;
- a rejected candidate archives with its **exact** ``failure_cause``
  (equality — no invented or softened reasons);
- persistence failure reports ``persistence="degraded"`` and completes
  in-memory with **no exception escaping** (the real error is logged);
- every stored record's ``evidence_kind`` is inside the whitelist
  ``{measured, simulated, heuristic, unverified}`` — enforced at write and
  load, fail-closed otherwise.

Every test points ``AGENT_WORKSPACE_HOME`` at a temp dir (the environment does
not isolate it).
"""

import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from alpha.evolution.engine import EvolutionEngine
from alpha.rsi.archive import archive_candidate, list_archive
from alpha.rsi.lineage import RsiLineageStore, record_from_evol_candidate

EVIDENCE_WHITELIST = {"measured", "simulated", "heuristic", "unverified"}


@pytest.fixture(autouse=True)
def rsi_home(tmp_path, monkeypatch):
    """Run every test against a temp AGENT_WORKSPACE_HOME (env is global)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    return tmp_path


def _candidate(candidate_id, **overrides):
    data = {
        "candidate_id": candidate_id,
        "cycle_id": "cycle-1",
        "surface": "prompt",
        "target": "system_prompt",
        "payload_hash": "sha256:test",
        "evidence_kind": "measured",
        "created_at": 1758000000.0,
        "status": "candidate",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_record_and_transition_append_real_lineage_events(rsi_home):
    store = RsiLineageStore()
    store.record(_candidate("cand-parent"), parent_id=None, mutation_operator="conservative")
    store.record(_candidate("cand-child"), parent_id="cand-parent", mutation_operator="alternative")
    lineage_path = rsi_home / "rsi" / "lineage.jsonl"
    lines = lineage_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "recorded"
    assert first["candidate_id"] == "cand-parent"
    # unknown parent -> null, never fabricated
    assert first["record"]["parent_id"] is None
    assert first["record"]["mutation_operator"] == "conservative"
    assert first["record"]["cycle_id"] == "cycle-1"
    second = json.loads(lines[1])
    assert second["record"]["parent_id"] == "cand-parent"

    promoted = store.transition("cand-child", "promoted", reason="strictly better vs baseline", evidence={"source": "real-benchmark"})
    assert promoted.status == "promoted"
    lines = lineage_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    last = json.loads(lines[-1])
    assert last["event"] == "transitioned"
    assert last["status"] == "promoted"
    assert last["reason"] == "strictly better vs baseline"  # verbatim
    assert last["evidence"] == {"source": "real-benchmark"}
    assert last["record"]["status"] == "promoted"

    # durability: a fresh store over the same storage loads the same events
    fresh = RsiLineageStore()
    assert fresh.get("cand-child") is not None
    assert fresh.get("cand-child").status == "promoted"
    assert fresh.get("cand-parent").parent_id is None
    assert fresh.skipped_lines == 0
    # a durable identity is recorded once — duplicates fail closed
    with pytest.raises(ValueError, match="already"):
        fresh.record(_candidate("cand-parent"), parent_id=None, mutation_operator="conservative")

    # atomic snapshot mirrors exactly the same records (tmp + os.replace)
    snapshot = json.loads((rsi_home / "rsi" / "lineage_snapshot.json").read_text(encoding="utf-8"))
    assert {record["candidate_id"] for record in snapshot["records"]} == {"cand-parent", "cand-child"}
    assert snapshot["skipped_corrupt_lines"] == 0
    assert not list((rsi_home / "rsi").glob("*.tmp"))


def test_ancestry_walks_real_parent_chain_and_stops_at_unknown_links(rsi_home):
    store = RsiLineageStore()
    store.record(_candidate("gen-0"), parent_id=None, mutation_operator="conservative")
    store.record(_candidate("gen-1"), parent_id="gen-0", mutation_operator="conservative")
    store.record(_candidate("gen-2"), parent_id="gen-1", mutation_operator="conservative")
    store.record(_candidate("orphan"), parent_id="never-recorded-parent", mutation_operator="alternative")

    # real parent chain, newest first
    chain = store.ancestry("gen-2")
    assert [record.candidate_id for record in chain] == ["gen-2", "gen-1", "gen-0"]
    assert [record.candidate_id for record in store.ancestry("gen-0")] == ["gen-0"]
    assert store.ancestry("nope") == []

    # unknown parent: the caller's claim is stored verbatim (absent parent ->
    # null above), and the walk STOPS at the unresolvable link instead of
    # inventing an ancestor.
    orphan_chain = store.ancestry("orphan")
    assert [record.candidate_id for record in orphan_chain] == ["orphan"]
    assert orphan_chain[0].parent_id == "never-recorded-parent"

    # a corrupted parent cycle can never hang the walk (visited set + budget)
    store._records["gen-0"].parent_id = "gen-2"  # craft a cycle the API cannot produce
    assert [record.candidate_id for record in store.ancestry("gen-2")] == ["gen-2", "gen-1", "gen-0"]


def test_corrupt_lineage_lines_skipped_with_honest_count(rsi_home, caplog):
    store = RsiLineageStore()
    store.record(_candidate("good-1"), parent_id=None, mutation_operator="conservative")
    lineage_path = rsi_home / "rsi" / "lineage.jsonl"
    with lineage_path.open("a", encoding="utf-8") as handle:
        handle.write("{this is not json at all\n")
        handle.write('{"event": "mystery_event", "at": 1.0}\n')
    store.record(_candidate("good-2"), parent_id="good-1", mutation_operator="alternative")

    with caplog.at_level(logging.WARNING, logger="alpha.rsi.lineage"):
        fresh = RsiLineageStore()
    # honest skipped count — reported both as an attribute and in the warning
    assert fresh.skipped_lines == 2
    assert "Skipped 2 corrupt/partial RSI lineage line(s)" in caplog.text

    # remaining records load; the corrupt lines produced NO records
    assert fresh.get("good-1") is not None
    assert fresh.get("good-2") is not None
    assert fresh.get("mystery_event") is None
    assert [record.candidate_id for record in fresh.ancestry("good-2")] == ["good-2", "good-1"]

    # the rebuilt snapshot contains only honest records (no fabricated history)
    snapshot = json.loads((rsi_home / "rsi" / "lineage_snapshot.json").read_text(encoding="utf-8"))
    assert sorted(record["candidate_id"] for record in snapshot["records"]) == ["good-1", "good-2"]
    assert snapshot["skipped_corrupt_lines"] == 2


def test_archive_retains_rejected_candidate_with_exact_failure_cause(rsi_home):
    cause = "benchmark regressions vs baseline"
    payload = {"surface": "prompt", "diff": "- max_budget_chars: 100"}
    path = archive_candidate("ev-rejected-1", payload=payload, status="rejected", failure_cause=cause, failed_evaluator="holdout_suite")
    expected = rsi_home / "rsi" / "archive" / "ev-rejected-1.json"
    assert path == expected
    entry = json.loads(expected.read_text(encoding="utf-8"))
    # equality with the supplied cause — no invented or softened reasons
    assert entry["failure_cause"] == cause
    assert entry["failed_evaluator"] == "holdout_suite"
    assert entry["status"] == "rejected"
    assert entry["candidate_id"] == "ev-rejected-1"
    assert entry["payload"] == payload
    # atomic write leaves no staging file behind
    assert not list(expected.parent.glob("*.tmp"))

    listed = list_archive()
    assert [item["candidate_id"] for item in listed] == ["ev-rejected-1"]
    rejected = list_archive(status="rejected")
    assert rejected == listed
    assert rejected[0]["failure_cause"] == cause
    assert list_archive(status="promoted") == []
    with pytest.raises(ValueError, match="not one of the allowed lineage statuses"):
        list_archive(status="banana")

    # path traversal is refused — archiving can never escape the directory
    with pytest.raises(ValueError, match="Unsafe candidate_id"):
        archive_candidate("../escape", payload={}, status="rejected", failure_cause=None, failed_evaluator=None)


def test_corrupt_archive_entries_skipped_with_honest_count(rsi_home, caplog):
    archive_candidate("ev-ok-1", payload={}, status="rejected", failure_cause="real cause", failed_evaluator=None)
    archive_dir = rsi_home / "rsi" / "archive"
    (archive_dir / "not-json.json").write_text("{broken", encoding="utf-8")
    (archive_dir / "foreign.json").write_text('{"hello": "world"}', encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="alpha.rsi.archive"):
        listed = list_archive()
    assert [entry["candidate_id"] for entry in listed] == ["ev-ok-1"]
    assert "Skipped 2 corrupt/non-archive file(s)" in caplog.text
    assert "real cause" in listed[0]["failure_cause"]


def test_persistence_degraded_reported_when_root_unwritable(rsi_home, monkeypatch, caplog):
    store = RsiLineageStore()
    lineage_path = rsi_home / "rsi" / "lineage.jsonl"
    assert not lineage_path.exists()

    def _deny(self, *args, **kwargs):
        raise OSError("workspace is read-only (test)")

    monkeypatch.setattr(Path, "mkdir", _deny)
    with caplog.at_level(logging.WARNING, logger="alpha.rsi.lineage"):
        record = store.record(_candidate("degraded-1"), parent_id=None, mutation_operator="conservative")
        transitioned = store.transition("degraded-1", "rejected", reason="holdout failed")

    # no exception escaped: both operations completed in-memory
    assert record.candidate_id == "degraded-1"
    # records are live handles: transition() mutates in place, so the earlier
    # reference shows the final status and identity-checks against the store
    assert record is store.get("degraded-1")
    assert transitioned is record
    assert record.status == "rejected"
    assert store.persistence == "degraded"
    assert not lineage_path.exists()
    # the real error text is disclosed, not swallowed
    assert "workspace is read-only (test)" in caplog.text

    # a fresh store sees an honest empty state — degraded storage never
    # fabricates a history it could not persist
    fresh = RsiLineageStore()
    assert fresh.get("degraded-1") is None
    assert fresh.persistence == "degraded"


def test_every_stored_record_evidence_kind_is_whitelisted(rsi_home):
    store = RsiLineageStore()
    for index, kind in enumerate(sorted(EVIDENCE_WHITELIST)):
        store.record(_candidate(f"kind-{index}", evidence_kind=kind), parent_id=None, mutation_operator="conservative")

    # every record — in memory, on disk, and in the snapshot — is whitelisted
    for line in (rsi_home / "rsi" / "lineage.jsonl").read_text(encoding="utf-8").splitlines():
        assert json.loads(line)["record"]["evidence_kind"] in EVIDENCE_WHITELIST
    snapshot = json.loads((rsi_home / "rsi" / "lineage_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["records"]
    for record in snapshot["records"]:
        assert record["evidence_kind"] in EVIDENCE_WHITELIST
    fresh = RsiLineageStore()
    for index in range(len(EVIDENCE_WHITELIST)):
        record = fresh.get(f"kind-{index}")
        assert record is not None
        assert record.evidence_kind in EVIDENCE_WHITELIST
        assert record.to_dict()["evidence_kind"] in EVIDENCE_WHITELIST

    # fail-closed: fabricated evidence labels are refused at the boundary
    with pytest.raises(ValueError, match="whitelist"):
        store.record(_candidate("bad-kind", evidence_kind="95% confident"), parent_id=None, mutation_operator="conservative")
    # legacy default maps to the equivalent honest neutral
    legacy = store.record(_candidate("legacy-kind", evidence_kind="unknown"), parent_id=None, mutation_operator="conservative")
    assert legacy.evidence_kind == "unverified"
    # status whitelist is equally fail-closed
    with pytest.raises(ValueError, match="not one of the allowed lineage statuses"):
        store.record(_candidate("bad-status", status="definitely-promoted"), parent_id=None, mutation_operator="conservative")
    with pytest.raises(ValueError, match="not one of the allowed lineage statuses"):
        store.transition("kind-0", "banana")


def test_transition_unknown_candidate_raises_keyerror(rsi_home):
    store = RsiLineageStore()
    with pytest.raises(KeyError, match="never-seen"):
        store.transition("never-seen", "promoted")


def test_promotions_reports_every_real_promotion_event(rsi_home):
    store = RsiLineageStore()
    store.record(_candidate("promo-1"), parent_id=None, mutation_operator="conservative")
    store.transition("promo-1", "benchmarking")
    store.transition("promo-1", "gated", reason="awaiting human approval")
    store.transition("promo-1", "promoted", reason="human approved + strictly better", evidence={"kind": "measured"})

    promotions = store.promotions()
    assert len(promotions) == 1
    promotion = promotions[0]
    assert promotion["candidate_id"] == "promo-1"
    assert promotion["reason"] == "human approved + strictly better"  # verbatim
    assert promotion["evidence"] == {"kind": "measured"}
    assert promotion["record"]["status"] == "promoted"
    assert promotion["current_status"] == "promoted"

    # a rollback never erases the promotion, and never hides the rollback
    store.transition("promo-1", "rolled_back", reason="canary degraded")
    promotions = store.promotions()
    assert len(promotions) == 1
    assert promotions[0]["current_status"] == "rolled_back"

    # durable across restart
    fresh = RsiLineageStore()
    assert len(fresh.promotions()) == 1
    assert fresh.promotions()[0]["reason"] == "human approved + strictly better"


def test_record_from_evol_candidate_mirrors_real_evolution_candidates(rsi_home):
    engine = EvolutionEngine()
    parent = engine.propose("prompt", "system_prompt", {"max_tokens": 100})
    child = engine.propose("prompt", "system_prompt", {"max_tokens": 80}, parent_id=parent.candidate_id)

    record = record_from_evol_candidate(child)
    assert record.candidate_id == child.candidate_id
    assert record.parent_id == parent.candidate_id  # real parent link, verbatim
    assert record.surface == "prompt"
    assert record.target == "system_prompt"
    assert record.status == "candidate"
    # honest unknowns — evolution records none of these; nothing is invented
    assert record.mutation_operator == "unknown"
    assert record.evidence_kind == "unverified"
    assert record.cycle_id == "unknown"
    # payload hash is a real sha256 over the canonical payload
    expected_hash = hashlib.sha256(json.dumps(child.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert record.payload_hash == expected_hash

    # idempotent mirror: a second call appends no second birth event
    lineage_path = rsi_home / "rsi" / "lineage.jsonl"
    lines_before = len(lineage_path.read_text(encoding="utf-8").splitlines())
    again = record_from_evol_candidate(child)
    assert again == record
    assert len(lineage_path.read_text(encoding="utf-8").splitlines()) == lines_before

    # a root candidate with no parent records null — never a guessed parent
    root_record = record_from_evol_candidate(parent)
    assert root_record.parent_id is None
    assert root_record.candidate_id == parent.candidate_id
    assert RsiLineageStore().get(parent.candidate_id) is not None


def test_missing_storage_yields_honest_empty_state(rsi_home):
    store = RsiLineageStore()
    assert store.get("anything") is None
    assert store.ancestry("anything") == []
    assert store.promotions() == []
    assert store.skipped_lines == 0
    assert store.persistence == "ok"  # honest: no write has failed yet
    assert list_archive() == []
