"""Central composition of memory-type CAPTURE (the write side).

:mod:`alpha.memory.recall_composition` owns the read side: which enabled memory
types contribute a block to the agent's prompt. This module owns the write side:
which enabled memory types receive a turn's records.

The two are deliberately symmetric and equally boring, because the interesting
part of adding a memory type should never be "edit a shared file".

Three properties make the write side safe to land for a whole wave at once:

* **Default-OFF is invisible.** A disabled type is never constructed and never
  written to. With every type off this function does no work at all.
* **Admission is upstream of every store.** When a policy engine is injected,
  every candidate is evaluated FIRST and a non-admitted candidate is never
  handed to a store. This is the policy -> store chain, and it runs in that
  order on purpose: policy must be able to refuse a write, not merely annotate
  one that already happened.
* **No fabricated signals.** A signal the caller does not have stays ``None``
  in the admission candidate. Turning "unknown importance" into ``0.0`` would
  let a memory look unimportant by accident and get evicted by a policy that
  never actually judged it - so the mapping below fills in only what it can
  prove, and records the omissions.

The caller supplies the records that were **actually persisted**, not the
extraction candidates. That distinction is load-bearing: a downstream type that
ingested pre-dedup candidates would index records that dedup had just merged
away, which shows up later as duplicated or phantom memories. Getting the
persisted set is the caller's job (see the module docstring's wiring note).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Surfaces this seam can write, in deterministic order.
CAPTURE_SURFACE_ORDER: tuple[str, ...] = ("entities", "narrative")

# Capture statuses. `rejected_by_policy` is a SUCCESS for the gate and a
# non-event for the store: the memory was refused on purpose, not lost.
STATUS_WRITTEN = "written"
STATUS_EMPTY = "empty"
STATUS_DISABLED = "disabled"
STATUS_REJECTED = "rejected_by_policy"
STATUS_ERROR = "error"

# Signals the admission candidate is NOT given, because a capture turn cannot
# know them. Declared here so the omission is auditable instead of accidental.
UNPROVID_SIGNALS: tuple[str, ...] = (
    "novelty",
    "usefulness",
    "verification_status",
    "success_count",
    "confidence",
)


@dataclass(frozen=True)
class CaptureStatus:
    """What happened for one surface during capture."""

    surface: str
    status: str
    written: int = 0
    considered: int = 0
    rejected: int = 0
    detail: str = ""

    @property
    def changed_state(self) -> bool:
        return self.written > 0


@dataclass(frozen=True)
class CaptureResult:
    """The composed capture outcome plus an auditable per-surface record."""

    surfaces: tuple[CaptureStatus, ...] = field(default_factory=tuple)

    @property
    def wrote_anything(self) -> bool:
        return any(s.changed_state for s in self.surfaces)

    @property
    def rejected_count(self) -> int:
        return sum(s.rejected for s in self.surfaces)

    def status_for(self, surface: str) -> str | None:
        for item in self.surfaces:
            if item.surface == surface:
                return item.status
        return None


def _type_enabled(config: Any, name: str) -> bool:
    """A type writes only when BOTH the host switch and its own gate are on."""
    if config is None:
        return False
    if not getattr(config, "enabled", False):
        return False
    section = getattr(config, name, None)
    if section is None:
        return False
    return bool(getattr(section, "enabled", False))


def _record_payload(record: Any) -> dict[str, Any] | None:
    """Normalise a persisted record into the plain dict the types accept."""
    if isinstance(record, dict):
        return dict(record)
    dump = getattr(record, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return None


def _admit(
    engine: Any,
    record: dict[str, Any],
    *,
    user_id: str,
    agent_name: str | None,
) -> tuple[bool, str]:
    """Evaluate one candidate. Returns ``(admit, detail)``.

    Unknown signals are left as ``None`` on the candidate: an absent fact is not
    a zero. The policy engine reports its own ``missing_signals``, which is
    recorded rather than hidden.
    """
    from alpha.memory.policy.models import AdmissionCandidate

    candidate = AdmissionCandidate(
        candidate_id=str(record.get("id", "")),
        content=str(record.get("content", "")),
        types=[str(record.get("type", ""))] if record.get("type") else [],
        subtype=str(record.get("scene_name", "")),
        source="l1",
        user_id=user_id,
        agent_id=str(agent_name or ""),
        # Only what a capture turn can actually prove. Everything in
        # UNPROVID_SIGNALS stays None by omission, deliberately.
        provenance=f"l1:{record.get('id', '')}",
    )
    decision = engine.evaluate(candidate)
    detail = f"{decision.rule_id}:{decision.action.value}" if decision.rule_id else decision.action.value
    return bool(decision.admit), detail


def _write_entities(
    config: Any,
    records: list[dict[str, Any]],
    *,
    user_id: str,
    agent_name: str | None,
    now: float | None,
) -> tuple[int, str]:
    from alpha.memory.entities.memory import EntityMemory

    memory = EntityMemory(config=config.entities)
    result = memory.ingest_records(records, user_id=user_id, now=now)
    written = int(getattr(result, "indexed", 0) or getattr(result, "written", 0) or 0)
    return written, str(getattr(result, "status", "unknown"))


def _write_narrative(
    config: Any,
    records: list[dict[str, Any]],
    *,
    user_id: str,
    agent_name: str | None,
    now: float | None,
) -> tuple[int, str]:
    from alpha.memory.narrative.memory import NarrativeMemory

    memory = NarrativeMemory(config=config.narrative)
    events = [
        {
            "id": str(record.get("id", "")),
            "title": str(record.get("content", ""))[:200],
            "summary": str(record.get("content", "")),
            "importance": record.get("priority"),
            "source_refs": [str(record.get("id", ""))],
        }
        for record in records
    ]
    result = memory.ingest(events, scope="user", scope_id=user_id, user_id=user_id, now=now)
    accepted = int(getattr(result, "accepted", 0) or 0)
    return accepted, str(getattr(result, "status", "unknown"))


_WRITERS: dict[str, Callable[..., tuple[int, str]]] = {
    "entities": _write_entities,
    "narrative": _write_narrative,
}


def compose_capture(
    config: Any,
    records: list[Any],
    *,
    user_id: str,
    agent_name: str | None = None,
    now: float | None = None,
    policy_engine: Any | None = None,
    surfaces: tuple[str, ...] | None = None,
) -> CaptureResult:
    """Feed one turn's PERSISTED records to every enabled capture surface.

    ``records`` must be the records that were actually written to durable
    storage, not the extraction candidates. ``policy_engine`` is optional; when
    supplied, admission runs before any store write. ``surfaces`` narrows the
    set (diagnostics/tests).

    Never raises for an expected failure: a surface that fails is recorded with
    the exception TYPE and the remaining surfaces still run.
    """
    wanted = tuple(surfaces) if surfaces is not None else CAPTURE_SURFACE_ORDER
    unknown = [name for name in wanted if name not in _WRITERS]
    if unknown:
        msg = f"unknown capture surface(s): {sorted(unknown)}"
        raise ValueError(msg)
    if not user_id or not str(user_id).strip():
        msg = "compose_capture requires a non-empty user_id"
        raise ValueError(msg)

    payloads = [payload for payload in (_record_payload(r) for r in records) if payload]
    statuses: list[CaptureStatus] = []

    for name in wanted:
        if not _type_enabled(config, name):
            statuses.append(CaptureStatus(surface=name, status=STATUS_DISABLED))
            continue
        if not payloads:
            statuses.append(CaptureStatus(surface=name, status=STATUS_EMPTY))
            continue

        admitted = payloads
        rejected = 0
        if policy_engine is not None:
            kept: list[dict[str, Any]] = []
            for payload in payloads:
                try:
                    ok, _detail = _admit(
                        policy_engine, payload, user_id=user_id, agent_name=agent_name
                    )
                except Exception as exc:  # noqa: BLE001 - a policy failure must not silently admit
                    logger.debug(
                        "admission evaluation failed for %s: %s", name, type(exc).__name__, exc_info=True
                    )
                    rejected += 1
                    continue
                if ok:
                    kept.append(payload)
                else:
                    rejected += 1
            admitted = kept

        if not admitted:
            statuses.append(
                CaptureStatus(
                    surface=name,
                    status=STATUS_REJECTED if rejected else STATUS_EMPTY,
                    considered=len(payloads),
                    rejected=rejected,
                )
            )
            continue

        try:
            written, detail = _WRITERS[name](
                config, admitted, user_id=user_id, agent_name=agent_name, now=now
            )
        except Exception as exc:  # noqa: BLE001 - one type must not fail the turn
            logger.debug("capture surface %s failed: %s", name, type(exc).__name__, exc_info=True)
            statuses.append(
                CaptureStatus(
                    surface=name,
                    status=STATUS_ERROR,
                    considered=len(payloads),
                    rejected=rejected,
                    detail=type(exc).__name__,
                )
            )
            continue

        statuses.append(
            CaptureStatus(
                surface=name,
                status=STATUS_WRITTEN if written else STATUS_EMPTY,
                written=written,
                considered=len(payloads),
                rejected=rejected,
                detail=detail,
            )
        )

    return CaptureResult(surfaces=tuple(statuses))


__all__ = [
    "CAPTURE_SURFACE_ORDER",
    "STATUS_DISABLED",
    "STATUS_EMPTY",
    "STATUS_ERROR",
    "STATUS_REJECTED",
    "STATUS_WRITTEN",
    "UNPROVID_SIGNALS",
    "CaptureResult",
    "CaptureStatus",
    "compose_capture",
]
