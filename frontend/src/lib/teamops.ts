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
  } catch {
    return [];
  }
}

export async function createGroup(name: string, members: string[]): Promise<void> {
  await send("/groups", "POST", { name, members });
}

export async function postGroupMessage(name: string, message: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/messages`, "POST", { message });
}

export async function groupMessages(name: string): Promise<Array<Record<string, unknown>>> {
  try {
    const d = await get<Record<string, unknown>>(`/groups/${encodeURIComponent(name)}`);
    return asList(d.messages ?? d, ["messages", "recent_messages", "data"]);
  } catch {
    return [];
  }
}

export async function startGroupRun(name: string, objective: string): Promise<void> {
  await send(`/groups/${encodeURIComponent(name)}/runs`, "POST", { objective });
}

/* ---------- Swarms ---------- */

export interface Swarm {
  id: string;
  objective: string;
  status: string;
  mode?: string;
  maxConcurrency?: number;
  progress?: { total?: number; completed?: number; running?: number; pending?: number; failed?: number; cancelled?: number };
  qualityScore?: number | null;
  terminalReason?: string | null;
  revision?: number;
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
  const d = await get<unknown>("/swarms");
  return asList(d, ["swarms", "data"]).map((s, i) => ({
    id: String(pick(s, ["id", "swarm_id"], `swarm-${i}`)),
    objective: String(pick(s, ["objective", "goal"], "")),
    status: String(pick(s, ["status", "state"], "unknown")),
    mode: String(pick(s, ["mode"], "")),
    maxConcurrency: Number(pick(s, ["max_concurrency"], 0)) || undefined,
    progress: (pick(s, ["progress"], undefined) as Swarm["progress"]) || undefined,
    qualityScore: typeof pick(s, ["quality_score"], null) === "number" ? Number(pick(s, ["quality_score"], 0)) : null,
    terminalReason: (pick(s, ["terminal_reason"], null) as string | null) ?? null,
    revision: Number(pick(s, ["revision"], 0)) || undefined,
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

export async function swarmAction(id: string, action: "pause" | "resume" | "cancel" | "step" | "run_async"): Promise<void> {
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
  } catch {
    return [];
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
  } catch {
    return [];
  }
}

export async function cancelJob(id: string): Promise<void> {
  await send(`/jobs/${encodeURIComponent(id)}/cancel`, "POST", {});
}

/* ---------- Autonomous company ---------- */

export async function companyStatus(): Promise<Record<string, unknown> | null> {
  try {
    return await get<Record<string, unknown>>("/company/status");
  } catch {
    return null;
  }
}

export async function executiveDigest(): Promise<string> {
  try {
    const d = await get<Record<string, unknown>>("/company/executive-digest");
    return String(pick(d, ["digest", "text", "summary"], "No digest available."));
  } catch {
    return "Executive digest is not available right now.";
  }
}

export async function companyKpis(): Promise<Array<Record<string, unknown>>> {
  try {
    const d = await get<unknown>("/company/kpis");
    return asList(d, ["kpis", "data"]);
  } catch {
    return [];
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

export async function killSwitchState(): Promise<{ active: boolean; detail: string }> {
  try {
    const d = await get<Record<string, unknown>>("/bots/kill-switch");
    return { active: Boolean(pick(d, ["active", "engaged"], false)), detail: JSON.stringify(d).slice(0, 300) };
  } catch {
    return { active: false, detail: "Kill-switch status unavailable." };
  }
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
