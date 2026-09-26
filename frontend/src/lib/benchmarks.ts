import { get, send, asList, pick } from "./http";

// Benchmark plane API client — real gateway surface only.
// Router: backend/app/gateway/routers/benchmarks.py (prefix /api/benchmarks).

/** Suite summary from BenchmarkRunner.list_suites(): {name, version, cases}. */
export interface BenchmarkSuite {
  name: string;
  version: string;
  /** Number of registered cases (the API reports a count, not a list). */
  cases: number;
  [key: string]: unknown;
}

/** BenchmarkResult.to_dict() — one recorded case result. */
export interface BenchmarkCaseResult {
  result_id: string;
  /** "suite@version" as stamped by the runner. */
  suite: string;
  case_id: string;
  passed: boolean;
  score: number;
  detail: string;
  duration_sec: number;
  created_at: number;
  [key: string]: unknown;
}

/** run_suite() report: totals plus per-case results. */
export interface BenchmarkRunReport {
  suite: string;
  version: string;
  total: number;
  passed: number;
  failed: number;
  results: BenchmarkCaseResult[];
  [key: string]: unknown;
}

function toSuite(raw: Record<string, unknown>): BenchmarkSuite {
  return {
    ...raw,
    name: String(pick(raw, ["name"], "")),
    version: String(pick(raw, ["version"], "")),
    cases: typeof raw.cases === "number" ? raw.cases : Number(pick(raw, ["cases"], 0)),
  };
}

function toResult(raw: Record<string, unknown>): BenchmarkCaseResult {
  return {
    ...raw,
    result_id: String(pick(raw, ["result_id", "id"], "")),
    suite: String(pick(raw, ["suite"], "")),
    case_id: String(pick(raw, ["case_id"], "")),
    passed: Boolean(raw.passed),
    score: typeof raw.score === "number" ? raw.score : Number(pick(raw, ["score"], 0)),
    detail: String(pick(raw, ["detail"], "")),
    duration_sec: typeof raw.duration_sec === "number" ? raw.duration_sec : 0,
    created_at: typeof raw.created_at === "number" ? raw.created_at : 0,
  };
}

function toRunReport(raw: Record<string, unknown>): BenchmarkRunReport {
  return {
    ...raw,
    suite: String(pick(raw, ["suite"], "")),
    version: String(pick(raw, ["version"], "")),
    total: Number(pick(raw, ["total"], 0)),
    passed: Number(pick(raw, ["passed"], 0)),
    failed: Number(pick(raw, ["failed"], 0)),
    results: asList(raw.results, ["results"]).map(toResult),
  };
}

/** GET /api/benchmarks/suites — registered suites (the demo suite is registered by the route). */
export async function listBenchmarkSuites(): Promise<BenchmarkSuite[]> {
  const d = await get<unknown>("/benchmarks/suites");
  return asList(d, ["suites", "data"]).map(toSuite);
}

/** GET /api/benchmarks/results — recent recorded results (empty list = nothing run yet). */
export async function listBenchmarkResults(limit = 50): Promise<BenchmarkCaseResult[]> {
  const bounded = Math.max(1, Math.min(Math.floor(limit), 200));
  const d = await get<unknown>(`/benchmarks/results?limit=${bounded}`);
  return asList(d, ["results", "data"]).map(toResult);
}

/** POST /api/benchmarks/suites/{name}/run — run a registered suite (404 when unknown). */
export async function runBenchmarkSuite(
  name: string,
  caseIds?: string[]
): Promise<BenchmarkRunReport> {
  const d = await send<Record<string, unknown>>(
    `/benchmarks/suites/${encodeURIComponent(name)}/run`,
    "POST",
    caseIds ? { case_ids: caseIds } : {}
  );
  return toRunReport(d ?? {});
}
