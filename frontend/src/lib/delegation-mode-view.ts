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
  hint: string;
}

/** A titled run of options, rendered as one `<optgroup>`. */
export interface DelegationGroup {
  id: string;
  label: string;
  options: DelegationOption[];
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
  const kept = (cut > max * 0.6 ? head.slice(0, cut) : head).trim().replace(/[,;:]$/, "");
  // Drop a trailing fragment that hangs off an UNCLOSED bracket.
  //
  // Measured against the real Gateway catalog: `deep-code-reviewer` describes
  // itself as "...and convention checks (verdict ...)", so cutting at the last
  // space left the row reading "…and convention checks (verdict…". That is the
  // exact mid-word cut this function exists to prevent, just wearing a bracket:
  // the last space fell *inside* a parenthesised clause, so "word boundary"
  // was not "clause boundary". An orphaned fragment is worse than a shorter
  // label, so the whole bracket tail goes.
  const closed = kept.replace(/\s*[([][^()[\]]*$/, "").trim();
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
      })),
    },
  ];
}

/** How much to delegate at all — separate from which worker runs. */
function modeGroups(): DelegationGroup[] {
  return [
    {
      id: "delegation",
      label: "Delegation",
      options: [
        { value: "off", label: "Off", hint: "answer without delegating" },
        { value: "auto", label: "Auto", hint: "Alpha decides whether to delegate" },
      ],
    },
  ];
}

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
    const blurb = firstSentence(choice.description) || "description not reported";
    const list = bySource.get(source) ?? [];
    list.push({
      value: `type:${choice.name}`,
      label: choice.displayName?.trim() || choice.name,
      hint: blurb,
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
      options: [{ value: "", label: "Unavailable", hint: input.error }],
    });
  } else if (input.choices && input.choices.length === 0) {
    groups.push({
      id: "catalog-empty",
      label: "Worker catalog",
      options: [{ value: "", label: "No named workers reported", hint: "the server returned an empty catalog" }],
    });
  }
  return groups;
}

/** `label · hint` is what a native `<select>` row can actually render. */
export function optionText(option: DelegationOption): string {
  return option.hint ? `${option.label} · ${option.hint}` : option.label;
}