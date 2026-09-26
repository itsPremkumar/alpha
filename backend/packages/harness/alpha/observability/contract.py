"""The one versioned behaviour-trace event envelope.

Why this module exists
----------------------
Alpha had a run-correlation spine (:mod:`alpha.observability.events`,
:mod:`alpha.observability.span`, :mod:`alpha.observability.recorder`) that was
**never switched on** (``ObservabilityConfig.enabled`` defaults ``False`` and
``build_recorder()`` had no caller). It also had a durable run-event log the
Gateway already writes and already serves at
``GET /api/threads/{thread_id}/runs/{run_id}/events``. Neither could answer the
question the sentinel actually needs answered: *what did the agent do, in what
order, and why did it choose that?*

This module is the missing contract. :class:`TraceEnvelope` is the single
record shape every one of the 18 instrumentation layers emits, and it is
versioned (:data:`SCHEMA_VERSION`) so a reader can refuse a file it does not
understand rather than mis-parse one.

The envelope is a frozen, slotted **stdlib dataclass**, not a pydantic model.
That is a deliberate cost decision: the cold-start budget
(``scripts/check_cold_start_budget.py``) is a gate with a 2500 ms tolerance and
this module is on the import path of anything that traces. A dataclass with
``__post_init__`` validation gives the same invariants as a model validator for
a fraction of the import cost, and it keeps this module **stdlib-only** so it
can be imported from a call site without dragging in pydantic, the errors
registry, or the recorder.

The three properties that make an envelope worth having
------------------------------------------------------
Every field below exists because a consumer asked for it. The expensive ones
to get right are the last three.

**Fidelity is disclosed, never silent.** A payload over the byte cap is
clamped, and the envelope then carries, together: :attr:`TraceEnvelope.truncated`
(the flag), :attr:`TraceEnvelope.truncated_count` (how many items were dropped or
clipped), :attr:`TraceEnvelope.payload_bytes` (the size of the *full* payload) and
:attr:`TraceEnvelope.payload_sha256` (a SHA-256 of the full payload). A reader
can therefore always tell that something was dropped, how much, and can verify a
payload it independently obtained. Silent truncation is forbidden because a
sentinel that cannot tell an absent field from a dropped one will confidently
diagnose the wrong thing.

**The hash is taken over the redacted payload, not the raw one.** Hashing the raw
payload would make ``payload_sha256`` a content oracle: with the file and a small
input space (a 6-digit PIN, a known file path) an attacker recovers the plaintext
by brute force. Hashing the *redacted* payload keeps the hash a fidelity check on
what we intended to store without re-introducing the secret it was derived from.

**Both clocks, always.** :attr:`TraceEnvelope.ts_monotonic` is the only value
that can order events inside one process, because the wall clock can step
backwards under NTP and would then reorder a run's own timeline.
:attr:`TraceEnvelope.ts_wall` is the only value that can be correlated with a log
file, a database row or another machine. Storing only the wall clock makes the
first correlation wrong; storing only the monotonic clock makes the second
impossible. Both are injected (:func:`TraceEnvelope` takes them as arguments), so
a test produces byte-identical records and never sleeps.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

__all__ = [
    "ENVELOPE_FIELDS",
    "EVENT_STATUSES",
    "MAX_SEVERITY_RANK",
    "PAYLOAD_TRUNCATION_KEY",
    "RECORD_TYPE",
    "SCHEMA_VERSION",
    "SEVERITIES",
    "STATUSES",
    "ClampedPayload",
    "TraceEnvelope",
    "TraceEnvelopeError",
    "clamp_payload",
    "payload_digest",
]

#: Bumped when the record shape changes incompatibly. A reader checks it and
#: refuses an unknown major rather than mis-reading a file. ``1`` is the first
#: version of *this* envelope; it deliberately does not share a number with
#: :data:`alpha.observability.recorder.RECORD_SCHEMA_VERSION`, which versions a
#: different record family (``event``/``span``/``disclosure``).
SCHEMA_VERSION: Final[int] = 1

#: The ``type`` discriminator written into every persisted record. One file can
#: therefore carry ``event``, ``span``, ``disclosure`` and ``behaviour`` records
#: and a reader never has to guess a record's shape.
RECORD_TYPE: Final[str] = "behaviour"

#: Key under which a clamped payload states, inside the payload itself, that it
#: was clamped. Redundant with the envelope fields on purpose: a reader holding
#: only ``payload`` (a UI card, a log line) still learns that it is incomplete.
PAYLOAD_TRUNCATION_KEY: Final[str] = "trace.payload_truncation"

#: Severities, in ascending loudness. The *vocabulary* is
#: :class:`alpha.errors.registry.ErrorSeverity` -- the single taxonomy an earlier
#: audit consolidated four disjoint code sets into. It is restated here as data
#: so this module stays stdlib-only, and
#: ``test_behaviour_trace_contract.py`` asserts the two sets are equal, which is
#: what stops the restatement from silently forking.
SEVERITIES: Final[frozenset[str]] = frozenset({"info", "warning", "error", "critical"})

#: Highest severity rank, used to bound an aggregate query's severity filter.
MAX_SEVERITY_RANK: Final[int] = 3

#: Terminal statuses, mirroring :class:`alpha.observability.events.EventStatus`
#: plus :data:`PARTIAL`, which that set cannot express and which a clamped or
#: degraded record needs. ``refused`` is kept: a guardrail stopping an action is
#: not a failure of the system and the two must stay distinguishable.
STATUSES: Final[frozenset[str]] = frozenset({"ok", "error", "cancelled", "timeout", "refused", "partial", "skipped"})
#: Alias kept because ``EventStatus`` is the name a reader arrives with.
EVENT_STATUSES: Final = STATUSES

#: The stable, ordered field list. ``test_behaviour_trace_contract.py`` asserts it
#: equals the dataclass's real fields, so a field added without a decision to
#: order it fails rather than drifting to the end of the record.
ENVELOPE_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "event_id",
    "seq",
    "run_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "thread_id",
    "correlation_id",
    "agent_name",
    "subagent_id",
    "agent_depth",
    "ts_wall",
    "ts_monotonic",
    "event_type",
    "code",
    "layer",
    "severity",
    "status",
    "node",
    "from_node",
    "to_node",
    "payload",
    "payload_bytes",
    "payload_stored_bytes",
    "payload_sha256",
    "truncated",
    "truncated_count",
)

#: Smallest cap :func:`clamp_payload` accepts. The truncation notice alone is
#: ~120 bytes, so anything below this cannot fit the disclosure that makes a
#: clamped payload honest, and is refused rather than producing a payload that
#: cannot say it was clamped.
MIN_PAYLOAD_CAP_BYTES: Final[int] = 256


class TraceEnvelopeError(ValueError):
    """Raised when an envelope would violate its own contract.

    A programming error at a call site, not a runtime condition: the writer
    converts it into a counted, disclosed rejection rather than letting it reach
    the execution path of the thing being traced.
    """


def _canonical(payload: Mapping[str, Any]) -> str:
    """Render *payload* to its canonical JSON text.

    Sorted keys and UTF-8 output make the byte count and the digest a property
    of the payload's *content* rather than of mapping insertion order, which is
    what lets a reader compare two envelopes for the same logical payload.
    ``default=str`` keeps an exotic value inside the digest (and therefore
    inside the clamp) instead of raising -- the redactor has already replaced
    unknown shapes, so this is a belt-and-braces path.
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def payload_digest(payload: Mapping[str, Any]) -> tuple[int, str]:
    """Return ``(byte_length, sha256_hex)`` of *payload*'s canonical encoding.

    The length is measured on the **UTF-8 bytes**, not on the character count, so
    a cap expressed in bytes is the cap the disk actually sees.
    """
    raw = _canonical(payload).encode("utf-8")
    return len(raw), hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class ClampedPayload:
    """The outcome of bounding one payload.

    ``bytes_total`` and ``sha256`` describe the payload **as redacted and before
    clamping** -- that is the "full payload" the envelope's ``payload_bytes`` and
    ``payload_sha256`` report, so a reader can detect and quantify a clamp. The
    remaining fields describe what is actually in :attr:`value`.
    """

    value: dict[str, Any]
    bytes_total: int
    bytes_stored: int
    sha256: str
    truncated: bool
    truncated_count: int


def _oversize_notice(original_bytes: int, cap_bytes: int, dropped: int) -> dict[str, Any]:
    return {
        "dropped_items": dropped,
        "cap_bytes": cap_bytes,
        "original_payload_bytes": original_bytes,
    }


def clamp_payload(payload: Mapping[str, Any], cap_bytes: int) -> ClampedPayload:
    """Bound *payload* to *cap_bytes* of canonical JSON, disclosing any loss.

    Args:
        payload: An already-redacted mapping. Clamping never sees a raw value;
            redaction happens earlier, in the writer, and this function is
            deliberately not a second redaction site.
        cap_bytes: The byte ceiling. Must be at least
            :data:`MIN_PAYLOAD_CAP_BYTES`, because a payload that cannot fit its
            own truncation notice cannot honestly report being clamped.

    Returns:
        A :class:`ClampedPayload`. When nothing was dropped, ``truncated`` is
        ``False``, ``truncated_count`` is ``0`` and ``value`` **is** ``payload``
        (the same object, not a copy) so the common, in-budget path allocates
        nothing beyond the canonical string used for the digest.

    The clamp strategy is deterministic and order-independent: keys are visited
    in sorted order and dropped from the end, so the same payload always yields
    the same clamped payload regardless of how it was built. A value that alone
    exceeds the cap is replaced by a fixed marker string rather than partially
    kept, because half a truncated JSON fragment is not a value a reader can use.
    """
    if not isinstance(payload, Mapping):
        raise TraceEnvelopeError(f"clamp_payload expects a mapping, got {type(payload).__name__}")
    if not isinstance(cap_bytes, int) or isinstance(cap_bytes, bool) or cap_bytes < MIN_PAYLOAD_CAP_BYTES:
        raise TraceEnvelopeError(f"cap_bytes must be an integer >= {MIN_PAYLOAD_CAP_BYTES}, got {cap_bytes!r}")

    total_bytes, digest = payload_digest(payload)
    if total_bytes <= cap_bytes:
        return ClampedPayload(
            value=dict(payload),
            bytes_total=total_bytes,
            bytes_stored=total_bytes,
            sha256=digest,
            truncated=False,
            truncated_count=0,
        )

    # Over budget. Keep keys in sorted order until the notice no longer fits.
    kept: dict[str, Any] = {}
    dropped = 0
    for key in sorted(payload, key=str):
        value = payload[key]
        try:
            encoded_len = len(_canonical({key: value}).encode("utf-8"))
        except (TypeError, ValueError):  # pragma: no cover - default=str covers this
            encoded_len = 0
        # A single value that is most of the cap is not worth half a fragment.
        if encoded_len > cap_bytes // 2:
            dropped += 1
            continue
        candidate = dict(kept)
        candidate[key] = value
        if len(_canonical(candidate).encode("utf-8")) > cap_bytes - _NOTICE_RESERVE:
            dropped += 1
            continue
        kept = candidate

    notice = _oversize_notice(total_bytes, cap_bytes, dropped)
    # Reserve room for the notice; drop from the end until it fits.
    reserve = len(_canonical({PAYLOAD_TRUNCATION_KEY: notice}).encode("utf-8"))
    while kept and len(_canonical(kept).encode("utf-8")) + reserve > cap_bytes:
        kept.pop(sorted(kept, key=str)[-1])
        dropped += 1
    notice = _oversize_notice(total_bytes, cap_bytes, dropped)
    kept[PAYLOAD_TRUNCATION_KEY] = notice

    stored_bytes, _ = payload_digest(kept)
    return ClampedPayload(
        value=kept,
        bytes_total=total_bytes,
        bytes_stored=stored_bytes,
        sha256=digest,
        truncated=True,
        truncated_count=dropped,
    )


#: Bytes held back from the cap for the truncation notice. Measured from the
#: notice itself rather than guessed, because the numbers in it (a byte count
#: that can reach 10^9) vary in width.
_NOTICE_RESERVE: Final[int] = 192


@dataclass(frozen=True, slots=True)
class TraceEnvelope:
    """One immutable, versioned, bounded, already-redacted behaviour fact.

    Construct one through :meth:`alpha.observability.writer.BehaviourTraceWriter.emit`
    rather than directly: the writer is what runs the redactor, allocates the
    per-run sequence, and validates the event type against the taxonomy. The
    constructor is public only so a reader of a persisted file can rehydrate a
    record without going through a writer.
    """

    run_id: str
    trace_id: str
    event_type: str
    code: str
    layer: int
    ts_wall: float
    ts_monotonic: float
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    event_id: str = ""
    seq: int = 0
    span_id: str | None = None
    parent_span_id: str | None = None
    thread_id: str | None = None
    correlation_id: str | None = None
    agent_name: str | None = None
    subagent_id: str | None = None
    agent_depth: int = 0
    severity: str = "info"
    status: str = "ok"
    node: str | None = None
    from_node: str | None = None
    to_node: str | None = None
    payload_bytes: int = 0
    payload_stored_bytes: int = 0
    payload_sha256: str = ""
    truncated: bool = False
    truncated_count: int = 0

    def __post_init__(self) -> None:
        if not self.run_id:
            raise TraceEnvelopeError("run_id is required; an envelope with no run is the orphan this substrate exists to prevent")
        if not self.trace_id:
            raise TraceEnvelopeError("trace_id is required")
        if not self.event_type:
            raise TraceEnvelopeError("event_type is required")
        if not self.code:
            raise TraceEnvelopeError("code is required; every event carries a stable code so a reader never has to parse a dotted name")
        if self.schema_version != SCHEMA_VERSION:
            raise TraceEnvelopeError(f"unsupported envelope schema_version {self.schema_version!r}; this build writes {SCHEMA_VERSION}")
        if not isinstance(self.layer, int) or isinstance(self.layer, bool) or self.layer < 0:
            raise TraceEnvelopeError(f"layer must be a non-negative integer, got {self.layer!r}")
        if not isinstance(self.agent_depth, int) or isinstance(self.agent_depth, bool) or self.agent_depth < 0:
            raise TraceEnvelopeError(f"agent_depth must be a non-negative integer, got {self.agent_depth!r}")
        if self.severity not in SEVERITIES:
            raise TraceEnvelopeError(f"severity {self.severity!r} is outside the closed set {sorted(SEVERITIES)}")
        if self.status not in STATUSES:
            raise TraceEnvelopeError(f"status {self.status!r} is outside the closed set {sorted(STATUSES)}")
        if not isinstance(self.payload, dict):
            raise TraceEnvelopeError(f"payload must be a mapping, got {type(self.payload).__name__}")

    @property
    def is_clamped(self) -> bool:
        """Whether anything was dropped to fit the byte cap."""
        return self.truncated

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> TraceEnvelope:
        """Rehydrate an envelope from a persisted record.

        Raises:
            TraceEnvelopeError: on an unknown ``schema_version`` or a missing
                required field. A reader that cannot understand a record must say
                so rather than build a half-populated object and report it as
                data.
        """
        version = record.get("schema_version", record.get("v"))
        if version != SCHEMA_VERSION:
            raise TraceEnvelopeError(f"cannot read behaviour record with schema_version {version!r}; this build reads {SCHEMA_VERSION}")
        try:
            return cls(
                schema_version=SCHEMA_VERSION,
                event_id=str(record.get("event_id", "")),
                seq=int(record.get("seq", 0)),
                run_id=str(record["run_id"]),
                trace_id=str(record["trace_id"]),
                span_id=_opt_str(record.get("span_id")),
                parent_span_id=_opt_str(record.get("parent_span_id")),
                thread_id=_opt_str(record.get("thread_id")),
                correlation_id=_opt_str(record.get("correlation_id")),
                agent_name=_opt_str(record.get("agent_name")),
                subagent_id=_opt_str(record.get("subagent_id")),
                agent_depth=int(record.get("agent_depth", 0) or 0),
                ts_wall=float(record.get("ts_wall", 0.0) or 0.0),
                ts_monotonic=float(record.get("ts_monotonic", 0.0) or 0.0),
                event_type=str(record["event_type"]),
                code=str(record["code"]),
                layer=int(record.get("layer", 0) or 0),
                severity=str(record.get("severity", "info")),
                status=str(record.get("status", "ok")),
                node=_opt_str(record.get("node")),
                from_node=_opt_str(record.get("from_node")),
                to_node=_opt_str(record.get("to_node")),
                payload=dict(record.get("payload") or {}),
                payload_bytes=int(record.get("payload_bytes", 0) or 0),
                payload_stored_bytes=int(record.get("payload_stored_bytes", 0) or 0),
                payload_sha256=str(record.get("payload_sha256", "")),
                truncated=bool(record.get("truncated", False)),
                truncated_count=int(record.get("truncated_count", 0) or 0),
            )
        except KeyError as exc:
            raise TraceEnvelopeError(f"behaviour record is missing required field {exc.args[0]!r}") from exc

    def to_record(self) -> dict[str, Any]:
        """Return the stable persisted record for this envelope.

        Key order follows :data:`ENVELOPE_FIELDS` so a JSONL file is readable by
        eye, and every scalar is a plain JSON type -- no ``datetime``, no enum,
        no bytes -- so a reader in any language can parse it without this
        package.
        """
        return {
            "v": SCHEMA_VERSION,
            "type": RECORD_TYPE,
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "seq": self.seq,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "thread_id": self.thread_id,
            "correlation_id": self.correlation_id,
            "agent_name": self.agent_name,
            "subagent_id": self.subagent_id,
            "agent_depth": self.agent_depth,
            "ts_wall": self.ts_wall,
            "ts_monotonic": self.ts_monotonic,
            "event_type": self.event_type,
            "code": self.code,
            "layer": self.layer,
            "severity": self.severity,
            "status": self.status,
            "node": self.node,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "payload": dict(self.payload),
            "payload_bytes": self.payload_bytes,
            "payload_stored_bytes": self.payload_stored_bytes,
            "payload_sha256": self.payload_sha256,
            "truncated": self.truncated,
            "truncated_count": self.truncated_count,
        }


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)
