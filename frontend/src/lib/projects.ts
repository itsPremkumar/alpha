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
