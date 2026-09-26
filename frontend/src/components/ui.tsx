"use client";

import React from "react";

/** Consistent page section wrapper: title + plain-language subtitle + actions. */
export function Section(props: {
  title: string;
  hint?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="workspace-section flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 py-5 w-full">
      <div className="max-w-6xl mx-auto space-y-4">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-base font-semibold tracking-tight">{props.title}</h2>
            {props.hint && <p className="text-xs text-muted-foreground mt-0.5 max-w-2xl">{props.hint}</p>}
          </div>
          {props.actions && <div className="flex items-center gap-2 flex-wrap">{props.actions}</div>}
        </div>
        {props.children}
      </div>
    </div>
  );
}

export function StatCard(props: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-border/60 bg-card px-3 py-2.5">
      <div className="text-sm font-bold leading-tight break-words">{props.value}</div>
      <div className="text-[10px] text-muted-foreground mt-1">{props.label}</div>
      {props.sub && <div className="text-[10px] text-muted-foreground">{props.sub}</div>}
    </div>
  );
}

export function EmptyState(props: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-border/70 bg-card/50 px-6 py-10 text-center">
      <p className="text-sm font-medium">{props.title}</p>
      {props.hint && <p className="text-xs text-muted-foreground mt-1 max-w-md mx-auto">{props.hint}</p>}
      {props.action && <div className="mt-3 flex justify-center">{props.action}</div>}
    </div>
  );
}

export function ErrorBox(props: { message: string; onRetry?: () => void }) {
  return (
    <div className="rounded-xl border border-destructive/40 bg-destructive/5 px-3 py-2.5 text-xs flex items-center gap-2 flex-wrap">
      <span className="flex-1 min-w-40">{props.message}</span>
      {props.onRetry && (
        <button
          type="button"
          onClick={props.onRetry}
          className="px-2.5 py-1 rounded-lg border border-border text-[11px] font-medium hover:bg-muted"
        >
          Try again
        </button>
      )}
    </div>
  );
}

export function Notice(props: { message: string }) {
  return (
    <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-3 py-2.5 text-xs text-emerald-700 dark:text-emerald-300">
      {props.message}
    </div>
  );
}

/** Labeled form field with hint text — keeps every form self-explanatory. */
export function Field(props: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-[11px] font-semibold">{props.label}</span>
      {props.children}
      {props.hint && <span className="block text-[11px] text-muted-foreground font-normal">{props.hint}</span>}
    </label>
  );
}

export const inputCls =
  "w-full bg-card border border-border/70 rounded-xl px-3 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40 text-foreground placeholder:text-muted-foreground";

export function Btn(props: {
  children: React.ReactNode;
  onClick?: (e: React.MouseEvent) => void;
  type?: "button" | "submit";
  variant?: "primary" | "ghost" | "danger";
  disabled?: boolean;
  title?: string;
  className?: string;
}) {
  const base =
    "inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold transition-opacity disabled:opacity-40";
  const style =
    props.variant === "danger"
      ? "bg-destructive text-destructive-foreground hover:opacity-90"
      : props.variant === "ghost"
        ? "border border-border hover:bg-muted"
        : "bg-primary text-primary-foreground hover:opacity-95";
  return (
    <button
      type={props.type || "button"}
      disabled={props.disabled}
      onClick={props.onClick}
      title={props.title}
      className={`${base} ${style} ${props.className || ""}`}
    >
      {props.children}
    </button>
  );
}

export function Badge(props: {
  children: React.ReactNode;
  tone?: "green" | "amber" | "gray" | "blue" | "purple" | "cyan" | "red" | "indigo";
}) {
  const tone =
    props.tone === "green"
      ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
      : props.tone === "amber"
        ? "bg-amber-500/10 text-amber-600 dark:text-amber-400"
        : props.tone === "blue"
          ? "bg-primary/10 text-primary"
          : props.tone === "indigo"
            ? "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400"
            : props.tone === "purple"
              ? "bg-purple-500/10 text-purple-600 dark:text-purple-400"
            : props.tone === "cyan"
              ? "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400"
              : props.tone === "red"
                ? "bg-red-500/10 text-red-600 dark:text-red-400"
                : "bg-muted text-muted-foreground";
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full ${tone}`}>
      {props.children}
    </span>
  );
}

/** Simple accessible modal drawer used by detail views. */
export function Modal(props: { title: string; subtitle?: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={props.title}>
      <div className="absolute inset-0 bg-black/40" onClick={props.onClose} />
      <aside className="relative w-full max-w-md h-full bg-card border-l border-border shadow-2xl flex flex-col overflow-hidden">
        <div className="p-4 border-b border-border/60">
          <h2 className="text-sm font-semibold">{props.title}</h2>
          {props.subtitle && <p className="text-[11px] text-muted-foreground mt-0.5">{props.subtitle}</p>}
        </div>
        <div className="flex-1 overflow-y-auto p-4">{props.children}</div>
        <div className="p-3 border-t border-border/60">
          <Btn variant="ghost" onClick={props.onClose}>
            Close
          </Btn>
        </div>
      </aside>
    </div>
  );
}

/** Loading placeholder grid. */
export function SkeletonList(props: { rows?: number }) {
  const rows = props.rows ?? 4;
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="rounded-xl border border-border/60 bg-card p-3 space-y-2 animate-pulse">
          <div className="h-3 rounded bg-muted w-1/3" />
          <div className="h-2.5 rounded bg-muted w-2/3" />
        </div>
      ))}
    </div>
  );
}
