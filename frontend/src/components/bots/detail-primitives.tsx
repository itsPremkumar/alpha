/**
 * Shared presentation primitives for the bot detail page.
 *
 * These were lifted out of `BotDetailView.tsx` because the page now renders a
 * dozen independent sections and each one has to make the same four decisions
 * (loading / failed / empty / present) in the same words. Inlining them a
 * second time is exactly how a "Projects (0)" header ends up sitting above a
 * failed read — the label and the state drift apart with nothing failing.
 *
 * Ownership rules:
 *   - `Section` owns the failed-read sentence. A panel must never render its
 *     own "nothing here" for a read that did not answer.
 *   - `Row` owns "not reported". `null` and `""` both render the words, never
 *     a blank cell that reads as a value the server actually sent.
 *   - `StatusBadge` owns enum tone **and** the unknown-enum marker, so a status
 *     from a newer Gateway is shown verbatim instead of being snapped to a
 *     colour this build picked.
 *   - `Notice` is for a *disclosure beside data that did arrive* (a partial
 *     list, a filter's hidden rows) — not for a failed read, which is always a
 *     `Section`.
 */

"use client";

import Link from "next/link";
import { AlertTriangle, Info } from "lucide-react";

import { enumOrUnknown, type DetailState } from "@/lib/bot-detail";
import { workspaceViewUrl, type WorkspaceView } from "@/lib/workspace-view";

export type Tone = "neutral" | "good" | "warn" | "bad";

/** The status vocabulary this build knows how to colour. */
export const KNOWN_STATUSES = ["active", "paused", "disabled"] as const;

/**
 * Section wrapper that owns the loading / error / empty / present decision, so
 * no individual panel can accidentally render "nothing here" when the truth is
 * "this read failed".
 *
 * Generic over the section's value type: `children` is only ever invoked in the
 * `ok` branch, with the settled value, so a panel physically cannot render
 * against a value that does not exist.
 */
export function Section<T>({
  title,
  state,
  children,
  emptyText,
  action,
}: {
  title: string;
  state: DetailState<T> | { state: "loading" };
  children?: (value: T) => React.ReactNode;
  emptyText?: string;
  /** Rendered in the header — a link out to the surface that owns this data. */
  action?: React.ReactNode;
}) {
  const tone = state.state === "error" ? "border-destructive/40 bg-destructive/5" : "border-border/60 bg-card/40";

  return (
    <section className={`rounded-xl border ${tone} p-4`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
        {action}
      </div>

      {state.state === "loading" && (
        <p className="text-sm text-muted-foreground" role="status">
          Loading…
        </p>
      )}

      {state.state === "error" && (
        <p className="text-sm text-destructive flex items-start gap-2" role="alert">
          <AlertTriangle className="size-3.5 mt-0.5 shrink-0" />
          <span>
            <span className="font-medium">Could not load this section.</span> The gateway said:{" "}
            <span className="font-mono text-xs">{state.reason}</span>
          </span>
        </p>
      )}

      {state.state === "ok" && !children && emptyText && <p className="text-sm text-muted-foreground">{emptyText}</p>}

      {state.state === "ok" && children?.(state.value)}
    </section>
  );
}

/** One labelled value. `null` renders "not reported", never an empty cell. */
export function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5 border-b border-border/40 last:border-0">
      <span className="text-xs text-muted-foreground shrink-0">{label}</span>
      <span className={`text-xs text-right break-words ${value ? "text-foreground" : "text-muted-foreground italic"}`}>
        {value || "not reported"}
      </span>
    </div>
  );
}

/**
 * A count that may not have been measured.
 *
 * Deliberately takes `number | null`: a caller that could reach for `?? 0`
 * would turn "the Gateway sent no counter" into a measured zero, which is the
 * single most common way a detail page fabricates state.
 */
export function CountRow({ label, value }: { label: string; value: number | null }) {
  return <Row label={label} value={value === null ? null : String(value)} />;
}

export function TagList({ items, empty }: { items: string[]; empty: string }) {
  if (!items.length) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((t) => (
        <span key={t} className="rounded-md border border-border/60 bg-muted/40 px-1.5 py-0.5 text-[11px] font-mono">
          {t}
        </span>
      ))}
    </div>
  );
}

const TONE_CLS: Record<Tone, string> = {
  good: "border-emerald-500/40 text-emerald-300 bg-emerald-500/10",
  warn: "border-amber-500/40 text-amber-300 bg-amber-500/10",
  bad: "border-destructive/40 text-destructive bg-destructive/10",
  neutral: "border-border/60 text-muted-foreground bg-muted/30",
};

export function StatusBadge({ status, known, tone }: { status: string | null; known?: boolean; tone?: Tone }) {
  if (status === null)
    return (
      <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${TONE_CLS.neutral}`}>
        status not reported
      </span>
    );
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${TONE_CLS[tone ?? "neutral"]}`}>
      {status}
      {known === false && <span className="ml-1 opacity-70">(unknown to this build)</span>}
    </span>
  );
}

/**
 * The bot-profile status badge: the one place the registry `status` vocabulary
 * is mapped to a colour. An unrecognised status is real server state, so it is
 * shown verbatim with a marker — never snapped to "active".
 */
export function BotStatusBadge({ status }: { status: string | null }) {
  const { value, known } = enumOrUnknown(status, KNOWN_STATUSES);
  const tone: Tone = value === "active" ? "good" : value === "paused" ? "warn" : value === "disabled" ? "bad" : "neutral";
  return <StatusBadge status={status === null ? null : value} known={known} tone={tone} />;
}

/**
 * A neutral badge carrying a server string verbatim.
 *
 * Used for room `mode` / `lifecycle`, employment `state`, kanban `status` and
 * event `event_type` — four vocabularies this build has no authority to rank,
 * so all four render the same way rather than borrowing a colour scale nobody
 * declared.
 */
export function VerbatimBadge({ value, empty }: { value: string | null; empty: string }) {
  if (value === null || value === "") return <span className="text-[11px] italic text-muted-foreground">{empty}</span>;
  return (
    <span className="rounded-md border border-border/60 bg-muted/40 px-1.5 py-0.5 font-mono text-[11px]">{value}</span>
  );
}

/** A disclosure rendered *beside data that did arrive*. Not a failed read. */
export function Notice({ children, tone = "neutral" }: { children: React.ReactNode; tone?: Tone }) {
  const icon = tone === "neutral" ? <Info className="size-3.5 mt-0.5 shrink-0" /> : <AlertTriangle className="size-3.5 mt-0.5 shrink-0" />;
  return (
    <p className={`rounded-lg border px-3 py-2 text-xs flex items-start gap-2 ${TONE_CLS[tone]}`}>
      {icon}
      <span>{children}</span>
    </p>
  );
}

/**
 * An honest empty list.
 *
 * `list === null` and `list.length === 0` are different facts and get
 * different sentences, because "the Gateway sent no list" and "the list is
 * empty" lead to opposite conclusions.
 */
export function ListState<T>({
  list,
  emptyText,
  noListText,
  children,
}: {
  list: T[] | null;
  emptyText: string;
  noListText: string;
  children: (rows: T[]) => React.ReactNode;
}) {
  if (list === null) return <p className="text-sm text-muted-foreground italic">{noListText}</p>;
  if (!list.length) return <p className="text-sm text-muted-foreground">{emptyText}</p>;
  return <>{children(list)}</>;
}

/** A link out to the workspace view that owns a deeper record for this data. */
export function ViewLink({ view, children }: { view: WorkspaceView; children: React.ReactNode }) {
  return (
    <Link
      href={workspaceViewUrl(view)}
      className="shrink-0 rounded-md border border-border/60 px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted/40 hover:text-foreground"
    >
      {children}
    </Link>
  );
}
