/**
 * Outcome actually reported by the run for one tool call.
 * `undefined` on a ToolCall means "nothing was reported yet" — callers MUST
 * NOT render that as success (see `toolStatusView` in ToolPill.tsx).
 *
 * This is the UI vocabulary. The wire vocabulary the Gateway/backend stamps on
 * a tool result is `ToolCallVerdict`; `sse-reducer.ts` translates one into the
 * other and is the single place that mapping lives.
 */
export type ToolCallStatus = "running" | "completed" | "failed" | "partial" | "error" | "unknown";

/**
 * Verdict vocabulary the backend writes onto a tool result, in precedence
 * order: `ToolMessage.status === "error"`, then the
 * `agent_workspace_tool_meta` stamp, then the `agent_workspace_tool_receipt`
 * stamp, then a structured `subagent_status` failure, then the bare
 * `ToolMessage.status` field.
 *
 * Note the trap this encodes: LangChain defaults `ToolMessage.status` to
 * `"success"`, so a bare `"success"` is the weakest possible evidence. It maps
 * to `completed` only after nothing stronger said otherwise, and a result whose
 * status is absent/unrecognized resolves to `"unknown"` — never to `completed`.
 */
export type ToolCallVerdict = "success" | "partial_success" | "error" | "failed" | "unknown";

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  /** Verbatim tool output (result text, or the error text) when the run reported one. */
  output?: string;
  /**
   * Observed status, derived only from what the run actually reported.
   * Absent = no result for this call has arrived yet; never assume
   * "completed". `unknown` = a result arrived but carried no verdict anyone
   * could resolve, which is also never success.
   */
  status?: ToolCallStatus;
}

export interface TodoItem {
  id: string;
  title: string;
  status: "pending" | "in_progress" | "completed" | "failed";
}

export interface ArtifactItem {
  id: string;
  name: string;
  type: string;
  content: string;
  language?: string;
}

export interface HumanApproval {
  id: string;
  toolName: string;
  args: Record<string, unknown>;
  prompt: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  thinking?: string;
  toolCalls?: ToolCall[];
  todos?: TodoItem[];
  artifacts?: ArtifactItem[];
  approvalRequest?: HumanApproval;
  autonomousDetection?: AutonomousDetection;
  createdAt: string;
  /** Durable thread-global message sequence when supplied by the Gateway. */
  sequence?: number;
  /** Complete normalized Gateway event, retained by the on-device archive. */
  raw?: unknown;
  /** Newest run that produced this message (enables feedback + stop). */
  runId?: string;
  /** Your rating for this answer (+1 / -1). */
  rating?: 1 | -1;
}

export interface Thread {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  status?: string;
  /** Owning specialist bot (from server thread metadata). Absent = Lead Agent / unassigned. */
  botName?: string | null;
  assistantId?: string | null;
  /** Server project membership (read-only metadata exposure). */
  projectId?: string | null;
}

export interface AIModel {
  id: string;
  name: string;
  provider: string;
  description?: string;
  is_free?: boolean;
  free_status?: string;
  quota_type?: string;
  rpm?: number;
  rpd?: number;
  reset_interval?: string;
  supports_tools?: boolean;
  supports_reasoning?: boolean;
}

export interface SlashCommandInfo {
  command: string;
  category: string;
  description: string;
  usage: string;
  is_core: boolean;
  is_autonomous_trigger: boolean;
  requires_approval: boolean;
}

export interface SlashCommandResult {
  status: string;
  command: string;
  output: string;
  data: Record<string, unknown>;
  autonomous_directives?: string[];
}

export interface AutonomousDetection {
  matched: boolean;
  command: string;
  phase: string;
  confidence: number;
  reason: string;
  rule_id: string;
  autonomous_directives?: string[];
  execution_result?: SlashCommandResult;
}


