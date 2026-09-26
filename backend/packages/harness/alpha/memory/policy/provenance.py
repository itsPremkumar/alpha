"""Append-only, per-day provenance for memory-admission decisions.

Only identifiers, hashes, policy metadata, and scope strings are persisted;
candidate content is never copied into this audit log.  The engine's pure
``evaluate`` method performs no I/O, while ``evaluate_and_record`` makes the
audit step explicit and reports write failures instead of pretending success.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.agents.memory.l1.paths import l1_root, safe_segment

from .models import AdmissionCandidate, AdmissionDecision


@dataclass(frozen=True, slots=True)
class ProvenanceResult:
    """Outcome of one append attempt."""

    ok: bool
    path: Path | None
    error: str = ""


def policy_root(storage_path: str | None = None) -> Path:
    """Resolve policy state using Alpha's existing runtime-home helper."""

    return l1_root(storage_path)


def provenance_log_path(root: Path, user_id: str | None, day: str) -> Path:
    """Return one user's append-only admission provenance JSONL path."""

    return root / "users" / safe_segment(user_id or "default") / "memory-policy" / "provenance" / f"{day}.jsonl"


def candidate_digest(candidate: AdmissionCandidate) -> str:
    """Hash stable candidate identity and content without logging the content."""

    payload = json.dumps(
        {
            "candidate_id": candidate.candidate_id,
            "content": candidate.content,
            "types": list(candidate.types),
            "subtype": candidate.subtype,
            "source": candidate.source,
            "provenance": candidate.provenance,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append_decision(
    candidate: AdmissionCandidate,
    decision: AdmissionDecision,
    *,
    storage_path: str | None = None,
    now: float | None = None,
) -> ProvenanceResult:
    """Append one decision as JSONL and return an honest success/failure result."""

    try:
        timestamp = datetime.now(tz=UTC) if now is None else datetime.fromtimestamp(float(now), tz=UTC)
        digest = candidate_digest(candidate)
        entry: dict[str, Any] = {
            "ts": timestamp.isoformat(),
            "candidate_id": candidate.candidate_id or digest,
            "candidate_hash": digest,
            "action": decision.action,
            "admit": decision.admit,
            "score": decision.score,
            "rule_id": decision.rule_id,
            "reason": decision.reason,
            "tier": decision.tier,
            "ttl_seconds": decision.ttl_seconds,
            "score_breakdown": dict(decision.score_breakdown),
            "missing_signals": list(decision.missing_signals),
            "user_id": candidate.user_id,
            "agent_id": candidate.agent_id,
            "project_id": candidate.project_id,
            "team_id": candidate.team_id,
            "task_id": candidate.task_id,
            "session_id": candidate.session_id,
            "source": candidate.source,
            "types": list(candidate.types),
        }
        path = provenance_log_path(policy_root(storage_path), candidate.user_id, timestamp.strftime("%Y-%m-%d"))
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        return ProvenanceResult(True, path)
    except (OSError, TypeError, ValueError, OverflowError) as exc:
        return ProvenanceResult(False, None, str(exc)[:1000])


def read_entries(
    day: str,
    *,
    user_id: str | None = None,
    storage_path: str | None = None,
) -> list[dict[str, Any]]:
    """Read valid JSON objects from one day, preserving append order."""

    path = provenance_log_path(policy_root(storage_path), user_id, day)
    try:
        if not path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                entries.append(item)
        return entries
    except OSError:
        return []


__all__ = [
    "ProvenanceResult",
    "append_decision",
    "candidate_digest",
    "policy_root",
    "provenance_log_path",
    "read_entries",
]
