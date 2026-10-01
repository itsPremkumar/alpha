/**
 * Model capability derivation.
 *
 * Everything here answers one question: *what can this model actually do, and
 * how sure are we?* The second half is the point. A provider catalog that omits
 * a field is reporting an **unknown**, which is a different fact from "false" —
 * rendering an unmeasured capability as a hard no tells the user the agent
 * cannot do something when nobody checked.
 *
 * So every capability is tri-state:
 *
 * - `true`  — the server said yes
 * - `false` — the server said no
 * - `null`  — nobody said (undeclared, or a degraded read)
 *
 * Never collapse `null` to `false`. `capabilityBadge()` is the only renderer,
 * and it has a distinct visual treatment for "not reported" precisely so the
 * three states cannot be confused on screen.
 *
 * Two sources feed this, and they are not interchangeable:
 *
 * - `GET /api/models` (`AIModel`) — the *configured* catalog. Authoritative for
 *   what this deployment can run, and the only source for a declared
 *   reasoning-effort ladder.
 * - `GET /api/models/discovery` (`DiscoveredModel`) — a provider's *live*
 *   catalog. Richer (context window, vision, price) but describes the provider,
 *   not this installation, and only covers providers that answered.
 *
 * A configured model therefore keeps its configured capabilities even when
 * discovery knows nothing about it, and discovery only *adds* detail for a model
 * whose id it actually reports.
 */

import type { AIModel } from "@/types/chat";

/**
 * A capability answer.
 *
 * `null` means **not reported** — never "no". Use `isUnsupported` when you need
 * a boolean and understand that it folds unknown into no.
 */
export type Capability = boolean | null;

/** Everything the UI may want to show about one model. */
export interface ModelCapabilities {
  /** Accepts image input. */
  vision: Capability;
  /** Can call tools. */
  tools: Capability;
  /** Has a thinking/reasoning mode. */
  thinking: Capability;
  /** Accepts a reasoning-effort level. */
  reasoningEffort: Capability;
  /** Generates images (distinct from *reading* them). */
  imageGeneration: Capability;
  /** Generates video. */
  video: Capability;
  /** Transcribes or synthesizes speech. */
  speech: Capability;
  /**
   * The effective input window in tokens, or `null` when unreported.
   *
   * Sourced from discovery's *endpoint* number when available, because the
   * window that matters is the one this endpoint enforces — a gateway can proxy
   * a model whose published card disagrees with the limit it actually applies.
   */
  contextWindow: number | null;
  /** USD per 1M input tokens, or `null` when unreported. */
  inputPrice: number | null;
  /** USD per 1M output tokens, or `null` when unreported. */
  outputPrice: number | null;
}

/** One model as reported by `GET /api/models/discovery`. */
export interface DiscoveredModel {
  id: string;
  name?: string;
  context_length?: number | null;
  endpoint_context_length?: number | null;
  endpoint_max_completion_tokens?: number | null;
  supports_vision?: boolean | null;
  supports_thinking?: boolean | null;
  reasoning_efforts?: string[] | null;
  supported_parameters?: string[] | null;
  input_price_per_million?: number | null;
  output_price_per_million?: number | null;
  is_free?: boolean;
  configured?: boolean;
}

/**
 * Parameter names that prove a modality. Providers advertise modality as a
 * request parameter rather than a boolean, so a model is video-capable only when
 * it says which parameter drives it.
 *
 * This is a *narrowing* read: a model absent from these lists reports no
 * modality, and a parameter name nobody recognises is ignored rather than
 * guessed at.
 */
const MODALITY_PARAMETERS: ReadonlyArray<readonly [RegExp, keyof Pick<ModelCapabilities, "imageGeneration" | "video" | "speech">]> = [
  [/^(image|images|text.to.image|generate_image)$/i, "imageGeneration"],
  [/^(video|generate_video|text.to.video)$/i, "video"],
  [/^(audio|speech|tts|stt|transcribe|text.to.speech|input_audio)$/i, "speech"],
];

/**
 * Read modalities out of a `supported_parameters` list.
 *
 * Returns one capability per generation modality: `true` where the provider
 * named a matching parameter, `false` where it named none — but only once at
 * least one recognised modality parameter proved the vocabulary applies. With
 * nothing recognised the whole read is `null`, because "we do not know these
 * parameter names" is not the same claim as "this model cannot generate images".
 */
function modalityFromParameters(parameters: string[] | null | undefined): Pick<ModelCapabilities, "imageGeneration" | "video" | "speech"> | null {
  if (!Array.isArray(parameters)) return null;

  const seen = new Set<"imageGeneration" | "video" | "speech">();
  for (const raw of parameters) {
    if (typeof raw !== "string") continue;
    // Providers namespace parameters (`image.high`, `audio.input`); the base
    // name is what identifies the modality.
    const name = raw.split(".")[0];
    for (const [pattern, capability] of MODALITY_PARAMETERS) {
      if (pattern.test(name)) seen.add(capability);
    }
  }
  if (seen.size === 0) return null;

  return {
    imageGeneration: seen.has("imageGeneration"),
    video: seen.has("video"),
    speech: seen.has("speech"),
  };
}

/**
 * Coerce a server value to a capability, preserving unknown.
 *
 * `undefined`, `null`, and any non-boolean all mean "not reported"; a real
 * boolean passes through. A truthy non-boolean (`1`, `"true"`) is *not*
 * coerced — that would be the client inventing an answer.
 */
export function toCapability(value: unknown): Capability {
  return typeof value === "boolean" ? value : null;
}

/**
 * Read a flag the declared `AIModel` type does not carry.
 *
 * The wire shape can gain a boolean before the client type is updated, and
 * `models[]` entries are `extra="allow"` server-side, so a field may be present
 * without being typed. Reading it defensively keeps a newly-shipped server flag
 * usable instead of forcing the flag to be dropped until both sides change.
 */
function readFlag(model: AIModel | null | undefined, key: string): boolean | null {
  if (!model) return null;
  const value = (model as unknown as Record<string, unknown>)[key];
  return typeof value === "boolean" ? value : null;
}

/** A positive number, or `null`. Rejects `0` and negatives as unmeasured. */
function toPositiveNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * The window to show. Prefers the endpoint's enforced limit, then the endpoint's
 * completion budget added to the published window, then the published window.
 */
export function effectiveContextWindow(discovered: DiscoveredModel | null | undefined): number | null {
  if (!discovered) return null;
  const endpoint = toPositiveNumber(discovered.endpoint_context_length);
  if (endpoint !== null) return endpoint;
  const published = toPositiveNumber(discovered.context_length);
  const completion = toPositiveNumber(discovered.endpoint_max_completion_tokens);
  if (published !== null && completion !== null) return published + completion;
  return published;
}

/**
 * Resolve every capability for `model`.
 *
 * `discovered` may be `null`, which is the normal case: most configured models
 * belong to a keyless gateway that publishes no catalog. That must degrade the
 * detail, never the truth — a configured entry's own declared flags always win.
 */
export function modelCapabilities(model: AIModel | null | undefined, discovered: DiscoveredModel | null | undefined): ModelCapabilities {
  const modalities = modalityFromParameters(discovered?.supported_parameters);
  // A recognised-modality list makes the absent modalities a real "no"; an
  // unrecognised or absent list leaves every one of them unknown.
  const modality = (key: "imageGeneration" | "video" | "speech"): Capability => (modalities ? modalities[key] : null);

  // `supports_thinking` and a declared ladder are both authoritative: a ladder
  // implies the entry accepts an effort, which is exactly what
  // `model_effort_support` does on the server.
  const ladder = Array.isArray(model?.reasoning_efforts) ? model.reasoning_efforts : null;
  const declaredEffort: Capability =
    ladder !== null && ladder.length > 0
      ? true
      : typeof model?.supports_reasoning_effort === "boolean"
        ? model.supports_reasoning_effort
        : toCapability(discovered?.reasoning_efforts);

  return {
    vision: toCapability(model?.supports_vision ?? discovered?.supports_vision),
    // A model that reasons or declares a ladder can be assumed tool-capable:
    // an agent harness that cannot call tools cannot use a ladder at all. This
    // is an inference, so it is only applied where the server said something.
    tools: typeof model?.supports_tools === "boolean" ? model.supports_tools : declaredEffort === true ? true : null,
    // `supports_reasoning` is the general flag, `supports_thinking` the
    // narrower one; either answering yes is enough. Reading `supports_thinking`
    // through the typed shape requires it to exist on `AIModel`, which the
    // server does not send — so it is read defensively.
    thinking: toCapability(model?.supports_reasoning ?? readFlag(model, "supports_thinking") ?? discovered?.supports_thinking),
    reasoningEffort: declaredEffort,
    imageGeneration: modality("imageGeneration"),
    video: modality("video"),
    speech: modality("speech"),
    contextWindow: effectiveContextWindow(discovered) ?? toPositiveNumber(model?.context_window),
    inputPrice: toPositiveNumber(discovered?.input_price_per_million),
    outputPrice: toPositiveNumber(discovered?.output_price_per_million),
  };
}

/** Fold unknown into a boolean. Use only where "not reported" means "no". */
export function isUnsupported(capability: Capability): boolean {
  return capability === false;
}

/** Format a token count compactly: 128000 → `128K`, 1048576 → `1M`. */
export function formatContextWindow(tokens: number | null): string | null {
  if (tokens === null || !Number.isFinite(tokens) || tokens <= 0) return null;
  if (tokens >= 1_000_000) {
    const millions = tokens / 1_000_000;
    return `${millions >= 10 ? Math.round(millions) : Math.round(millions * 10) / 10}M`;
  }
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`;
  return String(tokens);
}

/**
 * Price per 1M tokens, formatted.
 *
 * Both prices must be present and zero-free to render a price at all; a
 * half-known price is not a price. `0` *is* meaningful — it is how a genuinely
 * free model is distinguished from an unpriced one — so a zero pair renders
 * "Free".
 */
export function formatPrice(input: number | null, output: number | null): string | null {
  if (input === null || output === null) return null;
  if (input === 0 && output === 0) return "Free";
  const side = (value: number): string => (value === 0 ? "$0" : `$${value}`);
  return `${side(input)} in · ${side(output)} out / 1M`;
}

/** Presentation for one capability chip. */
export interface CapabilityBadge {
  /** Stable key for tests and keys. */
  key: string;
  /** Human label, e.g. "Image input". */
  label: string;
  /** What the server actually said. */
  state: Capability;
  /** Tailwind classes; the unknown state is visually distinct on purpose. */
  className: string;
  /** Tooltip text stating the evidence, including for unknown. */
  title: string;
}

const CAPABILITY_PRESENTATION: ReadonlyArray<{
  key: keyof ModelCapabilities;
  label: string;
  /** Tooltip when the server said yes. */
  yes: string;
  /** Tooltip when the server said no. */
  no: string;
  /** Tooltip when nobody reported it — must read as unknown, not as "no". */
  undeclared: string;
  onClass: string;
  offClass: string;
}> = [
  { key: "vision", label: "Image input", yes: "Accepts image input", no: "No image input", undeclared: "Image input not reported by the provider", onClass: "text-emerald-600", offClass: "text-muted-foreground/50 line-through" },
  { key: "tools", label: "Tools", yes: "Can call tools", no: "Cannot call tools", undeclared: "Tool support not reported by the provider", onClass: "text-emerald-600", offClass: "text-muted-foreground/50 line-through" },
  { key: "thinking", label: "Thinking", yes: "Has a thinking mode", no: "No thinking mode", undeclared: "Thinking support not reported by the provider", onClass: "text-amber-600", offClass: "text-muted-foreground/50 line-through" },
  { key: "reasoningEffort", label: "Effort", yes: "Accepts a reasoning-effort level", no: "Effort is fixed by the provider", undeclared: "Effort support not reported by the provider", onClass: "text-amber-600", offClass: "text-muted-foreground/50 line-through" },
  { key: "imageGeneration", label: "Image gen", yes: "Generates images", no: "Does not generate images", undeclared: "Image generation not reported by the provider", onClass: "text-sky-600", offClass: "text-muted-foreground/50 line-through" },
  { key: "video", label: "Video", yes: "Generates video", no: "Does not generate video", undeclared: "Video generation not reported by the provider", onClass: "text-violet-600", offClass: "text-muted-foreground/50 line-through" },
  { key: "speech", label: "Speech", yes: "Handles speech", no: "No speech support", undeclared: "Speech support not reported by the provider", onClass: "text-teal-600", offClass: "text-muted-foreground/50 line-through" },
];

const UNKNOWN_CLASS = "text-muted-foreground/50 border-dashed";

/**
 * Build the chips for one model's capabilities.
 *
 * A capability reported as `true` always renders — that is the whole point of
 * the picker. `false` renders too, because "no image input" is information a
 * user choosing a model for screenshots needs. `null` renders as a dashed
 * "not reported" chip rather than being hidden, so the absence of knowledge is
 * visible instead of silently reading as a lack of capability.
 */
export function capabilityBadges(capabilities: ModelCapabilities): CapabilityBadge[] {
  const badges: CapabilityBadge[] = [];
  for (const spec of CAPABILITY_PRESENTATION) {
    const state = toCapability(capabilities[spec.key]);
    const isUnknown = state === null;
    badges.push({
      key: spec.key,
      label: spec.label,
      state,
      className: isUnknown ? UNKNOWN_CLASS : state ? spec.onClass : spec.offClass,
      title: isUnknown ? spec.undeclared : state ? spec.yes : spec.no,
    });
  }
  return badges;
}

/**
 * Index discovery results by model id.
 *
 * A duplicate id keeps the **first** entry, matching the server's own
 * first-wins merge rather than letting a later row overwrite richer data.
 */
export function indexDiscoveredModels(discovered: readonly DiscoveredModel[] | null | undefined): Map<string, DiscoveredModel> {
  const index = new Map<string, DiscoveredModel>();
  if (!Array.isArray(discovered)) return index;
  for (const entry of discovered) {
    if (!entry || typeof entry.id !== "string" || !entry.id) continue;
    if (!index.has(entry.id)) index.set(entry.id, entry);
  }
  return index;
}

/**
 * Find the discovery row for a configured model.
 *
 * Ids disagree in the wild: a configured `alpha-free` is the router, while
 * discovery reports the concrete gateway model (`free:opencode-zen:space-bunny-free`).
 * So this tries the exact id, then the `free:<gateway>:<model>` form, then a bare
 * suffix match on the configured id — and returns `null` rather than a
 * best-effort wrong row.
 */
export function findDiscoveredModel(
  modelId: string | null | undefined,
  index: ReadonlyMap<string, DiscoveredModel>,
): DiscoveredModel | null {
  if (!modelId) return null;
  const direct = index.get(modelId);
  if (direct) return direct;

  const parts = modelId.split(":");
  if (parts[0] === "free" && parts.length === 3) {
    const viaFree = index.get(`free:${parts[1]}:${parts[2]}`);
    if (viaFree) return viaFree;
    const bare = index.get(parts[2]);
    if (bare) return bare;
  }

  const tail = parts[parts.length - 1];
  if (tail && tail !== modelId) {
    const viaTail = index.get(tail);
    if (viaTail) return viaTail;
  }
  return null;
}
