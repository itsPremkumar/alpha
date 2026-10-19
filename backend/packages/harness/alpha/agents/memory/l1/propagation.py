"""Staleness propagation into derived stores (Hy-Memory adoption slice #1).

When conflict detection removes a target record, derived stores that indexed
that record's content accumulate with no reversal pass — the StateMem-style
"state drift" failure mode: the derived artifact survives the supersession of
the fact it was extracted from. This module is the deterministic, LLM-free
MARKING pass (never a deletion): it walks the recorded mention lineage of every
confirmed-superseded record, names the dependent entities in a bounded,
id-only journal entry, and exposes the count for ``RunReport.stale_dependents``.

Lineage: Tencent Hy-Memory 2.0 staleness awareness (Apache-2.0, contract only —
no code copied; see ``docs/THIRD_PARTY_MEMORY_NOTICES.md``) and the Tencent
agent-memory "generation-log" journal shape Alpha already pins in ``l1/paths``.

Hard design rule: this module can never fail an L1 run. Every walk and every
journal write is wrapped: failures are logged and the run continues.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.agents.memory.l1.paths import l1_root, staleness_path

logger = logging.getLogger(__name__)

#: Dependent records marked per supersession batch — a hard ceiling; when the
#: walk hits it the caller discloses ``truncated=True`` rather than clamps in
#: silence.
DEPENDENT_LIMIT = 32


@dataclass(slots=True, frozen=True)
class StaleDependency:
    """One dependent derived-store record, identified by ids only.

    ``kind`` names the derived store (currently only ``entity``); ``id`` is the
    dependent record's id; ``record_id`` is the superseded record the lineage
    came from. Journal entries never carry record or entity CONTENT — the id
    alone lets a reviewer resolve the lineage, and content-free journals stay
    outside the recall surface.
    """

    kind: str
    id: str
    record_id: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id, "record_id": self.record_id}


def collect_entity_dependents(
    entity_memory: Any,
    *,
    user_id: str | None,
    superseded_ids: list[str],
) -> list[StaleDependency]:
    """Walk the recorded mention lineage of every superseded id.

    Read through ``EntityMemory.entities_for_record(record_id, user_id=...,
    include_unpromoted=True)`` — the same edge the entity capture pass writes,
    incluiding not-yet-promoted mentions so a fresh dependency cannot be
    missed because promotion has not caught up. Results are deduplicated,
    capped at ``DEPENDENT_LIMIT`` (in call order), and the whole walk is
    failure-isolated: an entity store that raises can never fail an L1 run.
    """

    if not superseded_ids or entity_memory is None:
        return []
    collected: list[StaleDependency] = []
    seen: set[tuple[str, str, str]] = set()

    def _extend(record_id: str, entities: Iterable[Any]) -> int:
        added = 0
        for entity in entities or ():
            if not entity:
                continue
            dependency = StaleDependency(kind="entity", id=str(entity), record_id=str(record_id))
            # One derived artifact is marked once, whichever superseded
            # record referenced it — the count is of dependents, not edges.
            key = (dependency.kind, dependency.id)
            if key in seen:
                continue
            seen.add(key)
            collected.append(dependency)
            added += 1
            if len(collected) >= DEPENDENT_LIMIT:
                break
        return added

    for record_id in superseded_ids:
        if len(collected) >= DEPENDENT_LIMIT:
            break
        try:
            entities = entity_memory.entities_for_record(str(record_id), user_id=user_id or "", include_unpromoted=True)
            _extend(str(record_id), entities)
        except Exception as exc:  # one bad record must not stop the walk
            logger.warning("staleness propagation: lineage walk failed for a record (%s)", type(exc).__name__)
            logger.debug("entity lineage exception", exc_info=exc)
    return collected[:DEPENDENT_LIMIT]


def staleness_journal_path(
    storage_path: str | Path | None,
    user_id: str | None = None,
    agent_name: str | None = None,
    *,
    now: float | None = None,
) -> Path:
    """Path of today's (or a fixed clock's) staleness journal for one user.

    ``{root}/users/{user}/l1/staleness/YYYY-MM-DD.jsonl`` — same per-user
    layout as the generation-log, in its own directory so a reviewer can sweep
    it without the write-ahead entries.
    """

    day = datetime.fromtimestamp(now if now is not None else time.time(), tz=UTC).strftime("%Y-%m-%d")
    return staleness_path(l1_root(storage_path), user_id, day)


def append_staleness_entry(
    *,
    user_id: str | None,
    superseded_record_ids: list[str],
    dependents: list[StaleDependency],
    storage_path: str,
    truncated: bool,
    agent_name: str | None = None,
    now: float | None = None,
) -> None:
    """Append one id-only staleness entry for the run — never raises.

    The journal is the observation channel for review, not a decision input:
    nothing in the L1 pipeline or recall path reads it back this release. A
    day file bounded by ``DEPENDENT_LIMIT`` per entry keeps the artifact
    reviewable, and an obstructed path (directory in the way, full disk) is
    logged and skipped rather than failing the capture run.
    """

    try:
        path = staleness_journal_path(storage_path, user_id, agent_name, now=now)
        entry = {
            "component": "l1_staleness",
            "recorded_at": now if now is not None else time.time(),
            "user_id": user_id,
            "agent_bucket": agent_name,
            "superseded_record_ids": list(superseded_record_ids),
            "stale_dependents": [dependent.to_dict() for dependent in dependents],
            "truncated": bool(truncated),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("staleness propagation: journal append failed (%s)", type(exc).__name__)
        logger.debug("staleness journal write exception", exc_info=exc)


__all__ = [
    "DEPENDENT_LIMIT",
    "StaleDependency",
    "append_staleness_entry",
    "collect_entity_dependents",
    "staleness_journal_path",
]
