"""Tests for the agent skill-proposal queue (H4)."""

import json
from types import SimpleNamespace

import pytest

from alpha.skills.proposals import (
    APPROVED,
    INSTALLED,
    MAX_SKILL_MD_CHARS,
    PENDING,
    REJECTED,
    SkillProposalStore,
    finding_summary,
    scan_proposal_markdown,
    validate_proposal_content,
    validate_proposal_name,
)

pytestmark = pytest.mark.anyio

CLEAN_MD = "---\nname: demo-skill\n---\n# Demo Skill\nDoes useful things safely.\n"
EVIL_KEY_MD = "---\nname: evil-skill\n---\n# Evil\n-----BEGIN PRIVATE KEY-----\nMIIEvwIBADANBg==\n-----END PRIVATE KEY-----\n"


def _store(tmp_path) -> SkillProposalStore:
    return SkillProposalStore(tmp_path / "proposals")


# ---------------------------------------------------------------------------
# Validation + scan gate
# ---------------------------------------------------------------------------


def test_name_validation():
    assert validate_proposal_name("pdf-tables") == "pdf-tables"
    for bad in ["", "UPPER", "has space", "under_score", "a" * 65, "semi;colon", "../escape"]:
        with pytest.raises(ValueError, match="[Nn]ame"):
            validate_proposal_name(bad)


def test_content_bounds():
    with pytest.raises(ValueError, match="non-empty"):
        validate_proposal_content("   ", "d")
    with pytest.raises(ValueError, match="exceeds"):
        validate_proposal_content("x" * (MAX_SKILL_MD_CHARS + 1), "d")
    with pytest.raises(ValueError, match="Description exceeds"):
        validate_proposal_content("ok", "d" * 501)


def test_clean_markdown_scans_without_findings():
    assert scan_proposal_markdown("demo-skill", CLEAN_MD) == []


def test_private_key_markdown_is_blocked():
    from alpha.skills.skillscan.orchestrator import StaticScanBlockedError

    with pytest.raises(StaticScanBlockedError) as exc_info:
        scan_proposal_markdown("evil-skill", EVIL_KEY_MD)
    assert exc_info.value.findings
    assert all(f["severity"] == "CRITICAL" for f in exc_info.value.findings)


def test_finding_summary_buckets():
    findings = [
        {"severity": "CRITICAL"},
        {"severity": "HIGH"},
        {"severity": "HIGH"},
        {"severity": "LOW"},
    ]
    assert finding_summary(findings) == {"critical": 1, "high": 2, "low": 1}


# ---------------------------------------------------------------------------
# Store lifecycle
# ---------------------------------------------------------------------------


def test_create_get_list_round_trip(tmp_path):
    store = _store(tmp_path)
    created = store.create("alice", "demo-skill", "Does things", CLEAN_MD, [{"severity": "LOW"}])
    assert created.status == PENDING
    assert created.created_by == "alice"
    fetched = store.get("alice", created.id)
    assert fetched is not None and fetched.name == "demo-skill"
    assert fetched.findings == [{"severity": "LOW"}]
    listed = store.list("alice")
    assert [p.id for p in listed] == [created.id]


def test_owner_isolation_has_no_oracle(tmp_path):
    store = _store(tmp_path)
    created = store.create("alice", "demo-skill", "d", CLEAN_MD)
    assert store.get("bob", created.id) is None
    assert store.list("bob") == []
    assert store.set_status("bob", created.id, REJECTED) is None
    assert store.get("alice", "not-hex-at-all") is None
    assert store.get("alice", "0" * 32) is None


def test_status_transitions_are_guarded(tmp_path):
    store = _store(tmp_path)
    created = store.create("alice", "demo-skill", "d", CLEAN_MD)
    with pytest.raises(ValueError, match="Cannot move"):
        store.set_status("alice", created.id, INSTALLED)
    approved = store.set_status("alice", created.id, APPROVED, reviewed_by="admin")
    assert approved is not None and approved.status == APPROVED
    assert approved.reviewed_by == "admin" and approved.reviewed_at
    with pytest.raises(ValueError, match="Cannot move"):
        store.set_status("alice", created.id, REJECTED)
    installed = store.set_status("alice", created.id, INSTALLED, reviewed_by="admin", installed_skill="demo-skill")
    assert installed is not None and installed.status == INSTALLED
    assert installed.installed_skill == "demo-skill"
    fresh = store.create("alice", "other-skill", "d", CLEAN_MD)
    denied = store.set_status("alice", fresh.id, REJECTED, reviewed_by="admin", reject_reason="nope")
    assert denied is not None and denied.status == REJECTED
    assert denied.reject_reason == "nope"
    with pytest.raises(ValueError, match="Reject reason exceeds"):
        store.set_status("alice", store.create("alice", "third-skill", "d", CLEAN_MD).id, REJECTED, reject_reason="x" * 2001)


def test_list_status_filter_and_newest_first(tmp_path, monkeypatch):
    from alpha.skills import proposals as proposals_module

    stamps = (f"2026-01-01T00:00:{second:02d}+00:00" for second in range(60))
    monkeypatch.setattr(proposals_module, "_utcnow", lambda: next(stamps))
    store = _store(tmp_path)
    first = store.create("alice", "aaa-skill", "d", CLEAN_MD)
    second = store.create("alice", "bbb-skill", "d", CLEAN_MD)
    store.set_status("alice", first.id, REJECTED, reviewed_by="admin")
    assert [p.id for p in store.list("alice")] == [second.id, first.id]
    assert [p.id for p in store.list("alice", status=REJECTED)] == [first.id]
    assert store.list("alice", status=APPROVED) == []


def test_malformed_files_are_skipped(tmp_path):
    root = tmp_path / "proposals"
    root.mkdir()
    (root / "broken.json").write_text("{not json", encoding="utf-8")
    (root / f"{'ab' * 16}.json").write_text(json.dumps({"nope": True}), encoding="utf-8")
    store = SkillProposalStore(root)
    created = store.create("alice", "demo-skill", "d", CLEAN_MD)
    assert [p.id for p in store.list("alice")] == [created.id]


# ---------------------------------------------------------------------------
# Agent tool
# ---------------------------------------------------------------------------


def _tool_runtime(user_id="alice"):
    return SimpleNamespace(state={}, context={"user_id": user_id}, config={"metadata": {}})


async def _call_propose_tool(monkeypatch, tmp_path, **kwargs):
    # NOTE: import the tool object directly — `import ...propose_skill_tool
    # as x` would bind the package-level shadowing attribute instead.
    from alpha.tools.builtins.propose_skill_tool import propose_skill_tool as tool_obj

    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "home"))
    coroutine = getattr(tool_obj, "coroutine", None)
    if coroutine is not None:
        return await coroutine(**kwargs)
    return tool_obj.func(**kwargs)


async def test_tool_validates_before_scanning(monkeypatch, tmp_path):
    out = await _call_propose_tool(monkeypatch, tmp_path, runtime=_tool_runtime(), name="BAD NAME", skill_md="x")
    assert out.startswith("Error:")
    assert "lowercase" in out


async def test_tool_blocks_malicious_content_without_storing(monkeypatch, tmp_path):
    home = tmp_path / "home"
    out = await _call_propose_tool(monkeypatch, tmp_path, runtime=_tool_runtime(), name="evil-skill", skill_md=EVIL_KEY_MD)
    assert out.startswith("Error: skill proposal blocked")
    proposals_dir = home / "skill_proposals"
    assert not proposals_dir.exists() or list(proposals_dir.glob("*.json")) == []


async def test_tool_records_clean_proposal(monkeypatch, tmp_path):
    home = tmp_path / "home"
    out = await _call_propose_tool(
        monkeypatch,
        tmp_path,
        runtime=_tool_runtime("alice"),
        name="demo-skill",
        skill_md=CLEAN_MD,
        description="Does things",
    )
    assert "recorded" in out and "demo-skill" in out
    stored = list((home / "skill_proposals").glob("*.json"))
    assert len(stored) == 1
    data = json.loads(stored[0].read_text(encoding="utf-8"))
    assert data["created_by"] == "alice" and data["status"] == PENDING
