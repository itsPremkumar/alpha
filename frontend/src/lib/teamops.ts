import { get, send, asList, pick } from "./http";

/* ---------- Group rooms ---------- */

export interface GroupRoom {
  name: string;
  members: string[];
  status: string;
}

export async function listGroups(): Promise<GroupRoom[]> {
  try {
    const d = await get<unknown>("/groups");
    return asList(d, ["rooms", "groups", "data"]).map((g) => ({
      name: String(pick(g, ["name"], "")),
      members: Array.isArray(g.members) ? (g.members as string[]) : [],
      status: String(pick(g, ["status"], "")),
    }));
  } catch (err) {
    // Owner-visible read: reject with the server's reason, exactly as
    // listSwarms does. Resolving [] here made TeamOpsSection's `Promise.all`
    // always resolve, so its `catch -> setError` could never fire and a down
    // Gateway rendered as "no rooms".
    throw err instanceof Error ? err : new Error("Failed to list group rooms");
  }
}

export async function createGroup(name: string, members: string[]): Promise<void> {
  await send("/groups", "POST", { name, members });
}

export async function postGroupMessage(name: string, message: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/messages`, "POST", { message });
}

// Rejects on failure. This used to `catch { return [] }`, so a 404/500 (a
// missing or broken room) rendered as "No messages yet — say hello below" —
// the UI asserting the room was quiet when in fact nothing had been read. The
// honest treatment for this same route already exists in comm.ts `getRoom`,
// which does not catch.
export async function groupMessages(name: string): Promise<Array<Record<string, unknown>>> {
  const d = await get<Record<string, unknown>>(`/groups/${encodeURIComponent(name)}`);
  return asList(d.messages ?? d, ["messages", "recent_messages", "data"]);
}

export async function startGroupRun(name: string, objective: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/runs`, "POST", { objective });
}

/* ---------- Swarms ---------- */

/**
 * The server's own task-state projection for one swarm.
 *
 * `SwarmPlan.progress()` (alpha/swarm/models.py:423) always returns all six
 * counters, but the type keeps them optional because an older Gateway, or a
 * plan persisted by one, may omit a key. Absent is therefore `undefined`, never
 * 0 — `swarmProgress()` below is what turns this into a view, and it is the
 * only thing allowed to decide what a missing counter means.
 */
export interface SwarmProgress {
  total?: number;
  completed?: number;
  running?: number;
  pending?: number;
  failed?: number;
  cancelled?: number;
}

export interface Swarm {
  id: string;
  objective: string;
  status: string;
  mode?: string;
  maxConcurrency?: number;
  progress?: SwarmProgress;
  qualityScore?: number | null;
  terminalReason?: string | null;
  revision?: number;
}

/** One counter of a swarm's task projection, with its absence made explicit. */
export interface SwarmProgressView {
  /** The server's number, or `null` when the server did not report it. */
  count: number | null;
  /** What a reader is entitled to conclude, in words. */
  note: string;
}

/**
 * A swarm's task counters, with every absent counter stated rather than shown
 * as a zero.
 *
 * The row used to render one 1.5px bar whose only numbers lived in an
 * `aria-label` reading `"2 of 7 tasks complete"`, built from
 * `completed ?? 0` and `total ?? 0`. Measured, that is:
 *
 *  * a zero-denominator `"0 of 0 tasks complete"` when the counters are absent,
 *    which `ui-legibility.test.mjs` already rules out elsewhere as "not
 *    information";
 *  * a fabricated measured 0% for counters nobody reported;
 *  * and, for the surface whose entire purpose is "several agents working at
 *    the same time", `running` / `pending` / `failed` / `cancelled` rendered
 *    nowhere at all — the concurrency was invisible.
 *
 * So every counter is resolved here, each one carrying its own note, and the
 * section renders all of them.
 */
export function swarmProgress(progress: SwarmProgress | undefined | null): {
  total: SwarmProgressView;
  completed: SwarmProgressView;
  running: SwarmProgressView;
  pending: SwarmProgressView;
  failed: SwarmProgressView;
  cancelled: SwarmProgressView;
  /** Bar width in percent, or `null` when there is nothing to measure. */
  percent: number | null;
  /** True when the server reported enough to say anything at all. */
  measured: boolean;
} {
  const view = (v: unknown): SwarmProgressView =>
    typeof v === "number" && Number.isFinite(v)
      ? { count: v, note: `${v}` }
      : { count: null, note: "not reported" };

  if (!progress) {
    const unknown = { count: null, note: "not reported" } as SwarmProgressView;
    return {
      total: unknown,
      completed: unknown,
      running: unknown,
      pending: unknown,
      failed: unknown,
      cancelled: unknown,
      percent: null,
      measured: false,
    };
  }
  const total = view(progress.total);
  const completed = view(progress.completed);
  const running = view(progress.running);
  const pending = view(progress.pending);
  const failed = view(progress.failed);
  const cancelled = view(progress.cancelled);
  // A ratio is only drawn from two numbers the server actually reported, and
  // never from a zero denominator.
  const percent =
    total.count !== null && total.count > 0 && completed.count !== null
      ? Math.min(100, (completed.count / total.count) * 100)
      : null;
  return {
    total,
    completed,
    running,
    pending,
    failed,
    cancelled,
    percent,
    measured: total.count !== null || completed.count !== null,
  };
}

/** Title for the swarm progress bar: the measured ratio, or why there is none. */
export function swarmProgressLabel(p: ReturnType<typeof swarmProgress>): string {
  if (!p.measured) return "Task progress not reported by the server";
  if (p.total.count === null) return "Task total not reported by the server";
  if (p.total.count === 0) return "No tasks in this swarm yet";
  if (p.completed.count === null) return "Completed count not reported by the server";
  return `${p.completed.count} of ${p.total.count} tasks complete`;
}

export interface SwarmMessage {
  message_id: string;
  sequence: number;
  topic: string;
  sender: string;
  kind: string;
  content: string;
  task_id?: string | null;
  trust: string;
  created_at: number;
}

export async function listSwarms(): Promise<Swarm[]> {
  // No try/catch: a failed read must reject so the caller renders the server's
  // reason. Resolving `[]` here would render "No swarms" for a Gateway that is
  // down — the catch-and-empty this file's own header comment warns about.
  const d = await get<unknown>("/swarms");
  return asList(d, ["swarms", "data"]).map((s) => ({
    // The plan's own id (`swarm_id`, alpha/swarm/models.py:439). It used to
    // default to a positional `swarm-${i}`, so a row that arrived without an
    // id was rendered in a monospace slot as if it were one — a fabricated
    // identifier the operator could not act on and could not match to a route.
    id: String(pick(s, ["id", "swarm_id"], "")),
    objective: String(pick(s, ["objective", "goal"], "")),
    // Verbatim, and never snapped to a known status. A status from a newer
    // Gateway is displayed as itself so an operator can see that the Gateway
    // knows a state this build has no name for.
    status: String(pick(s, ["status", "state"], "")),
    mode: String(pick(s, ["mode"], "")),
    // `max_concurrency` is `ge=1` server-side, so a real 0 cannot occur; a
    // missing value stays absent rather than becoming a measured 0.
    maxConcurrency: typeof s.max_concurrency === "number" ? s.max_concurrency : undefined,
    progress: (s.progress ?? undefined) as Swarm["progress"],
    qualityScore: typeof s.quality_score === "number" ? s.quality_score : null,
    terminalReason: typeof s.terminal_reason === "string" ? s.terminal_reason : null,
    // `|| undefined` used to turn a real revision 0 into "absent". Read the
    // type, not the truthiness.
    revision: typeof s.revision === "number" ? s.revision : undefined,
  }));
}

export async function createSwarm(objective: string, options?: { mode?: string; maxConcurrency?: number; items?: string[] }): Promise<void> {
  await send("/swarms", "POST", {
    goal: objective,
    mode: options?.mode ?? "auto",
    max_concurrency: options?.maxConcurrency ?? 8,
    items: options?.items,
  });
}

/**
 * Lifecycle verbs the Gateway actually mounts under `/api/swarms/{swarm_id}/`.
 *
 * These strings ARE the route segment — they are not a UI vocabulary that gets
 * translated later. The router mounts `run-async` (kebab), so `run_async`
 * addressed a path that does not exist and every "Run" press 404'd at routing.
 * Keeping the union here, next to the client that puts it on the wire, means
 * there is no second spelling to drift.
 *
 * Server: backend/app/gateway/routers/swarms.py
 *   @router.post("/{swarm_id}/pause" | "/resume" | "/cancel" | "/step" | "/run-async")
 */
export const SWARM_ACTIONS = ["pause", "resume", "cancel", "step", "run-async"] as const;

export type SwarmAction = (typeof SWARM_ACTIONS)[number];

export async function swarmAction(id: string, action: SwarmAction): Promise<void> {
  await send(`/swarms/${encodeURIComponent(id)}/${action}`, "POST", {});
}

export async function swarmDetails(id: string): Promise<Record<string, unknown>> {
  return get<Record<string, unknown>>(`/swarms/${encodeURIComponent(id)}`);
}

export async function swarmMetrics(id: string): Promise<Record<string, unknown>> {
  return get<Record<string, unknown>>(`/swarms/${encodeURIComponent(id)}/metrics`);
}

/** Optional blackboard filter. Field names mirror the router's query parameters. */
export interface SwarmMessageQuery {
  topic?: string;
  taskId?: string;
  sinceSequence?: number;
  limit?: number;
}

/**
 * How many messages one read returns when the caller asks for no explicit
 * window. Mirrors `get_swarm_messages`' `limit: int = 50` default, which the
 * router clamps to 1..256; the bus serves the NEWEST `limit` entries.
 */
export const SWARM_MESSAGE_WINDOW = 50;

/** Serialise the blackboard filter. Empty filter → no query string at all. */
function swarmMessageQuery(filter?: SwarmMessageQuery): string {
  const params = new URLSearchParams();
  if (filter?.topic) params.set("topic", filter.topic);
  if (filter?.taskId) params.set("task_id", filter.taskId);
  if (typeof filter?.sinceSequence === "number" && Number.isFinite(filter.sinceSequence)) {
    params.set("since_sequence", String(Math.max(0, Math.trunc(filter.sinceSequence))));
  }
  if (typeof filter?.limit === "number" && Number.isFinite(filter.limit)) {
    params.set("limit", String(Math.max(1, Math.min(256, Math.trunc(filter.limit)))));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

/**
 * Read one swarm's blackboard (GET /api/swarms/{swarm_id}/messages).
 *
 * The route path is a COMPLETE path literal and the filter rides in a separate
 * query string: a query suffix glued onto the template made the path
 * unresolvable for the zero-unwired-features audit, which read the call as
 * `/api/swarms/<id>/messages${query}` and found no route for it. The emitted URL
 * is unchanged; only the two concerns are now separate.
 *
 * Failure propagates — a dead or failing route must reject, never resolve to
 * `[]`, or the UI would render a broken blackboard as an empty one.
 */
export async function swarmMessages(id: string, filter?: string | SwarmMessageQuery): Promise<SwarmMessage[]> {
  const query = swarmMessageQuery(typeof filter === "string" ? { topic: filter } : filter);
  const d = await get<unknown>(`/swarms/${encodeURIComponent(id)}/messages` + query);
  return asList(d, ["messages", "data"]) as unknown as SwarmMessage[];
}

export async function publishSwarmMessage(id: string, message: { topic?: string; content: string; task_id?: string }): Promise<void> {
  await send(`/swarms/${encodeURIComponent(id)}/messages`, "POST", {
    topic: message.topic ?? "general",
    sender: "operator",
    kind: "observation",
    content: message.content,
    task_id: message.task_id,
  });
}

/* ---------- Durable MCP tasks ---------- */

export async function listMcpTasks(threadId: string): Promise<Array<Record<string, unknown>>> {
  try {
    const d = await get<unknown>(`/threads/${encodeURIComponent(threadId)}/mcp-tasks`);
    return asList(d, ["tasks", "data"]);
  } catch (err) {
    throw err instanceof Error ? err : new Error("Failed to list MCP tasks");
  }
}

/* ---------- Background jobs ---------- */

export interface Job {
  id: string;
  kind: string;
  status: string;
}

export async function listJobs(): Promise<Job[]> {
  try {
    const d = await get<unknown>("/jobs");
    return asList(d, ["jobs", "data"]).map((j, i) => ({
      id: String(pick(j, ["id", "job_id"], `job-${i}`)),
      kind: String(pick(j, ["kind", "type"], "")),
      status: String(pick(j, ["status", "state"], "unknown")),
    }));
  } catch (err) {
    throw err instanceof Error ? err : new Error("Failed to list background jobs");
  }
}

export async function cancelJob(id: string): Promise<void> {
  await send(`/jobs/${encodeURIComponent(id)}/cancel`, "POST", {});
}

/* ---------- Autonomous company ---------- */

/**
 * `GET /api/company/status` for the first organization the engine knows about.
 *
 * The route exists and answers 404 in one specific, ordinary case: the server
 * has no organization yet. `backend/app/gateway/routers/company.py` raises
 * `HTTPException(404, "No active organizations found. Bootstrap a company
 * first.")` when `engine.list_companies()` is empty.
 *
 * This used to `catch { return null }`, which destroyed that reason. The caller
 * in `lib/system.ts` then substituted its own wording — "Company engine idle" —
 * and the workspace header rendered that as the state of a subsystem that had
 * in fact never been set up. "Idle" claims an engine exists with nothing to do;
 * the server said no engine has been given anything to run.
 *
 * A failure therefore propagates with the server's `detail` intact, exactly as
 * `listGroups` / `listSwarms` / `listJobs` / `companyKpis` already do in this
 * file. Resolving `null` here is only correct for a genuine 2xx-with-no-body,
 * and callers must not read `null` as "idle".
 */
export async function companyStatus(): Promise<Record<string, unknown> | null> {
  return get<Record<string, unknown>>("/company/status");
}

/**
 * The executive briefing text, or `""` when the server sent none.
 *
 * Rejects on failure. This used to `catch { return "Executive digest is not
 * available right now." }`, which is a catch-and-empty wearing a sentence:
 * `TeamOpsSection` rendered that string inside the briefing card in the same
 * muted prose as a real briefing, so an unreachable Gateway and a genuinely
 * empty digest were indistinguishable — and the server's own `detail` was
 * thrown away. The empty case is now `""`, which the section words, and a
 * failure rejects so the section can show the reason.
 */
export async function executiveDigest(): Promise<string> {
  const d = await get<Record<string, unknown>>("/company/executive-digest");
  const text = pick(d, ["digest", "text", "summary"], "");
  return typeof text === "string" ? text : String(text ?? "");
}

export async function companyKpis(): Promise<Array<Record<string, unknown>>> {
  try {
    const d = await get<unknown>("/company/kpis");
    return asList(d, ["kpis", "data"]);
  } catch (err) {
    throw err instanceof Error ? err : new Error("Failed to read company KPIs");
  }
}

/* ---------- Bot operations (org-level) ---------- */

export async function orgChart(): Promise<Record<string, unknown> | null> {
  try {
    return await get<Record<string, unknown>>("/bots/organization-chart");
  } catch {
    return null;
  }
}

export async function fleetHealth(): Promise<Record<string, unknown> | null> {
  try {
    return await get<Record<string, unknown>>("/bots/health/overview");
  } catch {
    return null;
  }
}

/**
 * Emergency-stop state, tri-state on purpose.
 *
 * The Gateway answers `KillSwitchState` with `global_kill_switch_active` (a live
 * response: `{"global_kill_switch_active":false,"reason":"","engaged_at":null,
 * "paused_bots":{},"paused_count":0}`). This reader looked for `active`/
 * `engaged`, neither of which exists, so `pick` returned its hardcoded `false`
 * and the section painted a GREEN "running" badge and "team working normally"
 * from a constant — it could not have shown an engaged stop under any
 * circumstances, and a 500 painted the same green badge because the `catch`
 * returned the same `false`.
 *
 * `active: null` is the honest answer when the state is unknown, and it is a
 * different claim from `false` ("off"). The section renders null as an explicit
 * "state unknown" rather than as a healthy badge.
 */
export interface KillSwitchState {
  active: boolean | null;
  /** The server's own reason/payload, shown when the read fails or is unknown. */
  detail: string;
  reason: string | null;
  paused_count: number | null;
}

export async function killSwitchState(): Promise<KillSwitchState> {
  // No try/catch: a failed read must reject so the caller renders the server's
  // reason. Resolving `active: false` here would re-create the false all-clear.
  const d = await get<Record<string, unknown>>("/bots/kill-switch");
  return {
    active: typeof d.global_kill_switch_active === "boolean" ? d.global_kill_switch_active : null,
    detail: JSON.stringify(d).slice(0, 300),
    reason: typeof d.reason === "string" && d.reason !== "" ? d.reason : null,
    paused_count: typeof d.paused_count === "number" ? d.paused_count : null,
  };
}

export async function setKillSwitch(active: boolean, reason: string): Promise<void> {
  await send("/bots/kill-switch", "POST", { active, reason });
}

export async function pauseBot(name: string, reason: string): Promise<void> {
  await send(`/bots/${encodeURIComponent(name)}/pause`, "POST", { reason });
}

export async function resumeBot(name: string): Promise<void> {
  await send(`/bots/${encodeURIComponent(name)}/resume`, "POST", {});
}

export async function handoffTask(args: {
  task_id: string;
  from_bot: string;
  to_bot: string;
  objective: string;
}): Promise<void> {
  await send("/bots/handoff", "POST", { ...args, context_summary: "", handoff_notes: "" });
}

export async function matchBots(taskDescription: string, limit = 5): Promise<Array<Record<string, unknown>>> {
  const d = await send<Record<string, unknown>>("/bots/work-discovery/match", "POST", {
    task_description: taskDescription,
    limit,
  });
  return asList(d.matches ?? d, ["matches", "candidates", "data"]);
}

export async function orgEvents(limit = 30): Promise<Array<Record<string, unknown>>> {
  // Failure propagates: an empty feed must not masquerade as "nothing happened".
  const d = await get<unknown>(`/bots/events?limit=${limit}`);
  return asList(d, ["events", "data"]);
}
