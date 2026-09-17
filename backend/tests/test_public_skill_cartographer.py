"""Structural gate for the project-cartographer public skill.

The skill must keep parsing (name matches its directory) and stay free of
high-severity SkillScan findings, so it can never rot into an unloadable or
unshippable state unnoticed.
"""

from pathlib import Path

from agent_workspace.skills.parser import parse_skill_file
from agent_workspace.skills.skillscan.orchestrator import enforce_static_scan
from agent_workspace.skills.types import SkillCategory

SKILL_DIR = (
    Path(__file__).resolve().parents[2] / "skills" / "public" / "project-cartographer"
)

FORBIDDEN_SEVERITIES = frozenset({"CRITICAL", "HIGH"})


def test_cartographer_skill_parses_with_matching_name():
    skill = parse_skill_file(SKILL_DIR / "SKILL.md", SkillCategory.PUBLIC)
    assert skill is not None
    assert skill.name == "project-cartographer"
    assert skill.description
    assert len(skill.description) <= 1024


def test_cartographer_skill_has_no_high_severity_findings(tmp_path):
    import shutil

    staged = tmp_path / "project-cartographer"
    shutil.copytree(SKILL_DIR, staged)
    findings = enforce_static_scan(staged, skill_name="project-cartographer", app_config=None)
    bad = [
        (finding["rule_id"], finding["severity"])
        for finding in findings
        if finding["severity"] in FORBIDDEN_SEVERITIES
    ]
    assert bad == []
