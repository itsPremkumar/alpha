"""D1 clone-mode proofs: all three CloneMode branches + TTL lease expiry.

Covers, against the REAL ``alpha.bots.cloning`` / ``alpha.bots.ephemeral`` code
(no mocks of the engines themselves):

- EXACT_COPY: full surface copy with an ISOLATED memory namespace
  (``memory_scope = new_name``) and non-shared metadata; no directive injected,
  no generation bump, honest ``lease_status`` for ``ttl_seconds=0``.
- SPECIALIST_FORK: SOUL mission-directive injection, ordered skill/tool
  merging, and a REAL ephemeral lease (payload + on-disk UTF-8 store).
- ENHANCED_MUTATION: generation (version) bump, lineage chain across a
  fork-then-mutate sequence, model-tier override; source profiles unchanged.
- TTL expiry: an active clone lease whose ``expires_at`` passes is archived by
  ``check_leases()`` in the registry AND persisted as ``expired`` on disk
  (deterministic -- the lease clock is moved, no sleeps).

Registry/lease state is isolated per test via ``AGENT_WORKSPACE_HOME`` ->
tmp_path plus singleton resets (same pattern as ``test_bot_cloning_lease``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

import alpha.bots.cloning as cloning_mod
from alpha.bots.cloning import BotCloneEngine, CloneMode
from alpha.bots.ephemeral import EphemeralBotManager
from alpha.bots.profile import BotProfile
from alpha.bots.registry import BotRegistry


@pytest.fixture(autouse=True)
def isolated_bot_state(tmp_path, monkeypatch):
    """Point every bot/ephemeral singleton at a per-test temp home."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))

    import alpha.bots.ephemeral as ephemeral_mod
    import alpha.bots.registry as registry_mod

    monkeypatch.setattr(registry_mod, "_global_registry", None)
    monkeypatch.setattr(registry_mod, "_global_registry_path", None)
    monkeypatch.setattr(ephemeral_mod, "_ephemeral_manager", None)
    monkeypatch.setattr(cloning_mod, "_GLOBAL_CLONE_ENGINE", None)
    yield tmp_path


def _source_profile() -> BotProfile:
    return BotProfile(
        name="lead_dev",
        display_name="Lead Developer",
        role="Senior Engineer",
        soul="You write robust Python code.",
        model="model-base",
        toolsets=["code_editor"],
        skills=["python"],
        capabilities=["review"],
        department="engineering",
        metadata={"scratchpad_note": "source-only-state"},
    )


def _engine(tmp_path, registry: BotRegistry | None = None) -> BotCloneEngine:
    return BotCloneEngine(
        registry=registry or BotRegistry(storage_path=None),
        ephemeral_mgr=EphemeralBotManager(storage_path=tmp_path / "leases.json"),
    )


# ---------------------------------------------------------------------------
# EXACT_COPY -- isolated memory namespace, full surface copy
# ---------------------------------------------------------------------------


def test_exact_copy_isolates_memory_and_copies_surface(tmp_path):
    registry = BotRegistry(storage_path=None)
    registry.register(_source_profile())
    engine = _engine(tmp_path, registry)

    clone = engine.clone_bot(
        source_name="lead_dev",
        target_name="dev_copy_1",
        mode=CloneMode.EXACT_COPY,
        ttl_seconds=0,
    )

    # Surface copied exactly (distinct containers, equal contents).
    assert clone.toolsets == ["code_editor"]
    assert clone.toolsets is not registry.get_bot("lead_dev").toolsets
    assert clone.skills == ["python"]
    assert clone.model == "model-base"
    assert clone.soul == "You write robust Python code."
    assert "SPECIALIST MISSION DIRECTIVE" not in clone.soul  # EXACT_COPY injects nothing

    # Memory isolation: the clone owns a namespace equal to ITS name and
    # disjoint from the source's namespace.
    assert clone.memory_scope == "dev_copy_1"
    assert clone.memory_namespace() == "dev_copy_1"
    assert registry.get_bot("lead_dev").memory_namespace() == "lead_dev"
    assert clone.memory_namespace() != registry.get_bot("lead_dev").memory_namespace()

    # Metadata is inherited but NOT shared: mutating the clone cannot touch the source.
    assert clone.metadata["scratchpad_note"] == "source-only-state"
    clone.metadata["scratchpad_note"] = "clone-only-state"
    assert registry.get_bot("lead_dev").metadata["scratchpad_note"] == "source-only-state"
    assert registry.get_bot("lead_dev").skills == ["python"]  # source surface untouched

    # Lineage + mode + honest zero-TTL lease disclosure; no generation bump.
    assert clone.metadata["clone_mode"] == "exact_copy"
    assert clone.metadata["cloned_from"] == "lead_dev"
    assert clone.metadata["lineage"] == ["lead_dev"]
    assert clone.version == registry.get_bot("lead_dev").version
    assert clone.metadata["lease_status"].startswith("none:")
    assert "ttl_seconds=0" in clone.metadata["lease_status"]


# ---------------------------------------------------------------------------
# SPECIALIST_FORK -- SOUL directive + skill injection + real TTL lease
# ---------------------------------------------------------------------------


def test_specialist_fork_injects_directive_skills_and_real_lease(tmp_path):
    registry = BotRegistry(storage_path=None)
    registry.register(_source_profile())
    engine = _engine(tmp_path, registry)

    directive = "Focus strictly on Kubernetes Helm chart deployments."
    fork = engine.clone_bot(
        source_name="lead_dev",
        target_name="k8s_specialist",
        mode=CloneMode.SPECIALIST_FORK,
        specialist_directive=directive,
        skills_to_add=["k8s", "helm"],
        tools_to_add=["k8s_tool"],
        ttl_seconds=1800,
    )

    # SOUL: inherited body first, mission directive appended.
    assert fork.soul.startswith("You write robust Python code.")
    assert "SPECIALIST MISSION DIRECTIVE (k8s_specialist)" in fork.soul
    assert directive in fork.soul

    # Ordered, de-duplicated skill/tool injection.
    assert fork.skills == ["python", "k8s", "helm"]
    assert fork.toolsets == ["code_editor", "k8s_tool"]
    assert "k8s" in fork.capabilities
    assert fork.metadata["clone_mode"] == "specialist_fork"
    assert fork.metadata["lineage"] == ["lead_dev"]

    # Honest active lease payload...
    status = fork.metadata["lease_status"]
    assert status.startswith("active:")
    assert "ttl_seconds=1800" in status

    # ...backed by a REAL lease record in the manager...
    lease = engine.ephemeral_mgr.get_lease("k8s_specialist")
    assert lease is not None
    assert lease.status == "active"
    assert lease.ttl_seconds == 1800
    assert lease.remaining_seconds > 0
    assert lease.prompt_objective == directive

    # ...and persisted in the on-disk UTF-8 JSON store.
    payload = json.loads((tmp_path / "leases.json").read_text(encoding="utf-8"))
    stored_names = [entry["bot_name"] for entry in payload["leases"]]
    assert "k8s_specialist" in stored_names


# ---------------------------------------------------------------------------
# ENHANCED_MUTATION -- generation bump, lineage chain, model override
# ---------------------------------------------------------------------------


def test_enhanced_mutation_bumps_generation_and_extends_lineage(tmp_path):
    registry = BotRegistry(storage_path=None)
    registry.register(_source_profile())
    engine = _engine(tmp_path, registry)

    # Step 1: fork the base bot, then mutate the FORK so lineage chains.
    fork = engine.clone_bot(
        source_name="lead_dev",
        target_name="mut_base",
        mode=CloneMode.SPECIALIST_FORK,
        specialist_directive="Baseline directive.",
        ttl_seconds=0,
    )
    assert fork.metadata["lineage"] == ["lead_dev"]

    mutated = engine.clone_bot(
        source_name="mut_base",
        target_name="mut_v2",
        mode=CloneMode.ENHANCED_MUTATION,
        skills_to_add=["citation_scoring"],
        model_override="model-frontier",
        ttl_seconds=0,
    )

    # Generation advances ONLY for ENHANCED_MUTATION.
    assert mutated.version == fork.version + 1
    assert registry.get_bot("mut_base").version == fork.version  # source generation unchanged

    # Lineage chains across the fork -> mutate sequence.
    assert mutated.metadata["clone_mode"] == "enhanced_mutation"
    assert mutated.metadata["cloned_from"] == "mut_base"
    assert mutated.metadata["lineage"] == ["lead_dev", "mut_base"]

    # Model-tier override lands; new capability present in both merged lists.
    assert mutated.model == "model-frontier"
    assert "citation_scoring" in mutated.skills
    assert "citation_scoring" in mutated.capabilities

    # EXACT_COPY of the same source does NOT bump the generation.
    exact = engine.clone_bot(
        source_name="mut_base",
        target_name="mut_copy",
        mode=CloneMode.EXACT_COPY,
        ttl_seconds=0,
    )
    assert exact.version == fork.version
    assert exact.metadata["clone_mode"] == "exact_copy"


# ---------------------------------------------------------------------------
# TTL expiry -- expired lease archives the clone in registry AND on disk
# ---------------------------------------------------------------------------


def test_clone_lease_ttl_expiry_archives_clone(isolated_bot_state):
    tmp_path = isolated_bot_state
    # Default engine: its registry IS the global registry that check_leases()
    # resolves, so the expiry path and the assertions observe the same roster.
    engine = BotCloneEngine()
    engine.registry.register(_source_profile())

    clone = engine.clone_bot(
        source_name="lead_dev",
        target_name="ttl_clone",
        mode=CloneMode.SPECIALIST_FORK,
        specialist_directive="Short-lived probe directive.",
        ttl_seconds=900,
    )
    assert clone.metadata["lease_status"].startswith("active:")

    lease = engine.ephemeral_mgr.get_lease("ttl_clone")
    assert lease is not None and lease.status == "active"
    assert lease.remaining_seconds > 0

    # Deterministic expiry: move the lease clock into the past (no sleeps).
    lease.expires_at = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    expired = engine.ephemeral_mgr.check_leases()
    assert "ttl_clone" in expired

    # Lease record: expired + real archive reason.
    after = engine.ephemeral_mgr.get_lease("ttl_clone")
    assert after is not None
    assert after.status == "expired"
    assert after.archive_reason == "ttl_expired"

    # Registry: the clone profile is archived (real side effect of check_leases).
    archived = engine.registry.get_bot("ttl_clone")
    assert archived is not None
    assert archived.status == "archived"

    # A FRESH manager reading the same on-disk store agrees.
    fresh = EphemeralBotManager(storage_path=tmp_path / "bots" / "ephemeral.json")
    persisted = fresh.get_lease("ttl_clone")
    assert persisted is not None
    assert persisted.status == "expired"

    # Expired leases are no longer listed as active.
    assert not any(entry["bot_name"] == "ttl_clone" for entry in engine.ephemeral_mgr.list_active_specialists())
