"use client";

import React, { useEffect, useRef, useState } from "react";
import { ChevronDown, Wrench } from "lucide-react";
import type { ToolCall } from "@/types/chat";
import type { ToolTiming } from "@/lib/activity";
import { formatDuration, openTiming, settleTiming, summarizeToolNames } from "@/lib/activity";
import { ToolPill } from "./ToolPill";

interface ToolGroupProps {
  toolCalls: ToolCall[];
  /**
   * The run that issued these calls is still in flight. Drives the live
   * open-on-work behaviour; it never changes what is reported as done.
   */
  live?: boolean;
}

/**
 * The collapsed receipt for a turn's tool work: `Used 4 tools · shell, exa`.
 *
 * Quiet by default — one line instead of a stack of cards — but it opens
 * itself while a call is genuinely running so "is anything happening?" is
 * answered without a click, and folds back to the receipt when the work
 * settles. An explicit click always wins over that behaviour, so the control
 * never fights the person using it.
 *
 * Durations are client-observed (see `ToolTiming`): the wire carries no start
 * timestamp, so a call restored from history renders no duration at all rather
 * than an invented one.
 */
export function ToolGroup({ toolCalls, live = false }: ToolGroupProps) {
  const [open, setOpen] = useState(false);
  const [timings, setTimings] = useState<Record<string, ToolTiming>>({});
  const [now, setNow] = useState(() => Date.now());
  const userChose = useRef(false);

  const total = toolCalls.length;

  // First sighting and settlement are recorded here, not in the reducer, so
  // replaying a frame cannot move a timestamp. The `changed` guard keeps a
  // re-renders-only update from re-rendering the group.
  useEffect(() => {
    setTimings((previous) => {
      const stamp = Date.now();
      let changed = false;
      const next = { ...previous };
      for (const call of toolCalls) {
        if (!call.id) continue;
        const existing = next[call.id];
        if (!existing) {
          next[call.id] = openTiming(stamp, call.status !== undefined);
          changed = true;
        } else if (existing.end === undefined && call.status !== undefined) {
          next[call.id] = settleTiming(existing, stamp);
          changed = true;
        }
      }
      return changed ? next : previous;
    });
  }, [toolCalls]);

  const running = toolCalls.filter((call) => !call.status).length;
  const openWork = toolCalls.some((call) => call.id && timings[call.id]?.end === undefined && timings[call.id]?.observedRunning);

  // Fold open while work is actually running; collapse back to the receipt
  // once every call reported. Never overrides an explicit click.
  useEffect(() => {
    if (userChose.current) return;
    setOpen(running > 0);
  }, [running]);

  // Tick only while a duration is genuinely being measured.
  useEffect(() => {
    if (!openWork) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [openWork]);

  if (total === 0) return null;
  const summary = summarizeToolNames(toolCalls);

  return (
    <div className="my-2 rounded-lg border border-border/60 bg-muted/30 text-xs overflow-hidden" data-tool-group-size={total}>
      <button
        type="button"
        onClick={() => {
          userChose.current = true;
          setOpen((value) => !value);
        }}
        aria-expanded={open}
        aria-label={open ? "Hide tool calls" : "Show tool calls"}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        <Wrench className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
        <span className="shrink-0 font-semibold text-foreground/90">
          Used {total} tool{total === 1 ? "" : "s"}
        </span>
        {summary && (
          <span className="truncate text-muted-foreground" title={summary}>
            {summary}
          </span>
        )}
        {running > 0 && (
          <span className="shrink-0 rounded-full bg-sky-500/15 px-1.5 py-0.5 text-[10px] font-medium text-sky-500">
            {running} running
          </span>
        )}
        <ChevronDown
          className={`ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "" : "-rotate-90"}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="space-y-1 border-t border-border/50 p-1.5">
          {toolCalls.map((call) => (
            <ToolPill
              key={call.id}
              toolCall={call}
              duration={call.id ? formatDuration(timings[call.id], now) : ""}
              inFlight={live && call.status === undefined}
            />
          ))}
        </div>
      )}
    </div>
  );
}
