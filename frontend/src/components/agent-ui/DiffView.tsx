"use client";

/**
 * The shared diff renderer.
 *
 * One line-by-line unified-diff view used by every surface
 * that shows one: file-write tool blocks and the run's
 * workspace-changes panel. Additions read green, deletions
 * red, hunk headers dimmed — and every line carries its file
 * line numbers in the gutter, counted from the `@@` hunk
 * headers the way Codex-style diffs do. Lines before the
 * first hunk header get a blank gutter, never guessed numbers.
 */

import React from "react";
import type { ParsedDiff } from "@/lib/agent-ui";

interface DiffViewProps {
  diff: ParsedDiff;
  /** Max height class for the scroll region. */
  maxHeightClass?: string;
}

function gutterNo(value: number | null): string {
  return value === null ? "" : String(value);
}

export function DiffView({ diff, maxHeightClass = "max-h-64" }: DiffViewProps) {
  if (diff.lines.length === 0) return null;
  return (
    <pre
      className={`${maxHeightClass} overflow-auto px-3 py-2 font-mono text-[11px] leading-relaxed`}
      data-diff-view
    >
      {diff.lines.map((line, i) => {
        if (line.type === "hunk") {
          return (
            <div
              key={i}
              className="whitespace-pre-wrap break-words text-muted-foreground/70"
              data-diff-gutter="hunk"
            >
              {line.text}
            </div>
          );
        }
        const rowTone =
          line.type === "add"
            ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
            : line.type === "remove"
              ? "bg-red-500/10 text-red-600 dark:text-red-400"
              : "text-foreground/70";
        return (
          <div key={i} className={`whitespace-pre-wrap break-words ${rowTone}`}>
            <span
              className="mr-2 inline-block w-14 shrink-0 select-none text-right align-top text-[10px] text-muted-foreground/50"
              aria-hidden="true"
              data-diff-gutter="numbers"
            >
              <span className="inline-block w-6">{gutterNo(line.oldNo)}</span>
              <span className="inline-block w-6">{gutterNo(line.newNo)}</span>
            </span>
            <span>
              {line.type === "add"
                ? `+ ${line.text}`
                : line.type === "remove"
                  ? `− ${line.text}`
                  : line.text}
            </span>
          </div>
        );
      })}
    </pre>
  );
}
