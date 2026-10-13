import { get, send, asList, pick } from "./http";

/**
 * The server's own card vocabulary.
 *
 * `CompanyKanbanEngine.TaskStatus` (backend/packages/harness/alpha/company/
 * kanban.py:18) is a `StrEnum` of exactly six values:
 *
 *     backlog | todo | in_progress | review | done | blocked
 *
 * The board previously declared its own narrower four-value
 * `KanbanStatus` (`ready | in_progress | review | done`) and mapped the
 * server's status string through verbatim. `ready` is not a value the server
 * can ever hold, and `backlog`, `todo` and `blocked` were therefore values the
 * board had no column for. `TeamOpsSection` buckets cards with
 * `tasks.filter((t) => t.status === col.id)`, so a real `backlog`/`todo`/
 * `blocked` card matched NO column and was never rendered — while the header
 * still counted it, so the board read "Work board (3)" and showed one card
 * with nothing explaining the difference.
 *
 * `ready` survives as the on-the-wire spelling the UI and the `update` route
 * use for "todo" (the backend's own `list_tasks` filter does the same
 * translation: `clean_status == "ready" and t.status.value in ("todo",
 * "backlog")`, kanban.py:125). The two vocabularies are therefore mapped
 * explicitly here instead of by accident.
 */
export const SERVER_KANBAN_STATUSES = [
  "backlog",
  "todo",
  "in_progress",
  "review",
  "done",
  "blocked",
] as const;

export type ServerKanbanStatus = (typeof SERVER_KANBAN_STATUSES)[number];

/**
 * The board's four columns, named with the server's own statuses.
 *
 * `KANBAN_COLUMNS` is what the board renders, and every id in it is a value
 * the server can actually hold, so `filter(t => t.status === col.id)` can no
 * longer drop a real card. A status from a NEWER Gateway that is still not one
 * of these four is NOT snapped to a column: it is surfaced by
 * `unmappedKanbanTasks` and rendered in its own column (TeamOpsSection), so an
 * unfamiliar status is displayed rather than silently hidden or rewritten.
 */
export type KanbanStatus = "todo" | "in_progress" | "review" | "done";

export const KANBAN_COLUMNS: Array<{ id: KanbanStatus; label: string; hint: string }> = [
  { id: "todo", label: "To do", hint: "Waiting to start" },
  { id: "in_progress", label: "Doing", hint: "Someone is on it" },
  { id: "review", label: "Review", hint: "Needs a check" },
  { id: "done", label: "Done", hint: "Finished" },
];

/**
 * The column that carries a status the four real columns cannot hold.
 *
 * `backlog` and `blocked` are real server statuses with no column of their
 * own, and inventing a stage for them would be a fabrication — so they are
 * rendered in one explicitly-labelled "other statuses" column, next to any
 * status a newer Gateway might add. Nothing is coerced and nothing is dropped.
 */
export const UNMAPPED_COLUMN_ID = "unmapped";

/** Human label for one server status, or `null` when we do not recognise it. */
export function kanbanStatusLabel(status: string): string | null {
  if (status === "todo" || status === "ready") return "To do";
  if (status === "in_progress") return "Doing";
  if (status === "review") return "Review";
  if (status === "done") return "Done";
  if (status === "backlog") return "Backlog";
  if (status === "blocked") return "Blocked";
  return null;
}

export interface KanbanTask {
  id: string;
  title: string;
  /** The server's status, verbatim. Never coerced into a board column. */
  status: string;
  assignee: string;
  description: string;
  /** Server `created_at` (epoch seconds) / `updated_at`, or null when absent. */
  createdAt: number | null;
  updatedAt: number | null;
  /** Server `priority` (`low|normal|high|critical`), verbatim, or null. */
  priority: string | null;
  /** Server `result` text, or null when the server sent none. */
  result: string | null;
  companyId?: string;
}

function asEpoch(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * Failure propagates: an unreachable `/company/kanban/tasks` must not resolve
 * to `[]`, or the board would render "Work board (0)" for a Gateway that is
 * down. Callers show the reason instead (`serverBoardError` in KanbanSection,
 * `onError` in TeamOpsSection).
 */
export async function listKanbanTasks(): Promise<KanbanTask[]> {
  const { listCompanies, getCompanyWork } = await import("./company");
  const companies = (await listCompanies()).filter((company) => company.state === "active");
  if (companies.length) {
    const boards = await Promise.all(companies.map(async (company) => ({
      companyId: company.company_id,
      items: await getCompanyWork(company.company_id),
    })));
    const companyTasks = boards.flatMap(({ companyId, items }) => items.map((item) => ({
      id: `${companyId}::${item.task_id}`,
      title: item.title,
      status: item.column === "in_review" ? "review" : item.column,
      assignee: item.assignee_agent_handle ?? "",
      description: item.definition_of_done.join("\n"),
      createdAt: null,
      updatedAt: null,
      priority: item.priority ?? null,
      result: null,
      companyId,
    })));
    // The Company OS and the older autonomous-company engine use separate
    // stores. Preserve legacy cards when present; its documented 404 means
    // only that this owner has no legacy organization to read.
    try {
      return [...companyTasks, ...legacyTaskRows(await get<unknown>("/company/kanban/tasks?limit=100"))];
    } catch (error) {
      const status = error && typeof error === "object" && "status" in error
        ? (error as { status?: unknown }).status
        : null;
      if (status === 404 && error instanceof Error && /No active organizations found/i.test(error.message)) {
        return companyTasks;
      }
      throw error;
    }
  }
  const d = await get<unknown>("/company/kanban/tasks?limit=100");
  return legacyTaskRows(d);
}

function legacyTaskRows(data: unknown): KanbanTask[] {
  return asList(data, ["tasks", "data"]).map((t) => ({
    id: String(pick(t, ["id", "task_id"], "")),
    // Preserve the server's status verbatim so unfamiliar states stay visible.
    status: String(pick(t, ["status", "state"], "")),
    title: String(pick(t, ["title", "name", "summary"], "")),
    assignee: String(pick(t, ["assignee", "bot_name", "owner"], "")),
    description: String(pick(t, ["description", "body", "notes"], "")),
    createdAt: asEpoch(t.created_at),
    updatedAt: asEpoch(t.updated_at),
    priority: typeof t.priority === "string" && t.priority !== "" ? t.priority : null,
    result: typeof t.result === "string" && t.result !== "" ? t.result : null,
  }));
}

/**
 * Tasks whose status no board column can hold.
 *
 * The board renders these in `UNMAPPED_COLUMN_ID` instead of dropping them, so
 * a `backlog`/`blocked` card — or a status a newer Gateway introduced — is
 * visible and named rather than silently absent.
 */
export function unmappedKanbanTasks(tasks: KanbanTask[]): KanbanTask[] {
  return tasks.filter((t) => !(KANBAN_COLUMNS as Array<{ id: string }>).some((c) => c.id === t.status));
}

/**
 * The status one column-move away from `from`, or `null` when there is none.
 *
 * Returns `null` for a status the board does not model rather than clamping to
 * an end column: the old `order[indexOf(s) + dir]` with `indexOf === -1`
 * resolved BOTH directions to `order[0]`, so a `blocked` card un-blocked itself
 * on a left-click and on a right-click, silently discarding the blocked
 * reason. A move the board cannot express must be refused, not faked.
 */
export function nextKanbanStatus(from: string, dir: 1 | -1): KanbanStatus | null {
  const i = KANBAN_COLUMNS.findIndex((c) => c.id === from);
  if (i < 0) return null;
  const j = Math.min(KANBAN_COLUMNS.length - 1, Math.max(0, i + dir));
  const next = KANBAN_COLUMNS[j].id;
  return next === from ? null : next;
}

/**
 * The server status to post for a board move.
 *
 * `UpdateKanbanTaskRequest.new_status` is validated by
 * `CompanyKanbanEngine.update_task_status`, which does
 * `TaskStatus(new_status.lower())` (kanban.py:156). `TaskStatus` has no
 * `ready`, so the board's own spelling is translated to the server's here
 * rather than 500-ing on a stage the enum never declared.
 */
export function serverStatusFor(status: KanbanStatus): ServerKanbanStatus {
  return status;
}

export async function moveKanbanTask(taskId: string, status: KanbanStatus, note = ""): Promise<void> {
  await send(`/company/kanban/tasks/${encodeURIComponent(taskId)}/update`, "POST", {
    new_status: serverStatusFor(status),
    bot_name: "ui",
    log_message: note,
  });
}

/**
 * Activity-log reads. Failure propagates: an empty event feed must mean "the
 * server said nothing happened", not "the read failed". This used to
 * `catch { return [] }`, which made a renamed or unreachable route
 * indistinguishable from a quiet board.
 */
export async function kanbanEvents(): Promise<Array<Record<string, unknown>>> {
  const d = await get<unknown>("/company/kanban/events");
  return asList(d, ["events", "data"]);
}
