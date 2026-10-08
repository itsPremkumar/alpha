"use client";

/**
 * The artifact strip — how delivered artifacts are shown.
 *
 * `ChatMessage.artifacts` has been typed since the wire
 * first carried it, but no transcript renderer ever read
 * it — a delivered file was invisible in the chat. The
 * strip renders each artifact as a chip (type + name),
 * expanding inline to a bounded text preview so the
 * answer's outputs are one click away without leaving
 * the transcript. A binary or oversize body is labelled
 * as such rather than dumped.
 */

import React, { useState } from "react";
import {
  Braces,
  ChevronDown,
  FileText,
  FileCode2,
  Image as ImageIcon,
  type LucideIcon,
} from "lucide-react";
import type { ArtifactItem } from "@/types/chat";
import { CopyButton } from "./shared";

interface ArtifactStripProps {
  artifacts: ArtifactItem[];
}

const PREVIEW_CAP_CHARS = 4000;

function artifactIcon(type: string): LucideIcon {
  const t = type.toLowerCase();
  if (
    t === "image" ||
    t.includes("png") ||
    t.includes("jpg") ||
    t.includes("svg")
  )
    return ImageIcon;
  if (t.includes("json") || t === "code") return Braces;
  if (["md", "txt", "document", "text"].some((k) => t.includes(k)))
    return FileText;
  return FileCode2;
}

function ArtifactChip({ artifact }: { artifact: ArtifactItem }) {
  const [open, setOpen] = useState(false);
  const Icon = artifactIcon(artifact.type || artifact.name);
  const isText = typeof artifact.content === "string";
  const oversize = isText && artifact.content.length > PREVIEW_CAP_CHARS;
  const preview = isText
    ? oversize
      ? `${artifact.content.slice(0, PREVIEW_CAP_CHARS)}\n… (${artifact.content.length - PREVIEW_CAP_CHARS} more characters — download to read the rest)`
      : artifact.content
    : "";

  return (
    <div
      className="my-1.5 overflow-hidden rounded-lg border border-border/60 bg-muted/30 text-xs"
      data-artifact
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        <Icon className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
        <span className="shrink-0 rounded bg-primary/10 px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider text-primary">
          {artifact.type || "file"}
        </span>
        <code
          className="min-w-0 flex-1 truncate font-mono text-[11px] text-foreground/85"
          title={artifact.name}
        >
          {artifact.name}
        </code>
        {isText && (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
            {artifact.content.length} chars
          </span>
        )}
        <CopyButton
          text={isText ? artifact.content : artifact.name}
          label="Copy artifact content"
        />
        <ChevronDown
          className={`size-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "" : "-rotate-90"}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="border-t border-border/50">
          {isText ? (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] text-foreground/80">
              {preview}
            </pre>
          ) : (
            <div className="px-3 py-2 text-[11px] text-muted-foreground">
              Binary artifact — no text preview available
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ArtifactStrip({ artifacts }: ArtifactStripProps) {
  if (!artifacts || artifacts.length === 0) return null;
  return (
    <div className="my-2" data-artifact-strip>
      <div className="mb-1 flex items-center gap-1.5 px-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        <FileText className="size-3" aria-hidden="true" />
        <span>
          {artifacts.length} artifact{artifacts.length === 1 ? "" : "s"}{" "}
          delivered
        </span>
      </div>
      {artifacts.map((a) => (
        <ArtifactChip key={a.id} artifact={a} />
      ))}
    </div>
  );
}
