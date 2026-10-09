"use client";

/**
 * The turn summary strip — "Edited files, ran commands".
 *
 * One quiet line above a turn's tool blocks naming what the
 * turn did, the way Codex-style transcripts summarise a turn
 * (`32 files changed +369 −103`). Every figure comes from
 * `summarizeTurnWork`: per-kind call counts plus the `+N/−N`
 * totalled across the diffs the turn actually reported. A
 * turn with no parsed diffs shows no change totals at all —
 * never `+0 −0`. A single tool call needs no summary, so the
 * strip renders nothing for it.
 */

import React from "react";
import { Files, Terminal, Search, BookOpen, Globe, Bot } from "lucide-react";
import { summarizeTurnWork } from "@/lib/agent-ui";
import type { ToolCall } from "@/types/chat";

interface TurnSummaryStripProps {
  toolCalls: ToolCall[];
}

export function TurnSummaryStrip({ toolCalls }: TurnSummaryStripProps) {
  if (toolCalls.length < 2) return null;
  const work = summarizeTurnWork(toolCalls);

  const parts: React.ReactNode[] = [];
  if (work.files > 0) {
    parts.push(
      <span key="files" className="inline-flex items-center gap-1">
        <Files className="size-3" aria-hidden="true" />
        {work.files} file{work.files === 1 ? "" : "s"} changed
        {work.added !== null && work.removed !== null && (
          <span className="font-mono tabular-nums">
            <span className="text-emerald-500">+{work.added}</span>
            <span className="text-muted-foreground"> </span>
            <span className="text-red-500">−{work.removed}</span>
          </span>
        )}
      </span>,
    );
  }
  if (work.commands > 0) {
    parts.push(
      <span key="commands" className="inline-flex items-center gap-1">
        <Terminal className="size-3" aria-hidden="true" />
        {work.commands} command{work.commands === 1 ? "" : "s"}
      </span>,
    );
  }
  if (work.searches > 0) {
    parts.push(
      <span key="searches" className="inline-flex items-center gap-1">
        <Search className="size-3" aria-hidden="true" />
        {work.searches} search{work.searches === 1 ? "" : "es"}
      </span>,
    );
  }
  if (work.reads > 0) {
    parts.push(
      <span key="reads" className="inline-flex items-center gap-1">
        <BookOpen className="size-3" aria-hidden="true" />
        {work.reads} read{work.reads === 1 ? "" : "s"}
      </span>,
    );
  }
  if (work.browser > 0) {
    parts.push(
      <span key="browser" className="inline-flex items-center gap-1">
        <Globe className="size-3" aria-hidden="true" />
        {work.browser} page{work.browser === 1 ? "" : "s"}
      </span>,
    );
  }
  if (work.agents > 0) {
    parts.push(
      <span key="agents" className="inline-flex items-center gap-1">
        <Bot className="size-3" aria-hidden="true" />
        {work.agents} agent{work.agents === 1 ? "" : "s"}
      </span>,
    );
  }
  if (work.other > 0) {
    parts.push(
      <span key="other">
        {work.other} other tool{work.other === 1 ? "" : "s"}
      </span>,
    );
  }
  if (parts.length === 0) return null;

  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-1 text-[11px] text-muted-foreground"
      data-turn-summary
    >
      {parts.map((part, i) => (
        <React.Fragment key={i}>
          {i > 0 && (
            <span className="text-muted-foreground/40" aria-hidden="true">
              ·
            </span>
          )}
          {part}
        </React.Fragment>
      ))}
    </div>
  );
}
