import { get } from "./http";

/**
 * The full end-to-end read of ONE project.
 *
 * `ProjectsSection`'s card answered "which projects exist, and how many chats and
 * bots does this one hold". Everything else the Gateway knows about a project was
 * spread across ~20 read-only `GET /projects/{id}/*` routes, and the ones that
 * were reachable lived in other views that keep their own project picker — one of
 * them defaults to the FIRST project in the list, so "open my project's state"
 * routinely answered with a different project.
 *
 * This module is the single client for that whole surface, used by
 * `ProjectInspectorSection`. Two rules shape it:
 *
 *  1. **Every section is read independently.** There is no aggregate promise and
 *     no all-or-nothing read: a 404 on an older Gateway, a 403 on a locked-down
 *     deployment, or one transient timeout blanks exactly one block and states
 *     its own reason. Everything that answered still renders.
 *  2. **Absent is never zero.** A count the server did not send is `null` and
 *     renders as unknown; `last_verified: null` is "never verified", which is not
 *     the same claim as "not verified yet"; an unreadable value is reported as
 *     unreadable rather than coerced to a plausible-looking default.
 *
 * It is strictly read-only. Nothing here issues a POST/PATCH/PUT/DELETE, so the
 * inspector cannot become a second control surface for something the Workforce
 * view already owns.
 */

export type InspectorSection<T> =
  | { status: "ok"; data: T }
  | { status: "error"; error: string };

const enc = (id: string): string => encodeURIComponent(id);

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;

/** A string, or `null` when the field was absent. Never `""` for "unknown". */
const str = (record: Record<string, unknown>, key: string): string | null => {
  const value = record[key];
  return typeof value === "string" ? value : null;
};

/** A finite number, or `null`. Never coerced from a string or defaulted to 0. */
const num = (record: Record<string, unknown>, key: string): number | null => {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
};

/** A boolean, or `null`. A missing flag is unknown, not `false`. */
const bool = (record: Record<string, unknown>, key: string): boolean | null => {
  const value = record[key];
  return typeof value === "boolean" ? value : null;
};

/** A list of records; an absent or non-array value is an empty list. */
const rows = (value: unknown): Array<Record<string, unknown>> =>
  Array.isArray(value) ? (value as Array<Record<string, unknown>>) : [];

/** A list of strings; non-strings are dropped rather than stringified. */
const texts = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string") : [];

/** A `Record<string, string>` projection; a non-object value is empty. */
const textMap = (value: unknown): Record<string, string> => {
  const record = asRecord(value);
  if (!record) return {};
  const out: Record<string, string> = {};
  for (const [key, entry] of Object.entries(record)) {
    if (typeof entry === "string") out[key] = entry;
  }
  return out;
};

/** String arrays keyed by name, e.g. `pending_mentions: {bot: [...]}`. */
const textListMap = (value: unknown): Record<string, Array<Record<string, unknown>>> => {
  const record = asRecord(value);
  if (!record) return {};
  const out: Record<string, Array<Record<string, unknown>>> = {};
  for (const [key, entry] of Object.entries(record)) {
    if (Array.isArray(entry)) out[key] = rows(entry);
  }
  return out;
};

/**
 * Read one section, turning a rejection into the server's own reason.
 *
 * The message is carried raw so this module stays free of UI concerns; the
 * component runs it through `errMsg` when it renders it.
 */
async function section<T>(read: () => Promise<T>): Promise<InspectorSection<T>> {
  try {
    return { status: "ok", data: await read() };
  } catch (error) {
    return { status: "error", error: error instanceof Error ? error.message : String(error) };
  }
}

// ---------------------------------------------------------------------------
// Identity — the project row itself
// ---------------------------------------------------------------------------

export interface ProjectRecord {
  id: string;
  name: string;
  instructions: string;
  /** Free-form server-owned presentation metadata, kept verbatim. */
  presentation: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
}

async function fetchProjectRecord(projectId: string): Promise<ProjectRecord> {
  const body = await get<unknown>(`/projects/${enc(projectId)}`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable project record.");
  const id = record.id;
  const name = record.name;
  // A row without an id or a name cannot be shown, and silently naming it would
  // be worse than reporting the envelope as unreadable.
  if (typeof id !== "string" || !id || typeof name !== "string" || !name) {
    throw new Error("The server returned a project record without an id or a name.");
  }
  return {
    id,
    name,
    instructions: str(record, "instructions") ?? "",
    presentation: asRecord(record.presentation) ?? {},
    status: str(record, "status") ?? "",
    created_at: str(record, "created_at") ?? "",
    updated_at: str(record, "updated_at") ?? "",
  };
}

// ---------------------------------------------------------------------------
// Lifecycle state — the folded event log
// ---------------------------------------------------------------------------

export interface ProjectStateDigest {
  project_id: string | null;
  goal: string;
  phase: string;
  arch_version: string;
  active_agents: number | null;
  active_tasks: number | null;
  blocked_tasks: number | null;
  completed_tasks: number | null;
  failed_tasks: number | null;
  open_conflicts: number | null;
  open_risks: string[];
  /** `null` is "never verified" — not the same claim as "not verified yet". */
  last_verified: string | null;
  latest_decision: string | null;
  updated_at: string;
}

async function fetchState(projectId: string): Promise<ProjectStateDigest> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/state`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable project state.");
  // A risk arrives as a bare string on some paths and as an object on others;
  // both shapes are read, because a risk rendered as a blank bullet hides it.
  return {
    project_id: str(record, "project_id"),
    goal: str(record, "goal") ?? "",
    phase: str(record, "phase") ?? "",
    arch_version: str(record, "arch_version") ?? "",
    active_agents: num(record, "active_agents"),
    active_tasks: num(record, "active_tasks"),
    blocked_tasks: num(record, "blocked_tasks"),
    completed_tasks: num(record, "completed_tasks"),
    failed_tasks: num(record, "failed_tasks"),
    open_conflicts: num(record, "open_conflicts"),
    open_risks: (Array.isArray(record.open_risks) ? record.open_risks : []).map((risk) =>
      typeof risk === "string"
        ? risk
        : String(asRecord(risk)?.description ?? asRecord(risk)?.title ?? asRecord(risk)?.risk ?? ""),
    ),
    last_verified: str(record, "last_verified"),
    latest_decision: str(record, "latest_decision"),
    updated_at: str(record, "updated_at") ?? "",
  };
}

// ---------------------------------------------------------------------------
// Crew — members, the group room, and the coordination policy, in one read
// ---------------------------------------------------------------------------

export interface CrewMember {
  bot_name: string;
  role_in_project: string;
  /** The server's own enum string, kept verbatim. */
  status: string;
  current_task_id: string | null;
  blocked_reason: string | null;
  last_activity: string;
}

export interface CrewRoom {
  name: string;
  mode: string;
  moderator: string | null;
  members: string[];
  message_count: number | null;
  /** History kept, room inactive. Distinct from "no room". */
  parked: boolean | null;
}

export interface CollaborationSettings {
  orchestration_mode: string;
  moderator: string | null;
  max_concurrent_speakers: number | null;
  mention_policy: string;
  auto_handoff: boolean | null;
  conflict_policy: string;
  lock_policy: string;
  require_evidence: boolean | null;
  memory_budget_chars: number | null;
  transcript_digest_n: number | null;
  standup_interval_turns: number | null;
}

export interface CrewView {
  project_id: string;
  members: CrewMember[];
  /**
   * `null` is a real answer (a crew under two members has no room yet).
   * An *unreadable* room rejects instead, because "solo" and "unreadable" are
   * opposite claims and collapsing them hides a broken deployment.
   */
  room: CrewRoom | null;
  collaboration: CollaborationSettings;
  shared_memory: Record<string, unknown>;
  state: Record<string, unknown>;
  active_locks: Array<Record<string, unknown>>;
  recent_events: Array<Record<string, unknown>>;
  updated_at: string;
}

function toCrewMember(row: Record<string, unknown>, index: number): CrewMember {
  const botName = row.bot_name;
  // A member with no bot name cannot be rendered, let alone detached, so it is
  // rejected rather than shown as an agent nobody could remove.
  if (typeof botName !== "string" || !botName.trim()) {
    throw new Error(`The server returned project crew member ${index + 1} without a bot name.`);
  }
  return {
    bot_name: botName,
    role_in_project: str(row, "role_in_project") ?? "",
    status: str(row, "status") ?? "",
    current_task_id: str(row, "current_task_id"),
    blocked_reason: str(row, "blocked_reason"),
    last_activity: str(row, "last_activity") ?? "",
  };
}

function toCrewRoom(value: unknown): CrewRoom | null {
  if (value === null || value === undefined) return null;
  const record = asRecord(value);
  if (!record) throw new Error("The server returned an unreadable project room.");
  return {
    name: str(record, "name") ?? "",
    mode: str(record, "mode") ?? "",
    moderator: str(record, "moderator"),
    members: texts(record.members),
    message_count: num(record, "message_count"),
    parked: bool(record, "parked"),
  };
}

function toCollaboration(value: unknown): CollaborationSettings {
  const record = asRecord(value);
  // `orchestration_mode` is never defaulted: it is the one field the settings
  // form keys its options off, so an absent mode is an unreadable envelope
  // rather than a silent "moderated".
  if (!record || typeof record.orchestration_mode !== "string" || !record.orchestration_mode) {
    throw new Error("The server returned collaboration settings without an orchestration mode.");
  }
  return {
    // Preserved verbatim, so a mode from a newer Gateway is displayed rather
    // than snapped to a value this build happens to know.
    orchestration_mode: record.orchestration_mode,
    moderator: str(record, "moderator"),
    max_concurrent_speakers: num(record, "max_concurrent_speakers"),
    mention_policy: str(record, "mention_policy") ?? "",
    auto_handoff: bool(record, "auto_handoff"),
    conflict_policy: str(record, "conflict_policy") ?? "",
    lock_policy: str(record, "lock_policy") ?? "",
    require_evidence: bool(record, "require_evidence"),
    memory_budget_chars: num(record, "memory_budget_chars"),
    transcript_digest_n: num(record, "transcript_digest_n"),
    standup_interval_turns: num(record, "standup_interval_turns"),
  };
}

async function fetchCrew(projectId: string): Promise<CrewView> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/crew`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable project crew.");
  const projectId_ = record.project_id;
  if (typeof projectId_ !== "string" || !projectId_) {
    throw new Error("The server returned a project crew without a project id.");
  }
  if (!Array.isArray(record.members)) {
    throw new Error("The server returned a project crew without a member list.");
  }
  return {
    project_id: projectId_,
    members: rows(record.members).map(toCrewMember),
    room: toCrewRoom(record.room),
    collaboration: toCollaboration(record.collaboration),
    shared_memory: asRecord(record.shared_memory) ?? {},
    state: asRecord(record.state) ?? {},
    active_locks: rows(record.active_locks),
    recent_events: rows(record.recent_events),
    updated_at: str(record, "updated_at") ?? "",
  };
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------

export interface ProjectConversation {
  thread_id: string;
  /** Absent is a real state; the raw id is shown rather than an invented title. */
  display_name: string | null;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown> | null;
}

/** Server `limit` ceiling for `GET /projects/{id}/threads`. */
const THREAD_PAGE_SIZE = 500;

/**
 * Every conversation the project holds, following all offset pages.
 *
 * The route caps `limit` at 1000, so a project with more chats than one page
 * would silently lose the tail. A page that repeats the previous boundary means
 * the offset is not being honoured; that is reported, not looped on.
 */
async function fetchConversations(projectId: string): Promise<ProjectConversation[]> {
  const all: ProjectConversation[] = [];
  const seen = new Set<string>();
  let offset = 0;
  let previousBoundary = "";
  for (;;) {
    const query = new URLSearchParams({ limit: String(THREAD_PAGE_SIZE), offset: String(offset) });
    const body = await get<unknown>(`/projects/${enc(projectId)}/threads?${query.toString()}`);
    if (!Array.isArray(body)) throw new Error("The server returned an unreadable project conversation list.");
    const mapped = body.map((entry, index) => {
      const record = asRecord(entry);
      if (!record) throw new Error(`The server returned an unreadable conversation at position ${index + 1}.`);
      const threadId = str(record, "thread_id") ?? str(record, "id");
      if (!threadId) throw new Error("The server returned a project conversation without an id.");
      return {
        thread_id: threadId,
        display_name: str(record, "display_name") ?? str(record, "title"),
        created_at: str(record, "created_at") ?? "",
        updated_at: str(record, "updated_at") ?? "",
        metadata: asRecord(record.metadata),
      };
    });
    const boundary = mapped.length > 0 ? `${mapped[0].thread_id}:${mapped[mapped.length - 1].thread_id}` : "";
    if (mapped.length === THREAD_PAGE_SIZE && boundary && boundary === previousBoundary) {
      throw new Error("Project conversation pagination did not advance; stopped to avoid an endless loop.");
    }
    previousBoundary = boundary;
    for (const conversation of mapped) {
      if (seen.has(conversation.thread_id)) continue;
      seen.add(conversation.thread_id);
      all.push(conversation);
    }
    if (mapped.length < THREAD_PAGE_SIZE) break;
    offset += mapped.length;
  }
  return all;
}

// ---------------------------------------------------------------------------
// Shared memory (level 2) as an agent sees it
// ---------------------------------------------------------------------------

export interface TranscriptMessage {
  from: string;
  intent: string;
  text: string;
  at: string;
}

export interface TranscriptDigest {
  messages: TranscriptMessage[];
  total: number | null;
  compacted_upto: number | null;
  summary: string | null;
}

export interface ProjectMemory {
  project_id: string;
  goal: string | null;
  phase: string | null;
  recent_decisions: Array<Record<string, unknown>>;
  active_locks: string[];
  constitution_hash: string;
  transcript_digest: TranscriptDigest | null;
  open_questions: Array<Record<string, unknown>>;
  /** One line per agent: its most recent contribution. */
  member_briefs: Record<string, string>;
  /** Unanswered @mentions per agent — the per-agent work queue. */
  pending_mentions: Record<string, Array<Record<string, unknown>>>;
  updated_at: string;
}

async function fetchMemory(projectId: string): Promise<ProjectMemory> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/memory`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable project memory block.");
  // The route always echoes the project it read, so its absence means this is not
  // the memory envelope — an unreadable answer, not "nothing shared yet".
  if (typeof record.project_id !== "string" || !record.project_id) {
    throw new Error("The server returned a project memory block without a project id.");
  }
  // The digest is a structured block. Anything else is a shape this build cannot
  // read, so it stays null rather than being stringified into a plausible lie.
  const digest = (): TranscriptDigest | null => {
    const block = asRecord(record.transcript_digest);
    if (!block) return null;
    return {
      messages: rows(block.messages).map((entry) => ({
        from: str(entry, "from") ?? "",
        intent: str(entry, "intent") ?? "",
        text: str(entry, "text") ?? "",
        at: str(entry, "at") ?? "",
      })),
      total: num(block, "total"),
      compacted_upto: num(block, "compacted_upto"),
      summary: str(block, "summary"),
    };
  };
  return {
    project_id: record.project_id,
    goal: str(record, "goal"),
    phase: str(record, "phase"),
    recent_decisions: rows(record.recent_decisions),
    active_locks: texts(record.active_locks),
    // "none" is the server's explicit no-constitution marker, not a real hash.
    constitution_hash: str(record, "constitution_hash") ?? "",
    transcript_digest: digest(),
    open_questions: rows(record.open_questions),
    // A bot with no recorded contribution must be ABSENT, not given an empty brief.
    member_briefs: textMap(record.member_briefs),
    pending_mentions: textListMap(record.pending_mentions),
    updated_at: str(record, "updated_at") ?? "",
  };
}

// ---------------------------------------------------------------------------
// Constitution and the role-filtered context projection
// ---------------------------------------------------------------------------

export interface ProjectConstitution {
  present: boolean | null;
  /** Present when the project has one; absent when only a seed template exists. */
  markdown: string | null;
  sha16: string | null;
  updated_at: string;
  /** The seed the Gateway would write from, when it sent one. */
  template: string | null;
}

async function fetchConstitution(projectId: string): Promise<ProjectConstitution> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/constitution`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable constitution.");
  return {
    // `present` is the server's claim. Absent means we do not know, not false.
    present: bool(record, "present"),
    markdown: str(record, "markdown"),
    sha16: str(record, "sha16"),
    updated_at: str(record, "updated_at") ?? "",
    template: str(record, "template"),
  };
}

export interface ProjectContext {
  project_id: string;
  bot_role: string;
  /** Role-filtered sections, verbatim. Which sections exist depends on the role. */
  sections: Record<string, unknown>;
  constitution_hash: string | null;
}

async function fetchContext(projectId: string, botRole: string): Promise<ProjectContext> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/context?bot_role=${enc(botRole)}`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable project context.");
  return {
    project_id: str(record, "project_id") ?? "",
    bot_role: str(record, "bot_role") ?? botRole,
    sections: asRecord(record.sections) ?? {},
    constitution_hash: str(record, "constitution_hash"),
  };
}

// ---------------------------------------------------------------------------
// Activity — decisions (ADR) and the append-only event log
// ---------------------------------------------------------------------------

export interface ProjectDecision {
  id: string;
  title: string;
  body: string;
  reason: string;
  made_by: string;
  arch_version: string;
  approved_by: string | null;
  created_at: string;
}

async function fetchDecisions(projectId: string): Promise<ProjectDecision[]> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/decisions`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable decisions list.");
  return rows(record.decisions).map((row, index) => ({
    id: str(row, "decision_id") ?? str(row, "id") ?? `decision-${index}`,
    title: str(row, "title") ?? "",
    body: str(row, "body") ?? "",
    reason: str(row, "reason") ?? "",
    made_by: str(row, "made_by") ?? "",
    arch_version: str(row, "arch_version") ?? "",
    approved_by: str(row, "approved_by"),
    created_at: str(row, "created_at") ?? "",
  }));
}

export interface ProjectEvent {
  id: string;
  seq: number | null;
  type: string;
  actor: string;
  created_at: string;
  payload: Record<string, unknown> | null;
}

async function fetchEvents(projectId: string): Promise<ProjectEvent[]> {
  // The route caps `limit` at 1000 and `after_seq` at >= 0.
  const body = await get<unknown>(`/projects/${enc(projectId)}/events?after_seq=0&limit=1000`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable event feed.");
  return rows(record.events)
    .map((row, index) => ({
      id: str(row, "event_id") ?? str(row, "id") ?? `event-${index}`,
      seq: num(row, "seq"),
      type: str(row, "type") ?? "",
      actor: str(row, "actor") ?? "",
      created_at: str(row, "created_at") ?? "",
      payload: asRecord(row.payload),
    }))
    // Newest first, because the point of a feed is what just happened. An
    // undated row sorts last rather than first, so a row the server did not
    // date cannot displace a real recent event.
    .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
}

// ---------------------------------------------------------------------------
// Coordination — locks, handoffs, approvals, checkpoints
// ---------------------------------------------------------------------------

export interface ResourceLock {
  lock_id: string;
  scope: string;
  path: string;
  owner_bot: string;
  reason: string;
  created_at: number | null;
  expires_at: number | null;
}

export interface LockAccessRequest {
  request_id: string;
  lock_id: string;
  requester_bot: string;
  scope: string;
  path: string;
  mode: string;
  status: string;
  created_at: number | null;
}

export interface LocksView {
  locks: ResourceLock[];
  pending_requests: LockAccessRequest[];
}

async function fetchLocks(projectId: string): Promise<LocksView> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/locks`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable lock list.");
  return {
    locks: rows(record.locks).map((row, index) => ({
      lock_id: str(row, "lock_id") ?? `lock-${index}`,
      scope: str(row, "scope") ?? "",
      path: str(row, "path") ?? "",
      owner_bot: str(row, "owner_bot") ?? "",
      reason: str(row, "reason") ?? "",
      created_at: num(row, "created_at"),
      expires_at: num(row, "expires_at"),
    })),
    pending_requests: rows(record.pending_requests).map((row, index) => ({
      request_id: str(row, "request_id") ?? `request-${index}`,
      lock_id: str(row, "lock_id") ?? "",
      requester_bot: str(row, "requester_bot") ?? "",
      scope: str(row, "scope") ?? "",
      path: str(row, "path") ?? "",
      mode: str(row, "mode") ?? "",
      status: str(row, "status") ?? "",
      created_at: num(row, "created_at"),
    })),
  };
}

export interface HandoffRecord {
  handoff_id: string;
  task_id: string;
  from_bot: string;
  to_bot: string;
  objective: string;
  completed_work: string;
  findings: string;
  files_modified: string[];
  decisions: string[];
  remaining_work: string;
  known_risks: string[];
  tests: string[];
  recommended_next_action: string;
  status: string;
  created_at: string;
  accepted_at: string | null;
}

async function fetchHandoffs(projectId: string): Promise<HandoffRecord[]> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/handoffs`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable handoff list.");
  return rows(record.handoffs).map((row, index) => ({
    handoff_id: str(row, "handoff_id") ?? `handoff-${index}`,
    task_id: str(row, "task_id") ?? "",
    from_bot: str(row, "from_bot") ?? "",
    to_bot: str(row, "to_bot") ?? "",
    objective: str(row, "objective") ?? "",
    completed_work: str(row, "completed_work") ?? "",
    findings: str(row, "findings") ?? "",
    files_modified: texts(row.files_modified),
    decisions: texts(row.decisions),
    remaining_work: str(row, "remaining_work") ?? "",
    known_risks: texts(row.known_risks),
    tests: texts(row.tests),
    recommended_next_action: str(row, "recommended_next_action") ?? "",
    status: str(row, "status") ?? "",
    created_at: str(row, "created_at") ?? "",
    accepted_at: str(row, "accepted_at"),
  }));
}

export interface ApprovalRequest {
  request_id: string;
  bot_name: string;
  action_type: string;
  risk_level: string;
  details: Record<string, unknown>;
  diff_preview: string | null;
  status: string;
  resolution_comment: string;
  resolved_by: string | null;
  created_at: string;
  resolved_at: string | null;
}

async function fetchApprovals(projectId: string): Promise<ApprovalRequest[]> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/approvals`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable approval queue.");
  return rows(record.approvals).map((row, index) => ({
    request_id: str(row, "request_id") ?? `approval-${index}`,
    bot_name: str(row, "bot_name") ?? "",
    action_type: str(row, "action_type") ?? "",
    risk_level: str(row, "risk_level") ?? "",
    details: asRecord(row.details) ?? {},
    diff_preview: str(row, "diff_preview"),
    status: str(row, "status") ?? "",
    resolution_comment: str(row, "resolution_comment") ?? "",
    resolved_by: str(row, "resolved_by"),
    created_at: str(row, "created_at") ?? "",
    resolved_at: str(row, "resolved_at"),
  }));
}

export interface WorkspaceCheckpoint {
  checkpoint_id: string;
  tag: string;
  created_at: number | null;
  timestamp_iso: string;
  members: number | null;
  locks: number | null;
  contracts: number | null;
  worktrees: number | null;
  metadata: Record<string, unknown>;
}

async function fetchCheckpoints(projectId: string): Promise<WorkspaceCheckpoint[]> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/checkpoints`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable checkpoint list.");
  return rows(record.checkpoints).map((row, index) => ({
    checkpoint_id: str(row, "checkpoint_id") ?? `checkpoint-${index}`,
    tag: str(row, "tag") ?? "",
    created_at: num(row, "created_at"),
    timestamp_iso: str(row, "timestamp_iso") ?? "",
    // The snapshot arrays are counted, not inlined: an operator wants "how much
    // was captured", and a checkpoint with ten locks does not need ten rows here.
    members: Array.isArray(row.members_snapshot) ? row.members_snapshot.length : null,
    locks: Array.isArray(row.active_locks) ? row.active_locks.length : null,
    contracts: Array.isArray(row.contracts) ? row.contracts.length : null,
    worktrees: Array.isArray(row.worktrees) ? row.worktrees.length : null,
    metadata: asRecord(row.metadata) ?? {},
  }));
}

// ---------------------------------------------------------------------------
// The aggregate War Room dashboard — 20 subsystems in one read
// ---------------------------------------------------------------------------

export interface WarRoomContract {
  task_id: string;
  title: string;
  assignee_bot: string;
  verifier_bot: string | null;
  status: string;
  evidence_count: number | null;
  created_at: string;
}

export interface WarRoomLivingSpec {
  title: string;
  updated_at: string;
  sections: Array<{ section_key: string; title: string; content: string; last_author_bot: string; version: number | null; updated_at: string }>;
}

export interface WarRoomCostSummary {
  daily_budget_usd: number | null;
  current_spend_24h: number | null;
  budget_utilized_ratio: number | null;
  /** Per-bot token/cost split, keyed by bot name. */
  bot_breakdown: Record<string, { input_tokens: number | null; output_tokens: number | null; cost_usd: number | null }>;
}

export interface WarRoomStandup {
  timestamp: string;
  executive_summary: string;
  active_bots: string[];
  blockers: string[];
  stagnant_alerts: Array<{ task_id: string; assignee_bot: string; minutes_inactive: number | null; recommendation: string }>;
}

export interface WarRoomLeaderboardEntry {
  bot_name: string;
  challenges_attempted: number | null;
  challenges_passed: number | null;
  /** Null while the bot has no measured attempts — never an invented default. */
  pass_rate: number | null;
  reputation_score: number | null;
  rank: number | null;
}

export interface WarRoomCanaryResult {
  probe_id: string;
  target_url: string;
  status: string;
  http_status: number | null;
  latency_ms: number | null;
  recommendation: string;
  tested_at: string;
}

export interface WarRoomVisualReceipt {
  receipt_id: string;
  url: string;
  passed: boolean | null;
  visual_stability_score: number | null;
  verified_by: string;
  verified_at: string;
}

export interface AVOVersion {
  version_id: string;
  parent_id: string | null;
  hypothesis: string;
  modification: string;
  correctness: boolean | null;
  performance_score: number | null;
  quality_score: number | null;
  composite_score: number | null;
  rejection_reason: string | null;
  created_at: number | null;
}

export interface AVOLineage {
  head_id: string | null;
  versions: AVOVersion[];
  pareto_frontier: AVOVersion[];
  supervisor_status: string;
}

export interface EpistemicClaim {
  claim_id: string;
  text: string;
  status: string;
  confidence: number | null;
  bayesian_prior: number | null;
  bayesian_posterior: number | null;
  falsification_test: string;
  verification_method: string;
  supporting_evidence: string[];
  contradicting_evidence: string[];
  is_verified: boolean | null;
}

export interface RSIStatus {
  stage: string;
  active_configurations: Record<string, unknown>;
  last_cycle_summary: string | null;
}

export interface TrajectoryTrace {
  goal_id: string;
  total_steps: number | null;
  created_at: string;
  steps: Array<Record<string, unknown>>;
}

/**
 * `kill_switch.active` is tri-state on purpose. `null` means "not reported",
 * which is a different claim from "off" — the panel must not render a global
 * kill switch it never measured as a healthy "no".
 */
export interface KillSwitch {
  active: boolean | null;
  reason: string;
  paused_bots: Record<string, unknown>;
}

export interface WarRoomAggregate {
  project_id: string;
  /** The server's own literal; it does not derive it from the project row. */
  status: string;
  state: ProjectStateDigest;
  contracts: WarRoomContract[];
  living_spec: WarRoomLivingSpec | null;
  cost_summary: WarRoomCostSummary | null;
  standup: WarRoomStandup | null;
  leaderboard: WarRoomLeaderboardEntry[];
  canary_history: WarRoomCanaryResult[];
  visual_qa: WarRoomVisualReceipt[];
  avo_lineage: AVOLineage | null;
  epistemic_claims: EpistemicClaim[];
  rsi_status: RSIStatus | null;
  trajectories: TrajectoryTrace[];
  kill_switch: KillSwitch;
}

function toAVOVersion(row: Record<string, unknown>, index: number): AVOVersion {
  return {
    version_id: str(row, "version_id") ?? `version-${index}`,
    parent_id: str(row, "parent_id"),
    hypothesis: str(row, "hypothesis") ?? "",
    modification: str(row, "modification") ?? "",
    correctness: bool(row, "correctness"),
    performance_score: num(row, "performance_score"),
    quality_score: num(row, "quality_score"),
    composite_score: num(row, "composite_score"),
    rejection_reason: str(row, "rejection_reason"),
    created_at: num(row, "created_at"),
  };
}

async function fetchWarRoom(projectId: string): Promise<WarRoomAggregate> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/war-room`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable War Room aggregate.");
  // `state` is the same folded snapshot `/state` serves. It is mapped through
  // the same defaults so the two sections cannot disagree by construction.
  const state = asRecord(record.state) ?? {};
  const stateDigest: ProjectStateDigest = {
    project_id: str(state, "project_id"),
    goal: str(state, "goal") ?? "",
    phase: str(state, "phase") ?? "",
    arch_version: str(state, "arch_version") ?? "",
    active_agents: num(state, "active_agents"),
    active_tasks: num(state, "active_tasks"),
    blocked_tasks: num(state, "blocked_tasks"),
    completed_tasks: num(state, "completed_tasks"),
    failed_tasks: num(state, "failed_tasks"),
    open_conflicts: num(state, "open_conflicts"),
    open_risks: (Array.isArray(state.open_risks) ? state.open_risks : []).map((risk) =>
      typeof risk === "string" ? risk : String(asRecord(risk)?.description ?? ""),
    ),
    last_verified: str(state, "last_verified"),
    latest_decision: str(state, "latest_decision"),
    updated_at: str(state, "updated_at") ?? "",
  };

  const livingSpecRecord = asRecord(record.living_spec);
  const costRecord = asRecord(record.cost_summary);
  const standupRecord = asRecord(record.standup);
  const avoRecord = asRecord(record.avo_lineage);
  const rsiRecord = asRecord(record.rsi_status);
  const killRecord = asRecord(record.kill_switch);

  return {
    project_id: str(record, "project_id") ?? projectId,
    status: str(record, "status") ?? "",
    state: stateDigest,
    contracts: rows(record.contracts).map((row, index) => ({
      task_id: str(row, "task_id") ?? `task-${index}`,
      title: str(row, "title") ?? "",
      assignee_bot: str(row, "assignee_bot") ?? "",
      verifier_bot: str(row, "verifier_bot"),
      status: str(row, "status") ?? "",
      evidence_count: Array.isArray(row.evidence_receipts) ? row.evidence_receipts.length : null,
      created_at: str(row, "created_at") ?? "",
    })),
    living_spec: livingSpecRecord
      ? {
          title: str(livingSpecRecord, "title") ?? "",
          updated_at: str(livingSpecRecord, "updated_at") ?? "",
          sections: Object.entries(asRecord(livingSpecRecord.sections) ?? {}).map(([key, value]) => {
            const section = asRecord(value) ?? {};
            return {
              section_key: str(section, "section_key") ?? key,
              title: str(section, "title") ?? "",
              content: str(section, "content") ?? "",
              last_author_bot: str(section, "last_author_bot") ?? "",
              version: num(section, "version"),
              updated_at: str(section, "updated_at") ?? "",
            };
          }),
        }
      : null,
    cost_summary: costRecord
      ? {
          daily_budget_usd: num(costRecord, "daily_budget_usd"),
          current_spend_24h: num(costRecord, "current_spend_24h"),
          budget_utilized_ratio: num(costRecord, "budget_utilized_ratio"),
          bot_breakdown: Object.fromEntries(
            Object.entries(asRecord(costRecord.bot_breakdown) ?? {}).map(([bot, value]) => {
              const entry = asRecord(value) ?? {};
              return [
                bot,
                {
                  input_tokens: num(entry, "input_tokens"),
                  output_tokens: num(entry, "output_tokens"),
                  cost_usd: num(entry, "cost_usd"),
                },
              ];
            }),
          ),
        }
      : null,
    standup: standupRecord
      ? {
          timestamp: str(standupRecord, "timestamp") ?? "",
          executive_summary: str(standupRecord, "executive_summary") ?? "",
          active_bots: texts(standupRecord.active_bots),
          blockers: texts(standupRecord.blockers),
          stagnant_alerts: rows(standupRecord.stagnant_alerts).map((alert) => ({
            task_id: str(alert, "task_id") ?? "",
            assignee_bot: str(alert, "assignee_bot") ?? "",
            minutes_inactive: num(alert, "minutes_inactive"),
            recommendation: str(alert, "recommendation") ?? "",
          })),
        }
      : null,
    leaderboard: rows(record.leaderboard).map((row, index) => ({
      bot_name: str(row, "bot_name") ?? `bot-${index}`,
      challenges_attempted: num(row, "challenges_attempted"),
      challenges_passed: num(row, "challenges_passed"),
      pass_rate: num(row, "pass_rate"),
      reputation_score: num(row, "reputation_score"),
      rank: num(row, "rank"),
    })),
    canary_history: rows(record.canary_history).map((row, index) => ({
      probe_id: str(row, "probe_id") ?? `probe-${index}`,
      target_url: str(row, "target_url") ?? "",
      status: str(row, "status") ?? "",
      http_status: num(row, "http_status"),
      latency_ms: num(row, "latency_ms"),
      recommendation: str(row, "recommendation") ?? "",
      tested_at: str(row, "tested_at") ?? "",
    })),
    visual_qa: rows(record.visual_qa).map((row, index) => ({
      receipt_id: str(row, "receipt_id") ?? `receipt-${index}`,
      url: str(row, "url") ?? "",
      passed: bool(row, "passed"),
      visual_stability_score: num(row, "visual_stability_score"),
      verified_by: str(row, "verified_by") ?? "",
      verified_at: str(row, "verified_at") ?? "",
    })),
    avo_lineage: avoRecord
      ? {
          head_id: str(avoRecord, "head_id"),
          versions: rows(avoRecord.versions).map(toAVOVersion),
          pareto_frontier: rows(avoRecord.pareto_frontier).map(toAVOVersion),
          supervisor_status: str(avoRecord, "supervisor_status") ?? "",
        }
      : null,
    epistemic_claims: rows(record.epistemic_claims).map((row, index) => ({
      claim_id: str(row, "claim_id") ?? `claim-${index}`,
      text: str(row, "text") ?? "",
      status: str(row, "status") ?? "",
      confidence: num(row, "confidence"),
      bayesian_prior: num(row, "bayesian_prior"),
      bayesian_posterior: num(row, "bayesian_posterior"),
      falsification_test: str(row, "falsification_test") ?? "",
      verification_method: str(row, "verification_method") ?? "",
      supporting_evidence: texts(row.supporting_evidence),
      contradicting_evidence: texts(row.contradicting_evidence),
      is_verified: bool(row, "is_verified"),
    })),
    rsi_status: rsiRecord
      ? {
          stage: str(rsiRecord, "stage") ?? "",
          active_configurations: asRecord(rsiRecord.active_configurations) ?? {},
          last_cycle_summary: str(rsiRecord, "last_cycle_summary"),
        }
      : null,
    trajectories: rows(record.trajectories).map((row, index) => ({
      goal_id: str(row, "goal_id") ?? `goal-${index}`,
      total_steps: num(row, "total_steps"),
      created_at: str(row, "created_at") ?? "",
      steps: rows(row.steps),
    })),
    kill_switch: {
      // A missing key is unknown, NOT "no kill switch is active".
      active: bool(killRecord ?? {}, "active"),
      reason: str(killRecord ?? {}, "reason") ?? "",
      paused_bots: asRecord(killRecord?.paused_bots) ?? {},
    },
  };
}

// ---------------------------------------------------------------------------
// Autonomous subsystems — self-configuration, meta-compiler, perpetual daemon
// ---------------------------------------------------------------------------

export interface SelfConfigStatus {
  profile_id: string;
  goal: string;
  operating_mode: string;
  model_tier: string;
  primary_model: string;
  fallback_model: string;
  topology: string;
  active_tools: string[];
  reasoning_budget_tokens: number | null;
  max_turns: number | null;
  context_compaction_threshold: number | null;
  thought_depth: string;
  analyses_performed: number | null;
  last_analysis: Record<string, unknown> | null;
  updated_at: string;
}

async function fetchSelfConfig(projectId: string): Promise<SelfConfigStatus> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/self-config/status`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable self-configuration status.");
  const profile = asRecord(record.active_profile) ?? {};
  return {
    profile_id: str(profile, "profile_id") ?? "",
    goal: str(profile, "goal") ?? "",
    operating_mode: str(profile, "operating_mode") ?? "",
    model_tier: str(profile, "model_tier") ?? "",
    primary_model: str(profile, "primary_model") ?? "",
    fallback_model: str(profile, "fallback_model") ?? "",
    topology: str(profile, "topology") ?? "",
    active_tools: texts(profile.active_tools),
    reasoning_budget_tokens: num(profile, "reasoning_budget_tokens"),
    max_turns: num(profile, "max_turns"),
    context_compaction_threshold: num(profile, "context_compaction_threshold"),
    thought_depth: str(profile, "thought_depth") ?? "",
    analyses_performed: num(record, "analyses_performed"),
    last_analysis: asRecord(record.last_analysis),
    updated_at: str(profile, "updated_at") ?? "",
  };
}

export interface MetaLineage {
  blueprint_id: string;
  generation: number | null;
  name: string;
  architecture_tag: string;
  reasoning_strategy: string;
  memory_layout: string;
  tool_bindings: string[];
  specialization: string;
  mutation_notes: string;
  composite_score: number | null;
  passed_regression_suite: boolean | null;
  total_generations: number | null;
  blueprints_count: number | null;
  pareto_size: number | null;
  history: Array<Record<string, unknown>>;
}

async function fetchMetaLineage(projectId: string): Promise<MetaLineage> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/meta-compiler/lineage`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable meta-compiler lineage.");
  const head = asRecord(record.active_head) ?? {};
  const scorecard = asRecord(record.active_scorecard) ?? {};
  return {
    blueprint_id: str(head, "blueprint_id") ?? "",
    generation: num(head, "generation"),
    name: str(head, "name") ?? "",
    architecture_tag: str(head, "architecture_tag") ?? "",
    reasoning_strategy: str(head, "reasoning_strategy") ?? "",
    memory_layout: str(head, "memory_layout") ?? "",
    tool_bindings: texts(head.tool_bindings),
    specialization: str(head, "specialization") ?? "",
    mutation_notes: str(head, "mutation_notes") ?? "",
    composite_score: num(scorecard, "composite_score"),
    passed_regression_suite: bool(scorecard, "passed_regression_suite"),
    total_generations: num(record, "total_generations"),
    blueprints_count: num(record, "blueprints_count"),
    pareto_size: Array.isArray(record.pareto_frontier) ? record.pareto_frontier.length : null,
    history: rows(record.history),
  };
}

export interface PerpetualStatus {
  state: string;
  heartbeat_count: number | null;
  uptime_seconds: number | null;
  active_goals_count: number | null;
  total_tasks_discovered: number | null;
  tasks_completed_count: number | null;
  stagnation_incidents_recovered: number | null;
  consolidation_cycles_completed: number | null;
  last_heartbeat_at: string;
  active_goal: Record<string, unknown> | null;
  goals: number | null;
  tasks: number | null;
  stagnation_incidents: number | null;
  latest_consolidation: Record<string, unknown> | null;
}

async function fetchPerpetual(projectId: string): Promise<PerpetualStatus> {
  const body = await get<unknown>(`/projects/${enc(projectId)}/perpetual/status`);
  const record = asRecord(body);
  if (!record) throw new Error("The server returned an unreadable perpetual-daemon status.");
  const telemetry = asRecord(record.telemetry) ?? {};
  return {
    state: str(telemetry, "state") ?? "",
    heartbeat_count: num(telemetry, "heartbeat_count"),
    uptime_seconds: num(telemetry, "uptime_seconds"),
    active_goals_count: num(telemetry, "active_goals_count"),
    total_tasks_discovered: num(telemetry, "total_tasks_discovered"),
    tasks_completed_count: num(telemetry, "tasks_completed_count"),
    stagnation_incidents_recovered: num(telemetry, "stagnation_incidents_recovered"),
    consolidation_cycles_completed: num(telemetry, "consolidation_cycles_completed"),
    last_heartbeat_at: str(telemetry, "last_heartbeat_at") ?? "",
    active_goal: asRecord(record.active_goal),
    goals: Array.isArray(record.all_goals) ? record.all_goals.length : null,
    tasks: Array.isArray(record.tasks) ? record.tasks.length : null,
    stagnation_incidents: Array.isArray(record.stagnation_incidents) ? record.stagnation_incidents.length : null,
    latest_consolidation: asRecord(record.latest_consolidation),
  };
}

// ---------------------------------------------------------------------------
// The assembled read
// ---------------------------------------------------------------------------

export interface ProjectInspection {
  record: InspectorSection<ProjectRecord>;
  state: InspectorSection<ProjectStateDigest>;
  crew: InspectorSection<CrewView>;
  conversations: InspectorSection<ProjectConversation[]>;
  memory: InspectorSection<ProjectMemory>;
  constitution: InspectorSection<ProjectConstitution>;
  context: InspectorSection<ProjectContext>;
  decisions: InspectorSection<ProjectDecision[]>;
  events: InspectorSection<ProjectEvent[]>;
  locks: InspectorSection<LocksView>;
  handoffs: InspectorSection<HandoffRecord[]>;
  approvals: InspectorSection<ApprovalRequest[]>;
  checkpoints: InspectorSection<WorkspaceCheckpoint[]>;
  warRoom: InspectorSection<WarRoomAggregate>;
  selfConfig: InspectorSection<SelfConfigStatus>;
  metaCompiler: InspectorSection<MetaLineage>;
  perpetual: InspectorSection<PerpetualStatus>;
  /** True when at least one section failed, so the panel can say so up front. */
  partial: boolean;
}

/** Section name -> its own section, used for the partial-read disclosure. */
export function inspectionSections(inspection: ProjectInspection): Array<[string, InspectorSection<unknown>]> {
  return [
    ["identity", inspection.record],
    ["state", inspection.state],
    ["crew", inspection.crew],
    ["conversations", inspection.conversations],
    ["shared memory", inspection.memory],
    ["constitution", inspection.constitution],
    ["agent context", inspection.context],
    ["decisions", inspection.decisions],
    ["activity", inspection.events],
    ["locks", inspection.locks],
    ["handoffs", inspection.handoffs],
    ["approvals", inspection.approvals],
    ["checkpoints", inspection.checkpoints],
    ["war room", inspection.warRoom],
    ["self-configuration", inspection.selfConfig],
    ["meta-compiler", inspection.metaCompiler],
    ["perpetual daemon", inspection.perpetual],
  ];
}

/**
 * Read every subsystem at once, in parallel, each independently.
 *
 * This never rejects: a failed section carries the server's reason, and the rest
 * still render. `botRole` selects which sections the role-filtered context
 * projection includes — it is a *server-side* filter, so a different role
 * legitimately returns fewer sections, and that is shown as what the role can
 * see rather than as missing data.
 */
export async function inspectProject(projectId: string, botRole = "worker"): Promise<ProjectInspection> {
  const [
    record,
    state,
    crew,
    conversations,
    memory,
    constitution,
    context,
    decisions,
    events,
    locks,
    handoffs,
    approvals,
    checkpoints,
    warRoom,
    selfConfig,
    metaCompiler,
    perpetual,
  ] = await Promise.all([
    section(() => fetchProjectRecord(projectId)),
    section(() => fetchState(projectId)),
    section(() => fetchCrew(projectId)),
    section(() => fetchConversations(projectId)),
    section(() => fetchMemory(projectId)),
    section(() => fetchConstitution(projectId)),
    section(() => fetchContext(projectId, botRole)),
    section(() => fetchDecisions(projectId)),
    section(() => fetchEvents(projectId)),
    section(() => fetchLocks(projectId)),
    section(() => fetchHandoffs(projectId)),
    section(() => fetchApprovals(projectId)),
    section(() => fetchCheckpoints(projectId)),
    section(() => fetchWarRoom(projectId)),
    section(() => fetchSelfConfig(projectId)),
    section(() => fetchMetaLineage(projectId)),
    section(() => fetchPerpetual(projectId)),
  ]);
  return {
    record,
    state,
    crew,
    conversations,
    memory,
    constitution,
    context,
    decisions,
    events,
    locks,
    handoffs,
    approvals,
    checkpoints,
    warRoom,
    selfConfig,
    metaCompiler,
    perpetual,
    partial: [
      record,
      state,
      crew,
      conversations,
      memory,
      constitution,
      context,
      decisions,
      events,
      locks,
      handoffs,
      approvals,
      checkpoints,
      warRoom,
      selfConfig,
      metaCompiler,
      perpetual,
    ].some((entry) => entry.status === "error"),
  };
}