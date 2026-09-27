/**
 * Reasoning-effort ladder: the client mirror of the server's canonical levels.
 *
 * The vocabulary is shipped by `GET /api/models` as
 * `reasoning_effort_levels` + `reasoning_effort_labels` (weakest → strongest),
 * so the picker orders and labels from the server rather than from a copy that
 * drifts the moment a provider adds a rung. {@link FALLBACK_LADDER} exists only
 * for the degraded read (a failed or older Gateway) and is the same seven rungs
 * in the same order — it is a fallback, never the primary source.
 *
 * Three states are kept distinct on purpose, because collapsing them is how an
 * effort picker starts lying:
 *
 * - `"default"` — no explicit request. `null` on the wire. The provider's own
 *   default applies, and so does the model entry's `default_reasoning_effort`.
 * - a rung the model declares — the only thing the picker offers.
 * - a model with no declared ladder — the picker is *hidden*, not disabled with
 *   a working-looking menu. Offering rungs that would be silently clamped is
 *   exactly the honesty failure this module exists to prevent.
 *
 * Clamping matches the server (`alpha/config/reasoning_effort.py::clamp_effort`):
 * the strongest declared rung at or below the request, or the weakest declared
 * rung when the request is below every one. It runs here so switching models
 * keeps the user's intent as closely as the new model allows, and so the label
 * on the trigger is always a rung that model actually serves.
 */

import type { AIModel } from "@/types/chat";

/** Weakest → strongest. Mirrors `CANONICAL_EFFORTS` on the server. */
export const FALLBACK_LADDER: readonly string[] = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];

/** Mirrors `EFFORT_LABELS` on the server. */
export const FALLBACK_LABELS: Readonly<Record<string, string>> = {
  none: "Off",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra High",
  max: "Max",
};

/** The "send nothing and let the provider decide" choice. Not a rung. */
export const DEFAULT_EFFORT = "default" as const;
export type DefaultEffort = typeof DEFAULT_EFFORT;

/** A selected effort: the default choice, or a canonical rung. */
export type EffortChoice = DefaultEffort | string;

/** One row in the picker. */
export interface EffortOption {
  /** Canonical rung, or `default`. */
  value: EffortChoice;
  /** Server-supplied display label. */
  label: string;
  /** One line explaining the trade-off. Absent for `default`. */
  hint?: string;
  /** True when this is the rung the model entry pins for unrequested runs. */
  isModelDefault: boolean;
}

/**
 * Per-rung trade-off text.
 *
 * These are descriptions of *intent*, not measurements. A claim like "roughly
 * 3x the cost of low" is a number this codebase cannot measure for an
 * arbitrary provider, so the hints stay qualitative and the cost/latency advice
 * is phrased as a recommendation rather than an observation.
 */
const HINTS: Readonly<Record<string, string>> = {
  none: "Do not reason. Fastest and cheapest; best for lookups and trivial edits.",
  minimal: "Barely any reasoning. For short, well-specified answers.",
  low: "Light reasoning. Good for well-scoped tasks and quick fixes.",
  medium: "Balanced. The everyday default for most coding work.",
  high: "Thorough reasoning. Use for complex debugging, refactors, and design decisions.",
  xhigh: "Deep reasoning for long agentic or coding runs. Costs noticeably more than high.",
  max: "The most reasoning this model will spend in one pass.",
};

const UNSET_SPELLINGS = new Set(["", "default", "auto", "provider", "inherit"]);
const ALIASES: Readonly<Record<string, string>> = {
  off: "none",
  disabled: "none",
  no: "none",
  min: "minimal",
  "x-high": "xhigh",
  "x_high": "xhigh",
  "extra-high": "xhigh",
  "extra_high": "xhigh",
  "very-high": "xhigh",
  "very_high": "xhigh",
  extreme: "max",
  maximum: "max",
  ultra: "max",
  ultrathink: "max",
  adaptive: "high",
};

/** Resolve any accepted spelling to a canonical rung, or `null`. */
export function normalizeEffort(value: unknown): string | null {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  const text = String(value).trim().toLowerCase().replace(/_/g, "-");
  if (UNSET_SPELLINGS.has(text)) return null;
  const resolved = ALIASES[text] ?? text;
  return FALLBACK_LADDER.includes(resolved) ? resolved : null;
}

/**
 * The ladder the server declared, normalized and ordered weakest → strongest.
 *
 * An absent or empty list means the server declared nothing, which is a real
 * answer: that model has no effort control and the picker must be hidden.
 */
export function modelEffortLadder(model: Pick<AIModel, "reasoning_efforts"> | null | undefined): string[] {
  const declared = model?.reasoning_efforts;
  if (!Array.isArray(declared) || declared.length === 0) return [];
  const seen = new Set<string>();
  for (const entry of declared) {
    const rung = normalizeEffort(entry);
    if (rung) seen.add(rung);
  }
  return FALLBACK_LADDER.filter((rung) => seen.has(rung));
}

/** True when this model serves at least one rung, so the picker is meaningful. */
export function modelSupportsEffort(model: Pick<AIModel, "reasoning_efforts"> | null | undefined): boolean {
  return modelEffortLadder(model).length > 0;
}

/** The strongest declared rung at or below `requested`; the weakest when below all. */
export function clampEffort(requested: unknown, supported: readonly string[] | null | undefined): string | null {
  const target = normalizeEffort(requested);
  if (!target || !Array.isArray(supported) || supported.length === 0) return null;
  const targetIndex = FALLBACK_LADDER.indexOf(target);
  for (let i = supported.length - 1; i >= 0; i -= 1) {
    const index = FALLBACK_LADDER.indexOf(supported[i]);
    if (index >= 0 && index <= targetIndex) return supported[i];
  }
  return supported[0];
}

/**
 * Re-resolve a stored selection against a model.
 *
 * Called whenever the selected model changes. A selection the new model cannot
 * serve becomes `default` rather than a rung that would be clamped server-side
 * behind the user's back — the one case where silently lowering the level is
 * acceptable, because the alternative is refusing to run at all.
 */
export function reconcileEffortForModel(selection: EffortChoice, model: Pick<AIModel, "reasoning_efforts"> | null | undefined): EffortChoice {
  const ladder = modelEffortLadder(model);
  if (ladder.length === 0) return DEFAULT_EFFORT;
  if (selection === DEFAULT_EFFORT) return DEFAULT_EFFORT;
  const rung = normalizeEffort(selection);
  if (!rung) return DEFAULT_EFFORT;
  return ladder.includes(rung) ? rung : DEFAULT_EFFORT;
}

/**
 * Build the picker's rows for a model: "Default" first, then the model's own
 * rungs, strongest last so the list reads as an escalation.
 */
export function effortOptions(
  model: Pick<AIModel, "reasoning_efforts" | "default_reasoning_effort"> | null | undefined,
  ladder: readonly string[] = FALLBACK_LADDER,
  labels: Readonly<Record<string, string>> = FALLBACK_LABELS,
): EffortOption[] {
  const supported = modelEffortLadder(model);
  const modelDefault = normalizeEffort(model?.default_reasoning_effort);
  const options: EffortOption[] = [
    {
      value: DEFAULT_EFFORT,
      label: "Default",
      hint: modelDefault
        ? `Let the model decide. This entry runs ${labels[modelDefault] ?? modelDefault} when nothing is requested.`
        : "Let the model decide, using this model's own default reasoning level.",
      isModelDefault: false,
    },
  ];
  for (const rung of supported) {
    options.push({
      value: rung,
      label: labels[rung] ?? rung,
      hint: HINTS[rung],
      isModelDefault: rung === modelDefault,
    });
  }
  return options;
}

/** The label to show on the closed picker trigger. */
export function effortLabel(
  selection: EffortChoice,
  model: Pick<AIModel, "reasoning_efforts" | "default_reasoning_effort"> | null | undefined,
  labels: Readonly<Record<string, string>> = FALLBACK_LABELS,
): string {
  if (selection === DEFAULT_EFFORT) return "Default";
  const rung = normalizeEffort(selection);
  if (!rung) return "Default";
  return labels[rung] ?? rung;
}

/**
 * Why the picker is unavailable, or `null` when it is available.
 *
 * The UI must state this rather than render a dead control: "hidden because
 * nothing was declared" and "shown but pinned" are different answers, and a
 * greyed-out menu implies the model *might* start accepting a rung later.
 */
export function effortUnavailableReason(
  model: Pick<AIModel, "reasoning_efforts"> | null | undefined,
  knownModels: readonly AIModel[],
  selectedModelId: string,
): string | null {
  if (modelSupportsEffort(model)) return null;
  if (selectedModelId === "default" || !selectedModelId) {
    return knownModels.some(modelSupportsEffort)
      ? "Pick a model to choose how hard it reasons."
      : "No configured model declares a reasoning-effort ladder.";
  }
  return "This model declares no reasoning-effort levels, so its reasoning depth is fixed by the provider.";
}
