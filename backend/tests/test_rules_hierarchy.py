"""Tests for Hierarchical AGENTS.md Context & Scoped Rules Engine."""

from pathlib import Path

from agent_workspace.rules.hierarchy import (
    HierarchicalRuleEngine,
    init_deep_scaffold,
)


def test_hierarchical_context_discovery_and_ordering(tmp_path: Path):
    # Setup nested directories:
    # tmp_path/AGENTS.md
    # tmp_path/backend/AGENTS.md
    # tmp_path/backend/api/AGENTS.md
    # tmp_path/backend/api/routes/endpoint.py
    
    (tmp_path / "AGENTS.md").write_text("ROOT RULE: Global standards", encoding="utf-8")
    
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "AGENTS.md").write_text("BACKEND RULE: Python only", encoding="utf-8")

    api_dir = backend_dir / "api"
    api_dir.mkdir()
    (api_dir / "AGENTS.md").write_text("API RULE: FastAPI endpoints only", encoding="utf-8")

    routes_dir = api_dir / "routes"
    routes_dir.mkdir()
    endpoint_file = routes_dir / "endpoint.py"
    endpoint_file.write_text("# dummy file", encoding="utf-8")

    engine = HierarchicalRuleEngine(root_dir=tmp_path)
    discovered = engine.discover_context(endpoint_file)

    assert len(discovered) == 3
    # Ordered from root to leaf
    assert discovered[0]["path"] == "AGENTS.md"
    assert "ROOT RULE" in discovered[0]["content"]
    assert discovered[1]["path"] == "backend/AGENTS.md"
    assert "BACKEND RULE" in discovered[1]["content"]
    assert discovered[2]["path"] == "backend/api/AGENTS.md"
    assert "API RULE" in discovered[2]["content"]

    rendered = engine.render_prompt_block(endpoint_file)
    assert "<hierarchical_project_context" in rendered
    assert "ROOT RULE" in rendered
    assert "BACKEND RULE" in rendered
    assert "API RULE" in rendered


def test_init_deep_scaffold(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()

    created = init_deep_scaffold(tmp_path, subdirs=["backend", "frontend"])
    assert len(created) == 3
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "backend" / "AGENTS.md").exists()
    assert (tmp_path / "frontend" / "AGENTS.md").exists()
