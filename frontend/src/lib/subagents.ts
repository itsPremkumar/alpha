import { get, send, asList, pick } from "./http";

export interface SubagentDef {
  name: string;
  /**
   * The server's `display_name`, or `null` when it sent none.
   *
   * `null` is not "unnamed": every catalog row carries a `name`, and the
   * server sends `display_name=None` for builtins that have no separate label.
   * `subagent-catalog-view.ts` owns the fallback sentence so the list and the
   * detail pane cannot disagree about what this row is called.
   */
  displayName: string | null;
  description: string;
  /**
   * The full system prompt, or `null` when the server withheld it.
   *
   * `GET /api/subagents` gates this behind `is_admin_user`
   * (`subagents.py:183`): a non-admin receives `system_prompt=None` for every
   * row. So `null` means "you are not an admin", and rendering it as an empty
   * prompt would claim a definition has no instructions when it plainly does.
   * The reason is disclosed rather than guessed — `promptDisclosure()` names
   * the admin gate as the reading, because it is the one this route can
   * produce.
   */
  systemPrompt: string | null;
  /**
   * The tool allowlist, or `null` when the server did not report one.
   *
   * `null` and `[]` are opposite facts. `SubagentResponse.tools` is
   * `list[str] | None`, so a definition with no `tools` constraint is `null`
   * (unrestricted) while an explicit empty list is `[]` (nothing callable).
   * `pick` already distinguishes them — it returns a real `[]` and falls back
   * only on `undefined`/`null` — so this mapping preserves the distinction and
   * `asStringList` must not collapse it.
   */
  tools: string[] | null;
  /** The deny-list, `null` for unreported. Same `null` vs `[]` distinction. */
  disallowedTools: string[] | null;
  /** Skills this subagent was granted, `null` for unreported. */
  skills: string[] | null;
  model: string;
  /** Turn ceiling, or `null` when unreported — never defaulted to 50 here. */
  maxTurns: number | null;
  /** Wall-clock ceiling in seconds, or `null` when unreported. */
  timeoutSeconds: number | null;
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
  /**
   * True when this name is also claimed by a built-in or a `config.yaml`
   * entry. The server keeps all three rows rather than silently shadowing, so
   * two definitions can share a name — and without this flag the UI would
   * present an overridden definition as the only one.
   */
  conflict: boolean;
  /**
   * The `config.yaml -> subagents.agents.<name>` keys explicitly set for this
   * entry, or `null` when the server sent no block. Only fields the operator
   * actually wrote appear here (`_explicit_overrides` reads
   * `model_fields_set`), so an empty object means "nothing was overridden",
   * not "nothing is configured" — the defaults still apply.
   */
  configOverrides: Record<string, unknown> | null;
}

/**
 * Map one list value without collapsing "reported empty" into "not reported".
 *
 * `pick` returns a real `[]` for an empty array and falls back to `null` for
 * `undefined`/`null`, so the distinction survives the helper — a wrapper that
 * did `Array.isArray(x) ? x : null` would keep it, while
 * `(pick(s, ["tools"], []) as string[])` would erase it. Non-string entries are
 * dropped rather than stringified: `"12"` is not a tool name, and rendering it
 * as one would put an un-callable chip in the tool list.
 */
function asStringList(
  s: Record<string, unknown>,
  key: string,
): string[] | null {
  const v = pick<unknown>(s, [key], null);
  if (!Array.isArray(v)) return null;
  return v.filter((x): x is string => typeof x === "string" && x !== "");
}

/** A count the server sent, or `null` — never a locally-invented default. */
function asNumberOrNull(
  s: Record<string, unknown>,
  key: string,
): number | null {
  const v = pick<unknown>(s, [key], null);
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Rejects on failure — an unreachable catalog must not read as an empty one. */
export async function listSubagentCatalog(): Promise<SubagentDef[]> {
  const d = await get<unknown>("/subagents");
  return asList(d, ["subagents", "data"]).map((s) => ({
    name: String(pick(s, ["name"], "")),
    displayName:
      typeof pick<unknown>(s, ["display_name"], null) === "string"
        ? String(pick(s, ["display_name"], ""))
        : null,
    description: String(pick(s, ["description"], "")),
    systemPrompt:
      typeof pick<unknown>(s, ["system_prompt"], null) === "string"
        ? String(pick(s, ["system_prompt"], ""))
        : null,
    tools: asStringList(s, "tools"),
    disallowedTools: asStringList(s, "disallowed_tools"),
    skills: asStringList(s, "skills"),
    // `SubagentResponse.model` defaults to the literal "inherit", so echoing
    // that string is the server's own wording. An absent model is left as the
    // empty string and the view says so, rather than the client inventing
    // "inherit" on the server's behalf.
    model: String(pick(s, ["model"], "")),
    maxTurns: asNumberOrNull(s, "max_turns"),
    timeoutSeconds: asNumberOrNull(s, "timeout_seconds"),
    enabled: typeof s.enabled === "boolean" ? s.enabled : null,
    source: String(pick(s, ["source"], "")),
    editable: typeof s.editable === "boolean" ? s.editable : false,
    conflict: typeof s.conflict === "boolean" ? s.conflict : false,
    configOverrides:
      pick<unknown>(s, ["config_overrides"], null) &&
      typeof s.config_overrides === "object" &&
      !Array.isArray(s.config_overrides)
        ? (s.config_overrides as Record<string, unknown>)
        : null,
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
    s && typeof s === "object"
      ? toLiveSubagent(s as Record<string, unknown>, i)
      : toLiveSubagent({}, i),
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

export async function spawnSubagent(
  objective: string,
  role = "general-purpose",
): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>("/subagents/control/spawn", "POST", {
    objective,
    role,
    parent_agent_id: "ui",
  });
}

export async function cancelSubagent(
  id: string,
  reason = "Cancelled from UI",
): Promise<void> {
  await send(`/subagents/control/${encodeURIComponent(id)}/cancel`, "POST", {
    reason,
  });
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
export async function subagentResult(
  id: string,
): Promise<Record<string, unknown> | null> {
  const d = await get<Record<string, unknown>>(
    `/subagents/control/${encodeURIComponent(id)}/result`,
  );
  // The route answers either with the deliverable itself, or with
  // `{"status": <enum>, "result": null}` when there is none
  // (subagent_control.py:124). An explicit null `result` is therefore the
  // server's own "no deliverable" answer and nothing else — a payload without
  // that key IS the deliverable and is shown verbatim.
  if (d && typeof d === "object" && "result" in d && d.result === null)
    return null;
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
export function subagentStatusTone(
  status: string,
): "green" | "amber" | "blue" | "red" | undefined {
  if (status === "running") return "blue";
  if (status === "completed") return "green";
  if (["failed", "error", "stalled", "expired"].includes(status)) return "red";
  if (["blocked", "waiting", "cancelled", "archived"].includes(status))
    return "amber";
  if (["created", "initializing", "ready", "recovering"].includes(status))
    return "blue";
  return undefined;
}

/** Every value of the server's `SubagentStatusEnum`, so a new one is visible. */
export const KNOWN_SUBAGENT_STATUSES = [
  "created",
  "initializing",
  "ready",
  "running",
  "waiting",
  "blocked",
  "stalled",
  "completed",
  "failed",
  "recovering",
  "cancelled",
  "expired",
  "archived",
] as const;

/** True when this build names the status; false flags a newer Gateway. */
export function isKnownSubagentStatus(status: string): boolean {
  return (KNOWN_SUBAGENT_STATUSES as readonly string[]).includes(status);
}

/** The same rule for a work batch, whose statuses the Gateway owns separately. */
export function batchStatusTone(
  status: string,
): "green" | "amber" | "blue" | "red" | undefined {
  if (status === "running") return "blue";
  if (["completed", "done", "succeeded", "success"].includes(status))
    return "green";
  if (["failed", "error", "stalled", "expired"].includes(status)) return "red";
  if (["blocked", "cancelled", "partial"].includes(status)) return "amber";
  if (["pending", "queued", "created"].includes(status)) return "blue";
  return undefined;
}

/** The same rule for one item inside a batch. */
export function batchItemStatusTone(
  status: string,
): "green" | "amber" | "blue" | "red" | undefined {
  return batchStatusTone(status);
}
