"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  RUN_ISSUE_EVENT_TYPES,
  RunEvent,
  fetchRunEventsPage,
  isRunIssueEvent,
  runEventSeverity,
  summarizeRunEvent,
} from "@/lib/runs";
import { Badge, Btn, EmptyState, ErrorBox, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { AlertTriangle, ChevronLeft, ChevronRight, ListTree, Pause, Play } from "lucide-react";

/** Delay between two automatic replay steps. */
const PLAY_INTERVAL_MS = 600;
/** Rows actually rendered; the counter always states the real replayed total. */
const MAX_RENDERED_ROWS = 150;

function severityTone(severity: ReturnType<typeof runEventSeverity>) {
  return severity === "error" ? "red" : severity === "warn" ? "amber" : "gray";
}

function eventTime(value: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleTimeString();
}

/**
 * Step through one run's persisted event stream.
 *
 * The stream is read with the REST forward cursor
 * (`GET /threads/{id}/runs/{id}/events?after_seq=&event_types=&limit=`), so a
 * long run is paged instead of fetched once and truncated. "Errors only"
 * narrows the *server* query to the event types that can carry failure
 * evidence, then refines it with each event's own status/action — a completed
 * subagent or a loop *warning* is not shown as an error.
 */
export function RunReplayControls(props: { threadId: string; runId: string }) {
  const { threadId, runId } = props;
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [complete, setComplete] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setPlaying(false);
    try {
      const page = await fetchRunEventsPage(threadId, runId, {
        eventTypes: errorsOnly ? RUN_ISSUE_EVENT_TYPES : null,
      });
      setEvents(page.events);
      setComplete(page.complete);
      setCursor(0);
    } catch (e) {
      // A failed read must never render as "this run had no events".
      setEvents([]);
      setComplete(false);
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, [threadId, runId, errorsOnly]);

  useEffect(() => {
    void load();
  }, [load]);

  const lastIndex = Math.max(events.length - 1, 0);
  const atEnd = events.length === 0 || cursor >= lastIndex;
  const atStart = cursor <= 0;

  // Auto-play advances one persisted event per tick and stops at the end.
  useEffect(() => {
    if (!playing || events.length === 0) return;
    const timer = setInterval(() => {
      setCursor((c) => (c + 1 > lastIndex ? c : c + 1));
    }, PLAY_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [playing, lastIndex, events.length]);

  useEffect(() => {
    if (playing && atEnd) setPlaying(false);
  }, [playing, atEnd]);

  const revealed = useMemo(() => events.slice(0, cursor + 1), [events, cursor]);
  const rendered = useMemo(() => revealed.slice(-MAX_RENDERED_ROWS), [revealed]);
  const hiddenRows = revealed.length - rendered.length;
  const current = events.length > 0 ? events[Math.min(cursor, lastIndex)] : null;
  const issueCount = useMemo(() => events.filter((e) => isRunIssueEvent(e)).length, [events]);

  const toggleErrors = () => {
    setErrorsOnly((v) => !v);
  };

  return (
    <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-3">
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <div>
          <h4 className="text-sm font-semibold flex items-center gap-1.5">
            <ListTree className="size-4 text-primary" /> Run replay
          </h4>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            {loading
              ? "Loading this run's event stream…"
              : errorsOnly
                ? `Errors only — ${events.length} issue event${events.length === 1 ? "" : "s"} of the ${RUN_ISSUE_EVENT_TYPES.length} failure-bearing types`
                : `${events.length} event${events.length === 1 ? "" : "s"} loaded${complete ? "" : " (partial — more exist)"}`}
            {!loading && !errorsOnly && !complete && events.length > 0 && " · older/later events were not loaded"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Btn variant="ghost" onClick={toggleErrors} title="Ask the server only for the event types that can carry failures, then keep the ones whose own status says so">
            <AlertTriangle className="size-3.5" /> {errorsOnly ? "Show all events" : "Errors only"}
          </Btn>
          <Btn variant="ghost" onClick={() => void load()} disabled={loading}>
            Reload
          </Btn>
        </div>
      </div>

      {error ? (
        <ErrorBox message={`Couldn't read this run's event stream — the event count below is unavailable, not zero. (${error})`} onRetry={() => void load()} />
      ) : loading ? (
        <SkeletonList rows={3} />
      ) : events.length === 0 ? (
        <EmptyState
          title={errorsOnly ? "No failure events recorded" : "No events recorded"}
          hint={
            errorsOnly
              ? "The server was asked only for the failure-bearing event types (run.error, llm.error, subagent.end, and the two stop middlewares) and returned none."
              : "The run's event stream came back empty."
          }
        />
      ) : (
        <>
          <div className="flex items-center gap-2 flex-wrap">
            <Btn variant="ghost" onClick={() => { setPlaying(false); setCursor((c) => Math.max(0, c - 1)); }} disabled={atStart} title="Previous event">
              <ChevronLeft className="size-3.5" /> Back
            </Btn>
            <Btn variant="ghost" onClick={() => setPlaying((p) => !p)} title={playing ? "Pause the replay" : "Play the recorded steps"}>
              {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />} {playing ? "Pause" : "Play"}
            </Btn>
            <Btn variant="ghost" onClick={() => { setPlaying(false); setCursor((c) => Math.min(lastIndex, c + 1)); }} disabled={atEnd} title="Next event">
              Next <ChevronRight className="size-3.5" />
            </Btn>
            <span className="text-[11px] text-muted-foreground font-mono">
              step {events.length === 0 ? 0 : cursor + 1} / {events.length}
            </span>
            {issueCount > 0 && (
              <span className="text-[11px] text-destructive">{issueCount} error{issueCount === 1 ? "" : "s"} in this stream</span>
            )}
          </div>

          <input
            type="range"
            min={0}
            max={lastIndex}
            step={1}
            value={events.length === 0 ? 0 : Math.min(cursor, lastIndex)}
            onChange={(e) => {
              setPlaying(false);
              setCursor(Number(e.target.value));
            }}
            aria-label="Scrub through the run's recorded events"
            className="w-full accent-primary"
          />

          {current && (
            <div className="rounded-xl bg-muted/40 px-2.5 py-2 text-[11px]">
              <span className="font-semibold">{current.event_type}</span>
              <span className="text-muted-foreground"> • {current.category}</span>
              {current.seq !== null && <span className="text-muted-foreground font-mono"> • seq {current.seq}</span>}
              {current.created_at && <span className="text-muted-foreground"> • {eventTime(current.created_at)}</span>}
              <div className="mt-1 font-mono break-words">{summarizeRunEvent(current) || "(no readable content)"}</div>
            </div>
          )}

          <div className="space-y-1 max-h-72 overflow-y-auto">
            {hiddenRows > 0 && (
              <p className="text-[10px] text-muted-foreground">
                {hiddenRows} earlier replayed row{hiddenRows === 1 ? "" : "s"} not rendered (showing the last {MAX_RENDERED_ROWS}).
              </p>
            )}
            {rendered.map((e) => {
              const severity = runEventSeverity(e);
              return (
                <div
                  key={`${e.seq ?? "?"}-${e.event_type}`}
                  className={`rounded-lg px-2 py-1.5 text-[11px] flex items-start gap-2 ${
                    e === current ? "bg-primary/10 ring-1 ring-primary/30" : "bg-muted/30"
                  }`}
                >
                  <Badge tone={severityTone(severity)}>{severity}</Badge>
                  <span className="font-mono text-[10px] text-muted-foreground w-10 shrink-0">
                    {e.seq === null ? "—" : e.seq}
                  </span>
                  <div className="min-w-0">
                    <div className="font-semibold truncate">
                      {e.event_type}
                      {e.task_id && <span className="text-muted-foreground font-normal"> · {e.task_id}</span>}
                    </div>
                    <div className="text-muted-foreground font-mono break-words">{summarizeRunEvent(e, 160) || "(no readable content)"}</div>
                  </div>
                  {e.created_at && <span className="ml-auto text-[10px] text-muted-foreground shrink-0">{eventTime(e.created_at)}</span>}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
