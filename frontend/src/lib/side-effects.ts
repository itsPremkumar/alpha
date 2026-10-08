import { errMsg, get, send } from "./http";

/**
 * The effect journal client — `GET|POST /side-effects/*`.
 *
 * This is the browser half of the reconciliation console. The backend is
 * `app/gateway/routers/side_effects.py`, which projects ledger rows with an
 * explicit whitelist (`_entry_to_wire`) and never lets an argument or a result
 * cross the wire — only SHA-256 digests. Nothing here changes that: the client
 * maps what it is given and refuses to invent the rest.
 *
 * The rules, each with a tempting wrong reading:
 *
 * 1. **Absent is `null`, never `0`, `""` or `false`.** A count the Gateway did
 *    not send reads *not reported*, not `0` — "the ledger has no unknowns" and
 *    "the ledger could not be read" lead to opposite actions. `entries` is
 *    `SideEffectEntry[] | null` for the same reason: an omitted list is not an
 *    empty one.
 * 2. **Server enum strings are preserved verbatim.** `status`, `level`,
 *    `verdict`, `order` and `scope` are typed `string`, so a value from a newer
 *    Gateway renders as itself rather than being snapped to something this
 *    build knows.
 * 3. **Bounds are mirrored, not tightened.** `limit` is clamped to the server's
 *    `ge=1, le=500`; a filter value outside the server's own set is refused
 *    locally with the allowed set named, because sending it would only earn a
 *    422 with the same list in it.
 * 4. **The reconcile reason is the one required human field.** The server takes
 *    1–2000 characters and stores the text as the entry's `detail`; an empty or
 *    over-long reason is refused here with the bound named rather than sent.
 * 5. **`failureText` keeps a refusal verbatim.** `errMsg` replaces a 401/403/404
 *    detail with a generic sentence, which is right for an incidental failure
 *    and wrong for a reconciliation refusal — the server's words ("this entry
 *    was already settled by …") are the whole answer, so the section renders
 *    `failureText`.
 *
 * Coverage: `src/lib/side-effects.test.mjs`.
 */

/** Every status the list filter accepts, in the server's enum order. */
export const SIDE_EFFECT_STATUSES = ["pending", "in_flight", "completed", "failed", "unknown", "reconciled"] as const;

/** Every risk level the list filter accepts, in the server's enum order. */
export const SIDE_EFFECT_LEVELS = ["read_only", "low_risk", "moderate", "high_risk", "destructive"] as const;

/** The three verdicts a reconciliation may record. */
export const RECONCILE_VERDICTS = ["confirmed_success", "confirmed_failure", "undetermined"] as const;

export type SideEffectStatus = (typeof SIDE_EFFECT_STATUSES)[number];
export type SideEffectLevel = (typeof SIDE_EFFECT_LEVELS)[number];
export type ReconcileVerdict = (typeof RECONCILE_VERDICTS)[number];

/** The server's list bounds (`Query(ge=1, le=500)`, default 50). */
export const SIDE_EFFECT_LIMIT_DEFAULT = 50;
export const SIDE_EFFECT_LIMIT_MAX = 500;

/** The server's reason bound (`Field(min_length=1, max_length=2000)`). */
export const REASON_MIN_LENGTH = 1;
export const REASON_MAX_LENGTH = 2000;

/**
 * The headline counts. `by_status` / `by_level` are `null` when the Gateway
 * did not send them — never `{}`, because an empty record reads as "zero in
 * every bucket" and that is a measured claim.
 */
export interface SideEffectSummary {
  /** `false` means "the Gateway did not say it reported", not "the count is 0". */
  reported: boolean;
  /** `"all"` (admin) / `"owner"` (member), or `null` when not reported. */
  scope: string | null;
  total: number | null;
  by_status: Record<string, number> | null;
  by_level: Record<string, number> | null;
  unknown: number | null;
  /** Seconds the oldest `unknown` entry has waited; `null` when none is waiting. */
  oldest_unknown_age_seconds: number | null;
  generated_at: string | null;
}

/**
 * One ledger row as the API projects it.
 *
 * `status` / `level` are required on the wire, so a missing one maps to `""`
 * (a payload defect the view renders as an absent label) rather than to a value
 * this build made up. `needs_reconciliation` / `reconcilable` are tri-state:
 * `null` is "not reported" and must never be read as `false`.
 */
export interface SideEffectEntry {
  tool_call_id: string;
  tool_name: string;
  status: string;
  level: string;
  thread_id: string | null;
  run_id: string | null;
  user_id: string | null;
  arguments_digest: string | null;
  result_digest: string | null;
  verdict: string | null;
  detail: string | null;
  owner_worker_id: string | null;
  lease_expires_at: string | null;
  attempt: number | null;
  created_at: string | null;
  updated_at: string | null;
  needs_reconciliation: boolean | null;
  reconcilable: boolean | null;
}

/**
 * A page of entries. `entries` is `null` when the Gateway sent no list —
 * "the list did not arrive" and "the list arrived empty" are different facts.
 */
export interface SideEffectList {
  reported: boolean;
  scope: string | null;
  status_filter: string | null;
  /** `"oldest_first"` for the queue, `"newest_first"` otherwise. */
  order: string | null;
  count: number | null;
  entries: SideEffectEntry[] | null;
}

/** What the server confirmed about one recorded verdict. */
export interface ReconcileResult {
  tool_call_id: string;
  verdict: string | null;
  status: string | null;
  /** `true` when `undetermined` put the entry back to `unknown`. */
  reopened: boolean | null;
  /** `true` when a confirmed failure on a high-risk effect was escalated. */
  escalated: boolean | null;
  reconcilable: boolean | null;
  entry: SideEffectEntry | null;
}

export interface SideEffectListQuery {
  status?: string;
  level?: string;
  tool_name?: string;
  run_id?: string;
  thread_id?: string;
  user_id?: string;
  limit?: number;
}

// ---------------------------------------------------------------------------
// Mapping — verbatim where the server spoke, null where it did not
// ---------------------------------------------------------------------------

type Rec = Record<string, unknown>;

function rec(v: unknown): Rec {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {};
}

function optNum(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function optStr(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function optBool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

/**
 * A count table, or `null`.
 *
 * `null` covers three cases that must not become `{}`: the key was absent, it
 * was not an object, or at least one value was not a finite number. A table
 * with a hole in it is not a partial answer to "how many are unknown" — it is
 * an unreadable one, and the view says so.
 */
function optCounts(v: unknown): Record<string, number> | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const source = v as Rec;
  const out: Record<string, number> = {};
  for (const [key, value] of Object.entries(source)) {
    if (typeof value !== "number" || !Number.isFinite(value)) return null;
    out[key] = value;
  }
  return out;
}

/** Map one projected ledger row. Pure; exported for tests. */
export function mapEntry(raw: unknown): SideEffectEntry {
  const e = rec(raw);
  return {
    tool_call_id: optStr(e.tool_call_id) ?? "",
    tool_name: optStr(e.tool_name) ?? "",
    status: optStr(e.status) ?? "",
    level: optStr(e.level) ?? "",
    thread_id: optStr(e.thread_id),
    run_id: optStr(e.run_id),
    user_id: optStr(e.user_id),
    arguments_digest: optStr(e.arguments_digest),
    result_digest: optStr(e.result_digest),
    verdict: optStr(e.verdict),
    detail: optStr(e.detail),
    owner_worker_id: optStr(e.owner_worker_id),
    lease_expires_at: optStr(e.lease_expires_at),
    attempt: optNum(e.attempt),
    created_at: optStr(e.created_at),
    updated_at: optStr(e.updated_at),
    needs_reconciliation: optBool(e.needs_reconciliation),
    reconcilable: optBool(e.reconcilable),
  };
}

/** Map the summary envelope. Pure; exported for tests. */
export function mapSummary(raw: unknown): SideEffectSummary {
  const s = rec(raw);
  return {
    reported: s.reported === true,
    scope: optStr(s.scope),
    total: optNum(s.total),
    by_status: optCounts(s.by_status),
    by_level: optCounts(s.by_level),
    unknown: optNum(s.unknown),
    oldest_unknown_age_seconds: optNum(s.oldest_unknown_age_seconds),
    generated_at: optStr(s.generated_at),
  };
}

/** Map the list envelope. Pure; exported for tests. */
export function mapList(raw: unknown): SideEffectList {
  const l = rec(raw);
  const entries = Array.isArray(l.entries) ? l.entries : null;
  return {
    reported: l.reported === true,
    scope: optStr(l.scope),
    status_filter: optStr(l.status_filter),
    order: optStr(l.order),
    count: optNum(l.count),
    entries: entries ? entries.map(mapEntry) : null,
  };
}

/** Map the reconcile response. Pure; exported for tests. */
export function mapReconcileResult(raw: unknown): ReconcileResult {
  const r = rec(raw);
  const entry = r.entry === undefined || r.entry === null ? null : mapEntry(r.entry);
  return {
    tool_call_id: optStr(r.tool_call_id) ?? "",
    verdict: optStr(r.verdict),
    status: optStr(r.status),
    reopened: optBool(r.reopened),
    escalated: optBool(r.escalated),
    reconcilable: optBool(r.reconcilable),
    entry,
  };
}

// ---------------------------------------------------------------------------
// Request shaping
// ---------------------------------------------------------------------------

/**
 * Clamp a caller's limit onto the server's own `ge=1, le=500` window.
 *
 * Anything that is not a finite number falls back to the server's default
 * rather than being omitted, so a caller that passed garbage still gets a
 * bounded page instead of an unbounded one.
 */
export function clampLimit(limit: number | null | undefined): number {
  if (typeof limit !== "number" || !Number.isFinite(limit)) return SIDE_EFFECT_LIMIT_DEFAULT;
  return Math.min(SIDE_EFFECT_LIMIT_MAX, Math.max(1, Math.round(limit)));
}

/**
 * Refuse a filter the server would reject, naming the allowed set.
 *
 * Mirrors the router's own `invalid_filter` check exactly — same set, same
 * refusal — so a typo is explained before a round trip rather than after one.
 * An empty string is *omitted* instead (see `buildListPath`): the server reads
 * `status=` as a value, not as "no filter".
 */
function assertFilter(name: string, value: string | undefined, allowed: readonly string[]): void {
  if (value === undefined || value === "") return;
  if (!allowed.includes(value)) throw new Error(`unknown ${name} ${JSON.stringify(value)}; expected one of ${JSON.stringify([...allowed])}`);
}

/** Build `GET /side-effects?…`, omitting empty filters and clamping `limit`. */
export function buildListPath(query: SideEffectListQuery = {}): string {
  assertFilter("status", query.status, SIDE_EFFECT_STATUSES);
  assertFilter("level", query.level, SIDE_EFFECT_LEVELS);
  const params = new URLSearchParams();
  const put = (key: string, value: string | undefined): void => {
    if (value !== undefined && value !== "") params.set(key, value);
  };
  put("status", query.status);
  put("level", query.level);
  put("tool_name", query.tool_name);
  put("run_id", query.run_id);
  put("thread_id", query.thread_id);
  put("user_id", query.user_id);
  params.set("limit", String(clampLimit(query.limit)));
  return `/side-effects?${params.toString()}`;
}

/**
 * Refuse a reason the server would 422, naming the bound.
 *
 * The length is the **raw** string's, exactly as Pydantic measures it — no
 * trimming, because trimming here and not there would let a whitespace-only
 * reason through this check and be refused by the server.
 */
function assertReason(reason: string): void {
  const length = reason.length;
  if (length < REASON_MIN_LENGTH || length > REASON_MAX_LENGTH) {
    throw new Error(`reason must be ${REASON_MIN_LENGTH}–${REASON_MAX_LENGTH} characters (got ${length})`);
  }
}

/** Refuse a verdict the server does not accept, naming the allowed set. */
function assertVerdict(verdict: string): void {
  if (!(RECONCILE_VERDICTS as readonly string[]).includes(verdict)) {
    throw new Error(`unknown verdict ${JSON.stringify(verdict)}; expected one of ${JSON.stringify([...RECONCILE_VERDICTS])}`);
  }
}

/**
 * The sentence a failure renders.
 *
 * `errMsg` swaps a 401/403/404 detail for a generic sentence — correct for an
 * incidental call, wrong here: a reconciliation refusal carries the ledger's
 * own words (`already settled by …`, `reconciliation requires an
 * administrator …`), and those *are* the answer. So an `Error` that carries a
 * numeric status keeps its message verbatim, and everything else falls back to
 * the shared helper.
 */
export function failureText(err: unknown): string {
  if (err instanceof Error && err.message && typeof (err as { status?: unknown }).status === "number") return err.message;
  return errMsg(err);
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

/** `GET /side-effects/summary` — headline counts. Rejects with the server's reason. */
export async function fetchSideEffectSummary(): Promise<SideEffectSummary> {
  return mapSummary(await get<unknown>("/side-effects/summary"));
}

/** `GET /side-effects` — a bounded, filterable page of ledger rows. */
export async function fetchSideEffectList(query: SideEffectListQuery = {}): Promise<SideEffectList> {
  return mapList(await get<unknown>(buildListPath(query)));
}

/** `GET /side-effects/{tool_call_id}` — one row, re-read fresh. */
export async function fetchSideEffect(toolCallId: string): Promise<SideEffectEntry> {
  return mapEntry(await get<unknown>(`/side-effects/${encodeURIComponent(toolCallId)}`));
}

/**
 * `POST /side-effects/{tool_call_id}/reconcile` — record a verdict.
 *
 * Admin-only server-side; this client does not decide that, it sends the
 * request and surfaces the server's 403. Local refusals (a bound the server
 * would reject anyway) throw before any request is made.
 */
export async function reconcileSideEffect(toolCallId: string, verdict: string, reason: string): Promise<ReconcileResult> {
  assertVerdict(verdict);
  assertReason(reason);
  const raw = await send<unknown>(`/side-effects/${encodeURIComponent(toolCallId)}/reconcile`, "POST", { verdict, reason });
  return mapReconcileResult(raw);
}
