/**
 * A swarm's structure, as the words a person can act on.
 *
 * ## What was missing
 *
 * `frontend/src/lib/teamops.ts` already exported `swarmDetails()` and
 * `swarmMetrics()` against `GET /api/swarms/{id}` and `.../metrics` — and
 * **nothing imported either of them**. The swarms list therefore rendered one
 * line per swarm: id, status, strategy, objective, and aggregate counters
 * ("0 of 3 tasks complete · running 0 · pending 3").
 *
 * Everything that makes a swarm legible was already in the payload and simply
 * unread. Measured live on 2026-10-06 against `swm-db45b4f8`: 5 tasks, each
 * carrying 35 fields including `state`, `assigned_worker`, `worker_type`,
 * `dependencies`, `duration_seconds`, `attempts`, `token_usage`, `tool_calls`,
 * `capability_tags` and `error_message`; plus `metrics.team` (assigned vs
 * unassigned, with the reason per task), `metrics.leader_election` (leader,
 * score, method, reason, candidates) and a 15-type event vocabulary across the
 * 20 swarms on this install. "Several agents working together" was a caption
 * above a counter.
 *
 * ## Why this is a separate module
 *
 * Same reason as `teamops-progress.ts`: the honesty rules below are the whole
 * point, and they have to be testable without loading the section's import graph.
 *
 * ## The rules, each of which has a tempting wrong reading
 *
 * | Server sent | This renders | Never |
 * | --- | --- | --- |
 * | `assigned_worker: null` | `unassigned — no worker has claimed this` | the elected leader's name |
 * | no `state` key | `state not reported by the server` | `pending` |
 * | `state: "wat"` | `wat`, neutral tone | snapped to `running` |
 * | `duration_seconds: null` | `no duration reported` | `0s` |
 * | `token_usage` absent | `tokens not reported` | `0 tokens` |
 * | `dependencies: []` | `root task — nothing must finish first` | `waiting on nothing` |
 * | unknown `event_type` | the string verbatim | dropped from the timeline |
 * | a `result_summary` that is tool-call markup | flagged as not a summary | shown as the worker's answer |
 * | no `metrics` at all | `team and leader not reported` | "0 unassigned" |
 *
 * The last row of that table is the one that matters most. A `result_summary`
 * is only a summary if a human wrote prose into it; this server also writes raw
 * model channel output there. Presenting that as "the worker's answer" is the
 * same defect as rendering an absent counter as a zero, one layer further out.
 *
 * `state`, not `status`: the task payload has no `status` key. Reading
 * `task.status` yields `undefined` for every task, which would render a full DAG
 * of blank rows and read as "no state information" — technically true and
 * completely misleading.
 */

/** Visual weight for a badge. `gray` is the honest default for anything unknown. */
export type Tone = "blue" | "amber" | "green" | "red" | "gray";

/**
 * Sentinels that mark `result_summary` as raw model output rather than a
 * summary a person wrote. This is a FORMAT test, not a judgement about meaning:
 * a channel/role marker means the string was never composed for display, so it
 * cannot be presented as an answer no matter what it happens to contain.
 */
const TOOL_CALL_SENTINELS = ["tool_call", "minimax", "<tool", "</tool", "<|", "assistant\n"];

/** A nested object, or `{}`. Key presence is not enough: `{ token_usage: undefined }`
 * has the key AND no value, so `t.token_usage.foo` would throw on a payload that
 * merely skipped the field. */
function objectOr(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function has(obj: unknown, key: string): boolean {
  return typeof obj === "object" && obj !== null && key in (obj as Record<string, unknown>);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** A finite non-negative count, or `null` for "not reported". `0` is preserved. */
export function measuredCount(value: unknown): number | null {
  if (!isFiniteNumber(value) || value < 0) return null;
  return value;
}

/**
 * Whether a `result_summary` is presentable as prose.
 *
 * Exported because the panel needs it and the test needs to drive it directly.
 */
export function isDisplayableSummary(summary: unknown): boolean {
  if (typeof summary !== "string") return false;
  const text = summary.trim();
  if (!text) return false;
  return !TOOL_CALL_SENTINELS.some((s) => text.includes(s));
}

/** One row of the task DAG. Every note is what to print when the value is absent. */
export interface SwarmTaskRow {
  taskId: string;
  /** `null` when the server reported no objective. */
  objective: string | null;
  objectiveNote: string;
  /** Verbatim server string, or `null` when absent. Never inferred. */
  state: string | null;
  stateTone: Tone;
  stateNote: string;
  /** `null` means unassigned — a real, distinct outcome. */
  worker: string | null;
  workerNote: string;
  workerType: string | null;
  dependsOn: string[];
  dependencyNote: string;
  durationSeconds: number | null;
  durationNote: string;
  totalTokens: number | null;
  tokenNote: string;
  toolCalls: number | null;
  toolCallNote: string;
  /** `null` when there is no error. An empty string is not an error. */
  error: string | null;
  startedAt: string | null;
  completedAt: string | null;
  /** Only set when the server wrote something a person could read. */
  summary: string | null;
  summaryNote: string;
}

function stateTone(state: string | null): Tone {
  switch (state) {
    case "completed":
      return "green";
    case "running":
      return "blue";
    case "failed":
      return "red";
    case "cancelled":
      return "amber";
    case "pending":
    case "queued":
      return "gray";
    default:
      // Anything this build does not know renders neutral rather than being
      // snapped onto a known state the server never reported.
      return "gray";
  }
}

export function swarmTaskRows(tasks: unknown): SwarmTaskRow[] {
  // A list is tolerated as well as a map, because a plan serialised from a list
  // is the same plan; only the addressing differs.
  const entries: Array<[string, unknown]> = Array.isArray(tasks)
    ? tasks.map((t, i) => [String(has(t, "task_id") ? (t as Record<string, unknown>).task_id : i), t])
    : has(tasks, "x") || typeof tasks !== "object" || tasks === null
      ? []
      : Object.entries(tasks as Record<string, unknown>);

  return entries.map(([key, raw]) => {
    const t = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;

    const objective = typeof t.objective === "string" && t.objective.trim() ? t.objective : null;
    const state = typeof t.state === "string" && t.state.trim() ? t.state : null;
    const worker = typeof t.assigned_worker === "string" && t.assigned_worker.trim() ? t.assigned_worker : null;
    const workerType = typeof t.worker_type === "string" && t.worker_type.trim() ? t.worker_type : null;

    const depsRaw = t.dependencies;
    const dependsOn = Array.isArray(depsRaw)
      ? depsRaw.filter((d): d is string => typeof d === "string" && d.length > 0)
      : [];

    const duration = measuredCount(t.duration_seconds);
    // `objectOr`, not `has(...) ? t.token_usage : {}`: the key can be present
    // with an `undefined` value, and reading `.total_tokens` off that throws on a
    // payload that merely omitted the field.
    const usage = objectOr(t.token_usage);
    const totalTokens = measuredCount(usage.total_tokens);
    const toolCalls = measuredCount(t.tool_calls);

    const summaryText = t.result_summary;
    const displayable = isDisplayableSummary(summaryText);

    const error = typeof t.error_message === "string" && t.error_message.trim() ? t.error_message : null;

    return {
      taskId: typeof t.task_id === "string" && t.task_id ? t.task_id : key,
      objective,
      objectiveNote: objective === null ? "objective not reported by the server" : "",
      state,
      stateTone: stateTone(state),
      stateNote: state === null ? "state not reported by the server" : "",
      worker,
      workerNote:
        worker === null
          ? workerType === "ephemeral"
            ? "unassigned — this worker is ephemeral, so no bot name is expected"
            : "unassigned — no worker has claimed this task"
          : "",
      workerType,
      dependsOn,
      dependencyNote:
        dependsOn.length === 0
          ? "root task — nothing has to finish first"
          : `waits for ${dependsOn.join(", ")}`,
      durationSeconds: duration,
      durationNote: duration === null ? "no duration reported" : "",
      totalTokens,
      tokenNote: totalTokens === null ? "tokens not reported" : "",
      toolCalls,
      toolCallNote: toolCalls === null ? "tool calls not reported" : "",
      error,
      startedAt: typeof t.started_at === "string" && t.started_at ? t.started_at : null,
      completedAt: typeof t.completed_at === "string" && t.completed_at ? t.completed_at : null,
      summary: displayable ? (summaryText as string).trim() : null,
      summaryNote: displayable
        ? ""
        : // The distinction is whether text is actually THERE. An explicit
          // `undefined` leaves the key present (`"result_summary" in task` is
          // true), so a key-presence test claimed "raw model output" for a task
          // that stored nothing at all — which is a claim about content made
          // without any content. Only non-empty text can be undisplayable text.
          typeof summaryText === "string" && summaryText.trim().length > 0
          ? "the server stored raw model output here, not a summary written for display"
          : "no result recorded",
    };
  });
}

export interface SwarmLeaderView {
  /** `null` when no leader was elected or the field is absent. */
  leader: string | null;
  note: string;
  method: string | null;
  score: number | null;
  reason: string | null;
}

export function swarmLeaderView(detail: unknown): SwarmLeaderView {
  const metrics = objectOr(has(detail, "metrics") ? (detail as Record<string, unknown>).metrics : null);
  const election = objectOr(metrics.leader_election);

  if (Object.keys(election).length === 0) {
    return {
      leader: null,
      note: "no leader election reported by the server",
      method: null,
      score: null,
      reason: null,
    };
  }

  const leader = typeof election.leader === "string" && election.leader.trim() ? election.leader : null;
  return {
    leader,
    // An election with no winner is a real outcome and is reported as one.
    note: leader === null ? "the server reported an election but named no leader" : "",
    method: typeof election.method === "string" && election.method ? election.method : null,
    score: isFiniteNumber(election.score) ? election.score : null,
    reason: typeof election.reason === "string" && election.reason ? election.reason : null,
  };
}

export interface SwarmTeamView {
  assigned: number | null;
  unassigned: number | null;
  note: string;
}

export function swarmTeamView(detail: unknown): SwarmTeamView {
  const metrics = objectOr(has(detail, "metrics") ? (detail as Record<string, unknown>).metrics : null);
  const team = objectOr(metrics.team);

  if (Object.keys(team).length === 0) {
    return { assigned: null, unassigned: null, note: "team assignment not reported by the server" };
  }

  const assigned = measuredCount(team.assigned);
  const unassigned = measuredCount(team.unassigned);
  return {
    assigned,
    unassigned,
    note: assigned === null && unassigned === null ? "team counts not reported" : "",
  };
}

export interface SwarmEventRow {
  sequence: number | null;
  /** Verbatim. An unrecognised type is shown, not dropped. */
  eventType: string;
  at: string | null;
  taskId: string | null;
  worker: string | null;
}

/**
 * The event timeline, oldest first by `sequence` when every row has one.
 *
 * Rows without a sequence keep their server order at the end rather than being
 * dropped: an event the server recorded is evidence, and its absence from a
 * timeline would look like it never happened.
 */
export function swarmEventRows(events: unknown): SwarmEventRow[] {
  if (!Array.isArray(events)) return [];
  const rows = events
    .filter((e): e is Record<string, unknown> => typeof e === "object" && e !== null)
    .map((e, index) => ({
      sequence: measuredCount(e.sequence),
      order: index,
      eventType: typeof e.event_type === "string" && e.event_type ? e.event_type : "event type not reported",
      at: typeof e.timestamp === "string" && e.timestamp ? e.timestamp : null,
      taskId: typeof e.task_id === "string" && e.task_id ? e.task_id : null,
      worker: typeof e.worker === "string" && e.worker ? e.worker : null,
    }));
  return rows
    .sort((a, b) => {
      if (a.sequence === null && b.sequence === null) return a.order - b.order;
      if (a.sequence === null) return 1;
      if (b.sequence === null) return -1;
      return a.sequence - b.sequence;
    })
    .map(({ order: _order, ...rest }) => rest);
}

/** The whole panel's derivation, so the section renders without doing logic. */
export interface SwarmStructureView {
  tasks: SwarmTaskRow[];
  leader: SwarmLeaderView;
  team: SwarmTeamView;
  events: SwarmEventRow[];
  /** Set when the swarm carried no readable `tasks` field at all. */
  taskReadNote: string | null;
  eventReadNote: string | null;
}

export function swarmStructureView(
  detail: unknown,
  events: unknown,
): SwarmStructureView {
  const tasksRaw = has(detail, "tasks") ? (detail as Record<string, unknown>).tasks : undefined;
  const tasks = swarmTaskRows(tasksRaw);
  const events_ = swarmEventRows(events);

  return {
    tasks,
    leader: swarmLeaderView(detail),
    team: swarmTeamView(detail),
    events: events_,
    taskReadNote:
      tasks.length === 0
        ? "this swarm reported no tasks — the plan is empty, or the field was not sent"
        : null,
    eventReadNote: !Array.isArray(events)
      ? "the event log could not be read, so no timeline is shown"
      : events_.length === 0
        ? "the server reported no events for this swarm"
        : null,
  };
}