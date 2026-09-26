"use client";

import React, { useState } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  CircleSlash,
  HelpCircle,
  OctagonAlert,
  Send,
  ShieldAlert,
  Sparkles,
  Terminal,
  XCircle,
} from "lucide-react";
import type { ToolCall, ToolCallStatus } from "@/types/chat";

/**
 * Every state a tool call can be shown in.
 *
 * `"not-reported"` (no result arrived yet) and `"unknown"` (a result arrived
 * but nobody could resolve a verdict) are deliberately separate states: the
 * first is "we do not know yet", the second is "we looked and it is unknown".
 * Neither is ever drawn as success.
 */
export type ToolStatusState =
  | "success"
  | "error"
  | "failed"
  | "partial"
  | "running"
  | "unknown"
  | "not-reported";

export type ToolStatusView = {
  /** Resolved state; drives the icon, colour, wording and data attribute. */
  state: ToolStatusState;
  /** True only for a result the run actually reported as completed. */
  isSuccess: boolean;
  /** True for any state that must never read as success. */
  isFailure: boolean;
  label: string;
  /** Accessible description of the state. */
  title: string;
  /** Tailwind classes for both the status icon and the status label. */
  className: string;
  /** Heading for the tool-output block, or null when there is nothing to show. */
  outputHeading: string | null;
};

const VIEWS: Record<ToolStatusState, Omit<ToolStatusView, "state" | "isSuccess" | "isFailure">> = {
  success: {
    label: "completed",
    title: "The run reported this tool call as completed.",
    className: "text-emerald-500",
    outputHeading: "Result",
  },
  error: {
    label: "error",
    title: "The run reported an error for this tool call.",
    className: "text-red-500",
    outputHeading: "Error output",
  },
  failed: {
    label: "failed",
    title: "The run reported this tool call as failed.",
    className: "text-red-500",
    outputHeading: "Error output",
  },
  partial: {
    label: "partial",
    title: "The run reported only partial results for this tool call.",
    className: "text-amber-500",
    outputHeading: "Partial result",
  },
  running: {
    label: "running",
    title: "This tool call is still in flight.",
    className: "text-sky-500",
    outputHeading: "Output so far",
  },
  unknown: {
    label: "unknown",
    title: "A result arrived for this tool call but reported an unknown outcome.",
    className: "text-muted-foreground",
    outputHeading: "Unverified output",
  },
  "not-reported": {
    label: "no result reported",
    title: "No result has been reported for this tool call.",
    className: "text-muted-foreground",
    outputHeading: null,
  },
};

const STATUS_ICONS = {
  success: CheckCircle2,
  error: OctagonAlert,
  failed: XCircle,
  partial: AlertTriangle,
  running: CircleDashed,
  unknown: HelpCircle,
  "not-reported": CircleSlash,
} as const;

/**
 * Map a reported `ToolCall.status` onto what the pill actually shows.
 *
 * A missing status, or a status outside the known set, resolves to
 * `"not-reported"` — never to `"success"`. The pill has no independent opinion
 * about whether a call worked; it only reports what the run said.
 */
export function toolStatusView(status?: ToolCallStatus): ToolStatusView {
  const state: ToolStatusState = status === "completed" ? "success"
    : typeof status === "string" && status in VIEWS ? status
      : "not-reported";
  return {
    state,
    isSuccess: state === "success",
    isFailure: state !== "success",
    ...VIEWS[state],
  };
}

interface ToolPillProps {
  toolCall: ToolCall;
  /** Start expanded. Optional; the collapsed pill is the default. */
  defaultOpen?: boolean;
}

export function ToolPill({ toolCall, defaultOpen = false }: ToolPillProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const isA2A = toolCall.name === "message_agent";
  const isApproval = toolCall.name === "request_approval";
  const isGatekeeper = toolCall.name === "verify_and_complete";
  const isAudit = toolCall.name === "audit_code";

  const targetBot = isA2A ? String(toolCall.args?.target || "teammate") : null;
  const msgContent = isA2A ? String(toolCall.args?.message || "") : null;

  const status = toolStatusView(toolCall.status);
  const StatusIcon = STATUS_ICONS[status.state];

  return (
    <div
      className={`my-1.5 rounded-lg border text-xs overflow-hidden transition-colors ${
        isA2A ? "border-blue-500/40 bg-blue-500/5 dark:bg-blue-500/10" : "border-border/80 bg-muted/40"
      }`}
      data-tool-id={toolCall.id}
      data-tool-status={status.state}
    >
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        title={status.title}
        aria-expanded={isOpen}
        className="flex w-full items-center justify-between px-3 py-1.5 font-mono text-muted-foreground hover:bg-muted/70 transition-colors"
      >
        <div className="flex items-center gap-2">
          {isA2A ? (
            <>
              <Bot className="size-3.5 text-blue-500" />
              <span className="font-semibold text-blue-500">Agent-to-Agent DM</span>
              <span className="text-[11px] font-sans px-1.5 py-0.2 rounded bg-blue-500/20 text-blue-400 font-bold">
                ➔ @{targetBot}
              </span>
            </>
          ) : isApproval ? (
            <>
              <ShieldAlert className="size-3.5 text-amber-500" />
              <span className="font-semibold text-amber-500">Human Approval Gated</span>
            </>
          ) : isGatekeeper ? (
            <>
              {/* Accent marks the tool KIND only. Outcome is carried by the
                  status chip on the right, so this can never contradict it. */}
              <CheckSquare className="size-3.5 text-primary" />
              <span className="font-semibold text-foreground/90">DoD Contract Verification</span>
            </>
          ) : isAudit ? (
            <>
              <Sparkles className="size-3.5 text-purple-500" />
              <span className="font-semibold text-purple-500">Pre-Merge Audit Council</span>
            </>
          ) : (
            <>
              <Terminal className="size-3.5 text-primary" />
              <span className="font-semibold text-foreground/90">{toolCall.name}</span>
            </>
          )}
          <span className={`text-[10px] font-sans ${status.className}`}>{status.label}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <StatusIcon className={`size-3 ${status.className}`} aria-label={status.label} role="img" />
          {isOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </div>
      </button>

      {/* Special Quick-Preview for A2A Message without opening JSON */}
      {isA2A && msgContent && !isOpen && (
        <div className="px-3 py-1 border-t border-blue-500/20 bg-blue-500/5 text-[11px] text-foreground/80 flex items-center gap-2">
          <Send className="size-3 text-blue-400 shrink-0" />
          <span className="truncate italic">"{msgContent}"</span>
        </div>
      )}

      {isOpen && (
        <div className="border-t border-border/60 bg-background/50 p-2.5 space-y-2">
          <div>
            <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mb-1">Status</div>
            <div className={`flex items-center gap-1.5 text-[11px] ${status.className}`}>
              <StatusIcon className="size-3 shrink-0" aria-hidden="true" />
              <span className="font-semibold">{status.label}</span>
              <span className="text-muted-foreground">— {status.title}</span>
            </div>
          </div>
          {isA2A && msgContent ? (
            <div>
              <div className="text-[10px] font-semibold text-blue-400 uppercase tracking-wider mb-1">
                Dispatched Message Body to @{targetBot}
              </div>
              <div className="p-2 rounded-lg bg-blue-500/10 border border-blue-500/20 text-xs font-sans whitespace-pre-wrap text-foreground">
                {msgContent}
              </div>
            </div>
          ) : (
            <div>
              <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mb-1">Arguments</div>
              <pre className="p-2 rounded bg-muted/80 text-[11px] overflow-x-auto text-foreground font-mono">
                {JSON.stringify(toolCall.args, null, 2)}
              </pre>
            </div>
          )}
          {status.outputHeading && toolCall.output ? (
            <div>
              <div className={`text-[10px] font-semibold uppercase tracking-wider mb-1 ${status.className}`}>
                {status.outputHeading}
              </div>
              <pre className={`p-2 rounded bg-muted/80 text-[11px] overflow-x-auto font-mono max-h-48 whitespace-pre-wrap ${
                status.isSuccess ? "text-foreground" : status.className
              }`}>
                {toolCall.output}
              </pre>
            </div>
          ) : status.state === "not-reported" ? (
            <div className="text-[11px] text-muted-foreground italic">
              This call reported no result, so its outcome is unknown — it is not shown as successful.
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
