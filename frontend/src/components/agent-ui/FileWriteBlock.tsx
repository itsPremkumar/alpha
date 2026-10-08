"use client";

/**
 * The file-write / file-edit block.
 *
 * A card carrying the touched path (with a one-click copy),
 * the `+N / −N` stat computed from the diff body the run
 * actually reported, and a line-by-line diff view — additions
 * on green, deletions on red, hunk headers dimmed. An output
 * that is not a diff renders its raw text instead of an empty
 * diff, so a write that reported a plain message never looks
 * like a silent no-op.
 */

import React, { useState } from "react";
import {
  ChevronDown,
  FileCode2,
  FilePlus2,
  FilePenLine,
  FileText,
  type LucideIcon,
} from "lucide-react";
import type { ToolCall } from "@/types/chat";
import { extractFilePath, parseDiff } from "@/lib/agent-ui";
import { CopyButton } from "./shared";
import { DiffView } from "./DiffView";

interface FileWriteBlockProps {
  toolCall: ToolCall;
  /** `file-write` or `file-edit` — picks the header icon and label. */
  variant: "file-write" | "file-edit";
  /** Client-observed duration, pre-formatted. Empty renders nothing. */
  duration?: string;
}

const VARIANT_META: Record<
  FileWriteBlockProps["variant"],
  { label: string; Icon: LucideIcon }
> = {
  "file-write": { label: "Wrote file", Icon: FilePlus2 },
  "file-edit": { label: "Edited file", Icon: FilePenLine },
};

/** A tiny file-type icon from the extension. Decorative only. */
function fileIconFor(path: string): LucideIcon {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  if (["ts", "tsx", "js", "jsx", "mjs", "cjs"].includes(ext)) return FileCode2;
  if (ext === "md" || ext === "txt") return FileText;
  return FileText;
}

export function FileWriteBlock({
  toolCall,
  variant,
  duration,
}: FileWriteBlockProps) {
  const [open, setOpen] = useState(false);
  const path = extractFilePath(toolCall.args);
  const output = typeof toolCall.output === "string" ? toolCall.output : "";
  const diff = parseDiff(output);
  const isDiff = diff.lines.length > 0;
  const Meta = VARIANT_META[variant];
  const FileIcon = fileIconFor(path);
  const running = toolCall.status === undefined;

  return (
    <div
      className="my-2 overflow-hidden rounded-lg border border-emerald-500/25 bg-muted/30"
      data-file-block
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        <Meta.Icon
          className="size-3.5 shrink-0 text-emerald-500"
          aria-hidden="true"
        />
        <span className="text-[11px] font-semibold text-foreground/90">
          {Meta.label}
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
        {isDiff && (
          <span className="shrink-0 font-mono text-[10px] tabular-nums">
            <span className="text-emerald-500">+{diff.added}</span>
            <span className="text-muted-foreground"> </span>
            <span className="text-red-500">−{diff.removed}</span>
          </span>
        )}
        {duration && (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
            {duration}
          </span>
        )}
        {running && (
          <span className="shrink-0 rounded-full bg-sky-500/15 px-1.5 py-0.5 text-[10px] font-medium text-sky-500">
            running
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
          {isDiff ? (
            <div>
              {/* Codex-style diff header: the stat travels with the
                  diff itself, plus a one-click copy of the raw patch. */}
              <div className="flex items-center gap-2 border-b border-border/40 px-3 py-1">
                <span className="font-mono text-[10px] tabular-nums">
                  <span className="text-emerald-500">+{diff.added}</span>
                  <span className="text-muted-foreground"> </span>
                  <span className="text-red-500">−{diff.removed}</span>
                </span>
                <span className="flex-1" />
                <CopyButton text={output} label="Copy diff" />
              </div>
              <DiffView diff={diff} />
            </div>
          ) : output ? (
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] text-foreground/70">
              {output}
            </pre>
          ) : (
            <div className="px-3 py-2 text-[11px] text-muted-foreground">
              {running ? "Writing file…" : "No output reported"}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
