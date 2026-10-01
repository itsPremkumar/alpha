"use client";

import React, { useEffect, useState } from "react";
import {
  inspectProject,
  inspectionSections,
  InspectorSection,
  ProjectInspection,
  ApprovalRequest,
  AVOLineage,
  CrewView,
  EpistemicClaim,
  ProjectConstitution,
  ProjectContext,
  ProjectRecord,
  WorkspaceCheckpoint,
  HandoffRecord,
  KillSwitch,
  LocksView,
  MetaLineage,
  PerpetualStatus,
  ProjectConversation,
  ProjectDecision,
  ProjectEvent,
  ProjectMemory,
  ProjectStateDigest,
  ResourceLock,
  SelfConfigStatus,
  TrajectoryTrace,
  WarRoomAggregate,
} from "@/lib/project-inspector";
import { errMsg } from "@/lib/http";
import { absoluteStamp, parseTime } from "@/lib/time";
import { Badge, Btn, ErrorBox, SkeletonList } from "@/components/ui";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Brain,
  ClipboardCheck,
  FileText,
  Gavel,
  GitBranch,
  History,
  Lock,
  RefreshCw,
  ScrollText,
  ShieldCheck,
  Users,
  MessagesSquare,
  Coins,
  FlaskConical,
  Repeat,
  Cpu,
  TrendingUp,
} from "lucide-react";

/**
 * The full end-to-end read of one project.
 *
 * The project card answered "what is this project, how many chats and bots does
 * it hold". This answers everything else the Gateway already knows about the
 * same project: its folded lifecycle state, its crew and shared room, its
 * coordination policy, every conversation, its shared memory and role-filtered
 * context, its constitution, its decision log and event feed, its locks,
 * handoffs, approvals and checkpoints, and the twenty-subsystem War Room
 * aggregate plus the three autonomous subsystems.
 *
 * It is deliberately **read-only**. Every one of these subsystems already has a
 * control surface in the Workforce view; duplicating the buttons here would give
 * the operator two ways to mutate one thing, and the Workforce picker defaults to
 * the *first* project — so a copied control would act on a different project than
 * the one on screen. Actions belong to the surface that owns them.
 *
 * Honesty rules this component is built around:
 *  - Each section is independent. One route that fails states its own reason and
 *    the rest still render; the header says up front when the read is partial.
 *  - A count the server did not send is "unknown", never `0`.
 *  - `last_verified: null` is "never verified" — not a green tick.
 *  - The kill switch is tri-state: `true`/`false`/not reported.
 *  - A `null` optional list renders as an explicit "not reported by the server",
 *    which is a different claim from "there are none".
 */

/** A tab in the inspector. Each is one cluster of related reads. */
type InspectorTab =
  | "overview"
  | "crew"
  | "conversations"
  | "memory"
  | "governance"
  | "activity"
  | "operations"
  | "autonomy";

const TABS: Array<{ id: InspectorTab; label: string; icon: React.ReactNode }> = [
  { id: "overview", label: "Overview", icon: <ShieldCheck className="size-3" /> },
  { id: "crew", label: "Crew & room", icon: <Users className="size-3" /> },
  { id: "conversations", label: "Conversations", icon: <MessagesSquare className="size-3" /> },
  { id: "memory", label: "Memory & context", icon: <Brain className="size-3" /> },
  { id: "governance", label: "Governance", icon: <Gavel className="size-3" /> },
  { id: "activity", label: "Activity", icon: <Activity className="size-3" /> },
  { id: "operations", label: "Work & cost", icon: <Coins className="size-3" /> },
  { id: "autonomy", label: "Autonomy", icon: <FlaskConical className="size-3" /> },
];

/**
 * A measured number, or the honest unknown.
 *
 * `null` means the server did not send the field. Rendering `0` there would
 * claim the server measured zero, which is a different measurement entirely.
 */
function Count(props: { label: string; value: number | null; hint?: string }) {
  return (
    <div className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5" title={props.hint}>
      <p className="text-[10px] text-muted-foreground">{props.label}</p>
      <p className={`text-sm font-semibold tabular-nums ${props.value === null ? "text-muted-foreground" : ""}`}>
        {props.value === null ? "—" : props.value}
      </p>
    </div>
  );
}

/** A measured string, or an explicit unknown. */
function Field2(props: { label: string; value: string | null; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] text-muted-foreground">{props.label}</p>
      <p
        className={`text-[11px] break-words ${props.mono ? "font-mono" : ""} ${props.value ? "" : "text-muted-foreground"}`}
      >
        {props.value || "not reported"}
      </p>
    </div>
  );
}

/**
 * A timestamp, or an explicit unknown.
 *
 * An absent value must never reach `new Date("")`, which is the epoch and would
 * claim the event happened in 1970. The three wire shapes (ISO, epoch seconds,
 * epoch millis) all go through `parseTime` so this component never guesses.
 */
function When(props: { value: string | number | null | undefined; prefix?: string }) {
  const ms = parseTime(props.value ?? null);
  if (ms === null) {
    return <span className="text-muted-foreground">{props.prefix ? `${props.prefix} ` : ""}time not reported</span>;
  }
  const stamp = absoluteStamp(props.value ?? null);
  return (
    <time dateTime={new Date(ms).toISOString()} title={stamp ?? undefined}>
      {props.prefix ? `${props.prefix} ` : ""}
      {stamp}
    </time>
  );
}

function Grid(props: { children: React.ReactNode; cols?: number }) {
  return <div className={`grid gap-1.5 ${props.cols === 2 ? "grid-cols-2 sm:grid-cols-3" : "grid-cols-2 sm:grid-cols-4"}`}>{props.children}</div>;
}

function Head(props: { icon: React.ReactNode; title: string; right?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      <p className="text-[11px] font-semibold inline-flex items-center gap-1.5">
        {props.icon}
        {props.title}
      </p>
      {props.right ? <div className="ml-auto flex items-center gap-1.5">{props.right}</div> : null}
    </div>
  );
}

/**
 * A section that failed states the server's reason instead of disappearing.
 *
 * A block that silently vanishes is indistinguishable from a project with no
 * decisions, no locks, or no approvals — so the failure is named and the reason
 * shown in full.
 */
function Failed(props: { label: string; section: InspectorSection<unknown> }) {
  if (props.section.status !== "error") return null;
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-2.5 py-2">
      <p className="text-[11px] font-semibold text-destructive">{props.label} could not be read</p>
      <p className="text-[11px] text-destructive/90 mt-0.5 break-words">{props.section.error}</p>
    </div>
  );
}

function Panel(props: { title: string; icon: React.ReactNode; children: React.ReactNode; note?: string }) {
  return (
    <div className="rounded-xl border border-border/60 bg-card/40 p-3 space-y-2">
      <Head icon={props.icon} title={props.title} />
      {props.note ? <p className="text-[10px] text-muted-foreground">{props.note}</p> : null}
      {props.children}
    </div>
  );
}

/** A list that distinguishes "none" from "not reported". */
function List2(props: { empty: string; count: number | null | undefined; children: React.ReactNode }) {
  if (props.count === null || props.count === undefined) {
    return <p className="text-[11px] text-muted-foreground">The server did not report this list.</p>;
  }
  if (props.count === 0) return <p className="text-[11px] text-muted-foreground">{props.empty}</p>;
  return <>{props.children}</>;
}

// --------------------------------------------------------------------- overview

function StatePanel(props: { section: InspectorSection<ProjectStateDigest> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Project state" section={section} />;
  const s = section.data;
  return (
    <Panel title="Lifecycle state" icon={<ShieldCheck className="size-3.5 text-primary" />} note="Folded from the project's event log by the server. Counts the server did not send stay unknown.">
      <Grid>
        <Count label="active tasks" value={s.active_tasks} />
        <Count label="blocked" value={s.blocked_tasks} />
        <Count label="completed" value={s.completed_tasks} />
        <Count label="failed" value={s.failed_tasks} />
        <Count label="active agents" value={s.active_agents} />
        <Count label="open conflicts" value={s.open_conflicts} />
      </Grid>
      <Grid cols={2}>
        <Field2 label="phase" value={s.phase || null} />
        <Field2 label="architecture version" value={s.arch_version || null} mono />
      </Grid>
      <p className="text-[11px]">
        <span className="text-muted-foreground">Goal: </span>
        {s.goal ? <span className="whitespace-pre-wrap">{s.goal}</span> : <span className="text-muted-foreground">none recorded</span>}
      </p>
      <p className="text-[11px]">
        <span className="text-muted-foreground">Last verified: </span>
        {s.last_verified ? (
          // The server sent a timestamp, so the project has been verified. This
          // panel rendering is NOT what makes that true.
          <span className="text-emerald-600 dark:text-emerald-400">
            <When value={s.last_verified} />
          </span>
        ) : (
          <span className="text-muted-foreground">never verified</span>
        )}
      </p>
      <p className="text-[11px]">
        <span className="text-muted-foreground">Latest decision: </span>
        {s.latest_decision || <span className="text-muted-foreground">none recorded</span>}
      </p>
      {s.updated_at ? (
        <p className="text-[10px] text-muted-foreground">
          <When value={s.updated_at} prefix="State last changed" />
        </p>
      ) : null}
      {s.open_risks.length > 0 && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-2.5 py-2">
          <p className="text-[11px] font-semibold text-amber-700 dark:text-amber-400">Open risks ({s.open_risks.length})</p>
          <ul className="mt-1 space-y-0.5">
            {s.open_risks.map((risk, i) => (
              <li key={i} className="text-[11px] text-amber-700/90 dark:text-amber-400/90">
                {risk || <span className="italic text-muted-foreground">the server sent a risk with no text</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

/** The project record, including the two timestamps the list card never showed. */
function RecordPanel(props: { section: InspectorSection<ProjectRecord> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Project identity" section={section} />;
  const r = section.data;
  const presentationKeys = Object.keys(r.presentation);
  return (
    <Panel title="Identity" icon={<FileText className="size-3.5 text-primary" />}>
      <Grid cols={2}>
        <Field2 label="name" value={r.name || null} />
        <Field2 label="status" value={r.status || null} />
        <Field2 label="project id" value={r.id} mono />
        <Field2 label="created" value={r.created_at || null} />
        <Field2 label="last updated" value={r.updated_at || null} />
      </Grid>
      <div>
        <p className="text-[10px] text-muted-foreground">instructions</p>
        <p className="text-[11px] whitespace-pre-wrap break-words">
          {r.instructions || <span className="text-muted-foreground italic">none set</span>}
        </p>
      </div>
      {presentationKeys.length > 0 && (
        <details className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
          <summary className="cursor-pointer text-[11px] font-medium">
            presentation metadata ({presentationKeys.length} key{presentationKeys.length === 1 ? "" : "s"})
          </summary>
          <pre className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
            {JSON.stringify(r.presentation, null, 2)}
          </pre>
        </details>
      )}
    </Panel>
  );
}

/**
 * The kill switch, rendered as the three claims it actually is.
 *
 * `active: false` is "measured, not engaged". `null` is "the server did not
 * report it" and must never wear the `false` colour — that was the exact defect
 * this pattern exists to prevent.
 */
function KillSwitchPanel(props: { kill: KillSwitch }) {
  const { active } = props.kill;
  const tone = active === true ? "red" : active === false ? "green" : "gray";
  const label = active === true ? "engaged" : active === false ? "not engaged" : "not reported";
  const paused = Object.keys(props.kill.paused_bots);
  return (
    <Panel title="Kill switch" icon={<AlertTriangle className="size-3.5 text-primary" />} note="Global fleet control, as the server reports it for this project.">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone={tone} title={active === null ? "The server did not report the kill-switch state." : undefined}>
          {label}
        </Badge>
        {props.kill.reason ? <span className="text-[11px] text-muted-foreground">{props.kill.reason}</span> : null}
      </div>
      {active === null && (
        <p className="text-[11px] text-muted-foreground">
          The server sent no kill-switch field, so this is unknown — not a healthy &ldquo;no&rdquo;.
        </p>
      )}
      {paused.length > 0 && (
        <p className="text-[11px]">
          <span className="text-muted-foreground">Paused bots: </span>
          {paused.join(", ")}
        </p>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------------- crew

function CrewPanel(props: { section: InspectorSection<CrewView> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Project crew" section={section} />;
  const crew = section.data;
  const room = crew.room;
  const c = crew.collaboration;
  return (
    <>
      <Panel title={`Crew (${crew.members.length})`} icon={<Users className="size-3.5 text-primary" />}>
        {crew.members.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No agents are attached to this project.</p>
        ) : (
          <div className="space-y-1">
            {crew.members.map((m) => (
              <div key={m.bot_name} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[11px] font-semibold">{m.bot_name}</span>
                  <Badge tone={m.status === "active" ? "green" : "gray"}>{m.status || "unknown"}</Badge>
                  <span className="text-[10px] text-muted-foreground">{m.role_in_project || "no role reported"}</span>
                  {m.current_task_id ? (
                    <span className="text-[10px] text-muted-foreground font-mono">task {m.current_task_id}</span>
                  ) : null}
                </div>
                {m.blocked_reason ? (
                  <p className="text-[11px] text-amber-700 dark:text-amber-400 mt-0.5">blocked: {m.blocked_reason}</p>
                ) : null}
                {m.last_activity ? (
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    <When value={m.last_activity} prefix="last activity" />
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel
        title="Group room"
        icon={<MessagesSquare className="size-3.5 text-primary" />}
        note="A room exists only once the crew reaches two agents. Absent, parked and unreadable are three different states."
      >
        {room ? (
          <div className="space-y-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] font-medium">{room.name || "Unnamed room"}</span>
              {room.parked === null ? (
                <Badge tone="gray" title="The server did not report whether this room is parked.">
                  parked: not reported
                </Badge>
              ) : (
                <Badge tone={room.parked ? "amber" : "green"}>{room.parked ? "parked" : "active"}</Badge>
              )}
              {room.mode ? <Badge tone="blue">{room.mode}</Badge> : null}
            </div>
            <Grid cols={2}>
              <Count label="messages" value={room.message_count} />
              <Field2 label="moderator" value={room.moderator} />
            </Grid>
            {room.members.length > 0 && (
              <p className="text-[11px]">
                <span className="text-muted-foreground">Room members: </span>
                {room.members.join(", ")}
              </p>
            )}
            {room.parked === true && (
              <p className="text-[10px] text-amber-600 dark:text-amber-400">
                The room is parked with its history kept. Add an agent to resume it.
              </p>
            )}
          </div>
        ) : (
          <p className="text-[11px] text-muted-foreground">
            {crew.members.length < 2
              ? `No room yet — a shared room opens once this project has 2 agents. It has ${crew.members.length}.`
              : "No room reported by the server."}
          </p>
        )}
      </Panel>

      <Panel title="Coordination policy" icon={<GitBranch className="size-3.5 text-primary" />} note="Read-only here. Edit it in the project card, where the crew form owns it.">
        <Grid cols={2}>
          <Field2 label="orchestration mode" value={c.orchestration_mode} />
          <Field2 label="moderator" value={c.moderator} />
          <Field2 label="mention policy" value={c.mention_policy || null} />
          <Field2 label="conflict policy" value={c.conflict_policy || null} />
          <Field2 label="lock policy" value={c.lock_policy || null} />
          <Field2 label="max concurrent speakers" value={c.max_concurrent_speakers === null ? null : String(c.max_concurrent_speakers)} />
          <Field2 label="auto handoff" value={c.auto_handoff === null ? null : c.auto_handoff ? "on" : "off"} />
          <Field2 label="require evidence" value={c.require_evidence === null ? null : c.require_evidence ? "yes" : "no"} />
          <Field2 label="memory budget (chars)" value={c.memory_budget_chars === null ? null : String(c.memory_budget_chars)} />
          <Field2 label="transcript digest n" value={c.transcript_digest_n === null ? null : String(c.transcript_digest_n)} />
          <Field2 label="standup interval (turns)" value={c.standup_interval_turns === null ? null : String(c.standup_interval_turns)} />
        </Grid>
        {crew.updated_at ? (
          <p className="text-[10px] text-muted-foreground">
            <When value={crew.updated_at} prefix="Crew last reconciled" />
          </p>
        ) : null}
      </Panel>
    </>
  );
}

// ----------------------------------------------------------------- conversations

function ConversationsPanel(props: {
  section: InspectorSection<ProjectConversation[]>;
  onOpenThread: (id: string) => void;
}) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Conversations" section={section} />;
  const list = section.data;
  return (
    <Panel
      title={`Conversations (${list.length})`}
      icon={<MessagesSquare className="size-3.5 text-primary" />}
      note="Every conversation the project's own record holds, across all offset pages. Archived conversations are not returned by this route."
    >
      {list.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No conversations on this project yet.</p>
      ) : (
        <div className="space-y-1">
          {list.map((t) => (
            <div key={t.thread_id} className="flex items-center gap-2 rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
              <button
                type="button"
                onClick={() => props.onOpenThread(t.thread_id)}
                className="text-[11px] font-medium flex-1 text-left truncate hover:text-primary"
                title={t.thread_id}
              >
                {/* An absent display name is a real state; the raw id is shown
                    rather than an invented title. */}
                {t.display_name || <span className="font-mono text-[10px] text-muted-foreground">{t.thread_id}</span>}
              </button>
              <span className="text-[10px] text-muted-foreground shrink-0">
                {t.updated_at ? <When value={t.updated_at} /> : "no date"}
              </span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

// ----------------------------------------------------------------------- memory

function MemoryPanel(props: { section: InspectorSection<ProjectMemory> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Shared memory" section={section} />;
  const m = section.data;
  const digest = m.transcript_digest;
  const briefNames = Object.keys(m.member_briefs);
  const mentionNames = Object.keys(m.pending_mentions);
  return (
    <>
      <Panel title="Level-2 shared memory" icon={<Brain className="size-3.5 text-primary" />} note="What the crew shares, as an agent sees it. Absent values are null, not invented.">
        <Grid cols={2}>
          <Field2 label="goal" value={m.goal} />
          <Field2 label="phase" value={m.phase} />
          <Field2 label="constitution hash" value={m.constitution_hash || null} mono />
          <Field2 label="last updated" value={m.updated_at || null} />
        </Grid>
        <p className="text-[11px]">
          <span className="text-muted-foreground">Active locks: </span>
          {m.active_locks.length === 0 ? <span className="text-muted-foreground">none recorded</span> : m.active_locks.join(", ")}
        </p>
      </Panel>

      <Panel title="Member briefs" icon={<Users className="size-3.5 text-primary" />}>
        {/* A bot with no recorded contribution must be ABSENT here — an empty
            fabricated brief would read as "this agent reported nothing recent",
            which is a different claim from "we have no brief for it". */}
        {briefNames.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No member briefs recorded.</p>
        ) : (
          <div className="space-y-1">
            {briefNames.map((bot) => (
              <div key={bot} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
                <p className="text-[11px] font-semibold">{bot}</p>
                <p className="text-[11px] text-muted-foreground whitespace-pre-wrap">{m.member_briefs[bot]}</p>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="Pending mentions" icon={<ClipboardCheck className="size-3.5 text-primary" />} note="The per-agent work queue: unanswered @mentions.">
        {mentionNames.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No pending mentions recorded.</p>
        ) : (
          <div className="space-y-1.5">
            {mentionNames.map((bot) => (
              <div key={bot}>
                <p className="text-[11px] font-semibold">{bot} ({m.pending_mentions[bot].length})</p>
                <ul className="space-y-0.5">
                  {m.pending_mentions[bot].map((mention, i) => (
                    <li key={i} className="text-[11px] text-muted-foreground break-words">
                      {String(mention.text ?? JSON.stringify(mention))}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="Crew transcript digest" icon={<ScrollText className="size-3.5 text-primary" />}>
        {!digest ? (
          <p className="text-[11px] text-muted-foreground">No transcript digest reported.</p>
        ) : (
          <div className="space-y-1.5">
            <Grid cols={2}>
              <Count label="messages in room" value={digest.total} />
              <Count label="compacted up to" value={digest.compacted_upto} />
            </Grid>
            {digest.summary ? (
              <p className="text-[11px] whitespace-pre-wrap">
                <span className="text-muted-foreground">Earlier overflow: </span>
                {digest.summary}
              </p>
            ) : null}
            {digest.messages.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">The crew has not talked yet.</p>
            ) : (
              <ol className="space-y-1">
                {digest.messages.map((message, i) => (
                  <li key={i} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[11px] font-semibold">{message.from || "unknown"}</span>
                      <Badge tone="blue">{message.intent || "unspecified"}</Badge>
                      {message.at ? (
                        <span className="text-[10px] text-muted-foreground">
                          <When value={message.at} />
                        </span>
                      ) : null}
                    </div>
                    <p className="text-[11px] text-muted-foreground whitespace-pre-wrap mt-0.5">{message.text}</p>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </Panel>

      <Panel title="Open questions" icon={<GitBranch className="size-3.5 text-primary" />}>
        <List2 empty="No open questions recorded." count={m.open_questions.length}>
          <ul className="space-y-1">
            {m.open_questions.map((question, i) => (
              <li key={i} className="text-[11px] text-muted-foreground break-words">
                {String(question.text ?? question.question ?? JSON.stringify(question))}
              </li>
            ))}
          </ul>
        </List2>
      </Panel>

      <Panel title="Recent decisions in memory" icon={<Gavel className="size-3.5 text-primary" />}>
        <List2 empty="No decisions folded into memory." count={m.recent_decisions.length}>
          <ul className="space-y-1">
            {m.recent_decisions.map((row, i) => (
              <li key={i} className="text-[11px] text-muted-foreground break-words">
                {String(row.title ?? JSON.stringify(row))}
              </li>
            ))}
          </ul>
        </List2>
      </Panel>
    </>
  );
}

// ------------------------------------------------------------------- governance

function DecisionsPanel(props: { section: InspectorSection<ProjectDecision[]> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Decisions" section={section} />;
  return (
    <Panel title={`Decision log (${section.data.length})`} icon={<Gavel className="size-3.5 text-primary" />}>
      {section.data.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No decisions recorded yet.</p>
      ) : (
        <div className="space-y-1.5">
          {section.data.map((d) => (
            <details key={d.id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
              <summary className="cursor-pointer text-[11px] font-semibold">
                {d.title || <span className="text-muted-foreground">untitled decision</span>}
              </summary>
              <div className="mt-1 space-y-1">
                <div className="flex items-center gap-1.5 flex-wrap">
                  {d.approved_by ? (
                    <Badge tone="green">approved by {d.approved_by}</Badge>
                  ) : (
                    <Badge tone="gray">not approved</Badge>
                  )}
                  {d.arch_version ? <Badge tone="blue">{d.arch_version}</Badge> : null}
                </div>
                {d.body ? <p className="text-[11px] text-muted-foreground whitespace-pre-wrap">{d.body}</p> : null}
                {d.reason ? <p className="text-[11px] text-muted-foreground">Why: {d.reason}</p> : null}
                <p className="text-[10px] text-muted-foreground">
                  {d.made_by ? `by ${d.made_by} · ` : ""}
                  <When value={d.created_at} />
                </p>
              </div>
            </details>
          ))}
        </div>
      )}
    </Panel>
  );
}

function ConstitutionPanel(props: { section: InspectorSection<ProjectConstitution> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Constitution" section={section} />;
  const c = section.data;
  return (
    <Panel title="Constitution" icon={<ShieldCheck className="size-3.5 text-primary" />}>
      {c.present === true ? (
        <p className="text-[11px] text-emerald-600 dark:text-emerald-400">A constitution is in place for this project.</p>
      ) : c.present === false ? (
        <p className="text-[11px] text-muted-foreground">No constitution yet.</p>
      ) : (
        <p className="text-[11px] text-muted-foreground">The server did not report whether a constitution exists.</p>
      )}
      <Grid cols={2}>
        <Field2 label="sha16" value={c.sha16} mono />
        <Field2 label="updated" value={c.updated_at || null} />
      </Grid>
      {c.markdown ? (
        <details className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
          <summary className="cursor-pointer text-[11px] font-medium">Show the constitution</summary>
          <pre className="mt-1.5 max-h-72 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
            {c.markdown}
          </pre>
        </details>
      ) : null}
      {!c.markdown && c.template ? (
        <details className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
          <summary className="cursor-pointer text-[11px] font-medium">Show the seed template</summary>
          <pre className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
            {c.template}
          </pre>
        </details>
      ) : null}
    </Panel>
  );
}

function ContextPanel(props: { section: InspectorSection<ProjectContext> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Agent context" section={section} />;
  const c = section.data;
  const keys = Object.keys(c.sections);
  return (
    <Panel
      title="Role-filtered context"
      icon={<Brain className="size-3.5 text-primary" />}
      note={`What an agent of role “${c.bot_role || "worker"}” is allowed to see. Fewer sections here is the server's role filter at work, not missing data.`}
    >
      <Field2 label="constitution hash" value={c.constitution_hash} mono />
      {keys.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          This role has no populated context sections. The server filtered them out or nothing is recorded yet.
        </p>
      ) : (
        <div className="space-y-1.5">
          {keys.map((key) => (
            <details key={key} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
              <summary className="cursor-pointer text-[11px] font-semibold">{key}</summary>
              <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
                {JSON.stringify(c.sections[key], null, 2)}
              </pre>
            </details>
          ))}
        </div>
      )}
    </Panel>
  );
}

function ContractsPanel(props: { war: WarRoomAggregate | null }) {
  if (!props.war) return null;
  const contracts = props.war.contracts;
  return (
    <Panel title={`Task contracts (${contracts.length})`} icon={<ClipboardCheck className="size-3.5 text-primary" />} note="Definition-of-done contracts and how many evidence receipts each carries.">
      {contracts.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No task contracts recorded.</p>
      ) : (
        <div className="space-y-1">
          {contracts.map((c) => (
            <div key={c.task_id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[11px] font-semibold">{c.title || c.task_id}</span>
                <Badge tone="blue">{c.status || "unknown"}</Badge>
                {c.evidence_count !== null && c.evidence_count > 0 ? (
                  <Badge tone="green">{c.evidence_count} receipt{c.evidence_count === 1 ? "" : "s"}</Badge>
                ) : (
                  // 0 receipts is not the same claim as "unknown receipts".
                  <Badge tone="amber">no evidence receipts</Badge>
                )}
              </div>
              <p className="text-[10px] text-muted-foreground mt-0.5">
                assignee {c.assignee_bot || "not reported"}
                {c.verifier_bot ? ` · verifier ${c.verifier_bot}` : ""}
              </p>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function LivingSpecPanel(props: { war: WarRoomAggregate | null }) {
  if (!props.war || !props.war.living_spec) return null;
  const spec = props.war.living_spec;
  return (
    <Panel title="Living specification" icon={<FileText className="size-3.5 text-primary" />} note={spec.updated_at ? undefined : "The server did not report when the spec last changed."}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] font-medium">{spec.title || "Untitled specification"}</span>
        <Badge tone="gray">{spec.sections.length} section{spec.sections.length === 1 ? "" : "s"}</Badge>
        {spec.updated_at ? (
          <span className="text-[10px] text-muted-foreground">
            <When value={spec.updated_at} prefix="updated" />
          </span>
        ) : null}
      </div>
      {spec.sections.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No spec sections written yet.</p>
      ) : (
        <div className="space-y-1.5">
          {spec.sections.map((s) => (
            <details key={s.section_key} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
              <summary className="cursor-pointer text-[11px] font-semibold">
                {s.title || s.section_key}
                {s.version !== null ? (
                  <span className="ml-1.5 text-[10px] font-normal text-muted-foreground">v{s.version}</span>
                ) : null}
              </summary>
              <p className="text-[11px] text-muted-foreground whitespace-pre-wrap mt-1">{s.content}</p>
              <p className="text-[10px] text-muted-foreground mt-1">
                {s.last_author_bot ? `author ${s.last_author_bot} · ` : ""}
                {s.updated_at ? <When value={s.updated_at} /> : "time not reported"}
              </p>
            </details>
          ))}
        </div>
      )}
    </Panel>
  );
}

// --------------------------------------------------------------------- activity

function EventsPanel(props: { section: InspectorSection<ProjectEvent[]> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Activity" section={section} />;
  const events = section.data;
  return (
    <Panel
      title={`Event feed (${events.length})`}
      icon={<History className="size-3.5 text-primary" />}
      note="Newest first, from the project's append-only log. An undated row sorts last rather than first."
    >
      {events.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No activity recorded yet.</p>
      ) : (
        <ol className="space-y-1">
          {events.map((e) => (
            <li key={e.id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
                  {e.seq !== null ? `#${e.seq} · ` : ""}
                  <When value={e.created_at} />
                </span>
                <span className="font-medium shrink-0">{e.type || <span className="text-muted-foreground italic">untyped</span>}</span>
                {e.actor ? <span className="text-muted-foreground shrink-0">by {e.actor}</span> : null}
              </div>
              {e.payload ? (
                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
                  {JSON.stringify(e.payload, null, 2)}
                </pre>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------- operations

function LocksPanel(props: { section: InspectorSection<LocksView> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Locks" section={section} />;
  const { locks, pending_requests } = section.data;
  return (
    <>
      <Panel title={`Resource locks (${locks.length})`} icon={<Lock className="size-3.5 text-primary" />} note="Expired locks are swept server-side before this list is built.">
        {locks.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No locks held.</p>
        ) : (
          <div className="space-y-1">
            {locks.map((lock: ResourceLock) => (
              <div key={lock.lock_id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge tone="cyan">{lock.scope || "unknown scope"}</Badge>
                  <span className="text-[11px] font-mono break-all">{lock.path || "no path reported"}</span>
                </div>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  held by {lock.owner_bot || "not reported"}
                  {lock.reason ? ` · ${lock.reason}` : ""}
                </p>
                {lock.expires_at !== null && (
                  <p className="text-[10px] text-muted-foreground">
                    <When value={lock.expires_at} prefix="expires" />
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title={`Lock access requests (${pending_requests.length})`} icon={<Lock className="size-3.5 text-primary" />}>
        {pending_requests.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No access requests.</p>
        ) : (
          <div className="space-y-1">
            {pending_requests.map((r) => (
              <div key={r.request_id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge tone="amber">{r.status || "unknown"}</Badge>
                  <span className="text-[11px]">{r.requester_bot || "requester not reported"}</span>
                  <span className="text-[10px] text-muted-foreground">
                    wants {r.mode || "access"} on {r.scope} {r.path}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}

function HandoffsPanel(props: { section: InspectorSection<HandoffRecord[]> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Handoffs" section={section} />;
  return (
    <Panel title={`Handoffs (${section.data.length})`} icon={<Repeat className="size-3.5 text-primary" />} note="Task handovers between agents, with the context each one carried.">
      {section.data.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No handoffs recorded.</p>
      ) : (
        <div className="space-y-1.5">
          {section.data.map((h) => (
            <details key={h.handoff_id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
              <summary className="cursor-pointer text-[11px] font-semibold flex items-center gap-2 flex-wrap">
                <Badge tone={h.status === "accepted" ? "green" : "gray"}>{h.status || "unknown"}</Badge>
                {h.objective || h.task_id || "untitled handoff"}
              </summary>
              <div className="mt-1 space-y-1 text-[11px]">
                <p className="text-muted-foreground">
                  {h.from_bot || "unknown"} → {h.to_bot || "unknown"} on task {h.task_id || "not reported"}
                </p>
                {h.completed_work ? <p className="whitespace-pre-wrap">{h.completed_work}</p> : null}
                {h.findings ? <p className="whitespace-pre-wrap text-muted-foreground">Findings: {h.findings}</p> : null}
                {h.remaining_work ? <p className="whitespace-pre-wrap text-muted-foreground">Remaining: {h.remaining_work}</p> : null}
                {h.files_modified.length > 0 && (
                  <p className="text-muted-foreground break-words">Files: {h.files_modified.join(", ")}</p>
                )}
                {h.tests.length > 0 && <p className="text-muted-foreground break-words">Tests: {h.tests.join(", ")}</p>}
                {h.known_risks.length > 0 && (
                  <p className="text-amber-700 dark:text-amber-400">Known risks: {h.known_risks.join("; ")}</p>
                )}
                {h.recommended_next_action ? (
                  <p className="text-muted-foreground">Next: {h.recommended_next_action}</p>
                ) : null}
                <p className="text-[10px] text-muted-foreground">
                  <When value={h.created_at} prefix="created" />
                  {h.accepted_at ? (
                    <>
                      {" · "}
                      <When value={h.accepted_at} prefix="accepted" />
                    </>
                  ) : null}
                </p>
              </div>
            </details>
          ))}
        </div>
      )}
    </Panel>
  );
}

function ApprovalsPanel(props: { section: InspectorSection<ApprovalRequest[]> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Approvals" section={section} />;
  return (
    <Panel title={`Approvals (${section.data.length})`} icon={<ClipboardCheck className="size-3.5 text-primary" />} note="Authorization tickets awaiting — or already given — a human decision.">
      {section.data.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No approval requests.</p>
      ) : (
        <div className="space-y-1.5">
          {section.data.map((a) => (
            <details key={a.request_id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
              <summary className="cursor-pointer text-[11px] font-semibold flex items-center gap-2 flex-wrap">
                <Badge
                  tone={
                    a.status === "pending"
                      ? "amber"
                      : a.status === "approved"
                        ? "green"
                        : a.status === "rejected"
                          ? "red"
                          : "gray"
                  }
                >
                  {a.status || "unknown"}
                </Badge>
                <Badge tone={a.risk_level === "critical" || a.risk_level === "high" ? "red" : "blue"}>
                  {a.risk_level || "risk not reported"}
                </Badge>
                {a.action_type || "action not reported"}
              </summary>
              <div className="mt-1 space-y-1 text-[11px]">
                <p className="text-muted-foreground">requested by {a.bot_name || "not reported"}</p>
                {a.diff_preview ? (
                  <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 text-[10px]">
                    {a.diff_preview}
                  </pre>
                ) : null}
                {a.resolution_comment ? <p className="whitespace-pre-wrap">{a.resolution_comment}</p> : null}
                {Object.keys(a.details).length > 0 && (
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
                    {JSON.stringify(a.details, null, 2)}
                  </pre>
                )}
                <p className="text-[10px] text-muted-foreground">
                  <When value={a.created_at} prefix="raised" />
                  {a.resolved_by ? (
                    <>
                      {" · resolved by "}
                      {a.resolved_by}
                    </>
                  ) : null}
                  {a.resolved_at ? (
                    <>
                      {" · "}
                      <When value={a.resolved_at} />
                    </>
                  ) : null}
                </p>
              </div>
            </details>
          ))}
        </div>
      )}
    </Panel>
  );
}

function StandupPanel(props: { war: WarRoomAggregate | null }) {
  if (!props.war || !props.war.standup) return null;
  const standup = props.war.standup;
  return (
    <Panel title="Async standup" icon={<TrendingUp className="size-3.5 text-primary" />} note="The server's own briefing: who is active, what is finished, and what is stuck.">
      {standup.timestamp ? (
        <p className="text-[10px] text-muted-foreground">
          <When value={standup.timestamp} prefix="generated" />
        </p>
      ) : null}
      {standup.executive_summary ? (
        <p className="text-[11px] whitespace-pre-wrap">{standup.executive_summary}</p>
      ) : (
        <p className="text-[11px] text-muted-foreground">No executive summary in this briefing.</p>
      )}
      {standup.active_bots.length > 0 && (
        <p className="text-[11px]">
          <span className="text-muted-foreground">Active bots: </span>
          {standup.active_bots.join(", ")}
        </p>
      )}
      {standup.blockers.length > 0 ? (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-2.5 py-2">
          <p className="text-[11px] font-semibold text-amber-700 dark:text-amber-400">Blockers ({standup.blockers.length})</p>
          <ul className="mt-1 space-y-0.5">
            {standup.blockers.map((blocker, i) => (
              <li key={i} className="text-[11px] text-amber-700/90 dark:text-amber-400/90">
                {blocker}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {standup.stagnant_alerts.length > 0 && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-2.5 py-2">
          <p className="text-[11px] font-semibold text-amber-700 dark:text-amber-400">
            Stagnant tasks ({standup.stagnant_alerts.length})
          </p>
          <ul className="mt-1 space-y-1">
            {standup.stagnant_alerts.map((alert, i) => (
              <li key={i} className="text-[11px] text-amber-700/90 dark:text-amber-400/90">
                {alert.task_id} · {alert.assignee_bot || "assignee not reported"} ·{" "}
                {alert.minutes_inactive === null ? "idle time not reported" : `${alert.minutes_inactive} min idle`}
                {alert.recommendation ? ` — ${alert.recommendation}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

function CostPanel(props: { war: WarRoomAggregate | null }) {
  if (!props.war || !props.war.cost_summary) return null;
  const cost = props.war.cost_summary;
  const bots = Object.keys(cost.bot_breakdown);
  return (
    <Panel title="Cost & token governance" icon={<Coins className="size-3.5 text-primary" />}>
      <Grid>
        <Count label="daily budget (USD)" value={cost.daily_budget_usd} />
        <Count label="spend, last 24h (USD)" value={cost.current_spend_24h} />
        <Count label="budget used (ratio)" value={cost.budget_utilized_ratio} />
        <Count label="bots with spend" value={bots.length} />
      </Grid>
      {bots.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No per-bot spend recorded.</p>
      ) : (
        <div className="space-y-1">
          {bots.map((bot) => {
            const entry = cost.bot_breakdown[bot];
            return (
              <div key={bot} className="flex items-center gap-2 flex-wrap rounded-lg border border-border/60 bg-card px-2.5 py-1.5 text-[11px]">
                <span className="font-semibold">{bot}</span>
                <span className="text-muted-foreground">in {entry.input_tokens === null ? "—" : entry.input_tokens}</span>
                <span className="text-muted-foreground">out {entry.output_tokens === null ? "—" : entry.output_tokens}</span>
                <span className="text-muted-foreground">${entry.cost_usd === null ? "—" : entry.cost_usd.toFixed(4)}</span>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

function CheckpointsPanel(props: { section: InspectorSection<WorkspaceCheckpoint[]> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Checkpoints" section={section} />;
  return (
    <Panel title={`Workspace checkpoints (${section.data.length})`} icon={<History className="size-3.5 text-primary" />} note="Durable snapshots of the workspace. Snapshot arrays are counted here, not inlined.">
      {section.data.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No checkpoints recorded.</p>
      ) : (
        <div className="space-y-1">
          {section.data.map((c) => (
            <div key={c.checkpoint_id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[11px] font-semibold">{c.tag || "untagged"}</span>
                {c.timestamp_iso ? (
                  <span className="text-[10px] text-muted-foreground">
                    <When value={c.timestamp_iso} />
                  </span>
                ) : c.created_at !== null ? (
                  <span className="text-[10px] text-muted-foreground">
                    <When value={c.created_at} />
                  </span>
                ) : (
                  <span className="text-[10px] text-muted-foreground">time not reported</span>
                )}
              </div>
              <p className="text-[10px] text-muted-foreground mt-0.5">
                {c.members === null && c.locks === null && c.contracts === null
                  ? "The server reported no snapshot contents for this checkpoint."
                  : `${c.members ?? "—"} members · ${c.locks ?? "—"} locks · ${c.contracts ?? "—"} contracts · ${c.worktrees ?? "—"} worktrees`}
              </p>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

// --------------------------------------------------------------------- autonomy

function LeaderboardPanel(props: { war: WarRoomAggregate | null }) {
  if (!props.war) return null;
  const rows = props.war.leaderboard;
  return (
    <Panel title={`Benchmark leaderboard (${rows.length})`} icon={<TrendingUp className="size-3.5 text-primary" />} note="Per-bot arena results. A bot with no measured attempts has null rates, never an invented 0%.">
      {rows.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No benchmark results recorded.</p>
      ) : (
        <div className="space-y-1">
          {rows.map((entry) => (
            <div key={entry.bot_name} className="flex items-center gap-2 flex-wrap rounded-lg border border-border/60 bg-card px-2.5 py-1.5 text-[11px]">
              <span className="font-semibold">{entry.bot_name}</span>
              <span className="text-muted-foreground">
                {entry.challenges_passed === null ? "—" : entry.challenges_passed}/
                {entry.challenges_attempted === null ? "—" : entry.challenges_attempted} passed
              </span>
              <span className="text-muted-foreground">
                pass rate {entry.pass_rate === null ? "—" : `${(entry.pass_rate * 100).toFixed(0)}%`}
              </span>
              <span className="text-muted-foreground">
                reputation {entry.reputation_score === null ? "—" : entry.reputation_score}
              </span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function CanaryPanel(props: { war: WarRoomAggregate | null }) {
  if (!props.war) return null;
  const probes = props.war.canary_history;
  const receipts = props.war.visual_qa;
  return (
    <>
      <Panel title={`Canary probes (${probes.length})`} icon={<FlaskConical className="size-3.5 text-primary" />}>
        {probes.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No canary probes recorded.</p>
        ) : (
          <div className="space-y-1">
            {probes.map((probe) => (
              <div key={probe.probe_id} className="flex items-center gap-2 flex-wrap rounded-lg border border-border/60 bg-card px-2.5 py-1.5 text-[11px]">
                <Badge tone={probe.status === "healthy" ? "green" : probe.status === "degraded" ? "amber" : "red"}>
                  {probe.status || "not reported"}
                </Badge>
                <span className="font-mono text-[10px] break-all">{probe.target_url || "no target reported"}</span>
                <span className="text-muted-foreground">
                  http {probe.http_status === null ? "—" : probe.http_status} · {probe.latency_ms === null ? "—" : probe.latency_ms}ms
                </span>
                {probe.tested_at ? (
                  <span className="text-[10px] text-muted-foreground">
                    <When value={probe.tested_at} />
                  </span>
                ) : null}
                {probe.recommendation ? <span className="text-muted-foreground">{probe.recommendation}</span> : null}
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title={`Visual QA receipts (${receipts.length})`} icon={<ClipboardCheck className="size-3.5 text-primary" />}>
        {receipts.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No visual verification receipts.</p>
        ) : (
          <div className="space-y-1">
            {receipts.map((r) => (
              <div key={r.receipt_id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5 text-[11px]">
                <div className="flex items-center gap-2 flex-wrap">
                  {r.passed === null ? (
                    <Badge tone="gray" title="The server did not report a pass or fail verdict.">
                      verdict not reported
                    </Badge>
                  ) : (
                    <Badge tone={r.passed ? "green" : "red"}>{r.passed ? "passed" : "failed"}</Badge>
                  )}
                  <span className="font-mono text-[10px] break-all">{r.url || "no url reported"}</span>
                  <span className="text-muted-foreground">
                    stability {r.visual_stability_score === null ? "—" : r.visual_stability_score}
                  </span>
                </div>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  {r.verified_by ? `verified by ${r.verified_by}` : "verifier not reported"}
                  {r.verified_at ? (
                    <>
                      {" · "}
                      <When value={r.verified_at} />
                    </>
                  ) : null}
                </p>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}

function AVOPanel(props: { lineage: AVOLineage | null }) {
  if (!props.lineage) return null;
  const l = props.lineage;
  return (
    <Panel title="AVO lineage" icon={<GitBranch className="size-3.5 text-primary" />} note="Genetic-optimization version history and the current Pareto frontier.">
      <Grid cols={2}>
        <Field2 label="head version" value={l.head_id} mono />
        <Field2 label="supervisor" value={l.supervisor_status || null} />
        <Count label="versions" value={l.versions.length} />
        <Count label="pareto frontier" value={l.pareto_frontier.length} />
      </Grid>
      {l.versions.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No AVO versions recorded.</p>
      ) : (
        <div className="space-y-1">
          {l.versions.map((v) => (
            <div key={v.version_id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5 text-[11px]">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-mono text-[10px]">{v.version_id}</span>
                {v.correctness === null ? (
                  <Badge tone="gray">correctness not reported</Badge>
                ) : (
                  <Badge tone={v.correctness ? "green" : "red"}>{v.correctness ? "correct" : "incorrect"}</Badge>
                )}
                <span className="text-muted-foreground">composite {v.composite_score === null ? "—" : v.composite_score}</span>
              </div>
              {v.hypothesis ? <p className="text-muted-foreground mt-0.5">Hypothesis: {v.hypothesis}</p> : null}
              {v.modification ? <p className="text-muted-foreground">Change: {v.modification}</p> : null}
              {v.rejection_reason ? <p className="text-amber-700 dark:text-amber-400">Rejected: {v.rejection_reason}</p> : null}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function EpistemicsPanel(props: { claims: EpistemicClaim[] }) {
  return (
    <Panel title={`Epistemic claims (${props.claims.length})`} icon={<Brain className="size-3.5 text-primary" />} note="What the project believes, and what would falsify it. Confidence is the server's posterior.">
      {props.claims.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No claims registered.</p>
      ) : (
        <div className="space-y-1.5">
          {props.claims.map((c) => (
            <details key={c.claim_id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
              <summary className="cursor-pointer text-[11px] font-semibold flex items-center gap-2 flex-wrap">
                <Badge tone="blue">{c.status || "not reported"}</Badge>
                {c.text}
              </summary>
              <div className="mt-1 space-y-1 text-[11px]">
                <p className="text-muted-foreground">
                  confidence {c.confidence === null ? "—" : c.confidence} · prior{" "}
                  {c.bayesian_prior === null ? "—" : c.bayesian_prior} → posterior{" "}
                  {c.bayesian_posterior === null ? "—" : c.bayesian_posterior}
                </p>
                {c.falsification_test ? <p>Falsified by: {c.falsification_test}</p> : null}
                {c.verification_method ? <p className="text-muted-foreground">Verified by: {c.verification_method}</p> : null}
                {c.supporting_evidence.length > 0 && (
                  <p className="text-muted-foreground break-words">Supporting: {c.supporting_evidence.join("; ")}</p>
                )}
                {c.contradicting_evidence.length > 0 && (
                  <p className="text-amber-700 dark:text-amber-400 break-words">
                    Contradicting: {c.contradicting_evidence.join("; ")}
                  </p>
                )}
                {/* `is_verified` absent is unknown, not "unverified". */}
                {c.is_verified === null ? (
                  <p className="text-[10px] text-muted-foreground">The server reported no verified flag.</p>
                ) : (
                  <p className="text-[10px] text-muted-foreground">{c.is_verified ? "marked verified" : "not marked verified"}</p>
                )}
              </div>
            </details>
          ))}
        </div>
      )}
    </Panel>
  );
}

function RSIPanel(props: { war: WarRoomAggregate | null }) {
  if (!props.war || !props.war.rsi_status) return null;
  const rsi = props.war.rsi_status;
  const configs = Object.keys(rsi.active_configurations);
  return (
    <Panel title="Closed-loop RSI" icon={<Repeat className="size-3.5 text-primary" />}>
      <Grid cols={2}>
        <Field2 label="stage" value={rsi.stage || null} />
        <Count label="active configurations" value={configs.length} />
      </Grid>
      {rsi.last_cycle_summary ? (
        <p className="text-[11px] whitespace-pre-wrap">{rsi.last_cycle_summary}</p>
      ) : (
        <p className="text-[11px] text-muted-foreground">No cycle summary reported.</p>
      )}
      {configs.length > 0 && (
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 text-[10px]">
          {JSON.stringify(rsi.active_configurations, null, 2)}
        </pre>
      )}
    </Panel>
  );
}

function TrajectoriesPanel(props: { trajectories: TrajectoryTrace[] }) {
  return (
    <Panel title={`Trajectories (${props.trajectories.length})`} icon={<History className="size-3.5 text-primary" />}>
      {props.trajectories.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No recorded trajectories.</p>
      ) : (
        <div className="space-y-1.5">
          {props.trajectories.map((t) => (
            <details key={t.goal_id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
              <summary className="cursor-pointer text-[11px] font-semibold">
                {t.goal_id} · {t.total_steps === null ? "steps not reported" : `${t.total_steps} steps`}
              </summary>
              {t.created_at ? (
                <p className="text-[10px] text-muted-foreground mt-1">
                  <When value={t.created_at} prefix="recorded" />
                </p>
              ) : null}
              {t.steps.length === 0 ? (
                <p className="text-[11px] text-muted-foreground mt-1">No steps recorded.</p>
              ) : (
                <ol className="mt-1 space-y-1">
                  {t.steps.map((step, i) => (
                    <li key={i} className="rounded border border-border/50 bg-muted/30 px-2 py-1 text-[10px]">
                      <span className="font-medium">{String(step.tool_name || step.thought || `step ${i + 1}`)}</span>
                      {step.status ? <span className="ml-1.5 text-muted-foreground">{String(step.status)}</span> : null}
                    </li>
                  ))}
                </ol>
              )}
            </details>
          ))}
        </div>
      )}
    </Panel>
  );
}

function SelfConfigPanel(props: { section: InspectorSection<SelfConfigStatus> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Self-configuration" section={section} />;
  const c = section.data;
  return (
    <Panel title="Self-configuration" icon={<Cpu className="size-3.5 text-primary" />}>
      <Grid cols={2}>
        <Field2 label="operating mode" value={c.operating_mode || null} />
        <Field2 label="topology" value={c.topology || null} />
        <Field2 label="model tier" value={c.model_tier || null} />
        <Field2 label="primary model" value={c.primary_model || null} />
        <Field2 label="fallback model" value={c.fallback_model || null} />
        <Field2 label="thought depth" value={c.thought_depth || null} />
      </Grid>
      <Grid>
        <Count label="reasoning budget" value={c.reasoning_budget_tokens} />
        <Count label="max turns" value={c.max_turns} />
        <Count label="compaction threshold" value={c.context_compaction_threshold} />
        <Count label="analyses performed" value={c.analyses_performed} />
      </Grid>
      {c.goal ? <p className="text-[11px]"><span className="text-muted-foreground">Goal: </span>{c.goal}</p> : null}
      {c.active_tools.length > 0 && (
        <p className="text-[11px] break-words">
          <span className="text-muted-foreground">Active tools: </span>
          {c.active_tools.join(", ")}
        </p>
      )}
      {c.last_analysis ? (
        <details className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
          <summary className="cursor-pointer text-[11px] font-medium">Last goal analysis</summary>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
            {JSON.stringify(c.last_analysis, null, 2)}
          </pre>
        </details>
      ) : null}
    </Panel>
  );
}

function MetaCompilerPanel(props: { section: InspectorSection<MetaLineage> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Meta-compiler" section={section} />;
  const m = section.data;
  return (
    <Panel title="Agent meta-compiler" icon={<GitBranch className="size-3.5 text-primary" />}>
      <Grid cols={2}>
        <Field2 label="active head" value={m.blueprint_id || null} mono />
        <Field2 label="name" value={m.name || null} />
        <Field2 label="architecture tag" value={m.architecture_tag || null} />
        <Field2 label="specialization" value={m.specialization || null} />
        <Field2 label="reasoning strategy" value={m.reasoning_strategy || null} />
        <Field2 label="memory layout" value={m.memory_layout || null} />
      </Grid>
      <Grid>
        <Count label="generation" value={m.generation} />
        <Count label="total generations" value={m.total_generations} />
        <Count label="blueprints" value={m.blueprints_count} />
        <Count label="pareto frontier" value={m.pareto_size} />
      </Grid>
      {m.composite_score !== null && (
        <p className="text-[11px]">
          <span className="text-muted-foreground">Active scorecard: </span>
          composite {m.composite_score}
          {/* An absent regression flag is unknown, not "passed". */}
          {m.passed_regression_suite === null ? (
            <span className="text-muted-foreground"> · regression suite not reported</span>
          ) : (
            <span className={m.passed_regression_suite ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400"}>
              {m.passed_regression_suite ? " · regression suite passed" : " · regression suite failed"}
            </span>
          )}
        </p>
      )}
      {m.tool_bindings.length > 0 && (
        <p className="text-[11px] break-words">
          <span className="text-muted-foreground">Tool bindings: </span>
          {m.tool_bindings.join(", ")}
        </p>
      )}
      {m.mutation_notes ? <p className="text-[11px] text-muted-foreground">Mutation notes: {m.mutation_notes}</p> : null}
    </Panel>
  );
}

function PerpetualPanel(props: { section: InspectorSection<PerpetualStatus> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Perpetual daemon" section={section} />;
  const p = section.data;
  return (
    <Panel title="Perpetual daemon" icon={<Repeat className="size-3.5 text-primary" />}>
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone={p.state === "running" ? "green" : p.state ? "gray" : "gray"}>
          {p.state || "state not reported"}
        </Badge>
        {p.last_heartbeat_at ? (
          <span className="text-[10px] text-muted-foreground">
            <When value={p.last_heartbeat_at} prefix="last heartbeat" />
          </span>
        ) : null}
      </div>
      <Grid>
        <Count label="heartbeats" value={p.heartbeat_count} />
        <Count label="uptime (s)" value={p.uptime_seconds} />
        <Count label="active goals" value={p.active_goals_count} />
        <Count label="goals tracked" value={p.goals} />
        <Count label="tasks discovered" value={p.total_tasks_discovered} />
        <Count label="tasks completed" value={p.tasks_completed_count} />
        <Count label="stagnation recoveries" value={p.stagnation_incidents_recovered} />
        <Count label="consolidation cycles" value={p.consolidation_cycles_completed} />
      </Grid>
      {p.active_goal ? (
        <details className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
          <summary className="cursor-pointer text-[11px] font-medium">Active goal</summary>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
            {JSON.stringify(p.active_goal, null, 2)}
          </pre>
        </details>
      ) : (
        <p className="text-[11px] text-muted-foreground">No active goal reported.</p>
      )}
      {p.latest_consolidation ? (
        <details className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
          <summary className="cursor-pointer text-[11px] font-medium">Latest memory consolidation</summary>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
            {JSON.stringify(p.latest_consolidation, null, 2)}
          </pre>
        </details>
      ) : null}
    </Panel>
  );
}

// -------------------------------------------------------------------- component

export function ProjectInspectorSection(props: {
  projectId: string;
  /** Opens one of the project's conversations in the chat view. */
  onOpenThread: (id: string) => void;
  /** Closes the inspector and returns to the project list. */
  onClose: () => void;
  /**
   * Open the live control surface for THIS project.
   *
   * The Workforce view owns every mutating per-project action, and its own
   * project picker defaults to the first project — so this must select the
   * project explicitly, not merely switch views.
   */
  onOpenLiveProject?: (projectId: string) => void;
}) {
  const [inspection, setInspection] = useState<ProjectInspection | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<InspectorTab>("overview");
  const [botRole, setBotRole] = useState("worker");
  // Refresh is a repeated action, so it disables itself while a read is in
  // flight — a double-click must not open two reads of the same subsystem.
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    setRefreshing(true);
    setError(null);
    try {
      // inspectProject never rejects, so this only catches a whole-read failure
      // that cannot currently happen — surfaced anyway rather than swallowed.
      setInspection(await inspectProject(props.projectId, botRole));
    } catch (err) {
      setError(errMsg(err));
      setInspection(null);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    setTab("overview");
    void load();
    // Re-reads when the opened project or the inspected role changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.projectId, botRole]);

  // Named explicitly rather than by walking Object.keys: `partial` is a boolean,
  // and a filter that reaches for `.status` on it is one refactor away from
  // quietly reporting every section as failed.
  const failedSections = inspection
    ? inspectionSections(inspection)
        .filter(([, s]) => s.status === "error")
        .map(([label]) => label)
    : [];

  const record = inspection?.record.status === "ok" ? inspection.record.data : null;
  const title = record ? record.name : props.projectId;

  return (
    <div className="workspace-section flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 py-5 w-full">
      <div className="max-w-6xl mx-auto space-y-4">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <h2 className="text-base font-semibold tracking-tight flex items-center gap-2 flex-wrap">
              <button
                type="button"
                onClick={props.onClose}
                className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
              >
                <ArrowLeft className="size-3.5" /> All projects
              </button>
            </h2>
            <p className="text-sm font-semibold mt-1 break-words">{title}</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              Everything the Gateway knows about this one project, read live. Actions live in the project card and
              the Workforce view.
            </p>
            {record ? (
              <p className="text-[10px] text-muted-foreground mt-1 font-mono break-all" title={`Project id: ${record.id}`}>
                {record.id}
              </p>
            ) : null}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {props.onOpenLiveProject && (
              <Btn
                variant="ghost"
                onClick={() => props.onOpenLiveProject?.(props.projectId)}
                title="Open the live per-project control surface with this project selected"
              >
                <Activity className="size-3.5" /> Live controls
              </Btn>
            )}
            <Btn variant="ghost" onClick={() => void load()} disabled={refreshing}>
              <RefreshCw className="size-3.5" /> {refreshing ? "Refreshing…" : "Refresh"}
            </Btn>
          </div>
        </div>

        {error && <ErrorBox message={error} onRetry={() => void load()} />}

        {inspection?.partial && (
          <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 px-3 py-2.5 text-[11px] text-amber-700 dark:text-amber-400">
            Partly unreadable: {failedSections.length} section{failedSections.length === 1 ? "" : "s"} did not answer (
            {failedSections.join(", ")}). Everything else below is the server&rsquo;s own data — a missing section means
            the read failed, not that the project has none.
          </div>
        )}

        <div className="flex items-center gap-2 flex-wrap">
          <div className="inline-flex rounded-xl bg-muted/60 p-1 gap-0.5 flex-wrap">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-semibold transition-colors ${
                  tab === t.id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {t.icon}
                {t.label}
              </button>
            ))}
          </div>
          {tab === "memory" && (
            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <span>as role</span>
              <input
                value={botRole}
                onChange={(event) => setBotRole(event.target.value)}
                maxLength={64}
                className="bg-card border border-border/70 rounded-lg px-2 py-1 text-[11px] w-28"
                aria-label="Bot role for the context projection"
              />
              <span className="text-[10px]">the server filters context sections by role</span>
            </label>
          )}
        </div>

        {loading && !inspection ? (
          <SkeletonList rows={4} />
        ) : !inspection ? null : (
          <div className="space-y-3">
            {tab === "overview" && (
              <>
                <RecordPanel section={inspection.record} />
                <StatePanel section={inspection.state} />
                <KillSwitchPanel kill={inspection.warRoom.status === "ok" ? inspection.warRoom.data.kill_switch : { active: null, reason: "", paused_bots: {} }} />
                {inspection.warRoom.status === "error" ? <Failed label="War Room aggregate" section={inspection.warRoom} /> : null}
              </>
            )}

            {tab === "crew" && <CrewPanel section={inspection.crew} />}

            {tab === "conversations" && (
              <ConversationsPanel section={inspection.conversations} onOpenThread={props.onOpenThread} />
            )}

            {tab === "memory" && (
              <>
                <MemoryPanel section={inspection.memory} />
                <ContextPanel section={inspection.context} />
              </>
            )}

            {tab === "governance" && (
              <>
                <ConstitutionPanel section={inspection.constitution} />
                <DecisionsPanel section={inspection.decisions} />
                <ContractsPanel war={inspection.warRoom.status === "ok" ? inspection.warRoom.data : null} />
                <LivingSpecPanel war={inspection.warRoom.status === "ok" ? inspection.warRoom.data : null} />
              </>
            )}

            {tab === "activity" && <EventsPanel section={inspection.events} />}

            {tab === "operations" && (
              <>
                <StandupPanel war={inspection.warRoom.status === "ok" ? inspection.warRoom.data : null} />
                <CostPanel war={inspection.warRoom.status === "ok" ? inspection.warRoom.data : null} />
                <LocksPanel section={inspection.locks} />
                <HandoffsPanel section={inspection.handoffs} />
                <ApprovalsPanel section={inspection.approvals} />
                <CheckpointsPanel section={inspection.checkpoints} />
                {inspection.warRoom.status === "error" ? <Failed label="War Room aggregate" section={inspection.warRoom} /> : null}
              </>
            )}

            {tab === "autonomy" && (
              <>
                <RSIPanel war={inspection.warRoom.status === "ok" ? inspection.warRoom.data : null} />
                <AVOPanel lineage={inspection.warRoom.status === "ok" ? inspection.warRoom.data.avo_lineage : null} />
                <EpistemicsPanel claims={inspection.warRoom.status === "ok" ? inspection.warRoom.data.epistemic_claims : []} />
                <TrajectoriesPanel trajectories={inspection.warRoom.status === "ok" ? inspection.warRoom.data.trajectories : []} />
                <LeaderboardPanel war={inspection.warRoom.status === "ok" ? inspection.warRoom.data : null} />
                <CanaryPanel war={inspection.warRoom.status === "ok" ? inspection.warRoom.data : null} />
                <SelfConfigPanel section={inspection.selfConfig} />
                <MetaCompilerPanel section={inspection.metaCompiler} />
                <PerpetualPanel section={inspection.perpetual} />
                {inspection.warRoom.status === "error" ? <Failed label="War Room aggregate" section={inspection.warRoom} /> : null}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}