export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  output?: string;
  status?: "running" | "completed" | "failed";
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


