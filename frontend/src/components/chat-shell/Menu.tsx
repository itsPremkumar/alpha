"use client";

import React, { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

/**
 * A small dropdown used by the bot header and by every project row.
 *
 * The trigger and the panel are separate elements so the same control can open
 * from a wide button (the current-agent header) or a 20px chevron (a project
 * row) without duplicating the dismissal logic. Dismissal is a document
 * mousedown listener scoped to the root, which is why the root is a ref rather
 * than a JSX wrapper with a click handler: a wrapper would swallow the mousedown
 * that the panel's own items rely on.
 */
export function Menu(props: {
  /** Accessible name for the trigger, and for the panel's list role. */
  label: string;
  /** Rendered inside the trigger, left of the chevron. */
  trigger: React.ReactNode;
  /** Compact triggers drop the chevron and shrink the hit area. */
  compact?: boolean;
  children: (close: () => void) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={props.label}
        className={
          props.compact
            ? "p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
            : "w-full flex items-center gap-1.5 rounded-lg px-1.5 py-1 text-left hover:bg-muted/60"
        }
      >
        {props.trigger}
        {props.compact ? null : <ChevronDown className={`size-3 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />}
      </button>
      {open && (
        <div
          role="menu"
          aria-label={props.label}
          className={`absolute z-30 mt-1 w-60 max-w-[85vw] rounded-xl border border-border bg-card p-1 elev-3 ${props.compact ? "right-0" : "left-0"}`}
        >
          {props.children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

/** One row in a `Menu`. */
export function MenuItem(props: {
  icon?: React.ReactNode;
  label: string;
  hint?: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={props.disabled}
      onClick={props.onClick}
      title={props.hint}
      className="w-full flex items-start gap-2 rounded-lg px-2 py-1.5 text-left text-[11px] text-foreground/85 hover:bg-muted disabled:opacity-40 disabled:hover:bg-transparent"
    >
      {props.icon ? <span className="mt-0.5 shrink-0 text-muted-foreground">{props.icon}</span> : null}
      <span className="min-w-0">
        <span className="block truncate">{props.label}</span>
        {props.hint ? <span className="block truncate text-[10px] text-muted-foreground">{props.hint}</span> : null}
      </span>
    </button>
  );
}

/** A non-interactive divider with a caption. */
export function MenuGroup(props: { caption: string; children: React.ReactNode }) {
  return (
    <div className="mt-1 first:mt-0">
      <p className="px-2 pt-1.5 pb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{props.caption}</p>
      {props.children}
    </div>
  );
}

/** A menu row that opens a conversation rather than mutating anything. */
export function MenuLink(props: { label: string; value: string; onClick: () => void; muted?: boolean }) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={props.onClick}
      className={`w-full flex items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[11px] hover:bg-muted ${props.muted ? "text-muted-foreground italic" : "text-foreground/85"}`}
    >
      <span className="min-w-0 flex-1 truncate">{props.label}</span>
      {props.value ? <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">{props.value}</span> : null}
    </button>
  );
}
