"use client";

/**
 * The terminal block — how a shell command is shown.
 *
 * A dark terminal surface carrying the exact command (with a
 * one-click copy), the output the run reported (streamed line
 * by line while the call is in flight), and the sandbox's exit
 * code as a badge: green `0`, red for anything else, absent
 * when the output carried no marker. A call with no result yet
 * is "running", never a success — the honest-state rule every
 * agent-UI card follows.
 */

import React from "react";
import { CircleDashed, CheckCircle2, XCircle, Terminal } from "lucide-react";
import type { ToolCall } from "@/types/chat";
import { extractCommand, parseExitCode, stripExitMarker } from "@/lib/agent-ui";
import { CopyButton } from "./shared";

interface TerminalBlockProps {
  toolCall: ToolCall;
  /** True while the run that issued this call is still in flight. */
  inFlight?: boolean;
  /** Client-observed duration, pre-formatted. Empty renders nothing. */
  duration?: string;
}

export function TerminalBlock({
  toolCall,
  inFlight = false,
  duration,
}: TerminalBlockProps) {
  const command = extractCommand(toolCall.args);
  const rawOutput = typeof toolCall.output === "string" ? toolCall.output : "";
  const exitCode = parseExitCode(rawOutput);
  const body = stripExitMarker(rawOutput);
  const lines = body ? body.split("\n") : [];

  // A call with no reported status is running only while the run is
  // in flight; a restored-from-history call is "no result reported".
  const running = toolCall.status === undefined && inFlight;
  const settled = typeof toolCall.status === "string";

  return (
    <div
      className="my-2 overflow-hidden rounded-lg border border-sky-500/25 bg-slate-950/90 text-slate-200"
      data-terminal-block
    >
      {/* Command row */}
      <div className="flex items-center gap-2 border-b border-sky-500/20 bg-slate-900/80 px-3 py-1.5">
        <Terminal
          className="size-3.5 shrink-0 text-sky-400"
          aria-hidden="true"
        />
        <span className="font-mono text-[11px] text-sky-300">$</span>
        <code
          className="min-w-0 flex-1 truncate font-mono text-[11px] text-slate-100"
          title={command || "No command reported"}
        >
          {command || (
            <span className="text-slate-500">no command reported</span>
          )}
        </code>
        {duration && (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-slate-500">
            {duration}
          </span>
        )}
        <CopyButton
          text={command}
          label="Copy command"
          className="text-slate-400 hover:text-slate-200"
        />
        {/* Status glyph on the right */}
        {running && (
          <CircleDashed
            className="size-3.5 shrink-0 animate-spin text-sky-400"
            aria-label="Command running"
          />
        )}
        {settled && exitCode === 0 && (
          <CheckCircle2
            className="size-3.5 shrink-0 text-emerald-400"
            aria-label="Exit code 0"
          />
        )}
        {settled && exitCode !== null && exitCode !== 0 && (
          <XCircle
            className="size-3.5 shrink-0 text-red-400"
            aria-label={`Exit code ${exitCode}`}
          />
        )}
      </div>

      {/* Exit code badge */}
      {settled && exitCode !== null && (
        <div
          className={`flex items-center gap-1.5 border-b px-3 py-1 font-mono text-[10px] ${
            exitCode === 0
              ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400"
              : "border-red-500/20 bg-red-500/10 text-red-400"
          }`}
        >
          <span className="font-semibold">exit {exitCode}</span>
          <span className="text-slate-500">
            {exitCode === 0 ? "command succeeded" : "command failed"}
          </span>
        </div>
      )}

      {/* Output */}
      {(body || running) && (
        <pre
          className="max-h-56 overflow-auto px-3 py-2 font-mono text-[11px] leading-relaxed text-slate-300"
          data-terminal-output
        >
          {lines.map((line, i) => (
            <div key={i} className="whitespace-pre-wrap break-words">
              {line}
            </div>
          ))}
          {running && (
            <div
              className="mt-0.5 flex items-center gap-1.5 text-sky-400"
              aria-hidden="true"
            >
              <span className="inline-block size-1.5 animate-pulse rounded-full bg-sky-400" />
              <span className="text-[10px]">running…</span>
            </div>
          )}
        </pre>
      )}
    </div>
  );
}
