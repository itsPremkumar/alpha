"use client";

/**
 * The transcript search bar.
 *
 * A floating bar pinned above the composer: query input,
 * `3 of 12` counter, up/down match navigation, and a close
 * button. It owns no matching logic — hits come from
 * `findTranscriptMatches` in `lib/transcript-search.ts` —
 * it only walks the list the parent hands it. An empty
 * result says "no matches", never a bare zero.
 */

import React, { useEffect, useRef } from "react";
import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import type { TranscriptMatch } from "@/lib/transcript-search";
import { matchFieldLabel } from "@/lib/transcript-search";

interface TranscriptSearchProps {
  open: boolean;
  query: string;
  onQuery: (query: string) => void;
  matches: TranscriptMatch[];
  /** Index into `matches` of the current hit. -1 when there is none. */
  current: number;
  onStep: (direction: 1 | -1) => void;
  onClose: () => void;
}

export function TranscriptSearch({
  open,
  query,
  onQuery,
  matches,
  current,
  onStep,
  onClose,
}: TranscriptSearchProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  // Focus the input the moment the bar opens, so Ctrl+F flows
  // straight into typing.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  if (!open) return null;

  const total = matches.length;
  const active = current >= 0 && current < total ? matches[current] : null;

  return (
    <div
      className="mx-auto w-full max-w-4xl px-4"
      role="search"
      aria-label="Search conversation"
      data-transcript-search
    >
      <div className="flex items-center gap-1.5 rounded-xl border border-border/70 bg-card px-2.5 py-1.5 shadow-lg elev-2">
        <Search
          className="size-3.5 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onStep(e.shiftKey ? -1 : 1);
            else if (e.key === "Escape") onClose();
          }}
          placeholder="Search conversation…"
          aria-label="Search conversation"
          className="min-w-0 flex-1 bg-transparent text-xs text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
        {query.trim() ? (
          <span
            className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground"
            aria-live="polite"
          >
            {total === 0 ? "no matches" : `${current + 1} of ${total}`}
          </span>
        ) : (
          <span className="shrink-0 text-[10px] text-muted-foreground/70">
            type to search
          </span>
        )}
        <button
          type="button"
          onClick={() => onStep(-1)}
          disabled={total === 0}
          title="Previous match (Shift+Enter)"
          aria-label="Previous match"
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30"
        >
          <ChevronUp className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={() => onStep(1)}
          disabled={total === 0}
          title="Next match (Enter)"
          aria-label="Next match"
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30"
        >
          <ChevronDown className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={onClose}
          title="Close search (Esc)"
          aria-label="Close search"
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-3.5" />
        </button>
      </div>
      {active && (
        <div className="px-1 pt-1 text-[10px] text-muted-foreground">
          matched in {matchFieldLabel(active)}
        </div>
      )}
    </div>
  );
}
