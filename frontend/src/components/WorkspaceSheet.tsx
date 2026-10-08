"use client";

/**
 * The Agent Workspace sheet — the split-pane beside the chat.
 *
 * Claude-Artifacts pattern adapted to an agent transcript: the
 * thread stays the reasoning trail on the left, and this sheet
 * is the product of the session on the right — files the turn
 * changed (with diffs), pages the browser touched, commands it
 * ran, and artifacts it delivered. Every tab reuses the same
 * blocks the transcript renders, so the two surfaces can never
 * disagree about what the run reported.
 *
 * All rows come from `collectWorkspaceContent` over the messages
 * on screen: no network, no second source. A tab with nothing
 * reported says so rather than rendering an empty list as an
 * answer.
 */

import React, { useState } from "react";
import {
  Files,
  Terminal,
  Globe,
  Package,
  X,
  type LucideIcon,
} from "lucide-react";
import type { WorkspaceContent } from "@/lib/workspace-sheet";
import { FileWriteBlock } from "./agent-ui/FileWriteBlock";
import { TerminalBlock } from "./agent-ui/TerminalBlock";
import { BrowserBlock } from "./agent-ui/BrowserBlock";
import { ArtifactStrip } from "./agent-ui/ArtifactStrip";

type WorkspaceTab = "changes" | "terminal" | "browser" | "artifacts";

const TABS: Array<{ id: WorkspaceTab; label: string; Icon: LucideIcon }> = [
  { id: "changes", label: "Changes", Icon: Files },
  { id: "terminal", label: "Terminal", Icon: Terminal },
  { id: "browser", label: "Browser", Icon: Globe },
  { id: "artifacts", label: "Artifacts", Icon: Package },
];

interface WorkspaceSheetProps {
  content: WorkspaceContent;
  /** True while the run behind these messages is still in flight. */
  live?: boolean;
  onClose: () => void;
}

function countFor(content: WorkspaceContent, tab: WorkspaceTab): number {
  switch (tab) {
    case "changes":
      return content.files.length;
    case "terminal":
      return content.terminal.length;
    case "browser":
      return content.browser.length;
    case "artifacts":
      return content.artifacts.length;
  }
}

const EMPTY_SENTENCES: Record<WorkspaceTab, string> = {
  changes: "No file changes reported in this conversation yet.",
  terminal: "No commands run in this conversation yet.",
  browser: "The browser has not been driven in this conversation yet.",
  artifacts: "No artifacts delivered in this conversation yet.",
};

export function WorkspaceSheet({
  content,
  live = false,
  onClose,
}: WorkspaceSheetProps) {
  const [tab, setTab] = useState<WorkspaceTab>(() =>
    content.files.length > 0
      ? "changes"
      : content.terminal.length > 0
        ? "terminal"
        : content.browser.length > 0
          ? "browser"
          : "artifacts",
  );

  return (
    <div
      className="flex h-full flex-col overflow-hidden rounded-xl border border-border/60 bg-card"
      data-workspace-sheet
    >
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-border/50 px-3 py-2">
        <span className="text-xs font-semibold text-foreground">
          Agent workspace
        </span>
        <span
          className="rounded-full bg-primary/10 px-1.5 py-px font-mono text-[10px] tabular-nums text-primary"
          title="Files, commands, pages and artifacts reported in this conversation"
        >
          {content.total}
        </span>
        {live && (
          <span className="inline-flex items-center gap-1 text-[10px] text-sky-500">
            <span
              className="size-1.5 animate-pulse rounded-full bg-sky-500"
              aria-hidden="true"
            />
            live
          </span>
        )}
        <span className="flex-1" />
        <button
          type="button"
          onClick={onClose}
          title="Close workspace"
          aria-label="Close workspace"
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-3.5" />
        </button>
      </div>

      {/* Tabs */}
      <div
        className="flex items-center gap-0.5 border-b border-border/50 px-2 py-1"
        role="tablist"
        aria-label="Workspace views"
      >
        {TABS.map(({ id, label, Icon }) => {
          const count = countFor(content, id);
          const active = tab === id;
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(id)}
              className={`inline-flex flex-1 items-center justify-center gap-1 rounded-lg px-2 py-1.5 text-[11px] font-medium transition-colors ${
                active
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
              }`}
            >
              <Icon className="size-3.5" aria-hidden="true" />
              <span className="hidden xl:inline">{label}</span>
              <span
                className={`font-mono text-[10px] tabular-nums ${count > 0 ? "" : "opacity-50"}`}
              >
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Panels */}
      <div
        className="min-h-0 flex-1 overflow-y-auto p-2"
        data-workspace-tab={tab}
        role="tabpanel"
      >
        {tab === "changes" &&
          (content.files.length > 0 ? (
            <div className="space-y-1">
              {content.files.map((entry) => (
                <FileWriteBlock
                  key={`${entry.messageId}:${entry.call.id}`}
                  toolCall={entry.call}
                  variant={entry.variant}
                />
              ))}
            </div>
          ) : (
            <EmptyNote text={EMPTY_SENTENCES.changes} />
          ))}

        {tab === "terminal" &&
          (content.terminal.length > 0 ? (
            <div className="space-y-1">
              {content.terminal.map((entry) => (
                <TerminalBlock
                  key={`${entry.messageId}:${entry.call.id}`}
                  toolCall={entry.call}
                  inFlight={live && entry.call.status === undefined}
                />
              ))}
            </div>
          ) : (
            <EmptyNote text={EMPTY_SENTENCES.terminal} />
          ))}

        {tab === "browser" &&
          (content.browser.length > 0 ? (
            <div className="space-y-1">
              {content.browser.map((entry) => (
                <BrowserBlock
                  key={`${entry.messageId}:${entry.call.id}`}
                  toolCall={entry.call}
                  inFlight={live && entry.call.status === undefined}
                />
              ))}
            </div>
          ) : (
            <EmptyNote text={EMPTY_SENTENCES.browser} />
          ))}

        {tab === "artifacts" &&
          (content.artifacts.length > 0 ? (
            <ArtifactStrip
              artifacts={content.artifacts.map((a) => a.artifact)}
            />
          ) : (
            <EmptyNote text={EMPTY_SENTENCES.artifacts} />
          ))}
      </div>
    </div>
  );
}

function EmptyNote({ text }: { text: string }) {
  return (
    <div className="px-2 py-6 text-center text-[11px] text-muted-foreground">
      {text}
    </div>
  );
}
