import { get, send, asList, pick } from "./http";

export type KanbanStatus = "ready" | "in_progress" | "review" | "done";

export const KANBAN_COLUMNS: Array<{ id: KanbanStatus; label: string; hint: string }> = [
  { id: "ready", label: "To do", hint: "Waiting to start" },
  { id: "in_progress", label: "Doing", hint: "Someone is on it" },
  { id: "review", label: "Review", hint: "Needs a check" },
  { id: "done", label: "Done", hint: "Finished" },
];

export interface KanbanTask {
  id: string;
  title: string;
  status: string;
  assignee: string;
  description: string;
}

export async function listKanbanTasks(): Promise<KanbanTask[]> {
  const d = await get<unknown>("/company/kanban/tasks?limit=100");
  return asList(d, ["tasks", "data"]).map((t, i) => ({
    id: String(pick(t, ["id", "task_id"], `task-${i}`)),
    title: String(pick(t, ["title", "name", "summary"], "")),
    status: String(pick(t, ["status", "state"], "ready")),
    assignee: String(pick(t, ["assignee", "bot_name", "owner"], "")),
    description: String(pick(t, ["description", "notes"], "")),
  }));
}

export async function moveKanbanTask(taskId: string, status: KanbanStatus, note = ""): Promise<void> {
  await send(`/company/kanban/tasks/${encodeURIComponent(taskId)}/update`, "POST", {
    new_status: status,
    bot_name: "ui",
    log_message: note,
  });
}

export async function kanbanEvents(): Promise<Array<Record<string, unknown>>> {
  try {
    const d = await get<unknown>("/company/kanban/events");
    return asList(d, ["events", "data"]);
  } catch {
    return [];
  }
}
