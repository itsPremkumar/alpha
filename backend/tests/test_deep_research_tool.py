import os
import tempfile
import pytest

from agent_workspace.subagents.categories import (
    apply_category,
    get_category,
    list_category_names,
)
from agent_workspace.subagents.config import SubagentConfig
from agent_workspace.tools.builtins import deep_research


@pytest.mark.asyncio
async def test_deep_research_tool_invocation():
    with tempfile.TemporaryDirectory() as tmpdir:
        report_file = os.path.join(tmpdir, "deep_report.md")
        result = await deep_research.ainvoke({
            "topic": "Neuromorphic Computing Chips Architecture",
            "depth": 2,
            "max_sources": 5,
            "include_adversarial": True,
            "output_path": report_file,
        })

        assert isinstance(result, str)
        assert "Deep Research Report: Neuromorphic Computing Chips Architecture" in result
        assert "[S1]" in result
        assert "Verified Sources & Evidence Matrix" in result

        # Check that artifact file was written
        assert os.path.exists(report_file)
        with open(report_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "Neuromorphic Computing Chips Architecture" in content
            assert "[S1]" in content


def test_deep_research_subagent_category():
    # Verify category appears in catalog
    categories = list_category_names(None)
    assert "deep-research" in categories

    cat = get_category("deep-research")
    assert cat is not None
    assert cat.max_turns == 150
    assert "deep_research" in cat.tools
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
