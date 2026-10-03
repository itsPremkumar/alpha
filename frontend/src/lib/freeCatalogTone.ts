/**
 * Health of the keyless free-model catalog, as the header and the provider list
 * should draw it.
 *
 * This lives in `lib/` rather than beside the header markup because two surfaces
 * now read it: the trigger's status dot in `ChatView` and the per-provider dots
 * in `FreeCatalogMenu`. A second copy of the colour table would let the dot that
 * says "8/10 healthy" disagree with the eight green rows behind it, which is the
 * exact drift this derivation exists to prevent.
 */

/**
 * `unknown` is a real state, not a default to be optimised away: the server
 * reports `healthy: null` for a provider it has not probed, and a failed catalog
 * read leaves every provider unmeasured. Painting either of those green is an
 * optimistic success for something the server did not confirm.
 */
export type FreeCatalogTone = "good" | "partial" | "bad" | "unknown";

/** Dot colour per tone. Muted for `unknown` so it never reads as healthy. */
export const FREE_TONE_DOT: Record<FreeCatalogTone, string> = {
  good: "bg-emerald-500",
  partial: "bg-amber-500",
  bad: "bg-red-500",
  unknown: "bg-muted-foreground/40",
};

/**
 * Derive the catalog tone from the server's per-provider `healthy` flags.
 *
 * Exported and pure so the honesty test can drive it with the exact payload the
 * Gateway returns — including the "1/10 healthy" and "all null" cases that the
 * old hardcoded green dot could not represent.
 *
 * Grades the **measured** providers only: a mixed healthy/unmeasured catalog
 * grades on its measured rows, and a catalog with nothing measured is `unknown`
 * rather than a pass.
 */
export function freeCatalogTone(
  providers: Array<{ healthy: boolean | null }>,
): FreeCatalogTone {
  const measured = providers.filter((p) => p.healthy !== null);
  // Nobody was measured: unknown, which must not wear a success colour.
  if (measured.length === 0) return "unknown";
  const healthy = measured.filter((p) => p.healthy === true).length;
  if (healthy === 0) return "bad";
  return healthy === measured.length ? "good" : "partial";
}