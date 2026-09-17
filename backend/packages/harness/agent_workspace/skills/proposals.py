"""Agent-proposed skill queue with human approval (Hermes /learn + Workshop).

An agent (or user, via the API) proposes a skill as SKILL.md markdown. The
proposal is statically scanned BEFORE anything is stored: blockers fail
closed without persisting. An admin reviews findings and approves
(materializes + installs into the proposer's custom skills) or rejects
with a reason.

Proposals live as JSON files in one shared directory; every read filters on
``created_by`` and ids are unguessable, so there is no cross-user oracle.
The directory sits OUTSIDE skill discovery roots, so unreviewed content is
never discovered, activated, or scanned as a skill. Writes are atomic
(tmp + rename) behind a process lock.

Secrets note: proposal bodies are agent-supplied markdown. They are scanned
before storage, served back verbatim only to the owning user and admins,
and never executed, activated, or interpolated into prompts.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
INSTALLED = "installed"
PROPOSAL_STATUSES = (PENDING, APPROVED, REJECTED, INSTALLED)

MAX_SKILL_MD_CHARS = 65536
MAX_DESCRIPTION_CHARS = 500
MAX_REJECT_REASON_CHARS = 2000

_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def validate_proposal_name(name: str) -> str:
    """Validate a proposed skill name (lowercase hyphenated, Hermes-compatible)."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError("Skill name must be 1-64 chars of lowercase letters, digits, or hyphens (e.g. 'pdf-tables').")
    return name


def validate_proposal_content(skill_md: str, description: str) -> tuple[str, str]:
    """Validate proposal body bounds."""
    if not isinstance(skill_md, str) or not skill_md.strip():
        raise ValueError("Skill content (SKILL.md markdown) must be a non-empty string.")
    if len(skill_md) > MAX_SKILL_MD_CHARS:
        raise ValueError(f"Skill content exceeds the {MAX_SKILL_MD_CHARS}-char proposal cap; split references out (multi-file proposals are a follow-up).")
    if not isinstance(description, str):
        raise ValueError("Description must be a string.")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f"Description exceeds the {MAX_DESCRIPTION_CHARS}-char cap.")
    return skill_md, description


@dataclass
class SkillProposal:
    """A proposed skill awaiting human review."""

    id: str
    name: str
    description: str
    skill_md: str
    status: str = PENDING
    created_by: str = ""
    created_at: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    reject_reason: str | None = None
    installed_skill: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillProposal:
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            description=str(data.get("description", "")),
            skill_md=str(data.get("skill_md", "")),
            status=str(data.get("status", PENDING)),
            created_by=str(data.get("created_by", "")),
            created_at=str(data.get("created_at", "")),
            findings=list(data.get("findings") or []),
            reviewed_by=data.get("reviewed_by"),
            reviewed_at=data.get("reviewed_at"),
            reject_reason=data.get("reject_reason"),
            installed_skill=data.get("installed_skill"),
        )


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class SkillProposalStore:
    """File-backed proposal queue in one shared directory."""

    def __init__(self, root: str | Path):
        self._root = Path(root)
        self._lock = threading.Lock()

    def _path_for(self, proposal_id: str) -> Path:
        if not _ID_RE.match(proposal_id or ""):
            raise ValueError(f"Unknown skill proposal '{proposal_id}'.")
        return self._root / f"{proposal_id}.json"

    def _write(self, proposal: SkillProposal) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._path_for(proposal.id)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._root), prefix=".proposal-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(proposal.to_dict(), handle, ensure_ascii=False)
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    @staticmethod
    def _read_file(path: Path) -> SkillProposal | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.debug("Skipping unreadable proposal %s: %s", path.name, exc)
            return None
        try:
            return SkillProposal.from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping malformed proposal %s: %s", path.name, exc)
            return None

    def create(
        self,
        user_id: str,
        name: str,
        description: str,
        skill_md: str,
        findings: list[dict[str, Any]] | None = None,
    ) -> SkillProposal:
        validate_proposal_name(name)
        skill_md, description = validate_proposal_content(skill_md, description)
        proposal = SkillProposal(
            id=uuid.uuid4().hex,
            name=name,
            description=description,
            skill_md=skill_md,
            status=PENDING,
            created_by=user_id,
            created_at=_utcnow(),
            findings=[dict(finding) for finding in (findings or [])],
        )
        with self._lock:
            self._write(proposal)
        return proposal

    def get(self, user_id: str, proposal_id: str) -> SkillProposal | None:
        """Read one owned proposal; anything else returns None (no oracle)."""
        try:
            path = self._path_for(proposal_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        proposal = self._read_file(path)
        if proposal is None or proposal.created_by != user_id:
            return None
        return proposal

    def get_any(self, proposal_id: str) -> SkillProposal | None:
        """Read any proposal by id (admin-only callers)."""
        try:
            path = self._path_for(proposal_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        return self._read_file(path)

    def _iter_all(self, cap: int = 5000) -> list[SkillProposal]:
        if not self._root.is_dir():
            return []
        proposals = []
        for path in sorted(self._root.glob("*.json"), reverse=True)[:cap]:
            proposal = self._read_file(path)
            if proposal is not None:
                proposals.append(proposal)
        return proposals

    def list(self, user_id: str, status: str | None = None) -> list[SkillProposal]:
        """Newest-first proposals owned by the user, optionally filtered."""
        proposals = [proposal for proposal in self._iter_all() if proposal.created_by == user_id and (status is None or proposal.status == status)]
        # id tiebreak: coarse clock granularity can stamp identical times.
        proposals.sort(key=lambda proposal: (proposal.created_at, proposal.id), reverse=True)
        return proposals

    def list_all(self, status: str | None = None) -> list[SkillProposal]:
        """Newest-first proposals across users (admin-only callers)."""
        proposals = [proposal for proposal in self._iter_all() if status is None or proposal.status == status]
        proposals.sort(key=lambda proposal: (proposal.created_at, proposal.id), reverse=True)
        return proposals

    def _transition(
        self,
        proposal: SkillProposal,
        status: str,
        *,
        reviewed_by: str | None = None,
        reject_reason: str | None = None,
        installed_skill: str | None = None,
    ) -> SkillProposal:
        allowed = {
            PENDING: (APPROVED, REJECTED),
            APPROVED: (INSTALLED,),
        }.get(proposal.status, ())
        if status not in allowed:
            raise ValueError(f"Cannot move skill proposal '{proposal.id}' from {proposal.status} to {status}.")
        if status == REJECTED:
            reason = (reject_reason or "").strip()
            if len(reason) > MAX_REJECT_REASON_CHARS:
                raise ValueError(f"Reject reason exceeds the {MAX_REJECT_REASON_CHARS}-char cap.")
            proposal.reject_reason = reason or None
        if status == INSTALLED:
            proposal.installed_skill = installed_skill or proposal.name
        proposal.status = status
        proposal.reviewed_by = reviewed_by
        proposal.reviewed_at = _utcnow()
        self._write(proposal)
        return proposal

    def set_status(
        self,
        user_id: str,
        proposal_id: str,
        status: str,
        *,
        reviewed_by: str | None = None,
        reject_reason: str | None = None,
        installed_skill: str | None = None,
    ) -> SkillProposal | None:
        """Owner-scoped transition; None when missing/foreign (no oracle)."""
        with self._lock:
            proposal = self.get(user_id, proposal_id)
            if proposal is None:
                return None
            return self._transition(
                proposal,
                status,
                reviewed_by=reviewed_by,
                reject_reason=reject_reason,
                installed_skill=installed_skill,
            )

    def set_status_any(
        self,
        proposal_id: str,
        status: str,
        *,
        reviewed_by: str | None = None,
        reject_reason: str | None = None,
        installed_skill: str | None = None,
    ) -> SkillProposal | None:
        """Unscoped transition for admin flows (approve/reject any proposal)."""
        with self._lock:
            proposal = self.get_any(proposal_id)
            if proposal is None:
                return None
            return self._transition(
                proposal,
                status,
                reviewed_by=reviewed_by,
                reject_reason=reject_reason,
                installed_skill=installed_skill,
            )


def scan_proposal_markdown(
    skill_name: str,
    content: str,
    *,
    app_config: Any | None = None,
) -> list[dict[str, Any]]:
    """Static-scan proposal markdown; returns non-blocking findings.

    Raises the scanner's blocked error for blocker findings (callers convert
    to tool/HTTP errors); scanner infrastructure failures propagate.
    Synchronous local analysis — async callers offload with asyncio.to_thread.
    """
    import tempfile

    from agent_workspace.skills.skillscan.orchestrator import enforce_static_scan

    with tempfile.TemporaryDirectory(prefix="deerflow-proposal-scan-") as tmp:
        skill_dir = Path(tmp) / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        return enforce_static_scan(skill_dir, skill_name=skill_name, app_config=app_config)


def proposals_root() -> Path:
    """Shared proposal directory (owner filtering lives in the records)."""
    from agent_workspace.config.paths import Paths

    return Paths().base_dir / "skill_proposals"


def finding_summary(findings: list[dict[str, Any]]) -> dict[str, int]:
    """Count findings by severity for list views (serverities vary by scanner)."""
    summary: dict[str, int] = {}
    for finding in findings or []:
        severity = str(finding.get("severity", "info")).lower()
        summary[severity] = summary.get(severity, 0) + 1
    return summary
