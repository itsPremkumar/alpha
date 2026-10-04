/**
 * Per-bot LLM model configuration client.
 *
 * One bot may name a primary model, an ordered fallback chain, a counselling
 * panel and a mixture panel — but only from `config.yaml` `models[]`. That
 * makes this client different from the others: it is the boundary of a
 * *fail-closed validator*, so its job is to carry the server's verdict
 * (including every 422 issue) back to the panel rather than to normalise it
 * away.
 *
 * Routes (all under `/api`):
 *
 *   GET    /bots/{name}/model-config          stored block + resolved plan + known models
 *   PUT    /bots/{name}/model-config          validate and store `{config}`
 *   DELETE /bots/{name}/model-config          clear the block
 *   POST   /bots/{name}/model-config/preview  validate + resolve, never saves
 *
 * Honesty rules this client exists to enforce:
 *
 *  - A 422's `detail` is `{message, issues}` — an *object*, which the shared
 *    `ApiClientError` deliberately does not parse (it carries string details
 *    only). Dropping that body would show "Request failed (HTTP 422)" while
 *    the server named every problem, so the transport here captures the error
 *    body and rethrows it as `ModelConfigValidationError` carrying the issues.
 *  - `known_models` absent (older Gateway) maps to `null`, not `[]`: "the
 *    server did not report the list" and "the server reported zero declared
 *    models" are different facts and the picker must say which.
 *  - The draft helpers are pure and never invent a value the server did not
 *    send: `configToDraft` tolerates junk so a corrupt block still renders as
 *    *something* the operator can fix instead of throwing on mount.
 */

import { ApiClientError, apiUrl, createApiClient, GATEWAY_BASE } from "./api-client";

// ── Server shapes ──────────────────────────────────────────────────────────

export interface ModelConfigIssue {
  code: string;
  field: string;
  message: string;
  severity: string;
}

export interface CounselView {
  enabled: boolean;
  members: string[];
  rounds: number;
  quorum: number;
  effort: string | null;
}

export interface MixtureView {
  enabled: boolean;
  references: string[];
  aggregator: string | null;
  strategy: string;
  max_workers: number;
}

export interface ModelPlanView {
  primary: string | null;
  primary_source: string;
  fallbacks: string[];
  fallbacks_source: string;
  counsel: CounselView | null;
  counsel_source: string;
  mixture: MixtureView | null;
  mixture_source: string;
  sampling: Record<string, unknown>;
  sampling_source: string;
}

export interface ModelPlanLimits {
  max_fallbacks: number;
  max_mixture_references: number;
  max_counsel_members: number;
  max_counsel_rounds: number;
  max_mixture_workers: number;
  mixture_strategies: string[];
}

/** `describe_model_plan()`'s envelope: the plan plus WHY each value won. */
export interface ResolvedModelConfig {
  bot: string;
  plan: ModelPlanView;
  /** Precedence ladder, strongest first. Rendered, never re-derived. */
  precedence: string[];
  limits: ModelPlanLimits;
}

export interface BotModelConfigView {
  name: string;
  model: string | null;
  /** The RAW stored block, exactly as the registry holds it. */
  config: Record<string, unknown>;
  resolved: ResolvedModelConfig;
  issues: ModelConfigIssue[];
  valid: boolean;
  /** Declared `models[]` names, sorted. `null` when the Gateway sent none. */
  known_models: string[] | null;
}

export interface ModelConfigPreview {
  name: string;
  config: Record<string, unknown>;
  resolved: ResolvedModelConfig;
  issues: ModelConfigIssue[];
  valid: boolean;
}

export interface ModelConfigClearResult {
  name: string;
  config: Record<string, unknown>;
  cleared: boolean;
}

// ── Errors ─────────────────────────────────────────────────────────────────

/**
 * A refused save carrying every issue the validator found.
 *
 * Separate from `ApiClientError` because the shared client's `detail` is a
 * string: an object detail is *not* copied out of it, so without this the
 * panel could not render the per-field problems the server produced.
 */
export class ModelConfigValidationError extends Error {
  readonly status: number;
  readonly issues: ModelConfigIssue[];

  constructor(message: string, issues: ModelConfigIssue[], status: number) {
    super(message || `Model configuration rejected (HTTP ${status}).`);
    this.name = "ModelConfigValidationError";
    this.status = status;
    this.issues = issues;
  }
}

function isIssue(value: unknown): value is ModelConfigIssue {
  if (!value || typeof value !== "object") return false;
  const rec = value as Record<string, unknown>;
  return typeof rec.field === "string" && typeof rec.message === "string";
}

/** Pull `detail.issues` out of a captured 422 body (exported for tests). */
export function issuesFromBody(body: unknown): ModelConfigIssue[] {
  if (!body || typeof body !== "object") return [];
  const detail = (body as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object") return [];
  const raw = (detail as Record<string, unknown>).issues;
  if (!Array.isArray(raw)) return [];
  return raw.filter(isIssue).map((issue) => ({
    code: typeof issue.code === "string" ? issue.code : "invalid",
    field: issue.field,
    message: issue.message,
    severity: typeof issue.severity === "string" ? issue.severity : "error",
  }));
}

/** Pull `detail.message` out of a captured body (exported for tests). */
export function messageFromBody(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as Record<string, unknown>).detail;
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail === "object") {
    const message = (detail as Record<string, unknown>).message;
    if (typeof message === "string" && message) return message;
  }
  return null;
}

// ── Draft model (what the panel edits) ─────────────────────────────────────

export interface SamplingRow {
  key: string;
  value: string;
}

export interface CounselDraft {
  enabled: boolean;
  members: string[];
  rounds: number;
  quorum: number;
  effort: string;
}

export interface MixtureDraft {
  enabled: boolean;
  references: string[];
  aggregator: string;
  strategy: string;
  max_workers: number;
}

export interface ModelConfigDraft {
  primary: string;
  fallbacks: string[];
  sampling: SamplingRow[];
  counsel: CounselDraft;
  mixture: MixtureDraft;
}

/** Server defaults, mirrored so "unchanged" can be detected without a diff. */
export const DEFAULT_COUNSEL: CounselDraft = { enabled: false, members: [], rounds: 1, quorum: 0, effort: "" };
export const DEFAULT_MIXTURE: MixtureDraft = {
  enabled: false,
  references: [],
  aggregator: "",
  strategy: "parallel",
  max_workers: 4,
};

export function emptyDraft(): ModelConfigDraft {
  return { primary: "", fallbacks: [], sampling: [], counsel: { ...DEFAULT_COUNSEL }, mixture: { ...DEFAULT_MIXTURE } };
}

const asString = (value: unknown): string => (typeof value === "string" ? value : "");
const asStringList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((v): v is string => typeof v === "string" && v.trim() !== "") : [];
const asInt = (value: unknown, fallback: number): number =>
  typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : fallback;

/**
 * Coerce one sampling row's text into the scalar the provider will get.
 *
 * `temperature=0.2` typed into a text box must arrive as a number — a string
 * `"0.2"` validates locally and then fails at the provider. Everything else
 * stays the literal text the operator typed: guessing that `"gpt"` should be
 * a boolean would be inventing a value.
 */
export function coerceSamplingValue(raw: string): string | number | boolean {
  const text = raw.trim();
  if (text === "true") return true;
  if (text === "false") return false;
  if (/^-?(0|[1-9]\d*)(\.\d+)?$/.test(text)) {
    const num = Number(text);
    if (Number.isFinite(num)) return num;
  }
  return text;
}

/** Render a stored sampling scalar back into an editable row. */
export function formatSamplingValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  // Objects/arrays are not sampling scalars; show them as the JSON they are
  // rather than "[object Object]" so nothing looks silently editable.
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}

/**
 * Map the RAW stored block onto a draft (exported for tests).
 *
 * Tolerant by design: the stored block may be the product of a hand edit or an
 * older schema, and a panel that throws on mount is a panel the operator can
 * never open to fix the very block that broke it.
 */
export function configToDraft(config: unknown): ModelConfigDraft {
  const draft = emptyDraft();
  if (!config || typeof config !== "object") return draft;
  const rec = config as Record<string, unknown>;

  draft.primary = asString(rec.primary).trim();
  draft.fallbacks = asStringList(rec.fallbacks);

  if (rec.sampling && typeof rec.sampling === "object" && !Array.isArray(rec.sampling)) {
    draft.sampling = Object.entries(rec.sampling as Record<string, unknown>).map(([key, value]) => ({
      key,
      value: formatSamplingValue(value),
    }));
  }

  const counsel = rec.counsel;
  if (counsel && typeof counsel === "object" && !Array.isArray(counsel)) {
    const c = counsel as Record<string, unknown>;
    draft.counsel = {
      enabled: c.enabled === true,
      members: asStringList(c.members),
      rounds: asInt(c.rounds, 1),
      quorum: asInt(c.quorum, 0),
      effort: asString(c.effort).trim(),
    };
  }

  const mixture = rec.mixture;
  if (mixture && typeof mixture === "object" && !Array.isArray(mixture)) {
    const m = mixture as Record<string, unknown>;
    draft.mixture = {
      enabled: m.enabled === true,
      references: asStringList(m.references),
      aggregator: asString(m.aggregator).trim(),
      strategy: asString(m.strategy).trim() || "parallel",
      max_workers: asInt(m.max_workers, 4),
    };
  }

  return draft;
}

/**
 * Map a draft onto the payload the validator accepts (exported for tests).
 *
 * Empty sections are OMITTED rather than sent as empty objects: the server
 * treats "absent" as inherit-everything, so sending `counsel: {enabled:false}`
 * would store a block that changes nothing while making the profile look
 * configured.
 */
export function draftToConfig(draft: ModelConfigDraft): Record<string, unknown> {
  const config: Record<string, unknown> = {};
  const primary = draft.primary.trim();
  if (primary) config.primary = primary;
  const fallbacks = draft.fallbacks.map((f) => f.trim()).filter(Boolean);
  if (fallbacks.length) config.fallbacks = fallbacks;

  const sampling: Record<string, unknown> = {};
  for (const row of draft.sampling) {
    const key = row.key.trim();
    if (!key) continue;
    sampling[key] = coerceSamplingValue(row.value);
  }
  if (Object.keys(sampling).length) config.sampling = sampling;

  const c = draft.counsel;
  if (c.enabled || c.members.length || c.rounds !== 1 || c.quorum !== 0 || c.effort.trim()) {
    config.counsel = {
      enabled: c.enabled,
      members: c.members.map((m) => m.trim()).filter(Boolean),
      rounds: c.rounds,
      quorum: c.quorum,
      effort: c.effort.trim() || null,
    };
  }

  const m = draft.mixture;
  if (m.enabled || m.references.length || m.aggregator.trim() || m.strategy !== "parallel" || m.max_workers !== 4) {
    config.mixture = {
      enabled: m.enabled,
      references: m.references.map((r) => r.trim()).filter(Boolean),
      aggregator: m.aggregator.trim() || null,
      strategy: m.strategy,
      max_workers: m.max_workers,
    };
  }

  return config;
}

/** Structural equality for two drafts — used to gate Save/Preview buttons. */
export function draftsEqual(a: ModelConfigDraft, b: ModelConfigDraft): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

// ── Transport ──────────────────────────────────────────────────────────────

export interface ModelConfigClientOptions {
  /** Injected in tests; defaults to `globalThis.fetch` at call time. */
  fetch?: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  getCookie?: () => string;
  baseUrl?: string;
}

export interface ModelConfigClient {
  get(name: string): Promise<BotModelConfigView>;
  put(name: string, config: Record<string, unknown>): Promise<BotModelConfigView>;
  clear(name: string): Promise<ModelConfigClearResult>;
  preview(
    name: string,
    config: Record<string, unknown>,
    context?: { request_model?: string | null; bot_model?: string | null; custom_agent_model?: string | null },
  ): Promise<ModelConfigPreview>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

/** Reject a 200 whose body is not the envelope this contract promises. */
function expectEnvelope(body: unknown, where: string): Record<string, unknown> {
  if (!isRecord(body)) throw new Error(`The Gateway returned an unreadable response for ${where}.`);
  return body;
}

function toView(body: unknown): BotModelConfigView {
  const rec = expectEnvelope(body, "the model configuration");
  const resolved = isRecord(rec.resolved) ? (rec.resolved as unknown as ResolvedModelConfig) : null;
  if (!resolved || !isRecord(resolved.plan)) {
    throw new Error("The Gateway returned a model configuration without a resolved plan.");
  }
  const issues = Array.isArray(rec.issues) ? rec.issues.filter(isIssue) : [];
  const known = Array.isArray(rec.known_models) ? rec.known_models.filter((m): m is string => typeof m === "string") : null;
  return {
    name: typeof rec.name === "string" ? rec.name : "",
    model: typeof rec.model === "string" ? rec.model : null,
    config: isRecord(rec.config) ? rec.config : {},
    resolved,
    issues,
    valid: rec.valid === true,
    known_models: known,
  };
}

function toPreview(body: unknown): ModelConfigPreview {
  const rec = expectEnvelope(body, "the preview");
  const resolved = isRecord(rec.resolved) ? (rec.resolved as unknown as ResolvedModelConfig) : null;
  if (!resolved || !isRecord(resolved.plan)) {
    throw new Error("The Gateway returned a preview without a resolved plan.");
  }
  return {
    name: typeof rec.name === "string" ? rec.name : "",
    config: isRecord(rec.config) ? rec.config : {},
    resolved,
    issues: Array.isArray(rec.issues) ? rec.issues.filter(isIssue) : [],
    valid: rec.valid === true,
  };
}

/**
 * Build a client. Each instance owns its own error-body capture so two panels
 * cannot read each other's 422 payloads.
 */
export function createModelConfigClient(options: ModelConfigClientOptions = {}): ModelConfigClient {
  const netFetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> =>
    options.fetch ? options.fetch(input, init) : globalThis.fetch(input, init);

  // Error bodies keyed by `METHOD url`; the shared client consumes its own
  // copy of the body before throwing, so this is the only place the raw
  // `{message, issues}` payload survives.
  const errorBodies = new Map<string, unknown>();

  const capturingFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const response = await netFetch(input, init);
    const key = `${(init?.method || "GET").toUpperCase()} ${String(input)}`;
    if (response.ok) {
      errorBodies.delete(key);
      return response;
    }
    try {
      errorBodies.set(key, await response.clone().json());
    } catch {
      errorBodies.set(key, null);
    }
    return response;
  };

  const request = createApiClient({
    fetch: capturingFetch,
    getCookie: options.getCookie,
    baseUrl: options.baseUrl,
  });

  const base = options.baseUrl ?? GATEWAY_BASE;
  const path = (name: string, suffix = "") => `/bots/${encodeURIComponent(name)}${suffix}`;

  /** Run one call, converting a captured 422 into a typed validation error. */
  async function call<T>(method: string, relative: string, parse: (body: unknown) => T, init: RequestInit = {}): Promise<T> {
    const key = `${method} ${apiUrl(relative, base)}`;
    try {
      const response = await request(relative, { ...init, method });
      return parse(await response.json());
    } catch (err) {
      const body = errorBodies.get(key);
      errorBodies.delete(key);
      const issues = issuesFromBody(body);
      if (issues.length) {
        throw new ModelConfigValidationError(messageFromBody(body) || "", issues, err instanceof ApiClientError ? err.status : 422);
      }
      throw err;
    }
  }

  const json = (payload: unknown): RequestInit => ({
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  return {
    get: (name) => call("GET", path(name, "/model-config"), toView),
    put: (name, config) => call("PUT", path(name, "/model-config"), toView, json({ config })),
    clear: (name) =>
      call("DELETE", path(name, "/model-config"), (body) => {
        const rec = expectEnvelope(body, "the clear confirmation");
        return {
          name: typeof rec.name === "string" ? rec.name : "",
          config: isRecord(rec.config) ? rec.config : {},
          cleared: rec.cleared === true,
        };
      }),
    preview: (name, config, context) =>
      call("POST", path(name, "/model-config/preview"), toPreview, json({ config, ...(context || {}) })),
  };
}

/** The default client components import. */
export const modelConfigClient = createModelConfigClient();

export function fetchBotModelConfig(name: string): Promise<BotModelConfigView> {
  return modelConfigClient.get(name);
}

export function saveBotModelConfig(name: string, config: Record<string, unknown>): Promise<BotModelConfigView> {
  return modelConfigClient.put(name, config);
}

export function clearBotModelConfig(name: string): Promise<ModelConfigClearResult> {
  return modelConfigClient.clear(name);
}

export function previewBotModelConfig(
  name: string,
  config: Record<string, unknown>,
  context?: { request_model?: string | null; bot_model?: string | null; custom_agent_model?: string | null },
): Promise<ModelConfigPreview> {
  return modelConfigClient.preview(name, config, context);
}
