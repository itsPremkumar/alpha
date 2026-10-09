"use client";

/**
 * The read block — how a file read is shown.
 *
 * Reads (`hashline_read`, `read_file`, `present_file`, …) are
 * the most common tool in a coding turn, and they used to
 * render as a generic pill with a JSON blob. The card leads
 * with the path (language-tagged from the extension, with a
 * one-click copy), the size of what came back, and — expanded —
 * the content the run reported, in a bounded scroll region.
 */

import React, { useState } from "react";
import {
  BookOpen,
  ChevronDown,
  FileCode2,
  FileText,
  type LucideIcon,
} from "lucide-react";
import type { ToolCall } from "@/types/chat";
import { extractFilePath, textCounts } from "@/lib/agent-ui";
import { CopyButton } from "./shared";

interface ReadBlockProps {
  toolCall: ToolCall;
  /** True while the run that issued this call is still in flight. */
  inFlight?: boolean;
  /** Client-observed duration, pre-formatted. Empty renders nothing. */
  duration?: string;
}

function fileIconFor(path: string): LucideIcon {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  if (
    [
      "ts",
      "tsx",
      "js",
      "jsx",
      "mjs",
      "cjs",
      "py",
      "rs",
      "go",
      "java",
      "css",
      "html",
      "sh",
      "yaml",
      "yml",
      "json",
    ].includes(ext)
  )
    return FileCode2;
  return FileText;
}

function languageOf(path: string): string | null {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return ext && path.includes(".") ? ext : null;
}

export function ReadBlock({
  toolCall,
  inFlight = false,
  duration,
}: ReadBlockProps) {
  const [open, setOpen] = useState(false);
  const path = extractFilePath(toolCall.args);
  const output = typeof toolCall.output === "string" ? toolCall.output : "";
  const counts = textCounts(output);
  const running = toolCall.status === undefined && inFlight;
  const FileIcon = fileIconFor(path);
  const language = languageOf(path);

  return (
    <div
      className="my-2 overflow-hidden rounded-lg border border-cyan-500/25 bg-muted/30 text-xs"
      data-read-block
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        <BookOpen
          className="size-3.5 shrink-0 text-cyan-500"
          aria-hidden="true"
        />
        <span className="text-[11px] font-semibold text-foreground/90">
          Read file
        </span>
        <FileIcon
          className="size-3.5 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
        <code
          className="min-w-0 flex-1 truncate font-mono text-[11px] text-foreground/80"
          title={path || "No path reported"}
        >
          {path || (
            <span className="text-muted-foreground">no path reported</span>
          )}
        </code>
        {language && (
          <span className="shrink-0 rounded bg-cyan-500/10 px-1.5 py-px font-mono text-[9px] font-semibold uppercase text-cyan-500">
            {language}
          </span>
        )}
        {counts && (
          <span
            className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground"
            title="Lines and characters the run reported"
          >
            {counts.lines} lines
          </span>
        )}
        {duration && (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
            {duration}
          </span>
        )}
        {running && (
          <span className="shrink-0 rounded-full bg-sky-500/15 px-1.5 py-0.5 text-[10px] font-medium text-sky-500">
            reading
          </span>
        )}
        <CopyButton text={path} label="Copy file path" />
        <ChevronDown
          className={`size-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "" : "-rotate-90"}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="border-t border-border/50">
          {output ? (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] text-foreground/75">
              {output}
            </pre>
          ) : (
            <div className="px-3 py-2 text-[11px] text-muted-foreground">
              {running ? "Reading file…" : "No content reported"}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
