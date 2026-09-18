import { get, asList, pick } from "./http";

export interface FeatureFlags {
  agentsApi: boolean;
  browserControl: boolean;
  mcpTasks: boolean;
  subagentBatches: boolean;
}

export async function fetchFeatures(): Promise<FeatureFlags> {
  try {
    const d = await get<Record<string, unknown>>("/features");
    return {
      agentsApi: Boolean(pick(d.agents_api as unknown, ["enabled"], false)),
      browserControl: Boolean(pick(d.browser_control as unknown, ["enabled"], false)),
      mcpTasks: Boolean(pick(d.mcp_tasks as unknown, ["enabled"], false)),
      subagentBatches: Boolean(
        pick(d.subagent_batches as unknown, ["worker_running", "enabled"], false)
      ),
    };
  } catch {
    return { agentsApi: false, browserControl: false, mcpTasks: false, subagentBatches: false };
  }
}

export interface ConsoleStats {
  runs: number;
  threads: number;
  agents: number;
  tokens: number;
  cost: number | null;
  currency: string | null;
  raw: Record<string, unknown>;
}

export async function fetchConsoleStats(): Promise<ConsoleStats> {
  const d = await get<Record<string, unknown>>("/console/stats");
  return {
    runs: Number(pick(d, ["runs", "total_runs"], 0)),
    threads: Number(pick(d, ["threads", "total_threads"], 0)),
    agents: Number(pick(d, ["agents", "total_agents"], 0)),
    tokens: Number(pick(d, ["tokens", "total_tokens"], 0)),
    cost: (d.cost as number | null) ?? (d.total_cost as number | null) ?? null,
    currency: (d.currency as string | null) ?? null,
    raw: d,
  };
}

export interface ConsoleRun {
  run_id: string;
  thread_id: string;
  thread_title: string;
  status: string;
  model: string;
  tokens: number;
  cost: number | null;
  created_at: string;
}

export async function fetchConsoleRuns(limit = 30): Promise<ConsoleRun[]> {
  const d = await get<unknown>(`/console/runs?limit=${limit}`);
  return asList(d, ["runs", "data"]).map((r, i) => ({
    run_id: String(pick(r, ["run_id", "id"], `run-${i}`)),
    thread_id: String(pick(r, ["thread_id"], "")),
    thread_title: String(pick(r, ["thread_title", "title"], "Untitled")),
    status: String(pick(r, ["status"], "unknown")),
    model: String(pick(r, ["model", "model_name"], "—")),
    tokens: Number(pick(r, ["tokens", "total_tokens"], 0)),
    cost: (r.cost as number | null) ?? null,
    created_at: String(pick(r, ["created_at"], "")),
  }));
}

export interface UsagePoint {
  day: string;
  tokens: number;
}

export interface UsageBreakdown {
  model: string;
  tokens: number;
  cost: number | null;
}

export async function fetchConsoleUsage(): Promise<{ series: UsagePoint[]; byModel: UsageBreakdown[] }> {
  const d = await get<Record<string, unknown>>("/console/usage");
  const series = asList(d.daily ?? d.series, []).map((p) => ({
    day: String(pick(p, ["day", "date"], "")),
    tokens: Number(pick(p, ["tokens", "total_tokens"], 0)),
  }));
  const byModel = asList(d.by_model ?? d.models ?? d.breakdown, []).map((m) => ({
    model: String(pick(m, ["model", "model_name"], "unknown")),
    tokens: Number(pick(m, ["tokens", "total_tokens"], 0)),
    cost: (m.cost as number | null) ?? null,
  }));
  return { series, byModel };
}

export interface OpsStatus {
  version: string;
  uptime: string;
  serverTime: string;
  docsEnabled: boolean;
  raw: Record<string, unknown>;
}

export async function fetchOpsVersion(): Promise<string> {
  try {
    const d = await get<Record<string, unknown>>("/ops/version");
    return String(pick(d, ["version", "agent_workspace_version"], "unknown"));
  } catch {
    return "unknown";
  }
}

export async function fetchOpsStatus(): Promise<OpsStatus> {
  const d = await get<Record<string, unknown>>("/ops/status");
  return {
    version: "",
    uptime: String(pick(d, ["uptime", "uptime_seconds"], "—")),
    serverTime: String(pick(d, ["server_time", "now"], "")),
    docsEnabled: Boolean(pick(d, ["docs_enabled", "GATEWAY_ENABLE_DOCS"], false)),
    raw: d,
  };
}
