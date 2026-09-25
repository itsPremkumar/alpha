"""Hermetic contract tests for Alpha's social/shared memory package."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alpha.memory.social import (
    Counterpart,
    InteractionSummary,
    Relationship,
    SocialConfig,
    SocialMemorySystem,
    SocialStore,
    decayed_trust,
    summarize_interaction,
)
from alpha.memory.social.relationships import RelationshipManager

_DAY = datetime.fromtimestamp(1_800_000_000.0, tz=UTC).strftime("%Y-%m-%d")


def make_config(tmp_path: Path, **overrides) -> SocialConfig:
    values = {
        "enabled": True,
        "storage_path": str(tmp_path / "social"),
        "max_counterparts": 20,
        "max_shared_facts": 20,
        "relationship_decay_half_life_days": 10.0,
    }
    values.update(overrides)
    return SocialConfig.model_validate(values)


def make_system(tmp_path: Path, **overrides) -> SocialMemorySystem:
    return SocialMemorySystem(config=make_config(tmp_path, **overrides))


class FakeModel:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.prompts: list[str] = []

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.response


def test_social_memory_is_off_by_default_and_disabled_writes_leave_no_state(tmp_path: Path) -> None:
    config = SocialConfig(storage_path=str(tmp_path / "social"))
    system = SocialMemorySystem(config=config)

    assert config.enabled is False
    assert system.enabled is False
    assert system.add_fact("user:a", "secret").reason == "social_memory_disabled"
    interaction = system.record_interaction("user:a", "Alice")
    assert interaction.reason == "social_memory_disabled"
    assert system.relationship_block("user:a").status == "disabled"
    assert system.shared_context("user:a").status == "disabled"
    assert system.store.list_scope_ids() == []


def test_counterpart_upsert_casefold_resolution_and_merge_are_audited(tmp_path: Path) -> None:
    system = make_system(tmp_path)
    alice = Counterpart(
        id="cp-alice",
        display_name="Alice",
        aliases=["ali"],
        first_seen=100,
        last_seen=100,
    )
    duplicate = Counterpart(
        id="cp-alice-2",
        display_name="ALICE",
        first_seen=90,
        last_seen=110,
    )
    bob = Counterpart(id="cp-bob", display_name="Bob", first_seen=100, last_seen=100)
    bob_alias = Counterpart(id="cp-bobby", display_name="Robert", first_seen=100, last_seen=100)

    assert system.upsert_counterpart("user:a", alice, now=105).success is True
    assert system.upsert_counterpart("user:a", duplicate, now=105).success is True
    system.upsert_counterpart("user:a", bob, now=105)
    system.upsert_counterpart("user:a", bob_alias, now=105)

    resolved = system.store.list_counterparts("user:a")
    assert [item.id for item in resolved] == ["cp-alice", "cp-bob", "cp-bobby"]
    assert "cp-alice-2" in next(item for item in resolved if item.id == "cp-alice").aliases
    assert system.relationships.resolve_counterpart_id("user:a", "ALI") == "cp-alice"
    assert system.relationships.resolve_counterpart_id("user:a", "cp-alice-2") == "cp-alice"

    merged = system.merge_counterparts(
        "user:a",
        "cp-bobby",
        "cp-bob",
        reason="verified duplicate profile",
        now=110,
    )
    assert merged.success is True
    assert [item.id for item in system.store.list_counterparts("user:a")] == ["cp-alice", "cp-bob"]
    assert system.relationships.resolve_counterpart_id("user:a", "robert") == "cp-bob"
    event_day = datetime.fromtimestamp(110, tz=UTC).strftime("%Y-%m-%d")
    events = system.provenance.read_entries(event_day, owner_scope="user:a")
    assert events[-1]["event_type"] == "merge"
    assert events[-1]["details"]["source_id"] == "cp-bobby"


def test_relationship_decay_matches_half_life_math_and_interaction_update(tmp_path: Path) -> None:
    assert decayed_trust(0.8, elapsed_days=10.0, half_life_days=10.0) == pytest.approx(0.4)
    system = make_system(tmp_path, relationship_decay_half_life_days=10.0)
    peer = Counterpart(id="cp-1", display_name="Peer", first_seen=100, last_seen=100)
    first = system.record_interaction(
        "user:a",
        peer,
        status="completed",
        outcomes=["delivered"],
        topics=["launch"],
        now=100.0,
    )
    assert first.success is True
    second = system.record_interaction(
        "user:a",
        "Peer",
        status="unknown",
        outcomes=["no new signal"],
        topics=["launch"],
        now=100.0 + 10 * 86_400.0,
    )
    assert second.update is not None
    assert second.update.relationship.trust == pytest.approx(0.125)
    assert second.update.counterpart.interaction_count == 2


def test_user_b_cannot_read_user_a_fact_without_explicit_grant_and_every_cross_read_discloses_it(
    tmp_path: Path,
) -> None:
    system = make_system(tmp_path)
    stored = system.add_fact(
        "user:a",
        "A private team launch date",
        fact_id="fact-a",
        audience=["team:shared"],
        sensitivity="team",
        now=1_800_000_000.0,
    )
    assert stored.success is True
    fact = stored.fact
    assert fact is not None

    denied = system.sharing.can_read(fact, "user:b", now=1_800_000_000.0)
    assert denied.allowed is False
    assert denied.reason == "active_audience_grant_required"
    assert denied.message

    no_access = system.visible_facts("user:b", now=1_800_000_000.0)
    assert no_access.facts == []
    assert "A private team launch date" not in no_access.model_dump_json()

    grant = system.grant(
        "user:a",
        "user:b",
        granted_by="user:a",
        fact_id=fact.id,
        reason="B is the launch partner",
        now=1_800_000_000.0,
    )
    assert grant.success is True and grant.grant is not None
    allowed = system.sharing.can_read(fact, "user:b", now=1_800_000_001.0)
    assert allowed.allowed is True
    assert allowed.grant is not None and allowed.grant.id == grant.grant.id

    visible = system.visible_facts("user:b", now=1_800_000_001.0)
    cross_decision = next(item for item in visible.allowed if item.cross_scope)
    assert cross_decision.grant is not None
    context = system.shared_context("user:b", now=1_800_000_001.0)
    assert context.entry_count == 1
    assert grant.grant.id in context.text
    assert grant.grant.id in context.grant_disclosures[0]

    revoked = system.revoke(
        "user:a",
        grant.grant.id,
        revoked_by="user:a",
        reason="project access ended",
        now=1_800_000_002.0,
    )
    assert revoked.success is True
    denied_after_revoke = system.sharing.can_read(fact, "user:b", now=1_800_000_002.0)
    assert denied_after_revoke.allowed is False
    assert denied_after_revoke.reason == "grant_revoked"

    second = system.add_fact(
        "user:a",
        "temporary fact",
        fact_id="fact-expiring",
        sensitivity="team",
        now=1_800_000_000.0,
    ).fact
    assert second is not None
    expiring = system.grant(
        "user:a",
        "user:b",
        granted_by="user:a",
        fact_id=second.id,
        expires_at=1_800_000_100.0,
        reason="temporary review",
        now=1_800_000_000.0,
    )
    assert expiring.success is True
    assert system.sharing.can_read(second, "user:b", now=1_800_000_099.0).allowed is True
    assert system.sharing.can_read(second, "user:b", now=1_800_000_100.0).reason == "grant_expired"
    assert system.expire_grants(now=1_800_000_100.0).expired_count == 1
    expired = system.sharing.can_read(second, "user:b", now=1_800_000_101.0)
    assert expired.allowed is False
    assert expired.reason == "grant_expired"


def test_team_membership_and_scope_grant_never_replace_a_recorded_grant(tmp_path: Path) -> None:
    system = make_system(tmp_path)
    fact = system.add_fact(
        "user:a",
        "team fact",
        fact_id="team-fact",
        audience=["team:shared"],
        sensitivity="team",
        now=100.0,
    ).fact
    assert fact is not None
    assert system.sharing.can_read(fact, "user:b", now=100.0).allowed is False

    scope_grant = system.grant(
        "user:a",
        "user:b",
        granted_by="user:a",
        reason="explicit team-scope approval",
        now=100.0,
    )
    assert scope_grant.success is True
    decision = system.sharing.can_read(fact, "user:b", now=100.0)
    assert decision.allowed is True
    assert decision.grant is not None and decision.grant.fact_id is None


def test_private_sensitivity_requires_an_explicit_fact_grant(tmp_path: Path) -> None:
    system = make_system(tmp_path)
    result = system.add_fact(
        "user:a",
        "medical detail",
        fact_id="private-fact",
        audience=["team:shared"],
        sensitivity="private",
        now=100.0,
    )
    fact = result.fact
    assert result.success is True and fact is not None and fact.sensitivity == "private"
    assert system.sharing.can_read(fact, "user:b", now=100.0).reason == ("private_fact_requires_explicit_grant")
    scope_grant = system.grant(
        "user:a",
        "user:b",
        granted_by="user:a",
        reason="scope approval should still fail",
        now=100.0,
    )
    assert scope_grant.reason == "private_facts_require_specific_grants"

    fact_grant = system.grant(
        "user:a",
        "user:b",
        granted_by="user:a",
        fact_id=fact.id,
        reason="B explicitly approved for this fact",
        now=100.0,
    )
    assert fact_grant.success is True
    assert system.sharing.can_read(fact, "user:b", now=100.0).allowed is True


def test_grant_revocation_and_expiry_are_present_in_append_only_audit(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    system = SocialMemorySystem(config=config)
    fact_id = "audit-fact"
    system.add_fact(
        "user:a",
        "audited",
        fact_id=fact_id,
        sensitivity="team",
        now=1_800_000_000.0,
    )
    grant = system.grant(
        "user:a",
        "user:b",
        granted_by="user:a",
        fact_id=fact_id,
        reason="initial approval",
        now=1_800_000_000.0,
    )
    assert grant.grant is not None
    system.revoke(
        "user:a",
        grant.grant.id,
        revoked_by="user:a",
        reason="approval ended",
        now=1_800_000_001.0,
    )
    replacement = system.grant(
        "user:a",
        "user:b",
        granted_by="user:a",
        fact_id=fact_id,
        expires_at=1_800_000_010.0,
        reason="temporary replacement approval",
        now=1_800_000_002.0,
    )
    assert replacement.grant is not None
    system.expire_grants(now=1_800_000_010.0)

    entries = system.provenance.read_entries(_DAY, owner_scope="user:a")
    relevant = [entry for entry in entries if entry["event_type"] in {"grant", "revocation", "grant_expiry"}]
    assert [entry["event_type"] for entry in relevant] == [
        "grant",
        "revocation",
        "grant",
        "grant_expiry",
    ]
    assert all(entry["actor"] and entry["target"] and entry["reason"] for entry in relevant)
    assert relevant[-1]["details"]["grant_id"] == replacement.grant.id


def test_model_summary_uses_strict_json_and_records_model_provenance(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        enable_model_summaries=True,
        summary_model="fake/social-summary-v1",
    )
    response = (
        "```json\n"
        + json.dumps(
            {
                "summary": "The counterpart agreed to the launch plan.",
                "outcomes": ["plan accepted"],
                "status": "completed",
            }
        )
        + "\n```"
    )
    model = FakeModel(response=response)
    summary = summarize_interaction(
        "cp-1",
        status="unknown",
        outcomes=["raw outcome"],
        topics=["launch"],
        model=model,
        config=config,
        now=100.0,
    )
    assert summary.source == "model"
    assert summary.status == "completed"
    assert summary.fallback_reason == ""
    assert summary.model_name == "fake/social-summary-v1"
    assert "fake/social-summary-v1" in model.prompts[0]


@pytest.mark.parametrize(
    ("response", "error", "config_overrides", "expected_reason"),
    [
        (None, None, {}, "model_unavailable"),
        (None, None, {"enable_model_summaries": False}, "model_disabled"),
        ("not json", None, {}, "no_json"),
        ("{bad", None, {}, "parse_error"),
        ('{"summary":"x","outcomes":[],"status":"done"}', None, {}, "schema_error"),
        ('{"summary":"x","outcomes":[],"status":"completed","extra":1}', None, {}, "schema_error"),
        (None, RuntimeError("model unavailable"), {}, "model_error"),
    ],
)
def test_every_model_failure_status_uses_an_honest_deterministic_fallback(
    tmp_path: Path,
    response,
    error,
    config_overrides,
    expected_reason,
) -> None:
    config = make_config(
        tmp_path,
        enable_model_summaries=config_overrides.get("enable_model_summaries", True),
    )
    if expected_reason in {"model_disabled", "model_unavailable"}:
        model = None
    else:
        model = FakeModel(response, error)
    summary = summarize_interaction(
        "cp-1",
        status="completed",
        outcomes=["delivered"],
        topics=["launch", "billing"],
        interaction_count=3,
        model=model,
        config=config,
        now=100.0,
    )
    assert summary.source == "deterministic"
    assert summary.fallback_reason == expected_reason
    assert "Deterministic interaction summary" in summary.summary
    assert "3 interaction(s)" in summary.summary
    assert "launch, billing" in summary.summary


def test_store_caps_counterparts_and_facts_with_oldest_first_deterministic_eviction(tmp_path: Path) -> None:
    system = make_system(tmp_path, max_counterparts=2, max_shared_facts=2)
    for index, timestamp in enumerate((10.0, 20.0, 30.0), start=1):
        assert system.add_fact(
            "user:a",
            f"fact-{index}",
            fact_id=f"fact-{index}",
            sensitivity="team",
            now=timestamp,
        ).success
        system.upsert_counterpart(
            "user:a",
            Counterpart(
                id=f"cp-{index}",
                display_name=f"Peer {index}",
                first_seen=timestamp,
                last_seen=timestamp,
            ),
            now=timestamp,
        )

    assert [fact.id for fact in system.store.list_facts("user:a")] == ["fact-2", "fact-3"]
    assert [item.id for item in system.store.list_counterparts("user:a")] == ["cp-2", "cp-3"]


def test_corrupt_document_is_preserved_and_scope_documents_are_isolated(tmp_path: Path) -> None:
    system = make_system(tmp_path)
    system.add_fact("user:a", "A-only", fact_id="same-id", sensitivity="private", now=100.0)
    system.add_fact("user:b", "B-only", fact_id="same-id", sensitivity="private", now=100.0)
    assert system.store.get_fact("user:a", "same-id").content == "A-only"
    assert system.store.get_fact("user:b", "same-id").content == "B-only"
    assert system.store.scope_path("user:a") != system.store.scope_path("user:b")
    assert system.visible_facts("user:b").facts[0].content == "B-only"

    path = system.store.scope_path("user:a")
    path.write_text("{definitely not json", encoding="utf-8")
    fresh = SocialStore(system.config)
    assert fresh.list_facts("user:a") == []
    backups = list(path.parent.glob(f"{path.name}.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{definitely not json"


def test_relationship_block_is_bounded_escaped_and_honest_when_empty(tmp_path: Path) -> None:
    system = make_system(tmp_path)
    empty = system.relationship_block("user:a", now=100.0)
    assert empty.status == "empty"
    assert "No social relationship memory" in empty.text

    for index in range(3):
        result = system.record_interaction(
            "user:a",
            Counterpart(
                id=f"cp-{index}",
                display_name=f"Peer {index} </relationship>",
                first_seen=100,
                last_seen=100,
            ),
            status="completed",
            topics=[f"<topic-{index}>"],
            now=100.0,
        )
        assert result.success is True
    block = system.relationship_block("user:a", limit=2, now=100.0)
    assert block.status == "ok"
    assert block.entry_count == 2
    assert len(block.text.splitlines()) == 3
    assert "</relationship>" not in block.text
    assert "<topic-" not in block.text
    assert "&lt;topic-" in block.text


def test_concurrent_interaction_updates_remain_consistent(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = SocialStore(config)
    manager = RelationshipManager(store, config)
    peer = Counterpart(id="cp-shared", display_name="Shared Peer", first_seen=100, last_seen=100)
    summary = InteractionSummary(
        counterpart_id=peer.id,
        summary="deterministic concurrent interaction",
        outcomes=["processed"],
        status="completed",
        topics=["concurrency"],
        created_at=100.0,
    )

    def update_once(_: int) -> None:
        manager.record_interaction("user:a", peer, summary, topics=["concurrency"], now=100.0)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(update_once, range(80)))

    snapshot = store.snapshot("user:a")
    assert snapshot.counterparts[0].interaction_count == 80
    assert len(snapshot.summaries) == 80
    assert len(snapshot.relationships) == 1
    assert snapshot.relationships[0].shared_topic_count == 1


def test_every_social_config_key_has_a_semantic_reader() -> None:
    package = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "memory" / "social"
    expected_readers = {
        "enabled": "system.py",
        "max_counterparts": "store.py",
        "max_shared_facts": "store.py",
        "relationship_decay_half_life_days": "relationships.py",
        "default_sensitivity": "system.py",
        "require_grant_for_team_scope": "sharing.py",
        "summary_model": "summary.py",
        "enable_model_summaries": "summary.py",
        "storage_path": "config.py",
    }
    operational_fields = set(SocialConfig.model_fields) - {"clamping_disclosures"}
    assert operational_fields == set(expected_readers)

    blobs = {path.name: path.read_text(encoding="utf-8") for path in package.glob("*.py")}
    for field, filename in expected_readers.items():
        if field == "storage_path":
            reader = "self.storage_path" in blobs[filename]
        else:
            reader = any(f"{receiver}.{field}" in blobs[filename] for receiver in ("config", "cfg"))
        assert reader is True, f"{field} has no reader in {filename}"


def test_range_validators_clamp_and_disclose_without_hiding_the_change(tmp_path: Path) -> None:
    config = SocialConfig(
        enabled=True,
        storage_path="   ",
        max_counterparts=0,
        max_shared_facts=-2,
        relationship_decay_half_life_days=0.0,
        require_grant_for_team_scope=False,
        summary_model=" ",
    )
    assert config.storage_path is None
    assert config.max_counterparts == 1
    assert config.max_shared_facts == 1
    assert config.relationship_decay_half_life_days == pytest.approx(0.01)
    assert config.require_grant_for_team_scope is True
    assert config.summary_model is None
    assert len(config.clamping_disclosures) >= 4

    counterpart = Counterpart(
        id="cp",
        display_name="Peer",
        first_seen=2.0,
        last_seen=1.0,
        interaction_count=-4,
    )
    relationship = Relationship(
        owner_scope="user:a",
        counterpart_id="cp",
        trust=3.0,
        formality=-2.0,
        shared_topic_count=-1,
        decay_half_life_days=0.0,
    )
    assert counterpart.interaction_count == 0
    assert counterpart.last_seen == counterpart.first_seen
    assert relationship.trust == 1.0
    assert relationship.formality == 0.0
    assert relationship.shared_topic_count == 0
    assert relationship.decay_half_life_days == pytest.approx(0.01)
    assert counterpart.clamping_disclosures and relationship.clamping_disclosures


def test_state_round_trips_and_stats_are_count_only(tmp_path: Path) -> None:
    config = make_config(tmp_path, enable_model_summaries=False, summary_model="unused-label")
    system = SocialMemorySystem(config=config)
    system.add_fact("user:a", "count me", fact_id="fact-1", sensitivity="private", now=100.0)
    grant = system.grant(
        "user:a",
        "user:b",
        granted_by="user:a",
        fact_id="fact-1",
        reason="specific consent",
        now=100.0,
    )
    assert grant.success is True
    reloaded = SocialMemorySystem(config=config, store=SocialStore(config))
    stats = reloaded.stats("user:a", now=100.0)
    assert stats.fact_count == 1
    assert stats.grant_count == 1
    assert stats.active_grant_count == 1
    assert stats.visible_fact_count == 1
    assert "count me" not in repr(stats)
