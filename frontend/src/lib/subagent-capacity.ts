export const SUBAGENT_CONCURRENCY_OPTIONS = [1, 2, 3, 4, 6, 8] as const;

/** Keep the operator-facing concurrency choices within this run's total cap. */
export function concurrencyOptionsForTotal(total: number): number[] {
  const boundedTotal = Number.isFinite(total) ? Math.max(1, Math.floor(total)) : 1;
  return SUBAGENT_CONCURRENCY_OPTIONS.filter((count) => count <= boundedTotal);
}

/** Lowering the total cap must not leave a larger selected concurrency behind. */
export function concurrencyForTotal(concurrency: number, total: number): number {
  const options = concurrencyOptionsForTotal(total);
  const bounded = Number.isFinite(concurrency) ? Math.floor(concurrency) : 1;
  return [...options].reverse().find((count) => count <= bounded) ?? options[0] ?? 1;
}
