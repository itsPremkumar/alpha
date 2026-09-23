"""Tests for the FACT/TIP experience bank, storage rules, retrieval disclosure, hygiene, and honest tool rejections."""

import json
import time

from alpha.learning.experience.hygiene import (
    DEDUP_THRESHOLD,
    DOWNSHIFT_FORMULA,
    REVIEW_STALE_AFTER_SECONDS,
    HygieneReport,
    run_hygiene,
)
from alpha.learning.experience.models import NEUTRAL_CONFIDENCE, ExperienceKind, ExperienceRecord, OutcomeType
from alpha.learning.experience.retriever import SCORE_METHOD, ExperienceRetriever
from alpha.learning.experience.store import ExperienceStore
from alpha.tools.builtins import experience_tool as experience_tool_mod
from alpha.tools.builtins.experience_tool import consult_experience


def _aws_key() -> str:
    """A pattern-shaped fake key (16 chars after the AKIA prefix); not a real credential."""
    return "AKIA" + "ABCDEFGHIJKL1234"


# --- 1. FACT/TIP roundtrip ----------------------------------------------------


def test_fact_and_tip_roundtrip_on_disk(tmp_path):
    storage = tmp_path / "memory.json"
    store = ExperienceStore(storage_path=storage, load_defaults=False)

    fact = ExperienceRecord(
        experience_id="fact-pnpm",
        task_goal="Identify frontend package manager",
        outcome=OutcomeType.SUCCESS,
        kind="FACT",
        statement="This repo uses pnpm for frontend installs",
        evidence=["trace:run-123", "outcome:observed pnpm-lock.yaml"],
        tags=["pnpm", "repo"],
    )
    tip = ExperienceRecord(
        experience_id="tip-ruff",
        task_goal="Lint workflow",
        outcome=OutcomeType.SUCCESS,
        kind=ExperienceKind.TIP,
        statement="Run ruff format before ruff check",
        evidence=["outcome:lint passed locally"],
        tags=["lint"],
    )

    fact_result = store.add(fact)
    tip_result = store.add(tip)
    assert fact_result["stored"] is True
    assert tip_result["stored"] is True

    # Honest confidence baseline: neutral 0.5 until real reuse evidence exists.
    assert fact.confidence == NEUTRAL_CONFIDENCE == 0.5
    assert tip.confidence == 0.5

    # Serialized shape uses plain strings and stays JSON-serializable.
    fact_data = fact.to_dict()
    assert fact_data["kind"] == "FACT"
    assert fact_data["outcome"] == "success"
    json.dumps(fact_data)

    reloaded = ExperienceStore(storage_path=storage, load_defaults=False)
    got_fact = reloaded.get("fact-pnpm")
    got_tip = reloaded.get("tip-ruff")
    assert got_fact is not None and got_tip is not None
    assert got_fact.kind == ExperienceKind.FACT
    assert got_fact.statement == "This repo uses pnpm for frontend installs"
    assert got_fact.evidence == ["trace:run-123", "outcome:observed pnpm-lock.yaml"]
    assert got_fact.confidence == 0.5
    assert got_tip.kind == ExperienceKind.TIP
    assert got_tip.statement == "Run ruff format before ruff check"
    # Honest defaults on fresh records.
    assert got_fact.audit == []
    assert got_fact.hygiene_note == ""
    assert got_fact.expires_at is None
    assert got_fact.last_reviewed is None
    assert got_fact.success_count == 0 and got_fact.failure_count == 0 and got_fact.reuse_count == 0


# --- 2. backward compatibility ------------------------------------------------


def test_old_format_record_still_loads(tmp_path):
    storage = tmp_path / "old.json"
    old_payload = [
        {
            "task_goal": "Legacy goal",
            "outcome": "failure",
            "lessons_learned": ["legacy lesson"],
            "pitfalls_to_avoid": ["legacy pitfall"],
            "modified_files": ["a.py"],
            "error_types": ["ValueError"],
            "tags": ["legacy"],
            "experience_id": "legacy-1",
            "timestamp": 1700000000.0,
            "metadata": {"source": "old-format"},
        }
    ]
    storage.write_text(json.dumps(old_payload), encoding="utf-8")

    store = ExperienceStore(storage_path=storage, load_defaults=False)
    rec = store.get("legacy-1")
    assert rec is not None
    # Old records load with honest defaults for every new field.
    assert rec.kind == ExperienceKind.EPISODE
    assert rec.confidence == NEUTRAL_CONFIDENCE
    assert rec.evidence == []
    assert rec.audit == []
    assert rec.hygiene_note == ""
    assert rec.expires_at is None
    assert rec.last_reviewed is None

    # Re-serializes cleanly for the next load.
    roundtrip = ExperienceRecord.from_dict(rec.to_dict())
    assert roundtrip.kind == ExperienceKind.EPISODE
    json.dumps(rec.to_dict())

    # Legacy episode records without evidence remain storable via record()
    # (behaviour pinned by the pre-existing experience tests).
    legacy_result = store.record(ExperienceRecord(task_goal="another legacy", outcome="success"))
    assert legacy_result["stored"] is True


# --- 3. no-evidence rejection -------------------------------------------------


def test_missing_evidence_is_refused_with_honest_reason():
    store = ExperienceStore(load_defaults=False)

    fact = ExperienceRecord(
        experience_id="no-evidence-fact",
        task_goal="Unbacked claim",
        outcome=OutcomeType.SUCCESS,
        kind="FACT",
        statement="Some unbacked stable-sounding claim",
    )
    result = store.add(fact)
    assert result["stored"] is False
    assert "evidence" in result["reason"]
    assert store.get("no-evidence-fact") is None
    assert store.list_all() == []

    # record() also gates the new FACT/TIP kinds.
    tip_result = store.record(ExperienceRecord(task_goal="Unbacked tip", outcome=OutcomeType.SUCCESS, kind="TIP", statement="Do the thing"))
    assert tip_result["stored"] is False
    assert "evidence" in tip_result["reason"]

    # add() gates every kind, episodes included.
    episode_result = store.add(ExperienceRecord(task_goal="ep", outcome=OutcomeType.SUCCESS))
    assert episode_result["stored"] is False
    assert "evidence" in episode_result["reason"]

    # Whitespace-only evidence does not count as evidence.
    blank_result = store.add(ExperienceRecord(task_goal="blank", outcome=OutcomeType.SUCCESS, kind="FACT", statement="x", evidence=["   "]))
    assert blank_result["stored"] is False
    assert store.list_all() == []


# --- 4. secret/PII-shaped rejection -------------------------------------------


def test_secret_and_pii_shaped_content_refused():
    store = ExperienceStore(load_defaults=False)
    aws_key = _aws_key()

    secret_result = store.add(
        ExperienceRecord(
            task_goal="Deploy config",
            outcome=OutcomeType.SUCCESS,
            kind="FACT",
            statement=f"deploy uses {aws_key} on the box",
            evidence=["trace:1"],
        )
    )
    assert secret_result["stored"] is False
    assert "AWS access key ID shape" in secret_result["reason"]
    # The matched secret itself is never echoed back.
    assert aws_key not in secret_result["reason"]

    pii_result = store.add(
        ExperienceRecord(
            task_goal="Contacts",
            outcome=OutcomeType.SUCCESS,
            kind="FACT",
            statement="contact prem.kumar@example.com for access",
            evidence=["trace:2"],
        )
    )
    assert pii_result["stored"] is False
    assert "email-address (PII) shape" in pii_result["reason"]

    # The legacy record() path is screened too.
    legacy_result = store.record(ExperienceRecord(task_goal="password: hunter2hunter2 leaked", outcome=OutcomeType.FAILURE))
    assert legacy_result["stored"] is False
    assert "credential assignment" in legacy_result["reason"]

    assert store.list_all() == []


# --- 5. retrieval reasons / disclosure ----------------------------------------


def test_relevant_for_returns_fact_tip_with_disclosed_reasons():
    store = ExperienceStore(load_defaults=False)
    fact = ExperienceRecord(
        experience_id="fact-repo",
        task_goal="Identify package manager",
        outcome=OutcomeType.SUCCESS,
        kind="FACT",
        statement="This repo uses pnpm for frontend installs",
        evidence=["trace:1"],
        tags=["pnpm"],
    )
    tip = ExperienceRecord(
        experience_id="tip-lint",
        task_goal="Lint order",
        outcome=OutcomeType.SUCCESS,
        kind="TIP",
        statement="Run ruff format before ruff check",
        evidence=["trace:2"],
        tags=["lint", "ruff"],
    )
    episode = ExperienceRecord(
        experience_id="ep-pnpm",
        task_goal="Refactor pnpm build pipeline",
        outcome=OutcomeType.FAILURE,
        tags=["pnpm"],
    )
    expired_fact = ExperienceRecord(
        experience_id="fact-stale",
        task_goal="Temporary note",
        outcome=OutcomeType.SUCCESS,
        kind="FACT",
        statement="Temporary pnpm flag config",
        evidence=["trace:3"],
        tags=["pnpm"],
        expires_at=time.time() - 60,
    )
    for rec in (fact, tip, expired_fact):
        assert store.add(rec)["stored"] is True
    assert store.record(episode)["stored"] is True

    retriever = ExperienceRetriever(store=store)
    items = retriever.relevant_for("pnpm frontend install")
    assert items, "expected at least one FACT/TIP match"
    assert all(i["kind"] in ("FACT", "TIP") for i in items)
    assert all(i["score_method"] == SCORE_METHOD == "lexical_overlap_v1" for i in items)
    for item in items:
        assert item["score"] > 0
        assert item["reason"]
        assert "lexical" in item["reason"] and "overlap" in item["reason"]
        assert "no semantic model" in item["reason"]
        assert isinstance(item["evidence"], list) and item["evidence"]
        assert item["confidence"] == 0.5

    # Kind filtering, expired records skipped, episodes excluded.
    only_tip = retriever.relevant_for("ruff format lint", kinds=["TIP"])
    assert only_tip and all(i["kind"] == "TIP" for i in only_tip)
    returned_ids = {i["experience_id"] for i in items}
    assert "fact-stale" not in returned_ids
    assert "ep-pnpm" not in returned_ids

    # Existing retriever API stays intact for current callers.
    legacy_matches = retriever.retrieve("pnpm frontend install")
    assert legacy_matches
    assert any(m.experience_id == "ep-pnpm" for m in legacy_matches)


# --- 6. hygiene: dedup / merge ------------------------------------------------


def test_hygiene_dedup_merges_counts_and_audits(tmp_path):
    storage = tmp_path / "memory.json"
    store = ExperienceStore(storage_path=storage, load_defaults=False)
    now = time.time()

    a = ExperienceRecord(
        experience_id="fact-a",
        task_goal="pkg manager",
        outcome=OutcomeType.SUCCESS,
        kind="FACT",
        statement="This repo uses pnpm to install frontend dependencies",
        evidence=["trace:a"],
        tags=["pnpm"],
        success_count=2,
        failure_count=1,
        reuse_count=5,
        timestamp=now - 100,
    )
    b = ExperienceRecord(
        experience_id="fact-b",
        task_goal="pkg manager again",
        outcome=OutcomeType.SUCCESS,
        kind="FACT",
        statement="this repo uses pnpm to install frontend dependencies!",
        evidence=["trace:b"],
        tags=["pnpm"],
        success_count=1,
        failure_count=0,
        reuse_count=3,
        timestamp=now - 50,
    )
    words_c = "alfa bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango"
    words_d = words_c.rsplit(" ", 1)[0] + " uniform"
    c = ExperienceRecord(
        experience_id="fact-c",
        task_goal="long note",
        outcome=OutcomeType.SUCCESS,
        kind="FACT",
        statement=words_c,
        evidence=["trace:c"],
        timestamp=now - 90,
    )
    d = ExperienceRecord(
        experience_id="fact-d",
        task_goal="long note variant",
        outcome=OutcomeType.SUCCESS,
        kind="FACT",
        statement=words_d,
        evidence=["trace:d"],
        timestamp=now - 40,
    )
    for rec in (a, b, c, d):
        assert store.add(rec)["stored"] is True

    report = run_hygiene(store)
    assert isinstance(report, HygieneReport)
    assert report.scanned == 4
    assert len(report.merged) == 2

    # Duplicates removed, earliest record survives, real counts summed.
    assert store.get("fact-b") is None
    assert store.get("fact-d") is None
    survivor_a = store.get("fact-a")
    survivor_c = store.get("fact-c")
    assert survivor_a is not None and survivor_c is not None
    assert (survivor_a.success_count, survivor_a.failure_count, survivor_a.reuse_count) == (3, 1, 8)
    assert set(survivor_a.evidence) == {"trace:a", "trace:b"}
    assert set(survivor_c.evidence) == {"trace:c", "trace:d"}

    # In-record audit on survivors.
    merge_a = [e for e in survivor_a.audit if e["action"] == "dedup_merge"]
    assert len(merge_a) == 1
    assert merge_a[0]["detail"]["merged_id"] == "fact-b"
    assert merge_a[0]["detail"]["method"] == "normalized_equality"
    assert merge_a[0]["detail"]["run_id"] == report.run_id
    merge_c = [e for e in survivor_c.audit if e["action"] == "dedup_merge"]
    assert merge_c[0]["detail"]["method"].startswith("jaccard")
    assert merge_c[0]["detail"]["overlap"] >= DEDUP_THRESHOLD

    # Report audit covers both mutations; disclosures name threshold and formula.
    assert [e["action"] for e in report.audit if e["action"] == "dedup_merge"] == ["dedup_merge", "dedup_merge"]
    assert any(str(DEDUP_THRESHOLD) in note for note in report.notes)
    assert any(DOWNSHIFT_FORMULA in note for note in report.notes)

    # Sidecar written next to the store file, JSONL-parsable, run-tagged.
    assert report.sidecar_path is not None
    with open(report.sidecar_path, encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh]
    assert lines
    assert all(line["run_id"] == report.run_id for line in lines)

    # No silent rewrite: merged state + audit survive a disk reload.
    reloaded = ExperienceStore(storage_path=storage, load_defaults=False)
    assert reloaded.get("fact-b") is None
    reloaded_a = reloaded.get("fact-a")
    assert reloaded_a is not None
    assert reloaded_a.success_count == 3
    assert any(e["action"] == "dedup_merge" for e in reloaded_a.audit)


# --- 7. hygiene: expiry -------------------------------------------------------


def test_hygiene_expires_past_records(tmp_path):
    storage = tmp_path / "memory.json"
    store = ExperienceStore(storage_path=storage, load_defaults=False)
    stale = ExperienceRecord(
        experience_id="stale-1",
        task_goal="Old note",
        outcome=OutcomeType.SUCCESS,
        kind="FACT",
        statement="Old pnpm note",
        evidence=["trace:1"],
        expires_at=time.time() - 5,
    )
    fresh = ExperienceRecord(
        experience_id="fresh-1",
        task_goal="Current note",
        outcome=OutcomeType.SUCCESS,
        kind="FACT",
        statement="Current repo note",
        evidence=["trace:2"],
        expires_at=time.time() + 3600,
    )
    assert store.add(stale)["stored"] is True
    assert store.add(fresh)["stored"] is True

    report = run_hygiene(store)
    assert report.scanned == 2
    assert report.expired_ids == ["stale-1"]
    assert store.get("stale-1") is None
    assert store.get("fresh-1") is not None

    expire_entries = [e for e in report.audit if e["action"] == "expire"]
    assert expire_entries and expire_entries[0]["target_id"] == "stale-1"
    assert "expires_at" in expire_entries[0]["detail"]

    reloaded = ExperienceStore(storage_path=storage, load_defaults=False)
    assert reloaded.get("stale-1") is None
    assert reloaded.get("fresh-1") is not None


# --- 8. hygiene: down-rank with real arithmetic -------------------------------


def test_hygiene_downranks_from_real_failure_counts(tmp_path):
    storage = tmp_path / "memory.json"
    store = ExperienceStore(storage_path=storage, load_defaults=False)
    failing = ExperienceRecord(
        experience_id="fail-1",
        task_goal="Risky strategy",
        outcome=OutcomeType.FAILURE,
        kind="FACT",
        statement="Risky deploy strategy note",
        evidence=["trace:1"],
        success_count=0,
        failure_count=3,
    )
    stable = ExperienceRecord(
        experience_id="ok-1",
        task_goal="Stable strategy",
        outcome=OutcomeType.SUCCESS,
        kind="FACT",
        statement="Stable repo note",
        evidence=["trace:2"],
        success_count=5,
        failure_count=0,
    )
    assert store.add(failing)["stored"] is True
    assert store.add(stable)["stored"] is True

    report = run_hygiene(store)
    got = store.get("fail-1")
    assert got is not None
    # Real arithmetic: min(0.5, (0+1)/(0+3+2)) = 0.2
    expected = round(min(0.5, (0 + 1) / (0 + 3 + 2)), 4)
    assert expected == 0.2
    assert got.confidence == expected == 0.2

    # Formula + counts disclosed in the note field.
    assert DOWNSHIFT_FORMULA in got.hygiene_note
    assert "failure_count=3" in got.hygiene_note
    assert "success_count=0" in got.hygiene_note

    downrank_audit = [e for e in got.audit if e["action"] == "downrank"]
    assert len(downrank_audit) == 1
    detail = downrank_audit[0]["detail"]
    assert detail["old_confidence"] == 0.5
    assert detail["new_confidence"] == 0.2
    assert detail["failure_count"] == 3
    assert detail["formula"] == DOWNSHIFT_FORMULA
    assert detail["computed"] == "(0 + 1) / (0 + 3 + 2) = 0.2000"

    assert len(report.downranked) == 1
    assert report.downranked[0]["experience_id"] == "fail-1"
    assert report.downranked[0]["new_confidence"] == 0.2
    assert any(e["action"] == "downrank" for e in report.audit)

    # Hygiene never raises confidence: no failures means no change.
    assert store.get("ok-1").confidence == 0.5

    reloaded = ExperienceStore(storage_path=storage, load_defaults=False)
    assert reloaded.get("fail-1").confidence == 0.2

    # Idempotent: a second pass changes nothing and audits nothing.
    report2 = run_hygiene(store)
    assert report2.downranked == []
    assert report2.audit == []
    assert store.get("fail-1").confidence == 0.2


# --- 9. hygiene: review refresh -----------------------------------------------


def test_hygiene_refreshes_last_reviewed_with_audit(tmp_path):
    storage = tmp_path / "memory.json"
    store = ExperienceStore(storage_path=storage, load_defaults=False)
    rec = ExperienceRecord(
        experience_id="review-1",
        task_goal="Note to review",
        outcome=OutcomeType.SUCCESS,
        kind="FACT",
        statement="Reviewed repo note",
        evidence=["trace:1"],
    )
    assert store.add(rec)["stored"] is True

    report = run_hygiene(store)
    assert report.refreshed_ids == ["review-1"]
    got = store.get("review-1")
    assert got.last_reviewed is not None
    assert abs(got.last_reviewed - time.time()) < 60
    refresh_entries = [e for e in got.audit if e["action"] == "refresh_reviewed"]
    assert len(refresh_entries) == 1
    assert refresh_entries[0]["detail"]["interval_seconds"] == REVIEW_STALE_AFTER_SECONDS

    reloaded = ExperienceStore(storage_path=storage, load_defaults=False)
    assert reloaded.get("review-1").last_reviewed is not None

    # Freshly reviewed records are not re-stamped on the next pass.
    report2 = run_hygiene(store)
    assert report2.refreshed_ids == []
    assert report2.audit == []

    # A record reviewed longer ago than the disclosed interval is refreshed again.
    got.last_reviewed = time.time() - (8 * 24 * 3600)
    report3 = run_hygiene(store)
    assert report3.refreshed_ids == ["review-1"]


# --- 10. tool: FACT/TIP query + record shapes ---------------------------------


def test_tool_records_and_queries_fact_tip(monkeypatch):
    fresh_store = ExperienceStore(load_defaults=False)
    monkeypatch.setattr(experience_tool_mod, "_STORE", fresh_store)

    fact_response = json.loads(
        consult_experience.invoke(
            {
                "query": "This repo uses pnpm for frontend installs",
                "action": "record",
                "type": "FACT",
                "evidence": ["trace:obs-42"],
            }
        )
    )
    assert fact_response["status"] == "recorded"
    assert fact_response["stored"] is True
    assert fact_response["kind"] == "FACT"
    assert fact_response["confidence"] == 0.5
    assert "neutral_baseline" in fact_response["confidence_basis"]
    assert fact_response["evidence"] == ["trace:obs-42"]

    tip_response = json.loads(
        consult_experience.invoke(
            {
                "query": "Run ruff format before ruff check",
                "action": "record",
                "type": "tip",
                "evidence": ["outcome:lint clean"],
            }
        )
    )
    assert tip_response["status"] == "recorded"
    assert tip_response["kind"] == "TIP"

    fact_query = json.loads(consult_experience.invoke({"query": "pnpm frontend install in this repo", "action": "query", "type": "FACT"}))
    assert fact_query["match_count"] >= 1
    assert fact_query["score_method"] == "lexical_overlap_v1"
    assert fact_query["items"][0]["kind"] == "FACT"
    assert fact_query["items"][0]["score"] > 0
    assert fact_query["items"][0]["reason"]
    assert "lexical" in fact_query["note"]

    tip_query = json.loads(consult_experience.invoke({"query": "ruff format lint", "action": "query", "type": "TIP"}))
    assert tip_query["match_count"] >= 1
    assert all(i["kind"] == "TIP" for i in tip_query["items"])

    # Legacy query shape is unchanged when no type filter is given.
    legacy = json.loads(consult_experience.invoke({"query": "anything at all", "action": "query"}))
    assert "experiences" in legacy
    assert "markdown_advice" in legacy
    assert "match_count" in legacy


# --- 11. tool: rejections surfaced honestly -----------------------------------


def test_tool_surfaces_rejections_honestly(monkeypatch):
    monkeypatch.setattr(experience_tool_mod, "_STORE", ExperienceStore(load_defaults=False))

    no_evidence = json.loads(consult_experience.invoke({"query": "Repo uses sqlite for local tests", "action": "record", "type": "FACT"}))
    assert no_evidence["status"] == "rejected"
    assert no_evidence["stored"] is False
    assert "evidence" in no_evidence["reason"]
    assert no_evidence["status"] != "recorded"
    assert experience_tool_mod._STORE.list_all() == []

    aws_key = _aws_key()
    secret = json.loads(
        consult_experience.invoke(
            {
                "query": f"deploy key {aws_key}",
                "action": "record",
                "type": "FACT",
                "evidence": ["trace:1"],
            }
        )
    )
    assert secret["status"] == "rejected"
    assert "AWS access key ID shape" in secret["reason"]
    assert aws_key not in secret["reason"]
    assert experience_tool_mod._STORE.list_all() == []

    bad_type = json.loads(consult_experience.invoke({"query": "x", "action": "query", "type": "PASSWORD"}))
    assert "error" in bad_type
    assert "FACT" in bad_type["error"]

    bad_outcome = json.loads(consult_experience.invoke({"query": "y", "action": "record", "outcome": "meh"}))
    assert bad_outcome["status"] == "rejected"
    assert bad_outcome["stored"] is False
    assert "Invalid outcome" in bad_outcome["reason"]


# --- 12. tool: legacy episode path stays honest --------------------------------


def test_tool_legacy_episode_record_stays_compatible(monkeypatch):
    monkeypatch.setattr(experience_tool_mod, "_STORE", ExperienceStore(load_defaults=False))

    response = json.loads(
        consult_experience.invoke(
            {
                "query": "Cache invalidation on tenant update",
                "action": "record",
                "outcome": "success",
                "lessons": ["Invalidate multi-tenant cache keys using namespace patterns"],
                "pitfalls": ["Flushing the entire Redis cache affects other tenants"],
            }
        )
    )
    assert response["status"] == "recorded"
    assert response["kind"] == "EPISODE"
    assert response["evidence"] == []
    # Honest disclosure: the legacy path stored without evidence references.
    assert "without evidence" in response["note"]

    stored = experience_tool_mod._STORE.get(response["experience_id"])
    assert stored is not None
    assert stored.kind == ExperienceKind.EPISODE
