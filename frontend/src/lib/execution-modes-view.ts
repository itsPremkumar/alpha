// execution-modes-view.ts - the one place that says which execution modes this
// system has, where each is used, and whether the Gateway will tell you its
// current value.
//
// WHY THIS EXISTS
// ---------------
// The operator asked where Arena and the dynamic workflow were, because they
// were not listed anywhere. They exist: `ArenaPanel` in Team Ops, the council
// strategy picker in Messages, `WorkflowsSection`. But nothing collected them,
// so each mode was only discoverable if you already knew which surface owned it.
//
// A catalogue is the honest way to answer "what modes exist" - provided each row
// says whether its value is MEASURED or merely DOCUMENTED. That distinction is
// this module's whole job, and it is a three-state one:
//
//   `live`         the Gateway answered a read; the numbers are measured.
//   `auto-routed`  the engine decides per prompt; the value is observable only
//                  on the run it chose.
//   `config-only`  the setting exists in `config.yaml` and the Gateway has NO
//                  read route for it.
//
// WHY `config-only` IS ITS OWN STATE AND NOT A ROW OF NUMBERS
// -----------------------------------------------------------
// Measured against the Gateway while building this: there is no PUT/PATCH for
// subagent settings, arena config, or global orchestration mode, and no GET
// route listing council strategies, execution tiers, or the effective subagent
// ceilings either. `lib/subagent-capacity.ts` is pure arithmetic with no route
// behind it, and `lib/deliberation.ts` is POST-only.
//
// So a panel that printed "max_total_per_run: 6" or "strategies: auto, single,
// ensemble, council, debate" would be printing the values this build's source
// knows, while looking like a reading of the running system. That is the exact
// fabrication the project's rules forbid, so those families carry their state
// and a link, and no invented numbers.
//
// Every numeric field below is `number | null`, and `null` renders as "not
// reported". A ceiling the Gateway did not send must never become `0` - which
// would not read as unknown, it would read as "you may run zero agents".

import type { ArenaCatalogue, ArenaCeiling } from "./arena";

/** Whether a row's value is measured, engine-decided, or unreadable. */
export type ModeReadiness = "live" | "auto-routed" | "config-only";

export interface ModeFact {
  label: string;
  /** `null` when the Gateway did not report it - never a substituted zero. */
  value: string | number | null;
}

export interface ModeFamily {
  id: string;
  name: string;
  /** One line on what this mode actually does. */
  blurb: string;
  readiness: ModeReadiness;
  /**
   * Why this family is not `live`, in the operator's terms.
   *
   * `""` for a `live` family. For the others this is the load-bearing string:
   * "not exposed by the Gateway" is a different claim from "not measured", and
   * only one of them is an excuse for the absence.
   */
  readinessNote: string;
  /** The workspace view that owns this mode, for one-click navigation. */
  view: string;
  /** What the operator can do there, so the row is actionable. */
  action: string;
  facts: ModeFact[];
}

/** The measured Arena ceilings, in the order an operator would compare them. */
function arenaFacts(config: ArenaCeiling | null): ModeFact[] {
  if (!config) return [];
  return [
    { label: "Enabled", value: config.enabled === null ? null : config.enabled ? "yes" : "no" },
    { label: "Default competitors", value: config.defaultAgents },
    { label: "Maximum competitors", value: config.maxAgents },
    { label: "Default wave", value: config.defaultWave },
    { label: "Maximum wave", value: config.maxWave },
    { label: "Estimated tokens per call", value: config.estimateTokensPerCall },
    { label: "Confirmation required above calls", value: config.confirmationOverCalls },
    { label: "Max calls per run", value: config.maxCallsPerRun },
    { label: "Max tokens per run", value: config.maxTokensPerRun },
    { label: "Max wall seconds per run", value: config.maxWallSecondsPerRun },
    { label: "Max judge attempts", value: config.maxJudgeAttempts },
    { label: "Reasoning bank seeding", value: config.reasoningBankSeed === null ? null : config.reasoningBankSeed ? "on" : "off" },
  ];
}

/**
 * Build the catalogue.
 *
 * `arenaError` is kept separate from `config === null` on purpose: "the Gateway
 * answered and this deployment has Arena off" and "the read failed" are opposite
 * answers, and a panel that renders both as a blank block has thrown away the
 * only thing the operator needed to know.
 */
export function buildModeFamilies(input: {
  config: ArenaCeiling | null;
  catalogue: ArenaCatalogue | null;
  arenaError?: string | null;
}): ModeFamily[] {
  const arenaLive = input.config !== null || input.catalogue !== null;

  const arena: ModeFact[] = [];
  if (input.catalogue) {
    for (const profile of input.catalogue.profiles) {
      arena.push({
        label: `Profile · ${profile.name}`,
        value:
          profile.agents === null
            ? null
            : `${profile.agents} competitors${profile.wave === null ? "" : `, wave ${profile.wave}`}`,
      });
    }
    for (const mode of input.catalogue.modes) {
      arena.push({
        label: `Mode · ${mode.mode}`,
        // An advertised mode this build does not implement is reported as
        // such. Snapping it to a known mode would invent capability.
        value: mode.implemented === null ? "implementation not reported" : mode.implemented ? "implemented" : "not implemented",
      });
    }
  }

  const families: ModeFamily[] = [
    {
      id: "arena",
      name: "Arena",
      blurb: "Competitive sub-agents race one task against each other, then a judge picks the winner.",
      readiness: arenaLive ? "live" : "config-only",
      readinessNote: arenaLive
        ? ""
        : input.arenaError
          ? `The Gateway did not answer: ${input.arenaError}`
          : "The Gateway has no Arena configuration to report.",
      view: "team",
      action: "Team Ops - run a competition, project the cost before it starts",
      facts: [...arenaFacts(input.config), ...arena],
    },
    {
      id: "council",
      name: "Council",
      blurb: "Several models settle one disagreement: a single pass, an ensemble, a council or a debate.",
      // The strategy list is a client-side union and the only route is POST, so
      // naming the members here would present a source constant as a reading.
      readiness: "config-only",
      readinessNote: "The Gateway exposes no read route for the strategy list, so it is not listed here.",
      view: "messages",
      action: "Messages - pick the strategy when a disagreement needs settling",
      facts: [],
    },
    {
      id: "deliberation",
      name: "Staged deliberation",
      blurb: "A group works a question in ordered stages with quorum, recorded dissent and taint.",
      readiness: "live",
      readinessNote: "",
      view: "deliberation",
      action: "Deliberation - stage the question and read the quorum outcome",
      facts: [],
    },
    {
      id: "workflow-tier",
      name: "Dynamic workflow tier",
      blurb: "Per prompt the engine picks fast_direct, deep_reasoning, autonomous_workflow or multiagent_swarm.",
      // Choosing a tier by hand would contradict the "Default (auto-routed)"
      // label the workspace header already promises.
      readiness: "auto-routed",
      readinessNote: "Auto-routed per prompt. The tier that was chosen is shown on each Workflow run.",
      view: "workflows",
      action: "Workflows - preview the perceived intent and the tier it selected",
      facts: [],
    },
    {
      id: "orchestration",
      name: "Orchestration mode",
      blurb: "How a project's crew coordinates: parallel, moderated, quorum or round_robin.",
      // Readable per PROJECT, through that project's collaboration read. There
      // is no global read, so the global value stays config-only.
      readiness: "config-only",
      readinessNote: "Per project this is readable in Projects; the global default is config-only.",
      view: "projects",
      action: "Projects - read and set a project's collaboration mode",
      facts: [],
    },
    {
      id: "delegation",
      name: "Subagent delegation",
      blurb: "The composer's picker: off, auto, an intent preset, or one named worker from the catalog.",
      // The worker catalog IS readable (it backs the picker), but the process
      // ceilings are not, so this row links rather than restates them.
      readiness: "config-only",
      readinessNote: "The worker catalog is live in the composer; the process-wide ceilings are config-only.",
      view: "subagents",
      action: "Subagents - the catalog, live workers and per-run delegation progress",
      facts: [],
    },
  ];

  return families;
}

/**
 * The headline count of families whose value is measured.
 *
 * Reported rather than implied, because "8 modes" and "1 of those 8 is
 * measurable" are different facts and only the second one is actionable.
 */
export function liveFamilyCount(families: ModeFamily[]): number {
  return families.filter((f) => f.readiness === "live").length;
}

/** The sentence shown above the list. One claim, and it is a measured one. */
export function summarySentence(families: ModeFamily[]): string {
  const live = liveFamilyCount(families);
  const total = families.length;
  if (live === total) return `All ${total} mode families report a measured value.`;
  return (
    `${live} of ${total} mode families report a value the Gateway measured. ` +
    `The rest are listed so they are findable, with their state stated rather than a number invented.`
  );
}