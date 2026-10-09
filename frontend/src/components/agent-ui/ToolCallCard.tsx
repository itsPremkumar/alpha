"use client";

/**
 * The general tool-call card.
 *
 * One card per tool call that is *not* a specialised
 * terminal / file-write block. It carries the tool's kind
 * icon and name, a compact argument summary in the collapsed
 * header, the honest status chip, a client-observed duration,
 * and — expanded — the full arguments and the reported output,
 * each with a one-click copy. The status verdict is read
 * from `toolStatusView`, the single authority; this card
 * never re-derives it.
 */

import React, { useState } from "react";
import {
  Bot,
  BookOpen,
  ChevronDown,
  CircleDashed,
  CircleSlash,
  FlaskConical,
  Globe,
  HelpCircle,
  Image as ImageIcon,
  ListChecks,
  Monitor,
  OctagonAlert,
  Search,
  ShieldAlert,
  Plug,
  Wrench,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import type { ToolCall } from "@/types/chat";
import {
  classifyTool,
  splitMcpName,
  summarizeArgs,
  toolKindMeta,
  type ToolKind,
} from "@/lib/agent-ui";
import { toolStatusView, type ToolStatusState } from "@/components/ToolPill";
import { CopyButton, KIND_BG, KIND_BORDER, KIND_TEXT } from "./shared";

interface ToolCallCardProps {
  toolCall: ToolCall;
  /** True while the run that issued this call is still in flight. */
  inFlight?: boolean;
  /** Client-observed duration, pre-formatted. Empty renders nothing. */
  duration?: string;
}

const KIND_ICONS: Record<ToolKind, LucideIcon> = {
  terminal: CircleDashed, // terminal renders via TerminalBlock; this is a fallback
  "file-write": ListChecks, // file writes render via FileWriteBlock
  "file-edit": ListChecks,
  search: Search, // search renders via SearchResultsBlock; fallback
  research: FlaskConical, // research renders via SearchResultsBlock; fallback
  browser: Globe, // browser renders via BrowserBlock; fallback
  read: BookOpen, // reads render via ReadBlock; fallback
  computer: Monitor,
  media: ImageIcon,
  web: Globe,
  mcp: Plug,
  agent: Bot,
  approval: ShieldAlert,
  generic: Wrench,
};

const STATUS_ICONS: Record<ToolStatusState, LucideIcon> = {
  success: ListChecks,
  error: OctagonAlert,
  failed: XCircle,
  partial: OctagonAlert,
  running: CircleDashed,
  unknown: HelpCircle,
  "not-reported": CircleSlash,
};

export function ToolCallCard({
  toolCall,
  inFlight = false,
  duration,
}: ToolCallCardProps) {
  const [open, setOpen] = useState(false);
  const kind = classifyTool(toolCall.name);
  const meta = toolKindMeta(kind);
  const KindIcon = KIND_ICONS[kind];
  const status = toolStatusView(
    toolCall.status === undefined && inFlight ? "running" : toolCall.status,
  );
  const StatusIcon = STATUS_ICONS[status.state];
  const summary = summarizeArgs(toolCall);
  const argsJson = JSON.stringify(toolCall.args, null, 2);
  const output = typeof toolCall.output === "string" ? toolCall.output : "";
  // MCP names carry their server (`mcp__github__create_issue`):
  // show the server as its own chip and the bare tool name, so an
  // external capability reads as external.
  const mcp = kind === "mcp" ? splitMcpName(toolCall.name) : null;

  return (
    <div
      className={`my-1.5 overflow-hidden rounded-lg border bg-muted/30 text-xs ${KIND_BORDER[meta.tone]}`}
      data-tool-kind={kind}
      data-tool-status={status.state}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title={status.title}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        <span
          className={`flex size-5 shrink-0 items-center justify-center rounded-md ${KIND_BG[meta.tone]}`}
        >
          <KindIcon
            className={`size-3 ${KIND_TEXT[meta.tone]}`}
            aria-hidden="true"
          />
        </span>
        <span
          className="shrink-0 font-mono font-semibold text-foreground/90"
          title={toolCall.name}
        >
          {mcp?.server ? mcp.tool : toolCall.name}
        </span>
        {mcp?.server && (
          <span
            className="shrink-0 rounded bg-fuchsia-500/10 px-1.5 py-px font-mono text-[9px] font-semibold text-fuchsia-500"
            title={`MCP server: ${mcp.server}`}
          >
            {mcp.server}
          </span>
        )}
        {summary && (
          <span
            className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground"
            title={summary}
          >
            {summary}
          </span>
        )}
        {duration && (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
            {duration}
          </span>
        )}
        <span
          className={`shrink-0 inline-flex items-center gap-1 text-[10px] font-medium ${status.className}`}
        >
          <StatusIcon
            className={`size-3 ${status.state === "running" ? "animate-spin" : ""}`}
            aria-hidden="true"
          />
          {status.label}
        </span>
        <ChevronDown
          className={`size-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "" : "-rotate-90"}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="space-y-2 border-t border-border/50 p-2.5">
          {argsJson !== "{}" && (
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Arguments
                </span>
                <CopyButton text={argsJson} label="Copy arguments" />
              </div>
              <pre className="max-h-40 overflow-auto rounded-md border border-border/50 bg-background/60 p-2 font-mono text-[10px] text-foreground/80">
                {argsJson}
              </pre>
            </div>
          )}
          {output && (
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {status.outputHeading ?? "Output"}
                </span>
                <CopyButton text={output} label="Copy output" />
              </div>
              <pre className="max-h-48 overflow-auto rounded-md border border-border/50 bg-background/60 p-2 font-mono text-[10px] text-foreground/80 whitespace-pre-wrap break-words">
                {output}
              </pre>
            </div>
          )}
          {!output && status.state === "not-reported" && (
            <div className="text-[11px] text-muted-foreground">
              No result reported yet
            </div>
          )}
        </div>
      )}
    </div>
  );
}
