import { get } from "./http";

type Rec = Record<string, unknown>;

/**
 * The intelligence control plane client — `GET /intelligence/control-plane`.
 *
 * One composed read of every intelligence source (mode, loop health, evidence
 * ledger, journal, capability fabric, replay reservoir, goals) plus the metric
 * list and summary the Gateway assembles in
 * `alpha.intelligence.control_plane`. It is a client of a **read-only** route:
 * the module imports `get` only, and `intelligence.test.mjs` pins that.
 *
 * Three honesty rules decide every mapper here, mirroring the backend's own
 * contract (the payload carries `schema_version: alpha.control-plane.v1`):
 *
 * 1. **Absent is `null`, never `0`.** A count the Gateway did not send is
 *    `null`; a section that could not be read is `available: false` with the
 *    server's reason. A mapper that reached for `?? 0` would turn "could not
 *    read the store" into "there are zero goals".
 * 2. **The basis decides the displayed value, never the raw number.** A metric
 *    with `basis: "unowned"` and a smuggled `value: 0` renders as *no aggregate
 *    owner* — never `0` — so `metricValueText` consults `basis` first and the
 *    number only when the basis is `measured`.
 * 3. **Unknown enum strings are preserved verbatim.** A `regime` or `basis`
 *    from a newer Gateway renders as itself with a neutral tone rather than
 *    being snapped to a value this build knows.
 *
 * Coverage: `src/lib/intelligence.test.mjs`.
 */

/** The payload contract this build renders (backend `SCHEMA_VERSION`). */
export const CONTROL_PLANE_SCHEMA = "alpha.control-plane.v1";

/** The four honest answers a metric can have (backend `VALID_BASIS`). */
export const METRIC_BASES = ["measured", "unmeasured", "unavailable", "unowned"] as const;

/** The seven source sections, in payload order (backend `SOURCE_SECTIONS`). */
export const SOURCE_SECTIONS = ["mode", "loop_health", "ledger", "journal", "capability_fabric", "replay", "goals"] as const;

export type SourceSectionId = (typeof SOURCE_SECTIONS)[number];

/** Human labels for each source card — one name per section id. */
export const SECTION_LABELS: Record<SourceSectionId, string> = {
  mode: "Learning mode",
  loop_health: "Loop health",
  ledger: "Evidence ledger",
  journal: "Learning journal",
  capability_fabric: "Capability fabric",
  replay: "Replay reservoir",
  goals: "Autonomous goals",
};

/** One source's availability envelope, verbatim from the Gateway. */
export interface SourceEnvelope<T> {
  available: boolean;
  reason: string;
  data: T | null;
}

/** One metric row: a figure with the basis it was (or was not) measured on. */
export interface ControlPlaneMetric {
  name: string;
  /** Present only when `basis === "measured"`; the view refuses it otherwise. */
  value: number | boolean | string | null;
  unit: string;
  /** Typed as `string` so a basis from a newer Gateway is preserved verbatim. */
  basis: string;
  reason: string;
  source: string;
}

/** Counts per basis plus the one health state — every count nullable. */
export interface ControlPlaneSummary {
  measured: number | null;
  unmeasured: number | null;
  unavailable: number | null;
  unowned: number | null;
  total: number | null;
  /** Section ids that could not be read; `null` means not reported. */
  sources_unavailable: string[] | null;
  /** The loop-health regime mirrored verbatim; `null` means not reported. */
  health_state: string | null;
}

export interface ModeData {
  enabled: boolean | null;
  mode: string | null;
  permits: Rec | null;
}

/**
 * The loop-health report.
 *
 * `scored_attempts` is `null` (not `0`) in the config-failure fallback, which
 * deliberately omits the key — "measured zero attempts" and "no assessment ran"
 * are different facts and the view says different words for each.
 */
export interface LoopHealthReport {
  regime: string | null;
  scored_attempts: number | null;
  /** Present only in the config-failure fallback, where no assessment ran. */
  reason: string | null;
  bottleneck: string | null;
  recommended_action: string | null;
  reasons: string[] | null;
  gain_per_attempt_trend: number | null;
  subsystems_agreeing: number | null;
  subsystems_total: number | null;
}

export interface LoopHealthData {
  report: LoopHealthReport | null;
  required_subsystems: string[] | null;
  observed_events: number | null;
}

export interface LedgerData {
  candidates: number | null;
  records: number | null;
  enabled_subsystems: string[] | null;
}

export interface JournalData {
  integrity: { ok: boolean | null; checked: number | null; broken_at: number | null; reason: string | null } | null;
  corrupt_lines: number | null;
  entry_count: number | null;
}

export interface FabricData {
  active: number | null;
  status_counts: Rec | null;
  note: string | null;
}

export interface ReplayData {
  size: number | null;
  capacity: number | null;
  utilisation: number | null;
  oldest_age_seconds: number | null;
  strata: Rec | null;
}

export interface GoalsData {
  tracked: number | null;
  note: string | null;
}

export interface ControlPlane {
  schema_version: string | null;
  mode: SourceEnvelope<ModeData>;
  loop_health: SourceEnvelope<LoopHealthData>;
  ledger: SourceEnvelope<LedgerData>;
  journal: SourceEnvelope<JournalData>;
  capability_fabric: SourceEnvelope<FabricData>;
  replay: SourceEnvelope<ReplayData>;
  goals: SourceEnvelope<GoalsData>;
  /** `null` when the Gateway sent no list at all — distinct from `[]`. */
  metrics: ControlPlaneMetric[] | null;
  summary: ControlPlaneSummary;
}

// ---------------------------------------------------------------------------
// Mapping — verbatim where the server spoke, null where it did not
// ---------------------------------------------------------------------------

function rec(v: unknown): Rec {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {};
}

function optNum(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function optStr(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function optBool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

function optStrList(v: unknown): string[] | null {
  return Array.isArray(v) ? v.filter((entry): entry is string => typeof entry === "string") : null;
}

function optObj(v: unknown): Rec | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : null;
}

/**
 * One section envelope. A section the Gateway omitted entirely is disclosed as
 * `available: false` with its own reason — never silently `available: true`,
 * and never an exception that blanks the other six.
 */
function section<T>(raw: unknown, read: (data: Rec | null) => T): SourceEnvelope<T> {
  if (raw === undefined || raw === null) {
    return { available: false, reason: "the Gateway did not include this section in the payload", data: null };
  }
  const envelope = rec(raw);
  const available = envelope.available === true;
  return {
    available,
    reason: optStr(envelope.reason) ?? "",
    data: available ? read(optObj(envelope.data)) : null,
  };
}

function readMode(data: Rec | null): ModeData {
  const d = rec(data);
  return {
    enabled: optBool(d.enabled),
    mode: optStr(d.mode),
    permits: optObj(d.permits),
  };
}

function readLoopHealth(data: Rec | null): LoopHealthData {
  const d = rec(data);
  const report = optObj(d.report);
  let parsed: LoopHealthReport | null = null;
  if (report) {
    parsed = {
      regime: optStr(report.regime),
      scored_attempts: optNum(report.scored_attempts),
      reason: optStr(report.reason),
      bottleneck: optStr(report.bottleneck),
      recommended_action: optStr(report.recommended_action),
      reasons: optStrList(report.reasons),
      gain_per_attempt_trend: optNum(report.gain_per_attempt_trend),
      subsystems_agreeing: optNum(report.subsystems_agreeing),
      subsystems_total: optNum(report.subsystems_total),
    };
  }
  return {
    report: parsed,
    required_subsystems: optStrList(d.required_subsystems),
    observed_events: optNum(d.observed_events),
  };
}

function readLedger(data: Rec | null): LedgerData {
  const d = rec(data);
  return {
    candidates: optNum(d.candidates),
    records: optNum(d.records),
    enabled_subsystems: optStrList(d.enabled_subsystems),
  };
}

function readJournal(data: Rec | null): JournalData {
  const d = rec(data);
  const integrity = optObj(d.integrity);
  const entries = Array.isArray(d.entries) ? d.entries : null;
  return {
    integrity: integrity
      ? {
          ok: optBool(integrity.ok),
          checked: optNum(integrity.checked),
          broken_at: optNum(integrity.broken_at),
          reason: optStr(integrity.reason),
        }
      : null,
    corrupt_lines: optNum(d.corrupt_lines),
    entry_count: entries ? entries.length : null,
  };
}

function readFabric(data: Rec | null): FabricData {
  const d = rec(data);
  return {
    active: optNum(d.active),
    status_counts: optObj(d.status_counts),
    note: optStr(d.note),
  };
}

function readReplay(data: Rec | null): ReplayData {
  const d = rec(data);
  return {
    size: optNum(d.size),
    capacity: optNum(d.capacity),
    utilisation: optNum(d.utilisation),
    oldest_age_seconds: optNum(d.oldest_age_seconds),
    strata: optObj(d.strata),
  };
}

function readGoals(data: Rec | null): GoalsData {
  const d = rec(data);
  return {
    tracked: optNum(d.tracked),
    note: optStr(d.note),
  };
}

function toMetric(raw: unknown): ControlPlaneMetric | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const m = raw as Rec;
  const rawValue = m.value;
  return {
    name: optStr(m.name) ?? "",
    // Preserve the server's value verbatim; the *view* decides whether the
    // basis permits rendering it. Coercing here would hide a payload defect.
    value:
      (typeof rawValue === "number" && Number.isFinite(rawValue)) || typeof rawValue === "boolean" || typeof rawValue === "string" ? rawValue : null,
    unit: optStr(m.unit) ?? "",
    basis: optStr(m.basis) ?? "",
    reason: optStr(m.reason) ?? "",
    source: optStr(m.source) ?? "",
  };
}

function toSummary(raw: unknown): ControlPlaneSummary {
  const empty: ControlPlaneSummary = {
    measured: null,
    unmeasured: null,
    unavailable: null,
    unowned: null,
    total: null,
    sources_unavailable: null,
    health_state: null,
  };
  if (raw === undefined || raw === null || typeof raw !== "object" || Array.isArray(raw)) return empty;
  const s = raw as Rec;
  return {
    measured: optNum(s.measured),
    unmeasured: optNum(s.unmeasured),
    unavailable: optNum(s.unavailable),
    unowned: optNum(s.unowned),
    total: optNum(s.total),
    sources_unavailable: optStrList(s.sources_unavailable),
    health_state: optStr(s.health_state),
  };
}

/** Map a raw Gateway payload onto the typed plane. Pure; exported for tests. */
export function toControlPlane(raw: unknown): ControlPlane {
  const d = rec(raw);
  return {
    schema_version: optStr(d.schema_version),
    mode: section(d.mode, readMode),
    loop_health: section(d.loop_health, readLoopHealth),
    ledger: section(d.ledger, readLedger),
    journal: section(d.journal, readJournal),
    capability_fabric: section(d.capability_fabric, readFabric),
    replay: section(d.replay, readReplay),
    goals: section(d.goals, readGoals),
    metrics: Array.isArray(d.metrics) ? d.metrics.map(toMetric).filter((metric): metric is ControlPlaneMetric => metric !== null) : null,
    summary: toSummary(d.summary),
  };
}

/** Read the composed control plane. Rejects with the server's reason. */
export async function fetchControlPlane(): Promise<ControlPlane> {
  const raw = await get<unknown>("/intelligence/control-plane");
  return toControlPlane(raw);
}

// ---------------------------------------------------------------------------
// View derivations — the sentences this surface is allowed to say
// ---------------------------------------------------------------------------

/**
 * Words for a metric whose basis does not permit a number. Keyed by the four
 * bases this build knows; an unknown basis falls through to `value not shown`
 * rather than rendering a number whose meaning no build has declared.
 */
const BASIS_VALUE_WORDS: Record<string, string> = {
  "": "basis not reported",
  unmeasured: "not measured",
  unavailable: "source unavailable",
  unowned: "no aggregate owner",
};

/**
 * The value cell for one metric. **The basis decides, not the number**: only
 * `measured` may render a value, a measured `0` still renders `0`, and a
 * measured metric whose value did not arrive says *not reported* rather than
 * claiming a zero. Every other basis renders its words and ignores `value`
 * entirely — that is what stops a smuggled `0` on an `unowned` row from
 * reading as a measured figure.
 */
export function metricValueText(metric: ControlPlaneMetric): string {
  if (metric.basis === "measured") {
    const value = metric.value;
    if (value === null || value === undefined) return "not reported";
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
  }
  return BASIS_VALUE_WORDS[metric.basis] ?? "value not shown";
}

/** The basis badge label: verbatim from the server, named when absent. */
export function metricBasisLabel(basis: string): string {
  return basis === "" ? "basis not reported" : basis;
}

/**
 * Badge tone for a basis. These are **epistemic** colours, not health ones:
 * red means the source could not be read, amber a figure nobody owns, blue a
 * figure with evidence, and grey everything that carries no verdict — including
 * a basis this build does not recognise.
 */
export function metricBasisTone(basis: string): "blue" | "amber" | "red" | "gray" {
  switch (basis) {
    case "measured":
      return "blue";
    case "unavailable":
      return "red";
    case "unowned":
      return "amber";
    default:
      return "gray";
  }
}

/**
 * Badge tone for the loop-health regime. `insufficient_data`, an unreported
 * state and a regime from a newer Gateway are all grey — never green, because
 * none of them says the loop is working.
 */
export function healthStateTone(state: string | null): "green" | "amber" | "red" | "gray" {
  switch (state) {
    case "improving":
    case "stable":
      return "green";
    case "saturating":
      return "amber";
    case "regressing":
      return "red";
    default:
      return "gray";
  }
}

/** The health badge text: the regime verbatim, or an explicit absence. */
export function healthStateText(state: string | null): string {
  return state === null ? "health state not reported" : state;
}

/**
 * The summary line. Each count keeps its own words: a count the Gateway did
 * not send reads *count not reported*, while a count it really measured as
 * zero reads `0 <basis>` — the two lead to opposite conclusions and must not
 * share a rendering.
 */
export function summaryCountsLine(summary: ControlPlaneSummary): string {
  const part = (label: string, count: number | null): string => (count === null ? `${label} count not reported` : `${count} ${label}`);
  return [
    part("measured", summary.measured),
    part("unmeasured", summary.unmeasured),
    part("unavailable", summary.unavailable),
    part("unowned", summary.unowned),
    summary.total === null ? "total not reported" : `${summary.total} total`,
  ].join(" · ");
}

/**
 * The unavailable-sources notice, or `null` when there is nothing to announce.
 * A `null` list (not reported) says nothing here — each section card carries
 * its own availability, so no disclosure is lost.
 */
export function unavailableSourcesLine(names: string[] | null): string | null {
  if (names === null || names.length === 0) return null;
  return `${names.length} source${names.length === 1 ? "" : "s"} could not be read: ${names.join(", ")}. Each card below carries its own reason.`;
}

/**
 * Why a section card cannot show figures — `null` when it can show them.
 * An unavailable source without a reason says exactly that, and an available
 * source that arrived with no data says so too rather than rendering blanks.
 */
export function sectionDisclosure<T>(envelope: SourceEnvelope<T>): string | null {
  if (!envelope.available) return envelope.reason || "the Gateway reported this source unavailable without stating a reason";
  if (envelope.data === null) return "the Gateway reported this source available but sent no data";
  return null;
}
