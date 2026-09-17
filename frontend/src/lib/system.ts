import { get } from "./http";
import { fetchConsoleStats } from "./workspace";
import { fetchMemory } from "./memory";
import { listSkills } from "./skills";
import { listScheduledTasks } from "./scheduled";
import { channelStatus } from "./channels";
import { supervisionFleet } from "./supervision";
import { companyStatus } from "./teamops";
import { fetchMcpConfig } from "./mcp";

export interface Probe {
  key: string;
  label: string;
  blurb: string;
  ok: boolean | null; // null = not checked yet
  detail: string;
  ms: number;
}

async function runProbe<T>(key: string, label: string, blurb: string, fn: () => Promise<T>, summarize: (v: T) => string): Promise<Probe> {
  const started = Date.now();
  try {
    const v = await fn();
    return { key, label, blurb, ok: true, detail: summarize(v), ms: Date.now() - started };
  } catch (e) {
    return {
      key,
      label,
      blurb,
      ok: false,
      detail: e instanceof Error ? e.message.slice(0, 160) : "Unavailable",
      ms: Date.now() - started,
    };
  }
}

/** Live-check every subsystem so the UI can activate only what the server offers. */
export async function probeAll(): Promise<Probe[]> {
  // Raw fetch (no swallowed errors): the Gateway probe must fail honestly when offline.
  const feats = await get<Record<string, unknown>>("/features")
    .then((d) => ({
      agentsApi: Boolean((d.agents_api as Record<string, unknown>)?.enabled),
      browserControl: Boolean((d.browser_control as Record<string, unknown>)?.enabled),
      mcpTasks: Boolean((d.mcp_tasks as Record<string, unknown>)?.enabled),
      subagentBatches: Boolean(
        ((d.subagent_batches as Record<string, unknown>) || {}).worker_running ||
          ((d.subagent_batches as Record<string, unknown>) || {}).enabled
      ),
    }))
    .catch(() => null);
  return Promise.all([
    runProbe("gateway", "Gateway", "Core API answering", async () => {
      if (!feats) throw new Error("Server not reachable");
      return true;
    }, () => "online"),
    runProbe("agentsApi", "Custom agents API", "Agent builder endpoints", async () => {
      if (!feats || !feats.agentsApi) throw new Error("Switched off (agents_api.enabled=false)");
      return true;
    }, () => "enabled"),
    runProbe("browser", "Live browser", "Agent drives a real browser tab", async () => {
      if (!feats || !feats.browserControl) throw new Error("No browser capability on server");
      return true;
    }, () => "available"),
    runProbe("database", "History & usage", "SQL-backed runs, tokens, cost", async () => fetchConsoleStats(), (s) => `${s.runs} runs • ${s.threads} chats`),
    runProbe("memory", "Memory", "Facts the agent remembers", async () => fetchMemory(), (m) => `${m.facts.length} facts`),
    runProbe("skills", "Skills", "Toggleable abilities", async () => listSkills(), (s) => `${s.length} skills`),
    runProbe("scheduled", "Scheduler", "Recurring background work", async () => listScheduledTasks(), (t) => `${t.length} schedules`),
    runProbe("channels", "Chat channels", "Telegram / Slack / Discord…", async () => channelStatus(), (c) => (c.length === 0 ? "none linked" : `${c.length} running`)),
    runProbe("mcp", "App connections (MCP)", "External tool servers", async () => fetchMcpConfig(), (s) => (s.length === 0 ? "none added" : `${s.length} servers`)),
    runProbe("watchdog", "Safety watchdog", "Worker health + self-heal", async () => {
      const f = await supervisionFleet();
      if (!f) throw new Error("No watchdog data");
      return f;
    }, () => "watching"),
    runProbe("company", "Autonomous company", "KPIs, board, briefings", async () => {
      const c = await companyStatus();
      if (!c) throw new Error("Company engine idle");
      return c;
    }, () => "active"),
  ]);
}
