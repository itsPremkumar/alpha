"""Preview-only quality council; trusted signing and deployment are unavailable."""

from __future__ import annotations

import hashlib
import logging
from typing import Literal

from agent_workspace.enterprise.models import CryptographicSignature, ReleaseCandidate

logger = logging.getLogger(__name__)


class PreviewReleaseCandidate(ReleaseCandidate):
    evidence_kind: Literal["unknown", "simulated"] = "unknown"
    preview_holdout_passed: bool = False
    deployed: Literal[False] = False


class QualityCouncilQuorumEngine:
    """Stages release proposals and exposes explicitly simulated holdout previews."""

    def __init__(self):
        self._releases: dict[str, PreviewReleaseCandidate] = {}

    def stage_candidate_release(
        self,
        version: str,
        component: str,
        description: str,
        diff_content: str,
    ) -> ReleaseCandidate:
        release_id = f"rel-{version.replace('.', '-')}"
        diff_hash = hashlib.sha256(diff_content.encode("utf-8")).hexdigest()
        candidate = PreviewReleaseCandidate(
            release_id=release_id,
            version=version,
            component=component,
            description=description,
            diff_hash=diff_hash,
        )
        self._releases[candidate.release_id] = candidate
        logger.info("Staged new release candidate: %s (%s)", release_id, version)
        return candidate

    def run_holdout_benchmark(self, release_id: str) -> float:
        candidate = self._releases.get(release_id)
        if candidate is None:
            raise KeyError(f"Release candidate '{release_id}' not found.")
        score = 96.4
        candidate.holdout_benchmark_score = score
        candidate.evidence_kind = "simulated"
        candidate.preview_holdout_passed = score >= 90.0
        candidate.holdout_passed = False
        candidate.signatures = []
        candidate.architecture_approved = False
        candidate.security_scan_passed = False
        self._check_and_update_quorum(candidate)
        return score

    def sign_release(
        self,
        release_id: str,
        role: str,
        bot_name: str,
    ) -> CryptographicSignature:
        if release_id not in self._releases:
            raise KeyError(f"Release candidate '{release_id}' not found.")
        valid_roles = {"CTO_ARCH", "CISO_ASTRA", "SWE_BENCHMARK"}
        if role not in valid_roles:
            raise ValueError(f"Invalid signatory role '{role}'. Must be one of {sorted(valid_roles)}")
        raise PermissionError("No trusted signing authority is configured; simulated evidence cannot authorize release signoff.")

    def _check_and_update_quorum(self, candidate: ReleaseCandidate) -> bool:
        candidate.status = "preview" if getattr(candidate, "evidence_kind", "unknown") == "simulated" else "staged"
        candidate.promoted_at = None
        return False

    def promote_release_zero_downtime(self, release_id: str) -> ReleaseCandidate:
        candidate = self._releases.get(release_id)
        if candidate is None:
            raise KeyError(f"Release candidate '{release_id}' not found.")
        self._check_and_update_quorum(candidate)
        raise PermissionError("Cannot promote release: verified evidence, trusted signing authority, and a deployment adapter are required.")

    def get_release(self, release_id: str) -> ReleaseCandidate | None:
        return self._releases.get(release_id)

    def list_releases(self) -> list[ReleaseCandidate]:
        return sorted(self._releases.values(), key=lambda r: -r.created_at)

    def get_active_release(self, component: str = "enterprise-core") -> ReleaseCandidate | None:
        return None


_COUNCIL_ENGINE: QualityCouncilQuorumEngine | None = None


def get_council_quorum_engine() -> QualityCouncilQuorumEngine:
    global _COUNCIL_ENGINE
    if _COUNCIL_ENGINE is None:
        _COUNCIL_ENGINE = QualityCouncilQuorumEngine()
    return _COUNCIL_ENGINE
