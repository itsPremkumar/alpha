/**
 * BotDetailView — the dedicated single-bot detail page.
 *
 * This is deliberately NOT the existing `chat-shell/BotDetailPanel`. That panel
 * is an *editor* (it PATCHes fields); this is a *reader* whose whole job is to
 * show everything the gateway actually reports about one bot, section by
 * section, and to be explicit about what it could not read.
 *
 * The honesty contract, which is the reason this file looks the way it does:
 *
 *  - "Silence is not success." Each section renders one of four states:
 *    loading, failed-with-reason, empty-because-the-server-said-so, or present.
 *    A failed read NEVER looks like an empty one.
 *  - Absent is not zero. `unread_count: null`, `reputation_score: null` and a
 *    missing heartbeat all render as "not reported". Coercing them to 0 / 1 /
 *    "" would claim the server measured something it never did.
 *  - Unknown enums are shown verbatim. A `status` from a newer Gateway is
 *    printed as-is rather than snapped to "active".
 *
 * Every panel is independent: one failing route leaves the others intact, which
 * is why the bundle is settled with `Promise.allSettled`.
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Building2, RefreshCw } from "lucide-react";

import { botDisplayName, botInitials } from "@/types/bots";
import {
  BotDetailBundle,
  DetailState,
  detailValue,
  enumOrUnknown,
  fetchBotDetail,
} from "@/lib/bot-detail";
import type { BotProfile } from "@/types/bots";

const KNOWN_STATUSES = ["active", "paused", "disabled"] as const;

type Tone = "neutral" | "good" | "warn" | "bad";

/**
 * Section wrapper that owns the loading / error / empty / present decision, so
 * no individual panel can accidentally render "nothing here" when the truth is
 * "this read failed".
 *
 * Generic over the section's value type: `children` is only ever invoked in the
 * `ok` branch, with the settled value, so a panel physically cannot render
 * against a value that does not exist.
 */
function Section<T>({
  title,
  state,
  children,
  emptyText,
}: {
  title: string;
  state: DetailState<T> | { state: "loading" };
  children?: (value: T) => React.ReactNode;
  emptyText?: string;
}) {
  const tone =
    state.state === "error" ? "border-destructive/40 bg-destructive/5" : "border-border/60 bg-card/40";

  return (
    <section className={`rounded-xl border ${tone} p-4`}>
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-3">{title}</h2>

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

      {state.state === "ok" && !children && emptyText && (
        <p className="text-sm text-muted-foreground">{emptyText}</p>
      )}

      {state.state === "ok" && children?.(state.value)}
    </section>
  );
}

/** One labelled value. `null` renders "not reported", never an empty cell. */
function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5 border-b border-border/40 last:border-0">
      <span className="text-xs text-muted-foreground shrink-0">{label}</span>
      <span
        className={`text-xs text-right break-words ${value ? "text-foreground" : "text-muted-foreground italic"}`}
      >
        {value || "not reported"}
      </span>
    </div>
  );
}

function TagList({ items, empty }: { items: string[]; empty: string }) {
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

function StatusBadge({ status }: { status: string }) {
  const { value, known } = enumOrUnknown(status, KNOWN_STATUSES);
  const tone: Tone = value === "active" ? "good" : value === "paused" ? "warn" : value === "disabled" ? "bad" : "neutral";
  const cls = {
    good: "border-emerald-500/40 text-emerald-300 bg-emerald-500/10",
    warn: "border-amber-500/40 text-amber-300 bg-amber-500/10",
    bad: "border-destructive/40 text-destructive bg-destructive/10",
    neutral: "border-border/60 text-muted-foreground bg-muted/30",
  }[tone];
  // An unrecognised status is real server state, so it is shown verbatim with a
  // marker that this build does not know it - never snapped to "active".
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      {value}
      {!known && <span className="ml-1 opacity-70">(unknown to this build)</span>}
    </span>
  );
}

export function BotDetailView({ name }: { name: string }) {
  const [bundle, setBundle] = useState<BotDetailBundle | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setBundle(await fetchBotDetail(name));
    setLoading(false);
  }, [name]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !bundle) {
    return (
      <div className="p-8 text-sm text-muted-foreground" role="status">
        Loading {name}…
      </div>
    );
  }

  const profile = bundle?.profile;
  const bot: BotProfile | null = profile?.state === "ok" ? profile.value : null;

  return (
    <div className="mx-auto w-full max-w-5xl px-6 py-8 space-y-6">
      {/* ---- Header ---- */}
      <header className="flex items-start gap-4">
        <div className="flex size-16 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-600 to-blue-600 text-xl font-bold text-white">
          {bot ? botInitials(bot) : "?"}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-bold tracking-tight">{bot ? botDisplayName(bot) : name}</h1>
            {bot && <StatusBadge status={bot.status} />}
            <span className="rounded-md border border-border/60 px-1.5 py-0.5 text-[11px] font-mono text-muted-foreground">
              {name}
            </span>
          </div>
          {bot?.role && <p className="mt-1 text-sm text-muted-foreground">{bot.role}</p>}
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="flex items-center gap-1.5 rounded-lg border border-border/60 px-2.5 py-1.5 text-xs hover:bg-muted/40"
        >
          <RefreshCw className="size-3" /> Refresh
        </button>
      </header>

      {/* A profile read that failed means every identity field below is unknown.
          Say so once, loudly, rather than rendering a page of "not reported". */}
      {profile?.state === "error" && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4" role="alert">
          <p className="text-sm font-medium text-destructive">The bot profile could not be loaded.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Gateway said: <span className="font-mono">{profile.reason}</span>. The identity rows below are
            unknown, not empty.
          </p>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {/* ---- Identity ---- */}
        <Section title="Identity" state={profile ?? { state: "loading" }}>
          {(b) => (
            <div>
              <Row label="Internal name" value={b.name} />
              <Row label="Display name" value={b.display_name} />
              <Row label="Role" value={b.role} />
              <Row label="Department" value={b.department} />
              <Row label="Reports to" value={b.reports_to} />
              <Row label="Version" value={b.version != null ? String(b.version) : null} />
              <Row label="Epoch" value={b.epoch} />
              <Row label="Avatar" value={b.avatar} />
              <Row label="Model" value={b.model} />
              <Row label="Created" value={b.created_at} />
              <Row label="Updated" value={b.updated_at} />
              <Row label="Last active" value={b.last_active} />
            </div>
          )}
        </Section>

        {/* ---- SOUL ---- */}
        <Section title="Soul" state={profile ?? { state: "loading" }}>
          {(b) =>
            b.soul ? (
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">{b.soul}</p>
            ) : (
              <p className="text-sm text-muted-foreground">
                No SOUL recorded for this bot. That is the server&apos;s answer, not a failed read.
              </p>
            )
          }
        </Section>

        {/* ---- Capabilities ---- */}
        <Section title="Capabilities" state={profile ?? { state: "loading" }}>
          {(b) => (
            <div className="space-y-3">
              <div>
                <p className="mb-1.5 text-xs text-muted-foreground">Declared capabilities</p>
                <TagList items={b.capabilities} empty="None declared." />
              </div>
              <div>
                <p className="mb-1.5 text-xs text-muted-foreground">Responsibilities</p>
                <TagList items={b.responsibilities} empty="None recorded." />
              </div>
            </div>
          )}
        </Section>

        {/* ---- Skills & toolsets ---- */}
        <Section title="Skills & toolsets" state={profile ?? { state: "loading" }}>
          {(b) => (
            <div className="space-y-3">
              <div>
                <p className="mb-1.5 text-xs text-muted-foreground">Skills</p>
                <TagList items={b.skills} empty="No skills assigned." />
              </div>
              <div>
                <p className="mb-1.5 text-xs text-muted-foreground">Toolsets</p>
                <TagList items={b.toolsets} empty="No toolsets assigned." />
              </div>
            </div>
          )}
        </Section>

        {/* ---- Performance ---- */}
        <Section title="Performance" state={bundle?.performance ?? { state: "loading" }}>
          {(raw) => {
            const r = raw as Record<string, unknown>;
            const score = typeof r.reputation_score === "number" ? String(r.reputation_score.toFixed(3)) : null;
            return (
              <div>
                <Row label="Reputation score" value={score} />
                <Row label="Total runs" value={detailValue(r, "total_runs")} />
                <Row label="Completed" value={detailValue(r, "completed")} />
                <Row label="Failed" value={detailValue(r, "failed")} />
                <Row label="Avg duration (s)" value={detailValue(r, "avg_duration_sec")} />
              </div>
            );
          }}
        </Section>

        {/* ---- Liveness ---- */}
        <Section title="Liveness" state={profile ?? { state: "loading" }}>
          {(b) => (
            <div>
              <Row label="Heartbeat" value={b.heartbeat} />
              <Row label="Succession fallback" value={b.succession_fallback} />
              <Row
                label="Unread messages"
                value={b.unread_count != null ? String(b.unread_count) : null}
              />
              {/* A withheld body means the gateway found credential-shaped content
                  and chose not to project it. That must be announced, never
                  replaced with a placeholder body. */}
              {b.last_message_withheld && (
                <p className="mt-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-200">
                  The newest message body was withheld by the gateway because it looked credential-shaped. A
                  message exists; its content was deliberately not projected.
                </p>
              )}
              {!b.last_message_withheld && (
                <Row
                  label="Last message"
                  value={b.last_message_preview ?? (b.last_message_sender ? `from ${b.last_message_sender}` : null)}
                />
              )}
            </div>
          )}
        </Section>

        {/* ---- Inbox ---- */}
        <Section title="Direct messages" state={bundle?.inbox ?? { state: "loading" }}>
          {(raw) => {
            const box = raw as { messages: Array<Record<string, unknown>>; unread_count: number | null };
            const msgs = Array.isArray(box.messages) ? box.messages : [];
            return (
              <div className="space-y-2">
                <Row label="Unread" value={box.unread_count != null ? String(box.unread_count) : null} />
                {msgs.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Inbox is empty — the server reported no messages.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {msgs.slice(0, 20).map((m, i) => (
                      <li key={String(m.id ?? i)} className="rounded-lg border border-border/60 p-2 text-xs">
                        <span className="font-medium">{String(m.sender ?? "unknown sender")}</span>
                        <span className="text-muted-foreground"> · {String(m.created_at ?? "no timestamp")}</span>
                        <p className="mt-1 text-foreground/90">{String(m.body ?? "(no body)")}</p>
                      </li>
                    ))}
                    {msgs.length > 20 && (
                      <li className="text-[11px] text-muted-foreground">
                        Showing the first 20 of {msgs.length} messages.
                      </li>
                    )}
                  </ul>
                )}
              </div>
            );
          }}
        </Section>

        {/* ---- Routines ---- */}
        <Section title="Routines" state={profile ?? { state: "loading" }}>
          {(b) =>
            b.routines.length === 0 ? (
              <p className="text-sm text-muted-foreground">No routines recorded for this bot.</p>
            ) : (
              <ul className="space-y-1.5">
                {b.routines.map((r, i) => (
                  <li key={i} className="rounded-lg border border-border/60 p-2 text-xs font-mono">
                    {JSON.stringify(r)}
                  </li>
                ))}
              </ul>
            )
          }
        </Section>

        {/* ---- Org position ---- */}
        <Section title="Organization position" state={bundle?.organization ?? { state: "loading" }}>
          {(raw) => {
            const chart = raw as Record<string, unknown>;
            return (
              <div>
                <Row label="Department" value={detailValue(chart, "department")} />
                <Row label="Reports to" value={detailValue(chart, "reports_to")} />
                <p className="mt-2 text-[11px] text-muted-foreground">
                  Full hierarchy is available from the fleet view. This row reports only what the chart endpoint
                  returned for the whole organization.
                </p>
              </div>
            );
          }}
        </Section>

        {/* ---- Fleet context ---- */}
        <Section title="Fleet context" state={bundle?.fleetHealth ?? { state: "loading" }}>
          {(raw) => {
            const h = raw as Record<string, unknown>;
            return (
              <div>
                <Row label="Total bots" value={detailValue(h, "total")} />
                <Row label="Active" value={detailValue(h, "active")} />
                <Row label="Paused" value={detailValue(h, "paused")} />
                <Row label="Disabled" value={detailValue(h, "disabled")} />
                <Row label="Avg reputation" value={detailValue(h, "avg_reputation")} />
              </div>
            );
          }}
        </Section>
      </div>

      <p className="flex items-center gap-1.5 pt-2 text-[11px] text-muted-foreground">
        <Building2 className="size-3" />
        Every value above is read from the Gateway. Anything it did not report is shown as
        &ldquo;not reported&rdquo; rather than as a default.
      </p>
    </div>
  );
}