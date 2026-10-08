"use client";

/**
 * The browser block — how a browser-driving tool is shown.
 *
 * `browser_navigate_and_inspect` drives a headless page through
 * discrete actions (`navigate`, `dom`, `click`, `screenshot`,
 * `table`, `next`, `run`, `parse`, …). The card leads with the
 * action verb as a chip — the thing a reader scanning the
 * transcript needs first — then the live URL (linked, with a
 * one-click copy), the click coordinates when the action is a
 * click, and the reported observation expanded below.
 */

import React, { useState } from "react";
import {
  ChevronDown,
  CircleDashed,
  ExternalLink,
  Globe,
  MousePointerClick,
} from "lucide-react";
import type { ToolCall } from "@/types/chat";
import { extractBrowserAction, extractUrl } from "@/lib/agent-ui";
import { CopyButton } from "./shared";

interface BrowserBlockProps {
  toolCall: ToolCall;
  /** True while the run that issued this call is still in flight. */
  inFlight?: boolean;
  /** Client-observed duration, pre-formatted. Empty renders nothing. */
  duration?: string;
}

export function BrowserBlock({
  toolCall,
  inFlight = false,
  duration,
}: BrowserBlockProps) {
  const [open, setOpen] = useState(false);
  const action = extractBrowserAction(toolCall.args);
  const url = extractUrl(toolCall.args);
  const output = typeof toolCall.output === "string" ? toolCall.output : "";
  const running = toolCall.status === undefined && inFlight;

  const x = toolCall.args?.x;
  const y = toolCall.args?.y;
  const coords =
    typeof x === "number" && typeof y === "number" ? `${x}, ${y}` : null;

  return (
    <div
      className="my-2 overflow-hidden rounded-lg border border-blue-500/25 bg-muted/30 text-xs"
      data-browser-block
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        <Globe className="size-3.5 shrink-0 text-blue-500" aria-hidden="true" />
        {action ? (
          <span className="shrink-0 rounded bg-blue-500/10 px-1.5 py-px font-mono text-[10px] font-semibold text-blue-500">
            {action}
          </span>
        ) : (
          <span className="shrink-0 text-[11px] text-muted-foreground">
            browser
          </span>
        )}
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="min-w-0 flex-1 truncate font-mono text-[11px] text-foreground/80 hover:text-primary hover:underline"
            title={url}
          >
            {url}
          </a>
        ) : (
          <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
            no URL reported
          </span>
        )}
        {coords && (
          <span
            className="inline-flex shrink-0 items-center gap-1 font-mono text-[10px] text-muted-foreground"
            title="Click coordinates the run reported"
          >
            <MousePointerClick className="size-3" aria-hidden="true" />
            {coords}
          </span>
        )}
        {duration && (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
            {duration}
          </span>
        )}
        {running && (
          <CircleDashed
            className="size-3.5 shrink-0 animate-spin text-sky-400"
            aria-label="Browser action running"
          />
        )}
        <CopyButton text={url} label="Copy URL" />
        <ChevronDown
          className={`size-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "" : "-rotate-90"}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="border-t border-border/50">
          {output ? (
            <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] text-foreground/75">
              {output}
            </pre>
          ) : (
            <div className="px-3 py-2 text-[11px] text-muted-foreground">
              {running ? "Driving the page…" : "No observation reported"}
            </div>
          )}
          {url && (
            <div className="flex items-center gap-1 border-t border-border/40 px-3 py-1">
              <ExternalLink
                className="size-3 text-muted-foreground/60"
                aria-hidden="true"
              />
              <a
                href={url}
                target="_blank"
                rel="noreferrer"
                className="truncate font-mono text-[10px] text-muted-foreground hover:text-primary hover:underline"
              >
                Open page in a new tab
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
