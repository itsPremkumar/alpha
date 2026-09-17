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
}

export async function listSwarms(): Promise<Swarm[]> {
  try {
    const d = await get<unknown>("/swarms");
    return asList(d, ["swarms", "data"]).map((s, i) => ({
      id: String(pick(s, ["id", "swarm_id"], `swarm-${i}`)),
      objective: String(pick(s, ["objective", "goal"], "")),
      status: String(pick(s, ["status", "state"], "unknown")),
    }));
  } catch {
    return [];
  }
}

export async function createSwarm(objective: string): Promise<void> {
  await send("/swarms", "POST", { objective });
}

export async function swarmAction(id: string, action: "pause" | "resume" | "cancel" | "step"): Promise<void> {
  await send(`/swarms/${encodeURIComponent(id)}/${action}`, "POST", {});
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
  try {
    const d = await get<unknown>(`/bots/events?limit=${limit}`);
    return asList(d, ["events", "data"]);
  } catch {
    return [];
  }
}
