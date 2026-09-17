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

export async function listLiveSubagents(): Promise<LiveSubagent[]> {
  try {
    const d = await get<unknown>("/subagents/control");
    return asList(d, ["subagents", "data"]).map((s, i) => ({
      id: String(pick(s, ["id", "subagent_id"], `subagent-${i}`)),
      role: String(pick(s, ["role"], "")),
      objective: String(pick(s, ["objective", "task"], "")),
      status: String(pick(s, ["status", "state"], "unknown")),
      parent: String(pick(s, ["parent_agent_id", "parent"], "")),
    }));
  } catch {
    // Fallback: some deployments expose the registry under /subagents/live
    try {
      const d = await get<unknown>("/subagents/live");
      return asList(d, ["subagents", "data"]).map((s, i) => ({
        id: String(pick(s, ["id", "subagent_id"], `subagent-${i}`)),
        role: String(pick(s, ["role"], "")),
        objective: String(pick(s, ["objective", "task"], "")),
        status: String(pick(s, ["status", "state"], "unknown")),
        parent: String(pick(s, ["parent_agent_id", "parent"], "")),
      }));
    } catch {
      return [];
    }
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
