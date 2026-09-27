import { get, send, pick } from "./http";

export interface Project {
  id: string;
  name: string;
  instructions: string;
  presentation: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectAgentInput {
  name: string;
  role?: string | null;
}

export interface ProjectMember {
  project_id: string;
  bot_name: string;
  role_in_project: string;
  status: string;
  current_task_id: string | null;
  blocked_reason: string | null;
  joined_at: string;
  last_activity: string;
}

function toProject(p: Record<string, unknown>): Project {
  const id = p.id ?? p.project_id;
  const name = p.name;
  if (typeof id !== "string" || !id || typeof name !== "string") {
    throw new Error("The server returned an invalid project record.");
  }
  return {
    id,
    name,
    instructions: typeof p.instructions === "string" ? p.instructions : "",
    presentation:
      p.presentation && typeof p.presentation === "object"
        ? (p.presentation as Record<string, unknown>)
        : {},
    status: String(pick(p, ["status"], "active")),
    created_at: String(pick(p, ["created_at"], "")),
    updated_at: String(pick(p, ["updated_at"], "")),
  };
}

export async function listProjects(): Promise<Project[]> {
  const data = await get<unknown>("/projects");
  if (Array.isArray(data)) return data.map((project) => toProject(project as Record<string, unknown>));
  if (data && typeof data === "object" && Array.isArray((data as Record<string, unknown>).projects)) {
    return ((data as Record<string, unknown>).projects as Array<Record<string, unknown>>).map(toProject);
  }
  throw new Error("The server returned an unreadable project list.");
}

export async function createProject(
  name: string,
  instructions = "",
  agents: ProjectAgentInput[] = [],
): Promise<Project> {
  const d = await send<Record<string, unknown>>("/projects", "POST", { name, instructions, agents });
  return toProject(d);
}

export async function updateProject(id: string, patch: { name?: string; instructions?: string }): Promise<Project> {
  const d = await send<Record<string, unknown>>(`/projects/${encodeURIComponent(id)}`, "PATCH", patch);
  return toProject(d);
}

export async function archiveProject(id: string): Promise<void> {
  await send(`/projects/${encodeURIComponent(id)}/archive`, "POST", {});
}

export async function restoreProject(id: string): Promise<void> {
  await send(`/projects/${encodeURIComponent(id)}/restore`, "POST", {});
}

export async function deleteProject(id: string): Promise<void> {
  await send(`/projects/${encodeURIComponent(id)}`, "DELETE");
}

export interface ProjectThread {
  thread_id: string;
  display_name: string;
}

/** Server `limit` ceiling for GET /projects/{id}/threads. */
const PROJECT_THREAD_PAGE_SIZE = 500;

/**
 * Every conversation the project holds, following all offset pages.
 *
 * The route caps `limit` at 1000, so a project with more chats than one page
 * would silently lose the tail. A page that repeats the previous boundary means
 * the offset is not being honored; that is reported rather than looped on.
 */
export async function projectThreads(id: string): Promise<ProjectThread[]> {
  const all: ProjectThread[] = [];
  const seen = new Set<string>();
  let offset = 0;
  let previousBoundary = "";
  while (true) {
    const query = new URLSearchParams({
      limit: String(PROJECT_THREAD_PAGE_SIZE),
      offset: String(offset),
    });
    const data = await get<unknown>(`/projects/${encodeURIComponent(id)}/threads?${query.toString()}`);
    if (!Array.isArray(data)) throw new Error("The server returned an unreadable project conversation list.");
    const mapped = data.map((thread) => {
      const record = thread as Record<string, unknown>;
      const threadId = String(pick(record, ["thread_id", "id"], ""));
      if (!threadId) throw new Error("The server returned a project conversation without an id.");
      return {
        thread_id: threadId,
        // An absent display name is a real state, not a fabricated id.
        display_name: String(pick(record, ["display_name", "title"], "Untitled")),
      };
    });
    const boundary = mapped.length > 0 ? `${mapped[0].thread_id}:${mapped[mapped.length - 1].thread_id}` : "";
    if (mapped.length === PROJECT_THREAD_PAGE_SIZE && boundary && boundary === previousBoundary) {
      throw new Error("Project conversation pagination did not advance; stopped to avoid an endless loop.");
    }
    previousBoundary = boundary;
    for (const thread of mapped) {
      if (seen.has(thread.thread_id)) continue;
      seen.add(thread.thread_id);
      all.push(thread);
    }
    if (mapped.length < PROJECT_THREAD_PAGE_SIZE) break;
    offset += mapped.length;
  }
  return all;
}

/**
 * Validate one presence row. A row without a bot name is unusable for
 * membership management, so it is rejected instead of rendered as an
 * unnamed member that the user could never remove.
 */
function toProjectMember(row: unknown, projectId: string): ProjectMember {
  if (!row || typeof row !== "object") {
    throw new Error("The server returned an invalid project team row.");
  }
  const record = row as Record<string, unknown>;
  const botName = record.bot_name;
  if (typeof botName !== "string" || !botName.trim()) {
    throw new Error("The server returned a project team row without a bot name.");
  }
  return {
    project_id: typeof record.project_id === "string" && record.project_id ? record.project_id : projectId,
    bot_name: botName,
    role_in_project: String(pick(record, ["role_in_project", "role"], "worker")),
    // Render the server's own enum string; an unknown status stays unknown.
    status: String(pick(record, ["status"], "unknown")),
    current_task_id: typeof record.current_task_id === "string" && record.current_task_id ? record.current_task_id : null,
    blocked_reason: typeof record.blocked_reason === "string" && record.blocked_reason ? record.blocked_reason : null,
    joined_at: String(pick(record, ["joined_at"], "")),
    last_activity: String(pick(record, ["last_activity"], "")),
  };
}

export async function listProjectAgents(projectId: string): Promise<ProjectMember[]> {
  const data = await get<{ members?: unknown }>(`/projects/${encodeURIComponent(projectId)}/presence`);
  if (!Array.isArray(data.members)) throw new Error("The server returned an unreadable project team.");
  return data.members.map((row) => toProjectMember(row, projectId));
}

/** Re-read the confirmed roster; used to verify a membership mutation. */
export async function confirmProjectAgents(
  projectId: string,
  expected: string[],
  present: boolean,
): Promise<ProjectMember[]> {
  const members = await listProjectAgents(projectId);
  const roster = new Set(members.map((member) => member.bot_name));
  const missing = expected.filter((name) => (roster.has(name) !== present));
  if (missing.length > 0) {
    throw new Error(
      present
        ? `The server did not confirm ${missing.join(", ")} in this project's team.`
        : `The server still lists ${missing.join(", ")} in this project's team.`,
    );
  }
  return members;
}

export async function attachProjectAgents(
  projectId: string,
  agents: ProjectAgentInput[],
  role = "worker",
): Promise<unknown> {
  return send(`/projects/${encodeURIComponent(projectId)}/agents`, "POST", { agents, role });
}

export async function detachProjectAgent(projectId: string, botName: string): Promise<unknown> {
  return send(`/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(botName)}`, "DELETE");
}

// ------------------------------------------------------------------ crew layer

/**
 * One agent's participation, as `ProjectCrewService` flattens it.
 *
 * `status` is the server's own enum string and is kept verbatim: a status this
 * build has never heard of must render as itself, not collapse to "active".
 */
export interface CrewMember {
  bot_name: string;
  role_in_project: string;
  status: string;
  /** Task the agent is on, or null when it has none / the server did not say. */
  current_task_id: string | null;
  /** Why the agent is stuck, or null when it is not blocked. */
  blocked_reason: string | null;
  last_activity: string;
}

/**
 * The project's group room. `null` is a real state, not a failure: a crew of
 * fewer than two agents has no room yet (`crew.py` provisions it at the 2nd
 * member and *parks* rather than deletes it on the way back down to one).
 */
export interface CrewRoom {
  name: string;
  mode: string;
  moderator: string | null;
  members: string[];
  message_count: number;
  /** True when the room kept its history but is not currently active. */
  parked: boolean;
}

/** Per-project coordination policy (`project-config/collaboration.json`). */
export interface CollaborationSettings {
  orchestration_mode: string;
  moderator: string | null;
  max_concurrent_speakers: number;
  mention_policy: string;
  auto_handoff: boolean;
  conflict_policy: string;
  lock_policy: string;
  require_evidence: boolean;
  memory_budget_chars: number;
  transcript_digest_n: number;
  standup_interval_turns: number;
}

/** `CrewView` — members, room, settings, memory, locks, and events in one read. */
export interface CrewView {
  project_id: string;
  members: CrewMember[];
  room: CrewRoom | null;
  collaboration: CollaborationSettings;
  shared_memory: Record<string, unknown>;
  state: Record<string, unknown>;
  active_locks: Array<Record<string, unknown>>;
  recent_events: Array<Record<string, unknown>>;
  updated_at: string;
}

/**
 * Enum sets mirrored from `alpha.projects.crew` for the settings form. They
 * drive the *choices* offered, never the value displayed: if the Gateway
 * reports a mode this build does not know, the form adds it as an option so a
 * newer server's setting is never silently rewritten to a known value.
 */
export const ORCHESTRATION_MODES = ["mention", "moderated", "quorum", "parallel", "round_robin"] as const;
export const MENTION_POLICIES = ["strict", "advisory"] as const;
export const CONFLICT_POLICIES = ["block", "vote", "moderator"] as const;
export const LOCK_POLICIES = ["advisory", "strict"] as const;

/**
 * Every key the server's `CollaborationConfig` accepts. A patch containing
 * anything else is rejected there with a 422, so callers filter against this
 * rather than discovering it from an error.
 */
export const COLLABORATION_KEYS = [
  "orchestration_mode",
  "moderator",
  "max_concurrent_speakers",
  "mention_policy",
  "auto_handoff",
  "conflict_policy",
  "lock_policy",
  "require_evidence",
  "memory_budget_chars",
  "transcript_digest_n",
  "standup_interval_turns",
] as const;

/** A partial settings patch; only the keys the user actually changed are sent. */
export type CollaborationPatch = Partial<CollaborationSettings>;

/**
 * Validate one crew member. A row without a bot name cannot be rendered, let
 * alone detached, so it is rejected instead of shown as an unnamed agent.
 */
function toCrewMember(row: unknown): CrewMember {
  if (!row || typeof row !== "object") {
    throw new Error("The server returned an invalid project crew member.");
  }
  const record = row as Record<string, unknown>;
  const botName = record.bot_name;
  if (typeof botName !== "string" || !botName.trim()) {
    throw new Error("The server returned a project crew member without a bot name.");
  }
  return {
    bot_name: botName,
    role_in_project: String(pick(record, ["role_in_project", "role"], "worker")),
    status: String(pick(record, ["status"], "unknown")),
    current_task_id: typeof record.current_task_id === "string" && record.current_task_id ? record.current_task_id : null,
    blocked_reason: typeof record.blocked_reason === "string" && record.blocked_reason ? record.blocked_reason : null,
    last_activity: String(pick(record, ["last_activity"], "")),
  };
}

/**
 * The room is optional by design. A non-null, non-object `room` is a shape this
 * build cannot read, so it is reported rather than rendered as "no room" —
 * those are opposite claims (one says solo, the other says unreadable).
 */
function toCrewRoom(value: unknown): CrewRoom | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "object") {
    throw new Error("The server returned an unreadable project room.");
  }
  const record = value as Record<string, unknown>;
  return {
    name: String(pick(record, ["name"], "")),
    mode: String(pick(record, ["mode"], "unknown")),
    moderator: typeof record.moderator === "string" && record.moderator ? record.moderator : null,
    members: Array.isArray(record.members) ? record.members.map((m) => String(m)) : [],
    message_count: typeof record.message_count === "number" ? record.message_count : 0,
    parked: record.parked === true,
  };
}

/**
 * Map the collaboration block.
 *
 * The fallbacks below mirror each field's default in the server's
 * `CollaborationConfig` dataclass and are only reached if a Gateway omits the
 * key entirely. `orchestration_mode` is not defaulted: the settings form keys
 * its options off it, so an absent mode is an unreadable envelope, not a
 * silently substituted "moderated".
 */
function toCollaboration(value: unknown): CollaborationSettings {
  if (!value || typeof value !== "object") {
    throw new Error("The server returned an unreadable collaboration settings block.");
  }
  const record = value as Record<string, unknown>;
  const mode = record.orchestration_mode;
  if (typeof mode !== "string" || !mode) {
    throw new Error("The server returned collaboration settings without an orchestration mode.");
  }
  const int = (key: string, fallback: number): number =>
    typeof record[key] === "number" ? (record[key] as number) : fallback;
  return {
    orchestration_mode: mode,
    moderator: typeof record.moderator === "string" && record.moderator ? record.moderator : null,
    max_concurrent_speakers: int("max_concurrent_speakers", 3),
    mention_policy: String(pick(record, ["mention_policy"], "strict")),
    auto_handoff: record.auto_handoff !== false,
    conflict_policy: String(pick(record, ["conflict_policy"], "moderator")),
    lock_policy: String(pick(record, ["lock_policy"], "advisory")),
    require_evidence: record.require_evidence !== false,
    memory_budget_chars: int("memory_budget_chars", 6000),
    transcript_digest_n: int("transcript_digest_n", 40),
    standup_interval_turns: int("standup_interval_turns", 10),
  };
}

function toCrewView(value: unknown): CrewView {
  if (!value || typeof value !== "object") {
    throw new Error("The server returned an unreadable project crew.");
  }
  const record = value as Record<string, unknown>;
  const projectId = record.project_id;
  if (typeof projectId !== "string" || !projectId) {
    throw new Error("The server returned a project crew without a project id.");
  }
  if (!Array.isArray(record.members)) {
    throw new Error("The server returned a project crew without a member list.");
  }
  return {
    project_id: projectId,
    members: record.members.map(toCrewMember),
    room: toCrewRoom(record.room),
    collaboration: toCollaboration(record.collaboration),
    shared_memory:
      record.shared_memory && typeof record.shared_memory === "object"
        ? (record.shared_memory as Record<string, unknown>)
        : {},
    state: record.state && typeof record.state === "object" ? (record.state as Record<string, unknown>) : {},
    active_locks: Array.isArray(record.active_locks)
      ? (record.active_locks as Array<Record<string, unknown>>)
      : [],
    recent_events: Array.isArray(record.recent_events)
      ? (record.recent_events as Array<Record<string, unknown>>)
      : [],
    updated_at: String(pick(record, ["updated_at"], "")),
  };
}

/**
 * Read the whole crew in one request.
 *
 * The route is `ensure_crew`, not a passive read: it reconciles membership
 * against the group room as a side effect, so polling it is the supported way
 * to keep the UI honest. It emits nothing when the crew is already settled.
 */
export async function getCrew(projectId: string): Promise<CrewView> {
  const data = await get<unknown>(`/projects/${encodeURIComponent(projectId)}/crew`);
  return toCrewView(data);
}

/**
 * Apply a collaboration-settings patch. Returns the reconciled crew so the UI
 * renders what the server actually stored rather than the values it hoped for.
 *
 * An empty patch is never sent: the route rejects it with a 422, and silently
 * shipping one would look like a successful save.
 */
export async function updateCollaboration(
  projectId: string,
  patch: CollaborationPatch,
): Promise<CrewView> {
  const clean = Object.fromEntries(
    Object.entries(patch).filter(([, value]) => value !== undefined),
  );
  if (Object.keys(clean).length === 0) {
    throw new Error("No collaboration settings were changed, so nothing was sent.");
  }
  const data = await send<unknown>(
    `/projects/${encodeURIComponent(projectId)}/collaboration`,
    "PATCH",
    clean,
  );
  return toCrewView(data);
}

/** One message inside the crew's shared transcript digest. */
export interface TranscriptMessage {
  from: string;
  intent: string;
  text: string;
  at: string;
}

/**
 * The crew's shared conversation, as the memory bridge folds it.
 *
 * This is a structured object, not a blob of prose: `messages` is the window
 * from `compacted_upto` onward, `summary` is what the earlier overflow was
 * compacted into, and `total` is the room's full message count. A crew that
 * has not talked reports `messages: []` — which is a real "nothing yet", not a
 * missing field.
 */
export interface TranscriptDigest {
  messages: TranscriptMessage[];
  total: number;
  compacted_upto: number;
  summary: string | null;
}

export interface ProjectMemory {
  project_id: string;
  goal: string | null;
  phase: string | null;
  recent_decisions: Array<Record<string, unknown>>;
  active_locks: string[];
  constitution_hash: string;
  /** Last group messages, role-tagged — the crew's shared conversation. */
  transcript_digest: TranscriptDigest | null;
  /** Unresolved proposals/votes pulled out of the transcript. */
  open_questions: Array<Record<string, unknown>>;
  /** One line per agent: its most recent contribution. */
  member_briefs: Record<string, string>;
  /** Unanswered @mentions per agent — the per-agent work queue. */
  pending_mentions: Record<string, Array<Record<string, unknown>>>;
  updated_at: string;
}

/**
 * Level-2 shared memory as an agent sees it, for the crew inspector.
 *
 * Every field is optional in practice: `get_project_memory` merges the event
 * fold, the decision log, locks, the constitution, and the transcript bridge,
 * and a project that has never run may legitimately have none of them. Absent
 * values map to null / empty rather than being invented.
 */
export async function getProjectMemory(projectId: string): Promise<ProjectMemory> {
  const data = await get<unknown>(`/projects/${encodeURIComponent(projectId)}/memory`);
  if (!data || typeof data !== "object") {
    throw new Error("The server returned an unreadable project memory block.");
  }
  const record = data as Record<string, unknown>;
  // The route always echoes the project it read, so its absence means this is
  // not the memory envelope — an unreadable answer, not "nothing shared yet".
  if (typeof record.project_id !== "string" || !record.project_id) {
    throw new Error("The server returned a project memory block without a project id.");
  }
  const text = (key: string): string | null =>
    typeof record[key] === "string" && record[key] ? (record[key] as string) : null;

  // The digest is an object, not a string. Anything else is a shape this build
  // cannot read, so it stays null rather than being stringified into a lie.
  const digest = (): TranscriptDigest | null => {
    const value = record.transcript_digest;
    if (!value || typeof value !== "object") return null;
    const block = value as Record<string, unknown>;
    const messages: TranscriptMessage[] = [];
    if (Array.isArray(block.messages)) {
      for (const entry of block.messages) {
        if (!entry || typeof entry !== "object") continue;
        const row = entry as Record<string, unknown>;
        messages.push({
          from: String(pick(row, ["from"], "unknown")),
          intent: String(pick(row, ["intent"], "unknown")),
          text: String(pick(row, ["text"], "")),
          at: String(pick(row, ["at"], "")),
        });
      }
    }
    return {
      messages,
      total: typeof block.total === "number" ? block.total : messages.length,
      compacted_upto: typeof block.compacted_upto === "number" ? block.compacted_upto : 0,
      summary: typeof block.summary === "string" && block.summary ? block.summary : null,
    };
  };
  const briefs: Record<string, string> = {};
  if (record.member_briefs && typeof record.member_briefs === "object") {
    for (const [bot, brief] of Object.entries(record.member_briefs as Record<string, unknown>)) {
      if (typeof brief === "string") briefs[bot] = brief;
    }
  }
  const pending: Record<string, Array<Record<string, unknown>>> = {};
  if (record.pending_mentions && typeof record.pending_mentions === "object") {
    for (const [bot, items] of Object.entries(record.pending_mentions as Record<string, unknown>)) {
      if (Array.isArray(items)) pending[bot] = items as Array<Record<string, unknown>>;
    }
  }
  return {
    project_id: String(pick(record, ["project_id"], projectId)),
    goal: text("goal"),
    phase: text("phase"),
    recent_decisions: Array.isArray(record.recent_decisions)
      ? (record.recent_decisions as Array<Record<string, unknown>>)
      : [],
    active_locks: Array.isArray(record.active_locks)
      ? (record.active_locks as string[]).filter((l): l is string => typeof l === "string")
      : [],
    // "none" is the server's explicit no-constitution marker, not a real hash.
    constitution_hash: String(pick(record, ["constitution_hash"], "")),
    transcript_digest: digest(),
    open_questions: Array.isArray(record.open_questions)
      ? (record.open_questions as Array<Record<string, unknown>>)
      : [],
    member_briefs: briefs,
    pending_mentions: pending,
    updated_at: String(pick(record, ["updated_at"], "")),
  };
}

// ------------------------------------------------------------- quick-start

/**
 * A pre-filled starting point: instructions to write once, plus the agent
 * *roles* a crew of this shape needs.
 *
 * Roles are intentions, not names. Bots are runtime data in the roster, never
 * configuration, so a template cannot promise that a bot called "reviewer"
 * exists — the picker maps each role onto whatever bots the Gateway reports and
 * lets the user change the mapping before anything is created.
 */
export interface ProjectTemplate {
  id: string;
  name: string;
  summary: string;
  instructions: string;
  roles: string[];
  collaboration: CollaborationPatch;
}

export const PROJECT_TEMPLATES: ProjectTemplate[] = [
  {
    id: "blank",
    name: "Blank project",
    summary: "A plain workspace. Add agents later.",
    instructions: "",
    roles: [],
    collaboration: {},
  },
  {
    id: "feature",
    name: "Feature build",
    summary: "Plan, implement, then review before it ships.",
    instructions:
      "Plan the change before writing code. Keep the scope to what was asked, and state any assumption you had to make.",
    roles: ["architect", "coder", "reviewer"],
    collaboration: { orchestration_mode: "moderated", require_evidence: true, mention_policy: "strict" },
  },
  {
    id: "bug-hunt",
    name: "Bug hunt",
    summary: "Reproduce, isolate the cause, then fix and prove it.",
    instructions:
      "Reproduce the failure before theorising about it. Report the exact command or input that triggers it, and confirm the fix with evidence rather than assertion.",
    roles: ["reproducer", "diagnostician", "fixer"],
    collaboration: { orchestration_mode: "moderated", lock_policy: "strict", require_evidence: true },
  },
  {
    id: "research",
    name: "Research",
    summary: "Gather sources, compare them, and summarise the disagreement.",
    instructions:
      "Cite a source for every factual claim. Where sources disagree, say so rather than picking one silently.",
    roles: ["researcher", "fact-checker", "synthesizer"],
    collaboration: { orchestration_mode: "quorum", conflict_policy: "vote", require_evidence: true },
  },
  {
    id: "launch",
    name: "Launch prep",
    summary: "Draft the copy, check it, and line up the rollout.",
    instructions:
      "Say plainly what is ready and what is not. A launch note that overstates readiness is worse than one that lists the gaps.",
    roles: ["writer", "editor", "release-manager"],
    collaboration: { orchestration_mode: "parallel", conflict_policy: "moderator", standup_interval_turns: 5 },
  },
];

/** Look a template up by id. An unknown id yields the blank template, never a crash. */
export function getProjectTemplate(id: string): ProjectTemplate {
  return PROJECT_TEMPLATES.find((template) => template.id === id) || PROJECT_TEMPLATES[0];
}
