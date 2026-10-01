import { ChatMessage, Thread, AIModel, SlashCommandInfo, SlashCommandResult, AutonomousDetection } from "@/types/chat";

import { apiFetch, ApiClientError } from "./api-client";

/**
 * Distinguishable fetch outcome: a FAILED load is never an empty success.
 * Callers must branch on `ok`; an empty `value` on the ok path means the
 * server really returned nothing.
 */
export type FetchResult<T> =
  | { ok: true; value: T; incomplete?: string; resumeCursor?: number | string }
  | { ok: false; error: string };

function failed(err: unknown): { ok: false; error: string } {
  return { ok: false, error: err instanceof Error ? err.message : "Request failed" };
}

const PAGE_REQUEST_TIMEOUT_MS = 30_000;

async function fetchWithTimeout(path: string, init: RequestInit = {}): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PAGE_REQUEST_TIMEOUT_MS);
  try {
    return await apiFetch(path, { ...init, signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error(`Request timed out after ${PAGE_REQUEST_TIMEOUT_MS / 1000} seconds.`);
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Honest thread list: failure comes back as `{ ok: false }`, never as `[]`.
 * Use this from every surface that can show "you have no sessions".
 */
const THREAD_PAGE_SIZE = 200;

function threadFromResponse(thread: any): Thread {
  if (!thread || typeof thread.thread_id !== "string" || !thread.thread_id) {
    throw new Error("The server returned an invalid thread record.");
  }
  const metadata = thread.metadata && typeof thread.metadata === "object" ? thread.metadata : {};
  const values = thread.values && typeof thread.values === "object" ? thread.values : {};
  const mapped: Thread = {
    thread_id: thread.thread_id,
    // Empty optional strings let mergeThreads preserve a known local value
    // when an older Gateway omits the field entirely.
    title: metadata.title || values.title || thread.title || "",
    created_at: typeof thread.created_at === "string" ? thread.created_at : "",
    updated_at: typeof thread.updated_at === "string" ? thread.updated_at : "",
  };
  if (Object.hasOwn(metadata, "bot_name") || Object.hasOwn(thread, "bot_name")) {
    const botName = metadata.bot_name ?? thread.bot_name;
    mapped.botName = typeof botName === "string" && botName ? botName : null;
  }
  if (Object.hasOwn(thread, "assistant_id")) {
    mapped.assistantId = typeof thread.assistant_id === "string" && thread.assistant_id ? thread.assistant_id : null;
  }
  if (Object.hasOwn(metadata, "alpha_project_id") || Object.hasOwn(thread, "project_id")) {
    const projectId = metadata.alpha_project_id ?? thread.project_id;
    mapped.projectId = typeof projectId === "string" && projectId ? projectId : null;
  }
  return mapped;
}

export async function fetchThreadsResult(limit?: number): Promise<FetchResult<Thread[]>> {
  // The list endpoint is offset-paginated. With no explicit cap, walk every
  // page instead of silently stopping at the old 100-thread ceiling.
  const cap = typeof limit === "number" ? Math.max(1, Math.min(1000, Math.floor(limit))) : null;
  const pageSize = cap === null ? THREAD_PAGE_SIZE : Math.min(THREAD_PAGE_SIZE, cap);
  const byId = new Map<string, Thread>();
  let offset = 0;
  let previousBoundary = "";

  while (true) {
    try {
      const res = await fetchWithTimeout(`/threads/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: pageSize, offset }),
      });
      if (!res.ok) throw new Error(`Thread list failed (HTTP ${res.status}).`);
      const data = await res.json();
      let list: any[];
      if (Array.isArray(data)) list = data;
      else if (data && typeof data === "object" && Array.isArray(data.threads)) list = data.threads;
      else throw new Error("The server returned an unreadable thread list.");
      const mapped = list.map(threadFromResponse);
      const boundary = mapped.length > 0 ? `${mapped[0].thread_id}:${mapped[mapped.length - 1].thread_id}` : "";
      if (mapped.length === pageSize && boundary && boundary === previousBoundary) {
        throw new Error("Thread pagination did not advance; sync stopped to avoid an endless loop.");
      }
      previousBoundary = boundary;
      for (const thread of mapped) byId.set(thread.thread_id, thread);
      if (list.length < pageSize || (cap !== null && byId.size >= cap)) break;
      offset += list.length;
    } catch (error) {
      const value = Array.from(byId.values());
      if (value.length > 0) {
        return {
          ok: true,
          value: cap === null ? value : value.slice(0, cap),
          incomplete: failed(error).error,
          resumeCursor: offset,
        };
      }
      console.error("Failed to fetch threads:", error);
      return failed(error);
    }
  }

  const value = Array.from(byId.values());
  return { ok: true, value: cap === null ? value : value.slice(0, cap) };
}

/**
 * LEGACY-COMPAT thread list: returns `[]` on failure for older external
 * callers. The workspace UI uses `fetchThreadsResult()` so a failed read keeps
 * the local archive visible and surfaces the server reason. New code MUST use
 * the `FetchResult` entry point.
 */
export async function fetchThreads(limit?: number): Promise<Thread[]> {
  const result = await fetchThreadsResult(limit);
  return result.ok ? result.value : [];
}

export interface CreateThreadOptions {
  /** Specialist bot owning this conversation — stored on the server thread. */
  botName?: string | null;
  projectId?: string | null;
}

export async function createThread(title?: string, opts?: CreateThreadOptions): Promise<string> {
  const metadata: Record<string, string> = { title: title || "New Conversation" };
  if (opts?.botName) metadata.bot_name = opts.botName;
  const body: Record<string, unknown> = {
    metadata,
    ...(opts?.botName ? { assistant_id: opts.botName } : {}),
    ...(opts?.projectId ? { project_id: opts.projectId } : {}),
  };
  const res = await apiFetch(`/threads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail: string | null = null;
    try {
      const body = await res.clone().json();
      const candidate = body && typeof body === "object" ? body.detail ?? body.error : null;
      detail = typeof candidate === "string" && candidate ? candidate : null;
    } catch {
      /* Keep the status-only reason. */
    }
    throw new ApiClientError("http", res.status, detail);
  }
  const data = await res.json();
  if (!data || typeof data.thread_id !== "string" || !data.thread_id) {
    throw new Error("The server created an unreadable thread record.");
  }
  return data.thread_id;
}

/** Extract readable text from LangChain-style message content (string or block list). */
function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((b) => {
        if (typeof b === "string") return b;
        if (b && typeof b === "object") {
          const blk = b as Record<string, unknown>;
          if (typeof blk.text === "string") return blk.text;
          if (blk.type === "tool_use") return `[tool: ${String(blk.name ?? "unknown")}]`;
          if (blk.type === "image" || blk.type === "image_url") return "[image]";
          return JSON.stringify(blk);
        }
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  if (content && typeof content === "object") return JSON.stringify(content);
  return "";
}

/**
 * Honest thread history: failure comes back as `{ ok: false }`, never as an
 * empty conversation. Use this from every surface that can show "this chat
 * has no messages".
 */
const HISTORY_PAGE_SIZE = 200;

/**
 * Index every tool result in a feed by the call it answers.
 *
 * A persisted `tool_calls` entry carries only `{id, name, args, type}` - the
 * backend journals the *call*, not its outcome, on the assistant message. The
 * outcome lives on the separate `llm.tool.result` row, matched by
 * `tool_call_id`. Without this index every call restored from history is
 * statusless, and a statusless call renders as still-running with "no result
 * reported" - so a run that completed successfully is displayed as unfinished
 * work, on every reload, for every turn.
 *
 * The result row's own `status` is the authority, and the journal now writes an
 * honest one (an `Error: ...` tool return is recorded as an error rather than a
 * success). A result with no status at all is still a result, so it settles the
 * call rather than leaving it open.
 */
export function indexToolResultStatuses(rows: any[]): Map<string, "completed" | "failed"> {
  const statuses = new Map<string, "completed" | "failed">();
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const inner = row.content && typeof row.content === "object" ? row.content : row;
    if (String(inner.type || "") !== "tool") continue;
    const callId = typeof inner.tool_call_id === "string" ? inner.tool_call_id : "";
    if (!callId) continue;
    // A later row for the same call is the more recent statement about it.
    statuses.set(callId, inner.status === "error" ? "failed" : "completed");
  }
  return statuses;
}

function messageFromRow(message: any, index: number, toolStatuses?: Map<string, "completed" | "failed">): ChatMessage[] {
  // Event-store row shape (current backend).
  if (message && typeof message === "object" && ("event_type" in message || "seq" in message)) {
    const inner = message.content && typeof message.content === "object" ? message.content : {};
    const messageType = String(inner.type || "");
    const eventType = String(message.event_type || "");
    let role: ChatMessage["role"] = "assistant";
    if (messageType === "human" || eventType === "human_message") role = "user";
    else if (messageType === "system") role = "system";
    else if (messageType === "tool") role = "assistant";
    const text = textOf(inner.content ?? message.content ?? "");
    if (!text && messageType === "tool") return [];
    const feedback = message.feedback as { rating?: unknown } | null | undefined;
    const rating = feedback?.rating === 1 || feedback?.rating === -1 ? feedback.rating : undefined;
    return [
      {
        id: String(inner.id || (message.seq !== undefined ? `seq-${message.seq}` : `msg-${index}`)),
        role,
        content: text,
        thinking: inner.additional_kwargs?.thinking || "",
        toolCalls: [
          ...((inner.tool_calls || []) as any[]).map((toolCall: any) => ({
            id: toolCall.id,
            name: toolCall.name,
            args: toolCall.args || {},
            // Resolved from the matching result row; absent only when the run
            // genuinely produced no result for this call.
            ...(toolCall.id && toolStatuses?.has(toolCall.id)
              ? { status: toolStatuses.get(toolCall.id) }
              : {}),
          })),
          ...((inner.invalid_tool_calls || []) as any[]).map((toolCall: any, toolIndex: number) => ({
            id: toolCall.id || `invalid-${index}-${toolIndex}`,
            name: toolCall.name || "invalid_tool",
            args: toolCall.args || {},
            status: "failed" as const,
          })),
        ],
        // `null` when the Gateway stamped no time — never "now". Painting the
        // page-load instant onto an unstamped history row makes a fabricated
        // time indistinguishable from a measured one (see lib/time.ts).
        createdAt: message.created_at || inner.created_at || null,
        sequence: typeof message.seq === "number" ? message.seq : undefined,
        raw: message,
        runId: message.run_id || undefined,
        rating,
      } as ChatMessage,
    ];
  }

  // Legacy LangChain message shape (kept for cached/offline data).
  return [
    {
      id: message.id || `msg-${index}`,
      role: message.type === "human" || message.role === "user" ? "user" : "assistant",
      content: typeof message.content === "string" ? message.content : JSON.stringify(message.content),
      thinking: message.additional_kwargs?.thinking || "",
      toolCalls: (message.tool_calls || []).map((toolCall: any) => ({
        id: toolCall.id,
        name: toolCall.name,
        args: toolCall.args || {},
        ...(toolCall.id && toolStatuses?.has(toolCall.id) ? { status: toolStatuses.get(toolCall.id) } : {}),
      })),
      createdAt: message.created_at || null,
      raw: message,
    } as ChatMessage,
  ];
}

/**
 * Collapse feed rows that resolve to the same message id.
 *
 * The render list keys messages by `msg.id`, so two rows carrying the same id
 * — a re-journaled turn, or a legacy row whose message id equals the run id —
 * crash the chat view with a React duplicate-key error. The local archive merge
 * (`mergeMessages`) already treats same-id rows as one message; the direct
 * server-history path applies the same rule so both paths agree. First
 * occurrence wins: the feed is chronological, so the earliest row is the
 * canonical write of the message.
 */
function uniqueMessages(messages: ChatMessage[]): ChatMessage[] {
  const seen = new Set<string>();
  return messages.filter((message) => {
    if (seen.has(message.id)) return false;
    seen.add(message.id);
    return true;
  });
}

export async function fetchThreadHistoryResult(threadId: string): Promise<FetchResult<ChatMessage[]>> {
  // The message feed is backward-paginated with a sequence cursor. Walk all
  // pages so reopening a long chat never silently drops its oldest turns.
  // `pages` is newest-first; `beforeSeq` always holds the cursor of the most
  // recent ACCEPTED page, which is exactly where a truncated walk resumes.
  const pages: any[][] = [];
  let beforeSeq: number | undefined;
  const seenCursors = new Set<number>();

  while (true) {
    try {
      const query = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE) });
      if (beforeSeq !== undefined) query.set("before_seq", String(beforeSeq));
      const res = await fetchWithTimeout(
        `/threads/${encodeURIComponent(threadId)}/messages/page?${query.toString()}`,
      );
      if (!res.ok) throw new Error(`Thread history failed (HTTP ${res.status}).`);
      const data = await res.json();
      const isPage = Boolean(data && typeof data === "object" && !Array.isArray(data) && Array.isArray(data.data));
      let page: any[];
      if (isPage) page = data.data;
      else if (Array.isArray(data)) page = data;
      else if (data && typeof data === "object" && Array.isArray(data.messages)) page = data.messages;
      else throw new Error("The server returned an unreadable message page.");

      // A bare array is the legacy endpoint shape and has no cursor contract.
      if (!isPage) {
        pages.push(page);
        break;
      }
      if (data.has_more !== true) {
        // The server says this is the oldest page; the walk is complete.
        pages.push(page);
        break;
      }
      const nextCursor = Number(data.next_before_seq);
      if (
        !Number.isSafeInteger(nextCursor) ||
        nextCursor < 1 ||
        seenCursors.has(nextCursor) ||
        (beforeSeq !== undefined && nextCursor >= beforeSeq)
      ) {
        // The cursor is validated BEFORE the page is accepted: a page we cannot
        // place in the sequence would otherwise re-deliver rows the caller
        // already has, rendering a duplicated turn.
        throw new Error("Thread history pagination returned a non-decreasing or invalid cursor.");
      }
      seenCursors.add(nextCursor);
      pages.push(page);
      beforeSeq = nextCursor;
    } catch (error) {
      const rows = pages
        .slice()
        .reverse()
        .flat();
      const toolStatuses = indexToolResultStatuses(rows);
      const partial = rows.flatMap((message, index) => messageFromRow(message, index, toolStatuses));
      if (partial.length > 0) {
        return {
          ok: true,
          value: uniqueMessages(partial),
          incomplete: failed(error).error,
          // The last accepted cursor, not the newest page's: a later page's
          // cursor would skip everything already fetched.
          resumeCursor: beforeSeq,
        };
      }
      console.error("Failed to fetch thread history:", error);
      return failed(error);
    }
  }

  const rows = pages
    .slice()
    .reverse()
    .flat();
  // Built from every page fetched, so a call on page 1 still resolves against
  // its result even when the result lands on page 2.
  const toolStatuses = indexToolResultStatuses(rows);
  return {
    ok: true,
    value: uniqueMessages(rows.flatMap((message, index) => messageFromRow(message, index, toolStatuses))),
  };
}

/**
 * LEGACY-COMPAT thread history: returns `[]` on failure for older external
 * callers. The workspace UI and thread utilities use
 * `fetchThreadHistoryResult()` so a server failure can fall back to the
 * complete local archive without looking like a confirmed empty conversation.
 */
export async function fetchThreadHistory(threadId: string): Promise<ChatMessage[]> {
  const result = await fetchThreadHistoryResult(threadId);
  return result.ok ? result.value : [];
}

export const BUILTIN_FREE_MODELS: AIModel[] = [
  {
    id: "alpha-free",
    name: "✨ Alpha Free Auto-Router (No API Key)",
    provider: "Free Router",
    description: "Automatic failover across all verified free keyless providers (OVHcloud, Pollinations, LLM7, Vireonix, Cehpoint)",
    is_free: true,
    free_status: "no_key_free",
    quota_type: "keyless_free",
  },
  {
    id: "free:ovhcloud:Meta-Llama-3_3-70B-Instruct",
    name: "LLaMA 3.3 70B (OVHcloud Free)",
    provider: "ovhcloud",
    description: "Meta LLaMA 3.3 70B on OVHcloud European AI Endpoints — 100% Free, No Key Needed",
    is_free: true,
    free_status: "no_key_free",
    quota_type: "keyless_free",
  },
  {
    id: "free:ovhcloud:Qwen3-Coder-30B-A3B-Instruct",
    name: "Qwen3 Coder 30B (OVHcloud Free)",
    provider: "ovhcloud",
    description: "Qwen3 Coder 30B MoE on OVHcloud — 100% Free, No Key Needed",
    is_free: true,
    free_status: "no_key_free",
    quota_type: "keyless_free",
  },
  {
    id: "free:ovhcloud:Mistral-7B-Instruct-v0.3",
    name: "Mistral 7B v0.3 (OVHcloud Free)",
    provider: "ovhcloud",
    description: "Fast Mistral 7B Instruct on OVHcloud — 100% Free, No Key Needed",
    is_free: true,
    free_status: "no_key_free",
    quota_type: "keyless_free",
  },
  {
    id: "free:pollinations:openai-fast",
    name: "OpenAI Fast (Pollinations Free)",
    provider: "pollinations",
    description: "Ultra-low latency public AI router via Pollinations — No Key Needed",
    is_free: true,
    free_status: "no_key_free",
    quota_type: "keyless_free",
  },
  {
    id: "free:pollinations:openai",
    name: "OpenAI Standard (Pollinations Free)",
    provider: "pollinations",
    description: "Standard OpenAI generation via Pollinations — No Key Needed",
    is_free: true,
    free_status: "no_key_free",
    quota_type: "keyless_free",
  },
  {
    id: "free:llm7:codestral-latest",
    name: "Codestral Latest (LLM7 Free)",
    provider: "llm7",
    description: "Mistral Codestral coding model via LLM7 — No Key Needed",
    is_free: true,
    free_status: "no_key_free",
    quota_type: "keyless_free",
  },
  {
    id: "free:llm7:mistral-Nemo-Instruct-2407",
    name: "Mistral Nemo 12B (LLM7 Free)",
    provider: "llm7",
    description: "Mistral Nemo 128k context model via LLM7 — No Key Needed",
    is_free: true,
    free_status: "no_key_free",
    quota_type: "keyless_free",
  },
  {
    id: "free:vireonix:auto",
    name: "Vireonix Auto (Free Router)",
    provider: "vireonix",
    description: "Vireonix public keyless AI gateway",
    is_free: true,
    free_status: "no_key_free",
    quota_type: "keyless_free",
  },
  {
    id: "free:cehpoint:cehpoint-ai",
    name: "Cehpoint AI (Free Endpoint)",
    provider: "cehpoint",
    description: "Cehpoint public keyless model endpoint",
    is_free: true,
    free_status: "no_key_free",
    quota_type: "keyless_free",
  },
];

export interface ProviderModelItem {
  id: string;
  name: string;
  model_id: string;
  supports_thinking?: boolean;
  description?: string;
}

export interface LLMProviderCatalogItem {
  id: string;
  name: string;
  category: "keyless_free" | "recurring_free" | "free_gateway" | "trial_credits" | "paid" | "custom";
  key_env: string | null;
  configured: boolean;
  masked_key: string | null;
  portal_url: string;
  free_tier_note: string;
  base_url: string | null;
  default_models: ProviderModelItem[];
}

/**
 * One `GET /api/models` read: the configured models plus the server-declared
 * reasoning-effort vocabulary.
 *
 * The ladder and labels travel with the models on purpose. A client that
 * hardcodes its own copy of the rung order/labels drifts the moment a provider
 * adds a level, and the drift is invisible — the picker just shows a name the
 * server has never heard of. An absent field means an older Gateway, so the
 * caller's fallback vocabulary applies; it is never invented as an empty list.
 */
export interface ModelCatalog {
  models: AIModel[];
  /** Canonical effort rungs, weakest → strongest. */
  reasoningEffortLevels: string[];
  /** Display label per rung, keyed by the names above. */
  reasoningEffortLabels: Record<string, string>;
}

/** The vocabulary a read that carried none must fall back to. */
export const FALLBACK_EFFORT_LEVELS: readonly string[] = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];

export async function fetchModelCatalog(): Promise<ModelCatalog> {
  try {
    const res = await apiFetch(`/models`);
    if (!res.ok) throw new Error("Models endpoint error");
    const data = await res.json();
    const serverModels: AIModel[] = (data.models || []).map((m: any) => {
      const id = m.id || m.name;
      const isFree = Boolean(m.is_free || id.startsWith("free:") || id === "alpha-free");
      return {
        id,
        name: m.display_name || m.name || id,
        provider: m.provider || (id.startsWith("free:") ? id.split(":")[1] : "Standard"),
        description: m.description || "",
        is_free: isFree,
        free_status: m.free_status || (isFree ? "no_key_free" : undefined),
        quota_type: m.quota_type || (isFree ? "keyless_free" : "paid"),
        // Read the capability fields the server actually sends. The previous
        // mapping read `supports_tools`/`supports_reasoning`, which
        // `ModelResponse` has never contained, so both stayed permanently
        // `undefined` and every capability badge read as unknown.
        supports_vision: m.supports_vision ?? null,
        supports_tools: m.supports_tools ?? null,
        supports_reasoning: m.supports_reasoning ?? m.supports_thinking ?? null,
        supports_reasoning_effort: m.supports_reasoning_effort ?? null,
        // Absent stays absent: an entry that declared no ladder must keep
        // reading as "no effort control" rather than "declares zero levels".
        ...(Array.isArray(m.reasoning_efforts) ? { reasoning_efforts: m.reasoning_efforts } : {}),
        ...(m.default_reasoning_effort ? { default_reasoning_effort: m.default_reasoning_effort } : {}),
        // `null` (not 0, not "") because these are "not reported" facts.
        context_window: typeof m.context_window === "number" ? m.context_window : null,
        ...(typeof m.input_price_per_million === "number" || typeof m.output_price_per_million === "number"
          ? {
              pricing: {
                currency: "USD",
                input_per_million: typeof m.input_price_per_million === "number" ? m.input_price_per_million : null,
                output_per_million: typeof m.output_price_per_million === "number" ? m.output_price_per_million : null,
              },
            }
          : {}),
      };
    });
    const seen = new Set(serverModels.map((m) => m.id));
    for (const fm of BUILTIN_FREE_MODELS) {
      if (!seen.has(fm.id)) {
        serverModels.push(fm);
        seen.add(fm.id);
      }
    }
    const levels = Array.isArray(data.reasoning_effort_levels) && data.reasoning_effort_levels.length > 0 ? data.reasoning_effort_levels : FALLBACK_EFFORT_LEVELS;
    const labels = data.reasoning_effort_labels && typeof data.reasoning_effort_labels === "object" ? data.reasoning_effort_labels : {};
    return { models: serverModels, reasoningEffortLevels: levels, reasoningEffortLabels: labels };
  } catch {
    return { models: BUILTIN_FREE_MODELS, reasoningEffortLevels: [...FALLBACK_EFFORT_LEVELS], reasoningEffortLabels: {} };
  }
}

export async function fetchAvailableModels(): Promise<AIModel[]> {
  return (await fetchModelCatalog()).models;
}

export async function fetchProvidersCatalog(): Promise<LLMProviderCatalogItem[]> {
  // Rejects on failure, with the server's own reason.
  //
  // This used to end in `catch { return [] }`, which made a broken gateway
  // indistinguishable from a Gateway that genuinely has no bring-your-own
  // providers. That matters because the only caller, SettingsSection, already
  // does the honest thing: it pairs this read with `.catch(...)` and drives a
  // `providersCatalogUnavailable` state off the rejection, with a comment
  // stating that an empty list must mean "the call failed" rather than "no
  // providers". The swallow made that branch unreachable - `ok` was always
  // `true`, the unavailable state never rendered, and the settings page
  // confidently listed zero providers.
  //
  // The catalog is the server's authority on which providers exist; a
  // hand-copied browser fallback is exactly the second source of truth that
  // drifts (it is how `union-alpha` came to advertise thinking support the
  // backend rejects). So there is nothing to fall back *to* - only something
  // to report.
  const res = await apiFetch(`/models/providers`);
  return await res.json();
}

export async function configureProviderCredentials(payload: {
  provider: string;
  api_key?: string;
  base_url?: string;
  model_id?: string;
  display_name?: string;
  remove?: boolean;
}): Promise<{
  success: boolean;
  provider: string;
  configured: boolean;
  masked_key?: string | null;
  message: string;
}> {
  const res = await apiFetch(`/models/providers/configure`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(errText || `Failed to configure provider (${res.status})`);
  }
  return res.json();
}

export async function probeFreeModels(): Promise<{
  probes: Record<string, any>;
  synced_models_count: number;
  available_models: any[];
}> {
  const res = await apiFetch(`/models/free/probe`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(`Probe failed (${res.status})`);
  }
  return res.json();
}

export async function fetchCommands(category?: string, coreOnly?: boolean): Promise<SlashCommandInfo[]> {
  try {
    const params = new URLSearchParams();
    if (category) params.append("category", category);
    if (coreOnly) params.append("core_only", "true");
    const res = await apiFetch(`/api/commands?${params.toString()}`);
    const data = await res.json();
    return data.commands || [];
  } catch (err) {
    // A failed registry read is NOT "the server has no commands". `apiFetch`
    // already rejects on every non-2xx and on a transport failure, carrying the
    // gateway's own `detail`; resolving `[]` here collapsed a down backend and a
    // 500 into the same value as a genuinely empty registry, so the composer
    // silently kept its built-in list and disclosed nothing. Propagate instead.
    // `lib/commands.ts` reads the same two routes and already does this.
    throw err instanceof Error ? err : new ApiClientError("response");
  }
}

export async function searchCommands(q: string): Promise<SlashCommandInfo[]> {
  try {
    const res = await apiFetch(`/api/commands/search?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    return data.commands || [];
  } catch (err) {
    // Same contract as fetchCommands: a failed search must not render as
    // "no command matches" — that is how a broken search looks like a real
    // "there is no such command".
    throw err instanceof Error ? err : new ApiClientError("response");
  }
}

export async function executeSlashCommand(
  command: string,
  context?: Record<string, unknown>
): Promise<SlashCommandResult> {
  const res = await apiFetch(`/api/commands/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, context }),
  });
  if (!res.ok) {
    throw new Error(`Command execution failed with status: ${res.status}`);
  }
  return res.json();
}

export async function autoTriggerCommand(
  prompt: string,
  phase?: string,
  autoExecute: boolean = true,
  context?: Record<string, unknown>
): Promise<AutonomousDetection> {
  const res = await apiFetch(`/api/commands/auto-trigger`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, phase, auto_execute: autoExecute, context }),
  });
  if (!res.ok) {
    throw new Error(`Auto trigger failed: ${res.status}`);
  }
  return res.json();
}


