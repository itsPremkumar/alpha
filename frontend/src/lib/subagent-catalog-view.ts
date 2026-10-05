/**
 * Presentation derivation for the subagent catalog panel.
 *
 * ## Why this file exists
 *
 * `GET /api/subagents` sends **fifteen** fields per definition
 * (`subagents.py::SubagentResponse`). The client mapped six of them, so the UI
 * showed a name, a badge, one sentence and a model - and the other nine were
 * invisible: the tool allowlist, the deny-list, granted skills, the turn and
 * timeout ceilings, `display_name`, `conflict`, `config_overrides`, and the
 * system prompt itself. Measured live against the Gateway: 8 builtin
 * definitions, each carrying a full multi-paragraph prompt and an explicit tool
 * list, and the catalog block rendered **one line of description**.
 *
 * That is the specific shape this repo's own guidance calls out ("the count was
 * a claim with no evidence attached; the list is the evidence") applied to
 * definitions instead of models. An operator deciding whether to delegate to
 * `deep-architect` cannot see which tools it may call.
 *
 * ## Why it is a separate pure module
 *
 * Three fields each have a `null` state that is a **different fact** from `0`
 * or `[]`, and each would otherwise need the same sentence in the list row, the
 * detail header, and the footer. Declaring them twice is how a detail pane and
 * its own summary end up contradicting each other - one says "no tools", the
 * other says "not reported", for the same field. So the mapping lives in
 * `subagents.ts` (the transport's job) and every sentence lives here (the
 * presentation's job), and both are pure so `node --test` can drive the exact
 * strings the browser renders.
 *
 * The three distinctions that must not collapse:
 *
 * | Server sent | Meaning | Must not become |
 * |---|---|---|
 * | `tools: null` | no allowlist constraint - unrestricted | "no tools" |
 * | `tools: []` | an explicit empty allowlist - nothing callable | "not reported" |
 * | no `tools` key | shape drift / older Gateway | either of the above |
 * | `system_prompt: null` | withheld; the route is admin-gated | "no prompt" |
 * | `max_turns: null` | unreported | `50` (the server default) |
 * | `config_overrides: {}` | operator set nothing; defaults still apply | "not configured" |
 *
 * The last row is the subtle one. `_explicit_overrides` only reports keys the
 * operator actually wrote (`model_fields_set`), so an empty object is the
 * **normal** case for every definition on a stock install. Saying "no
 * overrides" would be true and useless; saying "not configured" would be false,
 * because the defaults are very much in force.
 */

import type { SubagentDef } from "./subagents";

/** Badge tone for a definition's `source`. Unknown sources stay neutral. */
export type SourceTone = "gray" | "blue" | "green" | "amber" | undefined;

/**
 * What each `source` value means, as the server defines it.
 *
 * `subagents.py::_catalog` builds three tiers and the router types the field as
 * `Literal["builtin", "config", "managed"]`. The precedence is real: the
 * execution path resolves `custom_agents` over `BUILTIN_SUBAGENTS`, so a
 * `config` row with the same name **replaces** the builtin - which is why
 * `conflict` exists and why this panel groups by source instead of flattening.
 *
 * A source this build does not name renders verbatim with a neutral tone. It is
 * never snapped to `builtin`, because "a source string from a newer Gateway
 * that happens to sort first" is not the same claim as "shipped with Alpha".
 */
export const SOURCE_META: Record<
  string,
  { label: string; tone: SourceTone; blurb: string }
> = {
  builtin: {
    label: "built in",
    tone: "gray",
    blurb:
      "Ships with Alpha. Edited through config.yaml or the registry API, not from here.",
  },
  config: {
    label: "config.yaml",
    tone: "blue",
    blurb:
      "Declared under subagents.custom_agents. Takes precedence over a built-in of the same name.",
  },
  managed: {
    label: "managed",
    tone: "green",
    blurb:
      "Created at runtime, by an agent through subagent_registry or by an admin here. Editable.",
  },
};

/** Server order for grouping, then unknown sources last so they stay visible. */
const SOURCE_ORDER = ["builtin", "config", "managed"] as const;

export function sourceMeta(source: string): {
  label: string;
  tone: SourceTone;
  blurb: string;
} {
  return (
    SOURCE_META[source] ?? {
      label: source || "source not reported",
      tone: undefined,
      blurb:
        "A source value this build does not recognise, shown exactly as the server reported it.",
    }
  );
}

export function isKnownSource(source: string): boolean {
  return Object.prototype.hasOwnProperty.call(SOURCE_META, source);
}

/** What the panel calls this definition, and where that name came from. */
export function titleOf(d: SubagentDef): {
  title: string;
  fromDisplayName: boolean;
} {
  if (d.displayName) return { title: d.displayName, fromDisplayName: true };
  return { title: d.name || "(unnamed definition)", fromDisplayName: false };
}

/**
 * The enabled state as a sentence plus a tone, never a colour alone.
 *
 * Three states, because `SubagentDef.enabled` is `boolean | null` and the third
 * is the one that regressed before: an absent flag used to be coerced to `true`
 * and painted green. Green asserts the subagent is callable. An unreported flag
 * asserts nothing, so it gets the neutral tone and a written reason.
 */
export function enabledView(enabled: boolean | null): {
  label: string;
  tone: "green" | "gray" | "muted";
  reason: string;
} {
  if (enabled === null) {
    return {
      label: "unknown",
      tone: "muted",
      reason:
        "The server did not report an enabled flag for this definition, so whether the agent can delegate to it was not measured.",
    };
  }
  return enabled
    ? {
        label: "on",
        tone: "green",
        reason: "The server reported this definition as enabled.",
      }
    : {
        label: "off",
        tone: "gray",
        reason:
          "The server reported this definition as disabled, so the agent will not be offered it.",
      };
}

/**
 * One capability list as a sentence, distinguishing all three states.
 *
 * `word` is what the server's value actually means for this field, so the
 * caller passes "allowlist"/"deny-list"/"skills" rather than hardcoding one.
 */
export function listView(
  values: string[] | null,
  word: string,
): {
  state: "absent" | "unrestricted" | "empty" | "listed";
  summary: string;
  chips: string[];
} {
  if (values === null) {
    return {
      state: "absent",
      summary:
        word === "allowlist"
          ? `No tool ${word}: this subagent is not restricted to a named set.`
          : `No ${word} reported by the server.`,
      chips: [],
    };
  }
  if (values.length === 0) {
    return {
      state: "empty",
      summary:
        word === "allowlist"
          ? `The ${word} is explicitly empty, so no tool is callable.`
          : `The server reported an empty ${word}.`,
      chips: [],
    };
  }
  return {
    state: "listed",
    summary: `${values.length} ${word} ${values.length === 1 ? "entry" : "entries"}.`,
    chips: [...values],
  };
}

/**
 * The turn ceiling as a sentence. `null` stays unreported, never `50`.
 *
 * `ManagedSubagentCreateRequest.max_turns` defaults to 50 and
 * `timeout_seconds` to 900, so those numbers are what a reader would guess, and
 * that is exactly why inventing them here would be the failure: the panel would
 * assert a budget it never read.
 */
export function limitView(value: number | null, unit: string): string {
  if (value === null) return `${unit} not reported`;
  return `${value} ${unit}`;
}

/**
 * Why a system prompt is missing, in words.
 *
 * `GET /api/subagents` computes `include_system_prompt = await
 * is_admin_user(request)` and sends `system_prompt=None` for every row to a
 * non-admin. That is a permission outcome, not a missing document, and it is the
 * only reason this route produces, so it is named rather than guessed at.
 */
export function promptDisclosure(systemPrompt: string | null): {
  present: boolean;
  reason: string;
} {
  if (systemPrompt === null) {
    return {
      present: false,
      reason:
        "The server withheld this prompt. GET /api/subagents only returns system_prompt to admins, so this is an admin gate, not a definition without instructions.",
    };
  }
  if (systemPrompt.trim() === "") {
    return {
      present: false,
      reason: "The server returned an empty prompt for this definition.",
    };
  }
  return { present: true, reason: "" };
}

/**
 * `config.yaml` overrides as sorted `key: value` rows, plus why the list may be
 * empty.
 *
 * Values are rendered with `JSON.stringify` so a list or a number reads the way
 * it is written in the file, and an absent block is disclosed rather than shown
 * as `{}`. The empty case is the common one and says what it means: the operator
 * set nothing, and the **definition's own defaults** are in force.
 */
export function overridesView(overrides: Record<string, unknown> | null): {
  rows: { key: string; value: string }[];
  note: string;
} {
  if (overrides === null) {
    return {
      rows: [],
      note: "The server reported no config_overrides block for this definition.",
    };
  }
  const keys = Object.keys(overrides).sort();
  if (keys.length === 0) {
    return {
      rows: [],
      note: "No config.yaml overrides for this name. The definition's own defaults are in force.",
    };
  }
  return {
    rows: keys.map((key) => {
      const v = overrides[key];
      let value: string;
      try {
        value = JSON.stringify(v) ?? String(v);
      } catch {
        value = String(v);
      }
      return { key, value };
    }),
    note: "Only keys written in config.yaml appear here; anything absent uses the definition's own default.",
  };
}

/** The conflict sentence, or `null` when there is no conflict. */
export function conflictNote(conflict: boolean): string | null {
  if (!conflict) return null;
  return "Another definition already uses this name. The execution path prefers config.yaml over a built-in of the same name, so a row further down this list may never run as written.";
}

/** Group definitions by source, keeping the server's tier order. */
export interface SourceGroup {
  source: string;
  label: string;
  blurb: string;
  tone: SourceTone;
  items: SubagentDef[];
}

export function groupBySource(defs: SubagentDef[]): SourceGroup[] {
  const groups = new Map<string, SubagentDef[]>();
  for (const d of defs) {
    const key = d.source || "source not reported";
    const bucket = groups.get(key);
    if (bucket) bucket.push(d);
    else groups.set(key, [d]);
  }
  const rank = (s: string) => {
    const i = (SOURCE_ORDER as readonly string[]).indexOf(s);
    return i === -1 ? SOURCE_ORDER.length : i;
  };
  return [...groups.entries()]
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
    .map(([source, items]) => ({
      source,
      ...sourceMeta(source),
      items: [...items].sort((x, y) => x.name.localeCompare(y.name)),
    }));
}

/**
 * Headline counts for the catalog header.
 *
 * `count` is the measured total and `enabled` is measured among it. A panel that
 * printed only `8 subagents` left the operator unable to tell whether any of the
 * eight was usable, which is the one thing a delegation catalog exists to
 * answer.
 */
export function catalogCounts(defs: SubagentDef[]): {
  total: number;
  enabled: number;
  disabled: number;
  unknown: number;
} {
  let enabled = 0;
  let disabled = 0;
  let unknown = 0;
  for (const d of defs) {
    if (d.enabled === true) enabled += 1;
    else if (d.enabled === false) disabled += 1;
    else unknown += 1;
  }
  return { total: defs.length, enabled, disabled, unknown };
}

/** The headline sentence, which must disclose unknown rather than hide it. */
export function countsSentence(
  counts: ReturnType<typeof catalogCounts>,
): string {
  if (counts.total === 0) return "The server reported no subagent definitions.";
  const parts = [`${counts.enabled} of ${counts.total} enabled`];
  if (counts.disabled) parts.push(`${counts.disabled} disabled`);
  if (counts.unknown)
    parts.push(`${counts.unknown} not reporting an enabled flag`);
  return parts.join(" | ");
}

/**
 * The one-line capability summary in a list row.
 *
 * Reads as a claim the server made, never as a measurement the panel took:
 * "all tools", a named count, or "tool allowlist not reported".
 */
export function rowCapabilityLine(d: SubagentDef): string {
  const tools = listView(d.tools, "allowlist");
  const skills = listView(d.skills, "skills");
  const bits: string[] = [];
  if (tools.state === "absent") bits.push("all tools");
  else if (tools.state === "empty") bits.push("no tools");
  else bits.push(`${tools.chips.length} tools`);
  if (skills.state === "listed") bits.push(`${skills.chips.length} skills`);
  else if (skills.state === "empty") bits.push("no skills");
  const turn = d.maxTurns === null ? "turns unreported" : `${d.maxTurns} turns`;
  bits.push(turn);
  return bits.join(" | ");
}
