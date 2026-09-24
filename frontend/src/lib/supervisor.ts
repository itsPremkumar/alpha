// lib/supervisor.ts — real-API client for the autonomy plane.
//
// Routers (backend/app/gateway/routers/autonomy.py, prefix /api/autonomy):
//   GET  /autonomy/status           → live AutonomySupervisor.status() + request-time AutonomyConfig flags
//   GET  /autonomy/sentinel/reports → capped read of the durable Sentinel report journal
//   POST /autonomy/sentinel/run     → one real Sentinel pass (observe-only unless auto_heal is explicit)
//   GET  /autonomy/sentinel/signals → observe-only signal collection (nothing diagnosed or fixed)
//
// Honesty contract (same canon as the scheduler job-memory journal):
//   * The supervisor sends 0.0 for a loop that has never run and "" for an
//     absent error/summary; those become null here so the UI can say
//     "never run" instead of rendering epoch 0 or an empty green row.
//   * An unavailable config block (`available: false`) keeps every config
//     field null and surfaces the server's real error string — it never
//     falls back to "enabled".
//   * A corrupt/unreadable report journal answers 500 with the file and line;
//     that rejects here and is rendered verbatim — never an empty history.
//   * `auto_heal` is ALWAYS sent explicitly; omission is never a way to
//     request a repair pass.
import { get, send, pick } from "./http";

/** One registered loop's live counters (AutonomySupervisor.status()). */
export interface LoopStatus {
  loop_id: string;
  description: string;
  enabled: boolean;
  runs: number;
  failures: number;
  parked: boolean;
  park_reason: string | null;
  running: boolean;
  /** null when the supervisor has 0.0 (the loop has never run). */
  last_run_at: number | null;
  /** null when never measured. */
  last_duration_seconds: number | null;
  /** null when the supervisor reported no error ("" means none). */
  last_error: string | null;
  /** null when the supervisor has no summary yet ("" means none). */
  last_summary: string | null;
  task_alive: boolean;
  /** Request-time config flags; null when the config block is unavailable or absent. */
  config_enabled: boolean | null;
  interval_seconds: number | null;
  jitter_seconds: number | null;
}

/** Request-time AutonomyConfig view (autonomy.py::_config_block). */
export interface ConfigBlock {
  available: boolean;
  /** Real error string when available is false; null otherwise. */
  error: string | null;
  autonomy_enabled: boolean | null;
  bus_enabled: boolean | null;
  note: string | null;
}

export interface SupervisorStatus {
  /** The supervisor's own master switch at process start. */
  supervisor_enabled: boolean;
  /** null when the supervisor has not started (0.0). */
  started_at: number | null;
  loops: LoopStatus[];
  config: ConfigBlock;
  notes: string[];
}

function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
}

function strOrNull(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function toLoop(loopId: string, raw: Record<string, unknown>, cfg: Record<string, unknown> | null): LoopStatus {
  const runs = Number(pick(raw, ["runs"], 0));
  const failures = Number(pick(raw, ["failures"], 0));
  return {
    loop_id: loopId,
    description: String(pick(raw, ["description"], "")),
    enabled: raw.enabled === true,
    runs: Number.isFinite(runs) ? runs : 0,
    failures: Number.isFinite(failures) ? failures : 0,
    parked: raw.parked === true,
    park_reason: strOrNull(raw.park_reason),
    running: raw.running === true,
    last_run_at: numOrNull(raw.last_run_at),
    last_duration_seconds: numOrNull(raw.last_duration_seconds),
    last_error: strOrNull(raw.last_error),
    last_summary: strOrNull(raw.last_summary),
    task_alive: raw.task_alive === true,
    config_enabled: cfg ? cfg.enabled === true : null,
    interval_seconds: cfg && typeof cfg.interval_seconds === "number" ? cfg.interval_seconds : null,
    jitter_seconds: cfg && typeof cfg.jitter_seconds === "number" ? cfg.jitter_seconds : null,
  };
}

/** GET /autonomy/status → live supervisor counters + config flags + notes. */
export async function getSupervisorStatus(): Promise<SupervisorStatus> {
  const d = await get<Record<string, unknown>>("/autonomy/status");
  const supervisor = (d.supervisor && typeof d.supervisor === "object" ? d.supervisor : {}) as Record<string, unknown>;
  const configRaw = (d.config && typeof d.config === "object" ? d.config : {}) as Record<string, unknown>;
  const rawLoops =
    supervisor.loops && typeof supervisor.loops === "object" ? (supervisor.loops as Record<string, unknown>) : {};
  const cfgLoops =
    configRaw.loops && typeof configRaw.loops === "object" ? (configRaw.loops as Record<string, unknown>) : {};
  const available = configRaw.available === true;
  const loops = Object.entries(rawLoops).map(([loopId, value]) => {
    const raw = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
    const cfgValue = cfgLoops[loopId];
    const cfg =
      cfgValue && typeof cfgValue === "object" ? (cfgValue as Record<string, unknown>) : null;
    return toLoop(loopId, raw, available ? cfg : null);
  });
  return {
    supervisor_enabled: supervisor.enabled === true,
    started_at: numOrNull(supervisor.started_at),
    loops,
    config: {
      available,
      error: available ? null : strOrNull(configRaw.error),
      autonomy_enabled: available ? configRaw.autonomy_enabled === true : null,
      bus_enabled: available ? configRaw.bus_enabled === true : null,
      note: strOrNull(configRaw.note),
    },
    notes: Array.isArray(d.notes) ? d.notes.map((n) => String(n)) : [],
  };
}

/** One Sentinel pass result (alpha.runtime.sentinel.runner.RunReport.to_dict()). */
export interface SentinelRunReport {
  duration_s: number;
  scanned: number;
  summary: string;
  fixed: number;
  reverted: number;
  escalated: number;
  errors: string[];
  /** LoopOutcome.to_dict() payloads, preserved verbatim as observed. */
  outcomes: Array<Record<string, unknown>>;
}

function toRunReport(d: Record<string, unknown>): SentinelRunReport {
  const duration = Number(pick(d, ["duration_s"], 0));
  const scanned = Number(pick(d, ["scanned"], 0));
  const fixed = Number(pick(d, ["fixed"], 0));
  const reverted = Number(pick(d, ["reverted"], 0));
  const escalated = Number(pick(d, ["escalated"], 0));
  return {
    duration_s: Number.isFinite(duration) ? duration : 0,
    scanned: Number.isFinite(scanned) ? scanned : 0,
    summary: String(pick(d, ["summary"], "")),
    fixed: Number.isFinite(fixed) ? fixed : 0,
    reverted: Number.isFinite(reverted) ? reverted : 0,
    escalated: Number.isFinite(escalated) ? escalated : 0,
    errors: Array.isArray(d.errors) ? d.errors.map((e) => String(e)) : [],
    outcomes: Array.isArray(d.outcomes) ? (d.outcomes as Array<Record<string, unknown>>) : [],
  };
}

/** One journal entry (recorded_at + trigger + auto_heal + report). */
export interface SentinelReportEntry {
  recorded_at: string;
  trigger: string | null;
  auto_heal: boolean | null;
  report: SentinelRunReport;
}

export interface SentinelReports {
  reports: SentinelReportEntry[];
  order: string | null;
  total: number;
  cap: number | null;
  source: string;
  disclosures: string[];
}

/** GET /autonomy/sentinel/reports?limit=N — clamped to the router's 1..200 window. */
export async function getSentinelReports(limit = 50): Promise<SentinelReports> {
  const bounded = Math.max(1, Math.min(Math.floor(limit), 200));
  const d = await get<Record<string, unknown>>(`/autonomy/sentinel/reports?limit=${bounded}`);
  const raw = Array.isArray(d.reports) ? (d.reports as Array<Record<string, unknown>>) : [];
  const total = Number(pick(d, ["total"], raw.length));
  return {
    reports: raw.map((entry) => {
      const report = (entry.report && typeof entry.report === "object" ? entry.report : {}) as Record<string, unknown>;
      return {
        recorded_at: String(pick(entry, ["recorded_at"], "")),
        trigger: strOrNull(entry.trigger),
        auto_heal: typeof entry.auto_heal === "boolean" ? entry.auto_heal : null,
        report: toRunReport(report),
      };
    }),
    order: strOrNull(d.order),
    total: Number.isFinite(total) ? total : raw.length,
    cap: typeof d.cap === "number" ? d.cap : null,
    source: String(pick(d, ["source"], "")),
    disclosures: Array.isArray(d.disclosures) ? d.disclosures.map((x) => String(x)) : [],
  };
}

/**
 * POST /autonomy/sentinel/run — run one real pass on the gateway.
 * `auto_heal` is always sent: false = observe-only (default), true = a
 * verified repair pass (checkpoint → verify → revert-on-red, bounded fixes).
 */
export async function runSentinelPass(autoHeal = false): Promise<SentinelRunReport> {
  const d = await send<Record<string, unknown>>("/autonomy/sentinel/run", "POST", { auto_heal: autoHeal });
  return toRunReport(d ?? {});
}

/** One observed signal (alpha.runtime.sentinel.signals.Signal.to_dict()). */
export interface SentinelSignal {
  source: string;
  kind: string;
  severity: string;
  message: string;
  fingerprint: string;
  detected_at: string;
  context: Record<string, unknown>;
}

export interface SentinelSignals {
  observe_only: boolean;
  fixes_applied: boolean;
  count: number;
  note: string;
  signals: SentinelSignal[];
}

/** GET /autonomy/sentinel/signals — collect-only; nothing is diagnosed or fixed. */
export async function getSentinelSignals(): Promise<SentinelSignals> {
  const d = await get<Record<string, unknown>>("/autonomy/sentinel/signals");
  const raw = Array.isArray(d.signals) ? (d.signals as Array<Record<string, unknown>>) : [];
  const count = Number(pick(d, ["count"], raw.length));
  return {
    observe_only: d.observe_only === true,
    fixes_applied: d.fixes_applied === true,
    count: Number.isFinite(count) ? count : raw.length,
    note: String(pick(d, ["note"], "")),
    signals: raw.map((s) => {
      const context = (s.context && typeof s.context === "object" ? s.context : {}) as Record<string, unknown>;
      return {
        source: String(pick(s, ["source"], "")),
        kind: String(pick(s, ["kind"], "")),
        severity: String(pick(s, ["severity"], "unknown")),
        message: String(pick(s, ["message"], "")),
        fingerprint: String(pick(s, ["fingerprint"], "")),
        detected_at: String(pick(s, ["detected_at"], "")),
        context,
      };
    }),
  };
}
