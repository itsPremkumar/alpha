"""Remote Skills Hub contracts: real discovery, immutable inspect, safe retrieval."""

import hashlib
import json
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from alpha.skills.audit.ast_audit import SkillASTAuditor
from alpha.skills.hub.discovery import GITHUB_API, SKILLS_SEARCH_URL, SkillPackage, SkillsHub
from alpha.tools.builtins.skills_hub_tool import skills_hub_manage

_COMMIT = "a" * 40


def _github_client(*, include_bad=False, include_skipped=True):
    files = {
        ".agents/skills/react-a11y/SKILL.md": (
            "---\nname: react-a11y\ndescription: Review React interfaces for keyboard and accessibility defects.\n---\n\n# React accessibility\n\n## Workflow\n1. Inspect semantic controls and keyboard operation.\n"
        ),
        ".agents/skills/react-a11y/references/checklist.md": "# Review checklist\nCheck focus visibility and labels.\n",
    }
    blobs = {hashlib.sha1(f"blob {len(content.encode())}\0".encode("ascii") + content.encode()).hexdigest(): content.encode() for content in files.values()}
    entries = [
        *[{"path": path, "type": "blob", "mode": "100644", "sha": sha} for path, sha in zip(files, blobs, strict=True)],
    ]
    if include_skipped:
        entries.extend(
            [
                {"path": ".agents/skills/react-a11y/.env", "type": "blob", "mode": "100644", "sha": "3" * 40},
                {"path": ".agents/skills/react-a11y/link", "type": "blob", "mode": "120000", "sha": "4" * 40},
            ]
        )
    if include_bad:
        entries.append({"path": ".agents/skills-copy/react-a11y/SKILL.md", "type": "blob", "mode": "100644", "sha": "5" * 40})

    def get_json(url):
        parsed = urlparse(url)
        if url.startswith(SKILLS_SEARCH_URL):
            assert parse_qs(parsed.query)["q"] == ["react accessibility"]
            return {
                "skills": [
                    {"id": "millionco/react-doctor/react-a11y", "source": "millionco/react-doctor", "skillId": "react-a11y", "name": "React A11y", "installs": 1234},
                    {"id": "evil.example/skill", "source": "evil.example", "skillId": "evil", "installs": 999999},
                ]
            }
        if url == f"{GITHUB_API}/repos/millionco/react-doctor":
            return {"default_branch": "main"}
        if url == f"{GITHUB_API}/repos/millionco/react-doctor/commits/main":
            return {"sha": _COMMIT}
        if url == f"{GITHUB_API}/repos/millionco/react-doctor/git/trees/{_COMMIT}?recursive=1":
            return {"sha": "b" * 40, "truncated": False, "tree": entries}
        raise AssertionError(f"unexpected URL {url}")

    def get_raw(url, *, max_bytes):
        parsed = urlparse(url)
        assert parsed.hostname == "raw.githubusercontent.com"
        parts = unquote(parsed.path).split("/")
        path = "/".join(parts[4:])
        content = files[path].encode()
        assert len(content) <= max_bytes
        return content

    return get_json, get_raw


def test_search_queries_public_catalog_and_filters_non_github_and_bad_names():
    get_json, get_raw = _github_client()
    hub = SkillsHub(get_json=get_json, get_raw=get_raw)
    found = hub.search("react accessibility")
    assert len(found) == 1
    assert found[0].install_ref == "millionco/react-doctor@react-a11y"
    assert found[0].installs == 1234
    assert found[0].url.startswith("https://skills.sh/")


def test_remote_inspection_pins_github_commit_and_builds_bounded_package():
    get_json, get_raw = _github_client()
    hub = SkillsHub(get_json=get_json, get_raw=get_raw)
    hub.search("react accessibility")
    package = hub.inspect("millionco/react-doctor@react-a11y")

    assert package.commit == _COMMIT
    assert package.skill_path == ".agents/skills/react-a11y"
    assert set(package.files) == {"SKILL.md", "references/checklist.md"}
    assert package.digest and len(package.digest) == 64
    assert package.installs == 1234
    assert any("hidden file" in warning for warning in package.warnings)
    assert any("symlink" in warning for warning in package.warnings)
    assert "source_commit" not in package.to_dict()


def test_remote_inspection_refuses_ambiguous_skill_directory():
    get_json, get_raw = _github_client(include_bad=True)
    hub = SkillsHub(get_json=get_json, get_raw=get_raw)
    with pytest.raises(ValueError, match="ambiguous"):
        hub.inspect("millionco/react-doctor@react-a11y")


def test_hub_install_never_writes_demo_placeholder_packages():
    get_json, get_raw = _github_client()
    hub = SkillsHub(get_json=get_json, get_raw=get_raw)
    ok, message = hub.install("cartographer")
    assert not ok
    assert "global demo-catalog installation is disabled" in message


def test_hub_audit_still_rejects_dangerous_code():
    result = SkillASTAuditor().audit_code("import subprocess\nsubprocess.run(['curl', 'https://evil.example'])")
    assert not result.is_safe
    assert any("subprocess" in violation for violation in result.violations)


def test_skills_hub_tool_search_returns_untrusted_remote_results(monkeypatch):
    get_json, get_raw = _github_client()
    hub = SkillsHub(get_json=get_json, get_raw=get_raw)
    monkeypatch.setattr("alpha.tools.builtins.skills_hub_tool.get_skills_hub", lambda: hub)
    result = json.loads(skills_hub_manage.invoke({"action": "search", "query_or_name": "react accessibility"}))
    assert result["untrusted_metadata"] is True
    assert result["popularity_is_not_quality_or_safety"] is True
    assert result["results"][0]["install_ref"] == "millionco/react-doctor@react-a11y"


def test_skills_hub_tool_audit_action():
    result = json.loads(skills_hub_manage.invoke({"action": "audit", "query_or_name": "import os\nos.system('dir')"}))
    assert result["is_safe"] is False
    assert result["violations"]


def test_tool_inspect_requires_digest_before_install(monkeypatch):
    get_json, get_raw = _github_client()
    hub = SkillsHub(get_json=get_json, get_raw=get_raw)
    monkeypatch.setattr("alpha.tools.builtins.skills_hub_tool.get_skills_hub", lambda: hub)
    hub.search("react accessibility")
    package = hub.inspect("millionco/react-doctor@react-a11y")
    result = skills_hub_manage.invoke({"action": "install", "query_or_name": package.install_ref})
    parsed = json.loads(result)
    assert parsed["error"] == "digest_required"
    assert "Inspect first" in parsed["message"]


def test_tool_install_rechecks_digest_and_uses_caller_scoped_archive_scanner(monkeypatch):
    get_json, get_raw = _github_client(include_skipped=False)
    hub = SkillsHub(get_json=get_json, get_raw=get_raw)
    hub.search("react accessibility")
    package = hub.inspect("millionco/react-doctor@react-a11y")
    installed_for = []

    class Storage:
        def install_skill_from_archive(self, archive_path):
            import zipfile

            installed_for.append("user-7")
            with zipfile.ZipFile(archive_path) as archive:
                assert "SKILL.md" in archive.namelist()
            return {"skill_name": "react-a11y"}

    monkeypatch.setattr("alpha.tools.builtins.skills_hub_tool.get_skills_hub", lambda: hub)
    monkeypatch.setattr("alpha.tools.builtins.skills_hub_tool.resolve_runtime_user_id", lambda runtime: "user-7")
    monkeypatch.setattr("alpha.tools.builtins.skills_hub_tool.get_or_new_user_skill_storage", lambda user_id: Storage())
    recorded = []
    monkeypatch.setattr("alpha.tools.builtins.skills_hub_tool._write_source_record", lambda user_id, pkg, name: recorded.append((user_id, pkg.digest, name)))

    result = json.loads(
        skills_hub_manage.func(
            action="install",
            query_or_name=package.install_ref,
            expected_digest=package.digest,
            runtime=object(),
        )
    )
    assert result["success"] is True
    assert result["skill_name"] == "react-a11y"
    assert installed_for == ["user-7"]
    assert recorded == [("user-7", package.digest, "react-a11y")]


def test_skill_package_ref_rejects_url_and_path_injection():
    get_json, get_raw = _github_client()
    hub = SkillsHub(get_json=get_json, get_raw=get_raw)
    with pytest.raises(ValueError, match="install reference"):
        hub.inspect("https://attacker.example/repo@react-a11y")


def test_skill_package_archive_rejects_traversal_path():
    package = SkillPackage(name="x", description="x", files={"SKILL.md": "ok", "../evil.md": "bad"})
    with pytest.raises(ValueError, match="unsafe relative package path"):
        SkillsHub.archive(package)
