import { get, send } from "./http";

/**
 * Bot routines: `GET`/`POST /api/bots/{name}/routines` and
 * `DELETE /api/bots/{name}/routines/{routine}`.
 *
 * A routine is the bot-shaped face of ONE scheduled task, and two stores each
 * answer half of it:
 *
 *   - the profile owns the routine record (name, schedule label, action, the
 *     id of the task it was created as);
 *   - the scheduler owns the task (`status`, next run, last run / error), which
 *     is why the route joins the two server-side and why this client keeps
 *     them apart: `task: null` renders as "no task recorded", never as an
 *     unscheduled routine.
 *
 * The detail page's honesty contract applies here: a field the Gateway did not
 * send is `null` ("not reported"), never `""` / `0` / `false`, and a payload
 * that carried no `routines` key at all stays `null` — distinct from a list the
 * server sent empty.
 */

export interface BotRoutineTask {
  id: string | null;
  /** Task lifecycle, verbatim (`enabled` / `paused` / whatever a newer build says). */
  status: string | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run_id: string | null;
  last_error: string | null;
  /** The scheduler's own count; `null` when the server omitted it. */
  run_count: number | null;
}

export interface BotRoutine {
  name: string;
  /** Server-derived display label ("cron: …" / "every 600s") — `null` when omitted. */
  schedule: string | null;
  action: string | null;
  /**
   * The profile flag recorded at creation. The LIVE answer is `task.status`;
   * a paused task does not rewrite the profile record, so this must never be
   * rendered as the current state.
   */
  enabled: boolean | null;
  /** The scheduled task this routine was created as; `null` when none recorded. */
  task_id: string | null;
  /** The task's owner — the account whose scheduled-tasks list carries it. */
  owner_user_id: string | null;
  schedule_type: string | null;
  schedule_spec: Record<string, unknown> | null;
  timezone: string | null;
  /** The live task, or `null` when no task is recorded for this routine. */
  task: BotRoutineTask | null;
  /** The server's own words when the joined task read failed. */
  task_error: string | null;
}

export interface BotRoutines {
  bot: string | null;
  /** `null` when the Gateway sent no `routines` key (≠ "this bot has none"). */
  routines: BotRoutine[] | null;
}

/** One routine creation, as the route accepts it. */
export interface BotRoutineDraft {
  name: string;
  action: string;
  schedule_type: "cron" | "interval";
  cron?: string;
  interval_seconds?: number;
  timezone: string;
}

/** The create answer: the stored routine and the task it was created as. */
export interface BotRoutineCreated {
  routine: BotRoutine | null;
  task: BotRoutineTask | null;
}

/** The delete answer: what was removed, and what happened to the task. */
export interface BotRoutineDeleted {
  routine: string | null;
  removed: boolean | null;
  /**
   * The server's own word for the task's disposal: `"deleted"`, `"not_found"`
   * (already gone), `"no_task_recorded"`, `"owner_not_recorded"` (left in
   * place on purpose) — passed through verbatim, never folded into a boolean.
   */
  task_deletion: string | null;
}

const asString = (v: unknown): string | null => (typeof v === "string" ? v : null);
const asNumber = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const asBool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
const asRow = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

/** Map the joined task record (exported for tests). `null` when not a record. */
export function normalizeRoutineTask(raw: unknown): BotRoutineTask | null {
  const row = asRow(raw);
  if (row === null) return null;
  return {
    id: asString(row.id),
    status: asString(row.status),
    next_run_at: asString(row.next_run_at),
    last_run_at: asString(row.last_run_at),
    last_run_id: asString(row.last_run_id),
    last_error: asString(row.last_error),
    run_count: asNumber(row.run_count),
  };
}

/** Map one routine record (exported for tests). `null` when it is not a record. */
export function normalizeBotRoutine(raw: unknown): BotRoutine | null {
  const row = asRow(raw);
  if (row === null) return null;
  return {
    // A nameless row is kept rather than dropped so a malformed entry cannot
    // silently shrink the list; the page renders the blank verbatim.
    name: asString(row.name) ?? "",
    schedule: asString(row.schedule),
    action: asString(row.action),
    enabled: asBool(row.enabled),
    task_id: asString(row.task_id),
    owner_user_id: asString(row.owner_user_id),
    schedule_type: asString(row.schedule_type),
    schedule_spec: asRow(row.schedule_spec),
    timezone: asString(row.timezone),
    task: normalizeRoutineTask(row.task),
    task_error: asString(row.task_error),
  };
}

/** Map `GET /api/bots/{name}/routines` (exported for tests). */
export function normalizeBotRoutines(raw: unknown): BotRoutines {
  const row = asRow(raw);
  const list = row !== null && Array.isArray(row.routines) ? row.routines : null;
  return {
    bot: row === null ? null : asString(row.bot_name),
    routines:
      list === null
        ? null
        : list.map(normalizeBotRoutine).filter((routine): routine is BotRoutine => routine !== null),
  };
}

/** Read a bot's routines with their live task state. */
export async function listBotRoutines(name: string): Promise<BotRoutines> {
  return normalizeBotRoutines(await get<unknown>(`/bots/${encodeURIComponent(name)}/routines`));
}

/**
 * Create one routine: a duplicate name is REFUSED by the server (409) rather
 * than silently replaced, so the error surfaces here unchanged.
 */
export async function createBotRoutine(name: string, draft: BotRoutineDraft): Promise<BotRoutineCreated> {
  const body: Record<string, unknown> = {
    name: draft.name,
    action: draft.action,
    schedule_type: draft.schedule_type,
    schedule_spec: draft.schedule_type === "cron" ? { cron: draft.cron } : { every_seconds: draft.interval_seconds },
    timezone: draft.timezone,
  };
  const raw = await send<Record<string, unknown>>(`/bots/${encodeURIComponent(name)}/routines`, "POST", body);
  const row = asRow(raw);
  return {
    routine: normalizeBotRoutine(row?.routine ?? null),
    task: normalizeRoutineTask(row?.task ?? null),
  };
}

/** Remove a routine and retire its scheduled task (in that order of proof). */
export async function deleteBotRoutine(name: string, routine: string): Promise<BotRoutineDeleted> {
  const raw = await send<Record<string, unknown>>(`/bots/${encodeURIComponent(name)}/routines/${encodeURIComponent(routine)}`, "DELETE");
  const row = asRow(raw);
  return {
    routine: row === null ? null : asString(row.routine),
    removed: row === null ? null : asBool(row.removed),
    task_deletion: row === null ? null : asString(row.task_deletion),
  };
}

/**
 * The schedule line for one routine: the stored label, else a reading of the
 * stored spec, else the raw `schedule_type` — `null` only when the record
 * carried none of them.
 */
export function routineScheduleLine(routine: BotRoutine): string | null {
  if (routine.schedule) return routine.schedule;
  const spec = routine.schedule_spec;
  if (routine.schedule_type === "cron" && typeof spec?.cron === "string") return `cron: ${spec.cron}`;
  if (typeof spec?.every_seconds === "number") return `every ${spec.every_seconds}s`;
  if (typeof spec?.run_at === "string") return `once at ${spec.run_at}`;
  return routine.schedule_type;
}

/**
 * The task-retirement sentence rendered after a delete, in the server's own
 * terms. `task_deletion` is passed through verbatim in the fallback so a newer
 * Gateway's word is shown rather than guessed at.
 */
export function deletionNote(result: BotRoutineDeleted): string {
  const name = result.routine ?? "the routine";
  switch (result.task_deletion) {
    case "deleted":
      return `Removed ${name} and retired its scheduled task.`;
    case "not_found":
      return `Removed ${name}. Its scheduled task was already gone — the dangling record is cleaned up.`;
    case "no_task_recorded":
      return `Removed ${name}. No scheduled task was recorded for it.`;
    case "owner_not_recorded":
      return `Removed ${name}. Its task's owner was not recorded, so the task (if any) was left in place.`;
    default:
      return result.task_deletion === null
        ? `Removed ${name}. The Gateway did not report the task's disposal.`
        : `Removed ${name} (task: ${result.task_deletion}).`;
  }
}

/** The browser's IANA zone, else "UTC" — the same default the schedules UI uses. */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}
