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
  /** Per-task requested model override, or null when no override was reported. */
  requestedModel: string | null;
  requestedModelNote: string;
  /** Distinct model labels recorded by worker evidence; never inferred from a request. */
  usedModels: string[];
  usedModelNote: string;
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
  /** Verbatim Gateway acceptance state; it is not a synonym for task state. */
  acceptanceStatus: string | null;
  /** Criteria count, or null when the Gateway omitted the field. */
  acceptanceCriteriaCount: number | null;
  /** Server-derived verification verdict, or null when absent. */
  acceptanceVerified: boolean | null;
  /** Plain-language distinction between execution completion and acceptance. */
  acceptanceNote: string;
  /** Advisory receipt citation audit; it does not prove claim correctness. */
  citationNote: string;
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
    const requestedModel = typeof t.model_override === "string" && t.model_override.trim()
      ? t.model_override.trim()
      : null;
    const usedModels = Array.isArray(t.evidence)
      ? [...new Set(t.evidence
          .map((item) => objectOr(item).model)
          .filter((model): model is string => typeof model === "string" && model.trim().length > 0)
          .map((model) => model.trim()))]
      : [];

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
    const acceptanceStatus = typeof t.acceptance_status === "string" && t.acceptance_status.trim()
      ? t.acceptance_status
      : null;
    const acceptanceCriteriaCount = Array.isArray(t.acceptance_criteria)
      ? t.acceptance_criteria.length
      : null;
    const verification = objectOr(t.verification);
    const acceptanceVerified = typeof verification.verified === "boolean"
      ? verification.verified
      : null;
    const acceptanceNote = acceptanceCriteriaCount === 0
      ? "No acceptance criteria were declared. Completed means execution ended; the result was not verified."
      : acceptanceVerified === true
        ? "Gateway acceptance checks passed."
        : acceptanceVerified === false
          ? "Gateway acceptance checks failed."
          : acceptanceCriteriaCount !== null && acceptanceCriteriaCount > 0
            ? "Acceptance criteria were declared, but no verification verdict was reported."
            : acceptanceStatus
              ? `Gateway acceptance status: ${acceptanceStatus}; verification was not inferred.`
              : "Acceptance criteria and verification were not reported by the Gateway.";
    const citationVerdict = objectOr(t.citation_audit);
    // Only an audit carrying the receipt-citations contract speaks. An empty
    // or foreign-shaped field means no audit was computed, so no note — and a
    // resolved verdict citing nothing is a vacuous pass that renders nothing,
    // mirroring the backend's `render_citation_verdict`.
    const citedIds = Array.isArray(citationVerdict.cited) ? citationVerdict.cited : [];
    const citationNote = citationVerdict.source !== "receipt_citations"
      ? ""
      : citationVerdict.citation_resolved === true
        ? citedIds.length > 0
          ? "Receipt citations resolve to recorded calls; this does not prove claim correctness."
          : ""
        : citationVerdict.no_citation_claims === true
          ? "Action claims were not supported by receipt citations."
          : "One or more receipt citations did not resolve to a successful matching call.";

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
      requestedModel,
      requestedModelNote: requestedModel === null ? "no per-task model override reported" : "",
      usedModels,
      usedModelNote: usedModels.length === 0 ? "model not reported in worker evidence" : "",
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
      acceptanceStatus,
      acceptanceCriteriaCount,
      acceptanceVerified,
      acceptanceNote,
      citationNote,
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
 * One topology badge row for the structure panel. The label/note copy mirrors
 * what the composer and Team Ops selectors say about each mode (`TeamOpsSection`
 * and `SwarmModeControl`); an unknown or missing mode is disclosed rather than
 * substituted.
 */
export interface SwarmModeBadge {
  /** The Gateway-reported topology, verbatim; `null` when not reported. */
  mode: string | null;
  label: string;
  note: string;
  tone: Tone;
}

const SWARM_MODE_BADGES: Record<string, { label: string; note: string }> = {
  auto: { label: "Auto", note: "topology routed from the goal and the measured task graph" },
  parallel: { label: "Parallel", note: "independent tasks run concurrently" },
  map_reduce: { label: "Map–Reduce", note: "independent work items, then one gather task combines them" },
  scatter_gather: { label: "Scatter–Gather", note: "broad exploration first, then a collection phase" },
  hierarchical: { label: "Hierarchical", note: "levels coordinate through parent tasks" },
  debate: { label: "Debate", note: "independent positions challenge one another" },
  ensemble: { label: "Ensemble", note: "independent solutions feed a synthesis" },
  coding_worktree: { label: "Coding Worktree", note: "coding workers run in isolated worktrees" },
  read_fanout: { label: "Read Fanout", note: "read-only workers, no isolation cost — safe to scale wide" },
  critical_path_split: { label: "Critical Path Split", note: "the longest dependency chain is shortened" },
};

export function swarmModeBadge(mode: unknown): SwarmModeBadge {
  if (typeof mode !== "string" || !mode.trim()) {
    return { mode: null, label: "Topology not reported", note: "the Gateway did not report this swarm's topology", tone: "gray" };
  }
  const reported = mode.trim();
  const known = SWARM_MODE_BADGES[reported];
  if (!known) {
    // A topology this frontend does not know is preserved verbatim: an older
    // client ahead of a newer Gateway must never silently re-label a mode.
    return { mode: reported, label: reported, note: "topology reported verbatim; this frontend has no description for it", tone: "amber" };
  }
  return { mode: reported, label: known.label, note: known.note, tone: "blue" };
}

/**
 * The plan-time advisory about the process admission pool, composed by the
 * Gateway coordinator at `create_swarm` into `plan.metrics["admission_advisory"]`.
 *
 * A swarm plan can declare up to 1000-way concurrency, but every swarm worker
 * executes through the process-wide subagent admission controller, so realized
 * parallelism is bounded by the installed `subagent_runtime` schedule. The
 * Gateway composes that seam into a note plus structured numbers; this view
 * carries it beside the topology badge, only on a fulfilled detail read.
 *
 * Every numeric field is `null` when the Gateway did not report it. A plan that
 * predates the disclosure is `reported: false` with an explanatory note — that
 * is "not reported", never "no capacity", and never a computed substitute.
 */
export interface SwarmAdmissionAdvisory {
  /** Whether the detail payload carried the advisory block at all. */
  reported: boolean;
  /** Installed pool size — how many subagents run concurrently. */
  maxRunning: number | null;
  /** Queue depth before admission refusals (0 is a real reported value). */
  maxQueued: number | null;
  /** Gateway-reported policy string, verbatim, or `null` when absent. */
  policy: string | null;
  queueTimeoutSeconds: number | null;
  /** `min(declared concurrency, max_running)` as the Gateway composed it. */
  expectedRealizedConcurrency: number | null;
  /** Pool (+queue under `queue`; pool alone under `reject`). */
  inflightCapacity: number | null;
  /** Tri-state: true = the declared width exceeds the in-flight ceiling; null = not measured. */
  widePlanRisk: boolean | null;
  /** The Gateway-composed note, verbatim. */
  note: string;
}

export function swarmAdmissionAdvisory(detail: unknown): SwarmAdmissionAdvisory {
  const root = objectOr(detail);
  const metrics = objectOr(has(root, "metrics") ? root.metrics : {});
  // The block normally lives under `metrics`; a payload that carries it at the
  // root is honoured only when `metrics` did not carry it.
  const advisory = objectOr(metrics["admission_advisory"] ?? root["admission_advisory"]);
  if (Object.keys(advisory).length === 0) {
    return {
      reported: false,
      maxRunning: null,
      maxQueued: null,
      policy: null,
      queueTimeoutSeconds: null,
      expectedRealizedConcurrency: null,
      inflightCapacity: null,
      widePlanRisk: null,
      note: "Process admission advisory not reported — this plan predates the disclosure, which is not a claim about capacity.",
    };
  }
  const policyValue = advisory["admission_policy"];
  const noteValue = advisory["note"];
  return {
    reported: true,
    maxRunning: measuredCount(advisory["max_running"]),
    maxQueued: measuredCount(advisory["max_queued"]),
    policy: typeof policyValue === "string" && policyValue.trim() ? policyValue : null,
    queueTimeoutSeconds: measuredCount(advisory["queue_timeout_seconds"]),
    expectedRealizedConcurrency: measuredCount(advisory["expected_realized_concurrency"]),
    inflightCapacity: measuredCount(advisory["inflight_capacity"]),
    widePlanRisk: typeof advisory["wide_plan_risk"] === "boolean" ? advisory["wide_plan_risk"] : null,
    note:
      typeof noteValue === "string" && noteValue.trim()
        ? noteValue
        : "The Gateway reported an admission advisory without its note; treat the structured fields above as the only statement it made.",
  };
}

export interface SwarmTelemetryView {
  parallelism: string;
  completion: string;
  riskSignals: string[];
  note: string;
}

/** Render only measurements returned by the Gateway telemetry snapshot. */
export function swarmTelemetryView(payload: unknown): SwarmTelemetryView {
  const root = objectOr(payload);
  const telemetry = objectOr(root.telemetry);
  const parallel = objectOr(telemetry.parallelism_realised);
  const completion = objectOr(telemetry.subtask_completion_ratio);
  const parallelState = typeof parallel.state === "string" ? parallel.state : null;
  const maxConcurrent = measuredCount(parallel.max_concurrent_observed);
  const parallelism = parallelState === "unknown"
    ? "task overlap not measured"
    : maxConcurrent === null && parallelState === null
      ? "task overlap not reported"
    : maxConcurrent === null
      ? "peak concurrent tasks not reported"
      : `${maxConcurrent} task${maxConcurrent === 1 ? "" : "s"} concurrent at peak${parallel.flagged === true ? " · serial collapse flagged" : ""}`;

  const spawned = measuredCount(completion.tasks_spawned);
  const completedWithResults = measuredCount(completion.tasks_with_results);
  const completionState = typeof completion.state === "string" ? completion.state : null;
  const completionText = completionState === "unknown"
    ? "meaningful completion not measured"
    : completionState === null
      ? "meaningful completion not reported"
    : spawned === null || completedWithResults === null
      ? "meaningful completion not reported"
      : `${completedWithResults} of ${spawned} tasks produced a result${completion.flagged === true ? " · low completion flagged" : ""}`;
  const rawSignals = Array.isArray(telemetry.risk_signals) ? telemetry.risk_signals : [];
  const riskSignals = [...new Set(rawSignals.filter((signal): signal is string => typeof signal === "string" && signal.trim().length > 0).map((signal) => signal.trim()))];
  const available = Object.keys(telemetry).length > 0;
  return {
    parallelism,
    completion: completionText,
    riskSignals,
    note: available
      ? "Measured from recorded task state; advisory signals do not verify result correctness."
      : "Swarm telemetry was not reported by the Gateway.",
  };
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
