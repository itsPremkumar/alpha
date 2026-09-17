import { get, send, asList, pick } from "./http";

export interface Project {
  id: string;
  name: string;
  instructions: string;
  status: string;
  created_at: string;
  updated_at: string;
}

function toProject(p: Record<string, unknown>): Project {
  return {
    id: String(pick(p, ["id", "project_id"], "")),
    name: String(pick(p, ["name"], "")),
    instructions: String(pick(p, ["instructions"], "")),
    status: String(pick(p, ["status"], "active")),
    created_at: String(pick(p, ["created_at"], "")),
    updated_at: String(pick(p, ["updated_at"], "")),
  };
}

export async function listProjects(): Promise<Project[]> {
  const d = await get<unknown>("/projects");
  return asList(d, ["projects", "data"]).map((p) => toProject(p));
}

export async function createProject(name: string, instructions = ""): Promise<Project> {
  const d = await send<Record<string, unknown>>("/projects", "POST", { name, instructions });
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

export async function projectThreads(id: string): Promise<ProjectThread[]> {
  try {
    const d = await get<unknown>(`/projects/${encodeURIComponent(id)}/threads`);
    return asList(d, ["threads", "data"]).map((t, i) => ({
      thread_id: String(pick(t, ["thread_id", "id"], `thread-${i}`)),
      display_name: String(pick(t, ["display_name", "title"], "Untitled")),
    }));
  } catch {
    return [];
  }
}
