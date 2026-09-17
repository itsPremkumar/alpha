import { ChatMessage, Thread, AIModel, SlashCommandInfo, SlashCommandResult, AutonomousDetection } from "@/types/chat";

import { apiFetch } from "./api-client";

export async function fetchThreads(limit = 100): Promise<Thread[]> {
  try {
    // Backend has no GET /threads — listing lives at POST /threads/search,
    // which returns a bare array of ThreadResponse records.
    const res = await apiFetch(`/threads/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit }),
    });
    if (!res.ok) return [];
    const data = await res.json();
    const list = Array.isArray(data) ? data : data.threads || [];
    return list.map((t: any) => ({
      thread_id: t.thread_id,
      // Server keeps the client-written title in metadata.title and the
      // auto-generated display name in values.title — neither is top-level.
      title: t.metadata?.title || t.values?.title || t.title || "Untitled Session",
      created_at: t.created_at || new Date().toISOString(),
      updated_at: t.updated_at || new Date().toISOString(),
      // Backend-owned bot association (thread metadata + assistant link).
      botName: t.metadata?.bot_name || t.bot_name || null,
      assistantId: t.assistant_id || null,
      projectId: t.metadata?.deerflow_project_id || t.project_id || null,
    }));
  } catch (err) {
    console.error("Failed to fetch threads:", err);
    return [];
  }
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

export async function fetchThreadHistory(threadId: string): Promise<ChatMessage[]> {
  try {
    // GET /threads/{id}/messages returns a bare array of run-event rows:
    // {seq, run_id, event_type, category, content: {type, content, ...}, created_at, feedback?}
    const res = await apiFetch(`/threads/${encodeURIComponent(threadId)}/messages?limit=100`);
    if (!res.ok) return [];
    const data = await res.json();
    const messages = Array.isArray(data) ? data : data.messages || [];
    return messages.flatMap((m: any, idx: number) => {
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
    });
  } catch (err) {
    console.error("Failed to fetch thread history:", err);
    return [];
  }
}

export async function fetchAvailableModels(): Promise<AIModel[]> {
  try {
    const res = await apiFetch(`/models`);
    if (!res.ok) throw new Error("Models endpoint error");
    const data = await res.json();
    return (data.models || []).map((m: any) => ({
      id: m.id || m.name,
      name: m.display_name || m.name || m.id,
      provider: m.provider || "Standard",
      description: m.description || "",
    }));
  } catch {
    // Live data only: no fabricated model list. Callers fall back to the
    // server-resolved "default" model until the Gateway is reachable.
    return [];
  }
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


