"""Self-improvement loop ports: usage telemetry, skill curator, authoring bar."""

from __future__ import annotations

import time

import pytest

from agent_workspace.skills.authoring import build_learn_prompt, validate_skill_draft
from agent_workspace.skills.curator import SkillCurator
from agent_workspace.skills.usage import SkillUsageTracker


@pytest.fixture()
def skills_root(tmp_path):
    root = tmp_path / "skills"
    for name in ("ship-it", "old-agent-skill", "pinned-skill", "human-skill"):
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return root


@pytest.fixture()
def usage(tmp_path):
    return SkillUsageTracker(usage_file=tmp_path / "skills" / ".usage.json")


def test_usage_bump_never_raises_and_tracks(usage):
    usage.record_use("ship-it", created_by="agent")
    usage.record_use("ship-it")
    usage.record_use("", created_by="agent")
    stats = usage.stats("ship-it")
    assert stats is not None and stats.uses == 2 and stats.created_by == "agent"
    assert usage.stats("missing") is None


def test_stale_candidates_only_agent_created(usage):
    usage.mark_created_by("old-agent-skill", "agent")
    usage.mark_created_by("human-skill", "human")
    stale = usage.stale_candidates(["old-agent-skill", "human-skill", "untracked"], stale_after_days=14.0)
    assert stale == ["old-agent-skill"]


def test_curator_stale_then_archive_recoverable(skills_root, usage):
    curator = SkillCurator(skills_root=skills_root, usage=usage)
    usage.mark_created_by("old-agent-skill", "agent")
    usage.mark_created_by("pinned-skill", "agent")
    curator.pin("pinned-skill")

    ancient = time.time() - 60 * 86400.0
    usage._rows["old-agent-skill"].last_used_at = ancient
    usage._rows["pinned-skill"].last_used_at = ancient
    usage._save()

    changed = curator.apply_transitions(stale_after_days=14.0, archive_after_days=30.0, now=time.time())
    assert changed["staled"] == ["old-agent-skill"]
    assert (skills_root / "pinned-skill" / "SKILL.md").exists()

    older = time.time()
    usage._rows["old-agent-skill"].last_used_at = ancient
    usage._save()
    changed = curator.apply_transitions(stale_after_days=14.0, archive_after_days=30.0, now=older + 1.0)
    assert changed["archived"] == ["old-agent-skill"]
    assert not (skills_root / "old-agent-skill").exists()
    assert (skills_root / ".archive" / "old-agent-skill" / "SKILL.md").exists()

    assert curator.restore("old-agent-skill") is True
    assert (skills_root / "old-agent-skill" / "SKILL.md").exists()
    assert curator.restore("never-existed") is False


def test_curator_never_touches_human_or_unknown(skills_root, usage):
    curator = SkillCurator(skills_root=skills_root, usage=usage)
    usage.mark_created_by("human-skill", "human")
    changed = curator.apply_transitions(stale_after_days=0.0, archive_after_days=0.0, now=time.time())
    assert changed == {"staled": [], "archived": []}
    report = curator.report()
    assert set(report["known"]) == {"ship-it", "old-agent-skill", "pinned-skill", "human-skill"}


def test_learn_prompt_covers_source_and_bar():
    prompt = build_learn_prompt("the deploy runbook in docs/")
    assert "deploy runbook" in prompt
    assert "60" in prompt and "Verification" in prompt
    assert build_learn_prompt("").startswith("Turn the following")


def test_skill_draft_validator():
    good_md = "# Ship It\nIntro line.\n## When to Use\nx\n## Prerequisites\ny\n## How to Run\n`hermes run`\n## Quick Reference\n- a\n## Procedure\n1. do\n## Pitfalls\nnone\n## Verification\nRun `make verify`.\n"
    assert validate_skill_draft("ship-it", "Ship services with one command.", good_md) == []

    findings = validate_skill_draft("Bad Name", "A comprehensive and powerful solution for everything " + "x" * 80, "# T\nno sections here\napi_key = 'secret12345'\n")
    assert any("lowercase-hyphenated" in f for f in findings)
    assert any("chars" in f for f in findings)
    assert any("marketing word" in f for f in findings)
    assert any("When to Use" in f for f in findings)
    assert any("secret" in f for f in findings)
    assert any("period" in f for f in validate_skill_draft("ok-name", "No period here", good_md))
