"""Offline self-documentation retrieval contract."""

from __future__ import annotations

import json

import pytest
from langgraph.prebuilt import ToolRuntime

from alpha.knowledge.self_documentation import (
    _CACHE,
    _CACHE_LOCK,
    _CACHE_MAX_ENTRIES,
    SelfDocumentationIndex,
    get_self_documentation_index,
)
from alpha.tools.builtins.self_documentation_tool import search_project_docs
from alpha.tools.tools import BUILTIN_TOOLS


def _write_project(root):
    (root / "docs").mkdir(parents=True)
    (root / "references").mkdir()
    (root / "node_modules" / "secret-package").mkdir(parents=True)

    (root / "AGENTS.md").write_text(
        "# Agent guidance\n\nUse the deterministic test gate before declaring work complete.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Alpha Test Project\n\nRun the focused test command from the repository root.\n",
        encoding="utf-8",
    )
    (root / "docs" / "setup.md").write_text(
        "# Setup\n\n## Scheduler\n\nSet `scheduler.enabled: true` before starting the gateway.\n",
        encoding="utf-8",
    )
    (root / "config.example.yaml").write_text(
        "scheduler:\n  enabled: false\n  queue_timeout_seconds: 900\n  wake_gate:\n    mode: deterministic\n",
        encoding="utf-8",
    )
    (root / "references" / "future.md").write_text(
        "# Future design\n\nA reference-only constellation planner may use quorum routing.\n",
        encoding="utf-8",
    )
    (root / "skills.custom").mkdir()
    (root / "skills.custom" / "SKILL.md").write_text(
        "# Private skill\n\nsecret-skill-token must never be indexed.\n",
        encoding="utf-8",
    )
    (root / ".env").write_text("OPENROUTER_API_KEY=do-not-index\n", encoding="utf-8")
    (root / "config.yaml").write_text("secret_runtime_token: do-not-index\n", encoding="utf-8")
    (root / "private.key").write_text("secret-private-key do-not-index\n", encoding="utf-8")
    (root / "node_modules" / "secret-package" / "README.md").write_text(
        "secret-node-token must never be indexed\n",
        encoding="utf-8",
    )


def _tool_runtime() -> ToolRuntime:
    return ToolRuntime(
        state={},
        context={},
        config={},
        stream_writer=lambda _: None,
        tool_call_id="self-docs-test",
        store=None,
    )


def test_search_prefers_current_authoritative_sources_and_excludes_secrets(tmp_path):
    _write_project(tmp_path)
    index = get_self_documentation_index(tmp_path, refresh=True)

    result = index.search("scheduler queue timeout seconds", max_results=5)

    assert result
    assert result[0]["path"] == "config.example.yaml"
    assert result[0]["authority"] == "config_schema"
    assert result[0]["reference_only"] is False
    assert result[0]["start_line"] >= 1
    assert result[0]["end_line"] >= result[0]["start_line"]
    assert result[0]["sha256"]

    indexed_paths = {source.path for source in index.snapshot.sources}
    assert "docs/setup.md" in indexed_paths
    assert "AGENTS.md" in indexed_paths
    assert ".env" not in indexed_paths
    assert "config.yaml" not in indexed_paths
    assert "skills.custom/SKILL.md" not in indexed_paths
    assert not any(path.startswith("node_modules/") for path in indexed_paths)

    everything = json.dumps(index.status(), sort_keys=True)
    assert "project_root" not in everything
    assert str(tmp_path) not in everything
    assert "do-not-index" not in everything
    assert "secret-skill-token" not in everything
    assert "secret-node-token" not in everything
    assert "secret-private-key" not in everything


def test_reference_material_is_opt_in_and_never_misrepresented_as_current(tmp_path):
    _write_project(tmp_path)

    current = get_self_documentation_index(tmp_path, include_references=False, refresh=True)
    assert current.search("constellation quorum routing") == []

    with_references = get_self_documentation_index(tmp_path, include_references=True, refresh=True)
    result = with_references.search("constellation quorum routing", max_results=5)

    assert result
    assert result[0]["path"] == "references/future.md"
    assert result[0]["authority"] == "design_reference"
    assert result[0]["reference_only"] is True
    assert "non-authoritative" in result[0]["authority_note"]


def test_read_is_confined_digest_checked_and_refreshes_stale_evidence(tmp_path):
    _write_project(tmp_path)
    index = get_self_documentation_index(tmp_path, refresh=True)
    hit = index.search("wake gate deterministic mode", max_results=3)[0]
    old_digest = hit["sha256"]
    old_index_id = index.snapshot.index_id

    fresh = index.read(
        "config.example.yaml",
        start_line=1,
        max_lines=4,
        expected_sha256=old_digest,
    )
    assert fresh["success"] is True
    assert fresh["content"].startswith("scheduler:")
    assert fresh["sha256"] == old_digest

    config = tmp_path / "config.example.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "  retry_limit: 3\n",
        encoding="utf-8",
    )
    stale = index.read(
        "config.example.yaml",
        start_line=1,
        max_lines=4,
        expected_sha256=old_digest,
    )
    assert stale["success"] is False
    assert stale["error"] == "source_changed"
    assert stale["expected_sha256"] == old_digest
    assert stale["current_sha256"] != old_digest

    refreshed = get_self_documentation_index(tmp_path)
    assert refreshed.snapshot.index_id != old_index_id
    assert refreshed.search("retry limit", max_results=3)[0]["path"] == "config.example.yaml"

    assert index.read("../.env")["error"] == "path_outside_index"
    assert index.read(".env")["error"] == "path_not_indexed"


def test_search_handles_utf8_empty_queries_and_invalid_limits(tmp_path):
    _write_project(tmp_path)
    (tmp_path / "docs" / "unicode.md").write_text(
        "# Configuración\n\nEl servidor usa `queue_timeout_seconds` con seguridad.\n",
        encoding="utf-8",
    )
    index = SelfDocumentationIndex(tmp_path, include_references=False)

    assert index.search("") == []
    assert index.search("does-not-exist-alpha-unique-token") == []
    assert index.search("configuración queue timeout", max_results=999)
    assert len(index.search("configuración", max_results=999)) <= 20

    with pytest.raises(ValueError, match="max_results"):
        index.search("scheduler", max_results=0)
    with pytest.raises(ValueError, match="path_prefix"):
        index.search("scheduler", path_prefix="../private")
    with pytest.raises(ValueError, match="path_prefix"):
        index.search("scheduler", path_prefix="C:/private")


def test_index_cache_is_lru_bounded(tmp_path) -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
    for index in range(_CACHE_MAX_ENTRIES + 4):
        root = tmp_path / f"project-{index}"
        _write_project(root)
        get_self_documentation_index(root, refresh=True)

    with _CACHE_LOCK:
        assert len(_CACHE) == _CACHE_MAX_ENTRIES


def test_builtin_tool_schema_cannot_select_another_root_or_force_refresh() -> None:
    properties = search_project_docs.tool_call_schema.model_json_schema()["properties"]

    assert "runtime" not in properties
    assert "project_root" not in properties
    assert "refresh" not in properties


def test_builtin_tool_is_registered_and_returns_structured_evidence(tmp_path, monkeypatch):
    _write_project(tmp_path)
    monkeypatch.setattr(
        "alpha.tools.builtins.self_documentation_tool.discover_project_root",
        lambda: tmp_path,
    )
    runtime = _tool_runtime()

    assert search_project_docs in BUILTIN_TOOLS

    raw = search_project_docs.invoke(
        {
            "runtime": runtime,
            "query": "scheduler queue timeout seconds",
            "max_results": 3,
        }
    )
    payload = json.loads(raw) if isinstance(raw, str) else raw

    assert payload["success"] is True
    assert payload["action"] == "search"
    assert payload["index_id"]
    assert payload["results"][0]["path"] == "config.example.yaml"
    assert payload["free_and_offline"] is True

    status_raw = search_project_docs.invoke({"runtime": runtime, "action": "status"})
    status = json.loads(status_raw) if isinstance(status_raw, str) else status_raw
    assert status["success"] is True
    assert status["network_calls"] == 0
    assert status["embedding_calls"] == 0
    assert status["source_count"] > 0

    too_large = search_project_docs.invoke({"runtime": runtime, "query": "x" * 4_001})
    assert too_large["success"] is False
    assert too_large["error"] == "invalid_argument"
    bad_digest = search_project_docs.invoke({"runtime": runtime, "action": "read", "path": "README.md", "expected_sha256": "abc"})
    assert bad_digest["success"] is False
    assert bad_digest["error"] == "invalid_argument"

    hit = payload["results"][0]
    read_raw = search_project_docs.invoke(
        {
            "runtime": runtime,
            "action": "read",
            "path": hit["path"],
            "expected_sha256": hit["sha256"],
        }
    )
    read_payload = json.loads(read_raw) if isinstance(read_raw, str) else read_raw
    assert read_payload["success"] is True
    assert read_payload["content"]
