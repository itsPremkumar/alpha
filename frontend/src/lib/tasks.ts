/**
 * The run-scoped task projection client (Bot Mode 3.5) — `GET /api/tasks?run_id=`.
 *
 * One read over every engine's tasks for one run, in the neutral
 * `alpha.tasks` `TaskContract` vocabulary. The graph plane and Team Ops both
 * consume this projection rather than each re-mapping engine statuses
 * client-side, so a status shown for a swarm task and a graph task means the
 * same thing by construction.
 *
 * The honesty rules travel with the shape:
 *
 *  * `engine_errors` is carried verbatim — a failed engine read is a reason,
 *    not a zero; the UI must never render "this run has no tasks" over a
 *    Gateway that merely could not answer one engine.
 *  * `engines` lists only engines actually queried, so a missing key is
 *    "not collected", never "collected and empty".
 *  * every absent field normalizes to `null`, never to a guessed default.
 */

import { get } from "./http";

/** One engine's task, in the neutral contract vocabulary. */
export interface RunTask {
  task_id: string | null;
  run_id: string | null;
  parent_task_id: string | null;
  title: string | null;
  objective: string | null;
  /** Which engine produced it: `graph` / `swarm`. */
  engine: string | null;
  /** The contract (narrow, canonical) status. */
  status: string | null;
  /** The engine's own status, carried rather than erased. */
  native_status: string | null;
  native_type: string | null;
  attempt_count: number | null;
  max_attempts: number | null;
}

/** The projection for one run. */
export interface RunTasksProjection {
  run_id: string;
  /** Total rows across engines, or `null` when the server sent no count. */
  count: number | null;
  /** Engines actually queried, each with its contributed row count. */
  engines: Record<string, number>;
  /** Per-engine read failures, verbatim — never folded into a zero. */
  engine_errors: Record<string, string>;
  tasks: RunTask[];
}

/** Map one contract row (exported for tests). Absent fields stay `null`. */
export function normalizeRunTask(raw: Record<string, unknown>): RunTask {
  const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
  const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
  return {
    task_id: str(raw.task_id),
    run_id: str(raw.run_id),
    parent_task_id: str(raw.parent_task_id),
    title: str(raw.title),
    objective: str(raw.objective),
    engine: str(raw.engine),
    status: str(raw.status),
    native_status: str(raw.native_status),
    native_type: str(raw.native_type),
    attempt_count: num(raw.attempt_count),
    max_attempts: num(raw.max_attempts),
  };
}

/** Map the route's envelope (exported for tests). */
export function normalizeRunTasksProjection(raw: Record<string, unknown>, runId: string): RunTasksProjection {
  const engines: Record<string, number> = {};
  if (raw.engines && typeof raw.engines === "object") {
    for (const [engine, count] of Object.entries(raw.engines as Record<string, unknown>)) {
      if (typeof count === "number" && Number.isFinite(count)) engines[engine] = count;
    }
  }
  const errors: Record<string, string> = {};
  if (raw.engine_errors && typeof raw.engine_errors === "object") {
    for (const [engine, reason] of Object.entries(raw.engine_errors as Record<string, unknown>)) {
      if (typeof reason === "string") errors[engine] = reason;
    }
  }
  const tasks = Array.isArray(raw.tasks)
    ? (raw.tasks as unknown[]).filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === "object").map(normalizeRunTask)
    : [];
  return {
    run_id: typeof raw.run_id === "string" ? raw.run_id : runId,
    count: typeof raw.count === "number" && Number.isFinite(raw.count) ? raw.count : null,
    engines,
    engine_errors: errors,
    tasks,
  };
}

/** Read every engine's tasks for one run, in the neutral contract. */
export async function fetchRunTasks(runId: string): Promise<RunTasksProjection> {
  const raw = await get<Record<string, unknown>>(`/tasks?run_id=${encodeURIComponent(runId)}`);
  return normalizeRunTasksProjection(raw ?? {}, runId);
}
