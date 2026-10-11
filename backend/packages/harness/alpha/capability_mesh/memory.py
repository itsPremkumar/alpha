"""Cross-run capability memory: compact, versioned outcome records (plan §12).

Phase B of the Alpha Capability Mesh turns "what happened last time" into
measurable capability evidence *without keeping the task itself*. One
:class:`CapabilityRecord` is one compact observation of one contribution — an
entity acting in one capability slot on one task — plus what happened to it
(selected, repaired, rejected, materially edited, unselected) and the measured
quality/cost/latency where they were measured, with honest ``None`` where they
were not.

Three rules hold this module together, extending the Phase A invariants:

* **No score without its profile version.** A record carrying a measured
  ``quality`` must name the ``profile_version`` (the rubric/evaluation profile)
  that produced it, and every aggregation here — ``estimate_for``, the
  evidence levels — is scoped to exactly ONE profile version. A rubric change
  invalidates comparability, so records from another version never pool into
  one number; they are excluded from the aggregation, not silently averaged in.
* **No future-dated evidence.** ``recorded_at`` must be a parseable ISO-8601
  timestamp no later than the clock at construction time (the same rule as
  ``alpha.mission.acceptance``); a record dated ahead of the clock cannot
  prove anything happened. An unparseable timestamp is refused for the same
  reason: it can only downgrade, never upgrade, the evidence it carries.
* **The memory never stores task text.** ``task_fingerprint`` is an opaque,
  caller-supplied string; :func:`fingerprint` exists so callers can derive one
  from raw text without this module ever receiving or keeping that content.
  NEVER pass the raw prompt or task text as ``task_fingerprint``.

Durability is deliberately simple: one ``capability_memory.jsonl`` per
directory, append-style. ``record()`` appends one JSON line (flush + fsync,
O(1)); compaction and pruning rewrite the whole bounded tail atomically (temp
file in the destination directory + fsync + ``os.replace``), UTF-8,
``ensure_ascii=False`` throughout. A missing file loads as an empty, healthy
memory — absence is not corruption — while a corrupt, truncated or dishonest
line raises :class:`MemoryStoreError` instead of silently starting empty (the
same house rule as ``PassportStore``). This is single-process JSONL: atomic
against a crash in one process, **never cross-process exactly-once**; no lock
is taken here, and a second Gateway worker sharing the directory must
serialize writes itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

# The timestamp parser is Phase A's single ISO-8601 rule (Z-suffix aware, naive
# read as UTC, unparseable is None); this module reuses it rather than growing
# a second opinion that could drift from derive_evidence_level's.
from alpha.capability_mesh.models import CapabilityEstimate, _parse_timestamp, derive_evidence_level

#: The single JSONL document the memory owns inside its directory.
MEMORY_FILE_NAME = "capability_memory.jsonl"

#: The bounded tail: recording beyond this many live records compacts the file
#: down to the newest ones. Explicit retention control is :meth:`CapabilityMemory.prune`.
DEFAULT_MAX_RECORDS = 10_000


class MemoryStoreError(RuntimeError):
    """The capability memory file is unreadable, corrupt, or dishonest."""


class OutcomeKind(StrEnum):
    """What happened to one contribution after it was produced (plan §12.1)."""

    SELECTED = "selected"
    REPAIRED = "repaired"
    REJECTED = "rejected"
    MATERIALLY_EDITED = "materially_edited"
    UNSELECTED = "unselected"


#: Outcomes in which the contribution actually made it into the deliverable.
#: Only these count as an *outcome* for evidence levels — repair credit is
#: real, but a fragment nobody shipped is not delivered work.
DELIVERED_OUTCOMES = frozenset({OutcomeKind.SELECTED, OutcomeKind.MATERIALLY_EDITED})

#: Credit-bearing outcomes for the conservative credit formula below.
SELECTION_OUTCOMES = frozenset({OutcomeKind.SELECTED, OutcomeKind.REPAIRED, OutcomeKind.MATERIALLY_EDITED})


def fingerprint(text: str) -> str:
    """A stable, non-reversible task fingerprint: ``fp_`` + first 16 hex of sha256.

    This helper exists so a caller can build ``task_fingerprint`` from raw task
    text WITHOUT the memory ever seeing or storing that text. The result is not
    encryption — it is a truncation chosen so the memory stays compact and the
    content never has to be kept. Do not use it as a secret: a short hash of
    guessable text can be matched by brute force, which is why the raw prompt
    must not be passed in either form.
    """
    if not text:
        raise ValueError("fingerprint requires the text to hash")
    return "fp_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _require_text(value: str, field: str) -> str:
    if not value:
        raise ValueError(f"{field} must be non-empty")
    return value


def _unit_interval(value: float | None, field: str) -> float | None:
    """Validate an optional 0..1 finite measurement; convert ints to float."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number in [0, 1] or None, got {value!r}")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{field} must be a finite number in [0, 1], got {value!r}")
    return number


def _non_negative(value: float | int | None, field: str) -> float | int | None:
    """Validate an optional finite measurement >= 0."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number >= 0 or None, got {value!r}")
    if not math.isfinite(float(value)) or value < 0:
        raise ValueError(f"{field} must be a finite number >= 0, got {value!r}")
    return value


@dataclass(frozen=True)
class CapabilityRecord:
    """One compact, versioned observation of one contribution (plan §12.1).

    ``entity_id`` names a passport entity, ``capability`` the capability it was
    exercised in, and ``slot`` the contribution slot/step it fed (Feature F's
    vocabulary, optional). ``quality`` is the measured 0..1 quality of *this
    contribution* — ``None`` when it was not measurable, never a guess.
    ``passed_hard_gate`` is tri-state on purpose: ``True``/``False`` are
    measurements, ``None`` means "not checked", and a truthy string is refused
    so it can never become a silent pass. ``confidence`` is the agent's OWN
    stated confidence in the contribution — a calibration *signal*, not
    evidence, and deliberately separate from the measured ``quality`` it is
    compared against later.
    """

    record_id: str
    task_fingerprint: str
    task_category: str
    entity_id: str
    capability: str
    outcome: OutcomeKind
    recorded_at: str
    slot: str | None = None
    quality: float | None = None
    passed_hard_gate: bool | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    failure_mode: str | None = None
    profile_version: str = ""
    confidence: float | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "outcome", OutcomeKind(str(self.outcome)))
        except ValueError as error:
            allowed = ", ".join(kind.value for kind in OutcomeKind)
            raise ValueError(f"unknown outcome {self.outcome!r}; expected one of: {allowed}") from error

        _require_text(self.record_id, "record_id")
        _require_text(self.task_fingerprint, "task_fingerprint")
        _require_text(self.task_category, "task_category")
        _require_text(self.entity_id, "entity_id")
        _require_text(self.capability, "capability")

        object.__setattr__(self, "quality", _unit_interval(self.quality, "quality"))
        object.__setattr__(self, "confidence", _unit_interval(self.confidence, "confidence"))
        if self.quality is not None and not self.profile_version:
            raise ValueError("quality requires a non-empty profile_version (no score without its profile version)")

        if self.passed_hard_gate is not None and not isinstance(self.passed_hard_gate, bool):
            raise ValueError(f"passed_hard_gate must be a bool or None (None means 'not checked'), got {self.passed_hard_gate!r}")
        object.__setattr__(self, "latency_ms", _non_negative(self.latency_ms, "latency_ms"))
        object.__setattr__(self, "cost_usd", _non_negative(self.cost_usd, "cost_usd"))

        stamp = _parse_timestamp(self.recorded_at)
        if stamp is None:
            raise ValueError(f"recorded_at must be a parseable ISO-8601 timestamp, got {self.recorded_at!r}")
        if stamp > datetime.now(UTC):
            raise ValueError(f"recorded_at {self.recorded_at!r} is in the future: a future-dated record cannot be evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "task_fingerprint": self.task_fingerprint,
            "task_category": self.task_category,
            "entity_id": self.entity_id,
            "capability": self.capability,
            "slot": self.slot,
            "outcome": self.outcome.value,
            "quality": self.quality,
            "passed_hard_gate": self.passed_hard_gate,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "failure_mode": self.failure_mode,
            "profile_version": self.profile_version,
            "confidence": self.confidence,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityRecord:
        # The constructor re-validates, so a hand-edited JSONL line cannot
        # bypass the honesty rules by skipping __post_init__.
        return cls(
            record_id=str(data["record_id"]),
            task_fingerprint=str(data["task_fingerprint"]),
            task_category=str(data["task_category"]),
            entity_id=str(data["entity_id"]),
            capability=str(data["capability"]),
            # Coerced (and validated with the friendly message) in __post_init__.
            outcome=data["outcome"],
            recorded_at=str(data["recorded_at"]),
            slot=data.get("slot"),
            quality=None if data.get("quality") is None else float(data["quality"]),
            passed_hard_gate=data.get("passed_hard_gate"),
            latency_ms=None if data.get("latency_ms") is None else int(data["latency_ms"]),
            cost_usd=None if data.get("cost_usd") is None else float(data["cost_usd"]),
            failure_mode=data.get("failure_mode"),
            profile_version=str(data.get("profile_version", "")),
            confidence=None if data.get("confidence") is None else float(data["confidence"]),
        )


@dataclass(frozen=True)
class CreditRow:
    """Conservative credit totals for one (entity, capability) pair.

    Every recorded contribution counts as one ``attempt``; the kind counts are
    the outcome tallies, and ``credit`` is the conservative score described in
    :meth:`CapabilityMemory.credit_for`. The constructor refuses kind counts
    that exceed ``attempts`` and a ``credit`` outside [0, 1], so a row that
    cannot arise from the formula cannot be minted by hand.
    """

    capability: str
    attempts: int
    selected: int
    repaired: int
    rejected: int
    materially_edited: int
    credit: float

    def __post_init__(self) -> None:
        _require_text(self.capability, "capability")
        for name in ("attempts", "selected", "repaired", "rejected", "materially_edited"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be an int >= 0, got {value!r}")
        if self.selected + self.repaired + self.rejected + self.materially_edited > self.attempts:
            raise ValueError(f"kind counts exceed attempts ({self.attempts}): a row must tally at most one outcome per attempt")
        if not math.isfinite(self.credit) or not 0.0 <= self.credit <= 1.0:
            raise ValueError(f"credit must be a finite number in [0, 1], got {self.credit!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "attempts": self.attempts,
            "selected": self.selected,
            "repaired": self.repaired,
            "rejected": self.rejected,
            "materially_edited": self.materially_edited,
            "credit": self.credit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreditRow:
        return cls(
            capability=str(data["capability"]),
            attempts=int(data["attempts"]),
            selected=int(data["selected"]),
            repaired=int(data["repaired"]),
            rejected=int(data["rejected"]),
            materially_edited=int(data["materially_edited"]),
            credit=float(data["credit"]),
        )


@dataclass(frozen=True)
class CreditReport:
    """Per-(entity, capability) conservative credit for one entity (plan §12.2)."""

    entity_id: str
    rows: tuple[CreditRow, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.entity_id, "entity_id")
        object.__setattr__(self, "rows", tuple(self.rows))
        for row in self.rows:
            if not isinstance(row, CreditRow):
                raise ValueError(f"rows must contain CreditRow entries, got {row!r}")

    def row_for(self, capability: str) -> CreditRow | None:
        """The credit row answering one capability question, or None."""
        for row in self.rows:
            if row.capability == capability:
                return row
        return None

    def total_credit(self) -> float | None:
        """The attempts-weighted mean credit across all rows; None with no attempts."""
        attempts = sum(row.attempts for row in self.rows)
        if attempts == 0:
            return None
        return sum(row.credit * row.attempts for row in self.rows) / attempts

    def to_dict(self) -> dict[str, Any]:
        return {"entity_id": self.entity_id, "rows": [row.to_dict() for row in self.rows]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreditReport:
        return cls(entity_id=str(data["entity_id"]), rows=tuple(CreditRow.from_dict(entry) for entry in data.get("rows", ())))


def _record_order(record: CapabilityRecord) -> tuple[datetime, str]:
    """Sort key: real time order, then id — never insertion order."""
    stamp = _parse_timestamp(record.recorded_at)
    # CapabilityRecord refuses unparseable timestamps at construction, so this
    # cannot be None for any record that exists.
    assert stamp is not None
    return (stamp, record.record_id)


class CapabilityMemory:
    """In-memory + durable accumulator of :class:`CapabilityRecord` rows.

    One memory is one directory holding one ``capability_memory.jsonl``. The
    constructor loads what is already there (a missing file is an empty,
    healthy memory; a corrupt one raises :class:`MemoryStoreError` rather than
    silently starting empty — one bad sector must not become amnesia).
    ``record()`` and ``prune()`` persist automatically; ``save()`` rewrites the
    bounded tail explicitly.
    """

    def __init__(self, directory: Path | str, *, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 1:
            raise ValueError(f"max_records must be an int >= 1, got {max_records!r}")
        self._directory = Path(directory)
        self._max_records = max_records
        self._records: list[CapabilityRecord] = []
        self.load()

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def path(self) -> Path:
        return self._directory / MEMORY_FILE_NAME

    @property
    def max_records(self) -> int:
        return self._max_records

    # --------------------------------------------------------------- durable

    def load(self) -> CapabilityMemory:
        """(Re)read the JSONL file into memory.

        A missing file is a genuinely empty memory — absence is not
        corruption. Anything else that fails to parse (bad JSON, a malformed
        entry, an entry violating the honesty rules such as a future-dated
        record) raises :class:`MemoryStoreError` naming the line, rather than
        silently starting empty and later writing amnesia back over the file.
        """
        path = self.path
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            loaded: list[CapabilityRecord] = []
        except (OSError, UnicodeDecodeError) as error:
            raise MemoryStoreError(f"capability memory '{path}' is unreadable: {error}") from error
        else:
            loaded = []
            if text.strip():
                for index, line in enumerate(text.splitlines(), start=1):
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise MemoryStoreError(f"capability memory '{path}' line {index} is not valid JSON: {error}") from error
                    try:
                        loaded.append(CapabilityRecord.from_dict(entry))
                    except (TypeError, KeyError, ValueError) as error:
                        raise MemoryStoreError(f"capability memory '{path}' line {index} is invalid: {error}") from error
        self._records = loaded
        return self

    def save(self) -> None:
        """Atomically rewrite the whole bounded tail as JSONL.

        Temp file in the destination directory, fsync, ``os.replace`` — a
        crash mid-write leaves the previous document rather than a truncated
        one. ``record()`` appends instead (cheaper); this is what compaction
        and :meth:`prune` use.
        """
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                for record in self._records:
                    handle.write(json.dumps(record.to_dict(), ensure_ascii=False))
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # ---------------------------------------------------------------- writes

    def record(self, record: CapabilityRecord) -> None:
        """Append one validated record and persist it. Assigns nothing.

        A duplicate ``record_id`` is refused by name: an id is the identity of
        an observation, and silently merging two would make every count
        ambiguous. When the live set exceeds ``max_records`` the file is
        compacted down to the newest tail.
        """
        if not isinstance(record, CapabilityRecord):
            raise ValueError(f"record() expects a CapabilityRecord, got {record!r}")
        if any(existing.record_id == record.record_id for existing in self._records):
            raise ValueError(f"duplicate record_id {record.record_id!r}: record ids identify one observation and may not repeat")
        self._records.append(record)
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records :]
            self.save()

    def prune(self, max_records: int, *, keep: Callable[[CapabilityRecord], bool] | None = None) -> int:
        """Bounded retention (plan §12.1): drop the oldest beyond ``max_records``.

        A record the ``keep`` predicate claims is NEVER dropped, so the total
        may exceed ``max_records`` when the predicate protects more than the
        budget — protection wins over the bound, always. Returns how many
        records were actually dropped, and persists when anything was.
        """
        if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 0:
            raise ValueError(f"max_records must be an int >= 0, got {max_records!r}")
        retained: list[CapabilityRecord] = []
        unprotected_kept = 0
        # Walk newest -> oldest so the bound sheds the oldest first.
        for record in sorted(self._records, key=_record_order, reverse=True):
            if keep is not None and keep(record):
                retained.append(record)
                continue
            if unprotected_kept < max_records:
                retained.append(record)
                unprotected_kept += 1
        dropped = len(self._records) - len(retained)
        if dropped:
            self._records = retained
            self.save()
        return dropped

    # ----------------------------------------------------------------- reads

    def records(
        self,
        entity_id: str | None = None,
        capability: str | None = None,
        task_category: str | None = None,
        since: str | datetime | None = None,
    ) -> list[CapabilityRecord]:
        """Filtered records in deterministic order: oldest first, then by id.

        ``since`` is inclusive and accepts an ISO-8601 string or a datetime; a
        timestamp nobody can parse is refused rather than ignored, because
        silently reading it as "all" would answer a narrower question with a
        wider dataset.
        """
        since_stamp: datetime | None = None
        if since is not None:
            if isinstance(since, datetime):
                since_stamp = since if since.tzinfo is not None else since.replace(tzinfo=UTC)
            else:
                since_stamp = _parse_timestamp(since)
                if since_stamp is None:
                    raise ValueError(f"since must be a parseable ISO-8601 timestamp or datetime, got {since!r}")
        selected = [
            record
            for record in self._records
            if (entity_id is None or record.entity_id == entity_id)
            and (capability is None or record.capability == capability)
            and (task_category is None or record.task_category == task_category)
            # A stored record is constructor-validated, so its timestamp always parses.
            and (since_stamp is None or _parse_timestamp(record.recorded_at) >= since_stamp)
        ]
        return sorted(selected, key=_record_order)

    def estimate_for(
        self,
        entity_id: str,
        capability: str,
        task_category: str = "*",
        *,
        profile_version: str,
        now: datetime | None = None,
    ) -> CapabilityEstimate:
        """THE learning function: records become one honest estimate (plan §12.3).

        ``sample_count`` is the number of matching attempts that were *observed*
        (including attempts nobody scored), while ``quality_mean`` and
        ``uncertainty`` are computed over the subset carrying a measured
        ``quality`` — with zero measured qualities both stay ``None``, because
        an attempt count is not a quality score. ``uncertainty`` is the sample
        standard error of the measured qualities, and for a single measured
        sample it is ``0.0``: that is the *absence* of a spread estimate, NOT a
        claim of exactness (``sample_count`` travels with it for that reason).

        Only records whose ``profile_version`` equals the one you pass are
        counted — a rubric change invalidates comparability, so v1 samples
        earn a v2 estimate nothing but an honest UNMEASURED. Task categories
        are never pooled: ``"*"`` answers from records measured under the
        wildcard category itself, not from a blind average across categories
        (plan §8.2). ``has_outcome`` requires a DELIVERED outcome — the
        contribution actually made it into the deliverable — and the result is
        stamped with ``profile_version`` and routed through
        :func:`~alpha.capability_mesh.models.derive_evidence_level`.
        """
        if not profile_version:
            raise ValueError("estimate_for requires a non-empty profile_version (no score without its profile version)")
        matching = [record for record in self._records if record.entity_id == entity_id and record.capability == capability and record.task_category == task_category and record.profile_version == profile_version]
        sample_count = len(matching)
        qualities = [record.quality for record in matching if record.quality is not None]
        quality_mean = sum(qualities) / len(qualities) if qualities else None
        if len(qualities) >= 2:
            variance = sum((quality - quality_mean) ** 2 for quality in qualities) / (len(qualities) - 1)
            uncertainty = math.sqrt(variance) / math.sqrt(len(qualities))
        elif len(qualities) == 1:
            uncertainty = 0.0  # no spread to estimate; see the docstring.
        else:
            uncertainty = None
        has_outcome = any(record.outcome in DELIVERED_OUTCOMES for record in matching)
        newest = max((_parse_timestamp(record.recorded_at) for record in matching), default=None)
        last_evaluated_at = newest.isoformat() if newest is not None else None
        return CapabilityEstimate(
            capability=capability,
            task_category=task_category,
            quality_mean=quality_mean,
            uncertainty=uncertainty,
            sample_count=sample_count,
            evidence_level=derive_evidence_level(sample_count, has_outcome=has_outcome, last_evaluated_at=last_evaluated_at, now=now),
            last_evaluated_at=last_evaluated_at,
            profile_version=profile_version,
        )

    def credit_for(self, entity_id: str, *, since: str | datetime | None = None) -> CreditReport:
        """Conservative credit assignment per (entity, capability) — plan §12.2.

        **Starting policy, not truth.** The formula is::

            credit = (selected + 0.5 * repaired + 0.25 * materially_edited) / attempts

        Every record counts as one attempt; REJECTED and UNSELECTED contribute
        nothing, and a contribution nobody measured (``quality is None`` with a
        non-selection outcome) is still an attempt that earns zero — "appeared
        in a success" earns nothing without a selection outcome. The weights
        encode a deliberate conservatism (selection > repair > material edit)
        chosen as a *defensible starting point* to be revisited against real
        routing outcomes, not as a measurement of worth. Attribution is at
        contribution-slot granularity wherever ``slot`` was recorded; richer
        marginal-value attribution (plan §12.2's with/without replay) is a
        later layer, not this formula.
        """
        since_stamp: datetime | None = None
        if since is not None:
            if isinstance(since, datetime):
                since_stamp = since if since.tzinfo is not None else since.replace(tzinfo=UTC)
            else:
                since_stamp = _parse_timestamp(since)
                if since_stamp is None:
                    raise ValueError(f"since must be a parseable ISO-8601 timestamp or datetime, got {since!r}")
        counts: dict[str, dict[str, int]] = {}
        for record in self._records:
            if record.entity_id != entity_id:
                continue
            if since_stamp is not None and _parse_timestamp(record.recorded_at) < since_stamp:
                continue
            tally = counts.setdefault(record.capability, {"attempts": 0, "selected": 0, "repaired": 0, "rejected": 0, "materially_edited": 0, "unselected": 0})
            tally["attempts"] += 1
            tally[record.outcome.value] += 1
        rows = []
        for capability in sorted(counts):
            tally = counts[capability]
            credit = (tally["selected"] + 0.5 * tally["repaired"] + 0.25 * tally["materially_edited"]) / tally["attempts"]
            rows.append(
                CreditRow(
                    capability=capability,
                    attempts=tally["attempts"],
                    selected=tally["selected"],
                    repaired=tally["repaired"],
                    rejected=tally["rejected"],
                    materially_edited=tally["materially_edited"],
                    credit=credit,
                )
            )
        return CreditReport(entity_id=entity_id, rows=tuple(rows))


__all__ = [
    "DEFAULT_MAX_RECORDS",
    "DELIVERED_OUTCOMES",
    "MEMORY_FILE_NAME",
    "SELECTION_OUTCOMES",
    "CapabilityMemory",
    "CapabilityRecord",
    "CreditReport",
    "CreditRow",
    "MemoryStoreError",
    "OutcomeKind",
    "fingerprint",
]
