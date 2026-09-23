"""ExperienceStore: Persistent store for episodic experience records.

Storage rules (honesty contract):
- ``add()`` is the evidence-gated intake: a record must carry a non-empty
  ``evidence`` reference list (trace ids / observed outcomes). Without it the
  store refuses and returns ``{"stored": False, "reason": "..."}`` — it never
  silently accepts and never claims success on refusal.
- ``record()`` is the legacy put kept for pre-existing callers. It refuses
  secret/PII-shaped content for every kind, and it also enforces the evidence
  rule for the new FACT/TIP kinds. Legacy EPISODE records may still be stored
  without evidence so existing callers keep working (their behaviour is pinned
  by ``tests/test_experience_memory.py`` and
  ``tests/test_reproduction_full_integration.py``).
- Secret/PII screening is a small pattern-only denylist of obvious shapes; it
  contains no real credentials and never echoes the matched content back in the
  rejection reason.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from alpha.learning.experience.models import ExperienceKind, ExperienceRecord, OutcomeType

logger = logging.getLogger(__name__)

StoreResult = dict[str, Any]

# Pattern-only denylist of obvious secret/PII shapes (no real credentials are
# stored here, and the matched text is never echoed back to callers).
SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key ID shape"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36}"), "GitHub token shape"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token shape"),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}"), "OpenAI-style API key shape"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "PEM private key block"),
    (
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
        "JWT-shaped token",
    ),
    (
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|"
            r"password|passwd|pwd|secret)\b\s*[:=]\s*['\"][^'\"]{6,}['\"]"
        ),
        "credential assignment with quoted value",
    ),
    (
        re.compile(r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key)\b\s*[:=]\s*[^\s'\"]{8,}"),
        "credential assignment with bare value",
    ),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "email-address (PII) shape"),
]

EVIDENCE_REASON = "missing evidence: a non-empty evidence reference list (trace ids / observed outcomes) is required before this record can be stored"


def find_secret_shape(text: str) -> str | None:
    """Return the denylist label matching ``text``, or None when nothing matches."""
    for pattern, label in SECRET_PATTERNS:
        if pattern.search(text):
            return label
    return None


def record_text(record: ExperienceRecord) -> str:
    """All human-readable content of a record, screened before storage."""
    parts = [
        record.task_goal,
        record.statement,
        *record.lessons_learned,
        *record.pitfalls_to_avoid,
        *record.tags,
        *record.error_types,
        *record.modified_files,
        json.dumps(record.metadata, default=str, ensure_ascii=False),
    ]
    return "\n".join(parts)


def refusal_reason(record: ExperienceRecord, *, require_evidence: bool) -> str | None:
    """Honest refusal reason for storing ``record``, or None when it may be stored."""
    secret_label = find_secret_shape(record_text(record))
    if secret_label:
        return f"refused: content matches a secret/PII denylist pattern ({secret_label}); experience banks must not store credentials or personal data"
    if require_evidence and not any(str(ref).strip() for ref in record.evidence):
        return EVIDENCE_REASON
    return None


class ExperienceStore:
    """Persistent storage repository managing episodic experience memory records."""

    def __init__(self, storage_path: str | Path | None = None, load_defaults: bool = True):
        self.storage_path = Path(storage_path) if storage_path else None
        self._records: dict[str, ExperienceRecord] = {}

        if self.storage_path and self.storage_path.exists():
            self._load_from_disk()
        elif load_defaults:
            self._load_default_experiences()

    def record(self, record: ExperienceRecord) -> StoreResult:
        """Add or update an experience record and flush to disk if path configured.

        Legacy-compatible put: always refuses secret/PII-shaped content, and for
        FACT/TIP kinds additionally requires an evidence reference list.
        EPISODE records may still be stored without evidence (backward
        compatibility pinned by existing tests). Returns a ``StoreResult`` —
        ``{"stored": False, "reason": ...}`` on refusal, never a silent accept.
        """
        reason = refusal_reason(record, require_evidence=record.kind != ExperienceKind.EPISODE)
        if reason is not None:
            logger.info("experience record %s refused: %s", record.experience_id, reason)
            return {"stored": False, "reason": reason}
        return self._put(record)

    def add(self, record: ExperienceRecord) -> StoreResult:
        """Evidence-gated intake for the FACT/TIP bank (and any new record).

        Every kind must cite a non-empty ``evidence`` list and pass the
        secret/PII denylist, otherwise the store refuses with an honest
        ``{"stored": False, "reason": ...}`` result.
        """
        reason = refusal_reason(record, require_evidence=True)
        if reason is not None:
            logger.info("experience record %s refused: %s", record.experience_id, reason)
            return {"stored": False, "reason": reason}
        return self._put(record)

    def _put(self, record: ExperienceRecord) -> StoreResult:
        self._records[record.experience_id] = record
        if self.storage_path:
            self._save_to_disk()
        return {
            "stored": True,
            "experience_id": record.experience_id,
            "kind": record.kind.value,
            "confidence": record.confidence,
        }

    def get(self, experience_id: str) -> ExperienceRecord | None:
        return self._records.get(experience_id)

    def list_all(self) -> list[ExperienceRecord]:
        return list(self._records.values())

    def remove(self, experience_id: str) -> bool:
        """Remove a record (used by experience hygiene); True when one was removed."""
        removed = self._records.pop(experience_id, None) is not None
        if removed and self.storage_path:
            self._save_to_disk()
        return removed

    def clear(self) -> None:
        self._records.clear()
        if self.storage_path and self.storage_path.exists():
            try:
                os.remove(self.storage_path)
            except OSError:
                pass

    def _save_to_disk(self) -> None:
        if not self.storage_path:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in self._records.values()]
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _load_from_disk(self) -> None:
        if not self.storage_path or not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    rec = ExperienceRecord.from_dict(item)
                    self._records[rec.experience_id] = rec
        except Exception as e:
            logger.warning(f"Failed to load experience store from {self.storage_path}: {e}")

    def _load_default_experiences(self) -> None:
        """Seed high-frequency enterprise SWE experience patterns."""
        self.record(
            ExperienceRecord(
                experience_id="exp_async_deadlock",
                task_goal="Refactor worker queues with asyncio and sync database client",
                outcome=OutcomeType.FAILURE,
                error_types=["RuntimeError", "EventLoopBlocked"],
                tags=["asyncio", "database", "deadlock"],
                lessons_learned=[
                    "Never invoke synchronous blocking DB calls inside async event loops; wrap with asyncio.to_thread.",
                    "Always set connect and query timeouts on database connection pools.",
                ],
                pitfalls_to_avoid=[
                    "Calling db.execute() synchronously within async FastAPI endpoints causes total server freeze.",
                ],
            )
        )
        self.record(
            ExperienceRecord(
                experience_id="exp_clean_patch_empty",
                task_goal="Fix bug in calculation module",
                outcome=OutcomeType.FAILURE,
                error_types=["EmptyPatchError", "CriticRejection"],
                tags=["patch", "critic", "verification"],
                lessons_learned=[
                    "Always run git diff --stat or git status --porcelain to confirm edits are committed to disk before finishing.",
                ],
                pitfalls_to_avoid=[
                    "Claiming task complete when only viewing files without writing changes.",
                ],
            )
        )
