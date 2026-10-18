"use client";

/**
 * Bot detail page (`/bots/[name]`).
 *
 * Deliberately separate from `chat-shell/BotDetailPanel`, which is an *editor*
 * that PATCHes profile fields. This route is a read-only survey of everything
 * the Gateway reports about one bot, and — where the full record genuinely
 * lives on another surface — a link out to it rather than a second copy.
 *
 * Two rules the whole page is built around, both enforced in
 * `lib/bot-detail.ts` rather than here:
 *
 *   1. **Every section fails alone.** Twelve reads settle independently, so a
 *      corrupt group store cannot blank a healthy project list.
 *   2. **Absence is never a value.** A count the Gateway did not send renders
 *      "not reported", never `0`; a list it did not send renders as absent,
 *      never as empty.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Building2, FlaskConical, PencilLine, RefreshCw } from "lucide-react";

import { botDisplayName, botInitials, type BotProfile } from "@/types/bots";
import { botSmokeBadge, runBotSmoke } from "@/lib/bots";
import {
  fetchBotDetail,
  fetchKanbanTasks,
  kanbanTasksFor,
  workRunsForBot,
  type BotDetailBundle,
  type BotGroupRoom,
  type BotProjectRow,
} from "@/lib/bot-detail";
import { elapsedWords, type FleetHealthSummary } from "@/lib/bot-working-status";
import { kanbanStatusLabel, type KanbanTask } from "@/lib/kanban";
import { workspaceViewUrl, type WorkspaceView } from "@/lib/workspace-view";
import { BotModelConfigPanel } from "./BotModelConfigPanel";
import { CapabilityScopePanel } from "./CapabilityScopePanel";
import { Modal } from "@/components/ui";
import { BotDetailPanel } from "@/components/chat-shell/BotDetailPanel";
import {
  BotStatusBadge,
  CountRow,
  ListState,
  Notice,
  Row,
  Section,
  SmokeBadge,
  TagList,
  VerbatimBadge,
  ViewLink,
} from "./detail-primitives";

/**
 * Where each block's *full* record lives.
 *
 * Only views that genuinely accept this data are listed. There is
 * deliberately no "open this bot's chat" entry: nothing on the chat surface
 * reads a bot parameter, so such a link would promise a selection it cannot
 * perform — the canonical thread id is printed instead, in its own section.
 */
const REDIRECTS: ReadonlyArray<{ view: WorkspaceView; label: string; why: string }> = [
  { view: "bots", label: "Bots", why: "roster, health strip and filters" },
  { view: "projects", label: "Projects", why: "crews, rosters and the per-project inspector" },
  { view: "company", label: "Company", why: "organizations, roles and units" },
  { view: "kanban", label: "Board", why: "every task, not only this bot's" },
  { view: "messages", label: "Messages", why: "group rooms and direct history" },
  { view: "chat", label: "Chat", why: "the live workspace" },
];

type KanbanState =
  | { state: "loading" }
  | { state: "error"; reason: string }
  | { state: "ok"; tasks: KanbanTask[] };

/**
 * The fleet-summary keys this build renders, typed as `keyof FleetHealthSummary`
 * so a state the Gateway adds is a compile error here rather than a silently
 * dropped row.
 */
const FLEET_SUMMARY_KEYS: ReadonlyArray<keyof FleetHealthSummary> = [
  "total",
  "healthy",
  "stale",
  "stalled",
  "dead",
  "sleeping",
  "suspended",
  "archived",
];

const errText = (err: unknown) => (err instanceof Error ? err.message : String(err));

export function BotDetailView({ name }: { name: string }) {
  const [bundle, setBundle] = useState<BotDetailBundle | null>(null);
  const [loading, setLoading] = useState(true);
  // The board is fleet-wide and lives behind its own client, so it is its own
  // read with its own failure — never folded into `fetchBotDetail`, where a
  // board outage would take the profile down with it.
  const [kanban, setKanban] = useState<KanbanState>({ state: "loading" });
  // The page itself stays a read-only survey; editing opens the one editor
  // that already exists (`chat-shell/BotDetailPanel`, which PATCHes through
  // the server's own field list) rather than growing a second field form here.
  const [editing, setEditing] = useState(false);
  // The smoke probe is an ACTION over the read above, so it gets its own state
  // and its own failure line: a probe that could not be started must not blank
  // the profile, and after a run the badge must show the SERVER's recorded
  // outcome (re-read), not the POST body.
  const [smokeBusy, setSmokeBusy] = useState(false);
  const [smokeError, setSmokeError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setKanban({ state: "loading" });
    void fetchKanbanTasks()
      .then((tasks) => setKanban({ state: "ok", tasks }))
      .catch((err: unknown) => setKanban({ state: "error", reason: errText(err) }));
    setBundle(await fetchBotDetail(name));
    setLoading(false);
  }, [name]);

  const runSmoke = useCallback(async () => {
    setSmokeError(null);
    setSmokeBusy(true);
    try {
      await runBotSmoke(name);
      await load();
    } catch (err) {
      setSmokeError(errText(err));
    } finally {
      setSmokeBusy(false);
    }
  }, [name, load]);

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
  const key = name.trim().toLowerCase();

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
            <BotStatusBadge status={bot?.status ?? null} />
            <SmokeBadge badge={botSmokeBadge(bot)} />
            <span className="rounded-md border border-border/60 px-1.5 py-0.5 text-[11px] font-mono text-muted-foreground">
              {name}
            </span>
          </div>
          {bot?.role && <p className="mt-1 text-sm text-muted-foreground">{bot.role}</p>}
        </div>
        <Link
          href={workspaceViewUrl("bots")}
          className="flex items-center gap-1.5 rounded-lg border border-border/60 px-2.5 py-1.5 text-xs hover:bg-muted/40"
        >
          <ArrowLeft className="size-3" /> Roster
        </Link>
        <button
          type="button"
          onClick={() => setEditing(true)}
          disabled={!bot}
          title={bot ? "Edit this bot's profile" : "The profile could not be read, so there is nothing to edit"}
          className="flex items-center gap-1.5 rounded-lg border border-border/60 px-2.5 py-1.5 text-xs hover:bg-muted/40 disabled:opacity-40"
        >
          <PencilLine className="size-3" /> Edit
        </button>
        <button
          type="button"
          onClick={() => void runSmoke()}
          disabled={!bot || smokeBusy}
          title={
            bot
              ? "Run one real bot-scoped run and record the observed result"
              : "The profile could not be read, so there is nothing to probe"
          }
          className="flex items-center gap-1.5 rounded-lg border border-border/60 px-2.5 py-1.5 text-xs hover:bg-muted/40 disabled:opacity-40"
        >
          <FlaskConical className="size-3" /> {smokeBusy ? "Probing…" : "Run smoke"}
        </button>
        <button
          type="button"
          onClick={() => void load()}
          className="flex items-center gap-1.5 rounded-lg border border-border/60 px-2.5 py-1.5 text-xs hover:bg-muted/40"
        >
          <RefreshCw className="size-3" /> Refresh
        </button>
      </header>

      {smokeError && (
        <p className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive" role="alert">
          The smoke probe could not be started: <span className="font-mono">{smokeError}</span>. Nothing was recorded —
          the badge above still shows the last smoke the Gateway observed.
        </p>
      )}

      {/* ---- Where the rest of the record lives ---- */}
      <nav aria-label="Related views" className="rounded-xl border border-border/60 bg-card/40 p-4">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
          The full record lives elsewhere too
        </h2>
        <p className="mb-3 text-xs text-muted-foreground">
          This page reads the per-bot projection. Each link opens the surface that owns the deeper data — the same
          Gateway, a different question.
        </p>
        <ul className="flex flex-wrap gap-2">
          {REDIRECTS.map((r) => (
            <li key={r.view}>
              <Link
                href={workspaceViewUrl(r.view)}
                title={r.why}
                className="block rounded-lg border border-border/60 px-2.5 py-1.5 text-xs hover:bg-muted/40"
              >
                <span className="font-medium">{r.label}</span>
                <span className="ml-1.5 text-muted-foreground">{r.why}</span>
              </Link>
            </li>
          ))}
        </ul>
      </nav>

      {/* A profile read that failed means every identity field below is unknown.
          Say so once, loudly, rather than rendering a page of "not reported". */}
      {profile?.state === "error" && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4" role="alert">
          <p className="text-sm font-medium text-destructive">The bot profile could not be loaded.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Gateway said: <span className="font-mono">{profile.reason}</span>. The identity rows below are unknown,
            not empty.
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
              {/* The scoping read is its own route with its own failure, so it
                  renders from the bundle's `capabilities` slot — never from the
                  profile above (a failed profile read must not silently scope
                  this panel out of existence, and vice versa). */}
              <CapabilityScopePanel
                name={name}
                state={bundle?.capabilities ?? { state: "loading" }}
                onSaved={load}
              />
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

        {/* ---- Working now ---- */}
        <Section title="Working now" state={bundle?.working ?? { state: "loading" }}>
          {(report) => {
            const runs = workRunsForBot(report, name);
            const pause = bundle?.pause;
            const pausedReason =
              pause?.state === "ok" ? (pause.value.paused?.[key] ?? null) : null;
            const killSwitch = pause?.state === "ok" ? pause.value.active : null;

            return (
              <div className="space-y-3">
                {/* A pause outranks a live run for the headline, but both facts
                    are shown: an admitted run really is finishing. */}
                {pausedReason !== null && (
                  <Notice tone="warn">
                    <span className="font-medium">Paused by an operator.</span> {pausedReason}
                  </Notice>
                )}
                {killSwitch === true && (
                  <Notice tone="bad">
                    <span className="font-medium">The fleet kill switch is engaged.</span> The Gateway reported it
                    active, so no bot is accepting new work.
                  </Notice>
                )}
                {killSwitch === null && pause?.state === "ok" && (
                  <Notice>
                    The kill-switch flag was not reported, so this page cannot say whether the fleet is stopped.
                  </Notice>
                )}
                {pause?.state === "error" && (
                  <Notice tone="warn">
                    Pause state could not be read: <span className="font-mono">{pause.reason}</span>. Working state
                    below is from the run store alone.
                  </Notice>
                )}

                {!report.reported && (
                  <p className="text-sm text-muted-foreground">
                    The run store could not be read{report.reason ? (
                      <>
                        : <span className="font-mono">{report.reason}</span>
                      </>
                    ) : null}
                    . This is not a claim that the bot is idle — the store that would answer is unavailable.
                  </p>
                )}

                {report.reported && runs.length === 0 && (
                  <p className="text-sm text-muted-foreground">
                    No run is currently attributed to this bot. Attribution is by the run&apos;s assistant id, so a
                    run started from elsewhere is not counted here.
                  </p>
                )}

                {runs.length > 0 && (
                  <ul className="space-y-1.5">
                    {runs.map((run) => (
                      <li key={run.run_id} className="rounded-lg border border-border/60 p-2 text-xs">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <VerbatimBadge value={run.status} empty="status not reported" />
                          <span className="font-mono">{run.run_id}</span>
                        </div>
                        <p className="mt-1 text-foreground/90">
                          {run.thread_title ? `on "${run.thread_title}"` : "on an untitled thread"}
                        </p>
                        <p className="mt-0.5 text-muted-foreground">
                          for {elapsedWords(run.elapsed_seconds)}
                          {run.model_name ? ` · on ${run.model_name}` : " · model not reported"}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          }}
        </Section>

        {/* ---- Performance ---- */}
        <Section title="Performance" state={bundle?.performance ?? { state: "loading" }}>
          {(p) => (
            <div>
              {/* `basis` travels with the numbers: the route reports 0 runs and
                  a 0.0 average for a bot that has never run, and only its own
                  sentence keeps that zero from reading as a measured idle
                  history rather than "nothing was ever recorded". */}
              {p.basis && <p className="mb-2 text-[11px] italic text-muted-foreground">{p.basis}</p>}
              <Row
                label="Reputation score"
                value={p.reputation_score === null ? null : p.reputation_score.toFixed(3)}
              />
              {/* The tier is the server's word, never this build's ranking of it. */}
              <VerbatimBadge value={p.reputation_tier} empty="tier not reported" />
              <Row
                label="Success rate (%)"
                value={p.success_rate_percent === null ? null : String(p.success_rate_percent)}
              />
              <CountRow label="Total runs" value={p.total_runs} />
              <CountRow label="Completed runs" value={p.completed_runs} />
              <CountRow label="Failed runs" value={p.failed_runs} />
              <Row
                label="Avg duration (s)"
                value={p.avg_duration_seconds === null ? null : String(p.avg_duration_seconds)}
              />
            </div>
          )}
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
                  The newest message body was withheld by the gateway because it looked credential-shaped. A message
                  exists; its content was deliberately not projected.
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

        {/* ---- Projects ---- */}
        <Section
          title="Projects"
          state={bundle?.projects ?? { state: "loading" }}
          action={<ViewLink view="projects">Open Projects</ViewLink>}
        >
          {(p) => (
            <div className="space-y-3">
              <ListState
                list={p.projects}
                noListText="The Gateway sent no project list, so membership is unknown rather than empty."
                emptyText="The server reported no projects for this bot."
              >
                {(rows) => (
                  <ul className="space-y-1.5">
                    {rows.map((row: BotProjectRow) => (
                      <li key={row.id} className="rounded-lg border border-border/60 p-2 text-xs">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="font-medium">{row.name || row.id}</span>
                          <VerbatimBadge value={row.status || null} empty="project status not reported" />
                        </div>
                        <p className="mt-1 text-muted-foreground">
                          {row.role ? `role: ${row.role}` : "role not reported"}
                          {row.member_status ? ` · membership: ${row.member_status}` : ""}
                        </p>
                        {row.id && (
                          <Link
                            href={workspaceViewUrl("projects", "/", { project: row.id })}
                            className="mt-2 inline-block text-[11px] font-medium text-primary hover:underline"
                          >
                            Open project hub
                          </Link>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </ListState>

              {p.unreadable && p.unreadable.length > 0 && (
                <Notice tone="warn">
                  {p.unreadable.length} project{p.unreadable.length === 1 ? " was" : "s were"} excluded because their
                  crew could not be read. Absence from the list above is not proof of non-membership for those.
                </Notice>
              )}

              {/* There is no per-bot memory store. Saying so is the honest
                  rendering of an empty memory block — the alternative reads as
                  "this bot has no memory at all", which is false. */}
              {p.memory_scoped && (
                <p className="rounded-lg border border-border/60 bg-muted/30 p-2 text-[11px] text-muted-foreground">
                  Memory is scoped <span className="font-mono">{p.memory_scoped}</span>
                  {p.memory_note ? ` — ${p.memory_note}` : ". There is no per-bot memory store."}
                </p>
              )}
            </div>
          )}
        </Section>

        {/* ---- Groups & subgroups ---- */}
        <Section
          title="Groups & subgroups"
          state={bundle?.groups ?? { state: "loading" }}
          action={<ViewLink view="messages">Open Messages</ViewLink>}
        >
          {(g) => (
            <div className="space-y-3">
              {g.note && (
                <p className="text-[11px] text-muted-foreground">
                  {g.rooms_checked !== null
                    ? `${g.rooms_checked} room${g.rooms_checked === 1 ? "" : "s"} checked. `
                    : ""}
                  {g.note}
                </p>
              )}

              <ListState
                list={g.rooms}
                noListText="The Gateway sent no room list, so group membership is unknown rather than empty."
                emptyText="This bot appears in no group room — not directly, by rule, or by inheritance."
              >
                {(rooms) => (
                  <ul className="space-y-1.5">
                    {rooms.map((room: BotGroupRoom) => (
                      <li key={room.room_id || room.name} className="rounded-lg border border-border/60 p-2 text-xs">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="font-medium">{room.name}</span>
                          <VerbatimBadge value={room.mode || null} empty="mode not reported" />
                          <VerbatimBadge value={room.lifecycle || null} empty="lifecycle not reported" />
                        </div>
                        {room.path.length > 1 && (
                          <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground" title={room.path.join(" / ")}>
                            {room.path.join(" / ")}
                          </p>
                        )}
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          {room.origins.map((o) => (
                            <span
                              key={o}
                              className="rounded-md border border-border/60 bg-muted/40 px-1.5 py-0.5 font-mono text-[11px]"
                            >
                              {o}
                            </span>
                          ))}
                          <span
                            className={`rounded-full border px-2 py-0.5 text-[11px] ${
                              room.effective
                                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                                : "border-border/60 bg-muted/30 text-muted-foreground"
                            }`}
                          >
                            {room.effective ? "can take part" : "cannot take part"}
                          </span>
                        </div>
                        <p className="mt-1 text-muted-foreground">
                          {/* Both counts always travel together: "3 direct" over
                              six visible members would be a fabricated count. */}
                          {room.direct_count ?? "—"} direct · {room.effective_count ?? "—"} effective
                          {room.parent_count !== null ? ` · ${room.parent_count} parent${room.parent_count === 1 ? "" : "s"}` : ""}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </ListState>

              {g.unreadable && g.unreadable.length > 0 && (
                <Notice tone="warn">
                  {g.unreadable.length} room{g.unreadable.length === 1 ? "" : "s"} could not be rostered. The bot may
                  be a member of those; this read does not know.
                </Notice>
              )}
            </div>
          )}
        </Section>

        {/* ---- Company employment ---- */}
        <Section
          title="Company employment"
          state={bundle?.companies ?? { state: "loading" }}
          action={<ViewLink view="company">Open Company</ViewLink>}
        >
          {(c) => (
            <div className="space-y-3">
              {c.note && <p className="text-[11px] text-muted-foreground">{c.note}</p>}

              <ListState
                list={c.companies}
                noListText="The Gateway sent no employment list, so employment is unknown rather than none."
                emptyText="No organization records an employment for this bot."
              >
                {(rows) => (
                  <ul className="space-y-1.5">
                    {rows.map((row) => {
                      const employment = row.employment;
                      const status = typeof employment.status === "string" ? employment.status : null;
                      return (
                        <li key={row.company_id} className="rounded-lg border border-border/60 p-2 text-xs">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="font-medium">{row.name || row.company_id}</span>
                            <VerbatimBadge value={row.archetype || null} empty="archetype not reported" />
                            {/* A terminated employment keeps its own status: "no
                                longer works here" is not "never worked here". */}
                            <VerbatimBadge value={status} empty="employment status not reported" />
                          </div>
                          <p className="mt-1 text-muted-foreground">
                            company state: {row.state || "not reported"}
                            {row.role && typeof row.role.name === "string" ? ` · role: ${row.role.name}` : " · role not reported"}
                            {row.unit && typeof row.unit.name === "string" ? ` · unit: ${row.unit.name}` : ""}
                          </p>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </ListState>

              {c.companies_checked !== null && (
                <p className="text-[11px] text-muted-foreground">
                  {c.companies_checked} organization{c.companies_checked === 1 ? "" : "s"} checked.
                </p>
              )}
            </div>
          )}
        </Section>

        {/* ---- Kanban tasks ---- */}
        <Section
          title="Board tasks"
          state={
            kanban.state === "ok"
              ? { state: "ok", value: kanban.tasks }
              : kanban.state === "error"
                ? { state: "error", reason: kanban.reason }
                : { state: "loading" }
          }
          action={<ViewLink view="kanban">Open Board</ViewLink>}
        >
          {(tasks) => {
            const mine = kanbanTasksFor(tasks, name);
            return (
              <div className="space-y-3">
                <p className="text-[11px] text-muted-foreground">
                  {tasks.length} task{tasks.length === 1 ? "" : "s"} on the board; {mine.length} assigned to this
                  bot by exact assignee match.
                </p>
                {mine.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No board task is assigned to this bot. Unassigned tasks are not counted as this bot&apos;s.
                  </p>
                ) : (
                  <ul className="space-y-1.5">
                    {mine.map((task) => (
                      <li key={task.id} className="rounded-lg border border-border/60 p-2 text-xs">
                        <div className="flex flex-wrap items-center gap-1.5">
                          {/* The server's status, verbatim — never coerced into a
                              board column this build happens to know. */}
                          <VerbatimBadge value={kanbanStatusLabel(task.status) ?? task.status} empty="status not reported" />
                          {task.priority && (
                            <span className="rounded-md border border-border/60 px-1.5 py-0.5 text-[11px]">
                              {task.priority}
                            </span>
                          )}
                        </div>
                        <p className="mt-1 font-medium">{task.title || "(untitled task)"}</p>
                        {task.description && <p className="mt-0.5 text-muted-foreground">{task.description}</p>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          }}
        </Section>

        {/* ---- Chat history ---- */}
        <Section title="Chat history" state={bundle?.chat ?? { state: "loading" }}>
          {(c) => {
            const recent = c.recent;
            return (
              <div className="space-y-3">
                <div>
                  <Row label="Canonical thread id" value={c.canonical_thread_id} />
                  <Row label="Unread" value={c.unread_count != null ? String(c.unread_count) : null} />
                </div>

                <ListState
                  list={recent}
                  noListText="The Gateway sent no message list, so history is unknown rather than empty."
                  emptyText="No messages on the canonical bot-chat thread yet."
                >
                  {(msgs) => (
                    <ul className="space-y-1.5">
                      {msgs.slice(0, 10).map((m, i) => (
                        <li key={String(m.id ?? i)} className="rounded-lg border border-border/60 p-2 text-xs">
                          <span className="font-medium">{String(m.sender ?? "unknown sender")}</span>
                          <span className="text-muted-foreground"> · {String(m.created_at ?? "no timestamp")}</span>
                          <p className="mt-1 text-foreground/90">{String(m.body ?? "(no body)")}</p>
                        </li>
                      ))}
                      {msgs.length > 10 && (
                        <li className="text-[11px] text-muted-foreground">
                          Showing the newest 10 of {msgs.length} messages.
                        </li>
                      )}
                    </ul>
                  )}
                </ListState>
              </div>
            );
          }}
        </Section>

        {/* ---- Direct messages ---- */}
        <Section title="Direct messages" state={bundle?.inbox ?? { state: "loading" }}>
          {(box) => {
            const msgs = box.messages;
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
        <Section
          title="Organization position"
          state={bundle?.organization ?? { state: "loading" }}
          action={<ViewLink view="workforce">Open Workforce</ViewLink>}
        >
          {(o) => {
            const reports = o.direct_reports;
            return (
              <div className="space-y-3">
                {/* The chart's node carries the bot's own department and status.
                    A bot the chart omits (archived rows are excluded) gets that
                    sentence rather than a row of blanks. */}
                {o.node === null ? (
                  <p className="text-sm text-muted-foreground">
                    The organization chart carries no node for this bot. Retired bots are excluded from it, so this is
                    the chart&apos;s answer, not a failed read.
                  </p>
                ) : (
                  <>
                    <Row label="Department (chart)" value={o.node.department || null} />
                    <Row label="Status (chart)" value={o.node.status || null} />
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs text-muted-foreground">Position</span>
                      <VerbatimBadge value={o.is_root === null ? null : o.is_root ? "root" : "subordinate"} empty="not reported" />
                    </div>
                  </>
                )}

                <div>
                  <p className="mb-1.5 text-xs text-muted-foreground">Reports directly to this bot</p>
                  {/* `reports_to` is on the profile; *its subordinates* appear
                      nowhere else, which is what this block is for. */}
                  {reports === null ? (
                    <p className="text-sm text-muted-foreground">
                      The chart did not say whether anyone reports to this bot.
                    </p>
                  ) : (
                    <TagList items={reports} empty="No bot reports directly to this one." />
                  )}
                </div>

                <CountRow label="Bots in chart" value={o.total_bots} />
              </div>
            );
          }}
        </Section>

        {/* ---- Fleet context ---- */}
        <Section
          title="Fleet context"
          state={bundle?.fleetHealth ?? { state: "loading" }}
          action={<ViewLink view="bots">Open Roster</ViewLink>}
        >
          {(health) => (
            <div>
              {/* `normalizeHealthOverview` reads the summary key by key, so a
                  state this Gateway does not know renders "not reported"
                  here rather than as `0`, which would claim the monitor
                  measured an empty fleet. */}
              <div className="grid grid-cols-2 gap-x-6">
                {FLEET_SUMMARY_KEYS.map((k) => (
                  <CountRow key={k} label={k} value={health.summary[k]} />
                ))}
              </div>
              <Row
                label="Fleet health score"
                value={health.fleet_health_score === null ? null : String(health.fleet_health_score)}
              />
              <p className="mt-2 text-[11px] text-muted-foreground">
                The fleet-wide heartbeat summary. It is context for this bot, not a per-bot verdict — this bot&apos;s
                own row is in the Liveness section.
              </p>
            </div>
          )}
        </Section>

        {/* ---- Org events ---- */}
        <Section title="Organization events" state={bundle?.events ?? { state: "loading" }}>
          {(e) => (
            <div className="space-y-3">
              <p className="text-[11px] text-muted-foreground">
                {e.count !== null
                  ? `${e.count} event${e.count === 1 ? "" : "s"} recorded with this bot as actor.`
                  : "The Gateway sent no event count."}
                {e.complete === false ? " The list below is shorter than the reported count." : ""}
              </p>
              <ListState
                list={e.events}
                noListText="The Gateway sent no event list, so the audit trail is unknown rather than empty."
                emptyText="No organization event names this bot as its actor."
              >
                {(rows) => (
                  <ul className="space-y-1.5">
                    {rows.slice(0, 20).map((ev, i) => (
                      <li key={i} className="rounded-lg border border-border/60 p-2 text-xs">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <VerbatimBadge
                            value={typeof ev.event_type === "string" ? ev.event_type : null}
                            empty="event type not reported"
                          />
                          <span className="font-mono text-[11px] text-muted-foreground">
                            {String(ev.created_at ?? "no timestamp")}
                          </span>
                        </div>
                        <p className="mt-1 text-muted-foreground">
                          {ev.target ? `target: ${String(ev.target)}` : "target not reported"}
                        </p>
                      </li>
                    ))}
                    {rows.length > 20 && (
                      <li className="text-[11px] text-muted-foreground">
                        Showing the first 20 of {rows.length} events.
                      </li>
                    )}
                  </ul>
                )}
              </ListState>
            </div>
          )}
        </Section>
      </div>

      {/* ---- Model configuration (its own read, its own failure state) ---- */}
      <BotModelConfigPanel name={name} botModel={bot?.model ?? null} />

      <p className="flex items-center gap-1.5 pt-2 text-[11px] text-muted-foreground">
        <Building2 className="size-3" />
        Every value above is read from the Gateway. Anything it did not report is shown as &ldquo;not
        reported&rdquo; rather than as a default, and each section fails on its own.
      </p>

      {/* The editor exists exactly once (`chat-shell/BotDetailPanel`, which
          PATCHes through the server's own accepted field list). Editing here
          opens that panel in a modal and re-reads the survey on save, so this
          page shows the server's answer — never the draft that was typed. */}
      {editing && bot && (
        <Modal
          title={`Edit ${botDisplayName(bot)}`}
          subtitle="Saves PATCH this bot's profile on the Gateway; the page re-reads its answer on save."
          onClose={() => setEditing(false)}
          className="max-w-lg bg-card border-l border-border"
        >
          <BotDetailPanel
            bot={bot}
            onSaved={() => {
              void load();
            }}
          />
        </Modal>
      )}
    </div>
  );
}
