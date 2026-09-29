"use client";

import React, { useEffect, useState } from "react";
import {
  listGroups, createGroup, postGroupMessage, groupMessages, startGroupRun, listSwarms, createSwarm,
  swarmAction, swarmMessages, publishSwarmMessage, SWARM_MESSAGE_WINDOW,
  swarmProgress, swarmProgressLabel,
  type Swarm, type SwarmAction, type SwarmMessage, listMcpTasks, listJobs, cancelJob,
  companyStatus, executiveDigest, companyKpis,
} from "@/lib/teamops";
import {
  listKanbanTasks, moveKanbanTask, KANBAN_COLUMNS, UNMAPPED_COLUMN_ID,
  kanbanStatusLabel, nextKanbanStatus, unmappedKanbanTasks,
  KanbanTask, KanbanStatus,
} from "@/lib/kanban";
import { fetchRoster, registerRosterAgent, sendAgentMessage, fetchInbox, RosterAgent, InboxMessage } from "@/lib/inbox";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { absoluteStamp, clockTime, type TimeInput } from "@/lib/time";
import { Plus, Send, Play, RefreshCw, Ban, ChevronLeft, ChevronRight, CircleSlash, User } from "lucide-react";

/**
 * Compact clock stamp for a message row.
 *
 * Returns nothing at all when the row carries no readable stamp: these rows
 * come from three different endpoints, and a row none of them stamped must not
 * inherit the time this page happened to render.
 */
function rowStamp(value: TimeInput) {
  const stamp = clockTime(value);
  if (!stamp) return null;
  return (
    <span className="ml-1.5 text-[10px] font-normal text-muted-foreground tabular-nums" title={absoluteStamp(value) ?? undefined}>
      {stamp}
    </span>
  );
}

type SubTab = "groups" | "inbox" | "swarms" | "jobs" | "company";

/**
 * Button caption per lifecycle verb. The verbs themselves are the Gateway's
 * route segments and are owned by lib/teamops; only the caption is a UI
 * concern, so it is mapped here.
 */
const SWARM_ACTION_LABEL: Record<SwarmAction, string> = {
  "run-async": "Run",
  step: "Step",
  pause: "Pause",
  resume: "Resume",
  cancel: "Cancel",
};

/**
 * The server's own terminal statuses.
 *
 * `SwarmPlan.status` documents `planning, running, paused, aggregating,
 * verifying, completed, failed, cancelled, budget_exhausted, stalled`
 * (alpha/swarm/models.py:405). A status OUTSIDE that list — including the empty
 * string an absent field now maps to — is shown verbatim but is not given the
 * full verb set, because the verbs here are state transitions and guessing a
 * transition for an unrecognised state is how a "Run" appears on a swarm the
 * server considers finished. Only "Cancel" is offered, which is safe from any
 * state.
 */
const SWARM_TERMINAL = ["completed", "failed", "cancelled", "budget_exhausted", "stalled"];
const SWARM_KNOWN = [
  "planning", "running", "paused", "aggregating", "verifying",
  "completed", "failed", "cancelled", "budget_exhausted", "stalled",
];

export function swarmActions(status: string): SwarmAction[] {
  if (SWARM_TERMINAL.includes(status)) return [];
  if (status === "paused") return ["resume", "cancel"];
  // A status this build has no name for: display it, offer only the safe verb.
  if (!SWARM_KNOWN.includes(status)) return ["cancel"];
  return ["run-async", "step", "pause", "cancel"];
}

export function TeamOpsSection(props: { threadId: string | null; mcpTasksAvailable: boolean }) {
  const [tab, setTab] = useState<SubTab>("groups");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [groups, setGroups] = useState<Array<{ name: string; members: string[]; status: string }>>([]);
  const [groupName, setGroupName] = useState("");
  const [groupMembers, setGroupMembers] = useState("");
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [groupDraft, setGroupDraft] = useState("");
  const [groupObjective, setGroupObjective] = useState("");

  const [swarms, setSwarms] = useState<Swarm[]>([]);
  const [swarmObjective, setSwarmObjective] = useState("");
  const [openSwarmBoard, setOpenSwarmBoard] = useState<string | null>(null);
  /**
   * Swarm whose lifecycle request is in flight.
   *
   * `swarmAction` had no guard at all: every verb was `onClick={() => act(…)}`
   * against a plain `Btn`, which disables only on an empty draft it never
   * had. `POST /swarms/{id}/run-async` starts a background runner and
   * `POST /swarms/{id}/step` advances one scheduler tick, so a double-click
   * on "Run" started a second runner and on "Step" advanced the DAG twice —
   * on the one surface whose job is running many agents at once. Guarded per
   * swarm, so a second swarm is still operable.
   */
  const [busySwarm, setBusySwarm] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Array<{ id: string; kind: string; status: string }>>([]);
  const [mcpTasks, setMcpTasks] = useState<Array<Record<string, unknown>>>([]);
  const [digest, setDigest] = useState("");
  const [kpis, setKpis] = useState<Array<Record<string, unknown>>>([]);
  /**
   * One flag per read, so a failure names ITSELF.
   *
   * `load()` used to wrap six independent reads in one `try`, so a
   * `/company/kpis` 500 set a single section-wide error while groups, swarms
   * and jobs still rendered, and the message could not say which read had
   * failed — the operator could not tell a KPI outage from a group-room one.
   * `MessagesSection` already models this with per-list flags
   * (MessagesSection.tsx:44-50); this is the same house pattern.
   */
  const [readErrors, setReadErrors] = useState<Record<string, string | null>>({
    groups: null, swarms: null, jobs: null, mcp: null, digest: null, kpis: null,
  });
  const markRead = (key: string, value: string | null) =>
    setReadErrors((prev) => ({ ...prev, [key]: value }));

  const load = async () => {
    setLoading(true);
    setError(null);
    // Each read below settles independently: one failed fetch flags only that
    // list as unavailable, never a silent empty array that reads as "the server
    // said there is nothing" — and a /jobs 500 no longer discards the group and
    // swarm lists that already resolved. Every branch discloses its own reason.
    //
    // The `catch` is the net for anything that escapes them (a synchronous
    // throw while applying a malformed payload, say), so a failure can never
    // become an unhandled rejection with nothing on screen. It is kept as a
    // bare one-liner because
    // `test_fe_audit_teamops_read_honesty.test.mjs` pins its exact shape.
    try {
      const [g, s, j] = await Promise.allSettled([listGroups(), listSwarms(), listJobs()]);
      if (g.status === "fulfilled") { setGroups(g.value); markRead("groups", null); }
      else { setGroups([]); markRead("groups", errMsg(g.reason)); }
      if (s.status === "fulfilled") { setSwarms(s.value); markRead("swarms", null); }
      else { setSwarms([]); markRead("swarms", errMsg(s.reason)); }
      if (j.status === "fulfilled") { setJobs(j.value); markRead("jobs", null); }
      else { setJobs([]); markRead("jobs", errMsg(j.reason)); }

      if (props.threadId && props.mcpTasksAvailable) {
        try {
          setMcpTasks(await listMcpTasks(props.threadId));
          markRead("mcp", null);
        } catch (e) {
          setMcpTasks([]);
          markRead("mcp", errMsg(e));
        }
      } else {
        setMcpTasks([]);
        markRead("mcp", null);
      }

      const [d, k] = await Promise.allSettled([executiveDigest(), companyKpis()]);
      if (d.status === "fulfilled") { setDigest(d.value); markRead("digest", null); }
      else { setDigest(""); markRead("digest", errMsg(d.reason)); }
      if (k.status === "fulfilled") { setKpis(k.value); markRead("kpis", null); }
      else { setKpis([]); markRead("kpis", errMsg(k.reason)); }
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const flash = (m: string) => {
    setNotice(m);
    window.setTimeout(() => setNotice(null), 4000);
  };

  const act = async (fn: () => Promise<void>, ok?: string) => {
    try {
      await fn();
      if (ok) flash(ok);
      await load();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  /**
   * Per-room message-load state. `null` means "not loaded yet", which is a
   * different claim from `[]` ("the room is quiet"). The client used to swallow
   * a failed read into `[]`, so a 404/500 rendered "No messages yet — say hello
   * below" and this catch could never fire.
   */
  const [groupMsgs, setGroupMsgs] = useState<Record<string, Array<Record<string, unknown>> | null>>({});
  const [groupMsgError, setGroupMsgError] = useState<Record<string, string | null>>({});
  /** Room whose autonomous run is in flight; blocks a duplicate start. */
  const [runningGroup, setRunningGroup] = useState<string | null>(null);

  const autoRun = async (name: string) => {
    if (runningGroup) return;
    const objective = groupObjective.trim();
    if (!objective) return;
    setRunningGroup(name);
    try {
      await startGroupRun(name, objective);
      // Only clear the draft once the server has accepted the run.
      setGroupObjective("");
      flash(`Autonomous run started for ${name}.`);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setRunningGroup(null);
    }
  };

  /**
   * One swarm lifecycle verb, with the control disabled for the round trip.
   *
   * The re-read happens in `finally`, so a failed press still leaves the row in
   * its real state and the control usable again — the busy flag is a UI lock,
   * not a claim that anything succeeded.
   */
  const runSwarmAction = async (id: string, action: SwarmAction) => {
    if (busySwarm) return;
    setBusySwarm(id);
    setError(null);
    try {
      await swarmAction(id, action);
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusySwarm(null);
    }
  };

  const openMessages = async (name: string) => {
    const isOpen = openGroup === name;
    setOpenGroup(isOpen ? null : name);
    if (!isOpen && groupMsgs[name] === undefined) {
      try {
        const ms = await groupMessages(name);
        setGroupMsgs((prev) => ({ ...prev, [name]: ms }));
        setGroupMsgError((prev) => ({ ...prev, [name]: null }));
      } catch (e) {
        // A failed read is NOT an empty room: say so, with the server's reason.
        setGroupMsgError((prev) => ({ ...prev, [name]: errMsg(e) }));
        setGroupMsgs((prev) => ({ ...prev, [name]: null }));
      }
    }
  };

  /**
   * A tab count is a measurement. While its read is in flight, or after it
   * failed, the count is not zero — nothing has been measured. The tab used to
   * read "Group chats (0)" on first paint and again whenever the read failed,
   * which is a claim the server never made.
   */
  const tabCount = (n: number, key: string) =>
    loading ? "…" : readErrors[key] ? "?" : String(n);

  const tabs: Array<{ id: SubTab; label: string }> = [
    { id: "groups", label: `Group chats (${tabCount(groups.length, "groups")})` },
    { id: "inbox", label: "Agent inbox" },
    { id: "swarms", label: `Swarms (${tabCount(swarms.length, "swarms")})` },
    { id: "jobs", label: `Jobs (${tabCount(jobs.length, "jobs")})` },
    { id: "company", label: "Company" },
  ];

  return (
    <Section
      title="Team ops"
      hint="Several agents working together: group chats with turn-taking, swarms for parallel jobs, background jobs, and the autonomous-company briefing."
      actions={
        <Btn variant="ghost" onClick={load}>
          <RefreshCw className="size-3.5" /> Refresh
        </Btn>
      }
    >
      {error && <ErrorBox message={error} onRetry={load} />}
      {/* Each read names itself, so "the Gateway is down" is never the whole
          story and no list silently reports an empty result for a failure. */}
      {readErrors.groups && <ErrorBox message={`Group rooms unavailable — failed to load, not empty. (${readErrors.groups})`} onRetry={load} />}
      {readErrors.swarms && <ErrorBox message={`Swarms unavailable — failed to load, not empty. (${readErrors.swarms})`} onRetry={load} />}
      {readErrors.jobs && <ErrorBox message={`Background jobs unavailable — failed to load, not empty. (${readErrors.jobs})`} onRetry={load} />}
      {readErrors.mcp && <ErrorBox message={`Durable tool tasks unavailable — failed to load, not empty. (${readErrors.mcp})`} onRetry={load} />}
      {readErrors.digest && <ErrorBox message={`Executive briefing unavailable — failed to load. (${readErrors.digest})`} onRetry={load} />}
      {readErrors.kpis && <ErrorBox message={`Key figures unavailable — failed to load, not zero. (${readErrors.kpis})`} onRetry={load} />}
      {notice && <Notice message={notice} />}

      <div className="flex gap-1 flex-wrap rounded-xl bg-muted/60 p-1 w-fit">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold ${tab === t.id ? "bg-card shadow" : "text-muted-foreground hover:text-foreground"}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading ? (
        <SkeletonList rows={4} />
      ) : tab === "groups" ? (
        <div className="space-y-2">
          <div className="rounded-2xl border border-border/60 bg-card p-4">
            <Field label="New group room" hint="Comma-separated member bot names, e.g. researcher, reviewer.">
              <div className="flex flex-col sm:flex-row gap-2">
                <input value={groupName} onChange={(e) => setGroupName(e.target.value)} placeholder="Room name…" className={inputCls} aria-label="Group room name" />
                <input value={groupMembers} onChange={(e) => setGroupMembers(e.target.value)} placeholder="researcher, reviewer" className={inputCls} aria-label="Group members" />
                <Btn onClick={() => groupName.trim() && act(() => createGroup(groupName.trim(), groupMembers.split(",").map((m) => m.trim()).filter(Boolean)).then(() => { setGroupName(""); setGroupMembers(""); }), "Room created.")} disabled={!groupName.trim()}>
                  <Plus className="size-3.5" /> Create
                </Btn>
              </div>
            </Field>
          </div>
          {readErrors.groups ? (
            <ErrorBox message={`Group rooms could not be read — this is a fetch failure, not an empty room list. (${readErrors.groups})`} onRetry={load} />
          ) : groups.length === 0 ? (
            <EmptyState title="No group rooms" hint="Create one above to let several bots discuss with turn-taking." />
          ) : (
            groups.map((g) => {
              // Read once per render so the message list's three states
              // (undefined / null / []) are distinguishable below.
              const loaded = groupMsgs[g.name];
              return (
              <div key={g.name} className="rounded-xl border border-border/60 bg-card">
                <div className="flex items-center gap-2 px-4 py-3 cursor-pointer" onClick={() => openMessages(g.name)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && openMessages(g.name)}>
                  <p className="text-sm font-semibold flex-1">{g.name || "unnamed room — the server sent no name"}</p>
                  {/* "0 members" claims a measured empty roster. A room whose
                      member list the server did not send has an unknown one. */}
                  <span className="text-[11px] text-muted-foreground">
                    {g.members.length === 0 ? "member list not reported" : `${g.members.length} members`}
                  </span>
                  {g.status ? (
                    <Badge tone="gray">{g.status}</Badge>
                  ) : (
                    <span className="text-[10px] text-muted-foreground">status not reported</span>
                  )}
                </div>
                {openGroup === g.name && (
                  <div className="px-4 pb-4 border-t border-border/50 pt-3 space-y-2">
                    <div className="space-y-1.5 max-h-56 overflow-y-auto">
                      {/* Three states, only one of which is an empty room.
                          `loaded` is read into a local so the narrowing is
                          explicit: undefined = not read yet, null = the read
                          produced no list, [] = the room is genuinely quiet. */}
                      {groupMsgError[g.name] ? (
                        <p className="text-[11px] text-destructive">
                          Could not read this room&apos;s messages — {groupMsgError[g.name]}
                        </p>
                      ) : loaded == null ? (
                        <p className="text-[11px] text-muted-foreground">
                          {loaded === undefined
                            ? "Loading messages…"
                            : "Messages unavailable — the Gateway did not return this room's history."}
                        </p>
                      ) : loaded.length === 0 ? (
                        <p className="text-[11px] text-muted-foreground">No messages yet — say hello below.</p>
                      ) : (
                        loaded.slice(-20).map((m, i) => (
                          <div key={i} className="text-[11px] rounded-lg bg-muted/40 px-2.5 py-1.5">
                            <span className="font-semibold">{String(m.author ?? m.bot ?? m.role ?? "bot")}: </span>
                            {String(m.content ?? m.text ?? JSON.stringify(m)).slice(0, 500)}
                            {rowStamp((m.created_at ?? m.timestamp ?? m.at ?? null) as TimeInput)}
                          </div>
                        ))
                      )}
                    </div>
                    <div className="flex gap-2">
                      <input value={groupDraft} onChange={(e) => setGroupDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && groupDraft.trim() && act(() => postGroupMessage(g.name, groupDraft.trim()).then(() => setGroupDraft("")).then(() => groupMessages(g.name)).then((ms) => setGroupMsgs((p) => ({ ...p, [g.name]: ms }))))} placeholder="Message the room…" className={inputCls} aria-label={`Message ${g.name}`} />
                      <Btn onClick={() => groupDraft.trim() && act(() => postGroupMessage(g.name, groupDraft.trim()).then(() => setGroupDraft("")).then(() => groupMessages(g.name)).then((ms) => setGroupMsgs((p) => ({ ...p, [g.name]: ms }))))} disabled={!groupDraft.trim()}>
                        <Send className="size-3.5" aria-hidden="true" />
                        <span className="sr-only">Send message to {g.name}</span>
                      </Btn>
                    </div>
                    <div className="flex gap-2">
                      <input value={groupObjective} onChange={(e) => setGroupObjective(e.target.value)} placeholder="Autonomous goal, e.g. Draft the launch plan…" className={inputCls} aria-label="Autonomous run objective" />
                      {/* Shape 5: POST /groups/{name}/runs mints a NEW run id per
                          request (backend/app/gateway/routers/groups.py:227) and
                          each run fans out to a subagent per member plus a
                          moderator pass. The objective used to be cleared only in
                          the `.then`, so a second click inside the request window
                          started a SECOND autonomous run — duplicate token spend
                          and a second record — while the button only disabled on
                          an EMPTY input. */}
                      <Btn
                        variant="ghost"
                        onClick={() => groupObjective.trim() && autoRun(g.name)}
                        disabled={!groupObjective.trim() || runningGroup === g.name}
                      >
                        <Play className="size-3.5" /> {runningGroup === g.name ? "Starting…" : "Auto-run"}
                      </Btn>
                    </div>
                  </div>
                )}
              </div>
              );
            })
          )}
        </div>
      ) : tab === "swarms" ? (
        <div className="space-y-2">
          <div className="rounded-2xl border border-border/60 bg-card p-4">
            <Field label="Create a swarm plan" hint="Create a bounded DAG, then run it explicitly when you are ready.">
              <div className="flex gap-2">
                <input value={swarmObjective} onChange={(e) => setSwarmObjective(e.target.value)} onKeyDown={(e) => e.key === "Enter" && swarmObjective.trim() && act(() => createSwarm(swarmObjective.trim()).then(() => setSwarmObjective("")), "Swarm plan created. Use Run to start execution.")} placeholder="Objective…" className={inputCls} />
                <Btn onClick={() => swarmObjective.trim() && act(() => createSwarm(swarmObjective.trim()).then(() => setSwarmObjective("")), "Swarm plan created. Use Run to start execution.")} disabled={!swarmObjective.trim()}>
                  <Play className="size-3.5" /> Create plan
                </Btn>
              </div>
            </Field>
          </div>
          {readErrors.swarms ? (
            <ErrorBox message={`Swarms could not be read — this is a fetch failure, not an empty swarm list. (${readErrors.swarms})`} onRetry={load} />
          ) : swarms.length === 0 ? (
            <EmptyState title="No swarms" hint="Create a bounded plan above, then run it when ready." />
          ) : (
            swarms.map((s) => {
              const p = swarmProgress(s.progress);
              // "several agents working at the same time" is the whole point of
              // this surface, so a lifecycle press is disabled for the round
              // trip. `run-async` and `step` are the two that are not
              // idempotent: a second press inside the request window starts a
              // second background runner / advances the DAG a second tick.
              const busy = busySwarm === s.id;
              return (
              <div key={s.id} className="rounded-xl border border-border/60 bg-card px-4 py-2.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-xs font-mono flex-1 min-w-32 break-all" title={s.id || undefined}>
                    {s.id ? s.id : "id not reported by the server"}
                  </p>
                  <Badge tone={s.status === "running" ? "blue" : "gray"}>{s.status || "status not reported"}</Badge>{s.mode && <Badge tone="gray">{s.mode}</Badge>}
                </div>
                {s.objective
                  ? <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">{s.objective}</p>
                  : <p className="text-[11px] text-muted-foreground mt-1">Objective not reported by the server.</p>}
                {/* Every counter the server reported, each naming its own
                    absence, plus the ratio. The bar alone used to carry the
                    only numbers, in an aria-label nobody reads. */}
                <div className="mt-2" aria-label={swarmProgressLabel(p)}>
                  {p.percent !== null && (
                    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                      <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${p.percent}%` }} />
                    </div>
                  )}
                  <p className="text-[10px] text-muted-foreground mt-1">
                    {p.measured
                      ? `${p.completed.note} of ${p.total.note} tasks complete`
                      : "Task progress not reported by the server."}
                    {" · "}running {p.running.note}
                    {" · "}pending {p.pending.note}
                    {p.failed.count !== null && p.failed.count > 0 && <>{" · "}failed {p.failed.note}</>}
                    {p.cancelled.count !== null && p.cancelled.count > 0 && <>{" · "}cancelled {p.cancelled.note}</>}
                  </p>
                </div>
                {s.terminalReason && (
                  <p className="text-[10px] text-muted-foreground mt-1">Stopped because: {s.terminalReason}</p>
                )}
                <div className="flex gap-2 mt-2 flex-wrap">
                  <Btn
                    variant="ghost"
                    onClick={() => setOpenSwarmBoard((cur) => (cur === s.id ? null : s.id))}
                    aria-expanded={openSwarmBoard === s.id}
                  >
                    Blackboard
                  </Btn>
                  {swarmActions(s.status).map((a) => (
                    <Btn
                      key={a}
                      variant="ghost"
                      disabled={busy}
                      title={busy ? `A ${SWARM_ACTION_LABEL[a].toLowerCase()} request for this swarm is still in flight` : undefined}
                      onClick={() => runSwarmAction(s.id, a)}
                    >
                      {busy ? "Working…" : SWARM_ACTION_LABEL[a]}
                    </Btn>
                  ))}
                </div>
                {openSwarmBoard === s.id && <SwarmMessagesPanel swarmId={s.id} />}
              </div>
              );
            })
          )}
        </div>
      ) : tab === "jobs" ? (
        <div className="space-y-2">
          {props.mcpTasksAvailable && (
            <div className="rounded-2xl border border-border/60 bg-card p-4">
              <p className="text-xs font-semibold mb-1.5">
                Long-running tool tasks in this chat ({readErrors.mcp ? "count unavailable" : mcpTasks.length})
              </p>
              {readErrors.mcp ? (
                <p className="text-[11px] text-muted-foreground">
                  This list could not be read, so the number of tasks is unknown
                  — it is not zero. See the banner above for the server&apos;s reason.
                </p>
              ) : mcpTasks.length === 0 ? (
                <p className="text-[11px] text-muted-foreground">None — durable tool work appears here.</p>
              ) : (
                mcpTasks.slice(0, 10).map((t, i) => (
                  <p key={i} className="text-[11px] font-mono rounded-lg bg-muted/40 px-2.5 py-1.5 mb-1 break-all">
                    {String(t.task_id ?? t.id ?? JSON.stringify(t)).slice(0, 200)}
                  </p>
                ))
              )}
            </div>
          )}
          {readErrors.jobs ? (
            <ErrorBox message={`Background jobs could not be read — this is a fetch failure, not an empty list. (${readErrors.jobs})`} onRetry={load} />
          ) : jobs.length === 0 ? (
            <EmptyState title="No background jobs" hint="Jobs are created by the server for long operations." />
          ) : (
            jobs.map((j) => (
              <div key={j.id} className="rounded-xl border border-border/60 bg-card px-4 py-2.5 flex items-center gap-2">
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-mono truncate">{j.id}</p>
                  <p className="text-[11px] text-muted-foreground">{j.kind}</p>
                </div>
                <Badge tone={j.status === "running" ? "blue" : "gray"}>{j.status}</Badge>
                {/* Icon-only with neither `title` nor an accessible name: a
                    screen reader announced "button" for a destructive action.
                    `Btn` forwards `title` but not `aria-label`, so the name is
                    carried as an `sr-only` child — which is what a screen
                    reader actually reads — and the id keeps the rows
                    distinguishable out of context. */}
                <Btn
                  variant="danger"
                  title={`Cancel background job ${j.id}`}
                  onClick={() => window.confirm("Cancel this job?") && act(() => cancelJob(j.id), "Cancelled.")}
                >
                  <Ban className="size-3.5" aria-hidden="true" />
                  <span className="sr-only">Cancel background job {j.id}</span>
                </Btn>
              </div>
            ))
          )}
        </div>
      ) : tab === "inbox" ? (
        <InboxPanel threadId={props.threadId} onError={setError} />
      ) : (
        <div className="space-y-3">
          <KanbanBoard onError={setError} />
          <CompanyDigest digest={digest} />
          <div className="rounded-2xl border border-border/60 bg-card p-4">
            <p className="text-xs font-semibold mb-2">
              Key figures ({readErrors.kpis ? "count unavailable" : kpis.length})
            </p>
            {readErrors.kpis ? (
              <p className="text-[11px] text-muted-foreground">
                The key figures could not be read, so nothing here is a
                measurement. See the banner above for the server&apos;s reason.
              </p>
            ) : kpis.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">The server reported no KPIs. That is an empty answer, not a measured zero.</p>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {kpis.slice(0, 12).map((k, i) => {
                  // `String(k.value ?? k.current ?? "")` rendered an EMPTY slot
                  // in the bold value position for a KPI the server sent
                  // without a value, and invented the name "KPI 3" for one
                  // without a label. Both are fabrications: an unnamed,
                  // unvalued tile is a claim the server never made.
                  const raw = k.value ?? k.current;
                  const value =
                    raw === null || raw === undefined || raw === ""
                      ? "not reported"
                      : String(raw);
                  const name = k.name ?? k.label ?? k.metric;
                  return (
                  <div key={i} className="rounded-xl bg-muted/40 p-2.5">
                    <p className={`text-sm font-bold ${value === "not reported" ? "text-[11px] font-normal text-muted-foreground" : ""}`}>
                      {value}
                    </p>
                    <p className="text-[10px] text-muted-foreground">
                      {name ? String(name) : `unnamed figure ${i + 1} — the server sent no label`}
                    </p>
                  </div>
                  );
                })}
              </div>
            )}
          </div>
          <CompanyStatusBox />
        </div>
      )}
    </Section>
  );
}

/**
 * The swarm blackboard (GET /api/swarms/{swarm_id}/messages) plus a composer
 * (POST …/messages).
 *
 * A FAILED read is its own state. The empty state may only be shown for a read
 * that actually succeeded and returned no messages — otherwise an unreachable
 * or renamed route would render as "this swarm has posted nothing", which is a
 * lie about a feature the operator cannot use.
 */
function SwarmMessagesPanel(props: { swarmId: string }) {
  const [messages, setMessages] = useState<SwarmMessage[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [postError, setPostError] = useState<string | null>(null);
  const [posting, setPosting] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setMessages(await swarmMessages(props.swarmId));
      setLoadError(null);
    } catch (e) {
      setMessages([]);
      setLoadError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.swarmId]);

  const post = async () => {
    const text = draft.trim();
    if (!text) return;
    setPosting(true);
    try {
      await publishSwarmMessage(props.swarmId, { content: text, topic: "general" });
      setDraft("");
      setPostError(null);
      await load();
    } catch (e) {
      setPostError(errMsg(e));
    } finally {
      setPosting(false);
    }
  };

  return (
    <div className="mt-2 pt-2 border-t border-border/50 space-y-2">
      {loadError ? (
        <ErrorBox message={"Blackboard unavailable: " + loadError} onRetry={load} />
      ) : loading ? (
        <SkeletonList rows={2} />
      ) : messages.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No messages on this swarm blackboard yet.</p>
      ) : (
        <>
          <div className="space-y-1.5 max-h-56 overflow-y-auto">
            {messages.map((m) => (
              <div key={m.message_id} className="text-[11px] rounded-lg bg-muted/40 px-2.5 py-1.5">
                <p className="font-semibold">
                  {m.sender}
                  <span className="font-normal text-muted-foreground"> · {m.topic} · #{m.sequence}</span>
                  {rowStamp(m.created_at)}
                </p>
                <p className="whitespace-pre-wrap break-words">{m.content}</p>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-muted-foreground">
            The gateway serves the newest {SWARM_MESSAGE_WINDOW} messages; older entries are not shown.
          </p>
        </>
      )}
      {postError && <ErrorBox message={"Could not post: " + postError} onRetry={post} />}
      <div className="flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && post()}
          placeholder="Post to the blackboard…"
          className={inputCls}
          aria-label={"Post to the blackboard of swarm " + props.swarmId}
        />
        <Btn onClick={post} disabled={posting || !draft.trim()}>
          <Send className="size-3.5" /> Post
        </Btn>
      </div>
    </div>
  );
}

/**
 * The briefing, or an honest statement of its absence.
 *
 * `executiveDigest` used to resolve a fixed sentence on failure, and this card
 * rendered whatever string it got in the same muted prose as a real briefing —
 * so a down Gateway read exactly like a quiet company. The three states are now
 * separate: a real briefing, a read that failed (the section's `readErrors
 * .digest` banner carries the reason), and a read that succeeded with no text.
 */
function CompanyDigest(props: { digest: string }) {
  return (
    <div className="rounded-2xl border border-border/60 bg-card p-4">
      <p className="text-xs font-semibold mb-1.5">Executive briefing</p>
      {props.digest ? (
        <p className="text-xs text-muted-foreground leading-relaxed whitespace-pre-wrap">{props.digest}</p>
      ) : (
        <p className="text-[11px] text-muted-foreground">
          The server returned no briefing text. That is an empty answer, not a
          measured &quot;nothing to report&quot; — see the banner above if the read
          itself failed.
        </p>
      )}
    </div>
  );
}

/**
 * The company's own status document, or the reason the server gave for not
 * having one.
 *
 * This used to be `companyStatus().then(setStatus).catch(() => setStatus(null))`
 * with `if (!status) return null`. That is the catch-and-empty the honesty rules
 * forbid: `GET /api/company/status` answers **404 "No active organizations
 * found. Bootstrap a company first."** whenever no company has been
 * bootstrapped, so the single most common real answer made this box vanish
 * without a trace — a user had no way to learn the feature existed, let alone
 * that it needed setting up.
 *
 * A failure now renders as a visible notice carrying the server's own `detail`.
 */
function CompanyStatusBox() {
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    companyStatus().then(
      (s) => {
        setStatus(s);
        setError(null);
      },
      (e) => {
        setStatus(null);
        setError(errMsg(e));
      },
    );
  }, []);
  if (error) {
    return (
      <div className="rounded-2xl border border-border/60 bg-card p-4">
        <p className="text-xs font-semibold mb-1">Company status</p>
        <p className="text-[11px] text-muted-foreground">
          The Gateway did not report a company. It said: {error}
        </p>
        <p className="text-[11px] text-muted-foreground mt-1">
          This is a server answer, not a failure to read: nothing here is a
          measurement of a running company, and no company has been set up.
        </p>
      </div>
    );
  }
  if (!status) return null;
  return (
    <div className="rounded-2xl border border-border/60 bg-card p-4">
      <p className="text-xs font-semibold mb-2">Company status</p>
      <pre className="text-[11px] font-mono whitespace-pre-wrap max-h-56 overflow-y-auto rounded-xl bg-muted/40 p-3">{JSON.stringify(status, null, 2).slice(0, 3000)}</pre>
    </div>
  );
}

/**
 * The company's own work board, straight from `GET /api/company/kanban/tasks`.
 *
 * Three measured defects lived here and all three are the same shape — the
 * board drew a conclusion the server never sent:
 *
 *  1. **Cards vanished.** Bucketing was `tasks.filter((t) => t.status ===
 *     col.id)` over four columns, but the server's `TaskStatus` enum has SIX
 *     values (`backlog | todo | in_progress | review | done | blocked`,
 *     alpha/company/kanban.py:18). Measured with rows of `backlog`, `todo` and
 *     `in_progress`: the header read **"Work board (3)"** and **one** card was
 *     rendered. A `blocked` card was equally invisible. The count and the
 *     board disagreed and nothing said why.
 *  2. **Both move buttons rewrote the card to "To do."** `order.indexOf(s)`
 *     is `-1` for an unmodelled status, so `order[min(3, max(0, -1 ± 1))]` is
 *     `order[0]` — in BOTH directions — and `disabled` keyed on the raw
 *     status, so the buttons looked live. A `blocked` card therefore
 *     un-blocked itself, discarding the reason, on either click.
 *  3. **The move buttons were `?`.** A bare ASCII question mark was the entire
 *     visible content of both controls (verified byte-wise), and the assignee
 *     line began with a literal `??`.
 *
 * Now: a card whose status no column holds is rendered in one explicitly
 * labelled "other statuses" column rather than dropped, a move the board cannot
 * express is refused and says so, and both controls carry a glyph and a name.
 */
function KanbanBoard(props: { onError: (m: string) => void }) {
  const [tasks, setTasks] = useState<KanbanTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  /** Card id whose move is in flight — a double-click must not double-post. */
  const [busyCard, setBusyCard] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      setTasks(await listKanbanTasks());
      setLoadError(null);
    } catch (e) {
      // Own error state, not only the section's: `listKanbanTasks` rejects, and
      // a section-wide banner does not say which of the four reads failed.
      setLoadError(errMsg(e));
      props.onError(`Work board unavailable — ${errMsg(e)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const move = async (t: KanbanTask, dir: 1 | -1) => {
    if (busyCard) return;
    const next = nextKanbanStatus(t.status, dir);
    if (!next) return; // no column to move into; the buttons are disabled instead
    setBusyCard(t.id);
    try {
      await moveKanbanTask(t.id, next, `Moved by UI from ${t.status}`);
      await load();
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setBusyCard(null);
    }
  };

  if (loading) return <SkeletonList rows={2} />;
  if (loadError) {
    return (
      <div className="rounded-2xl border border-border/60 bg-card p-4">
        <p className="text-xs font-semibold mb-1.5">Work board</p>
        <ErrorBox message={`Work board could not be read — this is a fetch failure, not an empty board. (${loadError})`} onRetry={load} />
      </div>
    );
  }
  if (tasks.length === 0) return null;

  // Cards the four columns cannot hold: `backlog`, `blocked`, and any status a
  // newer Gateway introduces. Rendered, named, and counted — never dropped.
  const other = unmappedKanbanTasks(tasks);
  const inColumns = tasks.length - other.length;
  const columns: Array<{ id: string; label: string; hint: string; items: KanbanTask[] }> = [
    ...KANBAN_COLUMNS.map((c) => ({ ...c, items: tasks.filter((t) => t.status === c.id) })),
    { id: UNMAPPED_COLUMN_ID, label: "Other statuses", hint: "Not shown in the columns above", items: other },
  ].filter((c) => c.items.length > 0 || c.id !== UNMAPPED_COLUMN_ID);

  return (
    <div className="rounded-2xl border border-border/60 bg-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <p className="text-xs font-semibold flex-1">
          Work board ({tasks.length})
          {/* The count and the board used to disagree with no explanation. */}
          {other.length > 0 && (
            <span className="font-normal text-muted-foreground">
              {" "}— {inColumns} in the columns below, {other.length} in other statuses
            </span>
          )}
        </p>
        <Btn variant="ghost" onClick={load}>
          <RefreshCw className="size-3.5" aria-hidden="true" /> Refresh
        </Btn>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
        {columns.map((col) => (
            <div key={col.id} className="rounded-xl bg-muted/40 p-2 space-y-1.5">
              <p className="text-[11px] font-bold px-1">{col.label} ({col.items.length})</p>
              <p className="text-[10px] text-muted-foreground px-1 -mt-1">{col.hint}</p>
              {col.items.slice(0, 12).map((t) => {
                const back = nextKanbanStatus(t.status, -1);
                const fwd = nextKanbanStatus(t.status, 1);
                const busy = busyCard === t.id;
                const label = t.title || t.id || "(untitled card)";
                return (
                <div key={t.id} className="rounded-lg bg-card border border-border/60 p-2">
                  <p className="text-[11px] font-medium leading-snug">{label}</p>
                  {/* The server's stage, named — including when this build has
                      no column for it, which is why it is in this column. */}
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    stage: {t.status ? (kanbanStatusLabel(t.status) ?? `unrecognised ("${t.status}")`) : "not reported by the server"}
                  </p>
                  {t.assignee && (
                    <p className="text-[10px] text-muted-foreground mt-0.5 inline-flex items-center gap-1">
                      <User className="size-3" aria-hidden="true" /> {t.assignee}
                    </p>
                  )}
                  {t.updatedAt && (
                    <p className="text-[10px] text-muted-foreground mt-0.5">
                      server updated {clockTime(t.updatedAt * 1000) ?? "at an unreadable time"}
                    </p>
                  )}
                  <div className="flex gap-1 mt-1.5">
                    <button
                      type="button"
                      onClick={() => move(t, -1)}
                      disabled={!back || busy}
                      className="p-0.5 rounded bg-muted hover:bg-muted/70 disabled:opacity-30"
                      title={back ? `Move "${label}" back to ${kanbanStatusLabel(back) ?? back}` : "This card is in the first column the board shows"}
                      aria-label={back ? `Move ${label} back to ${kanbanStatusLabel(back) ?? back}` : `Move ${label} back — not available`}
                    >
                      <ChevronLeft className="size-3.5" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={() => move(t, 1)}
                      disabled={!fwd || busy}
                      className="p-0.5 rounded bg-muted hover:bg-muted/70 disabled:opacity-30"
                      title={fwd ? `Move "${label}" forward to ${kanbanStatusLabel(fwd) ?? fwd}` : "This card is in the last column the board shows, or its stage is not one the board can move"}
                      aria-label={fwd ? `Move ${label} forward to ${kanbanStatusLabel(fwd) ?? fwd}` : `Move ${label} forward — not available`}
                    >
                      <ChevronRight className="size-3.5" aria-hidden="true" />
                    </button>
                    {busy && (
                      <span className="text-[10px] text-muted-foreground inline-flex items-center gap-1">
                        <CircleSlash className="size-3" aria-hidden="true" /> saving…
                      </span>
                    )}
                  </div>
                </div>
                );
              })}
              {col.items.length === 0 && <p className="text-[10px] text-muted-foreground px-1 py-2">No cards in this column</p>}
            </div>
        ))}
      </div>
    </div>
  );
}

function InboxPanel(props: { threadId: string | null; onError: (m: string) => void }) {
  const [roster, setRoster] = useState<RosterAgent[]>([]);
  const [who, setWho] = useState("");
  const [msgs, setMsgs] = useState<InboxMessage[]>([]);
  const [showRegister, setShowRegister] = useState(false);
  const [regName, setRegName] = useState("");
  const [sendTo, setSendTo] = useState("");
  const [sendText, setSendText] = useState("");

  const loadRoster = async () => {
    if (!props.threadId) return;
    try {
      const r = await fetchRoster(props.threadId);
      setRoster(r);
      if (!who && r.length > 0) setWho(r[0].name);
    } catch (e) {
      props.onError(errMsg(e));
    }
  };

  const loadInbox = async (agent: string) => {
    if (!props.threadId || !agent) return;
    try {
      setMsgs(await fetchInbox(props.threadId, agent));
    } catch (e) {
      props.onError(errMsg(e));
    }
  };

  useEffect(() => {
    setRoster([]);
    setMsgs([]);
    setWho("");
    loadRoster();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.threadId]);

  useEffect(() => {
    if (who) loadInbox(who);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [who]);

  if (!props.threadId) {
    return <EmptyState title="Pick a chat first" hint="The agent inbox lives inside a conversation." />;
  }

  return (
    <div className="space-y-2">
      <div className="rounded-2xl border border-border/60 bg-card p-4">
        <div className="flex items-center gap-2 mb-2">
          <p className="text-xs font-semibold flex-1">Who's in this chat ({roster.length})</p>
          <Btn variant="ghost" onClick={loadRoster}>
            <RefreshCw className="size-3.5" /> Refresh
          </Btn>
          <Btn variant="ghost" onClick={() => setShowRegister((v) => !v)}>
            <Plus className="size-3.5" /> Add agent
          </Btn>
        </div>
        {showRegister && (
          <div className="flex gap-2 mb-2">
            <input value={regName} onChange={(e) => setRegName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && regName.trim() && props.threadId && registerRosterAgent(props.threadId, regName.trim()).then(() => { setRegName(""); setShowRegister(false); loadRoster(); }).catch((e) => props.onError(errMsg(e)))} placeholder="Agent name, e.g. researcher…" className={inputCls} aria-label="Agent name" />
            <Btn onClick={() => regName.trim() && props.threadId && registerRosterAgent(props.threadId, regName.trim()).then(() => { setRegName(""); setShowRegister(false); loadRoster(); }).catch((e) => props.onError(errMsg(e)))}>Add</Btn>
          </div>
        )}
        {roster.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">Nobody registered yet — add the agents working here.</p>
        ) : (
          <div className="flex gap-1.5 flex-wrap">
            {roster.map((a) => (
              <button key={a.name} type="button" onClick={() => setWho(a.name)} className={`text-[11px] px-2.5 py-1.5 rounded-lg border font-medium ${who === a.name ? "border-primary bg-primary/10 text-primary" : "border-border/70 text-muted-foreground hover:text-foreground"}`}>
                {a.name} — {a.status}
              </button>
            ))}
          </div>
        )}
      </div>

      {who && (
        <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-2">
          <p className="text-xs font-semibold">Inbox: {who} ({msgs.length})</p>
          <div className="space-y-1.5 max-h-56 overflow-y-auto">
            {msgs.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">No messages — write one below.</p>
            ) : (
              msgs.slice(-20).map((m) => (
                <div key={m.id} className="text-[11px] rounded-lg bg-muted/40 px-2.5 py-1.5">
                  <span className="font-semibold">{m.from || "?"} → {m.to || "all"}: </span>
                  {m.content.slice(0, 500)}
                  {rowStamp(m.createdAt)}
                </div>
              ))
            )}
          </div>
          <div className="flex flex-col sm:flex-row gap-2">
            <select value={sendTo} onChange={(e) => setSendTo(e.target.value)} className={`${inputCls} sm:max-w-40`} aria-label="Send to">
              <option value="">Everyone</option>
              {roster.filter((a) => a.name !== who).map((a) => (
                <option key={a.name} value={a.name}>{a.name}</option>
              ))}
            </select>
            <input value={sendText} onChange={(e) => setSendText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && sendText.trim() && props.threadId && sendAgentMessage(props.threadId, who, sendTo || "all", sendText.trim()).then(() => { setSendText(""); loadInbox(who); }).catch((er) => props.onError(errMsg(er)))} placeholder={`Message as ${who}…`} className={inputCls} aria-label="Agent message" />
            <Btn onClick={() => sendText.trim() && props.threadId && sendAgentMessage(props.threadId, who, sendTo || "all", sendText.trim()).then(() => { setSendText(""); loadInbox(who); }).catch((er) => props.onError(errMsg(er)))}>
              <Send className="size-3.5" /> Send
            </Btn>
          </div>
        </div>
      )}
    </div>
  );
}
