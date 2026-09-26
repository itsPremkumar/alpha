#!/usr/bin/env python3
"""Render an Alpha run trace (JSONL) as a readable timeline or as JSON.

Reads the versioned JSONL trace file written by
``alpha.observability.recorder.JsonlFileSink`` and renders it for a human
reading an incident. One record per line, each tagged ``event``, ``span`` or
``disclosure``; the three record types share a file so a reader never has to
correlate two of them by timestamp after the fact.

Determinism is the design constraint, because an incident timeline is read
twice and compared. Records are ordered by ``(ts, record type rank, span_id,
name)`` -- a total order, so the same file always renders byte-identically
regardless of the order the concurrent sinks happened to write in. Ties inside
one timestamp are the common case (a span start and its first event share a
clock reading), and an unstable sort there would make two runs of this script
differ for no reason.

Truncation is disclosed, always
-------------------------------
A trace file can hold a hundred thousand records, and a timeline nobody can read
is not a timeline. ``--max-events`` (default 500) bounds what is rendered, and
whenever anything is left out the output says so in three places: a header
line, a trailing line, and a ``truncated`` object in the JSON form. The
default *drop policy* keeps the first and last N records and drops the middle,
because in an incident the beginning (what was asked) and the end (what broke)
are the two ends worth keeping. ``--keep`` switches to keeping the first N.

Usage
-----
    python scripts/export_run_trace.py TRACE.jsonl
    python scripts/export_run_trace.py TRACE.jsonl --format json --max-events 50
    python scripts/export_run_trace.py TRACE.jsonl --run-id <32-hex> --trace-id <id>
    python scripts/export_run_trace.py TRACE.jsonl --status error

Exit codes
----------
0  rendered (possibly with a disclosed truncation)
1  the file is missing, unreadable, or not a trace file
2  bad arguments
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

RECORD_SCHEMA_VERSION = 1
#: Records whose ``type`` is not one of these are counted and skipped rather
#: than crashing the render: a forward-compatible writer must not make an
#: older reader useless.
KNOWN_TYPES = ("event", "span", "disclosure")
_TYPE_RANK = {"span": 0, "event": 1, "disclosure": 2}
DEFAULT_MAX_EVENTS = 500


class TraceReadError(RuntimeError):
    """The input is not a readable Alpha trace file."""


def read_records(path: str | Path) -> tuple[list[dict[str, Any]], int]:
    """Return ``(records, unparsable_line_count)`` for the trace at *path*.

    A line that is not valid JSON, or is not a JSON object, is counted and
    skipped. A trace file is append-only and a run can be killed mid-write, so
    a torn final line is a normal thing to find and must not lose the records
    before it.
    """
    target = Path(path)
    if not target.is_file():
        raise TraceReadError(f"trace file not found: {target}")
    records: list[dict[str, Any]] = []
    unparsable = 0
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TraceReadError(f"could not read {target}: {exc}") from exc
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            unparsable += 1
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
        else:
            unparsable += 1
    if not records:
        raise TraceReadError(f"{target} contains no trace records")
    return records, unparsable


def record_time(record: dict[str, Any]) -> float | None:
    """Return the record's timeline position, or ``None`` when it has none.

    A ``span`` record stores its position as ``start``/``end`` because a span is
    an interval, while an ``event`` stores a single ``ts``. Reading only ``ts``
    would sort every span to the top of the timeline and make a run's shape
    unreadable, so the two shapes are reconciled here, once.
    """
    for key in ("ts", "start"):
        value = record.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    """Total order over records, so a render is reproducible.

    Spans sort before events at the same timestamp so the reader sees the
    interval open before the thing that happened inside it, and disclosures
    sort last because they are commentary on everything else. The trailing
    components exist only to break ties between records that share a timestamp,
    a span id and a name -- a concurrent sink can interleave two writes that
    close together -- so the order never depends on write order.
    """
    kind = str(record.get("type", ""))
    timestamp = record_time(record)
    return (
        timestamp is None,
        timestamp if timestamp is not None else 0.0,
        _TYPE_RANK.get(kind, 9),
        str(record.get("span_id", "") or ""),
        str(record.get("name", "") or ""),
        json.dumps(record.get("attributes", {}), sort_keys=True, default=str),
    )


def select(
    records: list[dict[str, Any]], max_events: int, *, keep: str = "ends"
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(selected, dropped_count)``.

    ``keep="ends"`` keeps the first and last halves; ``keep="first"`` keeps the
    prefix. Both are disclosed by the caller through the returned drop count.
    """
    ordered = sorted(records, key=_sort_key)
    if max_events <= 0 or len(ordered) <= max_events:
        return ordered, 0
    if keep == "first":
        return ordered[:max_events], len(ordered) - max_events
    head = max_events // 2
    tail = max_events - head
    return ordered[:head] + ordered[len(ordered) - tail :], len(ordered) - max_events


def filter_records(
    records: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    trace_id: str | None = None,
    name: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Return the records matching every supplied filter (conjunctive)."""
    selected = records
    if run_id is not None:
        selected = [record for record in selected if record.get("run_id") == run_id]
    if trace_id is not None:
        selected = [record for record in selected if record.get("trace_id") == trace_id]
    if name is not None:
        selected = [record for record in selected if record.get("name") == name]
    if status is not None:
        selected = [
            record for record in selected if str(record.get("status", "")) == status
        ]
    return selected


def _format_attributes(attributes: dict[str, Any]) -> str:
    if not attributes:
        return ""
    parts = [
        f"{key}={json.dumps(value, sort_keys=True, default=str)}"
        for key, value in sorted(attributes.items())
    ]
    return "  " + " ".join(parts)


def render_text(
    selected: list[dict[str, Any]],
    *,
    dropped: int,
    total: int,
    unparsable: int,
    filters: dict[str, Any],
) -> str:
    """Render the timeline as text.

    Spans are drawn with an explicit ``start``/``end`` pair and their duration
    rather than a bar, because a bar needs a width budget and this tool's
    output has to stay diffable and line-wrappable.
    """
    lines: list[str] = []
    active_filters = {key: value for key, value in filters.items() if value}
    if active_filters:
        lines.append(
            "filters: "
            + " ".join(
                f"{key}={value}" for key, value in sorted(active_filters.items())
            )
        )
    lines.append(f"records: {total} shown: {len(selected)} dropped: {dropped}")
    if unparsable:
        # A torn final line is normal after a kill; saying so keeps a reader
        # from assuming the timeline is complete.
        lines.append(f"unparsable lines skipped: {unparsable}")
    if dropped:
        lines.append(
            f"TRUNCATED: {dropped} of {total} records were not rendered; the timeline above is incomplete"
        )
    lines.append("")

    base = _base_timestamp(selected)
    for record in selected:
        kind = str(record.get("type", ""))
        stamp = _format_offset(record_time(record), base)
        if kind == "span":
            depth = int(record.get("depth", 0) or 0)
            indent = "  " * depth
            status = str(
                record.get("status") or ("ended" if record.get("ended") else "open")
            )
            duration = record.get("duration")
            duration_text = (
                f" {float(duration) * 1000:.1f}ms"
                if isinstance(duration, (int, float)) and not isinstance(duration, bool)
                else ""
            )
            flags = " [depth-capped]" if record.get("depth_capped") else ""
            dropped_attributes = int(record.get("dropped_attributes", 0) or 0)
            dropped_text = (
                f" [dropped-attrs={dropped_attributes}]" if dropped_attributes else ""
            )
            lines.append(
                f"{stamp}  {indent}span {record.get('name')} [{status}]{duration_text}{flags}{dropped_text} span={record.get('span_id')}"
            )
            if record.get("parent_span_id"):
                lines.append(f"{stamp}  {indent}  parent={record['parent_span_id']}")
        elif kind == "event":
            link = ""
            if not record.get("context_inherited", True):
                # The disclosed link. A reader must be able to see that this
                # event joined the trace across a boundary rather than having
                # inherited it.
                link = f" [LINK {record.get('link_kind') or 'unspecified'}]"
            lines.append(
                f"{stamp}    event {record.get('name')} [{record.get('status')}]{link} run={record.get('run_id')}"
            )
        elif kind == "disclosure":
            lines.append(
                f"{stamp}  ** DISCLOSURE {record.get('reason')}: {json.dumps({k: v for k, v in record.items() if k not in ('v', 'type', 'ts')}, sort_keys=True, default=str)}"
            )
        else:
            lines.append(f"{stamp}  ?? unknown record type {kind!r}")
        attribute_text = _format_attributes(dict(record.get("attributes") or {}))
        if attribute_text:
            lines.append(f"{stamp}  {attribute_text.strip()}")
    if dropped:
        lines.append("")
        lines.append(f"TRUNCATED: {dropped} of {total} records omitted by --max-events")
    return "\n".join(lines) + "\n"


def _base_timestamp(records: list[dict[str, Any]]) -> float:
    """Return the earliest known position, used as the timeline's zero point."""
    stamps = [
        value
        for value in (record_time(record) for record in records)
        if value is not None
    ]
    return min(stamps) if stamps else 0.0


def _format_offset(value: Any, base: float) -> str:
    """Render the offset from the timeline's zero point, in milliseconds.

    The stored timestamps are unix **seconds**, so the conversion belongs here.
    Formatting the raw difference and labelling it ``ms`` would render every
    span that completes inside a millisecond as ``+0.00ms`` -- which is exactly
    the range a fast tool call lives in, i.e. the case that matters most.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "   +?.??ms"
    return f"{(float(value) - base) * 1000.0:+8.2f}ms"


def render_json(
    selected: list[dict[str, Any]],
    *,
    dropped: int,
    total: int,
    unparsable: int,
    filters: dict[str, Any],
) -> str:
    """Render the timeline as a JSON document with an explicit disclosure block."""
    return json.dumps(
        {
            "schema": RECORD_SCHEMA_VERSION,
            "filters": {key: value for key, value in filters.items() if value},
            "truncated": dropped > 0,
            "disclosure": {
                "total_records": total,
                "rendered_records": len(selected),
                "dropped_records": dropped,
                "unparsable_lines": unparsable,
            },
            "records": selected,
        },
        indent=2,
        sort_keys=False,
        default=str,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="export_run_trace",
        description="Render an Alpha run trace (JSONL) as a timeline or as JSON.",
    )
    parser.add_argument("trace", help="path to the trace JSONL file")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format (default: text)",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=DEFAULT_MAX_EVENTS,
        help=f"maximum records to render (default: {DEFAULT_MAX_EVENTS}); 0 means no bound",
    )
    parser.add_argument(
        "--keep",
        choices=("ends", "first"),
        default="ends",
        help="which records to keep when the bound bites (default: ends)",
    )
    parser.add_argument(
        "--run-id", default=None, help="render only records with this run id"
    )
    parser.add_argument(
        "--trace-id", default=None, help="render only records with this trace id"
    )
    parser.add_argument(
        "--name", default=None, help="render only records with this event/span name"
    )
    parser.add_argument(
        "--status", default=None, help="render only records with this status"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_events < 0:
        print("error: --max-events must be >= 0", file=sys.stderr)
        return 2
    try:
        records, unparsable = read_records(args.trace)
    except TraceReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    total_before_filter = len(records)
    filtered = filter_records(
        records,
        run_id=args.run_id,
        trace_id=args.trace_id,
        name=args.name,
        status=args.status,
    )
    selected, dropped = select(filtered, args.max_events, keep=args.keep)
    filters = {
        "run_id": args.run_id,
        "trace_id": args.trace_id,
        "name": args.name,
        "status": args.status,
    }
    if args.format == "json":
        print(
            render_json(
                selected,
                dropped=dropped,
                total=total_before_filter,
                unparsable=unparsable,
                filters=filters,
            )
        )
    else:
        print(
            render_text(
                selected,
                dropped=dropped,
                total=total_before_filter,
                unparsable=unparsable,
                filters=filters,
            ),
            end="",
        )
    if not filtered:
        print("note: no records matched the supplied filters", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
