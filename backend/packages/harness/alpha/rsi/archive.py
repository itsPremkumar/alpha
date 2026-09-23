"""Durable RSI candidate archive (WP-A1, plan §3, features #5/#22).

Writes ``runtime_home()/rsi/archive/<candidate_id>.json`` atomically (tmp +
``os.replace`` via ``alpha.evolution.identity.atomic_write_json``) so every
archived candidate — **including rejected ones** — keeps its exact failure
cause. Honesty contract (§5.6):

- ``failure_cause`` / ``failed_evaluator`` are stored **verbatim**
  (equality-pinned in ``tests/test_rsi_lineage.py``); nothing is inferred,
  softened, or invented.
- Bounded fields, following ``skills/evolution_engine.py`` conventions
  (``MAX_CANDIDATE_CHARS`` / ``MAX_EVIDENCE_REF_CHARS``): an over-cap payload
  or cause raises with the real numbers instead of being silently truncated
  (a truncated archive entry would lose honest history).
- ``candidate_id`` must match a safe filename pattern (no path separators) —
  archiving can never escape the archive directory.
- Reads degrade honestly: missing archive dir → ``[]`` (honest empty state);
  corrupt entries are skipped with a logged count, never repaired or guessed.
- A failed write raises the real OS error — returning an unwritten path
  would be a lie.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.identity import atomic_write_json
from alpha.rsi.lineage import LINEAGE_STATUSES

logger = logging.getLogger(__name__)

# Caps copied from `skills/evolution_engine.py` conventions (plan §3 WP-A1:
# "bounded fields … caps copied from skills/evolution_engine.py conventions").
# Duplicated as values — not imported — so this module stays import-light;
# keep the two in sync with
# `skills.evolution_engine.{MAX_CANDIDATE_CHARS, MAX_EVIDENCE_REF_CHARS}`.
MAX_ARCHIVE_PAYLOAD_CHARS = 65536  # == skills.evolution_engine.MAX_CANDIDATE_CHARS
MAX_ARCHIVE_TEXT_CHARS = 2000  # == skills.evolution_engine.MAX_EVIDENCE_REF_CHARS

ARCHIVE_DIR_NAME = "archive"
_ARCHIVE_DIR_NAME = "rsi"  # archive lives at runtime_home()/rsi/archive

#: Safe filename ids: no separators, no dot-only names — traversal-proof.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_REQUIRED_ENTRY_KEYS = ("candidate_id", "status", "payload", "failure_cause", "failed_evaluator", "archived_at")


def _archive_dir() -> Path:
    return runtime_home() / _ARCHIVE_DIR_NAME / ARCHIVE_DIR_NAME


def _valid_archive_entry(entry: Any) -> bool:
    """Strict on our own on-disk format; anything else is corrupt (skipped)."""
    if not isinstance(entry, dict) or not all(key in entry for key in _REQUIRED_ENTRY_KEYS):
        return False
    if not isinstance(entry["candidate_id"], str) or not entry["candidate_id"]:
        return False
    if entry["status"] not in LINEAGE_STATUSES:
        return False
    for key in ("failure_cause", "failed_evaluator"):
        value = entry[key]
        if value is not None and not isinstance(value, str):
            return False
    if isinstance(entry["archived_at"], bool) or not isinstance(entry["archived_at"], (int | float)):
        return False
    return True


def archive_candidate(candidate_id: str, *, payload: Any, status: str, failure_cause: str | None, failed_evaluator: str | None) -> Path:
    """Atomically archive one candidate entry and return the written path.

    ``failure_cause``/``failed_evaluator`` are recorded verbatim — an honest
    archive of a *rejected* candidate must retain the exact reason it failed.
    """
    if not isinstance(candidate_id, str) or not _SAFE_ID_RE.match(candidate_id):
        raise ValueError(f"Unsafe candidate_id {candidate_id!r}; archive file names must match {_SAFE_ID_RE.pattern} (no path separators).")
    if status not in LINEAGE_STATUSES:
        raise ValueError(f"status {status!r} is not one of the allowed lineage statuses {list(LINEAGE_STATUSES)}.")
    for name, value in (("failure_cause", failure_cause), ("failed_evaluator", failed_evaluator)):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{name} must be a string or None, got {type(value).__name__}.")
        if isinstance(value, str) and len(value) > MAX_ARCHIVE_TEXT_CHARS:
            raise ValueError(f"{name} is {len(value)} chars, over the {MAX_ARCHIVE_TEXT_CHARS}-char cap; refusing to truncate (a truncated failure record would lose honest history).")
    entry = {
        "candidate_id": candidate_id,
        "status": status,
        "payload": payload,
        "failure_cause": failure_cause,
        "failed_evaluator": failed_evaluator,
        "archived_at": time.time(),
    }
    try:
        serialized = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Archive entry for {candidate_id!r} is not JSON-serializable: {exc}") from exc
    if len(serialized) > MAX_ARCHIVE_PAYLOAD_CHARS:
        raise ValueError(f"Archive entry for {candidate_id!r} is {len(serialized)} chars, over the {MAX_ARCHIVE_PAYLOAD_CHARS}-char cap; refusing to truncate (a truncated archive entry would lose honest history).")
    path = _archive_dir() / f"{candidate_id}.json"
    atomic_write_json(path, entry)  # OSError propagates: the real write error, never a fake path
    return path


def list_archive(status: str | None = None) -> list[dict[str, Any]]:
    """List archived candidates (optionally filtered), chronologically ordered.

    Missing storage → honest ``[]``; corrupt/non-archive files are skipped
    with a logged count, never repaired or guessed.
    """
    if status is not None and status not in LINEAGE_STATUSES:
        raise ValueError(f"status {status!r} is not one of the allowed lineage statuses {list(LINEAGE_STATUSES)}.")
    archive_dir = _archive_dir()
    if not archive_dir.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    skipped = 0
    for path in sorted(archive_dir.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable archive entry %s: %s", path, exc)
            skipped += 1
            continue
        if not _valid_archive_entry(entry):
            skipped += 1
            continue
        if status is None or entry["status"] == status:
            entries.append(entry)
    if skipped:
        logger.warning("Skipped %d corrupt/non-archive file(s) in %s", skipped, archive_dir)
    entries.sort(key=lambda item: (item["archived_at"], item["candidate_id"]))
    return entries
