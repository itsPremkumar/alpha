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
 * `alpha_tool_meta` stamp, then the `alpha_tool_receipt`
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

/**
 * One item of the live execution plan, as the model reported it.
 *
 * The wire shape is fixed by `alpha.agents.todo_events.TodoItem` (`id`,
 * `content`, `status`, `index`) and is normalized on the backend before it is
 * emitted, so the UI never has to guess at a field. `content` rather than
 * `title` is the model's own field name on `LangChain`'s `Todo`, and the id is
 * a hash of that content rather than a list index: `write_todos` replaces the
 * whole list on every call, so an index key would renumber every item after an
 * insertion and visibly rewrite an unrelated task's status.
 *
 * `cancelled` is Alpha's addition to the model's three statuses. The tool
 * contract explicitly lets the model drop items that are "no longer relevant",
 * and a UI that cannot say "dropped" will keep showing abandoned work as
 * permanently pending — which reads as a stuck agent.
 *
 * There is deliberately no `failed` here. The model never reports that a plan
 * step failed; only `cancelled` is an observable outcome, and inventing a fifth
 * state would put a claim on screen that no run ever reported.
 */
export type TodoStatus = "pending" | "in_progress" | "completed" | "cancelled";

export interface TodoItem {
  id: string;
  /** Verbatim step text as the model wrote it. */
  content: string;
  status: TodoStatus;
  /** Position in the plan, as sent by the backend. Not used as a React key. */
  index: number;
}

/**
 * Counters for a plan, computed on the backend over exactly the items that were
 * sent. `reported` and `truncated` travel with the list so the UI can say
 * "showing 200 of 340" rather than presenting a clipped plan as complete.
 *
 * `settled` counts completed *and* cancelled: both are outcomes the run
 * reached, which is what a user waiting on a long task actually needs to know.
 */
export interface TodoProgress {
  total: number;
  completed: number;
  in_progress: number;
  pending: number;
  cancelled: number;
  settled: number;
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
  createdAt: string | null;
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
  /**
   * Accepts image input. `null`/absent means *not reported*, which is not the
   * same as `false` — see `lib/model-capabilities.ts`.
   */
  supports_vision?: boolean | null;
  supports_tools?: boolean | null;
  supports_reasoning?: boolean | null;
  /** Server-declared acceptance of a reasoning-effort level. */
  supports_reasoning_effort?: boolean | null;
  /**
   * Effective input window in tokens as this deployment configures it, or
   * `null` when undeclared. The endpoint-enforced figure, when discovery
   * reports one, wins over this in `modelCapabilities`.
   */
  context_window?: number | null;
  /** Per-1M token prices in the deployment's configured currency. */
  pricing?: {
    currency?: string | null;
    input_per_million?: number | null;
    output_per_million?: number | null;
  } | null;
  /**
   * Reasoning-effort rungs this model actually serves, weakest first, as
   * reported by the server. `null`/absent means the entry declared no ladder,
   * which is a real answer: the effort picker must be hidden rather than
   * offering rungs the factory would silently clamp. Never coerce this to an
   * empty-object or a default rung.
   */
  reasoning_efforts?: string[] | null;
  /**
   * The rung used when a run requests no explicit effort. `null` means the
   * provider's own default applies.
   */
  default_reasoning_effort?: string | null;
}

export interface SlashCommandInfo {
  command: string;
  category: string;
  description: string;
  usage: string;
  is_core: boolean;
  is_autonomous_trigger: boolean;
  requires_approval: boolean;
  /**
   * Whether the registry reported a bound handler for this row.
   *
   * Tri-state, and `false` is not the same as absent: the registry returns it
   * because `execute_slash_command` answers a handler-less row with
   * `unimplemented` rather than success. Omitted means the read did not report
   * it, which is not a measurement and must not render as one.
   */
  has_handler?: boolean | null;
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


