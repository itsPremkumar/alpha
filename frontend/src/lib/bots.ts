import { BotProfile, BotSmokeOutcome, BotTemplate, FleetHealth } from "@/types/bots";
import { apiFetch } from "./api-client";

/**
 * Map the recorded smoke outcome, or `null` when none is recorded.
 *
 * `status` is the one field that must be present for the outcome to mean
 * anything; a malformed blob is treated as "no recorded smoke" rather than a
 * partial verdict. Every other field degrades to `null` individually — absent
 * is null, never `""` / `0` / `false` (the honesty contract in
 * `lib/bot-detail.ts`).
 */
function normalizeSmoke(raw: unknown): BotSmokeOutcome | null {
  if (!raw || typeof raw !== "object") return null;
  const s = raw as Record<string, unknown>;
  if (typeof s.status !== "string" || !s.status) return null;
  return {
    status: s.status,
    run_id: typeof s.run_id === "string" ? s.run_id : null,
    thread_id: typeof s.thread_id === "string" ? s.thread_id : null,
    observed_status: typeof s.observed_status === "string" ? s.observed_status : null,
    answered: s.answered === true,
    detail: typeof s.detail === "string" ? s.detail : null,
    at: typeof s.at === "string" ? s.at : null,
  };
}

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
    status: typeof raw.status === "string" && raw.status ? raw.status : null,
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
    // No recorded smoke => "created · unverified". The badge may not infer a
    // verdict the server did not record.
    smoke: normalizeSmoke(raw.smoke),
    alive: raw.alive === true,
  };
}

export async function fetchBots(params?: { status?: string; department?: string; activity?: boolean }): Promise<BotProfile[]> {
  // Live data only: an unreachable backend or an empty fleet returns [],
  // and the UI shows its honest empty state. No fabricated bots.
  const result = await fetchBotsResult(params);
  return result.ok ? result.value : [];
}

/**
 * The roster read, keeping "the server said there is nothing" apart from
 * "the read failed".
 *
 * `fetchBots` collapses both into `[]`, which is the right default for a
 * sidebar showing an empty state and the wrong one for the composer's `@` tag
 * palette: a picker that answers "no agents available" after a failed read is
 * claiming the fleet is empty. This returns the same `FetchResult` shape
 * `api.ts::fetchThreadsResult` established, so a surface that must disclose a
 * degraded read has one vocabulary for it.
 */
export async function fetchBotsResult(params?: {
  status?: string;
  department?: string;
  activity?: boolean;
}): Promise<{ ok: true; value: BotProfile[] } | { ok: false; error: string }> {
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
    return { ok: true, value: list.map((b: Record<string, unknown>) => normalizeBot(b)) };
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    console.error("Failed to fetch bots:", err);
    return { ok: false, error };
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

/**
 * The role-template catalog (`GET /api/bots/templates`).
 *
 * The live route answers `{templates: [{slug, display, role, avatar,
 * department, reports_to, responsibilities, capabilities}]}` — the handle is
 * `slug` (alpha/bots/templates.py `list_templates`). This projection used to
 * cast the array straight to `BotTemplate`, which reads `name`, so every row
 * would have arrived with an undefined handle the moment anything consumed it.
 *
 * Rejects on failure: `[]` must mean the server listed no templates, not that
 * the read failed (frontend/src/AGENTS.md → Client honesty rules).
 */
export async function fetchBotTemplates(): Promise<BotTemplate[]> {
  const res = await apiFetch(`/bots/templates`);
  const data = await res.json();
  const raw = data.templates;
  const list: Array<Record<string, unknown>> = Array.isArray(raw)
    ? raw
    : raw && typeof raw === "object"
      ? Object.entries(raw as Record<string, unknown>).map(([name, t]) => ({
          ...(t && typeof t === "object" ? (t as Record<string, unknown>) : {}),
          slug: name,
        }))
      : [];
  return list
    .map((t) => ({
      name: String(t.slug ?? t.name ?? ""),
      display: String(t.display ?? ""),
      role: String(t.role ?? ""),
      avatar: String(t.avatar ?? ""),
      department: String(t.department ?? ""),
      reports_to: typeof t.reports_to === "string" ? t.reports_to : null,
      responsibilities: Array.isArray(t.responsibilities) ? (t.responsibilities as string[]) : [],
      capabilities: Array.isArray(t.capabilities) ? (t.capabilities as string[]) : [],
    }))
    .filter((t) => t.name !== "");
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

/** What the create dialog collects; everything but `name` is optional. */
export interface BotCreateInput {
  name: string;
  role?: string;
  /** Template slug from `fetchBotTemplates` — the server resolves the rest. */
  template?: string;
  displayName?: string;
  department?: string;
  /** Must be declared in `config.yaml` models[]; the forge refuses the rest. */
  model?: string;
  skills?: string[];
}

/**
 * The forge's answer (`POST /api/bots`), carrying every honesty field.
 *
 * `alive` is load-bearing: it is true ONLY when the forge ran a smoke probe
 * and that probe answered. A clean build with no probe is `ok: true` with
 * `alive: false` and `smokeTested: false` — "created · unverified", never
 * "alive". `reason`, `steps` and `rollbackReport` are what a refusal carries —
 * including a clean rollback, which is the difference between "nothing was
 * built" and "something half-built is hiding on the roster".
 */
export interface BotForgeResult {
  ok: boolean;
  name: string;
  reason: string;
  alive: boolean;
  smokeTested: boolean;
  rolledBack: boolean;
  steps: string[];
  warnings: string[];
  rollbackReport: string[];
  elapsedSeconds: number;
  /** The forge's own operator-facing message (includes any "heads up" notes). */
  reply: string;
}

/**
 * Forge one bot through the transactional create (`POST /api/bots`).
 *
 * The route answers 200 with `ok: false` for a forge REFUSAL (name taken,
 * unknown template, overlapping role, unconfigured model) because the refusal
 * carries the steps and rollback report; a non-2xx here is a transport or
 * permission failure and rejects with the server's own text.
 */
export async function forgeBot(input: BotCreateInput): Promise<BotForgeResult> {
  const res = await apiFetch(`/bots`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      role: input.role ?? "",
      ...(input.template ? { template: input.template } : {}),
      ...(input.displayName ? { display_name: input.displayName } : {}),
      ...(input.department ? { department: input.department } : {}),
      ...(input.model ? { model: input.model } : {}),
      ...(input.skills && input.skills.length > 0 ? { skills: input.skills } : {}),
    }),
  });
  const data = (await res.json()) as Record<string, unknown>;
  return {
    ok: data.ok === true,
    name: String(data.name ?? input.name),
    reason: String(data.reason ?? ""),
    alive: data.alive === true,
    smokeTested: data.smoke_tested === true,
    rolledBack: data.rolled_back === true,
    steps: Array.isArray(data.steps) ? (data.steps as string[]) : [],
    warnings: Array.isArray(data.warnings) ? (data.warnings as string[]) : [],
    rollbackReport: Array.isArray(data.rollback_report) ? (data.rollback_report as string[]) : [],
    elapsedSeconds: typeof data.elapsed_seconds === "number" ? data.elapsed_seconds : 0,
    reply: String(data.reply ?? ""),
  };
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

/**
 * The smoke route's answer (`POST /api/bots/{name}/smoke`).
 *
 * The route ran one REAL bot-scoped run and observed it to terminal. `ok`
 * answers "was a terminal result observed" — a probe that ran and failed is
 * still `ok: true`, with the failure recorded. `alive` is true only when that
 * observed result was success. A non-2xx (403 non-admin, 404 unknown bot, 409
 * busy thread) rejects with the server's own text and is never painted as a
 * smoke result.
 */
export interface BotSmokeResult {
  ok: boolean;
  name: string;
  alive: boolean;
  smoke: BotSmokeOutcome | null;
  runId: string | null;
  reply: string | null;
  messageCount: number | null;
}

/** Run one real bot-scoped smoke probe and return the observed result. */
export async function runBotSmoke(name: string): Promise<BotSmokeResult> {
  const res = await apiFetch(`/bots/${encodeURIComponent(name)}/smoke`, { method: "POST" });
  const data = (await res.json()) as Record<string, unknown>;
  return {
    ok: data.ok === true,
    name: String(data.name ?? name),
    alive: data.alive === true,
    smoke: normalizeSmoke(data.smoke),
    runId: typeof data.run_id === "string" ? data.run_id : null,
    reply: typeof data.reply === "string" ? data.reply : null,
    messageCount: typeof data.message_count === "number" ? data.message_count : null,
  };
}

/** The badge state vocabulary: what a bot's record can say about verification. */
export type BotSmokeBadgeState = "unverified" | "passed" | "failed" | "timeout" | "unknown";

/** The badge view-model for one bot, computed once so tests can pin the words. */
export interface BotSmokeBadgeView {
  state: BotSmokeBadgeState;
  label: string;
  /** The smoke run id, shown beside a verdict. Never invented. */
  runId: string | null;
  /** The recorded failure reason, for the badge's `title`. */
  detail: string | null;
}

/**
 * The verification badge for one bot, exactly as the roster records it.
 *
 * Vocabulary is fixed by the plan: **`created · unverified`** (no smoke
 * recorded) → **`smoke passed`** (with the run id). A failed or timed-out
 * probe keeps its own label — neither can borrow "passed", and none of them
 * may render as "alive". A profile that failed to load is "verification
 * unknown", which is NOT the same claim as "created · unverified": the read
 * failed, so nothing is known about this bot at all.
 */
export function botSmokeBadge(bot: Pick<BotProfile, "smoke"> | null): BotSmokeBadgeView {
  if (!bot) {
    return { state: "unknown", label: "verification unknown — profile read failed", runId: null, detail: null };
  }
  const smoke = bot.smoke ?? null;
  if (!smoke) return { state: "unverified", label: "created · unverified", runId: null, detail: null };
  if (smoke.status === "passed") return { state: "passed", label: "smoke passed", runId: smoke.run_id, detail: smoke.detail };
  if (smoke.status === "failed") return { state: "failed", label: "smoke failed", runId: smoke.run_id, detail: smoke.detail };
  if (smoke.status === "timeout") return { state: "timeout", label: "smoke timed out", runId: smoke.run_id, detail: smoke.detail };
  // A verdict this build does not know is shown verbatim — never snapped to a
  // label this build picked.
  return { state: "unknown", label: `smoke ${smoke.status}`, runId: smoke.run_id, detail: smoke.detail };
}
