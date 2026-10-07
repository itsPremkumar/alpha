export const WORKSPACE_VIEW_IDS = [
  "overview",
  "chat",
  "warroom",
  "deliberation",
  "bots",
  "company",
  "messages",
  "peers",
  "external-alpha",
  "kanban",
  "runs",
  // `run-inspector` was missing here while `NavTabs.tsx` declared it, so
  // `isWorkspaceView("run-inspector")` returned false for a view the UI
  // navigates to. Documented in docs/WIRING_AUDIT.md §1.11 as a live bug.
  "run-inspector",
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
  "forge",
  "supervisor",
  "protocols",
  // The APEX executive control plane. Listed here for the same reason the two
  // ids below are: `WORKSPACE_VIEW_IDS` is a second list of the `WorkspaceView`
  // union, so an id that reaches the nav but not this file makes `?view=apex`
  // silently fall back to `chat`.
  "apex",
  // `reliability` was missing here while `NavTabs.tsx` declared it, so
  // `isWorkspaceView("reliability")` returned false and `?view=reliability`
  // silently fell back to `chat` — the same defect `run-inspector` had above.
  // `workspace-nav.test.mjs` now pins this list against the tab list in both
  // directions so neither can drift again.
  "reliability",
  // The intelligence control plane: `?view=intelligence` must resolve like
  // every other id, so this list stays a sibling of the `WorkspaceView` union.
  "intelligence",
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
