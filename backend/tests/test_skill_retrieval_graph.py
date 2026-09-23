"""Tests for scored skill retrieval and the directed skill relationship graph.

Covers the WorkSwarm-inspired gap: lexical retrieval with disclosed scoring
(and real evidence in ``reasons``), evidence-backed skill-graph edges,
chain suggestion over a seeded graph, honest empty-graph behavior, catalog
search delegating to the new scorer, and the additive gateway endpoints
behind their single module-level seams.

Persistence tests isolate ``runtime_home()`` via monkeypatch + tmp paths;
endpoint tests stub exactly one module-level function each. No global env
fixtures, no network.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.learning.graph import (
    BASELINE_CONFIDENCE,
    SKILL_EDGE_TYPES,
    SKILL_GRAPH_CHAIN_NOTE,
    SkillGraph,
    load_skill_graph,
    skill_graph_path,
)
from alpha.skills.catalog import MAX_RESULTS, SkillCatalog
from alpha.skills.retrieval import SCORE_METHOD, query_terms, retrieve_skills, score_skills
from alpha.skills.types import Skill, SkillCategory
from app.gateway.deps import get_config
from app.gateway.routers import skills as skills_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_skill(
    name: str,
    description: str = "",
    category: SkillCategory = SkillCategory.PUBLIC,
    allowed_tools: tuple[str, ...] | None = None,
) -> Skill:
    base = Path("/mnt/skills") / category.value / name
    return Skill(
        name=name,
        description=description,
        license=None,
        skill_dir=base,
        skill_file=base / "SKILL.md",
        relative_path=Path(name),
        category=category,
        allowed_tools=allowed_tools,
        enabled=True,
    )


@pytest.fixture
def seeded_skills() -> list[Skill]:
    return [
        _make_skill("data-analysis", "Analyze data with Python, pandas", allowed_tools=("execute_code",)),
        _make_skill("chart-visualization", "Visualize data with interactive charts"),
        _make_skill("podcast-generation", "Generate podcast scripts and audio"),
        _make_skill("warehouse-loader", "Load warehouse tables", category=SkillCategory.CUSTOM),
    ]


@pytest.fixture
def seeded_graph() -> SkillGraph:
    graph = SkillGraph()
    graph.add_edge("data-analysis", "chart-visualization", "produces", evidence="observed run #1", confidence=0.7)
    graph.add_edge("chart-visualization", "report-writer", "can_feed", evidence="observed run #1", confidence=0.6)
    graph.add_edge("podcast-generation", "audio-mixer", "produces", evidence="observed run #2")
    return graph


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.dependency_overrides[get_config] = lambda: SimpleNamespace()
    application.include_router(skills_router.router)
    return application


# ---------------------------------------------------------------------------
# Retrieval: ranking, evidence, disclosure, cutoffs
# ---------------------------------------------------------------------------


def test_retrieval_ranks_seeded_catalog_deterministically(seeded_skills: list[Skill]):
    result = retrieve_skills("chart data", seeded_skills)
    names = [match.skill.name for match in result.matches]
    # Per-term lexical scoring: chart-visualization covers both terms in its
    # name/description (0.45); data-analysis only "data" (0.3); the other two
    # skills overlap nothing and are never injected.
    assert names == ["chart-visualization", "data-analysis"]
    assert result.matches[0].match_score > result.matches[1].match_score
    # Deterministic regardless of catalog insertion order.
    shuffled = list(reversed(seeded_skills))
    assert [match.skill.name for match in retrieve_skills("chart data", shuffled).matches] == names


def test_retrieval_reasons_cite_real_evidence(seeded_skills: list[Skill]):
    # Description term: the reason must quote the matched term and its location.
    desc_match = retrieve_skills("pandas", seeded_skills).matches[0]
    assert desc_match.skill.name == "data-analysis"
    assert any("pandas" in reason for reason in desc_match.reasons)
    assert any("description" in reason for reason in desc_match.reasons)

    # Tool-requirement match: reason must quote the actual required tool.
    tool_matches = retrieve_skills("execute", seeded_skills).matches
    assert [m.skill.name for m in tool_matches] == ["data-analysis"]
    assert any("execute_code" in reason for reason in tool_matches[0].reasons)

    # Tag (category) match: the reason must quote the matched tag itself.
    tag_matches = retrieve_skills("custom", seeded_skills).matches
    assert [m.skill.name for m in tag_matches] == ["warehouse-loader"]
    assert "matched tag 'custom'" in list(tag_matches[0].reasons)


def test_retrieval_score_disclosure_fields_present(seeded_skills: list[Skill]):
    result = retrieve_skills("chart", seeded_skills)
    assert result.score_method == SCORE_METHOD == "lexical-heuristic"
    assert "lexical" in result.note.lower()
    assert "not measured" in result.note.lower()

    payload = result.to_dict()
    assert payload["score_method"] == SCORE_METHOD
    assert payload["weights"]["name_token"] == 0.6
    assert payload["results"], "sanity: 'chart' must match something"
    for row in payload["results"]:
        assert row["score_method"] == SCORE_METHOD
        assert 0.0 <= row["match_score"] <= 1.0
        assert row["reasons"]


def test_retrieval_respects_top_k_and_min_score(seeded_skills: list[Skill]):
    many = seeded_skills + [_make_skill(f"chart-{i:02d}", "Chart tooling") for i in range(6)]

    capped = retrieve_skills("chart", many, top_k=3)
    assert len(capped.matches) == 3
    assert capped.top_k == 3

    strict = retrieve_skills("chart", many, top_k=3, min_score=0.99)
    assert list(strict.matches) == []
    assert "min_score" in strict.note

    # Default cap never injects the whole catalog.
    default = retrieve_skills("chart", many)
    assert len(default.matches) <= 5 < len(many)


def test_retrieval_empty_catalog_is_honest():
    result = retrieve_skills("chart", [])
    assert list(result.matches) == []
    assert "empty" in result.note.lower()
    payload = result.to_dict()
    assert payload["results"] == []
    assert payload["catalog_size"] == 0


def test_retrieval_rejects_nonsensical_cutoffs(seeded_skills: list[Skill]):
    with pytest.raises(ValueError):
        retrieve_skills("chart", seeded_skills, top_k=0)
    with pytest.raises(ValueError):
        retrieve_skills("chart", seeded_skills, min_score=1.5)


def test_query_terms_drop_stopwords_and_short_tokens():
    terms = query_terms("the chart for AI")
    assert "the" not in terms
    assert "ai" not in terms  # below MIN_TERM_LENGTH
    assert "chart" in terms


def test_score_skills_excludes_non_matching_skills(seeded_skills: list[Skill]):
    scored = score_skills("pandas", seeded_skills)
    assert [match.skill.name for match in scored] == ["data-analysis"]


# ---------------------------------------------------------------------------
# Catalog search delegates to retrieval scoring
# ---------------------------------------------------------------------------


def test_catalog_search_consumes_retrieval_scoring(seeded_skills: list[Skill]):
    catalog = SkillCatalog(tuple(seeded_skills))

    # Whole-query regex cannot match "audio scripts" contiguously, but the
    # lexical scorer decomposes the query and finds podcast-generation.
    regex_only = [s.name for s in seeded_skills if "audio scripts" in f"{s.name} {s.description}".lower()]
    assert regex_only == []
    assert [s.name for s in catalog.search("audio scripts")] == ["podcast-generation"]

    # Historic behavior preserved: literal name hits still lead, cap holds.
    assert catalog.search("podcast")[0].name == "podcast-generation"
    assert len(catalog.search("chart")) <= MAX_RESULTS
    assert {s.name for s in catalog.search("select:data-analysis,podcast-generation")} == {"data-analysis", "podcast-generation"}
    assert catalog.search("") == []


# ---------------------------------------------------------------------------
# Skill relationship graph: edges, evidence, persistence
# ---------------------------------------------------------------------------


def test_add_edge_is_evidence_backed_and_never_invents_confidence():
    graph = SkillGraph()
    edge = graph.add_edge("data-analysis", "chart-visualization", "produces", evidence="observed run #1")
    assert edge.confidence == BASELINE_CONFIDENCE == 0.5
    assert edge.evidence_count == 1
    assert edge.last_validated is not None

    # Repeat observation: evidence_count increments, confidence does NOT move.
    again = graph.add_edge("data-analysis", "chart-visualization", "produces", evidence="observed run #2")
    assert again is edge
    assert again.evidence_count == 2
    assert again.confidence == 0.5
    assert len(again.evidence) == 2

    # A declaration without evidence adds nothing to the counts.
    no_evidence = graph.add_edge("data-analysis", "chart-visualization", "produces")
    assert no_evidence.evidence_count == 2
    assert no_evidence.confidence == 0.5

    # Only an explicit caller-validated confidence may set confidence.
    validated = graph.add_edge("podcast-generation", "audio-mixer", "requires", confidence=0.8)
    assert validated.confidence == 0.8
    graph.add_edge("podcast-generation", "audio-mixer", "requires", evidence="observed run #3")
    assert validated.evidence_count == 1
    assert validated.confidence == 0.8


def test_add_edge_validates_inputs():
    graph = SkillGraph()
    with pytest.raises(ValueError):
        graph.add_edge("a", "a", "produces")  # self-edge
    with pytest.raises(ValueError):
        graph.add_edge("a", "b", "sort_of_related")  # unknown type
    with pytest.raises(ValueError):
        graph.add_edge("", "b", "produces")  # empty endpoint
    with pytest.raises(ValueError):
        graph.add_edge("a", "b", "requires", confidence=1.5)  # out of range
    assert set(SKILL_EDGE_TYPES) == {"can_feed", "requires", "enhances", "conflicts_with", "alternative_to", "produces"}


def test_skill_graph_persistence_is_atomic_and_isolated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("alpha.learning.graph.runtime_home", lambda: tmp_path)

    graph = SkillGraph()
    graph.add_edge("a", "b", "can_feed", evidence="observed run")
    path = graph.save()
    assert path == skill_graph_path() == tmp_path / "skill_graph.json"
    assert path.exists()
    # Atomic tmp + os.replace: no leftover temp files beside the target.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["skill_graph.json"]

    reloaded = load_skill_graph()
    assert len(reloaded.edges) == 1
    edge = reloaded.edges[0]
    assert (edge.source, edge.target, edge.edge_type) == ("a", "b", "can_feed")
    assert edge.evidence_count == 1
    assert edge.evidence == ["observed run"]
    assert edge.confidence == BASELINE_CONFIDENCE

    # Corrupt state fails soft to an honest empty graph, never a crash.
    path.write_text("{not json", encoding="utf-8")
    assert SkillGraph.load().edges == []


def test_to_dict_shape_is_honest():
    empty = SkillGraph().to_dict()
    assert empty["edges"] == []
    assert empty["edge_count"] == 0
    assert "empty" in empty["note"].lower()
    assert empty["baseline_confidence"] == BASELINE_CONFIDENCE == 0.5
    assert set(empty["edge_types"]) == set(SKILL_EDGE_TYPES)

    graph = SkillGraph()
    graph.add_edge("a", "b", "enhances")
    filled = graph.to_dict()
    row = filled["edges"][0]
    assert row["confidence"] == 0.5
    assert row["evidence_count"] == 0
    assert row["last_validated"] is None
    assert "neutral" in filled["note"].lower()


# ---------------------------------------------------------------------------
# Chain suggestion
# ---------------------------------------------------------------------------


def test_suggest_chains_over_seeded_graph(seeded_graph: SkillGraph):
    result = seeded_graph.suggest_chains(start="data-analysis", goal="report-writer")
    assert result["score_method"] == "graph-lexical-heuristic"
    chains = result["chains"]
    assert [c["chain"] for c in chains] == [["data-analysis", "chart-visualization", "report-writer"]]
    top = chains[0]
    assert top["score"] == pytest.approx((0.7 + 0.6) / 2)
    assert top["total_evidence"] == 2
    assert any("produces" in reason for reason in top["reasons"])
    assert any("can_feed" in reason for reason in top["reasons"])
    assert "not a measured success rate" in result["note"]

    # Task-tag mode seeds and ranks lexically against the graph nodes.
    tagged = seeded_graph.suggest_chains(task_tags=["chart"])
    assert tagged["chains"], "tag mode should surface chains touching 'chart'"
    assert "chart-visualization" in tagged["chains"][0]["chain"]
    assert all(chain["score"] <= 1.0 for chain in tagged["chains"])


def test_suggest_chains_honest_when_no_path(seeded_graph: SkillGraph):
    result = seeded_graph.suggest_chains(start="report-writer", goal="data-analysis")
    assert result["chains"] == []
    assert result["note"]


def test_suggest_chains_empty_graph_is_honest():
    result = SkillGraph().suggest_chains(start="a", goal="b")
    assert result["chains"] == []
    assert "empty" in result["note"].lower()
    assert result["score_method"] == "graph-lexical-heuristic"

    # A populated graph with no filters is also honest, not speculative.
    graph = SkillGraph()
    graph.add_edge("a", "b", "can_feed")
    no_filter = graph.suggest_chains()
    assert no_filter["chains"] == []
    assert "start" in no_filter["note"].lower()


def test_suggest_chains_baseline_confidence_not_boosted():
    """All-baseline graphs score the neutral baseline, never a success rate."""
    graph = SkillGraph()
    graph.add_edge("a", "b", "can_feed", evidence="one")
    graph.add_edge("b", "c", "produces", evidence="two")
    result = graph.suggest_chains(start="a", goal="c")
    assert [c["score"] for c in result["chains"]] == [pytest.approx(BASELINE_CONFIDENCE)]
    assert SKILL_GRAPH_CHAIN_NOTE in result["note"]


# ---------------------------------------------------------------------------
# Endpoints (one module-level seam stubbed per test)
# ---------------------------------------------------------------------------


def test_retrieve_endpoint_ranks_with_stubbed_seam(app: FastAPI, seeded_skills: list[Skill], monkeypatch):
    monkeypatch.setattr(skills_router, "_retrieval_catalog", lambda config: seeded_skills)
    with TestClient(app) as client:
        response = client.get("/api/skills/retrieve", params={"query": "chart data", "top_k": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["score_method"] == "lexical-heuristic"
    assert "lexical" in body["note"].lower()
    assert "no embeddings" in body["note"].lower()
    assert len(body["results"]) <= 2
    assert body["results"][0]["name"] == "chart-visualization"
    assert body["results"][0]["reasons"]
    assert all(row["score_method"] == "lexical-heuristic" for row in body["results"])


def test_retrieve_endpoint_empty_catalog_is_honest(app: FastAPI, monkeypatch):
    monkeypatch.setattr(skills_router, "_retrieval_catalog", lambda config: [])
    with TestClient(app) as client:
        response = client.get("/api/skills/retrieve", params={"query": "chart"})
    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert "empty" in body["note"].lower()


def test_graph_endpoint_exposes_edges_and_chains(app: FastAPI, seeded_graph: SkillGraph, monkeypatch):
    monkeypatch.setattr(skills_router, "_skill_graph_store", lambda: seeded_graph)
    with TestClient(app) as client:
        plain = client.get("/api/skills/graph")
        chained = client.get("/api/skills/graph", params={"start": "data-analysis", "goal": "report-writer"})

    assert plain.status_code == 200
    body = plain.json()
    assert body["path"].endswith("skill_graph.json")
    assert body["edge_count"] == 3
    assert set(body["edge_types"]) == set(SKILL_EDGE_TYPES)
    assert body["baseline_confidence"] == 0.5
    assert body["chains"] is None  # no filters -> no speculative chains
    for edge in body["edges"]:
        assert {"source", "target", "edge_type", "confidence", "evidence_count", "last_validated"} <= set(edge)
    # Baseline confidence survives serialization; evidence never inflated it.
    podcast_edge = next(e for e in body["edges"] if e["source"] == "podcast-generation")
    assert podcast_edge["confidence"] == BASELINE_CONFIDENCE
    assert podcast_edge["evidence_count"] == 1

    assert chained.status_code == 200
    chains = chained.json()["chains"]
    assert chains["score_method"] == "graph-lexical-heuristic"
    assert [c["chain"] for c in chains["chains"]] == [["data-analysis", "chart-visualization", "report-writer"]]


def test_graph_endpoint_empty_graph_is_honest(app: FastAPI, monkeypatch):
    monkeypatch.setattr(skills_router, "_skill_graph_store", SkillGraph)
    with TestClient(app) as client:
        response = client.get("/api/skills/graph", params={"start": "x", "goal": "y"})
    assert response.status_code == 200
    body = response.json()
    assert body["edges"] == []
    assert "empty" in body["note"].lower()
    assert body["chains"]["chains"] == []
    assert "empty" in body["chains"]["note"].lower()
    assert body["chains"]["score_method"] == "graph-lexical-heuristic"
