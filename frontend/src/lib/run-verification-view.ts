/**
 * What a dynamic-workflow run must say about its own verification.
 *
 * ## The defect
 *
 * Measured against the live Gateway on 2026-10-06 with
 * `backend/scripts/probe_dynamic_run_report.py`, a completed run returned:
 *
 *     metadata.acceptance_passed   false
 *     metadata.acceptance_reason   "digest projection completed graph mechanics
 *                                   only; no domain task was executed"
 *     metadata.execution_label     "local_digest_projection"
 *     metadata.verification        {"registered_verifiers": 0, "executor_bound": false,
 *                                   "declared_nodes": ["task_05_dynamic_implementation"]}
 *
 * and journalled one `node_verification` event:
 *
 *     {"node_id": "task_05_dynamic_implementation", "passed": false, "status": "not_run",
 *      "command": "pytest -q",
 *      "reason": "the command was not run: no verification executor is bound in this host"}
 *
 * The backend was honest at every layer. The panel was not reading any of it:
 * `node_verification`, `acceptance_reason`, `registered_verifiers`,
 * `executor_bound` and `declared_nodes` each had **zero occurrences** in the whole
 * frontend. The user-facing texture was the §13 sentence
 * "Graph mechanics completed only", which is true and says nothing about the
 * three things an operator needs:
 *
 *   1. which nodes DECLARED a verification and therefore have an unproven claim,
 *   2. that the check named `pytest -q` was **not run**, and why,
 *   3. that the run's acceptance posture is a deliberate limitation of the
 *      digest projection rather than a judgement that the work was inadequate.
 *
 * (2) is the one that matters. A declared-but-unrun verification is the exact
 * shape this repo has already been burned by: the check exists in the plan, so a
 * reader assumes it passed. `passed: false` for a check that never executed is
 * not a failure and must not be dressed as one either — it is `not_run`, and the
 * server says so in its own words.
 *
 * ## The rules, each with a tempting wrong reading
 *
 * | Payload | This renders | Never |
 * | --- | --- | --- |
 * | `status: "not_run"` | `not run — <server's reason>` | `failed` |
 * | `passed: false` + `status: "not_run"` | the `not_run` sentence | `✗ verification failed` |
 * | `passed: false` + `status: "failed"` | `failed — <reason>` | `not run` |
 * | `passed: true` | `passed` | inferred from `status` alone |
 * | no `node_verification` events | `no node declared a verification` | `all checks passed` |
 * | `executor_bound: false` | the server's posture sentence verbatim | `0 verifications passed` |
 * | `registered_verifiers: 0` | `0 verifiers registered` | `verification unavailable` |
 *
 * Lives in its own module, like `dynamic-plan-view.ts`, `swarm-structure-view.ts`
 * and `teamops-progress.ts`, so the rules are testable without loading the
 * 2400-line section.
 */

export type Tone = "green" | "amber" | "red" | "gray";

/** A finite non-negative count, or `null` for "not reported". `0` is preserved. */
export function measuredCount(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  return value;
}

function objectOr(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stringOr(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/** One declared verification and what actually happened to it. */
export interface NodeVerificationView {
  nodeId: string | null;
  /** The literal status the server sent; `null` if it sent none. */
  status: string | null;
  /** Tri-state: `null` means the server did not report an outcome. */
  passed: boolean | null;
  command: string | null;
  reason: string | null;
  /**
   * `not_run` is NOT a failure. The distinction is the whole reason this module
   * exists, so it is a first-class field rather than something a caller infers.
   */
  ran: boolean;
  tone: Tone;
  sentence: string;
}

export function nodeVerificationViews(events: unknown): NodeVerificationView[] {
  if (!Array.isArray(events)) return [];
  const out: NodeVerificationView[] = [];
  for (const raw of events) {
    const e = objectOr(raw);
    if (e.event_type !== "node_verification") continue;
    const payload = objectOr(e.payload ?? e.details ?? {});

    const nodeId = stringOr(payload.node_id) ?? stringOr(e.task_id);
    const status = stringOr(payload.status);
    const passed = typeof payload.passed === "boolean" ? payload.passed : null;
    const command = stringOr(payload.command);
    const reason = stringOr(payload.reason);

    // `ran` is decided by the server's own status word, never inferred from
    // `passed`: a `not_run` check reports `passed: false` because nothing
    // passed, which is not the same as a check that ran and failed.
    const ran = status !== null && status !== "not_run";

    let tone: Tone;
    let sentence: string;
    if (status === null) {
      tone = "gray";
      sentence = `verification outcome not reported for ${nodeId ?? "a node"}`;
    } else if (!ran) {
      tone = "amber";
      // The server's reason is the useful half and is quoted, not paraphrased.
      sentence = reason
        ? `not run — ${reason}`
        : "not run — the server reported no reason, so the check's status is unknown";
    } else if (passed === true) {
      tone = "green";
      sentence = "passed";
    } else if (passed === false) {
      tone = "red";
      sentence = reason ? `failed — ${reason}` : "failed";
    } else {
      tone = "gray";
      sentence = `ran (${status}) but the server reported no pass/fail outcome`;
    }

    out.push({ nodeId, status, passed, command, reason, ran, tone, sentence });
  }
  return out;
}

/** The run's acceptance posture, from `metadata`. */
export interface RunAcceptanceView {
  /** Tri-state. `null` means the server did not report it. */
  acceptancePassed: boolean | null;
  /** The server's own reason, verbatim. */
  reason: string | null;
  executionLabel: string | null;
  /** What to print. */
  sentence: string;
  tone: Tone;
}

export function runAcceptanceView(metadata: unknown): RunAcceptanceView {
  const md = objectOr(metadata);
  const acceptancePassed = typeof md.acceptance_passed === "boolean" ? md.acceptance_passed : null;
  const reason = stringOr(md.acceptance_reason);
  const executionLabel = stringOr(md.execution_label);

  let sentence: string;
  let tone: Tone;
  if (acceptancePassed === null) {
    // The opposite error from the original defect: claiming a limitation the
    // server never stated. An absent posture is unknown.
    sentence = "acceptance posture not reported by the server";
    tone = "gray";
  } else if (acceptancePassed) {
    sentence = reason ?? "domain acceptance reported by the server";
    tone = "green";
  } else {
    sentence =
      reason ??
      "graph mechanics completed only; the server reported no reason, so the acceptance posture is unexplained";
    tone = "amber";
  }

  return { acceptancePassed, reason, executionLabel, sentence, tone };
}

/** The verification posture block, from `metadata.verification`. */
export interface VerificationPostureView {
  registeredVerifiers: number | null;
  executorBound: boolean | null;
  declaredNodes: string[];
  /** What to print about the posture itself. */
  sentence: string;
}

export function verificationPostureView(metadata: unknown): VerificationPostureView {
  const md = objectOr(metadata);
  const hasBlock = Object.keys(objectOr(md.verification)).length > 0;
  const v = objectOr(md.verification);

  const registeredVerifiers = measuredCount(v.registered_verifiers);
  const executorBound = typeof v.executor_bound === "boolean" ? v.executor_bound : null;
  const declaredNodes = Array.isArray(v.declared_nodes)
    ? v.declared_nodes.filter((n): n is string => typeof n === "string" && n.length > 0)
    : [];

  let sentence: string;
  if (!hasBlock) {
    sentence = "verification posture not reported by the server";
  } else if (executorBound === null) {
    sentence = "verification executor state not reported by the server";
  } else if (!executorBound && registeredVerifiers === 0) {
    // Measured live. This is a limitation of the host, not of the work.
    sentence = "no verification executor is bound and no verifier is registered on this host";
  } else if (!executorBound) {
    sentence = `${registeredVerifiers} verifier(s) registered, but no verification executor is bound`;
  } else {
    sentence = `${registeredVerifiers ?? "an unreported number of"} verifier(s) registered and a verification executor is bound`;
  }

  return { registeredVerifiers, executorBound, declaredNodes, sentence };
}

/** The whole verification reading for one run, so the section renders without logic. */
export interface RunVerificationView {
  acceptance: RunAcceptanceView;
  posture: VerificationPostureView;
  /** Declared verifications found in the event log, oldest first. */
  nodes: NodeVerificationView[];
  /**
   * Set when no `node_verification` event exists. This is a REAL and COMMON state
   * — the decomposition only writes `verification_cmd` where it can resolve a
   * name — so it must read as "nothing was declared", never as "all passed".
   */
  declaredNote: string | null;
  /** True when at least one check was declared and did NOT run. */
  hasUnrun: boolean;
}

export function runVerificationView(metadata: unknown, events: unknown): RunVerificationView {
  const nodes = nodeVerificationViews(events);
  const hasUnrun = nodes.some((n) => !n.ran && n.status !== null);
  return {
    acceptance: runAcceptanceView(metadata),
    posture: verificationPostureView(metadata),
    nodes,
    declaredNote:
      nodes.length === 0
        ? "no node declared a verification for this run, so nothing was checked"
        : null,
    hasUnrun,
  };
}