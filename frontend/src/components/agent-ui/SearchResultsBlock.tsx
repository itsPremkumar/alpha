"use client";

/**
 * The search / research block — how a search-family tool is shown.
 *
 * The assistant-ui WebSearch pattern, adapted to this app's
 * honesty contract: the query (or research topic) rides in a
 * pill, a shimmering "Searching…" line runs while the call is
 * in flight, and settled results land as rows — each with a
 * domain-lettered avatar, a title, the source domain, and the
 * snippet. Result rows link out to the reported URL.
 *
 * The output is parsed by `parseSearchResults`: a payload that
 * is not a result list (prose, an error page, a status line)
 * yields no rows, and the card then shows the raw output
 * instead of an empty result list. A result count is shown
 * only when results parsed — "Read 0 sources" is never
 * printed over a failed parse.
 */

import React, { useState } from "react";
import {
  ChevronDown,
  CircleDashed,
  ExternalLink,
  Globe,
  Search,
} from "lucide-react";
import type { ToolCall } from "@/types/chat";
import {
  domainOf,
  extractDepth,
  extractQuery,
  parseSearchResults,
} from "@/lib/agent-ui";
import { CopyButton } from "./shared";

interface SearchResultsBlockProps {
  toolCall: ToolCall;
  /** `search` or `research` — picks the header label. */
  variant: "search" | "research";
  /** True while the run that issued this call is still in flight. */
  inFlight?: boolean;
  /** Client-observed duration, pre-formatted. Empty renders nothing. */
  duration?: string;
}

/** Avatar background per domain initial. Decorative only. */
const AVATAR_TONES = [
  "bg-sky-500/15 text-sky-500",
  "bg-emerald-500/15 text-emerald-500",
  "bg-violet-500/15 text-violet-500",
  "bg-amber-500/15 text-amber-500",
  "bg-rose-500/15 text-rose-500",
  "bg-cyan-500/15 text-cyan-500",
];

function avatarTone(domain: string): string {
  let hash = 0;
  for (let i = 0; i < domain.length; i++)
    hash = (hash * 31 + domain.charCodeAt(i)) >>> 0;
  return AVATAR_TONES[hash % AVATAR_TONES.length];
}

export function SearchResultsBlock({
  toolCall,
  variant,
  inFlight = false,
  duration,
}: SearchResultsBlockProps) {
  const [open, setOpen] = useState(false);
  const query = extractQuery(toolCall.args);
  const depth = variant === "research" ? extractDepth(toolCall.args) : null;
  const rawOutput = typeof toolCall.output === "string" ? toolCall.output : "";
  const results = parseSearchResults(rawOutput);
  const running = toolCall.status === undefined && inFlight;

  return (
    <div
      className={`my-2 overflow-hidden rounded-lg border text-xs ${
        variant === "research"
          ? "border-orange-500/25 bg-muted/30"
          : "border-amber-500/25 bg-muted/30"
      }`}
      data-search-block={variant}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        <Search
          className={`size-3.5 shrink-0 ${variant === "research" ? "text-orange-500" : "text-amber-500"}`}
          aria-hidden="true"
        />
        {/* The query pill */}
        <span
          className="min-w-0 max-w-full truncate rounded-full border border-border/60 bg-background/60 px-2 py-0.5 font-sans text-[11px] text-foreground/85"
          title={query || "No query reported"}
        >
          {query || (
            <span className="text-muted-foreground">no query reported</span>
          )}
        </span>
        {depth !== null && (
          <span
            className="shrink-0 rounded-full bg-orange-500/10 px-1.5 py-px font-mono text-[9px] text-orange-500"
            title="Investigation depth the run asked for"
          >
            depth {depth}
          </span>
        )}
        {running ? (
          <span className="inline-flex shrink-0 items-center gap-1 text-[10px] text-amber-500/90">
            <CircleDashed className="size-3 animate-spin" aria-hidden="true" />
            <span className="shimmer">Searching…</span>
          </span>
        ) : (
          results.length > 0 && (
            <span className="shrink-0 text-[10px] text-muted-foreground">
              Read {results.length} source{results.length === 1 ? "" : "s"}
            </span>
          )
        )}
        {duration && (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
            {duration}
          </span>
        )}
        <CopyButton text={query} label="Copy search query" />
        <ChevronDown
          className={`size-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "" : "-rotate-90"}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="border-t border-border/50 p-1.5">
          {results.length > 0 ? (
            <ol className="space-y-1">
              {results.map((result, i) => {
                const domain = result.source || domainOf(result.url);
                const initial = (domain || result.title || "?")
                  .slice(0, 1)
                  .toUpperCase();
                return (
                  <li key={i}>
                    {result.url ? (
                      <a
                        href={result.url}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-muted/60 transition-colors"
                        title={result.url}
                      >
                        <span
                          className={`mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-md font-mono text-[10px] font-bold ${avatarTone(domain)}`}
                          aria-hidden="true"
                        >
                          {initial}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[11px] font-medium text-foreground/90">
                            {result.title}
                          </span>
                          {result.snippet && (
                            <span
                              className="block truncate text-[10px] text-muted-foreground"
                              title={result.snippet}
                            >
                              {result.snippet}
                            </span>
                          )}
                          {domain && (
                            <span className="mt-0.5 block font-mono text-[9px] text-muted-foreground/80">
                              {domain}
                            </span>
                          )}
                        </span>
                        <ExternalLink
                          className="mt-1 size-3 shrink-0 text-muted-foreground/60"
                          aria-hidden="true"
                        />
                      </a>
                    ) : (
                      <div
                        className="flex items-start gap-2 rounded-md px-2 py-1.5"
                        title="No URL reported for this result"
                      >
                        <span
                          className={`mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-md font-mono text-[10px] font-bold ${avatarTone(domain)}`}
                          aria-hidden="true"
                        >
                          {initial}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-[11px] font-medium text-foreground/90">
                            {result.title}
                          </span>
                          {result.snippet && (
                            <span
                              className="block truncate text-[10px] text-muted-foreground"
                              title={result.snippet}
                            >
                              {result.snippet}
                            </span>
                          )}
                        </span>
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          ) : rawOutput ? (
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words px-2 py-1 font-mono text-[10px] text-foreground/70">
              {rawOutput}
            </pre>
          ) : (
            <div className="px-2 py-1 text-[11px] text-muted-foreground">
              {running ? "Searching…" : "No results reported"}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
