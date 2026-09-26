"""Skill authoring round-trip (UTF-8 bytes) + audit-blocks-unreviewed-activation.

Covers, against the REAL authoring stack:

- UTF-8 round-trip: a draft authored with non-ASCII content publishes through
  ``SkillWorkshopEngine.publish_skill`` (``encoding="utf-8"``) and the exact
  bytes decode back to the same text -- verified on raw bytes, not just text.
- Audit-before-activation: ``publish_skill``'s static security scan raises
  ``StaticScanBlockedError`` for CRITICAL content BEFORE any directory or file
  is created in the activation root, so blocked content never activates.
- ``review_skill_package`` is a READ-ONLY audit: it labels results
  ``review_subject_entry`` (never ``skill_context_entry``) and creates nothing
  in skill storage; only an explicit publish writes the package.
- The proposal queue cannot skip review: ``pending -> installed`` is an
  illegal transition, and the queue directory holds no ``SKILL.md`` package
  (nothing for discovery to find) until an admin approves and installs.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from alpha.skills.authoring import validate_skill_draft
from alpha.skills.proposals import SkillProposalStore
from alpha.skills.skillscan.orchestrator import StaticScanBlockedError
from alpha.skills.storage.local_skill_storage import LocalSkillStorage
from alpha.skills.workshop import SkillWorkshopEngine
from alpha.tools.builtins.review_skill_package_tool import review_skill_package

_NON_ASCII = "café — 東京 🚀"


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        state={},
        context={"thread_id": "thread-1", "user_id": "default"},
        config={"configurable": {"thread_id": "thread-1", "user_id": "default"}},
        tool_call_id="tool-1",
    )


def _utf8_draft():
    return SkillWorkshopEngine.distill_from_trace(
        name="utf8-roundtrip-skill",
        description="Round trips authored skill bytes as UTF-8.",
        trace_steps=[{"tool": "exec", "action": f"Echo {_NON_ASCII}", "command": f"echo '{_NON_ASCII}'"}],
        when_to_use=[f"When repeating the {_NON_ASCII} report."],
        verification_cmd="echo ok",
    )


# ---------------------------------------------------------------------------
# UTF-8 round-trip through publish
# ---------------------------------------------------------------------------


def test_publish_round_trip_preserves_utf8_bytes(tmp_path):
    draft = _utf8_draft()
    assert draft.is_valid is True
    assert draft.findings == []
    assert validate_skill_draft(draft.name, draft.description, draft.markdown_content) == []
    assert _NON_ASCII in draft.markdown_content  # non-ASCII survives distillation

    target = SkillWorkshopEngine.publish_skill(draft, custom_skills_dir=tmp_path, overwrite=False)
    assert target == tmp_path / draft.name / "SKILL.md"

    raw = target.read_bytes()
    decoded = raw.decode("utf-8")  # file is valid UTF-8 (encoding="utf-8" write)
    assert _NON_ASCII in decoded
    assert _NON_ASCII.encode("utf-8") in raw  # the non-ASCII bytes themselves round-trip
    # Text-mode read equals the byte decode once newlines are normalized:
    # ``write_text`` emits CRLF via ``os.linesep`` on Windows (pre-existing
    # newline behavior, unrelated to encoding); the UTF-8 payload is identical.
    assert decoded.replace("\r\n", "\n") == target.read_text(encoding="utf-8")
    assert f"name: {draft.name}" in decoded
    assert "## Verification" in decoded


# ---------------------------------------------------------------------------
# Audit blocks unreviewed/blocked activation
# ---------------------------------------------------------------------------


def test_static_audit_blocks_content_before_activation(tmp_path):
    draft = SkillWorkshopEngine.distill_from_trace(
        name="blocked-activation-skill",
        description="Carries content the static audit blocks.",
        trace_steps=[{"tool": "exec", "action": "Write key", "command": "cat key.pem"}],
        verification_cmd="echo ok",
    )
    # Quality validator passes (it checks shape, not key material)...
    assert draft.is_valid is True
    # ...but the security audit must not: inject CRITICAL private-key content.
    draft.markdown_content += "\n-----BEGIN PRIVATE KEY-----\nMIIEvwIBADANBg==\n-----END PRIVATE KEY-----\n"

    target_root = tmp_path / "custom"
    with pytest.raises(StaticScanBlockedError) as exc_info:
        SkillWorkshopEngine.publish_skill(draft, custom_skills_dir=target_root, overwrite=False)
    assert "private key" in str(exc_info.value).lower()

    # Nothing landed in the activation root: no directory, no SKILL.md.
    assert not (target_root / draft.name).exists()
    assert not target_root.exists() or list(target_root.rglob("SKILL.md")) == []


def test_unreviewed_proposal_never_becomes_discoverable_skill(tmp_path):
    store_root = tmp_path / "skill_proposals"
    store = SkillProposalStore(store_root)
    proposal = store.create(
        "alice",
        "pending-skill",
        "Awaiting review.",
        "---\nname: pending-skill\n---\n# Pending\nDoes a thing.\n",
    )
    assert proposal.status == "pending"

    # The queue directory holds no skill package: discovery scans for SKILL.md,
    # so unreviewed content has nothing to find and cannot activate.
    assert list(store_root.rglob("SKILL.md")) == []

    # Review cannot be skipped: pending -> installed is an illegal transition.
    with pytest.raises(ValueError, match="from pending to installed"):
        store.set_status("alice", proposal.id, "installed")

    # Review first, then install is legal.
    approved = store.set_status("alice", proposal.id, "approved", reviewed_by="admin")
    assert approved is not None and approved.status == "approved"
    installed = store.set_status("alice", proposal.id, "installed", reviewed_by="admin")
    assert installed is not None and installed.status == "installed"
    assert installed.installed_skill == "pending-skill"


# ---------------------------------------------------------------------------
# review_skill_package is a read-only audit: it activates nothing
# ---------------------------------------------------------------------------


def _tree(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def test_review_audit_is_read_only_then_publish_activates(tmp_path, monkeypatch):
    draft = _utf8_draft()
    content = f"---\nname: {draft.name}\ndescription: {draft.description}\nversion: 0.1.0\n---\n\n{draft.markdown_content}\n"

    host = tmp_path / "skills_host"
    storage = LocalSkillStorage(host_path=str(host), container_path="/mnt/skills")
    monkeypatch.setattr(
        "alpha.tools.builtins.review_skill_package_tool.get_or_new_user_skill_storage",
        lambda user_id: storage,
    )

    # Audit FIRST (read-only): storage tree captured after construction, must
    # be byte-for-byte unchanged by the review.
    before = _tree(host)
    command = review_skill_package.func(target="inline://SKILL.md", inline_content=content, runtime=_runtime())
    message = command.update["messages"][0]
    payload = json.loads(message.content)

    assert payload["untrusted_review_data"] is True
    assert payload["facts"]["subject"]["declared_name"] == draft.name
    # Audit labels: review_subject_entry only -- never skill_context_entry,
    # which would mean the reviewed skill was activated/bound.
    assert "review_subject_entry" in message.additional_kwargs
    assert "skill_context_entry" not in message.additional_kwargs
    # Nothing was created or installed by the audit.
    assert _tree(host) == before

    # Activation happens only on the explicit publish, with identical bytes.
    saved = SkillWorkshopEngine.publish_skill(draft, custom_skills_dir=tmp_path / "custom", overwrite=False)
    assert saved.read_text(encoding="utf-8") == content
