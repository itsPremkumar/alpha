import { get } from "./http";
import { fetchConsoleStats } from "./workspace";
import { fetchMemory } from "./memory";
import { listSkills } from "./skills";
import { listScheduledTasks } from "./scheduled";
import { channelStatus } from "./channels";
import { fetchFleetWorkers, watchdogDetail } from "./supervision";
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

/**
 * Why a probe could not read its subsystem, when the answer is not just "no".
 *
 * `GET /api/company/status` answers **404 "No active organizations found.
 * Bootstrap a company first."** when no company has been bootstrapped. That is
 * an ordinary, actionable answer from a route that does exist — not a broken
 * route, and not an engine sitting idle with nothing to do.
 *
 * The probe used to swallow the `ApiClientError` and substitute its own
 * sentence, `"Company engine idle"`. That string is dishonest twice over: it
 * discards the reason the server actually gave, and it asserts a state
 * ("an engine exists and is idle") that the server explicitly contradicted. It
 * rendered in the workspace header as a grey dot and in the System control
 * center as a capability line, so the lie was on screen in two places.
 *
 * The server's own `detail` is now what reaches the UI, verbatim.
 */
export function probeReason(e: unknown): string {
  const message = e instanceof Error ? e.message.trim() : "";
  if (message) return message.slice(0, 200);
  return "The Gateway gave no reason.";
}

async function runProbe<T>(key: string, label: string, blurb: string, fn: () => Promise<T>, summarize: (v: T) => string): Promise<Probe> {
  const started = Date.now();
  try {
    const v = await fn();
    return { key, label, blurb, ok: true, detail: summarize(v), ms: Date.now() - started };
  } catch (e) {
    // The reason is the server's, not a UI adjective. `probeReason` keeps the
    // ApiClientError's `detail` — which is where a 404 body like "No active
    // organizations found. Bootstrap a company first." actually lives — instead
    // of replacing it with a word like "Unavailable" that names no cause.
    return {
      key,
      label,
      blurb,
      ok: false,
      detail: probeReason(e),
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
    runProbe("memory", "Memory", "Facts the agent remembers", async () => fetchMemory(), (m) => `${m.facts.length} ${m.facts.length === 1 ? "fact" : "facts"}`),
    runProbe("skills", "Skills", "Toggleable abilities", async () => listSkills(), (s) => `${s.length} ${s.length === 1 ? "skill" : "skills"}`),
    runProbe("scheduled", "Scheduler", "Recurring background work", async () => listScheduledTasks(), (t) => `${t.length} ${t.length === 1 ? "schedule" : "schedules"}`),
    runProbe("channels", "Chat channels", "Telegram / Slack / Discord…", async () => channelStatus(), (c) => (c.length === 0 ? "none linked" : `${c.length} running`)),
    runProbe("mcp", "App connections (MCP)", "External tool servers", async () => fetchMcpConfig(), (s) => (s.length === 0 ? "none added" : `${s.length} ${s.length === 1 ? "server" : "servers"}`)),
    // `fetchFleetWorkers` (the strict reader) rather than `supervisionFleet`, so
    // a failed read rejects with the Gateway's own reason instead of arriving as
    // a `null` the probe then has to describe in words of its own. The detail is
    // derived from the fleet instead of being the constant "watching", which said
    // nothing about whether any worker is actually reporting.
    runProbe("watchdog", "Safety watchdog", "Worker health + self-heal", () => fetchFleetWorkers(), watchdogDetail),
    // No `catch` here, and no invented fallback wording. `companyStatus()` now
    // propagates the Gateway's own 404 detail, so both the workspace header and
    // the System control centre read exactly what the server said:
    // "No active organizations found. Bootstrap a company first."
    runProbe("company", "Autonomous company", "KPIs, board, briefings", async () => {
      const c = await companyStatus();
      if (!c) throw new Error("The Gateway returned an empty company status document.");
      return c;
    }, () => "active"),
  ]);
}

