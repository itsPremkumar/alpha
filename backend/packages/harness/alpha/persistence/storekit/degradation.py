"""Damage classification and scoped degradation.

The rule this module exists to enforce: **classify damage to the component that
is damaged, and degrade only that component.**  An index fault is an index
fault; it is not whole-file corruption, and it must never take the transcript
store down with it.

Three things live here, each a generalisation of a defect that actually
happened:

:class:`DamageClass`
    A closed vocabulary of what can be wrong.  ``STALE_INDEX`` and
    ``WRITE_CORRUPTION`` are different classes with different repairs; calling
    both "corrupt" sends the operator to the wrong one.

:func:`classify_damage`
    Maps an exception plus context onto exactly one class, with a reason string
    that names the class rather than the symptom.

:func:`coerce_records`
    Per-record coercion for **every** reader.  One malformed row becomes an
    explicit placeholder plus a warning that names the row; the other rows still
    list.  A single bad record must not kill a listing.

:class:`SafeRepair`
    A repair that must *prove* its precondition before acting.  An unverifiable
    precondition aborts loudly; it never guesses.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Iterable, Iterator, Sequence

logger = logging.getLogger(__name__)

#: The placeholder a coerced record carries in place of its bad field.
UNREADABLE_PLACEHOLDER = "<unreadable:damaged-record>"


class DamageClass(StrEnum):
    """What is actually wrong, at the granularity that determines the repair.

    Each member exists because collapsing it into a neighbour sends an operator
    to the wrong fix:

    ``NONE``                nothing is wrong.
    ``STALE_INDEX``         the index is behind the data.  Rebuild it.
    ``INDEX_WRITE_CORRUPT`` the index itself is damaged.  Rebuild *it*, keep the
                            data.  Reporting this as whole-file corruption is how
                            a conversation gets killed over a bad MATCH query.
    ``INDEX_UNAVAILABLE``   the index engine is missing or refused to initialise.
                            Fall back to a scan; the store is untouched.
    ``QUERY_REJECTED``      one query was rejected.  Degrade that query only.
    ``RECORD_MALFORMED``    one record's payload will not validate.  Coerce that
                            record only.
    ``RECORD_UNREADABLE``   one record's stored bytes will not decode.
    ``SCHEMA_TOO_NEW``      written by a newer build.  Refuse, do not rewrite.
    ``FILE_CORRUPT``        the container itself is damaged.  This is the only
                            class that justifies quarantining the whole file.
    """

    NONE = "none"
    STALE_INDEX = "stale_index"
    INDEX_WRITE_CORRUPT = "index_write_corrupt"
    INDEX_UNAVAILABLE = "index_unavailable"
    QUERY_REJECTED = "query_rejected"
    RECORD_MALFORMED = "record_malformed"
    RECORD_UNREADABLE = "record_unreadable"
    SCHEMA_TOO_NEW = "schema_too_new"
    FILE_CORRUPT = "file_corrupt"


#: Which component a class is scoped to.  ``subsystem`` is the blast radius:
#: a class scoped to ``"search_index"`` must not degrade ``"transcript_store"``.
DAMAGE_SCOPE: dict[DamageClass, str] = {
    DamageClass.NONE: "none",
    DamageClass.STALE_INDEX: "search_index",
    DamageClass.INDEX_WRITE_CORRUPT: "search_index",
    DamageClass.INDEX_UNAVAILABLE: "search_index",
    DamageClass.QUERY_REJECTED: "search_index",
    DamageClass.RECORD_MALFORMED: "record",
    DamageClass.RECORD_UNREADABLE: "record",
    DamageClass.SCHEMA_TOO_NEW: "document",
    DamageClass.FILE_CORRUPT: "document",
}

#: Classes that need an operator (or a scheduled repair) to fix, but that still
#: must not take the whole system down with them.
NEEDS_OPERATOR: frozenset[DamageClass] = frozenset({DamageClass.INDEX_WRITE_CORRUPT})

#: Whether a class is repairable without operator action.
SELF_HEALING: frozenset[DamageClass] = frozenset(
    {
        DamageClass.NONE,
        DamageClass.STALE_INDEX,
        DamageClass.INDEX_UNAVAILABLE,
        DamageClass.QUERY_REJECTED,
        DamageClass.RECORD_MALFORMED,
        DamageClass.RECORD_UNREADABLE,
    }
)

#: Whether a class justifies failing the *whole* operation.  Only
#: ``FILE_CORRUPT`` and ``SCHEMA_TOO_NEW`` do; everything else degrades.
FAIL_CLOSED_CLASSES: frozenset[DamageClass] = frozenset(
    {DamageClass.FILE_CORRUPT, DamageClass.SCHEMA_TOO_NEW}
)

_FTS_PATTERNS: tuple[str, ...] = (
    r"database disk image is malformed",
    r"malformed database schema",
    r"\.vtable: no such module",
    r"unable to use function \w+ in the requested context",
    r"fts5: \w*error",
    r"database or disk is full",
)
_STALE_INDEX_PATTERNS: tuple[str, ...] = (
    r"no such table: \w*fts",
    r"no such (?:column|function): \w*fts",
    r"\bmissing\b.*\bindex\b",
    r"index is out of date",
    r"index is stale",
)
_QUERY_REJECTED_PATTERNS: tuple[str, ...] = (
    r"fts5: syntax error",
    r"malformed match expression",
    r"unterminated string",
    r"near \"",
)


@dataclass(frozen=True)
class DamageReport:
    """The honest diagnosis: what class, on which component, and why."""

    damage: DamageClass
    scope: str
    reason: str
    detail: str = ""
    #: Names of the individual records that were coerced, when applicable.
    damaged_records: tuple[str, ...] = ()
    fail_closed: bool = False
    self_healing: bool = True

    @property
    def degraded(self) -> bool:
        return self.damage is not DamageClass.NONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "damage": self.damage.value,
            "scope": self.scope,
            "reason": self.reason,
            "detail": self.detail,
            "damaged_records": list(self.damaged_records),
            "fail_closed": self.fail_closed,
            "self_healing": self.self_healing,
        }

    def summary(self) -> str:
        """One line an operator can act on.  Names the class, not the symptom."""
        if not self.degraded:
            return "no damage detected"
        parts = [f"{self.damage.value} on {self.scope}: {self.reason}"]
        if self.damaged_records:
            parts.append(f"records: {', '.join(self.damaged_records[:5])}")
        if self.fail_closed:
            parts.append("operation refused (this class is not safely degradable)")
        return "; ".join(parts)


def classify_damage(
    exc: BaseException,
    *,
    component: str = "store",
    context: str = "",
) -> DamageReport:
    """Classify *exc* into exactly one :class:`DamageClass`.

    The classifier is deliberately conservative in the *safe* direction: when it
    cannot tell a stale index from a corrupt one it says so in ``reason`` rather
    than picking the more dramatic label.
    """
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    # Order is the whole game here.  A stale index and a rejected query both
    # *mention* the FTS table, so they are tested before the index-corruption
    # patterns: testing corruption first would label every missing table
    # "index_write_corrupt" and send the operator to destroy a healthy index.
    for pattern in _STALE_INDEX_PATTERNS:
        if re.search(pattern, lowered):
            return _report(
                DamageClass.STALE_INDEX,
                component,
                "the index is behind the data; rebuild the index, do not touch the "
                "records",
                detail=text,
            )
    for pattern in _QUERY_REJECTED_PATTERNS:
        if re.search(pattern, lowered):
            return _report(
                DamageClass.QUERY_REJECTED,
                component,
                "one query was rejected; degrade that query and leave every other "
                "read intact",
                detail=text,
            )
    for pattern in _FTS_PATTERNS:
        if re.search(pattern, lowered):
            return _report(
                DamageClass.INDEX_WRITE_CORRUPT,
                component,
                "the full-text index itself is damaged; the underlying records are "
                "untouched and a rebuild of the index alone is the repair",
                detail=text,
            )
    if "no such module: fts5" in lowered or isinstance(exc, sqlite3.OperationalError) and (
        "fts5" in lowered
    ):
        return _report(
            DamageClass.INDEX_UNAVAILABLE,
            component,
            "this SQLite build has no usable FTS5; search falls back to a scan and "
            "the store is untouched",
            detail=text,
        )
    if isinstance(exc, (UnicodeDecodeError,)):
        return _report(
            DamageClass.RECORD_UNREADABLE,
            "record",
            "one record's stored bytes will not decode; that record is rendered as "
            "a placeholder and the rest still list",
            detail=text,
        )
    if isinstance(exc, (TypeError, ValueError)) and "record" in lowered:
        return _report(
            DamageClass.RECORD_MALFORMED,
            "record",
            "one record's payload will not validate; that record is rendered as a "
            "placeholder and the rest still list",
            detail=text,
        )
    if isinstance(exc, (TypeError, ValueError, KeyError)) and "payload" in lowered:
        return _report(
            DamageClass.FILE_CORRUPT,
            component,
            "the document container itself is malformed; this is the only class "
            "that justifies quarantining the whole document",
            detail=text,
        )
    if "unsupported_future_version" in lowered or "newer than target" in lowered:
        return _report(
            DamageClass.SCHEMA_TOO_NEW,
            "document",
            "written by a newer build; refuse rather than rewrite the operator's data",
            detail=text,
        )
    if isinstance(exc, sqlite3.DatabaseError) and "malformed" in lowered:
        return _report(
            DamageClass.FILE_CORRUPT,
            component,
            "the database container is malformed",
            detail=text,
        )
    return _report(
        DamageClass.FILE_CORRUPT,
        component,
        f"unclassified failure {type(exc).__name__}; treated as container damage "
        "because no narrower class matched"
        + (f" (context: {context})" if context else ""),
        detail=text,
    )


def _report(
    damage: DamageClass,
    component: str,
    reason: str,
    *,
    detail: str = "",
    damaged_records: Sequence[str] = (),
) -> DamageReport:
    return DamageReport(
        damage=damage,
        scope=DAMAGE_SCOPE.get(damage, component),
        reason=reason,
        detail=detail,
        damaged_records=tuple(damaged_records),
        fail_closed=damage in FAIL_CLOSED_CLASSES,
        self_healing=damage in SELF_HEALING,
    )


# ---------------------------------------------------------------------------
# per-record coercion
# ---------------------------------------------------------------------------
@dataclass
class CoercionResult:
    """The result of coercing a list of records one at a time."""

    records: list[Any] = field(default_factory=list)
    damaged: list[str] = field(default_factory=list)
    report: DamageReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": len(self.records),
            "damaged": list(self.damaged),
            "damage": self.report.to_dict() if self.report else None,
        }


def coerce_records(
    rows: Iterable[Any],
    *,
    identifier: Callable[[Any], str] | None = None,
    validate: Callable[[Any], Any] | None = None,
    placeholder: Any = None,
    component: str = "store",
) -> CoercionResult:
    """Coerce each row independently.  A bad row never removes a good one.

    Every reader of a record list must go through this.  A reader that does not
    is the defect this function exists to prevent.
    """
    out = CoercionResult()
    damaged: list[str] = []
    for index, row in enumerate(rows):
        name = (identifier(row) if identifier else None) or f"#{index}"
        try:
            if validate is not None:
                out.records.append(validate(row))
            else:
                out.records.append(row)
        except Exception as exc:  # noqa: BLE001 - per-record containment is the point
            damaged.append(str(name))
            logger.warning(
                "skipping unreadable record %s in %s: %s: %s",
                name,
                component,
                type(exc).__name__,
                exc,
            )
            out.records.append(placeholder if placeholder is not None else UNREADABLE_PLACEHOLDER)
    if damaged:
        out.damaged.extend(damaged)
        out.report = _report(
            DamageClass.RECORD_MALFORMED,
            "record",
            "one or more records were malformed; they are rendered as explicit "
            "placeholders and the listing continues",
            damaged_records=damaged,
        )
    return out


# ---------------------------------------------------------------------------
# scoped degradation
# ---------------------------------------------------------------------------
@dataclass
class Degradation:
    """Records that a component degraded and stayed usable."""

    component: str
    report: DamageReport
    fell_back: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "fell_back": self.fell_back,
            **self.report.to_dict(),
        }


@contextmanager
def degrade(
    component: str,
    *,
    fallback: Callable[[], Any] | None = None,
    on_damage: Callable[[DamageReport], None] | None = None,
) -> Iterator[list[Degradation]]:
    """Degrade *component* for the duration of the block, and only it.

    Usage::

        with degrade("search_index", fallback=_like_scan) as events:
            return search(...)
        # ``events`` says what happened; other components never learn about it.

    A failure whose class is in :data:`FAIL_CLOSED_CLASSES` is re-raised: no
    amount of politeness makes quarantining the wrong component correct.
    """
    recorded: list[Degradation] = []
    try:
        yield recorded
    except Exception as exc:  # noqa: BLE001 - classification is the whole point
        report = classify_damage(exc, component=component)
        recorded.append(Degradation(component=component, report=report))
        if on_damage is not None:
            on_damage(report)
        if report.fail_closed:
            logger.error("refusing to degrade %s: %s", component, report.summary())
            raise
        logger.warning(
            "%s degraded (%s); the rest of the system is unaffected",
            component,
            report.summary(),
        )
        if fallback is not None:
            value = fallback()
            if isinstance(value, Degradation):  # pragma: no cover - defensive
                recorded.append(value)


# ---------------------------------------------------------------------------
# repairs that must prove themselves
# ---------------------------------------------------------------------------
class UnsafeRepair(RuntimeError):
    """A repair was asked to act without a verifiable precondition."""


@dataclass
class RepairOutcome:
    """What a repair did, or why it refused."""

    action: str
    performed: bool
    reason: str
    precondition: str = ""
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "performed": self.performed,
            "reason": self.reason,
            "precondition": self.precondition,
            "evidence": self.evidence,
        }


@dataclass
class SafeRepair:
    """A repair that verifies its own precondition, or refuses to act.

    ``verify`` returns ``(ok, evidence)``.  When it cannot prove the precondition
    - an unavailable connection, an unreadable row count, an unknown schema -
    the repair aborts loudly with the reason.  It never guesses, and it never
    performs a partial repair.
    """

    action: str
    precondition: str
    verify: Callable[[], tuple[bool, str]]
    perform: Callable[[], str]

    def run(self, *, dry_run: bool = True) -> RepairOutcome:
        try:
            ok, evidence = self.verify()
        except Exception as exc:  # noqa: BLE001 - an unverifiable precondition is a refusal
            return RepairOutcome(
                action=self.action,
                performed=False,
                reason=(
                    f"refusing to {self.action}: the precondition "
                    f"({self.precondition}) could not be verified - "
                    f"{type(exc).__name__}: {exc}"
                ),
                precondition=self.precondition,
            )
        if not ok:
            return RepairOutcome(
                action=self.action,
                performed=False,
                reason=(
                    f"refusing to {self.action}: the precondition "
                    f"({self.precondition}) was not met - {evidence}"
                ),
                precondition=self.precondition,
                evidence=evidence,
            )
        if dry_run:
            return RepairOutcome(
                action=self.action,
                performed=False,
                reason=f"{self.action} is safe; run with dry_run=False to apply",
                precondition=self.precondition,
                evidence=evidence,
            )
        try:
            result = self.perform()
        except Exception as exc:  # noqa: BLE001
            return RepairOutcome(
                action=self.action,
                performed=False,
                reason=f"{self.action} aborted: {type(exc).__name__}: {exc}",
                precondition=self.precondition,
                evidence=evidence,
            )
        return RepairOutcome(
            action=self.action,
            performed=True,
            reason=result,
            precondition=self.precondition,
            evidence=evidence,
        )


def index_health_probe(connection: Any) -> tuple[bool, str]:
    """Verify that an FTS index is *behind* the data, not damaged.

    This is the precondition a rebuild must prove.  Note what it does **not**
    accept: the mere existence of the index table.  A stale index is present
    and wrong, and "it exists" is exactly the check that lets a repair act on a
    damaged index and destroy the data it was supposed to protect.
    """
    try:
        row = connection.execute(
            "SELECT count(*) FROM run_events_fts"
        ).fetchone()
        indexed = int(row[0]) if row else 0
        source = connection.execute(
            "SELECT count(*) FROM run_events WHERE category = 'message'"
        ).fetchone()
        expected = int(source[0]) if source else 0
    except Exception as exc:  # noqa: BLE001 - unverifiable is a refusal
        return False, f"index health could not be measured ({type(exc).__name__}: {exc})"
    if indexed == expected:
        return False, f"index is current ({indexed} rows); a rebuild would be a no-op"
    return True, f"index holds {indexed} rows, source holds {expected}"
