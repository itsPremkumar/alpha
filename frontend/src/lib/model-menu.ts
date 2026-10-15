/**
 * Pure derivations behind the composer's model menu.
 *
 * The menu replaces the native `<select>` the composer used to render. That
 * swap is the whole point: a native `<option>` cannot host a live health chip,
 * an API-key field or a settings link, so those three model-related controls
 * had to live *beside* the control as floating icons — one of them (the amber
 * probe) with no textual label at all. They are now rows inside the one menu.
 *
 * Everything here is pure so `model-menu.test.mjs` can drive the exact function
 * that produces each sentence rather than scraping prose out of JSX.
 *
 * **Health is a provider measurement, and it never becomes a model claim.** The
 * Gateway probes a gateway, not a model ID, so a model listed under a healthy
 * provider has *not* been individually proven to answer. That is why every
 * derived label here says "provider" — `healthyChip(providers, model)` can
 * return only "provider healthy", never "healthy", and a model whose provider
 * cannot be found returns `null` rather than borrowing a verdict from
 * somewhere else. Collapsing the two is how a panel ends up tinting forty rows
 * green on the strength of one gateway probe.
 */

import type { AIModel } from "@/types/chat";
import type { FreeProviderHealth } from "@/lib/freeModels";

/** The order the menu renders its groups in — free first, because it is what the picker exists to surface. */
export const MODEL_GROUP_ORDER = ["keyless", "quota", "standard"] as const;
export type ModelGroupKey = (typeof MODEL_GROUP_ORDER)[number];

/**
 * First-load selection for the composer. The catalog is usually ordered with
 * paid/provider-backed entries first, so using its first item bypasses the
 * Gateway's configured `default_model` (and can violate `spend.mode`). Keep
 * auto-routing as the default unless the operator has explicitly saved a
 * still-available model selection.
 */
export function initialSelectedModel(
  savedModel: string | null,
  models: readonly Pick<AIModel, "id">[],
): string {
  if (savedModel && (models.length === 0 || models.some((model) => model.id === savedModel))) {
    return savedModel;
  }
  return "default";
}

export interface ModelGroups {
  keyless: AIModel[];
  quota: AIModel[];
  standard: AIModel[];
}

/**
 * Split the effective model list into the three menu groups.
 *
 * Moved verbatim out of `Composer.tsx`, where it was inlined in the render and
 * therefore untestable. The predicates are unchanged — including the explicit
 * `free:<provider>` prefix list, which exists because those gateways report
 * `is_free` without any of the tri-state `free_status` / `quota_type` fields
 * that would otherwise be the only way in.
 */
export function groupModels(models: AIModel[]): ModelGroups {
  const groups: ModelGroups = { keyless: [], quota: [], standard: [] };
  for (const m of models) {
    if (m.is_free || m.id.startsWith("free:") || m.id === "alpha-free") {
      if (
        m.free_status === "no_key_free" ||
        m.quota_type === "keyless_free" ||
        m.quota_type === "keyless" ||
        m.id.startsWith("free:ovhcloud") ||
        m.id.startsWith("free:pollinations") ||
        m.id.startsWith("free:vireonix") ||
        m.id.startsWith("free:llm7") ||
        m.id.startsWith("free:cehpoint") ||
        m.id.startsWith("free:aihorde") ||
        m.id === "alpha-free"
      ) {
        groups.keyless.push(m);
      } else {
        groups.quota.push(m);
      }
    } else {
      groups.standard.push(m);
    }
  }
  return groups;
}

/** Human labels for each group, in render order. */
export const GROUP_LABELS: Record<ModelGroupKey, string> = {
  keyless: "Free models (no API key)",
  quota: "Free tier / gateway quota",
  standard: "Standard & custom models",
};

/**
 * Providers offered by the inline API-key form, with their tier kept.
 *
 * The tier is not decoration: it is the difference between a key you can add
 * to never pay (a recurring $0 quota) and one that starts billing you. A flat
 * list that put "OpenAI" beside "Google Gemini (15 RPM free)" would hide that
 * behind two rows of identical weight, which is why the form renders the tiers
 * as optgroups rather than one alphabetised list.
 */
export type KeyProviderTier = "free" | "trial" | "paid";

export interface KeyProviderOption {
  id: string;
  label: string;
  tier: KeyProviderTier;
}

export const KEY_PROVIDER_TIER_LABELS: Record<KeyProviderTier, string> = {
  free: "Recurring $0 free tier",
  trial: "Free trial credits",
  paid: "Commercial frontier — may bill you",
};

export const QUICK_KEY_PROVIDERS: KeyProviderOption[] = [
  { id: "gemini", label: "Google Gemini (15 RPM free)", tier: "free" },
  { id: "groq", label: "GroqCloud (14,400 RPD free)", tier: "free" },
  { id: "sambanova", label: "SambaNova Cloud (free tier)", tier: "free" },
  { id: "mistral", label: "Mistral La Plateforme (free)", tier: "free" },
  { id: "cohere", label: "Cohere Coral (1,000 req/mo free)", tier: "free" },
  { id: "cloudflare", label: "Cloudflare Workers AI (free)", tier: "free" },
  { id: "openrouter", label: "OpenRouter (:free & Union Alpha)", tier: "free" },
  { id: "nvidia", label: "NVIDIA NIM (1,000 credits)", tier: "trial" },
  { id: "cerebras", label: "Cerebras (1M tokens/day)", tier: "trial" },
  { id: "openai", label: "OpenAI (GPT-4o / o3-mini)", tier: "paid" },
  { id: "anthropic", label: "Anthropic (Claude 3.7 Sonnet)", tier: "paid" },
  { id: "deepseek", label: "DeepSeek official (V3 & R1)", tier: "paid" },
];

/** The tiers actually present, in label order — so an empty tier is not rendered as a heading with nothing under it. */
export function keyProviderTiers(providers: KeyProviderOption[]): KeyProviderTier[] {
  const present = new Set(providers.map((p) => p.tier));
  return (["free", "trial", "paid"] as KeyProviderTier[]).filter((t) => present.has(t));
}

/**
 * Capability marks for a row.
 *
 * `null` is *not reported* and renders nothing — deliberately not a cross. A
 * model nobody measured about is not a model without tools, and a struck-out
 * glyph would be a claim the server never made. This is the same tri-state
 * resolver the Settings cards use, so the two surfaces cannot disagree.
 */
export function modelMarks(m: AIModel, caps: { vision: boolean | null; tools: boolean | null; reasoningEffort: boolean | null }): string {
  const marks: string[] = [];
  if (caps.vision === true) marks.push("👁");
  if (caps.tools === true) marks.push("🔧");
  if (caps.reasoningEffort === true) marks.push("🧠");
  return marks.length ? marks.join("") : "";
}

/** Row label: name plus capability marks. */
export function modelMenuLabel(m: AIModel, caps: { vision: boolean | null; tools: boolean | null; reasoningEffort: boolean | null }): string {
  const marks = modelMarks(m, caps);
  return marks ? `${m.name} ${marks}` : m.name;
}

/**
 * The tooltip spelling out every capability including its unknown states.
 *
 * Kept as text because a tooltip is the one place a long, fully-qualified
 * sentence costs nothing: "image input: not reported" is the honest answer and
 * shortening it to a dash would erase which of the two absences it is.
 */
export function modelMenuTitle(
  m: AIModel,
  caps: { vision: boolean | null; tools: boolean | null; reasoningEffort: boolean | null; contextWindow: number | null },
  contextWindowText: string | null,
): string {
  const label = (state: boolean | null, yes: string, no: string) => (state === null ? `${yes}: not reported` : `${yes}: ${state ? "yes" : no}`);
  const parts: string[] = [m.name, label(caps.vision, "image input", "no"), label(caps.tools, "tools", "no"), label(caps.reasoningEffort, "reasoning effort", "no")];
  if (contextWindowText) parts.push(`context: ${contextWindowText} tokens`);
  return parts.join(" · ");
}

/**
 * The provider that serves this model, or `null` when none can be identified.
 *
 * Three probes, in order of specificity: an exact model-ID match in the
 * provider's own list, then the `free:<provider>` id form, then the declared
 * `provider` field. Returning `null` is a real answer and is what keeps the
 * chip honest — a model with no identifiable provider shows no chip rather
 * than inheriting the verdict of whichever provider happened to be first.
 */
export function findProviderForModel(providers: FreeProviderHealth[], model: AIModel): FreeProviderHealth | null {
  const lowerId = model.id.toLowerCase();
  for (const p of providers) {
    if (p.modelIds.some((id) => id.toLowerCase() === lowerId)) return p;
  }
  if (lowerId.startsWith("free:")) {
    const suffix = lowerId.slice("free:".length);
    const byName = providers.find((p) => p.name.toLowerCase() === suffix);
    if (byName) return byName;
  }
  const declared = (model.provider || "").toLowerCase();
  if (declared) {
    const byDeclared = providers.find((p) => p.name.toLowerCase() === declared);
    if (byDeclared) return byDeclared;
  }
  return null;
}

export type ProviderHealthKey = "healthy" | "failing" | "not_probed";

export interface ProviderHealthChip {
  key: ProviderHealthKey;
  /** Always names the *provider*. A model chip is not something the probe measured. */
  label: string;
  dotClass: string;
}

const HEALTH_DOT: Record<ProviderHealthKey, string> = {
  healthy: "bg-emerald-500",
  failing: "bg-red-500",
  not_probed: "bg-muted-foreground/40",
};

/**
 * The chip for a model, derived from its provider's measured state.
 *
 * `healthy: null` is a third state and renders "not probed" in grey — never
 * green, and never folded into `failing`. A server that has not measured a
 * gateway is reporting an absence of evidence, not evidence of a problem.
 */
export function healthyChip(providers: FreeProviderHealth[], model: AIModel): ProviderHealthChip | null {
  const provider = findProviderForModel(providers, model);
  if (!provider) return null;
  if (provider.healthy === true) return { key: "healthy", label: "provider healthy", dotClass: HEALTH_DOT.healthy };
  if (provider.healthy === false) return { key: "failing", label: "provider failing", dotClass: HEALTH_DOT.failing };
  return { key: "not_probed", label: "provider not probed", dotClass: HEALTH_DOT.not_probed };
}

export interface ProbeSummary {
  healthy: number;
  failing: number;
  notProbed: number;
  total: number;
  eligible: number;
}

export function probeCounts(providers: FreeProviderHealth[]): ProbeSummary {
  const counts: ProbeSummary = { healthy: 0, failing: 0, notProbed: 0, total: providers.length, eligible: 0 };
  for (const p of providers) {
    if (p.healthy === true) counts.healthy += 1;
    else if (p.healthy === false) counts.failing += 1;
    else counts.notProbed += 1;
    if (p.eligible) counts.eligible += 1;
  }
  return counts;
}

/**
 * The one-line probe result, with every state spelled out separately.
 *
 * "8 of 10 healthy" alone cannot say whether the other two failed or were
 * never probed — two opposite situations rendered as the same number — so each
 * non-zero bucket gets its own clause, and the zero-provider case says which of
 * the three possible reasons it is rather than reporting a confident `0/0`.
 */
export function probeSummaryText(providers: FreeProviderHealth[], readFailed: boolean): string {
  if (providers.length === 0) {
    return readFailed ? "The catalog read failed, so no provider was measured." : "The server reported no free providers.";
  }
  const c = probeCounts(providers);
  const bits: string[] = [`${c.healthy} of ${c.total} providers healthy`];
  if (c.failing > 0) bits.push(`${c.failing} failing`);
  if (c.notProbed > 0) bits.push(`${c.notProbed} not probed`);
  bits.push(`${c.eligible} eligible`);
  return bits.join(" · ");
}

/** The disclosure that keeps inline chips honest. Rendered once per menu, never per row. */
export const HEALTH_SCOPE_DISCLOSURE = "Health is measured per provider, not per model — a listed model is served by a gateway in that state, not individually proven.";

/** The trigger's own label for the current selection. */
export function selectedModelLabel(models: AIModel[], selectedId: string): string | null {
  const found = models.find((m) => m.id === selectedId);
  return found ? found.name : null;
}
