import { get, send, asList, pick } from "./http";

export interface ScheduledTask {
  id: string;
  title: string;
  prompt: string;
  schedule_type: string;
  schedule_spec: Record<string, unknown>;
  timezone: string;
  status: string;
  assistant_id?: string;
  next_run?: string;
}

function toTask(t: Record<string, unknown>): ScheduledTask {
  return {
    id: String(pick(t, ["id", "task_id"], "")),
    title: String(pick(t, ["title", "name"], "")),
    prompt: String(pick(t, ["prompt", "input"], "")),
    schedule_type: String(pick(t, ["schedule_type"], "")),
    schedule_spec: (t.schedule_spec as Record<string, unknown>) ?? {},
    timezone: String(pick(t, ["timezone"], "")),
    status: String(pick(t, ["status", "state"], "unknown")),
    assistant_id: typeof t.assistant_id === "string" ? t.assistant_id : undefined,
    next_run: typeof t.next_run_at === "string" ? t.next_run_at : typeof t.next_run === "string" ? t.next_run : undefined,
  };
}

export async function listScheduledTasks(): Promise<ScheduledTask[]> {
  const d = await get<unknown>("/scheduled-tasks");
  return asList(d, ["tasks", "data"]).map((t) => toTask(t));
}

export interface TaskDraft {
  title: string;
  prompt: string;
  schedule_type: "cron" | "interval";
  cron?: string;
  interval_seconds?: number;
  timezone: string;
  assistant_id?: string;
}

export async function createScheduledTask(draft: TaskDraft): Promise<ScheduledTask> {
  const body: Record<string, unknown> = {
    title: draft.title,
    prompt: draft.prompt,
    schedule_type: draft.schedule_type,
    schedule_spec:
      draft.schedule_type === "cron" ? { cron: draft.cron } : { every_seconds: draft.interval_seconds },
    timezone: draft.timezone,
  };
  if (draft.assistant_id) body.assistant_id = draft.assistant_id;
  const d = await send<Record<string, unknown>>("/scheduled-tasks", "POST", body);
  return toTask(d);
}

export async function pauseTask(id: string): Promise<void> {
  await send(`/scheduled-tasks/${encodeURIComponent(id)}/pause`, "POST", {});
}

export async function resumeTask(id: string): Promise<void> {
  await send(`/scheduled-tasks/${encodeURIComponent(id)}/resume`, "POST", {});
}

export async function triggerTask(id: string): Promise<void> {
  await send(`/scheduled-tasks/${encodeURIComponent(id)}/trigger`, "POST", {});
}

export async function deleteTask(id: string): Promise<void> {
  await send(`/scheduled-tasks/${encodeURIComponent(id)}`, "DELETE");
}

export async function taskRuns(id: string): Promise<Array<Record<string, unknown>>> {
  try {
    const d = await get<unknown>(`/scheduled-tasks/${encodeURIComponent(id)}/runs`);
    return asList(d, ["runs", "data"]);
  } catch {
    return [];
  }
}

export async function previewCron(cron: string, timezone: string): Promise<string[]> {
  const d = await send<Record<string, unknown>>("/scheduled-tasks/preview-cron", "POST", {
    cron,
    timezone,
    count: 5,
  });
  const occ = d.occurrences;
  if (!Array.isArray(occ)) return [];
  return occ.map((o) => {
    if (typeof o === "string") return o;
    const rec = o as Record<string, unknown>;
    return String(rec.local_time ?? rec.run_at ?? JSON.stringify(o));
  });
}

/** Friendly one-line summary of a task's schedule. */
export function describeSchedule(t: ScheduledTask): string {
  const spec = t.schedule_spec || {};
  if (t.schedule_type === "cron" && typeof spec.cron === "string") return `Cron: ${spec.cron}`;
  if (typeof spec.every_seconds === "number") return `Every ${spec.every_seconds}s`;
  return t.schedule_type || "manual";
}
