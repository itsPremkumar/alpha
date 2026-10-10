import { errMsg, get, send } from "./http";
import { absoluteStamp, relTime } from "./time";

/**
 * The mod kernel client — `GET|POST /mods/*`.
 *
 * This is the browser half of the operator surface over Alpha's ordered
 * middleware chain. The backend is `app/gateway/routers/mods.py`, a **read-only
 * projection**: nothing on it registers, mutates or reorders a mod, and nothing
 * executes a tool on a mod's behalf. The two mutations it does carry are the
 * hold decision and the mod-command run, and both are shaped exactly as the
 * router declares them.
 *
 * The rules, each with a tempting wrong reading:
 *
 * 1. **Absent is `null`, never `0`, `""` or `false`.** A count the Gateway did
 *    not send reads *not reported* — "the chain is empty" and "the chain could
 *    not be read" lead to opposite actions. Every list (`chain`, `mods`,
 *    `commands`, `entries`, `holds`, `affected_paths`) is `T[] | null` for the
 *    same reason: an omitted list is not an empty one.
 * 2. **Server strings are preserved verbatim.** `decision`, `outcome`, `status`,
 *    `kind` and `risk_level` are typed `string`, so a value from a newer Gateway
 *    renders as itself rather than being snapped to something this build knows.
 * 3. **`measurable` is tri-state.** `false` means the impact preview *could not
 *    be computed* — it is not "nothing is at stake" — and an absent `measurable`
 *    is a third state the Gateway never spoke. Both are kept apart from `true`.
 * 4. **A hold's `decision` is not whether it may proceed.** The store keeps an
 *    approval whose TTL has passed as `decision: "approved"`, because expiry is
 *    time-based and voids the decision rather than rewriting it. So
 *    `holdExpiryView` reads `expires_at` beside the decision and never lets the
 *    decision alone carry the verdict.
 * 5. **Bounds are mirrored, not tightened.** `limit` clamps onto the server's
 *    own `ge=1, le=1000` (audit) and `ge=1, le=500` (holds); the hold decision
 *    filter is refused locally *with the allowed set named*, because the router
 *    accepts any string and answers an unknown one with an empty list that
 *    reads as "no holds" — a fabricated absence, not a 422.
 * 6. **`failureText` keeps a refusal verbatim.** `errMsg` paraphrases 401/403/404
 *    on purpose, which is right for an incidental call and wrong here: the
 *    admin-only routes answer with their own sentence ("Admin privileges are
 *    required to read the mod audit ledger."), a missing audit mod answers
 *    `503`, and an approval-gated command answers `409` — those sentences *are*
 *    the answer, so the section renders them as written.
 * 7. **No client-side role check exists.** Authorisation is server-owned: the
 *    admin blocks render for every caller and the server's 403 is the verdict.
 *
 * Coverage: `src/lib/mods.test.mjs` (routes, verbs, the null-preserving
 * mappers, the clamps, the local refusals, `failureText`,
 * `holdExpiryView`) and `src/lib/mods-view.test.mjs` (the four-place wiring and
 * the honesty claims the panel renders).
 */

/** Server bound for `GET /mods/audit` — `Query(default=100, ge=1, le=1000)`. */
export const AUDIT_LIMIT_DEFAULT = 100;
export const AUDIT_LIMIT_MAX = 1000;

/** Server bound for `GET /mods/holds` — `Query(default=100, ge=1, le=500)`. */
export const HOLDS_LIMIT_DEFAULT = 100;
export const HOLDS_LIMIT_MAX = 500;

/** `HoldDecisionRequest.reason` — `Field(default="", max_length=2000)`. */
export const HOLD_REASON_MAX_LENGTH = 2000;

/** The four decisions a hold can carry, from `approvals.HoldDecision`. */
export const HOLD_DECISIONS = ["pending", "approved", "rejected", "expired"] as const;

/** Which of the two mutations a hold row may take. */
export const HOLD_VERBS = ["approve", "reject"] as const;
export type HoldVerb = (typeof HOLD_VERBS)[number];

// ---------------------------------------------------------------------------
// Shapes
// ---------------------------------------------------------------------------

/**
 * One row of the dispatch chain, as `control_chain()` reports it: sorted by the
 * same priority key `dispatch` uses, never by registration time.
 */
export interface ChainRow {
  order: number | null;
  mod: string;
  version: string | null;
  priority: number | null;
  first_party: boolean | null;
  /** `["*"]` when the mod subscribes to everything. */
  subscribed_events: string[] | null;
}

/**
 * One mod's review-facing description: declared beside observed, never merged.
 *
 * `error` is the kernel's own escape hatch — a `describe` that raised is
 * reported as `{"name", "error"}` and every other field is absent, so a mod
 * whose description could not be produced reads as unavailable rather than as a
 * card with nothing in it.
 */
export interface ModDescription {
  name: string;
  version: string | null;
  description: string | null;
  priority: number | null;
  first_party: boolean | null;
  /** What the registration path actually granted. */
  granted_capabilities: string[] | null;
  /** What the mod says it needs. A declaration, never a grant. */
  required_capabilities: string[] | null;
  subscribed_events: string[] | null;
  /** The `ModManifest` as declared (`hooks`, `calls`, `state_reads`, …). */
  declared: Record<string, unknown> | null;
  /** What the kernel observed (`hooks`, `calls`, `state_reads`, …). */
  observed: Record<string, unknown> | null;
  /** Every place the two disagree; `null` when the Gateway sent no list. */
  discrepancies: string[] | null;
  /** Present only when `describe` failed for this mod. */
  error: string | null;
}

/** `GET /mods` — the fleet, its ordered chain, and per-mod descriptions. */
export interface ModFleet {
  total: number | null;
  chain: ChainRow[] | null;
  mods: ModDescription[] | null;
}

/** One command a mod contributed. `requires_approval` is the mod's own flag. */
export interface ModCommand {
  name: string;
  mod_name: string;
  description: string;
  requires_approval: boolean;
  registered_at: number | null;
}

export interface ModCommandList {
  total: number | null;
  commands: ModCommand[] | null;
}

/** What a mod command reported when it ran. `status` is the mod's own word. */
export interface ModCommandResult {
  command: string;
  mod_name: string | null;
  status: string | null;
  output: string | null;
  requires_approval: boolean | null;
}

/** One audit ledger record (an explicit projection, redacted server-side). */
export interface AuditEntry {
  timestamp: number | null;
  event: string;
  event_id: string | null;
  source: string | null;
  outcome: string;
  reason: string | null;
  duration_ms: number | null;
  chain: string[] | null;
  payload_digest: string | null;
  /** Only present when a mod rewrote something on the way through. */
  rewrites: { mod: string | null; reason: string | null }[] | null;
}

/** Aggregate counters over the *retained window* — not over all time. */
export interface AuditStats {
  retained: number | null;
  capacity: number | null;
  events: number | null;
  by_mod: Record<string, number> | null;
  by_outcome: Record<string, number> | null;
}

export interface AuditPage {
  total: number | null;
  entries: AuditEntry[] | null;
  stats: AuditStats | null;
}

/** One durable hold. Timestamps are epoch **seconds** (`time.time()` floats). */
export interface HoldRecord {
  hold_id: string;
  idempotency_key: string;
  tool_name: string;
  risk_level: string;
  reason: string;
  run_id: string;
  tool_call_id: string;
  created_at: number | null;
  decided_at: number | null;
  decided_by: string | null;
  /** `pending` / `approved` / `rejected` / `expired`, verbatim. */
  decision: string;
  decision_reason: string | null;
  expires_at: number | null;
  /** The recorded impact block, when the holding mod produced one. */
  impact: Record<string, unknown> | null;
}

export interface HoldList {
  total: number | null;
  holds: HoldRecord[] | null;
  /** Where the store is written. Single-process — the panel says so. */
  store_file: string | null;
}

/** What a hold decision recorded. The store's own row, never an inference. */
export interface HoldDecisionResult {
  hold: HoldRecord | null;
}

/**
 * A bounded, read-only estimate of what a tool call would touch.
 *
 * `measurable: false` is the load-bearing value: it means the preview could not
 * be computed, never that nothing is at stake.
 */
export interface ImpactPreview {
  kind: string;
  /** `true` measured · `false` could not compute · `null` not reported. */
  measurable: boolean | null;
  summary: string;
  affected_paths: string[] | null;
  truncated: boolean | null;
  estimated_bytes: number | null;
  reason: string | null;
  evidence: Record<string, unknown> | null;
}

export interface AuditQuery {
  limit?: number;
  event_name?: string;
  mod_name?: string;
  outcome?: string;
}

export interface HoldQuery {
  decision?: string;
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

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

/**
 * A list of strings, or `null`.
 *
 * A non-array is `null` ("sent no list"), and so is an array carrying anything
 * that is not a string: a half-readable list is an unreadable answer, and
 * silently dropping the odd element would present a *filtered* list as the one
 * the server sent.
 */
function optStrList(v: unknown): string[] | null {
  if (!Array.isArray(v)) return null;
  if (v.some((x) => typeof x !== "string")) return null;
  return v as string[];
}

/** A counter table, or `null` — same unreadable-rule as `optStrList`. */
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

function optRecord(v: unknown): Rec | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : null;
}

/** Map one chain row. Pure; exported for tests. */
export function mapChainRow(raw: unknown): ChainRow {
  const c = rec(raw);
  return {
    order: optNum(c.order),
    mod: str(c.mod),
    version: optStr(c.version),
    priority: optNum(c.priority),
    first_party: optBool(c.first_party),
    subscribed_events: optStrList(c.subscribed_events),
  };
}

/** Map one mod description, including the `{"name","error"}` variant. Pure. */
export function mapModDescription(raw: unknown): ModDescription {
  const m = rec(raw);
  return {
    name: str(m.name),
    version: optStr(m.version),
    description: optStr(m.description),
    priority: optNum(m.priority),
    first_party: optBool(m.first_party),
    granted_capabilities: optStrList(m.granted_capabilities),
    required_capabilities: optStrList(m.required_capabilities),
    subscribed_events: optStrList(m.subscribed_events),
    declared: optRecord(m.declared),
    observed: optRecord(m.observed),
    discrepancies: optStrList(m.discrepancies),
    error: optStr(m.error),
  };
}

/** Map the `GET /mods` envelope. Pure; exported for tests. */
export function mapFleet(raw: unknown): ModFleet {
  const f = rec(raw);
  return {
    total: optNum(f.total),
    chain: Array.isArray(f.chain) ? f.chain.map(mapChainRow) : null,
    mods: Array.isArray(f.mods) ? f.mods.map(mapModDescription) : null,
  };
}

/** Map one projected mod command. Pure; exported for tests. */
export function mapModCommand(raw: unknown): ModCommand {
  const c = rec(raw);
  return {
    name: str(c.name),
    mod_name: str(c.mod_name),
    description: str(c.description),
    // The wire field is a plain `bool` with a `False` default, so it is always
    // present; an absent one is still `false` rather than `null`, because this
    // flag decides whether the panel may offer a Run button at all and "the
    // Gateway sent no flag" would leave the button's state unknown.
    requires_approval: c.requires_approval === true,
    registered_at: optNum(c.registered_at),
  };
}

/** Map the `GET /mods/commands` envelope. Pure; exported for tests. */
export function mapModCommandList(raw: unknown): ModCommandList {
  const l = rec(raw);
  return {
    total: optNum(l.total),
    commands: Array.isArray(l.commands) ? l.commands.map(mapModCommand) : null,
  };
}

/** Map a `POST /mods/commands/{name}` response. Pure; exported for tests. */
export function mapModCommandResult(raw: unknown): ModCommandResult {
  const r = rec(raw);
  return {
    command: str(r.command),
    mod_name: optStr(r.mod_name),
    status: optStr(r.status),
    output: optStr(r.output),
    requires_approval: optBool(r.requires_approval),
  };
}

/** Map one audit record. Pure; exported for tests. */
export function mapAuditEntry(raw: unknown): AuditEntry {
  const e = rec(raw);
  const rewrites = Array.isArray(e.rewrites) ? e.rewrites : null;
  return {
    timestamp: optNum(e.timestamp),
    event: str(e.event),
    event_id: optStr(e.event_id),
    source: optStr(e.source),
    outcome: str(e.outcome),
    reason: optStr(e.reason),
    duration_ms: optNum(e.duration_ms),
    chain: optStrList(e.chain),
    payload_digest: optStr(e.payload_digest),
    rewrites: rewrites === null ? null : rewrites.map((r) => ({ mod: optStr(rec(r).mod), reason: optStr(rec(r).reason) })),
  };
}

/** Map the audit aggregate block. Pure; exported for tests. */
export function mapAuditStats(raw: unknown): AuditStats | null {
  if (raw === null || raw === undefined) return null;
  const s = rec(raw);
  return {
    retained: optNum(s.retained),
    capacity: optNum(s.capacity),
    events: optNum(s.events),
    by_mod: optCounts(s.by_mod),
    by_outcome: optCounts(s.by_outcome),
  };
}

/** Map the `GET /mods/audit` envelope. Pure; exported for tests. */
export function mapAuditPage(raw: unknown): AuditPage {
  const p = rec(raw);
  return {
    total: optNum(p.total),
    entries: Array.isArray(p.entries) ? p.entries.map(mapAuditEntry) : null,
    stats: mapAuditStats(p.stats),
  };
}

/** Map one hold record. Pure; exported for tests. */
export function mapHold(raw: unknown): HoldRecord {
  const h = rec(raw);
  return {
    hold_id: str(h.hold_id),
    idempotency_key: str(h.idempotency_key),
    tool_name: str(h.tool_name),
    risk_level: str(h.risk_level),
    reason: str(h.reason),
    run_id: str(h.run_id),
    tool_call_id: str(h.tool_call_id),
    created_at: optNum(h.created_at),
    decided_at: optNum(h.decided_at),
    decided_by: optStr(h.decided_by),
    decision: str(h.decision),
    decision_reason: optStr(h.decision_reason),
    expires_at: optNum(h.expires_at),
    impact: optRecord(h.impact),
  };
}

/** Map the `GET /mods/holds` envelope. Pure; exported for tests. */
export function mapHoldList(raw: unknown): HoldList {
  const l = rec(raw);
  return {
    total: optNum(l.total),
    holds: Array.isArray(l.holds) ? l.holds.map(mapHold) : null,
    store_file: optStr(l.store_file),
  };
}

/** Map a hold decision response. Pure; exported for tests. */
export function mapHoldDecision(raw: unknown): HoldDecisionResult {
  const r = rec(raw);
  return { hold: r.hold === undefined || r.hold === null ? null : mapHold(r.hold) };
}

/** Map `POST /mods/preview`. Pure; exported for tests. */
export function mapImpactPreview(raw: unknown): ImpactPreview {
  const p = rec(raw);
  return {
    kind: str(p.kind),
    measurable: optBool(p.measurable),
    summary: str(p.summary),
    affected_paths: optStrList(p.affected_paths),
    truncated: optBool(p.truncated),
    estimated_bytes: optNum(p.estimated_bytes),
    reason: optStr(p.reason),
    evidence: optRecord(p.evidence),
  };
}

/**
 * Read one declared-or-observed list (`hooks`, `calls`, `state_reads`, …).
 *
 * Pure projection of a list the server already sent — never a re-derivation, so
 * a missing key reads `null` rather than `[]`, which would claim the manifest
 * declared nothing.
 */
export function manifestList(record: Rec | null, key: string): string[] | null {
  if (record === null) return null;
  return optStrList(record[key]);
}

// ---------------------------------------------------------------------------
// Request shaping
// ---------------------------------------------------------------------------

function clamp(limit: number | null | undefined, fallback: number, max: number): number {
  if (typeof limit !== "number" || !Number.isFinite(limit)) return fallback;
  return Math.min(max, Math.max(1, Math.round(limit)));
}

/** Clamp onto the audit route's own `ge=1, le=1000` window. */
export function clampAuditLimit(limit: number | null | undefined): number {
  return clamp(limit, AUDIT_LIMIT_DEFAULT, AUDIT_LIMIT_MAX);
}

/** Clamp onto the holds route's own `ge=1, le=500` window. */
export function clampHoldsLimit(limit: number | null | undefined): number {
  return clamp(limit, HOLDS_LIMIT_DEFAULT, HOLDS_LIMIT_MAX);
}

/**
 * Refuse a decision filter the store cannot hold, naming the allowed set.
 *
 * The router accepts any string and an unknown one matches nothing, so a typo
 * would return an empty list that reads as "no holds are waiting" — the exact
 * fabricated absence this client refuses to produce. Throwing here names the
 * set before a round trip instead of after one.
 */
export function assertHoldDecision(decision: string | undefined): void {
  if (decision === undefined || decision === "") return;
  if (!(HOLD_DECISIONS as readonly string[]).includes(decision)) {
    throw new Error(`unknown hold decision ${JSON.stringify(decision)}; expected one of ${JSON.stringify([...HOLD_DECISIONS])}`);
  }
}

/** `GET /mods/audit?…`, with the limit always present and clamped. */
export function buildAuditPath(query: AuditQuery = {}): string {
  const params = new URLSearchParams();
  const put = (key: string, value: string | undefined): void => {
    if (value !== undefined && value !== "") params.set(key, value);
  };
  put("event_name", query.event_name);
  put("mod_name", query.mod_name);
  put("outcome", query.outcome);
  params.set("limit", String(clampAuditLimit(query.limit)));
  return `/mods/audit?${params.toString()}`;
}

/** `GET /mods/holds?…`, with the decision validated and the limit clamped. */
export function buildHoldsPath(query: HoldQuery = {}): string {
  assertHoldDecision(query.decision);
  const params = new URLSearchParams();
  if (query.decision !== undefined && query.decision !== "") params.set("decision", query.decision);
  params.set("limit", String(clampHoldsLimit(query.limit)));
  return `/mods/holds?${params.toString()}`;
}

/**
 * Refuse a hold reason the server would 422, naming the bound.
 *
 * Measured on the **raw** string, exactly as Pydantic measures it — trimming
 * here would let a whitespace-only reason through a local check the server
 * rejects anyway.
 */
export function assertHoldReason(reason: string): void {
  if (reason.length > HOLD_REASON_MAX_LENGTH) {
    throw new Error(`reason must be at most ${HOLD_REASON_MAX_LENGTH} characters (got ${reason.length})`);
  }
}

/** Refuse a verdict verb the router does not have, naming the two it does. */
export function assertHoldVerb(verb: string): asserts verb is HoldVerb {
  if (!(HOLD_VERBS as readonly string[]).includes(verb)) {
    throw new Error(`unknown hold verb ${JSON.stringify(verb)}; expected one of ${JSON.stringify([...HOLD_VERBS])}`);
  }
}

/**
 * The sentence a failure renders.
 *
 * `errMsg` swaps a 401/403/404 detail for a generic sentence — correct for an
 * incidental call, wrong here: an admin refusal, a missing audit mod's `503`
 * and an approval-gated command's `409` each carry the server's own words, and
 * those *are* the answer. So an `Error` carrying a numeric status keeps its
 * message verbatim and everything else falls back to the shared helper.
 */
export function failureText(err: unknown): string {
  if (err instanceof Error && err.message && typeof (err as { status?: unknown }).status === "number") return err.message;
  return errMsg(err);
}

// ---------------------------------------------------------------------------
// Expiry — the field beside `decision`, never inside it
// ---------------------------------------------------------------------------

export interface HoldExpiryView {
  /** A display sentence about `expires_at`, never about whether work may run. */
  label: string;
  tone: "gray" | "amber";
  /** True only when the hold carries a stamp and *this* clock has passed it. */
  past: boolean;
}

/**
 * Read `expires_at` beside the decision.
 *
 * The store deliberately keeps `decision: "approved"` on a hold whose TTL has
 * elapsed — expiry is time-based and voids the verdict rather than rewriting it
 * — so a panel that rendered only `decision` would show "approved" for a hold
 * that can no longer release anything. This reads the timestamp too, against
 * the **browser's** clock, and says so: it never gates a control, because the
 * store is the only authority on whether a hold is expired.
 *
 * `now` is injectable so the branches are testable without sleeping.
 */
export function holdExpiryView(hold: HoldRecord, now: number = Date.now()): HoldExpiryView {
  const expiresAt = hold.expires_at;
  if (expiresAt === null || expiresAt <= 0) {
    return { label: "no expiry reported", tone: "gray", past: false };
  }
  const stamp = absoluteStamp(expiresAt * 1000);
  if (stamp === null) return { label: "expiry time unreadable", tone: "gray", past: false };
  if (now >= expiresAt * 1000) return { label: "expiry time passed", tone: "amber", past: true };
  return { label: `expires ${stamp}`, tone: "gray", past: false };
}

/** How long ago a hold was created, or `null` when nothing was recorded. */
export function holdAge(hold: HoldRecord, now: number = Date.now()): string | null {
  if (hold.created_at === null) return null;
  return relTime(hold.created_at * 1000, now);
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

/** `GET /mods` — the fleet, its ordered chain, and every mod's description. */
export async function fetchModFleet(): Promise<ModFleet> {
  return mapFleet(await get<unknown>("/mods"));
}

/** `GET /mods/commands` — the commands mods contributed. */
export async function fetchModCommands(): Promise<ModCommandList> {
  return mapModCommandList(await get<unknown>("/mods/commands"));
}

/**
 * `POST /mods/commands/{name}` — run one mod command now (no model turn).
 *
 * The body is the router's `ModCommandRunRequest`, so the caller's payload goes
 * under `payload` verbatim. An unknown command is `404` (so a caller can fall
 * through to the ordinary registry) and a command declaring
 * `requires_approval` is refused `409` — this route has no approval flow.
 */
export async function runModCommand(name: string, payload: Record<string, unknown> = {}): Promise<ModCommandResult> {
  return mapModCommandResult(await send<unknown>(`/mods/commands/${encodeURIComponent(name)}`, "POST", { payload }));
}

/** `GET /mods/audit?…` — recent records plus the retained-window counters. */
export async function fetchModAudit(query: AuditQuery = {}): Promise<AuditPage> {
  return mapAuditPage(await get<unknown>(buildAuditPath(query)));
}

/** `GET /mods/holds?…` — held actions, newest first. */
export async function fetchModHolds(query: HoldQuery = {}): Promise<HoldList> {
  return mapHoldList(await get<unknown>(buildHoldsPath(query)));
}

/**
 * `POST /mods/holds/{hold_id}/approve|reject` — record one operator decision.
 *
 * Admin-only server-side; the operator recorded is the authenticated caller,
 * never a body-supplied name. This client sends the reason and surfaces the
 * server's 403/404 verbatim. Local refusals (an over-long reason, an unknown
 * verb) throw before any request is made.
 */
export async function decideHold(holdId: string, verb: HoldVerb, reason: string): Promise<HoldDecisionResult> {
  assertHoldVerb(verb);
  assertHoldReason(reason);
  const raw = await send<unknown>(`/mods/holds/${encodeURIComponent(holdId)}/${verb}`, "POST", { reason });
  return mapHoldDecision(raw);
}

/**
 * `POST /mods/preview` — a bounded estimate of what a tool call would touch.
 *
 * **This never executes the tool.** It is a read-only walk and a bounded parse;
 * `measurable: false` means the preview could not be computed, never that
 * nothing is at stake.
 */
export async function previewImpact(toolName: string, toolArgs: Record<string, unknown> = {}): Promise<ImpactPreview> {
  if (toolName.trim() === "") throw new Error("tool_name must be a non-empty tool name");
  return mapImpactPreview(await send<unknown>("/mods/preview", "POST", { tool_name: toolName, tool_args: toolArgs }));
}
