import { BotProfile, BotTemplate, FleetHealth } from "@/types/bots";
import { apiFetch } from "./api-client";

/** Map one raw `GET /api/bots` row onto a BotProfile (exported for tests). */
export function normalizeBot(raw: Record<string, unknown>): BotProfile {
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
    // Activity is a requested projection. Absent stays null: rendering an
    // unrequested row as "0 unread" would claim the server measured zero
    // rather than that nobody asked.
    unread_count: typeof raw.unread_count === "number" ? raw.unread_count : null,
    last_message_preview: typeof raw.last_message_preview === "string" ? raw.last_message_preview : null,
    last_message_at: typeof raw.last_message_at === "number" ? raw.last_message_at : null,
    last_message_sender: typeof raw.last_message_sender === "string" ? raw.last_message_sender : null,
    last_message_withheld: raw.last_message_withheld === true,
  };
}

export async function fetchBots(params?: { status?: string; department?: string; activity?: boolean }): Promise<BotProfile[]> {
  // Live data only: an unreachable backend or an empty fleet returns [],
  // and the UI shows its honest empty state. No fabricated bots.
  try {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.department) qs.set("department", params.department);
    // Activity costs a per-bot inbox read plus a secret scan server-side, so it
    // is requested explicitly and the default roster read stays cheap.
    if (params?.activity) qs.set("activity", "true");
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

/**
 * One measured counter, or null when the server did not report it.
 *
 * The `|| 0` this replaces was the bug: it could not distinguish "the server
 * measured zero" from "the server sent no such field" (and, because the field
 * name was wrong, the second case was the only one that ever occurred). A
 * non-finite value is also treated as unmeasured rather than summed in as NaN.
 */
function measuredCount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Measured runs for one bot, or null when the Gateway reported no counter. */
export function totalRuns(bot: Pick<BotProfile, "task_stats">): number | null {
  return measuredCount(bot.task_stats?.total_runs);
}

/** Measured successful runs for one bot, or null when unreported. */
export function completedRuns(bot: Pick<BotProfile, "task_stats">): number | null {
  return measuredCount(bot.task_stats?.completed);
}

/** Measured failed runs for one bot, or null when unreported. */
export function failedRuns(bot: Pick<BotProfile, "task_stats">): number | null {
  return measuredCount(bot.task_stats?.failed);
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
  // Every bot must have reported a counter for the sum to mean anything.
  // Averaging `avg_reputation` above may safely exclude unmeasured bots (each
  // one is a disclosed exclusion), but a SUM cannot: dropping a bot that did
  // not answer lowers the total without saying so, so a fleet that ran 3 of 5
  // bots' work would read as "3 tasks done". A partial sum is worse than no
  // number, because it looks complete. null = the fleet total is not measurable.
  const runCounts = bots.map(totalRuns);
  const total_tasks =
    runCounts.length > 0 && runCounts.every((n): n is number => n !== null)
      ? runCounts.reduce((sum, n) => sum + n, 0)
      : null;
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

/**
 * PATCH a bot profile with only the keys that changed.
 *
 * The response is mapped back through `normalizeBot`, so the caller shows the
 * SERVER's answer after a save rather than the draft the user typed. That is the
 * same rule the project settings form follows: a 2xx means the request was
 * accepted, not proof the registry now reads the way the form claims.
 *
 * Throws with the server's reason. An empty patch is a 422, so this refuses one
 * outright rather than relying on every caller to remember.
 */
export async function updateBotProfile(
  name: string,
  patch: Record<string, unknown>,
): Promise<BotProfile | null> {
  if (Object.keys(patch).length === 0) {
    throw new Error("Refusing to send an empty update: the Gateway rejects it as invalid.");
  }
  const res = await apiFetch(`/bots/${encodeURIComponent(name)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  const body = (await res.json()) as Record<string, unknown>;
  // Tolerate a bare profile or one wrapped under `bot` / `profile`.
  const candidate = (body.bot ?? body.profile ?? body) as Record<string, unknown>;
  if (!candidate || typeof candidate !== "object" || !("name" in candidate)) return null;
  return normalizeBot(candidate);
}
