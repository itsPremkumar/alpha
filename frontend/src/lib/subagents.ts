import { get, send, asList, pick } from "./http";

export interface SubagentDef {
  name: string;
  description: string;
  model: string;
  /**
   * The server's `enabled` flag, or `null` when the server did not send one.
   *
   * It used to be `Boolean(pick(s, ["enabled"], true))` — absent became
   * **enabled**, and `SubagentsSection` painted that as a GREEN "on" badge. A
   * catalog entry the server never enabled was displayed as enabled, with no
   * way to tell the two apart. `SubagentResponse.enabled` is a plain
   * `bool = True` server-side, so a genuine absence is a shape drift or an
   * older Gateway, and it must read as unknown rather than as on.
   */
  enabled: boolean | null;
  source: string;
  editable: boolean;
}

/** Rejects on failure — an unreachable catalog must not read as an empty one. */
export async function listSubagentCatalog(): Promise<SubagentDef[]> {
  const d = await get<unknown>("/subagents");
  return asList(d, ["subagents", "data"]).map((s) => ({
    name: String(pick(s, ["name"], "")),
    description: String(pick(s, ["description"], "")),
    // `SubagentResponse.model` defaults to the literal "inherit", so echoing
    // that string is the server's own wording. An absent model is left as the
    // empty string and the view says so, rather than the client inventing
    // "inherit" on the server's behalf.
    model: String(pick(s, ["model"], "")),
    enabled: typeof s.enabled === "boolean" ? s.enabled : null,
    source: String(pick(s, ["source"], "")),
    editable: typeof s.editable === "boolean" ? s.editable : false,
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

/**
 * Fleet read for the status surfaces. Rejects on failure.
 *
 * Exact route: GET /api/subagents/control (subagent_control.py @router.get("")).
 * A former /subagents/live fallback was unwired on this gateway — the only match
 * would be GET /api/subagents/{name}, a single-bot lookup that cannot serve a
 * registry — so an unreachable gateway really is the remaining failure, and
 * that is precisely why it must REJECT.
 *
 * This caught and returned `[]`. Measured against a down Gateway, `[]` is what
 * `SubagentsSection` then rendered as "Running now (0)" and the `EmptyState`
 * "Nothing running" — the UI asserting that no helper is working, when in fact
 * nothing had been read. The `listSubagents` route is also admin-free and
 * answers for a fleet that may legitimately be empty, so an empty list is a
 * real answer and must be distinguishable from a failed read.
 *
 * Kept as the lenient alias for callers that have their own error surface; new
 * callers should prefer `fetchLiveSubagentsStrict` so the reason survives.
 */
export async function listLiveSubagents(): Promise<LiveSubagent[]> {
  return fetchLiveSubagentsStrict();
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

/**
 * The deliverable for one subagent, or `null` when the server says there is
 * none. Rejects on failure.
 *
 * The route answers 200 with `{"status": …, "result": null}` when the subagent
 * has no deliverable yet (subagent_control.py:117-124), and 404 with
 * `Subagent '<id>' not found.` otherwise. Those are different facts, and the
 * old `catch { return null }` collapsed both into `null` — so `SubagentsSection`
 * rendered "No result yet — it may still be working." for a request that never
 * completed, inventing a reason for a read that failed.
 *
 * Callers must render the rejection. `null` now means one thing only: the
 * server answered and reported no result.
 */
export async function subagentResult(id: string): Promise<Record<string, unknown> | null> {
  const d = await get<Record<string, unknown>>(`/subagents/control/${encodeURIComponent(id)}/result`);
  // The route answers either with the deliverable itself, or with
  // `{"status": <enum>, "result": null}` when there is none
  // (subagent_control.py:124). An explicit null `result` is therefore the
  // server's own "no deliverable" answer and nothing else — a payload without
  // that key IS the deliverable and is shown verbatim.
  if (d && typeof d === "object" && "result" in d && d.result === null) return null;
  return d;
}

/**
 * A subagent status as a badge tone, without ever inventing a state.
 *
 * The row read
 * `s.status === "running" ? "blue" : s.status === "failed" || s.status === "error" ? undefined : "green"`,
 * so EVERY status that was not `running`, `failed` or `error` drew a GREEN
 * badge. Measured with a status from a newer Gateway (`awaiting_approval`) the
 * string was preserved correctly and the tone still came out `green`.
 *
 * That is not hypothetical. `SubagentStatusEnum`
 * (alpha/subagents/lifecycle.py) is `created, initializing, ready, running,
 * waiting, blocked, stalled, completed, failed, recovering, cancelled, expired,
 * archived` — ten of those thirteen rendered green, including `cancelled`,
 * `stalled`, `expired` and `archived`, which are exactly the states an operator
 * needs to notice. Green is a claim that the work succeeded.
 *
 * The rule now: a status this build does not name gets the neutral tone and is
 * flagged as unrecognised, never the success colour.
 */
export function subagentStatusTone(status: string): "green" | "amber" | "blue" | "red" | undefined {
  if (status === "running") return "blue";
  if (status === "completed") return "green";
  if (["failed", "error", "stalled", "expired"].includes(status)) return "red";
  if (["blocked", "waiting", "cancelled", "archived"].includes(status)) return "amber";
  if (["created", "initializing", "ready", "recovering"].includes(status)) return "blue";
  return undefined;
}

/** Every value of the server's `SubagentStatusEnum`, so a new one is visible. */
export const KNOWN_SUBAGENT_STATUSES = [
  "created", "initializing", "ready", "running", "waiting", "blocked", "stalled",
  "completed", "failed", "recovering", "cancelled", "expired", "archived",
] as const;

/** True when this build names the status; false flags a newer Gateway. */
export function isKnownSubagentStatus(status: string): boolean {
  return (KNOWN_SUBAGENT_STATUSES as readonly string[]).includes(status);
}

/** The same rule for a work batch, whose statuses the Gateway owns separately. */
export function batchStatusTone(status: string): "green" | "amber" | "blue" | "red" | undefined {
  if (status === "running") return "blue";
  if (["completed", "done", "succeeded", "success"].includes(status)) return "green";
  if (["failed", "error", "stalled", "expired"].includes(status)) return "red";
  if (["blocked", "cancelled", "partial"].includes(status)) return "amber";
  if (["pending", "queued", "created"].includes(status)) return "blue";
  return undefined;
}

/** The same rule for one item inside a batch. */
export function batchItemStatusTone(status: string): "green" | "amber" | "blue" | "red" | undefined {
  return batchStatusTone(status);
}
