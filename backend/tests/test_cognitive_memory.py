"""Comprehensive Test Suite for Multi-Tier Cognitive Memory Architecture.

Verifies:
1. Working Memory (ephemeral scratchpad, attention decay, salient extraction)
2. Episodic Memory (flat traces, hierarchical abstracted episodes)
3. Semantic Fact Graph (epistemic beliefs, contradiction detection & reconciliation, traversal)
4. Procedural Skill Memory (trigger matching, execution feedback, success rates)
5. Spatio-Temporal Memory (interval overlap, time-travel queries)
6. Associative Memory (Hebbian links, spreading activation)
7. 3-Phase Sleep/Dream Consolidation (Light Sleep, REM, Deep Sleep, Ebbinghaus decay)
8. Context-Aware Hybrid Retrieval (BM25 + Vector + Graph + Temporal)
9. FastAPI Gateway API endpoints integration
"""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import memory
from agent_workspace.memory.cognitive.associative_memory import AssociativeNetwork
from agent_workspace.memory.cognitive.engine import CognitiveMemorySystem
from agent_workspace.memory.cognitive.episodic_memory import EpisodicMemoryEngine
from agent_workspace.memory.cognitive.models import (
    BeliefStatus,
    CognitiveTier,
    HybridRecallQuery,
    TraceOutcome,
)
from agent_workspace.memory.cognitive.procedural_memory import ProceduralSkillMemory
from agent_workspace.memory.cognitive.semantic_graph import SemanticBeliefGraph
from agent_workspace.memory.cognitive.spatio_temporal import SpatioTemporalMemory
from agent_workspace.memory.cognitive.working_memory import WorkingMemoryEngine


@pytest.fixture(autouse=True)
def isolated_cognitive_storage(tmp_path, monkeypatch):
    from agent_workspace.config.paths import Paths
    from agent_workspace.memory.cognitive import engine

    monkeypatch.setattr(engine, "get_paths", lambda: Paths(tmp_path))
    monkeypatch.setattr(engine, "_owner_systems", {})


def test_working_memory_lifecycle():
    wm = WorkingMemoryEngine(max_items=3, decay_half_life_seconds=10.0)

    # 1. Add items
    it1 = wm.add("Implement OAuth2 flow", context_tag="goal", attention_score=1.0, salience=0.8)
    wm.add("Token expiration might cause 401", context_tag="hypothesis", attention_score=0.9, salience=0.9)
    wm.add("Temporary trace id abc", context_tag="scratch", attention_score=0.5, salience=0.2)

    assert len(wm.list_active(min_attention=0.0)) == 3
    assert wm.get(it1.item_id) is not None

    # Capacity overflow
    wm.add("New attention focus", context_tag="focus", attention_score=1.0, salience=0.7)
    assert len(wm.list_active(min_attention=0.0)) <= 3

    # Salience extraction
    salient = wm.get_salient_for_promotion(min_salience=0.75)
    assert any(s.context_tag == "hypothesis" for s in salient)

    # Decay
    pruned = wm.decay_all(current_time=time.time() + 100.0)
    assert pruned >= 0

    # Clear
    wm.clear()
    assert len(wm.list_active(min_attention=0.0)) == 0


def test_episodic_memory_engine():
    ep = EpisodicMemoryEngine(max_flat_traces=10, max_episodes=5)

    # Record traces
    t1 = ep.record_trace(
        action="Run database migration",
        observation="Table users created successfully",
        outcome=TraceOutcome.SUCCESS,
        session_id="sess_1",
    )
    t2 = ep.record_trace(
        action="Seed mock users",
        observation="Unique constraint violation on email field",
        outcome=TraceOutcome.FAILURE,
        error_context="IntegrityError: duplicate key value",
        session_id="sess_1",
    )

    assert t1.outcome == TraceOutcome.SUCCESS
    assert t2.outcome == TraceOutcome.FAILURE
    assert t2.salience >= 0.7  # auto-boost on failure

    traces = ep.list_traces(session_id="sess_1")
    assert len(traces) == 2

    # Synthesize hierarchical episode
    episode = ep.create_hierarchical_episode(
        title="Database Migration & Seeding",
        summary="Migrated users schema and encountered unique email conflict in seed fixture.",
        session_id="sess_1",
        trace_ids=[t1.trace_id, t2.trace_id],
        key_learnings=["Ensure mock fixtures clear prior test data before seeding"],
        importance=0.85,
    )

    assert episode.episode_id.startswith("ep_")
    assert len(episode.traces) == 2
    assert t1.parent_episode_id == episode.episode_id
    assert t2.parent_episode_id == episode.episode_id

    episodes = ep.list_episodes(session_id="sess_1")
    assert len(episodes) == 1
    assert episodes[0].title == "Database Migration & Seeding"


def test_semantic_belief_graph_conflicts_and_traversal():
    graph = SemanticBeliefGraph(max_nodes=100)

    # 1. Add beliefs
    b1 = graph.add_belief("UserPreferences", "preferred_language", "Python", confidence=0.8)
    b2 = graph.add_belief("Python", "runs_on", "FastAPI", confidence=0.9)
    b3 = graph.add_belief("FastAPI", "uses_orm", "SQLAlchemy", confidence=0.85)

    # Edges
    e1 = graph.add_edge(b1.node_id, b2.node_id, relation="utilizes")
    e2 = graph.add_edge(b2.node_id, b3.node_id, relation="connects_to")
    assert e1 is not None and e2 is not None

    # Multi-hop Traversal
    hops = graph.traverse(b1.node_id, max_depth=2)
    assert len(hops) == 2
    hop_node_ids = [h[0].node_id for h in hops]
    assert b2.node_id in hop_node_ids
    assert b3.node_id in hop_node_ids

    # 2. Add conflicting belief
    time.sleep(0.01)  # Ensure distinct timestamp
    b_conflict = graph.add_belief("UserPreferences", "preferred_language", "TypeScript", confidence=0.95)

    conflicts = graph.detect_conflicts()
    assert len(conflicts) >= 1

    # Reconcile conflicts
    reconciled = graph.reconcile_conflicts()
    assert reconciled >= 1
    # Older b1 should be superseded by higher confidence/newer b_conflict
    assert b1.status == BeliefStatus.SUPERSEDED
    assert b1.superseded_by == b_conflict.node_id
    assert b_conflict.status == BeliefStatus.ACTIVE

    # Density metrics
    metrics = graph.density_metrics()
    assert metrics["active_beliefs"] >= 3
    assert metrics["superseded_beliefs"] >= 1


def test_procedural_skill_memory():
    pm = ProceduralSkillMemory()

    skill = pm.register_skill(
        name="pytest_targeted_runner",
        description="Run isolated pytest unit test on changed file",
        trigger_pattern=r"(pytest|unit test|run test)",
        steps=["uv run pytest tests/target.py", "check output"],
    )

    assert skill.success_count == 0
    pm.record_outcome(skill.skill_id, success=True)
    pm.record_outcome(skill.skill_id, success=True)
    pm.record_outcome(skill.skill_id, success=False, reason="ModuleNotFoundError")

    assert skill.success_count == 2
    assert skill.failure_count == 1
    assert skill.success_rate == 2 / 3
    assert "ModuleNotFoundError" in skill.failure_reasons

    # Match query
    matches = pm.find_matching_skills("I need to run test on memory")
    assert len(matches) > 0
    assert matches[0][0].name == "pytest_targeted_runner"


def test_spatio_temporal_memory():
    st = SpatioTemporalMemory()

    t_base = 1000.0
    st.record_event(
        title="PostgreSQL 14 Deployment",
        description="Database upgraded to PG 14",
        timestamp=t_base,
        valid_from=t_base,
        valid_to=t_base + 500.0,
        environment="production",
    )
    st.record_event(
        title="PostgreSQL 16 Migration",
        description="Database migrated to PG 16",
        timestamp=t_base + 500.0,
        valid_from=t_base + 500.0,
        valid_to=None,
        environment="production",
    )

    # Time-travel query at t = 1200 (PG 14 era)
    evts_early = st.query_at_time(t_base + 200.0)
    assert len(evts_early) == 1
    assert evts_early[0].title == "PostgreSQL 14 Deployment"

    # Time-travel query at t = 1600 (PG 16 era)
    evts_late = st.query_at_time(t_base + 600.0)
    assert len(evts_late) == 1
    assert evts_late[0].title == "PostgreSQL 16 Migration"


def test_associative_network_hebbian_and_spreading():
    net = AssociativeNetwork()

    # Link across tiers
    net.link_memories(CognitiveTier.WORKING, "wm_1", CognitiveTier.EPISODIC_FLAT, "tr_1", initial_weight=0.5)
    # Re-reinforce
    l1_re = net.link_memories(CognitiveTier.WORKING, "wm_1", CognitiveTier.EPISODIC_FLAT, "tr_1")
    assert l1_re.co_occurrences == 2
    assert l1_re.weight > 0.5

    # Second link
    net.link_memories(CognitiveTier.EPISODIC_FLAT, "tr_1", CognitiveTier.SEMANTIC_FACT, "fact_1", initial_weight=0.7)

    # Spreading activation from wm_1
    energy_map = net.spread_activation(
        activated_nodes=[(CognitiveTier.WORKING, "wm_1", 1.0)],
        decay_per_step=0.5,
        max_hops=2,
    )

    assert (CognitiveTier.EPISODIC_FLAT.value, "tr_1") in energy_map
    assert (CognitiveTier.SEMANTIC_FACT.value, "fact_1") in energy_map
    assert energy_map[(CognitiveTier.EPISODIC_FLAT.value, "tr_1")] > energy_map[(CognitiveTier.SEMANTIC_FACT.value, "fact_1")]


def test_sleep_dream_consolidation(tmp_path: Path):
    sys = CognitiveMemorySystem(storage_dir=tmp_path)

    # Populate raw working memory and episodic traces
    sys.working_mem.add("User strongly prefers dark mode in war room UI", salience=0.85)
    sys.working_mem.add("Redundant noise item that should decay", attention_score=0.01, salience=0.1)

    sys.episodic_mem.record_trace(
        action="deploy_docker_container",
        observation="Docker daemon socket permission denied",
        outcome=TraceOutcome.FAILURE,
        error_context="PermissionError: /var/run/docker.sock",
    )

    report = sys.consolidate()
    assert report.cycle_id.startswith("dream_")
    assert report.light_sleep_pruned >= 0
    assert report.rem_sleep_patterns_discovered >= 1
    assert report.deep_sleep_beliefs_crystallized >= 1
    assert len(report.summary) > 0

    # Verify state saved to disk
    assert (tmp_path / "cognitive_state.json").exists()


def test_context_aware_hybrid_retrieval(tmp_path: Path):
    sys = CognitiveMemorySystem(storage_dir=tmp_path)

    # Add specific test facts and procedures
    sys.semantic_graph.add_belief(
        subject="Agent WorkspaceStreaming",
        predicate="uses_protocol",
        object_val="ServerSentEvents_SSE",
        confidence=0.95,
        salience=0.9,
    )

    # Perform hybrid recall
    query = HybridRecallQuery(
        query="ServerSentEvents streaming protocol",
        limit=5,
        bm25_weight=0.4,
        vector_weight=0.4,
        graph_weight=0.1,
        temporal_weight=0.1,
    )
    results = sys.recall(query)

    assert len(results) > 0
    top = results[0]
    assert "ServerSentEvents" in top.snippet or "Agent WorkspaceStreaming" in top.title
    assert top.composite_score > 0.1
    assert top.bm25_score >= 0.0
    assert top.vector_score > 0.0


def test_fastapi_cognitive_memory_gateway_routes(tmp_path: Path):
    app = FastAPI()
    app.include_router(memory.router)

    with TestClient(app) as client:
        # 1. Overview
        resp = client.get("/api/memory/cognitive/overview")
        assert resp.status_code == 200
        overview_data = resp.json()
        assert "tiers" in overview_data
        assert "semantic_graph" in overview_data["tiers"]
        assert "consolidation" in overview_data

        # 2. Add Working Memory
        resp_wm = client.post(
            "/api/memory/cognitive/working",
            json={"content": "Investigate memory latency", "context_tag": "goal", "salience": 0.8},
        )
        assert resp_wm.status_code == 200
        assert resp_wm.json()["content"] == "Investigate memory latency"

        # List Working Memory
        resp_wm_list = client.get("/api/memory/cognitive/working")
        assert resp_wm_list.status_code == 200
        assert any(it["content"] == "Investigate memory latency" for it in resp_wm_list.json())

        # 3. Add Episodic Trace
        resp_ep = client.post(
            "/api/memory/cognitive/episodic",
            json={
                "action": "run_benchmarks",
                "observation": "Latency p99 under 5ms",
                "outcome": "success",
            },
        )
        assert resp_ep.status_code == 200
        assert resp_ep.json()["action"] == "run_benchmarks"

        # 4. Add Semantic Belief
        resp_sem = client.post(
            "/api/memory/cognitive/semantic",
            json={
                "subject": "CacheSubsystem",
                "predicate": "implements",
                "object_val": "LRU_TwoTier",
                "confidence": 0.9,
            },
        )
        assert resp_sem.status_code == 200
        assert resp_sem.json()["subject"] == "CacheSubsystem"

        # 5. Hybrid Recall Route
        resp_recall = client.post(
            "/api/memory/cognitive/recall",
            json={"query": "CacheSubsystem LRU latency", "limit": 5},
        )
        assert resp_recall.status_code == 200
        recalled = resp_recall.json()
        assert len(recalled) > 0
        assert "composite_score" in recalled[0]

        # 6. Trigger Consolidation
        resp_cons = client.post("/api/memory/cognitive/consolidate")
        assert resp_cons.status_code == 200
        assert "cycle_id" in resp_cons.json()

        # 7. Trigger Reconciliation
        resp_rec = client.post("/api/memory/cognitive/reconcile")
        assert resp_rec.status_code == 200
        assert "reconciled_count" in resp_rec.json()

        # 8. DELETE Semantic Belief
        node_id = resp_sem.json()["node_id"]
        resp_del_sem = client.delete(f"/api/memory/cognitive/semantic/{node_id}")
        assert resp_del_sem.status_code == 200
        assert resp_del_sem.json()["status"] == "deleted"

        # 9. DELETE Episodic Trace
        trace_id = resp_ep.json()["trace_id"]
        resp_del_ep = client.delete(f"/api/memory/cognitive/episodic/{trace_id}")
        assert resp_del_ep.status_code == 200
        assert resp_del_ep.json()["status"] == "deleted"

        # 10. Register & DELETE Procedural Skill
        resp_sk = client.post(
            "/api/memory/cognitive/procedural",
            json={
                "name": "test_gateway_skill",
                "description": "test skill description",
                "trigger_pattern": "test_pattern",
            },
        )
        assert resp_sk.status_code == 200
        skill_id = resp_sk.json()["skill_id"]
        resp_del_sk = client.delete(f"/api/memory/cognitive/procedural/{skill_id}")
        assert resp_del_sk.status_code == 200
        assert resp_del_sk.json()["status"] == "deleted"


def test_working_memory_multiple_decay_steps_no_compounding():
    """Verify that multiple consecutive decay_all() calls do not compound decay exponentially."""
    wm = WorkingMemoryEngine(decay_half_life_seconds=100.0)
    t0 = 1000.0
    item = wm.add("Task focus", attention_score=1.0, created_at=t0, last_decayed_at=t0)

    # First decay after 10 seconds (dt = 10s)
    wm.decay_all(current_time=t0 + 10.0)
    score_after_step1 = wm._items[item.item_id].attention_score
    assert 0.90 <= score_after_step1 <= 0.95

    # Second decay immediately after (dt = 0s)
    wm.decay_all(current_time=t0 + 10.0)
    score_after_zero_dt = wm._items[item.item_id].attention_score
    # In the old buggy code, this would have multiplied by exp(-k * 10) AGAIN, dropping to ~0.87
    assert score_after_zero_dt == score_after_step1

    # Third decay after another 10s (dt = 10s from previous decay, total 20s from start)
    wm.decay_all(current_time=t0 + 20.0)
    score_after_step2 = wm._items[item.item_id].attention_score
    assert 0.85 <= score_after_step2 <= 0.90


def test_cognitive_memory_full_persistence_roundtrip(tmp_path: Path):
    """Verify that all cognitive memory tiers (including edges, traces, links, and skill stats) persist and reload without loss."""
    sys1 = CognitiveMemorySystem(storage_dir=tmp_path)

    # 1. Add Semantic Beliefs & Edge
    b1 = sys1.semantic_graph.add_belief("AuthService", "requires", "JWT_RS256", confidence=0.9)
    b2 = sys1.semantic_graph.add_belief("JWT_RS256", "uses_key_size", "4096_bits", confidence=0.95)
    edge = sys1.semantic_graph.add_edge(b1.node_id, b2.node_id, relation="secures", weight=0.9)
    assert edge is not None

    # 2. Add Episodic Trace & Hierarchical Episode
    trace = sys1.episodic_mem.record_trace(
        action="validate_jwt_signature",
        observation="Signature matched public key",
        outcome=TraceOutcome.SUCCESS,
        session_id="sess_auth",
    )
    episode = sys1.episodic_mem.create_hierarchical_episode(
        title="Authentication Verification",
        summary="Verified JWT signature against public key",
        session_id="sess_auth",
        trace_ids=[trace.trace_id],
    )

    # 3. Add Procedural Skill with stats
    skill = sys1.procedural_mem.register_skill(
        name="refresh_oauth_token",
        description="Refresh expired tokens",
        trigger_pattern=r"token_expired",
        steps=["request /oauth/token", "update header"],
        success_count=7,
        failure_count=1,
        failure_reasons=["401 Unauthorized"],
    )

    # 4. Add Spatio-Temporal Event
    evt = sys1.spatio_temporal.record_event(
        title="Key Rotation",
        description="Rotated RS256 key pair",
        environment="staging",
    )

    # 5. Add Associative Link
    sys1.assoc_net.link_memories(
        source_tier=CognitiveTier.SEMANTIC_FACT,
        source_id=b1.node_id,
        target_tier=CognitiveTier.PROCEDURAL_SKILL,
        target_id=skill.skill_id,
        initial_weight=0.85,
    )

    # Save to disk
    sys1.save_to_disk()

    # Create new system loading from same storage_dir
    sys2 = CognitiveMemorySystem(storage_dir=tmp_path)

    # Verify Semantic Beliefs & Edge were preserved
    loaded_b1 = sys2.semantic_graph.get_node(b1.node_id)
    assert loaded_b1 is not None
    assert loaded_b1.statement == b1.statement

    loaded_b2 = sys2.semantic_graph.get_node(b2.node_id)
    assert loaded_b2 is not None

    traversed = sys2.semantic_graph.traverse(b1.node_id, max_depth=1)
    assert len(traversed) == 1
    assert traversed[0][0].node_id == b2.node_id

    # Verify Episodic Trace & Episode
    loaded_trace = sys2.episodic_mem.get_trace(trace.trace_id)
    assert loaded_trace is not None
    assert loaded_trace.action == "validate_jwt_signature"

    loaded_ep = sys2.episodic_mem.get_episode(episode.episode_id)
    assert loaded_ep is not None
    assert loaded_ep.traces == [trace.trace_id]

    # Verify Procedural Skill and stats
    loaded_skill = sys2.procedural_mem.get_skill(skill.skill_id)
    assert loaded_skill is not None
    assert loaded_skill.name == "refresh_oauth_token"
    assert loaded_skill.success_count == 7
    assert loaded_skill.failure_count == 1
    assert "401 Unauthorized" in loaded_skill.failure_reasons

    # Verify Spatio-Temporal Event
    loaded_evt = sys2.spatio_temporal._events.get(evt.event_id)
    assert loaded_evt is not None
    assert loaded_evt.title == "Key Rotation"

    # Verify Associative Link & Spreading Activation
    associations = sys2.assoc_net.get_associations_for(CognitiveTier.SEMANTIC_FACT, b1.node_id)
    assert len(associations) == 1
    assert associations[0].target_id == skill.skill_id


def test_associative_network_capacity_leak_prevention():
    """Verify that when links are pruned on capacity limit, adjacency index is cleaned without leaks."""
    net = AssociativeNetwork(max_links=2)
    l1 = net.link_memories(CognitiveTier.WORKING, "wm1", CognitiveTier.EPISODIC_FLAT, "tr1", initial_weight=0.1)
    net.link_memories(CognitiveTier.WORKING, "wm2", CognitiveTier.EPISODIC_FLAT, "tr2", initial_weight=0.2)
    assert len(net._links) == 2

    # Adding third link triggers capacity enforcement
    net.link_memories(CognitiveTier.WORKING, "wm3", CognitiveTier.EPISODIC_FLAT, "tr3", initial_weight=0.9)
    assert len(net._links) == 2
    # Oldest/lowest weight link (l1) should have been pruned
    assert l1.link_id not in net._links
    # Adjacency for wm1 should NOT contain l1.link_id
    wm1_links = net.get_associations_for(CognitiveTier.WORKING, "wm1")
    assert len(wm1_links) == 0


def test_three_way_belief_conflict_reconciliation():
    """Verify 3-way conflicting beliefs resolve cleanly without resurrecting superseded nodes."""
    graph = SemanticBeliefGraph()
    t = time.time()
    graph.add_belief("DatabaseEngine", "flavor", "PostgreSQL_14", confidence=0.7, created_at=t)
    graph.add_belief("DatabaseEngine", "flavor", "PostgreSQL_15", confidence=0.8, created_at=t + 10.0)
    b3 = graph.add_belief("DatabaseEngine", "flavor", "PostgreSQL_16", confidence=0.95, created_at=t + 20.0)

    reconciled = graph.reconcile_conflicts()
    assert reconciled >= 1
    # Newest, highest confidence b3 should win and be ACTIVE
    assert b3.status == BeliefStatus.ACTIVE


def test_procedural_skill_deduplication_and_reinforcement():
    """Verify duplicate skill registrations update existing skill instead of duplicating."""
    pm = ProceduralSkillMemory()
    s1 = pm.register_skill(
        name="deploy_service",
        description="Initial deploy procedure",
        trigger_pattern=r"deploy",
        steps=["step 1"],
    )
    s2 = pm.register_skill(
        name="deploy_service",
        description="Refined deploy procedure with healthcheck",
        trigger_pattern=r"deploy",
        steps=["step 1", "step 2 check health"],
    )
    assert s1.skill_id == s2.skill_id
    assert len(pm.list_skills()) == 1
    assert s2.description == "Refined deploy procedure with healthcheck"


def test_cognitive_memory_builtin_tool(tmp_path: Path):
    """Verify built-in cognitive_memory_tool works across all supported actions."""
    from agent_workspace.tools.builtins.cognitive_memory_tool import cognitive_memory_tool

    runtime = SimpleNamespace(context={"user_id": "tool-owner"})

    def invoke(arguments):
        return cognitive_memory_tool.func(runtime=runtime, **arguments)

    # 1. Overview
    res_ov = invoke({"action": "overview"})
    assert "tiers" in res_ov

    # 2. Store Belief
    res_sb = invoke(
        {
            "action": "store_belief",
            "subject": "MicroserviceArchitecture",
            "predicate": "uses_event_bus",
            "object_val": "Kafka",
            "confidence": 0.92,
        }
    )
    assert "stored" in res_sb

    # 3. Lookup Skill
    res_ls = invoke({"action": "lookup_skill", "query": "pytest verify"})
    assert "skills" in res_ls

    # 4. Record Step
    res_rs = invoke(
        {
            "action": "record_step",
            "query": "deploy_agent",
            "observation": "Container healthy on port 8000",
            "outcome": "success",
        }
    )
    assert "recorded" in res_rs

    # 5. Recall
    res_rc = invoke(
        {
            "action": "recall",
            "query": "MicroserviceArchitecture Kafka event bus",
            "limit": 3,
        }
    )
    assert "results" in res_rc
