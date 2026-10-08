/**
 * Pure presentation derivations for the Enterprise War Room section.
 *
 * Nothing here imports anything, for the same reason
 * `group-coordination-model.ts` does not: the claims below decide what an
 * operator is told about which number is real, so `node --test` has to be able
 * to drive them without a bundler or a Gateway.
 *
 * Three rules this file exists to keep:
 *
 * 1. **Provenance travels with the tab, not just with the section hint.** The
 *    War Room's one measured tab and its five preview tabs used to look
 *    identical in the bar, so "which of these can I act on?" was answerable
 *    only by reading a paragraph at the top. The chip is derived here so the
 *    label and the hint cannot drift apart.
 * 2. **Each tab depends on its own read.** The section issued one
 *    `Promise.all` for six routes, so a single 404 blanked all six — and the
 *    treasury tab rendered *nothing at all* when its read was null, which is
 *    silence presenting itself as an empty treasury. `TAB_SECTION` names the
 *    dependency so a failure blanks exactly one tab and says so.
 * 3. **A count badge only exists where the server sent a count.** `null`
 *    telemetry renders no badge rather than a zero.
 */

export type WarRoomTabId = "coordination" | "org_chart" | "rfcs" | "treasury" | "missions" | "council";

/**
 * `measured` = the tab reads live execution state.
 * `preview` = the tab reads the synthetic enterprise preview model.
 *
 * The wording matches the section's own hint ("preview evidence only, not
 * measured execution"), so the chip is a summary of a claim the surface
 * already makes, not a new one.
 */
export type TabProvenance = "measured" | "preview";

export type BadgeTone = "green" | "amber" | "gray" | "blue" | "purple" | "cyan" | "red" | "indigo";

export interface WarRoomTab {
  id: WarRoomTabId;
  label: string;
  provenance: TabProvenance;
  /** Short chip text, rendered in the tab. */
  provenanceLabel: string;
  /** The sentence behind the chip, rendered as its tooltip. */
  provenanceHint: string;
}

export const WAR_ROOM_TABS: readonly WarRoomTab[] = [
  {
    id: "coordination",
    label: "Live Coordination",
    provenance: "measured",
    provenanceLabel: "measured",
    provenanceHint: "Read from the run store and the activity ledger for one room, and re-read on an interval.",
  },
  {
    id: "org_chart",
    label: "C-Suite & Org Tree",
    provenance: "preview",
    provenanceLabel: "preview",
    provenanceHint: "Synthetic enterprise preview — an illustrative hierarchy, not a live headcount.",
  },
  {
    id: "rfcs",
    label: "Blackboard & RFCs",
    provenance: "preview",
    provenanceLabel: "preview",
    provenanceHint: "Preview debate and consensus records — not a security verification or an approval.",
  },
  {
    id: "treasury",
    label: "Fiscal Treasury",
    provenance: "preview",
    provenanceLabel: "preview",
    provenanceHint: "Synthetic token budgets and circuit breakers — not measured spend.",
  },
  {
    id: "missions",
    label: "Mission-to-Sprint DAG",
    provenance: "preview",
    provenanceLabel: "preview",
    provenanceHint: "Preview mission decomposition and sprint progress — not measured delivery.",
  },
  {
    id: "council",
    label: "Quality Council & Releases",
    provenance: "preview",
    provenanceLabel: "preview",
    provenanceHint: "Preview signature records — not cryptographic proof and not release authorization.",
  },
] as const;

export const WAR_ROOM_TAB_IDS: readonly WarRoomTabId[] = WAR_ROOM_TABS.map((t) => t.id);

/**
 * The provenance chip's tone. `measured` is blue rather than green: green
 * reads as "healthy", and provenance is a claim about where a number came
 * from, not about whether the system is well.
 */
export function provenanceTone(provenance: TabProvenance): BadgeTone {
  return provenance === "measured" ? "blue" : "amber";
}

/**
 * The provenance sentence on the collapsed preview-telemetry strip.
 *
 * The six preview cards used to be the first content in the section — above the
 * tab bar — so the least honest numbers on the surface sat in the header slot
 * even while `coordination` (the one measured tab) was the tab open beneath
 * them. Collapsing the strip keeps every card reachable while the sentence here
 * says what they are *before* anything expands.
 *
 * `present` is whether a telemetry payload actually arrived. A strip with no
 * payload says so rather than rendering six absent values, and it never claims
 * a metric count it was not sent.
 */
export function telemetryStripProvenance(present: boolean): string {
  return present
    ? "Synthetic enterprise telemetry — preview evidence only, not measured execution, not security verification, not release authorization."
    : "Preview telemetry has not been read, so no preview metric is shown.";
}

/** Which independent read a tab renders from. `null` means "none of them". */
export type WarRoomSectionKey = "telemetry" | "hierarchy" | "rfcs" | "treasury" | "missions" | "council";

export const WAR_ROOM_SECTION_KEYS: readonly WarRoomSectionKey[] = [
  "telemetry",
  "hierarchy",
  "rfcs",
  "treasury",
  "missions",
  "council",
];

export const TAB_SECTION: Record<WarRoomTabId, WarRoomSectionKey | null> = {
  coordination: null,
  org_chart: "hierarchy",
  rfcs: "rfcs",
  treasury: "treasury",
  missions: "missions",
  council: "council",
};

/**
 * The server-reported counts, kept structural so this module stays
 * import-free. Every field is nullable because the telemetry read may be
 * absent while other tabs answered.
 */
export interface EnterpriseCounts {
  departments_count?: number | null;
  active_rfcs_count?: number | null;
  active_sprints_count?: number | null;
}

/**
 * A tab's count badge, or `null` when the server sent no count.
 *
 * Deliberately refuses to derive a number from a client-side list length:
 * `rfcs.length` is what *arrived*, while `active_rfcs_count` is what the
 * server says exists, and a badge over a partial page would understate the tab
 * it labels.
 */
export function tabBadge(tabId: WarRoomTabId, counts: EnterpriseCounts | null): string | null {
  if (!counts) return null;
  const raw =
    tabId === "org_chart"
      ? counts.departments_count
      : tabId === "rfcs"
        ? counts.active_rfcs_count
        : tabId === "missions"
          ? counts.active_sprints_count
          : null;
  if (raw == null || typeof raw !== "number" || !Number.isFinite(raw)) return null;
  return String(raw);
}

/**
 * A named title for one failed section read.
 *
 * Each read reports its own failure. The alternative this replaced was one
 * `setError` for six routes, which left the operator unable to tell whether
 * the treasury was empty, missing, or simply the route that answered badly.
 */
export function sectionErrorTitle(section: WarRoomSectionKey): string {
  switch (section) {
    case "telemetry":
      return "Enterprise telemetry could not be read";
    case "hierarchy":
      return "Org tree could not be read";
    case "rfcs":
      return "RFC blackboard could not be read";
    case "treasury":
      return "Treasury could not be read";
    case "missions":
      return "Mission pipeline could not be read";
    case "council":
      return "Council releases could not be read";
    default:
      return "This section could not be read";
  }
}

/**
 * The empty-state wording for a tab whose read succeeded but returned nothing.
 *
 * Distinct from `sectionErrorTitle`: a server that answers "no rows" and a
 * server that answers 500 must not produce the same sentence, because one says
 * there is nothing to see and the other says we do not know.
 */
export function sectionEmptyTitle(section: WarRoomSectionKey): string {
  switch (section) {
    case "telemetry":
      return "No telemetry recorded yet";
    case "hierarchy":
      return "No hierarchy published";
    case "rfcs":
      return "No RFCs on the blackboard";
    case "treasury":
      return "No treasury published";
    case "missions":
      return "No sprints in the pipeline";
    case "council":
      return "No release candidates";
    default:
      return "Nothing to show";
  }
}

/**
 * Structural shape of each section's payload, kept loose on purpose so this
 * module stays import-free while still distinguishing "arrived empty" from
 * "never arrived".
 */
export interface SectionData {
  telemetry?: unknown;
  hierarchy?: { csuite?: unknown[]; departments?: unknown[] } | null;
  rfcs?: unknown[] | null;
  treasury?: unknown;
  sprints?: unknown[] | null;
  releases?: unknown[] | null;
}

/**
 * Whether a section's read actually produced something to render.
 *
 * This is the question the treasury tab used to answer *silently*: its body
 * was gated on `treasury &&`, so a null treasury rendered no error, no empty
 * state and no skeleton — a blank panel that is indistinguishable from a UI
 * that never loaded. `false` here has exactly three possible renderings
 * (failed / loading / genuinely empty) and each names which one it is.
 *
 * A published-but-empty hierarchy counts as `false`: an object the server
 * sent with no leaders and no departments is "nothing published", not "data".
 */
export function sectionHasData(section: WarRoomSectionKey, data: SectionData): boolean {
  switch (section) {
    case "telemetry":
      return data.telemetry != null;
    case "hierarchy": {
      const h = data.hierarchy;
      if (h == null) return false;
      return (h.csuite?.length ?? 0) > 0 || (h.departments?.length ?? 0) > 0;
    }
    case "rfcs":
      return (data.rfcs?.length ?? 0) > 0;
    case "treasury":
      return data.treasury != null;
    case "missions":
      return (data.sprints?.length ?? 0) > 0;
    case "council":
      return (data.releases?.length ?? 0) > 0;
    default:
      return false;
  }
}
