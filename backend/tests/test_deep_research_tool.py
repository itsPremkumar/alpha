import json
from pathlib import Path

import pytest

from alpha.research.engine import DeepResearchEngine
from alpha.subagents.categories import (
    apply_category,
    get_category,
    list_category_names,
)
from alpha.subagents.config import SubagentConfig
from alpha.tools.builtins import deep_research


@pytest.mark.asyncio
async def test_deep_research_tool_invocation(tmp_path, monkeypatch):
    # The tool's engine now defaults to live web backends (honest by
    # default); pin it to the explicit offline providers so the test stays
    # deterministic while every report/citation path stays real.
    monkeypatch.setattr(
        "alpha.tools.builtins.deep_research_tool.DeepResearchEngine",
        lambda: DeepResearchEngine(
            search_fn=DeepResearchEngine.mock_search,
            fetch_fn=DeepResearchEngine.mock_fetch,
        ),
    )
    monkeypatch.setattr(
        "alpha.tools.builtins.deep_research_tool._resolve_outputs_dir",
        lambda: tmp_path,
    )

    result = await deep_research.ainvoke(
        {
            "topic": "Neuromorphic Computing Chips Architecture",
            "depth": 2,
            "max_sources": 5,
            "include_adversarial": True,
            "output_path": "deep_report.md",
        }
    )
    payload = json.loads(result)

    assert payload["status"] == "completed"
    assert payload["output_error"] is None
    assert payload["adversarial_comparisons_detected"] == payload["contradictions_detected"]
    assert "Deep Research Report: Neuromorphic Computing Chips Architecture" in result
    assert "[S1]" in result
    assert "Gathered Sources & Evidence Matrix" in result

    report_file = Path(payload["saved_report_path"])
    assert report_file == tmp_path / "deep_report.md"
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "Neuromorphic Computing Chips Architecture" in content
    assert "[S1]" in content


@pytest.mark.asyncio
async def test_deep_research_tool_rejects_output_symlink(tmp_path, monkeypatch):
    async def empty_search(query: str, max_results: int = 5):
        return []

    async def should_not_fetch(url: str) -> str:
        raise AssertionError("No source should be fetched")

    monkeypatch.setattr(
        "alpha.tools.builtins.deep_research_tool.DeepResearchEngine",
        lambda: DeepResearchEngine(search_fn=empty_search, fetch_fn=should_not_fetch),
    )
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("do not overwrite", encoding="utf-8")
    try:
        (outputs / "report.md").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    monkeypatch.setattr(
        "alpha.tools.builtins.deep_research_tool._resolve_outputs_dir",
        lambda: outputs,
    )

    payload = json.loads(
        await deep_research.ainvoke(
            {
                "topic": "Unavailable Subject",
                "depth": 1,
                "max_sources": 3,
                "include_adversarial": False,
                "output_path": "report.md",
            }
        )
    )

    assert payload["saved_report_path"] is None
    assert "symbolic link" in payload["output_error"].lower()
    assert outside.read_text(encoding="utf-8") == "do not overwrite"


@pytest.mark.asyncio
async def test_deep_research_tool_reports_no_evidence_and_rejects_arbitrary_path(tmp_path, monkeypatch):
    async def empty_search(query: str, max_results: int = 5):
        return []

    async def should_not_fetch(url: str) -> str:
        raise AssertionError("No source should be fetched")

    monkeypatch.setattr(
        "alpha.tools.builtins.deep_research_tool.DeepResearchEngine",
        lambda: DeepResearchEngine(search_fn=empty_search, fetch_fn=should_not_fetch),
    )
    outside = tmp_path / "outside" / "report.md"

    result = await deep_research.ainvoke(
        {
            "topic": "Unavailable Subject",
            "depth": 1,
            "max_sources": 3,
            "include_adversarial": False,
            "output_path": str(outside),
        }
    )
    payload = json.loads(result)

    assert payload["status"] == "no_evidence"
    assert payload["sources_analyzed"] == 0
    assert payload["citations_verified"] == 0
    assert payload["saved_report_path"] is None
    assert "filename" in payload["output_error"].lower()
    assert not outside.exists()


def test_deep_research_subagent_category():
    # Verify category appears in catalog
    categories = list_category_names(None)
    assert "deep-research" in categories

    cat = get_category("deep-research")
    assert cat is not None
    assert cat.max_turns == 150
    assert "deep_research" in cat.tools
    assert "agent_eye_search" in cat.tools
    assert "agent_eye_sources" in cat.tools
    assert "compile_five_pass_search" in cat.tools

    # Test applying category to a base subagent config
    base = SubagentConfig(
        name="research-worker",
        description="Worker for research",
        system_prompt="Base worker prompt",
        max_turns=30,
        timeout_seconds=60,
    )

    resolution = apply_category(base, "deep-research", app_config=None)
    assert resolution.config_overrides["max_turns"] == 150
    assert "deep_research" in resolution.config_overrides["tools"]
    assert "deep-research" in resolution.prompt_suffix
    assert "5-pass search" in resolution.prompt_suffix
