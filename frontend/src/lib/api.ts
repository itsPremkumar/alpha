import { ChatMessage, Thread, AIModel, SlashCommandInfo, SlashCommandResult, AutonomousDetection } from "@/types/chat";

import { apiFetch } from "./api-client";

/**
 * Distinguishable fetch outcome: a FAILED load is never an empty success.
 * Callers must branch on `ok`; an empty `value` on the ok path means the
 * server really returned nothing.
 */
export type FetchResult<T> = { ok: true; value: T } | { ok: false; error: string };

function failed(err: unknown): { ok: false; error: string } {
  return { ok: false, error: err instanceof Error ? err.message : "Request failed" };
}

/**
 * Honest thread list: failure comes back as `{ ok: false }`, never as `[]`.
 * Use this from every surface that can show "you have no sessions".
 */
export async function fetchThreadsResult(limit = 100): Promise<FetchResult<Thread[]>> {
  try {
    // Backend has no GET /threads — listing lives at POST /threads/search,
    // which returns a bare array of ThreadResponse records.
    const res = await apiFetch(`/threads/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit }),
    });
    if (!res.ok) return { ok: false, error: `Thread list failed (HTTP ${res.status}).` };
    const data = await res.json();
    const list = Array.isArray(data) ? data : data.threads || [];
    return { ok: true, value: list.map((t: any) => ({
      thread_id: t.thread_id,
      // Server keeps the client-written title in metadata.title and the
      // auto-generated display name in values.title — neither is top-level.
      title: t.metadata?.title || t.values?.title || t.title || "Untitled Session",
      created_at: t.created_at || new Date().toISOString(),
      updated_at: t.updated_at || new Date().toISOString(),
      // Backend-owned bot association (thread metadata + assistant link).
      botName: t.metadata?.bot_name || t.bot_name || null,
      assistantId: t.assistant_id || null,
      projectId: t.metadata?.agent_workspace_project_id || t.project_id || null,
    })) };
  } catch (err) {
    console.error("Failed to fetch threads:", err);
    return failed(err);
  }
}

/**
 * LEGACY-COMPAT thread list: returns `[]` on failure, explicitly and ONLY for
 * ChatView.tsx (owned by the external agent — fenced), which calls this raw
 * inside un-awaited async helpers (ChatView.tsx:185 init Promise.all, :257
 * reloadThreads, :277 loadMessages); rejecting there would abort the whole
 * chat bootstrap. Every other/new caller must use fetchThreadsResult() and
 * render `{ok:false}` as an unavailable state. Known gap reported by the
 * wave-2 honesty audit: ChatView still merges a failed list as empty.
 */
export async function fetchThreads(limit = 100): Promise<Thread[]> {
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
  if (!res.ok) throw new Error("Failed to create thread");
  const data = await res.json();
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
          return JSON.stringify(blk).slice(0, 500);
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
export async function fetchThreadHistoryResult(threadId: string): Promise<FetchResult<ChatMessage[]>> {
  try {
    // GET /threads/{id}/messages returns a bare array of run-event rows:
    // {seq, run_id, event_type, category, content: {type, content, ...}, created_at, feedback?}
    const res = await apiFetch(`/threads/${encodeURIComponent(threadId)}/messages?limit=100`);
    if (!res.ok) return { ok: false, error: `Thread history failed (HTTP ${res.status}).` };
    const data = await res.json();
    const messages = Array.isArray(data) ? data : data.messages || [];
    return { ok: true, value: messages.flatMap((m: any, idx: number) => {
      // Event-store row shape (current backend).
      if (m && typeof m === "object" && ("event_type" in m || "seq" in m)) {
        const inner = m.content && typeof m.content === "object" ? m.content : {};
        const t = String(inner.type || "");
        const evt = String(m.event_type || "");
        let role: ChatMessage["role"] = "assistant";
        if (t === "human" || evt === "human_message") role = "user";
        else if (t === "system") role = "system";
        else if (t === "tool") role = "assistant";
        const text = textOf(inner.content ?? m.content ?? "");
        if (!text && t === "tool") return [];
        const fb = m.feedback as { rating?: unknown } | null | undefined;
        const rating = fb?.rating === 1 || fb?.rating === -1 ? fb.rating : undefined;
        return [
          {
            id: String(inner.id || (m.seq !== undefined ? `seq-${m.seq}` : `msg-${idx}`)),
            role,
            content: text,
            thinking: inner.additional_kwargs?.thinking || "",
            toolCalls: [
              ...((inner.tool_calls || []) as any[]).map((tc: any) => ({
                id: tc.id,
                name: tc.name,
                args: tc.args || {},
              })),
              ...((inner.invalid_tool_calls || []) as any[]).map((tc: any) => ({
                id: tc.id || `invalid-${idx}`,
                name: tc.name || "invalid_tool",
                args: tc.args || {},
                status: "failed" as const,
              })),
            ],
            createdAt: m.created_at || inner.created_at || new Date().toISOString(),
            runId: m.run_id || undefined,
            rating,
          } as ChatMessage,
        ];
      }
      // Legacy LangChain message shape (kept for cached/offline data).
      return [
        {
          id: m.id || `msg-${idx}`,
          role: m.type === "human" || m.role === "user" ? "user" : "assistant",
          content: typeof m.content === "string" ? m.content : JSON.stringify(m.content),
          thinking: m.additional_kwargs?.thinking || "",
          toolCalls: (m.tool_calls || []).map((tc: any) => ({
            id: tc.id,
            name: tc.name,
            args: tc.args || {},
          })),
          createdAt: m.created_at || new Date().toISOString(),
        } as ChatMessage,
      ];
    }) };
  } catch (err) {
    console.error("Failed to fetch thread history:", err);
    return failed(err);
  }
}

/**
 * LEGACY-COMPAT thread history: returns `[]` on failure, explicitly and ONLY
 * for ChatView.tsx (fenced — external agent), which calls this raw inside an
 * un-awaited loadMessages() helper (ChatView.tsx:277); rejecting there would
 * skip its local-cache fallback and stall message loading. threads-ext.ts
 * (owned) uses fetchThreadHistoryResult() instead. Known gap reported by the
 * wave-2 honesty audit: ChatView falls back to local cache / renders an empty
 * conversation when the gateway is down.
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

export async function fetchAvailableModels(): Promise<AIModel[]> {
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
        supports_tools: m.supports_tools,
        supports_reasoning: m.supports_reasoning || m.supports_thinking,
      };
    });
    const seen = new Set(serverModels.map((m) => m.id));
    for (const fm of BUILTIN_FREE_MODELS) {
      if (!seen.has(fm.id)) {
        serverModels.push(fm);
        seen.add(fm.id);
      }
    }
    return serverModels;
  } catch {
    return BUILTIN_FREE_MODELS;
  }
}

export async function fetchProvidersCatalog(): Promise<LLMProviderCatalogItem[]> {
  try {
    const res = await apiFetch(`/models/providers`);
    if (!res.ok) throw new Error("Providers catalog error");
    return await res.json();
  } catch {
    return [];
  }
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
    if (!res.ok) return [];
    const data = await res.json();
    return data.commands || [];
  } catch {
    return [];
  }
}

export async function searchCommands(q: string): Promise<SlashCommandInfo[]> {
  try {
    const res = await apiFetch(`/api/commands/search?q=${encodeURIComponent(q)}`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.commands || [];
  } catch {
    return [];
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


