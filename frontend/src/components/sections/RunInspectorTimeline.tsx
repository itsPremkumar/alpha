"use client";

import React, { useMemo, useState } from "react";
import { AlertTriangle, Flag, Waypoints } from "lucide-react";
import { Badge, ErrorBox } from "@/components/ui";
import { absoluteStamp, clockTime } from "@/lib/time";
import type { Timeline, TimelineEvent } from "@/lib/runs-inspector";
import {
  TIMELINE_FILTERS,
  filterCount,
  filterTimelineEvents,
  timelineFilterLabel,
  timelineNoMatchMessage,
  timelineSummary,
  type TimelineFilter,
  type TimelineFilterOption,
  type TimelineSummary,
} from "@/lib/runs-inspector-timeline";

/**
 * One run's event timeline: the summary, the three filters, and the list.
 *
 * Rendered where the inspector's inline timeline panel was, with the same props:
 *
 * ```tsx
 * <RunInspectorTimeline timeline={state.timeline} error={state.errors.timeline ?? null} />
 * ```
 *
 * Every derivation it needs (which rows a filter keeps, the counts beside the
 * buttons, the summary line, the sentence for a filter that matched nothing)
 * lives in `@/lib/runs-inspector-timeline`, so the numbers on screen are the
 * same ones the tests pin and this file only decides how they are worded and
 * laid out.
 *
 * The four states are kept apart on purpose, because each is a different claim
 * about the run: a **failed** read shows the Gateway's own reason and prints no
 * count at all; an **unread** one says it is reading; an **empty** one says the
 * Gateway reported no persisted events (the server's answer, not a failed
 * read); and a **partial** read says the bounded walk stopped early instead of
 * presenting a prefix as the run's whole stream.
 */

/* ── presentation helpers (mirrors the inspector's own panel vocabulary) ────── */

type Tone = "red" | "amber" | "gray";

/**
 * The colour for a severity. Only the colour is a presentation choice — the
 * word itself is always the run's own `severity`, rendered verbatim.
 */
function severityTone(severity: string): Tone {
  if (severity === "error") return "red";
  if (severity === "warn") return "amber";
  return "gray";
}

/** A time the server gave us, or the honest "not reported". */
function stamp(value: string | null): string {
  return absoluteStamp(value) ?? "time not reported";
}

/**
 * The short clock reading, or the same "not reported" wording.
 *
 * Deliberately not `--:--`: that glyph is what midnight looks like, and a row
 * the Gateway gave no time for must not read as though it were recorded at the
 * start of a day.
 */
function shortTime(value: string | null): string {
  return clockTime(value) ?? "time not reported";
}

/**
 * The inspector's panel chrome.
 *
 * The inspector's own `Panel` is private to `RunInspectorSection.tsx`, so the
 * same markup is reproduced here rather than imported: this component has to
 * read as one more panel in that stack, not as a differently shaped card.
 */
function Panel(props: { title: string; icon: React.ReactNode; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-border/60 bg-card p-4 space-y-3">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h3 className="text-sm font-semibold flex items-center gap-1.5">
          {props.icon}
          {props.title}
        </h3>
        {props.aside}
      </div>
      {props.children}
    </section>
  );
}

/** A bounded preview of a persisted payload. */
function Payload(props: { value: unknown; label: string; maxChars?: number }) {
  const limit = props.maxChars ?? 1800;
  let body: string | null = null;
  try {
    body = typeof props.value === "string" ? props.value : JSON.stringify(props.value, null, 2);
  } catch {
    body = null;
  }
  if (body === null) {
    return <p className="text-[11px] italic text-muted-foreground">{props.label} could not be read.</p>;
  }
  if (body.length > limit) {
    // A bounded preview that says how much it dropped; truncating silently would
    // let a short payload read as the whole record.
    return (
      <div className="space-y-1">
        <p className="text-[10px] font-semibold text-muted-foreground">
          {props.label} — first {limit} of {body.length} characters
        </p>
        <pre className="rounded-lg bg-muted/40 px-2 py-1.5 text-[10px] font-mono whitespace-pre-wrap break-all max-h-72 overflow-y-auto">
          {body.slice(0, limit)}
        </pre>
        <p className="text-[10px] text-amber-700 dark:text-amber-400">
          {body.length - limit} more characters are not shown; this preview is bounded, not the whole {props.label}.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-1">
      <p className="text-[10px] font-semibold text-muted-foreground">{props.label}</p>
      <pre className="rounded-lg bg-muted/40 px-2 py-1.5 text-[10px] font-mono whitespace-pre-wrap break-all max-h-72 overflow-y-auto">
        {body}
      </pre>
    </div>
  );
}

/* ── the filter controls ───────────────────────────────────────────────────── */

/**
 * The icon per filter.
 *
 * `All` and `Errors` reuse the inspector's own icons — the stream's `Waypoints`
 * and the failure `AlertTriangle` — so a filter and the thing it filters look
 * the same here as they do beside it. `Warnings` is deliberately a third glyph:
 * a warning on this stream is a *recorded intervention that is not a failure*,
 * and reusing the error triangle would erase the one distinction the run's own
 * severity makes.
 */
function FilterIcon(props: { id: TimelineFilter }) {
  if (props.id === "errors") return <AlertTriangle className="size-3" />;
  if (props.id === "warnings") return <Flag className="size-3" />;
  return <Waypoints className="size-3" />;
}

/**
 * One filter as a real button.
 *
 * `aria-pressed` carries the state, so the control is keyboard reachable and
 * reads as a toggle rather than as a link. The count beside the label is the
 * number of rows the button will show, and the label names only what it keeps —
 * an `Errors` button never lists a warning.
 */
function FilterButton(props: {
  option: TimelineFilterOption;
  active: boolean;
  count: number;
  onSelect: (id: TimelineFilter) => void;
}) {
  const { option } = props;
  return (
    <button
      type="button"
      aria-pressed={props.active}
      onClick={() => props.onSelect(option.id)}
      title={`Show only the ${option.label.toLowerCase()} this run recorded (${props.count})`}
      className={`inline-flex items-center gap-1.5 rounded-xl border px-2.5 py-1.5 text-[11px] transition-colors ${
        props.active
          ? "border-primary ring-1 ring-primary/30 bg-primary/5 font-semibold"
          : "border-border/60 hover:border-primary/40 text-muted-foreground hover:text-foreground"
      }`}
    >
      <FilterIcon id={option.id} />
      {option.label}
      <span className="font-mono text-[10px] text-muted-foreground">{props.count}</span>
    </button>
  );
}

/* ── the event rows ────────────────────────────────────────────────────────── */

function EventRow(props: { event: TimelineEvent }) {
  const { event } = props;
  return (
    <li className="rounded-lg bg-muted/30 px-2.5 py-1.5">
      <div className="flex items-center gap-2 flex-wrap text-[10px]">
        <Badge tone={severityTone(event.severity)}>{event.severity}</Badge>
        <span className="font-mono font-semibold">{event.eventType}</span>
        <span className="font-mono text-muted-foreground">{event.category}</span>
        {event.seq !== null && <span className="font-mono text-muted-foreground">seq {event.seq}</span>}
        {event.taskId !== null && <span className="font-mono text-muted-foreground">task {event.taskId}</span>}
        <span className="ml-auto font-mono text-muted-foreground" title={stamp(event.createdAt)}>
          {shortTime(event.createdAt)}
        </span>
      </div>
      <details className="mt-1">
        <summary className="text-[10px] text-muted-foreground cursor-pointer select-none">payload</summary>
        <div className="mt-1 space-y-1">
          <Payload value={event.content} label="content" maxChars={1800} />
          {Object.keys(event.metadata).length > 0 && <Payload value={event.metadata} label="metadata" maxChars={1200} />}
        </div>
      </details>
    </li>
  );
}

/* ── the summary line ──────────────────────────────────────────────────────── */

/** Counts and store-order time boundaries, in words the server supports. */
function SummaryLine(props: { summary: TimelineSummary }) {
  const { summary } = props;
  return (
    <p
      className="text-[10px] text-muted-foreground font-mono"
      title={`first ${stamp(summary.first)} · last ${stamp(summary.last)}`}
    >
      {summary.errors} error{summary.errors === 1 ? "" : "s"} · {summary.warnings} warning
      {summary.warnings === 1 ? "" : "s"} · {summary.info} info ·{" "}
      {summary.first === null || summary.last === null ? (
        "no event in this read reported a time"
      ) : (
        <>
          first {shortTime(summary.first)} · last {shortTime(summary.last)}
        </>
      )}
    </p>
  );
}

/* ── the surface ───────────────────────────────────────────────────────────── */

/** A stable empty list, so an unread timeline does not churn the memos below. */
const NO_EVENTS: TimelineEvent[] = [];

export function RunInspectorTimeline(props: { timeline: Timeline | null; error: string | null }): React.JSX.Element {
  const [filter, setFilter] = useState<TimelineFilter>("all");
  const events = props.timeline === null ? NO_EVENTS : props.timeline.events;
  const summary = useMemo(() => timelineSummary(events), [events]);
  const visible = useMemo(() => filterTimelineEvents(events, filter), [events, filter]);

  // A failed read is not an empty read, so nothing derived from the events is
  // shown at all — no count in the title, no summary, no filter counts.
  const failed = props.error !== null;

  return (
    <Panel
      title={`Event timeline${failed || !props.timeline ? "" : ` (${props.timeline.events.length})`}`}
      icon={<Waypoints className="size-4 text-primary" />}
      aside={
        failed || !props.timeline ? null : !props.timeline.complete ? (
          <Badge tone="amber">partial read</Badge>
        ) : (
          <Badge tone="gray">complete</Badge>
        )
      }
    >
      {props.error ? (
        <ErrorBox message={`This run's event stream could not be read, so the timeline is unavailable. (${props.error})`} />
      ) : props.timeline === null ? (
        <p className="text-[11px] text-muted-foreground">Reading the run&rsquo;s event stream&hellip;</p>
      ) : props.timeline.events.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          The Gateway reported no persisted events for this run. That is the server&rsquo;s answer, not a failed read.
        </p>
      ) : (
        <>
          <p className="text-[11px] text-muted-foreground">
            Every category the run journalled, in store order. The type and category are the server&rsquo;s own strings.
          </p>
          <SummaryLine summary={summary} />

          <div className="flex items-center gap-1.5 flex-wrap" role="group" aria-label="Filter this run's events">
            {TIMELINE_FILTERS.map((option) => (
              <FilterButton
                key={option.id}
                option={option}
                active={filter === option.id}
                count={filterCount(summary, option.id)}
                onSelect={setFilter}
              />
            ))}
          </div>

          {visible.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">{timelineNoMatchMessage(filter, summary)}</p>
          ) : (
            <ol className="space-y-1.5 max-h-[28rem] overflow-y-auto pr-1">
              {visible.map((event, i) => (
                <EventRow key={`${event.seq ?? "?"}-${i}`} event={event} />
              ))}
            </ol>
          )}

          {visible.length < summary.total && (
            <p className="text-[10px] text-muted-foreground">
              Showing {visible.length} of {summary.total} events. The other {summary.total - visible.length} are hidden
              by the {timelineFilterLabel(filter)} filter, not dropped.
            </p>
          )}

          {!props.timeline.complete && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">
              Partial: the bounded read stopped before the end of this run&rsquo;s event stream, so later events are not
              shown.
            </p>
          )}
        </>
      )}
    </Panel>
  );
}
