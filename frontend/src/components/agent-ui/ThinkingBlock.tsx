"use client";

/**
 * The thinking / reasoning block.
 *
 * Upgrades the old flat mono "Thought process" dump into a card
 * with an amber accent, a shimmer while the run is still writing
 * the trace, a rough token estimate, and a markdown body. It is
 * collapsed by default — reasoning is context, not the answer —
 * but a run still in flight opens itself so "what is it thinking"
 * is answered without a click. An explicit click always wins.
 */

import React, { useEffect, useRef, useState } from "react";
import { Brain, ChevronDown, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { estimateTokens } from "@/lib/agent-ui";

interface ThinkingBlockProps {
  thinking: string;
  /** True while the run that produced this trace is still in flight. */
  streaming?: boolean;
}

export function ThinkingBlock({
  thinking,
  streaming = false,
}: ThinkingBlockProps) {
  const [open, setOpen] = useState(false);
  const userChose = useRef(false);
  const tokens = estimateTokens(thinking);

  // Fold open while the trace is still being written; collapse once
  // the run settles. Never overrides an explicit click.
  useEffect(() => {
    if (userChose.current) return;
    setOpen(streaming);
  }, [streaming]);

  if (!thinking.trim()) return null;

  return (
    <div
      className="my-2 overflow-hidden rounded-lg border border-amber-500/25 bg-amber-500/[0.04]"
      data-thinking-block
    >
      <button
        type="button"
        onClick={() => {
          userChose.current = true;
          setOpen((v) => !v);
        }}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-amber-500/[0.06] transition-colors"
      >
        <Brain
          className="size-3.5 shrink-0 text-amber-500"
          aria-hidden="true"
        />
        <span className="text-[11px] font-semibold text-amber-600 dark:text-amber-400">
          Thinking
        </span>
        {streaming && (
          <span className="inline-flex items-center gap-1 text-[10px] text-amber-500/80">
            <Loader2 className="size-3 animate-spin" />
            <span className="shimmer">reasoning…</span>
          </span>
        )}
        {tokens !== null && (
          <span
            className="text-[10px] font-mono tabular-nums text-muted-foreground"
            title="Approximate, from text length — not a measured count"
          >
            ≈{tokens} tokens
          </span>
        )}
        <ChevronDown
          className={`ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "" : "-rotate-90"}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="border-t border-amber-500/15 px-3 py-2.5">
          <div className="response-prose text-[12px] text-muted-foreground">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {thinking}
            </ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}
