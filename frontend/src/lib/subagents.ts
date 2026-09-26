import { get, send, asList, pick } from "./http";

export interface SubagentDef {
  name: string;
  description: string;
  model: string;
  enabled: boolean;
  source: string;
  editable: boolean;
}

export async function listSubagentCatalog(): Promise<SubagentDef[]> {
  const d = await get<unknown>("/subagents");
  return asList(d, ["subagents", "data"]).map((s) => ({
    name: String(pick(s, ["name"], "")),
    description: String(pick(s, ["description"], "")),
    model: String(pick(s, ["model"], "inherit")),
    enabled: Boolean(pick(s, ["enabled"], true)),
    source: String(pick(s, ["source"], "")),
    editable: Boolean(pick(s, ["editable"], false)),
  }));
}

export interface LiveSubagent {
  id: string;
  role: string;
  objective: string;
  status: string;
  parent: string;
}

function toLiveSubagent(s: Record<string, unknown>, i: number): LiveSubagent {
  return {
    id: String(pick(s, ["id", "subagent_id"], `subagent-${i}`)),
    role: String(pick(s, ["role"], "")),
    objective: String(pick(s, ["objective", "task"], "")),
    status: String(pick(s, ["status", "state"], "unknown")),
    parent: String(pick(s, ["parent_agent_id", "parent"], "")),
  };
}

/**
 * Parse `GET /api/subagents/control` strictly: accepts a bare array or the
 * `{subagents|data: [...]}` envelope, and throws on anything else so callers
 * can distinguish "request failed" from "there are genuinely no subagents".
 */
export function parseLiveSubagents(body: unknown): LiveSubagent[] {
  let list: unknown = body;
  if (!Array.isArray(list)) {
    if (!list || typeof list !== "object") {
      throw new Error("The server returned an unreadable subagent list.");
    }
    const rec = list as Record<string, unknown>;
    const nested = [rec.subagents, rec.data].find((v) => Array.isArray(v));
    if (!nested) {
      throw new Error("The server returned an unreadable subagent list.");
    }
    list = nested;
  }
  return (list as unknown[]).map((s, i) =>
    s && typeof s === "object" ? toLiveSubagent(s as Record<string, unknown>, i) : toLiveSubagent({}, i),
  );
}

/** Strict variant for status surfaces: throws on failure instead of reporting an empty fleet. */
export async function fetchLiveSubagentsStrict(): Promise<LiveSubagent[]> {
  return parseLiveSubagents(await get<unknown>("/subagents/control"));
}

export async function listLiveSubagents(): Promise<LiveSubagent[]> {
  try {
    return await fetchLiveSubagentsStrict();
  } catch {
    // Exact route: GET /api/subagents/control (subagent_control.py @router.get("")). A former
    // /subagents/live fallback was unwired on this gateway — the only match would be
    // GET /api/subagents/{name}, a single-bot lookup that cannot serve a registry — so an
    // unreachable gateway is the only remaining failure and honestly yields [].
    return [];
  }
}

export async function spawnSubagent(objective: string, role = "general-purpose"): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>("/subagents/control/spawn", "POST", {
    objective,
    role,
    parent_agent_id: "ui",
  });
}

export async function cancelSubagent(id: string, reason = "Cancelled from UI"): Promise<void> {
  await send(`/subagents/control/${encodeURIComponent(id)}/cancel`, "POST", { reason });
}

export async function subagentResult(id: string): Promise<Record<string, unknown> | null> {
  try {
    return await get<Record<string, unknown>>(`/subagents/control/${encodeURIComponent(id)}/result`);
  } catch {
    return null;
  }
}
