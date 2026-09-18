"""Unit tests for the Skill Synthesis Workshop and Tool."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from agent_workspace.skills.workshop import SkillWorkshopEngine
from agent_workspace.tools.builtins.skill_workshop_tool import synthesize_reusable_skill


def test_distill_from_trace_creates_valid_draft():
    steps = [
        {"tool": "exec", "action": "Run build", "command": "cargo build --release"},
        {"tool": "exec", "action": "Run tests", "command": "cargo test --workspace"},
    ]
    draft = SkillWorkshopEngine.distill_from_trace(
        name="rust-build-and-test",
        description="Builds and runs tests for a Rust workspace.",
        trace_steps=steps,
        verification_cmd="cargo test --workspace",
    )

    assert draft.name == "rust-build-and-test"
    assert draft.description == "Builds and runs tests for a Rust workspace."
    assert draft.is_valid is True
    assert len(draft.findings) == 0
    assert "## When to Use" in draft.markdown_content
    assert "## Verification" in draft.markdown_content
    assert "cargo test --workspace" in draft.markdown_content


def test_publish_skill_lifecycle():
    with TemporaryDirectory() as tmp_dir:
        target_skills = Path(tmp_dir)
        steps = [
            {"tool": "exec", "action": "Clean temp", "command": "rm -rf tmp/cache"},
        ]
        draft = SkillWorkshopEngine.distill_from_trace(
            name="clean-temp-cache",
            description="Cleans temporary artifacts and build cache.",
            trace_steps=steps,
            verification_cmd="test ! -d tmp/cache",
        )
        assert draft.is_valid is True

        published_file = SkillWorkshopEngine.publish_skill(draft, custom_skills_dir=target_skills, overwrite=False)
        assert published_file.exists()
        content = published_file.read_text(encoding="utf-8")
        assert "name: clean-temp-cache" in content
        assert "## Procedure" in content

        # Overwrite=False should raise FileExistsError
        with pytest.raises(FileExistsError):
            SkillWorkshopEngine.publish_skill(draft, custom_skills_dir=target_skills, overwrite=False)


def test_synthesize_reusable_skill_tool():
    steps = [
        {"tool": "exec", "action": "Format code", "command": "ruff format ."},
    ]
    res = synthesize_reusable_skill.invoke(
        {
            "skill_name": "ruff-code-formatter",
            "description": "Formats python source files using ruff.",
            "steps_json": json.dumps(steps),
            "verification_command": "ruff format --check .",
            "auto_publish": False,
        }
    )

    assert "synthesized successfully and ready for review" in res
    assert "ruff-code-formatter" in res


def test_synthesize_reusable_skill_auto_publish(monkeypatch):
    with TemporaryDirectory() as tmp_dir:
        monkeypatch.setenv("AGENT_WORKSPACE_PROJECT_ROOT", tmp_dir)
        steps = [{"tool": "exec", "action": "Test action", "command": "echo test"}]
        res = synthesize_reusable_skill.invoke(
            {
                "skill_name": "auto-published-skill",
                "description": "Auto published skill for testing.",
                "steps_json": json.dumps(steps),
                "verification_command": "echo verify",
                "auto_publish": True,
            }
        )
        assert "synthesized and successfully published to" in res
        expected_file = Path(tmp_dir) / "skills" / "custom" / "auto-published-skill" / "SKILL.md"
        assert expected_file.exists()


def test_sanitize_edge_cases():
    # Names with leading/trailing hyphens or symbols
    draft = SkillWorkshopEngine.distill_from_trace(
        name="--custom_test_task--",
        description="Powerful and cutting-edge tool to execute test tasks in the project.",
        trace_steps=[{"tool": "exec", "command": "pytest"}],
        verification_cmd="pytest -q",
    )
    assert not draft.name.startswith("-")
    assert not draft.name.endswith("-")
    assert "powerful" not in draft.description.lower()
    assert "cutting-edge" not in draft.description.lower()
    assert draft.description.endswith(".")
    assert len(draft.description) <= 60
    assert draft.is_valid is True
