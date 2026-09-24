export const WORKSPACE_VIEW_IDS = [
  "chat",
  "warroom",
  "bots",
  "messages",
  "kanban",
  "runs",
  "files",
  "scheduled",
  "subagents",
  "skills",
  "memory",
  "projects",
  "dashboard",
  "agents",
  "team",
  "channels",
  "workforce",
  "system",
  "integration",
  "settings",
  "workflows",
] as const;

export type WorkspaceView = (typeof WORKSPACE_VIEW_IDS)[number];

export function isWorkspaceView(value: unknown): value is WorkspaceView {
  return typeof value === "string" && (WORKSPACE_VIEW_IDS as readonly string[]).includes(value);
}

export function workspaceViewFromSearch(search: string): WorkspaceView {
  const requested = new URLSearchParams(search).get("view");
  return isWorkspaceView(requested) ? requested : "chat";
}

export function workspaceViewUrl(view: WorkspaceView, pathname = "/"): string {
  const params = new URLSearchParams();
  if (view !== "chat") params.set("view", view);
  const query = params.toString();
  return query ? `${pathname}?${query}` : pathname;
}
