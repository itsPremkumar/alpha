import { errMsg, get, send } from "./http";

/**
 * The recursive-harness-improvement client — `/api/harness/*`.
 *
 * This is the browser half of the Harness Lab. The backend is
 * `app/gateway/routers/harness.py`, the operator surface over
 * `alpha.harness_improvement` (the Harness²-inspired method): one improvement
 * run snapshots the editable harness as an immutable bundle, probes candidate
 * diffs, contrasts them against the baseline, composes a successor, and
 * **never promotes anything itself** — promotion is a separate gated ladder
 * this client can only *ask* the server to advance.
 *
 * The rules, each with a tempting wrong reading:
 *
 * 1. **Absent is `null`, never `0`, `""` or `false`.** A count the Gateway did
 *    not send reads *not reported*, not `0` — "the ledger has no spend" and
 *    "the ledger could not report spend" lead to opposite actions. A missing
 *    list is `null`, not `[]`: "the list did not arrive" and "the list arrived
 *    empty" are different facts.
 * 2. **Server enum strings are preserved verbatim.** `stage`,
 *    `promotion_state`, `scope`, `status`, `finding` and the ladder map are
 *    typed `string`, so a value from a newer Gateway renders as itself rather
 *    than being snapped to something this build knows.
 * 3. **The client never computes ladder truth.** Whether a run may resume, a
 *    candidate may be promoted, or a refusal is substantive is the server's
 *    answer: `resumable` is read from the payload, and every approve / activate
 *    / rollback refusal is rendered verbatim. There is deliberately **no local
 *    terminal-stage list and no local copy of the ladder** — the ladder is read
 *    from `GET /promotions`, and the promotion gates are never re-derived here.
 * 4. **The feature flag splits run/advance writes from stop-or-restore
 *    writes.** While `harness_improvement.enabled` is false, `start`, `resume`,
 *    `approve` and `activate` answer `503 harness_improvement_disabled` (the
 *    reason names the config key), while `cancel`, `reject` and `rollback` stay
 *    reachable so the kill switch cannot trap work in flight or a bad active
 *    bundle. The client sends the request and surfaces whichever answer comes
 *    back — it never pre-refuses on its own guess about the flag.
 * 5. **Bounds are mirrored, not tightened.** `limit` mirrors the route's own
 *    `ge`/`le` window and falls back to the server default for garbage; the one
 *    local refusal is an approval kind outside the fixed privileged-name
 *    vocabulary (`scripts` / `middleware`), because sending it would only earn
 *    a 422 naming the same set.
 * 6. **`failureText` keeps a refusal verbatim.** `errMsg` replaces a
 *    401/403/404 detail with a generic sentence, which is right for an
 *    incidental failure and wrong for a promotion refusal — the server's words
 *    ("no operator approval is recorded …", "the regression suite has not been
 *    run") are the whole answer.
 *
 * Production posture the UI must render, not hide: the Gateway wires **no
 * executor, no editor and no baseline provider**, so `GET /status` carries a
 * `harness_capability` row (`state: "absent"`, `executes: false`) naming the
 * missing wiring, and a start is refused with `409 harness_baseline_absent`.
 * That is a deliberate trust boundary, not an unfinished edge.
 *
 * Coverage: `src/lib/harness-lab.test.mjs` (routes, verbs, the null-preserving
 * mappers, the request shaping, the local refusals) and
 * `src/lib/harness-lab-view.test.mjs` (the four-place wiring and the honesty
 * pins over `HarnessLabSection.tsx`).
 */

// ---------------------------------------------------------------------------
// Bounds — the router's own Query windows, mirrored
// ---------------------------------------------------------------------------

/** `GET /runs` bounds (`Query(ge=1, le=500)`, default 50). */
export const HARNESS_RUN_LIMIT_DEFAULT = 50;
export const HARNESS_RUN_LIMIT_MAX = 500;

/** `GET /runs/{id}/events` bounds (`Query(ge=1, le=2000)`, default 500). */
export const HARNESS_EVENT_LIMIT_DEFAULT = 500;
export const HARNESS_EVENT_LIMIT_MAX = 2000;

/** `GET /experiences` bounds (`Query(ge=1, le=500)`, default 100). */
export const HARNESS_EXPERIENCE_LIMIT_DEFAULT = 100;
export const HARNESS_EXPERIENCE_LIMIT_MAX = 500;

/** `GET /budgets` bounds (`Query(ge=1, le=2000)`, default 200). */
export const HARNESS_BUDGET_LIMIT_DEFAULT = 200;
export const HARNESS_BUDGET_LIMIT_MAX = 2000;

/** The request-body text bounds the server enforces (`Field(max_length=…)`). */
export const HARNESS_REASON_MAX_LENGTH = 2000;
export const HARNESS_TASK_MAX_LENGTH = 20000;
export const HARNESS_OUTCOME_MAX_LENGTH = 4000;
export const HARNESS_RUN_ID_MAX_LENGTH = 128;

/**
 * The privileged component kinds an operator approval may grant.
 *
 * This vocabulary is the server's, not ours: `evaluate_promotion` raises
 * `required_approvals` for `scripts` and `middleware` only (permission
 * components are outside the editable surface, so that flag can never fire).
 * A request granting anything else is refused by the server with
 * `422 harness_unexpected_approvals`; the local check refuses the same set
 * before a round trip, and the server remains the authority on what is
 * *pending* for a given candidate.
 */
export const HARNESS_APPROVAL_KINDS = ["scripts", "middleware"] as const;
export type HarnessApprovalKind = (typeof HARNESS_APPROVAL_KINDS)[number];

// ---------------------------------------------------------------------------
// Types — the wire projection, mapped with nulls preserved
// ---------------------------------------------------------------------------

/** One missing wiring seam, as `/status` reports it. */
export interface HarnessMissingWiring {
  wiring_point: string;
  why_absent: string;
}

/**
 * The machine-readable execution-plane posture. `executes: false` in every
 * shipped Gateway; `missing_wiring` names the seams a deployment would add.
 */
export interface HarnessCapability {
  capability: string | null;
  state: string | null;
  executes: boolean | null;
  /** `null` when the Gateway sent no list — not "wiring is complete". */
  missing_wiring: HarnessMissingWiring[] | null;
}

/** The store's readability plus bounded counts. */
export interface HarnessStoreView {
  root: string | null;
  readable: boolean | null;
  /** `null` when no counts block arrived; never `{}` (an empty one is a claim). */
  counts: Record<string, number> | null;
  unreadable: string[] | null;
  reason: string | null;
}

/** The active-pointer shape shared by `/status` and `/promotions`. */
export interface HarnessActivePointer {
  bundle_id: string;
  previous_bundle_id: string | null;
  activated_at: string | null;
  actor: string | null;
  reason: string | null;
}

/**
 * `/status`'s pointer, plus the manifest (or the reason it could not be read).
 * `manifest_error` is the pointer answering with a degraded store — never
 * silently absent.
 */
export interface HarnessStatusActiveBundle extends HarnessActivePointer {
  manifest: HarnessBundleRow | null;
  manifest_error: string | null;
}

/** One component reference on a bundle manifest. */
export interface HarnessComponentRef {
  component_id: string;
  kind: string;
  sha256: string | null;
  size_bytes: number | null;
}

/** One bundle manifest as the list projects it. */
export interface HarnessBundleRow {
  bundle_id: string;
  parent_bundle_id: string | null;
  created_at: string | null;
  created_by: string | null;
  agent_profile: string | null;
  /** Preserved verbatim; the ladder is read from `/promotions`. */
  promotion_state: string;
  component_count: number | null;
  components: HarnessComponentRef[] | null;
  alpha_runtime_version: string | null;
  tool_schema_version: string | null;
  policy_version: string | null;
  /** The run whose cycle produced this bundle; `null` for a baseline snapshot. */
  source_run_id: string | null;
  source_task_type: string | null;
}

/** The counts block on a run timeline row. `null` when it did not arrive whole. */
export interface HarnessRunCounts {
  checkpoints: number;
  proposals: number;
  validations: number;
  accepted_validations: number;
  probes: number;
  contrasts: number;
  experiences: number;
}

/** One bounded run timeline row (`GET /runs`). */
export interface HarnessRunRow {
  run_id: string;
  created_at: string | null;
  updated_at: string | null;
  stage: string;
  mode: string | null;
  created_by: string | null;
  task_fingerprint: string | null;
  task_type: string | null;
  outcome: string | null;
  base_bundle_id: string | null;
  successor_bundle_id: string | null;
  blocked_reason: string | null;
  error: string | null;
  cancel_requested: boolean | null;
  /** The server's own answer to "may this be resumed" — never re-derived here. */
  resumable: boolean | null;
  counts: HarnessRunCounts | null;
  notes: string[] | null;
}

/** The run list envelope. `runs: null` means no list arrived. */
export interface HarnessRunList {
  runs: HarnessRunRow[] | null;
  unreadable: string[] | null;
  complete: boolean | null;
  durability: string | null;
}

/** One journal line from a run's append-only JSONL journal. */
export interface HarnessEventRow {
  event: string | null;
  at: string | null;
  stage: string | null;
  reason: string | null;
  /** Any fields beyond the four above, verbatim — e.g. `candidate_id`. */
  extra: Record<string, unknown> | null;
}

/** One stage transition recorded inside the run record. */
export interface HarnessStageEventRow {
  event: string | null;
  stage: string | null;
  at: string | null;
  reason: string | null;
}

/**
 * The run record, projected to the fields this UI renders.
 *
 * It is the deep durable record; the projection keeps the task identity, the
 * terminal reasons, the notes, the transition history and the opaque
 * budget/composition/deliverable blocks, all null-preserving.
 */
export interface HarnessRunRecord {
  run_id: string | null;
  stage: string | null;
  mode: string | null;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  base_bundle_id: string | null;
  successor_bundle_id: string | null;
  task_text: string | null;
  task_outcome: string | null;
  task_type: string | null;
  task_fingerprint: string | null;
  blocked_reason: string | null;
  cancel_reason: string | null;
  cancel_requested: boolean | null;
  error: string | null;
  notes: string[] | null;
  history: HarnessStageEventRow[] | null;
  /** The stage names that carry a completed checkpoint. */
  checkpoint_stages: string[] | null;
  proposal_count: number | null;
  validation_count: number | null;
  probe_outcome_count: number | null;
  contrast_count: number | null;
  experience_count: number | null;
  budget_state: Record<string, unknown> | null;
  composition: Record<string, unknown> | null;
  deliverable: Record<string, unknown> | null;
  alpha_runtime_version: string | null;
  tool_schema_version: string | null;
  policy_version: string | null;
  source_task_type: string | null;
}

/** `GET /runs/{id}` — the full record plus its journal tail. */
export interface HarnessRunDetail {
  run: HarnessRunRecord | null;
  events: HarnessEventRow[] | null;
  unreadable_event_lines: string[] | null;
  resumable: boolean | null;
  next_stage: string | null;
  durability: string | null;
}

/** `GET /runs/{id}/events` — the journal with its own presence disclosure. */
export interface HarnessRunEvents {
  run_id: string | null;
  run_record_present: boolean | null;
  stage: string | null;
  events: HarnessEventRow[] | null;
  unreadable: string[] | null;
  limit: number | null;
}

/** The confirmation `start` / `resume` return. */
export interface HarnessRunReceipt {
  run_id: string | null;
  stage: string | null;
  resumable: boolean | null;
  next_stage: string | null;
  durability: string | null;
}

/** The confirmation `cancel` returns: durable request, applied at the boundary. */
export interface HarnessCancelReceipt extends HarnessRunReceipt {
  cancel_requested: boolean | null;
  applied_at_next_boundary: boolean | null;
  note: string | null;
}

/** `GET /bundles`. `bundles: null` means no list arrived. */
export interface HarnessBundleList {
  bundles: HarnessBundleRow[] | null;
  unreadable: string[] | null;
  complete: boolean | null;
  active_bundle: HarnessActivePointer | null;
}

/** A component body as `GET /bundles/{id}` redacts it on the way out. */
export interface HarnessComponentBody extends HarnessComponentRef {
  /** `null` when the body was redacted away or never stored. */
  content: string | null;
  /** `true` when the manifest references a blob this store does not hold. */
  missing: boolean | null;
}

/** `GET /bundles/{id}` — one manifest plus its component bodies. */
export interface HarnessBundleDetail {
  bundle: HarnessBundleRow | null;
  components: HarnessComponentBody[] | null;
  redactions: string[] | null;
  note: string | null;
}

/** One modified-component row in a diff. */
export interface HarnessModifiedComponent {
  component_id: string;
  kind: string | null;
  before_sha256: string | null;
  after_sha256: string | null;
  size_delta: number | null;
}

/** The component-level diff between two manifests. */
export interface HarnessBundleDiff {
  base_bundle_id: string | null;
  candidate_bundle_id: string | null;
  added: string[] | null;
  removed: string[] | null;
  modified: HarnessModifiedComponent[] | null;
  unchanged: string[] | null;
}

/** The diff bodies the route sends when `include_contents` is on. */
export interface HarnessDiffContents {
  added: Record<string, string | null> | null;
  modified: Record<string, { before: string | null; after: string | null }> | null;
}

/** `GET /bundles/{id}/diff`. */
export interface HarnessDiffDetail {
  diff: HarnessBundleDiff | null;
  contents: HarnessDiffContents | null;
  redactions: string[] | null;
  note: string | null;
}

/** One experience-library entry, with its computed staleness. */
export interface HarnessExperienceRow {
  experience_id: string;
  title: string | null;
  lesson: string | null;
  /** `"benefit"` | `"regression"`, verbatim. */
  finding: string | null;
  scope: string | null;
  task_type: string | null;
  status: string | null;
  domain_tags: string[] | null;
  component_kinds: string[] | null;
  run_ids: string[] | null;
  benefit: string | null;
  counter_evidence: string | null;
  created_at: string | null;
  last_reviewed_at: string | null;
  review_condition: string | null;
  expires_at: string | null;
  superseded_by: string | null;
  /** The server's computed staleness reasons; `null` when not reported. */
  staleness: string[] | null;
}

/** `GET /experiences`. */
export interface HarnessExperienceList {
  experiences: HarnessExperienceRow[] | null;
  unreadable: string[] | null;
  complete: boolean | null;
  total: number | null;
  limit: number | null;
}

/** `GET /experiences/{id}` — one entry plus its Markdown body. */
export interface HarnessExperienceDetail {
  experience: HarnessExperienceRow | null;
  markdown: string | null;
  staleness: string[] | null;
  redactions: string[] | null;
}

/** One durable promotion-log row. */
export interface HarnessPromotionEvent {
  event: string | null;
  bundle_id: string | null;
  previous_bundle_id: string | null;
  actor: string | null;
  reason: string | null;
  at: string | null;
  /** Privileged-kind approvals the event granted, when the row recorded them. */
  granted: string[] | null;
}

/** `GET /promotions` — the log, the pointer, and the ladder vocabulary. */
export interface HarnessPromotionHistory {
  events: HarnessPromotionEvent[] | null;
  unreadable: string[] | null;
  pointer: HarnessActivePointer | null;
  /** A corrupt pointer is a degraded read: named, never an absent pointer. */
  pointer_error: string | null;
  /** `{from_state: next_state}` — read from the server, never pinned here. */
  ladder: Record<string, string> | null;
  durability: string | null;
}

/** One measured budget-ledger row. An unreported dimension stays `null`. */
export interface HarnessBudgetRow {
  run_id: string | null;
  stage: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  elapsed_ms: number | null;
  recorded_at: string | null;
  ceilings: Record<string, unknown> | null;
}

/** `GET /budgets` — the ceilings plus what was measured against them. */
export interface HarnessBudgets {
  ceilings: Record<string, unknown> | null;
  ledger: HarnessBudgetRow[] | null;
  unreadable: string[] | null;
  complete: boolean | null;
  measured_model_calls: number | null;
  note: string | null;
  durability: string | null;
}

/** `POST /candidates/{id}/approve`'s confirmation. */
export interface HarnessApprovalReceipt {
  approved: boolean | null;
  bundle_id: string | null;
  note: string | null;
}

/** `POST /candidates/{id}/reject`'s confirmation. */
export interface HarnessRejectionReceipt {
  rejected: boolean | null;
  bundle_id: string | null;
  promotion_state: string | null;
}

/** `POST /active/{id}/activate`'s confirmation. */
export interface HarnessActivationReceipt {
  activated: boolean | null;
  bundle_id: string | null;
  previous_bundle_id: string | null;
  note: string | null;
}

/** `POST /active/{id}/rollback`'s confirmation. */
export interface HarnessRollbackReceipt {
  rolled_back: boolean | null;
  restored_bundle_id: string | null;
  from_bundle_id: string | null;
  note: string | null;
}

/** One deliverable check on a run request. */
export interface HarnessDeliverableCheck {
  name: string;
  path: string;
  kind?: string;
  detail?: string;
  sections?: string[];
  min_bytes?: number;
}

/** `POST /runs`'s body. `task_fingerprint` is deliberately not a field: the
 * server computes and salts it, so a caller cannot choose the identity a
 * cross-task summary is keyed on. */
export interface HarnessRunRequest {
  task: string;
  outcome: string;
  deliverables?: string[];
  checks?: HarnessDeliverableCheck[];
  allowed_tools?: string[];
  domain_tags?: string[];
  task_type?: string;
  uncertainties?: string[];
  run_id?: string;
}

/** `GET /experiences`'s filters. Empty strings are omitted, not sent. */
export interface HarnessExperienceQuery {
  task_type?: string;
  scope?: string;
  status?: string;
  domain_tag?: string[];
  limit?: number;
}

/** `GET /budgets`'s filters. */
export interface HarnessBudgetQuery {
  run_id?: string;
  limit?: number;
}

/** `GET /bundles/{id}/diff`'s options. */
export interface HarnessDiffOptions {
  /** Diff against an explicit bundle; omitted, the server uses the parent. */
  base?: string;
  /** `false` asks the server to skip bodies; omitted, the server sends them. */
  includeContents?: boolean;
}

// ---------------------------------------------------------------------------
// Mapping — verbatim where the server spoke, null where it did not
// ---------------------------------------------------------------------------

type Rec = Record<string, unknown>;

function rec(v: unknown): Rec {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {};
}

function optRec(v: unknown): Rec | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : null;
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
 * A string array, or `null`.
 *
 * `null` covers "key absent" and "not an array"; a mixed array with a hole is
 * also `null` — a list with an unreadable entry is not a partial answer to
 * "what did the server report", it is an unreadable one.
 */
function optStrs(v: unknown): string[] | null {
  if (!Array.isArray(v)) return null;
  return v.every((item) => typeof item === "string") ? (v as string[]) : null;
}

/**
 * A record table, or `null` — strict for the same reason `optCounts` in
 * `side-effects.ts` is: a half-readable table must not read as a whole answer.
 */
function optRecord(v: unknown): Record<string, unknown> | null {
  return optRec(v);
}

/** The run-counts block, `null` unless every key is a finite number. */
function mapRunCounts(v: unknown): HarnessRunCounts | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const counts = v as Rec;
  const checkpoints = optNum(counts.checkpoints);
  const proposals = optNum(counts.proposals);
  const validations = optNum(counts.validations);
  const acceptedValidations = optNum(counts.accepted_validations);
  const probes = optNum(counts.probes);
  const contrasts = optNum(counts.contrasts);
  const experiences = optNum(counts.experiences);
  if (checkpoints === null || proposals === null || validations === null || acceptedValidations === null || probes === null || contrasts === null || experiences === null) return null;
  return { checkpoints, proposals, validations, accepted_validations: acceptedValidations, probes, contrasts, experiences };
}

/** Map one run timeline row. Pure; exported for tests. */
export function mapRunRow(raw: unknown): HarnessRunRow {
  const r = rec(raw);
  return {
    run_id: optStr(r.run_id) ?? "",
    created_at: optStr(r.created_at),
    updated_at: optStr(r.updated_at),
    stage: optStr(r.stage) ?? "",
    mode: optStr(r.mode),
    created_by: optStr(r.created_by),
    task_fingerprint: optStr(r.task_fingerprint),
    task_type: optStr(r.task_type),
    outcome: optStr(r.outcome),
    base_bundle_id: optStr(r.base_bundle_id),
    successor_bundle_id: optStr(r.successor_bundle_id),
    blocked_reason: optStr(r.blocked_reason),
    error: optStr(r.error),
    cancel_requested: optBool(r.cancel_requested),
    resumable: optBool(r.resumable),
    counts: mapRunCounts(r.counts),
    notes: optStrs(r.notes),
  };
}

/** Map one journal line. Pure; exported for tests. */
export function mapEventRow(raw: unknown): HarnessEventRow {
  const r = rec(raw);
  const known = new Set(["event", "at", "stage", "reason", "run_id"]);
  const extra: Rec = {};
  for (const [key, value] of Object.entries(r)) {
    if (!known.has(key)) extra[key] = value;
  }
  return {
    event: optStr(r.event),
    at: optStr(r.at),
    stage: optStr(r.stage),
    reason: optStr(r.reason),
    extra: Object.keys(extra).length > 0 ? extra : null,
  };
}

/** Map the run record projection. Pure; exported for tests. */
export function mapRunRecord(raw: unknown): HarnessRunRecord {
  const r = rec(raw);
  const task = rec(r.task);
  const compatibility = rec(r.compatibility);
  const checkpoints = optRec(r.checkpoints);
  const history = Array.isArray(r.history)
    ? r.history.map((row) => {
        const h = rec(row);
        return { event: optStr(h.event), stage: optStr(h.stage), at: optStr(h.at), reason: optStr(h.reason) };
      })
    : null;
  return {
    run_id: optStr(r.run_id),
    stage: optStr(r.stage),
    mode: optStr(r.mode),
    created_at: optStr(r.created_at),
    updated_at: optStr(r.updated_at),
    created_by: optStr(r.created_by),
    base_bundle_id: optStr(r.base_bundle_id),
    successor_bundle_id: optStr(r.successor_bundle_id),
    task_text: optStr(task.task),
    task_outcome: optStr(task.outcome),
    task_type: optStr(task.task_type),
    task_fingerprint: optStr(task.task_fingerprint),
    blocked_reason: optStr(r.blocked_reason),
    cancel_reason: optStr(r.cancel_reason),
    cancel_requested: optBool(r.cancel_requested),
    error: optStr(r.error),
    notes: optStrs(r.notes),
    history,
    checkpoint_stages: checkpoints === null ? null : Object.keys(checkpoints),
    proposal_count: Array.isArray(r.proposals) ? r.proposals.length : null,
    validation_count: Array.isArray(r.validations) ? r.validations.length : null,
    probe_outcome_count: Array.isArray(r.probe_outcomes) ? r.probe_outcomes.length : null,
    contrast_count: Array.isArray(r.contrasts) ? r.contrasts.length : null,
    experience_count: Array.isArray(r.experiences) ? r.experiences.length : null,
    budget_state: optRecord(r.budget_state),
    composition: optRecord(r.composition),
    deliverable: optRecord(r.deliverable),
    alpha_runtime_version: optStr(compatibility.alpha_runtime_version),
    tool_schema_version: optStr(compatibility.tool_schema_version),
    policy_version: optStr(compatibility.policy_version),
    source_task_type: optStr(r.source_task_type),
  };
}

/** Map one component reference. Pure; exported for tests. */
export function mapComponentRef(raw: unknown): HarnessComponentRef {
  const c = rec(raw);
  return {
    component_id: optStr(c.component_id) ?? "",
    kind: optStr(c.kind) ?? "",
    sha256: optStr(c.sha256),
    size_bytes: optNum(c.size_bytes),
  };
}

/** Map one bundle manifest row. Pure; exported for tests. */
export function mapBundleRow(raw: unknown): HarnessBundleRow {
  const b = rec(raw);
  const compatibility = rec(b.compatibility);
  const provenance = rec(b.provenance);
  return {
    bundle_id: optStr(b.bundle_id) ?? "",
    parent_bundle_id: optStr(b.parent_bundle_id),
    created_at: optStr(b.created_at),
    created_by: optStr(b.created_by),
    agent_profile: optStr(b.agent_profile),
    promotion_state: optStr(b.promotion_state) ?? "",
    component_count: optNum(b.component_count),
    components: Array.isArray(b.components) ? b.components.map(mapComponentRef) : null,
    alpha_runtime_version: optStr(compatibility.alpha_runtime_version),
    tool_schema_version: optStr(compatibility.tool_schema_version),
    policy_version: optStr(compatibility.policy_version),
    source_run_id: optStr(provenance.source_run_id),
    source_task_type: optStr(provenance.source_task_type),
  };
}

/** Map the active-pointer shape. Pure; exported for tests. */
export function mapActivePointer(raw: unknown): HarnessActivePointer {
  const p = rec(raw);
  return {
    bundle_id: optStr(p.bundle_id) ?? "",
    previous_bundle_id: optStr(p.previous_bundle_id),
    activated_at: optStr(p.activated_at),
    actor: optStr(p.actor),
    reason: optStr(p.reason),
  };
}

/** Map the `/status` pointer with its manifest disclosure. Pure; exported for tests. */
export function mapStatusActiveBundle(raw: unknown): HarnessStatusActiveBundle {
  const pointer = mapActivePointer(raw);
  const r = rec(raw);
  return {
    ...pointer,
    manifest: r.manifest === undefined || r.manifest === null ? null : mapBundleRow(r.manifest),
    manifest_error: optStr(r.manifest_error),
  };
}

/** Map `/status`. Pure; exported for tests. */
export function mapStatus(raw: unknown): HarnessStatus {
  const s = rec(raw);
  const capability = rec(s.harness_capability);
  const store = rec(s.store);
  return {
    enabled: optBool(s.enabled),
    enabled_note: optStr(s.enabled_note),
    mode: optStr(s.mode),
    settings: optRecord(s.settings),
    ceilings: optRecord(s.ceilings),
    promotion_policy: optRecord(s.promotion_policy),
    config_hash: optStr(s.config_hash),
    capability: s.harness_capability === undefined || s.harness_capability === null ? null : {
      capability: optStr(capability.capability),
      state: optStr(capability.state),
      executes: optBool(capability.executes),
      missing_wiring: Array.isArray(capability.missing_wiring)
        ? capability.missing_wiring.map((row) => {
            const w = rec(row);
            return { wiring_point: optStr(w.wiring_point) ?? "", why_absent: optStr(w.why_absent) ?? "" };
          })
        : null,
    },
    durability: optStr(s.durability),
    store: s.store === undefined || s.store === null ? null : {
      root: optStr(store.root),
      readable: optBool(store.readable),
      counts: (() => {
        const counts = optRec(store.counts);
        if (counts === null) return null;
        const out: Record<string, number> = {};
        for (const [key, value] of Object.entries(counts)) {
          const n = optNum(value);
          if (n === null) return null;
          out[key] = n;
        }
        return out;
      })(),
      unreadable: optStrs(store.unreadable),
      reason: optStr(store.reason),
    },
    active_bundle: s.active_bundle === undefined || s.active_bundle === null ? null : mapStatusActiveBundle(s.active_bundle),
  };
}

/** The `/status` payload. */
export interface HarnessStatus {
  enabled: boolean | null;
  enabled_note: string | null;
  mode: string | null;
  settings: Record<string, unknown> | null;
  ceilings: Record<string, unknown> | null;
  promotion_policy: Record<string, unknown> | null;
  config_hash: string | null;
  capability: HarnessCapability | null;
  durability: string | null;
  store: HarnessStoreView | null;
  active_bundle: HarnessStatusActiveBundle | null;
}

/** Map a run receipt (`start` / `resume`). Pure; exported for tests. */
export function mapRunReceipt(raw: unknown): HarnessRunReceipt {
  const r = rec(raw);
  const run = rec(r.run);
  return {
    run_id: optStr(run.run_id),
    stage: optStr(run.stage),
    resumable: optBool(r.resumable),
    next_stage: optStr(r.next_stage),
    durability: optStr(r.durability),
  };
}

/** Map a cancel receipt. Pure; exported for tests. */
export function mapCancelReceipt(raw: unknown): HarnessCancelReceipt {
  const r = rec(raw);
  return {
    ...mapRunReceipt(raw),
    cancel_requested: optBool(r.cancel_requested),
    applied_at_next_boundary: optBool(r.applied_at_next_boundary),
    note: optStr(r.note),
  };
}

// ---------------------------------------------------------------------------
// Request shaping
// ---------------------------------------------------------------------------

/** Clamp a count onto one of the route's own windows, falling back to its default. */
function clampCount(limit: number | null | undefined, fallback: number, max: number): number {
  if (typeof limit !== "number" || !Number.isFinite(limit)) return fallback;
  return Math.min(max, Math.max(1, Math.round(limit)));
}

/** Clamp a run-list limit onto `ge=1, le=500`. */
export function clampRunLimit(limit: number | null | undefined): number {
  return clampCount(limit, HARNESS_RUN_LIMIT_DEFAULT, HARNESS_RUN_LIMIT_MAX);
}

/** Clamp an event-journal limit onto `ge=1, le=2000`. */
export function clampEventLimit(limit: number | null | undefined): number {
  return clampCount(limit, HARNESS_EVENT_LIMIT_DEFAULT, HARNESS_EVENT_LIMIT_MAX);
}

/** Clamp an experience-list limit onto `ge=1, le=500`. */
export function clampExperienceLimit(limit: number | null | undefined): number {
  return clampCount(limit, HARNESS_EXPERIENCE_LIMIT_DEFAULT, HARNESS_EXPERIENCE_LIMIT_MAX);
}

/** Clamp a budget-ledger limit onto `ge=1, le=2000`. */
export function clampBudgetLimit(limit: number | null | undefined): number {
  return clampCount(limit, HARNESS_BUDGET_LIMIT_DEFAULT, HARNESS_BUDGET_LIMIT_MAX);
}

/**
 * Refuse a reason the server would 422, naming the bound.
 *
 * The length is the **raw** string's, exactly as Pydantic measures it — no
 * trimming, because trimming here and not there would let an over-long reason
 * with trailing spaces through this check and be refused by the server.
 */
export function assertReason(reason: string, label = "reason"): void {
  if (reason.length > HARNESS_REASON_MAX_LENGTH) {
    throw new Error(`${label} must be at most ${HARNESS_REASON_MAX_LENGTH} characters (got ${reason.length})`);
  }
}

/** Refuse a text field outside the server's `min_length`/`max_length` bounds. */
function assertText(value: string, label: string, max: number): void {
  if (value.length < 1 || value.length > max) {
    throw new Error(`${label} must be 1–${max} characters (got ${value.length})`);
  }
}

/** Refuse an approval kind the server does not accept, naming the allowed set. */
export function assertApprovalKinds(kinds: readonly string[]): void {
  for (const kind of kinds) {
    if (!(HARNESS_APPROVAL_KINDS as readonly string[]).includes(kind)) {
      throw new Error(`unknown approval kind ${JSON.stringify(kind)}; expected one of ${JSON.stringify([...HARNESS_APPROVAL_KINDS])}`);
    }
  }
}

/** Build `GET /runs?limit=…`. Exported for tests. */
export function buildRunsPath(limit?: number): string {
  return `/harness/runs?limit=${clampRunLimit(limit)}`;
}

/** Build `GET /runs/{id}/events?limit=…`. Exported for tests. */
export function buildEventsPath(runId: string, limit?: number): string {
  return `/harness/runs/${encodeURIComponent(runId)}/events?limit=${clampEventLimit(limit)}`;
}

/** Build `GET /experiences?…`, omitting empty filters and clamping `limit`. */
export function buildExperiencesPath(query: HarnessExperienceQuery = {}): string {
  const params = new URLSearchParams();
  const put = (key: string, value: string | undefined): void => {
    if (value !== undefined && value !== "") params.set(key, value);
  };
  put("task_type", query.task_type);
  put("scope", query.scope);
  put("status", query.status);
  for (const tag of query.domain_tag ?? []) {
    if (tag !== "") params.append("domain_tag", tag);
  }
  params.set("limit", String(clampExperienceLimit(query.limit)));
  return `/harness/experiences?${params.toString()}`;
}

/** Build `GET /budgets?…`, omitting an empty `run_id` and clamping `limit`. */
export function buildBudgetsPath(query: HarnessBudgetQuery = {}): string {
  const params = new URLSearchParams();
  if (query.run_id !== undefined && query.run_id !== "") params.set("run_id", query.run_id);
  params.set("limit", String(clampBudgetLimit(query.limit)));
  return `/harness/budgets?${params.toString()}`;
}

/** Build `GET /bundles/{id}/diff` with its optional query. Exported for tests. */
export function buildBundleDiffPath(bundleId: string, options: HarnessDiffOptions = {}): string {
  const params = new URLSearchParams();
  if (options.base !== undefined && options.base !== "") params.set("base", options.base);
  if (options.includeContents === false) params.set("include_contents", "false");
  const suffix = params.toString();
  return `/harness/bundles/${encodeURIComponent(bundleId)}/diff${suffix ? `?${suffix}` : ""}`;
}

/**
 * The sentence a failure renders.
 *
 * `errMsg` swaps a 401/403/404 detail for a generic sentence — correct for an
 * incidental call, wrong here: a promotion refusal carries the server's own
 * words ("no operator approval is recorded …", "the regression suite has not
 * been run"), and those *are* the answer. So an `Error` that carries a numeric
 * status keeps its message verbatim, and everything else falls back to the
 * shared helper.
 */
export function failureText(err: unknown): string {
  if (err instanceof Error && err.message && typeof (err as { status?: unknown }).status === "number") return err.message;
  return errMsg(err);
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

/** `GET /harness/status` — posture, ceilings, store readability, wiring. */
export async function fetchHarnessStatus(): Promise<HarnessStatus> {
  return mapStatus(await get<unknown>("/harness/status"));
}

/** `GET /harness/runs` — newest-first timeline rows; unreadable rows are named. */
export async function fetchHarnessRuns(limit?: number): Promise<HarnessRunList> {
  const raw = rec(await get<unknown>(buildRunsPath(limit)));
  return {
    runs: Array.isArray(raw.runs) ? raw.runs.map(mapRunRow) : null,
    unreadable: optStrs(raw.unreadable),
    complete: optBool(raw.complete),
    durability: optStr(raw.durability),
  };
}

/**
 * `POST /harness/runs` — start one improvement run.
 *
 * Admin-only server-side, and refused while the feature is disabled or when no
 * baseline exists; this client sends the body and surfaces the server's refusal
 * (the `409 harness_baseline_absent` detail names the missing wiring). Local
 * refusals are the text bounds the server would 422.
 */
export async function startHarnessRun(request: HarnessRunRequest): Promise<HarnessRunReceipt> {
  assertText(request.task, "task", HARNESS_TASK_MAX_LENGTH);
  assertText(request.outcome, "outcome", HARNESS_OUTCOME_MAX_LENGTH);
  if (request.run_id !== undefined) assertText(request.run_id, "run_id", HARNESS_RUN_ID_MAX_LENGTH);
  const payload: Rec = { task: request.task, outcome: request.outcome };
  if (request.deliverables !== undefined) payload.deliverables = request.deliverables;
  if (request.checks !== undefined) payload.checks = request.checks;
  if (request.allowed_tools !== undefined) payload.allowed_tools = request.allowed_tools;
  if (request.domain_tags !== undefined) payload.domain_tags = request.domain_tags;
  if (request.task_type !== undefined) payload.task_type = request.task_type;
  if (request.uncertainties !== undefined) payload.uncertainties = request.uncertainties;
  if (request.run_id !== undefined) payload.run_id = request.run_id;
  return mapRunReceipt(await send<unknown>("/harness/runs", "POST", payload));
}

/** `GET /harness/runs/{id}` — the full durable record plus its journal tail. */
export async function fetchHarnessRun(runId: string): Promise<HarnessRunDetail> {
  const raw = rec(await get<unknown>(`/harness/runs/${encodeURIComponent(runId)}`));
  return {
    run: raw.run === undefined || raw.run === null ? null : mapRunRecord(raw.run),
    events: Array.isArray(raw.events) ? raw.events.map(mapEventRow) : null,
    unreadable_event_lines: optStrs(raw.unreadable_event_lines),
    resumable: optBool(raw.resumable),
    next_stage: optStr(raw.next_stage),
    durability: optStr(raw.durability),
  };
}

/** `GET /harness/runs/{id}/events` — the append-only journal, newest last. */
export async function fetchHarnessRunEvents(runId: string, limit?: number): Promise<HarnessRunEvents> {
  const raw = rec(await get<unknown>(buildEventsPath(runId, limit)));
  return {
    run_id: optStr(raw.run_id),
    run_record_present: optBool(raw.run_record_present),
    stage: optStr(raw.stage),
    events: Array.isArray(raw.events) ? raw.events.map(mapEventRow) : null,
    unreadable: optStrs(raw.unreadable),
    limit: optNum(raw.limit),
  };
}

/** `POST /harness/runs/{id}/resume` — re-enter the stage a parked run stopped at. */
export async function resumeHarnessRun(runId: string): Promise<HarnessRunReceipt> {
  return mapRunReceipt(await send<unknown>(`/harness/runs/${encodeURIComponent(runId)}/resume`, "POST", {}));
}

/**
 * `POST /harness/runs/{id}/cancel` — request cancellation, applied at the next
 * stage boundary. Deliberately reachable while the feature is disabled.
 */
export async function cancelHarnessRun(runId: string, reason?: string): Promise<HarnessCancelReceipt> {
  if (reason !== undefined) assertReason(reason);
  const payload: Rec = {};
  if (reason !== undefined && reason !== "") payload.reason = reason;
  return mapCancelReceipt(await send<unknown>(`/harness/runs/${encodeURIComponent(runId)}/cancel`, "POST", payload));
}

/** `GET /harness/bundles` — the immutable bundle tree plus the active pointer. */
export async function fetchHarnessBundles(): Promise<HarnessBundleList> {
  const raw = rec(await get<unknown>("/harness/bundles"));
  return {
    bundles: Array.isArray(raw.bundles) ? raw.bundles.map(mapBundleRow) : null,
    unreadable: optStrs(raw.unreadable),
    complete: optBool(raw.complete),
    active_bundle: raw.active_bundle === undefined || raw.active_bundle === null ? null : mapActivePointer(raw.active_bundle),
  };
}

/** `GET /harness/bundles/{id}` — one manifest plus its redacted component bodies. */
export async function fetchHarnessBundle(bundleId: string): Promise<HarnessBundleDetail> {
  const raw = rec(await get<unknown>(`/harness/bundles/${encodeURIComponent(bundleId)}`));
  return {
    bundle: raw.bundle === undefined || raw.bundle === null ? null : mapBundleRow(raw.bundle),
    components: Array.isArray(raw.components)
      ? raw.components.map((row) => {
          const c = rec(row);
          return { ...mapComponentRef(c), content: optStr(c.content), missing: optBool(c.missing) };
        })
      : null,
    redactions: optStrs(raw.redactions),
    note: optStr(raw.note),
  };
}

/** `GET /harness/bundles/{id}/diff` — the component-level diff against the parent. */
export async function fetchHarnessBundleDiff(bundleId: string, options: HarnessDiffOptions = {}): Promise<HarnessDiffDetail> {
  const raw = rec(await get<unknown>(buildBundleDiffPath(bundleId, options)));
  const diff = optRec(raw.diff);
  const contents = optRec(raw.contents);
  const added: Record<string, string | null> | null = (() => {
    const block = contents === null ? null : optRec(contents.added);
    if (block === null) return null;
    const out: Record<string, string | null> = {};
    for (const [key, value] of Object.entries(block)) out[key] = optStr(value);
    return out;
  })();
  const modified: Record<string, { before: string | null; after: string | null }> | null = (() => {
    const block = contents === null ? null : optRec(contents.modified);
    if (block === null) return null;
    const out: Record<string, { before: string | null; after: string | null }> = {};
    for (const [key, value] of Object.entries(block)) {
      const row = rec(value);
      out[key] = { before: optStr(row.before), after: optStr(row.after) };
    }
    return out;
  })();
  return {
    diff: diff === null ? null : {
      base_bundle_id: optStr(diff.base_bundle_id),
      candidate_bundle_id: optStr(diff.candidate_bundle_id),
      added: optStrs(diff.added),
      removed: optStrs(diff.removed),
      modified: Array.isArray(diff.modified)
        ? diff.modified.map((row) => {
            const m = rec(row);
            return {
              component_id: optStr(m.component_id) ?? "",
              kind: optStr(m.kind),
              before_sha256: optStr(m.before_sha256),
              after_sha256: optStr(m.after_sha256),
              size_delta: optNum(m.size_delta),
            };
          })
        : null,
      unchanged: optStrs(diff.unchanged),
    },
    contents: contents === null ? null : { added, modified },
    redactions: optStrs(raw.redactions),
    note: optStr(raw.note),
  };
}

/** Map one experience row (list shape or detail metadata). Pure; exported for tests. */
export function mapExperience(raw: unknown): HarnessExperienceRow {
  const e = rec(raw);
  return {
    experience_id: optStr(e.experience_id) ?? "",
    title: optStr(e.title),
    lesson: optStr(e.lesson),
    finding: optStr(e.finding),
    scope: optStr(e.scope),
    task_type: optStr(e.task_type),
    status: optStr(e.status),
    domain_tags: optStrs(e.domain_tags),
    component_kinds: optStrs(e.component_kinds),
    run_ids: optStrs(e.run_ids),
    benefit: optStr(e.benefit),
    counter_evidence: optStr(e.counter_evidence),
    created_at: optStr(e.created_at),
    last_reviewed_at: optStr(e.last_reviewed_at),
    review_condition: optStr(e.review_condition),
    expires_at: optStr(e.expires_at),
    superseded_by: optStr(e.superseded_by),
    staleness: optStrs(e.staleness),
  };
}

/** `GET /harness/experiences` — the lesson library, newest first. */
export async function fetchHarnessExperiences(query: HarnessExperienceQuery = {}): Promise<HarnessExperienceList> {
  const raw = rec(await get<unknown>(buildExperiencesPath(query)));
  return {
    experiences: Array.isArray(raw.experiences) ? raw.experiences.map(mapExperience) : null,
    unreadable: optStrs(raw.unreadable),
    complete: optBool(raw.complete),
    total: optNum(raw.total),
    limit: optNum(raw.limit),
  };
}

/** `GET /harness/experiences/{id}` — one lesson plus its Markdown body. */
export async function fetchHarnessExperience(experienceId: string): Promise<HarnessExperienceDetail> {
  const raw = rec(await get<unknown>(`/harness/experiences/${encodeURIComponent(experienceId)}`));
  const metadata = raw.metadata === undefined || raw.metadata === null ? null : mapExperience(raw.metadata);
  return {
    experience: metadata,
    markdown: optStr(raw.markdown),
    staleness: optStrs(raw.staleness),
    redactions: optStrs(raw.redactions),
  };
}

/** `GET /harness/promotions` — the durable promotion chain plus the ladder. */
export async function fetchHarnessPromotions(): Promise<HarnessPromotionHistory> {
  const raw = rec(await get<unknown>("/harness/promotions"));
  const ladder = optRec(raw.ladder);
  return {
    events: Array.isArray(raw.events)
      ? raw.events.map((row) => {
          const e = rec(row);
          const gates = rec(e.gates);
          return {
            event: optStr(e.event),
            bundle_id: optStr(e.bundle_id),
            previous_bundle_id: optStr(e.previous_bundle_id),
            actor: optStr(e.actor),
            reason: optStr(e.reason),
            at: optStr(e.at),
            granted: optStrs(gates.granted),
          };
        })
      : null,
    unreadable: optStrs(raw.unreadable),
    pointer: raw.pointer === undefined || raw.pointer === null ? null : mapActivePointer(raw.pointer),
    pointer_error: optStr(raw.pointer_error),
    ladder: ladder === null ? null : Object.fromEntries(Object.entries(ladder).filter((entry): entry is [string, string] => typeof entry[1] === "string")),
    durability: optStr(raw.durability),
  };
}

/**
 * `POST /harness/candidates/{id}/approve` — record an operator approval.
 *
 * The server refuses with `409 harness_promotion_gates_unmet` when any
 * substantive gate is unmet, and with `422 harness_unexpected_approvals` when
 * a granted kind is not pending; both render verbatim. An approval is
 * *recorded, not applied* — activation stays a separate explicit step.
 */
export async function approveHarnessCandidate(bundleId: string, options: { reason?: string; approvals?: string[] } = {}): Promise<HarnessApprovalReceipt> {
  const reason = options.reason ?? "";
  assertReason(reason);
  const approvals = options.approvals ?? [];
  assertApprovalKinds(approvals);
  const raw = rec(await send<unknown>(`/harness/candidates/${encodeURIComponent(bundleId)}/approve`, "POST", { reason, approvals }));
  return { approved: optBool(raw.approved), bundle_id: optStr(raw.bundle_id), note: optStr(raw.note) };
}

/** `POST /harness/candidates/{id}/reject` — record a rejection; pointer untouched. */
export async function rejectHarnessCandidate(bundleId: string, reason?: string): Promise<HarnessRejectionReceipt> {
  if (reason !== undefined) assertReason(reason);
  const payload: Rec = { reason: reason ?? "" };
  const raw = rec(await send<unknown>(`/harness/candidates/${encodeURIComponent(bundleId)}/reject`, "POST", payload));
  return { rejected: optBool(raw.rejected), bundle_id: optStr(raw.bundle_id), promotion_state: optStr(raw.promotion_state) };
}

/**
 * `POST /harness/active/{id}/activate` — atomic pointer swap to a fully gated
 * bundle. Every unmet gate refuses with its own words; this client never
 * decides that a gate passes.
 */
export async function activateHarnessBundle(bundleId: string, reason?: string): Promise<HarnessActivationReceipt> {
  if (reason !== undefined) assertReason(reason);
  const payload: Rec = {};
  if (reason !== undefined && reason !== "") payload.reason = reason;
  const raw = rec(await send<unknown>(`/harness/active/${encodeURIComponent(bundleId)}/activate`, "POST", payload));
  return {
    activated: optBool(raw.activated),
    bundle_id: optStr(raw.bundle_id),
    previous_bundle_id: optStr(raw.previous_bundle_id),
    note: optStr(raw.note),
  };
}

/**
 * `POST /harness/active/{id}/rollback` — restore the pointer's previous
 * known-good bundle. `bundleId` must be the pointer's *current* bundle; any
 * other id refuses with the server's own sentence.
 */
export async function rollbackHarnessBundle(bundleId: string, reason?: string): Promise<HarnessRollbackReceipt> {
  if (reason !== undefined) assertReason(reason);
  const payload: Rec = {};
  if (reason !== undefined && reason !== "") payload.reason = reason;
  const raw = rec(await send<unknown>(`/harness/active/${encodeURIComponent(bundleId)}/rollback`, "POST", payload));
  return {
    rolled_back: optBool(raw.rolled_back),
    restored_bundle_id: optStr(raw.restored_bundle_id),
    from_bundle_id: optStr(raw.from_bundle_id),
    note: optStr(raw.note),
  };
}

/** `GET /harness/budgets` — the measured ledger plus the ceilings it is measured against. */
export async function fetchHarnessBudgets(query: HarnessBudgetQuery = {}): Promise<HarnessBudgets> {
  const raw = rec(await get<unknown>(buildBudgetsPath(query)));
  return {
    ceilings: optRecord(raw.ceilings),
    ledger: Array.isArray(raw.ledger)
      ? raw.ledger.map((row) => {
          const b = rec(row);
          return {
            run_id: optStr(b.run_id),
            stage: optStr(b.stage),
            model: optStr(b.model),
            input_tokens: optNum(b.input_tokens),
            output_tokens: optNum(b.output_tokens),
            cost_usd: optNum(b.cost_usd),
            elapsed_ms: optNum(b.elapsed_ms),
            recorded_at: optStr(b.recorded_at),
            ceilings: optRecord(b.ceilings),
          };
        })
      : null,
    unreadable: optStrs(raw.unreadable),
    complete: optBool(raw.complete),
    measured_model_calls: optNum(raw.measured_model_calls),
    note: optStr(raw.note),
    durability: optStr(raw.durability),
  };
}
