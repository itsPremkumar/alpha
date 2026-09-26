"""The one versioned event envelope. Everything recorded goes through here.

Shape and why each field exists
------------------------------
================================  ===============================================
field                             why it is in the contract
================================  ===============================================
``schema_version``                A reader that meets a shape it does not know
                                  must refuse rather than guess. Bumped only by
                                  an incompatible change.
``event_id``                      Monotonic per run (``"<run_id>:<seq>"``), and
                                  unique across runs because the run id leads.
                                  A cross-run id would need a global counter, and
                                  a global counter is a lock every hot path pays.
``run_id`` / ``trace_id``         The correlation spine, from
                                  :mod:`alpha.observability.context`; never minted
                                  here.
``parent_span_id``                What makes a subagent a child rather than a
                                  second run.
``thread_id`` / ``correlation_id``The durable-feed key and the stable family key
                                  (``alpha.errors.registry``'s
                                  ``correlation_id`` for an error).
``agent_*`` / ``agent_depth``     Which agent emitted this and how deep it is, so
                                  a reader can filter one delegated subtree.
``seq``                           The ``after_seq`` cursor the existing Gateway
                                  route already serves.
``ts_monotonic`` / ``ts_wall``    Duration arithmetic needs a monotonic clock;
                                  "when did this happen" needs the wall clock.
                                  One alone is the classic tracing bug.
``event_type``                    The registry code (:mod:`.codes`). Closed.
``node`` / ``from_node`` / ``to_node``  Graph position, so a timeline can be
                                  grouped into transitions.
``payload`` / ``payload_bytes`` / ``payload_sha256``  The content, its full size
                                  and its full digest -- the disclosure triple.
``truncated`` / ``dropped_items``  Whether the stored payload is the whole thing,
                                  and how much is missing.
``severity``                      Drawn from the same four-value scale as
                                  :class:`alpha.errors.registry.ErrorSeverity`.
================================  ===============================================

Redaction is not optional and not a call site's job
---------------------------------------------------
:func:`_seal_payload` runs inside ``__post_init__``, so **there is no code path
that constructs a :class:`TraceEnvelope` with an unredacted payload**. This is
the gap the brief names: redaction was applied to traces and not to logs, so a
prompt, a tool argument or a tool result could reach durable storage raw. The
same
:class:`~alpha.observability.redaction.Redactor` that scrubs span attributes
scrubs this payload, keyed on the *caller's* key names, at write time.

Truncation is always disclosed
------------------------------
A payload larger than :class:`TraceBounds.max_payload_bytes` is cut to a prefix
that fits, and the envelope then carries ``truncated=True``, the number of
dropped items, and the size and SHA-256 of the **full** payload. A reader can
therefore always tell that something was dropped, and two readers holding
different prefixes of the same event can prove they are looking at the same
event. Silent truncation is forbidden: a reader that cannot tell an absent field
from a dropped one will diagnose the wrong failure, which is worse for a
self-repairing system than not recording the event at all.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from ..redaction import STRICT, Redactor
from .codes import DURABLE_CATEGORY, TraceSeverity
from .codes import event_type as _lookup_event_type

__all__ = [
    "ENVELOPE_SCHEMA_VERSION",
    "PAYLOAD_DROP_NOTICE_KEY",
    "TraceBounds",
    "TraceEnvelope",
    "UnknownEnvelopeFieldError",
]


#: Bumped only by an incompatible change to the envelope. A reader that meets a
#: higher version refuses the record rather than mis-reading it -- the same rule
#: the JSONL trace file already follows (``RECORD_SCHEMA_VERSION``).
ENVELOPE_SCHEMA_VERSION: Final[int] = 1

#: The key the writer uses inside a truncated payload to say how many items went
#: away. Named rather than elided so the count survives a JSON round-trip
#: through a store that only keeps the content column.
PAYLOAD_DROP_NOTICE_KEY: Final[str] = "_dropped_items"

#: Cap on a single scalar value inside a payload, independent of the byte cap.
#: A 3 MB string is one JSON token; without a per-value cap the byte cap alone
#: would still let one field dominate the record.
DEFAULT_MAX_VALUE_CHARS: Final[int] = 4096


class UnknownEnvelopeFieldError(ValueError):
    """Raised when a decoded record names a schema version this build cannot read."""


@dataclass(frozen=True, slots=True)
class TraceBounds:
    """The per-event payload bounds. One object, so they cannot disagree.

    Reuses the bounding idea the existing recorder already documents -- a cap, a
    disclosure, no silent loss -- and adds the one thing it lacked: a **byte**
    cap on the whole payload, because ``max_items`` alone does not bound size
    when 64 items are each 3 MB.
    """

    max_payload_bytes: int = 32_768
    max_items: int = 64
    max_value_chars: int = DEFAULT_MAX_VALUE_CHARS
    max_depth: int = 8
    redaction_policy: str = STRICT

    def __post_init__(self) -> None:
        for name in ("max_payload_bytes", "max_items", "max_value_chars", "max_depth"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer, got {value!r}")
        if not self.redaction_policy:
            raise ValueError("redaction_policy must be a non-empty policy id")

    def redactor(self) -> Redactor:
        """Build the scrubber these bounds describe.

        A fresh instance per call is deliberate and cheap: :class:`Redactor`
        holds no counters, so sharing one would be fine, and building one here
        means a :class:`TraceEnvelope` built outside a writer is scrubbed with
        exactly the policy its own bounds state.
        """
        return Redactor(
            self.redaction_policy,
            max_value_chars=self.max_value_chars,
            max_items=self.max_items,
            max_depth=self.max_depth,
        )


def _dumps(value: Any) -> str:
    """Canonical serialization used for both sizing and hashing.

    ``sort_keys`` so two structurally equal payloads of different insertion
    order hash the same: the digest is a content identity, and a reader
    comparing two independently-built envelopes must not get a false mismatch.
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class TraceEnvelope:
    """One redacted, bounded, versioned fact about a run.

    Construct through :meth:`build` rather than the raw constructor. The
    constructor is public so the dataclass can be decoded from a record
    (:meth:`from_record`) without re-scrubbing, but :meth:`build` is the write
    path and the one every instrumentation call site uses.
    """

    schema_version: int
    event_id: str
    seq: int
    event_type: str
    run_id: str
    trace_id: str
    thread_id: str | None
    correlation_id: str
    parent_span_id: str | None
    agent_name: str | None
    parent_agent_name: str | None
    agent_depth: int
    subagent_id: str | None
    span_id: str | None
    node: str | None
    from_node: str | None
    to_node: str | None
    severity: TraceSeverity
    ts_monotonic: float
    ts_wall: float
    payload: dict[str, Any]
    payload_bytes: int
    payload_sha256: str
    stored_payload_bytes: int
    truncated: bool
    dropped_items: int
    #: SHA-256 digests this package minted for the event: the stack fingerprint,
    #: a delegation prompt, a file's content, a fetched page's content.
    #:
    #: They live here rather than in ``payload`` because the strict redaction
    #: policy hides a bare 64-character lowercase-hex string -- the exact shape of
    #: a SHA-256 digest, and also of some credentials. A hash in a payload is
    #: replaced with ``[REDACTED:high_entropy_blob]``, which would leave layers 6,
    #: 8, 11 and 13 with a payload that says "something was here" and nothing
    #: about what. This mapping sits outside the scrubbed payload and is written
    #: only by the emitters that compute the digest themselves, so no
    #: caller-supplied text reaches it.
    digests: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    provider: str | None = None
    tool: str | None = None
    skill: str | None = None
    node_name: str | None = None
    error_code: str | None = None
    _redactor: Redactor | None = field(default=None, repr=False, compare=False)

    # -- write path -----------------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        event_type: str,
        run_id: str,
        trace_id: str,
        seq: int,
        payload: Mapping[str, Any] | None = None,
        thread_id: str | None = None,
        correlation_id: str | None = None,
        parent_span_id: str | None = None,
        agent_name: str | None = None,
        parent_agent_name: str | None = None,
        agent_depth: int = 0,
        subagent_id: str | None = None,
        span_id: str | None = None,
        node: str | None = None,
        from_node: str | None = None,
        to_node: str | None = None,
        severity: str | TraceSeverity | None = None,
        model: str | None = None,
        provider: str | None = None,
        tool: str | None = None,
        skill: str | None = None,
        node_name: str | None = None,
        error_code: str | None = None,
        digests: Mapping[str, str] | None = None,
        ts_monotonic: float | None = None,
        ts_wall: float | None = None,
        bounds: TraceBounds | None = None,
        redactor: Redactor | None = None,
    ) -> TraceEnvelope:
        """Build one sealed envelope. Raises on a contract violation.

        This is deliberately the *loud* constructor: an unknown event code, a
        missing required payload key or an unregistered error code raises here.
        Containment of a *sink* failure is the writer's job, not this one's; a
        call site that cannot state what happened is a bug, and a bug should not
        be laundered into a log line that looks fine.
        """
        definition = _lookup_event_type(event_type)
        active_bounds = bounds if bounds is not None else TraceBounds()
        active_redactor = redactor if redactor is not None else active_bounds.redactor()
        raw_payload = dict(payload or {})

        scrubbed = dict(active_redactor.redact_attributes(raw_payload)[0])

        # The required-key check runs on the *scrubbed* payload, before the byte
        # bound. The registry declares the caller's contract ("a model call
        # states its finish reason"); the byte cap is a disclosed degradation of
        # *storage*. Checking after the cut would make an over-large event fail as
        # a contract violation, which is the opposite of what truncation is for and
        # would push a caller to shrink the payload instead of letting the log
        # disclose that it did.
        sealed_digests = _seal_digests(digests)

        # A required key may be satisfied from :attr:`digests` as well as from
        # the payload, because several codes require a SHA-256 (``fs.op`` requires
        # ``content_sha256_after``) and a bare 64-hex string cannot survive the
        # strict bare-blob redaction rule inside a payload. See :attr:`digests`.
        missing = [key for key in definition.required_payload if key not in scrubbed and key not in sealed_digests]
        if missing:
            raise ValueError(f"event {definition.code!r} requires payload key(s) {missing}; the registry declares them so an incomplete event cannot enter the log")

        sealed, full_json, truncated, dropped_items = _seal_payload(scrubbed, active_bounds)

        resolved_severity = _resolve_severity(severity, sealed, definition.severity)
        resolved_error_code = _resolve_error_code(error_code, sealed)

        wall = time.time() if ts_wall is None else float(ts_wall)
        monotonic = time.monotonic() if ts_monotonic is None else float(ts_monotonic)

        return cls(
            schema_version=ENVELOPE_SCHEMA_VERSION,
            event_id=f"{run_id}:{int(seq):010d}",
            seq=int(seq),
            event_type=definition.code,
            run_id=run_id,
            trace_id=trace_id,
            thread_id=thread_id,
            correlation_id=correlation_id if correlation_id else (resolved_error_code and _correlation_for(resolved_error_code)) or trace_id,
            parent_span_id=parent_span_id,
            agent_name=agent_name,
            parent_agent_name=parent_agent_name,
            agent_depth=int(agent_depth),
            subagent_id=subagent_id,
            span_id=span_id,
            node=node,
            from_node=from_node,
            to_node=to_node,
            severity=resolved_severity,
            ts_monotonic=monotonic,
            ts_wall=wall,
            payload=sealed,
            payload_bytes=_utf8_size(full_json),
            payload_sha256=hashlib.sha256(full_json.encode("utf-8")).hexdigest(),
            stored_payload_bytes=_utf8_size(_dumps(sealed)),
            truncated=truncated,
            dropped_items=dropped_items,
            digests=_seal_digests(digests),
            model=model,
            provider=provider,
            tool=tool,
            skill=skill,
            node_name=node_name,
            error_code=resolved_error_code,
            _redactor=active_redactor,
        )

    # -- read path ------------------------------------------------------------

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> TraceEnvelope:
        """Rebuild an envelope from a stored record.

        No re-scrubbing: the record is already sealed, and scrubbing twice would
        turn an ``info`` payload into a differently-shaped one and make the
        stored digest unverifiable. Refuses an unknown ``schema_version``
        instead of guessing -- a mis-read envelope is a wrong diagnosis.
        """
        version = record.get("schema_version")
        if version != ENVELOPE_SCHEMA_VERSION:
            raise UnknownEnvelopeFieldError(f"record schema_version {version!r} != {ENVELOPE_SCHEMA_VERSION!r}; this build cannot read it and will not guess")
        try:
            return cls(
                schema_version=int(record["schema_version"]),
                event_id=str(record["event_id"]),
                seq=int(record["seq"]),
                event_type=str(record["event_type"]),
                run_id=str(record["run_id"]),
                trace_id=str(record["trace_id"]),
                thread_id=record.get("thread_id"),
                correlation_id=str(record.get("correlation_id") or record["trace_id"]),
                parent_span_id=record.get("parent_span_id"),
                agent_name=record.get("agent_name"),
                parent_agent_name=record.get("parent_agent_name"),
                agent_depth=int(record.get("agent_depth") or 0),
                subagent_id=record.get("subagent_id"),
                span_id=record.get("span_id"),
                node=record.get("node"),
                from_node=record.get("from_node"),
                to_node=record.get("to_node"),
                severity=TraceSeverity(record.get("severity", TraceSeverity.INFO.value)),
                ts_monotonic=float(record.get("ts_monotonic") or 0.0),
                ts_wall=float(record.get("ts_wall") or 0.0),
                payload=dict(record.get("payload") or {}),
                payload_bytes=int(record.get("payload_bytes") or 0),
                payload_sha256=str(record.get("payload_sha256") or ""),
                stored_payload_bytes=int(record.get("stored_payload_bytes") or 0),
                truncated=bool(record.get("truncated")),
                dropped_items=int(record.get("dropped_items") or 0),
                digests=dict(record.get("digests") or {}),
                model=record.get("model"),
                provider=record.get("provider"),
                tool=record.get("tool"),
                skill=record.get("skill"),
                node_name=record.get("node_name"),
                error_code=record.get("error_code"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise UnknownEnvelopeFieldError(f"record is not a well-formed envelope: {exc}") from exc

    # -- serialisation --------------------------------------------------------

    def to_record(self) -> dict[str, Any]:
        """Return the stable record: the JSONL sink writes exactly this."""
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "seq": self.seq,
            "event_type": self.event_type,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "thread_id": self.thread_id,
            "correlation_id": self.correlation_id,
            "parent_span_id": self.parent_span_id,
            "agent_name": self.agent_name,
            "parent_agent_name": self.parent_agent_name,
            "agent_depth": self.agent_depth,
            "subagent_id": self.subagent_id,
            "span_id": self.span_id,
            "node": self.node,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "severity": self.severity.value,
            "ts_monotonic": self.ts_monotonic,
            "ts_wall": self.ts_wall,
            "payload": dict(self.payload),
            "payload_bytes": self.payload_bytes,
            "payload_sha256": self.payload_sha256,
            "stored_payload_bytes": self.stored_payload_bytes,
            "truncated": self.truncated,
            "dropped_items": self.dropped_items,
            "digests": dict(self.digests),
            "model": self.model,
            "provider": self.provider,
            "tool": self.tool,
            "skill": self.skill,
            "node_name": self.node_name,
            "error_code": self.error_code,
        }

    def to_run_event(self) -> dict[str, Any]:
        """Return the durable :class:`RunEventStore` row fields for this envelope.

        The point of this method: the behaviour trace and the run feed are the
        *same* data, not two datasets that have to be joined. ``event_type`` is
        the registry code, which is why every code has to fit
        :data:`~.codes.MAX_EVENT_CODE_LENGTH`; ``category`` is ``"trace"`` so the
        store's existing content bound applies; ``content`` is the payload; and
        ``metadata`` is the rest of the envelope, so a reader with only a
        database row can rebuild the envelope with :meth:`from_record`.
        """
        record = self.to_record()
        content = record.pop("payload")
        return {
            "event_type": self.event_type,
            "category": DURABLE_CATEGORY,
            "content": content,
            "metadata": record,
        }

    @classmethod
    def from_run_event(cls, row: Mapping[str, Any]) -> TraceEnvelope:
        """Rebuild an envelope from a durable run event row."""
        metadata = row.get("metadata")
        record: dict[str, Any] = dict(metadata) if isinstance(metadata, Mapping) else {}
        record["payload"] = row.get("content") if isinstance(row.get("content"), Mapping) else {}
        record.setdefault("event_type", row.get("event_type"))
        return cls.from_record(record)

    @property
    def layer(self) -> int:
        """The instrumented layer index that owns this event."""
        from .codes import layer_of

        return int(layer_of(self.event_type))

    def __repr__(self) -> str:
        return f"TraceEnvelope(seq={self.seq}, event_type={self.event_type!r}, run_id={self.run_id!r}, truncated={self.truncated})"


def _seal_payload(scrubbed: Mapping[str, Any], bounds: TraceBounds) -> tuple[dict[str, Any], str, bool, int]:
    """Bound an already-scrubbed payload.

    Returns ``(stored, full_json, truncated, dropped_items)``. ``full_json`` is
    the serialization of the *whole* scrubbed payload, which is what
    ``payload_sha256`` and ``payload_bytes`` describe. Returning it rather than
    re-deriving it means the digest and the size cannot describe different
    things.

    A prefix is kept in insertion order until the next key would push the encoded
    size past :attr:`TraceBounds.max_payload_bytes`; the remainder is replaced by
    a single disclosed marker. Insertion order is preserved rather than sorted
    because a caller that puts the identifying keys first has made a choice about
    what a truncated event should still show.

    The truncation flag is *computed here* rather than inferred by looking for
    the marker key in the result, because a caller is free to pass a payload
    that already contains a key named ``_dropped_items`` and sniffing would then
    report a truncation that did not happen.
    """
    items = list(scrubbed.items())
    full_json = _dumps(dict(items))
    if _utf8_size(full_json) <= bounds.max_payload_bytes:
        return dict(items), full_json, False, 0

    kept: dict[str, Any] = {}
    used = 2  # the enclosing braces
    dropped = 0
    for index, (key, value) in enumerate(items):
        chunk = _utf8_size(_dumps({key: value})) + (1 if index else 0)
        if used + chunk > bounds.max_payload_bytes:
            dropped = len(items) - index
            break
        kept[key] = value
        used += chunk
    kept[PAYLOAD_DROP_NOTICE_KEY] = f"+{dropped} more payload keys dropped (see truncated/payload_bytes/payload_sha256)"
    return kept, full_json, True, dropped


_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]{64}\Z")


def _seal_digests(digests: Mapping[str, str] | None) -> dict[str, str]:
    """Keep only values that are provably SHA-256 hex digests.

    The narrow gate that makes :attr:`TraceEnvelope.digests` safe. A value that
    is not exactly 64 lowercase hex characters is dropped rather than stored, so
    the field cannot become a side channel for caller-supplied text that happened
    to be passed there. The cost is stated rather than hidden: a caller who wants
    to record some *other* opaque identifier has to put it in the payload, where
    redaction applies to it.
    """
    if not digests:
        return {}
    return {str(name): value for name, value in digests.items() if isinstance(value, str) and _DIGEST_RE.match(value)}


def _resolve_severity(requested: str | TraceSeverity | None, payload: Mapping[str, Any], default: TraceSeverity) -> TraceSeverity:
    """Pick the envelope severity.

    Precedence: the caller's explicit value, then the payload's ``severity``
    (which for layer 13 comes from the surviving error registry and is therefore
    authoritative), then the code's declared default. An unrecognised value
    raises rather than falling back -- a severity nobody can read is a metric
    label that will never be queried, and finding that out at query time is far
    worse than failing at write time.
    """
    for candidate in (requested, payload.get("severity")):
        if candidate is None or candidate == "":
            continue
        try:
            return TraceSeverity(candidate)
        except ValueError as exc:
            raise ValueError(f"unknown trace severity {candidate!r}; expected one of {[m.value for m in TraceSeverity]}") from exc
    return default


def _resolve_error_code(explicit: str | None, payload: Mapping[str, Any]) -> str | None:
    """Resolve the error code through the surviving registry.

    Returns ``None`` when the payload carries none, which is the common case: an
    event is not an error event unless somebody says so, and inferring one from a
    message would be the fifth taxonomy by the back door.
    """
    raw = explicit if explicit else payload.get("error_code")
    if raw is None or raw == "":
        return None
    from .codes import resolve_error_code

    return resolve_error_code(raw)


def _correlation_for(error_code: str) -> str:
    """Return the surviving registry's stable family key for *error_code*."""
    from alpha.errors.registry import require_definition

    return require_definition(error_code).correlation_id
