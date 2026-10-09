"use client";

/**
 * Shared presentation primitives for the Agent Response UI components.
 *
 * `KIND_TONE` maps a `ToolKind`'s tone name onto the Tailwind classes
 * the rest of the app already uses (`text-sky-500`, `bg-sky-500/15`),
 * so an agent-UI card never invents a colour outside the palette.
 * `CopyButton` is the one clipboard implementation these cards share:
 * a denied permission surfaces a worded remedy, never a silent no-op.
 */

import React, { useState } from "react";
import { Check, Copy } from "lucide-react";

type Tone =
  | "sky"
  | "emerald"
  | "violet"
  | "amber"
  | "orange"
  | "cyan"
  | "blue"
  | "fuchsia"
  | "rose"
  | "muted";

const TONE_TEXT: Record<Tone, string> = {
  sky: "text-sky-500",
  emerald: "text-emerald-500",
  violet: "text-violet-500",
  amber: "text-amber-500",
  orange: "text-orange-500",
  cyan: "text-cyan-500",
  blue: "text-blue-500",
  fuchsia: "text-fuchsia-500",
  rose: "text-rose-500",
  muted: "text-muted-foreground",
};

const TONE_BG: Record<Tone, string> = {
  sky: "bg-sky-500/10",
  emerald: "bg-emerald-500/10",
  violet: "bg-violet-500/10",
  amber: "bg-amber-500/10",
  orange: "bg-orange-500/10",
  cyan: "bg-cyan-500/10",
  blue: "bg-blue-500/10",
  fuchsia: "bg-fuchsia-500/10",
  rose: "bg-rose-500/10",
  muted: "bg-muted/40",
};

const TONE_BORDER: Record<Tone, string> = {
  sky: "border-sky-500/30",
  emerald: "border-emerald-500/30",
  violet: "border-violet-500/30",
  amber: "border-amber-500/30",
  orange: "border-orange-500/30",
  cyan: "border-cyan-500/30",
  blue: "border-blue-500/30",
  fuchsia: "border-fuchsia-500/30",
  rose: "border-rose-500/30",
  muted: "border-border/60",
};

export const KIND_TEXT = TONE_TEXT;
export const KIND_BG = TONE_BG;
export const KIND_BORDER = TONE_BORDER;

interface CopyButtonProps {
  /** Text to copy. Empty/whitespace renders nothing — there is nothing to copy. */
  text: string;
  label: string;
  className?: string;
}

/**
 * One-click copy with a transient check. A clipboard failure (a denied
 * permission) renders the error state rather than pretending success —
 * the same rule `lib/peer-network.ts`'s `useCopyButton` enforces.
 */
export function CopyButton({ text, label, className = "" }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);

  if (!text.trim()) return null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setFailed(false);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
      setFailed(true);
      setTimeout(() => setFailed(false), 2000);
    }
  };

  return (
    <button
      type="button"
      onClick={copy}
      title={failed ? "Copy failed — check clipboard permission" : label}
      aria-label={label}
      className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium transition-colors hover:bg-muted/70 ${
        failed
          ? "text-destructive"
          : copied
            ? "text-emerald-500"
            : "text-muted-foreground"
      } ${className}`}
    >
      {failed ? (
        <Copy className="size-3" />
      ) : copied ? (
        <Check className="size-3" />
      ) : (
        <Copy className="size-3" />
      )}
      <span>{copied ? "Copied" : failed ? "Copy failed" : "Copy"}</span>
    </button>
  );
}
