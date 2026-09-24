// lib/protocols.ts — real-API clients for the inter-agent protocol plane.
//
// Routers:
//   /api/protocols/a2a                        (a2a.py — capability cards + delegation)
//   /api/threads/{thread_id}/agent-messages   (agent_messages.py — roster + direct messaging)
//   /api/scheduled-tasks/...                  (deliveries.py — delivery ledger, blueprints, incidents)
//   /api/input-polish                         (input_polish.py — pre-send prompt optimizer)
//
// Honesty contract:
//   * Envelopes map verbatim; absent lists stay empty, never fabricated rows.
//   * Unknown status/mode/kind values from the server pass through as strings
//     (the server owns the whitelists; the UI labels what it was told).
//   * Every failure rejects — a 404 "Delivery record not found" surfaces its
//     detail instead of rendering an empty ledger.
//   * In-process state (delivery ledger, roster) is stated as such in the UI,
//     never as durable storage.
import { get, send, asList, pick } from "./http";

/* ── A2A protocol (a2a.py) ──────────────────────────────────────────── */

export interface A2ACapabilityCard {
  agent_id: string;
  name: string;
  description: string;
  version: string;
  skills: string[];
  supported_protocols: string[];
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  auth_mode: string;
  /** available | busy | offline — as the registry reports it. */
  availability: string;
  endpoint_url: string | null;
  created_at: number | null;
}

function toCard(raw: Record<string, unknown>): A2ACapabilityCard {
  const created = pick(raw, ["created_at"], null);
  return {
    agent_id: String(pick(raw, ["agent_id"], "")),
    name: String(pick(raw, ["name"], "")),
    description: String(pick(raw, ["description"], "")),
    version: String(pick(raw, ["version"], "")),
    skills: asList(raw.skills, ["skills"]).map((s) => String(s)),
    supported_protocols: asList(raw.supported_protocols, ["supported_protocols"]).map((s) => String(s)),
    input_schema:
      raw.input_schema && typeof raw.input_schema === "object"
        ? (raw.input_schema as Record<string, unknown>)
        : {},
    output_schema:
      raw.output_schema && typeof raw.output_schema === "object"
        ? (raw.output_schema as Record<string, unknown>)
        : {},
    auth_mode: String(pick(raw, ["auth_mode"], "")),
    availability: String(pick(raw, ["availability"], "unknown")),
    endpoint_url: typeof raw.endpoint_url === "string" ? raw.endpoint_url : null,
    created_at: typeof created === "number" ? created : null,
  };
}

/** GET /api/protocols/a2a/cards?skill_filter= — the live federation registry. */
export async function listA2ACards(skillFilter?: string): Promise<A2ACapabilityCard[]> {
  const query = skillFilter ? `?skill_filter=${encodeURIComponent(skillFilter)}` : "";
  const d = await get<unknown>(`/protocols/a2a/cards${query}`);
  return asList(d, ["cards", "data"]).map((raw) => toCard(raw as Record<string, unknown>));
}

/** GET /api/protocols/a2a/cards/{agent_id} — 404 rejects with the registry's reason. */
export async function getA2ACard(agentId: string): Promise<A2ACapabilityCard> {
  const d = await get<Record<string, unknown>>(`/protocols/a2a/cards/${encodeURIComponent(agentId)}`);
  return toCard(d ?? {});
}

/** POST /api/protocols/a2a/cards — register a capability card. */
export async function registerA2ACard(card: {
  agent_id: string;
  name: string;
  description: string;
  skills?: string[];
  availability?: string;
  endpoint_url?: string | null;
}): Promise<{ status: string; card: A2ACapabilityCard }> {
  const d = await send<Record<string, unknown>>("/protocols/a2a/cards", "POST", {
    agent_id: card.agent_id,
    name: card.name,
    description: card.description,
    skills: card.skills ?? [],
    availability: card.availability ?? "available",
    endpoint_url: card.endpoint_url ?? null,
  });
  const raw = (d.card && typeof d.card === "object" ? d.card : {}) as Record<string, unknown>;
  return { status: String(pick(d, ["status"], "unknown")), card: toCard(raw) };
}

export interface A2ADelegation {
  request_id: string;
  /** accepted | completed | rejected | failed. */
  status: string;
  deliverable: unknown;
  evidence: string[];
  error: string | null;
  execution_seconds: number;
  timestamp: number | null;
}

/** POST /api/protocols/a2a/delegate — dispatch a real delegation request. */
export async function delegateA2ATask(payload: {
  sender_agent_id?: string;
  target_agent_id: string;
  task_objective: string;
  context_data?: Record<string, unknown>;
  deadline_seconds?: number;
}): Promise<A2ADelegation> {
  const d = await send<Record<string, unknown>>("/protocols/a2a/delegate", "POST", {
    sender_agent_id: payload.sender_agent_id ?? "gateway-client",
    target_agent_id: payload.target_agent_id,
    task_objective: payload.task_objective,
    context_data: payload.context_data ?? {},
    deadline_seconds: payload.deadline_seconds ?? 120.0,
  });
  const seconds = Number(pick(d, ["execution_seconds"], 0));
  const timestamp = pick(d, ["timestamp"], null);
  return {
    request_id: String(pick(d, ["request_id"], "")),
    status: String(pick(d, ["status"], "unknown")),
    deliverable: d.deliverable ?? null,
    evidence: asList(d.evidence, ["evidence"]).map((e) => String(e)),
    error: typeof d.error === "string" ? d.error : null,
    execution_seconds: Number.isFinite(seconds) ? seconds : 0,
    timestamp: typeof timestamp === "number" ? timestamp : null,
  };
}

/* ── Agent messages (agent_messages.py, thread-scoped) ──────────────── */

export interface RosterAgent {
  name: string;
  role: string;
  status: string;
  [key: string]: unknown;
}

function toRosterAgent(raw: Record<string, unknown>): RosterAgent {
  return {
    ...raw,
    name: String(pick(raw, ["name"], "")),
    role: String(pick(raw, ["role"], "")),
    status: String(pick(raw, ["status"], "unknown")),
  };
}

/** GET /api/threads/{thread_id}/agent-messages/roster — process-local roster. */
export async function listAgentRoster(threadId: string): Promise<{ thread_id: string; agents: RosterAgent[]; count: number }> {
  const d = await get<Record<string, unknown>>(`/threads/${encodeURIComponent(threadId)}/agent-messages/roster`);
  const agents = asList(d.agents, ["agents"]).map((a) => toRosterAgent(a as Record<string, unknown>));
  const count = Number(pick(d, ["count"], agents.length));
  return {
    thread_id: String(pick(d, ["thread_id"], threadId)),
    agents,
    count: Number.isFinite(count) ? count : agents.length,
  };
}

/** POST .../agent-messages/register — 201 with the new descriptor. */
export async function registerRosterAgent(
  threadId: string,
  body: { name: string; role?: string; status?: string; metadata?: Record<string, unknown> },
): Promise<RosterAgent> {
  const d = await send<Record<string, unknown>>(`/threads/${encodeURIComponent(threadId)}/agent-messages/register`, "POST", {
    name: body.name,
    role: body.role ?? "worker",
    status: body.status ?? "idle",
    metadata: body.metadata ?? null,
  });
  return toRosterAgent(d ?? {});
}

export interface AgentSendResult {
  thread_id: string;
  status: string;
  [key: string]: unknown;
}

/** POST .../agent-messages/messages — direct (auto/steer/follow_up) or broadcast ("all"). */
export async function sendAgentMessage(
  threadId: string,
  body: { sender_name: string; receiver_name: string; content: string; mode?: string; kind?: string },
): Promise<AgentSendResult> {
  const d = await send<Record<string, unknown>>(`/threads/${encodeURIComponent(threadId)}/agent-messages/messages`, "POST", {
    sender_name: body.sender_name,
    receiver_name: body.receiver_name,
    content: body.content,
    mode: body.mode ?? "auto",
    kind: body.kind ?? "message",
  });
  return {
    ...(d ?? {}),
    thread_id: String(pick(d, ["thread_id"], threadId)),
    status: String(pick(d, ["status"], "unknown")),
  };
}

/**
 * GET .../agent-messages/inbox?agent_name=X&mark_as_read=false
 * Defaults to mark_as_read=false so merely viewing the UI does not consume
 * another agent's messages.
 */
export async function getAgentInbox(
  threadId: string,
  agentName: string,
  markAsRead = false,
): Promise<{ thread_id: string; agent_name: string; messages: Array<Record<string, unknown>>; count: number }> {
  const d = await get<Record<string, unknown>>(
    `/threads/${encodeURIComponent(threadId)}/agent-messages/inbox?agent_name=${encodeURIComponent(agentName)}&mark_as_read=${markAsRead}`,
  );
  const messages = asList(d.messages, ["messages"]) as Array<Record<string, unknown>>;
  const count = Number(pick(d, ["count"], messages.length));
  return {
    thread_id: String(pick(d, ["thread_id"], threadId)),
    agent_name: String(pick(d, ["agent_name"], agentName)),
    messages,
    count: Number.isFinite(count) ? count : messages.length,
  };
}

/* ── Scheduled delivery ledger + blueprints + incidents (deliveries.py) ─ */

export interface DeliveryRecord {
  [key: string]: unknown;
  status?: string;
  occurrence_id?: string;
  channel?: string | null;
  artifact_ref?: string | null;
  error?: string | null;
}

/** GET /api/scheduled-tasks/{task_id}/deliveries — the exactly-once audit ledger. */
export async function listTaskDeliveries(
  taskId: string,
): Promise<{ task_id: string; deliveries: DeliveryRecord[]; count: number }> {
  const d = await get<Record<string, unknown>>(`/scheduled-tasks/${encodeURIComponent(taskId)}/deliveries`);
  const deliveries = asList(d.deliveries, ["deliveries"]) as DeliveryRecord[];
  const count = Number(pick(d, ["count"], deliveries.length));
  return {
    task_id: String(pick(d, ["task_id"], taskId)),
    deliveries,
    count: Number.isFinite(count) ? count : deliveries.length,
  };
}

/** POST .../deliveries/claim?occurrence_id= — 201 {delivery, is_new}. */
export async function claimTaskDelivery(
  taskId: string,
  occurrenceId: string,
): Promise<{ delivery: DeliveryRecord | null; is_new: boolean }> {
  const d = await send<Record<string, unknown>>(
    `/scheduled-tasks/${encodeURIComponent(taskId)}/deliveries/claim?occurrence_id=${encodeURIComponent(occurrenceId)}`,
    "POST",
  );
  const delivery = d.delivery && typeof d.delivery === "object" ? (d.delivery as DeliveryRecord) : null;
  return { delivery, is_new: d.is_new === true };
}

/** POST .../deliveries/{occurrence_id}/mark — status must be the server's own enum. */
export async function markTaskDelivery(
  taskId: string,
  occurrenceId: string,
  body: { status: string; artifact_ref?: string | null; channel?: string | null; error?: string | null },
): Promise<DeliveryRecord> {
  const d = await send<Record<string, unknown>>(
    `/scheduled-tasks/${encodeURIComponent(taskId)}/deliveries/${encodeURIComponent(occurrenceId)}/mark`,
    "POST",
    {
      status: body.status,
      artifact_ref: body.artifact_ref ?? null,
      channel: body.channel ?? null,
      error: body.error ?? null,
    },
  );
  return (d ?? {}) as DeliveryRecord;
}

/** GET /api/scheduled-tasks/blueprints — reusable scheduled-job templates. */
export async function listScheduledBlueprints(): Promise<Array<Record<string, unknown>>> {
  const d = await get<unknown>("/scheduled-tasks/blueprints");
  return asList(d, ["blueprints", "data"]) as Array<Record<string, unknown>>;
}

/** POST /api/scheduled-tasks/blueprints/{id}/launch — 201 with the created cron job. */
export async function launchScheduledBlueprint(
  blueprintId: string,
  values: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const d = await send<Record<string, unknown>>(
    `/scheduled-tasks/blueprints/${encodeURIComponent(blueprintId)}/launch`,
    "POST",
    { values },
  );
  return (d ?? {}) as Record<string, unknown>;
}

/** GET /api/scheduled-tasks/{task_id}/incidents?unresolved_only= — in-process tracker. */
export async function listTaskIncidents(
  taskId: string,
  unresolvedOnly = false,
): Promise<{ task_id: string; incidents: Array<Record<string, unknown>>; count: number }> {
  const d = await get<Record<string, unknown>>(
    `/scheduled-tasks/${encodeURIComponent(taskId)}/incidents?unresolved_only=${unresolvedOnly}`,
  );
  const incidents = asList(d.incidents, ["incidents"]) as Array<Record<string, unknown>>;
  const count = Number(pick(d, ["count"], incidents.length));
  return {
    task_id: String(pick(d, ["task_id"], taskId)),
    incidents,
    count: Number.isFinite(count) ? count : incidents.length,
  };
}

/** POST /api/scheduled-tasks/incidents/{id}/resolve — 404 when already resolved. */
export async function resolveTaskIncident(incidentId: string): Promise<Record<string, unknown>> {
  const d = await send<Record<string, unknown>>(
    `/scheduled-tasks/incidents/${encodeURIComponent(incidentId)}/resolve`,
    "POST",
  );
  return (d ?? {}) as Record<string, unknown>;
}

/* ── Input polish (input_polish.py) ────────────────────────────────── */

export interface InputPolishResult {
  rewritten_text: string;
  changed: boolean;
}

/** POST /api/input-polish — one real pre-send rewrite (never a canned suggestion). */
export async function polishInput(
  text: string,
  opts: { locale?: string | null; thread_id?: string | null } = {},
): Promise<InputPolishResult> {
  const d = await send<Record<string, unknown>>("/input-polish", "POST", {
    text,
    locale: opts.locale ?? null,
    thread_id: opts.thread_id ?? null,
  });
  return {
    rewritten_text: String(pick(d, ["rewritten_text"], "")),
    changed: d.changed === true,
  };
}
