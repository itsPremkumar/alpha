// delegation-mode-view.ts — how the composer's subagent picker is organised.
//
// THE DEFECT (the operator's own screenshot). The picker was one flat list of
// ~14 options in a 176px-wide native select, so:
//
//   * three different KINDS of choice sat in one alphabetical-looking run:
//     how much to delegate (`off` / `auto`), which INTENT to delegate with
//     (`category:*`), and WHICH NAMED WORKER to run (`type:*`);
//   * descriptions were cut mid-word by `.slice(0, 72)`, producing rows that
//     read "with clear dele…", "convention c…", "sanitization-guard r…";
//   * the presets were in arbitrary order, so the two that differ most in cost
//     (`quick` = 15 turns / 120s) sat next to ones that quote no budget at all;
//   * the eight `deep-*` workers ran straight into the built-ins with nothing to
//     separate them.
//
// A native `<select>` is kept deliberately: it is keyboard-navigable, screen-
// reader-native and usable on a phone, all of which a div-built popover has to
// re-earn badly. `<optgroup>` buys the grouping without that cost.
//
// The honesty rules this module keeps:
//
//   * A budget is shown only where the configuration actually states one.
//     `quick` really is 15 turns / 120s; the others quote nothing, so they say
//     nothing rather than inventing a number to look comparable.
//   * A worker with no description is labelled as unreported, never filled in.
//   * The `value` strings (`off`, `auto`, `category:<name>`, `type:<name>`) are
//     the wire contract with `ChatView` and are unchanged by this file.

/** One selectable row. `hint` is the secondary line in the menu. */
export interface DelegationOption {
  value: string;
  label: string;
  /**
   * The short secondary line shown **in the popup row**.
   *
   * Kept short on purpose. The popup is drawn by the OS and is only as wide as
   * its widest row, so a full sentence per row stretched the whole list to
   * roughly 750px of near-unreadable one-line-per-worker text floating over the
   * composer. The full text is not discarded - it is in `detail`.
   */
  hint: string;
  /**
   * The unabridged description, shown as the control's tooltip for the
   * *selected* option, so nothing is lost when the row is shortened.
   *
   * `""` means the server reported no description at all; `hint` then says so
   * rather than this inventing one.
   */
  detail: string;
}

/** A titled run of options, rendered as one `<optgroup>`. */
export interface DelegationGroup {
  id: string;
  label: string;
  options: DelegationOption[];
}

/**
 * Connectives that must never end a label.
 *
 * Cutting at a word boundary is necessary but not sufficient: it happily leaves
 * "...regression, and" or "...bounded shell workflows with", which reads as a
 * broken sentence and tells the operator less than simply stopping one word
 * earlier. Measured against the real catalog, every one of these cuts landed on
 * exactly one of these words.
 */
const DANGLING = new Set([
  "and", "or", "but", "nor", "the", "a", "an", "with", "of", "to", "in", "on",
  "for", "at", "by", "from", "as", "into", "per", "via", "that", "which", "is",
  "are", "was", "were", "be", "been", "than", "then", "over", "under",
  "there", "here", "where", "when", "while", "whose", "whom", "after",
  "before", "during", "about", "against", "between", "through", "without",
  "within", "upon", "onto", "off", "out", "up", "down", "again", "also",
  "such", "both", "each", "any", "all", "its", "their", "our", "your", "his",
  "her", "my", "so",
]);

/**
 * Tidy the tail of a cut label: drop an orphaned bracket clause and a dangling
 * connective, repeatedly, because dropping one can expose the other.
 */
function trimLabelTail(kept: string): string {
  let out = kept.trim();
  for (let pass = 0; pass < 4; pass += 1) {
    const before = out;
    // An unclosed bracket means the last space fell INSIDE a clause, so the
    // "word boundary" was not a clause boundary. The whole tail goes.
    out = out.replace(/\s*[([][^()[\]]*$/, "").trim();
    const words = out.split(" ");
    if (words.length > 1 && DANGLING.has((words[words.length - 1] || "").toLowerCase())) {
      words.pop();
      out = words.join(" ");
    }
    out = out.replace(/[\s,;:·/—-]+$/, "");
    if (out === before) break;
  }
  return out;
}

/**
 * The first complete sentence of `text`, capped at `max`.
 *
 * A hard character cut is what produced "with clear dele…". Taking the sentence
 * boundary and only falling back to a word boundary when there is no sentence
 * end means a label is always a readable claim, never a fragment — and a worker
 * whose description runs past the cap is truncated at a word with an ellipsis so
 * it is visibly shortened rather than quietly clipped.
 */
export function firstSentence(text: string | null | undefined, max = 88): string {
  const flat = (text ?? "").replace(/\s+/g, " ").trim();
  if (!flat) return "";
  const stop = flat.search(/[.!?](\s|$)/);
  const sentence = stop === -1 ? "" : flat.slice(0, stop + 1).trim();
  if (sentence && sentence.length <= max) return sentence;
  // No sentence end inside the budget: cut on the last whole word.
  const head = (sentence || flat).slice(0, max);
  const cut = head.lastIndexOf(" ");
  const closed = trimLabelTail(cut > max * 0.6 ? head.slice(0, cut) : head);
  return closed.length < (sentence || flat).length ? `${closed}…` : closed;
}

/** Server-reported cost facts. Absent means "not reported", never a guess. */
const PRESET_BUDGET: Record<string, string> = {
  // `config.example.yaml` states these two explicitly; the rest do not, so
  // they get no budget rather than a made-up one.
  quick: "15 turns · 2 min cap",
  research: "100 turns",
};

/**
 * Intent presets, cheapest and fastest first.
 *
 * Ordering by cost is the point: the operator picking a subagent wants to know
 * what it will spend, and `quick` is a genuinely different order of magnitude
 * from a five-pass research run. Anything whose budget the server does not
 * report is ordered after the ones it does, so an unreported row never implies
 * a smaller bill by sitting beside a cheap one.
 */
const PRESETS: Array<{ name: string; label: string; blurb: string }> = [
  { name: "quick", label: "Quick", blurb: "low-latency, bounded" },
  { name: "general", label: "General", blurb: "standard bounded helper" },
  { name: "research", label: "Research", blurb: "multi-source, verified claims" },
  { name: "deep-research", label: "Deep research", blurb: "five-pass evidence workflow" },
];

function presetGroups(): DelegationGroup[] {
  const withBudget = PRESETS.filter((p) => PRESET_BUDGET[p.name]);
  const withoutBudget = PRESETS.filter((p) => !PRESET_BUDGET[p.name]);
  return [
    {
      id: "intent",
      label: "Intent preset — what the delegation is for",
      options: [...withBudget, ...withoutBudget].map((p) => ({
        value: `category:${p.name}`,
        label: p.label,
        hint: [p.blurb, PRESET_BUDGET[p.name]].filter(Boolean).join(" · "),
        detail: [
          p.blurb,
          PRESET_BUDGET[p.name],
          // Never let a preset read as costless when it is the one preset whose
          // ceiling the configuration states and the model honours per task.
          p.name === "quick" ? "ceiling applies per delegated task" : "",
        ]
          .filter(Boolean)
          .join(" · "),
      })),
    },
  ];
}

/** How much to delegate at all — separate from which worker runs. */
function modeGroups(): DelegationGroup[] {
  const rows = [
    { value: "off", label: "Off", hint: "answer without delegating" },
    { value: "auto", label: "Auto", hint: "Alpha decides whether to delegate" },
  ];
  return [
    {
      id: "delegation",
      label: "Delegation",
      options: rows.map((r) => ({ ...r, detail: r.hint })),
    },
  ];
}

/** How many characters of description a popup row may show. */
const ROW_HINT_MAX = 64;

/** The worker catalog, grouped by the source the server reported. */
function workerGroups(
  choices: Array<{ name: string; displayName?: string | null; description?: string | null; source?: string }> | null,
): DelegationGroup[] {
  if (!choices || choices.length === 0) return [];
  const bySource = new Map<string, DelegationOption[]>();
  for (const choice of choices) {
    // A worker with no reported source is grouped under "reported source", not
    // dropped and not folded into "builtin".
    const source = choice.source?.trim() || "source not reported";
    const flat = (choice.description ?? "").replace(/\s+/g, " ").trim();
    const blurb = firstSentence(choice.description, ROW_HINT_MAX) || "description not reported";
    const list = bySource.get(source) ?? [];
    list.push({
      value: `type:${choice.name}`,
      label: choice.displayName?.trim() || choice.name,
      hint: blurb,
      // The full text rides along for the tooltip; `""` truthfully means the
      // server sent no description, which `hint` already discloses.
      detail: flat,
    });
    bySource.set(source, list);
  }
  return [...bySource.entries()]
    .map(([source, options]) => ({
      id: `worker:${source}`,
      label: source,
      // Sorted so the picker is stable between renders, rather than in whatever
      // order the catalog happened to arrive in.
      options: options.sort((a, b) => a.label.localeCompare(b.label)),
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

/**
 * The whole picker, grouped and ordered.
 *
 * `error` and `empty` are reported as their own group rather than smuggled in
 * as a disabled row, because "the catalog could not be read" and "the catalog
 * is empty" are opposite answers and the operator must be able to tell them
 * apart.
 */
export function buildDelegationGroups(input: {
  choices: Array<{ name: string; displayName?: string | null; description?: string | null; source?: string }> | null;
  error?: string | null;
}): DelegationGroup[] {
  const groups = [...modeGroups(), ...presetGroups(), ...workerGroups(input.choices)];
  if (input.error) {
    groups.push({
      id: "catalog-error",
      label: "Worker catalog",
      options: [
        {
          value: "",
          label: "Unavailable",
          hint: input.error,
          // The popup row is a place, not a place to read a stack trace in; the
          // tooltip carries the server's full reason.
          detail: `Subagent catalog could not be read: ${input.error}`,
        },
      ],
    });
  } else if (input.choices && input.choices.length === 0) {
    groups.push({
      id: "catalog-empty",
      label: "Worker catalog",
      options: [
        {
          value: "",
          label: "No named workers reported",
          hint: "the server returned an empty catalog",
          detail: "The Gateway reported no named subagent workers. Delegation modes and intent presets are still available.",
        },
      ],
    });
  }
  return groups;
}

// ---------------------------------------------------------------------------
// Other engines — NOT delegation modes
// ---------------------------------------------------------------------------

/**
 * An execution engine that owns its own run, rather than a way to delegate one.
 *
 * These cannot be `delegationMode` values. The run request carries exactly two
 * delegation fields — `preferred_subagent_type` and
 * `preferred_subagent_category` — and nothing else. An `arena:standard` row
 * would send neither, so the server would treat it as plain `auto`: a control
 * that looks like a choice and changes nothing. So these are *navigational* rows
 * that open the surface which owns the engine, and their `value` is prefixed
 * `engine:` precisely so no prefix-stripping branch can mistake one for a
 * delegation mode.
 */
export interface EngineRow {
  id: string;
  label: string;
  /** What this engine is, and why it is not a delegation option. */
  hint: string;
  /** The workspace view that owns it. */
  view: string;
  /** Why it is unavailable, when it is. `""` means available. */
  unavailable: string;
}

export interface EngineGroup {
  id: string;
  label: string;
  rows: EngineRow[];
}

/**
 * The engines that exist beside the delegation picker.
 *
 * `arenaEnabled` is a real measurement (`GET /arena/config`), not a guess: when
 * the Gateway says Arena is off, the row is not offered as though it could run.
 * The remaining engines have **no read route** for their current state, which is
 * why their `unavailable` is `""` — the surface itself is the authority, and this
 * catalogue does not claim a value it cannot measure.
 */
export function buildEngineGroups(input: { arenaEnabled?: boolean | null }): EngineGroup[] {
  const arena =
    input.arenaEnabled === false
      ? {
          id: "arena",
          label: "Arena",
          hint: "competitive sub-agents, judged — reports Arena disabled",
          view: "team",
          unavailable: "Arena is disabled in this deployment's configuration.",
        }
      : {
          id: "arena",
          label: "Arena",
          hint: "competitive sub-agents, judged — starts its own run",
          view: "team",
          unavailable: "",
        };

  return [
    {
      id: "engines",
      label: "Other engines — each starts its own run",
      rows: [
        arena,
        {
          id: "workflow",
          label: "Dynamic workflow",
          hint: "the engine picks the tier per prompt — auto-routed",
          view: "workflows",
          unavailable: "",
        },
        {
          id: "council",
          label: "Council",
          hint: "several models settle one disagreement — starts its own run",
          view: "messages",
          unavailable: "",
        },
        {
          id: "deliberation",
          label: "Staged deliberation",
          hint: "ordered stages with quorum and recorded dissent",
          view: "deliberation",
          unavailable: "",
        },
        {
          id: "orchestration",
          label: "Orchestration mode",
          hint: "per project: parallel, moderated, quorum, round_robin",
          view: "projects",
          unavailable: "",
        },
      ],
    },
  ];
}

/** The value an engine row sends, namespaced so it can never be a mode. */
export function engineValue(row: EngineRow): string {
  return `engine:${row.id}`;
}

/** The delegation value implied by an `engine:` row, or `null` if it is not one. */
export function engineTargetView(groups: EngineGroup[], value: string): string | null {
  if (!value.startsWith("engine:")) return null;
  for (const group of groups) {
    for (const row of group.rows) {
      if (engineValue(row) === value) return row.view;
    }
  }
  return null;
}

/** `label · hint` is what a native `<select>` row can actually render. */
export function optionText(option: DelegationOption): string {
  return option.hint ? `${option.label} · ${option.hint}` : option.label;
}

/**
 * The full description of whichever option is currently selected, for the
 * control's `title`.
 *
 * This is what keeps the shortened popup rows from costing any information: the
 * row is scannable, and hovering the closed control still explains the current
 * choice in full. An unknown `value` (a persisted mode from a newer Gateway)
 * yields `""` rather than a guess about what it means.
 */
export function selectedDetail(groups: DelegationGroup[], value: string): string {
  for (const group of groups) {
    const found = group.options.find((o) => o.value === value);
    if (found) return found.detail || found.hint;
  }
  return "";
}