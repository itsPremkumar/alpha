import { get } from "./http";

/**
 * Real-work validation progress: what the agent was actually asked to do, and
 * whether it did it.
 *
 * This is the operator's answer to "is the system working?" and it is fed by
 * `scripts/reliability/alpha_workload_monitor.py`, which submits real tasks to
 * the Gateway's own run routes and then checks for the **side effect**. The
 * Gateway reads that monitor's ledger and serves it at `GET /api/ops/reliability`.
 *
 * The honesty rules this client enforces, each of which has a tempting wrong
 * reading:
 *
 * - **A verdict word is never mapped to a known set.** The server forwards the
 *   monitor's own word verbatim, so a verdict added by a newer monitor reaches
 *   the view instead of being snapped to PASS or FAIL.
 * - **`total` stays `null` when the server read nothing.** The server sends
 *   `null` (not `0`) when it could not read the ledger, because "I looked and
 *   found no workloads" and "I could not look" lead to opposite decisions. A
 *   view that rendered the first as "all clear" would be the exact defect this
 *   surface exists to catch.
 * - **A failed read rejects.** It never resolves to a zeroed matrix that reads
 *   as a healthy one.
 * - **`reason` travels with the absence**, so an unread ledger is diagnosable
 *   from the payload alone.
 */

/** One adjudicated workload attempt. */
export interface ReliabilityWorkload {
  /** Workload key, e.g. `A`. */
  key: string;
  title: string | null;
  /** Workload class (coding, research, multi-agent, ...) or null when absent. */
  kind: string | null;
  /** The monitor's own word, verbatim and unrecognised-friendly. */
  verdict: string;
  /** Why that verdict, in the monitor's words. */
  detail: string | null;
  runId: string | null;
  threadId: string | null;
  /** Measured duration in seconds, or null when nothing was measured. */
  elapsedSeconds: number | null;
  /** The model that served the run, or null when no run produced one. */
  model: string | null;
  /** The Gateway's own failure text, bounded server-side. */
  serverError: string | null;
  /** When the monitor adjudicated it, ISO 8601. */
  checkedAt: string | null;
}

export interface ReliabilityMatrix {
  /** False when the Gateway could not read the ledger at all. */
  reported: boolean;
  /** Machine-readable: `reported`, `ledger_not_found`, `ledger_unreadable`. */
  reason: string;
  /** Where the Gateway looked, so a missing file is diagnosable. */
  ledgerPath: string | null;
  /** Workloads recorded, or null when nothing was read — never 0 for that. */
  total: number | null;
  /** Evidence checks that passed, or null when nothing was read. */
  passed: number | null;
  /** Failed, errored or unverified workloads, or null when nothing was read. */
  broken: number | null;
  /** Count per verdict word, as the server measured them. */
  counts: Record<string, number>;
  /** Rows this response carries. */
  returned: number;
  /** True when the ledger held more rows than the response bound allows. */
  truncated: boolean;
  workloads: ReliabilityWorkload[];
  /** The server's reason in words, for the view to disclose. */
  reasonText: string;
}

const REASON_TEXT: Record<string, string> = {
  reported: "The Gateway read the workload ledger.",
  ledger_not_found:
    "No workload ledger exists yet. Nothing has been measured — this is NOT a passing matrix. Start the monitor to produce one.",
  ledger_unreadable:
    "The workload ledger exists but could not be read, so no workload is accounted for. This is a failed read, not an empty result.",
};

/** Verdict words that mean "this did not work". Unknown words are never assumed to pass. */
export const BROKEN_VERDICTS = ["FAIL", "ERROR", "UNVERIFIED"];

type Rec = Record<string, unknown>;

function rec(value: unknown): Rec {
  return value && typeof value === "object" ? (value as Rec) : {};
}

function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** A finite count, or null. `0` is a real measured zero and is preserved. */
function count(value: unknown): number | null {
  return num(value);
}

function toWorkload(raw: unknown, index: number): ReliabilityWorkload {
  const d = rec(raw);
  return {
    // A row with no key still needs an identity; the index keeps it visible
    // rather than rendering an unnamed row.
    key: str(d.workload) ?? `row ${index + 1}`,
    title: str(d.title),
    kind: str(d.kind),
    // Preserved verbatim: a verdict this build has never heard of is shown as
    // the monitor wrote it rather than coerced into a known bucket.
    verdict: str(d.verdict) ?? "UNKNOWN",
    detail: str(d.detail),
    runId: str(d.run_id),
    threadId: str(d.thread_id),
    elapsedSeconds: num(d.elapsed_s),
    model: str(d.model),
    serverError: str(d.server_error),
    checkedAt: str(d.checked_at),
  };
}

export function toReliability(body: unknown): ReliabilityMatrix {
  const d = rec(body);
  const rows = Array.isArray(d.workloads) ? d.workloads : [];
  const counts: Record<string, number> = {};
  const rawCounts = rec(d.counts);
  for (const [verdict, value] of Object.entries(rawCounts)) {
    const measured = num(value);
    if (measured !== null) counts[verdict] = measured;
  }
  const reason = str(d.reason) ?? "unknown";
  return {
    reported: d.reported === true,
    reason,
    ledgerPath: str(d.ledger_path),
    total: count(d.total),
    passed: count(d.passed),
    broken: count(d.broken),
    counts,
    returned: num(d.returned) ?? rows.length,
    truncated: d.truncated === true,
    workloads: rows.map(toWorkload),
    reasonText: REASON_TEXT[reason] ?? `The Gateway reported an unfamiliar reason "${reason}".`,
  };
}

/** Read the matrix. Rejects on failure; it never resolves to an empty matrix. */
export async function fetchReliability(): Promise<ReliabilityMatrix> {
  return toReliability(await get<unknown>("/ops/reliability"));
}

/** How one verdict should read in the view. */
export interface VerdictTone {
  tone: "green" | "red" | "amber" | "gray";
  label: string;
  /** True when the operator should treat this as needing attention. */
  broken: boolean;
}

/**
 * Map a verdict word to a tone.
 *
 * An unrecognised word is deliberately **amber and treated as broken**, not
 * green: this build cannot know what an unknown verdict means, and assuming it
 * is fine is the failure this whole surface exists to prevent. `SKIP` is the one
 * known word that is neither — it means the workload was not attempted.
 */
export function verdictTone(verdict: string): VerdictTone {
  switch (verdict) {
    case "PASS":
      return { tone: "green", label: "PASS", broken: false };
    case "FAIL":
      return { tone: "red", label: "FAIL", broken: true };
    case "ERROR":
      return { tone: "red", label: "ERROR", broken: true };
    case "UNVERIFIED":
      return { tone: "amber", label: "UNVERIFIED", broken: true };
    case "SKIP":
      return { tone: "gray", label: "SKIP", broken: false };
    default:
      return { tone: "amber", label: verdict || "UNKNOWN", broken: true };
  }
}

/** The headline sentence, which must never read as healthy on an unread matrix. */
export function matrixHeadline(matrix: ReliabilityMatrix): { value: string; tone: VerdictTone["tone"]; detail: string } {
  if (!matrix.reported) {
    return { value: "not reported", tone: "gray", detail: matrix.reasonText };
  }
  const total = matrix.total ?? 0;
  const passed = matrix.passed ?? 0;
  const broken = matrix.broken ?? 0;
  if (total === 0) {
    return {
      value: "no workloads yet",
      tone: "gray",
      detail: "The ledger is readable but holds no workload yet. Nothing has been validated, so nothing can be reported as passing.",
    };
  }
  return {
    value: `${passed}/${total} passed`,
    tone: broken > 0 ? (passed === 0 ? "red" : "amber") : "green",
    detail:
      broken === 0
        ? `Every ${total} recorded workload's evidence check passed.`
        : `${broken} of ${total} recorded workloads did not pass. The verdict is the evidence check's, not the run's status code.`,
  };
}
