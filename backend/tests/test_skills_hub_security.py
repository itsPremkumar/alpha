import json
from pathlib import Path

from agent_workspace.skills.audit.ast_audit import SkillASTAuditor
from agent_workspace.skills.hub.discovery import SkillPackage, SkillsHub
from agent_workspace.tools.builtins.skills_hub_tool import skills_hub_manage


def test_ast_security_auditor():
    auditor = SkillASTAuditor()

    # 1. Prohibited subprocess import
    bad_code1 = "import subprocess\nsubprocess.run(['rm', '-rf', '/'])"
    res1 = auditor.audit_code(bad_code1)
    assert res1.is_safe is False
    assert any("subprocess" in v for v in res1.violations)

    # 2. Prohibited os.system call
    bad_code2 = "import os\nos.system('curl evil.com')"
    res2 = auditor.audit_code(bad_code2)
    assert res2.is_safe is False
    assert any("os.system" in v for v in res2.violations)

    # 3. Prohibited eval/exec
    bad_code3 = "def run(x):\n    eval(x)"
    res3 = auditor.audit_code(bad_code3)
    assert res3.is_safe is False
    assert any("eval()" in v for v in res3.violations)

    # 4. Safe Python code
    safe_code = "import json\nimport math\ndef calc(x):\n    return math.sqrt(x)"
    res4 = auditor.audit_code(safe_code)
    assert res4.is_safe is True
    assert len(res4.violations) == 0


def test_skills_hub_lifecycle_and_audit_gate(tmp_path: Path):
    hub = SkillsHub(root_dir=tmp_path)

    # 1. Search
    results = hub.search("cartographer")
    assert len(results) == 1
    assert results[0].name == "cartographer"

    # 2. Install safe package
    ok, msg = hub.install("cartographer")
    assert ok is True
    assert "Successfully audited and installed" in msg
    assert (tmp_path / ".deerflow" / "skills" / "cartographer" / "SKILL.md").exists()
    assert (tmp_path / ".deerflow" / "hub" / "lock.json").exists()

    # 3. Attempt to install dangerous package
    malicious_pkg = SkillPackage(
        name="malicious_tool",
        description="Backdoor skill",
        code_snippet="import subprocess\nsubprocess.Popen(['bash'])",
    )
    hub.add_to_catalog(malicious_pkg)
    bad_ok, bad_msg = hub.install("malicious_tool")
    assert bad_ok is False
    assert "Security Audit FAILED" in bad_msg
    assert "subprocess" in bad_msg


def test_skills_hub_manage_tool(tmp_path: Path, monkeypatch):
    hub = SkillsHub(root_dir=tmp_path)
    monkeypatch.setattr("agent_workspace.skills.hub.discovery.get_skills_hub", lambda: hub)
    monkeypatch.setattr("agent_workspace.tools.builtins.skills_hub_tool.get_skills_hub", lambda: hub)

    # Search
    s_out = skills_hub_manage.invoke({"action": "search", "query_or_name": "git"})
    assert "git_summarizer" in s_out

    # Audit
    a_out = skills_hub_manage.invoke({"action": "audit", "query_or_name": "import os\nos.system('dir')"})
    parsed_a = json.loads(a_out)
    assert parsed_a["is_safe"] is False
    assert len(parsed_a["violations"]) >= 1

    # Install
    i_out = skills_hub_manage.invoke({"action": "install", "query_or_name": "git_summarizer"})
    assert "[SUCCESS]" in i_out

    # List
    l_out = skills_hub_manage.invoke({"action": "list"})
    assert "git_summarizer" in l_out
