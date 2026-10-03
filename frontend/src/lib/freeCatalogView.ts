/**
 * The strings the free-catalog dropdown renders per provider.
 *
 * These are pure derivations rather than inline JSX for the same reason
 * `lib/network.ts` owns `connectivityView` and `lib/activity.ts` owns
 * `deriveActivity`: every one of them is a place where a missing measurement is
 * one keystroke away from being rendered as a real one, and the only way to pin
 * that down in a test is to drive the function that produces the sentence.
 *
 * The rule they all share: **absence is worded, never filled in.** A `null`
 * latency is not `0 ms`, an unreported model count is not the length of the list
 * that arrived, and a bounded prefix is not a whole catalog.
 */

import type { FreeProviderHealth } from "./freeModels";

/**
 * Words for the tri-state health flag.
 *
 * `healthy: null` is what the server sends for a provider it never probed or
 * whose probe was inconclusive. It is a third state, so it gets its own word
 * rather than being folded into either verdict.
 */
export function healthLabel(provider: Pick<FreeProviderHealth, "healthy">): string {
  if (provider.healthy === true) return "healthy";
  if (provider.healthy === false) return "failing";
  return "not probed";
}

/**
 * Dot class for one provider's tri-state health.
 *
 * Unmeasured gets the muted dot, because "unmeasured" is neither healthy nor
 * sick and a grey row is the only honest drawing of it.
 */
export function healthDot(provider: Pick<FreeProviderHealth, "healthy">): string {
  if (provider.healthy === true) return "bg-emerald-500";
  if (provider.healthy === false) return "bg-red-500";
  return "bg-muted-foreground/40";
}

/**
 * Measured round-trip, or words.
 *
 * Never `0` and never a bare dash: zero is the fastest possible round-trip, and
 * a dash with no label is a number nobody can interpret.
 */
export function latencyText(provider: Pick<FreeProviderHealth, "latencyMs">): string {
  return provider.latencyMs === null
    ? "not reported"
    : `${provider.latencyMs.toFixed(1)} ms`;
}

/**
 * How many models this provider serves, or a disclosure that it did not say.
 *
 * Reads `modelCount` — the provider's true total — rather than
 * `modelIds.length`, because the server sends a bounded prefix: counting the
 * array would turn "25 shown of 61" into a confident "25 models".
 */
export function modelCountText(
  provider: Pick<FreeProviderHealth, "modelCount">,
): string {
  if (provider.modelCount === null) return "models not reported";
  return `${provider.modelCount} model${provider.modelCount === 1 ? "" : "s"}`;
}

/**
 * The bounded-prefix disclosure, or `null` when the list is complete.
 *
 * `catalog_dict()` sends at most `_MODELS_SHOWN_IN_API` (25) IDs per provider and
 * flags the cut with `models_truncated`. A silent 25-of-61 list presents itself
 * as the whole catalog, so the flag must be visible wherever the IDs are. The
 * mismatch is also treated as a truncation on its own, because trusting the flag
 * alone would let a server that forgot to set it hide a partial list.
 */
export function truncationText(
  provider: Pick<FreeProviderHealth, "modelIds" | "modelCount" | "modelsTruncated">,
): string | null {
  const truncated =
    provider.modelsTruncated ||
    (provider.modelCount ?? 0) > provider.modelIds.length;
  if (!truncated) return null;
  const total =
    provider.modelCount === null
      ? "an unreported number"
      : String(provider.modelCount);
  return `Showing ${provider.modelIds.length} of ${total} — the server sends a bounded prefix per provider.`;
}

/**
 * The headline counts, split by state.
 *
 * `8/10 healthy` alone cannot say whether the other two failed or were simply
 * never probed, so the failing and unmeasured counts are reported separately
 * rather than folded into the remainder.
 */
export function freeCatalogSummary(
  providers: Array<Pick<FreeProviderHealth, "healthy" | "eligible">>,
): { healthy: number; failing: number; notProbed: number; eligible: number; total: number } {
  return {
    healthy: providers.filter((p) => p.healthy === true).length,
    failing: providers.filter((p) => p.healthy === false).length,
    notProbed: providers.filter((p) => p.healthy === null).length,
    eligible: providers.filter((p) => p.eligible).length,
    total: providers.length,
  };
}

/**
 * The sentence shown when the healthy-only filter hides everything.
 *
 * "No healthy provider" is dangerously close to "no providers", so this names
 * the hidden count and says they are failing rather than absent.
 */
export function emptyFilterText(hiddenByFilter: number): string {
  return `No healthy provider. ${hiddenByFilter} provider${hiddenByFilter === 1 ? " is" : "s are"} failing or unmeasured — uncheck the filter to see why.`;
}