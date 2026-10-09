"use client";

/**
 * The tool-call blocks — how a turn's tool work is shown.
 *
 * Replaces the single generic "Used N tools" receipt with
 * kind-specialised blocks, because a shell command, a file
 * write, a web search and a file read are different shapes:
 *
 * - **terminal** calls render as `TerminalBlock` — the
 *   command, its streamed output and its exit code, with a
 *   one-click copy of the command.
 * - **file-write / file-edit** calls render as
 *   `FileWriteBlock` — the path, the `+N/−N` stat and the
 *   diff, with a one-click copy of the path.
 * - **search / research** calls render as
 *   `SearchResultsBlock` — the query pill, the source count,
 *   and result rows with domain avatars.
 * - **browser** calls render as `BrowserBlock` — the action
 *   verb, the live URL and the observation.
 * - **read** calls render as `ReadBlock` — the path, the
 *   size of what came back, and the content.
 * - everything else folds behind a receipt (the old
 *   "Used N tools" line), expanding to `ToolCallCard`s —
 *   with MCP servers split into their own chip.
 *
 * The split is by the tool's own name (`classifyTool`), so
 * it is a reading of what the run reported, never a guess.
 * Durations are client-observed (`ToolTiming`); a call
 * restored from history renders no duration, never `0s`.
 */

import React, { useEffect, useRef, useState } from "react";
import { ChevronDown, Wrench } from "lucide-react";
import type { ToolCall } from "@/types/chat";
import type { ToolTiming } from "@/lib/activity";
import { formatDuration, openTiming, settleTiming } from "@/lib/activity";
import {
  classifyTool,
  isBrowserTool,
  isFileWriteTool,
  isResearchTool,
  isSearchTool,
  isTerminalTool,
} from "@/lib/agent-ui";
import { TerminalBlock } from "./TerminalBlock";
import { FileWriteBlock } from "./FileWriteBlock";
import { SearchResultsBlock } from "./SearchResultsBlock";
import { BrowserBlock } from "./BrowserBlock";
import { ReadBlock } from "./ReadBlock";
import { ToolCallCard } from "./ToolCallCard";
import { TurnSummaryStrip } from "./TurnSummaryStrip";

interface AgentToolBlocksProps {
  toolCalls: ToolCall[];
  /** The run that issued these calls is still in flight. */
  live?: boolean;
}

export function AgentToolBlocks({
  toolCalls,
  live = false,
}: AgentToolBlocksProps) {
  const [timings, setTimings] = useState<Record<string, ToolTiming>>({});
  const [now, setNow] = useState(() => Date.now());
  const [othersOpen, setOthersOpen] = useState(false);
  const userChose = useRef(false);

  // Record first sighting and settlement here, not in the
  // reducer, so replaying a frame cannot move a timestamp.
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
  const openWork = toolCalls.some(
    (call) =>
      call.id &&
      timings[call.id]?.end === undefined &&
      timings[call.id]?.observedRunning,
  );

  // The "other tools" receipt folds open while generic work is
  // running; collapses back once every call reports. An explicit
  // click always wins.
  useEffect(() => {
    if (userChose.current) return;
    setOthersOpen(running > 0);
  }, [running]);

  useEffect(() => {
    if (!openWork) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [openWork]);

  if (toolCalls.length === 0) return null;

  const durationOf = (call: ToolCall) =>
    call.id ? formatDuration(timings[call.id], now) : "";

  const terminalCalls = toolCalls.filter((c) => isTerminalTool(c.name));
  const fileCalls = toolCalls.filter(
    (c) => isFileWriteTool(c.name) && !isTerminalTool(c.name),
  );
  const searchCalls = toolCalls.filter(
    (c) =>
      !isTerminalTool(c.name) &&
      !isFileWriteTool(c.name) &&
      isSearchTool(c.name),
  );
  const browserCalls = toolCalls.filter(
    (c) =>
      !isTerminalTool(c.name) &&
      !isFileWriteTool(c.name) &&
      !isSearchTool(c.name) &&
      isBrowserTool(c.name),
  );
  const readCalls = toolCalls.filter((c) => {
    if (
      isTerminalTool(c.name) ||
      isFileWriteTool(c.name) ||
      isSearchTool(c.name) ||
      isBrowserTool(c.name)
    )
      return false;
    return classifyTool(c.name) === "read";
  });
  const otherCalls = toolCalls.filter(
    (c) =>
      !isTerminalTool(c.name) &&
      !isFileWriteTool(c.name) &&
      !isSearchTool(c.name) &&
      !isBrowserTool(c.name) &&
      classifyTool(c.name) !== "read",
  );

  return (
    <div className="my-2 space-y-1" data-agent-tool-blocks>
      <TurnSummaryStrip toolCalls={toolCalls} />
      {terminalCalls.map((call) => (
        <TerminalBlock
          key={call.id}
          toolCall={call}
          inFlight={live}
          duration={durationOf(call)}
        />
      ))}

      {fileCalls.map((call) => (
        <FileWriteBlock
          key={call.id}
          toolCall={call}
          variant={
            classifyTool(call.name) === "file-edit" ? "file-edit" : "file-write"
          }
          duration={durationOf(call)}
        />
      ))}

      {searchCalls.map((call) => (
        <SearchResultsBlock
          key={call.id}
          toolCall={call}
          variant={isResearchTool(call.name) ? "research" : "search"}
          inFlight={live && call.status === undefined}
          duration={durationOf(call)}
        />
      ))}

      {browserCalls.map((call) => (
        <BrowserBlock
          key={call.id}
          toolCall={call}
          inFlight={live && call.status === undefined}
          duration={durationOf(call)}
        />
      ))}

      {readCalls.map((call) => (
        <ReadBlock
          key={call.id}
          toolCall={call}
          inFlight={live && call.status === undefined}
          duration={durationOf(call)}
        />
      ))}

      {otherCalls.length > 0 && (
        <OtherToolGroup
          calls={otherCalls}
          open={othersOpen}
          onToggle={() => {
            userChose.current = true;
            setOthersOpen((v) => !v);
          }}
          durationOf={durationOf}
          live={live}
        />
      )}
    </div>
  );
}

/** The folded receipt for calls without a specialised block. */
function OtherToolGroup({
  calls,
  open,
  onToggle,
  durationOf,
  live,
}: {
  calls: ToolCall[];
  open: boolean;
  onToggle: () => void;
  durationOf: (call: ToolCall) => string;
  live: boolean;
}) {
  const running = calls.filter((call) => !call.status).length;
  const names = calls
    .map((c) => c.name)
    .filter((v, i, a) => a.indexOf(v) === i)
    .slice(0, 3)
    .join(", ");
  const extra =
    calls.map((c) => c.name).filter((v, i, a) => a.indexOf(v) === i).length - 3;

  return (
    <div className="rounded-lg border border-border/60 bg-muted/30 text-xs overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-label={open ? "Hide tool calls" : "Show tool calls"}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        <Wrench className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
        <span className="shrink-0 font-semibold text-foreground/90">
          Used {calls.length} tool{calls.length === 1 ? "" : "s"}
        </span>
        {names && (
          <span className="truncate text-muted-foreground" title={names}>
            {names}
            {extra > 0 ? ` +${extra} more` : ""}
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
          {calls.map((call) => (
            <ToolCallCard
              key={call.id}
              toolCall={call}
              inFlight={live && call.status === undefined}
              duration={durationOf(call)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
