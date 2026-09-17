import tempfile
from pathlib import Path

from agent_workspace.skills.forge import (
    Skill,
    SkillForge,
    SkillParameter,
    SkillRegistry,
)


def test_skill_forge_from_trace():
    trace = [
        {"tool": "view_file", "action": "inspect_imports", "target": "backend/app/main.py"},
        {"tool": "replace_content", "action": "update_route", "target": "backend/app/main.py"},
        {"tool": "pytest", "command": "pytest backend/tests/test_routes.py", "action": "verify"},
    ]

    skill = SkillForge.forge_from_trace(
        name="update-backend-route",
        description="Inspects and updates routes in backend target file.",
        trace_steps=trace,
    )

    assert skill.name == "update-backend-route"
    assert len(skill.parameters) == 1
    assert skill.parameters[0].name == "target_path"
    assert "{target_path}" in skill.template

    # Instantiate with concrete parameter
    rendered = skill.instantiate(target_path="src/api.py")
    assert "src/api.py" in rendered


def test_skill_markdown_rendering():
    skill = Skill(
        name="safe-git-patch",
        description="Creates an isolated branch and executes patch.",
        template="1. Git checkout -b {branch_name}\n2. Apply patch to {target_path}",
        parameters=[
            SkillParameter(name="branch_name", description="Name of temporary branch", required=True),
            SkillParameter(name="target_path", description="File to patch", required=True),
        ],
    )
    md = skill.to_skill_md()
    assert "name: safe-git-patch" in md
    assert "## Parameters" in md
    assert "- `branch_name`" in md
    assert "## Instructions" in md


def test_skill_registry_and_test_gates():
    registry = SkillRegistry()

    skill = Skill(
        name="deploy-hotfix",
        description="Automated hotfix deployer.",
        template="Deploy {service} to {env}",
        parameters=[
            SkillParameter(name="service", required=True),
            SkillParameter(name="env", required=True),
        ],
    )

    # Test cases: 2 valid, 1 missing parameter
    test_cases = [
        {"params": {"service": "auth", "env": "staging"}},
        {"params": {"service": "payment", "env": "prod"}},
        {"params": {"service": "billing"}},  # missing env -> error
    ]

    pass_rate = registry.test_skill(skill, test_cases)
    assert pass_rate == round(2 / 3, 3)
    assert skill.test_count == 3

    # Reject if pass rate < 80%
    assert registry.register(skill, min_pass_rate=0.80) is False

    # Accept if pass rate threshold relaxed
    assert registry.register(skill, min_pass_rate=0.60) is True

    # Export to disk
    with tempfile.TemporaryDirectory() as tmpdir:
        paths = registry.export_to_dir(tmpdir)
        assert len(paths) == 1
        exported = Path(paths[0])
        assert exported.name == "SKILL.md"
        assert exported.exists()
