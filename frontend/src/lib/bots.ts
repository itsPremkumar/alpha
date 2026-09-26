import { BotProfile, BotTemplate, FleetHealth } from "@/types/bots";
import { apiFetch } from "./api-client";

function normalizeBot(raw: Record<string, unknown>): BotProfile {
  const taskStats = (raw.task_stats as BotProfile["task_stats"]) || {};
  return {
    name: String(raw.name || "unknown"),
    display_name: String(raw.display_name || raw.name || "Unknown"),
    role: String(raw.role || "Specialist Agent"),
    soul: typeof raw.soul === "string" ? raw.soul : "",
    model: typeof raw.model === "string" ? raw.model : undefined,
    toolsets: Array.isArray(raw.toolsets) ? (raw.toolsets as string[]) : [],
    skills: Array.isArray(raw.skills) ? (raw.skills as string[]) : [],
    avatar: typeof raw.avatar === "string" ? raw.avatar : "",
    status: typeof raw.status === "string" ? raw.status : "active",
    last_active: typeof raw.last_active === "string" ? raw.last_active : null,
    version: typeof raw.version === "number" ? raw.version : 1,
    epoch: typeof raw.epoch === "string" ? raw.epoch : null,
    department: typeof raw.department === "string" ? raw.department : "engineering",
    reports_to: typeof raw.reports_to === "string" ? raw.reports_to : null,
    responsibilities: Array.isArray(raw.responsibilities) ? (raw.responsibilities as string[]) : [],
    capabilities: Array.isArray(raw.capabilities) ? (raw.capabilities as string[]) : [],
    heartbeat: typeof raw.heartbeat === "string" ? raw.heartbeat : null,
    succession_fallback: typeof raw.succession_fallback === "string" ? raw.succession_fallback : null,
    // Backend returns null for bots with no recorded runs (unverified).
    // Never coerce null → 1 — that would fabricate a perfect score.
    reputation_score: typeof raw.reputation_score === "number" ? raw.reputation_score : null,
    task_stats: taskStats,
    routines: Array.isArray(raw.routines) ? (raw.routines as Array<Record<string, unknown>>) : [],
    created_at: typeof raw.created_at === "string" ? raw.created_at : null,
    updated_at: typeof raw.updated_at === "string" ? raw.updated_at : null,
  };
}

export async function fetchBots(params?: { status?: string; department?: string }): Promise<BotProfile[]> {
  // Live data only: an unreachable backend or an empty fleet returns [],
  // and the UI shows its honest empty state. No fabricated bots.
  try {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.department) qs.set("department", params.department);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    const res = await apiFetch(`/bots${suffix}`);
    const data = await res.json();
    const list = Array.isArray(data.bots) ? data.bots : [];
    return list.map((b: Record<string, unknown>) => normalizeBot(b));
  } catch (err) {
    console.error("Failed to fetch bots:", err);
    return [];
  }
}

export async function fetchBot(name: string): Promise<BotProfile | null> {
  try {
    const res = await apiFetch(`/bots/${encodeURIComponent(name)}`);
    return normalizeBot(await res.json());
  } catch (err) {
    console.error(`Failed to fetch bot ${name}:`, err);
    return null;
  }
}

export async function fetchBotTemplates(): Promise<BotTemplate[]> {
  try {
    const res = await apiFetch(`/bots/templates`);
    const data = await res.json();
    const raw = data.templates;
    if (Array.isArray(raw)) return raw as BotTemplate[];
    if (raw && typeof raw === "object")
      return Object.entries(raw).map(([name, t]) => ({ name, ...(t as Omit<BotTemplate, "name">) }));
    return [];
  } catch (err) {
    console.error("Failed to fetch bot templates:", err);
    return [];
  }
}

export async function fetchDepartments(): Promise<string[]> {
  try {
    const res = await apiFetch(`/bots/departments`);
    const data = await res.json();
    return Array.isArray(data.departments) ? data.departments : [];
  } catch (err) {
    console.error("Failed to fetch departments:", err);
    return [];
  }
}

export function computeFleetHealth(bots: BotProfile[]): FleetHealth {
  const total = bots.length;
  const active = bots.filter((b) => b.status === "active").length;
  const paused = bots.filter((b) => b.status === "paused").length;
  const disabled = bots.filter((b) => b.status === "disabled").length;
  // Average ONLY measured (non-null) scores. Unverified bots must not drag
  // the average to 0, and with nothing measured the honest answer is null —
  // never a fabricated fleet reputation number.
  const measured = bots
    .map((b) => b.reputation_score)
    .filter((s): s is number => typeof s === "number");
  const avg_reputation =
    measured.length === 0 ? null : measured.reduce((sum, s) => sum + s, 0) / measured.length;
  const total_tasks = bots.reduce((sum, b) => sum + (Number(b.task_stats?.total) || 0), 0);
  return { total, active, paused, disabled, avg_reputation, total_tasks };
}

export function uniqueDepartments(bots: BotProfile[]): string[] {
  return Array.from(new Set(bots.map((b) => b.department || "general"))).sort();
}

/** Tell the server this bot was invoked in a run (updates last_active/version). Best-effort. */
export async function touchBot(name: string): Promise<void> {
  try {
    await apiFetch(`/bots/${encodeURIComponent(name)}/match`, { method: "POST" });
  } catch {
    /* offline or not permitted — never block chatting */
  }
}
