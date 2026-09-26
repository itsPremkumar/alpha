"""Querying the trace: filters, the existing cursor, and aggregates.

The cursor is not a new idea. The Gateway already serves
``GET /threads/{id}/runs/{id}/events?after_seq=&event_types=&task_id=`` and the
durable store assigns ``seq`` per thread; :class:`TraceQuery` pages on the *same*
``seq``, so a reader that already pages the run feed pages the behaviour trace
with the same cursor and the same mental model, and one code path can serve both.

Every filter is optional and every combination is legal: an unfiltered query is
"the whole run in seq order", and a filtered one is a narrowing that never
changes the ordering or the cursor semantics. That matters because a query API
whose cursor changes meaning when you add a filter cannot be paged reliably.

Aggregates
----------
:func:`aggregate` is computed from the same filtered rows the list endpoint
returns, so an aggregate can never describe a different population than the page
above it. Latency percentiles come from the envelopes' own ``latency_ms`` /
``ttfb_ms`` payloads; the percentile definition is stated in
:func:`_percentile` rather than left to the reader's imagination.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from .codes import TraceSeverity
from .contract import TraceEnvelope, UnknownEnvelopeFieldError

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "TraceAggregate",
    "TraceFilter",
    "TracePage",
    "aggregate",
    "coerce_ints",
    "coerce_set",
    "count_unreadable_trace_rows",
    "envelopes_from_run_events",
    "matches",
    "query",
]


#: Page size default and cap. The cap matches the existing run-events route's
#: ``le=2000`` so one reader's notion of "a page" is the same for both endpoints.
DEFAULT_LIMIT: Final[int] = 500
MAX_LIMIT: Final[int] = 2000

#: A page may return fewer rows than ``limit`` because filters removed some, and
#: the caller must keep paging while ``has_more`` is true. It may also return
#: exactly ``limit`` with ``has_more`` true. It never returns ``limit`` rows with
#: ``has_more`` false and a further page waiting, so a caller that stops on
#: ``has_more`` cannot silently truncate.
_SCAN_SLACK: Final[int] = 4


@dataclass(frozen=True, slots=True)
class TraceFilter:
    """Every filter the query API accepts. All optional; all combinable.

    ``event_types`` and ``severities`` are sets, so a caller passing a
    comma-separated string from a query parameter and a caller passing a list
    get the same answer.
    """

    run_ids: frozenset[str] | None = None
    trace_ids: frozenset[str] | None = None
    thread_ids: frozenset[str] | None = None
    agent_names: frozenset[str] | None = None
    subagent_ids: frozenset[str] | None = None
    event_types: frozenset[str] | None = None
    layers: frozenset[int] | None = None
    severities: frozenset[str] | None = None
    min_severity: str | None = None
    nodes: frozenset[str] | None = None
    tools: frozenset[str] | None = None
    providers: frozenset[str] | None = None
    models: frozenset[str] | None = None
    error_codes: frozenset[str] | None = None
    since_ts: float | None = None
    until_ts: float | None = None
    include_truncated: bool = True
    only_truncated: bool = False

    def is_empty(self) -> bool:
        """Return whether this filter selects everything.

        Used by the route to skip the per-row predicate entirely on the common
        unfiltered read, and asserted in a test: a filter that reports itself
        empty while narrowing would make that optimisation silently lossy.
        """
        sets = (
            self.run_ids,
            self.trace_ids,
            self.thread_ids,
            self.agent_names,
            self.subagent_ids,
            self.event_types,
            self.layers,
            self.severities,
            self.nodes,
            self.tools,
            self.providers,
            self.models,
            self.error_codes,
        )
        scalars = (self.since_ts, self.until_ts, self.min_severity)
        return not any(sets) and not any(value is not None for value in scalars) and self.include_truncated and not self.only_truncated

    def describe(self) -> dict[str, Any]:
        """Return the active filters, for a response's echo of what was asked."""
        out: dict[str, Any] = {}
        for name in (
            "run_ids",
            "trace_ids",
            "thread_ids",
            "agent_names",
            "subagent_ids",
            "event_types",
            "layers",
            "severities",
            "nodes",
            "tools",
            "providers",
            "models",
            "error_codes",
        ):
            value = getattr(self, name)
            if value:
                out[name] = sorted(value)
        for name in ("since_ts", "until_ts", "min_severity"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        if not self.include_truncated:
            out["include_truncated"] = False
        if self.only_truncated:
            out["only_truncated"] = True
        return out


_SEVERITY_RANK: Final[dict[str, int]] = {member.value: index for index, member in enumerate(TraceSeverity)}


def coerce_set(value: Iterable[str] | str | None) -> frozenset[str] | None:
    """Coerce a comma-separated string or an iterable into a frozenset.

    Exported so the route's query parameters and a Python caller go through the
    *same* coercion. Two spellings of "a comma-separated list of event types"
    would eventually disagree about a trailing space, and the disagreement would
    look like a filter that silently matched nothing.
    """
    if value is None:
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
    else:
        parts = [str(part).strip() for part in value if str(part).strip()]
    return frozenset(parts) if parts else None


def coerce_ints(value: Iterable[int] | str | None) -> frozenset[int] | None:
    """Coerce a comma-separated string or an iterable into a frozenset of ints.

    Raises on a non-integer rather than dropping it: a layer filter of
    ``"2,banana"`` that silently became ``{2}`` would report a narrower result
    than the caller asked for, and nothing downstream could tell.
    """
    if value is None:
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
    else:
        parts = [str(part).strip() for part in value if str(part).strip()]
    if not parts:
        return None
    try:
        return frozenset(int(part) for part in parts)
    except ValueError as exc:
        raise ValueError(f"layer filter must be a comma-separated list of integers, got {value!r}") from exc


def matches(envelope: TraceEnvelope, criteria: TraceFilter) -> bool:
    """Return whether *envelope* satisfies every set filter in *criteria*.

    A pure function of the envelope, so the durable-store filter, the in-memory
    ring filter and the aggregate all agree by construction -- they all call this.
    """
    for attribute, wanted in (
        ("run_id", criteria.run_ids),
        ("trace_id", criteria.trace_ids),
        ("thread_id", criteria.thread_ids),
        ("agent_name", criteria.agent_names),
        ("subagent_id", criteria.subagent_ids),
        ("event_type", criteria.event_types),
        ("node", criteria.nodes),
        ("tool", criteria.tools),
        ("provider", criteria.providers),
        ("model", criteria.models),
        ("error_code", criteria.error_codes),
    ):
        if not wanted:
            continue
        value = getattr(envelope, attribute, None)
        if value is None or value not in wanted:
            return False
    if criteria.layers and int(envelope.layer) not in criteria.layers:
        return False
    if criteria.severities and envelope.severity.value not in criteria.severities:
        return False
    if criteria.min_severity is not None:
        if _SEVERITY_RANK.get(envelope.severity.value, 0) < _SEVERITY_RANK.get(criteria.min_severity, 0):
            return False
    if criteria.since_ts is not None and envelope.ts_wall < criteria.since_ts:
        return False
    if criteria.until_ts is not None and envelope.ts_wall > criteria.until_ts:
        return False
    if not criteria.include_truncated and envelope.truncated:
        return False
    if criteria.only_truncated and not envelope.truncated:
        return False
    return True


@dataclass(frozen=True, slots=True)
class TracePage:
    """One page of envelopes plus the cursor to continue from."""

    events: tuple[TraceEnvelope, ...]
    after_seq: int | None
    has_more: bool
    scanned: int
    total_matched: int | None = None

    def to_response(self) -> list[dict[str, Any]]:
        """The list shape the existing run-events route already returns."""
        return [envelope.to_record() for envelope in self.events]


def query(
    envelopes: Sequence[TraceEnvelope],
    criteria: TraceFilter | None = None,
    *,
    after_seq: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> TracePage:
    """Return one page of *envelopes* in seq order.

    Paging is on ``seq`` with a strict ``>`` comparison, so a caller that
    replays the last ``seq`` it saw never re-reads a row and never skips one
    when rows share a seq (which they cannot within a run, but can across runs
    in a merged read -- and ``>=`` would drop those).
    """
    active = criteria if criteria is not None else TraceFilter()
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    limit = min(limit, MAX_LIMIT)

    ordered = sorted(envelopes, key=lambda envelope: (envelope.seq, envelope.event_id))
    matched: list[TraceEnvelope] = []
    scanned = 0
    for envelope in ordered:
        if after_seq is not None and envelope.seq <= after_seq:
            continue
        scanned += 1
        if matches(envelope, active):
            matched.append(envelope)
        if len(matched) > limit:
            break

    has_more = len(matched) > limit
    page = matched[:limit]
    cursor = page[-1].seq if page else after_seq
    return TracePage(events=tuple(page), after_seq=cursor, has_more=has_more, scanned=scanned, total_matched=None if has_more else len(matched))


def envelopes_from_run_events(rows: Iterable[Mapping[str, Any]]) -> tuple[TraceEnvelope, ...]:
    """Rebuild envelopes from durable run-event rows.

    A row that is not a trace row at all -- a chat message, a legacy event with no
    envelope metadata -- is **not** an error and is not counted as one. Only a row
    that *declares* a ``schema_version`` and still cannot be read is a genuine
    skip, and :func:`count_unreadable_trace_rows` is the honest way to report it.

    Such a row must not fail the page: the route serves a run's whole history and
    one unreadable row must not make the other 4,000 unreachable. What is
    forbidden is skipping silently, which is why the aggregate carries the count
    alongside the rows it did return.
    """
    out: list[TraceEnvelope] = []
    for row in rows:
        try:
            out.append(TraceEnvelope.from_run_event(row))
        except (UnknownEnvelopeFieldError, KeyError, TypeError, ValueError):
            continue
    return tuple(out)


def count_unreadable_trace_rows(rows: Iterable[Mapping[str, Any]]) -> int:
    """Return how many *trace* rows in *rows* this build cannot decode.

    A row counts only when its metadata carries a ``schema_version``: that is the
    row claiming to be part of this contract. A chat message sharing the same feed
    is not a failed envelope, and counting it would report a decode failure that
    never happened -- a disclosure that cries wolf is one operators learn to
    ignore.
    """
    unreadable = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("schema_version") is None:
            continue
        try:
            TraceEnvelope.from_run_event(row)
        except (UnknownEnvelopeFieldError, KeyError, TypeError, ValueError):
            unreadable += 1
    return unreadable


# ---------------------------------------------------------------------------
# aggregates
# ---------------------------------------------------------------------------


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    """Nearest-rank percentile on the sorted sample. ``None`` for an empty sample.

    Nearest-rank rather than interpolated, and stated here rather than assumed: a
    p99 read off a 30-sample run should *be* the 30th worst sample, not a
    weighted average of the 30th and 31st, and "which percentile definition" is
    the question that makes two dashboards disagree about the same run.
    """
    if not values:
        return None
    ordered = sorted(values)
    if not 0.0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0, 1]")
    rank = max(1, min(len(ordered), int(-(-len(ordered) * fraction // 1))))
    return float(ordered[rank - 1])


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


@dataclass(frozen=True, slots=True)
class TraceAggregate:
    """Counts, tokens, cost, error counts and latency percentiles for one run."""

    total_events: int
    counts_by_type: dict[str, int]
    counts_by_layer: dict[str, int]
    counts_by_severity: dict[str, int]
    counts_by_agent: dict[str, int]
    error_counts: dict[str, int]
    truncated_events: int
    dropped_payload_items: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    cost_usd: float
    model_calls: int
    latency_ms_p50: float | None
    latency_ms_p95: float | None
    latency_ms_p99: float | None
    ttfb_ms_p50: float | None
    ttfb_ms_p99: float | None
    skipped_unreadable: int = 0

    def to_response(self) -> dict[str, Any]:
        return {
            "total_events": self.total_events,
            "counts_by_type": dict(sorted(self.counts_by_type.items())),
            "counts_by_layer": {key: self.counts_by_layer[key] for key in sorted(self.counts_by_layer, key=lambda item: int(item))},
            "counts_by_severity": dict(sorted(self.counts_by_severity.items())),
            "counts_by_agent": dict(sorted(self.counts_by_agent.items())),
            "error_counts": dict(sorted(self.error_counts.items())),
            "truncated_events": self.truncated_events,
            "dropped_payload_items": self.dropped_payload_items,
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "cached": self.cached_tokens,
                "reasoning": self.reasoning_tokens,
            },
            "cost_usd": round(self.cost_usd, 10),
            "model_calls": self.model_calls,
            "latency_ms": {"p50": self.latency_ms_p50, "p95": self.latency_ms_p95, "p99": self.latency_ms_p99},
            "ttfb_ms": {"p50": self.ttfb_ms_p50, "p99": self.ttfb_ms_p99},
            "skipped_unreadable": self.skipped_unreadable,
        }


#: The payload keys whose values contribute to a token total. Read from the model
#: layer's ``tokens`` mapping, which is the only place tokens are recorded, so an
#: aggregate cannot disagree with the events it summarises.
_TOKEN_KEYS: Final[tuple[str, str, str, str]] = ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens")


def aggregate(envelopes: Sequence[TraceEnvelope], *, skipped_unreadable: int = 0) -> TraceAggregate:
    """Summarise *envelopes*.

    Purely a function of the envelopes it is handed, which is what lets the route
    guarantee that an aggregate describes exactly the population the filtered page
    above it returned. Token and cost sums come from the model layer's payloads;
    latency percentiles from ``latency_ms`` and ``ttfb_ms`` on the same events.
    """
    counts_by_type: dict[str, int] = {}
    counts_by_layer: dict[str, int] = {}
    counts_by_severity: dict[str, int] = {}
    counts_by_agent: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    latencies: list[float] = []
    ttfbs: list[float] = []
    tokens = {key: 0 for key in _TOKEN_KEYS}
    cost = 0.0
    truncated = 0
    dropped = 0
    model_calls = 0

    for envelope in envelopes:
        counts_by_type[envelope.event_type] = counts_by_type.get(envelope.event_type, 0) + 1
        layer_key = str(envelope.layer)
        counts_by_layer[layer_key] = counts_by_layer.get(layer_key, 0) + 1
        counts_by_severity[envelope.severity.value] = counts_by_severity.get(envelope.severity.value, 0) + 1
        agent_key = envelope.agent_name or "(none)"
        counts_by_agent[agent_key] = counts_by_agent.get(agent_key, 0) + 1
        if envelope.error_code:
            error_counts[envelope.error_code] = error_counts.get(envelope.error_code, 0) + 1
        if envelope.truncated:
            truncated += 1
        dropped += envelope.dropped_items
        if envelope.event_type in ("model.call.completed", "model.call.failed", "model.call.requested"):
            model_calls += 1
        payload = envelope.payload
        latency = _as_float(payload.get("latency_ms"))
        if latency is not None:
            latencies.append(latency)
        ttfb = _as_float(payload.get("ttfb_ms"))
        if ttfb is not None:
            ttfbs.append(ttfb)
        nested = payload.get("tokens")
        if isinstance(nested, Mapping):
            for key in _TOKEN_KEYS:
                value = _as_int(nested.get(key))
                if value is not None:
                    tokens[key] += value
        cost_value = _as_float(payload.get("cost_usd"))
        if cost_value is not None:
            cost += cost_value

    return TraceAggregate(
        total_events=len(envelopes),
        counts_by_type=counts_by_type,
        counts_by_layer=counts_by_layer,
        counts_by_severity=counts_by_severity,
        counts_by_agent=counts_by_agent,
        error_counts=error_counts,
        truncated_events=truncated,
        dropped_payload_items=dropped,
        input_tokens=tokens["input_tokens"],
        output_tokens=tokens["output_tokens"],
        cached_tokens=tokens["cached_tokens"],
        reasoning_tokens=tokens["reasoning_tokens"],
        cost_usd=cost,
        model_calls=model_calls,
        latency_ms_p50=_percentile(latencies, 0.50),
        latency_ms_p95=_percentile(latencies, 0.95),
        latency_ms_p99=_percentile(latencies, 0.99),
        ttfb_ms_p50=_percentile(ttfbs, 0.50),
        ttfb_ms_p99=_percentile(ttfbs, 0.99),
        skipped_unreadable=skipped_unreadable,
    )
